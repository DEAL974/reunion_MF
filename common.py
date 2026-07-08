# -*- coding: utf-8 -*-
"""
common.py
=========
Éléments partagés entre les onglets du plugin "Réunion MF" (AROME et Radar) :
clé API Météo-France unique et dossier de cache racine.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import QDateTime, Qt

SETTINGS_GROUP = "reunion_mf"
CACHE_SUBDIR = "reunion_mf_cache"

# La Réunion est en GMT+4 toute l'année (pas d'heure d'été) : décalage fixe.
REUNION_UTC_OFFSET_HOURS = 4
REUNION_TZ = timezone(timedelta(hours=REUNION_UTC_OFFSET_HOURS))


def to_local_datetime(dt: datetime) -> datetime:
    """Convertit un datetime aware (généralement UTC) vers l'heure locale Réunion."""
    return dt.astimezone(REUNION_TZ)


def format_local_time(dt: datetime, fmt: str = "%d/%m %Hh%M") -> str:
    """Formate directement un datetime UTC en heure locale Réunion lisible."""
    return to_local_datetime(dt).strftime(fmt)


def utc_datetime_to_local_qdatetime(dt: datetime) -> QDateTime:
    """
    Construit un QDateTime portant le décalage GMT+4, à partir d'un
    datetime UTC. Tout code consommant ce QDateTime (Temporal Controller,
    plages fixes de couches, overlay) affichera directement l'heure
    locale sans conversion supplémentaire — point unique de centralisation.
    """
    qdt_utc = QDateTime.fromSecsSinceEpoch(int(dt.timestamp()), Qt.TimeSpec.UTC)
    return qdt_utc.toOffsetFromUtc(REUNION_UTC_OFFSET_HOURS * 3600)


def apply_fixed_temporal_range(layer, start: QDateTime, end: QDateTime) -> None:
    """
    Configure une couche raster pour le Temporal Controller QGIS : plage
    temporelle fixe (une échéance/un pas de temps = une tranche horaire).
    Partagé entre AROME et Radar, les deux modules rasters temporels du
    plugin (Observations utilise un mécanisme différent, propre aux
    couches vectorielles : ModeFeatureDateTimeInstantFromField).
    """
    from qgis.core import QgsDateTimeRange, QgsRasterLayerTemporalProperties

    temporal_props = layer.temporalProperties()
    temporal_props.setMode(QgsRasterLayerTemporalProperties.ModeFixedTemporalRange)
    temporal_props.setFixedTemporalRange(QgsDateTimeRange(start, end))
    temporal_props.setIsActive(True)


def get_api_key() -> str:
    """Lit la clé API Météo-France partagée (AROME + Radar utilisent le même compte)."""
    return QgsSettings().value(f"{SETTINGS_GROUP}/api_key", "", type=str)


def set_api_key(value: str) -> None:
    QgsSettings().setValue(f"{SETTINGS_GROUP}/api_key", value)


def get_cache_root() -> Path:
    """Dossier racine du cache local, commun aux deux modules (sous-dossiers distincts)."""
    return Path.home() / CACHE_SUBDIR


def warm_up_ssl() -> None:
    """
    Initialise le contexte SSL par défaut une fois, sur le thread
    principal, avant que la moindre tâche de fond (QgsTask) ne lance sa
    propre requête HTTPS. Mitigation d'un bug connu de CPython sous
    Windows : l'initialisation du magasin de certificats
    (ssl.load_default_certs) n'est pas totalement thread-safe, et deux
    threads qui l'initialisent simultanément peuvent provoquer un
    plantage natif (access violation) plutôt qu'une simple exception
    Python interceptable.
    """
    try:
        import ssl
        ssl.create_default_context()
    except Exception:
        pass  # best-effort : ne doit jamais empêcher le reste de charger


def try_open_temporal_controller_panel(iface) -> bool:
    """
    Tente de trouver et d'afficher le panneau/dock du Temporal Controller
    QGIS (recherche par titre, "temporel"/"temporal"). Retourne True si
    trouvé. Non garanti selon la version QGIS — échoue silencieusement.
    """
    from qgis.PyQt.QtWidgets import QDockWidget

    try:
        main_window = iface.mainWindow()
        for dock in main_window.findChildren(QDockWidget):
            title = dock.windowTitle().lower()
            if "temporel" in title or "temporal" in title:
                dock.setVisible(True)
                dock.raise_()
                return True
    except Exception:
        pass
    return False


class TimeOverlayManager:
    """
    Encadré HUD superposé au canevas carte, affichant le pas de temps
    courant du Temporal Controller. Partagé entre tous les onglets
    temporels (AROME, Observations) pour éviter d'empiler plusieurs
    encadrés si plusieurs modules sont utilisés dans la même session.

    Implémenté comme un QLabel natif, enfant direct du canevas — pas
    comme QgsAnnotation/AnnotationManager (approche initiale abandonnée
    après un bug observé en usage réel : l'encadré n'apparaissait que
    furtivement pendant un zoom/dézoom avant de se faire recouvrir par
    le rendu asynchrone de la couche temporelle, l'empilement de l'item
    d'annotation par rapport à l'item de rendu carte n'étant pas garanti
    par QGIS). Un widget Qt classique reste au-dessus du rendu carte par
    construction, sans dépendre de cet ordre d'empilement interne.
    """

    def __init__(self, iface):
        self.iface = iface
        self._label_widget = None
        self._label = "Réunion MF"
        self._legend_pixmap = None
        self._signal_connected = False
        self._layers_removed_connected = False

    def set_label(self, label: str) -> None:
        self._label = label

    def set_legend_pixmap(self, pixmap) -> None:
        """
        Définit (ou efface avec None) une image de légende affichée sous
        le titre/pas de temps, dans le même encadré — évite d'avoir deux
        encadrés positionnés séparément (source de chevauchement, la
        hauteur réelle de chacun n'étant pas connue à l'avance).
        """
        self._legend_pixmap = pixmap

    def ensure_active(self) -> bool:
        """Crée le label si besoin et branche les signaux. Retourne False si indisponible."""
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QLabel

        canvas = self.iface.mapCanvas()

        if self._label_widget is None:
            label_widget = QLabel(canvas)
            label_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            label_widget.setTextFormat(Qt.TextFormat.RichText)
            label_widget.move(10, 10)
            label_widget.hide()  # affiché seulement une fois un contenu défini
            self._label_widget = label_widget

        ok = True
        if not self._signal_connected:
            try:
                controller = canvas.temporalController()
                controller.updateTemporalRange.connect(self._on_range_changed)
                self._signal_connected = True
            except AttributeError:
                ok = False

        if not self._layers_removed_connected:
            try:
                from qgis.core import QgsProject
                QgsProject.instance().layersRemoved.connect(self._on_layers_removed)
                self._layers_removed_connected = True
            except AttributeError:
                pass

        return ok

    def _on_range_changed(self, time_range) -> None:
        if self._label_widget is None:
            return
        try:
            start_str = time_range.begin().toString("dd/MM/yyyy HH:mm")
        except Exception:
            return

        legend_html = ""
        if self._legend_pixmap is not None:
            import base64

            from qgis.PyQt.QtCore import QBuffer, QIODevice

            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            self._legend_pixmap.save(buffer, "PNG")
            b64_image = base64.b64encode(bytes(buffer.data())).decode("ascii")
            legend_html = f"<br/><img src='data:image/png;base64,{b64_image}'/>"

        self._label_widget.setText(
            f"<div style='background-color:rgba(255,255,255,200); padding:4px;'>"
            f"<b>{self._label}</b><br/>{start_str} (heure locale, GMT+4)"
            f"{legend_html}</div>"
        )
        self._label_widget.adjustSize()
        self._label_widget.raise_()
        self._label_widget.show()

    def refresh_now(self) -> None:
        """
        Force la mise à jour immédiate de l'encadré avec la plage
        temporelle courante, sans attendre que l'utilisateur fasse défiler
        le Temporal Controller (sinon la légende n'apparaîtrait qu'au
        prochain changement de pas de temps).
        """
        try:
            controller = self.iface.mapCanvas().temporalController()
            current_range = controller.dateTimeRangeForFrameNumber(controller.currentFrameNumber())
            self._on_range_changed(current_range)
        except Exception:
            pass

    def _on_layers_removed(self, layer_ids) -> None:
        """Retire l'overlay dès qu'il ne reste plus aucune couche temporelle active (tous modules confondus)."""
        if self._label_widget is None:
            return
        from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer

        project = QgsProject.instance()
        still_has_temporal = any(
            isinstance(lyr, (QgsRasterLayer, QgsVectorLayer)) and
            lyr.temporalProperties() is not None and
            lyr.temporalProperties().isActive()
            for lyr in project.mapLayers().values()
        )
        if not still_has_temporal:
            self._label_widget.hide()
            self._label_widget.deleteLater()
            self._label_widget = None

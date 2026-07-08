# -*- coding: utf-8 -*-
"""
radar_tab.py
============
Contenu de l'onglet Radar (intégré dans le dock partagé Réunion MF).

V1 : rafraîchissement manuel (bouton), pas de polling automatique — pour
éviter de solliciter l'API à intervalle fixe sans validation explicite de
l'utilisateur. Chaque rafraîchissement ajoute les nouvelles échéances au
groupe de couches, constituant un historique local glissant (la Réunion
API elle-même ne conservant que 20h, cf. radar_core.RADAR_RETENTION_HOURS).
"""

from __future__ import annotations

from datetime import timedelta

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsDateTimeRange,
    QgsInterval,
    QgsProject,
    QgsRasterLayer,
    QgsTask,
)
from qgis.gui import QgisInterface
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .common import (
    apply_fixed_temporal_range,
    format_local_time,
    get_api_key,
    get_cache_root,
    try_open_temporal_controller_panel,
    utc_datetime_to_local_qdatetime,
)
from .radar_core import RADAR_RETENTION_HOURS, RadarCoreError, RadarService

RADAR_STEP_MINUTES = 5


def _make_service() -> RadarService | None:
    api_key = get_api_key()
    if not api_key:
        return None
    return RadarService(api_key=api_key, cache_dir=get_cache_root() / "radar")


class RadarRefreshTask(QgsTask):
    """Télécharge le dernier paquet radar et produit les GeoTIFF Réunion."""

    def __init__(self, service: RadarService):
        super().__init__("Radar - Actualisation", QgsTask.Flag.CanCancel)
        self._service = service
        self.results: list | None = None
        self.error_message: str | None = None

    def run(self) -> bool:
        try:
            self.results = self._service.refresh()
            return True
        except RadarCoreError as exc:
            self.error_message = str(exc)
            return False
        except Exception as exc:
            self.error_message = f"Erreur inattendue : {exc}"
            return False


class RadarTabWidget(QWidget):
    """Contenu de l'onglet Radar."""

    GROUP_NAME = "Radar précipitations Réunion"

    def __init__(self, iface: QgisInterface, overlay_manager=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._overlay_manager = overlay_manager
        self._loaded_timestamps: set[str] = set()  # évite les doublons de couches
        self._min_ts = None  # bornes cumulées de l'historique local, pour le Temporal Controller
        self._max_ts = None
        self._build_ui()

    # -- construction de l'interface -----------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        info_label = QLabel(
            "Mosaïque radar de précipitation sur La Réunion (radars Piton "
            "Villers + Colorado), pas de 5 minutes.\n"
            f"Rétention côté Météo-France : {RADAR_RETENTION_HOURS}h glissantes "
            "(pas d'archive au-delà)."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        display_group = QGroupBox("Affichage")
        display_layout = QVBoxLayout()

        self.slider_opacite = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacite.setRange(0, 100)
        self.slider_opacite.setValue(80)
        self.label_opacite = QLabel("Opacité : 80 %")
        self.slider_opacite.valueChanged.connect(
            lambda v: self.label_opacite.setText(f"Opacité : {v} %")
        )
        display_layout.addWidget(self.label_opacite)
        display_layout.addWidget(self.slider_opacite)
        display_group.setLayout(display_layout)
        layout.addWidget(display_group)

        self.button_refresh = QPushButton("Actualiser (télécharger le dernier ¼h)")
        self.button_refresh.clicked.connect(self._on_refresh_clicked)
        layout.addWidget(self.button_refresh)

        self.button_purge = QPushButton(f"Vider le cache (> {RADAR_RETENTION_HOURS}h)")
        self.button_purge.clicked.connect(self._on_purge_clicked)
        layout.addWidget(self.button_purge)

        self.label_statut = QLabel("")
        self.label_statut.setWordWrap(True)
        layout.addWidget(self.label_statut)

        layout.addStretch()
        self.setLayout(layout)

    # -- actualisation -----------------------------------------------------

    def _on_refresh_clicked(self) -> None:
        service = _make_service()
        if service is None:
            QMessageBox.warning(
                self, "Clé API manquante",
                "Merci de renseigner la clé API Météo-France en haut du panneau."
            )
            return

        self.button_refresh.setEnabled(False)
        self.label_statut.setText("Téléchargement du paquet radar en cours (~9 Mo)...")

        task = RadarRefreshTask(service)
        task.taskCompleted.connect(lambda: self._on_refresh_finished(task))
        task.taskTerminated.connect(lambda: self._on_refresh_finished(task))
        QgsApplication.taskManager().addTask(task)

    def _on_refresh_finished(self, task: RadarRefreshTask) -> None:
        self.button_refresh.setEnabled(True)

        if task.results is None:
            message = task.error_message or "Échec de l'actualisation radar."
            self.label_statut.setText(f"Erreur : {message}")
            self.iface.messageBar().pushMessage(
                "Radar Réunion", message, level=Qgis.MessageLevel.Critical, duration=8
            )
            return

        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group = root.findGroup(self.GROUP_NAME)
        if group is None:
            group = root.insertGroup(0, self.GROUP_NAME)

        opacity = self.slider_opacite.value() / 100.0
        nb_ajoutees = 0

        for ts, path in task.results:
            key = ts.isoformat()
            if key in self._loaded_timestamps:
                continue  # déjà chargé lors d'une actualisation précédente

            layer_name = f"Radar Réunion — {format_local_time(ts)} (heure locale)"
            layer = QgsRasterLayer(str(path), layer_name)
            if not layer.isValid():
                continue

            self._apply_default_style(layer, opacity)

            start_local = utc_datetime_to_local_qdatetime(ts)
            end_local = utc_datetime_to_local_qdatetime(ts + timedelta(minutes=RADAR_STEP_MINUTES))
            apply_fixed_temporal_range(layer, start_local, end_local)

            project.addMapLayer(layer, addToLegend=False)
            group.addLayer(layer)
            self._loaded_timestamps.add(key)
            self._min_ts = ts if self._min_ts is None else min(self._min_ts, ts)
            self._max_ts = ts if self._max_ts is None else max(self._max_ts, ts)
            nb_ajoutees += 1

        group.setExpanded(False)

        panel_opened = False
        if self._min_ts is not None and nb_ajoutees > 0:
            overall_start = utc_datetime_to_local_qdatetime(self._min_ts)
            overall_end = utc_datetime_to_local_qdatetime(
                self._max_ts + timedelta(minutes=RADAR_STEP_MINUTES)
            )
            try:
                controller = self.iface.mapCanvas().temporalController()
                controller.setTemporalExtents(QgsDateTimeRange(overall_start, overall_end))
                controller.setFrameDuration(QgsInterval(RADAR_STEP_MINUTES * 60))
            except AttributeError:
                pass  # Temporal Controller reste utilisable manuellement

            panel_opened = try_open_temporal_controller_panel(self.iface)
            if self._overlay_manager is not None:
                self._overlay_manager.set_label("Radar Réunion")
                self._overlay_manager.set_legend_pixmap(None)  # efface une éventuelle légende AROME résiduelle
                self._overlay_manager.ensure_active()
                self._overlay_manager.refresh_now()

        if nb_ajoutees == 0:
            self.label_statut.setText(
                "Actualisation effectuée, mais aucune nouvelle échéance "
                "(déjà à jour depuis le dernier rafraîchissement)."
            )
        else:
            note = "" if panel_opened else (
                " Pour l'animer, ouvre le panneau Temporal Controller "
                "(Vue → Panneaux → Contrôleur temporel)."
            )
            self.label_statut.setText(
                f"{nb_ajoutees} nouvelle(s) échéance(s) ajoutée(s) au groupe "
                f"'{self.GROUP_NAME}'.{note}"
            )
        self.iface.messageBar().pushMessage(
            "Radar Réunion",
            f"{nb_ajoutees} nouvelle(s) échéance(s) chargée(s).",
            level=Qgis.MessageLevel.Success, duration=5,
        )

    # -- style par défaut --------------------------------------------------

    @staticmethod
    def _apply_default_style(layer: QgsRasterLayer, opacity: float) -> None:
        """
        Style à classes discrètes façon composite radar OPERA (palette
        fournie par l'utilisateur, 12 classes bleu->vert->jaune->orange->
        rouge->magenta->blanc). Les seuils de référence sont exprimés en
        mm/h (convention OPERA) ; la couche stocke un cumul sur 5 min
        (ACRR), donc chaque seuil est divisé par 12 pour rester cohérent
        avec l'unité réelle des données. Séparé de arome_styles.py car la
        sémantique (cumul court, échelle différente) diffère des
        paramètres AROME.
        """
        from qgis.core import QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer
        from qgis.PyQt.QtGui import QColor

        # (seuil bas de la classe, en mm/h ; couleur RGBA de la classe)
        classes_mm_par_heure = [
            (0.5, (191, 239, 255, 255)),    # >= 0,5 mm/h : cyan très pâle
            (1.0, (120, 220, 255, 255)),    # >= 1 mm/h : cyan clair
            (1.6, (52, 152, 235, 255)),     # >= 1,6 mm/h : bleu ciel
            (2.8, (30, 60, 200, 255)),      # >= 2,8 mm/h : bleu roi
            (4.7, (10, 110, 40, 255)),      # >= 4,7 mm/h : vert foncé
            (8.0, (40, 200, 40, 255)),      # >= 8 mm/h : vert vif
            (10.0, (255, 235, 0, 255)),     # >= 10 mm/h : jaune
            (25.0, (255, 150, 0, 255)),     # >= 25 mm/h : orange
            (30.0, (230, 20, 20, 255)),     # >= 30 mm/h : rouge
            (62.0, (230, 0, 230, 255)),     # >= 62 mm/h : magenta vif
            (100.0, (255, 170, 220, 255)),  # >= 100 mm/h : rose pâle
            (170.0, (255, 255, 255, 255)),  # >= 170 mm/h : blanc
        ]
        MM_PAR_HEURE_VERS_MM_5MIN = 5 / 60

        items = [
            # en dessous du premier seuil : transparent (pas de pluie significative)
            QgsColorRampShader.ColorRampItem(
                classes_mm_par_heure[0][0] * MM_PAR_HEURE_VERS_MM_5MIN, QColor(255, 255, 255, 0)
            )
        ]
        for i, (_, color) in enumerate(classes_mm_par_heure):
            is_last = i == len(classes_mm_par_heure) - 1
            upper_bound = (
                999.0 if is_last
                else classes_mm_par_heure[i + 1][0] * MM_PAR_HEURE_VERS_MM_5MIN
            )
            items.append(QgsColorRampShader.ColorRampItem(upper_bound, QColor(*color)))

        color_ramp = QgsColorRampShader()
        color_ramp.setColorRampType(QgsColorRampShader.Type.Discrete)
        color_ramp.setColorRampItemList(items)
        shader = QgsRasterShader()
        shader.setRasterShaderFunction(color_ramp)

        renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
        renderer.setClassificationMin(0.0)
        renderer.setClassificationMax(items[-1].value)
        layer.setRenderer(renderer)
        layer.renderer().setOpacity(opacity)
        layer.triggerRepaint()

    # -- purge du cache ------------------------------------------------

    def _on_purge_clicked(self) -> None:
        service = _make_service()
        if service is None:
            QMessageBox.warning(
                self, "Clé API manquante",
                "Merci de renseigner la clé API Météo-France en haut du panneau."
            )
            return

        removed = service.purge_cache(hours=RADAR_RETENTION_HOURS)
        self.label_statut.setText(f"{removed} fichier(s) de cache supprimé(s).")
        self.iface.messageBar().pushMessage(
            "Radar Réunion", f"{removed} fichier(s) supprimé(s) du cache.",
            level=Qgis.MessageLevel.Info, duration=4,
        )

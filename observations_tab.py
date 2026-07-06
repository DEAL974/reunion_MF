# -*- coding: utf-8 -*-
"""
observations_tab.py
====================
Contenu de l'onglet Observations (intégré dans le dock partagé Réunion MF).

Contrairement à AROME/Radar (rasters), les observations sont des points
vectoriels avec un vrai champ date (validity_time) : le Temporal
Controller QGIS peut animer une seule couche nativement, sans découpage
en groupe de couches par pas de temps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsDateTimeRange,
    QgsInterval,
    QgsProject,
    QgsTask,
    QgsVectorLayer,
    QgsVectorLayerTemporalProperties,
)
from qgis.gui import QgisInterface
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QDateTime, Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .common import REUNION_UTC_OFFSET_HOURS, get_api_key, get_cache_root, try_open_temporal_controller_panel
from .observations_core import OBS_PARAMS_MAP, ObservationsCoreError, ObservationsService
from .observations_styles import apply_labels, apply_style, disable_labels

TEMPORAL_FIELD = "validity_time"


def _parse_validity_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _make_service() -> ObservationsService | None:
    api_key = get_api_key()
    if not api_key:
        return None
    return ObservationsService(api_key=api_key, cache_dir=get_cache_root() / "observations")


class ObservationsFetchTask(QgsTask):
    """Télécharge et convertit les observations horaires du département."""

    def __init__(self, service: ObservationsService, id_departement: str):
        super().__init__("Observations - Téléchargement", QgsTask.Flag.CanCancel)
        self._service = service
        self._id_departement = id_departement
        self.result_path: Path | None = None
        self.error_message: str | None = None

    def run(self) -> bool:
        try:
            self.result_path = self._service.fetch(id_departement=self._id_departement)
            return True
        except ObservationsCoreError as exc:
            self.error_message = str(exc)
            return False
        except Exception as exc:
            self.error_message = f"Erreur inattendue : {exc}"
            return False


class ObservationsTabWidget(QWidget):
    """Contenu de l'onglet Observations."""

    LAYER_NAME = "Observations Réunion (24h)"
    ID_DEPARTEMENT = "974"

    def __init__(self, iface: QgisInterface, overlay_manager=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._overlay_manager = overlay_manager
        self._current_layer: QgsVectorLayer | None = None
        self._current_layer_id: str | None = None
        self._build_ui()
        QgsProject.instance().layersRemoved.connect(self._on_layers_removed)

    def _on_layers_removed(self, layer_ids) -> None:
        """Vide la référence à la couche courante dès qu'elle est supprimée du projet."""
        if self._current_layer_id is not None and self._current_layer_id in layer_ids:
            self._current_layer = None
            self._current_layer_id = None

    # -- construction de l'interface -----------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        info_label = QLabel(
            "Observations horaires des stations Météo-France de La Réunion "
            "(24h glissantes). Une seule couche, animable directement via "
            "le Temporal Controller (champ 'validity_time')."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        param_group = QGroupBox("Paramètre à afficher")
        param_form = QFormLayout()
        self.combo_parametre = QComboBox()
        for code, info in OBS_PARAMS_MAP.items():
            unite = f" ({info['unite']})" if info["unite"] else ""
            self.combo_parametre.addItem(f"{info['label_fr']}{unite}", userData=code)
        self.combo_parametre.currentIndexChanged.connect(self._on_parametre_changed)
        param_form.addRow("Paramètre :", self.combo_parametre)

        self.checkbox_valeurs = QCheckBox("Afficher les valeurs mesurées sur la carte")
        self.checkbox_valeurs.setChecked(True)
        self.checkbox_valeurs.toggled.connect(self._on_toggle_labels)
        param_form.addRow(self.checkbox_valeurs)

        param_group.setLayout(param_form)
        layout.addWidget(param_group)

        self.button_charger = QPushButton("Charger les observations (24h)")
        self.button_charger.clicked.connect(self._on_charger_clicked)
        layout.addWidget(self.button_charger)

        self.button_purge = QPushButton("Vider le cache local")
        self.button_purge.clicked.connect(self._on_purge_clicked)
        layout.addWidget(self.button_purge)

        self.label_statut = QLabel("")
        self.label_statut.setWordWrap(True)
        layout.addWidget(self.label_statut)

        layout.addStretch()
        self.setLayout(layout)

    # -- chargement ----------------------------------------------------

    def _on_charger_clicked(self) -> None:
        service = _make_service()
        if service is None:
            QMessageBox.warning(
                self, "Clé API manquante",
                "Merci de renseigner la clé API Météo-France en haut du panneau."
            )
            return

        self.button_charger.setEnabled(False)
        self.label_statut.setText("Téléchargement des observations en cours...")

        task = ObservationsFetchTask(service, self.ID_DEPARTEMENT)
        task.taskCompleted.connect(lambda: self._on_fetch_finished(task))
        task.taskTerminated.connect(lambda: self._on_fetch_finished(task))
        QgsApplication.taskManager().addTask(task)

    def _on_fetch_finished(self, task: ObservationsFetchTask) -> None:
        self.button_charger.setEnabled(True)

        if task.result_path is None:
            message = task.error_message or "Échec du chargement des observations."
            self.label_statut.setText(f"Erreur : {message}")
            self.iface.messageBar().pushMessage(
                "Observations Réunion", message, level=Qgis.MessageLevel.Critical, duration=8
            )
            return

        layer = QgsVectorLayer(str(task.result_path), self.LAYER_NAME, "ogr")
        if not layer.isValid():
            self.label_statut.setText("Erreur : la couche générée est invalide.")
            return

        self._current_layer = layer
        self._current_layer_id = layer.id()
        field = self.combo_parametre.currentData()
        apply_style(layer, field)
        if self.checkbox_valeurs.isChecked():
            unit = OBS_PARAMS_MAP.get(field, {}).get("unite", "")
            apply_labels(layer, field, unit)
        self._apply_temporal_properties(layer)

        QgsProject.instance().addMapLayer(layer)

        temporal_note = self._configure_temporal_extent_from_layer(layer)

        if self._overlay_manager is not None:
            label_fr = OBS_PARAMS_MAP.get(field, {}).get("label_fr", "Observations")
            self._overlay_manager.set_label(label_fr)
            self._overlay_manager.set_legend_pixmap(None)  # efface une éventuelle légende AROME résiduelle
            self._overlay_manager.ensure_active()

        panel_opened = try_open_temporal_controller_panel(self.iface)
        note = " Panneau Temporal Controller ouvert." if panel_opened else (
            " Ouvre le Temporal Controller manuellement (Vue → Panneaux → "
            "Contrôleur temporel) pour animer les 24h."
        )
        self.label_statut.setText(
            f"{layer.featureCount()} observations chargées ('{self.LAYER_NAME}')." +
            note + temporal_note
        )
        self.iface.messageBar().pushMessage(
            "Observations Réunion",
            f"{layer.featureCount()} observations chargées.",
            level=Qgis.MessageLevel.Success, duration=5,
        )

    # -- étendue temporelle réelle ---------------------------------------

    def _configure_temporal_extent_from_layer(self, layer: QgsVectorLayer) -> str:
        """
        Calcule le min/max réel de `validity_time` sur les features
        chargées, et configure le Temporal Controller dessus — plus fiable
        que de compter sur un calcul automatique de l'étendue en mode
        "instant depuis un champ" (non garanti selon la version QGIS).

        Le driver GeoJSON de QGIS détecte automatiquement ce champ comme
        DateTime et renvoie déjà des objets QDateTime (pas des chaînes),
        d'où la gestion des deux cas par sécurité.
        """
        raw_values = [
            feature[TEMPORAL_FIELD] for feature in layer.getFeatures()
            if feature[TEMPORAL_FIELD]
        ]
        if not raw_values:
            return " (étendue temporelle non déterminée : champ vide.)"

        qdatetimes: list[QDateTime] = []
        for value in raw_values:
            if isinstance(value, QDateTime):
                qdatetimes.append(value)
            else:
                try:
                    parsed = _parse_validity_time(str(value))
                    qdatetimes.append(QDateTime.fromSecsSinceEpoch(int(parsed.timestamp()), Qt.TimeSpec.UTC))
                except ValueError:
                    continue  # valeur illisible, ignorée plutôt que de tout faire échouer

        if not qdatetimes:
            return " (étendue temporelle non déterminée : format de date inattendu.)"

        # Qt compare les QDateTime sur l'instant réel, indépendamment de
        # leur fuseau/décalage propre : min/max restent corrects même en
        # mélangeant UTC (valeurs OGR) et autres représentations.
        dt_min = min(qdatetimes).toOffsetFromUtc(REUNION_UTC_OFFSET_HOURS * 3600)
        dt_max = max(qdatetimes).toOffsetFromUtc(REUNION_UTC_OFFSET_HOURS * 3600)

        try:
            controller = self.iface.mapCanvas().temporalController()
            controller.setTemporalExtents(QgsDateTimeRange(dt_min, dt_max))
            controller.setFrameDuration(QgsInterval(3600))  # pas horaire
            return ""
        except AttributeError:
            return " (configuration auto de l'étendue temporelle indisponible.)"

    # -- réactions aux changements de l'UI --------------------------------

    def _on_parametre_changed(self) -> None:
        """Réapplique style + étiquettes sur la couche déjà chargée, sans retéléchargement."""
        if self._current_layer is None or sip.isdeleted(self._current_layer):
            self._current_layer = None
            self._current_layer_id = None
            return
        field = self.combo_parametre.currentData()
        apply_style(self._current_layer, field)
        if self.checkbox_valeurs.isChecked():
            unit = OBS_PARAMS_MAP.get(field, {}).get("unite", "")
            apply_labels(self._current_layer, field, unit)
        if self._overlay_manager is not None:
            label_fr = OBS_PARAMS_MAP.get(field, {}).get("label_fr", "Observations")
            self._overlay_manager.set_label(label_fr)
            self._overlay_manager.set_legend_pixmap(None)  # efface une éventuelle légende AROME résiduelle

    def _on_toggle_labels(self, checked: bool) -> None:
        if self._current_layer is None or sip.isdeleted(self._current_layer):
            self._current_layer = None
            self._current_layer_id = None
            return
        if checked:
            field = self.combo_parametre.currentData()
            unit = OBS_PARAMS_MAP.get(field, {}).get("unite", "")
            apply_labels(self._current_layer, field, unit)
        else:
            disable_labels(self._current_layer)

    # -- propriétés temporelles ------------------------------------------

    @staticmethod
    def _apply_temporal_properties(layer: QgsVectorLayer) -> None:
        """
        Configure le Temporal Controller en mode "instant depuis un champ"
        (validity_time) — beaucoup plus simple que le mode "plage fixe"
        utilisé sur les rasters AROME/Radar, une seule couche suffit.

        Non vérifié en dehors de QGIS réel : le nom exact du mode
        (ModeFeatureDateTimeInstantFromField) est ma meilleure connaissance
        de l'API PyQGIS, à confirmer au premier essai.
        """
        temporal_props = layer.temporalProperties()
        try:
            temporal_props.setMode(
                QgsVectorLayerTemporalProperties.ModeFeatureDateTimeInstantFromField
            )
            temporal_props.setStartField(TEMPORAL_FIELD)
            temporal_props.setIsActive(True)
        except AttributeError:
            pass  # animation manuelle seulement si l'API diffère sur cette version

    # -- purge du cache ------------------------------------------------

    def _on_purge_clicked(self) -> None:
        service = _make_service()
        if service is None:
            QMessageBox.warning(
                self, "Clé API manquante",
                "Merci de renseigner la clé API Météo-France en haut du panneau."
            )
            return

        removed = service.purge_cache(hours=24)
        self.label_statut.setText(f"{removed} fichier(s) de cache supprimé(s).")
        self.iface.messageBar().pushMessage(
            "Observations Réunion", f"{removed} fichier(s) supprimé(s) du cache.",
            level=Qgis.MessageLevel.Info, duration=4,
        )

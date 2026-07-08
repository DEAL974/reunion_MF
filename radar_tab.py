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
    configure_temporal_animation,
    format_local_time,
    get_api_key,
    get_cache_root,
    try_open_temporal_controller_panel,
    utc_datetime_to_local_qdatetime,
    utc_datetime_to_qdatetime,
)
from .radar_core import RADAR_RETENTION_HOURS, RadarCoreError, RadarService
from .radar_styles import apply_style, build_legend_pixmap

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

    def __init__(self, iface: QgisInterface, overlay_manager=None, ensure_base_layers=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._overlay_manager = overlay_manager
        self._ensure_base_layers = ensure_base_layers
        self._loaded_layer_ids: dict[str, str] = {}  # ts.isoformat() -> id de couche, évite les doublons
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
            existing_id = self._loaded_layer_ids.get(key)
            if existing_id is not None and project.mapLayer(existing_id) is not None:
                continue  # déjà chargé lors d'une actualisation précédente ET toujours présent

            layer_name = f"Radar Réunion — {format_local_time(ts)} (heure locale)"
            layer = QgsRasterLayer(str(path), layer_name)
            if not layer.isValid():
                continue

            apply_style(layer, opacity)

            start_local = utc_datetime_to_local_qdatetime(ts)
            end_local = utc_datetime_to_local_qdatetime(ts + timedelta(minutes=RADAR_STEP_MINUTES))
            apply_fixed_temporal_range(layer, start_local, end_local)

            project.addMapLayer(layer, addToLegend=False)
            group.addLayer(layer)
            self._loaded_layer_ids[key] = layer.id()
            self._min_ts = ts if self._min_ts is None else min(self._min_ts, ts)
            self._max_ts = ts if self._max_ts is None else max(self._max_ts, ts)
            nb_ajoutees += 1

        group.setExpanded(False)

        # Toujours vérifié, même si aucune nouvelle échéance n'a été ajoutée
        # (actualisation "à jour") : l'action a réussi, l'habillage doit
        # rester présent indépendamment de l'arrivée ou non de données neuves.
        if self._ensure_base_layers is not None:
            self._ensure_base_layers()

        panel_opened = False
        # Reconfiguré à chaque actualisation réussie, pas seulement quand
        # une nouvelle échéance arrive : sinon un clic "déjà à jour" laisse
        # en place le réglage d'un autre module (ex: AROME), et Radar reste
        # inanimable tant qu'aucune donnée neuve n'est arrivée.
        if self._min_ts is not None:
            overall_start = utc_datetime_to_qdatetime(self._min_ts)
            overall_end = utc_datetime_to_qdatetime(
                self._max_ts + timedelta(minutes=RADAR_STEP_MINUTES)
            )
            try:
                controller = self.iface.mapCanvas().temporalController()
                configure_temporal_animation(
                    controller, overall_start, overall_end, RADAR_STEP_MINUTES * 60
                )
            except AttributeError:
                pass  # Temporal Controller reste utilisable manuellement

            panel_opened = try_open_temporal_controller_panel(self.iface)
            if self._overlay_manager is not None:
                self._overlay_manager.set_label("Radar Réunion")
                self._overlay_manager.set_legend_pixmap(build_legend_pixmap())
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

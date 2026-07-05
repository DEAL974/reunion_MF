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

from pathlib import Path

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

from .common import format_local_time, get_api_key, get_cache_root
from .radar_core import RADAR_RETENTION_HOURS, RadarCoreError, RadarService


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

    def __init__(self, iface: QgisInterface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._loaded_timestamps: set[str] = set()  # évite les doublons de couches
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

            project.addMapLayer(layer, addToLegend=False)
            group.addLayer(layer)
            self._loaded_timestamps.add(key)
            nb_ajoutees += 1

        group.setExpanded(False)

        if nb_ajoutees == 0:
            self.label_statut.setText(
                "Actualisation effectuée, mais aucune nouvelle échéance "
                "(déjà à jour depuis le dernier rafraîchissement)."
            )
        else:
            self.label_statut.setText(
                f"{nb_ajoutees} nouvelle(s) échéance(s) ajoutée(s) au groupe "
                f"'{self.GROUP_NAME}'."
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
        Style simple dégradé transparent -> bleu foncé selon l'intensité
        de précipitation (mm sur 5 min). Séparé de arome_styles.py car la
        sémantique (cumul court, échelle différente) diffère des paramètres
        AROME.
        """
        from qgis.core import QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer
        from qgis.PyQt.QtGui import QColor

        stops = [
            (0.0, (255, 255, 255, 0)),
            (0.2, (198, 219, 239, 180)),
            (1.0, (107, 174, 214, 220)),
            (4.0, (33, 113, 181, 240)),
            (10.0, (8, 48, 107, 255)),
        ]
        color_ramp = QgsColorRampShader()
        color_ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
        color_ramp.setColorRampItemList([
            QgsColorRampShader.ColorRampItem(v, QColor(r, g, b, a)) for v, (r, g, b, a) in stops
        ])
        shader = QgsRasterShader()
        shader.setRasterShaderFunction(color_ramp)

        renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
        renderer.setClassificationMin(stops[0][0])
        renderer.setClassificationMax(stops[-1][0])
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

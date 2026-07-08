# -*- coding: utf-8 -*-
"""
reunion_mf_gui.py
==================
Point d'assemblage du plugin "Réunion MF" : un dock unique contenant une
clé API Météo-France partagée (AROME + Radar utilisent le même compte) et
un QTabWidget avec les onglets AROME et Radar.
"""

from __future__ import annotations

from pathlib import Path

from qgis.core import Qgis, QgsProject, QgsRasterLayer, QgsVectorLayer
from qgis.gui import QgisInterface
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction,
    QDockWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .arome_tab import AromeTabWidget
from .common import TimeOverlayManager, warm_up_ssl
from .config_tab import ConfigTabWidget
from .observations_tab import ObservationsTabWidget
from .radar_tab import RadarTabWidget


class ReunionMFDockWidget(QDockWidget):
    """Panneau latéral unique du plugin, avec clé API partagée + onglets."""

    CONTOUR_LAYER_NAME = "Contour La Réunion"
    OSM_LAYER_NAME = "OpenStreetMap"

    def __init__(self, iface: QgisInterface, parent=None):
        super().__init__("Réunion MF", parent)
        self.iface = iface
        warm_up_ssl()  # avant toute tâche réseau, cf. common.warm_up_ssl
        self._contour_layer_id: str | None = None
        self._contour_reorder_connected = False
        self._reordering_guard = False
        self._build_ui()
        self._on_project_loaded()  # applique une première fois pour le projet déjà ouvert
        QgsProject.instance().readProject.connect(self._on_project_loaded)

    def _on_project_loaded(self, *args) -> None:
        """
        Ré-exécuté à chaque chargement de projet (nouveau ou existant),
        pas seulement à la création du dock — sinon changer de projet dans
        la même session QGIS sans redémarrer laissait le CRS/contour du
        projet précédent sans être retraités.
        """
        self._ensure_project_crs()
        self.ensure_base_layers()

    def ensure_base_layers(self) -> None:
        """
        (Ré)ajoute le contour Réunion et le fond OSM s'ils sont absents du
        projet. Appelé au chargement de projet, mais aussi par chaque
        onglet après génération de données (AROME/Radar/Observations) :
        readProject ne se déclenche ni sur "Nouveau projet" ni si
        l'utilisateur supprime ces couches en cours de session, ces deux
        cas laissaient sinon le contour/fond de carte durablement absents
        jusqu'au redémarrage de QGIS. _load_reunion_contour/_load_osm_basemap
        sont déjà idempotents (vérifient l'absence avant de recréer).
        """
        self._load_reunion_contour()
        self._load_osm_basemap()

    def _build_ui(self) -> None:
        container = QWidget()
        layout = QVBoxLayout()

        # -- Onglets --
        self.overlay_manager = TimeOverlayManager(self.iface)
        self.tabs = QTabWidget()

        self.config_tab = ConfigTabWidget(self.iface, on_unlock_changed=self._on_unlock_changed)
        self.arome_tab = AromeTabWidget(
            self.iface, overlay_manager=self.overlay_manager, ensure_base_layers=self.ensure_base_layers
        )
        self.radar_tab = RadarTabWidget(
            self.iface, overlay_manager=self.overlay_manager, ensure_base_layers=self.ensure_base_layers
        )
        self.observations_tab = ObservationsTabWidget(
            self.iface, overlay_manager=self.overlay_manager, ensure_base_layers=self.ensure_base_layers
        )

        self.tabs.addTab(self.config_tab, "Configuration")
        self.tabs.addTab(self.arome_tab, "AROME (prévisions)")
        self.tabs.addTab(self.radar_tab, "Radar (temps réel)")
        self.tabs.addTab(self.observations_tab, "Observations (stations)")

        # Verrouillage initial : les 3 onglets de données restent grisés
        # tant que la Configuration n'a pas validé la clé (le test auto au
        # démarrage, s'il y a déjà une clé enregistrée, débloque aussitôt).
        for index in range(1, self.tabs.count()):
            self.tabs.setTabEnabled(index, False)

        layout.addWidget(self.tabs)

        container.setLayout(layout)
        self.setWidget(container)

    def _on_unlock_changed(self, unlocked: bool) -> None:
        """Appelé par l'onglet Configuration après chaque test de clé."""
        for index in range(1, self.tabs.count()):
            self.tabs.setTabEnabled(index, unlocked)
        if unlocked:
            self.arome_tab._populate_packages()  # rafraîchit le catalogue avec la clé validée

    def _ensure_project_crs(self) -> None:
        """
        Force le CRS du projet vers EPSG:2975 (RGR92 / UTM 40S), le
        référentiel standard utilisé par tout le plugin. Corrige
        notamment le cas où le projet aurait hérité d'un pseudo-CRS
        ad hoc importé depuis un fichier GRIB AROME (reprojection à la
        volée peu fiable dans ce cas).

        Notification explicite (barre de messages) à chaque changement
        réel, pour que l'action ne soit jamais silencieuse — même si
        elle est automatique.
        """
        from qgis.core import QgsCoordinateReferenceSystem

        target_crs = QgsCoordinateReferenceSystem("EPSG:2975")
        project = QgsProject.instance()
        current_crs = project.crs()

        if current_crs.authid() != target_crs.authid():
            previous_desc = current_crs.description() or current_crs.authid() or "CRS inconnu/non standard"
            project.setCrs(target_crs)
            self.iface.messageBar().pushMessage(
                "Réunion MF",
                f"CRS du projet réglé sur EPSG:2975 (RGR92 / UTM 40S) — "
                f"précédemment : {previous_desc}.",
                level=Qgis.MessageLevel.Info, duration=6,
            )

    def _load_reunion_contour(self) -> None:
        """
        Charge automatiquement le contour de La Réunion en bas de la
        légende, une fois par session (pas de doublon si le dock est
        rouvert plusieurs fois). Fichier embarqué dans le plugin, source
        IGN (via le projet opendata france-geojson) : contour figé, pas
        de dépendance réseau au démarrage.
        """
        project = QgsProject.instance()
        root = project.layerTreeRoot()

        # Évite le doublon si déjà chargé (dock fermé/rouvert dans la session)
        existing = project.mapLayersByName(self.CONTOUR_LAYER_NAME)
        if existing:
            self._contour_layer_id = existing[0].id()
            self._connect_contour_keep_on_top()
            return

        contour_path = Path(__file__).parent / "data" / "reunion_contour.geojson"
        if not contour_path.exists():
            return  # absence silencieuse : pas bloquant pour le reste du plugin

        layer = QgsVectorLayer(str(contour_path), self.CONTOUR_LAYER_NAME, "ogr")
        if not layer.isValid():
            return

        # Forçage explicite du CRS : le fichier (RFC 7946) n'embarque pas
        # de métadonnée CRS, et selon la version de GDAL cette absence
        # peut être mal interprétée par le pilote OGR. On utilise EPSG:4627
        # (RGR92 géographique) plutôt qu'EPSG:4326 (WGS84 global), pour
        # rester cohérent avec EPSG:2975 (RGR92 / UTM 40S) déjà utilisé
        # partout ailleurs dans le plugin (vues Réunion AROME/Radar).
        from qgis.core import QgsCoordinateReferenceSystem
        layer.setCrs(QgsCoordinateReferenceSystem("EPSG:4627"))

        self._apply_contour_style(layer)
        project.addMapLayer(layer, addToLegend=False)
        root.insertLayer(0, layer)  # tout en haut, pour rester visible par-dessus les rasters météo
        self._contour_layer_id = layer.id()
        self._connect_contour_keep_on_top()

    def _connect_contour_keep_on_top(self) -> None:
        """Branche l'écoute qui remet le contour au sommet à chaque nouvelle couche ajoutée."""
        if self._contour_reorder_connected:
            return
        root = QgsProject.instance().layerTreeRoot()
        root.addedChildren.connect(self._on_tree_children_added)
        self._contour_reorder_connected = True

    def _on_tree_children_added(self, node, index_from, index_to) -> None:
        """
        Remet le nœud du contour en position 0 dès qu'un autre élément est
        ajouté à l'arbre des couches (couches météo, groupes de séries...).
        Garde-fou (_reordering_guard) contre la boucle infinie, puisque le
        repositionnement lui-même déclenche à nouveau ce même signal.
        """
        if self._reordering_guard or self._contour_layer_id is None:
            return

        root = QgsProject.instance().layerTreeRoot()
        contour_node = root.findLayer(self._contour_layer_id)
        if contour_node is None:
            return

        children = root.children()
        if not children or children[0] is contour_node:
            return  # déjà au sommet, rien à faire

        self._reordering_guard = True
        try:
            root.insertChildNode(0, contour_node.clone())
            root.removeChildNode(contour_node)
        finally:
            self._reordering_guard = False

    def _load_osm_basemap(self) -> None:
        """
        Charge automatiquement un fond OpenStreetMap (flux de tuiles XYZ,
        mécanisme natif QGIS), une fois par projet. Placé sous le contour
        de La Réunion (appelé juste avant dans _on_project_loaded), donc
        insertion après lui pour se retrouver en dessous dans la légende.

        Échec silencieux si hors-ligne ou si le service est indisponible :
        pas bloquant pour le reste du plugin (couches météo indépendantes).
        """
        project = QgsProject.instance()
        root = project.layerTreeRoot()

        for layer in project.mapLayersByName(self.OSM_LAYER_NAME):
            return  # déjà chargé, pas de doublon

        url = "type=xyz&url=https://tile.openstreetmap.org/%7Bz%7D/%7Bx%7D/%7By%7D.png&zmax=19&zmin=0"
        layer = QgsRasterLayer(url, self.OSM_LAYER_NAME, "wms")
        if not layer.isValid():
            return

        project.addMapLayer(layer, addToLegend=False)
        root.insertLayer(len(root.children()), layer)  # sous le contour (déjà en bas à ce stade)

    @staticmethod
    def _apply_contour_style(layer: QgsVectorLayer) -> None:
        """Contour seul (pas de remplissage), pour ne pas gêner la lecture des couches météo."""
        from qgis.core import QgsFillSymbol

        symbol = QgsFillSymbol.createSimple({
            "color": "0,0,0,0",            # remplissage transparent
            "outline_color": "80,80,80,200",
            "outline_width": "0.6",
        })
        layer.renderer().setSymbol(symbol)
        layer.triggerRepaint()


class ReunionMFPlugin:
    """Classe d'entrée du plugin, appelée par classFactory (__init__.py)."""

    def __init__(self, iface: QgisInterface):
        self.iface = iface
        self.dock_widget: ReunionMFDockWidget | None = None
        self.action: QAction | None = None

    def initGui(self) -> None:
        icon_path = str(Path(__file__).parent / "icon.png")
        icon = QIcon(icon_path) if Path(icon_path).exists() else QIcon()

        self.action = QAction(icon, "Réunion MF", self.iface.mainWindow())
        self.action.triggered.connect(self._toggle_dock)
        self.iface.addPluginToMenu("&Réunion MF", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self) -> None:
        self.iface.removePluginMenu("&Réunion MF", self.action)
        self.iface.removeToolBarIcon(self.action)
        if self.dock_widget is not None:
            self.iface.removeDockWidget(self.dock_widget)
            self.dock_widget = None

    def _toggle_dock(self) -> None:
        if self.dock_widget is None:
            self.dock_widget = ReunionMFDockWidget(self.iface, self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_widget)
        else:
            self.dock_widget.setVisible(not self.dock_widget.isVisible())

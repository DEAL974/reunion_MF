# -*- coding: utf-8 -*-
"""
arome_gui.py
============
Interface graphique du plugin "AROME Outre-Mer - Réunion".

V2 : catalogue de paquets et de paramètres découverts dynamiquement à
l'API/dans le GRIB réel (plus de mapping figé nécessaire pour supporter
SP2/SP3/HP1/HP2/HP3/IP1), série temporelle H+0 -> H+24, purge du cache.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsDateTimeRange,
    QgsInterval,
    QgsProject,
    QgsRasterLayer,
    QgsRasterLayerTemporalProperties,
    QgsTask,
)
from qgis.gui import QgisInterface
from qgis.PyQt.QtCore import QDateTime, Qt
from qgis.PyQt.QtGui import QStandardItem, QStandardItemModel
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .arome_core import (
    AROME_PARAMS_MAP,
    STATIC_PACKAGE_FALLBACK,
    AromeCoreError,
    AromeService,
)
from .arome_styles import apply_style, build_legend_pixmap
from .common import format_local_time, utc_datetime_to_local_qdatetime
from .common import get_api_key, get_cache_root

SERIE_TEMPORELLE_DEFAULT_HEURES = 24

# Options proposées à l'utilisateur. 36/48h dépassent la portée réelle du
# modèle sur certains réseaux (AROME-OM va généralement jusqu'à H+42, avec
# variation selon l'heure du réseau) : sans risque grâce au mécanisme de
# tolérance des échéances manquantes déjà en place (get_time_series).
SERIE_TEMPORELLE_OPTIONS_HEURES = [6, 12, 24, 36, 48]


def _reference_time_to_datetime(reference_time: str) -> datetime:
    """Parse une chaîne ISO 8601 UTC ('2026-07-03T18:00:00Z') en datetime."""
    return datetime.strptime(reference_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _apply_temporal_range(layer: QgsRasterLayer, start: QDateTime, end: QDateTime) -> None:
    """
    Configure la couche pour le Temporal Controller QGIS : plage temporelle
    fixe (une échéance = une tranche horaire). Point non testé en dehors
    de QGIS réel (signature d'API à vérifier au premier essai).
    """
    temporal_props = layer.temporalProperties()
    temporal_props.setMode(QgsRasterLayerTemporalProperties.ModeFixedTemporalRange)
    temporal_props.setFixedTemporalRange(QgsDateTimeRange(start, end))
    temporal_props.setIsActive(True)

# Ordre d'affichage des catégories et libellés lisibles.
_CATEGORY_ORDER = ["surface", "hauteur", "isobares", "autre"]
_CATEGORY_LABELS = {
    "surface": "— Paramètres de surface —",
    "hauteur": "— Niveaux hauteur (altitude) —",
    "isobares": "— Niveaux isobares (pression) —",
    "autre": "— Autres —",
}


def _detect_category(package: dict) -> str:
    """Déduit la catégorie (surface/hauteur/isobares) du titre, avec repli sur le code."""
    title = (package.get("title") or "").lower()
    code = (package.get("code") or "").upper()

    if "surface" in title:
        return "surface"
    if "hauteur" in title:
        return "hauteur"
    if "isobare" in title:
        return "isobares"

    # Repli sur le préfixe du code (utile pour la liste statique de secours)
    if code.startswith("SP"):
        return "surface"
    if code.startswith("HP"):
        return "hauteur"
    if code.startswith("IP"):
        return "isobares"
    return "autre"


def _sort_key(package: dict) -> tuple[int, int]:
    """
    Trie 'courants' avant 'additionnels', puis par numéro croissant
    (ex: 'additionnels', 'additionnels (2)', 'additionnels (3)'...).
    """
    title = (package.get("title") or "").lower()
    is_additionnel = 1 if "additionnel" in title else 0

    match = re.search(r"\((\d+)\)", title)
    numero = int(match.group(1)) if match else 1

    return (is_additionnel, numero)


def _group_and_sort_packages(packages: list[dict]) -> list[tuple[str, list[dict]]]:
    """Groupe les paquets par catégorie (ordre fixe), triés à l'intérieur de chaque groupe."""
    grouped: dict[str, list[dict]] = {cat: [] for cat in _CATEGORY_ORDER}
    for pkg in packages:
        grouped[_detect_category(pkg)].append(pkg)

    result = []
    for cat in _CATEGORY_ORDER:
        items = sorted(grouped[cat], key=_sort_key)
        if items:
            result.append((cat, items))
    return result


def _display_title(pkg: dict) -> str:
    """
    Libellé d'un paquet pour l'UI : titre renvoyé par l'API Météo-France,
    avec correction d'une coquille connue du catalogue ("paramêtres" au
    lieu de "paramètres", accent circonflexe erroné côté API, confirmé
    sur un paquet HP1 réel), et code technique ajouté entre parenthèses
    (ex: "(HP1)") pour repérage rapide - sauf si déjà présent dans le
    titre (cas de la liste statique de secours, qui l'inclut déjà).
    """
    title = (pkg.get("title") or "").replace("paramêtre", "paramètre").replace("Paramêtre", "Paramètre")
    code = pkg.get("code") or ""
    if code and code not in title:
        return f"{title} ({code})"
    return title


def _populate_grouped_combo(combo: QComboBox, packages: list[dict]) -> None:
    """Remplit un QComboBox avec des en-têtes de section non sélectionnables."""
    model = QStandardItemModel()

    for category, items in _group_and_sort_packages(packages):
        header = QStandardItem(_CATEGORY_LABELS[category])
        header.setFlags(Qt.ItemFlag.NoItemFlags)  # non sélectionnable, non cliquable
        header_font = header.font()
        header_font.setBold(True)
        header.setFont(header_font)
        model.appendRow(header)

        for pkg in items:
            item = QStandardItem(f"    {_display_title(pkg)}")
            item.setData(pkg["code"], Qt.ItemDataRole.UserRole)
            model.appendRow(item)

    combo.setModel(model)

    for row in range(model.rowCount()):
        if model.item(row).isEnabled():
            combo.setCurrentIndex(row)
            break


def _make_service() -> AromeService | None:
    api_key = get_api_key()
    if not api_key:
        return None
    return AromeService(api_key=api_key, cache_dir=get_cache_root() / "arome")


# ---------------------------------------------------------------------------
# Tâches de fond
# ---------------------------------------------------------------------------

class ListPackagesTask(QgsTask):
    """Récupère la liste des paquets disponibles depuis l'API."""

    def __init__(self, service: AromeService):
        super().__init__("AROME - Liste des paquets", QgsTask.Flag.CanCancel)
        self._service = service
        self.packages: list[dict] | None = None
        self.error_message: str | None = None

    def run(self) -> bool:
        try:
            self.packages = self._service.list_packages()
            return True
        except AromeCoreError as exc:
            self.error_message = str(exc)
            return False
        except Exception as exc:
            self.error_message = f"Erreur inattendue : {exc}"
            return False


def _format_level_label(level_desc: str) -> str:
    """
    Reformate un niveau vertical brut GDAL en libellé plus lisible.
    Ex: '100000[Pa] ISBL="Isobaric surface"' -> '1000 hPa'.
    Ex: '20[m] HTGL="Specified height level above ground"' -> '20 m'
    (format confirmé sur un paquet HP1 réel).
    Repli sur la chaîne brute si le format ne correspond à aucun des deux
    schémas connus.
    """
    if not level_desc:
        return ""
    match = re.match(r"^([\d.]+)\[Pa\]", level_desc)
    if match:
        try:
            hpa_value = float(match.group(1)) / 100.0
            return f"{hpa_value:g} hPa"
        except ValueError:
            pass
    match = re.match(r"^([\d.]+)\[m\]", level_desc)
    if match:
        try:
            m_value = float(match.group(1))
            return f"{m_value:g} m"
        except ValueError:
            pass
    return level_desc


class DiscoverParametersTask(QgsTask):
    """Télécharge un échantillon et inspecte les bandes réelles du paquet choisi."""

    def __init__(self, service: AromeService, package: str, echeance_heures: int):
        super().__init__("AROME - Analyse du paquet", QgsTask.Flag.CanCancel)
        self._service = service
        self._package = package
        self._echeance_heures = echeance_heures
        self.bands: list[dict] | None = None
        self.reference_time: str | None = None
        self.error_message: str | None = None

    def run(self) -> bool:
        try:
            self.bands, self.reference_time = self._service.discover_parameters(
                package=self._package, echeance_heures=self._echeance_heures
            )
            return True
        except AromeCoreError as exc:
            self.error_message = str(exc)
            return False
        except Exception as exc:
            self.error_message = f"Erreur inattendue : {exc}"
            return False


class LoadLayerTask(QgsTask):
    """Télécharge/extrait une couche unique (mode régional ou Réunion)."""

    def __init__(self, service: AromeService, package: str, band_index: int,
                 element: str, mode: str, echeance_heures: int,
                 reference_time: str | None, level_desc: str | None = None):
        super().__init__("AROME - Chargement de couche", QgsTask.Flag.CanCancel)
        self._service = service
        self._package = package
        self._band_index = band_index
        self._element = element
        self._mode = mode
        self._echeance_heures = echeance_heures
        self._reference_time = reference_time
        self._level_desc = level_desc
        self.result_path: Path | None = None
        self.error_message: str | None = None

    def run(self) -> bool:
        try:
            self.result_path = self._service.get_layer_by_band(
                package=self._package,
                band_index=self._band_index,
                element=self._element,
                mode=self._mode,
                reference_time=self._reference_time,
                echeance_heures=self._echeance_heures,
                level_desc=self._level_desc,
            )
            return True
        except AromeCoreError as exc:
            self.error_message = str(exc)
            return False
        except Exception as exc:
            self.error_message = f"Erreur inattendue : {exc}"
            return False


class LoadTimeSeriesTask(QgsTask):
    """
    Génère la série temporelle H+0 -> H+echeance_max (pas 1h). Rapporte la
    progression via self.setProgress() pour la barre de progression GUI.
    """

    def __init__(self, service: AromeService, package: str, band_index: int,
                 element: str, mode: str, echeance_max_heures: int,
                 reference_time: str | None, level_desc: str | None = None):
        super().__init__("AROME - Série temporelle", QgsTask.Flag.CanCancel)
        self._service = service
        self._package = package
        self._band_index = band_index
        self._element = element
        self._mode = mode
        self._echeance_max_heures = echeance_max_heures
        self._reference_time = reference_time
        self._level_desc = level_desc
        self.echeance_max_heures = echeance_max_heures  # exposé pour le nommage GUI
        self.results: list[tuple[int, Path]] | None = None
        self.skipped: list[int] = []
        self.error_message: str | None = None

    def run(self) -> bool:
        try:
            self.results, self.skipped = self._service.get_time_series(
                package=self._package,
                band_index=self._band_index,
                element=self._element,
                mode=self._mode,
                echeance_max_heures=self._echeance_max_heures,
                reference_time=self._reference_time,
                level_desc=self._level_desc,
                progress_callback=lambda i, total: self.setProgress(100 * i / total),
            )
            return True
        except AromeCoreError as exc:
            self.error_message = str(exc)
            return False
        except Exception as exc:
            self.error_message = f"Erreur inattendue : {exc}"
            return False


# ---------------------------------------------------------------------------
# Dock widget
# ---------------------------------------------------------------------------

class AromeTabWidget(QWidget):
    """Contenu de l'onglet AROME (intégré dans le dock partagé Réunion MF)."""

    def __init__(self, iface: QgisInterface, overlay_manager=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._overlay_manager = overlay_manager

        # état de découverte courant (peuplé après "Analyser le paquet")
        self._discovered_bands: list[dict] = []
        self._discovered_reference_time: str | None = None

        self._build_ui()
        # Ne pas peupler automatiquement ici : ça déclenchait une requête
        # réseau en parallèle de celle du test de clé dans l'onglet
        # Configuration (deux threads ouvrant une connexion HTTPS en même
        # temps), provoquant un plantage natif Windows (accès mémoire
        # invalide dans l'initialisation SSL, non thread-safe dans ce cas).
        # Le catalogue est désormais peuplé uniquement une fois la clé
        # validée (cf. ReunionMFDockWidget._on_unlock_changed).

    # -- construction de l'interface -----------------------------------

    def _build_ui(self) -> None:
        container = QWidget()
        layout = QVBoxLayout()

        # -- Bloc paquet + découverte --
        package_group = QGroupBox("Paquet AROME")
        package_form = QFormLayout()

        self.combo_paquet = QComboBox()
        package_form.addRow("Paquet :", self.combo_paquet)

        self.button_analyser = QPushButton("Analyser le paquet (découvrir les paramètres)")
        self.button_analyser.clicked.connect(self._on_analyser_clicked)
        package_form.addRow(self.button_analyser)

        self.combo_parametre = QComboBox()
        self.combo_parametre.setEnabled(False)
        package_form.addRow("Paramètre :", self.combo_parametre)

        package_group.setLayout(package_form)
        layout.addWidget(package_group)

        # -- Bloc sélection --
        selection_group = QGroupBox("Sélection")
        selection_form = QFormLayout()

        self.spin_echeance = QSpinBox()
        self.spin_echeance.setRange(0, 42)
        self.spin_echeance.setSingleStep(1)
        self.spin_echeance.setSuffix(" h")
        self.spin_echeance.setValue(6)
        selection_form.addRow("Échéance (H+) :", self.spin_echeance)

        self.radio_regional = QRadioButton("Vue régionale (Océan Indien)")
        self.radio_reunion = QRadioButton("Vue Réunion (recadrée, EPSG:2975)")
        self.radio_reunion.setChecked(True)
        selection_form.addRow(self.radio_regional)
        selection_form.addRow(self.radio_reunion)

        selection_group.setLayout(selection_form)
        layout.addWidget(selection_group)

        # -- Bloc affichage --
        display_group = QGroupBox("Affichage")
        display_form = QFormLayout()

        self.slider_opacite = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacite.setRange(0, 100)
        self.slider_opacite.setValue(80)
        self.label_opacite = QLabel("80 %")
        self.slider_opacite.valueChanged.connect(
            lambda v: self.label_opacite.setText(f"{v} %")
        )
        display_form.addRow("Opacité :", self.slider_opacite)
        display_form.addRow("", self.label_opacite)

        display_group.setLayout(display_form)
        layout.addWidget(display_group)

        # -- Boutons de chargement --
        self.button_charger = QPushButton("Charger la couche (échéance unique)")
        self.button_charger.clicked.connect(self._on_charger_clicked)
        layout.addWidget(self.button_charger)

        self.combo_serie_max = QComboBox()
        for heures in SERIE_TEMPORELLE_OPTIONS_HEURES:
            suffix = " (par défaut)" if heures == SERIE_TEMPORELLE_DEFAULT_HEURES else ""
            self.combo_serie_max.addItem(f"H+0 → H+{heures}{suffix}", userData=heures)
        self.combo_serie_max.setCurrentIndex(
            SERIE_TEMPORELLE_OPTIONS_HEURES.index(SERIE_TEMPORELLE_DEFAULT_HEURES)
        )
        layout.addWidget(QLabel("Portée de la série temporelle :"))
        layout.addWidget(self.combo_serie_max)

        self.button_serie = QPushButton("Charger la série temporelle (pas 1h)")
        self.button_serie.clicked.connect(self._on_charger_serie_clicked)
        layout.addWidget(self.button_serie)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # -- Maintenance --
        maintenance_group = QGroupBox("Maintenance")
        maintenance_layout = QVBoxLayout()
        self.button_purge = QPushButton("Vider le cache (fichiers > 3 jours)")
        self.button_purge.clicked.connect(self._on_purge_clicked)
        maintenance_layout.addWidget(self.button_purge)
        maintenance_group.setLayout(maintenance_layout)
        layout.addWidget(maintenance_group)

        self.label_statut = QLabel("")
        self.label_statut.setWordWrap(True)
        layout.addWidget(self.label_statut)

        layout.addStretch()
        self.setLayout(layout)

    # -- catalogue de paquets ------------------------------------------

    def _populate_packages(self) -> None:
        """Tente de récupérer la liste réelle des paquets ; repli statique sinon."""
        service = _make_service()
        self.combo_paquet.clear()

        if service is None:
            self._fill_packages_fallback()
            return

        task = ListPackagesTask(service)
        task.taskCompleted.connect(lambda: self._on_packages_listed(task))
        task.taskTerminated.connect(lambda: self._on_packages_listed(task))
        QgsApplication.taskManager().addTask(task)

    def _fill_packages_fallback(self) -> None:
        _populate_grouped_combo(self.combo_paquet, STATIC_PACKAGE_FALLBACK)

    def _on_packages_listed(self, task: ListPackagesTask) -> None:
        if task.packages:
            _populate_grouped_combo(self.combo_paquet, task.packages)
        else:
            self.label_statut.setText(
                "Catalogue de paquets indisponible (reseau/cle) -- liste par defaut utilisee."
            )
            self._fill_packages_fallback()

    # -- découverte des paramètres --------------------------------------

    def _on_analyser_clicked(self) -> None:
        service = _make_service()
        if service is None:
            QMessageBox.warning(self, "Clé API manquante",
                                 "Merci de renseigner ta clé API Météo-France.")
            return

        package = self.combo_paquet.currentData()
        if not package:
            return

        self.button_analyser.setEnabled(False)
        self.label_statut.setText(f"Analyse du paquet {package} en cours...")

        task = DiscoverParametersTask(service, package, self.spin_echeance.value())
        task.taskCompleted.connect(lambda: self._on_parameters_discovered(task))
        task.taskTerminated.connect(lambda: self._on_parameters_discovered(task))
        QgsApplication.taskManager().addTask(task)

    def _on_parameters_discovered(self, task: DiscoverParametersTask) -> None:
        self.button_analyser.setEnabled(True)

        if task.bands is None:
            message = task.error_message or "Analyse du paquet impossible."
            self.label_statut.setText(f"Erreur : {message}")
            return

        self._discovered_bands = task.bands
        self._discovered_reference_time = task.reference_time

        self.combo_parametre.clear()
        for band in task.bands:
            element = band["element"]
            override = AROME_PARAMS_MAP.get(element)
            niveau = _format_level_label(band["level_desc"] or "")

            if override:
                label = f"{override['label_fr']} ({override['unite']})"
                if niveau:
                    label += f" — {niveau}"
            else:
                label = f"{element} - {band['comment'] or 'sans description'}"
                if niveau:
                    label += f" [{niveau}]"

            self.combo_parametre.addItem(label, userData=band)

        self.combo_parametre.setEnabled(True)
        self.label_statut.setText(
            f"{len(task.bands)} parametre(s) detecte(s) dans le paquet "
            f"(run {task.reference_time})."
        )

    # -- chargement échéance unique --------------------------------------

    def _on_charger_clicked(self) -> None:
        service = _make_service()
        if service is None:
            QMessageBox.warning(self, "Clé API manquante",
                                 "Merci de renseigner ta clé API Météo-France.")
            return

        band = self.combo_parametre.currentData()
        if band is None:
            QMessageBox.information(
                self, "Paramètre non sélectionné",
                "Clique d'abord sur 'Analyser le paquet' pour choisir un paramètre."
            )
            return

        package = self.combo_paquet.currentData()
        mode = "reunion" if self.radio_reunion.isChecked() else "regional"
        echeance = self.spin_echeance.value()

        self.button_charger.setEnabled(False)
        self.label_statut.setText("Téléchargement / traitement en cours...")

        task = LoadLayerTask(
            service=service,
            package=package,
            band_index=band["band_index"],
            element=band["element"],
            mode=mode,
            echeance_heures=echeance,
            reference_time=self._discovered_reference_time,
            level_desc=band.get("level_desc"),
        )
        task.taskCompleted.connect(lambda: self._on_layer_loaded(task, band, mode, echeance))
        task.taskTerminated.connect(lambda: self._on_layer_loaded(task, band, mode, echeance))
        QgsApplication.taskManager().addTask(task)

    def _on_layer_loaded(self, task: LoadLayerTask, band: dict, mode: str, echeance: int) -> None:
        self.button_charger.setEnabled(True)

        if task.result_path is None:
            message = task.error_message or "Échec du chargement (raison inconnue)."
            self.label_statut.setText(f"Erreur : {message}")
            self.iface.messageBar().pushMessage(
                "AROME Outre-Mer", message, level=Qgis.MessageLevel.Critical, duration=8
            )
            return

        element = band["element"]
        layer_name = f"AROME_{element}_{mode}_H{echeance:03d}"
        layer = QgsRasterLayer(str(task.result_path), layer_name)

        if not layer.isValid():
            self.label_statut.setText("Erreur : la couche générée est invalide.")
            return

        opacity = self.slider_opacite.value() / 100.0
        apply_style(layer, element, opacity=opacity)

        QgsProject.instance().addMapLayer(layer)
        self.label_statut.setText(f"Couche chargée : {layer_name}")
        self.iface.messageBar().pushMessage(
            "AROME Outre-Mer", f"Couche '{layer_name}' chargée avec succès.",
            level=Qgis.MessageLevel.Success, duration=5,
        )

    # -- chargement série temporelle --------------------------------------

    def _on_charger_serie_clicked(self) -> None:
        service = _make_service()
        if service is None:
            QMessageBox.warning(self, "Clé API manquante",
                                 "Merci de renseigner ta clé API Météo-France.")
            return

        band = self.combo_parametre.currentData()
        if band is None:
            QMessageBox.information(
                self, "Paramètre non sélectionné",
                "Clique d'abord sur 'Analyser le paquet' pour choisir un paramètre."
            )
            return

        echeance_max = self.combo_serie_max.currentData()
        nb_echeances = echeance_max + 1
        confirm = QMessageBox.question(
            self, "Confirmer le chargement de série",
            f"Ceci va télécharger et traiter {nb_echeances} échéances "
            f"(H+0 à H+{echeance_max}, pas 1h). Les échéances au-delà de la "
            f"portée réelle du modèle seront automatiquement ignorées. "
            f"Cela peut prendre plusieurs minutes. Continuer ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        package = self.combo_paquet.currentData()
        mode = "reunion" if self.radio_reunion.isChecked() else "regional"

        self.button_serie.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.label_statut.setText("Chargement de la série temporelle en cours...")

        task = LoadTimeSeriesTask(
            service=service,
            package=package,
            band_index=band["band_index"],
            element=band["element"],
            mode=mode,
            echeance_max_heures=echeance_max,
            reference_time=self._discovered_reference_time,
            level_desc=band.get("level_desc"),
        )
        task.progressChanged.connect(lambda p: self.progress_bar.setValue(int(p)))
        task.taskCompleted.connect(lambda: self._on_serie_loaded(task, band, mode))
        task.taskTerminated.connect(lambda: self._on_serie_loaded(task, band, mode))
        QgsApplication.taskManager().addTask(task)

    def _on_serie_loaded(self, task: LoadTimeSeriesTask, band: dict, mode: str) -> None:
        self.button_serie.setEnabled(True)
        self.progress_bar.setVisible(False)

        if task.results is None:
            message = task.error_message or "Échec du chargement de la série."
            self.label_statut.setText(f"Erreur : {message}")
            self.iface.messageBar().pushMessage(
                "AROME Outre-Mer", message, level=Qgis.MessageLevel.Critical, duration=8
            )
            return

        element = band["element"]
        override = AROME_PARAMS_MAP.get(element)
        label_fr = override["label_fr"] if override else (band["comment"] or element)
        unit = override["unite"] if override else ""

        opacity = self.slider_opacite.value() / 100.0
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group_name = f"AROME {label_fr} {task.echeance_max_heures}h"
        group = root.insertGroup(0, group_name)
        group.setExpanded(False)  # replié par défaut

        pas_heures = 1  # cohérent avec get_time_series (pas fixe pour l'instant)
        reference_dt = None
        if self._discovered_reference_time:
            try:
                reference_dt = _reference_time_to_datetime(self._discovered_reference_time)
            except ValueError:
                reference_dt = None  # format inattendu : on continue sans le Temporal Controller

        for echeance, path in task.results:
            if reference_dt is not None:
                valid_dt = reference_dt + timedelta(hours=echeance)
                horodatage = format_local_time(valid_dt)
                layer_name = f"{label_fr} — {horodatage} (H+{echeance:03d})"
            else:
                layer_name = f"AROME_{element}_{mode}_H{echeance:03d}"

            layer = QgsRasterLayer(str(path), layer_name)
            if not layer.isValid():
                continue
            apply_style(layer, element, opacity=opacity)

            if reference_dt is not None:
                start_dt = reference_dt + timedelta(hours=echeance)
                end_dt = start_dt + timedelta(hours=pas_heures)
                _apply_temporal_range(
                    layer,
                    utc_datetime_to_local_qdatetime(start_dt),
                    utc_datetime_to_local_qdatetime(end_dt),
                )

            project.addMapLayer(layer, addToLegend=False)
            group.addLayer(layer)

        temporal_note = ""
        panel_opened = False
        if reference_dt is not None and task.results:
            overall_start = utc_datetime_to_local_qdatetime(
                reference_dt + timedelta(hours=task.results[0][0])
            )
            overall_end = utc_datetime_to_local_qdatetime(
                reference_dt + timedelta(hours=task.results[-1][0] + pas_heures)
            )
            try:
                controller = self.iface.mapCanvas().temporalController()
                controller.setTemporalExtents(QgsDateTimeRange(overall_start, overall_end))
                controller.setFrameDuration(QgsInterval(pas_heures * 3600))
            except AttributeError:
                # API légèrement différente selon la version QGIS : le
                # Temporal Controller reste utilisable manuellement même
                # si cette configuration automatique échoue.
                temporal_note = (
                    " (configuration auto du Temporal Controller indisponible, "
                    "réglage manuel possible via le panneau)."
                )

            panel_opened = self._try_open_temporal_controller_panel()
            if self._overlay_manager is not None:
                self._overlay_manager.set_label(label_fr)
                self._overlay_manager.set_legend_pixmap(build_legend_pixmap(element, unit))
                self._overlay_manager.ensure_active()
                self._overlay_manager.refresh_now()
        else:
            temporal_note = " (Temporal Controller non configuré : run de référence inconnu.)"

        self.label_statut.setText(
            f"Série temporelle chargée : {len(task.results)} échéances "
            f"dans le groupe '{group_name}'."
            + (f" ({len(task.skipped)} échéance(s) ignorée(s) : {task.skipped}, "
               f"paramètre absent à ces échéances.)" if task.skipped else "")
            + temporal_note
        )
        self.iface.messageBar().pushMessage(
            "AROME Outre-Mer",
            f"Série '{group_name}' chargée ({len(task.results)} échéances"
            + (f", {len(task.skipped)} ignorée(s)" if task.skipped else "")
            + ").",
            level=Qgis.MessageLevel.Success, duration=6,
        )

        if panel_opened:
            popup_text = (
                "La série temporelle a été chargée et le panneau Temporal "
                "Controller a été ouvert automatiquement.\n\n"
                "Clique sur le bouton ▶ (lecture) dans ce panneau pour "
                "animer les échéances."
            )
        else:
            popup_text = (
                "La série temporelle a été chargée.\n\n"
                "Pour l'animer, ouvre le panneau Temporal Controller :\n"
                "Vue → Panneaux → Contrôleur temporel\n"
                "(ou l'icône horloge dans la barre d'outils si elle est "
                "déjà affichée)\n\n"
                "Une fois ouvert, clique sur ▶ (lecture) pour dérouler "
                "les échéances."
            )
        QMessageBox.information(self, "Animer la série temporelle", popup_text)

    # -- utilitaire : ouverture du panneau Temporal Controller -----------

    def _try_open_temporal_controller_panel(self) -> bool:
        """
        Tente de trouver et d'afficher le panneau/dock du Temporal
        Controller. Recherche par titre plutôt que par nom d'objet
        interne (non documenté de façon stable selon les versions QGIS).
        Retourne True si un dock correspondant a été trouvé et affiché.

        Non vérifié en dehors de QGIS réel : si aucun dock ne correspond,
        la fonction retourne False sans lever d'exception, le popup
        explicatif prend alors le relais.
        """
        try:
            main_window = self.iface.mainWindow()
            for dock in main_window.findChildren(QDockWidget):
                title = dock.windowTitle().lower()
                if "temporel" in title or "temporal" in title:
                    dock.setVisible(True)
                    dock.raise_()
                    return True
        except Exception:
            pass
        return False

    def _on_purge_clicked(self) -> None:
        service = _make_service()
        if service is None:
            QMessageBox.warning(self, "Clé API manquante",
                                 "Merci de renseigner ta clé API Météo-France.")
            return

        removed = service.purge_cache(days=3)
        self.label_statut.setText(f"{removed} fichier(s) de cache supprimé(s).")
        self.iface.messageBar().pushMessage(
            "AROME Outre-Mer", f"{removed} fichier(s) supprimé(s) du cache.",
            level=Qgis.MessageLevel.Info, duration=4,
        )

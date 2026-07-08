# -*- coding: utf-8 -*-
"""
config_tab.py
==============
Onglet Configuration : explique comment obtenir une clé API Météo-France,
permet de la saisir et de la tester produit par produit. Le déblocage des
onglets AROME / Radar / Observations est conditionné à un test réussi.
"""

from __future__ import annotations

from typing import Callable

from qgis.core import QgsApplication, QgsTask
from qgis.gui import QgisInterface
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .common import get_api_key, set_api_key
from .config_core import ConfigTestResult, test_all

INSTRUCTIONS_HTML = """
<b>Comment obtenir une clé API Météo-France :</b>
<ol>
<li>Créer un compte sur
<a href="https://portail-api.meteofrance.fr">portail-api.meteofrance.fr</a></li>
<li>Dans <i>« Mes APIs »</i>, souscrire aux trois produits :
<i>Paquet AROME Outre-Mer</i>, <i>Paquet Radar</i>, <i>Paquet Observations</i></li>
<li>Pour l'application associée, choisir le mode <b>« API Key »</b>
(pas OAuth2)</li>
<li>Cliquer sur <b>« Générer Token »</b>, copier la clé obtenue</li>
<li>La coller ci-dessous, puis cliquer sur <b>« Tester »</b></li>
</ol>
<i>La clé est unique pour les trois produits — seul l'abonnement à
chaque produit peut différer.</i>
"""


class TestApiKeysTask(QgsTask):
    """Teste la clé API sur les trois produits, séquentiellement, en tâche de fond."""

    def __init__(self, api_key: str):
        super().__init__("Configuration - Test de la clé API", QgsTask.Flag.CanCancel)
        self._api_key = api_key
        self.results: list[ConfigTestResult] | None = None

    def run(self) -> bool:
        self.results = test_all(self._api_key)
        return True  # la tâche elle-même réussit toujours ; le détail est dans results


class ConfigTabWidget(QWidget):
    """Contenu de l'onglet Configuration."""

    def __init__(self, iface: QgisInterface, on_unlock_changed: Callable[[bool], None], parent=None):
        super().__init__(parent)
        self.iface = iface
        self._on_unlock_changed = on_unlock_changed
        self._status_labels: dict[str, QLabel] = {}
        self._build_ui()

        # Test automatique et silencieux au démarrage si une clé est déjà enregistrée
        if get_api_key():
            self._run_test()

    # -- construction de l'interface -----------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        instructions = QLabel(INSTRUCTIONS_HTML)
        instructions.setWordWrap(True)
        instructions.setOpenExternalLinks(True)
        instructions.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(instructions)

        key_group = QGroupBox("Clé API")
        key_form = QFormLayout()
        self.lineEdit_apikey = QLineEdit()
        self.lineEdit_apikey.setEchoMode(QLineEdit.EchoMode.Password)
        self.lineEdit_apikey.setPlaceholderText("Clé API Météo-France (portail-api)")
        self.lineEdit_apikey.setText(get_api_key())
        key_form.addRow("Clé API :", self.lineEdit_apikey)
        key_group.setLayout(key_form)
        layout.addWidget(key_group)

        self.button_test = QPushButton("Tester la clé")
        self.button_test.clicked.connect(self._on_test_clicked)
        layout.addWidget(self.button_test)

        status_group = QGroupBox("État des abonnements")
        status_layout = QVBoxLayout()
        for label in ("AROME", "Radar", "Observations"):
            lbl = QLabel(f"{label} : non testé")
            lbl.setWordWrap(True)  # messages de test parfois longs (cf. erreurs HTTP détaillées)
            self._status_labels[label] = lbl
            status_layout.addWidget(lbl)
        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        self.label_global = QLabel("")
        self.label_global.setWordWrap(True)
        layout.addWidget(self.label_global)

        layout.addStretch()
        self.setLayout(layout)

    # -- test de la clé ---------------------------------------------------

    def _on_test_clicked(self) -> None:
        api_key = self.lineEdit_apikey.text().strip()
        if not api_key:
            QMessageBox.warning(self, "Clé manquante", "Merci de coller une clé API avant de tester.")
            return
        set_api_key(api_key)
        self._run_test()

    def _run_test(self) -> None:
        api_key = get_api_key()
        self.button_test.setEnabled(False)
        for label, lbl in self._status_labels.items():
            lbl.setText(f"{label} : test en cours...")

        task = TestApiKeysTask(api_key)
        task.taskCompleted.connect(lambda: self._on_test_finished(task))
        task.taskTerminated.connect(lambda: self._on_test_finished(task))
        QgsApplication.taskManager().addTask(task)

    def _on_test_finished(self, task: TestApiKeysTask) -> None:
        self.button_test.setEnabled(True)
        results = task.results or []

        all_ok = bool(results)
        for result in results:
            lbl = self._status_labels.get(result.label)
            if lbl is None:
                continue
            if result.success is True:
                lbl.setText(f"{result.label} : \u2705 {result.message}")
            elif result.success is False:
                lbl.setText(f"{result.label} : \u274c {result.message}")
                all_ok = False
            else:
                lbl.setText(f"{result.label} : \u26a0\ufe0f {result.message}")
                # Statut indéterminé : ne bloque pas le déblocage à lui seul.

        if all_ok:
            self.label_global.setText(
                "Vérifications passées : les onglets AROME, Radar et Observations sont débloqués."
            )
        else:
            self.label_global.setText(
                "Au moins un produit n'a pas pu être confirmé — vérifie l'abonnement "
                "correspondant sur le portail, puis reteste."
            )

        self._on_unlock_changed(all_ok)

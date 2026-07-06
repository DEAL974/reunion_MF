# -*- coding: utf-8 -*-
"""
config_core.py
===============
Tests de validité de la clé API Météo-France, par produit.

Rappel technique : la clé (Application) est UNIQUE pour les trois
produits — c'est l'abonnement à chaque produit, pas la clé elle-même,
qui peut différer. D'où la nécessité de tester chaque endpoint
séparément avec la même clé, plutôt qu'un test global unique.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .arome_core import AromeAPIClient, AromeCoreError
from .http_utils import urlopen_https
from .observations_core import DEFAULT_DEPARTEMENT, ObservationsAPIClient, ObservationsCoreError
from .radar_core import RADAR_API_URL


@dataclass
class ConfigTestResult:
    label: str
    success: Optional[bool]  # True/False, ou None si indéterminé (non testable)
    message: str


def test_arome(api_key: str) -> ConfigTestResult:
    """Test léger : liste des paquets disponibles (catalogue JSON, pas de téléchargement de données)."""
    try:
        client = AromeAPIClient(api_key=api_key)
        packages = client.list_packages()
        return ConfigTestResult("AROME", True, f"{len(packages)} paquet(s) disponible(s).")
    except AromeCoreError as exc:
        return ConfigTestResult("AROME", False, str(exc))
    except Exception as exc:
        return ConfigTestResult("AROME", False, f"Erreur inattendue : {exc}")


def test_radar(api_key: str, timeout: int = 15) -> ConfigTestResult:
    """
    Pas d'endpoint catalogue léger pour le Radar (seul point d'accès :
    téléchargement du paquet complet, ~9 Mo). On tente une requête HEAD
    pour vérifier l'authentification sans télécharger le corps de la
    réponse ; si le serveur ne supporte pas HEAD, le résultat est
    indéterminé plutôt qu'un faux échec.
    """
    if not api_key:
        return ConfigTestResult("Radar", False, "Clé API manquante.")

    request = urllib.request.Request(RADAR_API_URL, headers={"apikey": api_key}, method="HEAD")
    try:
        with urlopen_https(request, timeout=timeout) as response:
            return ConfigTestResult("Radar", True, f"Accès confirmé (HTTP {response.status}).")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return ConfigTestResult("Radar", False, f"Accès refusé (HTTP {exc.code}) — abonnement manquant ?")
        if exc.code in (405, 501):
            return ConfigTestResult(
                "Radar", None,
                "Vérification légère indisponible pour cette API (HEAD non supporté par le "
                "serveur) — teste directement depuis l'onglet Radar pour confirmer."
            )
        return ConfigTestResult("Radar", False, f"Erreur HTTP {exc.code}.")
    except urllib.error.URLError as exc:
        return ConfigTestResult("Radar", False, f"Erreur réseau : {exc.reason}")


def test_observations(api_key: str) -> ConfigTestResult:
    """Test réel mais léger : téléchargement des observations d'un département (GeoJSON, quelques centaines de Ko)."""
    try:
        client = ObservationsAPIClient(api_key=api_key)
        features = client.download_department_hourly(DEFAULT_DEPARTEMENT)
        return ConfigTestResult("Observations", True, f"{len(features)} mesure(s) trouvée(s).")
    except ObservationsCoreError as exc:
        return ConfigTestResult("Observations", False, str(exc))
    except Exception as exc:
        return ConfigTestResult("Observations", False, f"Erreur inattendue : {exc}")


def test_all(api_key: str) -> list[ConfigTestResult]:
    """Teste les trois produits avec la même clé, séquentiellement."""
    return [test_arome(api_key), test_radar(api_key), test_observations(api_key)]

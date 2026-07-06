# -*- coding: utf-8 -*-
"""
observations_core.py
=====================
Moteur headless pour le module Observations du plugin "Réunion MF".

Rôle : télécharger les observations horaires (24h glissantes) d'un
département depuis l'API Paquet Observations de Météo-France, convertir
les unités vers des valeurs directement lisibles, et écrire un GeoJSON
local prêt à être chargé comme couche vectorielle QGIS.

Aucune dépendance GDAL/numpy : ce module est le plus léger du plugin
(uniquement stdlib : json, urllib, pathlib).

Source : https://public-api.meteofrance.fr/public/DPPaquetObs
Format  : GeoJSON, une feature par station ET par heure (pas empilement
          dans une seule feature) — confirmé par inspection réelle.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .http_utils import urlopen_https

API_BASE_URL = "https://public-api.meteofrance.fr/public/DPPaquetObs/v2"
DEFAULT_DEPARTEMENT = "974"  # La Réunion


class ObservationsCoreError(Exception):
    """Erreur métier du moteur Observations (auth, réseau, format inattendu)."""


# ---------------------------------------------------------------------------
# Sémantique des paramètres (codes SYNOP Météo-France)
# ---------------------------------------------------------------------------

# label_fr / unité AFFICHÉE (après conversion) pour chaque champ du GeoJSON.
OBS_PARAMS_MAP = {
    "t": {"label_fr": "Température", "unite": "°C"},
    "td": {"label_fr": "Point de rosée", "unite": "°C"},
    "tx": {"label_fr": "Température maximale", "unite": "°C"},
    "tn": {"label_fr": "Température minimale", "unite": "°C"},
    "u": {"label_fr": "Humidité relative", "unite": "%"},
    "ux": {"label_fr": "Humidité maximale", "unite": "%"},
    "un": {"label_fr": "Humidité minimale", "unite": "%"},
    "dd": {"label_fr": "Direction du vent moyen", "unite": "deg"},
    "ff": {"label_fr": "Vitesse du vent moyen", "unite": "m/s"},
    "dxy": {"label_fr": "Direction rafales", "unite": "deg"},
    "fxy": {"label_fr": "Vitesse rafales (période)", "unite": "m/s"},
    "ddraf": {"label_fr": "Direction rafale max", "unite": "deg"},
    "raf": {"label_fr": "Rafale maximale", "unite": "m/s"},
    "rr1": {"label_fr": "Précipitations (1h)", "unite": "mm"},
    "t_10": {"label_fr": "Température sol -10cm", "unite": "°C"},
    "t_20": {"label_fr": "Température sol -20cm", "unite": "°C"},
    "t_50": {"label_fr": "Température sol -50cm", "unite": "°C"},
    "t_100": {"label_fr": "Température sol -100cm", "unite": "°C"},
    "vv": {"label_fr": "Visibilité", "unite": "m"},
    "etat_sol": {"label_fr": "État du sol (code)", "unite": ""},
    "sss": {"label_fr": "Épaisseur de neige", "unite": "cm"},
    "n": {"label_fr": "Nébulosité totale", "unite": "octas"},
    "insolh": {"label_fr": "Durée d'ensoleillement", "unite": "min"},
    "ray_glo01": {"label_fr": "Rayonnement global (cumul horaire)", "unite": "Wh/m²"},
    "pres": {"label_fr": "Pression station", "unite": "hPa"},
    "pmer": {"label_fr": "Pression mer", "unite": "hPa"},
}

# Conversions : valeur_affichée = valeur_brute * facteur + offset
# t/td/tx/tn confirmés Kelvin par cohérence physique sur données réelles.
# pres/pmer : hypothèse Pa->hPa (convention Météo-France habituelle,
# jamais observée non-nulle sur les échantillons testés — à confirmer
# dès qu'une station avec pression apparaît).
# ray_glo01 : J/m² -> Wh/m² (cohérent avec un ordre de grandeur solaire
# plausible observé sur échantillon réel).
_KELVIN_FIELDS = ("t", "td", "tx", "tn", "t_10", "t_20", "t_50", "t_100")
_PASCAL_FIELDS = ("pres", "pmer")
_JOULE_FIELDS = ("ray_glo01",)


def _convert_value(field: str, value):
    if value is None:
        return None
    if field in _KELVIN_FIELDS:
        return round(value - 273.15, 1)
    if field in _PASCAL_FIELDS:
        return round(value / 100.0, 1)
    if field in _JOULE_FIELDS:
        return round(value / 3600.0, 1)
    return value


# ---------------------------------------------------------------------------
# Client API
# ---------------------------------------------------------------------------

class ObservationsAPIClient:
    """Client minimal pour l'API Paquet Observations de Météo-France (API Key)."""

    def __init__(self, api_key: str, timeout: int = 30):
        if not api_key:
            raise ObservationsCoreError("Clé API Météo-France manquante.")
        self._api_key = api_key
        self._timeout = timeout

    def download_department_hourly(self, id_departement: str = DEFAULT_DEPARTEMENT) -> list[dict]:
        """
        Télécharge les observations horaires (24h glissantes) de toutes
        les stations d'un département. Retourne la liste de features
        GeoJSON brutes (non converties).
        """
        url = (
            f"{API_BASE_URL}/paquet/horaire"
            f"?id-departement={id_departement}&format=geojson"
        )
        request = urllib.request.Request(url, headers={"apikey": self._api_key})
        try:
            with urlopen_https(request, timeout=self._timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise ObservationsCoreError(
                f"Erreur HTTP {exc.code} lors du téléchargement des observations. "
                f"Détail serveur : {detail or '(aucun)'}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ObservationsCoreError(f"Erreur réseau : {exc.reason}") from exc

        try:
            data = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ObservationsCoreError(
                f"Réponse non-JSON reçue (probable erreur API) : {exc}"
            ) from exc

        if isinstance(data, dict) and "code" in data and "message" in data:
            raise ObservationsCoreError(f"Erreur API : {data.get('message')}")

        if not isinstance(data, list):
            raise ObservationsCoreError(
                "Format de réponse inattendu (liste de features attendue)."
            )
        return data


# ---------------------------------------------------------------------------
# Conversion + écriture GeoJSON local
# ---------------------------------------------------------------------------

def convert_features(raw_features: list[dict]) -> list[dict]:
    """Applique les conversions d'unités sur chaque feature GeoJSON."""
    converted = []
    for feature in raw_features:
        new_feature = dict(feature)
        properties = dict(feature.get("properties", {}))
        for field in list(properties.keys()):
            properties[field] = _convert_value(field, properties.get(field))
        new_feature["properties"] = properties
        converted.append(new_feature)
    return converted


def write_geojson(features: list[dict], output_path: Path) -> Path:
    geojson_doc = {"type": "FeatureCollection", "features": features}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(geojson_doc, ensure_ascii=False), encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Cache local
# ---------------------------------------------------------------------------

class ObservationsCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def new_output_path(self, id_departement: str) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return self.cache_dir / f"observations_{id_departement}_{stamp}.geojson"

    def purge_older_than(self, hours: int = 24) -> int:
        cutoff = time.time() - hours * 3600
        removed = 0
        for file_path in self.cache_dir.glob("*.geojson"):
            if file_path.is_file() and file_path.stat().st_mtime < cutoff:
                file_path.unlink()
                removed += 1
        return removed


# ---------------------------------------------------------------------------
# Orchestration haut niveau
# ---------------------------------------------------------------------------

class ObservationsService:
    def __init__(self, api_key: str, cache_dir: Path):
        self.client = ObservationsAPIClient(api_key=api_key)
        self.cache = ObservationsCache(cache_dir=cache_dir)

    def fetch(self, id_departement: str = DEFAULT_DEPARTEMENT) -> Path:
        """
        Télécharge, convertit et écrit les observations 24h du
        département demandé. Retourne le chemin du GeoJSON local prêt à
        être chargé comme couche vectorielle QGIS.
        """
        raw_features = self.client.download_department_hourly(id_departement)
        converted = convert_features(raw_features)
        output_path = self.cache.new_output_path(id_departement)
        return write_geojson(converted, output_path)

    def purge_cache(self, hours: int = 24) -> int:
        return self.cache.purge_older_than(hours=hours)

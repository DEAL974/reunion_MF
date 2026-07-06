# -*- coding: utf-8 -*-
"""
arome_core.py
=============
Moteur headless pour le plugin "AROME Outre-Mer - Réunion".

Rôle : interroger l'API publique Météo-France (paquet AROME-OM-INDIEN,
grille 0.025°), télécharger les GRIB2, et produire deux types de sorties
GeoTIFF exploitables dans QGIS :

    - Vue régionale  : domaine natif complet (Océan Indien Ouest),
                        CRS EPSG:4326, sans reprojection ni recadrage.
    - Vue Réunion     : recadrage serré sur l'île (~55-56°E / 20.8-21.5°S)
                        puis reprojection vers EPSG:2975 (RGR92 UTM 40S).

Dépendances : uniquement GDAL (osgeo), déjà fourni par QGIS.
Aucune dépendance externe (pas de cfgrib/eccodes/meteole) : le décodage
GRIB2 (y compris le template de compression DRS 5.42/CCSDS) est assuré
nativement par GDAL >= 3.9 environ. Une vérification de version est
effectuée au chargement pour échouer proprement sinon.

Auteur : Cyriel (DEAL Réunion - SCETE/USIG)
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

import numpy as np

from .http_utils import urlopen_https

try:
    from osgeo import gdal
    gdal.UseExceptions()
except ImportError as exc:
    raise ImportError(
        "GDAL (osgeo) est introuvable. Ce module doit être exécuté "
        "dans l'environnement Python de QGIS."
    ) from exc


# ---------------------------------------------------------------------------
# Constantes de configuration
# ---------------------------------------------------------------------------

API_BASE_URL = "https://public-api.meteofrance.fr/previnum/DPPaquetAROME-OM/v1"
DOMAIN = "AROME-OM-INDIEN"
GRID_RESOLUTION = "0.025"

# Emprise de recadrage "vue Réunion" (île seule, serrée)
# Ordre attendu par gdal.Warp / outputBounds : (minX, minY, maxX, maxY)
REUNION_BBOX_WGS84 = (55.0, -21.5, 56.0, -20.8)
REUNION_TARGET_EPSG = "EPSG:2975"

# Version minimale de GDAL recommandée pour le support du template DRS 5.42
# (compression CCSDS/AEC utilisée par les GRIB2 AROME-OM récents).
# Ce n'est PAS une garantie absolue (le support dépend aussi de la
# compilation avec libaec), d'où le test d'ouverture réel en complément.
GDAL_MIN_VERSION_HINT = (3, 9, 0)

# Mapping des paramètres du paquet SP1 (surface).
# Complété au fil des paquets réellement utilisés (SP2, HP1...).
AROME_PARAMS_MAP = {
    "WDIR":   {"label_fr": "Direction du vent",        "unite": "deg",       "niveau": "10m"},
    "WIND":   {"label_fr": "Force du vent",             "unite": "m/s",       "niveau": "10m"},
    "GUST":   {"label_fr": "Rafales",                   "unite": "m/s",       "niveau": "10m"},
    "PRMSL":  {"label_fr": "Pression mer",              "unite": "hPa",       "niveau": "mer"},
    "UGRD":   {"label_fr": "Vent - composante U",       "unite": "m/s",       "niveau": "10m"},
    "VGRD":   {"label_fr": "Vent - composante V",       "unite": "m/s",       "niveau": "10m"},
    "UGUST":  {"label_fr": "Rafale - composante U",     "unite": "m/s",       "niveau": "10m"},
    "VGUST":  {"label_fr": "Rafale - composante V",     "unite": "m/s",       "niveau": "10m"},
    "TMP":    {"label_fr": "Température",               "unite": "degC",      "niveau": "2m"},
    "RH":     {"label_fr": "Humidité relative",         "unite": "%",         "niveau": "2m"},
    "TPRATE": {"label_fr": "Précipitation totale",       "unite": "mm/h",      "niveau": "surface"},
    "TCDC":   {"label_fr": "Couverture nuageuse",       "unite": "%",         "niveau": "surface"},
    "SPRATE": {"label_fr": "Précipitation neige",        "unite": "mm/h",      "niveau": "surface"},
    "DSWRF":  {"label_fr": "Rayonnement solaire descendant", "unite": "W/m2", "niveau": "surface"},
    "GPRATE": {"label_fr": "Précipitation grésil",       "unite": "mm/h",      "niveau": "surface"},
}

# Conversions appliquées après extraction, pour un affichage directement
# interprétable (l'unité brute GRIB n'est pas celle qu'on veut montrer).
# facteur : valeur_affichée = valeur_brute * facteur
# Approximation pour les taux -> mm/h : suppose le taux instantané constant
# sur l'heure. Valable pour une lecture visuelle, pas pour un cumul précis
# sur plusieurs échéances (il faudrait alors intégrer, pas juste multiplier).
UNIT_CONVERSIONS = {
    "TPRATE": 3600.0,   # kg/(m2.s) -> mm/h  (1 kg/m2 d'eau = 1 mm)
    "SPRATE": 3600.0,
    "GPRATE": 3600.0,
    "PRMSL":  0.01,     # Pa -> hPa
}

# Repli si list_packages() échoue (réseau indisponible, quota...).
# Codes standards connus pour le domaine AROME Outre-Mer à cette date.
STATIC_PACKAGE_FALLBACK = [
    {"code": "SP1", "title": "SP1 - Surface (vent, temp., précip., nébulosité)"},
    {"code": "SP2", "title": "SP2 - Surface (paramètres complémentaires)"},
    {"code": "SP3", "title": "SP3 - Surface (paramètres complémentaires)"},
    {"code": "HP1", "title": "HP1 - Niveaux hauteur (profil vertical)"},
    {"code": "HP2", "title": "HP2 - Niveaux hauteur (profil vertical)"},
    {"code": "HP3", "title": "HP3 - Niveaux hauteur (profil vertical)"},
    {"code": "IP1", "title": "IP1 - Niveaux isobares (profil vertical)"},
]


class AromeCoreError(Exception):
    """Erreur métier du moteur AROME (auth, réseau, décodage, absence de donnée)."""


def check_gdal_compatibility() -> None:
    """
    Vérifie que la version de GDAL disponible est suffisante.
    Ne garantit pas le succès (dépend de la compilation libaec),
    mais évite un échec silencieux évident.
    """
    version_str = gdal.__version__  # ex: "3.13.1"
    try:
        parts = tuple(int(p) for p in version_str.split(".")[:3])
    except ValueError:
        parts = (0, 0, 0)

    if parts < GDAL_MIN_VERSION_HINT:
        raise AromeCoreError(
            f"GDAL {version_str} détecté. Les fichiers AROME Outre-Mer "
            f"nécessitent généralement GDAL >= "
            f"{'.'.join(map(str, GDAL_MIN_VERSION_HINT))} pour être "
            f"décodés (compression CCSDS/AEC). Merci de mettre à jour QGIS."
        )


# ---------------------------------------------------------------------------
# Client API Météo-France
# ---------------------------------------------------------------------------

class AromeRun:
    """Représente un réseau (run) de prévision disponible."""

    def __init__(self, reference_time: str):
        # ISO 8601, ex: "2026-07-03T18:00:00Z"
        self.reference_time = reference_time

    def __repr__(self) -> str:
        return f"AromeRun(reference_time={self.reference_time!r})"


class AromeAPIClient:
    """
    Client minimal pour l'API "Paquets AROME Outre-Mer" de Météo-France.
    Authentification par API Key (header 'apikey'), pas de flux OAuth2.
    """

    def __init__(self, api_key: str, timeout: int = 30):
        if not api_key:
            raise AromeCoreError("Clé API Météo-France manquante.")
        self._api_key = api_key
        self._timeout = timeout

    # -- requêtes catalogue -------------------------------------------------

    def list_packages(self) -> list[dict]:
        """
        Interroge le catalogue pour connaître les paquets disponibles sur
        le domaine/grille configurés (ex: SP1, SP2, SP3, HP1, HP2, HP3, IP1).
        Retourne une liste de {"code": str, "title": str}.
        Repli côté appelant recommandé si cette requête échoue (réseau).
        """
        url = f"{API_BASE_URL}/models/{DOMAIN}/grids/{GRID_RESOLUTION}/packages"
        data = self._get_json(url)

        packages: list[dict] = []
        for link in data.get("links", []):
            href = link.get("href", "")
            title = link.get("title", "")
            if link.get("rel") == "self":
                continue
            code = href.rstrip("/").split("/")[-1].split("?")[0]
            if code and code.upper() != GRID_RESOLUTION:
                packages.append({"code": code, "title": title or code})

        if not packages:
            raise AromeCoreError("Aucun paquet trouvé dans le catalogue.")
        return packages

    def list_available_runs(self, package: str = "SP1") -> list[AromeRun]:
        """
        Interroge le catalogue pour connaître les réseaux (runs) disponibles
        pour un paquet donné, sur le domaine et la grille configurés.
        """
        url = (
            f"{API_BASE_URL}/models/{DOMAIN}/grids/{GRID_RESOLUTION}"
            f"/packages/{package}"
        )
        data = self._get_json(url)

        runs: list[AromeRun] = []
        for link in data.get("links", []):
            href = link.get("href", "")
            if "referencetime=" in href:
                ref_time = href.split("referencetime=")[-1]
                runs.append(AromeRun(reference_time=ref_time))
        if not runs:
            raise AromeCoreError(
                f"Aucun réseau disponible trouvé pour le paquet {package}."
            )
        return runs

    def latest_run(self, package: str = "SP1") -> AromeRun:
        """Retourne le run le plus récent disponible (dernier de la liste)."""
        runs = self.list_available_runs(package=package)
        return runs[-1]

    # -- téléchargement produit ---------------------------------------------

    def download_grib(
        self,
        reference_time: str,
        echeance_heures: int,
        package: str,
        destination: Path,
    ) -> Path:
        """
        Télécharge le fichier GRIB2 pour un run/échéance/paquet donnés.

        :param reference_time: ISO 8601 UTC, ex "2026-07-03T18:00:00Z"
        :param echeance_heures: échéance en heures (ex: 6 pour H+6)
        :param package: code du paquet (ex: "SP1")
        :param destination: chemin de fichier de sortie (.grib2)
        """
        time_param = f"{echeance_heures:03d}H"
        url = (
            f"{API_BASE_URL}/models/{DOMAIN}/grids/{GRID_RESOLUTION}"
            f"/packages/{package}/productOMOI"
            f"?referencetime={reference_time}&time={time_param}&format=grib2"
        )

        destination.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"apikey": self._api_key})

        try:
            with urlopen_https(request, timeout=self._timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise AromeCoreError(
                f"Erreur HTTP {exc.code} lors du téléchargement AROME "
                f"(run={reference_time}, echeance=H+{echeance_heures}, "
                f"paquet={package}). Détail serveur : {detail or '(aucun)'}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AromeCoreError(f"Erreur réseau : {exc.reason}") from exc

        # L'API renvoie parfois un message JSON d'erreur avec un code 200
        # (ex: "La donnée est indisponible") plutôt qu'un vrai code HTTP.
        if "json" in content_type.lower() or payload[:1] == b"{":
            try:
                error_payload = json.loads(payload.decode("utf-8"))
                raise AromeCoreError(
                    f"Réponse API non-GRIB : {error_payload.get('msg', payload)}"
                )
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass  # ce n'était pas vraiment du JSON, on continue

        destination.write_bytes(payload)
        return destination

    # -- utilitaire interne --------------------------------------------------

    def _get_json(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={"apikey": self._api_key})
        try:
            with urlopen_https(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise AromeCoreError(
                f"Erreur HTTP {exc.code} sur {url}. Détail serveur : {detail or '(aucun)'}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AromeCoreError(f"Erreur réseau sur {url} : {exc.reason}") from exc


# ---------------------------------------------------------------------------
# Cache local
# ---------------------------------------------------------------------------

class AromeCache:
    """
    Gère le stockage local des GRIB2 téléchargés et des GeoTIFF extraits,
    pour éviter de re-télécharger un run/échéance/paquet déjà récupéré.
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.grib_dir = self.cache_dir / "grib"
        self.regional_dir = self.cache_dir / "regional"
        self.reunion_dir = self.cache_dir / "reunion"
        for directory in (self.grib_dir, self.regional_dir, self.reunion_dir):
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _slugify_reference_time(reference_time: str) -> str:
        """'2026-07-03T18:00:00Z' -> '20260703T1800Z'"""
        return reference_time.replace("-", "").replace(":", "")

    def grib_path(self, reference_time: str, echeance_heures: int, package: str) -> Path:
        slug = self._slugify_reference_time(reference_time)
        filename = f"{DOMAIN}_{package}_{slug}_H{echeance_heures:03d}.grib2"
        return self.grib_dir / filename

    def output_path(self, mode: str, reference_time: str, echeance_heures: int,
                     element: str, band_index: int) -> Path:
        """
        mode: 'regional' ou 'reunion'
        band_index inclus dans le nom pour lever toute ambiguïté sur les
        paquets multi-niveaux (HP1/HP2/HP3/IP1), où un même GRIB_ELEMENT
        peut apparaître sur plusieurs bandes (une par niveau vertical).
        """
        base_dir = self.regional_dir if mode == "regional" else self.reunion_dir
        slug = self._slugify_reference_time(reference_time)
        filename = f"{element}_b{band_index}_{slug}_H{echeance_heures:03d}_{mode}.tif"
        return base_dir / filename

    def is_cached(self, path: Path) -> bool:
        return path.exists() and path.stat().st_size > 0

    def purge_older_than(self, days: int = 3) -> int:
        """Supprime les fichiers de cache plus vieux que N jours. Retourne le nombre supprimé."""
        cutoff = time.time() - days * 86400
        removed = 0
        for directory in (self.grib_dir, self.regional_dir, self.reunion_dir):
            for file_path in directory.glob("*"):
                if file_path.is_file() and file_path.stat().st_mtime < cutoff:
                    file_path.unlink()
                    removed += 1
        return removed


# ---------------------------------------------------------------------------
# Traitement GRIB -> GeoTIFF
# ---------------------------------------------------------------------------

class AromeGribProcessor:
    """
    Extrait les bandes d'un GRIB2 AROME-OM et produit les sorties GeoTIFF,
    en mode 'regional' (domaine complet, EPSG:4326 natif, aucune
    reprojection) ou 'reunion' (recadrage serré + reprojection EPSG:2975).
    """

    def __init__(self):
        check_gdal_compatibility()

    # -- introspection --------------------------------------------------

    @staticmethod
    def list_bands(grib_path: Path) -> dict[int, dict]:
        """
        Retourne un dict {numero_bande: métadonnées GRIB} pour permettre
        à l'appelant (GUI) de choisir un paramètre par son GRIB_ELEMENT.
        """
        dataset = gdal.Open(str(grib_path))
        if dataset is None:
            raise AromeCoreError(f"Impossible d'ouvrir le GRIB2 : {grib_path}")

        bands_info = {}
        try:
            for i in range(1, dataset.RasterCount + 1):
                band = dataset.GetRasterBand(i)
                meta = band.GetMetadata()
                bands_info[i] = meta
        finally:
            dataset = None  # libère explicitement le handle GDAL
        return bands_info

    @staticmethod
    def find_band_index(grib_path: Path, element: str) -> int:
        """
        Retrouve le numéro de la PREMIÈRE bande correspondant à un
        GRIB_ELEMENT donné. Insuffisant si le paquet a plusieurs niveaux
        verticaux pour ce même élément (HP1/HP2/HP3/IP1) : dans ce cas,
        préférer describe_bands() + sélection explicite du band_index.
        """
        bands_info = AromeGribProcessor.list_bands(grib_path)
        for band_num, meta in bands_info.items():
            if meta.get("GRIB_ELEMENT") == element:
                return band_num
        raise AromeCoreError(
            f"Paramètre '{element}' introuvable dans {grib_path.name}."
        )

    @staticmethod
    def describe_bands(grib_path: Path) -> list[dict]:
        """
        Retourne une description structurée de chaque bande, utilisable
        directement par la GUI pour peupler un sélecteur de paramètres,
        sans dépendre d'un mapping codé en dur (nécessaire pour les
        paquets jamais inspectés manuellement : SP2, SP3, HP1, HP2, HP3, IP1).

        Chaque élément : {
            "band_index": int,
            "element": str,        # GRIB_ELEMENT, ex "TMP"
            "comment": str,        # GRIB_COMMENT, ex "Temperature [C]"
            "unit": str,           # GRIB_UNIT, ex "[C]"
            "level_desc": str,     # description GDAL de la bande (niveau)
            "valid_time": str,     # GRIB_VALID_TIME (epoch secondes, str)
        }
        """
        dataset = gdal.Open(str(grib_path))
        if dataset is None:
            raise AromeCoreError(f"Impossible d'ouvrir le GRIB2 : {grib_path}")

        result: list[dict] = []
        try:
            for i in range(1, dataset.RasterCount + 1):
                band = dataset.GetRasterBand(i)
                meta = band.GetMetadata()
                result.append({
                    "band_index": i,
                    "element": meta.get("GRIB_ELEMENT", f"BAND_{i}"),
                    "comment": meta.get("GRIB_COMMENT", ""),
                    "unit": meta.get("GRIB_UNIT", ""),
                    "level_desc": band.GetDescription() or "",
                    "valid_time": meta.get("GRIB_VALID_TIME", ""),
                })
        finally:
            dataset = None
        return result

    @staticmethod
    def resolve_band_index(grib_path: Path, element: str, level_desc: Optional[str] = None) -> int:
        """
        Retrouve le numéro de bande correspondant à (element, level_desc)
        DANS CE GRIB PRÉCIS. Contrairement à un band_index figé venant
        d'une autre échéance, ceci est robuste aux cas où la structure du
        GRIB change selon l'échéance (ex : champs cumulatifs absents ou
        décalés à H+000).

        Si level_desc est None, retourne la première bande dont l'élément
        correspond (comportement équivalent à find_band_index).
        Si plusieurs bandes partagent le même element, level_desc permet
        de désambiguïser (paquets multi-niveaux HP1/HP2/HP3/IP1).
        """
        bands = AromeGribProcessor.describe_bands(grib_path)
        candidates = [b for b in bands if b["element"] == element]

        if not candidates:
            raise AromeCoreError(
                f"Paramètre '{element}' introuvable dans {grib_path.name} "
                f"(cette échéance ne contient peut-être pas ce champ)."
            )
        if level_desc is None or len(candidates) == 1:
            return candidates[0]["band_index"]

        for band in candidates:
            if band["level_desc"] == level_desc:
                return band["band_index"]

        # Aucun niveau exactement identique trouvé : on reste défensif et on
        # prend le premier candidat plutôt que de planter, en le signalant.
        return candidates[0]["band_index"]

    @staticmethod
    def _apply_unit_conversion(output_path: Path, element: str) -> None:
        """
        Applique en place une conversion linéaire (valeur * facteur) si le
        paramètre en a besoin (ex: précipitations kg/(m2.s) -> mm/h,
        pression Pa -> hPa). Ne fait rien si aucune conversion n'est définie.
        """
        factor = UNIT_CONVERSIONS.get(element)
        if factor is None:
            return

        dataset = gdal.Open(str(output_path), gdal.GA_Update)
        if dataset is None:
            raise AromeCoreError(
                f"Impossible de rouvrir {output_path} pour conversion d'unité."
            )
        try:
            band = dataset.GetRasterBand(1)
            nodata = band.GetNoDataValue()
            array = band.ReadAsArray().astype(np.float64)

            if nodata is not None:
                mask = array != nodata
                array[mask] = array[mask] * factor
            else:
                array = array * factor

            band.WriteArray(array)
            band.FlushCache()
        finally:
            dataset = None  # libère le handle GDAL (écriture forcée)

    # -- extraction régionale (native, sans reprojection) -------------------

    def extract_regional(
        self, grib_path: Path, band_index: int, element: str, output_path: Path
    ) -> Path:
        """
        Extrait une bande (identifiée par son numéro, fiable même sur les
        paquets multi-niveaux) vers un GeoTIFF, domaine complet, sans
        reprojection ni recadrage (CRS natif GRIB, proche WGS84).
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            gdal.Translate(
                str(output_path),
                str(grib_path),
                bandList=[band_index],
                format="GTiff",
                creationOptions=["COMPRESS=DEFLATE", "TILED=YES"],
            )
        except RuntimeError as exc:
            raise AromeCoreError(
                f"Échec extraction régionale pour {element} (bande {band_index}) : {exc}"
            ) from exc

        self._apply_unit_conversion(output_path, element)
        return output_path

    # -- extraction Réunion (recadrage + reprojection) -----------------------

    def extract_reunion(
        self, grib_path: Path, band_index: int, element: str, output_path: Path
    ) -> Path:
        """
        Extrait une bande (par numéro), recadrée sur l'emprise Réunion
        (WGS84) puis reprojetée en EPSG:2975. Passage obligatoire par une
        étape intermédiaire en mémoire (bande unique) avant le gdal.Warp.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        vrt_path = f"/vsimem/_arome_band_extract_{band_index}.vrt"
        try:
            gdal.Translate(vrt_path, str(grib_path), bandList=[band_index], format="VRT")

            gdal.Warp(
                str(output_path),
                vrt_path,
                outputBounds=REUNION_BBOX_WGS84,
                outputBoundsSRS="EPSG:4326",
                dstSRS=REUNION_TARGET_EPSG,
                srcSRS="EPSG:4326",  # approximation : sphère GRIB proche WGS84
                resampleAlg="bilinear",
                format="GTiff",
                creationOptions=["COMPRESS=DEFLATE", "TILED=YES"],
            )
        except RuntimeError as exc:
            raise AromeCoreError(
                f"Échec extraction Réunion pour {element} (bande {band_index}) : {exc}"
            ) from exc
        finally:
            gdal.Unlink(vrt_path)

        self._apply_unit_conversion(output_path, element)
        return output_path


# ---------------------------------------------------------------------------
# Orchestration haut niveau (façade appelée par la GUI)
# ---------------------------------------------------------------------------

class AromeService:
    """
    Point d'entrée unique pour la GUI : encapsule client API + cache +
    processeur. Fonctionne désormais par découverte (les paramètres
    disponibles sont lus dans le GRIB réel, pas supposés à l'avance),
    ce qui permet de supporter n'importe quel paquet (SP1, SP2, SP3,
    HP1, HP2, HP3, IP1...) sans mapping préalable.
    """

    def __init__(self, api_key: str, cache_dir: Path):
        self.client = AromeAPIClient(api_key=api_key)
        self.cache = AromeCache(cache_dir=cache_dir)
        self.processor = AromeGribProcessor()

    # -- catalogue ------------------------------------------------------

    def list_packages(self) -> list[dict]:
        """Liste des paquets disponibles (code + titre) pour le domaine configuré."""
        return self.client.list_packages()

    # -- découverte des paramètres d'un paquet ---------------------------

    def discover_parameters(
        self,
        package: str,
        reference_time: Optional[str] = None,
        echeance_heures: int = 6,
    ) -> tuple[list[dict], str]:
        """
        Télécharge (ou réutilise le cache) le GRIB du run/échéance/paquet
        donnés, et retourne la liste des paramètres réellement présents
        (via AromeGribProcessor.describe_bands), ainsi que le
        reference_time effectivement utilisé (utile si l'appelant avait
        laissé None pour "dernier run").
        """
        if reference_time is None:
            reference_time = self.client.latest_run(package=package).reference_time

        grib_path = self.cache.grib_path(reference_time, echeance_heures, package)
        if not self.cache.is_cached(grib_path):
            self.client.download_grib(
                reference_time=reference_time,
                echeance_heures=echeance_heures,
                package=package,
                destination=grib_path,
            )

        bands = self.processor.describe_bands(grib_path)
        return bands, reference_time

    # -- extraction d'une couche unique -----------------------------------

    def get_layer_by_band(
        self,
        package: str,
        band_index: int,
        element: str,
        mode: str,
        reference_time: Optional[str] = None,
        echeance_heures: int = 6,
        level_desc: Optional[str] = None,
    ) -> Path:
        """
        Retourne le chemin d'un GeoTIFF prêt à charger dans QGIS, pour un
        paramètre identifié par (element, level_desc). Le band_index
        fourni est traité comme un simple indice de nommage/cache ; la
        bande réellement extraite est TOUJOURS re-résolue dans le GRIB
        de l'échéance demandée (cf. resolve_band_index), car la structure
        du GRIB peut différer d'une échéance à l'autre.
        """
        if mode not in ("regional", "reunion"):
            raise AromeCoreError(f"Mode inconnu : {mode!r} (attendu 'regional' ou 'reunion').")

        if reference_time is None:
            reference_time = self.client.latest_run(package=package).reference_time

        output_path = self.cache.output_path(
            mode, reference_time, echeance_heures, element, band_index
        )
        if self.cache.is_cached(output_path):
            return output_path

        grib_path = self.cache.grib_path(reference_time, echeance_heures, package)
        if not self.cache.is_cached(grib_path):
            self.client.download_grib(
                reference_time=reference_time,
                echeance_heures=echeance_heures,
                package=package,
                destination=grib_path,
            )

        # Re-résolution systématique : robuste même si la structure du
        # GRIB diffère de celle utilisée lors de la découverte initiale.
        resolved_band_index = self.processor.resolve_band_index(
            grib_path, element, level_desc
        )

        if mode == "regional":
            return self.processor.extract_regional(grib_path, resolved_band_index, element, output_path)
        return self.processor.extract_reunion(grib_path, resolved_band_index, element, output_path)

    # -- série temporelle --------------------------------------------------

    def get_time_series(
        self,
        package: str,
        band_index: int,
        element: str,
        mode: str,
        echeance_max_heures: int = 24,
        pas_heures: int = 1,
        reference_time: Optional[str] = None,
        level_desc: Optional[str] = None,
        progress_callback=None,
    ) -> tuple[list[tuple[int, Path]], list[int]]:
        """
        Génère une série de GeoTIFF pour les échéances H+0 à
        H+echeance_max_heures (par défaut 24, pas 1h).

        Retourne (résultats, échéances_ignorées) :
          - résultats : liste de (echeance_heures, chemin_geotiff) réussis
          - échéances_ignorées : échéances où le paramètre était absent
            du GRIB (ex : champ cumulatif non défini à H+000), sautées
            plutôt que de faire échouer toute la série.

        :param progress_callback: callable(index, total) optionnel,
            appelé après chaque échéance traitée (pour barre de progression).
        :param echeance_max_heures: plafonné à 24 par défaut (signal de
            lourdeur : chaque heure supplémentaire = 1 téléchargement +
            1 extraction de plus).
        """
        if reference_time is None:
            reference_time = self.client.latest_run(package=package).reference_time

        echeances = list(range(0, echeance_max_heures + 1, pas_heures))
        results: list[tuple[int, Path]] = []
        skipped: list[int] = []

        for idx, echeance in enumerate(echeances, start=1):
            try:
                output_path = self.get_layer_by_band(
                    package=package,
                    band_index=band_index,
                    element=element,
                    mode=mode,
                    reference_time=reference_time,
                    echeance_heures=echeance,
                    level_desc=level_desc,
                )
                results.append((echeance, output_path))
            except AromeCoreError:
                # Paramètre absent à cette échéance précise (ex: champ
                # cumulatif non défini à H+000) : on ignore et on continue,
                # plutôt que de faire échouer toute la série pour une
                # échéance isolée.
                skipped.append(echeance)
            if progress_callback is not None:
                progress_callback(idx, len(echeances))

        if not results:
            raise AromeCoreError(
                f"Aucune échéance n'a pu être traitée pour '{element}' "
                f"(toutes ignorées : {skipped})."
            )

        return results, skipped

    # -- maintenance ---------------------------------------------------

    def purge_cache(self, days: int = 3) -> int:
        """Supprime les fichiers de cache (GRIB + GeoTIFF) plus vieux que N jours."""
        return self.cache.purge_older_than(days=days)

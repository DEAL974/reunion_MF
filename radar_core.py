# -*- coding: utf-8 -*-
"""
radar_core.py
=============
Moteur headless pour le module Radar du plugin "Réunion MF".

Rôle : télécharger le paquet mosaïque radar Météo-France (dernier quart
d'heure, métropole + Outre-mer), en extraire les fichiers Réunion
(format ODIM_H5), et produire des GeoTIFF exploitables dans QGIS.

Source de données : https://public-api.meteofrance.fr/public/DPPaquetRadar
Fichier ciblé      : T_IPRE20_C_LFPW_<AAAAMMJJHHMMSS>.h5 (mosaïque Réunion,
                      radars Piton Villers + Colorado, résolution 500 m)
Quantité            : ACRR (cumul de précipitation sur 5 min), gain=0.01

Contrainte connue de l'API : rétention de 20h glissantes, pas d'archive.
"remonter dans le temps" signifie donc constituer un historique local au
fil des rafraîchissements successifs, pas interroger un passé non capturé.

Dépendances : uniquement GDAL (osgeo) + numpy, déjà fournis par QGIS.
"""

from __future__ import annotations

import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    from osgeo import gdal
    gdal.UseExceptions()
except ImportError as exc:
    raise ImportError(
        "GDAL (osgeo) est introuvable. Ce module doit être exécuté "
        "dans l'environnement Python de QGIS."
    ) from exc


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

RADAR_API_URL = "https://public-api.meteofrance.fr/public/DPPaquetRadar/v1/mosaique/paquet"
REUNION_FILE_PREFIX = "T_IPRE20_C_LFPW_"
RADAR_RETENTION_HOURS = 20  # rétention réelle de l'API (pas d'archive au-delà)

# Sous-dataset ODIM_H5 contenant la donnée de précipitation (ACRR)
_SUBDATASET_TEMPLATE = 'HDF5:"{path}"://dataset1/data1/data'

# Clés de métadonnées ODIM_H5 exposées par GDAL sur le sous-dataset
_META_GAIN = "dataset1_data1_what_gain"
_META_OFFSET = "dataset1_data1_what_offset"
_META_NODATA = "dataset1_data1_what_nodata"
_META_UNDETECT = "dataset1_data1_what_undetect"

OUTPUT_NODATA_VALUE = -9999.0


class RadarCoreError(Exception):
    """Erreur métier du moteur Radar (auth, réseau, archive, décodage)."""


# ---------------------------------------------------------------------------
# Client API
# ---------------------------------------------------------------------------

class RadarAPIClient:
    """Client minimal pour l'API Paquet Radar de Météo-France (API Key)."""

    def __init__(self, api_key: str, timeout: int = 60):
        if not api_key:
            raise RadarCoreError("Clé API Météo-France manquante.")
        self._api_key = api_key
        self._timeout = timeout

    def download_package(self, destination: Path) -> Path:
        """
        Télécharge le paquet du dernier quart d'heure (toutes zones,
        métropole + Outre-mer confondues) vers `destination` (.tar.gz).
        """
        request = urllib.request.Request(RADAR_API_URL, headers={"apikey": self._api_key})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise RadarCoreError(
                f"Erreur HTTP {exc.code} lors du téléchargement du paquet radar. "
                f"Détail serveur : {detail or '(aucun)'}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RadarCoreError(f"Erreur réseau : {exc.reason}") from exc

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return destination


# ---------------------------------------------------------------------------
# Extraction de l'archive
# ---------------------------------------------------------------------------

def extract_reunion_files(archive_path: Path, extract_dir: Path) -> list[Path]:
    """
    Extrait uniquement les fichiers Réunion (préfixe T_IPRE20_C_LFPW_) du
    paquet radar téléchargé, ignore le reste (métropole, Antilles, etc.)
    """
    extract_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []

    try:
        with tarfile.open(archive_path, mode="r:gz") as tar:
            for member in tar.getmembers():
                name = Path(member.name).name
                if name.startswith(REUNION_FILE_PREFIX) and name.endswith(".h5"):
                    tar.extract(member, path=extract_dir, filter="data")
                    extracted.append(extract_dir / member.name)
    except tarfile.TarError as exc:
        raise RadarCoreError(f"Archive radar invalide ou corrompue : {exc}") from exc

    if not extracted:
        raise RadarCoreError(
            f"Aucun fichier Réunion ({REUNION_FILE_PREFIX}*.h5) trouvé dans le paquet radar. "
            "Le format ou le nommage a peut-être changé côté Météo-France."
        )
    return extracted


def parse_radar_timestamp(filename: str) -> datetime:
    """'T_IPRE20_C_LFPW_20260704064500.h5' -> datetime UTC correspondant."""
    stem = Path(filename).stem
    ts_str = stem.split("_")[-1]
    try:
        return datetime.strptime(ts_str, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RadarCoreError(f"Horodatage illisible dans le nom de fichier : {filename}") from exc


# ---------------------------------------------------------------------------
# Décodage ODIM_H5 -> GeoTIFF
# ---------------------------------------------------------------------------

def convert_h5_to_geotiff(h5_path: Path, output_path: Path) -> Path:
    """
    Convertit le sous-dataset de précipitation (ACRR) d'un fichier ODIM_H5
    en GeoTIFF, avec :
      - application du gain/offset (valeur réelle = brut * gain + offset)
      - undetect (radar actif, aucune pluie) -> 0.0
      - nodata (hors de portée radar) -> NoData réel du GeoTIFF
    """
    subdataset_uri = _SUBDATASET_TEMPLATE.format(path=h5_path)
    dataset = gdal.Open(subdataset_uri)
    if dataset is None:
        raise RadarCoreError(f"Impossible d'ouvrir le sous-dataset radar : {h5_path.name}")

    try:
        meta = dataset.GetMetadata()
        try:
            gain = float(meta.get(_META_GAIN, 1.0))
            offset = float(meta.get(_META_OFFSET, 0.0))
            raw_nodata = float(meta.get(_META_NODATA, 65535))
            raw_undetect = float(meta.get(_META_UNDETECT, 65534))
        except ValueError as exc:
            raise RadarCoreError(
                f"Métadonnées ODIM_H5 inattendues dans {h5_path.name} : {exc}"
            ) from exc

        band = dataset.GetRasterBand(1)
        raw_array = band.ReadAsArray().astype(np.float64)

        nodata_mask = raw_array == raw_nodata
        undetect_mask = raw_array == raw_undetect

        output_array = raw_array * gain + offset
        output_array[undetect_mask] = 0.0
        output_array[nodata_mask] = OUTPUT_NODATA_VALUE

        output_path.parent.mkdir(parents=True, exist_ok=True)
        driver = gdal.GetDriverByName("GTiff")
        out_ds = driver.Create(
            str(output_path),
            dataset.RasterXSize,
            dataset.RasterYSize,
            1,
            gdal.GDT_Float32,
            options=["COMPRESS=DEFLATE", "TILED=YES"],
        )
        if out_ds is None:
            raise RadarCoreError(f"Échec de création du GeoTIFF de sortie : {output_path}")

        try:
            out_ds.SetGeoTransform(dataset.GetGeoTransform())
            out_ds.SetProjection(dataset.GetProjection())
            out_band = out_ds.GetRasterBand(1)
            out_band.WriteArray(output_array.astype(np.float32))
            out_band.SetNoDataValue(OUTPUT_NODATA_VALUE)
            out_band.FlushCache()
        finally:
            out_ds = None  # force l'écriture sur disque
    finally:
        dataset = None

    return output_path


# ---------------------------------------------------------------------------
# Cache local
# ---------------------------------------------------------------------------

class RadarCache:
    """Cache local : archives brutes, .h5 extraits, GeoTIFF générés."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.archive_dir = self.cache_dir / "archives"
        self.h5_dir = self.cache_dir / "h5"
        self.geotiff_dir = self.cache_dir / "geotiff"
        for directory in (self.archive_dir, self.h5_dir, self.geotiff_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def new_archive_path(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return self.archive_dir / f"radar_package_{stamp}.tar.gz"

    def geotiff_path(self, ts: datetime) -> Path:
        stamp = ts.strftime("%Y%m%dT%H%M%SZ")
        return self.geotiff_dir / f"radar_reunion_{stamp}.tif"

    def is_cached(self, path: Path) -> bool:
        return path.exists() and path.stat().st_size > 0

    def purge_older_than(self, hours: int = RADAR_RETENTION_HOURS) -> int:
        """
        Supprime les fichiers plus vieux que `hours`. Par défaut alignée
        sur la rétention réelle de l'API (20h) : au-delà, la donnée n'a
        de toute façon plus de valeur de comparaison avec l'API elle-même.
        """
        cutoff = time.time() - hours * 3600
        removed = 0
        for directory in (self.archive_dir, self.h5_dir, self.geotiff_dir):
            for file_path in directory.glob("*"):
                if file_path.is_file() and file_path.stat().st_mtime < cutoff:
                    file_path.unlink()
                    removed += 1
        return removed


# ---------------------------------------------------------------------------
# Orchestration haut niveau (façade appelée par la GUI)
# ---------------------------------------------------------------------------

class RadarService:
    """Point d'entrée unique pour la GUI : téléchargement + décodage + cache."""

    def __init__(self, api_key: str, cache_dir: Path):
        self.client = RadarAPIClient(api_key=api_key)
        self.cache = RadarCache(cache_dir=cache_dir)

    def refresh(self) -> list[tuple[datetime, Path]]:
        """
        Télécharge le dernier paquet disponible (~9 Mo, toutes zones),
        extrait les fichiers Réunion (généralement 3, pas de 5 min), et
        retourne la liste (horodatage, chemin_geotiff) triée du plus
        ancien au plus récent.

        Chaque appel télécharge le paquet complet même si une grande
        partie n'est pas utilisée (pas de moyen de ne demander que la
        Réunion côté API) — à garder en tête en cas de rafraîchissement
        automatique fréquent.
        """
        archive_path = self.cache.new_archive_path()
        self.client.download_package(archive_path)

        h5_files = extract_reunion_files(archive_path, self.cache.h5_dir)

        results: list[tuple[datetime, Path]] = []
        for h5_path in sorted(h5_files):
            ts = parse_radar_timestamp(h5_path.name)
            output_path = self.cache.geotiff_path(ts)
            if not self.cache.is_cached(output_path):
                convert_h5_to_geotiff(h5_path, output_path)
            results.append((ts, output_path))

        results.sort(key=lambda item: item[0])
        return results

    def purge_cache(self, hours: int = RADAR_RETENTION_HOURS) -> int:
        return self.cache.purge_older_than(hours=hours)

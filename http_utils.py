# -*- coding: utf-8 -*-
"""
http_utils.py
=============
Utilitaire HTTP minimal, sans dépendance QGIS/Qt (module headless, au même
titre que les fichiers _core.py qui l'utilisent).
"""

from __future__ import annotations

import urllib.request


def urlopen_https(request: urllib.request.Request, timeout: float):
    """
    Ouvre une requête après validation explicite du schéma HTTPS.

    Toutes les URLs interrogées par ce plugin sont des constantes internes
    (API Météo-France, toujours en https). Ce contrôle explicite répond à
    l'audit de sécurité Bandit B310 (urlopen sans validation de schéma, qui
    permettrait sinon file:// ou un schéma inattendu) et lève une erreur
    claire si un refactor futur introduisait par erreur une URL non https.
    """
    if request.type != "https":
        raise ValueError(f"Schéma d'URL non autorisé pour cette requête : {request.type!r}")
    return urllib.request.urlopen(request, timeout=timeout)  # nosec B310

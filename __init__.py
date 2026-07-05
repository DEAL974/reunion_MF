# -*- coding: utf-8 -*-
"""
Point d'entrée du plugin QGIS "Réunion MF".
QGIS appelle classFactory(iface) au chargement du plugin.

Développement réalisé avec l'assistance d'une IA (Claude, Anthropic),
sous supervision et validation humaine de chaque composant
(DEAL Réunion - SCETE/USIG).
"""


def classFactory(iface):
    from .reunion_mf_gui import ReunionMFPlugin
    return ReunionMFPlugin(iface)

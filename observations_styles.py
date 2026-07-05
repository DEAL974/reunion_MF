# -*- coding: utf-8 -*-
"""
observations_styles.py
=======================
Rendu gradué (couleur de marqueur) par paramètre d'observation, appliqué
sur une couche vectorielle points. Palette reprise dans le même esprit
que arome_styles.py, adaptée à un rendu ponctuel plutôt que raster.
"""

from __future__ import annotations

from qgis.core import (
    QgsGraduatedSymbolRenderer,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsRendererRange,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor

# Paliers (borne_min, couleur RGB) par champ — la dernière borne sert de max.
STYLE_STOPS = {
    "t": [
        (10, (43, 131, 186)), (18, (171, 221, 164)), (24, (255, 255, 191)),
        (28, (253, 174, 97)), (32, (215, 25, 28)), (38, (128, 0, 38)),
    ],
    "tx": [
        (10, (43, 131, 186)), (18, (171, 221, 164)), (24, (255, 255, 191)),
        (28, (253, 174, 97)), (32, (215, 25, 28)), (38, (128, 0, 38)),
    ],
    "tn": [
        (10, (43, 131, 186)), (18, (171, 221, 164)), (24, (255, 255, 191)),
        (28, (253, 174, 97)), (32, (215, 25, 28)), (38, (128, 0, 38)),
    ],
    "ff": [
        (0, (237, 248, 233)), (3, (186, 228, 179)), (6, (116, 196, 118)),
        (10, (49, 163, 84)), (15, (0, 109, 44)), (25, (0, 68, 27)),
    ],
    "raf": [
        (0, (255, 255, 204)), (5, (255, 237, 160)), (10, (254, 178, 76)),
        (17, (253, 141, 60)), (25, (240, 59, 32)), (40, (189, 0, 38)),
    ],
    "rr1": [
        (0, (247, 251, 255)), (0.5, (198, 219, 239)), (2, (107, 174, 214)),
        (5, (49, 130, 189)), (15, (8, 81, 156)), (40, (8, 48, 107)),
    ],
    "u": [
        (20, (166, 97, 26)), (40, (223, 194, 125)), (60, (245, 245, 245)),
        (80, (128, 205, 193)), (100, (1, 133, 113)),
    ],
    "pres": [
        (995, (43, 131, 186)), (1005, (171, 221, 164)), (1013, (255, 255, 191)),
        (1020, (253, 174, 97)), (1030, (215, 25, 28)),
    ],
    "pmer": [
        (995, (43, 131, 186)), (1005, (171, 221, 164)), (1013, (255, 255, 191)),
        (1020, (253, 174, 97)), (1030, (215, 25, 28)),
    ],
}

DEFAULT_GRAY_STOPS = [(-9999, (150, 150, 150)), (9999, (80, 80, 80))]


def apply_style(layer: QgsVectorLayer, field: str, marker_size: float = 3.5) -> None:
    """
    Applique un rendu gradué (couleur du marqueur selon `field`) sur la
    couche points.

    Limite connue : les entités avec une valeur NULL pour `field`
    (fréquent selon l'équipement de la station) ne seront pas classées
    par QgsGraduatedSymbolRenderer et resteront rendues avec son symbole
    "hors classes" par défaut — pas de distinction visuelle personnalisée
    pour l'instant dans cette V1.
    """
    stops = STYLE_STOPS.get(field, DEFAULT_GRAY_STOPS)
    ranges = []

    for i in range(len(stops) - 1):
        lower, color_lower = stops[i]
        upper, _ = stops[i + 1]
        symbol = QgsMarkerSymbol.createSimple({
            "color": f"{color_lower[0]},{color_lower[1]},{color_lower[2]},230",
            "size": str(marker_size),
            "outline_color": "60,60,60,255",
            "outline_width": "0.2",
        })
        label = f"{lower} – {upper}"
        ranges.append(QgsRendererRange(lower, upper, symbol, label))

    renderer = QgsGraduatedSymbolRenderer(field, ranges)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def apply_labels(layer: QgsVectorLayer, field: str, unit: str) -> None:
    """
    Affiche la valeur mesurée (arrondie, avec unité) au-dessus de chaque
    point. Rien n'est affiché si la valeur est NULL pour la station
    (plutôt qu'un texte "None" disgracieux).
    """
    unit_suffix = f" || ' {unit}'" if unit else ""
    expression = (
        f'CASE WHEN "{field}" IS NOT NULL '
        f'THEN round("{field}", 1){unit_suffix} '
        f"ELSE '' END"
    )

    settings = QgsPalLayerSettings()
    settings.fieldName = expression
    settings.isExpression = True

    text_format = QgsTextFormat()
    text_format.setSize(8)

    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(1.0)
    buffer_settings.setColor(QColor(255, 255, 255))
    text_format.setBuffer(buffer_settings)
    settings.setFormat(text_format)

    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()


def disable_labels(layer: QgsVectorLayer) -> None:
    layer.setLabelsEnabled(False)
    layer.triggerRepaint()

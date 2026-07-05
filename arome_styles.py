# -*- coding: utf-8 -*-
"""
arome_styles.py
================
Styles de rendu automatiques par paramètre AROME.

Chaque paramètre a une rampe de couleurs adaptée à sa nature physique.
Les paramètres "cumulatifs à zéro" (précipitations, nébulosité) ont leur
premier palier en transparence totale, pour laisser voir le fond de carte
là où le phénomène est nul, indépendamment de l'opacité globale réglée
par l'utilisateur.

Les paramètres directionnels/composantes vectorielles (WDIR, UGRD, VGRD,
UGUST, VGUST) ne sont volontairement pas stylés avec une rampe de couleur
dédiée : ils sont plus pertinents en représentation vectorielle (flèches)
qu'en raster coloré. Ils reçoivent un style gris neutre par défaut.
"""

from __future__ import annotations

from qgis.core import (
    QgsRasterLayer,
    QgsSingleBandPseudoColorRenderer,
    QgsColorRampShader,
    QgsRasterShader,
)
from qgis.PyQt.QtGui import QColor


# Chaque entrée : liste de (valeur, (r, g, b, a)) croissante.
# a=0 -> transparence totale à ce palier (utile pour "zéro phénomène").
STYLE_STOPS = {
    "TMP": [
        (10, (43, 131, 186, 255)),
        (18, (171, 221, 164, 255)),
        (24, (255, 255, 191, 255)),
        (28, (253, 174, 97, 255)),
        (32, (215, 25, 28, 255)),
        (36, (128, 0, 38, 255)),
    ],
    "WIND": [
        (0, (255, 255, 255, 0)),
        (5, (200, 230, 201, 220)),
        (10, (255, 235, 132, 230)),
        (17, (253, 141, 60, 240)),
        (25, (215, 25, 28, 255)),
        (35, (103, 0, 13, 255)),
    ],
    "GUST": [
        (0, (255, 255, 255, 0)),
        (10, (200, 230, 201, 220)),
        (20, (255, 235, 132, 230)),
        (30, (253, 141, 60, 240)),
        (40, (215, 25, 28, 255)),
        (55, (103, 0, 13, 255)),
    ],
    "RH": [
        (20, (166, 97, 26, 255)),
        (40, (223, 194, 125, 255)),
        (60, (245, 245, 245, 255)),
        (80, (128, 205, 193, 255)),
        (100, (1, 133, 113, 255)),
    ],
    "TPRATE": [
        (0, (255, 255, 255, 0)),
        (0.5, (204, 236, 255, 180)),
        (2, (116, 173, 209, 220)),
        (5, (69, 117, 180, 240)),
        (15, (49, 54, 149, 255)),
        (40, (94, 0, 158, 255)),
    ],
    "SPRATE": [
        (0, (255, 255, 255, 0)),
        (0.5, (222, 235, 247, 200)),
        (2, (158, 202, 225, 230)),
        (5, (66, 146, 198, 255)),
        (15, (8, 69, 148, 255)),
    ],
    "GPRATE": [
        (0, (255, 255, 255, 0)),
        (0.5, (240, 220, 255, 200)),
        (2, (203, 158, 255, 230)),
        (5, (150, 66, 220, 255)),
        (15, (90, 8, 160, 255)),
    ],
    "TCDC": [
        (0, (255, 255, 255, 0)),
        (25, (240, 240, 240, 120)),
        (50, (210, 210, 210, 170)),
        (75, (160, 160, 160, 210)),
        (100, (100, 100, 100, 255)),
    ],
    "DSWRF": [
        (0, (40, 40, 80, 255)),
        (200, (80, 80, 160, 255)),
        (500, (255, 221, 89, 255)),
        (800, (255, 174, 0, 255)),
        (1100, (255, 94, 0, 255)),
    ],
    "PRMSL": [
        (995, (43, 131, 186, 255)),
        (1005, (171, 221, 164, 255)),
        (1013, (255, 255, 191, 255)),
        (1020, (253, 174, 97, 255)),
        (1030, (215, 25, 28, 255)),
    ],
}

# Rampe neutre pour les paramètres directionnels/composantes non stylés
# spécifiquement (WDIR, UGRD, VGRD, UGUST, VGUST).
DEFAULT_GRAYSCALE_STOPS = [
    (-50, (30, 30, 30, 200)),
    (0, (150, 150, 150, 200)),
    (50, (230, 230, 230, 200)),
]


def _build_shader(stops: list[tuple[float, tuple[int, int, int, int]]]) -> QgsRasterShader:
    """Construit un QgsRasterShader interpolé à partir d'une liste de paliers."""
    color_ramp = QgsColorRampShader()
    color_ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)

    ramp_items = []
    for value, (r, g, b, a) in stops:
        item = QgsColorRampShader.ColorRampItem(value, QColor(r, g, b, a))
        ramp_items.append(item)
    color_ramp.setColorRampItemList(ramp_items)

    shader = QgsRasterShader()
    shader.setRasterShaderFunction(color_ramp)
    return shader


def build_legend_pixmap(element: str, unit: str = "", width: int = 200, height: int = 46):
    """
    Construit une image de légende (barre de dégradé + valeurs min/max)
    à partir des mêmes paliers que apply_style(), pour affichage en
    overlay flottant sur le canevas (cf. AromeLegendWidget).
    """
    from qgis.PyQt.QtCore import Qt as _Qt
    from qgis.PyQt.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap

    stops = STYLE_STOPS.get(element, DEFAULT_GRAYSCALE_STOPS)
    values = [v for v, _ in stops]
    vmin, vmax = values[0], values[-1]
    span = (vmax - vmin) or 1

    margin, bar_height = 4, 14
    pixmap = QPixmap(width, height)
    pixmap.fill(_Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    gradient = QLinearGradient(margin, 0, width - margin, 0)
    for value, (r, g, b, a) in stops:
        position = max(0.0, min(1.0, (value - vmin) / span))
        gradient.setColorAt(position, QColor(r, g, b, a))

    painter.setPen(QColor(80, 80, 80))
    painter.setBrush(gradient)
    painter.drawRect(margin, margin, width - 2 * margin, bar_height)

    font = QFont()
    font.setPointSize(7)
    painter.setFont(font)
    painter.setPen(QColor(30, 30, 30))
    label_min = f"{vmin:g}"
    label_max = f"{vmax:g}" + (f" {unit}" if unit else "")
    painter.drawText(margin, margin + bar_height + 12, label_min)
    text_width = painter.fontMetrics().horizontalAdvance(label_max)
    painter.drawText(width - margin - text_width, margin + bar_height + 12, label_max)

    painter.end()
    return pixmap


def apply_style(layer: QgsRasterLayer, element: str, opacity: float = 0.8) -> None:
    """
    Applique le style automatique adapté au paramètre `element` sur la
    couche raster `layer`, avec une opacité globale de couche (0.0 à 1.0).

    :param layer: couche raster QGIS (1 bande) déjà chargée dans le projet
    :param element: code GRIB_ELEMENT (ex: "TMP", "WIND", "TPRATE")
    :param opacity: opacité globale de la couche (indépendante de la
                     transparence des paliers à zéro, qui reste à 0
                     quoi qu'il arrive pour les paramètres cumulatifs)
    """
    stops = STYLE_STOPS.get(element, DEFAULT_GRAYSCALE_STOPS)
    shader = _build_shader(stops)

    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    renderer.setClassificationMin(stops[0][0])
    renderer.setClassificationMax(stops[-1][0])

    layer.setRenderer(renderer)
    layer.renderer().setOpacity(max(0.0, min(1.0, opacity)))
    layer.triggerRepaint()

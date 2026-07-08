# -*- coding: utf-8 -*-
"""
radar_styles.py
================
Style de rendu et légende pour le module Radar.

Palette à 12 classes discrètes façon composite radar OPERA (fournie par
l'utilisateur), seuils exprimés en mm/h (convention OPERA). La couche
stocke un cumul sur 5 min (ACRR), donc chaque seuil est divisé par 12
pour rester cohérent avec l'unité réelle des données.
"""

from __future__ import annotations

from qgis.core import (
    QgsColorRampShader,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
)
from qgis.PyQt.QtGui import QColor

# (seuil bas de la classe, en mm/h ; couleur RGBA de la classe)
OPERA_CLASSES = [
    (0.5, (191, 239, 255, 255)),    # >= 0,5 mm/h : cyan très pâle
    (1.0, (120, 220, 255, 255)),    # >= 1 mm/h : cyan clair
    (1.6, (52, 152, 235, 255)),     # >= 1,6 mm/h : bleu ciel
    (2.8, (30, 60, 200, 255)),      # >= 2,8 mm/h : bleu roi
    (4.7, (10, 110, 40, 255)),      # >= 4,7 mm/h : vert foncé
    (8.0, (40, 200, 40, 255)),      # >= 8 mm/h : vert vif
    (10.0, (255, 235, 0, 255)),     # >= 10 mm/h : jaune
    (25.0, (255, 150, 0, 255)),     # >= 25 mm/h : orange
    (30.0, (230, 20, 20, 255)),     # >= 30 mm/h : rouge
    (62.0, (230, 0, 230, 255)),     # >= 62 mm/h : magenta vif
    (100.0, (255, 170, 220, 255)),  # >= 100 mm/h : rose pâle
    (170.0, (255, 255, 255, 255)),  # >= 170 mm/h : blanc
]
MM_PAR_HEURE_VERS_MM_5MIN = 5 / 60


def _build_shader() -> QgsRasterShader:
    """Construit un QgsRasterShader à classes discrètes à partir de OPERA_CLASSES."""
    items = [
        # en dessous du premier seuil : transparent (pas de pluie significative)
        QgsColorRampShader.ColorRampItem(
            OPERA_CLASSES[0][0] * MM_PAR_HEURE_VERS_MM_5MIN, QColor(255, 255, 255, 0)
        )
    ]
    for i, (_, color) in enumerate(OPERA_CLASSES):
        is_last = i == len(OPERA_CLASSES) - 1
        upper_bound = 999.0 if is_last else OPERA_CLASSES[i + 1][0] * MM_PAR_HEURE_VERS_MM_5MIN
        items.append(QgsColorRampShader.ColorRampItem(upper_bound, QColor(*color)))

    color_ramp = QgsColorRampShader()
    color_ramp.setColorRampType(QgsColorRampShader.Type.Discrete)
    color_ramp.setColorRampItemList(items)

    shader = QgsRasterShader()
    shader.setRasterShaderFunction(color_ramp)
    return shader


def apply_style(layer: QgsRasterLayer, opacity: float = 0.8) -> None:
    """Applique la palette à classes discrètes sur la couche radar (1 bande, ACRR mm/5min)."""
    shader = _build_shader()

    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    renderer.setClassificationMin(0.0)
    renderer.setClassificationMax(OPERA_CLASSES[-1][0] * MM_PAR_HEURE_VERS_MM_5MIN)

    layer.setRenderer(renderer)
    layer.renderer().setOpacity(max(0.0, min(1.0, opacity)))
    layer.triggerRepaint()


def build_legend_pixmap(width: int = 240, height: int = 46):
    """
    Construit une image de légende (12 blocs de couleur + seuils en mm/h)
    pour affichage en overlay flottant sur le canevas, sur le même
    principe que arome_styles.build_legend_pixmap.
    """
    from qgis.PyQt.QtCore import Qt as _Qt
    from qgis.PyQt.QtGui import QFont, QPainter, QPixmap

    margin, bar_height = 4, 14
    pixmap = QPixmap(width, height)
    pixmap.fill(_Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    n = len(OPERA_CLASSES)
    bar_width = width - 2 * margin
    segment_width = bar_width / n

    font = QFont()
    font.setPointSize(6)
    painter.setFont(font)

    for i, (threshold, color) in enumerate(OPERA_CLASSES):
        x = margin + i * segment_width
        painter.setPen(QColor(120, 120, 120))
        painter.setBrush(QColor(*color))
        painter.drawRect(int(x), margin, int(segment_width) + 1, bar_height)

        label = f"{threshold:g}"
        painter.setPen(QColor(20, 20, 20))
        painter.drawText(
            int(x), margin + bar_height + 11, int(segment_width) + 4, 10,
            _Qt.AlignmentFlag.AlignHCenter, label,
        )

    painter.setPen(QColor(30, 30, 30))
    painter.drawText(margin, margin + bar_height + 22, "seuils en mm/h (taux horaire équivalent)")

    painter.end()
    return pixmap

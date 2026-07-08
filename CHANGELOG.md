# Changelog

Toutes les modifications notables de ce plugin sont documentées ici.

## [0.3.6] - 2026-07-06

- Radar : la légende de la palette OPERA (12 classes) s'affiche maintenant
  dans l'encadré titre/pas de temps, comme pour AROME. Nouveau module
  `radar_styles.py` (palette + rendu + légende), sur le même principe que
  `arome_styles.py`.
- AROME : le run (réseau de référence, ex. "03/07 18h00") apparaît
  désormais dans le nom de la couche (chargement simple), le nom du groupe
  et le titre de l'encadré (série temporelle).
- Contour Réunion et fond OpenStreetMap : une nouvelle méthode
  `ensure_base_layers()` les réajoute s'ils sont absents, appelée après
  chaque génération de donnée par un des trois modules (pas seulement au
  chargement de projet). Corrige un cas où leur suppression manuelle, ou
  la création d'un nouveau projet dans la même session QGIS, les laissait
  durablement absents jusqu'au redémarrage de QGIS.

## [0.3.5] - 2026-07-06

- Radar : les échéances sont maintenant animables via le Temporal Controller
  (plage temporelle fixe par couche + `setTemporalExtents`/`setFrameDuration`
  recalculés à chaque actualisation), sur le même principe qu'AROME. Le
  module Radar n'avait jamais eu cette configuration jusqu'ici.
- Radar : nouvelle palette à 12 classes façon composite OPERA
  (bleu→vert→jaune→orange→rouge→magenta→blanc), remplaçant le dégradé bleu
  monochrome initial. Seuils fournis en mm/h convertis en mm/5 min pour
  correspondre à l'unité réelle des données (ACRR).
- Factorisation : `apply_fixed_temporal_range` déplacé dans `common.py`,
  partagé entre AROME et Radar au lieu d'être dupliqué.

## [0.3.4] - 2026-07-06

- Corrige un bug d'affichage signalé en usage réel : l'encadré titre/pas de
  temps/légende du Temporal Controller (AROME, Observations) n'apparaissait
  que furtivement lors d'un zoom/dézoom avant de disparaître, l'ordre
  d'empilement de l'annotation carte QGIS vis-à-vis du rendu asynchrone des
  couches temporelles n'étant pas garanti. Remplace le mécanisme
  `QgsAnnotation`/`AnnotationManager` par un `QLabel` natif, enfant direct
  du canevas, systématiquement au premier plan.

## [0.3.3] - 2026-07-06

- Qualité : corrige le reste des remarques Flake8 remontées par le scan
  plugins.qgis.org (E241 alignement de dictionnaires, E127 indentation de
  continuation, E305 lignes vides entre définitions, W503 saut de ligne
  avant opérateur binaire). Les lignes dépassant 79 caractères (E501)
  restent nombreuses mais sont purement cosmétiques, non corrigées ici.

## [0.3.2] - 2026-07-06

- Qualité : retrait des imports et variables locales inutilisés relevés par
  Flake8 (`typing.Optional`, `pathlib.Path`, deux variables locales mortes).

## [0.3.1] - 2026-07-06

Corrections suite à la soumission sur plugins.qgis.org.

- Sécurité : validation explicite du schéma HTTPS avant chaque `urlopen`
  (corrige les 5 findings Bandit B310 remontés par le scan automatique).
- `metadata.txt` : description et présentation traduites en anglais
  (requis par la checklist de soumission), champ `author` corrigé (un
  slash n'y est pas autorisé).
- AROME : format des niveaux hauteur (HP1/HP2/HP3, ex. "20 m") et coquille
  du catalogue de paquets ("paramêtres" → "paramètres") corrigés à
  l'affichage.
- Note technique : retrait d'une mention obsolète en section Contact.

## [0.3.0] - 2026-07-05

Première version préparée pour diffusion publique.

- Trois modules dans un panneau unique : AROME (prévisions), Radar (temps
  réel), Observations (stations).
- Onglet Configuration : saisie et test de la clé API Météo-France, unique
  pour les trois modules, déblocage conditionnel des autres onglets.
- CRS du projet forcé sur EPSG:2975, contour officiel de La Réunion maintenu
  en haut de la légende, fond de carte OpenStreetMap.
- Séries temporelles animées (Temporal Controller) pour AROME et
  Observations, avec overlay de titre/légende et heure locale (GMT+4).
- Renommage du plugin, anciennement « AROME Outre-Mer - Réunion »
  (`arome_reunion`).

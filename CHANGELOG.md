# Changelog

Toutes les modifications notables de ce plugin sont documentées ici.

## [0.3.11] - 2026-07-08

- Corrige la cause racine finale de l'absence d'animation, trouvée par
  comparaison directe des horodatages dans la console Python de QGIS :
  les frames du contrôleur s'affichaient en UTC (`...Z`) alors que les
  plages fixes des couches s'affichaient correctement en heure locale
  (`...+04:00`) — mêmes chiffres d'horloge, mais des instants réels
  décalés de 4h. `QgsTemporalNavigationObject.setTemporalExtents()`
  perdait le décalage +04:00 des `QDateTime` transmis et les
  réinterprétait tels quels comme de l'UTC. Pour Radar (fenêtre de
  15-30 min), ce décalage empêchait tout recoupement frame/couche ; pour
  AROME (pas d'1h sur 24h), un recoupement partiel restait possible,
  d'où l'animation "fonctionnelle en apparence" mais pas réellement
  correcte.
  Ajoute `common.utc_datetime_to_qdatetime` (UTC pur, sans décalage) et
  l'utilise pour les appels à `setTemporalExtents` uniquement — les
  plages par couche restent construites en heure locale, qui elles
  fonctionnaient déjà correctement.

## [0.3.10] - 2026-07-08

- Corrige le vrai dernier maillon du bug d'animation radar, trouvé grâce
  à un diagnostic console montrant un Temporal Controller parfaitement
  configuré (3 frames de 5 min, mode Animated) mais un groupe de couches
  radar entièrement **vide**. Cause : `_loaded_timestamps` (un `set` de
  timestamps "déjà vus") ne vérifiait jamais si la couche correspondante
  existait encore réellement dans le projet — si elle avait été
  supprimée manuellement puis qu'une actualisation ne ramenait aucune
  échéance vraiment nouvelle, le plugin la croyait "déjà chargée" et ne
  la recréait pas, laissant le Temporal Controller animer une fenêtre
  sans aucune couche à afficher. Remplacé par `_loaded_layer_ids` (dict
  timestamp → id de couche), qui revérifie `project.mapLayer(id)` avant
  de sauter une échéance.

## [0.3.9] - 2026-07-08

- Corrige la cause racine du bug d'animation, diagnostiquée via un test
  réel dans la console Python de QGIS : `temporalController().navigationMode()`
  restait à `NavigationOff` (jamais activé explicitement par le plugin),
  QGIS n'appliquait alors aucun filtrage temporel par couche. Ajoute un
  appel explicite à `setNavigationMode(Animated)`, regroupé avec
  `setTemporalExtents`/`setFrameDuration`/`rewindToStart` dans une
  nouvelle fonction partagée `common.configure_temporal_animation`
  (remplace la logique dupliquée entre AROME et Radar).
- Radar : le contrôleur est reconfiguré à chaque actualisation réussie,
  plus seulement quand une échéance neuve arrive — sinon un clic "déjà
  à jour" laissait en place la configuration d'un autre module.

## [0.3.8] - 2026-07-08

- Corrige un bug remonté en usage réel : après avoir animé AROME puis
  basculé sur Radar (ou l'inverse), les couches du second module
  n'apparaissaient jamais tant que le Temporal Controller était actif —
  le curseur "courant" du contrôleur restait positionné là où l'autre
  module l'avait laissé, hors de la nouvelle plage (généralement bien
  plus étroite pour le Radar : 15 min contre plusieurs heures pour
  AROME). `controller.rewindToStart()` est maintenant appelé après
  chaque reconfiguration de `setTemporalExtents`/`setFrameDuration`,
  dans les deux modules.

## [0.3.7] - 2026-07-08

- Corrige le chevauchement de texte dans la légende OPERA du Radar
  (préfixe "mm/h" + seuils sur deux rangées bien séparées, au lieu de
  trois rangées trop compactes dans l'espace de l'overlay).
- Corrige le réajout du contour Réunion / fond OSM pour le Radar :
  l'appel à `ensure_base_layers()` était sauté quand l'actualisation ne
  ramenait aucune échéance nouvelle ("déjà à jour"), alors que l'action
  avait bien réussi.

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

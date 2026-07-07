# Réunion MF

Plugin QGIS regroupant trois modules Météo-France pour La Réunion, dans un
panneau unique :

- **AROME** (prévisions) — API paquets AROME Outre-Mer (Océan Indien, grille
  0.025°), découverte dynamique des paramètres (surface, hauteur, niveaux
  isobares), vue régionale native ou vue Réunion reprojetée EPSG:2975, styles
  automatiques, séries temporelles animées via le Temporal Controller de QGIS.
- **Radar** (temps réel) — API Paquet Radar, mosaïque de précipitation sur La
  Réunion (résolution 500 m, pas de 5 min), actualisation manuelle constituant
  un historique local glissant.
- **Observations** (stations) — API Paquet Observations, mesures horaires des
  stations météo de La Réunion, animation temporelle native (couche
  vectorielle).

Une clé API Météo-France (portail-api.meteofrance.fr, mode « API Key ») est
nécessaire, partagée entre les trois modules. Un onglet Configuration dédié
guide sa saisie et teste l'accès à chaque produit.

## Prérequis

- QGIS ≥ 3.28
- GDAL avec support du décodage GRIB2 template DRS 5.42 (CCSDS/AEC) pour le
  module AROME — nécessite une version de GDAL récente (≈ 3.9/3.10) compilée
  avec `libaec`. Une version de GDAL trop ancienne échoue avec l'erreur
  `DRS Template 5.42 not defined` ; vérifier avec un téléchargement réel en
  cas de doute.
- Un compte et une clé API sur https://portail-api.meteofrance.fr

## Installation

Via le gestionnaire d'extensions QGIS une fois le plugin publié sur le dépôt
officiel, ou manuellement :

1. Télécharger le zip du plugin.
2. Si l'ancien plugin « AROME Outre-Mer - Réunion » (`arome_reunion`) est
   installé, le désinstaller au préalable — les deux coexisteraient sinon en
   tant que plugins distincts.
3. Installer via *Extensions > Installer une extension à partir d'un ZIP*.

## Fonctionnalités transverses

- Authentification unique par clé API (onglet Configuration), déblocage
  conditionnel des autres onglets une fois la clé validée.
- CRS du projet forcé automatiquement sur EPSG:2975 (RGR92 / UTM 40S).
- Contour officiel de La Réunion (source IGN, projet « france-geojson »)
  chargé et maintenu en haut de la légende.
- Fond de carte OpenStreetMap (flux XYZ natif QGIS) sous le contour.
- Horodatages affichés en heure locale (GMT+4), données sources conservées
  en UTC.

## Limites connues

- Le module Radar n'offre pas d'accès à un historique au-delà de la
  rétention de l'API (20h glissantes) ; l'historique local n'existe que s'il
  a été constitué au fil d'actualisations manuelles successives.
- Le champ pression (`pres`/`pmer`) du module Observations applique une
  conversion Pa → hPa non confirmée par une donnée réelle non nulle à ce
  jour.
- La reprojection régionale du GRIB AROME approxime la sphère native du
  modèle en WGS84 ; l'écart est jugé négligeable à l'échelle de La Réunion
  mais n'a pas fait l'objet d'une validation géodésique formelle.

Voir [`docs/note_technique_reunion_mf.pdf`](docs/note_technique_reunion_mf.pdf)
pour le détail technique complet.

## Contact

Bug, question ou suggestion : [ouvrir un ticket sur GitHub](https://github.com/DEAL974/reunion_MF/issues).

## Licence

GPL-3.0, voir [LICENSE](LICENSE).

## Développement

Développement réalisé avec l'assistance d'une IA (Claude, Anthropic), sous
supervision et validation humaine de chaque composant, pour la DEAL Réunion
(SCETE/USIG).

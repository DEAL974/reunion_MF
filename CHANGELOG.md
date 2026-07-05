# Changelog

Toutes les modifications notables de ce plugin sont documentées ici.

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

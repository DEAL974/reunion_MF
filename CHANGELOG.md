# Changelog

Toutes les modifications notables de ce plugin sont documentées ici.

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

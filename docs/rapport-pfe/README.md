# Rapport de PFE — projet LaTeX assemblé (template TEK-UP)

Projet complet et compilable. Il assemble le template TEK-UP (`tpl/isipfe.cls`) et les
six chapitres du rapport, conduits par CRISP-DM.

## Compilation

Le template utilise **biblatex avec le moteur `bibtex`** (pas `biber`) — voir `tpl/isipfe.cls`.

**Overleaf** (le plus simple) : téléverser le dossier entier, choisir `main.tex` comme document
principal, compilateur **pdfLaTeX**. Overleaf enchaîne les passes automatiquement.

**En local** (MiKTeX ou TeX Live) :

```bash
pdflatex main
bibtex   main
makeindex main
pdflatex main
pdflatex main
```

Trois passes de `pdflatex` sont nécessaires : la première résout les renvois, la deuxième
les mini-tables des matières (`minitoc`), la troisième la numérotation finale.

## Structure

| Fichier | Contenu | Phase CRISP-DM |
|---|---|---|
| `main.tex` | document maître | — |
| `preambule.tex` | compléments au template (amsmath, colonne `P`, bibliographie) | — |
| `global_config.tex` | **couverture, encadrants, résumés** | — |
| `introduction.tex` | introduction générale | — |
| `chapitre1.tex` | Cadre général du projet | hors cycle |
| `chapitre2.tex` | Compréhension métier | phase 1 |
| `chapitre3.tex` | Compréhension et préparation des données | phases 2 et 3 |
| `chapitre4.tex` | Modélisation : conception du moteur agentique | phase 4 |
| `chapitre5.tex` | Évaluation | phase 5 |
| `chapitre6.tex` | Déploiement et réalisation | phase 6 |
| `conclusion.tex` | conclusion générale | — |
| `acronymes.tex` | liste des abréviations (46 entrées) | — |
| `dedicaces.tex`, `remerciement.tex` | pages liminaires | — |
| `references.bib` | bibliographie, 44 entrées, style IEEE | — |
| `img/` | 42 figures | — |
| `img/screens/` | 21 captures de l'application réelle | — |

## À compléter avant le dépôt

1. **`global_config.tex`** — les champs marqués `← À COMPLÉTER` :
   - `\author{}` : nom de l'étudiant
   - `\proFramerName{}` : encadrant professionnel Ooredoo
   - `\academicFramerName{}` : encadrant académique TEK-UP

2. **`img/`** — quatre images sont des **remplacements provisoires** portant la mention
   « IMAGE À FOURNIR ». Les remplacer par les visuels réels :
   - `ooredoo_logo.png`
   - `ooredoo_values.png`
   - `ooredoo_organigramme.png`
   - `ooredoo_direction_technique.png`

3. **`dedicaces.tex`** et **`remerciement.tex`** — textes à personnaliser
   (les noms des encadrants et de l'équipe y sont génériques).

## Régénérer les figures

```bash
python figures.py           # figures des chapitres 1 et 2
python figures_data.py      # figures d'exploration — nécessite PostgreSQL accessible
python figures_schemas.py   # schémas des chapitres 1 et 3 à 6
```

Les trois scripts écrivent dans `img/`.

## Régénérer les captures d'écran

Nécessite le backend sur `:8000` et `ng serve` sur `:4200`, puis, depuis `D:\frontend\PFE` :

```bash
npx playwright test e2e/screenshots.spec.ts --project=chromium --workers=1
```

Les captures sont écrites dans `docs/rapport/img/screens/` — les recopier ensuite dans
`docs/rapport-pfe/img/screens/`.

## Vérifications automatiques

Le projet a été contrôlé : **61 images référencées et présentes**, **313 renvois croisés
sans rupture**, **85 citations toutes résolues**, **aucune figure ni tableau orphelin**.

## Adaptations apportées au template

- `preambule.tex` ajoute `amsmath` et `amssymb` (formules des chapitres 4 et 5),
  le type de colonne `P` et la ressource bibliographique `references.bib`.
- Les appels `\minitocsection` en tête de chapitre ont été retirés : la classe
  `isipfe.cls` les émet déjà depuis `\@makechapterhead`.
- Le chapitre de bibliographie manuelle (`webo.tex`) est remplacé par
  `\printbibliography[title={Bibliographie}]`, le rapport disposant d'une vraie
  bibliographie référencée.

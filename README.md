# pdf2sdmx

Lit les tableaux d'un rapport statistique en PDF et les écrit en SDMX 2.1. Chaque valeur
est contrôlée. Une valeur illisible est refusée et listée, jamais devinée.

Démo : https://pdf2sdmx.onrender.com. Hébergement gratuit : le premier chargement peut
prendre une minute, seul l'étage texte y tourne, et elle lit 10 pages à partir de celle
affichée.

## Résultats

Mesurés sur les bulletins trimestriels de l'Institut National de la Statistique du Niger.

| Mesure | Résultat | Document |
|---|---|---|
| Cellules exactes contre une saisie à la main | **94 / 94**, aucune valeur fausse | 3e trim. 2025, tableau 03.01, page 21 du PDF |
| Concordance avec le portail open data de la BAD | **56 / 58 (97 %)**, et 65 valeurs absentes du portail | même tableau, production, contre les séries 2024 du portail |
| SDMX-ML 2.1 contre les schémas XSD officiels | valide | 1er trim. 2024 et 3e trim. 2025, entiers, 71 pages chacun |
| Observations produites | 5 948 et 5 947 | les mêmes |
| Touchées par un contrôle arithmétique | 11 % | les mêmes |
| Erreur trouvée dans la source | quatre régions imprimées sur les mauvaises lignes, totaux justes | 3e trim. 2025, tableau 03.06, page 23 du PDF |

Les deux écarts avec le portail sont des totaux nationaux dont le périmètre diffère. Le
détail et les limites sont dans [MESURES.md](MESURES.md).

## Comment ça marche

```
pdfplumber      la couche texte du PDF
Camelot 2.0     un modèle qui retrouve les bords que pdfplumber rate
PaddleOCR-VL    un modèle de vision pour les pages scannées, câblé mais pas installé
```

L'étage qui laisse le moins d'échecs arithmétiques gagne, et ses lignes cassées sont
réparées depuis les autres. Chaque tableau est ensuite contrôlé (totaux, produits, suivi
d'une série d'une période à l'autre), codé contre un vocabulaire, et écrit en SDMX-CSV et
SDMX-ML 2.1. Les choix techniques sont dans [ARCHITECTURE.md](ARCHITECTURE.md).

## Ce qui sort

Un CSV par tableau, et pour le document entier un zip de cinq fichiers :

| Fichier | Contenu |
|---|---|
| `*_long.csv` | une ligne par nombre, avec son code, son libellé imprimé, sa page et l'étage qui l'a lu |
| `*_sdmx.csv` | SDMX-CSV 2.0 |
| `*_structure.xml` | SDMX-ML 2.1 : concepts, codelists, DSD, dataflow |
| `*_data.xml` | SDMX-ML 2.1, format générique |
| `*_to_review.csv` | chaque cellule qui a échoué un contrôle, avec la raison |

## Lancer

```bash
uv venv && source .venv/bin/activate
make install-ml       # pdfplumber, Camelot, torch CPU
make schemas          # les schémas SDMX officiels, une fois
make reference        # les codelists officielles du registre SDMX
python app.py         # interface et API sur http://localhost:7860

make test             # les tests
make evaluate         # exactitude contre la vérité terrain
make audit            # comparaison avec le portail open data
```

Qui publie, quel vocabulaire, quels seuils : tout se règle dans [.env.example](.env.example),
pas dans le code.

## Les normes suivies

Rien de ce qui suit n'est inventé ici. Les codes et les formats viennent de leurs sources
officielles, et l'outil vérifie ses sorties contre elles.

| | Ce qui en est pris | Source |
|---|---|---|
| SDMX 2.1 | le modèle, SDMX-ML et SDMX-CSV | https://sdmx.org/standards-2/ |
| Schémas XSD SDMX-ML 2.1 | la validation de conformité, à chaque exécution | https://github.com/sdmx-twg/sdmx-ml |
| Codelists transversales SDMX | `CL_FREQ`, `CL_OBS_STATUS`, `CL_UNIT_MULT` | https://sdmx.org/sdmx_cdcl/ |
| Registre global SDMX | d'où ces codelists sont téléchargées par `make reference` | https://registry.sdmx.org/ |
| Formats de temps SDMX | `YYYY`, `YYYY-A1` pour un exercice à cheval sur deux années, `YYYY-Qn`, `YYYY-MM-DD` | https://wiki.sdmxcloud.org/SDMX_Time_Formats |
| Modélisation d'un domaine | séparer la mesure de la chose mesurée plutôt qu'un code par combinaison | https://sdmx.org/guidelines/ |
| DSD des ODD | le modèle de ce découpage, seize dimensions | https://unstats.un.org/sdgs/iaeg-sdgs/sdmx-working-group/ |
| ISO 3166 | les codes de zone, pays et subdivisions | https://www.iso.org/iso-3166-country-codes.html |
| DSD du portail cible | `INDIC_AGRI`, `SPECULATION`, `CL_UNIT_MEASURE`, repris tels quels | https://ne.sdmx.afdb.org/ns-ws/rest/dataflow/NE1 |
| WDI de la Banque mondiale | les identifiants de composants, comparés aux nôtres | https://api.worldbank.org/v2/sdmx/rest/datastructure/WB/WDI/1.0 |

Outils : [pdfplumber](https://github.com/jsvine/pdfplumber),
[Camelot](https://github.com/atlanhq/camelot),
[PaddleOCR-VL](https://github.com/PaddlePaddle/PaddleOCR),
[sdmx1](https://sdmx1.readthedocs.io/), [Gradio](https://www.gradio.app/).

Licence MIT pour le code. Les PDF appartiennent à leurs éditeurs.

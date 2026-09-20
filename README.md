# pdf2sdmx

Lire les tableaux imprimés dans un rapport statistique en PDF et les écrire en SDMX, avec
un taux d'erreur mesuré.

## La question

Au départ : le détail que les instituts publient en PDF peut-il être récupéré
automatiquement ?

En cours de route, le portail open data de la banque de développement s'est avéré contenir
les mêmes séries, **de 1990 à 2024**. La prémisse ne tenait pas.

La question est devenue : **ce qu'un institut imprime concorde-t-il avec ce que son portail
publie, et qu'apporte le rapport en plus ?** À celle-là on peut répondre par des chiffres.

Sur la page comparée : **97 % des valeurs concordent** avec le portail, et **65 valeurs du
rapport n'y figurent pas**. Le détail est dans [MESURES.md](MESURES.md).

## Comment ça marche

Trois outils passent l'un après l'autre sur chaque page :

```
pdfplumber      la couche texte du PDF, rapide et exacte quand elle existe
Camelot 2.0     un modèle qui retrouve les bords que pdfplumber rate
PaddleOCR-VL    un modèle de vision, pour les pages scannées
```

Celui qui produit le moins d'échecs arithmétiques gagne. Ses lignes cassées sont réparées
depuis les autres quand ça fait baisser le compte. Chaque observation garde le nom de
l'étage qui l'a lue.

Ensuite chaque tableau est contrôlé (totaux, produits, sauts entre périodes), codé contre
un vocabulaire, et écrit en SDMX-CSV et SDMX-ML 2.1. Les deux messages XML passent les
schémas officiels du SDMX Technical Working Group, pas notre propre lecteur.

Une valeur illisible n'est jamais devinée : elle est refusée et listée.

## Ce qui sort

Un zip de cinq fichiers pour le document entier.

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
make install-ml       # pdfplumber, Camelot ml, torch CPU, environ 400 Mo
make schemas          # les schémas SDMX officiels, une fois
make reference        # les codelists officielles du registre SDMX
python app.py         # interface et API sur http://localhost:7860
```

Le bouton « Load the sample » ouvre un extrait de quatre pages, environ 40 s sur CPU.

```bash
make test             # 90 tests
make evaluate         # exactitude contre la vérité terrain
make audit            # comparaison avec le portail open data
make harvest          # ce qu'il reste à nommer dans le vocabulaire
```

## Mettre en ligne

Le service tourne sur [Koyeb](https://www.koyeb.com), formule gratuite : 512 Mo, endormi
après une heure sans visite, réveillé en quelques secondes.

**Create Web Service** → **GitHub** → ce dépôt → builder **Buildpack**. Basculer
l'interrupteur **Override** du champ **Run command** et saisir `python app.py`. Instance
**Free**, région **Frankfurt**. Le port est passé dans `$PORT` et l'application le lit.

`requirements.txt` ne contient que l'étage texte : torch pèse 738 Mo et ne tient pas dans
les 512 Mo. L'interface dit quels étages manquent, et un rapport avec une couche texte est
lu quand même. Pour la cascade complète, `requirements-full.txt` ou le `Dockerfile`, sur un
hôte disposant de 2 Go.

## Réglages

Tout ce qu'on peut dire à l'outil est dans [.env.example](.env.example) : qui publie, quel
vocabulaire, ce qui compte comme un tableau, comme une correspondance, comme une erreur.

Lire les rapports d'un autre institut se fait depuis ce fichier, pas depuis le code. Les
seuils ont été mesurés sur un seul éditeur ; un autre voudra les siens.

## Où regarder

```
src/pdf2sdmx/core/    extraction, contrôles, codage, écriture SDMX
src/pdf2sdmx/ui/      interface Gradio
src/pdf2sdmx/api/     routes FastAPI
mapping/              le vocabulaire, une ligne par libellé imprimé
truth/                la vérité terrain tapée à la main
scripts/              évaluation, audit, moisson du vocabulaire
```

[MESURES.md](MESURES.md) pour les chiffres et les limites,
[ARCHITECTURE.md](ARCHITECTURE.md) pour les choix techniques.

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

# Mesures

Tout ce qui est chiffré ici a été obtenu en relançant l'outil, pas en le citant.

## Documents testés

| | 1er trimestre 2024 | 3e trimestre 2025 |
|---|---|---|
| Pages | 71 | 71 |
| Pages portant un tableau | 42 | 43 |
| Tableaux lus | 53 | 57 |
| Tableaux montrés sous leur légende imprimée | 43 | 46 |
| Observations | 5 943 | 5 875 |
| Couvertes par un contrôle arithmétique | 10 % | 10 % |
| Signalées pour revue | 376 | 373 |
| Périodes SDMX invalides | 0 | 0 |
| SDMX-ML 2.1 contre les schémas officiels | valide | valide |

Deux rapports du même institut, à 18 mois d'écart. Un rapport entier prend 2 à 4 minutes
sur CPU. Aucune page n'a été choisie à la main.

## Exactitude, contre une vérité terrain

94 cellules tapées à la main depuis l'image de la page, sur un tableau du bulletin
3T 2025.

| | |
|---|---|
| Cellules exactes, pdfplumber seul | 84 / 94 (89,4 %) |
| Cellules exactes, cascade complète | **94 / 94 (100 %)** |
| Valeurs fausses arrivées en sortie | **0** |

Comment la cascade a récupéré les dix dernières : pdfplumber avait collé trois lignes en
une. Camelot lisait ce bloc correctement mais cassait 30 autres cellules. La cascade a
gardé pdfplumber et pris de Camelot les seules lignes concernées, parce que chaque échange
faisait baisser le nombre de contrôles en échec.

Le 100 % porte sur une page d'un type de tableau. Il faut le lire comme « la cascade et les
contrôles fonctionnent sur cette mise en page », pas comme une exactitude générale. Le
chiffre qui compte davantage est le zéro : aucune valeur fausse n'est sortie, parce que
toute cellule illisible est refusée et listée plutôt que devinée.

## Contre le portail open data

Le même institut publie sur un portail. `make audit` compare une page du rapport avec ce
que le portail contient déjà : ici une page du bulletin 3T 2025 contre les séries 2024
du portail.

| | |
|---|---|
| Valeurs lues dans le rapport | 123 |
| Publiées aussi par le portail | 58 |
| Concordent à moins de 0,5 % | **56 (97 %)** |
| Divergent | 2 |
| Présentes dans le rapport seulement | 65 |

Les deux écarts portent sur des totaux nationaux où le portail compte un périmètre que le
tableau ne couvre pas. Ce n'est pas une erreur, c'est une définition différente.

La concordance porte sur des chiffres que ce dépôt n'a pas produits. Ça vaut plus que
n'importe quel contrôle interne.

Les 65 valeurs sans équivalent, et la période que le portail n'a pas encore publiée, sont
ce que la lecture du rapport apporte. C'est l'argument, mesuré, plutôt qu'une supposition
sur ce qui manquerait.

## Ce que les contrôles couvrent

**10 % des observations sont touchées par un contrôle arithmétique.** À lire avant tout le
reste.

Les 90 % restants portent `OBS_STATUS = A` parce que rien ne les a contredits, pas parce
que quelque chose les a confirmés. L'interface l'affiche à l'écran plutôt que de laisser
supposer le contraire. Monter ce chiffre demande plus de contrôles, pas plus d'extraction.

Un tableau sans ligne de total, sans colonne de total et sans triplet dont le produit
peut être vérifié voit tous ses contrôles sautés.

## Erreurs trouvées dans la source

En comparant deux éditions : 32 sauts d'un facteur supérieur à 5 d'une période à l'autre,
listés dans `data/processed/to_review.csv`. Le plus net : une valeur imprimée
1 316 237 dans l'édition 1T 2024, contre 15 632 pour la même série un an plus tard.

Le contrôle ne dit pas laquelle des deux éditions a tort. Il liste les deux.

![Une série extraite, par région](figures/production_by_region.png)

## Limites

- **Deux documents, un pays, une langue.** Les règles de mise en page sont générales, mais
  les mots sont français : la légende, la ligne d'unité, les mois, les multiplicateurs, les
  libellés de total, le format des nombres. Une autre langue demande un autre jeu de ces
  constantes, pas une réécriture. Un autre pays demande un autre vocabulaire, qui est une
  entrée du programme.
- **Un contrôle valide des nombres, pas un sens.** Un tableau dont l'en-tête a été mal lu
  peut très bien tomber juste. C'est pourquoi un en-tête absent, ou qui a avalé une ligne
  de données, compte désormais comme un échec. Un en-tête simplement faux, lui, passe.
- **34 % des unités restent inconnues.** Les tableaux qui ne l'impriment nulle part.
- **82 % des observations ont une mesure identifiée.** Le reste porte `_Z`, faute de
  vocabulaire. Ça monte en remplissant `mapping/labels_to_codes.csv`, sans toucher au code.
- Les tableaux en paysage, et ceux qui courent sur deux pages, ne sont pas traités.
- La conformité est vérifiée contre les schémas XSD, qui valident la forme du message. Ils
  ne vérifient pas qu'un code existe dans sa liste ni qu'une période est réelle. Un registre
  comme FMR le ferait.
- L'étage vision (PaddleOCR-VL) est câblé mais pas installé. Les rapports testés ont une
  couche texte, il n'a jamais été nécessaire. Un rapport scanné en aurait besoin, et
  l'interface dit s'il est disponible.
- La réparation de lignes ne remplace une ligne que si le donneur est d'accord sur chaque
  cellule déjà lue et que le nombre d'échecs ne monte pas. Elle ne peut pas corriger une
  ligne que les deux étages ont mal lue.
- La vérité terrain a été tapée depuis l'image de la page pour ce dépôt, pas par l'institut.
  Elle mériterait une seconde relecture.

## Refaire les mesures

```bash
make evaluate    # exactitude contre truth/
make audit       # comparaison avec le portail
make test        # 90 tests
```

Ajouter une page : taper 10 à 30 cellules dans `truth/<pdf>_p<page>_truth.csv`, format dans
`truth/README.md`, puis relancer `make evaluate`.

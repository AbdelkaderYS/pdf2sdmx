# Mesures

Tout ce qui est chiffré ici a été obtenu en relançant l'outil, pas en le citant.

## Documents testés

Bulletins trimestriels de l'Institut National de la Statistique du Niger, mesurés le
26 septembre 2026 avec le code actuel.

| | 1er trimestre 2024 | 3e trimestre 2025 |
|---|---|---|
| Pages | 71 | 71 |
| Pages portant un tableau | 42 | 44 |
| Tableaux lus | 53 | 58 |
| Observations | 5 948 | 5 947 |
| Mesure identifiée | 86 % | 82 % |
| Couvertes par un contrôle arithmétique | 11 % | 11 % |
| Signalées pour revue | 416 | 1 012 |
| Sauts d'une période à l'autre signalés | 45 | 79 |
| Périodes SDMX invalides | 0 | 0 |
| SDMX-ML 2.1 contre les schémas officiels | valide | valide |

Deux rapports du même institut, à 18 mois d'écart. Un rapport entier prend 5 à 6 minutes
sur un CPU de portable. Aucune page n'a été choisie à la main.

Les cellules signalées sont pour l'essentiel des tableaux entiers dont l'en-tête n'a pas pu
être lu : un en-tête absent, ou qui a avalé une ligne de données, fait échouer tout le
tableau plutôt que de laisser passer des valeurs sous un mauvais nom.

## Exactitude, contre une vérité terrain

94 cellules tapées à la main depuis l'image de la page, sur le tableau 03.01 (résultats de
la campagne agricole par région) du bulletin 3T 2025, page 21 du PDF.

| | |
|---|---|
| Cellules exactes, pdfplumber seul | **94 / 94 (100 %)**, 84 avant le redécoupage des lignes |
| Cellules exactes, cascade complète | **94 / 94 (100 %)** |
| Valeurs fausses arrivées en sortie | **0** |

Les dix cellules que pdfplumber manquait venaient de trois lignes imprimées sans filet
entre elles : il les rendait comme une seule, chaque cellule contenant trois valeurs. La
cascade les récupérait d'abord en prenant ces lignes à Camelot. Une règle générale les
redécoupe maintenant dès la lecture : une ligne dont chaque valeur tient sur k lignes, au-dessus
de k-1 lignes vides dans ces colonnes, est remise en k lignes. pdfplumber seul suffit alors,
ce qui compte pour un hébergement qui n'a pas la place d'installer Camelot.

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

**11 % des observations sont touchées par un contrôle arithmétique.** À lire avant tout le
reste.

Les 89 % restants portent `OBS_STATUS = A` parce que rien ne les a contredits, pas parce
que quelque chose les a confirmés. L'interface l'affiche à l'écran plutôt que de laisser
supposer le contraire. Monter ce chiffre demande plus de contrôles, pas plus d'extraction.

Un tableau sans ligne de total, sans colonne de total et sans triplet dont le produit
peut être vérifié voit tous ses contrôles sautés.

Une somme ne voit pas des valeurs imprimées sur les mauvaises lignes. Le suivi d'une série
dans le temps les voit, mais seulement quand l'écart dépasse un facteur 5 : deux régions de
taille voisine échangées passent.

## Erreurs trouvées dans la source

En comparant deux éditions, les sauts d'un facteur supérieur à 5 d'une période à l'autre
sont listés dans `data/processed/to_review.csv`. Le plus net : une valeur imprimée
1 316 237 dans l'édition 1T 2024, contre 15 632 pour la même série un an plus tard.

Le contrôle ne dit pas laquelle des deux éditions a tort. Il liste les deux.

Dans le bulletin 3T 2025 lui-même, le tableau 03.06 (page 23 du PDF) du cheptel 2025 par région imprime les
valeurs de quatre régions sur les mauvaises lignes : Tahoua porte celles de Niamey,
Tillabéri celles de Tahoua, et ainsi de suite. Les totaux restent justes, puisqu'une
permutation ne change pas une somme. Aucun contrôle de total ne peut donc la voir. Le suivi
de chaque série d'une année à l'autre la voit, et l'interface le lance désormais sur tout le
document.

## Limites

- **Deux documents, un pays, une langue.** Les règles de mise en page sont générales, mais
  les mots sont français : la ligne d'unité, les mois, les multiplicateurs, les libellés de
  total. Le format des nombres (« 1 234,5 » ou « 1,234.5 ») est reconnu page par page, et
  « Table » comme « Tableau ». Sur l'annuaire agricole 2020, qui écrit « 0.881 », 407 valeurs
  étaient lues mille fois trop grandes avant cette détection, et le contrôle superficie ×
  rendement passait quand même. Une autre langue demande un autre jeu de ces constantes, pas
  une réécriture. Un autre pays demande un autre vocabulaire, qui est une
  entrée du programme.
- **Un contrôle valide des nombres, pas un sens.** Un tableau dont l'en-tête a été mal lu
  peut très bien tomber juste. C'est pourquoi un en-tête absent, ou qui a avalé une ligne
  de données, compte désormais comme un échec. Un en-tête simplement faux, lui, passe.
- **34 % des unités restent inconnues.** Les tableaux qui ne l'impriment nulle part.
- **82 à 86 % des observations ont une mesure identifiée.** Le reste porte `_Z`, faute de
  vocabulaire. Ça monte en remplissant `mapping/labels_to_codes.csv`, sans toucher au code.
- Les tableaux en paysage, et ceux qui courent sur deux pages, ne sont pas traités.
- Un tableau sans chiffres (liste de noms, annuaire) n'est pas converti : SDMX publie des
  nombres. L'interface compte ces pages et le dit, au lieu d'annoncer qu'aucun tableau
  n'a été trouvé.
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
make test
```

Ajouter une page : taper 10 à 30 cellules dans `truth/<pdf>_p<page>_truth.csv`, format dans
`truth/README.md`, puis relancer `make evaluate`.

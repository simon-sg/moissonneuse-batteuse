# Plan de correction — collisions codes INSEE / codes postaux (dept 35)

> Spécification d'implémentation autoportante. À exécuter phase par phase, dans l'ordre,
> **un commit par phase**. Lire CLAUDE.md d'abord (conventions réseau, discipline d'état,
> publication RUDI). Tout le code respecte le style existant : stdlib + `requests`,
> français dans les noms/docstrings, pas de nouvelle dépendance.

## 0. Contexte — le bug

Dans le département 35, certains codes INSEE de communes **hors** Rennes Métropole sont
identiques à des codes postaux de communes RM (ex. `35132` = code INSEE de Hirel, hors RM,
**et** code postal de Vezin-le-Coquet, RM). L'inverse existe aussi (`35120` = INSEE de
Gévezé, RM, **et** CP de Dol-de-Bretagne etc., hors RM).

Or le filtre de moisson pour une colonne `champ_iris` — `_ligne_est_rm()` dans
`src/filters/harvest.py:55` — appelle `est_valeur_commune_rm()`
(`src/filters/geographic.py:116`) qui accepte une valeur si elle est un CP RM **ou** un
code INSEE RM **ou** un nom de commune RM : l'union des interprétations, sans jamais
décider de la nature réelle (INSEE ou CP) de la colonne.

**Dégâts audités le 2026-07-11** (audit exécuté sur les fichiers de `data/`) :
- ~78 034 lignes hors RM publiées, dans 268 fichiers CSV répartis sur 121 datasets
  (colonnes de nature INSEE : les 11 codes « direction A » ci-dessous passent le filtre).
- 3 datasets ont une colonne **CP** taguée `champ_iris` (~353 lignes ambiguës) :
  dossiers `62da21021d3f7025e69ef260`-like — précisément `62da71c068871f4c54258c7c`
  (RNIC), `649519063913868fafc92210` (effectifs lycées pro), `6626ebfcabd5772d1ef5ea99`
  (Idéo langues collège).

**Décision produit actée** (2026-07-11, Simon) : l'ambiguïté des **CP partagés entre
communes voisines** (un CP RM couvre aussi des communes limitrophes hors RM, ex.
35190 Bécherel/Tinténiac — 47 communes concernées) est **acceptée**. Le filtre
`champ_cp` seul ne change PAS de sémantique. Ne pas « corriger » ça.

## 1. Les ensembles de codes (calculés depuis le référentiel officiel, à figer en Phase 1)

Référentiel : `https://geo.api.gouv.fr/departements/35/communes?fields=nom,code,codesPostaux`
(332 communes, 80 CP distincts).

- `INSEE_SEULEMENT_35` (299 codes) : codes INSEE du 35 qui ne sont le CP de personne.
  Ex. 35001, 35353. **Discriminants** : leur présence prouve une colonne de nature INSEE.
- `CP_SEULEMENT_35` (47 codes) : CP du 35 qui ne sont le code INSEE de personne.
  Ex. 35000, 35510, 35690. **Discriminants** : prouvent une colonne de nature CP.
- Codes en **désaccord RM** (14) — la valeur est RM sous une interprétation et hors RM
  sous l'autre ; c'est EUX qu'il faut arbitrer :
  - **Direction A** (11) — CP d'une commune RM / INSEE d'une commune hors RM :
    `35132` (Hirel), `35135` (Irodouër), `35136` (Janzé), `35150` (Lécousse),
    `35160` (Loutehel), `35170` (Mecé), `35190` (Monthault), `35200` (Moutiers),
    `35230` (Poilley), `35235` (Rannée), `35310` (Saint-Sauveur-des-Landes).
  - **Direction B** (3) — INSEE d'une commune RM / CP de communes hors RM :
    `35120` (Gévezé / CP Dol-de-Bretagne…), `35210` (Pacé / CP Châtillon-en-Vendelais…),
    `35240` (Le Rheu / CP Retiers…).
- Codes « double RM » (2), RM sous les deux interprétations, aucun arbitrage nécessaire :
  `35131` (INSEE L'Hermitage, CP Chartres-de-Bretagne/Pont-Péan), `35250` (INSEE
  Saint-Armel, CP Chevaigné/Saint-Sulpice — couvre aussi Mouazé hors RM : c'est
  l'ambiguïté CP-partagé acceptée, on garde).

---

## Phase 1 — Référentiel `src/conf/insee_cp_35.py`

Créer `src/conf/insee_cp_35.py`, **données inline commitées** (pas d'appel réseau à
l'import). Contenu :

```python
# Référentiel INSEE/CP du département 35 — 332 communes.
# Généré depuis https://geo.api.gouv.fr/departements/35/communes?fields=nom,code,codesPostaux
# le 2026-07-11. Pour régénérer : python3 src/conf/insee_cp_35.py > /tmp/nouveau.py
# (le bloc __main__ en bas du fichier ré-imprime le module à jour).

# code INSEE -> (nom officiel, tuple des codes postaux)
COMMUNES_35 = {
    "35001": ("Acigné", ("35690",)),
    ...  # les 332 entrées
}

INSEE_35 = frozenset(COMMUNES_35)
CP_35 = frozenset(cp for _, cps in COMMUNES_35.values() for cp in cps)
INSEE_SEULEMENT_35 = INSEE_35 - CP_35   # 299 codes — discriminant "colonne INSEE"
CP_SEULEMENT_35 = CP_35 - INSEE_35      # 47 codes  — discriminant "colonne CP"

# nom normalisé -> code INSEE (utiliser filters.geographic.normaliser au point d'usage ;
# ici stocker les noms déjà normalisés : minuscules, sans accents, -/_ -> espace)
NOMS_35_VERS_INSEE = { ... }            # 332 entrées, généré avec la même normalisation
```

Le générateur (bloc `if __name__ == "__main__":` du module) télécharge l'URL ci-dessus via
`connectors.http.session` et imprime le module complet — ainsi le fichier se régénère
lui-même. Vérifications à coder dans le générateur : 332 communes, tous les codes de
`conf.communes_rm.CODES_INSEE_RM` présents dans `INSEE_35`, tous les
`CODES_POSTAUX_RM` présents dans `CP_35` (sinon lever `SystemExit` avec message).

Attention normalisation des noms : réutiliser exactement `normaliser()` de
`filters/geographic.py` (import circulaire possible conf→filters→conf ; si c'est le cas,
dupliquer la petite fonction de normalisation dans le générateur uniquement, les données
commitées étant déjà normalisées).

**Commit 1** : `référentiel : codes INSEE/CP du dept 35 (conf/insee_cp_35.py)`.

---

## Phase 2 — Filtre « nature-aware » dans `src/filters/geographic.py`

### 2.1 Nouvelles fonctions

```python
from conf.insee_cp_35 import INSEE_35, CP_35, INSEE_SEULEMENT_35, CP_SEULEMENT_35, \
    NOMS_35_VERS_INSEE, COMMUNES_35

_RE_CEDEX = re.compile(r"\s+cedex(\s+\d+)?$")

def _normaliser_ville(ville: str) -> str:
    """normaliser() + suppression du suffixe 'cedex [n]'."""
    return _RE_CEDEX.sub("", normaliser(ville))

def detecter_nature_colonne(valeurs) -> str:
    """Détermine la nature d'une colonne de codes à partir d'un échantillon de valeurs
    (itérable de str). Retourne "insee", "cp" ou "inconnue".
    Ne compte que les valeurs à 5 chiffres commençant par "35" (seul le 35 est
    discriminant pour RM). Règle : majorité 3:1 requise, sinon "inconnue"."""
    n_insee = n_cp = 0
    for v in valeurs:
        v = str(v or "").strip()
        if len(v) == 5 and v.isdigit() and v.startswith("35"):
            if v in INSEE_SEULEMENT_35: n_insee += 1
            elif v in CP_SEULEMENT_35: n_cp += 1
    if n_insee > 3 * max(n_cp, 1): return "insee"
    if n_cp > 3 * max(n_insee, 1): return "cp"
    return "inconnue"

def classer_code_rm(valeur: str, ville: str | None = None) -> str:
    """Classe une valeur de colonne code-commune vis-à-vis de RM.
    Retourne : "rm" (RM quelle que soit l'interprétation), "hors_rm",
    "amb_insee" (RM seulement si la colonne est de nature INSEE — codes direction B),
    "amb_cp" (RM seulement si la colonne est de nature CP — codes direction A)."""
```

Logique de `classer_code_rm`, dans l'ordre :

1. `v = str(valeur).strip()`. Si `v == EPCI_SIREN_RM` → `"rm"`.
2. **Valeurs non "code à 5 chiffres"** — conserver le comportement actuel de
   `est_valeur_commune_rm` :
   - `len(v) >= 5 and v[:5].isdigit() and len(v) != 5` (IRIS 9 chiffres, codes suffixés) :
     `"rm"` si `est_iris_rm(v)` sinon `"hors_rm"`. (Un IRIS à 9 chiffres n'est jamais un
     CP : pas d'ambiguïté.)
   - Valeur non numérique : `"rm"` si `est_commune_rm(v)` sinon `"hors_rm"`
     (colonnes taguées iris qui contiennent en fait des noms — ça existe dans le backlog).
   - Sinon (numérique < 5 chiffres, vide…) : `"hors_rm"`.
3. **Valeur à 5 chiffres** : `insee_rm = v in CODES_INSEE_RM`, `cp_rm = v in CODES_POSTAUX_RM`.
   - Ni l'un ni l'autre → `"hors_rm"`.
   - Les deux (35131, 35250) → `"rm"`.
   - **Arbitrage par la ville d'abord**, si `ville` fournie et non vide :
     `code_ville = NOMS_35_VERS_INSEE.get(_normaliser_ville(ville))`.
     - Si `code_ville` et `code_ville not in CODES_INSEE_RM` → `"hors_rm"`
       (la ligne dit elle-même être une commune du 35 hors RM — cas Hirel).
     - Si `code_ville in CODES_INSEE_RM` : vérifier la **corroboration** pour se protéger
       des homonymes hors 35 (il existe un Saint-Armel dans le 56…) :
       `v == code_ville or v in COMMUNES_35[code_ville][1]` → `"rm"`.
       Si le code ne corrobore pas, ne pas trancher par la ville — continuer.
     - Ville inconnue du 35 (junk « Oui », noms de listes, communes d'autres depts) :
       continuer sans la ville.
   - `insee_rm` seul (v ∈ CODES_INSEE_RM, ∉ CODES_POSTAUX_RM) :
     - si `v not in CP_35` → `"rm"` (aucune lecture CP possible : 35353-type) ;
     - sinon → `"amb_insee"` (codes 35120/35210/35240).
   - `cp_rm` seul :
     - si `v not in INSEE_35` → `"rm"` (aucune lecture INSEE possible : 35510-type) ;
     - sinon → `"amb_cp"` (les 11 codes direction A, dont 35132 Vezin/Hirel).

```python
def est_code_rm(valeur, ville=None, nature="inconnue") -> bool:
    """Résolution complète : classer_code_rm + tranchage des ambigus par la nature.
    nature "inconnue" → défaut INSEE (sémantique du champ champ_iris : strict,
    on préfère perdre une ligne RM que publier une ligne hors RM)."""
    c = classer_code_rm(valeur, ville)
    if c == "rm": return True
    if c == "hors_rm": return False
    if c == "amb_insee": return nature != "cp"
    return nature == "cp"          # amb_cp
```

### 2.2 `est_valeur_commune_rm()`

La remplacer par un alias de compatibilité `est_code_rm(valeur)` (nature inconnue, pas de
ville) **et mettre à jour tous les appelants** pour passer ville/nature quand disponibles :
`src/filters/harvest.py:60`, `src/review.py:507`, imports dans
`src/connectors/analyseurs.py:48` et `src/harvest_batch.py:30`. Vérifier avec
`grep -rn est_valeur_commune_rm src/` qu'il ne reste aucun appel « aveugle » injustifié.

### 2.3 Câblage moisson — `src/filters/harvest.py`

- `_ligne_est_rm(...)` : ajouter les paramètres kw `nature_iris="inconnue"`. La branche
  `champ_iris` devient :
  ```python
  if champ_iris:
      ville = str(row.get(champ_ville, "")).strip() if champ_ville else None
      return est_code_rm(str(row.get(champ_iris, "")), ville, nature_iris)
  ```
  (Aujourd'hui `champ_ville` est ignoré dès que `champ_iris` existe — c'est précisément
  ce qu'on corrige. 154 des 212 candidats champ_iris ont aussi un champ_ville détecté.)
- `filtrer_csv(chemin, ...)` (dans `harvest_batch.py`), `filtrer_csv_bytes`,
  `filtrer_json`, et `filtrer_csv` de `main.py` : **pré-passe** avant le filtrage —
  si `champ_iris` résolu, lire jusqu'à 5 000 valeurs de la colonne
  (ré-itérer le reader puis rouvrir/re-parser ; les fichiers sont locaux, deux passes
  sont acceptables) → `nature = detecter_nature_colonne(...)` → passer `nature_iris=nature`
  à chaque appel `_ligne_est_rm`. Factoriser la pré-passe dans un helper de
  `filters/harvest.py` (`nature_champ_iris(fieldname, rows_sample)`), ne pas la dupliquer
  quatre fois.

### 2.4 Câblage découverte — `src/connectors/analyseurs.py`

`_compter_lignes_rm()` itère un flux (pas de seconde passe possible). Utiliser le
**comptage différé** :

```python
# branche champ_iris :
classes = {"rm": 0, "hors_rm": 0, "amb_insee": 0, "amb_cp": 0}
# ... dans la boucle :
c = classer_code_rm(code, ville=row.get(champ_ville) if champ_ville else None)
classes[c] += 1
# + compteurs discriminants n_insee_only / n_cp_only alimentés au fil de l'eau
#   (mêmes tests que detecter_nature_colonne)
# après la boucle :
nature = ("insee" if n_insee_only > 3*max(n_cp_only,1)
          else "cp" if n_cp_only > 3*max(n_insee_only,1) else "inconnue")
nb_rm = classes["rm"] + (classes["amb_cp"] if nature == "cp" else classes["amb_insee"])
```

(Garder la logique de complétion `champ_dep` + `zfill` existante en amont du classement.
Les buffers `exemples`/`premieres_lignes` peuvent rester approximatifs : n'y mettre que
des lignes classées `"rm"`.)

**Re-typage automatique** : si `nature == "cp"` et `champ_iris` détecté, le champ est en
réalité un code postal → dans le résultat retourné (`_construire_resultat`), déplacer :
`champ_cp = champ_iris` (seulement si `champ_cp` était None), `champ_iris = None`.
Ainsi les futurs candidats sont typés juste à la source.

### 2.5 Garde-fou d'en-tête — `deviner_champ_iris()`

Dans la boucle de secours sur `"insee" in e`, et en tête de fonction : **ne jamais**
retourner un en-tête contenant `"postal"` ou appartenant à `CHAMPS_CP`. (Le backlog
contient `champ_iris='code_postal'` et `'Code postal lieu de cours'` — probablement issus
du tag manuel, mais le garde-fou coûte deux lignes.)

**Commit 2** : `fix : filtre RM nature-aware — collisions codes INSEE/CP du dept 35`.

---

## Phase 3 — Tests unitaires

Créer `tests/test_collisions_insee_cp.py` (socle `unittest` existant, aucun réseau —
voir les autres fichiers de `tests/` pour le style). Cas minimum, avec ces asserts :

```python
# classer_code_rm — les 4 classes
classer_code_rm("35353") == "rm"            # INSEE Vezin, jamais un CP
classer_code_rm("35510") == "rm"            # CP Cesson, jamais un INSEE
classer_code_rm("35132") == "amb_cp"        # CP Vezin / INSEE Hirel
classer_code_rm("35120") == "amb_insee"     # INSEE Gévezé / CP Dol
classer_code_rm("35131") == "rm"            # double RM (L'Hermitage / Chartres-de-B.)
classer_code_rm("35300") == "hors_rm"       # Fougères, INSEE et CP, jamais RM
classer_code_rm("99999") == "hors_rm"
classer_code_rm("243500139") == "rm"        # EPCI
classer_code_rm("353530102") == "rm"        # IRIS 9 chiffres de Vezin
classer_code_rm("351320000") == "hors_rm"   # IRIS 9 chiffres de Hirel — PAS d'ambiguïté

# arbitrage ville
classer_code_rm("35132", "Hirel") == "hors_rm"
classer_code_rm("35132", "HIREL") == "hors_rm"
classer_code_rm("35132", "Vezin-le-Coquet") == "rm"     # corroboration CP
classer_code_rm("35120", "Dol-de-Bretagne") == "hors_rm"
classer_code_rm("35120", "Gévezé") == "rm"
classer_code_rm("35132", "Oui") == "amb_cp"             # ville junk → inchangé
classer_code_rm("35000", "Rennes cedex 9") == "rm"      # suffixe cedex normalisé

# est_code_rm — tranchage par nature
est_code_rm("35132", nature="insee") is False           # le bug d'origine, corrigé
est_code_rm("35132", nature="cp") is True
est_code_rm("35132") is False                           # défaut inconnu = strict INSEE
est_code_rm("35120", nature="cp") is False
est_code_rm("35120", nature="insee") is True

# detecter_nature_colonne
detecter_nature_colonne(["35001","35047","35238","35132"]) == "insee"
detecter_nature_colonne(["35000","35510","35690","35120"]) == "cp"
detecter_nature_colonne(["35132","35120"]) == "inconnue"    # que des ambigus
detecter_nature_colonne(["75056","69123"]) == "inconnue"    # hors 35 : non discriminant

# deviner_champ_iris — garde-fou
deviner_champ_iris(["code_postal", "nom"]) is None
```

Ajouter aussi un test d'intégration de `_ligne_est_rm` sur des dict-rows simulant le cas
RNIC (colonne CP taguée iris) et le cas fichier-des-décès (colonne INSEE, ville Hirel).

Lancer : `python3 -m unittest discover tests/` — les ~70 tests existants doivent rester
verts (certains testent peut-être `est_valeur_commune_rm` : adapter leurs asserts si le
nouveau comportement strict est la cause, ne PAS affaiblir le nouveau comportement).

**Commit 3** : `tests : collisions INSEE/CP (classer_code_rm, nature, arbitrage ville)`.

---

## Phase 4 — Rattrapage des données déjà moissonnées (offline)

Créer `src/reanalyser_faux_positifs.py`, exécutable standalone. **Aucun re-téléchargement**
(voir mémoire projet : pipeline incrémental exigé) : le filtre actuel étant un sur-ensemble
strict du filtre corrigé, il n'y a que des lignes à RETIRER des fichiers existants.

### 4.1 Comportement

- Par défaut : **dry-run** — imprime le rapport, ne modifie rien.
  `--appliquer` pour muter. `--dossier <nom>` pour cibler un seul dataset (debug).
- Construire la table dossier → config champs : entrées de `decouverte.json["candidats"]`
  (clé `dossier`, sinon `dataset_id`) + `conf/datasets.py::DATASETS`. Ne traiter que les
  configs ayant `champ_iris` (le filtre `champ_cp`/ville/adresse/… n'a pas changé).
- Pour chaque `data/<dossier>/` existant :
  - Fichiers cibles : `*.csv` + `*.json`, en excluant `rudi_metadata.json`,
    `wms_service.json`, `*_viewer.html`, `*_map.html`.
  - CSV : détecter encodage/délimiteur comme `filters/harvest.py` (`_detecter_encodage`,
    `_detecter_delimiteur`), résoudre les noms de champs via `_resoudre_champs`.
    Pré-passe nature (helper de Phase 2), puis garder les lignes où
    `_ligne_est_rm(row, ..., nature_iris=nature)` est vrai.
  - JSON : liste de dicts (format `save_json`) — même logique.
  - Réécrire **uniquement si des lignes ont été retirées** : CSV en utf-8, avec le même
    délimiteur et le même ordre de colonnes (`csv.DictWriter(f, fieldnames=entetes,
    delimiter=delim)` ; `extrasaction="ignore"`).
- État (respecter « Discipline d'état » de CLAUDE.md — sauvegarde incrémentale) :
  - Si au moins un fichier du dossier a changé : recalculer `nb_rm` (somme des lignes
    conservées), mettre à jour l'entrée du state (`state.json` — utiliser
    `state.construire_index_dossier()` pour retrouver l'entrée), poser
    `rudi_publie: False`. Ne PAS toucher aux `last_modified` (le skip-si-inchangé doit
    continuer à fonctionner). Le `nb_rm` par-ressource dans `ressources{}` peut rester
    tel quel (il ne sert qu'au skip, qui se fonde sur `last_modified`).
- **Détection de colonne CP mal taguée** : si la pré-passe donne `nature == "cp"` pour un
  `champ_iris`, ne PAS re-filtrer ce dossier ; le lister dans une section dédiée du
  rapport « à re-typer et re-moissonner » (attendu : les 3 datasets cités en §0).
  Avec `--appliquer` : corriger le candidat dans `decouverte.json`
  (`champ_cp = champ_iris`, `champ_iris = None` — muter via chargement/sauvegarde du
  fichier, pattern de `discover.py`), et **supprimer l'entrée state** de ces dataset_ids
  pour forcer leur re-moisson au prochain `harvest_batch.py` (re-téléchargement ciblé de
  3 datasets : acceptable et nécessaire, des lignes RM légitimes ont pu être perdues).
- **Datasets tombant à 0 ligne RM** : ne rien supprimer ; les lister dans une section
  « candidats probablement faux positifs — à exclure manuellement via /examen ou menu 2 ».
- **Colonnes iris aberrantes** (nature `"inconnue"` avec 0 discriminant, en-têtes du type
  `'NOM'`, `'Académie'`, `'nb_ucd'`, `'nofinesset'`) : lister dans une section « tag
  champ_iris douteux — revue manuelle », ne pas re-filtrer automatiquement.
- Rapport final : par dataset — lignes avant/après, retirées, nature détectée ; totaux.
  Ordre de grandeur attendu : **~78 000 lignes retirées, ~121 datasets modifiés**. Si le
  total s'écarte fortement (< 50 000 ou > 120 000), s'arrêter et investiguer avant
  d'appliquer.

### 4.2 Enchaînement post-application

Dans l'ordre, après `--appliquer` :
1. `python3 src/catalogue.py` — régénère catalogue + visionneuses depuis les fichiers.
2. Nœud RUDI démarré (carte dashboard ou `rudi_node.py`), puis
   `python3 src/publish_rudi.py` — republie tout ce qui est `rudi_publie: false`.
   (`rudi_metadata.json` n'embarque ni taille ni checksum : réuploader les fichiers
   re-filtrés suffit, pas besoin de régénérer les métadonnées.)
3. Si la base monitoring est configurée (`src/conf/monitor_db.json` présent) :
   `python3 src/monitor.py --import-data --refresh`.

### 4.3 Intégration menu (optionnel mais souhaitable)

Ajouter une action « Ré-analyser les faux positifs INSEE/CP » dans la section Maintenance
de `src/cli.py` (elle appelle le script en dry-run puis demande confirmation avant
`--appliquer`), et l'exposer dans le dashboard : ajout au dict `ACTIONS` de
`dashboard.py` **et** au tableau `ACTIONS` de `src/static/dashboard.js` (voir CLAUDE.md
« Tableau de bord web »).

**Commit 4** : `rattrapage : re-filtrage offline des faux positifs INSEE/CP (~78k lignes)`.

---

## Phase 5 — Backlog découverte (conditionnelle)

- `src/discover.py` contient un diff **non commité** (fonction
  `reanalyser_a_examiner_tabulaire()`, travail en cours de Simon). **Ne pas l'écraser ni
  le commiter dans les commits 1-4.** Une fois cette fonction terminée/commitée par
  Simon, la lancer : elle repassera le backlog `a_examiner` avec la cascade corrigée
  (des JDD classés « 0 ligne RM » avec l'ancienne cascade peuvent devenir candidats, et
  inversement).
- Les entrées de `decouverte.json["vus"]` (déjà décidées) ne sont pas re-scannées :
  choix assumé, cohérent avec le fonctionnement incrémental.

---

## Vérification finale (obligatoire avant de conclure)

1. `python3 -m unittest discover tests/` → tout vert.
2. Dry-run : `python3 src/reanalyser_faux_positifs.py` → totaux plausibles (voir §4.1).
3. Après `--appliquer`, sondages concrets :
   - `grep -ci hirel data/62da71c068871f4c54258c7c/*.csv` — Hirel absent des fichiers
     re-filtrés (ce dossier étant re-typé CP, vérifier plutôt sur un dossier direction A,
     ex. `grep -ci "hirel\|janze\|irodouer" data/66d997ba09e4429d7f846249/*.csv` → 0).
   - Vérifier qu'une ligne légitime Vezin-le-Coquet (INSEE 35353) est toujours présente
     dans un des fichiers re-filtrés.
4. `python3 src/catalogue.py` sans erreur ; ouvrir un `_viewer.html` régénéré.
5. Publication : `publish_rudi.py` — vérifier dans la sortie que les datasets modifiés
   repassent `rudi_publie: true`.

## Pièges connus (résumé)

- Encodages latin-1 et délimiteur `;` fréquents dans les CSV filtrés — toujours passer
  par les helpers existants de `filters/harvest.py`.
- Les colonnes « ville » contiennent parfois du bruit (Oui/Non, codes, noms de listes
  électorales, « Rennes cedex ») — c'est pour ça que l'arbitrage ville ne s'applique que
  si le nom matche une commune du 35, avec corroboration par le code pour les noms RM.
- La publication RUDI est sérialisée par un verrou module-level — ne pas paralléliser
  la republication.
- Ne jamais re-télécharger massivement : tout le rattrapage se fait sur les fichiers de
  `data/` (seuls les 3 datasets re-typés CP sont re-moissonnés).
- Le diff non commité de `src/discover.py` appartient à Simon — ne pas y toucher.

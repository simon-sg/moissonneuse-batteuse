# Dashboard : nœud source + portail hybride — état par module, patché/natif, démarrage/arrêt

> Plan d'implémentation pour agent exécutant. Portée : `src/dashboard.py`, `src/connectors/rudi_node.py`,
> `src/connectors/rudi_portal.py`, `src/harvest_auto.py`, `src/static/page_dashboard.html`, nouveau
> `src/static/page_infrastructure.html`. Aucune action destructrice, lecture seule sauf sections C et D
> (démarrage/arrêt explicitement demandées).

## Contexte

Le dashboard (`src/dashboard.py`) et le pipeline (`src/harvest_auto.py`) datent de l'époque
mono-conteneur Podman. Depuis (voir `PLAN_ENVIRONNEMENT_RUDI_SOURCE.md`,
`PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md`), l'environnement réel a changé :

- **Le nœud actif est le nœud source** : 4 process natifs (catalog:4030, storage:4031, manager:4032,
  jwtauth:4033), pas un conteneur. `src/conf/rudi_node.json` pointe déjà dessus. Un script
  `/media/simon/DATA4T/Dev/rudi-node-build/start-source-node.sh` sait les démarrer (+ un Mongo Podman
  dédié `rudi-source-mongo`). Le nœud Podman historique (`rudinode`, 3030-3032) est conservé mais
  **hors chaîne** — la publication ne le vise plus.
- **Le portail est hybride** : stack Docker Compose ROOB (11 microservices + front + Traefik + infra),
  où certains services tournent sur l'image officielle (`:v3.3.12`/`:v3.4.0`) et d'autres sur une image
  reconstruite depuis les sources patchées (`:source`), voire en process natif hors conteneur (konsult,
  étape 3 du plan environnement).

Trois angles morts concrets :
1. **Aucune visibilité par module** — le dashboard ne montre qu'un badge unique « Nœud RUDI » et un
   badge unique « Portail RUDI », sans dire quel module tourne ni s'il est patché ou vanilla.
2. **Le pipeline (`harvest_auto.py::_demarrer_noeud_rudi()`) réveille encore le conteneur Podman
   `rudinode`** (`rudi_node.statut_conteneur()`/`demarrer_conteneur()`), alors que la cible réelle de
   publication (`rudi_node.json`) est le nœud source natif — le warm-up ne réveille pas ce qu'il faudrait.
3. **Aucun contrôle démarrage/arrêt pour le nœud** dans le dashboard (le portail, lui, en a déjà un sur
   la page d'accueil).

Ce plan traite les trois.

## Vérifications faites sur la machine (à réutiliser telles quelles, ne pas re-découvrir)

- `src/conf/rudi_node.json` → `{"url": "http://localhost:4032", "url_catalog": "http://localhost:4030/catalog", ...}`.
- 4 sous-modules du nœud source clonés dans
  `/media/simon/DATA4T/Dev/rudi-node-build/rudi-node-container/src/{rudi-catalog,rudi-storage,rudi-manager,rudi-jwtauth}`.
  Le remote par défaut de chacun est `irisa` (pas `main`). `git rev-list --count origin/HEAD..HEAD`
  (comparaison de refs locales, **aucun accès réseau**) donne **1** pour catalog
  (branche `feat/enable-organization-update`) et storage (`pr/cors-credentials-reflection`), **0** pour
  manager et jwtauth (checkout direct sur `irisa`) — cohérent avec l'état documenté à l'étape 7bis de
  `PLAN_ENVIRONNEMENT_RUDI_SOURCE.md`.
- `/media/simon/DATA4T/Dev/rudi-node-build/start-source-node.sh` : vérifie/démarre Mongo (`rudi-source-mongo`,
  Podman, port 4027 — **échoue et `exit 1`** si Mongo ne répond pas, message suggérant
  `podman start rudi-source-mongo`), régénère la config des 4 modules, puis les lance chacun en tâche de
  fond (`&`) et **se termine lui-même** (le script ne fait pas de `wait` — les 4 process natifs survivent
  après la fin du script). Donc `subprocess.run(["bash", script], timeout=...)` bloque juste le temps du
  script (quelques secondes), pas le temps de vie des 4 process. Le script ne vérifie **pas** si les
  process tournent déjà avant de les relancer (double-lancement = échec de bind sur les ports déjà pris,
  pas une erreur globale) — à gater côté appelant.
- Portail ROOB : services dans `docker-compose-rudi.yml`/`docker-compose-network.yml` :
  `registry, gateway, acl, apigateway, strukture, kalim, konsult, kos, projekt, selfdata, konsent, portail`
  (+ infra : `reverse-proxy, database, mailhog, dataverse, solr, magnolia`), conteneurs nommés
  `rudiplatform-<service>-1`. Images `${rudi_version}` (`v3.3.12` actuellement) sauf `kalim` épinglé en
  dur `v3.4.0` ; `docker-compose-source.yml` peut override un service en `:source` (aucun actif
  actuellement, vérifié dans le fichier).
- `rudi-portal-source` (monorepo du build) : même primitive git fonctionne (`origin/HEAD` = `origin/main`),
  informatif seulement (un monorepo ne dit pas quel microservice précis a été patché).
- `harvest_auto.py:48-73` (`_demarrer_noeud_rudi()`) et `src/connectors/rudi_node.py` (`statut_conteneur()`,
  `demarrer_conteneur()`, `CONTENEUR_RUDI = "rudinode"`) : lus en entier, confirment le point 2 ci-dessus.
- `src/dashboard.py` : carte « Nœud RUDI » = `_etat_noeud()` (l.369) + `noeuds-container` dans
  `page_dashboard.html` (l.44-49, JS `actualiserNoeud()` ~l.263-280) — pas de bouton. Carte « Portail
  RUDI » a déjà ses boutons (`basculerPortail()`, `/api/portail/demarrer`/`arreter`, dashboard.py l.417-424,
  do_POST l.1052-1057) — patron à recopier pour le nœud.

## Conception

### A. Détection « patché / natif » par module

Deux mécanismes différents selon la nature du module (pas de logique unifiée artificielle) :

1. **Nœud source (process natifs, pas d'image)** → état du clone git du sous-module :
   `git rev-list --count origin/HEAD..HEAD` sur le dossier du sous-module.
   - `0` → **natif** (checkout vanilla de la branche par défaut)
   - `>0` → **patché** (n commits locaux non présents sur `origin/HEAD`)
   - impossible à déterminer (dossier absent, pas un repo git, `git` absent) → **inconnu**, jamais d'erreur.
2. **Portail (conteneurs)** → tag d'image réellement déployé (seule vérité qui compte, cf. piège #13 de
   `PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md`) :
   - tag `:source` → **patché**
   - tag `:vX.Y.Z` → **natif**, version affichée (utile pour repérer kalim épinglé différemment)
   - **mode hybride natif** (process Java hors conteneur, étape 3 du plan environnement) : détecté par
     `pgrep -af "rudi-microservice-"` — un module qui matche est **natif hybride**, indépendamment de
     l'état de son conteneur (qui peut être arrêté intentionnellement, cf. konsult).

### B. Nouvelles fonctions connecteurs (lecture seule, statut)

**`src/connectors/rudi_node.py`** :
- `_etat_git(chemin: str) -> dict` : helper générique (réutilisé aussi par `rudi_portal.py`), retourne
  `{"disponible": bool, "branche": str|None, "commits_avance": int|None, "patche": bool|None}`. `patche`
  vaut `None` (jamais `False` par défaut) quand `commits_avance` n'a pas pu être déterminé.
- `_CHEMIN_NOEUD_SOURCE = "/media/simon/DATA4T/Dev/rudi-node-build/rudi-node-container/src"`,
  `MODULES_NOEUD_SOURCE = {"catalog": {"dossier": "rudi-catalog", "port": 4030}, "storage": {...4031},
  "manager": {...4032}, "jwtauth": {...4033}}` (ports en dur, cohérent avec `CONTENEUR_RUDI` déjà en dur
  dans ce fichier — faits d'infra documentés dans CLAUDE.md).
- `etat_modules_noeud_source(conf: dict | None) -> list[dict]` : hôte dérivé de `conf["url"]` (repli
  `"localhost"` si `conf` est `None`), sonde chaque port (`session.get(..., timeout=3).status_code < 500`
  → up ; pour `"manager"` réutiliser **`noeud_pret(conf)`** existant, qui sonde déjà `/manager/conf` — le
  seul endpoint qui distingue vraiment « prêt » de « répond mais pas encore opérationnel »), combiné avec
  `_etat_git()` sur `_CHEMIN_NOEUD_SOURCE/<dossier>`. Ne lève jamais.
- **`statut_conteneur()` et `demarrer_conteneur()`/`arreter_conteneur()` prennent un paramètre
  `nom: str = CONTENEUR_RUDI`** (compatible avec l'appelant existant `harvest_auto.py`, qui après la
  section C ne les appellera plus directement pour le nœud source — mais reste utile pour afficher/piloter
  `rudinode` (hors chaîne) et `rudi-source-mongo` (Mongo dédié du nœud source) séparément).

**`src/connectors/rudi_portal.py`** :
- `_CHEMIN_PORTAIL_SOURCE = "/media/simon/DATA4T/Dev/rudi-portal-source"`.
- `MODULES_PORTAIL` (les 12 microservices/front) et `INFRA_PORTAIL` (les 6 infra, affichées sans notion
  patché/natif — images tierces).
- `_conteneurs_portail() -> dict[str, dict]` : **un seul** appel
  `docker ps -a --filter name=rudiplatform- --format '{{.Names}}\t{{.Image}}\t{{.State}}'` (pas un appel
  par service), parse le nom via `re.match(r"rudiplatform-(.+)-\d+$", nom)`.
- `_classifier_image(image: str | None) -> dict` : `:source$` → patché ; `:v?(\d+\.\d+\.\d+)$` → natif +
  version ; sinon inconnu.
- `_processus_natifs() -> set[str]` : un seul `pgrep -af "rudi-microservice-"`, regex
  `rudi-microservice-([a-z]+)`, ne garde que les noms dans `MODULES_PORTAIL`.
- `etat_modules_portail() -> list[dict]` : combine les trois ; le mode natif hybride prime sur l'état du
  conteneur.
- `etat_infra_portail() -> list[dict]` : même table restreinte à `INFRA_PORTAIL`, `{"module","etat","image"}`.
- `etat_git_portail_source() -> dict` : `from connectors.rudi_node import _etat_git` puis
  `_etat_git(_CHEMIN_PORTAIL_SOURCE)` — informatif (monorepo, pas par microservice).

Aucune de ces fonctions ne doit lever — mêmes garde-fous `try/except FileNotFoundError, subprocess.TimeoutExpired`
que `statut_conteneur()` existant dans les deux fichiers (patron à copier).

### C. Connexion pipeline → nœud source (corrige le warm-up cassé)

**`src/connectors/rudi_node.py`**, nouvelles fonctions de cycle de vie du nœud source natif :

```python
import time  # à ajouter aux imports du fichier

_SCRIPT_NOEUD_SOURCE = "/media/simon/DATA4T/Dev/rudi-node-build/start-source-node.sh"
_PATTERN_PROCESSUS_NOEUD_SOURCE = r"run-rudinode-(catalog|storage|manager|jwtauth)\.js"

def noeud_source_actif(conf: dict) -> bool:
    """Au moins un des 4 modules du nœud source répond déjà (gate anti double-lancement —
    le script ne vérifie pas lui-même si les process tournent déjà)."""
    return any(m["up"] for m in etat_modules_noeud_source(conf))

def demarrer_noeud_source(conf: dict) -> tuple[bool, str]:
    """Démarre le nœud RUDI source : Mongo dédié (Podman, rudi-source-mongo) si besoin, puis
    les 4 process natifs via start-source-node.sh (le script se termine seul, les process
    survivent en tâche de fond — subprocess.run() ne bloque pas leur durée de vie)."""
    if noeud_source_actif(conf):
        return True, "Nœud source déjà démarré."
    mongo = statut_conteneur("rudi-source-mongo")
    if mongo.get("etat") != "running":
        ok, msg = demarrer_conteneur("rudi-source-mongo")
        if not ok:
            return False, f"MongoDB du nœud source n'a pas pu démarrer : {msg}"
        time.sleep(2)
    try:
        r = subprocess.run(["bash", _SCRIPT_NOEUD_SOURCE], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return False, f"Script introuvable : {_SCRIPT_NOEUD_SOURCE}"
    except subprocess.TimeoutExpired:
        return False, "start-source-node.sh : délai dépassé (60s)."
    if r.returncode != 0:
        return False, ((r.stderr or r.stdout).strip()[-2000:] or "Échec du démarrage du nœud source.")
    return True, "Nœud source démarré (logs : rudi-node-container/source-logs/)."

def arreter_noeud_source() -> tuple[bool, str]:
    """Arrête les 4 process natifs (pkill par motif — reprend la commande déjà documentée en
    fin de start-source-node.sh). MongoDB n'est volontairement pas arrêté (partagé, coût nul à
    laisser tourner)."""
    try:
        subprocess.run(["pkill", "-f", _PATTERN_PROCESSUS_NOEUD_SOURCE],
                        capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return False, "pkill introuvable."
    return True, "Nœud source arrêté (MongoDB laissé tel quel)."
```

(`pkill` retourne 1 quand rien ne matche — ne pas traiter comme un échec, l'opération est idempotente.)

**`src/harvest_auto.py`** — réécrire `_demarrer_noeud_rudi()` (l.48-73) pour cibler le nœud source au lieu
du conteneur Podman :

```python
def _demarrer_noeud_rudi() -> None:
    """Démarre le nœud RUDI source (process natifs) si nécessaire et attend qu'il réponde.
    N'échoue jamais : si le nœud reste indisponible, la publication de cette exécution
    sera simplement différée (rudi_publie=false, rattrapée au prochain run)."""
    conf = rudi_node.charger_conf_rudi()
    if not conf:
        print("[Nœud RUDI] non configuré (src/conf/rudi_node.json absent) — publication ignorée cette fois.")
        return

    if rudi_node.noeud_source_actif(conf):
        print("[Nœud RUDI] nœud source déjà actif.")
    else:
        print("[Nœud RUDI] nœud source non démarré — démarrage...")
        ok, message = rudi_node.demarrer_noeud_source(conf)
        print(f"[Nœud RUDI] {message}")
        if not ok:
            return

    for tentative in range(1, _NOEUD_RUDI_TENTATIVES + 1):
        if rudi_node.noeud_pret(conf):
            print(f"[Nœud RUDI] prêt (après {tentative} tentative(s)).")
            return
        time.sleep(_NOEUD_RUDI_DELAI_S)
    print(f"[Nœud RUDI] toujours indisponible après {_NOEUD_RUDI_TENTATIVES * _NOEUD_RUDI_DELAI_S}s — "
          "la publication sera tentée quand même, puis rattrapée au prochain run si elle échoue.")
```

Mettre aussi à jour le docstring du module (l.11-13, « Démarrage du nœud RUDI local (conteneur Podman) »)
qui décrit encore l'ancien comportement.

Budget d'attente inchangé (`_NOEUD_RUDI_TENTATIVES=20 * _NOEUD_RUDI_DELAI_S=3` = 60s) — même ordre de
grandeur que l'ancien, le script échelonne déjà ses propres démarrages avec des `sleep 2`.

### D. Dashboard — page `/infrastructure` (statut détaillé, lecture seule) + contrôles nœud/portail

**Nouvelle page de statut détaillé** — suit le patron déjà en place pour `/portail` (page statique servie
via `_charger_page()`, topbar injecté) :

- **`src/static/page_infrastructure.html`** (nouveau, calqué sur `page_portail.html`) : trois sections —
  1. *Nœud source* : tableau Module / Port / État / Git (branche + badge patché/natif/inconnu) pour les
     4 process, ligne compacte pour `rudi-source-mongo` (Podman).
  2. *Nœud Docker (hors chaîne)* : badge unique via `statut_conteneur("rudinode")` — informatif seulement,
     pas de contrôle ici (nœud délibérément mis de côté, cf. CLAUDE.md).
  3. *Portail hybride* : tableau Module / Mode (conteneur / natif hybride / absent) / Image ou version /
     badge patché-natif-hybride-inconnu pour les 12 modules RUDI, ligne compacte pour les 6 infra
     (`etat_infra_portail()`), ligne info « checkout source : branche X, n commit(s) non fusionné(s) »
     (`etat_git_portail_source()`).
  - Badges : réutiliser les classes `.badge` existantes (`idle/running/termine/warn`, `dashboard.css`) ;
    ajouter un petit bloc `<style>` local (comme `page_portail.html` le fait déjà pour ses propres
    besoins) pour des variantes neutres patché/natif (ni bon ni mauvais signal en soi).
  - JS : `fetch("/api/infrastructure")` au chargement + `setInterval` 20 s (cette page fait plusieurs
    appels subprocess par rafraîchissement — un peu plus large que les 15 s du reste évite de les
    empiler). **Cette page reste en lecture seule** — les contrôles démarrage/arrêt vivent sur la page
    d'accueil (section suivante) pour ne pas dupliquer la logique de bouton à deux endroits ; ajouter
    simplement un lien retour « ← Tableau de bord ».

- **`src/dashboard.py`** :
  - `_etat_infrastructure() -> dict` (à côté de `_etat_noeud()`, l.369) :
    ```python
    conf = rudi_node.charger_conf_rudi()
    {
      "noeud_source": {
        "configure": bool(conf),
        "mongo": rudi_node.statut_conteneur("rudi-source-mongo"),
        "modules": rudi_node.etat_modules_noeud_source(conf) if conf else [],
      },
      "noeud_docker": rudi_node.statut_conteneur("rudinode"),
      "portail": {
        "modules": rudi_portal.etat_modules_portail(),
        "infra": rudi_portal.etat_infra_portail(),
        "checkout_source": rudi_portal.etat_git_portail_source(),
      },
    }
    ```
  - `PAGE_INFRASTRUCTURE_HTML = _charger_page("page_infrastructure.html", "infrastructure")` (à côté des
    `PAGE_*_HTML` existants, ~l.1125).
  - Routes `GET /infrastructure` → `PAGE_INFRASTRUCTURE_HTML`, `GET /api/infrastructure` →
    `_etat_infrastructure()`, ajoutées dans `do_GET` à côté de `/portail`/`/api/portail`.
  - `_html_topbar()` : ajouter `("infrastructure", "/infrastructure", "Infrastructure", False)` à `liens`,
    et transformer les `<span class="topbar-pill" id="tb-noeud">`/`id="tb-portail"` (l.75, l.77) en
    `<a href="/infrastructure" class="topbar-pill" ...>` (sur le modèle de `tb-job`/`tb-examen-pill`,
    déjà des `<a>`). `tb-superset` reste un `<span>` (hors sujet). Aucun changement à
    `dashboard.js::actualiserTopBar()` — sémantique des pills inchangée, seule la cible de clic change.

**Démarrage/arrêt du nœud source depuis la page d'accueil** (`page_dashboard.html`) — étend la carte
« Nœud RUDI » existante (l.44-49) sur le même patron que la carte « Portail RUDI » (boutons déjà présents,
l.61-68, JS `basculerPortail()`) :

- `_etat_noeud()` (dashboard.py l.369) : ajouter un champ `"actif": bool` = `rudi_node.noeud_source_actif(conf)`
  (distinct de `"pret"`, qui reste « manager pleinement opérationnel ») pour piloter l'étiquette du bouton.
- Nouveaux handlers dashboard.py (à côté de `_traiter_portail_demarrer/arreter`, l.417-424) :
  ```python
  def _traiter_noeud_demarrer() -> tuple[int, dict]:
      conf = rudi_node.charger_conf_rudi()
      if not conf:
          return 400, {"ok": False, "message": "Nœud RUDI non configuré."}
      ok, message = rudi_node.demarrer_noeud_source(conf)
      return (200 if ok else 500), {"ok": ok, "message": message}

  def _traiter_noeud_arreter() -> tuple[int, dict]:
      ok, message = rudi_node.arreter_noeud_source()
      return (200 if ok else 500), {"ok": ok, "message": message}
  ```
- Routes `do_POST` (à côté de `/api/portail/demarrer`/`arreter`, l.1052-1057) : `POST /api/noeud/demarrer`,
  `POST /api/noeud/arreter`.
- `page_dashboard.html` : dans le bloc `noeuds-container` (JS `actualiserNoeud()`, ~l.263-280), ajouter un
  bouton `<button id="btn-noeud" onclick="basculerNoeud()">` à côté du lien « Ouvrir », libellé
  Démarrer/Arrêter selon `n.actif` (badge continue d'utiliser `n.pret` pour « opérationnel/injoignable »,
  ajouter un état intermédiaire « démarrage… » quand `n.actif && !n.pret`) ; ajouter aussi un lien
  `<a href="/infrastructure">Détails →</a>`. Nouvelle fonction JS `basculerNoeud()` copiée de
  `basculerSuperset()`/`basculerPortail()` (variable de garde `noeudActionEnCours`, désactive le bouton
  pendant l'appel, `POST /api/noeud/${action}`, notifie, ré-affiche).
- Le portail a déjà ses boutons (`basculerPortail()`) — **aucun changement fonctionnel là**, seulement la
  cohérence visuelle avec le nouveau bouton du nœud (même classes CSS, même emplacement dans la carte).

## Fichiers touchés

| Fichier | Nature du changement |
|---|---|
| `src/connectors/rudi_node.py` | + `_etat_git()`, `MODULES_NOEUD_SOURCE`, `etat_modules_noeud_source()`, `noeud_source_actif()`, `demarrer_noeud_source()`, `arreter_noeud_source()` ; `statut_conteneur()`/`demarrer_conteneur()`/`arreter_conteneur()` prennent un paramètre `nom` ; `import time` |
| `src/connectors/rudi_portal.py` | + `MODULES_PORTAIL`, `INFRA_PORTAIL`, `_conteneurs_portail()`, `_classifier_image()`, `_processus_natifs()`, `etat_modules_portail()`, `etat_infra_portail()`, `etat_git_portail_source()` |
| `src/harvest_auto.py` | `_demarrer_noeud_rudi()` réécrite pour cibler le nœud source natif ; docstring module mis à jour |
| `src/dashboard.py` | + `_etat_infrastructure()`, `PAGE_INFRASTRUCTURE_HTML`, routes `/infrastructure`+`/api/infrastructure`, entrée nav, pills cliquables, `_traiter_noeud_demarrer/arreter`, routes `/api/noeud/demarrer`/`arreter`, `_etat_noeud()` enrichi (`actif`) |
| `src/static/page_infrastructure.html` | nouveau — calqué sur `page_portail.html` |
| `src/static/page_dashboard.html` | carte « Nœud RUDI » : bouton démarrer/arrêter + lien Détails, JS `basculerNoeud()` |

## Hors scope (délibérément non traité ici)

- Pas de contrôle démarrage/arrêt **par module** (ni pour le nœud source ni pour le portail) — seulement
  au niveau du nœud entier / du stack entier, comme le fait déjà le portail aujourd'hui.
- Pas de retouche à `menage_rudi_one_shot()`/`menage_organisations()` ni à la cible de publication —
  ce plan ajoute de la visibilité et corrige le warm-up, il ne change pas ce qui est publié où.

## Vérification

1. `python3 -m unittest discover tests/` — aucune régression ; vérifier l'absence d'import circulaire
   entre `rudi_portal.py` et `rudi_node.py` (import à sens unique : portal → node).
2. `python3 src/dashboard.py`, dans le navigateur :
   - `/infrastructure` charge sans erreur console avec la stack **arrêtée** (état constaté pendant
     l'exploration : ports 4030-4033 et conteneurs ROOB down) → toutes les lignes en état « arrêté/absent »
     propre, jamais d'exception ni de tableau vide silencieux.
   - Page d'accueil (`/`) : cliquer « Démarrer » sur la carte Nœud RUDI → les 4 process natifs démarrent
     (vérifier via `/infrastructure` ou `ps aux | grep rudinode-`), le badge passe par « démarrage… » puis
     « opérationnel ». Cliquer « Arrêter » → les 4 process disparaissent (Mongo reste up).
   - Démarrer le portail ROOB (bouton existant) et vérifier la table `/infrastructure` : modules en
     conteneur natif (`vX.Y.Z`), sauf override `:source` ou process natif (konsult) actif.
   - Cliquer les pills « Nœud »/« Portail » de la topbar depuis n'importe quelle page → amène sur `/infrastructure`.
3. `python3 src/harvest_auto.py` (ou juste appeler `_demarrer_noeud_rudi()` isolément) avec le nœud source
   arrêté au départ : vérifie qu'il démarre le nœud source (pas de tentative sur Podman `rudinode`), attend
   qu'il soit prêt, puis laisse `cli.executer_pipeline_complet()` publier normalement.
4. Revue manuelle (agent d'implémentation moins puissant, relu ensuite par l'agent superviseur) :
   `_etat_git()` / `_conteneurs_portail()` / `_processus_natifs()` / `demarrer_noeud_source()` /
   `arreter_noeud_source()` ne lèvent réellement jamais (chemin absent, binaire absent, timeout, script
   absent) ; le paramètre ajouté à `statut_conteneur()`/`demarrer_conteneur()`/`arreter_conteneur()` ne
   casse pas les appels existants sans argument.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Pipeline de moisson d'open data pertinent pour Rennes Métropole (43 communes, EPCI 243500139), publié vers un nœud RUDI local :

1. **Découverte** (`src/discover.py`) — recherche de candidats sur data.gouv.fr (CSV + WFS/WMS), interactive ou automatique
2. **Moisson tabulaire** (`src/main.py` — datasets configurés dans `DATASETS`, actuellement 2 entrées MyDataBall ; `src/harvest_batch.py` — candidats issus de la découverte dans `decouverte.json`)
3. **Moisson géo** (`src/harvest_geo.py`) — WFS (GeoJSON téléchargé), WMS (référence de service), OGC API Features
4. **Moissons directes** — `src/harvest_insee.py` (insee.fr), `src/harvest_oeb.py` (data-fair OEB), `src/harvest_bdnb.py` (ZIP BDNB dép. 35)
5. **Catalogue + publication** — `src/catalogue.py` (catalogue.json/html + visionneuses/cartes), `src/publish_rudi.py` (rattrapage RUDI)
6. **Monitoring (optionnel)** — `src/monitor.py` alimente une base PostGIS visualisée par Superset (voir « Monitoring & Superset »)

**Dépendances** : stdlib + `requests` pour tout le pipeline de moisson. Optionnels, dégradés proprement s'ils manquent : `psycopg2` (monitoring PostGIS uniquement), `openpyxl`/`pyarrow`/`fsspec` (analyse XLSX/Parquet en découverte), `rudi_node_write` (publication RUDI). Côté conteneurs : Podman pour le nœud RUDI local, Docker (compose) pour PostGIS+Superset.

## Commands

```bash
python3 src/cli.py         # entrée terminal : menu 19 actions en 4 sections (voir ACTIONS/SECTIONS)
python3 src/dashboard.py   # entrée web : mêmes actions à http://127.0.0.1:8765
python3 src/harvest_auto.py # entrée cron/Jenkins : découverte auto + pipeline complet (non planifié à ce jour)
```

Chaque script tourne aussi en standalone :

```bash
python3 src/discover.py             # session de découverte interactive
python3 src/main.py                 # moisson des datasets DATASETS (data.gouv.fr)
python3 src/harvest_batch.py        # moisson des candidats découverts (5 threads)
python3 src/harvest_insee.py [id ...]
python3 src/harvest_oeb.py [--decouvrir]
python3 src/harvest_bdnb.py
python3 src/harvest_geo.py
python3 src/catalogue.py
python3 src/publish_rudi.py         # rattrapage publication RUDI
python3 src/enrichir_descriptions.py # rattrapage descriptions vides
python3 src/enrichir_contacts.py [--dry-run] # rattrapage contacts génériques
python3 src/reanalyser_faux_positifs.py [--appliquer] [--dossier X] # rattrapage faux positifs INSEE/CP
python3 src/monitor.py --init-db|--refresh|--import-data|--import-ref|--geocode|--status|--full
```

**Tests** (stdlib `unittest`, logique pure sans réseau — filtres géo, traducteurs RUDI, cascade de détection, état) :

```bash
python3 -m unittest discover tests/
```

## Key files

| File | Role |
|---|---|
| `src/cli.py` | **Entrée terminal** — 19 actions en 4 sections (Moisson / Pipeline & publication / Maintenance / Données & infos) ; `executer_pipeline_complet()` |
| `src/dashboard.py` | **Entrée web** — serveur stdlib local-only réutilisant `cli.py` ; pages `/`, `/examen`, `/decouverte`, `/catalogue` |
| `src/harvest_auto.py` | **Entrée cron/Jenkins** — découverte non-interactive → warm-up nœud RUDI → pipeline complet |
| `src/discover.py` | Découverte : recherche API, pré-filtrage, revue interactive, `rechercher_et_filtrer_auto()` |
| `src/review.py` | Revue manuelle du backlog `a_examiner` : aperçus multi-format, tag de colonnes (ré-exporté par `discover.py`) |
| `src/harvest_batch.py` | Moisson des candidats découverts : état par-ressource, filtre RM multi-champ, publication inline |
| `src/main.py` | Moisson des datasets `DATASETS` (JSON **et** CSV, filtre RM complet) |
| `src/harvest_geo.py` / `src/harvest_insee.py` / `src/harvest_oeb.py` / `src/harvest_bdnb.py` | Moissons spécialisées (voir sections dédiées) |
| `src/monitor.py` | Monitoring : alimente PostGIS (métriques, données filtrées, référentiels, géocodage RVA, journal pipeline) |
| `src/conf/datasets.py` | `DATASETS`, `DATASETS_GEO`, `DATASETS_INSEE`, `DATASETS_OEB`, `DATASETS_BDNB` |
| `src/conf/communes_rm.py` | Référentiel : 43 communes, `COMMUNES_RM` (nom→CP), `CODES_INSEE_RM`, `INSEE_VERS_NOM`, `DEPARTEMENTS_RM`, circonscriptions, bbox |
| `src/conf/discover.py` | `KEYWORDS` (26 mots-clés compétences), `REQUETES_STRUCTUREES` (~50 requêtes), `CHAMPS_*` (détection colonnes) |
| `src/filters/geographic.py` | `est_dans_rm()`, `est_commune_rm()`, `normaliser()`, `est_circonscription_rm()`, `est_departement_rm()`, `est_point_rm()` |
| `src/filters/discovery.py` | Téléchargements d'extraits pour l'analyse + **cache disque 24 h des réponses API** |
| `src/filters/harvest.py` / `src/filters/csv.py` | Filtrage ligne-à-ligne RM / utilitaires CSV (slugifier, sauvegarder) |
| `src/connectors/http.py` | Session `requests` partagée : pool + retry/backoff + **timeout par défaut 30 s** — voir « Conventions réseau » |
| `src/connectors/datagouv.py` | API data.gouv.fr (métadonnées, téléchargement) |
| `src/connectors/analyseurs.py` | Analyse des ressources (CSV/XLSX/ZIP/GZ/Parquet/GeoJSON/WMS/WFS), cascade `_detecter_champs()` |
| `src/connectors/geo_services.py` | WFS/WMS/OGC API : capabilities, bbox, **probe GetMap**, contacts OWS, MetadataURL |
| `src/connectors/insee.py` / `oeb.py` / `bdnb.py` / `sirene.py` | Connecteurs sources (INSEE scraping failsafe, data-fair, ZIP streaming, SIREN RM cache 30 j) |
| `src/connectors/rudi_node.py` | `publier_dataset()`, `charger_conf_rudi()`, contrôle Podman, `toutes_metadonnees_rudi()`, `supprimer_dataset/_organisation()` |
| `src/connectors/rudi_publish.py` | `publier_si_configue()` — point unique de publication best-effort, **sérialisé par verrou** (voir « Publication RUDI ») |
| `src/connectors/contacts.py` | Extraction/résolution de contacts (data.gouv, fallback RFC 2606) |
| `src/connectors/rva.py` | Géocodage API RVA Rennes Métropole (clé dans `src/conf/rva_key.json`, non commitée ; cache interdit par CGU) |
| `src/connectors/superset.py` | Contrôle du conteneur Docker Superset (`mb-superset`) |
| `src/connectors/download.py` | Téléchargement streaming vers cache disque partagé (`data/cache/`) |
| `src/translation/datagouv_to_rudi.py` | `traduire_metadonnees()` + `traduire_metadonnees_service()` (voies data.gouv tabulaire + géo) |
| `src/translation/rudi_builder.py` | Constructeur partagé `construire_rudi_metadata()` + helpers `media_*()` (voies INSEE/OEB/BDNB) |
| `src/translation/description_secours.py` | `generer_complement()` — description de secours factuelle (voir « Fallback descriptions ») |
| `src/state.py` | `charger_state()`/`sauvegarder_state()` génériques + `construire_index_dossier()` (index dossier→state multi-fichiers) |
| `src/publish_rudi.py` | Rattrapage publication + `menage_rudi_one_shot()`/`menage_organisations()` (voir « Publication RUDI ») |
| `src/enrichir_descriptions.py` / `src/enrichir_contacts.py` | Rattrapages one-shot sur les `rudi_metadata.json` existants |
| `src/reanalyser_faux_positifs.py` | Rattrapage offline des faux positifs INSEE/CP (re-filtrage des fichiers moissonnés, dry-run par défaut — menu 16) |
| `src/tee.py` | Classe `Tee` (sortie dupliquée) — dashboard + harvest_auto |
| `src/static/` | `dashboard.css` + `dashboard.js` partagés, servis sous `/static/` par le dashboard |
| `data/decouverte.json` | État de découverte : vus, candidats, exclus, echecs, exclusions_termes, a_examiner, historique |

## Discovery state (`data/decouverte.json`)

```json
{
  "vus":               ["dataset-id", ...],   // seen and decided (skip/candidat/exclus)
  "candidats":         [{...}, ...],           // datasets confirmed to have RM data
  "exclus":            ["dataset-id", ...],   // permanent manual skip decisions
  "echecs":            ["dataset-id", ...],   // analysis failed, will be retried
  "echecs_n":          {"dataset-id": 2},     // consecutive failure count
  "sans_ressource":    ["dataset-id", ...],   // no CSV/JSON resource available
  "exclusions_termes": ["Landes", ...],       // term-based exclusions (org/title)
  "a_examiner":        [{...}, ...],          // ambiguous cases from automated discovery
  "historique":        [{...}, ...]           // "exclure"/"ignorer" decisions on a_examiner entries
}
```

Only `exclus` and `exclusions_termes` survive a history reset — those are deliberate user decisions.

`historique` holds a full snapshot of the `a_examiner` entry (plus `decision`: `"exclure"`|`"ignorer"`, and `date_decision`) at the moment `resoudre_a_examiner()` resolves it that way — populates the "Exclus"/"Ignorés" tabs on `/examen` with enough context to display without re-fetching, and lets `rouvrir_historique()` reconstruct the entry byte-for-byte if the decision is undone. `"candidat"`/`"ajouter_geo"` decisions don't get a `historique` entry — they're already visible elsewhere (`candidats`/`data/geo_services.json`).

Les entrées candidat/`a_examiner` portent les champs de filtrage détectés ou tagués : `champ_cp`, `champ_ville`, `champ_iris`, `champ_adresse`, `champ_siren`, `champ_epci`, `champ_circonscription`, `champ_dep`, plus `last_modified` (fast-path du batch).

## Discovery pipeline

Search → pre-filter → review (interactive) ou décision automatique :

1. **API search** (`REQUETES_STRUCTUREES`): structured queries with `granularity=fr:commune`, `granularity=fr:iris`, `featured=true`, keyword queries, format queries (`format=wms/wfs/geojson`), producer queries (INSEE, Cerema, MTECT). Uses `_paginer()` which handles 404-on-page-N+1 as normal end-of-pagination. Les réponses API sont mises en **cache disque 24 h** (`filters/discovery.py::_lire/_ecrire_cache_api`).
2. **Pre-filter** (`pre_filtrer()`): 3-state parallel pipeline:
   - Check title/description for geo markers → if none, check CSV headers
   - If geo found: analyse full CSV for RM rows
   - Returns `("skip", None)` / `("candidat", result)` / `("presenter", result)`
   - `"candidat"` (nb_rm > 0) is auto-added; `"presenter"` goes to interactive review
3. **Interactive loop**: shows fiche (title, org, ANALYSE line if pre-filtered), asks `s/p/a/x/q`

**Mode automatique** (`rechercher_et_filtrer_auto()`, utilisé par `harvest_auto.py` et le dashboard) : jamais d'`input()`. Tabulaire avec RM détecté → `candidats` ; tabulaire ambigu ou analyse échouée → upsert dans `a_examiner`. **Les services géo ne passent plus par `a_examiner`** : un WMS avec couches RM confirmées (probe GetMap) ou un WFS avec features RM est **auto-ajouté à `geo_services.json`** ; un service géo avec `nb_rm == 0` est **auto-exclu** (`decouverte["exclus"]`).

## Geographic detection in CSVs

`_detecter_champs()` (`connectors/analyseurs.py`, used by every format's analysis path) tries in priority order:
1. **IRIS/INSEE code** (`deviner_champ_iris`): 5-digit commune code or 9-digit IRIS code → `est_iris_rm()` checks against `CODES_INSEE_RM`. Anti-faux-positifs : `_PREFIXES_INSEE_EXCLUS`.
2. **Postal code + city** (`deviner_champs`): → `est_dans_rm()`. Anti-faux-positifs commune : `_FAUX_POSITIFS_VILLE`.
3. **Address text** (`deviner_champ_adresse`): regex for `35xxx` postal codes or commune name match
4. **SIREN/SIRET** (`deviner_champ_siren`): 9/14-digit id → cross-referenced against `obtenir_sirens_rm()`
5. **EPCI** (`deviner_champ_epci`, `CHAMPS_EPCI`): code EPCI == 243500139
6. **Coordinates/geometry** (`deviner_champs_geo`): separate lat/lon columns, or a single combined column — `"lat,lon"` (OpenDataSoft `geo_point_2d`), WKT `POINT/POLYGON/MULTIPOLYGON(...)` (y compris colonnes centroïde `centroid*`), or a GeoJSON geometry serialized as JSON (`geom`/`geo_shape`/`the_geom`) — tested via `est_point_rm()`. For a multi-vertex geometry this is a bbox vertex test, not a true intersection.
7. **Circonscription législative** (`deviner_champ_circonscription`): avant-dernier recours → `est_circonscription_rm()` (formats `"035-01"`, `"35-01"`, `"3501"`… normalisés en `"DDD-NN"`). Signal volontairement peu fiable : une circonscription déborde de RM (voir « Known limitations »).
8. **Département** (`champ_dep`): tout dernier recours → `est_departement_rm()` (`DEPARTEMENTS_RM = {"035"}`). Encore plus grossier que la circonscription — ne prouve que « quelque part en Ille-et-Vilaine ».

Each step is only tried if the previous ones found nothing (mutually exclusive cascade). `normaliser()` strips accents, lowercases, converts `_`/`-` to spaces — applied to column headers before matching. Tous ces champs sont persistés sur le candidat et filtrés par `harvest_batch.py::_ligne_est_rm()`.

## API gotchas

- `sort=-views` works; `sort=-metrics.views` returns HTTP 400
- `granularity=fr:commune` works; `granularity=commune` returns 0 results
- 404 on page N+1 is normal end-of-pagination, not an error
- `ressourcerie-datalocale-1` (datalocale.fr) is permanently dead — blacklisted in `ORGS_EXCLUES`
- OEB (data-fair) : le filtrage direct `field=value` ne fonctionne pas, passer par `qs=` Elasticsearch (voir `connectors/oeb.py`)

## Column dictionary detection

Some resources have a "Colonne,Description" header format (Ecolab style).
- `telecharger_extrait_csv()` returns `"__DICTIONNAIRE__"` sentinel
- `analyser_csv()` returns `None` (not a failure, just wrong resource type)
- Interactive loop tries the next CSV resource when dict is detected

## WMS/WFS discovery flow

Quand `discover.py` rencontre une ressource `wms`, `analyser_wms()` :
- Sonde GetCapabilities → couches dont la bbox recoupe RM. `nb_rm` = **nombre de couches RM** (pas 0).
- Bbox absente ou déraisonnable (>10× RM) → **probe GetMap réel** : requête 2×2 px au centre de RM, analyse du contenu PNG (`wms_probe_donnees_rm()`/`_png_a_donnees()`) pour distinguer tuile vide et données réelles.
- En découverte **automatique** : auto-ajout à `geo_services.json` si couches RM, auto-exclusion sinon (voir « Discovery pipeline »). En session **interactive** : proposition d'ajout à `DATASETS_GEO` (JSON à copier dans `src/conf/datasets.py`).
- Maintenance : `reanalyser_wms_a_examiner()` (menu 13 — reprend le reliquat du backlog) et `nettoyer_wms_geo_services()` (menu 14 — re-vérifie les couches déjà enregistrées par probe GetMap).

WMS in catalogue: `harvest_geo.py` saves `wms_service.json` → `catalogue.py` generates `wms_map.html` (Leaflet + panneau latéral de couches avec recherche/cases à cocher).

RUDI publication: WFS/OGC API → GeoJSON files uploaded as `media_type: FILE` + SERVICE entry ; WMS → SERVICE entry only (`interface_contract: "wms"`).

**Détection de changements géo** : `traiter_wfs()`/`traiter_ogcapi()`/`traiter_geojson()` retournent `(fichiers, changee[, contact])` ; `data/state_geo.json` stocke une signature (ETag, Last-Modified, Content-Length) par couche. Si rien n'a changé **et** que `rudi_metadata.json` existe, régénération et publication sont sautées. Les WMS sont toujours regénérés (GetCapabilities léger, pas de signature exploitable).

## Adding a dataset to harvest

After discovery identifies a candidate, add to `src/conf/datasets.py`:
```python
{
    "dataset_id": "the-dataset-slug-or-id",
    "dossier": "local-folder-name",
    "champ_cp": "column_name_for_postal_code",   # or champ_ville / champ_iris / champ_siren / ...
    "champ_ville": "column_name_for_city",
    "theme": "environment",                       # required — see valid values below
}
```

`theme` is mandatory. `traduire_metadonnees()` raises `ValueError` if missing or invalid. Valid RUDI themes (defined in `THEMES_RUDI` in `src/translation/datagouv_to_rudi.py`):

| Code | Label FR |
|---|---|
| `economy` | Economie |
| `citizenship` | Citoyenneté |
| `energyNetworks` | Réseaux, Energie |
| `culture` | Culture, Sports, Loisirs |
| `transportation` | Mobilité, Transport |
| `children` | Enfance |
| `environment` | Environnement |
| `townPlanning` | Urbanisme |
| `location` | Référentiels géographiques |
| `education` | Education |
| `publicSpace` | Espace public |
| `health` | Santé, Sécurité |
| `housing` | Logement |
| `society` | Social |

## Adding a geo service to harvest

Add to `DATASETS_GEO` in `src/conf/datasets.py`:
```python
{
    "id": "local-unique-slug",
    "type": "wfs",         # "wfs" | "wms" | "ogcapi"
    "url": "https://...",  # base URL, no OGC params
    "couches": ["layer1"], # optional; auto-detected if absent
    "titre": "Title",      # optional; read from service if absent
    "producteur": "Org",   # optional
    "dossier": "local-folder-name",
    "theme": "environment",
}
```

- WFS/OGC: each layer downloaded as `{typename}.geojson` in `data/{dossier}/`
- WMS: `wms_service.json` saved (no file download); catalogue generates live Leaflet WMS map

## Conventions réseau (connecteurs)

- Tout code HTTP passe par la `session` partagée de `src/connectors/http.py` — pool de connexions + retry/backoff (3 retries sur 429/5xx et erreurs de connexion, pas de retry 4xx) + **timeout par défaut de 30 s** (sous-classe de `Session` ; un `timeout=` explicite prime toujours). Ne pas appeler `requests.get/head/post` directement — plus aucun appel direct ne subsiste dans `src/`.
- Continuer à poser un `timeout=` explicite proportionné au payload (métadonnées : ~10-30 s ; téléchargements streaming : 30-120 s) — le défaut est un filet de sécurité, pas une dispense.
- `src/connectors/rudi_node.py` est le foyer canonique de `charger_conf_rudi()`. Ne pas redéfinir un loader local dans un nouveau script.

## Discipline d'état (state*.json) pour un script de moisson

For any harvest script that tracks per-dataset state (skip unchanged sources):
- Save state **incrementally** (after each dataset, not only at the end of the run).
- The state entry is written as soon as the **local** pipeline succeeds (download → filter → translate → save `rudi_metadata.json`), regardless of whether the RUDI publish step succeeds. "Did I harvest this" and "did I publish this" are two independent concerns.
- RUDI publish outcome is tracked with `"rudi_publie": bool` on the state entry — `True` only if the publish actually succeeded this run. Don't gate the state *write* on publish success — gate the `rudi_publie` *value*, and let `src/publish_rudi.py` retry.
- Tolerate a corrupted/truncated state file by falling back to `{}` with a printed warning (see `charger_state()`).
- A **legitimately empty result** (0 RM rows — `"vide"`) still gets a state entry (`nb_rm: 0`), so it's cached like any unchanged source. An **`"echec"`** (network/parsing error) never gets a state entry and is always retried.

**État par-ressource dans `harvest_batch.py`** : chaque entrée state porte un dict `ressources` (`resource_id` → last_modified + nb_rm). `_comparer_ressources()` permet : fast-path si le `last_modified` global du candidat est inchangé, saut des ressources individuellement inchangées, et re-téléchargement des seules ressources modifiées. Les ressources confirmées vides et inchangées sont ignorées (`ressources_a_ignorer`).

Fichiers d'état : `data/state.json` (main + batch), `state_insee.json`, `state_oeb.json`, `state_bdnb.json`, `state_geo.json` (signatures de couches + `_rudi_publie` par dossier). Schémas distincts, non unifiés (voir « Known limitations »). `state.construire_index_dossier()` reconstruit l'index dossier→(state, entrée) trans-fichiers.

## Publication RUDI (inline + rattrapage + ménage)

Chaque script de moisson publie inline via `connectors/rudi_publish.py::publier_si_configue()` (y compris `harvest_batch` désormais) : best-effort, jamais bloquant, résultat tracé dans `rudi_publie`. **La publication est sérialisée par un verrou module-level** — les moissons tournent en parallèle (workers batch + pipeline parallélisé) mais le `get_or_create` d'organisations/contacts du nœud n'est pas idempotent sous concurrence (doublons). Ne pas contourner ce point d'étranglement.

`src/publish_rudi.py` est le rattrapage — il travaille **uniquement depuis les `rudi_metadata.json` sur disque** :
- Scanne `data/<dossier>/rudi_metadata.json`, résout l'origine via `construire_index_dossier()` (state.json / state_insee / state_oeb / state_bdnb) ou `DATASETS_GEO`.
- Publie ce qui n'est pas `rudi_publie: true` ; à la réussite, bascule le flag — y compris pour les géo via `state_geo["_rudi_publie"]["<dossier>"]`.
- Un dossier sans correspondance dans les états ni `DATASETS_GEO` est listé pour revue manuelle, jamais auto-publié.
- Reconstruit `fichiers_filtres` en prenant le préfixe `media_type: "FILE"` d'`available_formats` dans l'ordre (mapping positionnel — l'ordre doit être préservé).

**Ménage** (menu CLI 15, `action_menage_rudi()`) : `menage_rudi_one_shot()` liste les datasets présents sur le nœud sans contrepartie locale (orphelins), `menage_organisations()` les organisations plus référencées ; le CLI **propose ensuite leur suppression effective** (`supprimer_dataset()`/`supprimer_organisation()`), protégée par la confirmation tapée `SUPPRIMER`. La détection seule ne mute rien.

**Nettoyage des FILE orphelins** : `publier_dataset()` retire d'`available_formats` les entrées FILE dont le fichier local a disparu (le nœud refuse un media FILE sans fichier physique), puis recorrige le `media_id` de toutes les entrées non-FILE (la lib exige des UUIDv4).

**`media_metadata_page`** : tous les générateurs (datagouv, service géo, INSEE, OEB, BDNB) ajoutent en fin d'`available_formats` une entrée SERVICE `source-metadata` pointant vers la fiche source, avec `media_id` déterministe (UUIDv5 de l'URL).

## Contacts

`connectors/contacts.py` : `extraire_contacts_datagouv()` (depuis les métadonnées data.gouv) et `resoudre_contacts()` avec fallback `contact@example.org` (domaine réservé RFC 2606). Branché dans `traduire_metadonnees()`/`traduire_metadonnees_service()`/`rudi_builder`. Côté géo, `geo_services.py` extrait les contacts OWS (WFS) et WMS (`wfs_get_contact`, `_extraire_contact_wms`) + MetadataURL. `publier_dataset()` privilégie les contacts de la source et ne retombe sur le contact générique du nœud qu'en dernier recours. `src/enrichir_contacts.py` rattrape les JDD déjà moissonnés restés au fallback.

## Fallback descriptions (`src/translation/description_secours.py`)

Many sources never provide a usable description (data.gouv datasets with empty `description`, INSEE, geo services). `generer_complement()` builds 1-3 factual sentences from what's actually available — theme label, producer, CSV/GeoJSON column names, WMS layer names, keywords as last resort. It never invents what the data means. `description_quasi_vide()` gates this (< 40 chars pour data.gouv ; toujours pour INSEE et géo).

Wired into every live harvest path (les scripts passent `entetes_colonnes` aux traducteurs). `src/enrichir_descriptions.py` est le rattrapage pour les JDD antérieurs — idempotent via le marqueur "Jeu de données du thème". `catalogue.py::partie_descriptive()` extrait la partie descriptive du `summary` pour l'afficher sur les cartes du catalogue.

## Purge de données (`src/cli.py`)

Menu « Purger des données existantes » : **8 items** indépendants, confirmés un par un (tailles affichées en direct) :
1. Cache de téléchargement intégral (`data/cache/`) ; 2. Cache > 7 jours seulement ; 3. État de moisson (les **5** `state*.json`) ; 4. Sessions de découverte en attente ; 5. Services géo auto-découverts (`geo_services.json`) ; 6. Catalogue généré (+ visionneuses/cartes) ; 7. Historique de découverte (**préserve** `exclus` et `exclusions_termes`) ; 8. **Toutes les données moissonnées** — seule à exiger la confirmation tapée `SUPPRIMER`, purge aussi les états.

Un nouveau fichier/dossier top-level sous `data/` qui doit survivre à l'item 8 doit être un **fichier**, pas un répertoire (l'item ne `rmtree` que les répertoires hors `cache`).

## Tableau de bord web (`src/dashboard.py`)

Stdlib-only (`http.server.ThreadingHTTPServer`), **127.0.0.1 uniquement** — aucune authentification, actions destructrices possibles ; ne jamais exposer au-delà de la machine.

- **Zéro logique métier dupliquée** : importe `cli` et appelle ses `action_*`, `PURGE_ITEMS`, `etat_projet()`. Nouvelle action = l'ajouter au dict `ACTIONS` Python **et** au tableau `ACTIONS` du JS.
- **Assets partagés** : `src/static/dashboard.css`/`dashboard.js` servis sous `/static/`, topbar commun injecté via le marqueur `<!--TOPBAR-->` (`_html_topbar()`). Le catalogue écrit sur disque est **autonome** (CSS inliné à la génération, marqueur invisible en `file://`) — le dashboard substitue le marqueur quand il le sert (`/catalogue`).
- **Un job à la fois** (`_verrou_job`, HTTP 409 sinon) ; un second verrou bloque les purges pendant une moisson. Log en direct par capture stdout (`tee.Tee`) + polling 1 s.
- **Pages** : `/` (actions, état, nœud RUDI, Superset, badge examen), `/examen` (backlog en 6 onglets : À examiner / Analyse échouée / Sans ressource / Services géo / Exclus / Ignorés — les 2 derniers depuis `decouverte["historique"]` via `GET /api/historique`, avec `POST /api/historique/rouvrir` pour annuler), `/decouverte` (édition de la config de recherche — requêtes/mots-clés/nb_pages persistés — et test de découverte en direct), `/catalogue`.
- **Découverte interactive exclue** (elle appelle `input()`) — le pipeline du dashboard appelle `executer_pipeline_complet()` sans prompt. `cli.action_moisson_insee(ids=...)` accepte un paramètre pour la même raison.
- Ajout d'un candidat/service géo depuis `/examen` **déclenche automatiquement** (best-effort) la chaîne moisson+catalogue+publication correspondante (`moisson_batch_et_publier`/`moisson_geo_et_publier`) ; si un job tourne déjà, no-op — le prochain run ramasse l'ajout.
- **Cartes nœud RUDI (Podman) et Superset (Docker)** : contrôle direct des conteneurs (`/api/noeud/*`, `/api/superset/*`). `noeud_pret()` fait un vrai probe HTTP du manager — ne pas se rabattre sur le seul statut Podman (bouton « cassé » sinon). Conteneurs : `rudinode` (`CONTENEUR_RUDI` dans `rudi_node.py`), `mb-superset`.
- Purge revalidée **côté serveur** exactement comme `cli._confirmer()`.
- `GET /data/<chemin>` sert les fichiers sous `data/` (path-traversal-guardé) — un lien `file://` depuis une page `http://` est bloqué par les navigateurs.

## Découverte automatique (`src/harvest_auto.py`)

Entrée non-interactive pour cron/Jenkins — ex. `0 5 * * * cd <repo> && python3 src/harvest_auto.py >> logs/harvest_auto.log 2>&1`. Trois étapes, aucun `input()` :

1. **Découverte non-interactive** — `rechercher_et_filtrer_auto()` (voir « Discovery pipeline » pour les règles d'auto-décision).
2. **Warm-up nœud RUDI** — démarre le conteneur Podman si besoin, attend `noeud_pret()` ~60 s ; ne fait jamais échouer le run (publication différée sinon).
3. **Pipeline complet** — `cli.executer_pipeline_complet()` : moisson tabulaire → batch en séquence, puis **INSEE/OEB/BDNB/géo en parallèle** (`ThreadPoolExecutor(max_workers=4)`), puis catalogue → publication RUDI. Chaque étape est chronométrée et journalisée dans la base monitoring si configurée (`_log_pipeline_etape()`, silencieux sinon).

Exit `0` seulement si tout a réussi. **Logs** : chaque run écrit `logs/harvest_auto_<horodatage>.log` (un fichier par run, gitignoré) ; `cli._executer()` imprime la traceback complète sur échec d'étape.

**Revue du backlog `a_examiner`** : dashboard `/examen` (3 actions par entrée : Ajouter aux candidats / Faux positif / Ignorer) ou revue manuelle CLI (menu 2, `discover.revue_manuelle_a_examiner()` dans `src/review.py`) — aperçu colonne par colonne (CSV/XLSX/GeoJSON/Parquet + gz/bz2/zip), tag manuel du type de variable (`_TYPES_VARIABLES` : INSEE/IRIS, CP seul, commune, adresse, SIREN/SIRET, lat/lon, circonscription…), recomptage `nb_rm` sur le fichier complet avant confirmation. Tout passe par `resoudre_a_examiner()` (source unique de mutation).

## Monitoring & Superset (sous-système optionnel)

Chaîne d'observation du pipeline, **entièrement optionnelle** (le pipeline de moisson n'en dépend jamais) :

- **Infra** : `docker-compose.yml` → PostGIS 16-3.4 (`mb-postgis`, 127.0.0.1:5433) + Superset 4.1.1 (`mb-superset`, 127.0.0.1:8088). Secrets dans `.env` (gitignoré, gabarit `.env.example`).
- **`src/monitor.py`** (CLI standalone, nécessite `psycopg2`) : `--init-db` (4 schémas : monitor/ref/decouverte/filtered, DDL idempotent), `--refresh` (métriques des `state*.json` → `metrics_history`), `--import-data` (CSV/GeoJSON filtrés → `filtered.data_rows`/`geo_features`, reprojection EPSG:2154), `--import-ref` (référentiels communes/IRIS/SIREN depuis `conf/communes_rm.py::INSEE_VERS_NOM`), `--geocode` (adresses sans code INSEE via RVA), `--status`, `--full`. Conf : `src/conf/monitor_db.json` (gitignoré, gabarit `.example`) ; les noms de schémas sont validés (`[a-z_][a-z0-9_]*`) avant interpolation SQL.
- **Journal pipeline** : `cli.executer_pipeline_complet()` journalise chaque étape (durée, succès) dans `monitor.pipeline_runs` si la base est configurée — silencieux sinon.
- **Provisionnement Superset** : `superset/setup_full.py` (+ `setup_dashboard.py`, `post_setup.py`), à exécuter dans le conteneur (voir docstring). Identifiants lus depuis l'environnement (`SUPERSET_ADMIN_USER/PASSWORD`, `POSTGRES_PASSWORD`). `superset/scratch/` (gitignoré) = scripts de debug jetables.
- **Contrôle** : menu CLI 19 ou carte Superset du dashboard.

## Known limitations / not addressed

Identifiées et laissées en l'état délibérément — décision produit/architecture requise :

- **Fichiers d'état séparés, non unifiés** : 5 schémas distincts (voir « Discipline d'état »). L'unification a des implications de migration. Les couches WMS n'ont toujours **aucun** skip-si-inchangé (GetCapabilities re-sondé à chaque run — volume négligeable).
- **Nuances heuristiques de `discover.py`** inchangées : tie-breaking du délimiteur CSV, couverture Parquet (pushdown sur colonne `COMMUNE` uniquement), sémantique `nb_rm` multi-ressources. Elles affectent la qualité de sélection d'un outil calibré à la main — ne pas les changer sans l'utilisateur.
- **Persistance partielle de l'auto-détection** : `traiter_resultat()`, `rechercher_et_filtrer_auto()`, `_reanalyser_candidats_sans_champ()` ne reportent pas `champ_siren`/`champ_lat`/`champ_lon` sur le candidat persisté (le tag manuel et le filtre harvest, eux, les gèrent).
- **Thème RUDI heuristique pour les candidats auto** : `_detecter_theme()` (scoring mots-clés) — parfois faux, à surveiller au catalogue.
- **`harvest_auto.py` re-balaye toutes les `REQUETES_STRUCTUREES` chaque run** (le cache API 24 h et `deja_vus` amortissent, mais le listing n'est pas incrémental).
- **`harvest_auto.py` n'est planifié nulle part** : aucun crontab/systemd/Jenkins n'existe sur cette machine — tout tourne à la main. D'où l'importance de l'auto-déclenchement depuis `/examen`.
- **`champ_circonscription` et `champ_dep` sont plus grossiers que tout autre champ** : une circonscription déborde de RM, un département encore plus — derniers recours assumés, source occasionnelle de faux positifs quand ce sont les seuls champs détectés.
- **Refactorings envisagés non retenus** (bénéfice/risque insuffisant à ce stade) : runner de moisson générique pour les 6 boucles traiter→publier, unification des fichiers d'état, découpage de `analyseurs.py`/`discover.py`/`catalogue.py`, purge `git filter-repo` de l'historique (données volumineuses commitées avant juillet 2026 ; `data/` est gitignoré depuis).
- **Rotation recommandée** du mot de passe du compte `simon` du nœud RUDI local (présent dans l'historique git via un ancien `.example` ; nœud local non exposé, risque faible).

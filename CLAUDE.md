# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Pipeline de moisson d'open data pertinent pour Rennes Métropole (43 communes, EPCI 243500139), publié vers un nœud RUDI local :

1. **Découverte** (`src/discover.py`) — recherche de candidats sur data.gouv.fr (CSV + WFS/WMS), interactive ou automatique
2. **Moisson tabulaire** (`src/main.py` — datasets configurés dans `DATASETS`, actuellement 2 entrées MyDataBall ; `src/harvest_batch.py` — candidats issus de la découverte dans `decouverte.json`)
3. **Moisson géo** (`src/harvest_geo.py`) — WFS (GeoJSON téléchargé), WMS (référence de service), OGC API Features
4. **Moissons directes** — `src/harvest_insee.py` (insee.fr), `src/harvest_oeb.py` (data-fair OEB), `src/harvest_bdnb.py` (ZIP BDNB dép. 35)
5. **Catalogue + publication** — `src/catalogue.py` (catalogue.json/html + visionneuses/cartes), `src/publish_rudi.py` (rattrapage RUDI)
6. **Interrogation en langage naturel (prototype)** — `src/mcp_rudi_server.py`, serveur MCP métadonnées seules (voir « Serveur MCP RUDI »)

**Dépendances** : stdlib + `requests` pour tout le pipeline de moisson. Optionnels, dégradés proprement s'ils manquent : `openpyxl`/`pyarrow`/`fsspec` (analyse XLSX/Parquet en découverte), `rudi_node_write` (publication RUDI). Côté conteneurs : Podman pour le nœud RUDI local. Le serveur MCP (`src/mcp_rudi_server.py`) requiert en plus `mcp` (SDK officiel, épinglé `1.29.0` — la 2.0.0 renomme `FastMCP` en `MCPServer`, API non compatible) ; installé ici via `pip install --user --break-system-packages` (environnement Debian externally-managed, même contrainte que `rudi_node_write`/`openpyxl`).

## Commands

```bash
python3 src/cli.py         # entrée terminal : menu 25 actions en 5 sections (voir ACTIONS/SECTIONS)
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
python3 src/renvoi_auto_apres_validation.py [--force] # renvoi auto après validation d'organisation en UI (cron-friendly) ; --force ignore le filtre organisation validée (ex « réparer les JDD refusés »)
python3 src/enrichir_descriptions.py # rattrapage descriptions vides
python3 src/enrichir_html_descriptions.py [--appliquer] [--dossier X] # rattrapage summary[].html (rendu Markdown->HTML, voir « Rendu HTML du summary »)
python3 src/enrichir_organisations.py [--dry-run] # descriptions producteurs (local nœud uniquement)
python3 src/enrichir_contacts.py [--dry-run] # rattrapage contacts génériques
python3 src/reanalyser_faux_positifs.py [--appliquer] [--dossier X] # rattrapage faux positifs INSEE/CP
```

**Tests** (stdlib `unittest`, logique pure sans réseau — filtres géo, traducteurs RUDI, cascade de détection, état) :

```bash
python3 -m unittest discover tests/
```

## Workflow Git / PR

Merger une PR **en local** (`git checkout main && git merge <branche>` puis `git push`), jamais via le bouton "Merge" de l'interface GitHub. Préférence explicite de l'utilisateur (2026-10-11) : un merge sur GitHub laisse le `main` local en retard, ce qui oblige ensuite à `git pull`/`git merge origin/main` pour rattraper — un aller-retour évitable. Un seul geste (merge local + push) au lieu de deux (merge GitHub + pull).

## Key files

| File | Role |
|---|---|
| `src/cli.py` | **Entrée terminal** — 25 actions en 5 sections (Moisson / Pipeline & publication / Maintenance / Données & infos / Nœud & portail RUDI) ; `executer_pipeline_complet()` |
| `src/dashboard.py` | **Entrée web** — serveur stdlib local-only réutilisant `cli.py` ; pages `/`, `/examen`, `/decouverte`, `/catalogue` |
| `src/harvest_auto.py` | **Entrée cron/Jenkins** — découverte non-interactive → warm-up nœud RUDI → pipeline complet |
| `src/discover.py` | Découverte : recherche API, pré-filtrage, revue interactive, `rechercher_et_filtrer_auto()` |
| `src/review.py` | Revue manuelle du backlog `a_examiner` : aperçus multi-format, tag de colonnes (ré-exporté par `discover.py`) |
| `src/harvest_batch.py` | Moisson des candidats découverts : état par-ressource, filtre RM multi-champ, publication inline |
| `src/main.py` | Moisson des datasets `DATASETS` (JSON **et** CSV, filtre RM complet) |
| `src/harvest_geo.py` / `src/harvest_insee.py` / `src/harvest_oeb.py` / `src/harvest_bdnb.py` | Moissons spécialisées (voir sections dédiées) |
| `src/conf/datasets.py` | `DATASETS`, `DATASETS_GEO`, `DATASETS_INSEE`, `DATASETS_OEB`, `DATASETS_BDNB` |
| `src/conf/communes_rm.py` | Référentiel : 43 communes, `COMMUNES_RM` (nom→CP), `CODES_INSEE_RM`, `INSEE_VERS_NOM`, `DEPARTEMENTS_RM`, circonscriptions, bbox |
| `src/conf/discover.py` | `KEYWORDS` (26 mots-clés compétences), `REQUETES_STRUCTUREES` (~50 requêtes), `CHAMPS_*` (détection colonnes) |
| `src/conf/organisations.py` | `ALIAS_ORGANISATIONS` — alias curées des producteurs connus (titre Wikipédia, override, ou repli forcé) |
| `src/filters/geographic.py` | `est_dans_rm()`, `est_commune_rm()`, `normaliser()`, `est_circonscription_rm()`, `est_departement_rm()`, `est_point_rm()` |
| `src/filters/discovery.py` | Téléchargements d'extraits pour l'analyse + **cache disque 24 h des réponses API** |
| `src/filters/harvest.py` / `src/filters/csv.py` | Filtrage ligne-à-ligne RM / utilitaires CSV (slugifier, sauvegarder) |
| `src/connectors/http.py` | Session `requests` partagée : pool + retry/backoff + **timeout par défaut 30 s** — voir « Conventions réseau » |
| `src/connectors/datagouv.py` | API data.gouv.fr (métadonnées, téléchargement) |
| `src/connectors/analyseurs.py` | Analyse des ressources (CSV/XLSX/ZIP/GZ/Parquet/GeoJSON/WMS/WFS), cascade `_detecter_champs()` |
| `src/connectors/geo_services.py` | WFS/WMS/OGC API : capabilities, bbox, **probe GetMap**, contacts OWS, MetadataURL |
| `src/connectors/insee.py` / `oeb.py` / `bdnb.py` / `sirene.py` | Connecteurs sources (INSEE scraping failsafe, data-fair, ZIP streaming, SIREN RM cache 30 j) |
| `src/connectors/rudi_node_lifecycle.py` | Hérité de l'ancien `connectors/rudi_node.py`, amputé de la publication (passée au toolkit) : contrôle Podman, démarrage/arrêt du nœud source, `etat_modules_noeud_source()` |
| `src/connectors/rudi_publish.py` | `publier_si_configue()` — point unique de publication best-effort, **sérialisé par verrou** ; accumule aussi les `local_id` publiés ce run (voir « Publication RUDI ») |
| `src/connectors/contacts.py` | Extraction/résolution de contacts (data.gouv, fallback RFC 2606) |
| `src/connectors/wikipedia.py` | Résumés Wikipédia/Wikidata pour les organisations (caption CC0 + summary CC BY-SA) |
| `src/connectors/rva.py` | Géocodage API RVA Rennes Métropole (clé dans `src/conf/rva_key.json`, non commitée ; cache interdit par CGU) |
| `src/connectors/download.py` | Téléchargement streaming vers cache disque partagé (`data/cache/`) |
| `src/translation/datagouv_to_rudi.py` | `traduire_metadonnees()` + `traduire_metadonnees_service()` (voies data.gouv tabulaire + géo) |
| `rudi_harvest_toolkit.pivot` (package `rudi-harvest-toolkit`) | Constructeur partagé `construire_rudi_metadata()` + helpers `media_*()` (voies INSEE/OEB/BDNB) — extrait de ce repo le 2026-10-11 |
| `src/translation/description_secours.py` | `generer_complement()` — description de secours factuelle (voir « Fallback descriptions ») |
| `rudi_harvest_toolkit.markdown` (package `rudi-harvest-toolkit`) | `markdown_vers_html()` — convertisseur Markdown léger alimentant `summary[].html` (voir « Rendu HTML du summary ») — extrait de ce repo le 2026-10-11 |
| `src/translation/organisation_secours.py` | `enrichir_organisation()` — caption + summary des producteurs (voir « Fallback descriptions d'organisations ») |
| `src/state.py` | `charger_state()`/`sauvegarder_state()` génériques + `construire_index_dossier()` (index dossier→state multi-fichiers) |
| `src/publish_rudi.py` | Rattrapage publication + `menage_rudi_one_shot()`/`menage_organisations()` (voir « Publication RUDI ») |
| `src/vider_rudi.py` | `vider_noeud_proprement()` (menu 22) — vidage **scopé au pipeline** : ne supprime que les JDD avec contrepartie locale (`data/<dossier>/rudi_metadata.json`) et leurs organisations productrices non partagées avec un JDD tiers. `purger_noeud_portail()` (menu 23) — purge radicale de **tout** (sauvegarde + Mongo nœud + Strukture + Dataverse, sans validation modérateur) |
| `src/renvoi_auto_apres_validation.py` | Renvoi auto vers le portail des JDD `refused` (menu 12) — organisation nouvellement validée en UI Strukture **ou**, avec `--force`/`force=True` (ex-menu « Réparer les JDD refusés », fusionné le 2026-10-14), tout `refused` sans condition sur l'organisation — renvoi un par un via `renvoyer_ressources_connues()` (PUT producteur normal, jamais la route admin — voir « Renvoi auto après validation d'organisation ») |
| `src/reparer_medias_dupliques.py` | Réparation ponctuelle (menu 24) — dédoublonne les médias FILE des `rudi_metadata.json` sur disque puis re-publie les JDD concernés (bug portail A7 « Duplicate key » → `ERR-500`, voir « Renvoi auto après validation d'organisation ») |
| `src/valider_organisations_portail.py` | Validation automatique (menu 25) des demandes d'organisation en attente côté portail (création/modification/archivage + rattachement producteur en cascade), avec le compte animateur — **réservé au portail local de dev/test** (voir « Validation automatique des organisations ») |
| `src/demo_rm_precheck.py` | Garde-fous pour publier sur un **nœud externe potentiellement partagé** (démo) — pré-checks de collision par `local_id`/organisation/contact, neutralisation de contacts sensibles, scoping du nettoyage par recherche ciblée (pas par diff générique). Jamais utilisé pour le nœud de dev local (voir « Publication sur un nœud externe (démo) ») |
| `src/publish_rudi_geo.py` | Dernière étape du pipeline — déclare les flux WMS/WFS/OGC API (projet voisin `rudi-geo`) pour les JDD publiés ce run (voir « Publication RUDI ») |
| `src/enrichir_descriptions.py` / `src/enrichir_contacts.py` / `src/enrichir_organisations.py` | Rattrapages one-shot (descriptions JDD, contacts génériques) + `enrichir_organisations.enrichir()` (descriptions producteurs, **local au nœud uniquement** depuis le 2026-10-15), aussi **étape du pipeline** |
| `src/enrichir_html_descriptions.py` | Rattrapage `summary[].html` des JDD déjà moissonnés (voir « Rendu HTML du summary ») — dry-run par défaut, `--appliquer` pour écrire |
| `src/reanalyser_faux_positifs.py` | Rattrapage offline des faux positifs INSEE/CP (re-filtrage des fichiers moissonnés, dry-run par défaut — menu 16) |
| `src/mcp_rudi_server.py` | Serveur MCP prototype — interrogation en langage naturel des métadonnées RUDI (voir « Serveur MCP RUDI ») |
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
- `rudi_harvest_toolkit.rudi_client` (package `rudi-harvest-toolkit`) est le foyer canonique de `charger_conf_rudi()`. Ne pas redéfinir un loader local dans un nouveau script.

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

**Cible de publication (mono-nœud).** Le pipeline publie vers **un seul** nœud RUDI, défini par `src/conf/rudi_node.json` (dict `url`/`url_catalog`/`usr`/`pwd`, gitignoré). `rudi_publie` est un **booléen**. Depuis 2026-07-24 la cible est le **nœud source bâti depuis les sources RUDI** (processus natifs, catalog 4030 / storage 4031 / manager 4032 / jwtauth 4033), qui alimente un **portail hybride** (ROOB Docker, microservices remplaçables à chaud par leur version source). Le nœud Docker Podman `rudinode` (3030-3032) est **hors chaîne** mais conservé. Cet environnement de dev RUDI (montage, bascule, correctifs de code source à remonter en PR) est documenté dans **`PLAN_ENVIRONNEMENT_RUDI_SOURCE.md`** (racine). `url_catalog` par config est indispensable : le catalog du nœud source est sur 4030, pas 3030.

`src/publish_rudi.py` est le rattrapage — il travaille **uniquement depuis les `rudi_metadata.json` sur disque** :
- Scanne `data/<dossier>/rudi_metadata.json`, résout l'origine via `construire_index_dossier()` (state.json / state_insee / state_oeb / state_bdnb) ou `DATASETS_GEO`.
- Publie ce qui n'est pas `rudi_publie: true` ; à la réussite, bascule le flag — y compris pour les géo via `state_geo["_rudi_publie"]["<dossier>"]`.
- Un dossier sans correspondance dans les états ni `DATASETS_GEO` est listé pour revue manuelle, jamais auto-publié.
- Reconstruit `fichiers_filtres` en prenant le préfixe `media_type: "FILE"` d'`available_formats` dans l'ordre (mapping positionnel — l'ordre doit être préservé).

**Services géo (`src/publish_rudi_geo.py`, dernière étape du pipeline).** Déclare sur le nœud RUDI, pour chaque JDD géo-publiable, des médias `SERVICE` pointant vers les flux WMS/WFS/OGC API Features du projet voisin **`rudi-geo`** (`../rudi-node-build/rudi-geo/`, MapServer au-dessus des fichiers déjà publiés — pas de republication, pas de copie). Scope automatique : uniquement les `local_id` effectivement (re)publiés pendant **ce run**, via `connectors/rudi_publish.py::local_ids_publies_ce_run()` — alimenté par `publier_si_configue()` **et** par la boucle de rattrapage de `publish_rudi.py` (les deux appellent `enregistrer_local_id_publie()`), vidé en tête de `cli.py::executer_pipeline_complet()` (`demarrer_nouveau_run()` — le process peut vivre plus longtemps qu'un run, voir `dashboard.py`). Aucune nouvelle détection de changement : le fast-path "rien n'a changé, on saute" de chaque moisson (déjà en place en amont de la publication RUDI elle-même) est le filtre "nouveau ou modifié" réutilisé tel quel.

Shelle vers `python3 declare_services.py --apply --local-id <uuid> ...` dans le répertoire `rudi-geo` (chemin par défaut : repo voisin au même niveau que `moissonneuse-batteuse` ; surchargeable par la clé `rudi_geo_dir` de `src/conf/rudi_node.json`), credentials/URL manager transmis en variables d'environnement depuis la même config (`RUDI_MANAGER_URL`/`RUDI_USER`/`RUDI_PASSWORD`) — une seule source de vérité pour les identifiants du nœud, jamais dupliqués entre les deux dépôts. Si les conteneurs `rudi-geo` ne répondent pas (port 8090), tente un `docker compose up -d` puis attend, même schéma que `harvest_auto.py::_demarrer_noeud_rudi()` pour le nœud RUDI lui-même. **Toujours best-effort** : une erreur ou une indisponibilité de `rudi-geo` est journalisée et sautée, ne fait jamais échouer le pipeline de moisson.

**Ménage** (menu CLI 18, `action_menage_rudi()`) : `menage_rudi_one_shot()` liste les datasets présents sur le nœud sans contrepartie locale (orphelins), `menage_organisations()` les organisations plus référencées ; le CLI **propose ensuite leur suppression effective** (`supprimer_dataset()`/`supprimer_organisation()`), protégée par la confirmation tapée `SUPPRIMER`. La détection seule ne mute rien.

**Vidage scopé au pipeline** (menu 22, `vider_rudi.vider_noeud_proprement()`) : ne touche **que ce que le pipeline a créé** — les JDD ayant une contrepartie locale (`data/<dossier>/rudi_metadata.json`), et leurs organisations productrices **non référencées par un JDD laissé en place** (une organisation partagée avec un dataset d'un tiers est épargnée). Les JDD/organisations déposés par d'autres utilisateurs restent intacts ; sans aucune contrepartie locale, rien n'est supprimé (journalisé). À ne pas confondre avec `purger_noeud_portail()` (menu 23), la purge **radicale** qui efface vraiment tout (Mongo du nœud + Strukture + **file d'intégration Kalim** (`kalim_data.integration_request`/`_error` — sans ça, repartir avec les mêmes `local_id`/`global_id` déterministes peut retomber sur un `ERR-304` à cause d'une vieille ligne, sans rapport avec une quelconque course entre process) + Dataverse, après sauvegarde).

**Réinitialisation de `rudi_publie`** (dernière étape des deux actions ci-dessus, `vider_rudi.reinitialiser_rudi_publie()`) : sans elle, un nœud/portail vidé ne republie **jamais** les JDD déjà moissonnés — le fast-path « rien n'a changé depuis le dernier run » de chaque script de moisson (`last_modified` inchangé) retourne avant même d'appeler la publication, laissant intact l'ancien flag `rudi_publie: true` posé lors d'un run précédent (constaté le 2026-10-14 : un pipeline relancé sur un nœud vierge n'a republié que les JDD réellement nouveaux/modifiés depuis le dernier run, tout le reste étant silencieusement sauté par `publish_rudi.py`, qui ne regarde que ce flag). Remet `rudi_publie` à `False` dans les 5 `state*.json` — scopé aux seuls JDD effectivement supprimés pour le vidage propre, à tout l'état pour la purge radicale — sans jamais toucher `data/cache/` ni les fichiers déjà filtrés/traduits sur disque (zéro retéléchargement, zéro refiltrage ; `publish_rudi.py` republie directement depuis les `rudi_metadata.json` déjà sur disque).

## Validation automatique des organisations (portail local, dev)

`src/valider_organisations_portail.py` (menu 25, `action_valider_organisations_portail()`,
dashboard) remplace le clic manuel « valider » dans l'UI Strukture pendant un test de
bout-en-bout — sans ça, chaque organisation créée par le pipeline doit être validée une par
une à la main par un animateur avant que ses JDD ne passent de `refused` (`ERR-112`) à
`published`.

**Garde-fou dur, non contournable même en mode non-interactif (dashboard)** :
`_verifier_portail_local()` refuse toute URL dont l'hôte n'est pas `rudi.localhost`/
`localhost`/`127.0.0.1` — cette action ne doit **jamais** s'exécuter contre un portail de
production, où la validation doit rester un geste humain réfléchi.

**Flux API** (confirmé dans le code source de `rudi-portal-source` — `TasksController`,
`OrganizationsController`, `LinkedProducerController`, `AbstractTaskServiceImpl`,
`OrganizationWorkflowContext`, `organization-process.bpmn20.xml`,
`linked-producer-process.bpmn20.xml`) :
1. `POST {url_portail}/authenticate` (`login`/`password` du compte **animateur**, lu depuis
   `src/conf/credentials_animateur.json`, gitignoré — voir `credentials_animateur.example.json`)
   — le JWT est dans le header de réponse `Authorization` (déjà préfixé `"Bearer "`), pas dans
   le corps.
2. `GET {url_portail}/strukture/v1/tasks?asAdmin=true` — `asAdmin` est **obligatoire** :
   sans lui, l'endpoint renvoie un 500 sec (confirmé en conditions réelles le 2026-10-14, 86
   organisations validées avec succès une fois corrigé). Le filtre serveur `status` est un
   faux-ami : il correspond à une variable de process BPMN interne (`TaskConstants.STATUS`),
   pas au champ `Task.status` visible dans la réponse JSON — lui passer `"PENDING"` renvoie
   silencieusement `[]`. Filtrage entièrement côté client sur `assignee is None` et
   `asset.process_definition_key` présent dans `PROCESS_SEGMENTS` (voir point 4).
3. `PUT .../{segment}/tasks/{id}/claim` puis `PUT .../{segment}/tasks/{id}/do-it/validated`
   (aucun corps dans les deux cas) — `do-it` doit être appelé avec le **même** compte que le
   `claim`, sinon `AbstractTaskServiceImpl.doIt()` refuse (« Task is not assigned to you »).
   `"validated"` est la seule action positive du BPMN ; l'autre valeur possible est `"refused"`.
4. **Deux process BPMN distincts** (`PROCESS_SEGMENTS`), chacun avec son propre segment d'URL :
   `organization-process` → `/organizations/tasks/...` (création/modification/archivage d'une
   organisation) et `linked-producer-process` → `/linked-producers/tasks/...` (rattachement/
   détachement producteur↔organisation). **Valider une tâche `organization-process` de
   création déclenche automatiquement, dans le même appel `do-it/validated`
   (`OrganizationWorkflowContext.startAttachOrganizationToProvider()`), la création d'une
   nouvelle tâche `linked-producer-process`** non assignée — c'est la « demande de
   rattachement » qui reste indéfiniment en attente si on ne reliste pas les tâches après
   coup (bug constaté le 2026-10-14 : seule `organization-process` était couverte, la
   validation semblait complète mais les JDD restaient `refused`/`ERR-112`). Pour la
   rattraper, `valider_toutes_organisations()` boucle en plusieurs passes (liste → validation
   → re-liste, jusqu'à `MAX_PASSES` ou jusqu'à une passe sans **nouvelle** tâche) — une tâche
   déjà tentée (succès ou échec) n'est jamais retentée à la passe suivante.

Best-effort par tâche : une tâche en échec n'empêche pas de traiter les suivantes.

## Renvoi auto après validation d'organisation

`src/renvoi_auto_apres_validation.py` (menu CLI 12, `action_renvoi_auto_apres_validation()`, **et étape du pipeline complet** — voir plus bas) répond à un problème précis : une organisation productrice créée via `publier_dataset()` (cf. ci-dessus) passe par une tâche BPMN de validation modérateur côté UI Strukture ; tant qu'elle n'est pas validée, le nœud reçoit `ERR-112` (« provider non lié à l'organisation ») de Kalim et le JDD concerné reste `metadata_status: refused`. Ce script automatise le rattrapage une fois la validation faite en UI, **sans republier ni retélécharger quoi que ce soit** :

- Détection par `rudi_harvest_toolkit.rudi_client.organisations_nouvellement_validees()` — compare les organisations à `organization_status: VALIDATED` côté nœud à un état persisté (`data/etat_organisations_validees.json`, `{"organisations_traitees": [...]}`) pour ne retraiter que les organisations **nouvellement** validées depuis le dernier passage (jamais tout le catalogue).
- `ressources_pour_organisations()` filtre `toutes_metadonnees_rudi()` sur `producer.organization_id`.
- **Périmètre restreint aux seules ressources encore `refused` et non déjà (re)soumises par un autre maillon du même run** (`local_ids_publies_ce_run()`). Renvoyer une ressource déjà `published`/`sent` — ou déjà publiée par la moisson inline / le rattrapage `publish_rudi` dans le même run — redéclenche `sendToPortal()` côté nœud alors que Kalim traite encore l'envoi précédent : le `GET` préalable du nœud ne trouve pas encore la ressource → `POST` en double → `ERR-304` (« valeur déjà utilisée »), qui fait repasser le JDD en `refused` alors qu'il est bien intégré au portail. C'était la cause des JDD « visibles dans le portail mais refusés côté nœud » (constaté : 33 JDD avec un rapport `OK` puis un `ERR-304`). Une organisation reste non marquée traitée si une de ses ressources bloquées a été laissée en vol ce run (retentée au passage suivant).
- `renvoyer_ressources_connues()` renvoie chaque ressource **un par un** (`PUT /catalog/v1/resources` — la route producteur normale, celle que `publier_dataset()` utilise déjà — **jamais** la route admin `/catalog/admin/portal/resources/send`, qui exige un profil qu'un harvester ne doit jamais détenir). Un `PUT` déclenche lui-même `sendToPortal()` côté nœud (`overwriteMetadata`, indépendant du statut de l'organisation). Pause de 2s entre chaque PUT, jamais en lot : `sendToPortal()` est fire-and-forget côté nœud (pas attendu par le PUT qui le déclenche), donc des envois consécutifs sans pause restent concurrents et peuvent surcharger konsult — ce risque existait déjà avec l'ancienne route admin (voir [[project_crash_recovery_rudi_stack]] en mémoire, qui documente l'incident sur cette route-là) et persiste par ce nouveau chemin, pour la même raison de fond.
- L'état n'est marqué traité qu'**après** la tentative de renvoi (pas avant) : si le process est interrompu en cours de route, la prochaine exécution retrouve les mêmes organisations comme « nouvelles » et retente.
- **Deux cas couverts** : (1) ressources `refused` des organisations **nouvellement validées** depuis le dernier passage ; (2) **rattrapage automatique** de *toutes* les ressources `refused` hors vol dont l'organisation est déjà `VALIDATED` — refus transitoires ou anciens (ex. `ERR-304`/`ERR-500` corrigés depuis) qu'aucune validation fraîche ne reprendrait jamais. Une ressource refusée dont l'organisation **n'est pas encore** `VALIDATED` est ignorée par défaut (ce serait un `ERR-112` futile, repris automatiquement dès la validation via le cas 1) — **sauf avec le paramètre `force`** (voir juste après).
- Conçu pour un usage périodique (cron, ou relancé à la main après chaque session de validation en UI) et pour fonctionner **sans agent** — un simple `python3 src/renvoi_auto_apres_validation.py`, aucune dépendance au dashboard ni à un LLM. **Inclus dans `cli.ETAPES_PIPELINE`** (après la publication RUDI) : chaque passage du pipeline renvoie donc automatiquement les JDD dont l'organisation a été validée depuis le run précédent, et rattrape les refus résiduels repris par le cas 2.

**Levier manuel `force`** (`main(force=True)` / CLI `--force` / menu 12 avec confirmation interactive — fusion le 2026-10-14 de l'ancien menu « Réparer les JDD refusés », `src/reparer_jdd_refuses.py`, supprimé) : ignore le filtre « organisation VALIDATED » du cas 2 et re-soumet **tous** les `metadata_status: refused` hors vol, sans condition sur leur organisation. Utile pour forcer une reprise immédiate sans attendre un passage du pipeline, ou pour un `refused` dont la cause n'a rien à voir avec une organisation (vieux `ERR-304`/`ERR-500` déjà corrigés). Sans ce flag, le script reste sans risque à automatiser (jamais un `ERR-112` futile) ; idempotent dans les deux cas — relancé sans rien à renvoyer, il ne fait rien.

**Dédoublonnage des médias FILE** : deux ressources sources au même titre produisent le même slug, donc le même nom de fichier filtré → `available_formats` porte deux médias FILE identiques (même `media_name`, même `media_id`). Le nœud les envoie alors au portail sous le même identifiant et Kalim plante (bug A7 : IllegalStateException « Duplicate key » → rapport `ERR-500`) : le JDD reste `refused` alors qu'il est intégré. Traité à trois niveaux : `harvest_batch` dédoublonne ses fichiers par nom à la génération (le JSON, les dicts et PDF suivent la même clé), `publier_dataset()` filtre défensivement les FILE dupliqués (garde la première occurrence, réaligne `fichiers_filtres`) au cas où une fiche ancienne repasserait, et `src/reparer_medias_dupliques.py` (menu 24) rattrape les fiches déjà écrites (`--appliquer` pour écrire et re-publier).

**Nettoyage des FILE orphelins** : `publier_dataset()` retire d'`available_formats` les entrées FILE dont le fichier local a disparu (le nœud refuse un media FILE sans fichier physique), puis recorrige le `media_id` de toutes les entrées non-FILE (la lib exige des UUIDv4).

**`media_metadata_page`** : tous les générateurs (datagouv, service géo, INSEE, OEB, BDNB) ajoutent en fin d'`available_formats` une entrée SERVICE `source-metadata` pointant vers la fiche source, avec `media_id` déterministe (UUIDv5 de l'URL).

## Contacts

`connectors/contacts.py` : `extraire_contacts_datagouv()` (depuis les métadonnées data.gouv) et `resoudre_contacts()` avec fallback `contact@example.org` (domaine réservé RFC 2606). Branché dans `traduire_metadonnees()`/`traduire_metadonnees_service()`/`rudi_harvest_toolkit.pivot`. Côté géo, `geo_services.py` extrait les contacts OWS (WFS) et WMS (`wfs_get_contact`, `_extraire_contact_wms`) + MetadataURL. `publier_dataset()` privilégie les contacts de la source et ne retombe sur le contact générique du nœud qu'en dernier recours. `src/enrichir_contacts.py` rattrape les JDD déjà moissonnés restés au fallback.

## Fallback descriptions (`src/translation/description_secours.py`)

Many sources never provide a usable description (data.gouv datasets with empty `description`, INSEE, geo services). `generer_complement()` builds 1-3 factual sentences from what's actually available — theme label, producer, CSV/GeoJSON column names, WMS layer names, keywords as last resort. It never invents what the data means. `description_quasi_vide()` gates this (< 40 chars pour data.gouv ; toujours pour INSEE et géo).

Wired into every live harvest path (les scripts passent `entetes_colonnes` aux traducteurs). `src/enrichir_descriptions.py` est le rattrapage pour les JDD antérieurs — idempotent via le marqueur "Jeu de données du thème". `catalogue.py::partie_descriptive()` extrait la partie descriptive du `summary` pour l'afficher sur les cartes du catalogue.

## Rendu HTML du summary (`rudi_harvest_toolkit.markdown`, package `rudi-harvest-toolkit`)

Le portail (depuis sa v3.3.2, 22/09/2025) affiche le résumé long d'un JDD en injectant `summary[].html ?? summary[].text` tel quel en `innerHTML` (`dataset-informations.component.ts::getSummaryDescription()` → `language.service.ts::getRichTextForCurrentLanguage()`). Les descriptions sources (data.gouv.fr notamment) sont écrites en Markdown (gras, listes, liens, paragraphes séparés par une ligne vide) — sans `html`, ce Markdown s'affichait tel quel sur la fiche catalogue (syntaxe littérale, retours à la ligne perdus puisque le rendu HTML ignore les `\n` hors balises).

`markdown_vers_html()` convertit le sous-ensemble réellement utilisé (gras, italique, liens http/https, listes à puces/numérotées, paragraphes, retours à la ligne simples → `<br>`, pseudo-titres `#`/`##`) — volontairement pas un parseur Markdown complet, aucune dépendance ajoutée (voir « Dépendances » en tête de fichier). Tout le texte source est échappé avant insertion ; les liens à schéma non http(s) sont dégradés en texte simple. Câblé dans `construire_rudi_metadata()` (clé `html` omise si vide, pour que le repli `?? text` côté portail fonctionne). `src/enrichir_html_descriptions.py` est le rattrapage pour les JDD déjà moissonnés/publiés — `--appliquer` pour écrire (dry-run par défaut), même mécanique de démarquage `rudi_publie` que `enrichir_synopsis.py`.

**Piège côté publication** : `rudi_node_write` (1.4.4, dernière version publiée sur PyPI) ne modélise que `lang`/`text` pour `summary[]` — son `RudiMetadata.from_json()`/`put_metadata()` perd silencieusement `html` en le faisant passer par ce modèle incomplet. `rudi_harvest_toolkit.rudi_client::_put_metadata()` (toolkit) contourne ça : valide via `RudiMetadata.from_json()` (mêmes erreurs qu'avant en cas de dict malformé), sérialise via `to_json_str()` (**pas** `to_json()` — ce dernier a un bug propre à la lib pour les champs liste-de-`Serializable` du type de `summary`/`synopsis`, voir commentaire dans le code), puis réinjecte `html` apparié par `lang` (`_reinjecter_summary_html()`) avant le `PUT /resources`. `publier_dataset()` l'appelle à la place de `writer.put_metadata()`. Si une future version de `rudi_node_write` modélise `html`, cette réinjection devient un no-op inoffensif.

## Fallback descriptions d'organisations (`src/translation/organisation_secours.py`)

Les organisations productrices publiées sur le nœud RUDI n'ont **aucune description** — le `producer` publié ne contient que `organization_name`. `enrichir_organisation()` génère `organization_caption` (phrase courte) et `organization_summary` (2-3 phrases) via :
1. **Alias curée** (`src/conf/organisations.py::ALIAS_ORGANISATIONS`) — override manuel ou titre Wikipédia exact
2. **Wikipédia FR** (`src/connectors/wikipedia.py::resumer_wikipedia()`) — caption = description Wikidata (CC0), summary = extract Wikipédia (CC BY-SA) + suffixe `" (source : Wikipédia)"`
3. **Repli factuel** — `"Producteur de jeux de données moissonnés sur {source_label}."`

Câblé dans `construire_rudi_metadata()` (`source_producteur` + `page_producteur` params), donc toutes les voies de moisson (data.gouv, INSEE, OEB, BDNB, géo) enrichissent les organisations au moment de la publication. `publier_dataset()` met à jour les orgs existantes sans summary via `put_admin_api`. `src/enrichir_organisations.py` est le rattrapage pour les orgs déjà publiées — `--dry-run` par défaut, `--force` pour ré-enrichir les non-vides.

## Purge de données (`src/cli.py`)

Menu « Purger des données existantes » : **8 items** indépendants, confirmés un par un (tailles affichées en direct) :
1. Cache de téléchargement intégral (`data/cache/`) ; 2. Cache > 7 jours seulement ; 3. État de moisson (les **5** `state*.json`) ; 4. Sessions de découverte en attente ; 5. Services géo auto-découverts (`geo_services.json`) ; 6. Catalogue généré (+ visionneuses/cartes) ; 7. Historique de découverte (**préserve** `exclus` et `exclusions_termes`) ; 8. **Toutes les données moissonnées** — seule à exiger la confirmation tapée `SUPPRIMER`, purge aussi les états.

Un nouveau fichier/dossier top-level sous `data/` qui doit survivre à l'item 8 doit être un **fichier**, pas un répertoire (l'item ne `rmtree` que les répertoires hors `cache`).

## Tableau de bord web (`src/dashboard.py`)

Stdlib-only (`http.server.ThreadingHTTPServer`), **127.0.0.1 uniquement** — aucune authentification, actions destructrices possibles ; ne jamais exposer au-delà de la machine.

- **Zéro logique métier dupliquée** : importe `cli` et appelle ses `action_*`, `PURGE_ITEMS`, `etat_projet()`. Nouvelle action = l'ajouter au dict `ACTIONS` Python **et** au tableau `ACTIONS` du JS.
- **Assets partagés** : `src/static/dashboard.css`/`dashboard.js` servis sous `/static/`, topbar commun injecté via le marqueur `<!--TOPBAR-->` (`_html_topbar()`). Le catalogue écrit sur disque est **autonome** (CSS inliné à la génération, marqueur invisible en `file://`) — le dashboard substitue le marqueur quand il le sert (`/catalogue`).
- **Un job à la fois** (`_verrou_job`, HTTP 409 sinon) ; un second verrou bloque les purges pendant une moisson. Log en direct par capture stdout (`tee.Tee`) + polling 1 s.
- **Pages** : `/` (actions, état, nœud RUDI, badge examen), `/examen` (backlog en 6 onglets : À examiner / Analyse échouée / Sans ressource / Services géo / Exclus / Ignorés — les 2 derniers depuis `decouverte["historique"]` via `GET /api/historique`, avec `POST /api/historique/rouvrir` pour annuler), `/decouverte` (édition de la config de recherche — requêtes/mots-clés/nb_pages persistés — et test de découverte en direct), `/catalogue`.
- **Découverte interactive exclue** (elle appelle `input()`) — le pipeline du dashboard appelle `executer_pipeline_complet()` sans prompt. `cli.action_moisson_insee(ids=...)` accepte un paramètre pour la même raison.
- Ajout d'un candidat/service géo depuis `/examen` **déclenche automatiquement** (best-effort) la chaîne moisson+catalogue+publication correspondante (`moisson_batch_et_publier`/`moisson_geo_et_publier`) ; si un job tourne déjà, no-op — le prochain run ramasse l'ajout.
- **Carte nœud RUDI (Podman)** : contrôle direct du conteneur (`/api/noeud/*`). `noeud_pret()` fait un vrai probe HTTP du manager — ne pas se rabattre sur le seul statut Podman (bouton « cassé » sinon). Conteneur : `rudinode` (`CONTENEUR_RUDI` dans `rudi_node.py`).
- Purge revalidée **côté serveur** exactement comme `cli._confirmer()`.
- `GET /data/<chemin>` sert les fichiers sous `data/` (path-traversal-guardé) — un lien `file://` depuis une page `http://` est bloqué par les navigateurs.

## Découverte automatique (`src/harvest_auto.py`)

Entrée non-interactive pour cron/Jenkins — ex. `0 5 * * * cd <repo> && python3 src/harvest_auto.py >> logs/harvest_auto.log 2>&1`. Trois étapes, aucun `input()` :

1. **Découverte non-interactive** — `rechercher_et_filtrer_auto()` (voir « Discovery pipeline » pour les règles d'auto-décision).
2. **Warm-up nœud RUDI** — démarre le conteneur Podman si besoin, attend `noeud_pret()` ~60 s ; ne fait jamais échouer le run (publication différée sinon).
3. **Pipeline complet** — `cli.executer_pipeline_complet()` : moisson tabulaire → batch en séquence, puis **INSEE/OEB/BDNB/géo en parallèle** (`ThreadPoolExecutor(max_workers=4)`), puis catalogue → publication RUDI → **renvoi auto après validation d'organisation** (`renvoi_auto_apres_validation.main`, renvoie les JDD débloqués par une validation UI depuis le run précédent) → **enrichissement des organisations** (descriptions, local au nœud uniquement, `enrichir_organisations.enrichir()`) → services géo. Chaque étape est encadrée par un message de début et de fin (`▶ [n/N] <étape> — début HH:MM:SS` puis `✔|✗ [n/N] <étape> — terminé|ÉCHEC en <durée> (HH:MM:SS)`), y compris les moissons parallèles qui s'entrelacent — pour qu'une étape longue et silencieuse (ex. Génération du catalogue) reste traçable.

**Piège Ctrl+C pendant le bloc parallèle** (constaté le 2026-10-14) : un signal d'interruption n'arrête que le thread principal — jamais les workers d'un `ThreadPoolExecutor`. En sortant du `with ThreadPoolExecutor(...)`, Python appelle toujours `shutdown(wait=True)`, qui **bloque jusqu'à la fin du travail en cours des 4 threads**, quoi qu'il arrive. Un Ctrl+C pendant cette étape (la plus longue, surtout à cause de la moisson géo) donne donc l'impression que le process est mort alors qu'il continue de publier vers le nœud en tâche de fond — et relancer un 2e `cli.py` dans ce laps de temps fait cohabiter deux process publiant la même ressource à quelques dizaines de ms d'écart → `ERR-304` sur l'un des deux, qui peut ensuite laisser un statut `refused` trompeur sur une ressource en réalité publiée (le nœud vérifie `integration_error_id` avant `published_at` dans `reckonMetadataStatus()` — bug confirmé côté code source `rudi-catalog`, jamais corrigé). **Avant de relancer un pipeline, toujours vérifier `ps aux | grep cli.py` plutôt que de se fier à un Ctrl+C.**

Exit `0` seulement si tout a réussi. **Logs** : chaque run écrit `logs/harvest_auto_<horodatage>.log` (un fichier par run, gitignoré) ; `cli._executer()` imprime la traceback complète sur échec d'étape.

**Revue du backlog `a_examiner`** : dashboard `/examen` (3 actions par entrée : Ajouter aux candidats / Faux positif / Ignorer) ou revue manuelle CLI (menu 2, `discover.revue_manuelle_a_examiner()` dans `src/review.py`) — aperçu colonne par colonne (CSV/XLSX/GeoJSON/Parquet + gz/bz2/zip), tag manuel du type de variable (`_TYPES_VARIABLES` : INSEE/IRIS, CP seul, commune, adresse, SIREN/SIRET, lat/lon, circonscription…), recomptage `nb_rm` sur le fichier complet avant confirmation. Tout passe par `resoudre_a_examiner()` (source unique de mutation).

## Serveur MCP RUDI (`src/mcp_rudi_server.py`) — prototype étape 1

Exploration en cours (2026-07-31) d'une interrogation en langage naturel du catalogue RUDI via MCP (Model Context Protocol), pensée en étapes :

1. **Métadonnées seules** (implémenté) — le serveur ici présent.
2. Requêtes sur le contenu réel des fichiers tabulaires (non fait — nécessiterait un chargement à la volée, ex. DuckDB, scopé aux formats simples).
3. Section « analyse » côté portail (cartes/dashboards multi-nœuds) — projet BI séparé, recoupe l'existant Superset ([[project-dashboards-superset]] en mémoire), pas engagé.

Le serveur réutilise le même connecteur que `rudi_harvest_toolkit.rudi_client` (`RudiNodeWriter` + `RudiNodeAuth` du package `rudi_node_write`, config `src/conf/rudi_node.json`) — read-only, aucun outil de mutation exposé. Outils : `rechercher_jeux_de_donnees` (texte libre + filtres thème/producteur), `obtenir_jeu_de_donnees` (fiche complète par `global_id`), `lister_themes`, `lister_producteurs`, `statistiques_catalogue`.

- Recherche texte libre maison (pas l'API du connecteur, qui ne fait que du filtrage structuré exact) : normalisation accents/casse + score par nombre de tokens de la requête trouvés dans titre/synopsis/thème/producteur/mots-clés.
- `available_formats[].connector.url` est renvoyé tel quel dans `obtenir_jeu_de_donnees` mais pointe vers `host.docker.internal:4031` — résoluble seulement depuis l'intérieur d'un conteneur du nœud source, pas depuis le poste qui exécute le serveur MCP. Attendu à ce stade (étape 1 = métadonnées, pas de téléchargement).
- Dépendance `mcp` **épinglée à `1.29.0`** (API `FastMCP`) — la version publiée `2.0.0` renomme la classe en `MCPServer` avec une API incompatible, pas adoptée ici volontairement.
- Enregistré comme serveur MCP local (`claude mcp add`, scope `local` — privé, non commité dans `.mcp.json`) pour test direct depuis Claude Code.

## Publication sur un nœud externe (démo)

Le pipeline est pensé mono-nœud (`src/conf/rudi_node.json` unique) et `menage_rudi_one_shot()`
suppose le nœud **dédié** (tout ce qui y est sans contrepartie locale est un orphelin à
nettoyer). Ces deux hypothèses tombent pour une démo sur un nœud externe mis à disposition
par une autre équipe, potentiellement partagé avec d'autres producteurs.

`src/demo_rm_precheck.py` couvre ce cas sans modifier le reste du pipeline :
- **`verifier`** — avant toute publication : nœud joignable, `url_catalog` explicite, auth
  réelle (`POST front/login`), thème valide, et surtout **pré-check de collision** par
  `local_id` (déterministe, cf. `_local_id_depuis_dataset_id()` — identique pour tout
  producteur moissonnant le même JDD data.gouv, donc `publier_dataset()` écraserait
  silencieusement un dataset tiers existant), par nom d'organisation et par nom/email de
  contact. Toute collision avec un objet qui n'est pas à nous est un **STOP dur** (exception
  `CollisionDetectee`), jamais un avertissement.
- **`neutraliser`** — remplace les contacts sources identifiés comme portant l'identité
  d'une vraie personne (ex. `mydataball_iris_2022` : nom + email personnel moissonnés depuis
  data.gouv.fr) par le repli générique du producteur, **avant** tout rattrapage de
  publication. `verifier` refuse de continuer si cette neutralisation n'a pas été appliquée.
- **`snapshot`/`enregistrer`/`diff`** — le nettoyage se base en priorité sur une **recherche
  ciblée par `local_id` connu** (immune à l'activité d'un tiers pendant la fenêtre de démo),
  pas sur un diff temporel générique. Les snapshots orgs/contacts "avant" ne servent que de
  filet de sécurité secondaire. Limitation connue et non résolue : les médias (fichiers)
  côté storage ne sont pas supprimables par ce toolkit, seule la fiche catalog l'est.

**Étapes de rattrapage qui balaient *tout* le nœud (pas seulement nos dossiers locaux) —
à restreindre explicitement sur un nœud partagé.** Constaté en démo RM le 2026-10-16 :
`renvoi_auto_apres_validation.py` a renvoyé 6 vraies ressources RM (organisation
`IRISA - RM_migration`, validée de longue date mais traitée comme « nouvellement validée »
par un clone sans historique) en plus des 2 JDD MyDataBall visés — comportement normal et
documenté (son cas 2, rattrapage de tout `refused` dont l'organisation est `VALIDATED`,
sans distinction de propriétaire) mais hors périmètre d'une démo sur nœud tiers. Contrairement
aux rattrapages `enrichir_descriptions.py`/`enrichir_contacts.py`/`enrichir_html_descriptions.py`
(qui ne lisent que `data/*/rudi_metadata.json`, donc déjà scopés à nos propres dossiers), ces
deux-là interrogent l'état complet du nœud :
- `renvoi_auto_apres_validation.py --scope-fichier <chemin>` : restreint l'ensemble des
  ressources `refused` renvoyées aux seuls `local_id` présents comme clés dans le fichier
  JSON donné — passer le `data/_publies_par_nous.json` écrit par
  `demo_rm_precheck.py enregistrer`.
- `enrichir_organisations.py --organisations "Nom1,Nom2"` : restreint le balayage de
  `writer.organization_list` aux seuls `organization_name` listés — sinon une organisation
  tierce sans résumé (ex. `IRISA - RM_migration`) se verrait enrichie, une écriture sur une
  donnée qui n'est pas la nôtre.

Sans l'une de ces options (usage normal du pipeline sur le nœud de dev dédié), le
comportement est inchangé : « tout le nœud » veut dire « tout ce qui est à nous », donc
aucune restriction n'est appliquée par défaut.

**Aucune suppression de média (fichier storage) n'est possible, sur aucun nœud** (trouvé le
2026-10-16, vérifié par code source + test réel, y compris avec un compte manager admin
local — voir [[project_rudi_storage_media_suppression_impossible]] en mémoire). Retirer un
média d'`available_formats` ou supprimer le dataset entier ne libère jamais l'espace
storage ; la vraie route de suppression de `rudi-storage` existe mais exige une permission
réservée à des comptes système internes au service, hors de portée pour tout compte
producteur **ou** admin manager. **Règle à respecter sans exception pour tout essai de
taille/volume sur un nœud externe** : toujours le même nom de fichier pour un même « slot »
de test (le `media_id` est dérivé du nom — un nom différent crée un orphelin permanent et
invisible, jamais un remplacement). Un orphelin déjà créé se neutralise en ré-uploadant un
fichier vide sous le même `media_id` connu, directement vers storage. **Démo RM (2026-10-16)**
: budget fixé à 200 Mo maximum de données uploadées au total tant que la vraie limite de ce
nœud n'est pas confirmée (le nœud local, lui, a un seuil codé en dur à 500 000 000 octets).

**Création d'organisation (corrigé le 2026-10-15)** : `demander_creation_organisation()`
passe désormais par un `POST {url_catalog}/organizations` sur le **nœud**, avec les
identifiants nœud habituels — c'est le nœud lui-même qui relaie la demande au portail en
utilisant **sa propre identité pré-configurée** (confirmée en conditions réelles contre RM/R7 :
tâche de modération créée, validée en UI, cascade `linked-producer-process` validée,
`organization_status`/`linked_producer_status` passés à `VALIDATED`). **`url_portail` n'est
plus nécessaire pour la création** — ni `usr_portail`/`pwd_portail`.

**Détachement d'organisation (corrigé le 2026-10-15, même jour que la création)** :
`demander_detachement_producteur()` passe elle aussi par le **nœud** (`GET
{url_catalog}/portal/organizations/{uuid}/detach` — **sans** segment `admin/`, sinon le
manager redirige silencieusement (308) vers l'UI et `requests` suit cette redirection,
donnant un faux succès sans qu'aucune tâche ne soit créée ; bug trouvé et corrigé le
2026-10-15 contre le nœud source local avant de valider le chemin définitif), auth nœud
habituelle) — trouvé en capturant le trafic réseau (HAR) du bouton « détacher » de l'UI
nœud (`rudi-manager`, `producerCard.js`), pas en devinant : l'ancien appel portail direct
(`DELETE {url_portail}/strukture/v1/linked-producers/request/{uuid}`) échouait
systématiquement en `401` contre RM/R7 (ce compte n'a aucune identité portail, ni OAuth2
client_credentials ni login classique `POST /authenticate`) — confirmé par 3 méthodes
d'auth testées avant de trouver la bonne route. **`url_portail` n'est plus nécessaire non
plus pour le
détachement.**

**Mise à jour d'organisation : abandonnée (2026-10-15, signalé cassé par l'équipe RUDI
elle-même)** — `demander_mise_a_jour_organisation()` n'est plus à utiliser. Conséquence :
une organisation créée par le pipeline doit être complète **dès la création**
(description, URL…) plutôt que compter sur un enrichissement a posteriori — contrainte
plus forte qu'avant pour `demander_creation_organisation()`/`enrichir_organisations.py`.

**Suppression d'organisation : toujours un appel portail direct**, sans route nœud
équivalente trouvée (vérifié : le bouton "corbeille" de l'UI nœud ne fait qu'un `DELETE`
nœud classique, sans aucun relais portail — cohérent avec `genericController.js::
deleteSingleObject`, qui ne relaie que `OBJ_METADATA`, jamais les organisations). Nécessite
donc toujours de vrais identifiants ROBOT portail (`usr_portail`/`pwd_portail`), absents
pour RM — à défaut, suppression à faire par un compte humain modérateur/animateur sur le
portail (UI), après que le détachement (lui automatisable) a été validé. Voir
`rudi-harvest-toolkit/README.md` « Organisations productrices et validation portail ».

À utiliser uniquement sur une instance clonée séparément (pas le checkout de dev local),
pointant vers le nœud externe — jamais sur `rudinode`/le nœud source local.

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

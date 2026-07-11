# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Three-phase pipeline for harvesting open data relevant to Rennes Métropole (43 communes, EPCI code 243500139):

1. **Discovery** (`src/discover.py`) — interactive tool to find candidate datasets on data.gouv.fr, now including WMS services
2. **Harvest tabular** (`src/main.py` / `src/harvest_batch.py`) — automated pipeline for configured datasets (download → filter RM → translate to RUDI → publish). **Note** : `DATASETS` (in `src/conf/datasets.py`) is currently empty — `src/main.py` is a no-op until a data.gouv.fr tabular dataset is added there. The active harvest paths today are `src/harvest_insee.py` (`DATASETS_INSEE`) and `src/harvest_geo.py` (`DATASETS_GEO`).
3. **Harvest geo** (`src/harvest_geo.py`) — pipeline for geographic services: WFS (download GeoJSON) and WMS (save service reference)
4. **Harvest INSEE** (`src/harvest_insee.py`) — direct downloads from insee.fr (outside data.gouv.fr) for publications configured in `DATASETS_INSEE`, with millésime/URL failsafe via scraping (see `src/connectors/insee.py`)

## Commands

```bash
python3 src/cli.py         # terminal entry point: interactive menu to every action below + full pipeline + purge
python3 src/dashboard.py   # web entry point: same actions as a local dashboard at http://127.0.0.1:8765
```

`src/cli.py` is the terminal way to run the project — it presents a numbered menu (découverte, moisson tabulaire/batch/INSEE/géo, catalogue, publication RUDI, enrichissement des descriptions, pipeline complet, purge de données, état du projet) and calls each script's `main()` in-process. `src/dashboard.py` is the web equivalent (see "Tableau de bord web" below) — same underlying functions, browser UI instead of a terminal menu, minus interactive discovery. Each script also still runs standalone for scripted/cron use:

```bash
python3 src/discover.py    # interactive discovery session (CSV + WFS + WMS)
python3 src/main.py        # harvest tabular data.gouv.fr datasets
python3 src/harvest_batch.py # harvest batch (discovered candidates from decouverte.json)
python3 src/harvest_insee.py [id ...] # harvest INSEE direct publications (optionally filtered by id)
python3 src/harvest_geo.py # harvest geographic services (WFS/WMS/OGC API)
python3 src/catalogue.py   # (re)generate data/catalogue.json + data/catalogue.html
python3 src/publish_rudi.py # catch-up publish to the RUDI node for anything not yet marked rudi_publie
python3 src/enrichir_descriptions.py # catch-up: fill in empty/near-empty summaries for already-harvested JDD
python3 src/harvest_auto.py # unattended daily entry point for cron/Jenkins — see "Découverte automatique" below
```

No dependencies beyond `requests` (stdlib otherwise).

## Key files

| File | Role |
|---|---|
| `src/cli.py` | **Entry point (terminal)** — interactive menu over every action (découverte/moisson/catalogue), pipeline complet, purge de données, état du projet |
| `src/dashboard.py` | **Entry point (web)** — local-only HTTP dashboard reusing `cli.py`'s functions; see "Tableau de bord web" below |
| `src/discover.py` | Discovery: API search, pre-filtering, interactive review, candidate tracking (CSV + WFS + WMS) |
| `src/main.py` | Harvest: download, filter, translate, save for RUDI (tabular datasets) |
| `src/harvest_geo.py` | Harvest: WFS→GeoJSON download, WMS service reference, OGC API Features |
| `src/conf/datasets.py` | `DATASETS`, `DATASETS_GEO`, `DATASETS_INSEE` — all configured datasets |
| `src/conf/communes_rm.py` | Reference data: 43 communes, INSEE codes, postal codes, circonscriptions législatives |
| `src/filters/geographic.py` | `est_dans_rm()`, `est_commune_rm()`, `normaliser()`, `est_circonscription_rm()` |
| `src/connectors/datagouv.py` | data.gouv.fr API calls (metadata, download) |
| `src/connectors/geo_services.py` | WFS/WMS/OGC API connector functions |
| `src/connectors/insee.py` | INSEE direct download (HEAD probe + scraping failsafe), ZIP member extraction |
| `src/connectors/sirene.py` | SIREN list for Rennes Métropole establishments (cached, TTL 30 days) |
| `src/connectors/rudi_node.py` | `publier_dataset()` (publish to RUDI node) + `charger_conf_rudi()` (canonical loader for `src/conf/rudi_node.json`) |
| `src/connectors/http.py` | Shared `requests.Session()` (connection pooling + retry/backoff) used by all connectors above — see "Conventions réseau" below |
| `src/publish_rudi.py` | Catch-up RUDI publication from already-saved `rudi_metadata.json` files — see "Discipline d'état" and "Publication RUDI différée" below |
| `src/translation/datagouv_to_rudi.py` | `traduire_metadonnees()` + `traduire_metadonnees_service()` |
| `src/translation/description_secours.py` | `generer_complement()` — fallback description (theme/producer/columns) for empty/near-empty source descriptions; used by all 3 translators and by `enrichir_descriptions.py`. See "Fallback descriptions" below |
| `src/enrichir_descriptions.py` | Catch-up: regenerates `summary` for already-harvested JDD whose description was empty/near-empty, from files already on disk (no re-download) — see "Fallback descriptions" below |
| `src/state.py` | Run-to-run state (last_modified per dataset) in `data/state.json`, used by `main.py`/`harvest_batch.py`. `harvest_insee.py` keeps its own separate `data/state_insee.json` (not unified — see "Known limitations") |
| `src/catalogue.py` | Builds `data/catalogue.json` + `data/catalogue.html` + WMS map viewers |
| `src/harvest_auto.py` | **Entry point (cron/Jenkins)** — unattended daily run: non-interactive discovery → RUDI node warm-up → `cli.executer_pipeline_complet()`; see "Découverte automatique" below |
| `data/decouverte.json` | Discovery state: vus, candidats, exclus, echecs, exclusions_termes, a_examiner |

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
  "a_examiner":        [{...}, ...],          // ambiguous cases from automated discovery — see below
  "historique":        [{...}, ...]           // "exclure"/"ignorer" decisions on a_examiner entries — see below
}
```

Only `exclus` and `exclusions_termes` survive a history reset — those are deliberate user decisions.

`historique` holds a full snapshot of the `a_examiner` entry (plus `decision`: `"exclure"`|`"ignorer"`, and
`date_decision`) at the moment `resoudre_a_examiner()` resolves it that way — populates the "Exclus"/"Ignorés"
tabs on `/examen` (see "Tableau de bord web" below) with enough context to display without re-fetching, and lets
`rouvrir_historique()` reconstruct the entry byte-for-byte if the decision is undone. `"candidat"`/`"ajouter_geo"`
decisions don't get a `historique` entry — they're already visible elsewhere (`candidats`/`data/geo_services.json`).

## Discovery pipeline

Search → pre-filter → interactive review:

1. **API search** (`REQUETES_STRUCTUREES`): structured queries with `granularity=fr:commune`, `granularity=fr:iris`, `featured=true`, keyword queries. Uses `_paginer()` which handles 404-on-page-N+1 as normal end-of-pagination.
2. **Pre-filter** (`pre_filtrer()`): 3-state parallel pipeline:
   - Check title/description for geo markers → if none, check CSV headers
   - If geo found: analyse full CSV for RM rows
   - Returns `("skip", None)` / `("candidat", result)` / `("presenter", result)`
   - `"candidat"` (nb_rm > 0) is auto-added; `"presenter"` goes to interactive review
3. **Interactive loop**: shows fiche (title, org, ANALYSE line if pre-filtered), asks `s/p/a/x/q`

## Geographic detection in CSVs

`_detecter_champs()` (used by `analyser_csv()` and every other format's analysis path) tries in priority order:
1. **IRIS/INSEE code** (`deviner_champ_iris`): 5-digit commune code or 9-digit IRIS code → `est_iris_rm()` checks against `CODES_INSEE_RM`
2. **Postal code + city** (`deviner_champs`): → `est_dans_rm()`
3. **Address text** (`deviner_champ_adresse`): regex for `35xxx` postal codes or commune name match
4. **SIREN/SIRET** (`deviner_champ_siren`): 9/14-digit id → cross-referenced against `obtenir_sirens_rm()`
5. **Coordinates/geometry** (`deviner_champs_geo`): separate lat/lon columns, or a single combined column — `"lat,lon"` (OpenDataSoft `geo_point_2d`), WKT `POINT/POLYGON/MULTIPOLYGON(...)`, or a GeoJSON geometry serialized as JSON (`geom`/`geo_shape`/`the_geom`, e.g. `{"coordinates": [...]}`) — tested via `est_point_rm()`. For a multi-vertex geometry (polygon...) this is a bbox vertex test, not a true intersection, same tolerance already used for WMS/WFS layer bbox overlap.
6. **Circonscription législative** (`deviner_champ_circonscription`): absolute last resort, tried only if nothing above matched — → `est_circonscription_rm()` checks the code against `CIRCONSCRIPTIONS_RM`. Deliberately least-trusted signal: see "Known limitations" below for why.

Each step is only tried if the previous ones found nothing (mutually exclusive cascade, not simultaneous signals). `normaliser()` strips accents, lowercases, converts `_`/`-` to spaces — applied to column headers before matching.

## API gotchas

- `sort=-views` works; `sort=-metrics.views` returns HTTP 400
- `granularity=fr:commune` works; `granularity=commune` returns 0 results
- 404 on page N+1 is normal end-of-pagination, not an error
- `ressourcerie-datalocale-1` (datalocale.fr) is permanently dead — blacklisted in `ORGS_EXCLUES`

## Column dictionary detection

Some resources have a "Colonne,Description" header format (Ecolab style).
- `telecharger_extrait_csv()` returns `"__DICTIONNAIRE__"` sentinel
- `analyser_csv()` returns `None` (not a failure, just wrong resource type)
- Interactive loop tries the next CSV resource when dict is detected

## WMS/WFS discovery flow

When `discover.py` finds a resource with format `wms`, it calls `analyser_wms()`:
- Probes WMS GetCapabilities → lists layers with RM bbox overlap
- Always returns `("presenter", result)` → goes to interactive review (never auto-added)
- User prompted: "Ajouter aux services géo (DATASETS_GEO) ?"
- If yes: prints a JSON entry to copy into `DATASETS_GEO` in `src/conf/datasets.py`
- Then run `python3 src/harvest_geo.py` to harvest

WMS in catalogue:
- `harvest_geo.py` saves `wms_service.json` (layer list + URL) in the data folder
- `catalogue.py` detects it → generates `wms_map.html` (Leaflet + WMS tile layer)
- Catalogue HTML shows a "carte" link for the live WMS map

RUDI publication:
- WFS/OGC API: GeoJSON files uploaded as `media_type: FILE` + SERVICE entry (WFS endpoint)
- WMS: SERVICE entry only (`interface_contract: "wms"`, no file upload)

## Adding a dataset to harvest

After discovery identifies a candidate, add to `src/conf/datasets.py`:
```python
{
    "dataset_id": "the-dataset-slug-or-id",
    "dossier": "local-folder-name",
    "champ_cp": "column_name_for_postal_code",   # or champ_ville / champ_iris
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

After discovery or manual identification, add to `DATASETS_GEO` in `src/conf/datasets.py`:
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

- Every connector under `src/connectors/` makes HTTP calls through the shared `session` from `src/connectors/http.py` (a single `requests.Session()` with connection pooling and a retry/backoff adapter — 3 retries on 429/5xx and connection errors, no retry on 4xx). New connector code should import and use this `session` rather than calling `requests.get/head/post` directly.
- Every outbound call must set `timeout=`. There is no global default — pick a value proportional to the expected payload (metadata calls: ~10-30s; streaming downloads: 30-120s).
- `src/connectors/rudi_node.py` is the canonical home for `charger_conf_rudi()` (loads `src/conf/rudi_node.json`). Don't redefine a local `_charger_conf_rudi()` in a new harvest script — import it from there.

## Discipline d'état (state.json) pour un script de moisson

For any harvest script that tracks per-dataset state (`data/state.json`-style cache to skip unchanged sources):
- Save state **incrementally** (after each dataset, not only at the end of the run) — a crash or interruption partway through a run should not lose the state already earned for datasets already processed.
- The state entry is written as soon as the **local** pipeline succeeds (download → filter → translate → save `rudi_metadata.json` to disk), regardless of whether the RUDI publish step that follows succeeds. This keeps "did I harvest this" (cheap to skip via `dataset_a_change()` / `_inchange()`) and "did I publish this" (cheap to retry without re-harvesting) as two independent concerns.
- RUDI publish outcome is tracked with an explicit `"rudi_publie": bool` field on the state entry — `True` only if `publier_dataset()` actually succeeded this run; `False` if there was no `rudi_node.json` conf, or publish raised. See `traiter_dataset()` in `src/main.py` and `traiter_publication()` in `src/harvest_insee.py` for the pattern. Don't gate the state *write* on publish success — gate the `rudi_publie` *value* instead, and let `src/publish_rudi.py` (see below) handle retrying.
- Tolerate a corrupted/truncated state file by falling back to `{}` with a printed warning rather than crashing the whole run (see `charger_state()` in `src/state.py`).
- A **legitimately empty result** (0 RM rows after filtering — `"vide"` in `harvest_batch.py`) still gets a state entry (`nb_rm: 0`, no filtered file on disk), so it's cached like any other unchanged source and isn't re-downloaded every run. This is deliberately different from an **`"echec"`** (network/parsing error), which never gets a state entry and is always retried next run — an empty result is a confirmed fact about the source, a failure might resolve itself once code or network conditions change.

## Publication RUDI différée (`src/publish_rudi.py`)

Harvesting and publishing to the RUDI node are decoupled: each harvest script attempts to publish inline right after translating a dataset, but if that fails (node unreachable) or is skipped (no `rudi_node.json` yet), the dataset is still recorded as harvested (`rudi_publie: false`) rather than retried via a full re-harvest next run.

`src/publish_rudi.py` is the catch-up step — it works **only from already-saved `rudi_metadata.json` files on disk**, no re-download/re-filter:
- Scans every `data/<dossier>/rudi_metadata.json`.
- Resolves each dossier's origin by matching it against the `dossier` field in `state.json` (tabular/batch) or `state_insee.json` (INSEE) entries, or against `DATASETS_GEO` (geo services, which have no state file — always retried, same as `harvest_geo.py` itself).
- Publishes only entries where `rudi_publie` is not already `true`; on success, flips the flag in the corresponding state file (geo entries are tracked via `state_geo["_rudi_publie"]["<dossier>"]`).
- A dossier with `rudi_metadata.json` but no match in either state file or `DATASETS_GEO` (e.g. a config entry that was later removed) is listed but **not** auto-published — printed for manual review instead, since there's no way to confirm it's still meant to be published.
- Reconstructs the `fichiers_filtres` argument for `publier_dataset()` by taking the `media_type: "FILE"` prefix of `available_formats` in order (the translation functions always put FILE entries before the trailing SERVICE entry — `publier_dataset()` maps `fichiers_filtres[i]` positionally to `available_formats[i]`, so order must be preserved).

Included as the final step of `cli.py`'s "pipeline complet". The very first run after this feature was introduced republishes everything (~170 datasets in this repo) since pre-existing state entries predate the `rudi_publie` field and are treated as not-yet-confirmed — expected one-time cost, not a bug.

## Fallback descriptions (`src/translation/description_secours.py`)

Many sources never provide a usable description: data.gouv.fr datasets with an empty `description` field, INSEE direct publications (no description field at all on insee.fr), and geo services (`DATASETS_GEO` has no description field). Left alone, the 3 translation functions (`traduire_metadonnees()`, `traduire_metadonnees_service()` in `datagouv_to_rudi.py`, `_generer_rudi_metadata()` in `harvest_insee.py`) would produce a `summary` that's pure boilerplate ("Version localisée sur Rennes Métropole...\n\nSource : URL") with zero actual content.

`description_secours.generer_complement()` builds 1-3 factual sentences from whatever's actually available — theme label, producer, CSV/GeoJSON column names, WMS layer names, or keywords as a last resort — and appends them to `summary`. It never invents what the data means, only states what's verifiably true (thème, producteur, colonnes/couches réelles). `description_quasi_vide()` gates this: triggered when the source description is under `SEUIL_CARACTERES` (40 chars) for data.gouv.fr datasets; always triggered for INSEE and geo (no source description ever exists for those two paths).

This is wired into the live harvest paths, so **every future harvest automatically gets a non-empty description** — `harvest_batch.py`/`main.py` pass `entetes_colonnes` (CSV headers from the just-filtered file) into `traduire_metadonnees()`; `harvest_insee.py`'s `traiter_publication()` tracks `dernieres_entetes` the same way; `traduire_metadonnees_service()` reads GeoJSON properties (WFS/OGC) or WMS layer names directly.

For JDD already harvested before this existed, `src/enrichir_descriptions.py` is the catch-up step (same pattern as `publish_rudi.py`): scans `data/<dossier>/rudi_metadata.json`, regenerates `summary` only where it's still quasi-empty, reading columns from the filtered CSV/GeoJSON already on disk — no re-download, no RUDI republish (run "Publier sur le nœud RUDI" afterward if you want the fix to reach the node). Idempotent: detects its own previously-injected text via the `MARQUEUR` string ("Jeu de données du thème") and skips already-enriched entries.

`catalogue.py` surfaces the result: `partie_descriptive()` (shared, also used by the catch-up script) strips the standard localization preamble from `summary` to get just the descriptive part, exposed as `description` in `catalogue.json` and rendered under the synopsis on each catalogue card.

## Purge de données (`src/cli.py`)

The CLI's "Purger des données existantes" menu offers 7 independent, individually-confirmed items (sizes shown live):
1. **Cache de téléchargement** (`data/cache/`) — low risk, fully regenerable, usually the biggest disk hog (HTTP download cache shared by `discover.py` and `harvest_batch.py`).
2. **État de moisson** (`state.json` + `state_insee.json`) — forces a full re-check of every source next run.
3. **Sessions de découverte en attente** (`derniere_recherche.json`, `derniers_prefiltres.json`) — discovery's own resume cache.
4. **Services géo auto-découverts** (`geo_services.json`) — feeds `DATASETS_GEO`; deleting loses auto-detected services (manual entries in `datasets.py` are unaffected).
5. **Catalogue généré** — `catalogue.json`/`.html` + every `*_viewer.html`/`*_map.html`/`wms_map.html`; fully regenerable.
6. **Historique de découverte** (`decouverte.json`) — resets `vus`/`candidats`/`echecs`/`echecs_n`/`sans_ressource`/`a_examiner` but **preserves** `exclus` and `exclusions_termes` (deliberate manual decisions, per the "Discovery state" section above).
7. **Toutes les données moissonnées** (every `data/<dossier>/` except `cache`) — the only item requiring the typed confirmation `SUPPRIMER` rather than `oui`. Also clears `state.json`/`state_insee.json` as part of the same action — otherwise the next run would see unchanged `last_modified` timestamps and skip re-harvesting despite the local files being gone.

If you add a new top-level file/dir under `data/` that should survive a "données moissonnées" purge (item 7), it must be a **file**, not a directory — item 7 only `rmtree`s directories (besides `cache`, which has its own item).

## Tableau de bord web (`src/dashboard.py`)

Stdlib-only HTTP server (`http.server.ThreadingHTTPServer`, no framework) exposing the same actions as `cli.py` in a browser at `http://127.0.0.1:8765`. Binds to **127.0.0.1 only** — there is no authentication, and it can trigger destructive (purge) or networked (RUDI publish) actions, so it must never be exposed beyond the local machine.

- **Zero duplicated business logic**: `dashboard.py` imports `cli` and calls its `action_*` functions, `PURGE_ITEMS`, `etat_projet()`, and `ETAPES_PIPELINE` directly. If you add a new action to `cli.py`, wire it into `dashboard.py`'s `ACTIONS` dict (Python) and the `ACTIONS` array in `PAGE_HTML`'s embedded JS — both are simple declarative lists, not logic to reimplement.
- **One job at a time**: a module-level lock (`_verrou_job`) rejects a new `/api/job/<action>` request with HTTP 409 while one is already running. A second `_traiter_purge()` lock also blocks purges while a harvest job is active, to avoid mutating state files mid-run.
- **Live log via stdout capture, not SSE**: while a job runs, its target thread's `sys.stdout` is swapped for a `_Tee` that writes to both an `io.StringIO` buffer and the real stdout (so the terminal running `dashboard.py` still shows everything too). The browser polls `GET /api/job` every second and re-renders the full buffer — simplest robust option with stdlib only; no incremental diffing, fine at this log volume. Carriage-return progress lines (`print(..., end="\r")`, e.g. download progress in `datagouv.py`) accumulate as raw text rather than overwriting in place — cosmetic only.
- **Découverte interactive is excluded** from the dashboard's action set — `discover.main()` (and `cli.action_pipeline_complet()`'s discovery-inclusion prompt) calls `input()`, which would block the job thread forever with no way to answer from a fire-and-forget POST. The dashboard's pipeline button calls `cli.executer_pipeline_complet()` directly (no discovery step, no prompts) rather than `cli.action_pipeline_complet()`. The page shows a static card pointing to `python3 src/cli.py` for discovery.
- **`cli.action_moisson_insee(ids=...)`** takes an optional `ids` param specifically so the dashboard can pass the web form's text value without going through `input()` — `ids=None` (the terminal default) still prompts; `ids=""` or a space-separated string never does.
- Purge confirmation is re-validated **server-side** (`_traiter_purge()`) exactly like `cli._confirmer()` — never trust the browser's disabled-button state alone for the destructive item (typed `SUPPRIMER`, case-sensitive exact match).
- **Nœud RUDI card is the one exception to "go through `cli.py`"**: it calls `connectors/rudi_node.py`'s `statut_conteneur()`/`demarrer_conteneur()`/`arreter_conteneur()`/`noeud_pret()` directly (`GET /api/noeud`, `POST /api/noeud/<demarrer|arreter>`), because there's no terminal equivalent in `cli.py` to reuse — Podman container control has no CLI menu entry today. The container name is `CONTENEUR_RUDI` in `rudi_node.py` (currently `"rudinode"`) — update it there if the local node container is ever renamed. `statut_conteneur()` alone only reflects the Podman process state (`running` right after `podman start`), not whether the app inside has finished booting — `noeud_pret()` does a real HTTP probe of the manager URL, and `_etat_noeud()`'s `pret` field gates both the "Ouvrir le nœud" link and the badge (shows "démarrage…" with faster 2s polling until ready, see `actualiserNoeud()`'s `intervalleRapideNoeud`) — don't collapse this back to relying on Podman status alone, that's what made the button look broken.
- **Catalogue link is served by the dashboard itself, not `file://`**: `GET /data/<chemin>` (`Handler._servir_fichier_donnees()`) serves any file under `data/` (path-traversal-guarded, `mimetypes`-guessed content type) so that `catalogue.html` and the sibling `*_viewer.html`/`*_map.html`/resource files it links to (relative paths from `catalogue.json`, e.g. `"<dossier>/<fichier>"`) resolve correctly under `/data/...`. A direct `file://` link was tried first and doesn't work — modern browsers block top-level navigation from an `http://` page to a `file://` URL.
- **"JDD à examiner" lives on its own page** (`GET /examen` → `PAGE_EXAMEN_HTML`), not inline on the main dashboard — the full table (title/org/reason/actions per entry) made the main page too tall once the backlog grew. The main page keeps only a one-line summary with a count badge (`actualiserBadgeExamen()`, polls `/api/a_examiner` every 15s for `.length` only) and a link to `/examen`. The two pages' `<style>` blocks are independent copies of the shared rules they each need (badge/button/table.purge/notif) rather than a shared constant — small enough duplication that a shared-CSS abstraction wasn't worth it.
- **`/examen` splits the backlog into 6 tabs** (`ONGLETS` in `PAGE_EXAMEN_HTML`'s JS, `basculerOnglet()`): "À examiner" (tabulaire only, still pending) / "Analyse échouée" (raison starts with `"analyse échouée"`, `sans_ressource` not yet True) / "Sans ressource" (`sans_ressource === true`) / "Services géo" (WFS/WMS still pending) / "Exclus" / "Ignorés". The first four are four mutually-exclusive client-side filters over the one `GET /api/a_examiner` array (`chargerExamen()`), same array as before, no new logic server-side — `sans_ressource` and `raison` are checked first, then the remainder is split `type === "tabulaire"` (→ "À examiner") vs not (→ "Services géo"). "Exclus"/"Ignorés" are backed by a separate `GET /api/historique` (`_historique_json()` in `dashboard.py`), sourced from `decouverte["historique"]` (see "Discovery state" above) — each entry there is a full snapshot of the `a_examiner` entry at decision time, so these tabs render title/org/raison without any re-fetch. `_historique_json()` also merges in any bare dataset-id-only entries from the legacy `decouverte["exclus"]` list (pre-dates `historique`) with a "titre inconnu" placeholder and no "Rouvrir" button (`rouvrable: false` — no snapshot to restore). `POST /api/historique/rouvrir` (`discover.rouvrir_historique()`) undoes an "exclure"/"ignorer" decision: re-appends the stored snapshot to `a_examiner` and, for an exclusion, also strips the id back out of `decouverte["exclus"]` — otherwise `_filtrer_communs()`'s "already excluded" check would silently block it from ever being re-discovered again even though it's sitting back in the visible backlog.
- **"Services géo" has a bulk-resolve button**: `discover.resoudre_wfs_confirmes_en_masse()` applies the `"ajouter_geo"` decision to every WFS entry with `nb_rm > 0` in one call (`POST /api/a_examiner/resoudre_wfs_masse`) — covers the reliquat of WFS confirmed *before* `rechercher_et_filtrer_auto()`'s auto-bypass existed (that bypass only prevents *future* WFS-with-features from ever reaching `a_examiner`; it doesn't retroactively resolve entries already sitting there). The button in `PAGE_EXAMEN_HTML` only renders when at least one such entry is present in the currently loaded batch.
- **Adding a candidate/geo service from `/examen` auto-triggers its harvest+publish, best-effort**: `_traiter_a_examiner()` and `_traiter_resoudre_wfs_masse()` call `_demarrer_job()` (the same one-job-at-a-time mechanism as the manual dashboard buttons) right after a successful `"candidat"`/`"ajouter_geo"` decision — `moisson_batch_et_publier`/`moisson_geo_et_publier` (two new chained `ACTIONS` entries: `cli.action_moisson_batch()`/`action_moisson_geo()` → `action_catalogue()` → `action_publier_rudi()`, deliberately narrower than the 8-step `pipeline_complet` since INSEE/OEB/BDNB/tabulaire data.gouv are unrelated to what just changed). This exists because without it, an addition made via `/examen` was pure `decouverte.json`/`geo_services.json` bookkeeping — inert until someone manually ran the corresponding moisson + catalogue + publish steps (or `harvest_auto.py`, which is **not** wired to any cron/systemd/Jenkins on this machine — see "Known limitations"). If a job is already running when the decision is made, the trigger is a no-op (the decision itself is never blocked or lost) — `harvest_batch.py`/`harvest_geo.py` re-read the *entire* `decouverte["candidats"]`/`DATASETS_GEO` list on every run and skip already-unchanged sources via `state.json`/`state_geo.json`, so whichever job is currently running (or the next manually-triggered one) picks up the addition anyway. Both chained actions are also exposed as manual buttons on the main dashboard page (`ACTIONS` in `PAGE_HTML`'s JS) as a fallback for when the auto-trigger was skipped.
- **A manual "Analyser" failure now reclassifies the row instantly**: `_traiter_a_examiner_preview()` used to persist nothing on a transient failure (network/parsing error, `permanent=False`) — the row silently stayed in "À examiner" with no trace. It now calls the new `discover.marquer_a_examiner_echec()` (same shape as `marquer_a_examiner_verifie()`), which sets `raison` to the `"analyse échouée"`-prefixed format the tab partition already recognizes. The `permanent=True` case (no analyzable resource format at all) is unchanged — still routes to "Sans ressource" via `marquer_a_examiner_verifie()`, a distinct and already-correct state. JS `analyserJdd()` now calls `chargerExamen()` unconditionally on any failure (previously only when `permanent`), so the row moves to the right tab immediately instead of waiting for the next periodic reload.

## Découverte automatique (`src/harvest_auto.py`)

Unattended daily entry point, meant to be scheduled on cron/Jenkins — e.g.
`0 5 * * * cd <repo> && python3 src/harvest_auto.py >> logs/harvest_auto.log 2>&1`, or an
equivalent Jenkins job calling the same script. Runs three steps, none of them ever calling
`input()`:

1. **Non-interactive discovery** — `discover.rechercher_et_filtrer_auto(decouverte)` runs the
   same search + pre-filter pipeline as the interactive `discover.py` session (`_paginer()`
   over `REQUETES_STRUCTUREES`, `_filtrer_communs()`, `pre_filtrer()` in parallel — no logic
   duplicated), but never blocks on a decision:
   - Tabular datasets with RM data detected (`nb_rm > 0`) are added to `decouverte["candidats"]`
     automatically, exactly like the interactive session already does today.
   - WFS/WMS services, and tabular datasets that are ambiguous (0 RM detected, or the analysis
     itself failed) are upserted (deduped by `dataset_id`) into a new persistent list,
     `decouverte["a_examiner"]`, instead of being shown interactively — see schema in
     "Discovery state" above.
2. **RUDI node warm-up** — if `src/conf/rudi_node.json` exists, starts the Podman container
   (`rudi_node.demarrer_conteneur()`) if it isn't already running, then polls
   `rudi_node.noeud_pret()` for up to ~60s. Never fails the run: if the node stays unreachable,
   publication for this run is simply deferred (`rudi_publie: false`), and gets retried by
   `publish_rudi.py` on the next scheduled run — the same tolerance the pipeline already has.
3. **Existing pipeline** — calls `cli.executer_pipeline_complet()` unchanged (moisson
   tabulaire/batch/INSEE/OEB/BDNB/géo → catalogue → publication RUDI).

Exits `0` only if discovery ran without raising *and* every pipeline step succeeded — `1`
otherwise, so a Jenkins job can flag a failed build. Running the interactive `discover.py` by
hand in between scheduled runs is safe: both write to the same `decouverte.json`, and
`candidats`/`vus` are pure accumulations (no conflict), so nothing gets duplicated or lost.

**Logs**: every run writes its full console output to `logs/harvest_auto_<horodatage>.log`
(one file per run — easier to pull up a specific failed run than to grep one ever-growing
file; `*.log` is gitignored). `cli._executer()` — used by every pipeline step, and by
`harvest_auto.py` itself for the discovery step — prints the full traceback (not just
`str(e)`) on any step failure, so the log file alone is normally enough to diagnose a failure
without reproducing it interactively.

**Reviewing the `a_examiner` backlog**: `src/dashboard.py` exposes it under the "JDD à
examiner" card (`GET`/`POST /api/a_examiner`, backed by `discover.resoudre_a_examiner()`).
Three actions per entry: **Ajouter aux candidats** (tabular only, requires a detected field —
false negative, the data was actually usable), **Faux positif** (adds to `decouverte["exclus"]`,
permanent), **Ignorer** (just removes the entry — e.g. once a WFS/WMS JSON snippet has been
copied by hand into `DATASETS_GEO` in `src/conf/datasets.py`, still a deliberate manual step,
not automated). This is the same curation gate the interactive tool already applies to geo
services — the dashboard view only makes the backlog visible across days instead of requiring
a live terminal session.

**Manual column tagging for the `a_examiner` backlog** (menu item 2, `src/cli.py` →
`discover.revue_manuelle_a_examiner()`): for the harder cases the dashboard's "Ajouter aux
candidats" can't handle — auto-detection found *no* usable column at all, or the wrong one —
this walks each tabular `a_examiner` entry, downloads its CSV resource, and shows a
column-by-column preview (header + up to 3 example values) instead of raw preview lines. The
user then tells it directly which column is the INSEE/IRIS code, the postal code, the city,
the address, or a SIREN/SIRET — one prompt per field type, defaulting to whatever
auto-detection already guessed (`champs_detectes`) when there is one. It recomputes `nb_rm` on
the full downloaded file with the tagged columns (reusing `_compter_lignes_rm()`, the same
counting function the auto-detection path uses) before asking to confirm. Confirming or
excluding both go through `resoudre_a_examiner()` (now accepting an optional
`champs_manuels` override) so there's still a single place that mutates
`a_examiner`/`candidats` — the dashboard's API keeps working unchanged.

Scoped to **tabular entries only** (WFS/WMS stay on the existing dashboard/interactive-review
paths) and **CSV resources only** — XLSX/ZIP/GZ/Parquet candidates are reported and left in the
backlog rather than supported end-to-end. `champ_dep` (INSEE code reconstructed from a 3-digit
code + a separate département column, a narrow DGFiP pattern) isn't taggable here either. Like
`discover.main()`'s interactive loop, this is `input()`-heavy end to end, so — same reasoning
as the "Découverte automatique" exclusion above — it is **not** wired into `dashboard.py`; a
web equivalent is a deliberate future step, not an oversight.

Tagging a column as **SIREN/SIRET** is a first-class filter field now, not just an analysis
signal: `champ_siren` is persisted on the candidat dict alongside `champ_cp/champ_ville/
champ_iris/champ_adresse`, and `harvest_batch.py`'s `_ligne_est_rm()` (and every `filtrer_*`
function feeding it) knows how to filter by it — matches rows whose value, truncated to 9
digits, is in `connectors/sirene.py::obtenir_sirens_rm()` (the RM establishment SIREN set,
already itself scoped to RM addresses at the source — this *is* the "cross-reference with
addresses" the SIREN case needs). Before this, `champ_siren` was computed during auto-analysis
(`discover.py::_compter_lignes_rm()`) but silently dropped everywhere a candidat got persisted
— confirming a SIREN-detected candidate would harvest 0 rows. That auto-detection persistence
gap (`traiter_resultat()`, `rechercher_et_filtrer_auto()`, `_reanalyser_candidats_sans_champ()`
still don't carry `champ_siren`/`champ_lat`/`champ_lon` into the candidat dict) is unchanged —
only the manual-tagging path and the harvest-time filter were fixed.

The manual "type de variable" picker (`discover._TYPES_VARIABLES`, shared by
`revue_manuelle_a_examiner()` and the dashboard's `a_examiner` test tool) has a standalone
**"Code postal seul"** type (`champ_cp`) alongside the "commune" bucket — useful when a column
is known to hold *only* a postal code (not an INSEE/IRIS code or a commune name), so the count
isn't muddied by `est_valeur_commune_rm()`'s broader matching. Auto-detection (`CHAMPS_CP`) has
always matched `code_postal`/`code postal`/`codepostal` directly, plus any header containing
"postal" as a substring fallback — this was already working before the standalone type existed.

**Centroïde / WKT point columns**: `est_point_rm()` (mirrored in `discover.py` and
`harvest_batch.py`) now also parses a WKT `"POINT(lon lat)"` string, not just the OpenDataSoft
`"lat,lon"` combined format — common for a `centroid`/`centroide`/`the_geom_centroid`-style
column. Auto-detection's `CHAMPS_GEO_POINT` list gained `centroid` and its usual variants
(`centroid_geom`, `the_geom_centroid`, `centroid_wgs84`, …), and the manual picker's existing
"Latitude / longitude" type covers WKT centroid columns too — no separate type was needed since
both formats resolve to the same `champ_lat`/`champ_lon=None` combined-column representation.

**Circonscription législative** works the same way as SIREN: `champ_circonscription` is
persisted on candidat/`a_examiner` entries, taggable via `_TYPES_VARIABLES` in the manual-review
flow (CLI and dashboard `/examen`), and filtered by `harvest_batch.py`'s `_ligne_est_rm()` via
`filters/geographic.py::est_circonscription_rm()` (accepts common real-world formats —
`"035-01"`, `"35-01"`, `"3501"`... — normalized to `"DDD-NN"` before the membership check).
Unlike SIREN, it's deliberately tried **last** in every detection cascade — after IRIS, adresse,
SIREN, EPCI, and lat/lon all fail — because a circonscription is geographically larger than RM
(see "Known limitations" below), so it's the least trustworthy signal available.

## Known limitations / not addressed

These were identified during a full pipeline audit but deliberately left as-is rather than changed silently — they need a product/architecture decision rather than a mechanical fix:

- **Separate state files, not unified**: `data/state.json` (main.py, harvest_batch.py), `data/state_insee.json` (harvest_insee.py), and `data/state_geo.json` (harvest_geo.py — WFS/OGC/GeoJSON layers, keyed by signature/ETag/Last-Modified) each use their own schema. Unifying them has migration implications and wasn't done as part of the audit cleanup. WMS layers specifically have **no** skip-if-unchanged logic at all (`traiter_wms()` takes no state) — re-fetched every run; left as-is since `DATASETS_GEO` only has a handful of entries, so the volume impact is negligible.
- **`discover.py` heuristic-level matching nuances** are unchanged: CSV delimiter tie-breaking (`_detecter_delimiteur()` picks the max-count candidate, arbitrary on ties), Parquet column-name coverage for filter pushdown (only recognizes a `COMMUNE`-named column), and the `nb_rm` aggregation semantics when a candidate matches on multiple geo fields across different resources (the returned `nb_rm` is the total across all matched resources, not scoped to the specific field reported). These affect *candidate selection quality* in an interactive tool the user calibrates by hand — changing matching behavior without their input risks silent drift, so they're documented here rather than changed.
- **`DATASETS` is empty** (see "Project overview" above) — `src/main.py`'s tabular data.gouv.fr pipeline is currently inactive, not broken.
- **RUDI theme for auto-discovered candidates stays heuristic**: `rechercher_et_filtrer_auto()` never passes a `theme` to `traduire_metadonnees()`, so it falls back to `_detecter_theme()` (keyword scoring against title/description) exactly like the interactive session already does — occasionally wrong, worth an occasional glance at the catalogue rather than a blocker.
- **`harvest_auto.py` still re-runs the full `REQUETES_STRUCTUREES` sweep every day** (up to 50 pages each); `deja_vus` avoids re-downloading/re-analysing already-known datasets, but the API listing itself isn't incremental. Not addressed here — revisit only if run duration becomes an actual problem.
- **`harvest_auto.py` is not actually scheduled on this machine**: no crontab entry, systemd `.timer`, or Jenkinsfile exists in or around this repo — the crontab line in "Découverte automatique" above is only ever a suggested example, never installed. In practice `harvest_auto.py`/`cli.executer_pipeline_complet()` only run when launched by hand. This is why the `/examen` auto-trigger-on-add (see "Tableau de bord web" above) matters in practice: without it, or without someone periodically running the full pipeline, additions from `/examen` and results of `discover.py` sessions accumulate in `decouverte.json`/`geo_services.json` without ever reaching the RUDI node.
- **`champ_circonscription` is coarser than every other geographic filter field**: a circonscription législative is geographically larger than Rennes Métropole — e.g. RM's 1st circonscription (`035-01`) also covers communes south of Rennes outside RM. Matching "circonscription code ∈ {the 7 RM-overlapping codes}" only proves a row is *somewhere in a circonscription overlapping RM*, not inside one of the 43 communes — unlike IRIS/CP+ville/adresse/SIREN/EPCI/lat-lon, which all precisely identify RM membership. This is why it's always tried last (`discover.py::_detecter_champs()`, `harvest_batch.py::_ligne_est_rm()`) — accepted as an occasional false-positive source for candidates where it's the only field ever detected, not fixed, since there's no cheap way to shrink a circonscription to just its RM-overlapping portion from a single code column.

## Évol. 1 : Détection de changements dans les services géographiques (`src/harvest_geo.py`)

Les trois fonctions de téléchargement (`traiter_wfs()`, `traiter_ogcapi()`, `traiter_geojson()`) retournent désormais un tuple `(list, bool)` — le second élément (`changee`) indique si au moins une couche/fichier a été effectivement re-téléchargé (modifié depuis le dernier run). `data/state_geo.json` stocke une signature (ETag, Last-Modified, Content-Length) par couche WFS/OGC/GeoJSON, exactement comme `state.json` pour les datasets tabulaires.

`traiter_geo_dataset()` (`harvest_geo.py:234`) exploite ce signal :
- Si `changee` est `False` **et** que `rudi_metadata.json` existe déjà sur disque, il saute entièrement la régénération des métadonnées RUDI et la publication sur le nœud — le dataset est considéré comme inchangé.
- Si `changee` est `True` (au moins une couche a changé, ou c'est un WMS — toujours regénéré, GetCapabilities étant léger), la régénération et la publication sont déclenchées comme avant.

Ce mécanisme évite des appels RUDI inutiles à chaque run quand seuls 1 ou 2 services parmi des dizaines ont réellement évolué. Les couches WMS restent toujours regénérées (pas de signature exploitable, le sondage GetCapabilities est négligeable).

## Évol. 2 : Ménage RUDI one-shot et nettoyage des médias FILE orphelins

### 2a : `toutes_metadonnees_rudi()` et nettoyage des FILE manquants (`src/connectors/rudi_node.py`)

`toutes_metadonnees_rudi(conf)` (`rudi_node.py:123`) interroge le nœud RUDI pour retourner la liste complète de tous les datasets enregistrés — utilisé par `publish_rudi.menage_rudi_one_shot()` pour détecter les orphelins (voir 2b). Appelle `writer.filter_metadata_list({})` via la lib `rudi_node_write`.

`publier_dataset()` (`rudi_node.py:200-202`) nettoie désormais les entrées `available_formats` dont le `media_type` est `"FILE"` mais dont le fichier local correspondant a disparu (supprimé entre-temps, dossier purgé manuellement, etc.) :
```python
noms_locaux = {os.path.basename(p) for p in fichiers_filtres if os.path.isfile(p)}
medias[:] = [m for m in medias if m.get("media_type") != "FILE" or m.get("media_name") in noms_locaux]
```
Sans ce filtre, la publication échouait sur une référence de fichier absente — le nœud RUDI refuse un media FILE sans fichier physique associé. Les entrées SERVICE (source data.gouv.fr, source-metadata, etc.) ne sont pas concernées.

### 2b : `menage_rudi_one_shot()` et entrée menu CLI (`src/publish_rudi.py` + `src/cli.py`)

`menage_rudi_one_shot()` (`publish_rudi.py:137`) interroge le nœud RUDI via `toutes_metadonnees_rudi()`, collecte tous les `local_id` des `rudi_metadata.json` présents sur disque, et retourne la liste des datasets enregistrés sur le nœud mais sans contrepartie locale — les **orphelins** (config supprimée, purge de données, migration).
- N'interroge que le nœud, ne mute jamais rien.
- Imprimé en sortie standard avec titre et `local_id` pour chaque orphelin.

`cli.py` expose cette fonction via l'entrée de menu **15. Ménage RUDI one-shot (JDD orphelins sur le nœud)** (`action_menage_rudi()`, ligne 173), pour inspection manuelle avant une éventuelle suppression côté nœud.

## Évol. 3 : Nouveaux mots-clés et requêtes de découverte (`src/conf/discover.py`)

`KEYWORDS` (`discover.py:18`) est passé de quelques entrées génériques (commune, code postal, code insee, iris, adresse) à **26 mots-clés** couvrant les compétences métropolitaines et communales : urbanisme, voirie, stationnement, mobilité, déchet, assainissement, eau, logement, action sociale, sport, culture, crèche, école, périscolaire, marché, espaces verts, éclairage public, cimetière, économie, propreté, habitat, participation citoyenne, équipement public, zone d'activité, restauration scolaire, équipement sportif.

`REQUETES_STRUCTUREES` (`discover.py:34`) s'est enrichi de **30+ nouvelles entrées** structurées par compétence, avec `sort=-views` pour prioriser par popularité. Nouvelles requêtes ajoutées :
- Recherches par format : `format=wms`, `format=wfs`, `format=geojson` (détection des services géographiques)
- Compétences métropolitaines : urbanisme, permis de construire, stationnement, voirie, mobilité, déchets, assainissement, eau potable, éclairage public, logement social, action sociale
- Équipements et services : équipement sportif, équipement culturel, petite enfance, zone d'activité, développement économique, participation citoyenne, propreté urbaine, espaces verts, marché, cimetière
- Thèmes spécialisés : budget participatif, habitat indigne, piscine, restauration scolaire, aire d'accueil, fourrière, bibliothèque, trame verte
- Organismes producteurs : INSEE (`organization: 534fff81a3a7292c64a77e5c`), Cerema (`5c812a16634f416583ed1876`), MTECT-écologie (`534fff8da3a7292c64a77eee`)

Ces nouvelles requêtes augmentent significativement la couverture de l'API data.gouv.fr — chaque mot-clé pagine jusqu'à 50 pages (`NB_PAGES`), soit jusqu'à 1000 résultats par requête. `_filtrer_communs()` et `deja_vus` évitent le re-téléchargement/re-analayse des datasets déjà connus.

## Évol. 4 : `media_metadata_page` — lien vers la fiche source dans les métadonnées RUDI

Une nouvelle entrée `SERVICE` de type `media_metadata_page` est désormais incluse dans `available_formats` par **tous les générateurs de métadonnées RUDI** :

```python
media_metadata_page = {
    "media_id": (UUID déterministe basé sur l'URL de la fiche source),
    "media_type": "SERVICE",
    "media_name": "source-metadata",
    "media_caption": "Fiche de métadonnées du jeu de données source sur data.gouv.fr",
    "connector": {"url": url_fiche_source, "interface_contract": "dwnl"},
}
```

Elle est ajoutée en dernière position de `available_formats` (après les FILE et l'entrée SERVICE source), et apparaît dans les traductions suivantes :

| Fonction | Fichier | Lignes |
|---|---|---|
| `traduire_metadonnees()` | `src/translation/datagouv_to_rudi.py` | 225-234 (création), 246 (append) |
| `traduire_metadonnees_service()` | `src/translation/datagouv_to_rudi.py` | 368-377 (création), 388 (append) |
| `_generer_rudi_metadata()` (INSEE) | `src/harvest_insee.py` | 125-134 (création), 174 (inclusion) |
| `_generer_rudi_metadata()` (OEB) | `src/harvest_oeb.py` | 105-114 (création), 138 (inclusion) |
| `_generer_rudi_metadata()` (BDNB) | `src/harvest_bdnb.py` | 111-117 (création), 136 (inclusion) |

Le `media_id` est déterministe (UUIDv5 basé sur l'URL de la fiche source) — stable d'un run à l'autre, pas de duplication sur le nœud RUDI. Le `media_type` est `"SERVICE"` (pas un fichier à uploader), donc `publier_dataset()` dans `rudi_node.py` ne tente pas de l'uploader ni de vérifier un fichier local — seul le bloc JSON est envoyé au nœud dans `available_formats`.

Cette entrée permet au nœud RUDI d'afficher un lien cliquable vers la fiche de métadonnées d'origine (data.gouv.fr, insee.fr, portail OEB, BDNB) depuis l'interface du catalogue RUDI, sans avoir à ouvrir le fichier source complet.

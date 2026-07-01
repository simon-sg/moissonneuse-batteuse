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
| `src/conf/communes_rm.py` | Reference data: 43 communes, INSEE codes, postal codes |
| `src/filters/geographic.py` | `est_dans_rm()`, `est_commune_rm()`, `normaliser()` |
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
| `data/decouverte.json` | Discovery state: vus, candidats, exclus, echecs, exclusions_termes |

## Discovery state (`data/decouverte.json`)

```json
{
  "vus":               ["dataset-id", ...],   // seen and decided (skip/candidat/exclus)
  "candidats":         [{...}, ...],           // datasets confirmed to have RM data
  "exclus":            ["dataset-id", ...],   // permanent manual skip decisions
  "echecs":            ["dataset-id", ...],   // analysis failed, will be retried
  "echecs_n":          {"dataset-id": 2},     // consecutive failure count
  "sans_ressource":    ["dataset-id", ...],   // no CSV/JSON resource available
  "exclusions_termes": ["Landes", ...]        // term-based exclusions (org/title)
}
```

Only `exclus` and `exclusions_termes` survive a history reset — those are deliberate user decisions.

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

`analyser_csv()` tries in priority order:
1. **IRIS/INSEE code** (`deviner_champ_iris`): 5-digit commune code or 9-digit IRIS code → `est_iris_rm()` checks against `CODES_INSEE_RM`
2. **Postal code + city** (`deviner_champs`): → `est_dans_rm()`
3. **Address text** (`deviner_champ_adresse`): regex for `35xxx` postal codes or commune name match

`normaliser()` strips accents, lowercases, converts `_`/`-` to spaces — applied to column headers before matching.

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

## Publication RUDI différée (`src/publish_rudi.py`)

Harvesting and publishing to the RUDI node are decoupled: each harvest script attempts to publish inline right after translating a dataset, but if that fails (node unreachable) or is skipped (no `rudi_node.json` yet), the dataset is still recorded as harvested (`rudi_publie: false`) rather than retried via a full re-harvest next run.

`src/publish_rudi.py` is the catch-up step — it works **only from already-saved `rudi_metadata.json` files on disk**, no re-download/re-filter:
- Scans every `data/<dossier>/rudi_metadata.json`.
- Resolves each dossier's origin by matching it against the `dossier` field in `state.json` (tabular/batch) or `state_insee.json` (INSEE) entries, or against `DATASETS_GEO` (geo services, which have no state file — always retried, same as `harvest_geo.py` itself).
- Publishes only entries where `rudi_publie` is not already `true`; on success, flips the flag in the corresponding state file (geo entries have nothing to flip — there's no state for them).
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
6. **Historique de découverte** (`decouverte.json`) — resets `vus`/`candidats`/`echecs`/`echecs_n`/`sans_ressource` but **preserves** `exclus` and `exclusions_termes` (deliberate manual decisions, per the "Discovery state" section above).
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

## Known limitations / not addressed

These were identified during a full pipeline audit but deliberately left as-is rather than changed silently — they need a product/architecture decision rather than a mechanical fix:

- **Two separate state files**: `data/state.json` (main.py, harvest_batch.py) and `data/state_insee.json` (harvest_insee.py) are not unified into one schema. Unifying them has migration implications and wasn't done as part of the audit cleanup.
- **`discover.py` heuristic-level matching nuances** are unchanged: CSV delimiter tie-breaking (`_detecter_delimiteur()` picks the max-count candidate, arbitrary on ties), Parquet column-name coverage for filter pushdown (only recognizes a `COMMUNE`-named column), and the `nb_rm` aggregation semantics when a candidate matches on multiple geo fields across different resources (the returned `nb_rm` is the total across all matched resources, not scoped to the specific field reported). These affect *candidate selection quality* in an interactive tool the user calibrates by hand — changing matching behavior without their input risks silent drift, so they're documented here rather than changed.
- **`DATASETS` is empty** (see "Project overview" above) — `src/main.py`'s tabular data.gouv.fr pipeline is currently inactive, not broken.

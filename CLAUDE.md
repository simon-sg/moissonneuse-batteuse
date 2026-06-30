# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Three-phase pipeline for harvesting open data relevant to Rennes Métropole (43 communes, EPCI code 243500139):

1. **Discovery** (`src/discover.py`) — interactive tool to find candidate datasets on data.gouv.fr, now including WMS services
2. **Harvest tabular** (`src/main.py` / `src/harvest_batch.py`) — automated pipeline for configured datasets (download → filter RM → translate to RUDI → publish)
3. **Harvest geo** (`src/harvest_geo.py`) — pipeline for geographic services: WFS (download GeoJSON) and WMS (save service reference)

## Commands

```bash
python3 src/discover.py    # interactive discovery session (CSV + WFS + WMS)
python3 src/main.py        # harvest tabular data.gouv.fr datasets
python3 src/harvest_geo.py # harvest geographic services (WFS/WMS/OGC API)
python3 src/catalogue.py   # (re)generate data/catalogue.json + data/catalogue.html
```

No dependencies beyond `requests` (stdlib otherwise).

## Key files

| File | Role |
|---|---|
| `src/discover.py` | Discovery: API search, pre-filtering, interactive review, candidate tracking (CSV + WFS + WMS) |
| `src/main.py` | Harvest: download, filter, translate, save for RUDI (tabular datasets) |
| `src/harvest_geo.py` | Harvest: WFS→GeoJSON download, WMS service reference, OGC API Features |
| `src/conf/datasets.py` | `DATASETS`, `DATASETS_GEO`, `DATASETS_INSEE` — all configured datasets |
| `src/conf/communes_rm.py` | Reference data: 43 communes, INSEE codes, postal codes |
| `src/filters/geographic.py` | `est_dans_rm()`, `est_commune_rm()`, `normaliser()` |
| `src/connectors/datagouv.py` | data.gouv.fr API calls (metadata, download) |
| `src/connectors/geo_services.py` | WFS/WMS/OGC API connector functions |
| `src/translation/datagouv_to_rudi.py` | `traduire_metadonnees()` + `traduire_metadonnees_service()` |
| `src/state.py` | Run-to-run state (last_modified per dataset) in `data/state.json` |
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

# Plan — Deux dashboards Superset : « Moisson & Pipeline » et « Données territoriales RM »

## Contexte

Le sous-système monitoring (PostGIS `mb-postgis` :5433 + Superset 4.1.1 `mb-superset` :8088) est fonctionnel, mais seul un dashboard de test existe (id=1, slug `moissonneuse-monitoring`, 10 charts partiellement pertinents). Objectif : le remplacer par **deux dashboards conçus** :

1. **« Moisson & Pipeline »** — suivi opérationnel du moissonnage : volumes de JDD à chaque étape (découverte → moisson → publication RUDI), durées/succès du pipeline, backlog.
2. **« Données territoriales RM »** — analyse des données moissonnées relatives au territoire : volumes par commune/thème, couverture territoriale, cartes.

**Décisions utilisateur** : supprimer le dashboard de test ; cartes complètes (contours communes + clé Mapbox) ; inclure les fixes monitor.py.

### État des données (constaté en base, 2026-07-12)

- `monitor.metrics_history` : snapshot journalier (PK `date_key`), 5 lignes. ⚠️ Les jours où seul `_log_pipeline` a tourné sont à 0 partout sauf durée/succès → **toute série temporelle doit filtrer `n_dossiers > 0`**.
- `monitor.pipeline_runs` : 71 lignes, une par étape et par run (`etape`, `duree_secondes`, `succes`). ⚠️ `error_message` jamais renseigné (fix prévu).
- `monitor.datasets` : 401 JDD (`source`, `theme`, `nb_rm`, `rudi_publie`, `data_imported`, `date_harvest`). ⚠️ `titre`/`producteur` remplis seulement pour les géo (fix prévu).
- `filtered.data_rows` : 12,64 M lignes (`dossier`, `theme`, `code_insee` 56 %, `code_iris` 17 %, `code_postal` 19 %, `siren` 29 %, `properties` JSONB). ⚠️ `source` codé en dur `'tabular'` (fix prévu) ; `nom_commune` souvent = code INSEE → toujours joindre `ref.communes_rm` pour le libellé.
- `filtered.geo_features` : 556 k géométries EPSG:2154 propres (POINT 360 k, MULTIPOINT 140 k, MULTIPOLYGON 56 k). Pas de thème → joindre `monitor.datasets` sur `dossier`.
- `ref.communes_rm` : 43 communes avec nom, CP, **centroïde** (pas de contour — import prévu). `ref.iris_rm`/`ref.sirens_rm` : attributs quasi tous NULL → **ne pas fonder de chart dessus** (hors comptage par code IRIS dans data_rows).
- `decouverte.a_examiner`/`candidats`/`historique` : ⚠️ `historique` accumule des doublons à chaque `--refresh` (pas de clé unique) → dédupliquer dans les vues.

### Pattern de provisionnement validé (à réutiliser)

`superset/setup_full.py` est le patron fonctionnel : auth API REST (Bearer + CSRF, admin/admin par défaut via env `SUPERSET_ADMIN_USER/PASSWORD`), vues PostgreSQL physiques créées par psycopg2 (`CREATE OR REPLACE VIEW` dans le schéma `monitor`), datasets physiques (`POST /dataset/`), charts avec `params` **et `query_context`** (helper `build_query_context()` — indispensable, sinon big_number/pie/table cassés), dashboard + `position_json` (packing en ROWs, `height ≥ 5` sinon invisible ; hauteur px = `height*8-32`). Exécution : `docker cp <script> mb-superset:/tmp/ && docker exec -i mb-superset python3 /tmp/<script>` (requests + psycopg2 déjà dans l'image ; PostGIS joignable en `postgis:5432`).

⚠️ Ne PAS reprendre le reset destructif global de `setup_full.py` (il supprime TOUS les dashboards/charts) — supprimer par slug/nom explicites. ⚠️ Bug latent : variable `DSN` non définie dans `get_or_create_db()` — la database « PostGIS Moissonneuse » (id=1) existe, la réutiliser.

---

## Étape 1 — Fixes monitor.py / cli.py (alimentation)

Fichier : `src/monitor.py` (DDL lignes ~110-245, refresh ~423-641, import-data ~698-743, import-ref ~294-380, `_log_pipeline` ~1014-1032) ; `src/cli.py` (`_log_pipeline_etape` ~293-306, `_exec_etape` ~336-341).

1. **Titre + producteur des JDD non-géo** : dans `_refresh`, étendre `_lire_theme_depuis_rudi()` (monitor.py:411-420) en `_lire_meta_depuis_rudi()` qui retourne aussi `resource_title` et `producer.organization_name` du `data/<dossier>/rudi_metadata.json` ; renseigner `monitor.datasets.titre/producteur` pour toutes les sources (upsert existant).
2. **`data_rows.source` réel** : dans `--import-data`, remplacer le `'tabular'` codé en dur (monitor.py:~893) par la valeur `source` de l'entrée `monitor.datasets` correspondante (déjà connue au moment de l'import, l'import itère sur cette table).
3. **`error_message` dans `pipeline_runs`** : `cli._exec_etape` capture déjà l'exception → passer `str(exc)` (tronqué ~500 car.) à `_log_pipeline_etape(etape, duree, succes, erreur=None)` → `monitor._log_pipeline` l'écrit dans la colonne existante.
4. **Étape « Mise à jour monitoring » en fin de pipeline** : dans `cli.executer_pipeline_complet()`, après la publication RUDI, si la base est configurée (même garde silencieuse que `_log_pipeline_etape`), enchaîner `monitor` refresh + import-data (l'import est incrémental via `datasets.data_imported`) + import-ref si tables vides. Best-effort, jamais bloquant — même philosophie que la publication RUDI. Sans ça les dashboards ne se rafraîchissent que quand Simon lance monitor.py à la main.
5. **Contours des communes** : dans `--import-ref`, ajouter `geom GEOMETRY(MultiPolygon, 2154)` à `ref.communes_rm` (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — le DDL `SQL_INIT_DB` est idempotent, l'aligner aussi) et importer les contours depuis `https://geo.api.gouv.fr/communes?codeEpci=243500139&format=geojson&geometry=contour` (43 communes, ~1 requête). Télécharger via la `session` partagée de `src/connectors/http.py` (convention réseau du projet) vers le fichier top-level `data/contours_communes_rm.geojson` (un **fichier** top-level sous `data/` survit à la purge item 8), puis insérer avec reprojection 4326→2154 (réutiliser le helper de reprojection existant de l'import centroïdes/geojson).

Respecter la dégradation propre : tout reste optionnel si `psycopg2`/conf absents.

## Étape 2 — Infra Docker (compose + Mapbox)

Fichier : `docker-compose.yml`, `.env.example`.

- Ajouter au service superset : `MAPBOX_API_KEY: ${MAPBOX_API_KEY:-}` (la config par défaut de Superset lit cette env var — pas besoin de monter `superset_config.py`). Ajouter la clé dans `.env.example` avec commentaire (compte Mapbox gratuit). **Sans clé, les charts deck.gl restent fonctionnels mais sur fond noir** — dégradation acceptable.
- Ajouter un volume nommé `superset_home:/app/superset_home` au service superset : actuellement la métadonnée Superset (SQLite interne) est **perdue à chaque recréation du conteneur**. Le `docker compose up -d` de recréation détruira le dashboard de test — sans importance, on le remplace, et le script de provisionnement re-crée tout.
- Note : `superset/superset_config.py` est vestigial (non monté) — le supprimer ou le documenter comme tel.

## Étape 3 — Vues SQL (schéma `monitor`, créées par le script de provisionnement)

Remplacent les 6 vues actuelles (garder les noms existants quand le contenu est repris). Toutes en `CREATE OR REPLACE VIEW` :

**Dashboard 1 :**
- `v_overview` (existante, enrichie) : dernier snapshot `metrics_history` **où `n_dossiers > 0`** + agrégats live de `monitor.datasets` (total JDD, publiés, % publiés) + backlog.
- `v_pipeline_history` (existante, corrigée) : `metrics_history WHERE n_dossiers > 0`, colonnes candidats/vus/exclus/a_examiner/taille_data (Mo)/lignes RM par source.
- `v_pipeline_etapes` (nouvelle) : `pipeline_runs` brut + `date_run`, pour séries par étape (exclure `etape = 'test'`).
- `v_pipeline_dernier_run` (nouvelle) : durée/succès par étape du dernier `date_run`.
- `v_pipeline_echecs` (nouvelle) : étapes `succes = false`, avec `error_message`, 50 dernières.
- `v_datasets_source` (existante, enrichie) : + `n_importes`, `pct_publies`.
- `v_datasets_non_publies` (nouvelle) : `datasets WHERE NOT rudi_publie` (dossier, titre, source, date_harvest).
- `v_entonnoir` (nouvelle) : entonnoir découverte→publication en lignes `(etape, n)` : vus / candidats / moissonnés (COUNT datasets) / publiés (COUNT FILTER rudi_publie) — vus/candidats depuis le dernier snapshot `metrics_history` valide.

**Dashboard 2 :**
- `v_rows_par_commune` (existante, enrichie) : jointure `ref.communes_rm` (libellé propre), + `n_datasets` (COUNT DISTINCT dossier), + `n_lignes`.
- `v_carte_communes` (nouvelle) : `code_insee, nom, n_lignes, n_datasets, ST_AsGeoJSON(ST_Transform(geom, 4326)) AS contour` — pour le deck_polygon (format geojson par ligne).
- `v_rows_par_theme` (nouvelle) : `theme, n_lignes, n_datasets` depuis `data_rows` (libellés FR des thèmes via CASE, cf. `THEMES_RUDI`).
- `v_commune_theme` (nouvelle) : matrice `nom_commune × theme → n_lignes` (jointure communes_rm ; pour la heatmap).
- `v_top_datasets` (nouvelle) : top 30 par volume : `dossier, titre, producteur, theme, source, n_lignes` (jointure `data_rows` groupé × `monitor.datasets`).
- `v_producteurs` (nouvelle) : `producteur, n_datasets, n_lignes` (après fix étape 1).
- `v_qualite_geo` (nouvelle) : répartition des 12,6 M lignes par type de rattachement : `CASE` sur la première colonne non-NULL parmi code_insee/code_iris/code_postal/siren/aucun → `(rattachement, n)`.
- `v_geo_summary` (existante, enrichie) : `dossier, titre, theme, type_geom (GeometryType), n` — jointure `monitor.datasets`.
- `v_carte_points_geo` (nouvelle) : échantillon des features ponctuelles pour la carte : `dossier, titre, theme, ST_X/ST_Y(ST_Transform(geometry, 4326)) AS lon/lat` sur POINT + centroïdes de MULTIPOINT, `TABLESAMPLE`/`LIMIT ~50000` pour rester fluide.

## Étape 4 — Dashboard 1 « Moisson & Pipeline » (slug `mb-pipeline`)

Viz types déjà éprouvés dans l'instance : `big_number_total`, `dist_bar`, `table`, `pie`, `line` (+ `funnel` echarts, dispo en 4.1.1). Chaque chart avec `params` + `query_context`.

**Rangée 1 — KPIs** (6 × big_number_total sur `v_overview`, height 25) :
| # | Indicateur | Mesure |
|---|---|---|
| 1 | JDD moissonnés | total `monitor.datasets` (toutes sources) |
| 2 | Publiés sur RUDI | count publiés + subheader « sur N » |
| 3 | Taux de publication | % publiés |
| 4 | Lignes RM filtrées | somme `*_rm` du dernier snapshot |
| 5 | Backlog à examiner | `decouverte_a_examiner` |
| 6 | Durée dernier pipeline | somme `duree_secondes` du dernier `date_run` (min) |

**Rangée 2 — Exécution du pipeline** :
7. « Durée par étape (dernier run) » — dist_bar sur `v_pipeline_dernier_run`, tri décroissant.
8. « Durée des étapes dans le temps » — line sur `v_pipeline_etapes` (SUM durée, groupby etape, granularité P1D) : montre la dérive (batch ~26 min domine).
9. « Échecs récents » — table sur `v_pipeline_echecs` (date, étape, error_message) — actionnable.

**Rangée 3 — Entonnoir & découverte** :
10. « Entonnoir découverte → publication » — funnel sur `v_entonnoir` (vus 2951 → candidats 422 → moissonnés 401 → publiés 375).
11. « Évolution du backlog » — line sur `v_pipeline_history` (a_examiner, candidats, exclus).

**Rangée 4 — Sources & publication** :
12. « État par source » — table sur `v_datasets_source` (source, n, publiés, importés, lignes RM).
13. « JDD non publiés sur RUDI » — table sur `v_datasets_non_publies` — actionnable.
14. « Volumétrie dans le temps » — line sur `v_pipeline_history` (taille_data Mo, lignes RM).

## Étape 5 — Dashboard 2 « Données territoriales RM » (slug `mb-territoire`)

**Rangée 1 — KPIs** (5 × big_number_total) :
| # | Indicateur | Source |
|---|---|---|
| 1 | Lignes de données RM | COUNT data_rows (12,6 M) |
| 2 | Entités géographiques | COUNT geo_features (556 k) |
| 3 | Communes couvertes | COUNT DISTINCT code_insee ∩ RM, subheader « / 43 » |
| 4 | Thèmes couverts | COUNT DISTINCT theme, « / 14 » |
| 5 | JDD avec données | COUNT datasets data_imported |

**Rangée 2 — Cartes** (deck.gl, height 70) :
6. « Volume de données par commune » — `deck_polygon` sur `v_carte_communes` (geojson = `contour`, couleur = `n_lignes` — **échelle log ou breakpoints** : Rennes 3,2 M vs Miniac 237, une échelle linéaire serait illisible).
7. « Localisation des données géo » — `deck_scatter` (ou `deck_screengrid` si trop dense) sur `v_carte_points_geo`, couleur par thème.

**Rangée 3 — Thèmes & couverture** :
8. « Lignes par thème » — dist_bar sur `v_rows_par_theme` (libellés FR).
9. « Couverture commune × thème » — heatmap sur `v_commune_theme` (métrique log(n_lignes)) : le chart le plus riche du dashboard, montre les trous de couverture.
10. « JDD distincts par commune » — dist_bar (width 12) sur `v_rows_par_commune` (`n_datasets`) : richesse au-delà du seul volume (le volume brut est écrasé par Rennes).

**Rangée 4 — Contenus & qualité** :
11. « Top JDD par volume » — table sur `v_top_datasets` (titre, producteur, thème, lignes).
12. « Principaux producteurs » — dist_bar sur `v_producteurs`.
13. « Types de géométries » — pie sur `v_geo_summary` agrégée par `type_geom`.
14. « Rattachement géographique des lignes » — pie sur `v_qualite_geo` (insee / iris / cp / siren / aucun) : indicateur de qualité du filtrage.

**Filtres natifs du dashboard** (json_metadata `native_filter_configuration`) : Thème + Commune, appliqués aux charts 8-11 (les vues exposent les colonnes nécessaires).

## Étape 6 — Script de provisionnement `superset/setup_dashboards.py`

Nouveau script canonique (remplace `setup_full.py`, qui peut être conservé en référence ou supprimé — `setup_dashboard.py` et `post_setup.py` deviennent obsolètes, à supprimer). Repartir des helpers de `setup_full.py` : `_login/_api`, `chart_fd`, `metric_obj`, `build_query_context`, `create_dataset`, algo de layout.

1. Auth API + connexion psycopg2 (`postgis:5432`, creds env).
2. `ensure_pg_views()` : les ~17 vues de l'étape 3.
3. Réutiliser la database id existante (recherche « moissonneuse » ; corriger le bug `DSN` si chemin création).
4. **Nettoyage ciblé** (pas de reset global) : supprimer le dashboard slug `moissonneuse-monitoring`, ses charts, et les datasets obsolètes (ids virtuels résiduels 1-6 : `metrics_vue`, `data_rows_par_commune`, `a_examiner_repartition`, `geo_features_par_dossier`, `pipeline_today`, + physiques remplacés) — par **nom/slug explicite**, jamais « tout ».
5. Datasets physiques sur chaque vue ; `main_dttm_col` sur les vues temporelles (`date_key`, `date_run`).
6. Création des ~28 charts (params + query_context) puis des 2 dashboards avec layout en rangées (KPI height 25, charts 45, tables/cartes 70) et filtres natifs sur `mb-territoire`.
7. Vérification intégrée : `GET /chart/{id}/data` pour chaque chart, rapport OK/KO en sortie.

Exécution documentée en docstring : `sg docker -c "docker cp superset/setup_dashboards.py mb-superset:/tmp/ && docker exec -i mb-superset python3 /tmp/setup_dashboards.py"`.

Après validation : regénérer un export versionné (`superset/dashboards/`, remplace `dashboard_export.zip`) via `GET /api/v1/dashboard/export/`.

## Ordre d'implémentation & vérification

1. Étape 1 (monitor/cli) → relancer `python3 src/monitor.py --refresh --import-ref` puis `--import-data` (incrémental) → vérifier en SQL : `datasets.titre/producteur` remplis, `data_rows.source` corrigé sur les nouveaux imports (les 12,6 M lignes existantes gardent `tabular` — acceptable, les vues joignent `monitor.datasets` pour la source), `ref.communes_rm.geom` 43/43.
2. Étape 2 (compose) → `docker compose up -d` (recrée superset ; metadata de test perdue = voulu).
3. Étape 6 (provisionnement) → exécuter le script, contrôler le rapport de vérification.
4. Contrôle visuel : ouvrir http://127.0.0.1:8088/superset/dashboard/mb-pipeline/ et `/mb-territoire/` — vérifier notamment la choroplèthe (contours + échelle log) et la heatmap.
5. Tests : `python3 -m unittest discover tests/` (les fixes cli/monitor ne doivent rien casser ; ajouter un test si un helper pur est extrait, ex. classification `v_qualite_geo` si faite côté Python).
6. Rejouer le pipeline complet (`cli` menu ou dashboard) → vérifier que l'étape « Mise à jour monitoring » journalise et que les dashboards reflètent le run (nouvelle ligne `pipeline_runs`, snapshot `metrics_history` complet le jour même).

## Points de vigilance pour l'implémenteur

- `query_context` obligatoire sur chaque chart (leçon durement apprise du dashboard de test).
- `position_json` : height ≥ 5 ; big_number 25, charts 45, tables/cartes 70 ; width 12 pour les charts à ~43 barres.
- Séries temporelles `metrics_history` : toujours `WHERE n_dossiers > 0`.
- `decouverte.historique` : doublons → `SELECT DISTINCT ON (dataset_id, decision, date_decision)` si utilisé.
- Choroplèthe : échelle **log** ou classes manuelles (écart 237 → 3,2 M).
- Clé Mapbox absente = fond de carte noir mais données visibles — ne pas bloquer dessus.
- Ne jamais rendre le pipeline de moisson dépendant du monitoring (dégradation silencieuse si psycopg2/conf absents — convention existante).

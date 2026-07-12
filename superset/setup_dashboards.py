"""
Provisionne les deux dashboards Superset : « Moisson & Pipeline » et « Données territoriales RM ».

Remplace setup_full.py. Crée les vues SQL, datasets physiques, charts (params + query_context),
dashboards avec layout en rangées, et filtres natifs sur mb-territoire.

Exécution :
    sg docker -c "docker cp superset/setup_dashboards.py mb-superset:/tmp/ && docker exec -i mb-superset python3 /tmp/setup_dashboards.py"
"""
import json
import os
import time

import psycopg2
import requests

API = "http://localhost:8088/api/v1"

s = requests.Session()

# ---------------------------------------------------------------------------
# Auth API
# ---------------------------------------------------------------------------

def _login():
    r = s.post(f"{API}/security/login",
               json={"username": os.environ.get("SUPERSET_ADMIN_USER", "admin"),
                     "password": os.environ.get("SUPERSET_ADMIN_PASSWORD", "admin"),
                     "provider": "db"})
    r.raise_for_status()
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    r2 = s.get(f"{API}/security/csrf_token/")
    s.headers["X-CSRFToken"] = r2.json()["result"]
    s.headers["Referer"] = "http://localhost:8088/"


def _api(method, path, **kw):
    r = s.request(method, f"{API}{path}", **kw)
    if r.status_code >= 400:
        print(f"  {method} {path} -> {r.status_code}: {r.text[:200]}")
        return None
    return r.json()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_or_create_db():
    """Find or create the PostGIS database connection in Superset."""
    r = _api("GET", "/database/")
    if r:
        for db in r.get("result", []):
            if "moissonneuse" in db.get("database_name", "").lower():
                return db["id"]
    # Create if not found
    password = os.environ.get("POSTGRES_PASSWORD", "moissonneuse")
    dsn = f"postgresql+psycopg2://monitor:{password}@postgis:5432/moissonneuse"
    r2 = _api("POST", "/database/", json={
        "database_name": "PostGIS Moissonneuse",
        "sqlalchemy_uri": dsn,
        "expose_in_sqllab": True,
    })
    if r2:
        return r2["id"]
    return None


# ---------------------------------------------------------------------------
# SQL Views
# ---------------------------------------------------------------------------

VIEWS = {
    # ── Dashboard 1 : Moisson & Pipeline ──

    "v_overview": """
CREATE OR REPLACE VIEW monitor.v_overview AS
SELECT
    (SELECT date_key FROM monitor.metrics_history WHERE n_dossiers > 0 ORDER BY date_key DESC LIMIT 1) AS date_key,
    (SELECT tabulaire_total FROM monitor.metrics_history WHERE n_dossiers > 0 ORDER BY date_key DESC LIMIT 1) AS tabulaire_total,
    (SELECT tabulaire_rudi FROM monitor.metrics_history WHERE n_dossiers > 0 ORDER BY date_key DESC LIMIT 1) AS tabulaire_rudi,
    (SELECT tabulaire_rm FROM monitor.metrics_history WHERE n_dossiers > 0 ORDER BY date_key DESC LIMIT 1) AS tabulaire_rm,
    (SELECT insee_total FROM monitor.metrics_history WHERE n_dossiers > 0 ORDER BY date_key DESC LIMIT 1) AS insee_total,
    (SELECT insee_rudi FROM monitor.metrics_history WHERE n_dossiers > 0 ORDER BY date_key DESC LIMIT 1) AS insee_rudi,
    (SELECT insee_rm FROM monitor.metrics_history WHERE n_dossiers > 0 ORDER BY date_key DESC LIMIT 1) AS insee_rm,
    (SELECT filtered_rows FROM monitor.metrics_history WHERE n_dossiers > 0 ORDER BY date_key DESC LIMIT 1) AS filtered_rows,
    (SELECT n_dossiers FROM monitor.metrics_history WHERE n_dossiers > 0 ORDER BY date_key DESC LIMIT 1) AS n_dossiers,
    (SELECT SUM(duree_secondes) FROM monitor.pipeline_runs
     WHERE date_run = (SELECT MAX(date_run) FROM monitor.pipeline_runs WHERE etape != 'test')
       AND etape != 'test') AS pipeline_duree_sec,
    (SELECT BOOL_AND(succes) FROM monitor.pipeline_runs
     WHERE date_run = (SELECT MAX(date_run) FROM monitor.pipeline_runs WHERE etape != 'test')
       AND etape != 'test') AS pipeline_succes,
    (SELECT decouverte_candidats FROM monitor.metrics_history WHERE n_dossiers > 0 ORDER BY date_key DESC LIMIT 1) AS decouverte_candidats,
    (SELECT decouverte_a_examiner FROM monitor.metrics_history WHERE n_dossiers > 0 ORDER BY date_key DESC LIMIT 1) AS decouverte_a_examiner,
    (SELECT COUNT(*) FROM monitor.datasets) AS total_jdd,
    (SELECT COUNT(*) FROM monitor.datasets WHERE rudi_publie) AS jdd_publies,
    (SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE rudi_publie) / NULLIF(COUNT(*), 0), 1) FROM monitor.datasets) AS pct_publies
""",

    "v_pipeline_history": """
CREATE OR REPLACE VIEW monitor.v_pipeline_history AS
SELECT date_key,
       decouverte_candidats AS candidats,
       decouverte_vus AS vus,
       decouverte_exclus AS exclus,
       decouverte_a_examiner AS a_examiner,
       ROUND(taille_data_bytes / 1048576.0, 1) AS taille_data_mo,
       filtered_rows AS lignes_rm,
       tabulaire_rm + insee_rm + oeb_rm + bdnb_rm AS total_rm,
       pipeline_duree_sec, pipeline_succes::int AS pipeline_succes
FROM monitor.metrics_history
WHERE n_dossiers > 0
ORDER BY date_key
""",

    "v_pipeline_etapes": """
CREATE OR REPLACE VIEW monitor.v_pipeline_etapes AS
SELECT id, date_run, start_time, end_time, etape, duree_secondes, succes, error_message
FROM monitor.pipeline_runs
WHERE etape != 'test'
ORDER BY date_run DESC, start_time
""",

    "v_pipeline_dernier_run": """
CREATE OR REPLACE VIEW monitor.v_pipeline_dernier_run AS
SELECT etape, duree_secondes, succes, error_message
FROM monitor.pipeline_runs
WHERE date_run = (SELECT MAX(date_run) FROM monitor.pipeline_runs)
  AND etape != 'test'
ORDER BY duree_secondes DESC
""",

    "v_pipeline_echecs": """
CREATE OR REPLACE VIEW monitor.v_pipeline_echecs AS
SELECT date_run, etape, error_message, duree_secondes
FROM monitor.pipeline_runs
WHERE succes = FALSE
ORDER BY date_run DESC, start_time DESC
LIMIT 50
""",

    "v_datasets_source": """
CREATE OR REPLACE VIEW monitor.v_datasets_source AS
SELECT source,
       COUNT(*) AS n,
       SUM(nb_rm) AS total_rm,
       COUNT(*) FILTER (WHERE rudi_publie) AS n_publies,
       COUNT(*) FILTER (WHERE data_imported) AS n_importes,
       ROUND(100.0 * COUNT(*) FILTER (WHERE rudi_publie) / NULLIF(COUNT(*), 0), 1) AS pct_publies
FROM monitor.datasets
GROUP BY source
ORDER BY n DESC
""",

    "v_datasets_non_publies": """
CREATE OR REPLACE VIEW monitor.v_datasets_non_publies AS
SELECT dossier, titre, source, theme, date_harvest
FROM monitor.datasets
WHERE NOT rudi_publie
ORDER BY date_harvest DESC
""",

    "v_entonnoir": """
CREATE OR REPLACE VIEW monitor.v_entonnoir AS
SELECT * FROM (VALUES
    ('Vus', (SELECT decouverte_vus FROM monitor.metrics_history WHERE n_dossiers > 0 ORDER BY date_key DESC LIMIT 1)),
    ('Candidats', (SELECT decouverte_candidats FROM monitor.metrics_history WHERE n_dossiers > 0 ORDER BY date_key DESC LIMIT 1)),
    ('Moissonnés', (SELECT COUNT(*) FROM monitor.datasets)),
    ('Publiés', (SELECT COUNT(*) FROM monitor.datasets WHERE rudi_publie))
) AS t(etape, n)
""",

    # ── Dashboard 2 : Données territoriales RM ──

    "v_rows_par_commune": """
CREATE OR REPLACE VIEW monitor.v_rows_par_commune AS
SELECT c.code_insee, c.nom, c.code_postal,
       COUNT(DISTINCT dr.dossier) AS n_datasets,
       COUNT(*) AS n_lignes
FROM filtered.data_rows dr
JOIN ref.communes_rm c ON dr.code_insee = c.code_insee
GROUP BY c.code_insee, c.nom, c.code_postal
ORDER BY n_lignes DESC
""",

    "v_carte_communes": """
CREATE OR REPLACE VIEW monitor.v_carte_communes AS
SELECT c.code_insee, c.nom,
       COUNT(DISTINCT dr.dossier) AS n_datasets,
       COUNT(*) AS n_lignes,
       ST_AsGeoJSON(ST_Transform(c.geom, 4326)) AS contour
FROM ref.communes_rm c
JOIN filtered.data_rows dr ON dr.code_insee = c.code_insee
WHERE c.geom IS NOT NULL
GROUP BY c.code_insee, c.nom, c.geom
""",

    "v_rows_par_theme": """
CREATE OR REPLACE VIEW monitor.v_rows_par_theme AS
SELECT
    CASE theme
        WHEN 'economy' THEN 'Économie'
        WHEN 'citizenship' THEN 'Citoyenneté'
        WHEN 'energyNetworks' THEN 'Réseaux, Énergie'
        WHEN 'culture' THEN 'Culture, Sports, Loisirs'
        WHEN 'transportation' THEN 'Mobilité, Transport'
        WHEN 'children' THEN 'Enfance'
        WHEN 'environment' THEN 'Environnement'
        WHEN 'townPlanning' THEN 'Urbanisme'
        WHEN 'location' THEN 'Référentiels géographiques'
        WHEN 'education' THEN 'Éducation'
        WHEN 'publicSpace' THEN 'Espace public'
        WHEN 'health' THEN 'Santé, Sécurité'
        WHEN 'housing' THEN 'Logement'
        WHEN 'society' THEN 'Social'
        ELSE COALESCE(theme, 'inconnu')
    END AS theme_label,
    theme,
    COUNT(*) AS n_lignes,
    COUNT(DISTINCT dossier) AS n_datasets
FROM filtered.data_rows
GROUP BY theme
ORDER BY n_lignes DESC
""",

    "v_commune_theme": """
CREATE OR REPLACE VIEW monitor.v_commune_theme AS
SELECT c.nom, c.code_insee,
       dr.theme,
       COUNT(*) AS n_lignes
FROM filtered.data_rows dr
JOIN ref.communes_rm c ON dr.code_insee = c.code_insee
GROUP BY c.nom, c.code_insee, dr.theme
""",

    "v_top_datasets": """
CREATE OR REPLACE VIEW monitor.v_top_datasets AS
SELECT dr.dossier, d.titre, d.producteur,
       d.theme, d.source,
       COUNT(*) AS n_lignes
FROM filtered.data_rows dr
JOIN monitor.datasets d ON dr.dossier = d.dossier
GROUP BY dr.dossier, d.titre, d.producteur, d.theme, d.source
ORDER BY n_lignes DESC
LIMIT 30
""",

    "v_producteurs": """
CREATE OR REPLACE VIEW monitor.v_producteurs AS
SELECT COALESCE(producteur, 'Inconnu') AS producteur,
       COUNT(*) AS n_datasets,
       SUM(nb_rm) AS n_lignes
FROM monitor.datasets
WHERE producteur IS NOT NULL
GROUP BY producteur
ORDER BY n_datasets DESC
""",

    "v_qualite_geo": """
CREATE OR REPLACE VIEW monitor.v_qualite_geo AS
SELECT
    CASE
        WHEN code_insee IS NOT NULL THEN 'INSEE'
        WHEN code_iris IS NOT NULL THEN 'IRIS'
        WHEN code_postal IS NOT NULL THEN 'Code postal'
        WHEN siren IS NOT NULL THEN 'SIREN'
        ELSE 'Aucun'
    END AS rattachement,
    COUNT(*) AS n
FROM filtered.data_rows
GROUP BY rattachement
ORDER BY n DESC
""",

    "v_geo_summary": """
CREATE OR REPLACE VIEW monitor.v_geo_summary AS
SELECT gf.dossier, d.titre, d.theme,
       ST_GeometryType(gf.geometry) AS type_geom,
       COUNT(*) AS n
FROM filtered.geo_features gf
LEFT JOIN monitor.datasets d ON gf.dossier = d.dossier
GROUP BY gf.dossier, d.titre, d.theme, ST_GeometryType(gf.geometry)
ORDER BY n DESC
""",

    "v_carte_points_geo": """
CREATE OR REPLACE VIEW monitor.v_carte_points_geo AS
SELECT gf.dossier, d.titre, d.theme,
       ST_X(ST_Transform(ST_Centroid(gf.geometry), 4326)) AS lon,
       ST_Y(ST_Transform(ST_Centroid(gf.geometry), 4326)) AS lat
FROM filtered.geo_features gf
LEFT JOIN monitor.datasets d ON gf.dossier = d.dossier
WHERE ST_GeometryType(gf.geometry) IN ('ST_Point', 'ST_MultiPoint')
LIMIT 50000
""",
}


def ensure_pg_views():
    """Create all monitoring views in PostgreSQL."""
    password = os.environ.get("POSTGRES_PASSWORD", "moissonneuse")
    pg = psycopg2.connect(host="postgis", port=5432, dbname="moissonneuse",
                          user="monitor", password=password)
    pg.autocommit = True
    cur = pg.cursor()
    for name, sql in VIEWS.items():
        try:
            # DROP first to avoid column-rename conflicts with CREATE OR REPLACE
            cur.execute(f"DROP VIEW IF EXISTS monitor.{name}")
            cur.execute(sql)
            print(f"  View {name} OK")
        except Exception as e:
            print(f"  View {name} ERREUR: {e}")
    cur.close()
    pg.close()


# ---------------------------------------------------------------------------
# Helpers (from setup_full.py)
# ---------------------------------------------------------------------------

def build_query_context(ds_str, params):
    """Build query_context for a chart so dashboard uses POST /chart/data."""
    ds_parts = ds_str.split("__")
    datasource = {"id": int(ds_parts[0]), "type": ds_parts[1]}
    queries = []
    if params.get("query_mode") == "raw" and params.get("all_columns"):
        q = {"columns": params["all_columns"]}
        if params.get("metric"):
            q["metrics"] = [params["metric"]]
        queries.append(q)
    elif params.get("metric"):
        metric = params["metric"]
        query = {"metrics": [metric]}
        if params.get("groupby"):
            query["columns"] = params["groupby"]
        elif params.get("all_columns"):
            query["columns"] = params["all_columns"]
        queries.append(query)
    elif params.get("metrics"):
        metric_list = params["metrics"]
        query = {"metrics": metric_list, "columns": params.get("groupby", [])}
        if params.get("granularity_sqla"):
            # Série temporelle : sans is_timeseries + extras, /chart/data agrège
            # toutes les dates en une seule ligne (pas d'axe de temps)
            query["granularity"] = params["granularity_sqla"]
            query["is_timeseries"] = True
            query["extras"] = {"time_grain_sqla": params.get("time_grain_sqla", "P1D")}
            query["time_range"] = params.get("time_range", "No filter")
        queries.append(query)
    if not queries:
        # Fallback for deck.gl charts with no metric: fetch all columns
        all_cols = params.get("all_columns", [])
        if all_cols:
            queries.append({"columns": all_cols})
    qc = {
        "datasource": datasource,
        "queries": queries,
        "force": False,
        "result_format": "json",
        "result_type": "full",
    }
    return json.dumps(qc)


def create_dataset(db_id, table_name, schema="monitor"):
    """Register a physical view as a Superset dataset. Deletes existing if found."""
    r = _api("GET", "/dataset/")
    if r:
        for ds in r.get("result", []):
            if ds.get("table_name") == table_name and ds.get("schema") == schema:
                _api("DELETE", f"/dataset/{ds['id']}")
                time.sleep(0.3)
    r2 = _api("POST", "/dataset/", json={
        "database": db_id,
        "schema": schema,
        "table_name": table_name,
    })
    if r2:
        time.sleep(1)
        return r2.get("id")
    return None


def find_dataset_id(table_name):
    r = _api("GET", "/dataset/")
    if r:
        for ds in r.get("result", []):
            if ds.get("table_name") == table_name:
                return ds["id"]
    return None


def _metric(col_name, col_type, aggregate="SUM"):
    return {
        "expressionType": "SIMPLE",
        "column": {"column_name": col_name, "type": col_type, "type_generic": 0},
        "aggregate": aggregate,
        "label": f"{aggregate}({col_name})",
    }


def _metric_sql(sql_expr, label):
    return {
        "expressionType": "SQL",
        "sqlExpression": sql_expr,
        "label": label,
    }


def _chart_fd(ds_id, viz, extra):
    fd = {"datasource": f"{ds_id}__table", "viz_type": viz, "time_range": "No filter"}
    fd.update(extra)
    return json.dumps(fd)


# ---------------------------------------------------------------------------
# Chart definitions
# ---------------------------------------------------------------------------

def _define_charts(DS):
    """Return list of (dash, name, viz_type, ds_key, params_raw, height, width).

    dash vaut "d1" (Moisson & Pipeline) ou "d2" (Données territoriales RM) —
    l'appartenance est portée par chaque chart, pas par sa position.
    params_raw is built lazily via a callable(ds_id) so that missing datasets
    are silently skipped without raising KeyError on DS[key] during evaluation.
    """
    charts = []
    dash_courant = ["d1"]

    def _add(name, viz, ds_key, params_fn, h, w):
        if ds_key not in DS:
            return
        charts.append((dash_courant[0], name, viz, ds_key, params_fn(DS[ds_key]), h, w))

    def _fd(ds_key, viz, extra):
        return lambda ds_id: _chart_fd(ds_id, viz, extra)

    # ═══════ Dashboard 1 : Moisson & Pipeline ═══════

    # Row 1 — KPIs (height 25)
    _add("JDD moissonnés", "big_number_total", "v_overview",
        _fd("v_overview", "big_number_total", {"metric": _metric("total_jdd", "BIGINT"), "subheader": "JDD toutes sources"}),
        25, 4)
    _add("Publiés sur RUDI", "big_number_total", "v_overview",
        _fd("v_overview", "big_number_total", {"metric": _metric("jdd_publies", "BIGINT"), "subheader": "sur N total"}),
        25, 4)
    _add("Taux de publication", "big_number_total", "v_overview",
        _fd("v_overview", "big_number_total", {"metric": _metric("pct_publies", "DOUBLE PRECISION"), "subheader": "% publiés"}),
        25, 4)
    _add("Lignes RM filtrées", "big_number_total", "v_overview",
        _fd("v_overview", "big_number_total", {"metric": _metric("filtered_rows", "BIGINT"), "subheader": "lignes importées"}),
        25, 4)
    _add("Backlog à examiner", "big_number_total", "v_overview",
        _fd("v_overview", "big_number_total", {"metric": _metric("decouverte_a_examiner", "INTEGER"), "subheader": "en attente"}),
        25, 4)
    _add("Durée dernier pipeline", "big_number_total", "v_overview",
        _fd("v_overview", "big_number_total", {"metric": _metric("pipeline_duree_sec", "DOUBLE PRECISION"), "subheader": "secondes"}),
        25, 4)

    # Row 2 — Pipeline execution
    _add("Durée par étape (dernier run)", "dist_bar", "v_pipeline_dernier_run",
        _fd("v_pipeline_dernier_run", "dist_bar", {"metrics": [_metric("duree_secondes", "DOUBLE PRECISION")], "groupby": ["etape"], "order_desc": True}),
        45, 12)
    _add("Durée des étapes dans le temps", "line", "v_pipeline_etapes",
        _fd("v_pipeline_etapes", "line", {"metrics": [_metric("duree_secondes", "DOUBLE PRECISION")], "groupby": ["etape"], "granularity_sqla": "date_run", "time_grain_sqla": "P1D"}),
        45, 12)
    _add("Échecs récents", "table", "v_pipeline_echecs",
        _fd("v_pipeline_echecs", "table", {"all_columns": ["date_run", "etape", "error_message"], "query_mode": "raw"}),
        45, 12)

    # Row 3 — Entonnoir & découverte
    _add("Entonnoir découverte → publication", "funnel", "v_entonnoir",
        _fd("v_entonnoir", "funnel", {"metric": _metric("n", "BIGINT"), "groupby": ["etape"]}),
        45, 6)
    _add("Évolution du backlog", "line", "v_pipeline_history",
        _fd("v_pipeline_history", "line", {"metrics": [_metric("candidats", "INTEGER"), _metric("a_examiner", "INTEGER"), _metric("exclus", "INTEGER")], "groupby": [], "granularity_sqla": "date_key", "time_grain_sqla": "P1D"}),
        45, 6)

    # Row 4 — Sources & publication
    _add("État par source", "table", "v_datasets_source",
        _fd("v_datasets_source", "table", {"all_columns": ["source", "n", "n_publies", "n_importes", "total_rm"], "query_mode": "raw"}),
        45, 6)
    _add("JDD non publiés sur RUDI", "table", "v_datasets_non_publies",
        _fd("v_datasets_non_publies", "table", {"all_columns": ["dossier", "titre", "source", "theme", "date_harvest"], "query_mode": "raw"}),
        45, 6)
    _add("Volumétrie dans le temps", "line", "v_pipeline_history",
        _fd("v_pipeline_history", "line", {"metrics": [_metric("taille_data_mo", "DOUBLE PRECISION"), _metric("total_rm", "BIGINT")], "groupby": [], "granularity_sqla": "date_key", "time_grain_sqla": "P1D"}),
        45, 12)

    # ═══════ Dashboard 2 : Données territoriales RM ═══════
    dash_courant[0] = "d2"

    # Row 1 — KPIs (height 25)
    _add("Lignes de données RM", "big_number_total", "v_rows_par_commune",
        _fd("v_rows_par_commune", "big_number_total", {"metric": _metric("n_lignes", "BIGINT"), "subheader": "total filtré"}),
        25, 4)
    _add("Communes couvertes", "big_number_total", "v_rows_par_commune",
        _fd("v_rows_par_commune", "big_number_total", {"metric": _metric_sql("COUNT(DISTINCT code_insee)", "Communes"), "subheader": "/ 43 communes"}),
        25, 4)
    _add("Thèmes couverts", "big_number_total", "v_rows_par_theme",
        _fd("v_rows_par_theme", "big_number_total", {"metric": _metric_sql("COUNT(DISTINCT theme)", "Thèmes"), "subheader": "/ 14 thèmes"}),
        25, 4)

    # Row 2 — Cartes deck.gl (height 70). Sans MAPBOX_API_KEY : fond noir,
    # données visibles quand même.
    _add("Volume de données par commune", "deck_polygon", "v_carte_communes",
        _fd("v_carte_communes", "deck_polygon", {
            "line_column": "contour", "line_type": "geojson",
            "metric": _metric("n_lignes", "BIGINT"),
            "groupby": ["contour"],
            "reverse_long_lat": False,
            "filled": True, "stroked": True, "extruded": False,
            "multiplier": 1, "line_width": 30, "line_width_unit": "meters",
            "fill_color_picker": {"r": 31, "g": 119, "b": 180, "a": 1},
            "stroke_color_picker": {"r": 255, "g": 255, "b": 255, "a": 1},
            "linear_color_scheme": "blue_white_yellow",
            "opacity": 75,
            # Volumes très asymétriques (Rennes 3,2 M vs ~240) : classes manuelles
            "break_points": ["0", "25000", "75000", "150000", "300000", "3500000"],
            "num_buckets": "5",
            "table_filter": False, "toggle_polygons": True,
            "legend_position": "tr", "legend_format": ".3s",
            "autozoom": True,
            "mapbox_style": "mapbox://styles/mapbox/light-v9",
            "viewport": {"latitude": 48.117, "longitude": -1.68, "zoom": 9.3,
                         "bearing": 0, "pitch": 0},
        }),
        70, 12)
    _add("Localisation des données géo", "deck_scatter", "v_carte_points_geo",
        _fd("v_carte_points_geo", "deck_scatter", {
            "spatial": {"type": "latlong", "latCol": "lat", "lonCol": "lon"},
            "all_columns": ["lon", "lat", "theme", "titre", "dossier"],
            "query_mode": "raw",
            "row_limit": 50000,
            "point_radius_fixed": {"type": "fix", "value": 60},
            "point_unit": "square_m", "min_radius": 1, "max_radius": 250,
            "multiplier": 1,
            "dimension": "theme",
            "color_picker": {"r": 31, "g": 119, "b": 180, "a": 1},
            "color_scheme": "supersetColors",
            "legend_position": "tr", "legend_format": None,
            "autozoom": True,
            "mapbox_style": "mapbox://styles/mapbox/light-v9",
            "viewport": {"latitude": 48.117, "longitude": -1.68, "zoom": 9.3,
                         "bearing": 0, "pitch": 0},
        }),
        70, 12)

    # Row 3 — Thèmes & couverture
    _add("Lignes par thème", "dist_bar", "v_rows_par_theme",
        _fd("v_rows_par_theme", "dist_bar", {"metrics": [_metric("n_lignes", "BIGINT")], "groupby": ["theme_label"], "order_desc": True}),
        45, 12)
    _add("Couverture commune × thème", "heatmap", "v_commune_theme",
        lambda ds_id: _chart_fd(ds_id, "heatmap", {"metric": _metric("n_lignes", "BIGINT"), "groupby": ["nom", "theme"], "show_legend": True, "show_values": False, "normalize_across": "heatmap", "all_columns_x": "nom", "all_columns_y": "theme"}),
        70, 12)
    _add("JDD distincts par commune", "dist_bar", "v_rows_par_commune",
        _fd("v_rows_par_commune", "dist_bar", {"metrics": [_metric("n_datasets", "BIGINT")], "groupby": ["nom"], "order_desc": True}),
        45, 12)

    # Row 4 — Contenus & qualité
    _add("Top JDD par volume", "table", "v_top_datasets",
        _fd("v_top_datasets", "table", {"all_columns": ["titre", "producteur", "theme", "source", "n_lignes"], "query_mode": "raw"}),
        45, 6)
    _add("Principaux producteurs", "dist_bar", "v_producteurs",
        _fd("v_producteurs", "dist_bar", {"metrics": [_metric("n_datasets", "BIGINT")], "groupby": ["producteur"], "order_desc": True}),
        45, 6)
    _add("Types de géométries", "pie", "v_geo_summary",
        _fd("v_geo_summary", "pie", {"metric": _metric("n", "BIGINT"), "groupby": ["type_geom"]}),
        45, 6)
    _add("Rattachement géographique", "pie", "v_qualite_geo",
        _fd("v_qualite_geo", "pie", {"metric": _metric("n", "BIGINT"), "groupby": ["rattachement"]}),
        45, 6)

    return charts


# ---------------------------------------------------------------------------
# Dashboard layouts
# ---------------------------------------------------------------------------

def _build_layout(chart_specs):
    """Build position_json from (chart_id, height, width) list grouped in rows.

    Row plan: KPIs (4-5 per row, h=25), charts (h=45 or h=70).
    """
    children = []
    positions = {}
    y_cursor = 0
    col = 0
    row_key = None
    row_width = 0

    for i, (cid, h, w) in enumerate(chart_specs):
        ckey = f"CHART-{i}"
        if col == 0 or row_width + w > 12:
            col = 0
            row_width = 0
            row_key = f"ROW-{i}"
            positions[row_key] = {
                "type": "ROW", "id": row_key, "children": [],
                "meta": {"width": 12, "height": h,
                         "position": {"x": 0, "y": y_cursor, "width": 12, "height": h}},
            }
            children.append(row_key)
            y_cursor += h
        positions[ckey] = {
            "type": "CHART", "id": ckey, "children": [],
            "meta": {"chartId": cid, "width": w, "height": h,
                     "position": {"x": col, "y": y_cursor - h, "width": w, "height": h}},
        }
        positions[row_key]["children"].append(ckey)
        col += w
        row_width += w

    layout = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"], "meta": {}},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": children, "meta": {}},
        **positions,
    }
    return layout


# ---------------------------------------------------------------------------
# Native filters for mb-territoire
# ---------------------------------------------------------------------------

# Vues portant chaque colonne filtrable : un filtre natif ne doit cibler que
# les charts dont le dataset possède la colonne, sinon leurs requêtes échouent.
_DS_AVEC_THEME = {"v_rows_par_theme", "v_carte_points_geo", "v_commune_theme",
                  "v_top_datasets", "v_geo_summary"}
_DS_AVEC_NOM = {"v_rows_par_commune", "v_carte_communes", "v_commune_theme"}


def _filtre_natif(fid, label, colonne, dataset_id, charts_in_scope, excluded):
    return {
        "id": f"NATIVE_FILTER-{fid}",
        "name": label,
        "filterType": "filter_select",
        "targets": [{"datasetId": dataset_id, "column": {"name": colonne}}],
        "defaultDataMask": {"extraFormData": {}, "filterState": {}, "ownState": {}},
        "cascadeParentIds": [],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": excluded},
        "controlValues": {"enableEmptyFilter": False, "defaultToFirstItem": False,
                          "multiSelect": True, "searchAllOptions": False,
                          "inverseSelection": False},
        "type": "NATIVE_FILTER",
        "description": "",
        "chartsInScope": charts_in_scope,
        "tabsInScope": [],
    }


def _build_native_filters(datasets, charts_d2):
    """charts_d2 : liste de dicts {id, ds_key} des charts du dashboard territoire."""
    filtres = []
    for fid, label, colonne, ds_ref, ds_ok in (
        ("theme", "Thème", "theme", "v_rows_par_theme", _DS_AVEC_THEME),
        ("commune", "Commune", "nom", "v_rows_par_commune", _DS_AVEC_NOM),
    ):
        dataset_id = datasets.get(ds_ref)
        if not dataset_id:
            continue
        in_scope = [c["id"] for c in charts_d2 if c["ds_key"] in ds_ok]
        excluded = [c["id"] for c in charts_d2 if c["ds_key"] not in ds_ok]
        filtres.append(_filtre_natif(fid, label, colonne, dataset_id, in_scope, excluded))
    return filtres


# ---------------------------------------------------------------------------
# Main provisioning
# ---------------------------------------------------------------------------

def main():
    _login()

    # Step 1: Database
    print("Step 1: Database connection")
    db_id = get_or_create_db()
    if not db_id:
        print("FATAL: cannot create DB connection")
        return
    print(f"  DB id={db_id}")

    # Step 2: SQL views
    print("\nStep 2: PostgreSQL views")
    ensure_pg_views()

    # Step 3: Datasets
    print("\nStep 3: Datasets")
    views_to_register = [
        ("v_overview", "monitor"),
        ("v_pipeline_history", "monitor"),
        ("v_pipeline_etapes", "monitor"),
        ("v_pipeline_dernier_run", "monitor"),
        ("v_pipeline_echecs", "monitor"),
        ("v_datasets_source", "monitor"),
        ("v_datasets_non_publies", "monitor"),
        ("v_entonnoir", "monitor"),
        ("v_rows_par_commune", "monitor"),
        ("v_carte_communes", "monitor"),
        ("v_rows_par_theme", "monitor"),
        ("v_commune_theme", "monitor"),
        ("v_top_datasets", "monitor"),
        ("v_producteurs", "monitor"),
        ("v_qualite_geo", "monitor"),
        ("v_geo_summary", "monitor"),
        ("v_carte_points_geo", "monitor"),
    ]
    datasets = {}
    for table_name, schema in views_to_register:
        ds_id = create_dataset(db_id, table_name, schema)
        if ds_id:
            datasets[table_name] = ds_id
            print(f"  {table_name}: id={ds_id}")
        else:
            found = find_dataset_id(table_name)
            if found:
                datasets[table_name] = found
                print(f"  {table_name}: id={found} (existing)")
            else:
                print(f"  {table_name}: FAILED")

    if not datasets:
        print("FATAL: no datasets created")
        return

    # Set main_dttm_col on time-series datasets
    print("\nStep 3b: Set main_dttm_col")
    for tbl in ["v_overview", "v_pipeline_history", "v_pipeline_etapes"]:
        ds_id = datasets.get(tbl)
        if ds_id:
            col = "date_key" if tbl != "v_pipeline_etapes" else "date_run"
            _api("PUT", f"/dataset/{ds_id}", json={"main_dttm_col": col})
            print(f"  {tbl}: main_dttm_col={col}")
    time.sleep(1)

    # Step 4: Clean old dashboard by slug
    print("\nStep 4: Clean old artifacts")
    r = _api("GET", "/dashboard/")
    if r:
        for dash in r.get("result", []):
            slug = dash.get("slug", "")
            title = dash.get("dashboard_title", "")
            if slug in ("moissonneuse-monitoring", "mb-pipeline", "mb-territoire") or \
               "Moissonneuse" in title or "Pipeline" in title or "Territoire" in title:
                # Delete associated charts
                r2 = _api("GET", f"/dashboard/{dash['id']}/charts")
                if r2:
                    for ch in r2.get("result", []):
                        _api("DELETE", f"/chart/{ch['id']}")
                _api("DELETE", f"/dashboard/{dash['id']}")
                print(f"  Deleted old dashboard: {title} (id={dash['id']})")
                time.sleep(0.3)

    # Step 5: Charts
    print("\nStep 5: Charts")
    chart_defs = _define_charts(DS=datasets)
    charts = []  # dicts {id, name, ds_key, h, w, dash}
    for dash, name, viz, ds_key, params_raw, h, w in chart_defs:
        ds_id = datasets.get(ds_key)
        if not ds_id:
            print(f"  SKIP {name}: dataset {ds_key} not found")
            continue
        params = json.loads(params_raw)
        ds_str = f"{ds_id}__table"
        qc = build_query_context(ds_str, params)
        r = _api("POST", "/chart/", json={
            "datasource_id": ds_id, "datasource_type": "table",
            "slice_name": name, "viz_type": viz,
            "params": params_raw,
            "query_context": qc,
        })
        if r:
            charts.append({"id": r["id"], "name": name, "ds_key": ds_key,
                           "h": h, "w": w, "dash": dash})
            print(f"  Chart {r['id']}: {name} ({viz}, h={h}, w={w})")
        else:
            print(f"  FAILED: {name}")

    if not charts:
        print("FATAL: no charts created")
        return

    # Step 6: Dashboards
    print("\nStep 6: Dashboards")
    charts_d2 = [c for c in charts if c["dash"] == "d2"]
    dashboards = [
        ("Moisson & Pipeline", "mb-pipeline",
         [c for c in charts if c["dash"] == "d1"], []),
        ("Données territoriales RM", "mb-territoire",
         charts_d2, _build_native_filters(datasets, charts_d2)),
    ]
    for title, slug, dash_charts, native_filters in dashboards:
        specs = [(c["id"], c["h"], c["w"]) for c in dash_charts]
        layout = _build_layout(specs)
        r = _api("POST", "/dashboard/", json={
            "dashboard_title": title, "slug": slug, "published": True,
        })
        if not r:
            print(f"  FAILED: dashboard {title}")
            continue
        dash_id = r["id"]
        for c in dash_charts:
            _api("PUT", f"/chart/{c['id']}", json={"dashboards": [dash_id]})
        _api("PUT", f"/dashboard/{dash_id}", json={
            "position_json": json.dumps(layout),
            "json_metadata": json.dumps({
                "timed_refresh_immune_slices": [], "expanded_slices": {},
                "refresh_frequency": 0, "default_filters": "{}",
                "native_filter_configuration": native_filters,
                "chart_configuration": {},
                "color_scheme": "", "label_colors": {}, "cross_filters_enabled": True,
            }),
        })
        print(f"  Dashboard '{title}': id={dash_id}, {len(dash_charts)} charts, "
              f"{len(native_filters)} filtres natifs")
        print(f"  URL: http://127.0.0.1:8088/superset/dashboard/{slug}/")

    # Step 7: Verify — interroge les DONNÉES de chaque chart, pas sa seule existence
    print("\nStep 7: Verify chart data")
    ok_count = 0
    ko_count = 0
    for c in charts:
        r = _api("GET", f"/chart/{c['id']}/data/")
        res = (r or {}).get("result", [{}])[0]
        err = res.get("error")
        if r and not err:
            ok_count += 1
            print(f"  OK  {c['name']} (rows={res.get('rowcount')})")
        else:
            ko_count += 1
            print(f"  KO  {c['name']} (id={c['id']}): {str(err)[:120]}")
    print(f"  {ok_count} OK, {ko_count} KO")
    print(f"\n✓ Done. Login: admin / admin")


if __name__ == "__main__":
    main()

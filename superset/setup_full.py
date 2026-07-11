"""
Build Moissonneuse-batteuse dashboard in Superset from scratch.

Stores query_context on every chart so the dashboard uses v1/chart/data
(which works for all viz types) instead of the legacy explore_json endpoint
(which only handles server-side viz types like 'line' / 'dist_bar').

Runs inside mb-superset container:
    sg docker -c "docker cp superset/setup_full.py mb-superset:/tmp/ && docker exec -i mb-superset python3 /tmp/setup_full.py"
"""
import json
import os
import time

import psycopg2
import requests

API = "http://localhost:8088/api/v1"

s = requests.Session()


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


def get_or_create_db():
    """Find or create the PostGIS database connection in Superset."""
    r = _api("GET", "/database/")
    if r:
        for db in r.get("result", []):
            if "moissonneuse" in db.get("database_name", "").lower():
                return db["id"]
    r2 = _api("POST", "/database/", json={
        "database_name": "PostGIS Moissonneuse",
        "sqlalchemy_uri": DSN,
        "expose_in_sqllab": True,
    })
    if r2:
        return r2["id"]
    return None


def ensure_pg_views():
    """Create monitoring views in PostgreSQL."""
    pg = psycopg2.connect(host="postgis", port=5432, dbname="moissonneuse",
                          user="monitor",
                          password=os.environ.get("POSTGRES_PASSWORD", "moissonneuse"))
    pg.autocommit = True
    cur = pg.cursor()
    views = {
        "v_overview": """CREATE OR REPLACE VIEW monitor.v_overview AS
SELECT date_key, tabulaire_total, tabulaire_rudi,
       tabulaire_rm, insee_total, insee_rudi, insee_rm,
       filtered_rows, n_dossiers, pipeline_duree_sec,
       pipeline_succes, decouverte_candidats, decouverte_a_examiner
FROM monitor.metrics_history
ORDER BY date_key DESC LIMIT 1""",
        "v_datasets_source": """CREATE OR REPLACE VIEW monitor.v_datasets_source AS
SELECT source, COUNT(*) as n,
       SUM(nb_rm) as total_rm,
       COUNT(*) FILTER (WHERE rudi_publie) as n_publies
FROM monitor.datasets GROUP BY source ORDER BY n DESC""",
        "v_datasets_theme": """CREATE OR REPLACE VIEW monitor.v_datasets_theme AS
SELECT COALESCE(theme,'inconnu') as theme, COUNT(*) as n
FROM monitor.datasets GROUP BY theme ORDER BY n DESC""",
        "v_rows_par_commune": """CREATE OR REPLACE VIEW monitor.v_rows_par_commune AS
SELECT r.nom_commune, r.code_insee, COUNT(*) as n_lignes
FROM filtered.data_rows r
WHERE r.code_insee IN (SELECT code_insee FROM ref.communes_rm)
GROUP BY r.nom_commune, r.code_insee
ORDER BY n_lignes DESC""",
        "v_pipeline_history": """CREATE OR REPLACE VIEW monitor.v_pipeline_history AS
SELECT date_key, pipeline_duree_sec,
       pipeline_succes::int as pipeline_succes, tabulaire_rudi, insee_rudi
FROM monitor.metrics_history
ORDER BY date_key DESC LIMIT 30""",
        "v_geo_summary": """CREATE OR REPLACE VIEW monitor.v_geo_summary AS
SELECT dossier, COUNT(*) as n
FROM filtered.geo_features GROUP BY dossier
ORDER BY n DESC LIMIT 20""",
    }
    for name, sql in views.items():
        cur.execute(sql)
        print(f"  View {name} created")
    cur.close()
    pg.close()


def build_query_context(ds_str, params):
    """Build a query_context JSON string for a chart's params.

    Storing query_context forces the dashboard frontend to fetch data
    via POST /api/v1/chart/data (which handles all viz types) instead of
    falling back to the legacy explore_json endpoint (which only works for
    server-side viz types like 'line' / 'dist_bar').
    """
    ds_parts = ds_str.split("__")
    datasource = {"id": int(ds_parts[0]), "type": ds_parts[1]}

    # Extract queries from params — different viz types store metric info differently
    queries = []
    if params.get("metric"):
        # single-metric charts: big_number_total, pie
        metric = params["metric"]
        query = {"metrics": [metric]}
        if params.get("groupby"):
            query["columns"] = params["groupby"]
        elif params.get("all_columns"):
            query["columns"] = params["all_columns"]
        queries.append(query)
    elif params.get("metrics"):
        # multi-metric charts: line, dist_bar
        metric_list = params["metrics"]
        query = {"metrics": metric_list, "columns": params.get("groupby", [])}
        if params.get("granularity_sqla"):
            query["granularity"] = params["granularity_sqla"]
            query["time_range"] = params.get("time_range", "No filter")
        queries.append(query)
    elif params.get("query_mode") == "raw" and params.get("all_columns"):
        queries.append({"columns": params["all_columns"]})

    qc = {
        "datasource": datasource,
        "queries": queries,
        "force": False,
        "result_format": "json",
        "result_type": "full",
    }
    return json.dumps(qc)


def create_dataset(db_id, table_name, schema="monitor"):
    """Register a physical view as a Superset dataset."""
    r = _api("GET", "/dataset/")
    if r:
        for ds in r.get("result", []):
            if ds.get("table_name") == table_name:
                _api("DELETE", f"/dataset/{ds['id']}")
                time.sleep(0.3)
    r2 = _api("POST", "/dataset/", json={
        "database": db_id,
        "schema": schema,
        "table_name": table_name,
    })
    if r2:
        time.sleep(1)  # let column detection happen
        return r2.get("id")
    return None


def find_dataset_id(table_name):
    r = _api("GET", "/dataset/")
    if r:
        for ds in r.get("result", []):
            if ds.get("table_name") == table_name:
                return ds["id"]
    return None


def main():
    _login()
    print("Step 1: Database connection")
    db_id = get_or_create_db()
    if not db_id:
        print("FATAL: cannot create DB connection")
        return
    print(f"  DB id={db_id}")

    print("\nStep 2: PostgreSQL views")
    ensure_pg_views()

    print("\nStep 3: Datasets")
    table_names = ["v_overview", "v_datasets_source", "v_datasets_theme",
                   "v_rows_par_commune", "v_pipeline_history", "v_geo_summary"]
    datasets = {}
    for name in table_names:
        ds_id = create_dataset(db_id, name)
        if ds_id:
            datasets[name] = ds_id
            print(f"  {name}: id={ds_id}")
        else:
            # Try to find existing
            found = find_dataset_id(name)
            if found:
                datasets[name] = found
                print(f"  {name}: id={found} (existing)")
            else:
                print(f"  {name}: FAILED")

    if not datasets:
        print("FATAL: no datasets created")
        return

    # Set main_dttm_col on time-series datasets so time-based queries work
    print("\nStep 3b: Set main_dttm_col")
    for tbl in ["v_overview", "v_pipeline_history"]:
        ds_id = datasets.get(tbl)
        if ds_id:
            _api("PUT", f"/dataset/{ds_id}",
                 json={"main_dttm_col": "date_key"})
            print(f"  {tbl}: main_dttm_col=date_key")
    time.sleep(1)

    # ---- Clean old charts & dashboards ----
    print("\nStep 4: Clean old artifacts")
    for table in ["dashboard", "chart"]:
        r = _api("GET", f"/{table}/")
        if r:
            for item in r.get("result", []):
                _api("DELETE", f"/{table}/{item['id']}")

    # ---- Charts ----
    print("\nStep 5: Charts")
    DS = datasets
    def chart_fd(ds_str, viz, extra):
        fd = {"datasource": ds_str, "viz_type": viz, "time_range": "No filter"}
        fd.update(extra)
        return json.dumps(fd)
    def metric_obj(col_name, col_type, aggregate="MAX"):
        return {"expressionType": "SIMPLE",
                "column": {"column_name": col_name, "type": col_type, "type_generic": 0},
                "aggregate": aggregate,
                "label": f"{aggregate}({col_name})"}
    chart_defs = [
        ("Aperçu global (total jdd)", "big_number_total", DS["v_overview"],
         chart_fd(f'{DS["v_overview"]}__table', "big_number_total",
                  {"metric": metric_obj("tabulaire_total", "INTEGER"), "subheader": "JDD tabulaires"})),
        ("Aperçu global (lignes filtrées)", "big_number_total", DS["v_overview"],
         chart_fd(f'{DS["v_overview"]}__table', "big_number_total",
                  {"metric": metric_obj("filtered_rows", "BIGINT"), "subheader": "Lignes filtrées RM"})),
        ("Aperçu global (publiés RUDI)", "big_number_total", DS["v_overview"],
         chart_fd(f'{DS["v_overview"]}__table', "big_number_total",
                  {"metric": metric_obj("tabulaire_rudi", "INTEGER"), "subheader": "Publiés sur RUDI"})),
        ("Durée du dernier pipeline", "big_number_total", DS["v_overview"],
         chart_fd(f'{DS["v_overview"]}__table', "big_number_total",
                  {"metric": metric_obj("pipeline_duree_sec", "DOUBLE PRECISION"), "subheader": "Secondes"})),
        ("Sources des données", "dist_bar", DS["v_datasets_source"],
         chart_fd(f'{DS["v_datasets_source"]}__table', "dist_bar",
                  {"metrics": [{"expressionType": "SIMPLE", "column": {"column_name": "n", "type": "BIGINT", "type_generic": 0}, "aggregate": "SUM", "label": "SUM(n)"}],
                   "groupby": ["source"]})),
        ("Détail par source", "table", DS["v_datasets_source"],
         chart_fd(f'{DS["v_datasets_source"]}__table', "table",
                  {"all_columns": ["source", "n", "total_rm", "n_publies"],
                   "query_mode": "raw"})),
        ("Répartition par thème", "pie", DS["v_datasets_theme"],
         chart_fd(f'{DS["v_datasets_theme"]}__table', "pie",
                  {"metric": {"expressionType": "SIMPLE", "column": {"column_name": "n", "type": "BIGINT", "type_generic": 0}, "aggregate": "SUM", "label": "SUM(n)"},
                   "groupby": ["theme"]})),
        ("Lignes par commune", "dist_bar", DS["v_rows_par_commune"],
         chart_fd(f'{DS["v_rows_par_commune"]}__table', "dist_bar",
                  {"metrics": [{"expressionType": "SIMPLE", "column": {"column_name": "n_lignes", "type": "BIGINT", "type_generic": 0}, "aggregate": "SUM", "label": "SUM(n_lignes)"}],
                   "groupby": ["nom_commune"]})),
        ("Évolution du pipeline", "line", DS["v_pipeline_history"],
         chart_fd(f'{DS["v_pipeline_history"]}__table', "line",
                  {"metrics": [{"expressionType": "SIMPLE", "column": {"column_name": "tabulaire_rudi", "type": "INTEGER", "type_generic": 0}, "aggregate": "MAX", "label": "MAX(tabulaire_rudi)"}],
                   "groupby": ["date_key"], "granularity_sqla": "date_key", "time_grain_sqla": "P1D"})),
        ("Entités géo par dossier", "dist_bar", DS["v_geo_summary"],
         chart_fd(f'{DS["v_geo_summary"]}__table', "dist_bar",
                  {"metrics": [{"expressionType": "SIMPLE", "column": {"column_name": "n", "type": "BIGINT", "type_generic": 0}, "aggregate": "SUM", "label": "SUM(n)"}],
                   "groupby": ["dossier"]})),
    ]

    chart_ids = []
    chart_heights = []
    chart_widths = []
    for name, viz, ds_id, params_raw in chart_defs:
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
            cid = r["id"]
            chart_ids.append(cid)
            h = {"big_number_total": 25, "table": 70}.get(viz, 45)
            chart_heights.append(h)
            # Lignes par commune has 43 bars — needs full width to be readable
            w = 12 if "Lignes par commune" in name else 4
            chart_widths.append(w)
            print(f"  Chart {cid}: {name} (height={h}, width={w})")

    # ---- Dashboard ----
    print("\nStep 6: Dashboard")
    r = _api("POST", "/dashboard/", json={
        "dashboard_title": "Moissonneuse-batteuse — Monitoring",
        "slug": "moissonneuse-monitoring",
        "published": True,
    })
    if not r:
        print("FATAL: cannot create dashboard")
        return
    dash_id = r["id"]
    print(f"  Dashboard id={dash_id}")

    # Link charts to dashboard
    for cid in chart_ids:
        _api("PUT", f"/chart/{cid}", json={
            "dashboards": [dash_id],
        })

    # Build layout with ROW wrappers so charts sit side by side.
    # GRID_BASE_UNIT = 8 px, CHART_MARGIN = 32 px in Superset 4.1.1,
    # so chartHeight = meta.height * 8 - 32.  With height < 5 the
    # chart has zero visible area — see ChartHolder.tsx.
    children = []
    positions = {}
    row_key = None
    y_cursor = 0
    col = 0
    row_width = 0
    for i, (cid, h, w) in enumerate(zip(chart_ids, chart_heights, chart_widths)):
        ckey = f"CHART-{i}"
        # Start a new row if this chart won't fit or the row is empty
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
    meta = json.dumps({
        "timed_refresh_immune_slices": [], "expanded_slices": {},
        "refresh_frequency": 0, "default_filters": "{}",
        "native_filter_configuration": [], "chart_configuration": {},
        "color_scheme": "", "label_colors": {}, "cross_filters_enabled": True,
    })
    _api("PUT", f"/dashboard/{dash_id}", json={
        "dashboard_title": "Moissonneuse-batteuse — Monitoring",
        "slug": "moissonneuse-monitoring",
        "published": True,
        "position_json": json.dumps(layout),
        "json_metadata": meta,
    })
    print(f"  Layout: {len(chart_ids)} charts")

    # -- Verify --
    print("\nStep 7: Verify")
    # Check the charts are linked
    r = _api("GET", f"/dashboard/{dash_id}/charts")
    if r:
        print(f"  Charts linked: {len(r.get('result', []))}")

    # Check each chart's datasource
    for cid in chart_ids:
        r2 = _api("GET", f"/chart/{cid}")
        if r2:
            ds_in_params = json.loads(r2["result"]["params"]).get("datasource", "?")
            print(f"  Chart {cid}: datasource={ds_in_params}")

    print(f"\n✓ Dashboard ready: http://127.0.0.1:8088/superset/dashboard/{dash_id}/")
    print(f"  Login: admin / admin")


if __name__ == "__main__":
    main()

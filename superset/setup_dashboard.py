"""
Build Moissonneuse-batteuse dashboard in Superset.

Usage from the host:
    sg docker -c "docker cp superset/setup_dashboard.py mb-superset:/tmp/ && docker exec -i mb-superset python3 /tmp/setup_dashboard.py"

Prerequisites:
    - Container mb-superset running
    - Database "PostGIS Moissonneuse" already configured in Superset (id=1)
    - psycopg2-binary installed in container
"""
import json
import os
import time

import requests

API = "http://localhost:8088/api/v1"
DB_NAME = "PostGIS Moissonneuse"
DASHBOARD_SLUG = "moissonneuse-monitoring"
DASHBOARD_TITLE = "Moissonneuse-batteuse — Monitoring"

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


def _get_db_id():
    r = _api("GET", "/database/")
    if r:
        for db in r.get("result", []):
            if DB_NAME in db.get("database_name", ""):
                return db["id"]
    return None


def _list_all(table):
    """Fetch all items using pagination."""
    items = []
    page = 0
    while True:
        r = _api("GET", f"/{table}/?q=(columns:!(id,table_name,slice_name,dashboard_title,slug),page:{page},page_size:100)")
        if not r:
            break
        batch = r.get("result", [])
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def _delete_ds(name):
    for ds in _list_all("dataset"):
        if ds.get("table_name") == name:
            _api("DELETE", f"/dataset/{ds['id']}")
            time.sleep(0.2)


def _delete_chart(name):
    for ch in _list_all("chart"):
        if ch.get("slice_name") == name:
            _api("DELETE", f"/chart/{ch['id']}")
            time.sleep(0.2)


def _upsert_dataset(db_id, table_name, sql):
    _delete_ds(table_name)
    r = _api("POST", "/dataset/", json={
        "database": db_id, "table_name": table_name, "sql": sql,
        "normalize_columns": False,
    })
    return r["id"] if r else None


def _find_or_create_dashboard():
    for d in _list_all("dashboard"):
        if d.get("slug") == DASHBOARD_SLUG:
            print(f"  Dashboard existant: id={d['id']}")
            return d["id"]
    r = _api("POST", "/dashboard/", json={
        "dashboard_title": DASHBOARD_TITLE,
        "slug": DASHBOARD_SLUG,
        "published": True,
    })
    if r:
        print(f"  Dashboard créé: id={r['id']}")
        return r["id"]
    return None


def _create_chart(datasource_id, slice_name, viz_type, form_data, dashboard_id):
    _delete_chart(slice_name)
    fd = dict(form_data)
    r = _api("POST", "/chart/", json={
        "datasource_id": datasource_id,
        "datasource_type": "table",
        "slice_name": slice_name,
        "viz_type": viz_type,
        "params": json.dumps(fd),
        "dashboards": [dashboard_id] if dashboard_id else [],
    })
    return r["id"] if r else None


def _update_layout(dash_id, chart_ids):
    """Set dashboard position JSON with all charts in a 3-column grid."""
    children = []
    positions = {}
    for i, ch_id in enumerate(chart_ids):
        cid = f"CHART-{i}"
        children.append(cid)
        col = i % 3
        row = i // 3
        positions[cid] = {
            "type": "CHART", "id": cid, "children": [],
            "meta": {
                "chartId": ch_id,
                "width": 4, "height": 4,
                "position": {"x": col * 4, "y": row * 4, "width": 4, "height": 4},
            },
        }
    layout = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": children},
        **positions,
    }
    r = _api("PUT", f"/dashboard/{dash_id}", json={
        "dashboard_title": DASHBOARD_TITLE,
        "slug": DASHBOARD_SLUG,
        "published": True,
        "position_json": json.dumps(layout),
    })
    return r is not None


def main():
    _login()
    db_id = _get_db_id()
    if not db_id:
        print("ERREUR: Base de données PostGIS non trouvée")
        return
    print(f"Base de données: id={db_id}")

    # --- Datasets ---
    sqls = {
        "overview": """SELECT date_key, tabulaire_total, tabulaire_rudi,
       tabulaire_rm, insee_total, insee_rudi, insee_rm,
       filtered_rows, n_dossiers, pipeline_duree_sec,
       pipeline_succes, decouverte_candidats, decouverte_a_examiner
FROM monitor.metrics_history
ORDER BY date_key DESC LIMIT 1""",
        "datasets_source": """SELECT source, COUNT(*) as n,
       SUM(nb_rm) as total_rm,
       COUNT(*) FILTER (WHERE rudi_publie) as n_publies
FROM monitor.datasets GROUP BY source ORDER BY n DESC""",
        "datasets_theme": """SELECT COALESCE(theme,'inconnu') as theme,
       COUNT(*) as n FROM monitor.datasets
GROUP BY theme ORDER BY n DESC""",
        "rows_par_commune": """SELECT r.nom_commune, r.code_insee,
       COUNT(*) as n_lignes
FROM filtered.data_rows r
WHERE r.code_insee IN (SELECT code_insee FROM ref.communes_rm)
GROUP BY r.nom_commune, r.code_insee
ORDER BY n_lignes DESC""",
        "pipeline_history": """SELECT date_key, pipeline_duree_sec,
       pipeline_succes::int as pipeline_succes,
       tabulaire_rudi, insee_rudi
FROM monitor.metrics_history
ORDER BY date_key DESC LIMIT 30""",
        "geo_summary": """SELECT dossier, COUNT(*) as n
FROM filtered.geo_features GROUP BY dossier
ORDER BY n DESC LIMIT 20""",
    }
    datasets = {}
    for name, sql in sqls.items():
        ds_id = _upsert_dataset(db_id, name, sql)
        if ds_id:
            datasets[name] = ds_id
            print(f"  Dataset {name}: {ds_id}")
        else:
            print(f"  ERREUR création dataset {name}")

    dash_id = _find_or_create_dashboard()
    if not dash_id:
        print("ERREUR: impossible de créer/trouver le dashboard")
        return

    # --- Charts ---
    chart_ids = []
    chart_defs = []

    ds_k = datasets.get
    if ds_k("overview"):
        chart_defs.append((ds_k("overview"), "Aperçu global (total jdd)", "big_number_total",
            {"metric": "tabulaire_total", "subheader": "JDD tabulaires",
             "time_range": "No filter"}))
        chart_defs.append((ds_k("overview"), "Aperçu global (lignes filtrées)", "big_number_total",
            {"metric": "filtered_rows", "subheader": "Lignes filtrées RM",
             "time_range": "No filter"}))
        chart_defs.append((ds_k("overview"), "Aperçu global (publiés RUDI)", "big_number_total",
            {"metric": "tabulaire_rudi", "subheader": "Publiés sur RUDI",
             "time_range": "No filter"}))
        chart_defs.append((ds_k("overview"), "Durée du dernier pipeline", "big_number_total",
            {"metric": "pipeline_duree_sec", "subheader": "Secondes",
             "time_range": "No filter"}))

    if ds_k("datasets_source"):
        chart_defs.append((ds_k("datasets_source"), "Sources des données", "dist_bar",
            {"metrics": ["n"], "groupby": ["source"], "time_range": "No filter"}))
        chart_defs.append((ds_k("datasets_source"), "Détail par source", "table",
            {"all_columns": ["source", "n", "total_rm", "n_publies"],
             "query_mode": "raw", "time_range": "No filter"}))

    if ds_k("datasets_theme"):
        chart_defs.append((ds_k("datasets_theme"), "Répartition par thème", "pie",
            {"groupby": ["theme"], "metric": "n", "time_range": "No filter"}))

    if ds_k("rows_par_commune"):
        chart_defs.append((ds_k("rows_par_commune"), "Lignes par commune", "dist_bar",
            {"metrics": ["n_lignes"], "groupby": ["nom_commune"],
             "time_range": "No filter"}))

    if ds_k("pipeline_history"):
        chart_defs.append((ds_k("pipeline_history"), "Évolution du pipeline", "line",
            {"metrics": ["pipeline_duree_sec"], "groupby": ["date_key"],
             "time_range": "No filter"}))

    if ds_k("geo_summary"):
        chart_defs.append((ds_k("geo_summary"), "Entités géo par dossier", "dist_bar",
            {"metrics": ["n"], "groupby": ["dossier"], "time_range": "No filter"}))

    for ds_id, name, viz, form in chart_defs:
        cid = _create_chart(ds_id, name, viz, form, dash_id)
        if cid:
            chart_ids.append(cid)
            print(f"  Chart {name}: {cid}")

    # --- Layout ---
    if chart_ids:
        ok = _update_layout(dash_id, chart_ids)
        print(f"  Mise à jour du layout: {'OK' if ok else 'ECHEC'}")

    print(f"\n✓ Dashboard prêt: http://127.0.0.1:8088/superset/dashboard/{dash_id}/")
    print(f"  Login: admin / admin")


if __name__ == "__main__":
    main()

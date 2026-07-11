"""
Post-setup: set main_dttm_col on time-based datasets
"""
import requests, json
import os

s = requests.Session()
r = s.post("http://localhost:8088/api/v1/security/login",
           json={"username": os.environ.get("SUPERSET_ADMIN_USER", "admin"),
                 "password": os.environ.get("SUPERSET_ADMIN_PASSWORD", "admin"),
                 "provider": "db"})
s.headers["Authorization"] = "Bearer " + r.json()["access_token"]
s.headers["X-CSRFToken"] = s.get("http://localhost:8088/api/v1/security/csrf_token/").json()["result"]
s.headers["Referer"] = "http://localhost:8088/"

# Find dataset IDs
r = s.get("http://localhost:8088/api/v1/dataset/")
ds_by_name = {}
for ds in r.json().get("result", []):
    ds_by_name[ds["table_name"]] = ds["id"]

# Set main_dttm_col
for tn in ("v_overview", "v_pipeline_history"):
    ds_id = ds_by_name.get(tn)
    if ds_id:
        r = s.put(f"http://localhost:8088/api/v1/dataset/{ds_id}", json={"main_dttm_col": "date_key"})
        print(f"{tn} (id={ds_id}): main_dttm_col set -> {r.status_code}")

# Test v1/chart/data for chart 1
r = s.get("http://localhost:8088/api/v1/chart/1")
params = json.loads(r.json()["result"]["params"])
ds_str = params["datasource"]
ds_id = int(ds_str.split("__")[0])
metric = params.get("metric")
payload = {
    "datasource": {"id": ds_id, "type": "table"},
    "queries": [{"metrics": [metric], "row_limit": 1}],
    "force": False,
    "result_format": "json",
    "result_type": "full",
}
r = s.post("http://localhost:8088/api/v1/chart/data", json=payload)
print(f"v1/chart/data chart 1: {r.status_code}")
print(f"  result: {r.json().get('result', [{}])[0].get('data', [])[:1]}")

# Test explore_json for chart 9 (line) with granularity_sqla in form_data
r = s.get("http://localhost:8088/api/v1/chart/9")
params9 = json.loads(r.json()["result"]["params"])
print(f"Chart 9 params: granularity_sqla={params9.get('granularity_sqla')}")

# Test explore_json directly
fd = json.dumps({"slice_id": 9})
r = s.post(f"http://localhost:8088/superset/explore_json/?form_data={fd}&dashboard_id=1", json={})
print(f"explore_json chart 9: {r.status_code}")
if r.status_code >= 400:
    print(f"  Error: {r.text[:200]}")
else:
    print(f"  OK: {r.text[:100]}")

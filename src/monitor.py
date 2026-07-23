"""
Monitoring et import dans PostGIS pour le dashboard Superset.

Usage :
    python3 src/monitor.py --init-db          # Crée les schémas et tables
    python3 src/monitor.py --refresh          # Met à jour les métriques depuis les state_*.json
    python3 src/monitor.py --import-data      # Importe les données filtrées dans filtered
    python3 src/monitor.py --import-ref       # Importe les données de référence (communes, iris, sirens)
    python3 src/monitor.py --geocode          # Géocode les adresses via API RVA
    python3 src/monitor.py --log-pipeline [durée] [succès] [étape]
    python3 src/monitor.py --drop-filtered    # Supprime le schéma filtered
    python3 src/monitor.py --status           # Affiche les stats
    python3 src/monitor.py --full             # Init + refresh + import-ref

Options :
    --dossier X   : restreint l'import à un dossier
    --limit N     : limite le nombre de lignes importées (test)
"""

import argparse
import csv
import datetime
import io
import json
import os
import re
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

import discover
from conf.communes_rm import (
    COMMUNES_RM, CODES_POSTAUX_RENNES, CODES_INSEE_RM,
    CIRCONSCRIPTIONS_PAR_COMMUNE, BBOX_RM, INSEE_VERS_NOM,
)
from conf.datasets import DATASETS, DATASETS_GEO, DATASETS_INSEE, DATASETS_OEB, DATASETS_BDNB
from connectors.analyseurs import _detecter_champs, _detecter_delimiteur, _format_analysable
from connectors.http import session
from connectors.sirene import obtenir_sirens_rm
from filters.geographic import normaliser, est_iris_rm, est_dans_rm, est_point_rm

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CONF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conf")

_COLS_GEO_KEY = frozenset({
    "code_insee", "code_iris", "code_postal", "nom_commune", "siren",
})
_TYPES_COLS = {
    "code_insee": "CHAR(5)", "code_iris": "CHAR(9)",
    "code_postal": "TEXT", "nom_commune": "TEXT", "siren": "CHAR(9)",
}
# RVA geocoding columns to extract for address rows
_COLS_ADRESSE_RVA = {"insee", "idlane", "idaddress", "number", "extension",
                      "addr1", "addr2", "addr3", "x", "y", "zipcode"}

_CACHE_GEOJSON_CENTROIDES = None


# ---------------------------------------------------------------------------
# Config DB
# ---------------------------------------------------------------------------

def _charger_conf_db() -> dict:
    chemin = os.path.join(CONF_DIR, "monitor_db.json")
    if not os.path.isfile(chemin):
        print("[monitor] Fichier de config introuvable : src/conf/monitor_db.json")
        print("[monitor] Copier src/conf/monitor_db.example.json → monitor_db.json et éditer.")
        sys.exit(1)
    with open(chemin, encoding="utf-8") as f:
        conf = json.load(f)
    for champ in ("host", "port", "db", "user", "password"):
        if champ not in conf:
            print(f"[monitor] Champ manquant dans monitor_db.json : {champ}")
            sys.exit(1)
    return conf


def _connecter(conf: dict):
    if psycopg2 is None:
        print("[monitor] psycopg2 non installé. Pour installer : pip install psycopg2-binary")
        sys.exit(1)
    return psycopg2.connect(
        host=conf["host"], port=conf["port"], dbname=conf["db"],
        user=conf["user"], password=conf["password"],
    )


def _nom_schema(conf: dict, cle: str) -> str:
    """Nom de schéma depuis la conf, validé comme identifiant PostgreSQL simple —
    les noms sont interpolés dans le DDL/DML en f-string, on refuse donc tout
    caractère qui permettrait une injection depuis un monitor_db.json altéré."""
    nom = conf.get(cle, cle)
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", nom):
        raise ValueError(f"Nom de schéma invalide dans monitor_db.json : {nom!r}")
    return nom


# ---------------------------------------------------------------------------
# SQL DDL (idempotent)
# ---------------------------------------------------------------------------

SQL_INIT_DB = """
CREATE SCHEMA IF NOT EXISTS {schema_monitor};
CREATE SCHEMA IF NOT EXISTS {schema_ref};
CREATE SCHEMA IF NOT EXISTS {schema_decouverte};
CREATE SCHEMA IF NOT EXISTS {schema_filtered};

CREATE TABLE IF NOT EXISTS {schema_monitor}.metrics_history (
    date_key DATE PRIMARY KEY,
    datasets_tabulaire INT DEFAULT 0, datasets_geo INT DEFAULT 0,
    datasets_insee INT DEFAULT 0, datasets_oeb INT DEFAULT 0, datasets_bdnb INT DEFAULT 0,
    tabulaire_total INT DEFAULT 0, tabulaire_rudi INT DEFAULT 0, tabulaire_rm BIGINT DEFAULT 0,
    insee_total INT DEFAULT 0, insee_rudi INT DEFAULT 0, insee_rm BIGINT DEFAULT 0,
    oeb_total INT DEFAULT 0, oeb_rudi INT DEFAULT 0, oeb_rm BIGINT DEFAULT 0,
    bdnb_total INT DEFAULT 0, bdnb_rudi INT DEFAULT 0, bdnb_rm BIGINT DEFAULT 0,
    decouverte_candidats INT DEFAULT 0, decouverte_vus INT DEFAULT 0,
    decouverte_exclus INT DEFAULT 0, decouverte_echecs INT DEFAULT 0,
    decouverte_a_examiner INT DEFAULT 0, decouverte_sans_ressource INT DEFAULT 0,
    n_dossiers INT DEFAULT 0, taille_data_bytes BIGINT DEFAULT 0, taille_cache_bytes BIGINT DEFAULT 0,
    pipeline_duree_sec DOUBLE PRECISION, pipeline_succes BOOLEAN,
    filtered_rows BIGINT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {schema_monitor}.datasets (
    id TEXT PRIMARY KEY,
    dossier TEXT NOT NULL,
    source TEXT NOT NULL,
    theme TEXT,
    titre TEXT,
    producteur TEXT,
    nb_rm INT DEFAULT 0,
    date_harvest DATE,
    rudi_publie BOOLEAN DEFAULT FALSE,
    rudi_publie_source BOOLEAN,
    last_modified TIMESTAMPTZ,
    data_imported BOOLEAN DEFAULT FALSE,
    data_imported_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {schema_monitor}.pipeline_runs (
    id SERIAL PRIMARY KEY,
    date_run DATE NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    etape TEXT NOT NULL,
    duree_secondes DOUBLE PRECISION,
    succes BOOLEAN,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {schema_ref}.communes_rm (
    code_insee CHAR(5) PRIMARY KEY,
    nom TEXT NOT NULL,
    code_postal TEXT,
    centroid GEOMETRY(Point, 2154),
    geom GEOMETRY(MultiPolygon, 2154)
);

CREATE TABLE IF NOT EXISTS {schema_ref}.iris_rm (
    code_iris CHAR(9) PRIMARY KEY,
    nom TEXT,
    code_commune CHAR(5) REFERENCES {schema_ref}.communes_rm(code_insee),
    centroid GEOMETRY(Point, 2154)
);

CREATE TABLE IF NOT EXISTS {schema_ref}.sirens_rm (
    siren CHAR(9) PRIMARY KEY,
    raison_sociale TEXT,
    code_insee CHAR(5) REFERENCES {schema_ref}.communes_rm(code_insee),
    adresse TEXT,
    geometry GEOMETRY(Point, 2154)
);

CREATE TABLE IF NOT EXISTS {schema_decouverte}.a_examiner (
    dataset_id TEXT PRIMARY KEY,
    titre TEXT,
    organisation TEXT,
    url TEXT,
    type TEXT,
    raison TEXT,
    nb_rm INT,
    service_url TEXT,
    couches JSONB,
    sans_ressource BOOLEAN DEFAULT FALSE,
    date_decouverte DATE,
    imported_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {schema_decouverte}.candidats (
    dataset_id TEXT PRIMARY KEY,
    titre TEXT,
    nb_rm INT,
    champs TEXT,
    theme TEXT,
    date_decouverte DATE,
    imported_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {schema_decouverte}.historique (
    id SERIAL PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    titre_au_moment TEXT,
    date_decision DATE,
    imported_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {schema_filtered}.data_rows (
    id BIGSERIAL,
    dossier TEXT NOT NULL,
    dataset_id TEXT,
    source TEXT,
    theme TEXT,
    code_insee CHAR(5),
    code_iris CHAR(9),
    code_postal TEXT,
    nom_commune TEXT,
    siren CHAR(9),
    properties JSONB,
    imported_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {schema_filtered}.geo_features (
    id BIGSERIAL,
    dossier TEXT NOT NULL,
    couche TEXT,
    properties JSONB,
    geometry GEOMETRY(Geometry, 2154) NOT NULL,
    imported_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_geo_features_geom
    ON {schema_filtered}.geo_features USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_data_rows_dossier
    ON {schema_filtered}.data_rows (dossier, code_insee);
"""


def _init_db(conf: dict, cur) -> None:
    fmt = {
        "schema_monitor": _nom_schema(conf, "schema_monitor"),
        "schema_ref": _nom_schema(conf, "schema_ref"),
        "schema_decouverte": _nom_schema(conf, "schema_decouverte"),
        "schema_filtered": _nom_schema(conf, "schema_filtered"),
    }
    cur.execute(SQL_INIT_DB.format(**fmt))
    # Migration : ajouter rudi_publie_source si la table existe déjà sans cette colonne
    sch_mon = fmt["schema_monitor"]
    cur.execute(f"""
        ALTER TABLE {sch_mon}.datasets
        ADD COLUMN IF NOT EXISTS rudi_publie_source BOOLEAN
    """)
    print("[monitor] Schémas et tables créés/vérifiés.")


# ---------------------------------------------------------------------------
# Import données de référence
# ---------------------------------------------------------------------------

def _charger_centroides_communes() -> dict[str, tuple[float, float]]:
    """Charge les centroïdes des communes RM depuis le dossier moissonné."""
    global _CACHE_GEOJSON_CENTROIDES
    if _CACHE_GEOJSON_CENTROIDES is not None:
        return _CACHE_GEOJSON_CENTROIDES
    centroides = {}
    dossier = os.path.join(DATA_DIR, "centroides-communes-rm")
    if not os.path.isdir(dossier):
        return centroides
    for fname in os.listdir(dossier):
        if fname.endswith(".geojson"):
            chemin = os.path.join(dossier, fname)
            try:
                with open(chemin, encoding="utf-8") as f:
                    data = json.load(f)
                for feat in data.get("features", []):
                    props = feat.get("properties", {})
                    code = str(props.get("INSEE_COM") or props.get("insee", "")).strip()
                    if not code or len(code) < 5:
                        continue
                    geom = feat.get("geometry", {})
                    if geom.get("type") == "Point":
                        coords = geom.get("coordinates", [])
                        if len(coords) >= 2:
                            centroides[code] = (float(coords[0]), float(coords[1]))
            except (OSError, json.JSONDecodeError):
                continue
    _CACHE_GEOJSON_CENTROIDES = centroides
    return centroides


def _importer_contours_communes(conf: dict, cur) -> None:
    """Télécharge les contours des 43 communes RM et les importe dans ref.communes_rm.geom."""
    sch = _nom_schema(conf, "schema_ref")
    contours_path = os.path.join(DATA_DIR, "contours_communes_rm.geojson")

    # Télécharger si absent ou ancien (> 30 jours)
    need_download = True
    if os.path.isfile(contours_path):
        age = time.time() - os.path.getmtime(contours_path)
        if age < 30 * 86400:
            need_download = False

    if need_download:
        url = "https://geo.api.gouv.fr/communes?codeEpci=243500139&format=geojson&geometry=contour"
        print(f"[monitor] Téléchargement des contours communes RM…")
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            with open(contours_path, "w", encoding="utf-8") as f:
                f.write(r.text)
            print(f"  Sauvegardé : {contours_path}")
        except Exception as e:
            print(f"  (Erreur téléchargement contours : {e})")
            return

    # Importer les contours dans la base
    try:
        with open(contours_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  (Erreur lecture contours : {e})")
        return

    features = data.get("features", [])
    if not features:
        print("  Aucun contour trouvé dans le fichier.")
        return

    n = 0
    for feat in features:
        props = feat.get("properties", {})
        code_insee = str(props.get("code") or props.get("INSEE_COM") or "").strip()
        if not code_insee or len(code_insee) != 5:
            continue
        geom = feat.get("geometry")
        if not geom:
            continue
        wkt = _geojson_geom_to_wkt(geom)
        if not wkt:
            continue
        cur.execute(f"""
            UPDATE {sch}.communes_rm
            SET geom = ST_Transform(ST_GeomFromText(%s), 2154)
            WHERE code_insee = %s
        """, (f"SRID=4326;{wkt}", code_insee))
        n += 1
    print(f"  {n} contours de communes importés (4326 → 2154).")


def _import_ref(conf: dict, cur) -> None:
    sch = _nom_schema(conf, "schema_ref")
    print("[monitor] Import des communes RM…")

    # S'assurer que la colonne geom existe (pour les tables préexistantes)
    cur.execute(f"""
        ALTER TABLE {sch}.communes_rm
        ADD COLUMN IF NOT EXISTS geom GEOMETRY(MultiPolygon, 2154)
    """)

    centroides = _charger_centroides_communes()

    insee_vers_nom = INSEE_VERS_NOM
    insee_vers_cp = {
        code: COMMUNES_RM.get(nom.lower(), "")
        for code, nom in insee_vers_nom.items()
    }
    # Correction pour Rennes qui a 3 CP
    insee_vers_cp["35238"] = "35000"

    n = 0
    for code_insee, nom in sorted(insee_vers_nom.items()):
        cp = insee_vers_cp.get(code_insee, "")
        pt = centroides.get(code_insee)
        geom_wkt = None
        if pt:
            lon, lat = pt
            # ST_Transform from 4326 to 2154
            geom_wkt = f"SRID=4326;POINT({lon} {lat})"
        if geom_wkt:
            cur.execute(f"""
                INSERT INTO {sch}.communes_rm (code_insee, nom, code_postal, centroid)
                VALUES (%s, %s, %s, ST_Transform(ST_GeomFromText(%s), 2154))
                ON CONFLICT (code_insee) DO UPDATE SET
                    nom = EXCLUDED.nom, code_postal = EXCLUDED.code_postal,
                    centroid = EXCLUDED.centroid
            """, (code_insee, nom, cp, geom_wkt))
        else:
            cur.execute(f"""
                INSERT INTO {sch}.communes_rm (code_insee, nom, code_postal)
                VALUES (%s, %s, %s)
                ON CONFLICT (code_insee) DO UPDATE SET
                    nom = EXCLUDED.nom, code_postal = EXCLUDED.code_postal
            """, (code_insee, nom, cp))
        n += 1
    print(f"  {n} communes importées.")

    # Iris RM : extraire depuis les données INSEE moissonnées
    print("[monitor] Import des IRIS RM (depuis données INSEE)…")
    n_iris = 0
    for dossier in ("insee_bic_iris", "insee_bic_iris_diplomes"):
        dpath = os.path.join(DATA_DIR, dossier)
        if not os.path.isdir(dpath):
            continue
        for fname in os.listdir(dpath):
            if not (fname.endswith(".csv") and "rennesmetropole" in fname):
                continue
            chemin = os.path.join(dpath, fname)
            try:
                with open(chemin, encoding="utf-8-sig") as f:
                    sample = f.read(8192)
                    delim = _detecter_delimiteur(sample)
                    f.seek(0)
                    reader = csv.DictReader(f, delimiter=delim)
                    iris_set = set()
                    for row in reader:
                        code = str(row.get("IRIS", row.get("iris", ""))).strip()
                        if len(code) >= 5:
                            iris_set.add(code)
                    for code in sorted(iris_set):
                        code_commune = code[:5]
                        if code_commune in CODES_INSEE_RM:
                            cur.execute(f"""
                                INSERT INTO {sch}.iris_rm (code_iris, code_commune)
                                VALUES (%s, %s) ON CONFLICT DO NOTHING
                            """, (code, code_commune))
                            n_iris += 1
            except Exception as e:
                print(f"  (Erreur lecture IRIS depuis {fname} : {e})")
    print(f"  {n_iris} IRIS importés.")

    # SIREN RM
    print("[monitor] Import des SIREN RM…")
    sirens = obtenir_sirens_rm()
    n_siren = 0
    for siren in sorted(sirens):
        cur.execute(f"""
            INSERT INTO {sch}.sirens_rm (siren) VALUES (%s) ON CONFLICT DO NOTHING
        """, (siren,))
        n_siren += 1
    print(f"  {n_siren} SIREN importés.")

    # Contours des communes (fichier top-level, survit à la purge)
    _importer_contours_communes(conf, cur)


# ---------------------------------------------------------------------------
# Refresh : lecture des state_*.json -> monitor.datasets + metrics_history
# ---------------------------------------------------------------------------

def _lire_state(chemin: str) -> dict:
    if not os.path.isfile(chemin):
        return {}
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def _upsert_datasets(cur, sch: str, entries: list[tuple]) -> None:
    """entries: [(id, dossier, source, theme, titre, producteur, nb_rm,
                  date_harvest, rudi_publie, rudi_publie_source, last_modified), ...]"""
    for e in entries:
        cur.execute(f"""
            INSERT INTO {sch}.datasets
                (id, dossier, source, theme, titre, producteur, nb_rm, date_harvest,
                 rudi_publie, rudi_publie_source, last_modified, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                dossier = EXCLUDED.dossier, theme = EXCLUDED.theme,
                titre = EXCLUDED.titre, producteur = EXCLUDED.producteur,
                nb_rm = EXCLUDED.nb_rm,
                date_harvest = EXCLUDED.date_harvest,
                rudi_publie = EXCLUDED.rudi_publie,
                rudi_publie_source = EXCLUDED.rudi_publie_source,
                last_modified = EXCLUDED.last_modified,
                updated_at = NOW()
        """, e)


def _lire_meta_depuis_rudi(dossier_nom: str) -> dict:
    """Lit thème, titre et producteur depuis le rudi_metadata.json d'un dossier moissonné."""
    chemin = os.path.join(DATA_DIR, dossier_nom, "rudi_metadata.json")
    if os.path.isfile(chemin):
        try:
            with open(chemin, encoding="utf-8") as f:
                meta = json.load(f)
            producteur = None
            org = meta.get("organization") or meta.get("producer") or {}
            if isinstance(org, dict):
                producteur = org.get("organization_name") or org.get("name")
            elif isinstance(org, str):
                producteur = org
            return {
                "theme": meta.get("theme"),
                "titre": meta.get("resource_title") or meta.get("title"),
                "producteur": producteur,
            }
        except (OSError, ValueError):
            pass
    return {"theme": None, "titre": None, "producteur": None}


def _refresh(conf: dict, cur) -> None:
    sch_mon = _nom_schema(conf, "schema_monitor")
    print("[monitor] Rafraîchissement des métriques…")

    # Lire tous les state files
    state_batch = _lire_state(os.path.join(DATA_DIR, "state.json"))
    state_insee = _lire_state(os.path.join(DATA_DIR, "state_insee.json"))
    state_oeb = _lire_state(os.path.join(DATA_DIR, "state_oeb.json"))
    state_bdnb = _lire_state(os.path.join(DATA_DIR, "state_bdnb.json"))
    state_geo = _lire_state(os.path.join(DATA_DIR, "state_geo.json"))
    geo_rudi_publie = state_geo.get("_rudi_publie", {})

    # Compter les datasets configurés
    n_tab = len(DATASETS)  # empty today
    n_geo = len(DATASETS_GEO)
    n_insee = len(DATASETS_INSEE)
    n_oeb = len(DATASETS_OEB)
    n_bdnb = len(DATASETS_BDNB)

    now = datetime.date.today()

    def _extraire_rudi(rp_val):
        """Extrait (rudi_publie: bool, rudi_publie_source: bool|None) depuis l'état."""
        if isinstance(rp_val, dict):
            return any(rp_val.values()), rp_val.get("source")
        if isinstance(rp_val, bool):
            return rp_val, None
        return False, None

    # Upsert datasets (tabular/batch)
    entries = []
    for ds_id, ds in state_batch.items():
        nb_rm = ds.get("nb_rm", 0) or 0
        lmod = ds.get("last_modified")
        if lmod and isinstance(lmod, str):
            try:
                lmod = lmod.replace("+00:00", "").replace("Z", "")
                if "." in lmod:
                    lmod = datetime.datetime.fromisoformat(lmod)
                else:
                    lmod = datetime.datetime.strptime(lmod, "%Y-%m-%dT%H:%M:%S")
            except (ValueError, TypeError):
                lmod = None
        meta = _lire_meta_depuis_rudi(ds.get("dossier", ds_id))
        rp, rp_src = _extraire_rudi(ds.get("rudi_publie"))
        entries.append((
            ds_id, ds.get("dossier", ds_id), "tabular",
            meta["theme"], meta["titre"], meta["producteur"], nb_rm,
            ds.get("date_harvest", str(now)), rp, rp_src, lmod,
        ))

    # INSEE
    for ds_id, ds in state_insee.items():
        meta = _lire_meta_depuis_rudi(ds.get("dossier", ds_id))
        rp, rp_src = _extraire_rudi(ds.get("rudi_publie"))
        entries.append((
            ds_id, ds.get("dossier", ds_id), "insee",
            meta["theme"], meta["titre"], meta["producteur"],
            ds.get("nb_rm", 0),
            ds.get("date_harvest", str(now)), rp, rp_src, None,
        ))

    # OEB
    for ds_id, ds in state_oeb.items():
        meta = _lire_meta_depuis_rudi(ds.get("dossier", ds_id))
        rp, rp_src = _extraire_rudi(ds.get("rudi_publie"))
        entries.append((
            ds_id, ds.get("dossier", ds_id), "oeb",
            meta["theme"], meta["titre"], meta["producteur"],
            ds.get("nb_rm", 0),
            ds.get("date_harvest", str(now)), rp, rp_src, None,
        ))

    # BDNB
    for ds_id, ds in state_bdnb.items():
        meta = _lire_meta_depuis_rudi(ds.get("dossier", ds_id))
        rp, rp_src = _extraire_rudi(ds.get("rudi_publie"))
        entries.append((
            ds_id, ds.get("dossier", ds_id), "bdnb",
            meta["theme"], meta["titre"] or "BDNB", meta["producteur"],
            ds.get("nb_rm", 0),
            ds.get("date_harvest", str(now)), rp, rp_src, None,
        ))

    # Geo services (from DATASETS_GEO) — rudi_publie depuis state_geo.json
    for g in DATASETS_GEO:
        gid = g.get("id", "")
        if gid:
            dossier = g.get("dossier", gid)
            rp_geo = geo_rudi_publie.get(dossier, False)
            rp_geo_dict = rp_geo if isinstance(rp_geo, dict) else ({"docker": rp_geo} if isinstance(rp_geo, bool) else {})
            rp_any = any(rp_geo_dict.values()) if rp_geo_dict else False
            rp_src = rp_geo_dict.get("source")
            entries.append((
                gid, dossier, "geo_" + g.get("type", "wfs"),
                g.get("theme"), g.get("titre"), g.get("producteur"), 0,
                str(now), rp_any, rp_src, None,
            ))

    _upsert_datasets(cur, sch_mon, entries)

    # Découverte
    decouverte = {}
    chemin_dec = os.path.join(DATA_DIR, "decouverte.json")
    if os.path.isfile(chemin_dec):
        with open(chemin_dec, encoding="utf-8") as f:
            decouverte = json.load(f)

    # Import a_examiner
    sch_dec = _nom_schema(conf, "schema_decouverte")
    for entry in decouverte.get("a_examiner", []):
        ds_id = entry.get("dataset_id", "")
        if not ds_id:
            continue
        cur.execute(f"""
            INSERT INTO {sch_dec}.a_examiner
                (dataset_id, titre, organisation, url, type, raison, nb_rm, service_url,
                 couches, sans_ressource, date_decouverte)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (dataset_id) DO UPDATE SET
                titre = EXCLUDED.titre, organisation = EXCLUDED.organisation,
                url = EXCLUDED.url, type = EXCLUDED.type, raison = EXCLUDED.raison,
                nb_rm = EXCLUDED.nb_rm, service_url = EXCLUDED.service_url,
                couches = EXCLUDED.couches, sans_ressource = EXCLUDED.sans_ressource
        """, (
            ds_id, entry.get("titre"), entry.get("organisation"), entry.get("url"),
            entry.get("type"), entry.get("raison"), entry.get("nb_rm"),
            entry.get("service_url"),
            json.dumps(entry.get("couches", [])) if entry.get("couches") else None,
            entry.get("sans_ressource", False), str(now),
        ))

    # Import candidats
    for entry in decouverte.get("candidats", []):
        ds_id = entry.get("dataset_id", "")
        if not ds_id:
            continue
        cur.execute(f"""
            INSERT INTO {sch_dec}.candidats
                (dataset_id, titre, nb_rm, champs, theme, date_decouverte)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (dataset_id) DO UPDATE SET
                titre = EXCLUDED.titre, nb_rm = EXCLUDED.nb_rm,
                champs = EXCLUDED.champs, theme = EXCLUDED.theme
        """, (
            ds_id, entry.get("titre"), entry.get("nb_rm"),
            str(entry.get("champs", {})), entry.get("theme"), str(now),
        ))

    # Import historique
    for entry in decouverte.get("historique", []):
        ds_id = entry.get("dataset_id", "")
        if not ds_id:
            continue
        cur.execute(f"""
            INSERT INTO {sch_dec}.historique
                (dataset_id, decision, titre_au_moment, date_decision)
            VALUES (%s, %s, %s, %s)
        """, (
            ds_id, entry.get("decision"), entry.get("titre"),
            str(entry.get("date_decision", now)),
        ))

    # Taille disque
    n_dossiers = 0
    taille_data = 0
    for nom in os.listdir(DATA_DIR):
        chemin = os.path.join(DATA_DIR, nom)
        if nom == "cache":
            continue
        if os.path.isdir(chemin):
            n_dossiers += 1
            for racine, _dirs, fichiers in os.walk(chemin):
                for f in fichiers:
                    try:
                        taille_data += os.path.getsize(os.path.join(racine, f))
                    except OSError:
                        pass

    taille_cache = 0
    cache_dir = os.path.join(DATA_DIR, "cache")
    if os.path.isdir(cache_dir):
        for racine, _dirs, fichiers in os.walk(cache_dir):
            for f in fichiers:
                try:
                    taille_cache += os.path.getsize(os.path.join(racine, f))
                except OSError:
                    pass

    # Computed metrics
    def _somme_rm(state: dict) -> int:
        return sum(d.get("nb_rm", 0) or 0 for d in state.values())

    def _nb_rudi(state: dict) -> int:
        n = 0
        for d in state.values():
            rp = d.get("rudi_publie")
            if isinstance(rp, dict):
                if any(rp.values()):
                    n += 1
            elif rp:
                n += 1
        return n

    dec = decouverte or {}
    metrics = {
        "date_key": now,
        "datasets_tabulaire": n_tab, "datasets_geo": n_geo,
        "datasets_insee": n_insee, "datasets_oeb": n_oeb, "datasets_bdnb": n_bdnb,
        "tabulaire_total": len(state_batch), "tabulaire_rudi": _nb_rudi(state_batch),
        "tabulaire_rm": _somme_rm(state_batch),
        "insee_total": len(state_insee), "insee_rudi": _nb_rudi(state_insee),
        "insee_rm": _somme_rm(state_insee),
        "oeb_total": len(state_oeb), "oeb_rudi": _nb_rudi(state_oeb),
        "oeb_rm": _somme_rm(state_oeb),
        "bdnb_total": len(state_bdnb), "bdnb_rudi": _nb_rudi(state_bdnb),
        "bdnb_rm": _somme_rm(state_bdnb),
        "decouverte_candidats": len(dec.get("candidats", [])),
        "decouverte_vus": len(dec.get("vus", [])),
        "decouverte_exclus": len(dec.get("exclus", [])),
        "decouverte_echecs": len(dec.get("echecs", [])),
        "decouverte_a_examiner": len(dec.get("a_examiner", [])),
        "decouverte_sans_ressource": len(dec.get("sans_ressource", [])),
        "n_dossiers": n_dossiers,
        "taille_data_bytes": taille_data,
        "taille_cache_bytes": taille_cache,
    }

    cols = ", ".join(metrics.keys())
    placeholders = ", ".join("%s" for _ in metrics)
    updates = ", ".join(f"{k} = EXCLUDED.{k}" for k in metrics if k != "date_key")
    cur.execute(f"""
        INSERT INTO {sch_mon}.metrics_history ({cols})
        VALUES ({placeholders})
        ON CONFLICT (date_key) DO UPDATE SET {updates}
    """, list(metrics.values()))

    print(f"[monitor] Métriques mises à jour ({now.isoformat()}).")
    print(f"  Tabulaire : {len(state_batch)} JDD, {_nb_rudi(state_batch)} publiés, "
          f"{_somme_rm(state_batch)} lignes RM")
    print(f"  INSEE : {len(state_insee)} publications")
    print(f"  Découverte : {len(dec.get('a_examiner',[]))} à examiner, "
          f"{len(dec.get('candidats',[]))} candidats")
    print(f"  Disque : {n_dossiers} dossiers, {taille_data // 1024 // 1024} Mo "
          f"(cache {taille_cache // 1024 // 1024} Mo)")


# ---------------------------------------------------------------------------
# Import des données filtrées dans filtered.data_rows
# ---------------------------------------------------------------------------

def _lire_csv_entetes(chemin: str) -> list[str]:
    """Lit uniquement l'en-tête d'un CSV."""
    with open(chemin, encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096)
    delim = _detecter_delimiteur(sample)
    reader = csv.DictReader(io.StringIO(sample + "\n"), delimiter=delim)
    return list(reader.fieldnames or [])


def _extraire_colonnes_geo(entetes: list[str], row: dict) -> dict:
    """Extrait les colonnes géo clés d'une ligne CSV, en utilisant les mêmes
    fonctions de détection que discover.py."""
    (champ_cp, champ_ville, champ_iris, _champ_dep, _champ_epci, _champ_adresse, champ_siren,
     _champ_lat, _champ_lon, _champ_circo) = _detecter_champs(entetes)

    geo = {"code_insee": None, "code_iris": None, "code_postal": None,
           "nom_commune": None, "siren": None}

    if champ_iris:
        code = str(row.get(champ_iris, "")).strip()
        if len(code) >= 5:
            if len(code) == 9 and code[:5] in CODES_INSEE_RM:
                geo["code_iris"] = code
                geo["code_insee"] = code[:5]
            elif len(code) == 5 and code in CODES_INSEE_RM:
                geo["code_insee"] = code

    if champ_cp:
        cp = str(row.get(champ_cp, "")).strip()
        if cp.startswith("35") and len(cp) == 5:
            geo["code_postal"] = cp

    if champ_ville:
        ville = str(row.get(champ_ville, "")).strip()
        geo["nom_commune"] = ville
        if not geo["code_insee"]:
            geo["code_insee"] = _NOM_NORMALISE_VERS_INSEE.get(normaliser(ville), "")
    if champ_siren:
        s = str(row.get(champ_siren, "")).strip().replace(" ", "")
        if s.isdigit() and len(s) in (9, 14):
            geo["siren"] = s[:9]

    return geo


# Nom normalisé (via filters.geographic.normaliser) → code INSEE, pour retrouver
# le code d'une commune RM depuis un libellé libre. Dérivé du référentiel partagé.
_NOM_NORMALISE_VERS_INSEE = {normaliser(nom): code for code, nom in INSEE_VERS_NOM.items()}


def _importer_csv(chemin: str, dossier: str, dataset_id: str, source: str, theme: str,
                  cur, sch: str, limite: int = 0) -> int:
    """Importe un CSV filtré dans filtered.data_rows. Retourne le nombre de lignes insérées."""
    with open(chemin, encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(8192)
    delim = _detecter_delimiteur(sample)
    f = io.StringIO(sample + "\n")
    reader = csv.DictReader(f, delimiter=delim)
    entetes = list(reader.fieldnames or [])

    if not entetes:
        return 0

    n = 0
    batch = []
    f = open(chemin, encoding="utf-8-sig", errors="replace")
    reader = csv.DictReader(f, delimiter=delim)
    for row in reader:
        geo = _extraire_colonnes_geo(entetes, row)
        properties = json.dumps(row, ensure_ascii=False)
        batch.append((
            dossier, dataset_id, source, theme,
            geo.get("code_insee"), geo.get("code_iris"),
            geo.get("code_postal"), geo.get("nom_commune"), geo.get("siren"),
            properties,
        ))
        n += 1
        if len(batch) >= 500:
            psycopg2.extras.execute_values(cur, f"""
                INSERT INTO {sch}.data_rows
                    (dossier, dataset_id, source, theme,
                     code_insee, code_iris, code_postal, nom_commune, siren, properties)
                VALUES %s
            """, batch, template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)")
            batch = []
        if limite and n >= limite:
            break
    if batch:
        psycopg2.extras.execute_values(cur, f"""
            INSERT INTO {sch}.data_rows
                (dossier, dataset_id, source, theme,
                 code_insee, code_iris, code_postal, nom_commune, siren, properties)
            VALUES %s
        """, batch, template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)")
    f.close()
    return n


def _deviner_srid_geojson(geom: dict) -> int:
    """Tente de détecter le SRID d'une géométrie GeoJSON.
    
    - Si la première coordonnée absolue < 180 → EPSG:4326 (lon/lat)
    - Sinon → EPSG:2154 (Lambert-93, métrique)
    """
    coords = geom.get("coordinates")
    if not coords:
        return 4326
    try:
        while isinstance(coords[0], (list, tuple)):
            coords = coords[0]
        x = float(coords[0])
        return 4326 if abs(x) < 180 else 2154
    except (TypeError, ValueError, IndexError):
        return 4326


def _importer_geojson(chemin: str, dossier: str, cur, sch: str) -> int:
    """Importe un fichier GeoJSON filtré dans filtered.geo_features.

    Retourne le nombre de features importées. Les géométries sont reprojetées
    en EPSG:2154 (Lambert-93) si elles sont en EPSG:4326.
    """
    try:
        with open(chemin, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"    (Erreur lecture GeoJSON {chemin} : {e})")
        return 0

    features = data.get("features", [])
    if not features:
        return 0

    couche = os.path.splitext(os.path.basename(chemin))[0]
    batch = []
    for feat in features:
        props = feat.get("properties", {})
        geom = feat.get("geometry")
        if not geom or not geom.get("coordinates"):
            continue
        wkt = _geojson_geom_to_wkt(geom)
        if not wkt:
            continue
        properties = json.dumps(props, ensure_ascii=False)
        srid = _deviner_srid_geojson(geom)
        batch.append((dossier, couche, properties, f"SRID={srid};{wkt}"))
        if len(batch) >= 500:
            psycopg2.extras.execute_values(cur, f"""
                INSERT INTO {sch}.geo_features (dossier, couche, properties, geometry)
                VALUES %s
            """, batch, template=f"(%s, %s, %s::jsonb, ST_Transform(ST_GeomFromText(%s), 2154))")
            batch = []
    if batch:
        psycopg2.extras.execute_values(cur, f"""
            INSERT INTO {sch}.geo_features (dossier, couche, properties, geometry)
            VALUES %s
        """, batch, template=f"(%s, %s, %s::jsonb, ST_Transform(ST_GeomFromText(%s), 2154))")
    return len(features)


def _geojson_geom_to_wkt(geom: dict) -> str | None:
    """Convertit une géométrie GeoJSON en WKT pour PostGIS.
    Gère Point, LineString, Polygon, Multi*."""
    gtype = geom.get("type", "")
    coords = geom.get("coordinates")
    if not coords or not gtype:
        return None

    try:
        if gtype == "Point":
            return f"POINT({coords[0]} {coords[1]})"
        elif gtype == "MultiPoint":
            pts = ", ".join(f"{c[0]} {c[1]}" for c in coords)
            return f"MULTIPOINT({pts})"
        elif gtype == "LineString":
            pts = ", ".join(f"{c[0]} {c[1]}" for c in coords)
            return f"LINESTRING({pts})"
        elif gtype == "MultiLineString":
            lines = ", ".join(
                "(" + ", ".join(f"{c[0]} {c[1]}" for c in line) + ")"
                for line in coords
            )
            return f"MULTILINESTRING({lines})"
        elif gtype == "Polygon":
            rings = ", ".join(
                "(" + ", ".join(f"{c[0]} {c[1]}" for c in ring) + ")"
                for ring in coords
            )
            return f"POLYGON({rings})"
        elif gtype == "MultiPolygon":
            polys = ", ".join(
                "(" + ", ".join(
                    "(" + ", ".join(f"{c[0]} {c[1]}" for c in ring) + ")"
                    for ring in polygon
                ) + ")"
                for polygon in coords
            )
            return f"MULTIPOLYGON({polys})"
    except (IndexError, TypeError, ValueError):
        return None
    return None


def _importer_geometries_ref(conf: dict, cur) -> None:
    """Mise à jour des géométries des communes depuis les données moissonnées
    centroides-communes-rm (déjà chargées dans _import_ref)."""
    # Déjà fait dans _import_ref

    # Import centroïdes depuis filtered.geo_features dans ref.communes_rm
    sch_ref = _nom_schema(conf, "schema_ref")
    sch_filt = _nom_schema(conf, "schema_filtered")
    cur.execute(f"""
        UPDATE {sch_ref}.communes_rm c
        SET centroid = g.geometry
        FROM {sch_filt}.geo_features g
        WHERE g.dossier = 'centroides-communes-rm'
          AND g.properties->>'INSEE_COM' = c.code_insee
          AND c.centroid IS NULL
    """)


def _import_data(conf: dict, cur, dossier_filtre: str = "", limite: int = 0) -> None:
    sch_mon = _nom_schema(conf, "schema_monitor")
    sch_filt = _nom_schema(conf, "schema_filtered")
    print("[monitor] Import des données filtrées…")

    # Parcourir les dossiers data/<dossier>/
    dossiers = sorted(
        n for n in os.listdir(DATA_DIR)
        if os.path.isdir(os.path.join(DATA_DIR, n))
        and n != "cache"
    )

    total_rows = 0
    total_geo = 0
    total_dossiers = 0

    for dossier in dossiers:
        if dossier_filtre and dossier != dossier_filtre:
            continue
        dpath = os.path.join(DATA_DIR, dossier)
        if dossier in ("cache",):
            continue

        # Trouver le rudi_metadata.json pour avoir source/theme
        source = "tabular"
        theme = None
        dataset_id = dossier
        titre = None
        producteur = None
        meta_path = os.path.join(dpath, "rudi_metadata.json")
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                theme = meta.get("theme")
                dataset_id = meta.get("local_id", meta.get("global_id", dossier))
                titre = meta.get("resource_title") or meta.get("title")
                org = meta.get("organization") or meta.get("producer") or {}
                if isinstance(org, dict):
                    producteur = org.get("organization_name") or org.get("name")
                elif isinstance(org, str):
                    producteur = org
            except (OSError, json.JSONDecodeError):
                pass

        # Récupérer la vraie source depuis monitor.datasets
        cur.execute(f"""
            SELECT source FROM {sch_mon}.datasets WHERE dossier = %s LIMIT 1
        """, (dossier,))
        row_src = cur.fetchone()
        if row_src:
            source = row_src[0]

        fichiers = sorted(os.listdir(dpath))
        n_csv = 0
        n_geo = 0

        for fname in fichiers:
            fpath = os.path.join(dpath, fname)
            if not os.path.isfile(fpath):
                continue

            # CSV filtré : contient "rennesmetropole" dans le nom
            if fname.endswith(".csv") and "rennesmetropole" in fname:
                n = _importer_csv(fpath, dossier, dataset_id, source, theme,
                                  cur, sch_filt, limite)
                n_csv += n
                total_rows += n

            # GeoJSON (WFS/OGC) : tous les .geojson du dossier (sans rennesmetropole dans le nom)
            elif fname.endswith(".geojson"):
                n = _importer_geojson(fpath, dossier, cur, sch_filt)
                n_geo += n
                total_geo += n

        if n_csv or n_geo:
            total_dossiers += 1
            print(f"  {dossier}: {n_csv} lignes, {n_geo} features géo")
            # Marquer comme importé et renseigner titre/producteur si disponibles
            cur.execute(f"""
                UPDATE {sch_mon}.datasets
                SET data_imported = TRUE, data_imported_at = NOW(),
                    titre = COALESCE(%s, titre), producteur = COALESCE(%s, producteur)
                WHERE dossier = %s
            """, (titre, producteur, dossier))
            # Commit périodique pour éviter rollback en cas d'interruption
            if total_dossiers % 20 == 0:
                cur.connection.commit()
                print(f"    [progress] {total_dossiers}/{len(dossiers)} dossiers — {total_rows} lignes")

    print(f"[monitor] Import terminé : {total_dossiers} dossiers, "
          f"{total_rows} lignes, {total_geo} features géo.")

    # Mettre à jour le compteur filtered_rows dans metrics_history du jour
    cur.execute(f"SELECT COUNT(*) FROM {sch_filt}.data_rows")
    cnt = cur.fetchone()[0]
    cur.execute(f"""
        UPDATE {sch_mon}.metrics_history
        SET filtered_rows = %s
        WHERE date_key = %s
    """, (cnt, datetime.date.today()))


# ---------------------------------------------------------------------------
# Géocodage via RVA
# ---------------------------------------------------------------------------

def _geocode(conf: dict, cur) -> None:
    """Géocode les lignes de data_rows qui ont une adresse mais pas de code_insee,
    via l'API RVA (getfulladdresses)."""
    try:
        from connectors.rva import geocoder_adresse
    except ImportError:
        print("[monitor] Connecteur RVA non disponible — pas de géocodage.")
        return

    sch = _nom_schema(conf, "schema_filtered")
    sch_ref = _nom_schema(conf, "schema_ref")

    # Trouver les lignes avec une propriété adresse mais sans code_insee
    cur.execute(f"""
        SELECT DISTINCT d.id, d.properties
        FROM {sch}.data_rows d
        WHERE d.code_insee IS NULL
          AND d.properties->>'adresse' IS NOT NULL
          AND LENGTH(d.properties->>'adresse') > 5
        LIMIT 1000
    """)
    rows = cur.fetchall()
    if not rows:
        print("[monitor] Aucune ligne à géocoder.")
        return

    print(f"[monitor] Géocodage de {len(rows)} adresses via API RVA…")
    n_ok = 0
    n_err = 0
    for rid, props in rows:
        adresse = props.get("adresse", "")
        if not adresse:
            continue
        # Commune peut être dans une autre propriété
        insee = props.get("code_insee") or props.get("codgeo", "")
        result = geocoder_adresse(adresse, insee=insee if insee and len(insee) >= 5 else None)
        addr = result[0] if result else {}
        x, y = addr.get("x"), addr.get("y")
        if x and y:
            insee_rva = addr.get("insee", "")
            cur.execute(f"""
                UPDATE {sch}.data_rows
                SET code_insee = %s
                WHERE id = %s AND code_insee IS NULL
            """, (insee_rva, rid))
            n_ok += 1
        else:
            n_err += 1
    print(f"  {n_ok} adresses géocodées, {n_err} échecs.")


# ---------------------------------------------------------------------------
# Pipeline log
# ---------------------------------------------------------------------------

def _log_pipeline(conf: dict, cur,
                  duree: float = 0, succes: bool = True, etape: str = "",
                  erreur: str | None = None) -> None:
    sch = _nom_schema(conf, "schema_monitor")
    now = datetime.datetime.now()
    cur.execute(f"""
        INSERT INTO {sch}.pipeline_runs
            (date_run, start_time, end_time, etape, duree_secondes, succes, error_message)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (now.date(), now - datetime.timedelta(seconds=duree), now,
          etape or "pipeline", duree, succes,
          (erreur[:500] if erreur else None)))
    # Insert/update metrics_history for today
    cur.execute(f"""
        INSERT INTO {sch}.metrics_history (date_key, pipeline_duree_sec, pipeline_succes)
        VALUES (%s, %s, %s)
        ON CONFLICT (date_key) DO UPDATE SET
            pipeline_duree_sec = EXCLUDED.pipeline_duree_sec,
            pipeline_succes = EXCLUDED.pipeline_succes
    """, (now.date(), duree, succes))
    print(f"[monitor] Pipeline log: {etape} {duree:.0f}s {'OK' if succes else 'ECHEC'}")


# ---------------------------------------------------------------------------
# Drop filtered
# ---------------------------------------------------------------------------

def _drop_filtered(conf: dict, cur) -> None:
    sch = _nom_schema(conf, "schema_filtered")
    cur.execute(f"DROP SCHEMA IF EXISTS {sch} CASCADE")
    print(f"[monitor] Schéma {sch} supprimé.")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def _status(conf: dict, cur) -> None:
    sch_mon = _nom_schema(conf, "schema_monitor")
    sch_filt = _nom_schema(conf, "schema_filtered")
    sch_ref = _nom_schema(conf, "schema_ref")
    sch_dec = _nom_schema(conf, "schema_decouverte")

    for label, sql in [
        ("Datasets monitorés", f"SELECT COUNT(*) FROM {sch_mon}.datasets"),
        ("Datasets publiés RUDI", f"SELECT COUNT(*) FROM {sch_mon}.datasets WHERE rudi_publie = TRUE"),
        ("Datasets publiés (source)", f"SELECT COUNT(*) FROM {sch_mon}.datasets WHERE rudi_publie_source = TRUE"),
        ("Lignes filtrées importées", f"SELECT COUNT(*) FROM {sch_filt}.data_rows"),
        ("Features géo importées", f"SELECT COUNT(*) FROM {sch_filt}.geo_features"),
        ("Communes de référence", f"SELECT COUNT(*) FROM {sch_ref}.communes_rm"),
        ("IRIS de référence", f"SELECT COUNT(*) FROM {sch_ref}.iris_rm"),
        ("SIREN de référence", f"SELECT COUNT(*) FROM {sch_ref}.sirens_rm"),
        ("JDD à examiner", f"SELECT COUNT(*) FROM {sch_dec}.a_examiner"),
        ("Candidats découverte", f"SELECT COUNT(*) FROM {sch_dec}.candidats"),
        ("Entrées historique", f"SELECT COUNT(*) FROM {sch_dec}.historique"),
    ]:
        try:
            cur.execute(sql)
            print(f"  {label} : {cur.fetchone()[0]}")
        except Exception as e:
            print(f"  {label} : (table absente — {e})")

    try:
        cur.execute(f"""
            SELECT date_key, tabulaire_total, tabulaire_rudi, tabulaire_rm,
                   insee_total, insee_rm, decouverte_a_examiner, filtered_rows
            FROM {sch_mon}.metrics_history
            ORDER BY date_key DESC LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            print(f"\n  Dernier snapshot ({row[0].isoformat()}):")
            print(f"    Tabulaire: {row[1]} JDD, {row[2]} publiés, {row[3]} lignes RM")
            print(f"    INSEE: {row[4]} publications, {row[5]} lignes RM")
            print(f"    Backlog: {row[6]} à examiner")
            print(f"    Lignes importées: {row[7]}")
    except Exception:
        pass

    # Taille de la base
    cur.execute("""
        SELECT pg_size_pretty(pg_database_size(current_database()))
    """)
    print(f"\n  Taille base PostGIS : {cur.fetchone()[0]}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        description="Monitoring Moissonneuse-batteuse → PostGIS + Superset",
    )
    parser.add_argument("--init-db", action="store_true", help="Créer les schémas et tables")
    parser.add_argument("--refresh", action="store_true", help="Mettre à jour les métriques")
    parser.add_argument("--import-data", action="store_true", help="Importer les données filtrées")
    parser.add_argument("--import-ref", action="store_true", help="Importer les données de référence")
    parser.add_argument("--geocode", action="store_true", help="Géocoder via API RVA")
    parser.add_argument("--log-pipeline", nargs="*", help="Enregistrer un run pipeline (durée succès étape)")
    parser.add_argument("--drop-filtered", action="store_true", help="Supprimer le schéma filtered")
    parser.add_argument("--status", action="store_true", help="Afficher les stats")
    parser.add_argument("--full", action="store_true", help="Init + refresh + import-ref")
    parser.add_argument("--dossier", type=str, default="", help="Restreindre à un dossier")
    parser.add_argument("--limit", type=int, default=0, help="Limiter le nombre de lignes (test)")
    args = parser.parse_args(argv)

    conf = _charger_conf_db()

    # Pipeline log from command line (called by cli.py)
    if args.log_pipeline is not None:
        conn = _connecter(conf)
        try:
            cur = conn.cursor()
            duree = float(args.log_pipeline[0]) if len(args.log_pipeline) > 0 else 0
            succes = args.log_pipeline[1].lower() in ("true", "1", "ok") if len(args.log_pipeline) > 1 else True
            etape = args.log_pipeline[2] if len(args.log_pipeline) > 2 else "pipeline"
            _log_pipeline(conf, cur, duree, succes, etape)
            conn.commit()
        finally:
            conn.close()
        return

    if args.full:
        args.init_db = True
        args.refresh = True
        args.import_ref = True

    if not any([args.init_db, args.refresh, args.import_data, args.import_ref,
                args.geocode, args.drop_filtered, args.status]):
        parser.print_help()
        return

    conn = _connecter(conf)
    try:
        cur = conn.cursor()

        if args.init_db:
            _init_db(conf, cur)
            conn.commit()

        if args.drop_filtered:
            _drop_filtered(conf, cur)
            conn.commit()

        if args.refresh:
            _refresh(conf, cur)
            conn.commit()

        if args.import_ref:
            _import_ref(conf, cur)
            conn.commit()
            _importer_geometries_ref(conf, cur)
            conn.commit()

        if args.import_data:
            _import_data(conf, cur, args.dossier, args.limit)
            conn.commit()

        if args.geocode:
            _geocode(conf, cur)
            conn.commit()

        if args.status:
            _status(conf, cur)

    finally:
        conn.close()


if __name__ == "__main__":
    main()

# moissonneuse-batteuse

*Projet expérimental conçu avec l'aide des agents IA Claude (Sonnet, Fable) et OpenCode (Big Pickle).*

Pipeline de moisson d'open data pertinent pour **Rennes Métropole** (43 communes, EPCI 243500139), avec publication automatique vers un nœud [RUDI](http://rudi.fr) (Rennes Urban Data Interface).

Le pipeline découvre des jeux de données publics (principalement sur [data.gouv.fr](https://www.data.gouv.fr)), les filtre pour ne garder que les lignes/couches concernant le territoire de Rennes Métropole, les traduit au format de métadonnées RUDI, puis les publie sur un nœud RUDI local.

## Sommaire

- [Fonctionnement du pipeline](#fonctionnement-du-pipeline)
- [Composants](#composants)
- [Installation](#installation)
- [Configuration](#configuration)
- [Utilisation](#utilisation)
- [Tests](#tests)
- [Structure du dépôt](#structure-du-dépôt)
- [Limitations connues](#limitations-connues)

## Fonctionnement du pipeline

1. **Découverte** — recherche de candidats sur data.gouv.fr (CSV, WFS/WMS) par mots-clés et requêtes structurées, avec revue interactive ou automatique des cas ambigus.
2. **Moisson tabulaire** — téléchargement, filtrage géographique (code postal, commune, IRIS, SIREN, EPCI, coordonnées, circonscription, département — cascade de détection par ordre de fiabilité) et traduction au format RUDI.
3. **Moisson géo** — services WFS (téléchargement GeoJSON), WMS (référence de service pour carte Leaflet), OGC API Features.
4. **Moissons directes** — sources spécialisées : insee.fr, data-fair OEB (Observatoire de l'Environnement en Bretagne), ZIP BDNB (Base de Données Nationale des Bâtiments, département 35).
5. **Catalogue + publication** — génération d'un catalogue HTML (visionneuses de données, cartes) et publication des métadonnées + fichiers sur un nœud RUDI.

Chaque script de moisson conserve un état incrémental sur disque (`data/state*.json`) pour ne retélécharger que ce qui a changé, et publie vers RUDI en best-effort sans jamais bloquer le pipeline.

## Composants

| Composant | Rôle |
|---|---|
| `src/cli.py` | Entrée terminal — menu de 20 actions (moisson, pipeline, maintenance, données) |
| `src/dashboard.py` | Entrée web — mêmes actions via une interface locale (`http://127.0.0.1:8765`) |
| `src/harvest_auto.py` | Entrée cron/Jenkins — découverte automatique + pipeline complet, non interactive |
| `src/discover.py` | Découverte de nouveaux jeux de données sur data.gouv.fr |
| `src/main.py` / `src/harvest_batch.py` | Moisson tabulaire (datasets configurés / candidats découverts) |
| `src/harvest_geo.py` | Moisson des services géographiques (WFS/WMS/OGC API) |
| `src/harvest_insee.py` / `src/harvest_oeb.py` / `src/harvest_bdnb.py` | Moissons directes spécialisées |
| `src/catalogue.py` | Génération du catalogue HTML (avec visionneuses et cartes) |
| `src/publish_rudi.py` | Rattrapage de publication vers le nœud RUDI |

Le détail exhaustif des modules (connecteurs, filtres, traducteurs, état) est documenté dans [`CLAUDE.md`](CLAUDE.md), qui fait office de documentation technique de référence pour ce dépôt.

## Installation

Prérequis : **Python 3.10+** (le code utilise la syntaxe d'union de types `X | None`).

```bash
git clone git@github.com:simon-sg/moissonneuse-batteuse.git
cd moissonneuse-batteuse
python3 -m venv .venv
source .venv/bin/activate
pip install requests
```

`requests` est la seule dépendance obligatoire (moisson tabulaire, géo, publication de base). Des dépendances optionnelles étendent certaines fonctionnalités et sont dégradées proprement si absentes :

```bash
pip install openpyxl pyarrow fsspec   # analyse XLSX/Parquet en phase de découverte
pip install rudi_node_write   # publication vers un nœud RUDI
```

Pour un nœud RUDI local de test, Podman est utilisé.

## Configuration

Toute la configuration sensible (identifiants, clés) est **gitignorée** ; seuls des gabarits `.example` sont versionnés.

### Nœud RUDI (obligatoire pour la publication)

```bash
cp src/conf/rudi_node.example.json src/conf/rudi_node.json
```

```json
{
  "url": "http://localhost:4032",
  "url_catalog": "http://localhost:4030/catalog",
  "usr": "...",
  "pwd": "..."
}
```

Sans ce fichier, le pipeline de moisson fonctionne normalement (téléchargement, filtrage, catalogue) mais la publication RUDI est simplement sautée (best-effort).

### Jeux de données à moissonner

Les sources tabulaires et géographiques sont déclarées dans `src/conf/datasets.py` (`DATASETS`, `DATASETS_GEO`, `DATASETS_INSEE`, `DATASETS_OEB`, `DATASETS_BDNB`). Voir « Ajout d'un jeu de données » et « Ajout d'un service géo » dans `CLAUDE.md` pour le format attendu.

## Utilisation

### Interface web (recommandé)

```bash
python3 src/dashboard.py
```

Ouvrir `http://127.0.0.1:8765`. Le serveur écoute uniquement en local (pas d'authentification — ne jamais exposer au-delà de la machine). Pages : accueil (actions + état), revue du backlog de découverte, configuration de la découverte, catalogue généré.

### Ligne de commande

```bash
python3 src/cli.py
```

Menu interactif à 20 actions réparties en 4 sections : Moisson, Pipeline & publication, Maintenance, Données & infos.

### Scripts individuels

Chaque étape du pipeline tourne aussi en standalone :

```bash
python3 src/discover.py             # session de découverte interactive
python3 src/main.py                 # moisson des datasets configurés (data.gouv.fr)
python3 src/harvest_batch.py        # moisson des candidats découverts
python3 src/harvest_insee.py [id ...]
python3 src/harvest_oeb.py [--decouvrir]
python3 src/harvest_bdnb.py
python3 src/harvest_geo.py
python3 src/catalogue.py
python3 src/publish_rudi.py         # rattrapage de publication RUDI
python3 src/enrichir_descriptions.py   # rattrapage des descriptions vides
python3 src/enrichir_organisations.py [--dry-run]   # rattrapage des descriptions producteurs
python3 src/enrichir_contacts.py [--dry-run]        # rattrapage des contacts génériques
python3 src/reanalyser_faux_positifs.py [--appliquer] [--dossier X]
```

### Automatisation (cron/Jenkins)

```bash
python3 src/harvest_auto.py
```

Découverte automatique (sans interaction) → démarrage du nœud RUDI si nécessaire → pipeline complet (moisson, catalogue, publication). Code de sortie `0` uniquement si tout a réussi. Un fichier de log horodaté est écrit dans `logs/` à chaque exécution. Exemple de crontab :

```
0 5 * * * cd /chemin/vers/moissonneuse-batteuse && python3 src/harvest_auto.py >> logs/harvest_auto.log 2>&1
```

## Tests

Tests unitaires stdlib (`unittest`), logique pure sans accès réseau (filtres géographiques, traducteurs RUDI, cascade de détection, gestion d'état) :

```bash
python3 -m unittest discover tests/
```

## Structure du dépôt

```
src/
  cli.py, dashboard.py, harvest_auto.py   # points d'entrée
  discover.py, review.py                   # découverte + revue manuelle
  main.py, harvest_batch.py                # moisson tabulaire
  harvest_geo.py, harvest_insee.py,
  harvest_oeb.py, harvest_bdnb.py          # moissons spécialisées
  catalogue.py, publish_rudi.py            # catalogue + publication
  enrichir_*.py, reanalyser_faux_positifs.py   # scripts de rattrapage ponctuels
  state.py, tee.py                          # utilitaires partagés (état, log dupliqué)
  conf/                                     # configuration (datasets, communes RM, secrets gitignorés)
  connectors/                               # accès réseau (data.gouv, RUDI, WFS/WMS, INSEE, OEB, BDNB...)
  filters/                                  # filtrage géographique + CSV
  translation/                              # traduction vers le format de métadonnées RUDI
  static/                                   # assets du dashboard web
tests/                                      # tests unitaires (sans réseau)
data/                                       # données moissonnées + état (gitignoré, régénérable)
CLAUDE.md                                   # documentation technique de référence
```

`data/` est intégralement gitignoré depuis juillet 2026 : c'est un cache régénérable par une moisson, pas une source de vérité à versionner.

## Limitations connues

Voir la section « Known limitations / not addressed » de [`CLAUDE.md`](CLAUDE.md) pour la liste à jour des simplifications assumées (fichiers d'état non unifiés, thème RUDI heuristique, `harvest_auto.py` non planifié sur cette machine, etc.).

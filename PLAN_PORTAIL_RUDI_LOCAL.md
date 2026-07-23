# Déployer un portail RUDI local (ROOB) et y raccorder le nœud producteur existant

> **Plan destiné à être implémenté par un agent autonome.** Toutes les informations nécessaires sont dans ce document (l'agent n'a pas accès à la conversation d'origine). Un contrôle de l'implémentation sera fait ensuite par un autre agent : respecter les livrables et la checklist de vérification à la lettre, et consigner les écarts constatés dans le README livré.

## 1. Contexte

Le pipeline `moissonneuse-batteuse` (`/media/simon/DATA4T/Dev/moissonneuse-batteuse`) publie de l'open data vers un **nœud producteur RUDI local** :
- Conteneur **Podman** nommé `rudinode`, image locale `rudinode-local` (= `ghcr.io/rudi-platform/rudinode:2.7.4`), **actuellement arrêté** (`Exited (143)`).
- Volume : `/media/simon/DATA4T/Dev/moissonneuse-batteuse/rudi-node/data:/data` ; ports publiés 3030 (catalog API), 3031 (storage), 3032 (manager UI).
- Commande de lancement actuelle :
  ```bash
  SU="cnVkaW5vZGUgYWRtaW46R3dvRDFiTmt5N1F1ZjNrbG1NZVk3NUhnVFdtUDZsZFpzU0ZJLWJDY1NMVWI2MldKOTZkMlJRVDZlMTFUd0E0eGNzTDljSHVNSnFaSkh4eW1SZE1iemRhMUM5WU8yU3Q2QVJoMmhlZFN1UmpZWW5PcXZpbDFEWDJ4cDJqZTZ3"
  podman run -d --name rudinode \
      --volume /media/simon/DATA4T/Dev/moissonneuse-batteuse/rudi-node/data:/data \
      --publish 3030:3030 --publish 3031:3031 --publish 3032:3032 \
      -e SU="$SU" rudinode-local
  ```
  Comptes manager : SuperAdmin `rudinode admin` / `W#2*Zp8eH6QZfh]` ; admin `simon` / `simonrudi`.

**Objectif** : tester la chaîne de publication jusqu'au bout en déployant le **portail RUDI** en local (version conteneurisée officielle), en y **déclarant un producteur neuf « Moissonneuse-batteuse »**, et en raccordant le nœud dans les deux sens (moissonnage pull + notification push).

Décisions actées avec l'utilisateur :
- **Producteur neuf** créé proprement via les API du portail (pas de réutilisation du nœud de démo `nodestub`).
- **Déploiement manuel + scripts documentés** dans un dossier autonome. **Interdiction de modifier le repo `moissonneuse-batteuse`** (ni code, ni dashboard, ni CLI). Seule exception : créer le fichier de conf portail du nœud sous `rudi-node/data/` (hors git — vérifier avec `git check-ignore` avant écriture ; en cas de doute, ne pas committer quoi qu'il arrive).

## 2. Faits établis (recherche préalable — sources vérifiées)

### Le portail : rudi-out-of-the-box (ROOB)
- Dépôt : https://github.com/rudi-platform/rudi-out-of-the-box — portail RUDI complet dockerisé, `.env` avec `base_dn=localhost` et `rudi_version=v3.3.3`, images Docker Hub `rudiplatform/rudi-microservice-*`.
- 4 fichiers compose : `docker-compose-magnolia.yml` (CMS), `docker-compose-rudi.yml` (microservices + PostgreSQL), `docker-compose-dataverse.yml` (stockage métadonnées), `docker-compose-network.yml` (Traefik v2.11, labels de routage).
- Nécessite **git-lfs** (`git lfs pull` récupère les dumps de BDD). git-lfs n'est **pas installé** sur la machine (`git lfs` → commande introuvable) → `sudo apt install git-lfs`.
- Routage Traefik (port 80/443 hôte) par Host header :
  - `http://rudi.localhost/` → front portail ; `/oauth…`, `/authenticate`, `/acl` → microservice ACL ; `/kalim` → Kalim ; `/strukture`, `/node` → Strukture ; `/konsult` → Konsult ; `/medias/...` réécrit vers apigateway ; `/gateway` → gateway.
  - `http://dataverse.localhost`, `http://magnolia.localhost`, `http://solr.localhost`.
- Ports publiés sur l'hôte : 80, 443 (Traefik), 8761 (registry Eureka), 35432/35433/35434 (PostgreSQL rudi/dataverse/magnolia), 8025 (Mailhog). **Tous vérifiés libres** sur la machine.
- Identifiants pré-configurés (fichier `documentation/identifiants.md` du dépôt) — les plus utiles :
  - Super admin portail : `rudi` / `Rud1R00B-admin`
  - Animateur : `animateur@rennesmetropole.fr` / `Rud1R00B-animateur`
  - BDD rudi : `rudi` / `Rud1R00B-db-rudi` sur `localhost:35432`, base `rudi` (schémas `strukture_data`, `acl_data`, …)
  - Nœuds de démo (login ACL = UUID du node_provider) : `5596b5b2-b227-4c74-a9a1-719e7c1008c7` / `Rud1R00B-NP-nodestub` (nodestub, url seedée `http://172.17.0.1:28001/nodestub`), sib `d7ffa7cc-…` / `Rud1R00B-NP-sib`, irisa `d343dd99-…` / `Rud1R00B-NP-irisa`
  - Mailhog : `rudi-mailhog` / `Rud1R00B-mh` ; Dataverse : `dataverseAdmin` / `Rud1R00B-dvadmin` ; Magnolia : `superuser` / `Rud1R00B-mgl-admin`
- **Pas de persistance PostgreSQL par défaut** : les volumes `postgresql_data` sont commentés dans `docker-compose-rudi.yml` (données perdues au `docker compose down`, conservées au simple `stop/start`).
- Prérequis annoncés : 24-32 Go RAM. Machine : 31 Go RAM, 8 cœurs, ~1 To libre sur `/media/simon/DATA4T`, Docker 29.1.3 + Compose 2.40.3. Aucun autre conteneur Docker ne tourne actuellement (Superset/PostGIS monitoring arrêtés — les laisser arrêtés pendant les tests).

### Mécanique de raccordement portail ↔ nœud (source : code `rudi-platform/rudi-portal`, branche main)
- **Moissonnage (pull)** : le microservice **Kalim** re-scanne toutes les 30 s la liste des nœuds `harvestable` (via Strukture) et planifie pour chacun un cron (champ `harvesting_cron`, défaut `0 0 * * * *` = toutes les heures, format Spring 6 champs). Le harvest fait `GET {node_provider.url}/resources?updated_after={last_harvesting_date}&offset=…` **sans authentification** (classes `HarvestingScheduler/HarvestingHelper/HarvestingConfiguration`, `resourcesPath` défaut `/resources`) et attend un JSON `{total, items:[Metadata…]}`. Rapport d'intégration : `PUT {node_provider.url}/resources/{global_id}/report`.
- **Modèle de données portail** : table `strukture_data.node_provider` (uuid, url, harvestable, notifiable, harvesting_cron, last_harvesting_date, version, opening_date), rattachée à un `provider`. Le compte du nœud est un utilisateur **ACL type ROBOT, login = UUID du node_provider, rôle `PROVIDER`**.
- **API du portail** (préfixes vus du Traefik, tous sur `http://rudi.localhost`) :
  - `POST /oauth2/token` — Basic auth `login:password`, body `grant_type=client_credentials&username=…&password=…` (form-urlencoded) → `{access_token}`. C'est la mécanique qu'utilise le nœud lui-même (vu dans `rudi-node-catalog/src/config/confPortal.js`). Repli si le grant échoue pour le compte `rudi` : `POST /authenticate` (form login du front).
  - `POST /strukture/v1/providers` (rôle ADMINISTRATOR) — créer le producteur `{code, label, openingDate}`.
  - `POST /strukture/v1/providers/{providerUuid}/nodes` (rôle ADMINISTRATOR) — créer le nœud `{url, harvestable, notifiable, harvestingCron, version, openingDate}` → retourne le `uuid` du node_provider. **Vérifié dans `ProviderServiceImpl.createNode` : ne crée PAS le compte ACL** (le `NodeProviderUserHelper` n'y est pas branché dans cette version) → créer le compte explicitement :
  - `GET /acl/v1/roles` puis `POST /acl/v1/users` (rôle ADMINISTRATOR) — `{login:<node_uuid>, password:<clair>, type:"ROBOT", company:…, firstname:…, roles:[<objet rôle PROVIDER>]}`. Vérifié dans `UserServiceImpl.createUser` : le mot de passe fourni est **bcrypt-é côté serveur**. (Ne pas utiliser `PUT …/password` : `updateUserPassword` exige l'ancien mot de passe.)
- **Côté nœud** (source : `rudi-platform/rudi-node-container` branche `irisa`, README §1H, et `rudi-node-catalog/0-ini/portal_conf_default.ini`) : la connexion au portail s'active en passant `-e PORTAL_CONF="/data/conf/rudi-catalog-portal.ini"` au conteneur, fichier ini :
  ```ini
  [portal]
  portal_url = http://rudi.localhost
  login = <uuid du node_provider>
  passw = <mot de passe encodé base64>   ; echo -n "$PWD" | basenc --base64 -w 0
  is_pwd_b64 = true
  ```
  Le nœud utilise alors : `oauth2/token` (token), `oauth2/jwks` (clé publique portail pour valider les rapports), `kalim/v1/resources` (push des métadonnées), `konsult/v1/…`.
- **Réseau inter-runtimes** (nœud sous **Podman**, portail sous **Docker**) :
  - Portail → nœud : les conteneurs Docker joignent l'hôte via `172.17.0.1` (passerelle bridge Docker — c'est exactement le pattern du seed nodestub). Le nœud publie 3030-3032 sur `0.0.0.0` → **URL du node_provider : `http://172.17.0.1:3030/api/v1`** (le harvest donnera `http://172.17.0.1:3030/api/v1/resources`).
  - Nœud → portail : le conteneur Podman doit résoudre `rudi.localhost` vers l'hôte (Traefik :80) **avec le bon Host header** → relancer le nœud avec `--add-host rudi.localhost:host-gateway`. Si cette version de podman ne supporte pas le mot-clé `host-gateway`, utiliser l'IP passerelle du réseau podman (la relever via `podman run --rm alpine ip route | awk '/default/ {print $3}'`).

## 3. Livrables

Dossier autonome `/media/simon/DATA4T/Dev/rudi-portal-local/` (hors du repo moissonneuse-batteuse ; y initialiser un git local est optionnel — si oui, `.gitignore` sur `credentials.json` et `rudi-out-of-the-box/`) :

```
rudi-portal-local/
├── README.md                        # doc complète : démarrage/arrêt, identifiants, raccordement, dépannage, écarts constatés
├── rudi-out-of-the-box/             # clone ROOB (modifications locales minimales, documentées dans README)
├── raccordement/
│   ├── declarer_noeud.py            # script idempotent : producteur + nœud + compte ROBOT → credentials.json
│   ├── credentials.json             # généré (chmod 600) : provider_uuid, node_uuid, node_password, dates
│   └── rudi-catalog-portal.ini.exemple
└── noeud/
    └── relancer_noeud.sh            # relance podman avec PORTAL_CONF + add-host (reprend la commande §1)
```

Plus, côté nœud (hors livrable git) : `/media/simon/DATA4T/Dev/moissonneuse-batteuse/rudi-node/data/conf/rudi-catalog-portal.ini`.

## 4. Étapes d'implémentation

### Étape 0 — Pré-requis
1. `sudo apt install -y git-lfs && git lfs install` (demander à l'utilisateur de taper `! sudo apt install -y git-lfs` si sudo interactif nécessaire).
2. Vérifier la résolution : `getent hosts rudi.localhost dataverse.localhost magnolia.localhost` → doivent donner 127.0.0.1 (systemd-resolved résout `*.localhost` nativement) ; sinon ajouter à `/etc/hosts` : `127.0.0.1 rudi.localhost dataverse.localhost magnolia.localhost solr.localhost`.
3. Vérifier que les ports 80, 443, 8761, 8025, 35432-35434 sont toujours libres (`ss -tlnp`).

### Étape 1 — Déployer le portail
```bash
mkdir -p /media/simon/DATA4T/Dev/rudi-portal-local && cd /media/simon/DATA4T/Dev/rudi-portal-local
git clone https://github.com/rudi-platform/rudi-out-of-the-box.git
cd rudi-out-of-the-box && git lfs pull
chmod -R 777 data && chmod -R 755 config
docker compose -f docker-compose-magnolia.yml -f docker-compose-rudi.yml \
               -f docker-compose-dataverse.yml -f docker-compose-network.yml \
               --profile "*" up -d
```
- Laisser `.env` tel quel (`base_dn=localhost`).
- Démarrage long (Dataverse + ~10 microservices Java) : surveiller `docker compose … ps` et attendre que `database` soit healthy et que les microservices soient enregistrés dans Eureka (`http://localhost:8761`). Prévoir 5-15 min.
- **Contrôles de l'étape** : `http://rudi.localhost` affiche le portail ; connexion UI avec `rudi`/`Rud1R00B-admin` fonctionne ; le catalogue montre les jeux de démo Dataverse.

### Étape 2 — Déclarer producteur + nœud + compte (script `raccordement/declarer_noeud.py`)
Python stdlib + `requests` (dispo sur la machine). Comportement :
1. **Token admin** : `POST http://rudi.localhost/oauth2/token`, header Basic `rudi:Rud1R00B-admin`, body `grant_type=client_credentials&username=rudi&password=Rud1R00B-admin`. Si 4xx, replis dans l'ordre : (a) `scope=read,write` ajouté ; (b) `POST /authenticate` (form `login`/`password`) et réutiliser le cookie/JWT retourné. Documenter dans le README lequel a fonctionné.
2. **Producteur** : chercher d'abord `GET /strukture/v1/providers?libelle=…` (idempotence) ; sinon `POST /strukture/v1/providers` avec `{"code":"MOISSONNEUSE_BATTEUSE","label":"Moissonneuse-batteuse Rennes Métropole","openingDate":"<now ISO>"}` → `provider_uuid`.
3. **Nœud** : `GET /strukture/v1/providers/{provider_uuid}/nodes` (idempotence) ; sinon `POST` avec :
   ```json
   {"url":"http://172.17.0.1:3030/api/v1","harvestable":true,"notifiable":true,
    "harvestingCron":"0 */5 * * * *","version":"2.7.4","openingDate":"<now ISO>"}
   ```
   → `node_uuid`. (Cron 5 min pour les tests ; noter dans le README qu'en usage durable on repasserait à horaire.)
4. **Compte ROBOT** : générer un mot de passe fort (`secrets.token_urlsafe(24)` + garantir les classes de caractères usuelles). `GET /acl/v1/roles` → objet rôle `code == "PROVIDER"`. `GET /acl/v1/users?login=<node_uuid>` (idempotence) ; sinon `POST /acl/v1/users` avec `{"login":"<node_uuid>","password":"<clair>","type":"ROBOT","company":"MOISSONNEUSE_BATTEUSE","firstname":"Moissonneuse-batteuse","lastname":"Noeud","roles":[<objet rôle PROVIDER>]}`. Adapter le payload aux champs réellement exigés (le modèle exact est dans l'OpenAPI ACL du portail ; en cas d'erreur 400, lire le message et ajuster).
5. **Contrôle immédiat** : `POST /oauth2/token` avec Basic `<node_uuid>:<password>` → doit rendre un `access_token`. Échec = s'arrêter et diagnostiquer (comparer avec le compte nodestub via psql `localhost:35432`, user `rudi`/`Rud1R00B-db-rudi` : `SELECT login,type FROM acl_data."user" WHERE login LIKE '5596%';` et la table des rôles associés).
6. Écrire `raccordement/credentials.json` (chmod 600) : `{portal_url, provider_uuid, node_uuid, node_login, node_password, created}`.

### Étape 3 — Configurer et relancer le nœud
1. Écrire `/media/simon/DATA4T/Dev/moissonneuse-batteuse/rudi-node/data/conf/rudi-catalog-portal.ini` (et sa copie d'exemple anonymisée dans `raccordement/`) :
   ```ini
   [portal]
   portal_url = http://rudi.localhost
   login = <node_uuid>
   passw = <node_password base64>
   is_pwd_b64 = true
   ```
   NB : le dossier `rudi-node/data` appartient peut-être à un UID de conteneur (sous-UID podman) — si besoin passer par `podman unshare` pour écrire dans le volume.
2. `noeud/relancer_noeud.sh` : `podman rm -f rudinode` puis la commande de lancement du §1 **plus** `-e PORTAL_CONF="/data/conf/rudi-catalog-portal.ini"` et `--add-host rudi.localhost:host-gateway` (fallback IP, cf. §2). Garder le `-e SU="$SU"` existant à l'identique.
3. **Contrôles de l'étape** : `curl -s http://localhost:3030/api/v1/resources | head` répond ; `podman logs rudinode` montre l'initialisation de la connexion portail sans erreur d'authentification (chercher `portal` dans les logs) ; le manager `http://localhost:3032/manager/` fonctionne toujours.

### Étape 4 — Vérification bout-en-bout (checklist à dérouler et consigner dans le README)
1. **Pull — joignabilité** : `docker run --rm curlimages/curl -s http://172.17.0.1:3030/api/v1/resources` → JSON `{total, items}` non vide (le nœud héberge déjà des JDD publiés par le pipeline).
2. **Pull — moissonnage** : dans les 5-6 min, `docker logs $(docker ps -qf name=kalim)` montre le harvest du nœud ; les JDD du nœud apparaissent sur `http://rudi.localhost` (rechercher un titre connu du catalogue local). Si erreurs d'intégration : les lire dans les logs kalim — des rejets de validation de métadonnées (contrat nœud 2.7.4 vs portail 3.3.3) sont possibles ; consigner chaque type d'erreur dans le README (section « Écarts »), ne pas tenter de patcher le portail.
3. **Rapports** : logs kalim montrent les `PUT …/report` ; `podman logs rudinode` montre leur réception (200).
4. **Téléchargement via portail** : ouvrir un JDD moissonné sur le portail, télécharger un media. Si échec parce que les URLs de connecteur pointent sur `localhost:3031` : vérifier avec `curl -s http://localhost:3030/api/v1/resources | python3 -m json.tool | grep -A2 connector`, et documenter le correctif (URL publique du storage du nœud à passer en `http://172.17.0.1:3031`, joignable depuis les conteneurs ET le navigateur hôte) — l'appliquer seulement si un paramètre de conf du nœud le permet proprement (pas de bidouille dans la BDD du nœud).
5. **Push — notification** : republier un JDD (`cd /media/simon/DATA4T/Dev/moissonneuse-batteuse && python3 src/publish_rudi.py`, ou re-moisson d'un dataset) → `podman logs rudinode` montre l'appel `kalim/v1/resources` ; le JDD est créé/mis à jour côté portail sans attendre le cron.
6. **Mails** : Mailhog `http://localhost:8025` (comptes `rudi-mailhog`/`Rud1R00B-mh`) reçoit les éventuels rapports/notifications mail du portail.

### Étape 5 — README + mémoire
1. `README.md` du dossier : architecture (schéma ci-dessous), commandes démarrage/arrêt (`up -d` / `stop` ; expliquer que `down` détruit les BDD non persistées et qu'il faut alors rejouer `declarer_noeud.py` — ou décommenter les volumes `postgresql_data` dans `docker-compose-rudi.yml`, au choix documenté), identifiants utiles, procédure de raccordement pas-à-pas, section dépannage (logs à consulter ; SQL `UPDATE strukture_data.node_provider SET last_harvesting_date = NULL WHERE uuid='<node_uuid>';` pour forcer un re-moissonnage complet), section « Écarts constatés » alimentée pendant l'implémentation.
2. Écrire une mémoire projet `/home/simon/.claude/projects/-media-simon-DATA4T-Dev-moissonneuse-batteuse/memory/project-portail-rudi-local.md` (+ ligne dans `MEMORY.md`) : chemins, UUIDs, comptes, pièges rencontrés, état final.

## Architecture (pour le README)

```
Navigateur ──► http://rudi.localhost (Traefik :80, Docker)
                 ├─ front portail, konsult, kalim, acl (/oauth2), strukture, projekt, konsent, kos…
                 ├─ Dataverse (métadonnées) + Magnolia (CMS) + 3× PostgreSQL + Mailhog (:8025)
                 │
  pull  : kalim ──► http://172.17.0.1:3030/api/v1/resources        (nœud Podman, via passerelle Docker)
  push  : nœud  ──► http://rudi.localhost/kalim/v1/resources        (via --add-host … host-gateway)
                 ▲
  pipeline moissonneuse-batteuse ──► nœud (manager :3032) — INCHANGÉ
```

## Points d'attention / garde-fous
- **Ne pas modifier le repo `moissonneuse-batteuse`** (aucun commit, aucun fichier suivi). Le pipeline publie vers le nœud comme avant.
- **RAM** : pile lourde ; ne pas démarrer Superset/PostGIS monitoring en parallèle. Si la machine swappe (`free -h`), le noter et arrêter les profils non indispensables.
- **Sécurité** : identifiants par défaut partout — usage strictement local, ne rien exposer hors de la machine.
- **Aucune suppression** : ne supprimer ni conteneurs existants (hors `podman rm -f rudinode` pour sa relance), ni volumes, ni données du nœud.
- En cas de blocage sur une API du portail, inspecter la BDD (`psql -h localhost -p 35432 -U rudi rudi`, mdp `Rud1R00B-db-rudi`) en s'inspirant du seed nodestub, mais **préférer toujours l'API** pour les écritures ; un éventuel INSERT/UPDATE SQL doit être minimal, consigné dans le README, et limité aux schémas `strukture_data`/`acl_data`.

## Critères de réussite (pour le contrôle final)
1. `docker compose … ps` : tous les services ROOB up ; portail accessible et fonctionnel sur `http://rudi.localhost`.
2. `credentials.json` présent, script `declarer_noeud.py` rejouable sans erreur (idempotent).
3. Token OAuth2 obtenu avec le compte du nœud (`node_uuid`).
4. Nœud relancé avec `PORTAL_CONF`, logs sans erreur d'auth portail ; manager :3032 toujours OK.
5. Les JDD du nœud sont visibles sur le portail après moissonnage (au moins un vérifié nominativement).
6. Une republication depuis le pipeline remonte au portail (push) — ou, si le push échoue pour une raison de contrat, l'écart est documenté et le pull fonctionne.
7. README complet + mémoire projet écrite.

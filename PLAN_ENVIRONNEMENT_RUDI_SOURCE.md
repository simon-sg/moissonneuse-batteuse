# Environnement RUDI « source » (nœud + portail) en parallèle du Docker, alimentés tous les deux par le pipeline

> **v2 — 2026-07-22.** Révision après audit sur machine (composes ROOB, `.properties` des microservices,
> `pom.xml` amont, sous-modules du nœud, outillage installé, surface d'impact pipeline). Les faits marqués
> ✅ ont été vérifiés en lisant les fichiers ou en interrogeant le réseau, pas déduits. La v1 de ce plan
> contenait quatre erreurs structurantes, corrigées ci-dessous.

## Contexte

Objectif : **proposer des pull requests à l'équipe `rudi-platform`**. Le chantier portail précédent
(`PLAN_PORTAIL_RUDI_LOCAL.md`, `rudi-portal-local/RAPPORT_BUGS_RUDI.md`) a produit 24 anomalies
documentées, dont plusieurs contournées par des relais maison faute de pouvoir modifier le code. Pour
transformer ces contournements en correctifs proposables, il faut pouvoir **construire, exécuter et
patcher RUDI depuis les sources**, sans casser l'environnement Docker existant qui porte 383 fiches
au catalogue et sert de démonstrateur.

Résultat attendu : un cycle « je modifie du code amont → je le vois tourner → j'ouvre une PR » d'environ
deux minutes, et un environnement de démonstration qui reste intact pendant ce temps.

## Ce que l'audit a corrigé par rapport à la v1

| # | La v1 supposait | Réalité vérifiée |
|---|---|---|
| 1 | Le portail = ~11 microservices Java + un front Angular | ✅ Le portail ROOB comprend **aussi Dataverse** (le magasin réel des métadonnées, appli Payara), **Solr**, **Magnolia** (CMS), Mailhog, Traefik et **3 Postgres**. Dataverse/Solr/Magnolia **ne sont pas dans le monorepo `rudi-portal`** — ils ne peuvent pas être « lancés depuis les sources », seulement conteneurisés |
| 2 | « Pas de Traefik nécessaire, `gateway` sert de point d'entrée unique » | ✅ Faux : `gateway` ne traite que `/gateway`. Le point d'entrée réel est le **nginx du conteneur front** (`config/portail-nginx.conf`, 14 `location` vers `https://<service>:8443`) et/ou **Traefik** (17 routeurs par labels + 2 routeurs de contournement dans `config/traefik-dynamic.yml`). Un environnement source doit reconstituer l'un des deux |
| 3 | Les scripts d'init SQL du portail seraient « à rejouer manuellement » | ✅ Plus grave : chez toi le dump `rudi.backup` est **désactivé** (incompatible Flyway) et seuls `01-usr.sql`/`02-extension.sql`/`04-grant.sql` tournent. Un déploiement frais démarre donc avec **`kos_data.skos_concept` et `strukture_data.organization` vides** → **ERR-303 + ERR-113 garantis** sur toute publication. Les 20 concepts SKOS et 90 organisations de ton portail actuel ont été semés à la main et n'existent nulle part sous forme reproductible |
| 4 | Le code cloné « peut diverger » de l'image ROOB | ✅ Il diverge, et c'est chiffré : sources `main` = **3.4.0 / JDK 21 / Spring Boot 3.5.7**, images ROOB = **v3.3.12** (sauf kalim, v3.4.0). L'environnement source n'est pas un clone du Docker — certains bugs y seront déjà corrigés, d'autres nouveaux |

Trois découvertes supplémentaires, absentes de la v1 :

- ✅ **Le nœud source ne porte pas tes correctifs.** `src/rudi-storage/src/httpService.js` du clone contient
  toujours les 4 `Access-Control-Allow-Origin: '*'` (l. 386, 402, 505, 785) et la regex OPTIONS cassée
  `/\/z?download\/:uuid/` (l. 183) — exactement ce que tu as patché dans l'image via `podman commit`.
  **C'est la PR n° 1 toute trouvée**, et elle ne demande aucun portail pour être validée.
- ✅ **Outillage manquant** : `javac`, `mvn` et `ng` sont absents de la machine (seul un JRE 21 est présent) ;
  les 4 composants du nœud demandent **Node 22** (`.nvmrc`) alors que Node **24.18.0** est installé.
- ✅ **Le registre privé est de nouveau en ligne** : `repository.aqmo.org/npm` → 200, tarballs sur
  `gitlab.aqmo.org` → 200. Les 4 composants du nœud en dépendent (`@aqmo.org/jwt-lib`,
  `@aqmo.org/rudi_logger`, absents de npmjs → 404). **À revérifier avant de commencer** : ces hôtes ont
  déjà été indisponibles plusieurs jours, et l'installation du nœud source est bloquée sans eux.

## Décisions actées (2026-07-22)

1. **Portail source : progression en 3 couches**, pas de natif intégral. Le natif coûterait 11 JVM en
   HTTPS/8443 avec keystore partagé et Eureka en TLS, un point d'entrée nginx à reconstituer, un
   superviseur de ~15 process, et ~11 Go de heap **réservé** par environnement (`-Xms` = `-Xmx` dans le
   Dockerfile amont) sur une machine de 31 Go. Le rebuild d'image ne coûte que ~5 s (le `Dockerfile`
   racine ne fait qu'un `ADD` du jar), les configs sont montées en volume (aucun rebuild pour un
   `.properties`), et le debug pas-à-pas s'attache à travers un conteneur via JDWP.
2. **Nœud source : processus natifs**, comme prévu en v1. Ici le natif est réellement bon marché
   (4 process Node, `nodemon` déjà câblé dans les `package.json`, pas de maillage TLS/Eureka), et c'est
   la cible de la PR n° 1.
3. **`rudi_publie` : dict par nœud + migration douce** — `{"docker": true, "source": false}`, un booléen
   hérité étant lu comme `{"docker": <valeur>}`.
4. **Aucun rattrapage rétroactif** : le nœud source ne reçoit que ce que les prochains runs moissonnent.
   Les 372 fiches déjà publiées sur le nœud Docker n'y seront pas repoussées.

## Prérequis machine (étape 0, bloquante)

```bash
sudo apt install openjdk-21-jdk maven          # javac + mvn absents ; JDK 21 imposé par pom.xml (jdk.cible=21)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash && nvm install 22
curl -sS -o /dev/null -w "%{http_code}\n" https://repository.aqmo.org/npm/@aqmo.org/jwt-lib   # doit répondre 200
```

Budget à annoncer : le premier `mvn install` complet du monorepo (11 microservices + génération OpenAPI +
front Angular) prend **20-60 min** et remplit **1-2 Go dans `~/.m2`** (estimation, à confirmer au premier run).
Disque : 1015 Go libres sur `/media/simon/DATA4T` ✅, largement suffisant.

## Table des ports

Occupés aujourd'hui (✅ relevés sur la machine) — **à ne jamais réutiliser** :

| Port(s) | Occupant |
|---|---|
| 3030-3032 | nœud RUDI Podman (`rudinode`) |
| 3033, 3034 | shim paginant, relais WMS (`rudi-portal-local/shim/`) |
| 80, 443 | Traefik ROOB |
| 4848, 8081 | Dataverse (admin Payara, HTTP) |
| 8025 | Mailhog · **8082** Magnolia · **8761** Eureka · **8983** Solr |
| 8088 | front portail ROOB — **déjà en conflit avec `mb-superset`** |
| 35432, 35433, 35434 | Postgres rudi / dataverse / magnolia |
| 5433 | `mb-postgis` (monitoring) |

Plage proposée pour l'environnement source — **4xxx**, aucune collision :

| Port | Rôle |
|---|---|
| 4027 | Mongo du nœud source (conteneur Podman dédié, `rudi-source-mongo`, image `mongo:7`) |
| 4030, 4031, 4032, 4033 | catalog / storage / manager / jwtauth du nœud source |
| 4080, 4443 | Traefik de la stack portail source (couche 3) |
| 4088 | front portail source |
| 4761 | Eureka source · **45432** Postgres source |
| 4848→**4849**, 8081→**48081**, 8983→**48983** | Dataverse/Solr source, si 2e instance (couche 3) |
| 5005-5015 | ports JDWP de debug, un par microservice patché |

## Étapes

### Étape 1 — Socle de build du portail — ✅ FAIT (contrôlé le 2026-07-23)

> `rudi-portal-source` cloné sur le tag **v3.4.0**, **12/12** jars `*-facade.jar` construits, dist
> Angular `rudi-application-front-office-angular-dist.zip` (7,9 Mo) produite, `~/.m2` à 2,8 Go avec les
> artefacts `org.rudi`. JDK 21 + Maven 3.8.7 installés. Rien à corriger.


Cloner `rudi-platform/rudi-portal` à côté des autres (`/media/simon/DATA4T/Dev/rudi-portal-source`), puis
`mvn install -DskipTests` à la racine. Livrable : tous les jars `*-facade.jar` présents dans les
`target/`, donc les images reconstructibles par le `Dockerfile` racine (une cible par microservice,
`--target rudi-microservice-<nom>`).

Vérification : `mvn install` se termine sans erreur et `ls rudi-microservice/*/*-facade/target/*.jar`
liste 12 jars.

⚠️ Le module `rudi-application` construit le front Angular **via Maven** (il produit
`rudi-application-front-office-angular-dist.zip`) — pas besoin d'`ng` installé globalement pour builder,
seulement pour le mode `ng serve` du travail front.

### Étape 2 — Nœud source en processus natifs — ✅ FAIT, 2 défauts corrigés (contrôlé le 2026-07-23)

> 4 process sur Node **v22.23.1** (conforme aux `.nvmrc`), Mongo 7 dédié sur 4027 avec des bases
> distinctes (`rudi_catalog_src`, `rudi_media_src`) et des dossiers `source-media`/`-db`/`-keys`
> /`-pubkeys` : **isolation vérifiée, aucun partage avec le nœud Docker**. Aucune section `[portal]`
> configurée — le nœud source ne pousse pas vers le portail ROOB, son catalogue reste vide.
>
> Corrigés : (1) `_api_version()` (pipeline) codait en dur le port 3030 — les fiches publiées sur le
> nœud source auraient porté la version du catalog *voisin*, ou « 1.4.0 » par défaut quand celui-ci est
> arrêté ; un champ `url_catalog` par nœud a été ajouté (source → 1.4.3 vérifié). (2) le mot de passe du
> compte SU du manager était stocké **en clair** au lieu d'un haché — `matchPassword()` échouait donc
> toujours et le manager rendait un message trompeur sur le *nom d'utilisateur* ; réécrit en haché
> (sauvegarde `.bak` à côté). Le pipeline s'authentifie désormais et voit les 14 thèmes.
>
> Restes connus, non bloquants : le front React du manager n'est pas construit (pas d'UI web sur 4032 —
> `npm --prefix front ci && npm --prefix front run build:local`), et aucun script de lancement n'existe
> encore (les 4 process ont été démarrés à la main — c'est l'objet de l'étape 7).
>
> **Publication bout-en-bout validée le 2026-07-23** (`src/conf/rudi_nodes.json` créé, gitignoré) :
> une fiche réelle publiée simultanément sur les deux nœuds, chacun avec son propre `global_id` et son
> média servi par *son* storage (3031 / 4031). Deux blocages levés au passage : le storage source
> n'avait aucune section `[auth]` (aucune identité de manager connue → 401 à chaque upload), et
> `noeud_pret()` déclarait le nœud Docker prêt alors que `/manager/conf` renvoyait encore 500 pendant
> ~20 s après un `podman start` — la sonde interroge désormais cet endpoint.


Les 4 sous-modules sont déjà clonés dans `/media/simon/DATA4T/Dev/rudi-node-build/rudi-node-container/src/`
(branche `irisa`) ✅. Pour chacun : `nvm use 22`, `npm ci`, puis le script `start` du `package.json`
(tous passent par `nodemon` — rechargement à chaud gratuit).

- Références de configuration, **à lire sans les exécuter** : `install/env-catalog.sh`, `env-storage.sh`,
  `env-manager.sh`, `env-jwtauth.sh`, `env-db.sh` (variables d'env attendues) et `ini/*.ini.am`
  (gabarits des `.ini`, dont `rudi-catalog-portal.ini.am`).
- **Mongo** : le nœud utilise `mongoose` 8.15 ✅ ; l'image Podman embarque un `mongod` Alpine
  (`apk add mongodb`) dont la version exacte est à relever (`podman exec rudinode mongod --version`)
  avant de choisir l'image du conteneur Mongo dédié — un `mongo:7` récent est le défaut raisonnable.
- Le manager a un **front React séparé** (`src/rudi-manager/front/`, `react-scripts`) à builder ou servir à part.

Vérification : les 4 process répondent sur 4030/4031/4032, et `POST` d'une fiche de test via le manager
aboutit. **Puis, immédiatement : PR n° 1** (voir étape 7) — elle ne dépend d'aucun portail.

### Étape 3 — Boucle de travail hybride sur le portail (le cœur du cycle)

C'est le mode par défaut au quotidien. La stack ROOB tourne normalement ; pour le microservice que tu
patches :

```bash
docker compose ... stop konsult                          # on libère la place
java -Drudi.config=<ROOB>/config/konsult \
     -Dspring.config.additional-location=file:<ROOB>/config/konsult/ \
     -agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=*:5005 \
     -jar rudi-microservice/rudi-microservice-konsult/rudi-microservice-konsult-facade/target/*.jar
```

Le service natif s'enregistre auprès du même Eureka et lit la **même configuration** que le conteneur
qu'il remplace : aucune réécriture de hostname, aucun port à décaler. Coût RAM nul (un service en
remplace un autre), boucle de correction la plus courte, debug pas-à-pas dans l'IDE.

Deux points à vérifier au premier essai, non résolus par la lecture seule :
- le service natif doit joindre `acl`, `registry`, `database`, `dataverse` **par leur nom Docker** →
  ajouter les alias correspondants dans `/etc/hosts` vers `127.0.0.1` et publier les ports concernés,
  ou lancer le process dans le réseau Docker ;
- Eureka publie `prefer-ip-address=true` ✅ : vérifier que l'IP annoncée par le process natif est bien
  joignable depuis les conteneurs.

Pour le **front Angular**, `ng serve` avec un `proxy.conf.json` calqué sur `config/portail-nginx.conf`
est nettement supérieur — c'est l'exception native assumée.

### Étape 4 — Stack portail source complète, montée à la demande

À faire seulement quand une validation bout-en-bout en isolation est nécessaire (ou pour reproduire un
bug de volume). Copier le jeu de composes ROOB dans un projet distinct (`name:` différent, ports de la
table 4xxx, volumes et réseau dédiés), et remplacer les `image:` du Docker Hub par les images bâties
localement (`rudiplatform/rudi-microservice-konsult:source`).

- **Dataverse/Solr** : deux options, à trancher au moment où l'étape est atteinte — 2e instance dédiée
  (isolation totale, +3-4 Go de RAM, seed depuis `config/dataverse-init/dataverse.backup`, 69 Mo ✅) ou
  instance partagée avec un **alias de collection distinct** (`dataverse.api.rudi.data.alias=rudi_data_src`,
  ~3-4 Go économisés, panne Dataverse commune aux 2 portails). Recommandation : alias distinct.
- **Magnolia est optionnel** : ✅ ton portail fonctionne avec le conteneur `magnolia` arrêté.
- **Baisser les heaps** : `XMX=512M` au lieu du `-Xmx1G -Xms1G` par défaut du Dockerfile, sinon les deux
  stacks réservent ~24 Go sur 31.
- **Porter les 2 routeurs de contournement** de `config/traefik-dynamic.yml` (D3bis `/medias/…/dwnl`,
  D5 `/medias/…/wms`) — ou vérifier s'ils sont devenus inutiles en 3.4.0, ce qui est en soi une
  information à remonter à l'équipe RUDI.

### Étape 5 — Raccordement nœud source ↔ portail source, et **semis des référentiels**

⚠️ **L'étape que la v1 ignorait, et le premier mur fonctionnel.** Un portail frais a
`kos_data.skos_concept` et `strukture_data.organization` **vides** → toute publication échoue en ERR-303
(thème/licence inconnus du SKOS) et ERR-113 (organisation productrice absente) ✅.

Livrable exigé : un **script de seed idempotent** (`rudi-portal-local/bdd/seed_referentiels.sql`),
extrait de ta base ROOB actuelle qui, elle, est correctement peuplée :

```bash
docker exec rudiplatform-database-1 pg_dump -U rudi -d rudi \
  --data-only -t kos_data.skos_concept -t 'kos_data.skos_concept_*' \
  -t strukture_data.organization -t strukture_data.linked_producer > seed_referentiels.sql
```

Ce script a une double valeur : il rend l'environnement source utilisable, **et il est exactement la
contribution recommandée dans `RAPPORT_BUGS_RUDI.md`** (« découpler les données de référence du dump
applicatif, les livrer comme seed idempotent »). Candidat PR n° 2 vers `rudi-out-of-the-box`.

Ensuite seulement : `rudi-catalog-portal.ini` du nœud source pointant vers le portail source,
enregistrement du `node_provider` (compte ROBOT) en rejouant `PLAN_PORTAIL_RUDI_LOCAL.md` §2 et
`raccordement/declarer_noeud.py` — en gardant à l'esprit que la mécanique peut différer entre 3.3.12 et
3.4.0, et que `declarer_noeud.py` est déjà marqué obsolète depuis la recréation de la base.

### Étape 6 — Pipeline `moissonneuse-batteuse` multi-nœuds — ✅ FAIT (relu et corrigé le 2026-07-22)

> Implémenté, puis relu. Quatre défauts corrigés à la relecture : `publish_rudi.py` n'avait pas été
> repris (un dict non vide est toujours truthy → plus aucun rattrapage possible dès le premier état
> écrit au nouveau format) ; `lire_rudi_publie()` traitait une entrée sans clé comme « hors périmètre »
> alors que 27 entrées de `state.json` sont dans ce cas et étaient rattrapées jusqu'ici ;
> `publier_dataset()` mute la fiche en place et le même dict partait vers les deux nœuds ; le nom de
> nœud auto-généré ignorait le port (deux nœuds sur `localhost` → même nom). Vérifié par simulation sur
> les états réels : 8 publications en attente sur `docker`, **0** vers `source` sans `--retroactif`,
> 370 avec. Spécification d'origine ci-dessous, conservée comme référence.


**Configuration.** Nouveau `src/conf/rudi_nodes.json` : liste d'objets `{"nom", "url", "usr", "pwd",
"principal": bool}`. `charger_conf_rudi()` conserve sa signature actuelle et retourne **le nœud
principal** (compatibilité des 7 appelants ✅ : `cli.py:185`, `dashboard.py:369`, `harvest_auto.py:52`,
`publish_rudi.py` ×3, `enrichir_contacts.py:87`) ; une nouvelle `charger_confs_rudi() -> list[dict]`
alimente la publication. Si `rudi_nodes.json` est absent, on retombe sur `rudi_node.json` traité comme
l'unique nœud `"docker"`.

**Publication.** `connectors/rudi_publish.py::publier_si_configue()` itère sur la liste et retourne un
**dict `{nom_noeud: bool}`** au lieu d'un booléen. Le `threading.Lock()` module-level devient un **dict de
verrous, un par nœud** (la sérialisation reste indispensable : le `get_or_create` d'organisations du nœud
n'est pas idempotent sous concurrence). Un échec sur un nœud n'empêche jamais les autres.

**Sémantique « rien de rétroactif »** — c'est le point de conception à ne pas rater, il découle
directement de la décision 4 :

| Valeur dans l'état | Sens | Comportement de `publish_rudi.py` |
|---|---|---|
| clé du nœud **absente** | jamais tenté, hors périmètre | **ne rattrape pas** |
| `false` | tenté et échoué | rattrape |
| `true` | publié | ne fait rien |

Un booléen hérité est lu comme `{"docker": <valeur>}` — donc les 372 entrées existantes n'ont pas de clé
`"source"` et ne déclenchent aucun rattrapage. Toute **nouvelle** moisson écrit une clé pour **tous** les
nœuds configurés, ce qui fait entrer naturellement les nouveaux JDD dans le périmètre des deux. Prévoir
un `--noeud <nom> --retroactif` explicite pour forcer plus tard si besoin.

**Surface d'impact à traiter** (tout est vérifié ✅) :

- 6 appels à `publier_si_configue` : `main.py:141`, `harvest_batch.py:833`, `harvest_insee.py:323`,
  `harvest_oeb.py:147`, `harvest_bdnb.py:193`, `harvest_geo.py:286`
- écriture dans les 5 fichiers d'état, dont la forme particulière de `state_geo["_rudi_publie"][dossier]`
  (`harvest_geo.py:289`, `publish_rudi.py:105`)
- 3 rattrapages qui **remettent le flag à `false`** : `enrichir_descriptions.py:125`,
  `reanalyser_faux_positifs.py:197`, `backfill_connector_parameters_geo.py:137` → doivent le faire par
  nœud, sans créer de clé pour un nœud hors périmètre
- helpers de lecture/écriture à centraliser dans `src/state.py`, à côté de `construire_index_dossier()`
- `monitor.py` : la colonne `monitor.datasets.rudi_publie` est lue par tes dashboards Superset →
  **la garder telle quelle** (= nœud principal) et ajouter `rudi_publie_source BOOLEAN` *nullable*
  (NULL = hors périmètre) via un `ALTER TABLE … ADD COLUMN IF NOT EXISTS`, sur le modèle de ce qui
  existe déjà l. 360. Sites de `monitor.py` à traiter : l. 468-475 (upsert), 514, 543-581 (5 sources),
  683 (comptage), 1160 (requête `--status`)
- affichage : `cli.py:466-479` et `519`, `src/static/page_dashboard.html:250-252`
- `dashboard.py` : `_etat_noeud()` et `CONTENEUR_RUDI = "rudinode"` sont câblés sur **un** conteneur
  Podman ; le nœud source étant en process natifs, prévoir soit une carte distincte avec des scripts
  start/stop, soit une abstraction « cycle de vie » par nœud. `noeud_pret()` accepte déjà `conf` ✅
- `harvest_auto.py::_demarrer_noeud_rudi()` ne réveille que le nœud Podman
- `publish_rudi.py::menage_rudi_one_shot()`/`menage_organisations()` raisonnent sur **un** nœud :
  décider explicitement s'ils opèrent sur le principal seulement (recommandé — le ménage supprime des
  données, ne pas l'étendre implicitement)
- tests : `tests/` ne couvre pas la publication, mais un test de la **migration douce**
  (booléen hérité → dict, absence de clé ≠ `false`) vaut d'être ajouté

### Étape 7 — Cycle correctif → PR

Fork, branche dédiée, PR selon le `CONTRIBUTING.md` de l'organisation `rudi-platform` (licence EUPL-1.2).
Deux candidates sont déjà mûres :

1. **`rudi-node-storage`** — CORS reflétant l'origine + `Access-Control-Allow-Credentials` au lieu du
   wildcard, et regex OPTIONS `/\/z?download\/:uuid/` corrigée en syntaxe Express. Le correctif existe
   déjà, appliqué dans ton image via `podman commit` ; il suffit de le porter sur le clone ✅.
2. **`rudi-out-of-the-box`** — seed de référentiels idempotent (étape 5).

Les autres (proxy WMS de l'apigateway, `block()` de `HarvestingHelper`, buffer 256 Ko de Kalim,
`connector_parameters` perdus) demandent l'environnement source complet pour être validées.

## Points de vigilance restants

- **Divergence 3.3.12 → 3.4.0** : avant d'investir sur un correctif, vérifier qu'il n'est pas déjà
  corrigé en `main`. Plusieurs bugs du `RAPPORT_BUGS_RUDI.md` ont été constatés sur les images v3.3.12.
- **Migration de schéma** : les microservices gèrent leur DDL (Flyway activé sur kalim, désactivé sur
  konsult ✅) et le dump ROOB est incompatible avec Flyway — d'où sa désactivation chez toi. Ne pas
  restaurer le dump 332 Mo dans la base source : partir des 3 SQL d'init + laisser migrer + semer les
  référentiels (étape 5).
- **Disponibilité du registre `aqmo.org`** : re-tester avant l'étape 2 ; sans lui, `npm ci` du nœud échoue.
- **Version Mongo** de l'image Podman vs conteneur neuf : relever avant de choisir.
- **Le piège Traefik documenté en mémoire** (labels perdus si `acl`/`gateway`/`kos` sont recréés sans
  `docker-compose-network.yml`) s'appliquera **aussi** à la stack source de la couche 3, qui reprend le
  même jeu de composes. Il est constaté 2× déjà — ne pas recréer un service sans le jeu complet.
- **Ne pas casser l'existant** : la stack ROOB porte 383 fiches et sert de démonstrateur. Toute
  manipulation de la couche 3 doit se faire dans un **projet Docker Compose au nom distinct**, jamais en
  modifiant `rudi-out-of-the-box/` en place.

## Vérification

1. **Étape 2** — les 4 process du nœud source répondent sur 4030-4032 ; le nœud Podman continue de
   tourner sans interférence ; publication d'une fiche de test via le manager source.
2. **Étape 3** — un `konsult` natif remplace son conteneur, s'enregistre dans Eureka, et une requête
   `curl http://rudi.localhost/konsult/v1/datasets/metadatas?limit=1` répond comme avant. Modifier une
   chaîne visible dans le source, rebâtir, relancer : le changement apparaît en moins de 3 min.
3. **Étape 5** — après semis, publier un JDD de test depuis le nœud source vers le portail source :
   aucun ERR-303 ni ERR-113, la fiche apparaît au catalogue `konsult`.
4. **Étape 6** — lancer une moisson qui produit un nouveau JDD, et vérifier :
   - il est publié sur **les deux** nœuds, avec `rudi_publie: {"docker": true, "source": true}` ;
   - **aucune** des 372 fiches préexistantes n'a été repoussée vers le nœud source (grep : elles n'ont
     pas de clé `"source"`) ;
   - nœud source arrêté → la publication vers le nœud Docker aboutit quand même, et l'entrée porte
     `{"docker": true, "source": false}`, rattrapée au run suivant ;
   - `python3 -m unittest discover tests/` passe, dashboard et `cli.py` affichent l'état par nœud sans
     régression, et les dashboards Superset continuent d'afficher « Datasets publiés RUDI ».
5. **Étape 7** — la PR n° 1 est ouverte et son correctif est vérifiable sur le nœud source (téléchargement
   d'un média depuis une page servie par une autre origine, sans le contournement Traefik).

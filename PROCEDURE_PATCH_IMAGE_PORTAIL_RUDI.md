# Procédure : patcher une image du portail RUDI (mode hybride) — checklist

> **But.** Modifier le code source d'un microservice du portail RUDI (`rudi-portal-source`), rebâtir
> **son** image, la redéployer **sans casser** le reste de la stack (mode hybride : le microservice
> patché tourne en `:source`, les voisins restent en `:v3.3.12`). À suivre **à chaque** patch d'image
> pour éviter le bazar. Cochez chaque case ; **ne passez pas à l'étape suivante tant que la
> vérification n'est pas verte.**
>
> Cette procédure est née d'un incident multi-agents (24/07/2026) : restarts en ordre dispersé →
> incohérence JWT, catalogue 502, faux diagnostic d'auth. Les pièges ci-dessous sont **réels et vécus**.

---

## 0. Référence environnement (à connaître avant de toucher quoi que ce soit)

| Élément | Valeur |
|---|---|
| Sources portail (build) | `/media/simon/DATA4T/Dev/rudi-portal-source` (monorepo, **3.4.0**, JDK 21) |
| Stack ROOB (compose) | `/media/simon/DATA4T/Dev/rudi-portal-local/rudi-out-of-the-box` |
| Nom projet compose | `rudiplatform` (top-level `name:` dans **chaque** compose → **pas** de `-p`) |
| Jeu de composes | `-f docker-compose-magnolia.yml -f docker-compose-rudi.yml -f docker-compose-dataverse.yml -f docker-compose-network.yml` **+ `--profile "*"`** |
| Version stack | tout en `${rudi_version}=v3.3.12` **sauf kalim = v3.4.0** (codé en dur, intentionnel) |
| Entrée HTTP | Traefik (`rudiplatform-reverse-proxy-1`) → `http://rudi.localhost` (base_dn=localhost) |
| Front | `rudiplatform-portail-1` = **nginx** servant `/usr/share/nginx/html` (dist Angular bakée) |
| Routeur file Traefik | `rudi-out-of-the-box/config/traefik-dynamic.yml` (file provider, hot-reload) |
| Cibles Dockerfile | `--target rudi-microservice-<nom>` (un par service) et `--target rudi-application-front-office` (front) |

**Commande de recréation type** (jamais `down` → perte des volumes PG non persistés) :
```bash
cd /media/simon/DATA4T/Dev/rudi-portal-local/rudi-out-of-the-box
docker compose -f docker-compose-magnolia.yml -f docker-compose-rudi.yml \
  -f docker-compose-dataverse.yml -f docker-compose-network.yml \
  [-f docker-compose-source.yml] --profile "*" \
  up -d --force-recreate --no-deps <service...>
```

---

## 1. Phase 0 — Baseline santé (AVANT toute modif)

Snapshot de l'état sain, pour savoir vers quoi revenir et détecter ce qu'on casse.

- [ ] **Conteneurs** : `docker ps --format '{{.Names}}\t{{.Status}}' | grep rudiplatform` → tous *Up*.
- [ ] **Catalogue** : `curl -s -o /dev/null -w "%{http_code}" 'http://rudi.localhost/konsult/v1/datasets/metadatas?limit=1'` → **200**.
- [ ] **Front** : `curl -s -o /dev/null -w "%{http_code}" http://rudi.localhost/` → **200**.
- [ ] **Auth anonyme** (⚠️ **POST**, pas GET) :
      `curl -s -D - -o /dev/null -X POST -H "Content-Type: application/json" -d '{}' http://rudi.localhost/anonymous | grep -i '^HTTP\|^Authorization'`
      → **200** + en-tête `Authorization: Bearer …`.
- [ ] **Pas d'incohérence JWT** : `docker logs --since 5m rudiplatform-acl-1 | grep -c "Invalid signature"` → **0**.
- [ ] **Labels Traefik** : `docker exec rudiplatform-reverse-proxy-1 wget -qO- http://localhost:8080/api/http/routers | grep -oE '"name":"[^"]+@docker"' | sort -u` → présence de `acl/apigateway/gateway/konsult/portail@docker`.
- [ ] **`extra_hosts` apigateway (silencieux, aucun des checks ci-dessus ne le détecte)** :
      `docker inspect rudiplatform-apigateway-1 --format '{{.HostConfig.ExtraHosts}}'` →
      `[host.docker.internal:host-gateway]`. Si vide : le tableau/la carte des JDD (médias FILE,
      servis via `host.docker.internal:4031`) sont cassés, alors que front/catalogue/anonyme
      restent tous **200** — voir piège 14.

Si un point est rouge **avant** de commencer, régler d'abord (voir §6 Dépannage).

---

## 2. Phase 1 — Patcher la source (discipline de branche)

- [ ] Une branche `fix/<sujet>` par problème (cf. `feedback-pr-atomiques` / `PLAN_PR_RUDI_TABLEAU_CARTE.md`).
- [ ] Pour un déploiement combinant plusieurs correctifs : créer une branche d'intégration
      (`integration/<lot>`) et y **merger** les `fix/*` (fichiers disjoints = merge propre).
- [ ] `git -C /media/simon/DATA4T/Dev/rudi-portal-source branch --show-current` → confirme la bonne branche.
- [ ] **Repo partagé** : ce working tree peut être utilisé par d'autres tâches. Vérifier qu'on ne
      marche pas sur une branche/checkout en cours d'utilisation ailleurs avant de switcher.

---

## 3. Phase 2 — Builder le jar (ou la dist front)

Le `Dockerfile` fait un `ADD` du jar depuis `target/` → **il faut mvn install AVANT le docker build**.

**Microservice Java :**
- [ ] `cd /media/simon/DATA4T/Dev/rudi-portal-source`
- [ ] `mvn -q -DskipTests -pl <chemin-module-facade>[,<autre-module>] -am install`
      (ex. `rudi-microservice/rudi-microservice-apigateway/rudi-microservice-apigateway-facade` ; `-am`
      rebâtit les dépendances patchées, ex. `rudi-facet-kaccess`).
- [ ] **Vérifier l'artefact** (pas seulement l'exit code) :
      `ls -la <module>/target/*-facade.jar` existe **et** mtime récent.
      ⚠️ Les `[ERROR] MavenReportException: javadoc` sont **non fatals** (failOnError=false) tant que le
      jar est produit et que le reactor va au bout.

**Front Angular (⚠️ piège Node) :**
- [ ] Builder avec le **Node épinglé v20.15.1**, PAS le Node système (nvm v24 → `ng build` échoue sur
      `@ampproject/remapping`). Voir `reference-portail-front-build-node` :
      ```bash
      export PATH="/media/simon/DATA4T/Dev/rudi-portal-source/rudi-application/rudi-application-front-office/node_installation/node:$PATH"
      node -v   # DOIT afficher v20.15.1
      cd .../rudi-application-front-office/angular-project && npm run build-prod
      ```
- [ ] **Vérifier `dist/angular-project/` existe** (mtime récent) — `npm run` peut rapporter exit 0 sans dist.
- [ ] Pour l'image : la cible Dockerfile `rudi-application-front-office` prend
      `rudi-application-front-office/target/rudi-application-front-office-angular-dist.zip` (produit par le
      build Maven du module `rudi-application`), pas le `dist/` du build manuel — builder via Maven **ou**
      ajuster. (Test rapide sans image : `docker cp dist/angular-project/. rudiplatform-portail-1:/usr/share/nginx/html/` + hard-refresh — éphémère, sert de rollback.)

---

## 4. Phase 3 — Builder l'image

- [ ] `cd /media/simon/DATA4T/Dev/rudi-portal-source`
- [ ] `docker build --target rudi-microservice-<nom> -t rudiplatform/rudi-microservice-<nom>:source .`
      (le build peut dépasser 2 min : base apt + ADD d'un jar ~150 Mo → **lancer en arrière-plan**).
- [ ] **Vérifier l'image** : `docker images | grep '<nom>:source'` → présente, taille plausible, récente.

---

## 5. Phase 4 — Déployer (recréer LE conteneur)

- [ ] **Override d'image** dans `rudi-out-of-the-box/docker-compose-source.yml` — n'activer QUE les
      services dont l'image `:source` **existe déjà** (une entrée pointant vers une image inexistante
      casse le service au recreate) :
      ```yaml
      name: rudiplatform
      services:
        apigateway:
          image: rudiplatform/rudi-microservice-apigateway:source
      ```
- [ ] Recréer **ce** service avec le **jeu complet** de composes + l'override + `--profile "*"` :
      ```bash
      cd /media/simon/DATA4T/Dev/rudi-portal-local/rudi-out-of-the-box
      docker compose -f docker-compose-magnolia.yml -f docker-compose-rudi.yml \
        -f docker-compose-dataverse.yml -f docker-compose-network.yml \
        -f docker-compose-source.yml --profile "*" \
        up -d --no-deps --force-recreate <service>
      ```
- [ ] ⚠️ **Ne jamais** recréer avec un jeu de composes **incomplet** : les labels Traefik d'`apigateway`
      et `portail` (dont le rewrite `^/medias/(.*) → /apigateway/datasets/$1`) sont dans
      **`docker-compose-network.yml`** ; les perdre = « not authorized » / routes mortes.
- [ ] **Vérifier labels après recreate** : `docker exec rudiplatform-reverse-proxy-1 wget -qO-
      http://localhost:8080/api/http/routers | grep '<service>@docker'` → présent.
- [ ] **Attendre le boot** : `docker logs --since 3m rudiplatform-<service>-1 | grep "Started .*Application"`.
      Boot ~40 s ; **~130 s si `registry` (Eureka) a été recréé** (les services attendent Eureka).

---

## 6. Phase 5 — Valider fonctionnellement

Refaire **toute la checklist Phase 0** (§1), puis les tests spécifiques au correctif, ex. :
- [ ] **D3 (téléchargement média / tableau)** : `curl -i 'http://rudi.localhost/medias/{gid}/{mid}/dwnl'`
      → **200 + données** (plus de 302 `/login`).
- [ ] **Carte / WMS** : onglet Carte rendu ; `/medias/{gid}/{mid}/wms?...GetMap...` → 200 + PNG.
- [ ] **Aucune régression** : catalogue 200, `POST /anonymous` 200, `Invalid signature` = 0.

---

## 7. Pièges vérifiés (mémoire de l'incident) — à ne pas réapprendre

1. **Auth anonyme = `POST /anonymous`, jamais GET.** Un `GET /anonymous` renvoie **401** (`Null
   authentification`) — c'est normal, pas une panne. Le token revient dans les **en-têtes de réponse**
   (`Authorization` / `X-Token`) du **POST**. Ne pas diagnostiquer une panne d'auth sur un GET.
2. **`Invalid signature` (acl) = incohérence JWT inter-services** après restarts en ordre dispersé /
   skew de versions. **Fix : recreate coordonnée de TOUT le tier applicatif** en une fois
   (`registry acl gateway apigateway strukture konsult kos konsent projekt selfdata portail`), pas un
   restart de service isolé. Après recreate propre : 0 `Invalid signature`.
3. **Éditer un fichier config bind-monté** (ex. `traefik-dynamic.yml`, mounté dans le reverse-proxy) :
   une écriture par **rename atomique** (éditeurs, outils Write) **remplace l'inode** → le conteneur
   garde l'**ancien** inode et ne voit **pas** le changement. **Vérifier** avec
   `docker exec <ctn> cat <chemin-interne>` ; si l'ancien contenu persiste, **restart du conteneur
   consommateur** pour re-binder (`docker restart rudiplatform-reverse-proxy-1`).
4. **`--profile "*"` obligatoire** : sans lui, `solr` est filtré et `dataverse` le référence →
   `invalid compose project`.
5. **`konsult-natif@file`** (routeur Traefik priorité 200 → `https://host.docker.internal:8443`) est un
   reliquat du mode **konsult natif** (hybride). Si le natif est mort → `/konsult` en **502** alors que
   `konsult@docker` est sain. Désactiver le routeur (dans `traefik-dynamic.yml`, puis restart
   reverse-proxy — cf. piège 3) **ou** relancer le konsult natif.
6. **Front = nginx + dist bakée.** URLs API **origin-relatives** (`/konsult/v1`, `/anonymous`,
   `/medias/…`), donc un rebuild du même câblage ne change pas la config. Test rapide par `docker cp`
   du `dist/` dans `rudiplatform-portail-1:/usr/share/nginx/html/`.
7. **Jamais `docker compose down`** : les volumes PostgreSQL ne sont pas persistés (ROOB) → perte des
   BDD/comptes/référentiels. Utiliser `up -d --force-recreate --no-deps <service>`.
8. **Recreate de `registry` (Eureka)** = boot ~130 s des dépendants (ils attendent Eureka). Patienter
   avant de conclure à une panne (502 transitoires normaux pendant ce temps).
9. **Skew de versions assumé** : kalim `v3.4.0` parmi des voisins `v3.3.12` (intentionnel) ; un konsult
   natif 3.4.0 peut coexister en hybride. En tenir compte avant d'incriminer une version.
10. **Repo `rudi-portal-source` partagé** entre tâches/agents : un `git checkout` peut tirer le tapis
    sous une autre tâche. Vérifier la branche avant de switcher.
11. **Moniteur de build qui « ne finit jamais ».** Un `pgrep -f "docker build …"` dans un job de
    surveillance **matche sa propre ligne de commande** → il s'attend lui-même à l'infini (faux « build
    en cours depuis 22 min » alors que l'image est prête depuis longtemps). **Ne jamais** détecter la fin
    d'un build par un `pgrep` sur le nom de la commande. Détecter par l'**artefact** :
    `docker images | grep ':source'` (image présente + `CreatedSince` récent), ou un marqueur unique
    écrit en fin de commande. Idem `mvn` : vérifier le jar, pas le process. Le **legacy builder** Docker
    (sans buildx) est lent (~5-7 min ici pour apt base + ADD 150 Mo) : c'est normal, mais ça n'excuse pas
    un faux « en cours ».
12b. **`localhost` dans un conteneur ≠ l'hôte.** Un microservice conteneurisé (apigateway…) qui doit
    joindre un service **natif tournant sur l'hôte** (ex. storage du nœud source sur `:4031`) ne peut
    PAS utiliser `localhost:<port>` (= le conteneur lui-même) : utiliser **`host.docker.internal:<port>`**
    + `extra_hosts: "host.docker.internal:host-gateway"` sur le service. Symptôme typique : média/route
    « injoignable » → 302/`/login` sans erreur claire. Vérifier la joignabilité depuis le conteneur
    (`docker exec <ctn> wget -qS http://host.docker.internal:<port>/...`) avant d'incriminer le code.
    Côté données : les URL stockées (ex. `apigateway_data.api.url`) doivent elles aussi être en
    `host.docker.internal` (cf. `project-portail-media-serving`).
13. **Image déployée ≠ correctif actif.** Un conteneur *Up* sur `:source` ne prouve **pas** que le
    correctif fonctionne. **Toujours** exécuter le test fonctionnel qui exerce précisément le fix
    (ex. D3 : `/medias/{gid}/{mid}/dwnl` ne doit plus renvoyer `302 /login?error`). Si le test échoue
    malgré l'image `:source` : vérifier (a) que le jar a bien été rebâti depuis la **bonne branche**
    (`mvn -pl … -am` sur la branche d'intégration, jar mtime postérieur au patch) ; (b) que la **route
    réellement empruntée** passe bien par le microservice patché (labels Traefik + réécriture de chemin,
    ex. `/medias/(.*) → /apigateway/datasets/$1` dans `docker-compose-network.yml`) ; (c) les **logs du
    microservice** pour la requête (chemin reçu, décision sécurité) — un `302 /login` = `formLogin` sur
    une route non couverte par le permit-all.
14. **Relever la stack COMPLÈTE (`docker compose … up -d` sans `-f docker-compose-source.yml`)
    supprime silencieusement l'`extra_hosts` d'`apigateway`.** Vécu le 2026-07-27 : un `up -d` avec
    seulement les 4 fichiers de base (après un stack éteint/repartant de zéro) recrée `apigateway`
    **sans** l'override du piège 12b — `host.docker.internal` ne résout plus rien d'utile dans ce
    conteneur, donc le tableau **et** la carte de tous les JDD géo cassent, alors que Phase 0 classique
    (catalogue/front/anonyme/JWT/labels) reste **entièrement verte** : ces 5 checks ne touchent jamais
    la voie média. **Symptôme** : `/medias/{gid}/{mid}/dwnl` → 502 (ou timeout) au lieu de 200.
    **Fix** : `docker inspect rudiplatform-apigateway-1 --format '{{.HostConfig.ExtraHosts}}'` doit
    lister `host.docker.internal:host-gateway` ; sinon recréer **avec** `-f docker-compose-source.yml`
    en plus du jeu complet (`up -d --no-deps --force-recreate apigateway`), puis re-tester une route
    média réelle (pas seulement les 5 checks Phase 0). **Règle** : dès que la stack ROOB est relevée
    en partant de zéro (pas un simple `stop`/`start`), inclure `-f docker-compose-source.yml` dans la
    commande `up -d` même si aucune image `:source` n'est activée dedans — les overrides `extra_hosts`
    qu'il porte (indépendants de toute image `:source`) doivent survivre au recreate.

---

## 8. Rollback

- Image : remettre `image: …:v3.3.12` (retirer/commenter l'entrée dans `docker-compose-source.yml`) puis
  `up -d --force-recreate --no-deps <service>`.
- Front (test `docker cp`) : `docker restart rudiplatform-portail-1` restaure la dist bakée d'origine.
- Config bind-montée : restaurer le fichier + restart du conteneur consommateur.

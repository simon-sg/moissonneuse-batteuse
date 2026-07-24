# Remise au propre des contournements « vue Tableau + vue Carte » RUDI — Guide d'implémentation

> **À lire en entier avant de toucher quoi que ce soit.** Ce document est un plan d'exécution
> détaillé. Il transforme les contournements bricolés (relais Traefik, proxy WMS Python, patches CORS,
> `connector_parameters` factices) en **correctifs propres du code source**, sous forme de **PR
> atomiques (1 PR = 1 seul problème)**, puis **retire les contournements devenus inutiles**.
>
> **Règles impératives pour l'agent qui implémente :**
> 1. **Une PR à la fois.** Ne jamais mélanger deux correctifs dans une même branche/commit.
> 2. **Toujours `Read` le fichier avant de l'éditer** — les numéros de ligne ci-dessous peuvent avoir
>    bougé. Repère le code par son **contenu**, pas par son numéro de ligne.
> 3. **Ne rien inventer.** Si un fichier/chemin/commande ne correspond pas à ce qui est décrit,
>    **arrête-toi et signale-le** au lieu de deviner.
> 4. **Ne pas retirer un contournement tant que son correctif n'est pas déployé ET validé** (section 8).
> 5. **Ne jamais toucher** : le routeur Traefik `konsult-natif` (infra du mode hybride, pas un bug) ni
>    les `connector_parameters` **réels** des médias SERVICE WMS/WFS (métadonnée légitime, dite « W6 »).
> 6. Après chaque étape, exécuter la validation associée (section 9) **avant** de passer à la suivante.

---

## 1. Contexte

Sur le portail RUDI local (ROOB), les onglets **« Données tabulaires »** et **« Carte »** de la page
détail d'un jeu de données (JDD) ne fonctionnaient pas. Ils ont été rendus fonctionnels par une pile de
contournements, faute d'accès au code amont. On a désormais les sources :

- **Portail** : `/media/simon/DATA4T/Dev/rudi-portal-source` (monorepo `rudi-platform/rudi-portal`, **3.4.0**,
  JDK 21 / Spring Boot 3.5.7, front Angular inclus).
- **Nœud producteur** : `/media/simon/DATA4T/Dev/rudi-node-build/rudi-node-container/src/` (sous-modules
  `rudi-storage`, `rudi-catalog`… branche `irisa`).
- **Pipeline** : `/media/simon/DATA4T/Dev/moissonneuse-batteuse` (ce repo).

Les bugs sont documentés en détail dans `/media/simon/DATA4T/Dev/rudi-portal-local/RAPPORT_BUGS_RUDI.md`
(bugs **D3** tableau/téléchargement, **D4** carte, **D5** proxy WMS, **A6** intégration géo). Le présent
guide en dérive les correctifs.

**Objectif** : ouvrir plusieurs PR séparées et atomiques vers `rudi-platform`, poussables au fil de
l'eau, puis nettoyer les contournements.

---

## 2. Emplacements

| Rôle | Chemin |
|---|---|
| Portail source (monorepo Java + Angular) | `/media/simon/DATA4T/Dev/rudi-portal-source` |
| Nœud source (sous-modules Node) | `/media/simon/DATA4T/Dev/rudi-node-build/rudi-node-container/src/` |
| Pipeline moissonneuse-batteuse | `/media/simon/DATA4T/Dev/moissonneuse-batteuse` |
| Contournements Traefik + shim WMS | `/media/simon/DATA4T/Dev/rudi-portal-local/rudi-out-of-the-box/config/traefik-dynamic.yml` et `/media/simon/DATA4T/Dev/rudi-portal-local/shim/` |
| Rapport de bugs (référence) | `/media/simon/DATA4T/Dev/rudi-portal-local/RAPPORT_BUGS_RUDI.md` |
| Stack ROOB (Docker) | `/media/simon/DATA4T/Dev/rudi-portal-local/rudi-out-of-the-box/` |

---

## 3. Inventaire des contournements (état actuel)

| Réf | Contournement | Bug | Emplacement | Sort |
|---|---|---|---|---|
| **W1** | Patch CORS nœud (reflète Origin + `Allow-Credentials`) | D3 | `rudi-storage/src/httpService.js` (branche `fix/cors-credentials-reflection`, committé) | → PR-1 (déjà écrit) |
| **W2** | Routeur Traefik `medias-dwnl` (réécrit `/medias/…/dwnl` → storage, strip auth) | D3 | `traefik-dynamic.yml` | Retirer après PR-1 **+** PR-3 |
| **W3** | Routeur Traefik `medias-wms` + proxy `wms_proxy.py` (port 3034) | D5 | `traefik-dynamic.yml` + `shim/` | Retirer après PR-4 **+** PR-5 (si validé) |
| **W4** | `connector_parameters` factices sur médias **FILE** + entrées dwnl | D4 | `datagouv_to_rudi.py` + tests + `backfill_connector_parameters_geo.py` | Retirer après PR-6 **+** PR-7 |
| **W5** | Ré-injection `connector_parameters` après upload | D6 (bug maison, déjà corrigé) | `rudi_node.py` | Simplifier après W4 |
| **W6** | `connector_parameters` **réels** sur médias SERVICE WMS/WFS | A6 | `datagouv_to_rudi.py` | **Légitime — NE PAS retirer** |

---

## 4. Comment déployer et valider un correctif portail (mode hybride)

Décidé avec Simon : on ne relance pas tout le portail natif. Pour **un** microservice patché, on
rebâtit **uniquement son image** et on recrée **ce seul conteneur** ; les autres restent sur les images
publiées `:v3.3.12`.

**Recette générique (à confirmer au premier run) :**

```bash
cd /media/simon/DATA4T/Dev/rudi-portal-source
# 1. Rebâtir le jar du module patché (ex. apigateway) :
mvn -q -pl rudi-microservice/rudi-microservice-apigateway/rudi-microservice-apigateway-facade -am install -DskipTests
# 2. Rebâtir l'image de CE microservice (une cible par service dans le Dockerfile racine) :
docker build -f Dockerfile --target rudi-microservice-apigateway -t rudiplatform/rudi-microservice-apigateway:source .
# 3. Dans le compose ROOB, pointer l'image de ce service sur :source, puis recréer CE conteneur.
```

> **⚠️ Piège Traefik (vu 2×, cf. `RAPPORT_BUGS_RUDI.md` / mémoire).** Recréer un conteneur avec un jeu
> de composes **incomplet** lui fait perdre ses labels Traefik → routeur absent → « not authorized »
> partout. **Toujours** recréer avec le **jeu complet** de fichiers compose (commande exacte dans
> `/media/simon/DATA4T/Dev/rudi-portal-local/README.md`, section « Démarrer le portail »). Le front
> Angular est un microservice comme un autre : même recette (`--target` du service front).

Pour le **front Angular** en développement, `ng serve` avec le `proxy.conf.json` est plus rapide que
rebâtir l'image, mais la validation finale doit se faire sur l'image reconstruite.

**Nœud** : pas d'image à rebâtir — les 4 process Node tournent en natif (`nodemon`). Un correctif nœud
= un commit sur le sous-module, puis relance du process concerné.

**Avant chaque PR portail** : vérifier que le bug **subsiste bien sur la source 3.4.0** (le clone est en
3.4.0, les images ROOB en v3.3.12 — certains bugs peuvent déjà être corrigés en amont). Si le code
amont est déjà corrigé, ne pas ouvrir la PR : le noter et passer au contournement à retirer.

**Messages de commit** : conventionnels (`fix(scope): …`), en anglais, **sans** trailer d'IA
(contributions upstream signées par Simon). Une PR = un commit logique.

---

## 5. Les PR (atomiques)

### PR-1 — nœud storage : CORS reflète l'Origin + Allow-Credentials (bug D3)

- **Dépôt** : `rudi-platform/rudi-node-storage`.
- **Fichier** : `rudi-node-build/rudi-node-container/src/rudi-storage/src/httpService.js`.
- **Statut** : **déjà commité** — commit `cf374b3 fix(storage): reflect Origin + Allow-Credentials in
  CORS headers` sur la branche `fix/cors-credentials-reflection`. Le code reflète `req.headers.origin ||
  '*'` + `Vary: Origin` + `Access-Control-Allow-Credentials: true` dans `optionCors()`, `postFile()`,
  `sendFile`, `sendAndClose()`.
- **⚠️ Problème à corriger AVANT la PR** : cette branche contient **aussi** un second commit
  `8c7670c fix: max file size` — un problème **distinct**. Règle « 1 PR = 1 problème » → **scinder** :
  ```bash
  cd /media/simon/DATA4T/Dev/rudi-node-build/rudi-node-container/src/rudi-storage
  # Branche propre ne contenant QUE le commit CORS, depuis la base irisa :
  git checkout -b pr/cors-credentials-reflection irisa
  git cherry-pick cf374b3
  # (le commit max-file-size 8c7670c ira dans sa propre branche → PR-2bis)
  ```
- **Action PR** : fork `rudi-platform/rudi-node-storage`, pousser `pr/cors-credentials-reflection`,
  ouvrir la PR. Argumentaire : `withCredentials:true` (forcé par le portail) est incompatible avec
  `Access-Control-Allow-Origin: '*'` ; une config `cors()` à liste blanche existait déjà en commentaire.
- **Retire** : rien seul (W2 tombe avec **PR-1 + PR-3**).

### PR-2bis — nœud storage : max file size (hors périmètre, mais prêt)

- **Dépôt** : `rudi-platform/rudi-node-storage`. **Fichier** : même. Commit `8c7670c`.
- Problème indépendant de la visualisation. Branche dédiée (`git checkout -b pr/max-file-size irisa;
  git cherry-pick 8c7670c`), PR séparée. Priorité basse.

### PR-3 — apigateway : autoriser la route de téléchargement média en accès public (bug D3)

- **Dépôt** : `rudi-platform/rudi-portal`. **Module** : `rudi-microservice-apigateway`.
- **Fichier** : `rudi-portal-source/rudi-microservice/rudi-microservice-apigateway/rudi-microservice-apigateway-facade/src/main/java/org/rudi/microservice/apigateway/facade/config/security/SecurityConstants.java`
- **Problème** : `/apigateway/datasets/{gid}/{mid}/dwnl` renvoie 302 `/login` (authentifié ou non) car
  `/apigateway/datasets/**` n'est **pas** dans `SB_PERMIT_ALL_URL` → tombe sur
  `anyExchange().authenticated()` (`WebSecurityConfig.java:103`). Or `SecurityConstants` déclare déjà
  `SB_INCLUDE_URLS` avec `\\/apigateway\\/datasets\\/.*` pour le filtre anonyme — l'accès public **était
  prévu**, seule la permit-all n'a pas suivi.
- **Changement — AVANT :**
  ```java
  	protected static final String[] SB_PERMIT_ALL_URL = {
  			// URL public
  			"/apigateway/v1/application-information", "/apigateway/v1/healthCheck", "/apigateway/v1/encryption-key",
  			// OAuth2
  			"/oauth/**",
  			// swagger ui / openapi
  			"favicon.ico", "/apigateway/v3/api-docs/**", "/apigateway/swagger-ui/**", "/apigateway/swagger-ui.html",
  			"/apigateway/swagger-resources/**", "/apigateway/webjars/**",
  			// configuration ?
  			"/configuration/ui", "/configuration/security" };
  ```
  **APRÈS** (ajouter une entrée, garder tout le reste) :
  ```java
  	protected static final String[] SB_PERMIT_ALL_URL = {
  			// URL public
  			"/apigateway/v1/application-information", "/apigateway/v1/healthCheck", "/apigateway/v1/encryption-key",
  			// Téléchargement de média (données ouvertes) : accès public, cohérent avec SB_INCLUDE_URLS
  			"/apigateway/datasets/**",
  			// OAuth2
  			"/oauth/**",
  			// swagger ui / openapi
  			"favicon.ico", "/apigateway/v3/api-docs/**", "/apigateway/swagger-ui/**", "/apigateway/swagger-ui.html",
  			"/apigateway/swagger-resources/**", "/apigateway/webjars/**",
  			// configuration ?
  			"/configuration/ui", "/configuration/security" };
  ```
  > Si on veut être plus restrictif que `/**`, remplacer par `"/apigateway/datasets/**/dwnl"` — mais
  > `SB_INCLUDE_URLS` (filtre anonyme) utilise déjà `\\/apigateway\\/datasets\\/.*`, donc `/**` est cohérent.
- **Déploiement** : recette §4 pour `rudi-microservice-apigateway`.
- **Retire (avec PR-1)** : le routeur Traefik `medias-dwnl` (**W2**).
- **Commit** : `fix(apigateway): permit public access to dataset media download route`.

### PR-4 — apigateway : dédoublonner la fusion de query du reroutage (bug D5, cause 1)

- **Dépôt** : `rudi-platform/rudi-portal`. **Module** : `rudi-microservice-apigateway`.
- **Fichier** : `.../rudi-microservice-apigateway-facade/src/main/java/org/rudi/microservice/apigateway/facade/config/gateway/filters/RerouteToRequestUrlFilter.java`
- **Problème** : `filter()` concatène les params de `connector.url` (`oQuery`) et ceux de la requête
  entrante (`iQuery`) avec `&` **sans dédoublonner** → `SERVICE`/`REQUEST`/`VERSION` en double sur le
  GetMap envoyé au serveur WMS externe.
- **Changement — AVANT** (méthode `filter`, autour de la construction de `mergedUrl`) :
  ```java
  		// on récupère les paramètres issus de l'url déclarée
  		String oQuery = prepareRawQuery(routeUri);
  		log.debug("RerouteToRequestUrlFilter with {} and {}", oQuery, uri.getQuery());

  		// on récupère les paramètres issus de l'appel
  		String iQuery = prepareQueryParam(exchange);

  		checkParameters(exchange, uri);

  		URI mergedUrl = UriComponentsBuilder.fromUri(uri).scheme(routeUri.getScheme()).host(routeUri.getHost())
  				.port(routeUri.getPort()).path(routeUri.getPath()).query(StringUtils.join(List.of(oQuery, iQuery), '&'))
  				.build(encoded).toUri();
  ```
  **APRÈS** — fusionner par clé, la requête entrante l'emporte (même logique que `wms_proxy.py::_fusionner_query`) :
  ```java
  		// on récupère les paramètres issus de l'url déclarée
  		String oQuery = prepareRawQuery(routeUri);
  		log.debug("RerouteToRequestUrlFilter with {} and {}", oQuery, uri.getQuery());

  		// on récupère les paramètres issus de l'appel
  		String iQuery = prepareQueryParam(exchange);

  		checkParameters(exchange, uri);

  		// Fusion dédoublonnée par clé (insensible à la casse) : les params de la requête entrante
  		// (GetMap) l'emportent sur ceux stockés dans l'url du connecteur (GetCapabilities), sinon
  		// SERVICE/REQUEST/VERSION apparaissent en double dans l'url sortante vers le serveur externe.
  		String mergedQuery = mergeQueryParams(oQuery, iQuery);

  		URI mergedUrl = UriComponentsBuilder.fromUri(uri).scheme(routeUri.getScheme()).host(routeUri.getHost())
  				.port(routeUri.getPort()).path(routeUri.getPath()).query(mergedQuery)
  				.build(encoded).toUri();
  ```
  **et ajouter la méthode** (au même niveau que `prepareQueryParam`, en réutilisant `java.util.LinkedHashMap`) :
  ```java
  	/**
  	 * Fusionne deux query strings « k=v&k2=v2 » en dédoublonnant par clé (insensible à la casse).
  	 * Les entrées de {@code incoming} écrasent celles de {@code base} à clé égale. L'ordre est stable.
  	 */
  	protected String mergeQueryParams(String base, String incoming) {
  		java.util.Map<String, String> merged = new java.util.LinkedHashMap<>();
  		for (String part : StringUtils.split(StringUtils.defaultString(base), '&')) {
  			int eq = part.indexOf('=');
  			String key = eq >= 0 ? part.substring(0, eq) : part;
  			merged.put(key.toLowerCase(), part);
  		}
  		for (String part : StringUtils.split(StringUtils.defaultString(incoming), '&')) {
  			int eq = part.indexOf('=');
  			String key = eq >= 0 ? part.substring(0, eq) : part;
  			merged.put(key.toLowerCase(), part);
  		}
  		return StringUtils.join(merged.values(), '&');
  	}
  ```
  > `StringUtils` est déjà importé (`org.apache.commons.lang3.StringUtils`). Vérifier au `Read` que
  > `split`/`defaultString`/`join` sont bien ceux de commons-lang3 (ils le sont).
- **Déploiement** : recette §4 pour `rudi-microservice-apigateway`.
- **Nuance à écrire dans la PR** : le `RAPPORT_BUGS_RUDI.md` note qu'une url dupliquée renvoie quand
  même 200 en test manuel — **ce correctif est nécessaire mais peut ne pas suffire** à lui seul à
  éliminer le 500 en requête unique. Voir aussi PR-5.
- **Retire (avec PR-5, si validé)** : W3.
- **Commit** : `fix(apigateway): deduplicate query params when rerouting to connector URL`.

### PR-5 — facet-kaccess : rendre thread-safe le cache d'instances vides (bug D5, cause 2 / CME)

- **Dépôt** : `rudi-platform/rudi-portal`. **Module** : `rudi-facet/rudi-facet-kaccess`.
- **Fichier** : `rudi-portal-source/rudi-facet/rudi-facet-kaccess/src/main/java/org/rudi/facet/kaccess/helper/dataset/metadatablock/mapper/fields/ObjectsUtils.java`
- **Problème** : `EMPTY_INSTANCES` est un `HashMap` statique muté par `computeIfAbsent` **sans
  synchronisation**. Sous rafale de tuiles WMS parallèles (OpenLayers/Leaflet), `ConcurrentModificationException`.
- **Changement — AVANT :**
  ```java
  import java.lang.reflect.InvocationTargetException;
  import java.util.HashMap;
  import java.util.Map;
  ...
  	private static final Map<Class<?>, Object> EMPTY_INSTANCES = new HashMap<>();
  ```
  **APRÈS :**
  ```java
  import java.lang.reflect.InvocationTargetException;
  import java.util.Map;
  import java.util.concurrent.ConcurrentHashMap;
  ...
  	private static final Map<Class<?>, Object> EMPTY_INSTANCES = new ConcurrentHashMap<>();
  ```
  > Retirer l'import `java.util.HashMap` (devient inutilisé), ajouter `java.util.concurrent.ConcurrentHashMap`.
  > `computeIfAbsent` (ligne suivante) reste inchangé, il est thread-safe sur `ConcurrentHashMap`.
- **Déploiement** : `rudi-facet-kaccess` est une dépendance d'apigateway (et d'autres) → rebâtir l'image
  du/des service(s) qui l'embarquent (au minimum `rudi-microservice-apigateway`).
- **Nuance PR** : correctif de robustesse sous charge ; comme PR-4, ne garantit pas seul la disparition
  du 500 en requête unique (cause exacte non confirmée sans capture réseau — le noter).
- **Retire (avec PR-4, si validé)** : W3.
- **Commit** : `fix(kaccess): use ConcurrentHashMap for shared empty-instance cache`.

### PR-6 — front : baser la garde carte sur le média réellement affiché (bug D4, défaut a)

- **Dépôt** : `rudi-platform/rudi-portal`. **Module** : front Angular
  `rudi-application/rudi-application-front-office/angular-project/`.
- **Fichier** : `.../angular-project/src/app/features/data-set/pages/detail/detail.component.ts`
- **Problème** : `handleMetadataProperties()` valide `available_formats[0].connector.connector_parameters`
  (le **premier** média, quel que soit son type) et exige 4 clés — alors que le média rendu est
  `this.mediaToDisplayMap` (positionné par le getter `isMapDisplayed`), qui peut être un **FILE GeoJSON**
  sans `connector_parameters`. Résultat : `mapHasError=true` à tort, carte bloquée.
- **Changement — AVANT :**
  ```ts
      private handleMetadataProperties(metadata: Metadata): void {
          if (this.isMapDisplayed) {
              const connectorParameters: ConnectorConnectorParameters[] = metadata.available_formats[0].connector.connector_parameters;
              if (connectorParameters) {
                  this.mapHasError = !this.hasAllRequiredKeys(connectorParameters, MAP_CONNECTOR_PARAMETERS_REQUIRED);
              } else {
                  this.mapHasError = true;
              }
          }
      }
  ```
  **APRÈS :**
  ```ts
      private handleMetadataProperties(metadata: Metadata): void {
          if (this.isMapDisplayed) {
              // this.mediaToDisplayMap est positionné par le getter isMapDisplayed : c'est le média
              // RÉELLEMENT rendu sur la carte, pas available_formats[0].
              const media = this.mediaToDisplayMap;
              const objet = media as MediaFile;

              // Un GeoJSON téléchargé (média FILE) se rend directement depuis connector.url : il n'a
              // jamais de connector_parameters et ne doit pas bloquer la carte.
              if (objet.file_type === FileTypes.GEO_JSON) {
                  this.mapHasError = false;
                  return;
              }

              // Média SERVICE (WMS/WFS/WMTS…) : les 4 clés sont réellement nécessaires pour construire
              // la requête GetMap/GetFeature.
              const connectorParameters: ConnectorConnectorParameters[] = media.connector.connector_parameters;
              this.mapHasError = !connectorParameters
                  || !this.hasAllRequiredKeys(connectorParameters, MAP_CONNECTOR_PARAMETERS_REQUIRED);
          }
      }
  ```
  > `MediaFile`, `FileTypes`, `MAP_CONNECTOR_PARAMETERS_REQUIRED`, `ConnectorConnectorParameters` sont
  > **déjà** utilisés/importés dans ce fichier (cf. getter `isMapDisplayed` et l'ancien code). Vérifier
  > au `Read` qu'aucun nouvel import n'est requis.
- **Déploiement** : rebâtir l'image du front (ou `ng serve` pour tester vite).
- **Retire** : le placeholder `connector_parameters` sur FILE côté pipeline (**W4**, section 8).
- **Commit** : `fix(front): validate map connector params on displayed media, allow GeoJSON files`.

### PR-7 — front : ne pas utiliser `default_crs` comme projection de la carte entière (bug D4, défaut b)

- **Dépôt** : `rudi-platform/rudi-portal`. **Module** : front Angular.
- **Fichier** : `.../angular-project/src/app/shared/core/maps/map/map.component.ts`
- **Problème** : dans `ngAfterViewInit()`, quand un média est affiché, `viewProjectionString =
  getDefaultCrs(media)` est utilisé comme **projection de la `View` OpenLayers** (donc du fond de plan).
  Si `default_crs` ≠ `EPSG:3857`, le fond de plan web-mercator est reprojeté et **déformé**. La
  projection de la vue doit **toujours** être `EPSG:3857` (native du fond) ; le `default_crs` du média
  ne sert qu'à sa propre couche (WMS/WFS), reprojetée par OpenLayers vers la vue.
- **Changement — AVANT** (branche `if (this.media != null)` de `ngAfterViewInit`) :
  ```ts
              // Affichage de données cartographiques d'un JDD récupération de la projection et register avec proj4
              if (this.media != null) {
                  let projectionString = getDefaultCrs(this.media);
                  projectionString ??= DEFAULT_VIEW_PROJECTION;
                  this.viewProjectionString = projectionString;
                  projection = this.displayMapService.registerAndGetProjection(projectionString).pipe(
                      tap(() => {
                          this.centeredPoint = proj4(GPS_PROJECTION, projectionString, this.mapCenter);
                          const topLeft = proj4(GPS_PROJECTION, projectionString, this.mapCenterTopleft);
                          const bottomRight = proj4(GPS_PROJECTION, projectionString, this.mapCenterBottomRight);
                          this.initExtent = boundingExtent([topLeft, bottomRight]);
                      })
                  );
              }
  ```
  **APRÈS :**
  ```ts
              // La projection de la VUE (et du fond de plan) est TOUJOURS EPSG:3857, projection native
              // des fonds web-mercator. Le default_crs propre au connecteur du média n'est PAS la
              // projection de la carte entière : on l'enregistre seulement dans proj4 pour qu'OpenLayers
              // sache reprojeter la couche WMS/WFS de ce média vers la vue (cf. map.layer.function.ts).
              if (this.media != null) {
                  const mediaCrs = getDefaultCrs(this.media);
                  this.viewProjectionString = DEFAULT_VIEW_PROJECTION;
                  const register$ = (mediaCrs && mediaCrs !== DEFAULT_VIEW_PROJECTION)
                      ? this.displayMapService.registerAndGetProjection(mediaCrs).pipe(map(() => get(DEFAULT_VIEW_PROJECTION)))
                      : of(get(DEFAULT_VIEW_PROJECTION));
                  projection = register$.pipe(
                      tap(() => {
                          this.centeredPoint = proj4(GPS_PROJECTION, DEFAULT_VIEW_PROJECTION, this.mapCenter);
                          const topLeft = proj4(GPS_PROJECTION, DEFAULT_VIEW_PROJECTION, this.mapCenterTopleft);
                          const bottomRight = proj4(GPS_PROJECTION, DEFAULT_VIEW_PROJECTION, this.mapCenterBottomRight);
                          this.initExtent = boundingExtent([topLeft, bottomRight]);
                      })
                  );
              }
  ```
  > Vérifier au `Read` que `map` (rxjs), `of`, `get` (ol/proj) sont **déjà** importés (`of` et `get`
  > le sont — cf. branche `else` ; `map` de `rxjs/operators` l'est très probablement, sinon l'ajouter).
  > La branche `else` (pas de média) reste **inchangée**. Ne pas toucher `map.component.ts:555`
  > (`viewProjectionString ?? DEFAULT_VIEW_PROJECTION`) : avec le fix, `viewProjectionString` vaut
  > désormais toujours `EPSG:3857`, ce qui est correct.
- **Note de non-régression importante à mettre dans la PR** : avec les données actuelles du pipeline
  (`default_crs` toujours = `EPSG:3857`), ce correctif est **neutre** (la vue était déjà en 3857). Il
  devient utile dès qu'un producteur publie un `default_crs` différent (ex. `EPSG:2154`) — cas que le
  pipeline pourra alors produire une fois W4 retiré.
- **Déploiement** : rebâtir l'image du front.
- **Retire** : le forçage de `default_crs=EPSG:3857` côté pipeline (composante de **W4**).
- **Commit** : `fix(front): keep map view in EPSG:3857, reproject only the media layer`.

### PR-8 — kalim : tolérer `connector_parameters` null dans le validateur (bug A6)

- **Dépôt** : `rudi-platform/rudi-portal`. **Module** : `rudi-microservice-kalim`.
- **Fichier** : `.../rudi-microservice-kalim-service/src/main/java/org/rudi/microservice/kalim/service/integration/impl/validator/interfacecontract/map/parameter/ConnectorValidator.java`
- **Problème** : pour un contrat validable (WFS/WMS/WMTS/CUSTOM), la boucle `for (… :
  connector.getConnectorParameters())` ne tolère pas `null` → NPE → rapport producteur **ERR-500**
  générique. `connector_parameters` est pourtant **optionnel** dans le standard RUDI. Le check
  précédent (`checkMandatoryParameters`) utilise déjà `CollectionUtils.emptyIfNull`.
- **Changement — AVANT :**
  ```java
  			// Pas de champs obligatoires manquants, validation de ceux renseignés
  			for (ConnectorConnectorParametersInner parameterInner : connector.getConnectorParameters()) {
  				connectorParameterValidators.stream().filter(element -> element.accept(parameterInner)).findFirst()
  						.ifPresent(validator -> integrationRequestsErrors
  								.addAll(validator.validate(parameterInner, connector.getInterfaceContract())));
  			}
  ```
  **APRÈS :**
  ```java
  			// Pas de champs obligatoires manquants, validation de ceux renseignés.
  			// connector_parameters est optionnel dans le standard RUDI : tolérer null (emptyIfNull),
  			// sinon NPE -> ERR-500 générique pour une fiche pourtant légale.
  			for (ConnectorConnectorParametersInner parameterInner : CollectionUtils
  					.emptyIfNull(connector.getConnectorParameters())) {
  				connectorParameterValidators.stream().filter(element -> element.accept(parameterInner)).findFirst()
  						.ifPresent(validator -> integrationRequestsErrors
  								.addAll(validator.validate(parameterInner, connector.getInterfaceContract())));
  			}
  ```
  > `org.apache.commons.collections4.CollectionUtils` est **déjà** importé (utilisé par
  > `checkMandatoryParameters`). Aucun nouvel import.
- **Déploiement** : recette §4 pour `rudi-microservice-kalim`.
- **Retire** : rien de supprimable (W6 reste une métadonnée légitime). PR de robustesse pure.
- **Suivi optionnel (PR séparée, hors périmètre)** : le sous-problème A6 « liste vide `[]` perdue à la
  re-sérialisation interne » (inclusion NON_EMPTY de Jackson) est un **défaut distinct** ; à traiter à
  part si on le décide.
- **Commit** : `fix(kalim): tolerate null connector parameters in connector validation`.

### PR-9 — nœud catalog : réactiver la modif d'organisations via l'API admin (RUDI-5672)

- **Dépôt** : `rudi-platform/rudi-node-catalog`. **Fichier** :
  `rudi-node-build/rudi-node-container/src/rudi-catalog/src/controllers/genericController.js`.
- **Statut** : **déjà commité** — `1dbc775 feat(catalog): enable organization update via admin API
  (RUDI-5672)` sur la branche `feat/enable-organization-update` (working tree propre). Retire la garde
  `NotImplementedError` sur `OBJ_ORGANIZATIONS`, réactivant l'upsert d'orgs admin (utilisé par
  l'enrichissement des producteurs du pipeline).
- **Action PR** : fork `rudi-platform/rudi-node-catalog`, pousser la branche, ouvrir la PR.
- **Hors périmètre visualisation** — inclus car prêt et atomique. À pousser indépendamment.

---

## 6. Ordre de soumission suggéré

Indépendantes, poussables au fil de l'eau :

1. **PR-1** (nœud CORS) — déjà écrite, ne dépend d'aucun portail. Commencer là.
2. **PR-3** (apigateway permit-all) — petite, débloque le téléchargement média (tableau **et** carte).
3. **PR-6**, **PR-7** (front D4 a puis b) — débloquent la carte proprement.
4. **PR-8** (kalim A6) — robustesse, isolée.
5. **PR-4**, **PR-5** (D5) — les plus incertaines (500 requête unique non confirmé) : **valider en
   environnement avant de retirer le proxy WMS (W3)**.
6. **PR-9**, **PR-2bis** — prêtes, hors périmètre visualisation, quand tu veux.

---

## 7. Rappel : ce qu'on ne touche pas

- Le routeur Traefik **`konsult-natif`** de `traefik-dynamic.yml` : infra du mode hybride, pas un
  contournement de bug. **Laisser en place.**
- Les `connector_parameters` **réels** des médias SERVICE WMS/WFS dans le pipeline (fonctions
  `_connector_parameters_wms` / `_connector_parameters_wfs`) : métadonnée légitime (**W6**). **Garder.**
- Le correctif maison **D6** dans `rudi_node.py` n'est pas un bug RUDI (c'était notre pipeline) : pas
  de PR. Il sera seulement **simplifié** (section 8, W5) une fois W4 retiré.

---

## 8. Retrait des contournements (APRÈS déploiement + validation des correctifs)

> Chaque retrait est **conditionné** à la mise en place effective ET vérifiée (section 9) des PR
> correspondantes. Ne rien retirer « en avance ».

### W2 — routeur Traefik `medias-dwnl` (après **PR-1 + PR-3** déployées et validées)

Fichier : `/media/simon/DATA4T/Dev/rudi-portal-local/rudi-out-of-the-box/config/traefik-dynamic.yml`.
Supprimer : le routeur `medias-dwnl`, les middlewares `medias-dwnl-rewrite` et `medias-dwnl-strip-auth`,
et le service `node-storage`. **Garder** `konsult-natif` et la section `medias-wms`/`wms-relay` (tant
que W3 n'est pas retiré). Recharger Traefik (file provider → rechargement à chaud). Vérifier ensuite
que `/medias/{gid}/{mid}/dwnl` passe bien par l'apigateway (§9.1).

### W3 — routeur `medias-wms` + proxy WMS (après **PR-4 + PR-5** déployées ET GetMap OK via apigateway)

1. Dans `traefik-dynamic.yml` : supprimer le routeur `medias-wms` et le service `wms-relay`.
2. Arrêter et supprimer le proxy Python :
   ```bash
   cd /media/simon/DATA4T/Dev/rudi-portal-local/shim
   kill "$(cat wms_proxy.pid 2>/dev/null)" 2>/dev/null
   rm -f wms_proxy.py lancer_wms_proxy.sh wms_proxy.pid wms_proxy.log
   ```
   > Ne pas toucher `shim_pagination.py` / `lancer_shim.sh` (shim du **pull** Kalim, sujet distinct).
3. **Condition d'arrêt** : si le 500 GetMap **persiste** en requête unique via l'apigateway après
   PR-4+PR-5, **ne pas retirer W3** — le laisser en place et remonter à l'équipe RUDI (capture réseau).

### W4 — placeholders `connector_parameters` sur médias FILE/dwnl (après **PR-6 + PR-7**)

Fichier : `/media/simon/DATA4T/Dev/moissonneuse-batteuse/src/translation/datagouv_to_rudi.py`.

1. **Média FILE** (dans la boucle `for chemin, typename in fichiers_geojson`) — retirer la ligne
   `connector_parameters` du connecteur FILE :
   ```python
   # AVANT
               "connector": {
                   "url": "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD",
                   "interface_contract": "dwnl",
                   "connector_parameters": _placeholder_connector_parameters(),
               },
   # APRÈS
               "connector": {
                   "url": "À_RENSEIGNER_APRES_DEPOT_SUR_NOEUD",
                   "interface_contract": "dwnl",
               },
   ```
2. **Média `source-metadata`** (`media_metadata_page`, contrat `dwnl`) — retirer de même :
   ```python
   # AVANT
           "connector": {
               "url": url_metadata,
               "interface_contract": "dwnl",
               "connector_parameters": _placeholder_connector_parameters(),
           },
   # APRÈS
           "connector": {
               "url": url_metadata,
               "interface_contract": "dwnl",
           },
   ```
3. **Branche `else` du SERVICE** (cas `geojson` → contrat `dwnl`) — remplacer le placeholder par une
   absence de `connector_parameters`. Adapter le bloc :
   ```python
   # AVANT
       if contract == "wms":
           connector_parameters = _connector_parameters_wms(couches_rm)
       elif contract == "wfs":
           premier_typename = fichiers_geojson[0][1] if fichiers_geojson else None
           connector_parameters = _connector_parameters_wfs(premier_typename)
       else:
           connector_parameters = _placeholder_connector_parameters()
       available_formats.append({
           "media_id": media_id_service,
           "media_type": "SERVICE",
           "media_name": f"service-{service_type}",
           "media_caption": caption_service,
           "connector": {
               "url": caps_url,
               "interface_contract": contract,
               "connector_parameters": connector_parameters,
           },
       })
   # APRÈS
       connecteur_service = {
           "url": caps_url,
           "interface_contract": contract,
       }
       if contract == "wms":
           connecteur_service["connector_parameters"] = _connector_parameters_wms(couches_rm)
       elif contract == "wfs":
           premier_typename = fichiers_geojson[0][1] if fichiers_geojson else None
           connecteur_service["connector_parameters"] = _connector_parameters_wfs(premier_typename)
       # contrat "dwnl" (geojson statique) : pas de connector_parameters (contrat non validable)
       available_formats.append({
           "media_id": media_id_service,
           "media_type": "SERVICE",
           "media_name": f"service-{service_type}",
           "media_caption": caption_service,
           "connector": connecteur_service,
       })
   ```
4. **Fonction devenue potentiellement inutile** : `_placeholder_connector_parameters` (l. ~297). Elle
   est **aussi** importée par `src/backfill_connector_parameters_geo.py` (voir point 6) → ne la
   supprimer qu'**après** avoir traité ce script.
5. **Tests** : `/media/simon/DATA4T/Dev/moissonneuse-batteuse/tests/test_translation.py` encode le
   contournement (assertions que **tous** les médias, FILE compris, portent les 4 clés). À corriger :
   - Le test `test_toutes_entrees_ont_connector_parameters` (~l. 140) : **inverser** l'intention —
     désormais les médias **FILE** et les SERVICE **dwnl** ne portent **pas** `connector_parameters`,
     seuls les SERVICE **WMS/WFS** les portent. Renommer/réécrire en ce sens.
   - Le test sur les entrées FILE (~l. 121-124) : remplacer l'assertion « FILE porte les 4 clés » par
     « FILE ne porte pas de `connector_parameters` » (ou ne bloque pas la carte).
   - **Garder** `test_wms_connector_parameters_reels` (~l. 98-107) : valeurs réelles sur SERVICE WMS = W6.
   - Lancer `python3 -m unittest discover tests/` → tout doit passer.
6. **Script de backfill obsolète** : `/media/simon/DATA4T/Dev/moissonneuse-batteuse/src/backfill_connector_parameters_geo.py`
   était le rattrapage **rétroactif** du contournement D4 (déjà appliqué sur les 95 JDD géo). Une fois
   PR-6+PR-7 en place, il n'a plus de raison d'être. **Recommandation** : le supprimer (il est le
   dernier consommateur de `_placeholder_connector_parameters`, ce qui permet ensuite de supprimer la
   fonction au point 4). Vérifier d'abord qu'aucun menu CLI / dashboard ne l'appelle encore
   (`grep -rn backfill_connector_parameters_geo src/`), sinon retirer aussi l'entrée correspondante.
7. **Republier** les JDD géo pour propager la nouvelle forme (sans placeholder) : réinitialiser
   `rudi_publie` des dossiers géo puis `python3 src/publish_rudi.py` (ou le menu de rattrapage). Vérifier
   la carte (§9.2) sur BD HAIE et un WMS-only **sans** placeholder.

### W5 — ré-injection `connector_parameters` après upload (après W4, cosmétique)

Fichier : `/media/simon/DATA4T/Dev/moissonneuse-batteuse/src/connectors/rudi_node.py`, dans
`publier_dataset()`. Après W4, les médias FILE ne portent plus de `connector_parameters` (et la boucle
d'upload ne traite **que** des médias FILE) → la ré-injection est morte. Retirer :
```python
# AVANT
          caption = medias[i].get("media_caption", "")
          connector_parameters = medias[i].get("connector", {}).get("connector_parameters")
          ...
          medias[i] = json.loads(str(media_info))
          if caption:
              medias[i]["media_caption"] = caption
          if connector_parameters:
              medias[i].setdefault("connector", {})["connector_parameters"] = connector_parameters
# APRÈS
          caption = medias[i].get("media_caption", "")
          ...
          medias[i] = json.loads(str(media_info))
          if caption:
              medias[i]["media_caption"] = caption
```
Adapter le commentaire au-dessus (qui parle du bug D4/D6). Relancer les tests + une publication de
contrôle d'un JDD géo (§9.2).

---

## 9. Validation (bout-en-bout, contournement retiré)

À exécuter après chaque déploiement + retrait. **JDD témoins** : un tabulaire CSV réel ; **BD HAIE**
(WFS-avec-fichiers, cas majoritaire) ; un **WMS-only** (« Bases gravimétriques »).

1. **D3 / Tableau** (PR-1 + PR-3, W2 retiré) : l'onglet « Données tabulaires » se remplit ;
   `curl -i 'http://rudi.localhost/medias/{gid}/{mid}/dwnl'` renvoie **200 + le CSV** (plus de 302
   `/login`), **connecté ET anonyme**. Le flux passe par l'apigateway (plus par le routeur `medias-dwnl`).
2. **D4 / Carte** (PR-6 + PR-7, W4/W5 retirés) : sur BD HAIE et sur le WMS-only, l'onglet « Carte »
   s'instancie (le composant `app-map-tab` est monté), affiche le GeoJSON / la couche WMS, et le **fond
   de plan n'est pas déformé**. Republier au moins un JDD géo **sans** placeholder et confirmer que la
   carte marche toujours.
3. **D5 / WMS** (PR-4 + PR-5, W3 retiré) : sur le WMS-only, `/medias/{gid}/{mid}/wms?...GetMap...` passe
   par l'apigateway (plus par le shim 3034) et renvoie **200 + PNG**, y compris sous rafale de tuiles
   (déplacement/zoom). **Si le 500 persiste en requête unique → ne pas retirer W3** et remonter à RUDI.
4. **A6 / Intégration** (PR-8) : pousser un JDD géo dont un média SERVICE wfs/wms **n'a pas** de
   `connector_parameters` → intégration **OK** (plus d'ERR-500 / NPE `ConnectorValidator`).
5. **Non-régression pipeline** : `cd /media/simon/DATA4T/Dev/moissonneuse-batteuse && python3 -m unittest
   discover tests/` passe après W4/W5.

---

## 10. Checklist de contrôle (pour la revue, à cocher par le relecteur)

- [ ] PR-1 : branche ne contient **que** le commit CORS (`cf374b3`), max-file-size désolidarisé.
- [ ] PR-3 : `SB_PERMIT_ALL_URL` contient `/apigateway/datasets/**`, rien d'autre modifié.
- [ ] PR-4 : `mergeQueryParams` dédoublonne par clé (casse insensible), entrant prioritaire ; `filter()`
      utilise `mergedQuery`.
- [ ] PR-5 : `ConcurrentHashMap`, import `HashMap` retiré, `computeIfAbsent` inchangé.
- [ ] PR-6 : garde basée sur `mediaToDisplayMap`, FILE GeoJSON accepté sans `connector_parameters`.
- [ ] PR-7 : `View` toujours en `EPSG:3857`, CRS média seulement enregistré dans proj4 ; branche `else`
      intacte ; `map.component.ts:555` non cassé.
- [ ] PR-8 : boucle enveloppée dans `CollectionUtils.emptyIfNull`, pas de nouvel import.
- [ ] PR-9 : garde `NotImplementedError` orgs retirée, working tree propre.
- [ ] Chaque PR = 1 commit logique, message conventionnel, **sans** trailer d'IA.
- [ ] W2/W3/W4/W5 retirés **seulement** après validation (section 9) ; `konsult-natif` et W6 intacts.
- [ ] `python3 -m unittest discover tests/` vert après W4/W5 ; tests D4 réécrits (FILE sans params).
- [ ] `backfill_connector_parameters_geo.py` supprimé et plus référencé ; `_placeholder_connector_parameters`
      supprimée.

---

## Annexe — Références bugs (RAPPORT_BUGS_RUDI.md)

| Réf | Bug | Fichier source corrigé |
|---|---|---|
| D3 (nœud) | CORS wildcard incompatible `withCredentials` | `rudi-storage/src/httpService.js` (PR-1) |
| D3 (portail) | `/apigateway/datasets/**` absent de permit-all → 302 `/login` | `SecurityConstants.java` (PR-3) |
| D4a | garde carte sur `available_formats[0]` au lieu du média affiché | `detail.component.ts` (PR-6) |
| D4b | `default_crs` utilisé comme projection de toute la carte | `map.component.ts` (PR-7) |
| D5-1 | fusion de query sans dédoublonnage | `RerouteToRequestUrlFilter.java` (PR-4) |
| D5-2 | `HashMap` partagé non thread-safe (CME) | `ObjectsUtils.java` (PR-5) |
| A6 | NPE `connector_parameters` null dans le validateur | `ConnectorValidator.java` (PR-8) |
| RUDI-5672 | modif d'orgs admin désactivée | `genericController.js` (PR-9) |

# Plan : correctifs source + purge des 161 fiches en refus portail

**Contexte** : sur l'environnement hybride (nœud source natif 4030-4033 + portail ROOB
`integration/full-2026-07-28`), 356 JDD visibles au catalogue portail, mais le nœud compte
202 « envoyés » et 161 « refus portail ». Diagnostic complet dans la mémoire
`project-portail-rudi-local.md` (session 2026-07-29). Décision : instance expérimentale,
purge complète acceptée si besoin — corriger proprement plutôt que contourner.

**Incident à ne pas reproduire** : `GET /catalog/admin/portal/resources/send` (nœud, port 4030)
n'est **pas** une route de lecture — elle déclenche un vrai envoi de masse. Traiter comme une
action, jamais comme un probe.

---

## 1. Correctifs source identifiés

### PR-1 — `rudi-catalog` (nœud source) : l'envoi de masse ne throttle rien (bug D2)

**Fichier** : `rudi-node-build/rudi-node-container/src/rudi-catalog/src/controllers/portalController.js`,
fonction `sendAllMetadataToPortal` (ligne ~324).

**Bug confirmé (lecture du code)** : la boucle par paquets de 5 est cosmétique —
```js
sendMetadataToPortal(metadataList[bucketLen * n + i]?.[API_METADATA_ID])
  .then((res) => logD(mod, fun, `OK: ${beautify(res)}`))
  .catch((err) => logW(mod, fun, `ERR: ${beautify(err)}`))
sleepMs(50)          // <-- jamais awaité : ne bloque rien
```
`sleepMs` retourne une Promise (`utils/jsUtils.js:200`) mais elle n'est jamais attendue, et
`sendMetadataToPortal(...)` est fire-and-forget (`.then/.catch` sans `await`). Résultat : les
383+ appels partent quasi simultanément → tempête de renégociation de token portail (~140 en
3 min constatées) → ACL/konsult saturés → cascade de timeouts. C'est le mécanisme exact que j'ai
redéclenché par accident.

**Correctif** : rendre la boucle réellement séquentielle par paquet.
```js
for (let n = 0; n < bucketCount; n++) {
  const bucket = metadataList.slice(bucketLen * n, bucketLen * n + bucketLen)
  await Promise.allSettled(
    bucket.map((meta) =>
      sendMetadataToPortal(meta?.[API_METADATA_ID])
        .then((res) => logD(mod, fun, `OK: ${beautify(res)}`))
        .catch((err) => logW(mod, fun, `ERR: ${beautify(err)}`))
    )
  )
  await sleepMs(500)   // vraie pause entre paquets, réglable
}
```
Risque : faible, comportemental uniquement (throttling), pas de changement de schéma/API.
Bénéfice : rend l'envoi de masse enfin utilisable sans auto-saturation — condition nécessaire
avant de relancer un renvoi complet des 161 fiches à l'étape 5 ci-dessous.

### PR-2 — `kalim` (portail-source) : durcissement de la création POST (bug A8, confiance partielle)

**Fichier** :
`rudi-portal-source/rudi-microservice/rudi-microservice-kalim/rudi-microservice-kalim-service/src/main/java/org/rudi/microservice/kalim/service/integration/impl/handlers/PostIntegrationRequestTreatmentHandler.java`,
méthode `treat()`.

**Ce qui est vérifié** : `validateAndSetErrors()` (thème/licence, `ERR_303`) s'exécute bien
**avant** `treat()` dans `handleInternal()` — donc une fiche rejetée sur ERR-303 ne devrait pas,
dans ce chemin, déclencher `datasetService.createDataset()`. Le rollback existe déjà pour
`getDataset()`/`createApi()` (le `catch` supprime le dataset créé), mais **pas** pour
`createDataset()` lui-même :
```java
protected void treat(...) {
    final String doi = datasetService.createDataset(metadata);   // <-- pas de try/catch ici
    try {
        final Metadata metadataCreated = datasetService.getDataset(doi);
        createApi(integrationRequest, metadataCreated);
    } catch (...) {
        datasetService.deleteDataset(doi);   // rollback seulement pour les étapes suivantes
        throw e;
    }
}
```
Si `createDataset()` lui-même échoue à mi-chemin (le facet `dataverse` sous-jacent peut faire
un create-puis-publish en deux temps interne, non audité ici), le dataset resterait orphelin
sans rollback. **Je n'ai pas trouvé avec certitude le chemin exact qui produit le fantôme observé
dans le rapport (A8)** — la validation thème/licence semble se faire en amont dans le code actuel.
Le correctif ci-dessous est une amélioration défensive raisonnable (durcit un vrai trou de rollback
constaté) mais ne garantit pas à 100% de couvrir le mécanisme exact d'A8 — d'où le plan de purge
en réconciliation (§2), qui ne dépend pas de cette certitude.

**Correctif proposé** :
```java
protected void treat(IntegrationRequestEntity integrationRequest, Metadata metadata)
        throws DataverseAPIException, ApiGatewayApiException {
    String doi = null;
    try {
        doi = datasetService.createDataset(metadata);
        final Metadata metadataCreated = datasetService.getDataset(doi);
        createApi(integrationRequest, metadataCreated);
    } catch (final DataverseAPIException | ApiGatewayApiException | RuntimeException e) {
        if (doi != null) {
            log.error("Rollback : suppression du JDD créé suite à une erreur", e);
            datasetService.deleteDataset(doi);
        }
        throw e;
    }
}
```
Risque : faible (le rollback existant est juste étendu à une étape de plus).

### PR-3 (optionnelle, non prioritaire) — `rudi-catalog` : durcir `sendMetadataToPortal` (bug D1)

Une fois PR-2 en place, un GET unitaire sur une fiche vraiment rejetée devrait renvoyer 404
(comportement déjà correctement géré : bascule sur le POST). PR-3 ne serait qu'une défense
supplémentaire (ne pas blanchir `integration_error_id` avant confirmation réelle d'un envoi) —
non nécessaire si PR-2 + la purge du §2 suffisent. À ne faire que si des refus réapparaissent
après la purge.

---

## 2. Plan de purge / remise à plat des 161 fiches

Approche **réconciliation ciblée**, pas un `docker compose down` (perdrait aussi les référentiels
KOS/organisations déjà semés à la main — coûteux à refaire, cf. bug B1). Option nucléaire en fin
de section si le ciblé s'avère trop sale.

**Étape 0 — Déployer les correctifs**
1. Appliquer PR-1 sur `rudi-catalog` (juste un redémarrage du process natif, pas de build Docker).
2. Appliquer PR-2 sur une branche `fix/kalim-rollback-create-dataset`, build image `:source` de
   kalim, redéployer (process habituel, cf. `PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md`).

**Étape 1 — Identifier précisément les 161 fiches**
Script en lecture seule (Python ou mongosh) qui liste, depuis la Mongo du nœud
(`rudi-source-mongo`, collection metadata), tous les documents avec `integration_error_id` défini
→ export `global_id` + `local_id` + dernier `reportId` connu. Sert de liste de référence pour
toutes les étapes suivantes (avant/après).

**Étape 2 — Purge portail ciblée**
Pour chaque `global_id` de la liste de l'étape 1 :
1. Vérifier sur le portail si un enregistrement existe (`GET konsult/v1/datasets/{global_id}/metadatas`).
2. Si oui (fantôme confirmé) : supprimer le dataset côté Dataverse (API admin Dataverse ou requête
   SQL ciblée sur les tables dataset/dataset_version liées à ce DOI) + l'entrée éventuelle dans
   `kalim_data.integration_request` associée à ce `global_id`.
3. Journaliser chaque suppression (id, doi, date) pour traçabilité — instance expérimentale mais
   autant garder une trace.

**Étape 3 — Reset des flags côté nœud**
Sur la Mongo du nœud, pour les mêmes documents : `unset` de `integration_error_id` et
`published_at` (les deux champs que `reckonMetadataStatus` lit pour décider Refused/Published),
et purge de la « waiting room » en mémoire (redémarrage du process `rudi-catalog` suffit, c'est un
tableau in-memory, pas persisté).

**Étape 4 — Test sur échantillon**
Avant le renvoi complet, renvoyer manuellement 2-3 fiches de la liste (route `sendMetadata`
unitaire, pas `sendAllMetadataToPortal`) et vérifier : plus d'erreur `global_id déjà utilisée`,
rapport `integration_status: OK`, fiche visible au catalogue (`konsult/v1/datasets/metadatas`).

**Étape 5 — Renvoi complet**
Une fois l'échantillon validé, déclencher `GET /catalog/admin/portal/resources/send?metadata_status=Refused`
(maintenant sûr grâce à PR-1) pour renvoyer les fiches restantes de la liste en une fois.
Surveiller `catalog.log` pendant l'opération.

**Étape 6 — Vérification finale**
Recompter : total catalogue portail (`konsult/v1/datasets/metadatas`), nombre « envoyés » et
« refus » côté nœud. Objectif : refus → 0 (ou seulement les cas légitimement invalides, ex. MIME
ODS déjà identifié mais volontairement hors scope de ce plan), et catalogue == total nœud validé.

**Option nucléaire (si le ciblé laisse des scories)** : réinitialiser entièrement la base
applicative du portail (`rudiplatform-database-1`, schémas kalim_data/strukture/konsult) +
Dataverse, puis rejouer le provisionnement des référentiels (20 concepts KOS, 91 organisations —
voir `project-portail-rudi-local.md` §« Ce qui marche ») et relancer un envoi complet depuis le
nœud. Plus lourd (re-seed KOS/organisations) mais garanti propre.

---

## Statut

Plan préparé, rien exécuté sur le portail/nœud à ce stade (hors incident de sondage déjà survenu
et documenté). En attente de feu vert pour l'exécution — proposition : PR-1 (faible risque,
bénéfice immédiat) peut être appliquée et déployée tout de suite ; PR-2 + purge (§2) à valider
avant de lancer, car ça touche les données du portail.

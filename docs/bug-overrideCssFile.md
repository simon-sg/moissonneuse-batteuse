# Bug : `overrideCssFile` non chargé par le front-office Angular

## STATUT au 2026-07-27 — CORRIGÉ (source), déploiement durable en attente

Confirmé comme un vrai bug d'intégration (pas un mécanisme mal compris) après vérification
complète de toutes les couches possibles :
- `index.html` (source, pas seulement le bundle) : aucun placeholder, aucun templating serveur.
- Image du front (nginx `1.31.2-alpine` ou variante Apache `ci/docker/front-office`) : copie
  statique seule ; les directives `mod_substitute` de `httpd.conf` sont commentées et sans
  rapport (base-href uniquement).
- Backend Konsult : implémente bien son côté du contrat (`CustomizationHelper.java` mappe et
  sert le fichier).
- Source Angular réelle (`src/app/**`, pas le bundle compilé) : recherche exhaustive de
  `override_css_file`/`overrideCssFile` → **zéro résultat avant correctif**. Le client généré
  depuis l'OpenAPI (`konsult.service.ts::downloadCustomizationResource()`) existe et est
  fonctionnel, mais n'était jamais appelé pour ce fichier (seulement pour `main_logo`).
- Les variables CSS visées (`--primary-color`, `--accent-color`, etc.) sont de vraies custom
  properties `:root` utilisées dans tout `styles.scss`/`_variables-rudi.scss` → le mécanisme,
  une fois câblé, fonctionne par cascade normale (le lien injecté en dernier dans `<head>`
  l'emporte).

**Correctif** : branche `fix/front-inject-override-css` (base `main` = v3.4.0) dans
`/media/simon/DATA4T/Dev/rudi-portal-source`, commit `8c896ea8`. Ajoute `loadOverrideCss()` dans
`AppComponent` (`src/app/app.component.ts`), appelée depuis `ngOnInit()` à côté du mécanisme
existant `loadScripts()` (même pattern). Récupère `CustomizationDescription.override_css_file`
via `CustomizationService`, télécharge la ressource via `KonsultService.downloadCustomizationResource()`,
crée un `<link rel="stylesheet">` pointant vers un `Blob` URL et l'ajoute à `document.head`.

**Validation fonctionnelle (2026-07-27)** — stack ROOB complète relevée, config réelle du
dashboard (`config/konsult/customization.json` + `style-override.css`, couleurs de test
`--primary-color: #ff7800`, `--accent-color: #c061cb`) :
1. `GET /konsult/v1/customizations?lang=fr` → 200, `override_css_file: "overrideCssFile.css"`.
2. `GET /konsult/v1/customizations/resources/overrideCssFile.css` → 200, contenu = le CSS de test.
3. Bundle front déployé (`main.14f667a080683fd9.js`) contient bien la chaîne `override_css_file`
   (preuve que le code patché a été servi).
4. **Capture d'écran headless Chrome** de `http://rudi.localhost/` : nav, sous-titre hero et
   éléments d'accent affichés en orange/violet (valeurs de test), au lieu du bleu/corail par
   défaut — confirmation visuelle directe que l'override s'applique.

Déploiement : le dist Angular reconstruit (`mvn -pl rudi-application/rudi-application-front-office
-am install`, Node v20.15.1 épinglé) a été copié par `docker cp` dans le conteneur
`rudiplatform-portail-1` en place (fallback documenté dans
`PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md` §3) — **éphémère**, perdu au prochain
`--force-recreate` du conteneur (l'image reste `:v3.4.0`).

**Bloqueur pour une image `:source` durable — sans rapport avec ce correctif** : `docker build
--target rudi-application-front-office` échoue *avant* d'atteindre ce stage : le `Dockerfile`
racine référence `rudi-microservice/rudi-microservice-provider/rudi-microservice-provider-facade`,
un module qui **n'existe pas** dans ce checkout (`ADD failed: file not found in build context`).
Le builder Docker legacy (pas de buildx installé) construit tous les stages dans l'ordre du
fichier jusqu'à la cible, y compris les stages non liés. À résoudre séparément (créer/retirer le
module côté source, ou installer buildx pour un build par dépendances) avant de pouvoir rebâtir
une image `:source` propre du front. `docker-compose-source.yml` a une entrée `portail:` prête
mais **laissée commentée** tant que ce bloqueur n'est pas levé (sinon le prochain recreate casse
sur une image inexistante).

**Dashboard moissonneuse-batteuse** : aucune évolution nécessaire. `rudi_portal_config.py` +
`page_portail.html`/`portail.js` génèrent déjà `customization.json` (`overrideCssFile`) et
`style-override.css` correctement — c'était uniquement le consommateur front qui manquait.

---

## Résumé (bug d'origine)

Le champ `overrideCssFile` de `customization.json` est correctement traité par le backend
Konsult (lecture, mapping UUID, servitude via `/konsult/v1/customizations/resources/`),
mais le front-office Angular **ne demandait jamais le fichier CSS** et ne l'injectait pas dans
la page.

## Versions concernées

- `rudiplatform/rudi-application-front-office` : **v3.3.12** (testé), **v3.4.0** (vérifié via Docker Hub — le champ n'est pas non plus utilisé), et confirmé dans la source `rudi-portal-source` (3.4.0) avant correctif.
- `rudiplatform/rudi-microservice-konsult` : v3.3.12 / 3.4.0.

## Comportement attendu

D'après la doc et la structure de `customization.json`, le front-office devrait :

1. Appeler `GET /konsult/v1/customizations?lang=fr`
2. Récupérer le champ `override_css_file` (valeur : `"overrideCssFile.css"`)
3. Appeler `GET /konsult/v1/customizations/resources/overrideCssFile.css`
4. Injecter un `<link rel="stylesheet">` dans `document.head`

## Comportement observé (avant correctif)

- Les étapes 1-2 fonctionnaient : l'API retournait bien `override_css_file: "overrideCssFile.css"`
- L'étape 3 n'était **jamais appelée** : le navigateur ne faisait aucune requête vers `/konsult/v1/customizations/resources/overrideCssFile.css`
- Le fichier CSS n'était donc jamais chargé, les variables CSS restaient aux valeurs par défaut

## Correctif appliqué

Fichier : `rudi-application/rudi-application-front-office/angular-project/src/app/app.component.ts`
(dépôt `rudi-portal-source`, branche `fix/front-inject-override-css`).

```ts
// Ajout au constructeur : CustomizationService, KonsultService, LogService

ngOnInit(): void {
    this.mediaSize = this.breakpointObserver.getMediaSize();
    this.loadScripts();
    this.loadOverrideCss();
}

/**
 * Charge et injecte le CSS de surcharge (customization.json > overrideCssFile), s'il est configuré.
 * Permet à un déploiement de personnaliser les couleurs (--primary-color, --accent-color, etc.)
 * sans reconstruire le bundle Angular.
 */
loadOverrideCss(): void {
    this.customizationService.getCustomizationDescription().pipe(
        switchMap((description: CustomizationDescription) => {
            if (!description.override_css_file) {
                return EMPTY;
            }
            return this.konsultService.downloadCustomizationResource(description.override_css_file);
        })
    ).subscribe({
        next: (blob: Blob) => {
            if (!blob || blob.size === 0) {
                return;
            }
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = URL.createObjectURL(blob);
            document.head.appendChild(link);
        },
        error: (error) => this.logger.error(error)
    });
}
```

## Suite

- Branche prête localement, **non poussée** (convention `feedback-pr-atomiques` : Simon pousse
  lui-même au fil de l'eau).
- À ouvrir comme PR atomique séparée vers `rudi-platform/rudi-portal` quand le bloqueur d'image
  (module `rudi-microservice-provider` manquant) est résolu ou hors de propos pour la revue GitHub
  (la PR elle-même n'a pas besoin que l'image locale se rebuild pour être proposée en revue).

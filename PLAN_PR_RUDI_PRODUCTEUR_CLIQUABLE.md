# Rendre le nom du producteur cliquable partout dans le portail RUDI — Guide d'implémentation

> **À lire en entier avant de toucher quoi que ce soit.** Ce document est un plan d'exécution
> détaillé pour un futur agent d'implémentation, moins puissant que celui qui l'a écrit. Il a été
> produit après une exploration complète et vérifiée (lecture directe des fichiers + de la
> configuration Spring Security) du portail RUDI source, **pas une supposition**.
>
> **Règles impératives pour l'agent qui implémente :**
> 1. **Une PR à la fois.** Ne jamais mélanger deux PR (A/B/C/D ci-dessous) dans la même
>    branche/commit.
> 2. **Toujours `Read` le fichier avant de l'éditer** — les numéros de ligne ci-dessous ont été
>    vérifiés le 2026-07-27 par lecture directe des fichiers réels, mais peuvent avoir bougé d'ici
>    l'implémentation. Repère le code par son **contenu**, pas par son numéro de ligne.
> 3. **Ne rien inventer.** Si un fichier/chemin/commande ne correspond pas à ce qui est décrit,
>    **arrête-toi et signale-le** au lieu de deviner.
> 4. **Contrainte impérative posée par Simon, à respecter dans toute future évolution RUDI** : les
>    évolutions du nœud et du portail RUDI ne doivent **jamais** dépendre de comportements propres à
>    notre pipeline de moisson (`moissonneuse-batteuse`) que les autres producteurs RUDI n'auraient
>    pas. Ce plan est 100% générique — il ne fait que créer des liens vers une page qui existe déjà
>    pour **tout** producteur ayant un `organization_id`, quelle que soit la source de ses données.
> 5. Après chaque PR, exécuter la validation associée (section 6) **avant** de passer à la suivante.

---

## 1. Contexte et découverte clé

**Demande de Simon** : partout où le nom d'un producteur apparaît dans le portail RUDI (catalogue,
en-tête d'un JDD, contact, JDD liés...), il doit être cliquable et amener vers **la page de
l'organisation**.

**Découverte principale (exploration du 2026-07-27, vérifiée par lecture directe du code Angular ET
du code Java du microservice `strukture`)** : cette page **existe déjà** et est **déjà publique**.
Ce n'est donc pas un travail de création de page — seulement du **câblage de liens manquants**. Le
raisonnement complet :

1. Le portail a une route `/organization/detail/:organizationUuid/:name` (ou sans `:name`), qui
   affiche `OrganizationInformationsComponent` : nom, description, URL/contact, **et la liste
   paginée des JDD du producteur** (`app-dataset-list [producerUuid]="organization.uuid"`), plus ses
   réutilisations. Un onglet "Administration" ne s'affiche que si l'utilisateur connecté est
   administrateur de cette organisation — sinon la page est un simple profil en lecture seule.
   Fichiers : `src/app/features/organization/pages/detail/detail.component.ts/html`,
   `src/app/features/organization/components/organization-informations/organization-informations.component.ts/html`.
2. Cette route est protégée par le même `AuthGuardService` que `/catalogue` — **ce garde ne bloque
   personne** : il tente une authentification "utilisateur", et en cas d'échec **authentifie
   automatiquement une session anonyme** (`authenticateAsAnonymous()`) puis laisse passer. C'est le
   mécanisme standard qui rend tout le catalogue public accessible sans compte. Vérifié dans
   `src/app/core/services/auth-guard.service.ts`.
3. Côté API, l'endpoint réellement appelé pour charger l'organisation est
   `GET /strukture/v1/organizations/{uuid}` (`OrganizationsController.getOrganization`, fichier
   `rudi-microservice/rudi-microservice-strukture/rudi-microservice-strukture-facade/src/main/java/org/rudi/microservice/strukture/facade/controller/OrganizationsController.java`,
   ligne ~84). Le filtre Spring Security du microservice `strukture`
   (`WebSecurityConfig.java`, `.anyRequest().fullyAuthenticated()`) exige une authentification, mais
   **`getOrganization()` ne porte aucune annotation `@PreAuthorize`** contrairement à la quasi-totalité
   des autres endpoints du contrôleur (qui exigent `ADMINISTRATOR`/`MODERATOR`/`USER`...). La session
   anonyme du point 2 est une authentification OAuth2 valide (juste avec des droits limités) — elle
   suffit donc à charger n'importe quelle organisation en lecture.
4. **`metadata.producer.organization_id`** (exposé publiquement par l'API `konsult` sur **chaque**
   JDD du catalogue, sans authentification particulière) **est le même identifiant** que l'`uuid`
   `strukture` de l'organisation. Preuve : le logo producteur affiché sur les cartes du catalogue
   public (`app-organization-logo [organizationId]="metadata.producer.organization_id"`) est
   récupéré via `ProducersMetierService` (`src/app/core/services/producers-metier.service.ts`), qui
   appelle **directement** `ProducersService` du module `strukture/api-strukture` avec cet id. Le
   catalogue public utilise donc déjà cet id comme identifiant `strukture` — c'est exactement l'id
   qu'il faut passer dans l'URL `/organization/detail/:organizationUuid`.

**Conséquence pratique : aucune nouvelle page, aucun nouveau composant, aucun nouvel appel API n'est
nécessaire.** Il suffit d'ajouter un lien (`routerLink`) vers
`/organization/detail/{{ organization_id }}/{{ slug(organization_name) }}` partout où
`organization_id` + `organization_name` sont déjà disponibles dans le template.

**Effet de bord positif non prévu par la demande initiale** : `Organization` (modèle public
`api-kaccess`, celui de `metadata.producer`) contient aussi `organization_caption` et
`organization_summary` — les champs que notre pipeline moissonneuse-batteuse enrichit déjà
(Wikipédia + repli factuel, voir `src/translation/organisation_secours.py`) via l'API admin du
**nœud** RUDI. **Grep exhaustif du front (`grep -rn "organization_caption\|organization_summary"
src/app`) : zéro occurrence.** Ces champs ne sont affichés nulle part dans le portail — ni sur la
page organisation existante (qui affiche `organization?.description`, un champ **différent**, propre
au modèle `strukture`, potentiellement vide pour tous les producteurs importés depuis un nœud). C'est
documenté comme piste future non retenue pour ce lot (§7) — **hors périmètre**, à ne pas faire sans
validation explicite de Simon, car cela toucherait le modèle `strukture` et sa page d'administration,
pas seulement l'affichage.

---

## 2. Emplacements

| Rôle | Chemin |
|---|---|
| Portail source (monorepo Java + Angular) | `/media/simon/DATA4T/Dev/rudi-portal-source` |
| Racine du front Angular | `rudi-application/rudi-application-front-office/angular-project/` |
| Page organisation (cible des liens, **existante, non modifiée par ce plan**) | `.../src/app/features/organization/pages/detail/detail.component.ts` + `.../components/organization-informations/organization-informations.component.ts` |
| Routes organisation (existantes, **non modifiées**) | `.../src/app/features/organization/organization-routing.module.ts` |
| Contrôleur REST organisation (référence uniquement, **non modifié** — monorepo Java) | `rudi-microservice/rudi-microservice-strukture/rudi-microservice-strukture-facade/src/main/java/org/rudi/microservice/strukture/facade/controller/OrganizationsController.java` |
| Procédure de build/déploiement/validation du front | `/media/simon/DATA4T/Dev/moissonneuse-batteuse/PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md` (section 3 « Front Angular » : Node épinglé v20.15.1, cible Dockerfile `rudi-application-front-office`) |
| Pipeline moissonneuse-batteuse (référence uniquement, **non modifié** par ce plan) | `/media/simon/DATA4T/Dev/moissonneuse-batteuse` |

Stack : Angular 19, composants **standalone** (imports déclarés dans `@Component({imports:[...]})`,
pas de NgModule), syntaxe de template moderne `@if`/`@for`/`@else`.

**Modèle de donnée exploité (`Organization`, généré OpenAPI, commun à tous les producteurs RUDI)** —
`micro_service_modules/api-kaccess/model/organization.ts` :
```ts
export interface Organization {
    organization_id: string;         // obligatoire — c'est aussi l'uuid strukture (voir §1.4)
    organization_name: string;       // obligatoire
    organization_address?: string;
    organization_coordinates?: OrganizationOrganizationCoordinates;
    organization_caption?: string;   // jamais affiché aujourd'hui — hors périmètre, voir §7
    organization_summary?: string;   // jamais affiché aujourd'hui — hors périmètre, voir §7
}
```
Le champ `Contact` (contacts d'un JDD, contacts de métadonnées) porte un `organization_name` mais
**aucun `organization_id`** — impossible à relier de façon fiable à une organisation. Voir §7 « Ce
qui reste volontairement non cliquable ».

**Utilitaire de slug déjà existant et déjà utilisé pour les liens `/catalogue/detail/:id/:name`** —
`src/app/core/services/codecs/uri-component-codec.ts::normalizeString()` (retire accents/diacritiques,
remplace le reste par des `-`, minuscule). Réutilisé tel quel dans ce plan, jamais réécrit.

**Route cible pour tous les liens de ce plan** :
```
/organization/detail/{organization_id}/{normalizeString(organization_name)}
```
(le segment `:name` est optionnel côté route — les deux formes existent dans
`organization-routing.module.ts` — mais on l'inclut partout où le nom est disponible, par cohérence
avec `/catalogue/detail/:id/:name`).

---

## 3. PR-A — cartes de JDD réutilisables (catalogue, page organisation, page projet, page d'accueil, "autres JDD")

**Le plus gros effet de levier** : ces deux composants sont utilisés par de très nombreuses pages.
Un seul patch par composant suffit à rendre le producteur cliquable partout où une carte de JDD
apparaît.

### A.1 — `DataSetCardComponent` (grille catalogue, page organisation, listes de projet)

Utilisé exclusivement via `app-dataset-list` (`src/app/shared/business/dataset/common/dataset-list/dataset-list.component.html`),
lui-même utilisé par :
- `list-container.component.html` → grille principale du catalogue (`/catalogue`)
- `organization-informations.component.html` → liste des JDD sur la page organisation elle-même
- listes de JDD de projet (`project-dataset-list`, etc.)

Fichiers : `src/app/shared/business/dataset/common/data-set-card/data-set-card.component.ts` et
`.html`.

**`data-set-card.component.html`** (vérifié, ligne ~19 le 2026-07-27) :

AVANT :
```html
      <span class="w-100 data-set-card-producer ">{{metadata.producer.organization_name}}</span>
```

APRÈS :
```html
      <a class="w-100 data-set-card-producer link-primary"
        [routerLink]="['/organization/detail', metadata.producer.organization_id, uriComponentCodec.normalizeString(metadata.producer.organization_name)]"
        (click)="$event.stopPropagation()">{{metadata.producer.organization_name}}</a>
```
> `(click)="$event.stopPropagation()"` est **indispensable** : toute la `mat-card` porte déjà
> `(click)="ifNotSelectableGoToDetail()"` (ligne ~8) qui navigue vers le détail du JDD. Sans ce
> `stopPropagation`, cliquer sur le nom du producteur déclencherait **les deux navigations** (course
> entre le routage organisation et le routage JDD). Utiliser un vrai `<a [routerLink]>` (plutôt qu'un
> `(click)` + `router.navigate()`) donne gratuitement le clic-molette/nouvel onglet et le
> comportement clavier standard d'un lien.
> `uriComponentCodec` doit être accessible depuis le template : le composant l'a déjà en
> `private readonly` (constructeur, pour `ifNotSelectableGoToDetail`) — le passer en `protected
> readonly` ou `public readonly` pour qu'Angular l'expose au template (voir ci-dessous).

**`data-set-card.component.ts`** :

AVANT (constructeur, vérifié ligne ~35-42 le 2026-07-27) :
```ts
    constructor(
        private readonly themeCacheService: ThemeCacheService,
        private readonly breakpointObserver: BreakpointObserverService,
        private readonly languageService: LanguageService,
        private readonly uriComponentCodec: URIComponentCodec,
        private readonly matIconRegistry: MatIconRegistry,
        private readonly domSanitizer: DomSanitizer,
        private readonly router: Router,
    ) {
```
APRÈS (uniquement la visibilité de `uriComponentCodec` change, `private` → `protected`, pour être
lisible depuis le template) :
```ts
    constructor(
        private readonly themeCacheService: ThemeCacheService,
        private readonly breakpointObserver: BreakpointObserverService,
        private readonly languageService: LanguageService,
        protected readonly uriComponentCodec: URIComponentCodec,
        private readonly matIconRegistry: MatIconRegistry,
        private readonly domSanitizer: DomSanitizer,
        private readonly router: Router,
    ) {
```

Import à ajouter (ligne ~7, à côté de l'import existant de `Router`) :

AVANT :
```ts
import {Router} from '@angular/router';
```
APRÈS :
```ts
import {Router, RouterLink} from '@angular/router';
```

Décorateur `@Component` — ajouter `RouterLink` au tableau `imports` (vérifié ligne ~22 le
2026-07-27) :

AVANT :
```ts
    imports: [MatCard, NgClass, MatCardContent, OrganizationLogoComponent, MatIcon, MatButton, MatTooltip, SlicePipe, SplitPipe, TruncateTextPipe]
```
APRÈS :
```ts
    imports: [MatCard, NgClass, MatCardContent, OrganizationLogoComponent, MatIcon, MatButton, MatTooltip, SlicePipe, SplitPipe, TruncateTextPipe, RouterLink]
```

`Router` (le service, pas `RouterLink`) reste utilisé tel quel par `ifNotSelectableGoToDetail()` —
ne pas y toucher.

### A.2 — `DatasetsInfosComponent` (page d'accueil, "autres JDD" sur la page détail, listes de projet, tableau de JDD)

Utilisé par : `home/components/jdd-section`, `data-set/components/dataset-informations` (bloc
"Autres jeux de données sur le même thème"), `project/pages/detail`,
`shared/business/projects/projects-datasets-tables/dataset-table`.

Fichiers : `src/app/shared/business/dataset/common/dataset-infos/dataset-infos.component.ts` et
`.html`.

**`dataset-infos.component.html`** (vérifié lignes 13-18 le 2026-07-27) :

AVANT :
```html
  <div class="text-container">
    <div class="dataset-info">
      @if (organizationName) {
        <div class="dataset-info-organization_name"><span >{{ organizationName }}</span>
      </div>
    }
```
APRÈS :
```html
  <div class="text-container">
    <div class="dataset-info">
      @if (organizationName) {
        <div class="dataset-info-organization_name">
          @if (organizationId) {
            <a class="link-primary" [routerLink]="['/organization/detail', organizationId, uriComponentCodec.normalizeString(organizationName)]"
              (click)="$event.stopPropagation()">{{ organizationName }}</a>
          } @else {
            <span>{{ organizationName }}</span>
          }
      </div>
    }
```
> Ici `organizationId` est un `@Input()` déjà présent (voir `.ts`) mais peut être vide selon
> l'appelant (ex. la page d'accueil et "autres JDD" le passent toujours ; vérifier chaque appelant
> avant de considérer le `@else` mort). Le `<div>` parent porte déjà
> `(click)="handleClickOnDatasetCard(currentJddId, resourceTitle)"` (ligne 2) — même raison de
> `stopPropagation` que pour A.1.

**`dataset-infos.component.ts`** — `uriComponentCodec` est déjà injecté en `private readonly`
(constructeur, ligne ~61) : passer en `protected readonly` comme en A.1. Ajouter `RouterLink` à
l'import `@angular/router` existant (ligne 5 : `import {Router} from '@angular/router';` →
`import {Router, RouterLink} from '@angular/router';`) et au tableau `imports` du décorateur (ligne
16 : `imports: [NgClass, OrganizationLogoComponent, MatIcon, MatDivider]` →
`imports: [NgClass, OrganizationLogoComponent, MatIcon, MatDivider, RouterLink]`).

**Commit** : `feat(front): make producer name clickable on dataset cards`
**Branche** : `feat/dataset-card-clickable-producer`

---

## 4. PR-B — en-tête de page (`PageHeadingComponent`)

Composant affichant le bandeau en haut de la page détail d'un JDD (`/catalogue/detail/...`), de la
page organisation elle-même (`/organization/detail/...`), et de la page détail JDD "selfdata"
(`personal-space/pages/selfdata-dataset-details`). Le nom du producteur y est déjà affiché en texte
brut (`producer-name`), juste au-dessus du titre du JDD.

Fichiers : `src/app/shared/core/layout/page-heading/page-heading.component.ts` et `.html`.

**`page-heading.component.html`** (vérifié le 2026-07-27) :

AVANT :
```html
      <div class="producer-name">{{organizationName}}</div>
```
APRÈS :
```html
      @if (organizationId && organizationClickable) {
        <a class="producer-name" [routerLink]="['/organization/detail', organizationId, uriComponentCodec.normalizeString(organizationName)]">{{organizationName}}</a>
      } @else {
        <div class="producer-name">{{organizationName}}</div>
      }
```
> Pas de `stopPropagation` nécessaire ici : rien d'englobant n'a de `(click)` sur ce bandeau.

**`page-heading.component.ts`** — ajouter l'input `organizationClickable` (défaut `true`), injecter
`URIComponentCodec`, importer `RouterLink` :

AVANT :
```ts
import { NgClass } from '@angular/common';
import {Component, Input} from '@angular/core';
import {MatIcon} from '@angular/material/icon';
import {BreakpointObserverService, MediaSize} from '@core/services/breakpoint-observer.service';
import {OrganizationLogoComponent} from '../../../business/organisation/organization-logo/organization-logo.component';

@Component({
    selector: 'app-page-heading',
    templateUrl: './page-heading.component.html',
    styleUrls: ['./page-heading.component.scss'],
    imports: [NgClass, OrganizationLogoComponent, MatIcon]
})
export class PageHeadingComponent {

    @Input()
    organizationId: string;

    @Input()
    organizationName: string;

    @Input()
    icon: string;

    @Input()
    resourceTitle: string;

    @Input()
    status: string;

    mediaSize: MediaSize;

    constructor(private readonly breakpointObserverService: BreakpointObserverService) {
        this.mediaSize = this.breakpointObserverService.getMediaSize();
    }
}
```
APRÈS :
```ts
import { NgClass } from '@angular/common';
import {Component, Input} from '@angular/core';
import {MatIcon} from '@angular/material/icon';
import {RouterLink} from '@angular/router';
import {BreakpointObserverService, MediaSize} from '@core/services/breakpoint-observer.service';
import {URIComponentCodec} from '@core/services/codecs/uri-component-codec';
import {OrganizationLogoComponent} from '../../../business/organisation/organization-logo/organization-logo.component';

@Component({
    selector: 'app-page-heading',
    templateUrl: './page-heading.component.html',
    styleUrls: ['./page-heading.component.scss'],
    imports: [NgClass, OrganizationLogoComponent, MatIcon, RouterLink]
})
export class PageHeadingComponent {

    @Input()
    organizationId: string;

    @Input()
    organizationName: string;

    @Input()
    icon: string;

    @Input()
    resourceTitle: string;

    @Input()
    status: string;

    /**
     * Permet de désactiver le lien vers la page organisation (ex. quand ce bandeau est déjà
     * affiché sur la page de cette organisation elle-même).
     */
    @Input()
    organizationClickable = true;

    mediaSize: MediaSize;

    constructor(
        private readonly breakpointObserverService: BreakpointObserverService,
        protected readonly uriComponentCodec: URIComponentCodec,
    ) {
        this.mediaSize = this.breakpointObserverService.getMediaSize();
    }
}
```

**Désactiver le lien sur son propre call site** — `src/app/features/organization/pages/detail/detail.component.html`
(vérifié le 2026-07-27) : ici `organizationName` reçoit en réalité le libellé traduit générique
`'organization.label'` (le mot "Organisation"), pas un vrai nom d'organisation — un lien vers
elle-même serait un non-sens visuel.

AVANT :
```html
  <app-page-heading
    [organizationName]="'organization.label'|translate"
    [resourceTitle]="organization?.name"
    [organizationId]="organization?.uuid"
    [status]="getStatus()"
    >
  </app-page-heading>
```
APRÈS :
```html
  <app-page-heading
    [organizationName]="'organization.label'|translate"
    [resourceTitle]="organization?.name"
    [organizationId]="organization?.uuid"
    [organizationClickable]="false"
    [status]="getStatus()"
    >
  </app-page-heading>
```

> Les deux autres appelants (`data-set/pages/detail/detail.component.html` et
> `personal-space/pages/selfdata-dataset-details/selfdata-dataset-details.component.html`) ne
> changent **pas** : ils passent déjà un vrai `organizationName`/`organizationId` de producteur et
> bénéficient du lien par défaut (`organizationClickable` par défaut à `true`). Vérifier tout de même
> `selfdata-dataset-details.component.html` au `Read` pour confirmer qu'il suit le même schéma
> d'inputs que `data-set/pages/detail/detail.component.html` avant de considérer que rien à faire —
> ne pas juste supposer.

**Commit** : `feat(front): make producer name clickable in page heading`
**Branche** : `feat/page-heading-clickable-producer`

---

## 5. PR-C — bloc "Info Producteur" de l'onglet Informations (page détail JDD)

Fichier : `src/app/features/data-set/components/dataset-informations/dataset-informations.component.html`
(section "Info Producteur", vérifiée lignes 134-156 le 2026-07-27 — le bloc "Autres jeux de données"
juste après, lignes 176-205, est **déjà couvert par PR-A.2** car il utilise `app-dataset-infos`, ne
pas le retoucher ici).

AVANT :
```html
            <div class="info-prod-content">
              <div class="producer-info">
                @if (metadata.producer.organization_name) {
                  <span
                  >{{metadata.producer.organization_name | uppercase}}</span>
                }
                @if (metadata.producer.organization_address) {
                  <span
                  >{{metadata.producer.organization_address}}</span>
                }
              </div>
```
APRÈS :
```html
            <div class="info-prod-content">
              <div class="producer-info">
                @if (metadata.producer.organization_name) {
                  @if (metadata.producer.organization_id) {
                    <a class="link-primary"
                      [routerLink]="['/organization/detail', metadata.producer.organization_id, uriComponentCodec.normalizeString(metadata.producer.organization_name)]"
                    >{{metadata.producer.organization_name | uppercase}}</a>
                  } @else {
                    <span>{{metadata.producer.organization_name | uppercase}}</span>
                  }
                }
                @if (metadata.producer.organization_address) {
                  <span
                  >{{metadata.producer.organization_address}}</span>
                }
              </div>
```
> Pas de `stopPropagation` nécessaire : ce bloc n'est dans aucun élément parent cliquable.
> Les contacts juste en dessous (`@for (data of metadata?.contacts; ...)`, ligne ~161-169,
> `{{data.organization_name}}`) **restent non cliquables** — le type `Contact` n'a pas
> d'`organization_id` (voir §7).

**`dataset-informations.component.ts`** — `Router` est déjà importé et injecté (ligne 9 / ligne ~62)
mais n'est pas utilisé pour ce patch : on utilise `routerLink` directement, pas `router.navigate()`.
Ajouter `URIComponentCodec` :

AVANT (import, ligne 9-16 vérifiée le 2026-07-27) :
```ts
import {Router} from '@angular/router';
import {LanguageService} from '@core/i18n/language.service';
import {BreakpointObserverService, MediaSize} from '@core/services/breakpoint-observer.service';
import {FiltersService} from '@core/services/filters.service';
```
APRÈS :
```ts
import {Router, RouterLink} from '@angular/router';
import {LanguageService} from '@core/i18n/language.service';
import {BreakpointObserverService, MediaSize} from '@core/services/breakpoint-observer.service';
import {FiltersService} from '@core/services/filters.service';
import {URIComponentCodec} from '@core/services/codecs/uri-component-codec';
```
Ajouter `URIComponentCodec` au constructeur (`protected readonly uriComponentCodec:
URIComponentCodec,` — même traitement de visibilité qu'en PR-A) et `RouterLink` au tableau `imports`
du décorateur `@Component` (vérifié ligne ~30 : `imports: [LoaderComponent, MatCard, MatCardTitle,
MatCardContent, MatButton, MatTooltip, MatIcon, DataSetInfosComponent, OrganizationLogoComponent,
MatDivider, ContactButtonComponent, DatasetsInfosComponent, AsyncPipe, UpperCasePipe, DatePipe,
TranslatePipe]` → ajouter `RouterLink` à la fin).

**Commit** : `feat(front): make producer name clickable in dataset producer info panel`
**Branche** : `feat/dataset-info-panel-clickable-producer`

---

## 6. PR-D (optionnelle, priorité basse) — bloc "Infos fournisseur" de l'onglet Métadonnées

Section technique, moins visible (`metadata.metadata_info.metadata_provider` — le fournisseur des
*métadonnées*, un concept distinct du producteur des *données*, parfois identique parfois vide selon
les sources). Même type `Organization`, donc même `organization_id` exploitable si présent.

Fichier : `src/app/features/data-set/components/data-set-infos/data-set-infos.component.html`
(vérifié lignes 634-639 le 2026-07-27).

AVANT :
```html
      <div class="d-flex w-50 flex-column">
        @if (metadata?.metadata_info.metadata_provider?.organization_name) {
          <span class="f-w-600">
            {{metadata?.metadata_info.metadata_provider.organization_name | uppercase}}
          </span>
        }
```
APRÈS :
```html
      <div class="d-flex w-50 flex-column">
        @if (metadata?.metadata_info.metadata_provider?.organization_name) {
          @if (metadata.metadata_info.metadata_provider?.organization_id) {
            <a class="f-w-600 link-primary"
              [routerLink]="['/organization/detail', metadata.metadata_info.metadata_provider.organization_id, uriComponentCodec.normalizeString(metadata.metadata_info.metadata_provider.organization_name)]"
            >{{metadata?.metadata_info.metadata_provider.organization_name | uppercase}}</a>
          } @else {
            <span class="f-w-600">
              {{metadata?.metadata_info.metadata_provider.organization_name | uppercase}}
            </span>
          }
        }
```
> `metadata_contacts` juste en dessous (ligne ~646-660, `metadata_contact.organization_name`) est du
> type `Contact` — **reste non cliquable**, même limitation qu'en PR-C.

**`data-set-infos.component.ts`** — ni `Router` ni `URIComponentCodec` ne sont importés aujourd'hui
dans ce fichier (vérifié le 2026-07-27) : les ajouter (import + injection `protected readonly
uriComponentCodec: URIComponentCodec` dans le constructeur existant, ligne ~106) et ajouter
`RouterLink` au tableau `imports` du décorateur (fichier volumineux — chercher la ligne exacte du
tableau `imports` par contenu, pas par numéro).

**Commit** : `feat(front): make provider name clickable in dataset metadata panel`
**Branche** : `feat/metadata-provider-clickable`

---

## 7. Ce qui reste volontairement non cliquable

- **Contacts** (`metadata.contacts` sur la page détail, `metadata_info.metadata_contacts` sur
  l'onglet Métadonnées) : le type `Contact` (`micro_service_modules/api-kaccess/model/contact.ts`)
  n'expose qu'un `organization_name` texte libre, **aucun `organization_id`**. Impossible de
  résoudre de façon fiable vers une page organisation sans risque de lien faux (nom approchant mais
  organisation différente, ou aucune organisation `strukture` correspondante). Pas de solution
  front-only propre — nécessiterait une évolution du modèle `Contact` côté nœud RUDI (hors
  périmètre, hors main de notre pipeline en plus, cf. règle 4 en tête de document).

## 8. Piste non retenue pour ce lot — afficher `organization_caption`/`organization_summary`

Voir la découverte en §1 : ces champs standards RUDI existent dans le modèle `Organization` exposé
publiquement (`metadata.producer.organization_caption`/`organization_summary`), notre pipeline les
peuple déjà pour nos organisations (Wikipédia + repli factuel, `src/translation/organisation_secours.py`),
mais **rien ne les affiche dans le portail** — la page organisation existante affiche
`organization?.description`, un champ différent du modèle `strukture` (interne au portail, rempli
manuellement par les administrateurs d'organisation, vide par défaut pour les organisations importées
depuis un nœud). Une évolution cohérente serait de faire lire `organization?.description` en repli
sur `organization_caption`/`organization_summary` du producteur si vide — mais cela touche le modèle
`strukture`/l'administration d'organisation, un périmètre plus large que "rendre un nom cliquable".
**Décision à prendre avec Simon avant de l'entreprendre** — non inclus dans ce lot.

---

## 9. Validation

À exécuter après chaque PR déployée (procédure de build/déploiement :
`PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md`, section 3 « Front Angular », Node épinglé v20.15.1) :

1. **Session anonyme (obligatoire)** : ouvrir le portail en navigation privée (pas de session
   connectée existante) et vérifier que la page `/organization/detail/<uuid>/<nom>` s'affiche bien
   (nom, description ou message "pas de description", liste des JDD du producteur) **sans erreur
   401/403** dans l'onglet réseau du navigateur. C'est la vérification empirique du raisonnement du
   §1 — si elle échoue, **s'arrêter et documenter l'échec** avant de poursuivre une quelconque PR de
   ce plan : cela invaliderait la prémisse entière (il faudrait alors reconsidérer l'approche, voir
   remarque ci-dessous).
2. **PR-A** :
   - Grille catalogue (`/catalogue`) : cliquer sur le nom du producteur d'une carte → arrive sur sa
     page organisation, **sans** naviguer aussi vers le détail du JDD (vérifier qu'une seule
     navigation a lieu, pas de flash intermédiaire vers `/catalogue/detail/...`).
   - Molette/ctrl-clic sur le nom du producteur → ouvre la page organisation dans un nouvel onglet
     (comportement natif d'un `<a>`, à vérifier car c'est un gain direct du choix `routerLink`).
   - Page d'accueil (section JDD mis en avant) : même vérification.
   - Page détail JDD, bloc "Autres jeux de données sur le même thème" : même vérification.
   - Page organisation elle-même, liste de ses JDD (`app-dataset-list` interne) : cliquer sur le nom
     du producteur d'une carte doit re-naviguer vers **la même** page organisation (cas trivial,
     mais vérifier l'absence d'erreur/boucle).
3. **PR-B** : page détail JDD → nom du producteur dans le bandeau du haut cliquable. Page
   organisation elle-même → le mot "Organisation" dans le bandeau du haut **n'est pas** un lien
   (vérifie `organizationClickable="false"`).
4. **PR-C** : page détail JDD, onglet Informations, carte "Info Producteur" → nom cliquable ; les
   contacts listés juste en dessous restent en texte brut (non-régression volontaire, §7).
5. **PR-D** : page détail JDD, onglet Informations, panneau "Infos fournisseur" (dépliable) → nom
   cliquable si `metadata_provider` est renseigné pour le JDD testé (trouver un JDD où ce champ est
   effectivement peuplé — sinon le panneau reste vide, comportement inchangé).
6. **Non-régression générale** : sur chacune des pages touchées, vérifier qu'aucun autre élément
   cliquable existant n'a été cassé (ex. le clic sur le reste de la carte de JDD navigue toujours
   vers le détail du JDD).

**Si l'étape 1 échoue** (401/403 sur la page organisation en session anonyme) : le raisonnement du
§1.3 était correct en théorie (code source du contrôleur vérifié) mais un élément d'infrastructure
non visible depuis le code (config de déploiement, filtre de gateway additionnel, rôle par défaut de
la session anonyme différent de ce qui est attendu) bloque en pratique. Dans ce cas, **ne pas
continuer ce plan tel quel** — remonter le blocage précis (message d'erreur, endpoint en échec) à
Simon avant toute autre action, ce plan entier repose sur cette page étant publique.

---

## 10. Checklist de contrôle (pour la revue, à cocher par le relecteur)

- [ ] Étape 1 de validation (§9) exécutée en premier, **avant** toute PR — page organisation
      accessible en session anonyme, confirmé par inspection réseau.
- [ ] PR-A : `data-set-card.component` et `dataset-infos.component` — nom producteur cliquable,
      `stopPropagation` empêchant la double navigation, `RouterLink` importé et ajouté à `imports`.
- [ ] PR-B : `page-heading.component` — nom producteur cliquable par défaut, désactivé
      (`organizationClickable=false`) sur son propre call site dans la page organisation.
- [ ] PR-C : bloc "Info Producteur" de la page détail JDD — nom cliquable, contacts non touchés.
- [ ] PR-D (si faite) : bloc "Infos fournisseur" (métadonnées) — nom cliquable, contacts non
      touchés.
- [ ] Aucun lien créé pour un `organization_name` sans `organization_id` correspondant (dégradation
      propre en `<span>` partout).
- [ ] Aucune tentative de faire pointer un lien vers une organisation à partir d'un `Contact` (§7).
- [ ] Aucun fichier de `moissonneuse-batteuse` modifié.
- [ ] Aucun fichier du contrôleur `strukture`/backend Java modifié (référence uniquement, §2).
- [ ] Chaque PR = 1 commit logique, message conventionnel anglais, **sans** trailer d'IA (cf.
      mémoire `feedback-pr-atomiques` — contributions upstream signées par Simon).
- [ ] Validation §9 exécutée sur chaque PR déployée avant de la considérer terminée.

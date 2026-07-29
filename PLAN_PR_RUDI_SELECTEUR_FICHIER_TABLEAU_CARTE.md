# Ajouter un sélecteur de fichier aux vues « Tableau » et « Carte » (portail RUDI) — Guide d'implémentation

> **À lire en entier avant de toucher quoi que ce soit.** Ce document est un plan d'exécution
> détaillé pour un futur agent d'implémentation. Il ajoute un menu déroulant au-dessus des vues
> « Données tabulaires » et « Carte » de la fiche JDD du portail, permettant de choisir quel fichier
> visualiser quand un JDD en propose plusieurs. Sous forme de **PR atomiques (1 PR = 1 seul
> problème)**.
>
> **Règles impératives pour l'agent qui implémente :**
> 1. **Une PR à la fois.** Ne jamais mélanger PR-A et PR-B dans la même branche/commit.
> 2. **Toujours `Read` le fichier avant de l'éditer** — les numéros de ligne ci-dessous ont été
>    vérifiés le 2026-07-27 par lecture directe des fichiers réels, mais peuvent avoir bougé d'ici
>    l'implémentation. Repère le code par son **contenu**, pas par son numéro de ligne.
> 3. **Ne rien inventer.** Si un fichier/chemin/commande ne correspond pas à ce qui est décrit,
>    **arrête-toi et signale-le** au lieu de deviner.
> 4. **Contrainte impérative posée par Simon, à respecter dans toute future évolution RUDI** : les
>    évolutions du nœud et du portail RUDI ne doivent **jamais** dépendre de comportements propres à
>    notre pipeline de moisson (`moissonneuse-batteuse`) que les autres producteurs RUDI n'auraient
>    pas. Concrètement ici : les libellés du sélecteur n'utilisent **que** des champs standards du
>    modèle RUDI (`media_name`, `media_caption`, `file_type`) — jamais de pattern-matching sur nos
>    conventions internes de nommage de fichier.
> 5. Après chaque PR, exécuter la validation associée (section 6) **avant** de passer à la suivante.

---

## 1. Contexte

Sur la page détail d'un JDD, les onglets **« Données tabulaires »** et **« Carte »** n'affichent
aujourd'hui que le **premier** fichier compatible trouvé dans `metadata.available_formats`, même
quand le JDD en propose plusieurs (ex. plusieurs CSV, ou plusieurs GeoJSON/WMS/WFS). C'est une suite
du lot `PLAN_PR_RUDI_LISTE_FICHIERS_JDD.md` qui a ajouté l'affichage de `media_name`/`media_caption`
dans la liste des fichiers (onglet Informations) : l'utilisateur voit désormais qu'il y a plusieurs
fichiers, mais ne peut en visualiser qu'un seul, toujours le même.

**Objectif** : ajouter un menu déroulant **au-dessus** de la vue tableau et **au-dessus** de la vue
carte, permettant de choisir quel fichier visualiser parmi les candidats du JDD.

**Découverte importante lors de la vérification du code réel (2026-07-27)** : contrairement à ce que
laissait supposer le statut « ✅ correctes » de `PLAN_PR_RUDI_TABLEAU_CARTE.md` pour PR-6/PR-7,
**PR-6 n'est pas appliquée dans le code source actuel** — `handleMetadataProperties()` utilise
toujours `available_formats[0]` au lieu du média réellement affiché (vérifié en lisant
`detail.component.ts` lignes 639-648 ligne par ligne). Le présent plan **absorbe et dépasse**
l'intention de PR-6 : la validation des `connector_parameters` est déplacée vers le composant qui
affiche réellement le média sélectionné et **rejouée à chaque changement de sélection** (ce que
PR-6, même appliquée, n'aurait pas fait puisqu'elle ne visait qu'un calcul unique au chargement).

---

## 2. Emplacements

| Rôle | Chemin |
|---|---|
| Portail source (monorepo Java + Angular) | `/media/simon/DATA4T/Dev/rudi-portal-source` |
| Racine du front Angular | `rudi-application/rudi-application-front-office/angular-project/` |
| Page détail JDD | `.../src/app/features/data-set/pages/detail/detail.component.ts` + `.html` |
| Onglet tableau | `.../src/app/features/data-set/components/spreadsheet-tab/spreadsheet-tab.component.ts` + `.html` + `.scss` |
| Onglet carte | `.../src/app/features/data-set/components/map-tab/map-tab.component.ts` + `.html` + `.scss` |
| Composant carte OpenLayers (leaf) | `.../src/app/shared/core/maps/map/map.component.ts` |
| Grille tableau (leaf, ag-grid) | `.../src/app/features/data-set/components/spreadsheet/spreadsheet.component.ts` — **non touché** (voir §7) |
| Service extension de fichier | `.../src/app/core/services/konsult-metier.service.ts` (méthode `getMediaFileExtension`, déjà utilisée à 3 endroits) |
| Constantes carto | `.../src/app/core/services/map/map-protocols.ts`, `.../map-connector-required-parameters.ts`, `.../src/app/core/file-types.ts` |
| Traductions (seul fichier de langue) | `.../angular-project/src/assets/i18n/fr.json` |
| Procédure de build/déploiement/validation | `/media/simon/DATA4T/Dev/moissonneuse-batteuse/PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md`, section 3 « Front Angular » (Node épinglé v20.15.1) |
| Pipeline moissonneuse-batteuse | **non modifié** par ce plan (règle 4 ci-dessus) |

---

## 3. Décision d'architecture

### 3.1 Où vit la liste des candidats ?

Dans `DetailComponent`, qui possède déjà toute la logique de filtrage (`FileTypes.TEXT_CSV`/`VND_MS_EXCEL` pour le tableau, `FileTypes.GEO_JSON`/`MAP_PROTOCOLS_SUPPORTED` pour la carte). Les deux getters `isSpreadsheetDisplayed`/`isMapDisplayed` sont **refactorés pour perdre leur effet de bord** : la construction de la liste des candidats devient une méthode privée appelée **une fois** dans `ngOnInit` (au même endroit que l'ancien `handleMetadataProperties`), et les getters deviennent de simples `return this.xxxCandidates.length > 0`. Deux nouveaux champs `tableMediaCandidates: MediaFile[]` et `mapMediaCandidates: Media[]` sont exposés en `@Input() candidates` aux composants enfants.

### 3.2 Où vit l'état « média actuellement sélectionné » ?

**Dans chaque composant enfant** (`SpreadsheetTabComponent`, `MapTabComponent`), pas dans `DetailComponent`. `DetailComponent` continue de fournir `mediaToDisplayTable`/`mediaToDisplayMap` comme **valeur par défaut** (identique au comportement actuel — premier candidat trouvé), mais la sélection courante et son changement sont gérés localement par chaque enfant via un champ `selectedMedia` initialisé depuis `@Input() mediaToDisplay` en `ngOnInit`, puis modifié par le menu déroulant local. Ce choix évite un aller-retour parent↔enfant à chaque sélection et garde chaque onglet autonome, dans l'esprit du pattern déjà existant pour le changement de fond de plan (`MapComponent.switchLayer()`, géré localement).

### 3.3 Comment la sélection se propage jusqu'au rendu ?

- **Tableau** : `SpreadsheetTabComponent.ngOnInit()` est refactoré pour extraire son corps dans une méthode privée `loadTable(media)`, rejouable depuis un nouveau `onMediaSelected()` déclenché par le `mat-select`. Aucun changement requis dans `SpreadsheetComponent` (leaf ag-grid) : le bloc `@if (displayTable) { <app-spreadsheet ...> }` est imbriqué dans `@if (!displayTableLoading)`, qui repasse à `true` puis `false` à chaque rechargement — Angular détruit/recrée `<app-spreadsheet>` à chaque sélection, ses `@Input() rowData`/`columnDefs` sont donc toujours à jour dès l'instanciation. Pas de piège de réactivité ici.
- **Carte** : c'est plus délicat car `<app-map>` (le composant OpenLayers) **reste monté en continu** entre deux sélections (il n'est pas dans un `@if` qui bascule à chaque changement de média). Son `@Input() media` n'est aujourd'hui lu **qu'une fois** dans `ngAfterViewInit()`. Il faut donc lui ajouter `ngOnChanges()` (voir §5.6) pour qu'un changement de `media` après le premier rendu déclenche un rechargement **ciblé** de la seule couche de données du JDD (sans reconstruire toute la carte OpenLayers, sans toucher au fond de plan). Cela nécessite de tracker la couche de données ajoutée (`mediaDataLayer`) pour pouvoir la retirer proprement, et de corriger un piège latent : `addFeatureInteraction()` enregistrait un nouveau listener `map.on('click', ...)` à **chaque** chargement de couche WFS/GeoJSON — recharger plusieurs fois empilerait des listeners concurrents qui se marchent dessus sur la popup de feature. Il est donc refactoré pour n'être enregistré **qu'une fois**, et consulter dynamiquement un champ `interactiveMediaLayer` mis à jour à chaque chargement.

### 3.4 Où vit la validation `mapHasError` (bug D4a) ?

**Déplacée dans `MapTabComponent`** (renommée `hasMediaError`), calculée via une méthode privée `computeMediaError(media)` reprenant exactement la logique voulue par PR-6 de `PLAN_PR_RUDI_TABLEAU_CARTE.md` (accepter un GeoJSON FILE sans `connector_parameters`, exiger les 4 clés `MAP_CONNECTOR_PARAMETERS_REQUIRED` pour un média SERVICE), mais appliquée au **média réellement sélectionné à cet instant** et **rejouée dans `onMediaSelected()`**. `DetailComponent.handleMetadataProperties()`/`mapHasError`/`hasAllRequiredKeys()`/`isValidObject()` sont **supprimées** : `detail.component.html` ne fait plus le `@if (!mapHasError) {...} @else {<app-error-box>}` en amont, il monte toujours `<app-map-tab>` qui gère lui-même en interne le bascule map/erreur — exactement le pattern déjà en place dans ce même template pour `isErrorAccess`/`isErrorServer`. C'est ce déplacement qui résout structurellement le problème : plus de recalcul figé au chargement, la garde vit désormais dans le composant qui connaît la sélection courante.

**Important — hors périmètre, ne pas toucher** : le bug D4b (`default_crs` utilisé comme projection de toute la vue, PR-7 de `PLAN_PR_RUDI_TABLEAU_CARTE.md`, non plus appliquée dans le code actuel) n'est **pas** corrigé par ce plan. Conséquence : si l'utilisateur bascule entre deux médias cartographiques ayant des `default_crs` différents, l'affichage sera mal projeté (bug préexistant, maintenant aussi atteignable par sélection et non plus seulement au chargement initial). Comme le pipeline moissonneuse-batteuse ne produit aujourd'hui que du `default_crs=EPSG:3857`, ce cas ne se manifestera pas en pratique sur nos JDD — à documenter comme limitation connue (§8), pas à corriger ici.

### 3.5 1 PR ou 2 PR ?

**2 PR atomiques**, nommées **PR-A** (tableau) et **PR-B** (carte) :

- **PR-A** est petite et autonome : 2 fichiers de composant + `DetailComponent` + i18n. Aucune gestion de cycle de vie complexe, aucun état persistant à nettoyer.
- **PR-B** est structurellement plus lourde : elle touche 3 fichiers de composants (dont `MapComponent`, le plus complexe du front carto, avec un vrai refactor de gestion de listener OpenLayers) et déplace la logique de validation `mapHasError`. La mélanger avec PR-A romprait la règle « 1 PR = 1 problème » et rendrait la revue de PR-B (la partie risquée) plus difficile à isoler.

Pas de 3ᵉ PR « refactor partagé » pour l'extraction candidats/getters : sans UI de sélection, ce refactor n'a aucune valeur ni testabilité visible en soi (diff invisible à l'écran) — il est donc rattaché à la PR qui le consomme (PR-A pour `tableMediaCandidates`, PR-B pour `mapMediaCandidates`), chacune restant un problème utilisateur cohérent et testable de bout en bout. Les deux PR sont **fonctionnellement indépendantes** (elles touchent des getters/méthodes disjoints) ; PR-A est recommandée en premier car plus simple, mais l'ordre inverse est possible moyennant un rebase trivial du bloc `tap()` de `ngOnInit` (voir note en tête de PR-B).

---

## 4. PR-A — Sélecteur de fichier pour l'onglet « Données tabulaires »

### 4.1 `detail.component.ts`

**AVANT** (getter, vérifié lignes 181-192) :
```ts
    get isSpreadsheetDisplayed(): boolean {
        for (const item of this.metadata.available_formats) {
            const objet: MediaFile = item as MediaFile;
            if (objet.file_type === FileTypes.TEXT_CSV ||
                objet.file_type === FileTypes.VND_MS_EXCEL) {
                this.mediaToDisplayTable = item;
                return true;
            }
        }

        return false;
    }
```

**APRÈS :**
```ts
    get isSpreadsheetDisplayed(): boolean {
        return this.tableMediaCandidates.length > 0;
    }
```

Ajouter le champ (à côté de `mediaToDisplayTable`, vérifié ligne 89) :

**AVANT :**
```ts
    mediaToDisplayTable: Media;
    mediaToDisplayMap: Media;
```
**APRÈS :**
```ts
    mediaToDisplayTable: Media;
    mediaToDisplayMap: Media;

    /**
     * Liste des médias éligibles à l'affichage tabulaire (CSV/Excel), pour le sélecteur de fichier
     * de l'onglet « Données tabulaires ». Construite une seule fois au chargement du JDD (voir
     * buildTableMediaCandidates), pas dans le getter isSpreadsheetDisplayed (appelé à chaque cycle
     * de détection de changements par le template : il ne doit pas avoir d'effet de bord).
     */
    tableMediaCandidates: MediaFile[] = [];
```

Ajouter la méthode privée (par exemple juste après `isMapDisplayed`, avant `themePicto`) :
```ts
    /**
     * Construit la liste des médias pouvant être affichés dans l'onglet « Données tabulaires »
     * (CSV/Excel), et sélectionne par défaut le premier trouvé — comportement identique à l'ancien
     * getter isSpreadsheetDisplayed, mais sans effet de bord au sein d'un getter.
     * @private
     */
    private buildTableMediaCandidates(metadata: Metadata): void {
        this.tableMediaCandidates = metadata.available_formats.filter((item: Media) => {
            const objet: MediaFile = item as MediaFile;
            return objet.file_type === FileTypes.TEXT_CSV || objet.file_type === FileTypes.VND_MS_EXCEL;
        }) as MediaFile[];
        this.mediaToDisplayTable = this.tableMediaCandidates[0];
    }
```

Appeler cette méthode dans `ngOnInit` (vérifié lignes 244-257), juste après `handleMetadataProperties` (que PR-A **ne touche pas** — c'est PR-B qui la supprimera) :

**AVANT :**
```ts
            tap((metadata: Metadata) => {
                if (metadata) {
                    this.metadata = metadata;
                    this.restrictedAccess = this.metadata?.access_condition?.confidentiality?.restricted_access;
                    this.handleMetadataProperties(this.metadata);

                    // L'item sélectionné est le premier type FILE de la liste des formats disponibles
                    this.selectedItem = this.metadata.available_formats.filter(f => f.media_type === 'FILE')[0];
                    this.conceptUri = this.getConceptUri();
                    this.licenceLabel = this.getLicenceLabel();
                } else {
                    throw Error('Le JDD récupéré depuis le serveur est NUL, anormal, arrêt du traitement');
                }
            }),
```
**APRÈS :**
```ts
            tap((metadata: Metadata) => {
                if (metadata) {
                    this.metadata = metadata;
                    this.restrictedAccess = this.metadata?.access_condition?.confidentiality?.restricted_access;
                    this.handleMetadataProperties(this.metadata);
                    this.buildTableMediaCandidates(this.metadata);

                    // L'item sélectionné est le premier type FILE de la liste des formats disponibles
                    this.selectedItem = this.metadata.available_formats.filter(f => f.media_type === 'FILE')[0];
                    this.conceptUri = this.getConceptUri();
                    this.licenceLabel = this.getLicenceLabel();
                } else {
                    throw Error('Le JDD récupéré depuis le serveur est NUL, anormal, arrêt du traitement');
                }
            }),
```

Aucun nouvel import nécessaire (`FileTypes`, `MediaFile`, `Media`, `Metadata` déjà importés).

### 4.2 `detail.component.html`

**AVANT** (vérifié lignes 44-53) :
```html
          @if (isSpreadsheetDisplayed) {
            <app-tab
              [label]="'metaData.tabulatedDataTitle'|translate"
              [icon]="'tabulated-data'">
              <ng-template>
                <app-spreadsheet-tab [metadata]="metadata" [mediaToDisplay]="mediaToDisplayTable">
                </app-spreadsheet-tab>
              </ng-template>
            </app-tab>
          }
```
**APRÈS :**
```html
          @if (isSpreadsheetDisplayed) {
            <app-tab
              [label]="'metaData.tabulatedDataTitle'|translate"
              [icon]="'tabulated-data'">
              <ng-template>
                <app-spreadsheet-tab
                  [metadata]="metadata"
                  [mediaToDisplay]="mediaToDisplayTable"
                  [candidates]="tableMediaCandidates">
                </app-spreadsheet-tab>
              </ng-template>
            </app-tab>
          }
```

### 4.3 `spreadsheet-tab.component.ts` (fichier complet, remplace l'existant)

```ts
import { NgClass } from '@angular/common';
import {Component, Input, OnInit} from '@angular/core';
import {FormsModule} from '@angular/forms';
import {MatButton} from '@angular/material/button';
import {MatCheckbox} from '@angular/material/checkbox';
import {MatOption} from '@angular/material/core';
import {MatFormField, MatLabel} from '@angular/material/form-field';
import {MatIcon} from '@angular/material/icon';
import {MatSelect, MatSelectChange} from '@angular/material/select';
import {DataSetAccessService} from '@core/services/data-set/data-set-access.service';
import {DisplayTableDataInterface} from '@core/services/data-set/display-table-data.interface';
import {DisplayTableService} from '@core/services/data-set/display-table.service';
import {IconRegistryService} from '@core/services/icon-registry.service';
import {KonsultMetierService} from '@core/services/konsult-metier.service';
import {LogService} from '@core/services/log.service';
import {TranslatePipe} from '@ngx-translate/core';
import {ErrorBoxComponent} from '@shared/core/common/error-box/error-box.component';
import {LoaderComponent} from '@shared/core/common/loader/loader.component';
import {ErrorWithCause} from '@shared/models/error-with-cause';
import {ALL_TYPES} from '@shared/models/title-icon-type';
import {Media, MediaFile, Metadata} from 'micro_service_modules/api-kaccess';
import {catchError, switchMap} from 'rxjs/operators';
import {WorkBook} from 'xlsx';
import {SpreadsheetComponent} from '../spreadsheet/spreadsheet.component';

const EMPTY_SEARCH = '';

@Component({
    selector: 'app-spreadsheet-tab',
    templateUrl: './spreadsheet-tab.component.html',
    styleUrls: ['./spreadsheet-tab.component.scss'],
    imports: [LoaderComponent, FormsModule, MatIcon, NgClass, MatButton, MatCheckbox, ErrorBoxComponent, SpreadsheetComponent, TranslatePipe, MatFormField, MatLabel, MatSelect, MatOption]
})
export class SpreadsheetTabComponent implements OnInit {

    @Input()
    metadata: Metadata;

    @Input()
    mediaToDisplay: Media;

    /**
     * Liste des médias tabulaires candidats (CSV/Excel) pour le sélecteur de fichier au-dessus du
     * tableau. Fournie par DetailComponent (DetailComponent.tableMediaCandidates). Si elle contient
     * 0 ou 1 élément, le sélecteur n'est pas affiché (comportement identique à avant ce lot).
     */
    @Input()
    candidates: MediaFile[] = [];

    /** Le média actuellement affiché, initialisé depuis mediaToDisplay puis piloté par le sélecteur. */
    selectedMedia: Media;

    searchTerms = '';
    displayTableLoading = false;
    displayTable = false;
    usesHeader = false;
    displayTableData: DisplayTableDataInterface;
    workbook: WorkBook;
    errorAccess = false;
    errorDownloading = false;
    businessErrorMessage: string;
    unFilteredRowData: unknown[] = [];
    displayResults = false;

    constructor(
        private readonly displayTableService: DisplayTableService,
        private readonly iconRegistryService: IconRegistryService,
        private readonly logService: LogService,
        private readonly datasetAccessService: DataSetAccessService,
        private readonly konsultMetierService: KonsultMetierService
    ) {
        iconRegistryService.addAllSvgIcons(ALL_TYPES);
    }

    switchHeader(): void {
        this.usesHeader = !this.usesHeader;
        this.displayTableData = this.displayTableService.convertToDisplayableData(this.workbook, this.usesHeader);
        this.unFilteredRowData = this.displayTableData.rowData;
        this.onReset();
    }

    ngOnInit(): void {
        if (this.metadata && this.mediaToDisplay) {
            this.selectedMedia = this.mediaToDisplay;
            this.loadTable(this.selectedMedia);
        }
    }

    /**
     * Déclenché par le sélecteur de fichier au-dessus du tableau, quand le JDD propose plusieurs
     * fichiers tabulaires. Recharge entièrement le tableau pour le fichier nouvellement choisi.
     */
    onMediaSelected(event: MatSelectChange): void {
        const media: Media = event.value;
        if (media === this.selectedMedia) {
            return;
        }
        this.selectedMedia = media;
        this.searchTerms = EMPTY_SEARCH;
        this.displayResults = false;
        this.loadTable(media);
    }

    /**
     * Fonction permettant de retourner l'extension du fichier, utilisée pour le libellé du
     * sélecteur de fichier quand le média candidat n'a pas de media_name.
     */
    getMediaFileExtension(media: Media): string {
        return this.konsultMetierService.getMediaFileExtension(media);
    }

    /**
     * Télécharge et convertit en tableau affichable le fichier correspondant au média donné.
     * Extrait de l'ancien corps de ngOnInit pour pouvoir être rejoué à chaque changement de
     * sélection dans le nouveau sélecteur de fichier.
     * @private
     */
    private loadTable(media: Media): void {
        this.displayTableLoading = true;
        this.errorAccess = false;
        this.errorDownloading = false;
        this.businessErrorMessage = undefined;
        this.datasetAccessService.hasAccess(this.metadata).pipe(
            switchMap((hasAccess: boolean) => {
                if (hasAccess) {
                    this.errorAccess = false;
                    return this.displayTableService.downloadTableFile(media.connector.url).pipe(
                        catchError((error) => {
                            // Cas erreur avec un message à afficher côté front
                            if (error instanceof ErrorWithCause && error.code != null) {
                                this.businessErrorMessage = error.functionalMessage;
                                throw error;
                            }

                            // Cas erreur générique => message générique
                            this.errorDownloading = true;
                            throw new ErrorWithCause('Erreur lors du téléchargement des données', error);
                        })
                    );
                } else {
                    this.errorAccess = true;
                    throw new Error('Accès à la fonctionnalité d\'affichage tabulaire interdit dans ce contexte');
                }
            })
        ).subscribe({
            next: (workbook: WorkBook) => {
                this.errorDownloading = false;
                this.displayTableLoading = false;
                this.displayTable = true;
                this.workbook = workbook;
                this.displayTableData = this.displayTableService.convertToDisplayableData(this.workbook, this.usesHeader);
                this.unFilteredRowData = this.displayTableData.rowData;
            },
            error: (e) => {
                this.logService.error(e);
                this.displayTableLoading = false;
                this.displayTable = false;
            }
        });
    }

    /**
     * Fonction permettant de vider le champ input de la recherche et l'initialisation de la liste
     */
    onReset(): void {
        this.searchTerms = EMPTY_SEARCH;
        this.displayTableData.rowData = this.unFilteredRowData;
        this.onChanges();
    }

    /**
     * Méthode liée au déclenchement de l'event "key.enter" du champ de recherche
     */
    onChanges(): void {
        if (this.searchTerms) {
            this.displayTableData.rowData = this.filteredRowData;
            this.displayResults = true;
        } else {
            this.displayTableData.rowData = this.unFilteredRowData;
            this.displayResults = false;
        }

    }

    get filteredRowData(): unknown[] {
        const filterText = this.searchTerms.toLowerCase();
        return this.displayTableData.rowData.filter((data: unknown) => {
            return Object.values(data).some((value: unknown) => {
                return String(value).toLowerCase().includes(filterText);
            });
        });
    }
}
```

> Note pour l'implémenteur : `KonsultMetierService` est `providedIn: 'root'`, l'injection directe est sûre (déjà utilisé de la même façon dans `data-set-infos.component.ts` et `detail.component.ts`). `MatOption` s'importe bien depuis `@angular/material/core` (vérifié, convention du dépôt), pas depuis `@angular/material/select`.

### 4.4 `spreadsheet-tab.component.html`

**AVANT** (fichier complet, vérifié) :
```html
<div>
  <app-loader [allPage]="false" [isLight]="true" [noText]="true"
  [active]="displayTableLoading"></app-loader>
  @if (!displayTableLoading) {
    ...
```

**APRÈS** — insérer le sélecteur juste après la balise `<div>` d'ouverture, **avant** `<app-loader>` (reste visible pendant un rechargement, contrairement au reste du contenu qui est dans `@if (!displayTableLoading)`) :
```html
<div>
  @if (candidates && candidates.length > 1) {
    <div class="table-media-select">
      <mat-form-field appearance="outline">
        <mat-label>{{ 'common.fichierAAfficher'|translate }}</mat-label>
        <mat-select [value]="selectedMedia" [disabled]="displayTableLoading" (selectionChange)="onMediaSelected($event)">
          @for (candidate of candidates; track candidate) {
            <mat-option [value]="candidate">
              @if (candidate.media_name) {
                {{ candidate.media_name }}
              } @else {
                {{ 'common.fichier'|translate }} ({{ getMediaFileExtension(candidate) }})
              }
            </mat-option>
          }
        </mat-select>
      </mat-form-field>
    </div>
  }
  <app-loader [allPage]="false" [isLight]="true" [noText]="true"
  [active]="displayTableLoading"></app-loader>
  @if (!displayTableLoading) {
    ...
```
(le reste du fichier — à partir de `@if (!displayTableLoading) {` jusqu'à la fin — **ne change pas**.)

### 4.5 `spreadsheet-tab.component.scss`

Ajouter à la fin du fichier :
```scss
.table-media-select {
    margin-bottom: 20px;

    mat-form-field {
        width: 100%;
        max-width: 420px;
    }
}
```

### 4.6 i18n — `fr.json`

**AVANT** (bloc `common`, vérifié ligne 354) :
```json
        "fichier": "Fichier",
```
**APRÈS :**
```json
        "fichier": "Fichier",
        "fichierAAfficher": "Fichier à afficher",
```

**Commit** : `feat(front): let user pick which file to display in the tabular data tab`
**Branche** : `feat/dataset-table-media-selector`

---

## 5. PR-B — Sélecteur de fichier pour l'onglet « Carte »

> **Prérequis logique (pas technique)** : ce plan présente PR-B en supposant PR-A déjà mergée (le bloc `tap()` de `ngOnInit` inclura donc déjà l'appel à `buildTableMediaCandidates`). Si PR-B est faite en premier, relire le fichier réel et localiser le même point d'insertion par son contenu (juste après l'assignation de `this.metadata`).

### 5.1 `detail.component.ts`

Supprimer le champ (vérifié ligne 96) :
```ts
    mapHasError: boolean = false;
```

Ajouter, à côté de `tableMediaCandidates` :
```ts
    /**
     * Liste des médias éligibles à l'affichage cartographique (GeoJSON ou protocole WMS/WFS/WMTS),
     * pour le sélecteur de fichier de l'onglet « Carte ». Construite une seule fois au chargement du
     * JDD (voir buildMapMediaCandidates), pas dans le getter isMapDisplayed.
     */
    mapMediaCandidates: Media[] = [];
```

**AVANT** (getter, vérifié lignes 194-205) :
```ts
    get isMapDisplayed(): boolean {
        for (const item of this.metadata.available_formats) {
            const objet: MediaFile = item as MediaFile;
            if (objet.file_type === FileTypes.GEO_JSON ||
                MAP_PROTOCOLS_SUPPORTED.includes(objet.connector.interface_contract)) {
                this.mediaToDisplayMap = item;
                return true;
            }
        }

        return false;
    }
```
**APRÈS :**
```ts
    get isMapDisplayed(): boolean {
        return this.mapMediaCandidates.length > 0;
    }
```

Ajouter la méthode privée (juste après `buildTableMediaCandidates`) :
```ts
    /**
     * Construit la liste des médias pouvant être affichés dans l'onglet « Carte » (GeoJSON ou
     * protocole cartographique WMS/WFS/WMTS), et sélectionne par défaut le premier trouvé —
     * comportement identique à l'ancien getter isMapDisplayed, mais sans effet de bord.
     * @private
     */
    private buildMapMediaCandidates(metadata: Metadata): void {
        this.mapMediaCandidates = metadata.available_formats.filter((item: Media) => {
            const objet: MediaFile = item as MediaFile;
            return objet.file_type === FileTypes.GEO_JSON ||
                MAP_PROTOCOLS_SUPPORTED.includes(objet.connector.interface_contract);
        });
        this.mediaToDisplayMap = this.mapMediaCandidates[0];
    }
```

Dans `ngOnInit`, **remplacer** l'appel à `handleMetadataProperties` par `buildMapMediaCandidates` :

**AVANT** (état après PR-A) :
```ts
                    this.handleMetadataProperties(this.metadata);
                    this.buildTableMediaCandidates(this.metadata);
```
**APRÈS :**
```ts
                    this.buildTableMediaCandidates(this.metadata);
                    this.buildMapMediaCandidates(this.metadata);
```

Supprimer entièrement les 3 méthodes (vérifiées lignes 639-660) :
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

    // Vérification que toutes les clés obligatoires sont présentes et valides
    private hasAllRequiredKeys(connectorParameters: ConnectorConnectorParameters[], requiredKeys: string[]): boolean {
        return requiredKeys.every(requiredKey =>
            connectorParameters.some(obj => this.isValidObject(obj) && obj.key === requiredKey)
        );
    }

    // Validation d'un un objet
    private isValidObject(obj: any): boolean {
        return typeof obj.key === 'string' && 'value' in obj;
    }
```
(cette logique est déplacée, corrigée, et rendue réactive dans `MapTabComponent`, voir §5.3).

Imports à retirer (vérifiés inutilisés ailleurs dans le fichier par grep) :

**AVANT** (ligne 24) :
```ts
import {MAP_CONNECTOR_PARAMETERS_REQUIRED} from '@core/services/map/map-connector-required-parameters';
```
→ **supprimer la ligne entière.**

**AVANT** (ligne 51) :
```ts
import {ConnectorConnectorParameters, Licence, LicenceStandard, Media, MediaFile, Metadata} from 'micro_service_modules/api-kaccess';
```
**APRÈS :**
```ts
import {Licence, LicenceStandard, Media, MediaFile, Metadata} from 'micro_service_modules/api-kaccess';
```

`FileTypes`, `MediaFile`, `MAP_PROTOCOLS_SUPPORTED` restent utilisés par `buildTableMediaCandidates`/`buildMapMediaCandidates` — **ne pas les retirer**.

### 5.2 `detail.component.html`

**AVANT** (vérifié lignes 54-72) :
```html
          @if (isMapDisplayed) {
            <app-tab
              [label]="'metaData.mapData'|translate"
              [icon]="'map'"
              [invisible]="false"
              >
              <ng-template>
                @if (!mapHasError) {
                  <app-map-tab
                    [metadata]="metadata"
                    [mediaToDisplay]="mediaToDisplayMap"
                  ></app-map-tab>
                } @else {
                  <app-error-box [text]="'metaData.mapDataTab.error' | translate">
                  </app-error-box>
                }
              </ng-template>
            </app-tab>
          }
```
**APRÈS :**
```html
          @if (isMapDisplayed) {
            <app-tab
              [label]="'metaData.mapData'|translate"
              [icon]="'map'"
              [invisible]="false"
              >
              <ng-template>
                <app-map-tab
                  [metadata]="metadata"
                  [mediaToDisplay]="mediaToDisplayMap"
                  [candidates]="mapMediaCandidates"
                ></app-map-tab>
              </ng-template>
            </app-tab>
          }
```

`ErrorBoxComponent` devient inutilisé dans ce fichier/composant (c'était son seul usage dans `detail.component.html`) : retirer l'import et l'entrée du tableau `imports` du `@Component`.

**AVANT** (import, vérifié ligne 39) :
```ts
import {ErrorBoxComponent} from '@shared/core/common/error-box/error-box.component';
```
→ **supprimer.**

**AVANT** (tableau `imports`, vérifié ligne 69) :
```ts
    imports: [CommonModule, MatSidenavContainer, MatSidenavContent, LoaderComponent, NgClass, PageHeadingComponent, TabsComponent, TabComponent, DatasetInformationsComponent, SpreadsheetTabComponent, MapTabComponent, ErrorBoxComponent, BannerButtonComponent, MatMenuTrigger, MatIcon, MatMenu, FormsModule, ReactiveFormsModule, MatRadioGroup, MatRadioButton, MatButton, PopoverComponent, ProjectListComponent, RouterOutlet, TranslatePipe]
```
**APRÈS :**
```ts
    imports: [CommonModule, MatSidenavContainer, MatSidenavContent, LoaderComponent, NgClass, PageHeadingComponent, TabsComponent, TabComponent, DatasetInformationsComponent, SpreadsheetTabComponent, MapTabComponent, BannerButtonComponent, MatMenuTrigger, MatIcon, MatMenu, FormsModule, ReactiveFormsModule, MatRadioGroup, MatRadioButton, MatButton, PopoverComponent, ProjectListComponent, RouterOutlet, TranslatePipe]
```

### 5.3 `map-tab.component.ts` (fichier complet, remplace l'existant)

```ts
import {Component, Input, OnInit} from '@angular/core';
import {MatCard} from '@angular/material/card';
import {MatOption} from '@angular/material/core';
import {MatFormField, MatLabel} from '@angular/material/form-field';
import {MatSelect, MatSelectChange} from '@angular/material/select';
import {FileTypes} from '@core/file-types';
import {DataSetAccessService} from '@core/services/data-set/data-set-access.service';
import {DisplayMapService} from '@core/services/data-set/display-map.service';
import {KonsultMetierService} from '@core/services/konsult-metier.service';
import {LogService} from '@core/services/log.service';
import {MAP_CONNECTOR_PARAMETERS_REQUIRED} from '@core/services/map/map-connector-required-parameters';
import {TranslatePipe} from '@ngx-translate/core';
import {ErrorBoxComponent} from '@shared/core/common/error-box/error-box.component';
import {LoaderComponent} from '@shared/core/common/loader/loader.component';
import {MapComponent} from '@shared/core/maps/map/map.component';
import {ConnectorConnectorParameters, Media, MediaFile, Metadata} from 'micro_service_modules/api-kaccess';
import {LayerInformation} from 'micro_service_modules/konsult/konsult-model';
import {switchMap} from 'rxjs/operators';
import MediaTypeEnum = Media.MediaTypeEnum;

@Component({
    selector: 'app-map-tab',
    templateUrl: './map-tab.component.html',
    styleUrls: ['./map-tab.component.scss'],
    imports: [MatCard, LoaderComponent, MapComponent, ErrorBoxComponent, TranslatePipe, MatFormField, MatLabel, MatSelect, MatOption]
})
export class MapTabComponent implements OnInit {

    @Input()
    metadata: Metadata;

    @Input()
    mediaToDisplay: Media;

    /**
     * Liste des médias cartographiables candidats (GeoJSON FILE ou SERVICE WMS/WFS/WMTS) pour le
     * sélecteur de fichier au-dessus de la carte. Fournie par DetailComponent
     * (DetailComponent.mapMediaCandidates). Si elle contient 0 ou 1 élément, le sélecteur n'est pas
     * affiché (comportement identique à avant ce lot).
     */
    @Input()
    candidates: Media[] = [];

    /** Utilisé par le template pour distinguer le libellé FILE (extension) du libellé SERVICE (contrat). */
    readonly mediaType = MediaTypeEnum;

    /** Le média actuellement affiché, initialisé depuis mediaToDisplay puis piloté par le sélecteur. */
    selectedMedia: Media;

    isMapLoading: boolean;
    isErrorAccess: boolean;
    isErrorServer: boolean;

    /**
     * Vrai si le média sélectionné ne peut pas être affiché sur la carte (connector_parameters
     * manquants/incomplets pour un média SERVICE). Remplace l'ancien DetailComponent.mapHasError,
     * recalculé au chargement ET à chaque changement de sélection (voir computeMediaError).
     */
    hasMediaError = false;

    baseLayers: LayerInformation[] = [];

    constructor(
        private readonly datasetAccessService: DataSetAccessService,
        private readonly displayMapService: DisplayMapService,
        private readonly konsultMetierService: KonsultMetierService,
        private readonly logService: LogService
    ) {
    }

    ngOnInit(): void {
        if (this.metadata && this.mediaToDisplay) {
            this.selectedMedia = this.mediaToDisplay;
            this.hasMediaError = this.computeMediaError(this.selectedMedia);
            this.isMapLoading = true;
            this.datasetAccessService.hasAccess(this.metadata).pipe(
                switchMap((hasAccess: boolean) => {
                    this.isErrorAccess = !hasAccess;
                    return this.displayMapService.getDatasetBaseLayers();
                })
            ).subscribe({
                next: (baseLayers: LayerInformation[]) => {
                    this.baseLayers = baseLayers;
                    this.isMapLoading = false;
                },
                error: (e) => {
                    this.logService.error(e);
                    this.isMapLoading = false;
                }
            });
        }
    }

    /**
     * Déclenché par le sélecteur de fichier au-dessus de la carte, quand le JDD propose plusieurs
     * médias cartographiables. app-map réagit tout seul au changement de son @Input() media (voir
     * MapComponent.ngOnChanges) : ici on met juste à jour la sélection et on revalide les
     * connector_parameters du nouveau média.
     */
    onMediaSelected(event: MatSelectChange): void {
        const media: Media = event.value;
        if (media === this.selectedMedia) {
            return;
        }
        this.selectedMedia = media;
        this.hasMediaError = this.computeMediaError(media);
    }

    /**
     * Fonction permettant de retourner l'extension du fichier, utilisée pour le libellé du
     * sélecteur de fichier pour les médias FILE sans media_name.
     */
    getMediaFileExtension(media: Media): string {
        return this.konsultMetierService.getMediaFileExtension(media);
    }

    /**
     * Vérifie si le média donné peut être affiché sur la carte.
     * - Un GeoJSON téléchargé (média FILE) se rend directement depuis connector.url : il n'a jamais
     *   de connector_parameters et ne doit pas être bloqué.
     * - Un média SERVICE (WMS/WFS/WMTS…) a besoin des 4 clés MAP_CONNECTOR_PARAMETERS_REQUIRED pour
     *   construire sa requête GetMap/GetFeature.
     * Remplace l'ancien DetailComponent.handleMetadataProperties() (calculé une seule fois sur
     * available_formats[0] au chargement du JDD, cf. bug D4a de PLAN_PR_RUDI_TABLEAU_CARTE.md) : la
     * vérification est maintenant portée par le composant qui affiche réellement le média, sur le
     * média réellement sélectionné, et rejouée à chaque changement de sélection.
     * @private
     */
    private computeMediaError(media: Media): boolean {
        const mediaFile = media as MediaFile;
        if (mediaFile.file_type === FileTypes.GEO_JSON) {
            return false;
        }
        const connectorParameters: ConnectorConnectorParameters[] = media.connector?.connector_parameters;
        return !connectorParameters || !this.hasAllRequiredKeys(connectorParameters, MAP_CONNECTOR_PARAMETERS_REQUIRED);
    }

    // Vérification que toutes les clés obligatoires sont présentes et valides
    private hasAllRequiredKeys(connectorParameters: ConnectorConnectorParameters[], requiredKeys: string[]): boolean {
        return requiredKeys.every(requiredKey =>
            connectorParameters.some(obj => this.isValidObject(obj) && obj.key === requiredKey)
        );
    }

    // Validation d'un objet
    private isValidObject(obj: any): boolean {
        return typeof obj.key === 'string' && 'value' in obj;
    }
}
```

### 5.4 `map-tab.component.html` (fichier complet, remplace l'existant)

```html
<mat-card class="main-container">
  <app-loader [allPage]="false" [isLight]="true" [noText]="true" [active]="isMapLoading"></app-loader>
  @if (!isMapLoading && !isErrorAccess) {
    <div class="map-header">
      @if (candidates && candidates.length > 1) {
        <mat-form-field appearance="outline">
          <mat-label>{{ 'common.fichierAAfficher'|translate }}</mat-label>
          <mat-select [value]="selectedMedia" (selectionChange)="onMediaSelected($event)">
            @for (candidate of candidates; track candidate) {
              <mat-option [value]="candidate">
                @if (candidate.media_name) {
                  {{ candidate.media_name }}
                } @else if (candidate.media_type === mediaType.File) {
                  {{ 'common.fichier'|translate }} ({{ getMediaFileExtension(candidate) }})
                } @else {
                  {{ 'metaData.service'|translate }} ({{ candidate.connector?.interface_contract }})
                }
              </mat-option>
            }
          </mat-select>
        </mat-form-field>
      }
    </div>
    @if (!hasMediaError) {
      <div class="map-container">
        <app-map [mapId]="'rudi-map-tab'"
          [baseLayers]="baseLayers"
          [hasSearchAddress]="true"
          [metadata]="metadata"
          [media]="selectedMedia"
          >
        </app-map>
      </div>
    } @else {
      <app-error-box
        [text]="'metaData.mapDataTab.error' | translate"
        >
      </app-error-box>
    }
  }
  @if (isErrorAccess) {
    <app-error-box
      [text]="'metaData.tabulatedDataTab.errorAccess' | translate"
      >
    </app-error-box>
  }
  @if (isErrorServer) {
    <app-error-box
      [text]="'metaData.tabulatedDataTab.errorDownloading' | translate"
      >
    </app-error-box>
  }
</mat-card>
```

Points importants de cette réécriture :
- Le sélecteur (`.map-header`) reste **hors** du `@if (!hasMediaError)` : si l'utilisateur choisit un média en erreur, il doit pouvoir revenir en arrière via le même menu, pas rester bloqué devant le message d'erreur sans échappatoire.
- `[media]="selectedMedia"` remplace `[media]="mediaToDisplay"` sur `<app-map>` : c'est la sélection courante, pas la valeur par défaut, qui doit être transmise au composant carte.
- Le texte d'erreur réutilise la clé i18n existante `metaData.mapDataTab.error` (déjà présente dans `fr.json`, inchangée) — pas de nouvelle clé requise pour ce message.

### 5.5 `map-tab.component.scss`

**AVANT :**
```scss
.map-header {
    height: 50px;
    background-color: var(--primary-color);
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
}
```
**APRÈS :**
```scss
.map-header {
    min-height: 50px;
    height: auto;
    background-color: var(--primary-color);
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    display: flex;
    align-items: center;
    padding: 0 16px;

    mat-form-field {
        width: 100%;
        max-width: 420px;
    }
}
```
> `.map-header` était jusqu'ici une bande vide (50px, fond `--primary-color`) — purement décorative. Y insérer un `mat-form-field` en `appearance="outline"` demande une vérification visuelle manuelle du contraste (texte/contour du champ par défaut sur ce fond coloré) une fois l'image reconstruite ; ajuster si illisible (ex. `color-scheme` ou classe de thème du form-field). Cette vérification pixel ne peut pas être faite depuis l'exploration statique du code — à faire en §6.
>
> **Confirmé en test réel (bug trouvé et corrigé après implémentation)** : avec `padding: 0 16px` (zéro padding vertical), l'étiquette flottante du `mat-form-field appearance="outline"` (dessinée à cheval sur la bordure supérieure du champ, convention Material) déborde d'environ 7px **au-dessus** de la boîte de `.map-header` (`overflow: visible`, mesuré via `getBoundingClientRect()` : label à `top: 437` vs `.map-header` à `top: 444`). Sans marge au-dessus, ce débordement retombe sur la bande grise des onglets juste au-dessus au lieu de rester sur le fond bleu — visuellement, le sélecteur paraît « superposé » au mauvais endroit. **Premier correctif** (`padding: 14px 16px 4px` + fond blanc du champ + `subscriptSizing="dynamic"`) a réglé le débordement mais révélé un **second problème** : l'étiquette flottante retombait alors moitié sur le fond blanc du champ, moitié sur le fond bleu du bandeau (juste en dehors de la boîte blanche), illisible. **Correctif final retenu** (suggéré par Simon) : sortir entièrement le sélecteur de `.map-header` (qui redevient la simple bande bleue décorative d'origine) et le placer **au-dessus**, sur le fond clair de la carte — même traitement que le sélecteur de l'onglet Tableau (`.table-media-select`), qui n'avait jamais posé ce problème. Commits sur `feat/dataset-map-media-selector` : `fix(front): keep the map file selector label inside the header band` → `fix(front): give the map file selector a white background` → `fix(front): move the map file selector above the header band` (ce dernier rend les deux précédents caducs mais ils restent dans l'historique, cf. §3.5 sur les commits atomiques successifs d'un même correctif itéré en test réel).
>
> **Second bug trouvé en testant, sans rapport avec le sélecteur** : sur la carte, aucune donnée GeoJSON ne s'affichait jamais (fond de plan seul), quel que soit le fichier sélectionné — confirmé **pré-existant** (reproduit à l'identique sur le code d'avant PR-B). Cause : `map.layer.function.ts::createGeoJsonLayer()` appelle `new GeoJSON().readFeatures(geojsonObject)` **sans `featureProjection`** → pas de reprojection EPSG:4326→EPSG:3857, les features atterrissent près de l'origine de la projection Mercator, à des milliers de km de la zone affichée. Corrigé sur une branche **séparée** `fix/geojson-file-media-projection` (base `v3.4.1`, indépendante de la sélection de fichier — « 1 PR = 1 problème ») : ajout de `{featureProjection: DEFAULT_VIEW_PROJECTION}`. Les couches WFS n'étaient pas affectées (le serveur renvoie déjà les données dans la projection demandée). Vérifié en combinant temporairement les deux branches dans un worktree jetable : marqueur/polygones s'affichent correctement après le correctif.

### 5.6 `map.component.ts` — rendre `media` réactif après le premier rendu

Ajouter `OnChanges`/`SimpleChanges` à l'import Angular et à la classe :

**AVANT** (ligne 1 et ligne 47, vérifiées) :
```ts
import {AfterViewInit, Component, Input, OnInit} from '@angular/core';
...
export class MapComponent implements AfterViewInit, OnInit {
```
**APRÈS :**
```ts
import {AfterViewInit, Component, Input, OnChanges, OnInit, SimpleChanges} from '@angular/core';
...
export class MapComponent implements AfterViewInit, OnInit, OnChanges {
```

Ajouter deux champs (par exemple juste après `currentBaseLayer`, vérifié ligne 151) :

**AVANT :**
```ts
    /**
     * Le fond de carte OL actuel
     */
    currentBaseLayer: BaseLayer;
```
**APRÈS :**
```ts
    /**
     * Le fond de carte OL actuel
     */
    currentBaseLayer: BaseLayer;

    /**
     * La couche de données du JDD actuellement affichée sur la carte (WMS/WMTS/WFS/GeoJSON), pour
     * pouvoir la retirer proprement lors d'un changement de média sélectionné (voir ngOnChanges,
     * removeCurrentMediaLayer).
     */
    mediaDataLayer: BaseLayer;

    /**
     * La couche actuellement interactive (cliquable pour la popup de feature). Mise à jour à chaque
     * chargement de couche de données (voir handleLoadLayers). Le listener de clic unique (voir
     * addFeatureInteraction, enregistré une seule fois dans initMap()) la consulte dynamiquement,
     * pour rester valide après un changement de média sans empiler un nouveau listener à chaque fois.
     */
    interactiveMediaLayer: BaseLayer;
```

Ajouter la méthode `ngOnChanges` (par exemple juste après `ngOnInit`, avant `ngAfterViewInit`) :
```ts
    /**
     * Réagit à un changement de média sélectionné (sélecteur de fichier de l'onglet Carte, voir
     * MapTabComponent). Le tout premier changement (chargement initial) est ignoré : il est déjà
     * géré par ngAfterViewInit. Si la carte n'est pas encore initialisée (this.map == null — cas
     * rare d'une sélection changée avant la fin de l'initialisation asynchrone de la projection), on
     * ne fait rien : ngAfterViewInit lira de toute façon la dernière valeur de this.media au moment
     * où il s'exécutera (Angular affecte les @Input() avant tout hook de cycle de vie).
     */
    ngOnChanges(changes: SimpleChanges): void {
        if (changes.media && !changes.media.firstChange && this.map != null) {
            this.removeCurrentMediaLayer();
            this.handleLoadLayers();
        }
    }
```

Modifier `handleLoadLayers()` (vérifié lignes 417-445) :

**AVANT :**
```ts
    private handleLoadLayers(): void {
        // Gestion chargement des données du JDD
        if (this.metadata != null && this.media != null) {
            let layer;
            if (this.media.connector.interface_contract === MAP_PROTOCOLS.WMS) {
                layer = this.mapLayerFunction.createWmsDataLayer(this.metadata.global_id, this.media);
            } else if (this.media.connector.interface_contract === MAP_PROTOCOLS.WMTS) {
                layer = this.mapLayerFunction.createWmtsDataLayer(this.metadata.global_id, this.media);
            } else if (this.media.connector.interface_contract === MAP_PROTOCOLS.WFS) {
                layer = this.mapLayerFunction.createWfsDataLayer(this.metadata.global_id, this.media);
                this.addFeatureInteraction(layer);
            } else if (this.media.media_type === MediaTypeEnum.File) {
                const mediaFile: MediaFile = this.media as MediaFile;
                if (mediaFile.file_type === FileTypes.GEO_JSON) {
                    this.mapLayerFunction.createGeojsonDataLayer(this.media).subscribe({
                        next: (baseLayer: BaseLayer) => {
                            this.map.getLayers().push(baseLayer);
                            this.addFeatureInteraction(baseLayer);
                        }
                    });
                }
            }

            if (layer != null) {
                this.map.getLayers().push(layer);
            }
        }

    }
```
**APRÈS :**
```ts
    private handleLoadLayers(): void {
        // Gestion chargement des données du JDD
        if (this.metadata != null && this.media != null) {
            let layer;
            if (this.media.connector.interface_contract === MAP_PROTOCOLS.WMS) {
                layer = this.mapLayerFunction.createWmsDataLayer(this.metadata.global_id, this.media);
            } else if (this.media.connector.interface_contract === MAP_PROTOCOLS.WMTS) {
                layer = this.mapLayerFunction.createWmtsDataLayer(this.metadata.global_id, this.media);
            } else if (this.media.connector.interface_contract === MAP_PROTOCOLS.WFS) {
                layer = this.mapLayerFunction.createWfsDataLayer(this.metadata.global_id, this.media);
                this.interactiveMediaLayer = layer;
            } else if (this.media.media_type === MediaTypeEnum.File) {
                const mediaFile: MediaFile = this.media as MediaFile;
                if (mediaFile.file_type === FileTypes.GEO_JSON) {
                    const requestedMedia = this.media;
                    this.mapLayerFunction.createGeojsonDataLayer(this.media).subscribe({
                        next: (baseLayer: BaseLayer) => {
                            // Le média a pu changer pendant le téléchargement (asynchrone) du geojson :
                            // si l'utilisateur a re-sélectionné un autre fichier entre-temps, ce
                            // résultat devenu obsolète est ignoré.
                            if (this.media !== requestedMedia) {
                                return;
                            }
                            this.mediaDataLayer = baseLayer;
                            this.map.getLayers().push(baseLayer);
                            this.interactiveMediaLayer = baseLayer;
                        }
                    });
                }
            }

            if (layer != null) {
                this.mediaDataLayer = layer;
                this.map.getLayers().push(layer);
            }
        }

    }
```

Ajouter `removeCurrentMediaLayer()` et modifier `initMap()` pour enregistrer le listener de clic **une seule fois** :

**AVANT** (fin de `initMap`, vérifiée) :
```ts
        // Chargement des dépendances
        this.handleLoadLayers();
        this.handleMapEvents();

        if (this.initExtent != null) {
            this.map.getView().fit(this.initExtent);
        }
    }
```
**APRÈS :**
```ts
        // Chargement des dépendances
        this.handleLoadLayers();
        this.handleMapEvents();
        this.addFeatureInteraction();

        if (this.initExtent != null) {
            this.map.getView().fit(this.initExtent);
        }
    }

    /**
     * Retire de la carte l'éventuelle couche de données actuellement affichée, et ferme la popup
     * ouverte (qui pourrait référencer une feature de cette couche). Appelé avant de recharger la
     * couche pour un nouveau média sélectionné (voir ngOnChanges).
     * @private
     */
    private removeCurrentMediaLayer(): void {
        if (this.mediaDataLayer != null) {
            this.map.removeLayer(this.mediaDataLayer);
            this.mediaDataLayer = null;
        }
        this.interactiveMediaLayer = null;
        this.handleClosePopup();
    }
```

Enfin, `addFeatureInteraction` change de signature — elle ne prend plus la couche en paramètre (fermeture), elle consulte `this.interactiveMediaLayer` :

**AVANT** (vérifiée en fin de fichier) :
```ts
    addFeatureInteraction(vectorLayer: BaseLayer): void {
        this.map.on('click', (event) => {
            if (this.popupFeature) {
                this.popupFeature.setStyle();
            }

            const feature = this.map.forEachFeatureAtPixel(event.pixel, (clickedFeature, clickedLayer) => {
                    return clickedLayer === vectorLayer ? clickedFeature : null;
                }
            );

            if (feature) {
                const hoveredStyle = getHoveredStyle(feature.getGeometry().getType());
                this.currentHoverStyle = hoveredStyle;
                this.popupFeature = feature as Feature<Geometry>;
                this.popupFeature.setStyle(hoveredStyle);
                const coordinates = feature.getGeometry().getExtent();
                const center = getCenter(coordinates);
                this.popup.setPosition(center);
                this.map.addOverlay(this.popup);
            } else {
                this.popupFeature = null;
                this.map.removeOverlay(this.popup);
            }
        });
    }
```
**APRÈS :**
```ts
    /**
     * Enregistre (une seule fois, voir l'appel dans initMap()) le listener de clic gérant la popup
     * de feature. Consulte this.interactiveMediaLayer dynamiquement (mis à jour à chaque chargement
     * de couche, voir handleLoadLayers) plutôt que de capturer une couche par fermeture : évite
     * d'empiler un nouveau listener map.on('click', ...) à chaque changement de média sélectionné.
     */
    addFeatureInteraction(): void {
        this.map.on('click', (event) => {
            if (this.interactiveMediaLayer == null) {
                return;
            }

            if (this.popupFeature) {
                this.popupFeature.setStyle();
            }

            const feature = this.map.forEachFeatureAtPixel(event.pixel, (clickedFeature, clickedLayer) => {
                    return clickedLayer === this.interactiveMediaLayer ? clickedFeature : null;
                }
            );

            if (feature) {
                const hoveredStyle = getHoveredStyle(feature.getGeometry().getType());
                this.currentHoverStyle = hoveredStyle;
                this.popupFeature = feature as Feature<Geometry>;
                this.popupFeature.setStyle(hoveredStyle);
                const coordinates = feature.getGeometry().getExtent();
                const center = getCenter(coordinates);
                this.popup.setPosition(center);
                this.map.addOverlay(this.popup);
            } else {
                this.popupFeature = null;
                this.map.removeOverlay(this.popup);
            }
        });
    }
```

> **Pourquoi ce refactor est nécessaire (pas cosmétique)** : avant ce lot, `addFeatureInteraction(vectorLayer)` n'était appelée qu'**une fois** au chargement initial (pour une couche WFS ou GeoJSON), donc au plus un seul listener `map.on('click', ...)` existait jamais. Avec le sélecteur de fichier, `handleLoadLayers()` peut désormais être rejouée plusieurs fois dans la vie du composant (une fois par sélection) : sans ce refactor, chaque sélection d'un média WFS/GeoJSON empilerait un **nouveau** listener capturant l'ancienne couche par fermeture, et ces listeners obsolètes casseraient la popup de la couche courante (`else` branch qui la ferme systématiquement dès qu'ils ne trouvent pas leur propre couche, potentiellement après le listener courant). Le passage à un unique listener consultant dynamiquement `this.interactiveMediaLayer` supprime ce risque.

**Commit** : `feat(front): let user pick which file to display in the map tab`
**Branche** : `feat/dataset-map-media-selector`

---

## 6. Validation

Procédure de build/déploiement (Node épinglé) : `PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md`, section 3 « Front Angular » :
```bash
export PATH="/media/simon/DATA4T/Dev/rudi-portal-source/rudi-application/rudi-application-front-office/node_installation/node:$PATH"
node -v   # DOIT afficher v20.15.1
cd /media/simon/DATA4T/Dev/rudi-portal-source/rudi-application/rudi-application-front-office/angular-project
npm run build-prod
ls -la dist/angular-project/   # vérifier l'existence ET la date de modification récente, pas juste l'exit code
```
Pour une itération rapide sans reconstruire l'image (éphémère, sert de rollback au hard-refresh) :
```bash
docker cp dist/angular-project/. rudiplatform-portail-1:/usr/share/nginx/html/
```
Pour la validation finale « vraie » : reconstruire l'image via le module Maven `rudi-application-front-office` (produit `rudi-application-front-office-angular-dist.zip`) puis suivre Phase 3/4 de la procédure (`docker build --target rudi-application-front-office ...`, recréation du conteneur avec le jeu complet de composes).

### 6.1 Choix du JDD témoin

Le JDD `1bff9394-07aa-40b0-af39-9053caa7e0ab` (« Carte des loyers » 2022, 8 médias téléchargeables — déjà utilisé pour valider `PLAN_PR_RUDI_LISTE_FICHIERS_JDD.md`) est un bon point de départ, mais **sa composition exacte (nombre de CSV vs GeoJSON/WMS/WFS) n'a pas pu être vérifiée dans cette exploration statique du code** (pas d'accès direct à la base du nœud/portail depuis l'environnement d'exploration). **Avant de commencer la validation**, l'agent qui implémente doit :
1. Ouvrir la page détail de ce JDD, onglet **Informations → Sources de données** (fonctionnalité déjà livrée par `PLAN_PR_RUDI_LISTE_FICHIERS_JDD.md`, qui affiche `media_name`/l'extension pour chaque panneau).
2. Compter combien de panneaux sont `Fichier "..." (csv)` ou `(xlsx)` (candidats tableau) et combien sont des GeoJSON FILE ou des `Service (wms)`/`Service (wfs)` (candidats carte).
3. S'il y a ≥ 2 candidats tableau **et/ou** ≥ 2 candidats carte, ce JDD suffit pour valider respectivement PR-A et/ou PR-B.
4. Sinon, chercher un autre JDD réel avec la même méthode (onglet Informations d'un JDD BDNB multi-tables pour le tableau ; un JDD géo avec plusieurs fichiers `fichiers_geojson` moissonnés pour la carte), et **le noter dans la PR** pour que Simon sache quel JDD a servi de témoin.

### 6.2 PR-A — Tableau

Sur le JDD retenu (≥ 2 candidats tableau) :
1. Le sélecteur « Fichier à afficher » apparaît au-dessus du tableau, listant tous les candidats CSV/Excel avec leur `media_name` (ou repli `Fichier (extension)`).
2. Sélection par défaut à l'ouverture de l'onglet = comportement identique à avant ce lot (premier candidat de `available_formats`, non-régression).
3. Choisir un 2ᵉ fichier dans le menu → le loader s'affiche brièvement, puis le tableau se recharge avec les **nouvelles** données (colonnes et lignes différentes du 1er fichier — vérifier visuellement que le contenu a bien changé, pas juste le libellé du sélecteur).
4. La recherche (`searchbox`) et l'option « en-tête » se réinitialisent proprement après un changement de sélection (pas de résultats de recherche de l'ancien fichier affichés sur le nouveau).
5. Sur un JDD avec **un seul** fichier tabulaire (ou zéro), le sélecteur **n'apparaît pas** — onglet identique à avant ce lot.
6. Non-régression accès restreint : sur un JDD à accès restreint, `errorAccess` s'affiche toujours correctement (le chemin `hasAccess()` n'a pas changé de comportement, juste été extrait dans `loadTable()`).

### 6.3 PR-B — Carte

Sur le JDD retenu (≥ 2 candidats carte, idéalement un mélange GeoJSON + WMS/WFS si disponible, sinon 2 GeoJSON) :
1. Le sélecteur « Fichier à afficher » apparaît dans le bandeau au-dessus de la carte (`.map-header`), lisible (vérifier le contraste texte/fond `--primary-color` — ajuster le CSS si besoin, voir §5.5).
2. Sélection par défaut = comportement identique à avant ce lot (non-régression sur JDD mono-fichier : sélecteur absent, carte inchangée).
3. Choisir un 2ᵉ média dans le menu → la couche de données affichée sur la carte change (contenu visuellement différent), **sans** que le fond de plan/zoom/centrage ne soit perturbé, et **sans** rechargement complet de la carte OpenLayers (pas de clignotement du fond de plan).
4. Cliquer sur une feature de la nouvelle couche (si GeoJSON/WFS) ouvre la popup correspondante — vérifie que le refactor du listener de clic (`interactiveMediaLayer`) fonctionne après un changement de sélection, **pas seulement au premier chargement**.
5. Basculer plusieurs fois de suite entre les mêmes 2-3 fichiers (aller-retour) → pas de doublon de popup, pas d'accumulation visible de comportement (test manuel du non-empilement de listeners).
6. **`hasMediaError` réactif** : si le JDD témoin a un média SERVICE dont les `connector_parameters` sont incomplets (ou en simuler un via un JDD de test), le sélectionner affiche le message d'erreur `metaData.mapDataTab.error`, **et le sélecteur reste visible et utilisable** pour revenir à un fichier valide (point clé de la demande §5.4).
7. Non-régression `isErrorAccess`/`isErrorServer` : comportement inchangé sur un JDD à accès restreint ou en cas d'erreur serveur au chargement des fonds de plan.
8. Sur le JDD WMS-only (« Bases gravimétriques », cité dans `PLAN_PR_RUDI_TABLEAU_CARTE.md`), si un seul média SERVICE existe : sélecteur absent, comportement identique à avant ce lot (non-régression zéro/un candidat).

### 6.4 Checklist de contrôle (revue)

- [ ] PR-A : `isSpreadsheetDisplayed` ne fait plus d'effet de bord ; `tableMediaCandidates` construit une fois dans `ngOnInit`.
- [ ] PR-A : sélecteur absent si `candidates.length <= 1` ; visible et fonctionnel sinon.
- [ ] PR-A : `loadTable(media)` correctement extrait, rejoué à l'identique pour le chargement initial ET la sélection.
- [ ] PR-B : `isMapDisplayed` ne fait plus d'effet de bord ; `mapMediaCandidates` construit une fois dans `ngOnInit`.
- [ ] PR-B : `DetailComponent.handleMetadataProperties`/`mapHasError`/`hasAllRequiredKeys`/`isValidObject` supprimées, imports inutilisés (`ConnectorConnectorParameters`, `MAP_CONNECTOR_PARAMETERS_REQUIRED`, `ErrorBoxComponent`) retirés de `detail.component.ts`.
- [ ] PR-B : `MapTabComponent.hasMediaError` recalculé dans `ngOnInit` **et** `onMediaSelected` ; GeoJSON FILE toujours accepté sans `connector_parameters`.
- [ ] PR-B : `MapComponent.addFeatureInteraction()` enregistrée **une seule fois** (appel unique dans `initMap()`), plus aucun appel avec paramètre `vectorLayer` ailleurs dans le fichier.
- [ ] PR-B : `handleLoadLayers()` protège contre la réponse GeoJSON asynchrone obsolète (`this.media !== requestedMedia`).
- [ ] PR-B : `ngOnChanges` ignore le premier changement (`firstChange`) et ne fait rien si `this.map == null`.
- [ ] Aucun fichier de `moissonneuse-batteuse` modifié.
- [ ] Chaque PR = 1 commit logique, message conventionnel anglais, **sans** trailer d'IA (contributions upstream signées par Simon).
- [ ] Validation §6.2 exécutée pour PR-A, §6.3 pour PR-B, sur un JDD réel multi-fichiers avant de considérer chaque PR terminée.

---

## 7. Ce qu'on ne touche pas

- **`SpreadsheetComponent`** (leaf ag-grid, `.../components/spreadsheet/spreadsheet.component.ts`) : le mécanisme de destruction/recréation via le `@if (displayTable)` imbriqué dans `@if (!displayTableLoading)` suffit déjà à le rafraîchir correctement à chaque sélection — aucune modification requise.
- **`data-set-infos.component.ts`/`.html`** (liste des fichiers, onglet Informations) : fonctionnalité livrée séparément par `PLAN_PR_RUDI_LISTE_FICHIERS_JDD.md`, sans lien avec la prévisualisation tableau/carte.
- **`/media/simon/DATA4T/Dev/moissonneuse-batteuse`** (pipeline) : aucun fichier de ce dépôt n'est modifié par ce plan — 100 % front.
- **Le bug D4b / `default_crs` comme projection de vue** (`map.component.ts`, `ngAfterViewInit`, PR-7 de `PLAN_PR_RUDI_TABLEAU_CARTE.md`) : non appliqué actuellement, **non corrigé par ce plan**. Limitation connue documentée en §8.
- **D5 (WMS 500/dédoublonnage de query, `RerouteToRequestUrlFilter.java`)** et **`ConcurrentHashMap` (`ObjectsUtils.java`)** : sujets backend indépendants de ce plan (PR-4/PR-5 de `PLAN_PR_RUDI_TABLEAU_CARTE.md`), non touchés.
- **Le routeur Traefik `konsult-natif`** et tout contournement d'infra (`traefik-dynamic.yml`, shim WMS) : hors périmètre front, non touchés.

---

## 8. Limitation connue, assumée pour ce lot

Si un JDD propose des médias cartographiables avec des `default_crs` **différents** entre eux, basculer via le sélecteur de fichier entre deux médias de CRS différents affichera la seconde couche mal projetée (car la projection de la `View` OpenLayers reste figée sur celle du **premier** média chargé — bug D4b préexistant, non corrigé ici, cf. §3.4 et §7). Aujourd'hui le pipeline moissonneuse-batteuse ne produit que `default_crs=EPSG:3857`, donc ce cas ne se manifestera pas sur nos JDD ; il redeviendra pertinent si PR-7 de `PLAN_PR_RUDI_TABLEAU_CARTE.md` est un jour appliquée et qu'un producteur RUDI publie des CRS hétérogènes entre médias d'un même JDD.

---

## Annexe — Fichiers critiques pour l'implémentation

- `rudi-application/rudi-application-front-office/angular-project/src/app/features/data-set/pages/detail/detail.component.ts` (+ `.html`)
- `rudi-application/rudi-application-front-office/angular-project/src/app/features/data-set/components/spreadsheet-tab/spreadsheet-tab.component.ts` (+ `.html` + `.scss`)
- `rudi-application/rudi-application-front-office/angular-project/src/app/features/data-set/components/map-tab/map-tab.component.ts` (+ `.html` + `.scss`)
- `rudi-application/rudi-application-front-office/angular-project/src/app/shared/core/maps/map/map.component.ts`
- `rudi-application/rudi-application-front-office/angular-project/src/assets/i18n/fr.json`

(chemins racine : `/media/simon/DATA4T/Dev/rudi-portal-source/`)

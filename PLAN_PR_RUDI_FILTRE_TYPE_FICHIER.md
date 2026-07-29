# Ajouter un filtre « Type de fichier » (choix multiple) au catalogue Angular (portail RUDI) — Guide d'implémentation

> **À lire en entier avant de toucher quoi que ce soit.** Ce document est un plan d'exécution
> détaillé pour un futur agent d'implémentation, moins puissant que celui qui l'a rédigé. Il
> transforme une demande d'évolution du catalogue (« ajouter un filtre par type de fichier, choix
> multiple, ex. CSV+JSON, ou WMS+WFS, ou PDF ») en correctif propre du code source du portail.
>
> **Règles impératives pour l'agent qui implémente :**
> 1. **Une seule PR pour toute cette fonctionnalité.** Contrairement à d'autres plans de ce dépôt
>    (`PLAN_PR_RUDI_TABLEAU_CARTE.md`, `PLAN_PR_RUDI_LISTE_FICHIERS_JDD.md`) qui bundlent plusieurs
>    bugs indépendants à séparer en PR atomiques, **ici il s'agit d'une seule fonctionnalité
>    cohérente** : modèle, service, UI et branchement sont interdépendants (un filtre sans UI ne sert
>    à rien, une UI sans le branchement dans la recherche ne fait rien). Ne split pas en plusieurs
>    PR. Les « étapes » ci-dessous sont un ordre d'implémentation à l'intérieur de la même branche,
>    pas des PR séparées.
> 2. **Toujours `Read` le fichier avant de l'éditer** — les numéros de ligne ci-dessous ont été
>    vérifiés le 2026-07-27 par lecture directe des fichiers réels, mais peuvent avoir bougé d'ici
>    l'implémentation. Repère le code par son **contenu**, pas par son numéro de ligne.
> 3. **Ne rien inventer.** Si un fichier/chemin/commande ne correspond pas à ce qui est décrit,
>    **arrête-toi et signale-le** au lieu de deviner.
> 4. **Contrainte impérative posée par Simon, à respecter dans toute future évolution RUDI** : les
>    évolutions du nœud et du portail RUDI ne doivent **jamais** dépendre de comportements propres à
>    notre pipeline de moisson (`moissonneuse-batteuse`) que les autres producteurs RUDI n'auraient
>    pas. Concrètement ici : la liste des types de fichiers proposés dans le filtre **ne doit pas
>    être une liste figée à la main** (ex. `['csv','json','pdf']`) mais **calculée dynamiquement** à
>    partir du contenu réel du catalogue — le portail sert potentiellement des JDD d'autres
>    producteurs que Rennes Métropole, avec d'autres formats. Voir §3.2.
> 5. Après l'implémentation complète, exécuter la validation manuelle décrite en §6 **avant** de
>    considérer la tâche terminée.

---

## 1. Contexte

Le catalogue du portail (page `/catalogue`, liste des JDD) propose déjà des filtres multi-critères
dans une barre d'outils : **Thématique**, **Producteur**, **Couverture temporelle**, **Statut
(ouvert/restreint)**. Chacun s'ouvre dans un menu déroulant avec une liste de cases à cocher (sauf
Statut qui est un choix unique). Objectif de cette évolution : ajouter un filtre **Type de fichier**
au même endroit, avec la même ergonomie (menu déroulant, cases à cocher, choix multiple), permettant
par exemple de ne montrer que les JDD ayant un CSV ou un JSON, ou seulement ceux exposant un WMS/WFS,
ou seulement ceux avec un PDF.

**Diagnostic (exploration du 2026-07-27, lecture directe du code front ET du client API généré)** :

- Les filtres existants (Thématique, Producteur, Couverture temporelle, Statut) sont **tous
  transmis au backend** via `KonsultMetierService.searchMetadatas()` →
  `konsultService.searchMetadatas(freeText, themes, keywords, producerNames, dateDebut, dateFin,
  restrictedAccess, gdprSensitive, globalId, producerUuids, offset, limit, order)` — la recherche,
  la pagination **et le total** viennent tous du serveur (microservice `konsult`, backend Java du
  nœud/portail RUDI).
- **Le type de fichier/média n'existe pas comme paramètre de recherche côté backend.** Vérifié dans
  le client généré (`micro_service_modules/konsult/konsult-api/api/konsult.service.ts:1168`, méthode
  `searchMetadatas`) : aucun paramètre `media_type`/`file_type`/`format`. Ajouter ce paramètre
  impliquerait de modifier le microservice Java `konsult` (backend), le spec OpenAPI, et de
  régénérer le client Angular — un changement d'une tout autre ampleur (autre dépôt/langage), **hors
  scope de ce plan**. Voir §7 « Piste non retenue ».
- **La donnée existe cependant déjà côté front, dans les résultats déjà récupérés.** Chaque
  `Metadata` retourné par la recherche porte un tableau `available_formats: Media[]` (déjà utilisé
  pour l'affichage détail du JDD, cf. `PLAN_PR_RUDI_LISTE_FICHIERS_JDD.md`). Pour chaque `Media` :
  - `media_type` vaut `'FILE'`, `'SERVICE'` ou `'SERIES'` (`Media.MediaTypeEnum`, fichier
    `micro_service_modules/api-kaccess/model/media.ts`).
  - Pour `FILE` : `file_type` est un type MIME (`MediaFile.file_type`,
    `micro_service_modules/api-kaccess/model/mediaFile.ts`) — le front sait déjà le convertir en
    extension lisible via `KonsultMetierService.getMediaFileExtension(media)`
    (`konsult-metier.service.ts:242-254`, utilise le package `mime` + une base MIME custom), déjà
    utilisée pour afficher `Fichier (csv)` dans le détail du JDD.
  - Pour `SERVICE` : `connector.interface_contract` (`Connector.interface_contract`,
    `micro_service_modules/api-kaccess/model/connector.ts`) vaut `'wms'`, `'wfs'` ou `'wmts'` (enum
    `MAP_PROTOCOLS`, `src/app/core/services/map/map-protocols.ts`) pour les couches cartographiques,
    ou `'dwnl'` pour un lien de téléchargement direct classique (mais dans ce cas `media_type` vaut
    `'FILE'`, pas `'SERVICE'` — voir `detail-functions.ts:39`).

**Conclusion d'architecture : ce filtre doit être appliqué côté front (client-side), pas transmis au
backend.** Cela casse une hypothèse implicite du code existant : jusqu'ici, pagination et total
viennent toujours du serveur. Voir §4 pour comment ce plan gère cette bascule proprement, sans
casser l'affichage ni la pagination.

---

## 2. Emplacements

| Rôle | Chemin |
|---|---|
| Portail source (monorepo Java + Angular) | `/media/simon/DATA4T/Dev/rudi-portal-source` |
| Racine du front Angular | `rudi-application/rudi-application-front-office/angular-project/` |
| Procédure de build/déploiement/validation du front | `/media/simon/DATA4T/Dev/moissonneuse-batteuse/PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md` (Node épinglé v20.15.1, cible Dockerfile `rudi-application-front-office`) |
| Pipeline moissonneuse-batteuse (référence uniquement, **non modifié** par ce plan) | `/media/simon/DATA4T/Dev/moissonneuse-batteuse` |

Stack : Angular 19, composants **standalone** (imports déclarés dans `@Component({imports:[...]})`,
pas de NgModule), syntaxe de template moderne `@if`/`@for`. Tous les chemins ci-dessous sont relatifs
à `.../angular-project/`.

### 2.1 Fichiers existants à lire pour comprendre le pattern (ne pas éditer sauf indication contraire)

| Fichier | Rôle |
|---|---|
| `src/app/shared/models/filters.ts` | Interface `Filters` — état global des filtres |
| `src/app/core/services/filters.service.ts` | `FiltersService` — orchestre tous les filtres (`BehaviorSubject<Filters>`) |
| `src/app/core/services/filters/filter.ts` | Classe abstraite `Filter<T>` — base commune |
| `src/app/core/services/filters/array-filter.ts` | Classe abstraite `ArrayFilter` (filtres multi-valeurs — `string[]`) |
| `src/app/core/services/filters/producer-names-filter.ts` | Exemple minimal de filtre multi-valeurs (à dupliquer) |
| `src/app/core/services/konsult-metier.service.ts` | `KonsultMetierService` — appel API recherche + `getMediaFileExtension()` |
| `src/app/shared/business/dataset/filters/filter-forms/filter-form.component.ts` | Classe abstraite du composant de formulaire de filtre |
| `src/app/shared/business/dataset/filters/filter-forms/array-filter-form.component.ts` | Classe abstraite du composant multi-valeurs (cases à cocher) |
| `src/app/shared/business/dataset/filters/filter-forms/producer-names-filter-form/` (`.ts`+`.html`+`.scss`) | Exemple complet de composant de formulaire multi-valeurs (**modèle à dupliquer**) |
| `src/app/shared/business/dataset/filters/filter-forms/item.ts` | Interface `Item { name: string; value: any; }` |
| `src/app/shared/business/dataset/filters/filter-menu/filter-menu.component.ts` | Bouton + menu déroulant Material générique, déjà réutilisable tel quel |
| `src/app/shared/business/dataset/common/dataset-list-banner/` (`.ts`+`.html`) | Barre d'outils qui assemble tous les `app-filter-menu` |
| `src/app/shared/business/dataset/filters/list-container/` (`.ts`+`.html`) | Composant parent qui peuple `themes`/`producerNames` et les items sélectionnés (chips) |
| `src/app/shared/business/dataset/filters/filters-items-list/` (`.ts`+`.html`) | Affichage des « chips » de filtres actifs + suppression individuelle |
| `src/app/shared/business/dataset/common/dataset-list/dataset-list.component.ts` | **Composant qui appelle réellement la recherche** (`searchMetadatas()`) et gère la pagination |
| `src/app/shared/utils/page-result-utils.ts` | `PageResultUtils.fetchAllElementsUsing()` — **pattern déjà existant** pour récupérer TOUTES les pages d'une recherche paginée (déjà utilisé par `getMetadatasByUuids()`) |
| `src/assets/i18n/fr.json` | Traductions (bloc `"filterBox"` autour de la ligne 451) |

---

## 3. Principe de fonctionnement du nouveau filtre

### 3.1 Dérivation du « type » d'un média

Un même JDD (`Metadata`) peut avoir plusieurs médias dans `available_formats`. On calcule, pour
chaque média, une étiquette de type :

- Si `media.media_type === 'FILE'` → l'extension déjà calculée par
  `KonsultMetierService.getMediaFileExtension(media)`, mise en majuscules (ex. `CSV`, `JSON`,
  `PDF`, `GEOJSON`, `ZIP`, `XLSX`...). **Ne pas réinventer un mapping MIME→extension : réutiliser
  cette méthode existante**, pour que le type affiché dans le filtre corresponde exactement à ce qui
  est affiché dans le détail du JDD (`Fichier (csv)`).
- Si `media.media_type === 'SERVICE'` → `media.connector?.interface_contract` mis en majuscules
  (ex. `WMS`, `WFS`, `WMTS`). Si absent, ignorer ce média pour le filtre (ne pas planter).
- Si `media.media_type === 'SERIES'` (rare, non utilisé par le pipeline moissonneuse-batteuse
  actuellement) → ignorer ce média pour le filtre (aucun type exploitable de façon fiable et
  générique).

Un JDD peut donc avoir plusieurs types (ex. un CSV + un PDF de documentation + un WMS). **Le
prédicat de correspondance est un OU** : un JDD est retenu par le filtre si **au moins un** de ses
médias correspond à **au moins un** des types cochés — c'est le sens naturel de « cocher CSV et
JSON » évoqué dans la demande (afficher les JDD qui ont l'un ou l'autre).

### 3.2 Liste des cases à cocher proposées : calcul dynamique, pas de liste figée

Comme rappelé en règle 4 en tête de ce document : ne pas coder en dur une liste
`['csv', 'json', 'pdf', 'wms', 'wfs']`. À la place, dupliquer le pattern déjà utilisé pour peupler
`themes`/`producerNames` (facettes dynamiques), mais calculé côté front puisqu'il n'existe pas de
facette backend pour ce champ (voir §7) :

- Nouvelle méthode `KonsultMetierService.getAvailableFileTypes(): Observable<string[]>` qui :
  1. Récupère **tout le catalogue, sans aucun filtre actif**, via
     `PageResultUtils.fetchAllElementsUsing(offset => this.searchMetadatas(EMPTY_FILTERS_LIKE, null, offset, MAX_RESULTS_PER_REQUEST))`
     — reprendre exactement le pattern de `getMetadatasByUuids()` (`konsult-metier.service.ts:126-143`),
     mais avec des filtres vides (recherche/thèmes/producteurs/dates tous vides) au lieu de
     `globalIds`.
  2. Pour chaque `Metadata` récupéré, calcule la liste de types de ses `available_formats` (§3.1).
  3. Fait l'union de tous ces types, déduplique, trie alphabétiquement, retourne le tableau de
     `string[]`.
- Cette méthode est appelée **une seule fois**, au chargement du catalogue (voir §4.3), exactement
  comme `getProducerNames()`/`getThemeCodes()` le sont déjà dans `list-container.component.ts`
  (`ngOnInit`, ligne 131-133).
- Le dépôt compte actuellement environ 400 JDD (cf. mémoire projet) : ce scan complet représente
  ~4 requêtes de 100 résultats (`MAX_RESULTS_PER_REQUEST = 100`,
  `konsult-metier.service.ts:17`) — coût négligeable, fait une fois par chargement de page.

### 3.3 Application du filtre à la recherche : bascule de mode dans `dataset-list.component.ts`

C'est le point le plus délicat. Comportement actuel de `searchMetadatas()`
(`dataset-list.component.ts:124-139`) : appelle le backend avec `offset`/`limit`, le backend renvoie
directement la page + le total, affichés tels quels. Comme le type de fichier n'est pas filtrable
côté backend, quand ce filtre est actif il faut :

1. Récupérer **tous** les JDD qui correspondent aux **autres** filtres actifs (thème, producteur,
   dates, recherche libre, statut) — pas seulement la page courante — via le même pattern
   `PageResultUtils.fetchAllElementsUsing`, mais cette fois **avec** les filtres courants
   (`this.filtersService.currentFilters`), en réutilisant `konsultMetierService.searchMetadatas(...)`
   avec `offset`/`MAX_RESULTS_PER_REQUEST` croissants.
2. Filtrer ce tableau en mémoire avec le prédicat du §3.1 (types cochés).
3. Découper (`slice`) le tableau filtré à `[this.offset, this.offset + this.limit]` pour obtenir la
   page courante, et poser `total = tableauFiltré.length`.
4. Assigner `this.metadataList = { total, items: pageDécoupée }` — **exactement la même forme
   d'objet** que le chemin backend actuel, donc **aucune modification du template
   `dataset-list.component.html` n'est nécessaire** (la pipe `paginate` de `ngx-pagination` y est déjà
   utilisée avec un `totalItems` explicite différent de `metadataListItems.length`, ce qui est
   exactement ce pattern de pagination « pré-découpée côté source de données » — vérifier ce
   comportement fait partie de la validation, §6).
5. Quand le filtre type de fichier **n'est pas actif**, garder strictement le chemin backend actuel
   inchangé (pas de régression sur le cas nominal, le plus fréquent).

---

## 4. Étapes d'implémentation

### 4.1 Modèle et service de filtre

**Fichier `src/app/shared/models/filters.ts`** — ajouter un champ à l'interface (après `producerNames`) :
```ts
    fileTypes: string[];
```

**Fichier `src/app/core/services/filters.service.ts`** :
- Dans `EMPTY_FILTERS` (ligne ~12-25), ajouter `fileTypes: [],`.
- Importer et instancier un nouveau `FileTypesFilter` comme les autres (voir ligne 8/37/38) :
  ```ts
  readonly fileTypesFilter = new FileTypesFilter(this, this.filters);
  ```
- L'ajouter dans `childrenFilters` (ligne 43-50), pour qu'il soit inclus dans `deleteAllFilters()`
  et `isFiltered`.

**Nouveau fichier `src/app/core/services/filters/file-types-filter.ts`** (copier exactement
`producer-names-filter.ts`, juste renommer) :
```ts
import {Filters} from '@shared/models/filters';
import {BehaviorSubject} from 'rxjs';
import {FiltersService} from '../filters.service';
import {ArrayFilter} from './array-filter';

export class FileTypesFilter extends ArrayFilter {

  constructor(filtersService: FiltersService, filters: BehaviorSubject<Filters>) {
    super(filtersService, filters);
  }

  protected get filtersKey(): string {
    return 'fileTypes';
  }

}
```

**Fichier `src/app/core/services/konsult-metier.service.ts`, méthode `getMetadatasByUuids`
(lignes 126-143)** : le littéral `Filters` construit là (lignes 129-140) doit aussi avoir
`fileTypes: [],` — sinon compilation TypeScript en échec (champ obligatoire manquant sur
l'interface `Filters`).

### 4.2 Helpers de dérivation de type — `KonsultMetierService`

Dans `src/app/core/services/konsult-metier.service.ts`, à côté de `getMediaFileExtension()`
(ligne 242-254), ajouter :

```ts
const UNKNOWN_MEDIA_TYPE = 'AUTRE';

getMediaTypeLabel(media: Media): string {
    if (media.media_type === MediaTypeEnum.File) {
        return this.getMediaFileExtension(media).toUpperCase();
    }
    if (media.media_type === MediaTypeEnum.Service) {
        const interfaceContract = media.connector?.interface_contract;
        return interfaceContract ? interfaceContract.toUpperCase() : UNKNOWN_MEDIA_TYPE;
    }
    return UNKNOWN_MEDIA_TYPE;
}

getDatasetFileTypes(metadata: Metadata): string[] {
    const types = (metadata.available_formats ?? [])
        .map(media => this.getMediaTypeLabel(media))
        .filter(type => type !== UNKNOWN_MEDIA_TYPE);
    return Array.from(new Set(types));
}

datasetMatchesFileTypes(metadata: Metadata, selectedTypes: string[]): boolean {
    if (!selectedTypes?.length) {
        return true;
    }
    const datasetTypes = this.getDatasetFileTypes(metadata);
    return selectedTypes.some(type => datasetTypes.includes(type));
}

getAvailableFileTypes(): Observable<string[]> {
    return PageResultUtils.fetchAllElementsUsing<MetadataList, Metadata>(offset =>
        this.searchMetadatas({
            search: '',
            themes: [],
            keywords: [],
            producerNames: [],
            dates: {debut: '', fin: ''},
            order: DEFAULT_ORDER_VALUE,
            accessStatus: null,
            globalIds: [],
            producerUuids: [],
            fileTypes: [],
        }, null, offset, MAX_RESULTS_PER_REQUEST)
    ).pipe(
        map(metadatas => {
            const allTypes = metadatas.flatMap(metadata => this.getDatasetFileTypes(metadata));
            return Array.from(new Set(allTypes)).sort();
        })
    );
}

searchAllMetadatasMatchingFilters(filters: Filters, accessStatusHiddenValues?: AccessStatusFiltersType[]): Observable<Metadata[]> {
    return PageResultUtils.fetchAllElementsUsing<MetadataList, Metadata>(offset =>
        this.searchMetadatas(filters, accessStatusHiddenValues, offset, MAX_RESULTS_PER_REQUEST)
    );
}
```

Notes :
- `MediaTypeEnum` est déjà importable via `Media.MediaTypeEnum` (voir imports existants dans
  `dataset-informations.component.ts:25` pour la syntaxe exacte `import MediaTypeEnum =
  Media.MediaTypeEnum;` — à ajouter en haut de `konsult-metier.service.ts` si absent).
- `PageResultUtils` n'est pas encore importé dans ce fichier — ajouter
  `import {PageResultUtils} from '@shared/utils/page-result-utils';` (vérifier l'alias exact utilisé
  ailleurs dans ce même fichier, ex. `MetadataUtils` importé en `'@shared/utils/metadata-utils'`
  ligne 4 — suivre le même style d'import).
- Vérifier que `map` est déjà importé depuis `rxjs/operators` (déjà utilisé ligne 224, 230) —
  réutiliser.

### 4.3 Composant de formulaire (cases à cocher)

Dupliquer intégralement le dossier
`src/app/shared/business/dataset/filters/filter-forms/producer-names-filter-form/` en
`.../filter-forms/file-types-filter-form/`, avec les 3 fichiers renommés
(`file-types-filter-form.component.ts`/`.html`/`.scss`).

**`file-types-filter-form.component.ts`** — remplacer le contenu de la classe :
```ts
import {Component} from '@angular/core';
import {FiltersService} from '@core/services/filters.service';
import {ArrayFilter} from '@core/services/filters/array-filter';
import {ArrayFilterFormComponent} from '@shared/business/dataset/filters/filter-forms/array-filter-form.component';
import {Item} from '@shared/business/dataset/filters/filter-forms/item';

import {FormsModule, ReactiveFormsModule} from '@angular/forms';
import {MatCheckbox} from '@angular/material/checkbox';
import {MatButton} from '@angular/material/button';
import {TranslatePipe} from '@ngx-translate/core';

@Component({
    selector: 'app-file-types-filter-form',
    templateUrl: './file-types-filter-form.component.html',
    styleUrls: ['./file-types-filter-form.component.scss'],
    imports: [FormsModule, ReactiveFormsModule, MatCheckbox, MatButton, TranslatePipe]
})
export class FileTypesFilterFormComponent extends ArrayFilterFormComponent<string> {

    constructor(
        protected readonly filtersService: FiltersService
    ) {
        super(filtersService);
    }

    get formArrayName(): string {
        return 'fileTypes';
    }

    protected get formGroupName(): string {
        return 'fileType';
    }

    protected getItemFromValue(value: string): Item {
        return {
            name: value,
            value
        };
    }

    protected getFilterFrom(filtersService: FiltersService): ArrayFilter {
        return filtersService.fileTypesFilter;
    }

}
```

**`file-types-filter-form.component.html`** — identique au fichier producteur (juste vérifier que le
sélecteur `app-producer-names-filter-form` n'apparaît pas dans le HTML, sinon le corriger — a priori
ce template est générique et ne référence pas le composant par son propre nom, mais **vérifier en
lisant le fichier copié**).

### 4.4 Branchement dans la barre d'outils

**`src/app/shared/business/dataset/common/dataset-list-banner/dataset-list-banner.component.ts`** :
- Importer `FileTypesFilterFormComponent` et l'ajouter au tableau `imports`.
- Ajouter `@Input() fileTypes: string[];` et
  `@Output() selectedFileTypeItemsChange = new EventEmitter<Item[]>();`.

**`.../dataset-list-banner.component.html`** — ajouter un nouveau bloc `<app-filter-menu>` après
celui du Producteur (après la ligne 30), sur le même modèle :
```html
<app-filter-menu
    #fileTypesMenu
    (closed)="fileTypesForm.revert()"
    [counter$]="fileTypesForm.counter$"
    buttonTextKey="filterBox.typeFichier">
    <app-file-types-filter-form
        #fileTypesForm
        (selectedItemsChange)="selectedFileTypeItemsChange.emit($event)"
        (submitFormEvent)="fileTypesMenu.close()"
        [values]="fileTypes"
        ulClass="ps-0">
    </app-file-types-filter-form>
</app-filter-menu>
```

### 4.5 `list-container.component.ts` / `.html`

**`.ts`** :
- Ajouter `fileTypes: string[];` (à côté de `producerNames: string[];`, ligne 81).
- Ajouter `selectedFileTypeItems: Item[] = [];` (à côté de `selectedProducerItems`, ligne 85).
- Dans `ngOnInit()` (lignes 126-136), à côté de l'appel `getProducerNames()`, ajouter :
  ```ts
  this.konsultMetierService.getAvailableFileTypes().subscribe(
      fileTypes => this.fileTypes = fileTypes
  );
  ```
- Dans le getter `hasSelectedItems` (lignes 101-108), ajouter
  `this.selectedFileTypeItems?.length > 0 ||` à la disjonction.

**`.html`** :
- Sur `<app-dataset-list-banner>` (lignes 10-20), ajouter :
  ```html
  (selectedFileTypeItemsChange)="selectedFileTypeItems = $event"
  [fileTypes]="fileTypes"
  ```
- Sur `<app-filters-items-list>` (lignes 22-29), ajouter :
  ```html
  [selectedFileTypeItems]="selectedFileTypeItems"
  ```

### 4.6 Chips de filtres actifs — `filters-items-list.component.ts` / `.html`

**`.ts`** :
- Ajouter `@Input() selectedFileTypeItems: Item[];` (à côté de `selectedProducerItems`, ligne 27).
- Ajouter une méthode :
  ```ts
  deleteFileTypeFilter(fileType: Item): void {
      this.filtersService.fileTypesFilter.remove(fileType.value);
  }
  ```
- Dans `hasSelectedAllOtherFiltre()` (lignes 50-61), ajouter une branche
  `else if (this.selectedFileTypeItems.length > 0) { return this.selectedFileTypeItems.some(value => value.value !== null); }`
  (même style que les branches existantes — **attention à l'ordre des `else if`**, garder la
  structure telle quelle et juste insérer une branche supplémentaire).

**`.html`** — dupliquer le bloc `<!-- PRODUCER -->` (lignes 16-28) en `<!-- FILE TYPE -->` juste
après, en remplaçant `selectedProducerItems`/`producer`/`deleteProducerFilter` par
`selectedFileTypeItems`/`fileType`/`deleteFileTypeFilter`.

### 4.7 Application réelle du filtre — `dataset-list.component.ts`

C'est l'étape qui rend le filtre fonctionnel (les étapes précédentes ne faisaient que le rendre
visible/cochable). Modifier `searchMetadatas()` (lignes 124-139) :

```ts
searchMetadatas(): void {
    this.isLoading = true;
    const fileTypes = this.filtersService.fileTypesFilter.value;
    if (fileTypes?.length) {
        this.searchMetadatasFilteredByFileType(fileTypes);
        return;
    }
    this.konsultMetierService
        .searchMetadatas(this.filtersService.currentFilters, this.accessStatusHiddenValues, this.offset, this.limit)
        .subscribe({
            next: (data) => {
                this.metadataList = data ?? EMPTY_METADATA_LIST;
                this.metadataListTotal.emit(data.total);
                this.isLoading = false;
            },
            error: (error) => {
                this.isLoading = false;
                this.logService.error('getMetadatas failed', error.message);
            }
        });
}

private searchMetadatasFilteredByFileType(fileTypes: string[]): void {
    this.konsultMetierService
        .searchAllMetadatasMatchingFilters(this.filtersService.currentFilters, this.accessStatusHiddenValues)
        .subscribe({
            next: (allMetadatas) => {
                const filtered = allMetadatas.filter(metadata =>
                    this.konsultMetierService.datasetMatchesFileTypes(metadata, fileTypes)
                );
                this.metadataList = {
                    total: filtered.length,
                    items: filtered.slice(this.offset, this.offset + this.limit)
                };
                this.metadataListTotal.emit(this.metadataList.total);
                this.isLoading = false;
            },
            error: (error) => {
                this.isLoading = false;
                this.logService.error('getMetadatas (file type filter) failed', error.message);
            }
        });
}
```

**Ne pas modifier `dataset-list.component.html`** — la forme de `this.metadataList` reste identique
(`{total, items}`), donc la pipe `paginate` existante continue de fonctionner sans changement (à
confirmer en validation, §6).

### 4.8 Traductions

**`src/assets/i18n/fr.json`**, bloc `"filterBox"` (autour de la ligne 451-468) — ajouter une clé,
par exemple après `"producteur": "Producteur",` :
```json
        "typeFichier": "Type de fichier",
```

---

## 5. Récapitulatif des fichiers touchés

| Fichier | Nature du changement |
|---|---|
| `src/app/shared/models/filters.ts` | +1 champ `fileTypes` |
| `src/app/core/services/filters.service.ts` | +1 filtre enregistré |
| `src/app/core/services/filters/file-types-filter.ts` | **nouveau** (copie de `producer-names-filter.ts`) |
| `src/app/core/services/konsult-metier.service.ts` | +4 méthodes, +1 champ dans `getMetadatasByUuids` |
| `.../filter-forms/file-types-filter-form/*.ts/.html/.scss` | **nouveau** (copie de `producer-names-filter-form`) |
| `.../dataset-list-banner.component.ts/.html` | +1 `<app-filter-menu>`, +1 `@Input`/`@Output` |
| `.../filters/list-container/list-container.component.ts/.html` | +1 fetch initial, +wiring |
| `.../filters-items-list/filters-items-list.component.ts/.html` | +1 chip + suppression |
| `.../common/dataset-list/dataset-list.component.ts` | bascule de mode dans `searchMetadatas()` |
| `src/assets/i18n/fr.json` | +1 clé de traduction |

---

## 6. Validation manuelle

Suivre `PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md` (§3 Front Angular, Node v20.15.1 épinglé) pour builder
et déployer le front modifié, puis sur le catalogue en conditions réelles :

1. **Build** : vérifier que `ng build`/`ng serve` compile sans erreur TypeScript (le champ
   `fileTypes` ajouté à l'interface `Filters` est obligatoire — toute construction littérale d'un
   objet `Filters` ailleurs dans le code qui l'omettrait ferait échouer la compilation ; `grep -rn
   "): Filters\|Filters = {" src/app` pour s'assurer qu'aucun autre endroit que
   `getMetadatasByUuids` ne construit un littéral `Filters` complet).
2. **Le bouton « Type de fichier » apparaît** dans la barre d'outils du catalogue, avec la même
   apparence que Thématique/Producteur.
3. **La liste des cases à cocher n'est pas vide** et correspond aux types réellement présents dans
   le catalogue (comparer avec quelques fiches JDD connues, ex. un JDD BDNB avec CSV, un JDD avec
   WMS).
4. **Cocher un seul type** (ex. `PDF`) → seuls les JDD ayant un média PDF dans `available_formats`
   s'affichent ; le total affiché (« X résultats ») correspond au nombre réellement filtré, pas au
   total du catalogue.
5. **Cocher deux types** (ex. `CSV` + `JSON`) → les JDD ayant l'un OU l'autre s'affichent (union, pas
   intersection).
6. **Cocher `WMS` + `WFS`** → uniquement les JDD géo concernés.
7. **Pagination** : avec un filtre actif qui laisse plus de `limit` (36) résultats, vérifier que le
   changement de page affiche bien des JDD différents (pas de doublon ni de page vide) — c'est le
   point le plus fragile de ce plan (slice manuel + `ngx-pagination`), **tester spécifiquement ce
   cas**.
8. **Combiner** avec un autre filtre actif (ex. Thématique) → le filtre type de fichier s'applique
   bien en plus, pas à la place.
9. **Chip de suppression** : le type coché apparaît comme chip sous la barre de filtres, et cliquer
   sur sa croix le décoche et relance la recherche (retour au mode backend normal si c'était le
   dernier type coché).
10. **« Effacer tout »** réinitialise aussi ce filtre.
11. **Décocher tout** (aucun type sélectionné) → retour exact au comportement actuel (pagination
    backend), sans régression sur le nombre de résultats affichés par rapport à avant cette PR.
12. Contrôler la console navigateur pendant les tests 4-8 : pas d'erreur, pas de requête en boucle.

---

## 7. Piste non retenue : filtrage côté backend

L'alternative propre à long terme serait d'exposer un vrai paramètre de recherche côté backend
(microservice Java `konsult`, ex. `mediaTypes`/`fileFormats` en paramètre de
`GET /v1/resources/search` ou équivalent), avec une vraie facette agrégée (comme
`producer_organization_name`/`theme` déjà exposées via `searchMetadataFacets()`,
`konsult-metier.service.ts:213-220`). Cela donnerait une pagination et un total corrects nativement,
sans le contournement client-side de ce plan (fetch intégral + filtre + pagination manuelle en
mémoire), et bénéficierait à **tous** les producteurs RUDI consultant cette API (cohérent avec la
contrainte de la règle 4).

**Non retenu pour cette itération** car :
- Ça touche un autre dépôt/langage (backend Java du microservice `konsult`, pas seulement
  l'Angular du front-office) — changement de bien plus grande ampleur et risque.
- Ça nécessite de régénérer le client TypeScript depuis le spec OpenAPI, ce qui n'est pas un geste
  anodin dans ce monorepo (voir les pièges déjà documentés dans
  `PLAN_ENVIRONNEMENT_RUDI_SOURCE.md`/`PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md` pour d'autres
  composants du monorepo).
- Le volume actuel du catalogue (~400 JDD) rend le contournement front-only de ce plan largement
  suffisant en performance.

Si le catalogue grossit significativement (plusieurs milliers de JDD), le scan intégral
(`getAvailableFileTypes()` et `searchAllMetadatasMatchingFilters()`) deviendra coûteux et cette
piste backend devra être reconsidérée — à signaler à Simon si ce point est atteint, ne pas
l'anticiper maintenant.

---

## 8. Limites connues (assumées pour cette itération)

- **Un scan intégral du catalogue par recherche filtrée par type** (au lieu d'une simple page) —
  acceptable au volume actuel (~400 JDD / ~4 requêtes de 100), mais plus coûteux que les autres
  filtres qui restent purement backend. Documenté en §7.
- **Les médias `SERIES`** ne sont pas exploitables pour ce filtre (pas de champ générique fiable
  équivalent à une extension ou un `interface_contract`) — ignorés silencieusement, pas de case à
  cocher pour eux. Aucun impact connu pour les producteurs actuels du portail (le pipeline
  moissonneuse-batteuse ne produit pas de média `SERIES`).
- **Le tri (`OrderFilter`) reste backend** même quand le filtre type de fichier est actif : le tri
  est appliqué par le backend sur l'ensemble récupéré via `searchAllMetadatasMatchingFilters()`
  (l'ordre est un paramètre de `searchMetadatas()`, transmis normalement), donc pas de régression
  attendue sur ce point — à confirmer néanmoins en validation manuelle (tester un tri « plus
  récent » avec le filtre type de fichier actif).

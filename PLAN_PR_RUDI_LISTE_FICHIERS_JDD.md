# Afficher le nom de fichier + la légende dans la liste des sources de données (portail RUDI) — Guide d'implémentation

> **À lire en entier avant de toucher quoi que ce soit.** Ce document est un plan d'exécution
> détaillé pour un futur agent d'implémentation. Il transforme un défaut d'affichage front (aucune
> distinction visuelle entre plusieurs fichiers d'un même JDD) en correctif propre du code source
> du portail, sous forme de **PR atomiques (1 PR = 1 seul problème)**.
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
>    pas. Concrètement ici : n'affiche/ne déduis **que** des champs standards du modèle RUDI
>    (`media_name`, `media_caption`, `file_type`...) — ne fais **jamais** de pattern-matching sur nos
>    conventions internes de nommage de fichier (préfixes `dict-`/`doc-`, suffixe `-rennesmetropole`,
>    notion de "millésime" BDNB). Le correctif doit bénéficier à **tous** les producteurs RUDI, pas
>    seulement à nos JDD.
> 5. Après chaque PR, exécuter la validation associée (section 5) **avant** de passer à la suivante.

---

## 1. Contexte

Sur la page détail d'un JDD du portail RUDI, l'onglet « Informations » → section « Sources de
données » liste les fichiers disponibles (`available_formats` du modèle RUDI), mais chaque panneau
n'affiche que le format entre parenthèses (ex. `Fichier (csv)`), jamais le nom du fichier. Quand un
JDD a plusieurs CSV (années différentes, dictionnaire de variables, documentation PDF), rien ne les
distingue dans la liste — il faut déplier chaque panneau et deviner lequel est lequel. Objectif
minimal : afficher `Fichier "donnees-2020" (csv)`.

**Diagnostic (exploration du 2026-07-27, confirmée par lecture directe du code pipeline ET du front
portail)** : ce n'est **pas** un manque de données mais un défaut d'affichage front. Le modèle RUDI
standard `Media` (`media.ts`, généré OpenAPI, commun à **tous** les producteurs RUDI, pas une
invention de notre pipeline) expose déjà :
- `media_name` — nom physique réel du fichier. Notre pipeline le remplit avec extension (ex.
  `dict-nomenclature-2020.csv`, `doc-guide-methodologique.pdf`, `bic-iris-rennesmetropole.csv`).
- `media_caption` — texte déjà riche et contextualisé. Notre pipeline le remplit ainsi, ex. :
  `"Données 2020 — données filtrées sur Rennes Métropole (CSV)"`,
  `"Dictionnaire des variables — {titre original de la ressource}"`,
  `"Documentation — {titre}"`.
- `media_dates` — dates par média (`created`/`updated`/...), prévu par le standard mais **non
  peuplé par notre pipeline actuellement** (seule une date globale au niveau du JDD existe) — voir
  §6 « Piste non retenue ».

Recherche exhaustive dans le front (`grep -rn "media_caption\|media_name\|media_dates" src/app`) :
- `media_name` : une seule occurrence, `detail.component.ts:379`, comme **fallback silencieux** du
  nom de fichier au moment du téléchargement (`saveAs(blob, filename || media.media_name)`) — jamais
  affiché visuellement.
- `media_caption` : **zéro occurrence** dans tout le front.
- `media_dates` : **zéro occurrence** dans tout le front.

Les données existent déjà côté modèle/API — il ne s'agit que d'un défaut d'affichage dans les
templates. **Aucune notion structurée de "millésime"/"dictionnaire"/"version" n'existe dans le
schéma `available_formats`** (vérifié aussi côté pipeline) : uniquement des conventions de préfixe
de nom de fichier (`dict-`, `doc-`) et du texte libre. Voir la règle 4 ci-dessus : on n'expose donc
que les champs standards, jamais nos conventions.

---

## 2. Emplacements

| Rôle | Chemin |
|---|---|
| Portail source (monorepo Java + Angular) | `/media/simon/DATA4T/Dev/rudi-portal-source` |
| Racine du front Angular | `rudi-application/rudi-application-front-office/angular-project/` |
| Composant cible principal (liste des fichiers, onglet Informations) | `.../src/app/features/data-set/components/data-set-infos/data-set-infos.component.html` + `.ts` |
| Second endroit à corriger (menu déroulant de téléchargement) | `.../src/app/features/data-set/pages/detail/detail.component.html` |
| Modèle `Media`/`MediaFile` (généré OpenAPI, déjà à jour, ne pas éditer) | `.../angular-project/micro_service_modules/api-kaccess/model/media.ts`, `mediaFile.ts` |
| Procédure de build/déploiement/validation du front | `/media/simon/DATA4T/Dev/moissonneuse-batteuse/PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md` (section 3 « Front Angular » : Node épinglé v20.15.1, cible Dockerfile `rudi-application-front-office`) |
| Pipeline moissonneuse-batteuse (référence uniquement, **non modifié** par ce plan) | `/media/simon/DATA4T/Dev/moissonneuse-batteuse` |

Stack : Angular 19, composants **standalone** (imports déclarés dans `@Component({imports:[...]})`,
pas de NgModule), syntaxe de template moderne `@if`/`@for`/`@else`.

---

## 3. PR-A — afficher `media_name` et `media_caption` (le cœur de la demande)

### Fichier 1 : `data-set-infos.component.html`

Bloc `@for (media of metadata.available_formats...)` (vérifié aux lignes 342-366 le 2026-07-27) :

**AVANT :**
```html
          <mat-expansion-panel-header class="rudi-mat-expansion-panel-source" id="medias-table-header">
            @if (metaDataFunctions.isMediaTypeFile(media)) {
              <mat-panel-title class="rudi-panel-source-title">
                {{'common.fichier'|translate}} ({{getMediaFileExtension(media)}})
              </mat-panel-title>
            }
            @if (metaDataFunctions.isMediaTypeSeries(media)) {
              <mat-panel-title class="rudi-panel-source-title">
                <span>{{'common.streaming'|translate}} ({{getMediaIndexSeries(i)}})</span>
              </mat-panel-title>
            }
            <!--                    Titre pour les medias de type Service-->
            @if (metaDataFunctions.isMediaTypeService(media)) {
              <mat-panel-title
                class="rudi-panel-source-title">
                <span>{{'metaData.service'|translate}} {{getInterfaceContract(media)}}</span>
              </mat-panel-title>
            }
          </mat-expansion-panel-header>
```

**APRÈS :**
```html
          <mat-expansion-panel-header class="rudi-mat-expansion-panel-source" id="medias-table-header">
            @if (metaDataFunctions.isMediaTypeFile(media)) {
              <mat-panel-title class="rudi-panel-source-title">
                @if (media.media_name) {
                  {{'common.fichier'|translate}} "{{ media.media_name }}" ({{getMediaFileExtension(media)}})
                } @else {
                  {{'common.fichier'|translate}} ({{getMediaFileExtension(media)}})
                }
              </mat-panel-title>
            }
            @if (metaDataFunctions.isMediaTypeSeries(media)) {
              <mat-panel-title class="rudi-panel-source-title">
                <span>{{'common.streaming'|translate}} ({{getMediaIndexSeries(i)}})</span>
              </mat-panel-title>
            }
            <!--                    Titre pour les medias de type Service-->
            @if (metaDataFunctions.isMediaTypeService(media)) {
              <mat-panel-title
                class="rudi-panel-source-title">
                <span>{{'metaData.service'|translate}} {{getInterfaceContract(media)}}</span>
              </mat-panel-title>
            }
            @if (media.media_caption) {
              <mat-panel-description class="rudi-panel-source-caption">
                {{ media.media_caption }}
              </mat-panel-description>
            }
          </mat-expansion-panel-header>
```

> `<mat-panel-description>` est un sibling natif de `<mat-panel-title>` dans
> `mat-expansion-panel-header` (API standard Angular Material, aucun changement de structure
> requis). Placé pour les **3 types de média** (FILE/SERIES/SERVICE), pas seulement FILE, car
> `media_caption` existe sur `Media` (type de base), pas seulement `MediaFile`.
> `media_name`/`media_caption` sont optionnels (`?`) dans le modèle — gérés avec `@if`/`@else`,
> jamais affichés vides.

### Fichier 2 : `data-set-infos.component.ts`

Ajouter l'import Angular Material et l'enregistrer dans le composant standalone :

**AVANT** (ligne 8) :
```ts
import {MatExpansionPanel, MatExpansionPanelHeader, MatExpansionPanelTitle} from '@angular/material/expansion';
```
**APRÈS :**
```ts
import {MatExpansionPanel, MatExpansionPanelDescription, MatExpansionPanelHeader, MatExpansionPanelTitle} from '@angular/material/expansion';
```

**AVANT** (ligne 63, décorateur `@Component`, tableau `imports`) :
```ts
    imports: [MatCardHeader, MatCardTitle, MatCardContent, LoaderComponent, MatExpansionPanel, MatExpansionPanelHeader, NgClass, ExtendedModule, MatExpansionPanelTitle, BooleanDataBlockComponent, MatError, MatIcon, MatButton, MapComponent, OrganizationLogoComponent, ContactButtonComponent, AsyncPipe, UpperCasePipe, DatePipe, TranslatePipe, ReplaceIfNullPipe]
```
**APRÈS** (ajouter `MatExpansionPanelDescription` juste après `MatExpansionPanelTitle`) :
```ts
    imports: [MatCardHeader, MatCardTitle, MatCardContent, LoaderComponent, MatExpansionPanel, MatExpansionPanelHeader, NgClass, ExtendedModule, MatExpansionPanelTitle, MatExpansionPanelDescription, BooleanDataBlockComponent, MatError, MatIcon, MatButton, MapComponent, OrganizationLogoComponent, ContactButtonComponent, AsyncPipe, UpperCasePipe, DatePipe, TranslatePipe, ReplaceIfNullPipe]
```

### Fichier 3 : `detail.component.html`

Menu déroulant de téléchargement (vérifié aux lignes 91-98 le 2026-07-27) :

**AVANT :**
```html
                            @for (item of downloadableMedias; track item; let i = $index) {
                              <mat-radio-button
                                [checked]="i === 0"
                                [value]="item"
                                class="mb-2">
                                {{ getMediaFileExtension(item) }}
                              </mat-radio-button>
                            }
```

**APRÈS :**
```html
                            @for (item of downloadableMedias; track item; let i = $index) {
                              <mat-radio-button
                                [checked]="i === 0"
                                [value]="item"
                                class="mb-2">
                                @if (item.media_name) {
                                  {{ item.media_name }} ({{ getMediaFileExtension(item) }})
                                } @else {
                                  {{ getMediaFileExtension(item) }}
                                }
                              </mat-radio-button>
                            }
```
> Vérifier au `Read` de `detail.component.ts` que `getMediaFileExtension` y est bien défini/importé
> (il est déjà appelé à la ligne 96 avant ce patch → oui). Aucun nouvel import de service attendu.

**Commit** : `feat(front): display media file name and caption in dataset media list`
**Branche** : `feat/dataset-media-list-display-name-caption`

---

## 4. PR-B — UX : icône par format + tri par nom au sein d'un même type de média

Dépend de PR-A (touche le même bloc de titre) — appliquer PR-A d'abord. Séparée de PR-A car ajout
de confort indépendant (règle « 1 PR = 1 problème », cf. `PLAN_PR_RUDI_TABLEAU_CARTE.md`).

### `data-set-infos.component.ts`

**Tri** — `mediasSortedFunction` (vérifié aux lignes 152-163 le 2026-07-27) :

AVANT :
```ts
    /**
     * Tri permettant d'afficher les media file avant les media series
     */
    mediasSortedFunction(media1: Media, media2: Media): number {
        if (media1.media_type === media2.media_type) {
            return 0;
        } else if (media1.media_type === MediaTypeEnum.File) {
            return -1;
        } else {
            return 1;
        }
    }
```
APRÈS :
```ts
    /**
     * Tri permettant d'afficher les media file avant les media series, puis les media d'un même
     * type triés par nom (media_name) pour regrouper visuellement les fichiers apparentés.
     */
    mediasSortedFunction(media1: Media, media2: Media): number {
        if (media1.media_type !== media2.media_type) {
            return media1.media_type === MediaTypeEnum.File ? -1 : 1;
        }
        return (media1.media_name ?? '').localeCompare(media2.media_name ?? '');
    }
```

**Icône** — nouvel helper, ajouté juste après `getMediaFileExtension()` (vérifié aux lignes 172-177
le 2026-07-27) :
```ts
    /**
     * Fonction permettant de retourner l'icône Material associée au format du fichier
     */
    getMediaFileIcon(media: Media): string {
        const ICONS_PAR_EXTENSION: Record<string, string> = {
            csv: 'table_chart',
            tsv: 'table_chart',
            json: 'data_object',
            geojson: 'map',
            pdf: 'picture_as_pdf',
            xlsx: 'grid_on',
            xls: 'grid_on',
            zip: 'folder_zip',
        };
        const extension = this.getMediaFileExtension(media)?.toLowerCase();
        return ICONS_PAR_EXTENSION[extension] ?? 'description';
    }
```

### `data-set-infos.component.html`

Dans le bloc FILE du titre (résultat de PR-A), ajouter l'icône avant le texte :
```html
            @if (metaDataFunctions.isMediaTypeFile(media)) {
              <mat-panel-title class="rudi-panel-source-title">
                <mat-icon class="rudi-panel-source-icon">{{ getMediaFileIcon(media) }}</mat-icon>
                @if (media.media_name) {
                  {{'common.fichier'|translate}} "{{ media.media_name }}" ({{getMediaFileExtension(media)}})
                } @else {
                  {{'common.fichier'|translate}} ({{getMediaFileExtension(media)}})
                }
              </mat-panel-title>
            }
```
> `MatIcon` est déjà importé et enregistré dans ce composant (utilisé lignes 423, 439, 442 pour
> d'autres icônes) — aucun nouvel import.

**Commit** : `feat(front): sort dataset media list by name and show a format icon`
**Branche** : `feat/dataset-media-list-icons-sort`

---

## 5. Validation

À exécuter après chaque PR déployée (procédure de build/déploiement :
`PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md`, section 3 « Front Angular », Node épinglé v20.15.1) :

1. **JDD témoin multi-fichiers** : un JDD data.gouv avec dictionnaire de colonnes (ex. un candidat
   moissonné par `harvest_batch.py` avec `fichiers_dicts`), ou un JDD BDNB multi-tables. Sur la page
   détail, onglet Informations → « Sources de données » :
   - Chaque panneau FILE affiche bien `Fichier "<nom>" (<extension>)` avec un nom distinct par
     fichier.
   - La légende (`media_caption`) apparaît sous le titre, immédiatement visible sans déplier.
   - (PR-B) une icône cohérente avec le format apparaît, et les fichiers du même type sont triés par
     nom.
2. **Menu de téléchargement** (`detail.component.html`) : ouvrir le menu « Télécharger » sur ce même
   JDD → chaque option affiche `<nom> (<extension>)`, permettant de distinguer deux fichiers de même
   format.
3. **Non-régression médias SERIES/SERVICE** : sur un JDD avec un média `SERVICE` (WMS/WFS) ou
   `SERIES`, vérifier que le titre reste correct (`Streaming (n)` / `Service <contrat>`) et que la
   légende (si `media_caption` présent) s'affiche sans casser la mise en page — pas de texte vide
   moche si `media_caption` est absent.
4. **Aucune régression** sur le reste de la page détail (carte, tableau, autres panneaux
   d'information) — ces composants ne consomment pas `available_formats` en liste (voir §6).

---

## 6. Piste non retenue pour ce lot — `media_dates` par fichier

`media_dates` (champ standard RUDI, par média : `created`/`updated`/`validated`/...) serait le moyen
le plus fiable de signaler des millésimes différents (une date exploitable, sans dépendre d'un nom
de fichier bien formé). Aujourd'hui : le standard le permet, mais **notre pipeline ne le peuple pas
par fichier** (seule une date globale au niveau du JDD existe dans `dataset_dates`), et le front ne
l'affiche nulle part. Documenté ici comme axe futur possible — **explicitement hors périmètre de ce
lot** (décision prise avec Simon). Si retenu un jour : (a) peupler `media_dates.updated` par média
côté pipeline (`src/translation/rudi_builder.py`, `datagouv_to_rudi.py`) à partir des dates de
ressource déjà disponibles (`last_modified` par ressource data.gouv/INSEE) — reste un champ
standard, pas une convention propriétaire ; (b) afficher `media.media_dates?.updated` dans le
panneau front, dégradant proprement (rien affiché) pour les producteurs qui ne le peuplent pas.

---

## 7. Ce qu'on ne touche pas

- Aucun changement dans `moissonneuse-batteuse` (pipeline) : `media_name`/`media_caption` sont déjà
  correctement peuplés pour nos JDD, le défaut est uniquement côté affichage front. Ne pas modifier
  `src/translation/rudi_builder.py` ni `src/translation/datagouv_to_rudi.py` pour ce lot.
- Aucune convention de nommage propriétaire (préfixes `dict-`/`doc-`, suffixe `-rennesmetropole`) ne
  doit apparaître dans le code du front — le correctif doit rester utilisable par n'importe quel
  producteur RUDI qui remplit `media_name`/`media_caption`.

---

## 8. Checklist de contrôle (pour la revue, à cocher par le relecteur)

- [ ] PR-A : titre FILE affiche `media_name` entre guillemets si présent, dégrade proprement sinon.
- [ ] PR-A : `<mat-panel-description>` affiche `media_caption` pour FILE/SERIES/SERVICE.
- [ ] PR-A : `MatExpansionPanelDescription` importé et ajouté au tableau `imports` du composant.
- [ ] PR-A : menu de téléchargement (`detail.component.html`) affiche aussi `media_name`.
- [ ] PR-B : tri secondaire par `media_name` au sein d'un même `media_type`.
- [ ] PR-B : icône Material cohérente avec l'extension, `MatIcon` déjà importé (pas de nouvel import).
- [ ] Chaque PR = 1 commit logique, message conventionnel anglais, **sans** trailer d'IA (cf.
      mémoire `feedback-pr-atomiques` — contributions upstream signées par Simon).
- [ ] Aucun fichier de `moissonneuse-batteuse` modifié.
- [ ] Validation §5 exécutée sur un JDD réel multi-fichiers avant de considérer la PR terminée.

# Téléchargement multi-formats en ZIP (portail RUDI) — Guide d'implémentation

> **À lire en entier avant de toucher quoi que ce soit.** Plan d'exécution détaillé pour un futur
> agent d'implémentation. Une seule PR atomique (un seul problème : le menu de téléchargement passe
> de sélection unique à sélection multiple avec archive ZIP), sur une seule branche.
>
> **Règles impératives pour l'agent qui implémente :**
> 1. **Toujours `Read` les fichiers avant de les éditer** — les numéros de ligne ci-dessous ont été
>    vérifiés le 2026-07-27 par lecture directe du code, mais peuvent avoir bougé. Repère le code par
>    son **contenu**, pas par son numéro de ligne.
> 2. **Ne rien inventer.** Si un fichier/chemin ne correspond pas à ce qui est décrit, arrête-toi et
>    signale-le au lieu de deviner.
> 3. **100% front, zéro changement pipeline/nœud** — cohérent avec la contrainte déjà appliquée sur
>    le lot précédent (voir `PLAN_PR_RUDI_LISTE_FICHIERS_JDD.md`) : le zip est généré **côté
>    navigateur** à partir des fichiers déjà téléchargeables tels quels, aucune convention propre à
>    notre pipeline n'est nécessaire.
> 4. Après implémentation, exécuter la validation décrite en section 6 **avant** de considérer la PR
>    terminée.

---

## 1. Contexte

Sur la page détail d'un JDD, le bouton « Télécharger » (en haut à droite) ouvre un menu listant les
formats disponibles sous forme de **boutons radio** (sélection unique) : on choisit un format, on
clique sur « Télécharger », un seul fichier est téléchargé.

**Demande** : remplacer les boutons radio par des **cases à cocher** (sélection multiple). Si
l'utilisateur coche **plus d'un** format, le téléchargement doit produire **un seul fichier ZIP**
contenant l'ensemble des fichiers sélectionnés (tels quels, pas de transformation de contenu). Si un
seul format reste coché (cas le plus courant), le comportement actuel (téléchargement direct du
fichier, sans zip) est conservé à l'identique.

**Pourquoi c'est faisable sans toucher au backend** : chaque entrée du menu correspond à un média
`available_formats` de `media_type: "FILE"` (les médias `SERVICE`/`WMS`/`WFS` ne sont **jamais**
proposés dans ce menu — voir `canDownloadMedia()` ci-dessous), déjà téléchargeable individuellement
via une simple requête HTTP GET authentifiée retournant un Blob. Zipper ces blobs **côté navigateur**
avant de déclencher un seul téléchargement est suffisant — aucune API zip côté nœud/portail n'est
nécessaire.

---

## 2. Emplacements

| Rôle | Chemin |
|---|---|
| Portail source | `/media/simon/DATA4T/Dev/rudi-portal-source` |
| Racine front Angular | `rudi-application/rudi-application-front-office/angular-project/` |
| Composant cible (menu de téléchargement, page détail JDD) | `.../src/app/features/data-set/pages/detail/detail.component.html` + `.ts` |
| Filtre des médias téléchargeables (référence, **ne pas modifier**) | `.../src/app/features/data-set/pages/detail/detail-functions.ts` (méthode `canDownloadMedia()`) |
| Service de téléchargement (référence, **ne pas modifier**) | `.../src/app/core/services/konsult-metier.service.ts` (méthode `downloadMetadataMedia()`) |
| Slugifieur déjà existant, réutilisable pour le nom du zip | `.../src/app/core/services/codecs/uri-component-codec.ts` (`normalizeString()`) |
| i18n FR | `.../src/assets/i18n/fr.json` (clés `availableFormats.*`, seul fichier de langue existant) |
| Procédure de build/déploiement/validation du front | `PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md` (racine `moissonneuse-batteuse`) |

Stack : Angular 21 (post-`v3.4.1`, voir mémoire `reference-portail-front-build-node` pour la version
Node à utiliser), composants standalone, syntaxe de contrôle de flux moderne `@if`/`@for`.

---

## 3. État actuel vérifié (2026-07-27)

`detail.component.html`, section « Bouton télécharger » (~l. 73-120) :
```html
@if (!isSelfdata && hasDownloadableMedia) {
<app-banner-button #clickMenuFormatTrigger="matMenuTrigger" [matMenuTriggerFor]="formatMenu">
  <div class="d-flex align-items-center">
    <mat-icon class="icon-syle icon-save me-l">save_alt</mat-icon>
    <div [ngClass]="{'banner-icon-libelle': mediaSize.isSm}">{{ 'availableFormats.telecharger'|translate }}</div>
  </div>
  <mat-menu #formatMenu="matMenu" role="menu" yPosition="below">
    <div (click)="$event.stopPropagation()" class="rudi-select-panel d-flex flex-column">
      <form [formGroup]="form">
        <div class="menu-radioGroup-scrollable">
          <h4 class="menu-libelle">{{ 'availableFormats.formatsDisponibles'|translate }}</h4>
          <mat-radio-group #radioGroup="matRadioGroup"
            (click)="$event.stopPropagation()"
            aria-label="Select an option"
            formControlName="options" class="d-flex flex-column">
            @for (item of downloadableMedias; track item; let i = $index) {
              <mat-radio-button [checked]="i === 0" [value]="item" class="mb-2">
                @if (item.media_name) {
                  {{ item.media_name }} ({{ getMediaFileExtension(item) }})
                } @else {
                  {{ getMediaFileExtension(item) }}
                }
              </mat-radio-button>
            }
          </mat-radio-group>
        </div>
        @if (!isSelfdata) {
          <button (click)="onDownloadFormat()" [ngClass]="{'btn-download-format':metadata.available_formats.length == null }"
            class="button menu-btn-download" mat-raised-button type="submit">
            {{ 'availableFormats.telecharger'|translate }}
          </button>
        }
      </form>
    </div>
  </mat-menu>
</app-banner-button>
}
```

`detail.component.ts` (extraits pertinents) :
- `form: FormGroup` construit dans le constructeur : `this.form = this.fb.group({ options: [] });`
- `get/set selectedItem` lit/écrit `this.form.controls.options.value` (un seul `Media`).
- Dans `ngOnInit()` : `this.selectedItem = this.metadata.available_formats.filter(f => f.media_type === 'FILE')[0];` (présélection du premier fichier).
- `onDownloadFormat()` (~l. 383-407) :
  ```ts
  onDownloadFormat(): void {
      this.isLoading = true;
      const selectedItem = this.selectedItem;
      if (selectedItem) {
          this.konsultMetierService.downloadMetadataMedia(selectedItem.connector.url)
              .subscribe({
                  next: (response) => {
                      this.isLoading = false;
                      this.downLoadFile(response, selectedItem);
                  },
                  error: () => {
                      this.isLoading = false;
                      const message = this.translateService.instant('common.echec');
                      const linkLabel = this.translateService.instant('common.ici');
                      this.propertiesMetierService.get('front.contact').subscribe(link => {
                          this.snackBarService.openSnackBar({
                              message: `${message} <a href="${link}">${linkLabel}</a>.`,
                              level: Level.ERROR
                          });
                      });
                  }
              });
          this.clickMenuFormatTrigger.closeMenu();
      }
  }
  ```
- `downLoadFile(response, media)` (~l. 372-378) : construit un `Blob` depuis la réponse HTTP, extrait le nom de fichier du header `content-disposition` ou retombe sur `media.media_name`, puis `saveAs(blob, filename)`.
- `downloadableMedias` (peuplé par `initDownloadableMedias()`) : filtré via `dataSetDetailsFunctions.canDownloadMedia(media, metadata)`, qui exige `media_type === 'FILE'` **et** `interface_contract === 'dwnl'` — **les médias SERVICE (WMS/WFS) n'apparaissent jamais dans ce menu**, donc tous les items sont déjà des fichiers blob-téléchargeables individuellement.
- Imports actuels utiles : `MatRadioButton, MatRadioGroup` (imports Material du composant), `FormBuilder, FormGroup, FormsModule, ReactiveFormsModule` (Angular forms), `saveAs` (`file-saver`), `Media, MediaFile` (`micro_service_modules/api-kaccess`).

**Aucun test unitaire** ne couvre `onDownloadFormat`/`selectedItem`/`form` dans
`detail.component.spec.ts` (vérifié par grep) — pas de test à adapter.

---

## 4. Changements à faire

### 4.1 Ajouter la dépendance ZIP

Ajouter `jszip` (`^3.10.1` ou plus récent au moment de l'implémentation) dans `package.json` du
projet Angular (`dependencies`, pas `devDependencies` — utilisé au runtime) :
```json
"jszip": "^3.10.1",
```
> Pas de `@types/jszip` nécessaire : JSZip fournit ses propres types TypeScript.
> Après l'ajout, lancer `npm install` (ou régénérer le lockfile) avant de builder — voir
> `reference-portail-front-build-node` (mémoire) pour la version de Node à utiliser sur ce checkout
> (post-`v3.4.1` : Node ≥ v20.19/v22.12, **pas** le v20.15.1 historique).
> **Alternative plus légère envisageable** : `fflate` (aucun avertissement "not ESM" au build,
> bundle plus petit) mais API bas niveau (Uint8Array, pas de méthode `.file()`/`.generateAsync()`
> directe) — plus de code à écrire pour un gain marginal ici. Recommandation : **JSZip**, sauf si la
> taille du bundle devient un problème mesuré.

### 4.2 `detail.component.ts`

**Imports** — remplacer :
```ts
import {FormBuilder, FormGroup, FormsModule, ReactiveFormsModule} from '@angular/forms';
...
import {MatRadioButton, MatRadioGroup} from '@angular/material/radio';
...
import {BehaviorSubject, combineLatest, from, Observable, of, throwError} from 'rxjs';
import {catchError, filter, map, switchMap, take, tap} from 'rxjs/operators';
```
par :
```ts
import {MatCheckbox} from '@angular/material/checkbox';
...
import {BehaviorSubject, combineLatest, forkJoin, from, Observable, of, throwError} from 'rxjs';
import {catchError, filter, map, switchMap, take, tap} from 'rxjs/operators';
import JSZip from 'jszip';
```
> Retirer `FormBuilder, FormGroup, FormsModule, ReactiveFormsModule` et `MatRadioButton,
> MatRadioGroup` **seulement si** un `grep` confirme qu'ils ne sont plus utilisés ailleurs dans ce
> composant après les changements ci-dessous (ce devrait être le cas — `form`/`fb` n'étaient utilisés
> que pour ce menu). Retirer aussi `FormsModule, ReactiveFormsModule, MatRadioGroup, MatRadioButton`
> du tableau `imports` du décorateur `@Component` (~l. 69), ajouter `MatCheckbox`.
> Retirer le paramètre constructeur `private readonly fb: FormBuilder` s'il devient inutilisé.

**Remplacer le contrôle de formulaire par un tableau simple** — supprimer :
```ts
form: FormGroup;
...
this.form = this.fb.group({
    options: []
});
...
get selectedItem(): Media {
    return this.form.controls.options.value;
}

set selectedItem(selectedItem: Media) {
    this.form.setValue({
        options: selectedItem || null
    });
}
```
par :
```ts
selectedMedias: Media[] = [];

isMediaSelected(media: Media): boolean {
    return this.selectedMedias.includes(media);
}

toggleMediaSelection(media: Media, checked: boolean): void {
    this.selectedMedias = checked
        ? [...this.selectedMedias, media]
        : this.selectedMedias.filter(m => m !== media);
}
```

**Dans `ngOnInit()`**, remplacer la ligne de présélection :
```ts
// AVANT
this.selectedItem = this.metadata.available_formats.filter(f => f.media_type === 'FILE')[0];
```
```ts
// APRÈS — même comportement par défaut (1 seul fichier présélectionné, celui qui était coché avant)
const premierMediaFichier = this.metadata.available_formats.filter(f => f.media_type === 'FILE')[0];
this.selectedMedias = premierMediaFichier ? [premierMediaFichier] : [];
```

**Réécrire `onDownloadFormat()`** et factoriser la gestion d'erreur (déjà dupliquée dans le code
existant pour `onDownloadFile()` — même pattern, à ne pas dupliquer une 3e fois) :
```ts
/**
 * Fonction permettant de télécharger le ou les formats sélectionnés.
 * 1 seul format coché : téléchargement direct, comportement inchangé.
 * Plusieurs formats cochés : les fichiers sont regroupés dans une seule archive ZIP.
 */
onDownloadFormat(): void {
    if (this.selectedMedias.length === 0) {
        return;
    }
    this.isLoading = true;
    this.clickMenuFormatTrigger.closeMenu();

    if (this.selectedMedias.length === 1) {
        const media = this.selectedMedias[0];
        this.konsultMetierService.downloadMetadataMedia(media.connector.url)
            .subscribe({
                next: (response) => {
                    this.isLoading = false;
                    this.downLoadFile(response, media);
                },
                error: () => this.handleDownloadError()
            });
        return;
    }

    forkJoin(
        this.selectedMedias.map(media =>
            this.konsultMetierService.downloadMetadataMedia(media.connector.url).pipe(
                map(response => ({media, blob: response.body}))
            )
        )
    ).subscribe({
        next: (results) => this.downloadAsZip(results),
        error: () => this.handleDownloadError()
    });
}

/**
 * Regroupe les fichiers téléchargés dans une seule archive ZIP et déclenche le téléchargement.
 * @private
 */
private downloadAsZip(results: { media: Media, blob: Blob }[]): void {
    const zip = new JSZip();
    const nomsUtilises = new Set<string>();
    results.forEach(({media, blob}) => {
        // Anti-collision : deux médias ne devraient jamais partager le même media_name pour nos
        // JDD, mais le standard RUDI ne le garantit pas pour tout producteur — éviter qu'un fichier
        // en écrase silencieusement un autre dans le zip.
        let nom = media.media_name || 'fichier';
        let compteur = 2;
        while (nomsUtilises.has(nom)) {
            nom = `${media.media_name || 'fichier'}_${compteur}`;
            compteur++;
        }
        nomsUtilises.add(nom);
        zip.file(nom, blob);
    });
    zip.generateAsync({type: 'blob'}).then(zipBlob => {
        this.isLoading = false;
        const nomZip = this.uriComponentCodec.normalizeString(this.metadata.resource_title) + '.zip';
        saveAs(zipBlob, nomZip);
    });
}

/**
 * Gestion d'erreur commune aux téléchargements (simple ou multiple).
 * @private
 */
private handleDownloadError(): void {
    this.isLoading = false;
    const message = this.translateService.instant('common.echec');
    const linkLabel = this.translateService.instant('common.ici');
    this.propertiesMetierService.get('front.contact').subscribe(link => {
        this.snackBarService.openSnackBar({
            message: `${message} <a href="${link}">${linkLabel}</a>.`,
            level: Level.ERROR
        });
    });
}
```
> **Décision assumée (à ne pas re-discuter sans remonter à Simon)** : `forkJoin` échoue dès qu'**un
> seul** téléchargement échoue — dans ce cas, **aucun** zip n'est produit, même si d'autres fichiers
> ont déjà été récupérés avec succès (même philosophie « tout ou rien » que le message d'erreur
> générique existant). Pas de téléchargement partiel silencieux.
> `response.body` est déjà un `Blob` (le service `downloadMetadataMedia` utilise `responseType:
> 'blob'`) — vérifier ce typage au `Read` de `konsult-metier.service.ts` avant d'écrire `map(response
> => ({media, blob: response.body}))`.

### 4.3 `detail.component.html`

Remplacer le bloc radio (section 3 ci-dessus) par :
```html
<div (click)="$event.stopPropagation()" class="rudi-select-panel d-flex flex-column">
  <div class="menu-radioGroup-scrollable">
    <h4 class="menu-libelle">{{ 'availableFormats.formatsDisponibles'|translate }}</h4>
    <div (click)="$event.stopPropagation()" class="d-flex flex-column">
      @for (item of downloadableMedias; track item) {
        <mat-checkbox
          [checked]="isMediaSelected(item)"
          (change)="toggleMediaSelection(item, $event.checked)"
          class="mb-2">
          @if (item.media_name) {
            {{ item.media_name }} ({{ getMediaFileExtension(item) }})
          } @else {
            {{ getMediaFileExtension(item) }}
          }
        </mat-checkbox>
      }
    </div>
  </div>
  @if (!isSelfdata) {
    <button
      (click)="onDownloadFormat()"
      [disabled]="selectedMedias.length === 0"
      [ngClass]="{'btn-download-format':metadata.available_formats.length == null }"
      class="button menu-btn-download"
      mat-raised-button
      type="button">
      {{ 'availableFormats.telecharger'|translate }}
    </button>
  }
</div>
```
> Différences avec l'existant : le `<form [formGroup]="form">` disparaît (remplacé par un `<div>`
> simple — la sélection n'a plus besoin de Reactive Forms) ; `type="submit"` → `type="button"` (il
> n'y a plus de formulaire à soumettre) ; ajout de `[disabled]` quand rien n'est coché (l'ancien code
> radio garantissait toujours 1 élément coché, ce n'est plus vrai si l'utilisateur décoche tout).
> **Pas de `mat-radio-group`/`MatCheckboxGroup` équivalent nécessaire** : chaque `mat-checkbox` est
> indépendante, la sélection est gérée manuellement via `isMediaSelected()`/`toggleMediaSelection()`.

### 4.4 (Optionnel, recommandé) Libellé dynamique du bouton

Pour rendre explicite qu'un téléchargement groupé produit un zip, faire varier le texte du bouton
selon le nombre de formats cochés. Ajouter dans `fr.json` (section `availableFormats`, ~l. 755-758) :
```json
"availableFormats": {
    "formatsDisponibles": "Formats disponibles :",
    "telecharger": "Télécharger",
    "telechargerZip": "Télécharger (.zip)"
},
```
Et dans le template, remplacer `{{ 'availableFormats.telecharger'|translate }}` (bouton uniquement,
**pas** le libellé du bouton principal du bandeau qui reste générique) par :
```html
{{ (selectedMedias.length > 1 ? 'availableFormats.telechargerZip' : 'availableFormats.telecharger') | translate }}
```

---

## 5. Ce qu'on ne touche pas

- `canDownloadMedia()` (`detail-functions.ts`) et `downloadMetadataMedia()`
  (`konsult-metier.service.ts`) : logique de filtrage/téléchargement individuel, inchangée et
  réutilisée telle quelle.
- Aucune route/API backend : le zip est généré **entièrement côté navigateur**.
- Le comportement de téléchargement à 1 seul format reste **strictement identique** (même appel
  `downLoadFile()`, même extraction de nom de fichier depuis `content-disposition`).

---

## 6. Validation

Sur un JDD réel multi-fichiers, ex. `1bff9394-07aa-40b0-af39-9053caa7e0ab` (« Carte des loyers »
2022, 8 médias téléchargeables) — voir `PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md` pour build/déploiement :

1. **Sélection unique (non-régression)** : ouvrir le menu, ne cocher qu'un seul format (ou laisser la
   présélection par défaut), cliquer « Télécharger » → un seul fichier est téléchargé, nom et contenu
   identiques à avant ce correctif.
2. **Sélection multiple** : cocher 2 formats ou plus, cliquer « Télécharger » → **un seul fichier
   `.zip`** est téléchargé (nom = titre du JDD slugifié + `.zip`), contenant exactement les fichiers
   cochés, chacun lisible et correspondant au bon contenu (ouvrir le zip, vérifier les fichiers un par
   un).
3. **Aucune sélection** : décocher tout → le bouton « Télécharger » est désactivé (pas de clic
   possible), aucune erreur.
4. **Erreur réseau** (si simulable, ex. couper la connexion nœud le temps du test) : le message
   d'erreur générique existant s'affiche, `isLoading` repasse à `false`, pas de zip partiel généré.
5. **Non-régression** : front/catalogue/auth anonyme toujours 200 (checklist Phase 0 de
   `PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md`), aucune erreur de build liée à `jszip` (avertissement
   CommonJS "not ESM" possible et non bloquant, comme déjà vu pour `geotiff`/`fuzzysort`).

---

## 7. Checklist de contrôle (pour la revue, à cocher par le relecteur)

- [ ] `jszip` ajouté en `dependencies` (pas `devDependencies`), `npm install` relancé.
- [ ] `mat-radio-group`/`mat-radio-button` entièrement remplacés par des `mat-checkbox`
      indépendantes ; `MatCheckbox` importé et enregistré dans le composant standalone.
- [ ] `form`/`FormGroup`/`fb` retirés s'ils ne sont plus utilisés ailleurs dans le composant
      (vérifié par grep avant suppression des imports).
- [ ] `onDownloadFormat()` : 1 sélection → comportement identique à avant ; ≥2 sélections → zip
      unique via `forkJoin` + `JSZip`, gestion d'erreur factorisée (`handleDownloadError()`).
- [ ] Anti-collision de noms dans le zip (deux médias de même `media_name` ne s'écrasent pas).
- [ ] Bouton désactivé si `selectedMedias.length === 0`.
- [ ] `type="submit"` → `type="button"` (plus de `<form>`).
- [ ] (Optionnel) libellé du bouton dynamique zip vs fichier unique, clé i18n ajoutée dans `fr.json`
      uniquement (seul fichier de langue existant).
- [ ] Validation §6 exécutée sur un JDD réel multi-fichiers avant de considérer la PR terminée.
- [ ] Commit conventionnel anglais, sans trailer d'IA (cf. mémoire `feedback-pr-atomiques`).

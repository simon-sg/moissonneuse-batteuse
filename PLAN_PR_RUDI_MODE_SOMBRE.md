# Mode sombre pour le portail RUDI (front Angular) — Guide d'implémentation

> **À lire en entier avant de toucher quoi que ce soit.** Ce document est un plan d'exécution
> détaillé pour un futur agent d'implémentation, moins puissant que celui qui l'a écrit (destiné à
> être exécuté par `opencode` avec un modèle gratuit). Il a été produit après une exploration
> complète et vérifiée (lecture directe des fichiers + `grep` exhaustifs sur les usages de chaque
> variable CSS concernée) du front Angular du portail RUDI source, **pas une supposition**.
>
> **Règles impératives pour l'agent qui implémente :**
> 1. **Toujours `Read`/`cat` le fichier avant de l'éditer** — les extraits ci-dessous ont été
>    vérifiés le 2026-07-29 par lecture directe des fichiers réels, mais peuvent avoir bougé d'ici
>    l'implémentation. Repère le code par son **contenu**, pas par un numéro de ligne supposé.
> 2. **Ne rien inventer.** Si un fichier/chemin/commande ne correspond pas à ce qui est décrit,
>    **arrête-toi et documente l'écart** dans ton dernier message plutôt que de deviner.
> 3. **Ne renomme et ne modifie la valeur d'aucune variable CSS existante en dehors de celles
>    listées explicitement en section 3.** Ce projet réutilise abondamment les mêmes variables CSS
>    (`--white`, `--black`, `--secondary-color`, `--banner-solid-color`...) à la fois comme couleur
>    de fond de bloc ET comme couleur de texte-sur-fond-coloré (ex. texte blanc sur un bouton bleu).
>    Les inverser en mode sombre casserait la lisibilité de ces boutons. La section 3 explique
>    précisément quelles variables sont sûres à modifier et pourquoi les autres ne le sont pas —
>    **c'est le piège principal de cette tâche, ne pas le réintroduire par "simplification".**
> 4. **Une seule PR, un seul commit logique** (voir mémoire `feedback-pr-atomiques` : PR séparées,
>    un problème chacune — ici le problème est unique : "ajouter un mode sombre bascule dans la
>    topbar"). Message de commit conventionnel anglais, **sans trailer d'IA** (contributions
>    upstream signées par Simon).
> 5. **Portée volontairement bornée** (section 8, "Ce qui n'est pas couvert par cette PR") : ce plan
>    ne prétend pas rendre chaque pixel du portail thématisable en une passe. Il livre une
>    infrastructure de mode sombre réelle et fonctionnelle qui couvre le chrome de l'application
>    (topbar, fond de page, composants Angular Material) et la majorité des composants métier (ceux
>    qui utilisent déjà les variables CSS partagées). Une trentaine de fichiers `.component.scss`
>    avec des couleurs codées en dur restent hors périmètre, listés en section 8 — ne pas tenter de
>    les couvrir "pendant qu'on y est", ça multiplierait le risque de régression pour un gain
>    marginal dans cette première passe.
> 6. **Exécute la validation (section 9) avant de considérer la tâche terminée**, notamment un
>    build réel avec le Node épinglé (voir section 2) — pas seulement une relecture du diff.

---

## 1. Contexte et demande

**Demande de Simon** : ajouter un mode sombre au portail RUDI, avec un bouton dans la topbar, en
haut à droite, pour basculer entre mode clair et mode sombre.

**Approche retenue** : une classe `dark-theme` posée sur `<html>` par un service Angular
(`ThemeService`), qui pilote :
- des **variables CSS custom properties** déjà utilisées partout dans le SCSS de l'app (fond de
  page, texte, bordures) — réécrites sous le sélecteur `:root.dark-theme`,
- un **second thème Angular Material** (couleurs seulement, `mat.m2-define-dark-theme` +
  `mat.all-component-colors()`) scopé sous `.dark-theme`, pour que les composants Material
  (cartes, dialogues, menus, champs de formulaire, tableaux, onglets...) basculent automatiquement
  — c'est le mécanisme officiellement documenté par Angular Material pour un thème alterné piloté à
  l'exécution (pas de rebuild nécessaire pour changer de thème).

Préférence utilisateur persistée en `localStorage`, avec repli sur la préférence système
(`prefers-color-scheme: dark`) si aucune préférence n'a encore été choisie.

---

## 2. Emplacements

| Rôle | Chemin |
|---|---|
| Portail source (monorepo Java + Angular) | `/media/simon/DATA4T/Dev/rudi-portal-source` |
| Racine du front Angular | `rudi-application/rudi-application-front-office/angular-project/` |
| Procédure de build/déploiement/validation du front | `/media/simon/DATA4T/Dev/moissonneuse-batteuse/PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md` (section 3 « Front Angular » : Node épinglé **v20.15.1**, pas le Node système — voir mémoire `reference-portail-front-build-node`) |
| Pipeline moissonneuse-batteuse (référence uniquement, **non modifié** par ce plan) | `/media/simon/DATA4T/Dev/moissonneuse-batteuse` |
| Repo partagé entre tâches concurrentes | voir mémoire `feedback-rudi-repo-partage-worktree` — **ne jamais `git add -A`**, vérifier `git status`/`git branch --show-current` avant de toucher au checkout partagé, utiliser un `git worktree` isolé en cas de doute |

Stack : Angular 21 (`@angular/material` v21.2.14), composants **standalone** (imports déclarés dans
`@Component({imports:[...]})`, pas de NgModule), syntaxe de template moderne `@if`/`@for`/`@else`.
Thème Material actuel défini avec l'API M2 "legacy" (`mat.m2-define-light-theme`, palettes
indigo/pink/red reproduisant l'ancien thème prebuilt "indigo-pink") dans
`src/app/styles/_mat-theming.scss` — ce plan reste sur la même API M2 pour le thème sombre, par
cohérence (ne pas migrer vers M3 dans cette PR).

---

## 3. Variables CSS : lesquelles sont sûres à inverser, lesquelles ne le sont pas

Fichier de référence : `src/app/styles/_colors.scss` (bloc `:root` unique, toutes les couleurs de
l'app). Vérifié par `grep -rn` exhaustif sur chaque variable dans tout `src/app/**/*.scss` le
2026-07-29.

### 3.1 — Variables **dangereuses à inverser**, ne pas y toucher

| Variable | Pourquoi elle est piégée |
|---|---|
| `--white`, `--black`, `--secondary-color` | Utilisées à la fois comme fond de bloc ET comme couleur de **texte blanc/noir sur fond coloré** (ex. `--secondary-color` = texte blanc sur bouton bleu `--primary-color`, sur le footer bleu marine, sur les icônes de bannière — **52 usages**, très majoritairement du texte-sur-fond-coloré). Les inverser rendrait ce texte illisible (texte sombre sur fond resté coloré). |
| `--banner-solid-color`, `--banner-solid-color-60` | Nommées "fond de bandeau" mais utilisées **majoritairement comme couleur de texte** sur des boutons/bandeaux `--primary-color` (`common-style.scss` mixin `button-blue()`, `create-project-stepper.scss`, `contact-button.component.scss`...). Même piège que ci-dessus. |
| `--accent-color`, `--accent-color-svg`, `--focus`, `--error-color`, `--error-color-text`, `--success-color`, `--green-color-svg`, `--rudi-header-primary-color` | Couleurs de marque/statut (orange accent, bleu focus, rouge erreur, vert succès). Restent inchangées en mode sombre — pratique standard, elles ont un contraste suffisant sur fond sombre comme sur fond clair sans retouche. |
| `--rudi-login-background-color` | Fond spécifique à la page de connexion (`login.component.scss`, déjà dans la liste des ~30 fichiers à couleurs codées en dur, hors périmètre — voir section 8). Ne pas l'isoler du reste de cette page. |

### 3.2 — Variables **sûres à inverser** (retenues pour cette PR)

| Variable | Usage vérifié | Raison de la sûreté |
|---|---|---|
| `--primary-text` (40 usages), `--secondary-text` (15 usages), `--primary-text-light` | Texte de corps de page (labels, paragraphes, titres) posé sur le fond blanc de la page. Aucun usage trouvé en texte-sur-fond-coloré. |
| `--blocks-and-menus-outline`, `--keywords-outline`, `--fake-line` | Exclusivement `border-color`/`background-color` de bordures neutres (grep vérifié : aucun usage en `color:` de texte de contenu, sauf `mat-divider` où c'est la couleur du trait lui-même). |

### 3.3 — Cas particulier : `--primary-color`

**94 usages au total**, à la fois comme **fond** de bouton/badge/bandeau (36 occurrences —
`background(-color): var(--primary-color)`, toujours combiné avec du texte clair
`--secondary-color`/`--banner-solid-color` par-dessus, cohérent dans les deux modes) ET comme
**texte** de navigation/titres/liens sur le fond blanc de la page (58 occurrences — ex.
`.nav-link-global { color: var(--primary-color) }` dans `header.component.scss`). Aucune séparation
propre n'existe entre ces deux rôles sans réécrire ~94 sites d'appel (hors périmètre de cette PR).

**Décision retenue** : une seule valeur éclaircie en mode sombre (`#6badd9` au lieu de `#004680`),
utilisée pour les deux rôles. Résultat assumé : les liens/titres restent lisibles sur fond sombre
(c'est le principal), les boutons à fond `--primary-color` prennent une teinte légèrement plus
claire qu'en mode clair (variation de marque acceptable, pratique courante en mode sombre — ce
n'est **pas** un bug à corriger dans cette PR). Si un site d'appel précis pose un problème de
contraste visible en test réel, le documenter plutôt que de complexifier le système de variables
pour ce seul cas.

---

## 4. Nouveau fichier — `ThemeService`

**Chemin** : `src/app/core/services/theme.service.ts` (nouveau fichier — vérifier que
`src/app/core/services/` existe bien, c'est déjà le dossier de `authentication.service.ts`,
`breakpoint-observer.service.ts` etc. vus dans `header.component.ts`).

```ts
import {DOCUMENT} from '@angular/common';
import {Inject, Injectable} from '@angular/core';
import {BehaviorSubject} from 'rxjs';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'rudi-theme';
const DARK_CLASS = 'dark-theme';

/**
 * Gère la préférence de thème clair/sombre de l'utilisateur : lecture initiale
 * (localStorage, puis repli sur la préférence système), application de la classe
 * `dark-theme` sur <html>, et persistance du choix.
 */
@Injectable({providedIn: 'root'})
export class ThemeService {

    private readonly themeSubject = new BehaviorSubject<Theme>(this.readInitialTheme());
    readonly theme$ = this.themeSubject.asObservable();

    constructor(@Inject(DOCUMENT) private readonly document: Document) {
        this.applyTheme(this.themeSubject.value);
    }

    get currentTheme(): Theme {
        return this.themeSubject.value;
    }

    toggleTheme(): void {
        this.setTheme(this.currentTheme === 'dark' ? 'light' : 'dark');
    }

    setTheme(theme: Theme): void {
        this.themeSubject.next(theme);
        this.applyTheme(theme);
        try {
            localStorage.setItem(STORAGE_KEY, theme);
        } catch {
            // stockage indisponible (navigation privée stricte...) : le choix ne sera pas
            // mémorisé au prochain chargement, mais le toggle reste fonctionnel pour la session.
        }
    }

    private applyTheme(theme: Theme): void {
        this.document.documentElement.classList.toggle(DARK_CLASS, theme === 'dark');
    }

    private readInitialTheme(): Theme {
        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            if (stored === 'light' || stored === 'dark') {
                return stored;
            }
        } catch {
            // stockage indisponible : repli direct sur la préférence système
        }
        const prefersDark = typeof window !== 'undefined' && !!window.matchMedia
            && window.matchMedia('(prefers-color-scheme: dark)').matches;
        return prefersDark ? 'dark' : 'light';
    }
}
```

La classe est posée sur `document.documentElement` (`<html>`, pas `<body>`) : les CSS custom
properties héritent à travers tout le DOM depuis n'importe quel ancêtre, y compris le
`.cdk-overlay-container` qu'Angular Material ajoute en dernier enfant de `<body>` pour les
dialogues/menus/autocomplete — poser la classe sur `<html>` couvre donc aussi les overlays sans
traitement spécial.

---

## 5. Nouveau composant — bouton de bascule (`ThemeToggleComponent`)

**Dossier** : `src/app/shared/core/layout/theme-toggle/` (nouveau — à côté de
`src/app/shared/core/layout/header/` et `.../footer/` déjà existants).

**`theme-toggle.component.ts`** :
```ts
import {AsyncPipe} from '@angular/common';
import {Component} from '@angular/core';
import {MatIconButton} from '@angular/material/button';
import {MatIcon} from '@angular/material/icon';
import {MatTooltip} from '@angular/material/tooltip';
import {TranslatePipe} from '@ngx-translate/core';
import {ThemeService} from '@core/services/theme.service';

@Component({
    selector: 'app-theme-toggle',
    templateUrl: './theme-toggle.component.html',
    styleUrls: ['./theme-toggle.component.scss'],
    imports: [MatIconButton, MatIcon, MatTooltip, TranslatePipe, AsyncPipe]
})
export class ThemeToggleComponent {

    readonly theme$ = this.themeService.theme$;

    constructor(private readonly themeService: ThemeService) {
    }

    handleClickToggle(): void {
        this.themeService.toggleTheme();
    }
}
```

**`theme-toggle.component.html`** :
```html
@if (theme$ | async; as theme) {
  <button mat-icon-button
    type="button"
    class="theme-toggle-btn"
    [attr.aria-label]="(theme === 'dark' ? 'header.themeToggle.switchToLight' : 'header.themeToggle.switchToDark') | translate"
    [matTooltip]="(theme === 'dark' ? 'header.themeToggle.switchToLight' : 'header.themeToggle.switchToDark') | translate"
    (click)="handleClickToggle()">
    @if (theme === 'dark') {
      <mat-icon fontIcon="light_mode" aria-hidden="true"></mat-icon>
    } @else {
      <mat-icon fontIcon="dark_mode" aria-hidden="true"></mat-icon>
    }
  </button>
}
```
> `fontIcon="light_mode"`/`"dark_mode"` : icônes standard de la police Material Icons déjà utilisée
> ailleurs dans ce header (`fontIcon="menu"`, `"close"`, `"account_circle"` dans
> `header.component.html`) — pas de nouvel asset à charger.

**`theme-toggle.component.scss`** :
```scss
.theme-toggle-btn {
    margin: 0 4px;
    color: var(--secondary-text);

    mat-icon {
        scale: 1.2;
    }
}
```
> Réutilise `--secondary-text` (texte noir en mode clair, texte clair en mode sombre — voir §3.2) :
> l'icône reste cohérente avec le reste du texte du header sans variable dédiée.

---

## 6. Câblage dans le header

### 6.1 — `header.component.ts`

Ajouter l'import et l'entrée `imports` (vérifié le 2026-07-29, imports actuels ligne ~30-31) :

AVANT :
```ts
import {CustomRouterlinkDirective} from '@shared/utils/directives/custom-routerlink-directive/custom-routerlink.directive';
import {CustomizationDescription, KonsultService} from 'micro_service_modules/konsult/konsult-api';
import {forkJoin, switchMap} from 'rxjs';


const DEFAULT_PICTO: Base64EncodedLogo = '/assets/images/logo_bleu_orange.svg';

@Component({
    selector: 'app-header',
    templateUrl: './header.component.html',
    styleUrls: ['./header.component.scss'],
    imports: [RouterLink, CustomRouterlinkDirective, NgClass, NgTemplateOutlet, MatButton, MatMenuTrigger,
        MatMiniFabAnchor, MatIcon, MatMiniFabButton, RouterLinkActive, MatMenu, MatMenuItem, TranslatePipe]
})
```
APRÈS :
```ts
import {CustomRouterlinkDirective} from '@shared/utils/directives/custom-routerlink-directive/custom-routerlink.directive';
import {ThemeToggleComponent} from '@shared/core/layout/theme-toggle/theme-toggle.component';
import {CustomizationDescription, KonsultService} from 'micro_service_modules/konsult/konsult-api';
import {forkJoin, switchMap} from 'rxjs';


const DEFAULT_PICTO: Base64EncodedLogo = '/assets/images/logo_bleu_orange.svg';

@Component({
    selector: 'app-header',
    templateUrl: './header.component.html',
    styleUrls: ['./header.component.scss'],
    imports: [RouterLink, CustomRouterlinkDirective, NgClass, NgTemplateOutlet, MatButton, MatMenuTrigger,
        MatMiniFabAnchor, MatIcon, MatMiniFabButton, RouterLinkActive, MatMenu, MatMenuItem, TranslatePipe,
        ThemeToggleComponent]
})
```
> Respecte l'ordre alphabétique approximatif déjà présent dans les imports du fichier (imports
> `@shared/...` groupés) — vérifier au `Read` avant d'éditer, ne pas casser un éventuel tri ESLint
> (`import/order`) si le projet en a un configuré ; sinon l'ordre exact importe peu.

### 6.2 — `header.component.html`

Le bouton doit apparaître **en haut à droite**, donc comme dernier élément de `#main-nav` (rangée
flex, le dernier enfant est le plus à droite), **en dehors** des blocs conditionnels
mobile/desktop pour être visible dans les deux cas.

Repérer ce bloc (vérifié le 2026-07-29, fin de `#main-nav`) :

AVANT :
```html
      @if (!isConnectedAsUser) {
        <div>
          @if (mediaSize.isDeviceMobile) {
            <div>
              <a class="login-btn-mobile" href="#" routerLink="login" mat-mini-fab
                aria-label="Redirection vers la page de connexion"
                (click)="handleClickGoLogin()">
                <mat-icon fontIcon="account_circle" aria-hidden="false" aria-label="Ouvrir le menu"></mat-icon>
              </a>
            </div>
          }
          @if (mediaSize.isDeviceDesktop) {
            <div>
              <a class="login-btn-desktop" href="#" routerLink="login"
                aria-label="Redirection vers la page de connexion"
                (click)="handleClickGoLogin()">
                {{ 'header.logIn'| translate }}
              </a>
            </div>
          }
        </div>
      }
    </div>
  </div>
```
APRÈS :
```html
      @if (!isConnectedAsUser) {
        <div>
          @if (mediaSize.isDeviceMobile) {
            <div>
              <a class="login-btn-mobile" href="#" routerLink="login" mat-mini-fab
                aria-label="Redirection vers la page de connexion"
                (click)="handleClickGoLogin()">
                <mat-icon fontIcon="account_circle" aria-hidden="false" aria-label="Ouvrir le menu"></mat-icon>
              </a>
            </div>
          }
          @if (mediaSize.isDeviceDesktop) {
            <div>
              <a class="login-btn-desktop" href="#" routerLink="login"
                aria-label="Redirection vers la page de connexion"
                (click)="handleClickGoLogin()">
                {{ 'header.logIn'| translate }}
              </a>
            </div>
          }
        </div>
      }
      <app-theme-toggle></app-theme-toggle>
    </div>
  </div>
```
> `<app-theme-toggle>` est ajouté juste avant le `</div>` qui ferme `#main-nav` (le tout premier
> `</div>` après le bloc `!isConnectedAsUser`, PAS le second qui ferme `.header-container` — bien
> vérifier l'imbrication au `Read` avant d'éditer, ce fichier a plusieurs `</div>` consécutifs à cet
> endroit).

### 6.3 — `header.component.scss`

Remplacer les couleurs codées en dur qui empêchent le header lui-même de basculer en sombre. Deux
substitutions systématiques dans ce fichier :
- toute couleur de **fond** codée en dur qui vaut blanc (`#FFFFFF`, `#ffffff`) → `var(--surface-color)`
  (nouvelle variable, voir §7 — vaut blanc en mode clair, une surface sombre en mode sombre)
- toute couleur codée en dur `#1D1D1B` (icônes, bordures de focus) → `var(--secondary-text)`
  (existante, voir §3.2 — noir en mode clair, clair en mode sombre)
- la bordure du header `border-bottom: 1px solid #d5d8de;` → `border-bottom: 1px solid var(--border-color);`
  (nouvelle variable, voir §7 — mêmes raisons)

Exemple concret sur le bloc le plus visible (vérifié ligne ~1-12) :

AVANT :
```scss
.header-container {
    position: fixed;
    z-index: 2;
    display: flex;
    justify-content: space-between;
    align-items: center;
    width: 100%;
    height: var(--app-header-height);
    padding: 0.5rem 1.3rem;
    background-color: #FFFFFF;
    border-bottom: 1px solid #d5d8de;
}
```
APRÈS :
```scss
.header-container {
    position: fixed;
    z-index: 2;
    display: flex;
    justify-content: space-between;
    align-items: center;
    width: 100%;
    height: var(--app-header-height);
    padding: 0.5rem 1.3rem;
    background-color: var(--surface-color);
    border-bottom: 1px solid var(--border-color);
}
```

Appliquer la **même substitution** partout ailleurs dans ce fichier où les mêmes littéraux
apparaissent (au moins : `.login-btn-mobile { --mat-fab-container-color: #ffffff !important; ... mat-icon { color: #1D1D1B; } }`,
`.burger-menu-btn { --mat-fab-container-color: #ffffff !important; ... mat-icon { color: #1D1D1B; } }`,
`.mat-mdc-mini-fab { background: #ffffff !important; ... &:focus-visible { border: 2px solid #1D1D1B !important; } }`).

**Vérification après édition** — cette commande ne doit plus rien trouver dans ce fichier précis :
```bash
grep -niE "#FFFFFF|#1D1D1B|#d5d8de" rudi-application/rudi-application-front-office/angular-project/src/app/shared/core/layout/header/header.component.scss
```
(si elle trouve encore une occurrence, c'est qu'un site d'appel a été oublié — le corriger avant de
continuer, ne pas laisser un résidu partiel qui rendrait le header à moitié thématisé.)

---

## 7. Variables CSS — `_colors.scss`

**Fichier** : `src/app/styles/_colors.scss`.

1. Ajouter 3 nouvelles variables au bloc `:root` existant (fond de page/surface/bordure du chrome
   applicatif — aucun conflit avec l'existant, valeurs identiques aux littéraux actuellement codés
   en dur qu'elles remplacent) :

AVANT (fin du fichier) :
```scss
    // Variables de couleurs telles que définies dans les maquettes Zeplin
    --0-classic-blue: var(--primary-color);
    --2-aplat-bandeau: var(--banner-solid-color);
    --3-texte-gris-fonc: var(--primary-text);
    --4-contours-blocs-menus: var(--blocks-and-menus-outline);
    --background-s-lection-active-tableau: var(--selected-table-row);
}
```
APRÈS :
```scss
    // Variables de couleurs telles que définies dans les maquettes Zeplin
    --0-classic-blue: var(--primary-color);
    --2-aplat-bandeau: var(--banner-solid-color);
    --3-texte-gris-fonc: var(--primary-text);
    --4-contours-blocs-menus: var(--blocks-and-menus-outline);
    --background-s-lection-active-tableau: var(--selected-table-row);

    // Chrome applicatif (mode sombre) — nouvelles variables, pas d'usage existant à préserver
    --app-background-color: #ffffff;
    --surface-color: #ffffff;
    --border-color: #d5d8de;
}

// Mode sombre — voir PLAN_PR_RUDI_MODE_SOMBRE.md §3 pour la liste des variables volontairement
// NON inversées ici (--white/--black/--secondary-color/--banner-solid-color... utilisées comme
// texte-sur-fond-coloré, les inverser casserait leur lisibilité).
:root.dark-theme {
    // Chrome applicatif
    --app-background-color: #121316;
    --surface-color: #1c1e22;
    --border-color: #34383f;

    // Texte de corps de page
    --primary-text: #d6d8dd;
    --secondary-text: #f2f3f5;
    --primary-text-light: #9aa0ab;

    // Bordures neutres
    --blocks-and-menus-outline: #454a52;
    --keywords-outline: #383c43;
    --fake-line: #383c43;

    // Bleu de marque, éclairci pour rester lisible en texte comme en fond (voir §3.3)
    --primary-color: #6badd9;
}
```

---

## 8. Fond de page et thème Material

### 8.1 — `app.component.scss`

Le fond blanc de la page entière est posé explicitement en dur (vérifié le 2026-07-29) :

AVANT :
```scss
.app {
  -ms-text-size-adjust: 100%;
  -webkit-text-size-adjust: 100%;
  -webkit-tap-highlight-color: rgba(0, 0, 0, 0);
  margin: 0;
  font-size: 16px;
  line-height: 1.42857143;
  min-height: 100%;
  height: 100%;
  background-color: #fff;
}
```
APRÈS :
```scss
.app {
  -ms-text-size-adjust: 100%;
  -webkit-text-size-adjust: 100%;
  -webkit-tap-highlight-color: rgba(0, 0, 0, 0);
  margin: 0;
  font-size: 16px;
  line-height: 1.42857143;
  min-height: 100%;
  height: 100%;
  background-color: var(--app-background-color);
}
```

### 8.2 — `_mat-theming.scss`

Ajouter un second thème Material, couleurs seulement, scopé sous `.dark-theme`. Repérer le thème
clair existant (vérifié le 2026-07-29) :

AVANT :
```scss
$app-theme: mat.m2-define-light-theme((
    color: (
        primary: $app-primary,
        accent: $app-accent,
        warn: $app-warn
    ),
    typography: $open-sans-typography
));
@include mat.all-component-themes($app-theme);
```
APRÈS :
```scss
$app-theme: mat.m2-define-light-theme((
    color: (
        primary: $app-primary,
        accent: $app-accent,
        warn: $app-warn
    ),
    typography: $open-sans-typography
));
@include mat.all-component-themes($app-theme);

// Thème sombre — couleurs seulement (typo/densité inchangées), basculé à l'exécution par la
// classe `dark-theme` posée sur <html> par ThemeService. Mêmes palettes de marque que le thème
// clair : seule la surface/le texte-sur-surface de Material change, cohérent avec §3 (les
// couleurs de marque restent inchangées en mode sombre).
$app-theme-dark: mat.m2-define-dark-theme((
    color: (
        primary: $app-primary,
        accent: $app-accent,
        warn: $app-warn
    ),
    typography: $open-sans-typography
));

.dark-theme {
    @include mat.all-component-colors($app-theme-dark);
}
```
> Si `mat.all-component-colors()` échoue à la compilation (mixin renommé/absent dans
> `@angular/material` 21.2.14 — l'API M2 legacy évolue d'une version à l'autre) : chercher le nom
> exact avec `grep -rn "all-component-colors\|component-colors" node_modules/@angular/material/_index.scss node_modules/@angular/material/core/_core.scss 2>/dev/null` (ou équivalent) et l'utiliser
> tel quel. **Ne pas remplacer par `all-component-themes` en le dupliquant tel quel** dans le
> sélecteur `.dark-theme` — cette mixin réinjecte aussi la typographie et l'espacement (density),
> ce qui provoquerait des redéfinitions inutiles/conflictuelles ; il faut la variante "couleurs
> seulement".

---

## 9. Traductions — `fr.json`

**Fichier** : `src/assets/i18n/fr.json` (seule locale du projet, vérifié le 2026-07-29 —
`src/assets/i18n/` ne contient que ce fichier).

Repérer le bloc `"header"` (vérifié ligne ~248-272) :

AVANT (fin du bloc `header`) :
```json
        "accountMenu": {
            "myAccount": "Mon compte",
            "myActivity": "Mon activité",
            "myNotifications": "Mes notifications",
            "mySelfdata": "Mes données personnelles"
        }
    },
```
APRÈS :
```json
        "accountMenu": {
            "myAccount": "Mon compte",
            "myActivity": "Mon activité",
            "myNotifications": "Mes notifications",
            "mySelfdata": "Mes données personnelles"
        },
        "themeToggle": {
            "switchToDark": "Passer en mode sombre",
            "switchToLight": "Passer en mode clair"
        }
    },
```
> Attention à la virgule ajoutée après le bloc `accountMenu` (JSON strict, une virgule en trop ou
> manquante casse le fichier entier) — valider avec `python3 -m json.tool
> src/assets/i18n/fr.json > /dev/null` après édition (ou `node -e
> "JSON.parse(require('fs').readFileSync('src/assets/i18n/fr.json'))"`).

---

## 10. Ce qui n'est pas couvert par cette PR (limitation connue, assumée)

**~30 fichiers `.component.scss` contiennent des couleurs codées en dur** (hex littéraux, hors
`_colors.scss`/`_mat-theming.scss` déjà traités) et ne basculeront donc pas en mode sombre dans
cette première passe — ils resteront rendus avec leurs couleurs de mode clair (pas d'erreur, pas de
texte illisible : ils gardent simplement leur apparence actuelle même quand `dark-theme` est actif).
Liste complète (vérifiée par `grep -rlE "#[0-9a-fA-F]{3,6}\b" src/app --include="*.scss"` le
2026-07-29, 37 fichiers dont les 4 déjà traités par cette PR — soit 33 restants) :

```
src/app/features/data-set/components/data-set-infos/data-set-infos.component.scss
src/app/features/data-set/components/select-project-dialog/select-project-dialog.component.scss
src/app/features/data-set/pages/list/list.component.scss
src/app/features/home/components/themes-section/themes-section.component.scss
src/app/features/login/pages/login/login.component.scss
src/app/features/organization/components/list-container/list-container.component.scss
src/app/features/personal-space/components/d3-bar-chart/d3-bar-chart.component.scss
src/app/features/personal-space/components/generic-data/generic-data.component.scss
src/app/features/personal-space/components/organization-tab/organization-tab.component.scss
src/app/features/personal-space/components/project-api-tab/project-api-tab.component.scss
src/app/features/personal-space/components/project-owner-detail/project-owner-detail.component.scss
src/app/features/project/components/data-set-button/data-set-button.component.scss
src/app/features/project/components/step1-project/step1-project.component.scss
src/app/features/project/components/step2-project/step2-project.component.scss
src/app/shared/business/dataset/common/data-set-card/data-set-card.component.scss
src/app/shared/business/dataset/common/dataset-list-banner/dataset-list-banner.component.scss
src/app/shared/business/dataset/common/dataset-list/dataset-list.component.scss
src/app/shared/business/dataset/filters/filter-menu/filter-menu.component.scss
src/app/shared/business/home/rudi-swiper/rudi-swiper.component.scss
src/app/shared/business/projects/project-card/project-card.component.scss
src/app/shared/core/common/boolean-data-block/boolean-data-block.component.scss
src/app/shared/core/common/error-box/error-box.component.scss
src/app/shared/core/common/paginator/paginator.component.scss
src/app/shared/core/form/password-strength/password-strength.component.scss
src/app/shared/core/form/radio-list/radio-list.component.scss
src/app/shared/core/form/uploader/uploader.component.scss
src/app/shared/core/layout/popover/popover.component.scss
src/app/shared/core/maps/map/map.component.scss
src/app/shared/core/search/multi-select-autocomplete/multi-select-autocomplete.component.scss
src/app/shared/core/workflow/fields/workflow-field-attachment/workflow-field-attachment.component.scss
src/app/shared/core/workflow/workflow-expansion/workflow-expansion.component.scss
src/app/styles/create-project-stepper.scss
src/app/styles/pagination.scss
```
**Ne pas les toucher dans cette PR.** S'ils sautent aux yeux en test réel (section 9), les noter
dans le message de fin de tâche comme candidats à une PR de suivi — ne pas les corriger à la volée,
ça romprait la règle 4 (une PR, un problème).

Egalement non couvert, volontairement : contraste fin (AA/AAA) affiné par composant, icônes SVG
`fill: var(--primary-color)` inchangées (le bleu éclairci de §3.3 s'applique aussi à elles), carte
Leaflet/OpenLayers (`map.component.scss`, dans la liste ci-dessus), tableaux ag-grid (thème
`ag-theme-alpine` importé globalement dans `styles.scss`, non retouché).

---

## 11. Validation

À exécuter après implémentation (procédure de build :
`PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md` section 3, Node épinglé **v20.15.1** —
`node_installation/node`, PAS le Node système nvm v24, voir mémoire
`reference-portail-front-build-node`) :

1. **Build propre** : `ng build` (ou la commande du `package.json` du projet) doit terminer sans
   erreur de compilation SCSS/TypeScript. C'est la vérification minimale avant tout test visuel.
2. **`fr.json` reste un JSON valide** (voir §9).
3. **Test visuel** (build + `docker cp` du `dist/` dans le conteneur nginx pour un test rapide, ou
   `ng serve` si plus simple pour ce test précis) :
   - Le bouton de bascule apparaît en haut à droite de la topbar, sur mobile ET desktop.
   - Un clic bascule immédiatement toute l'app en mode sombre : fond de page sombre, texte clair et
     lisible, header sombre avec icônes claires, cartes/menus/dialogues Material sombres.
   - Un second clic revient au mode clair.
   - Recharger la page (F5) après avoir choisi le sombre : le mode sombre est conservé
     (persistance `localStorage`).
   - Ouvrir le portail en navigation privée avec le thème système du navigateur/OS réglé sur sombre
     (aucune préférence stockée) : le portail démarre directement en mode sombre.
   - Naviguer sur quelques pages représentatives (accueil, catalogue, détail d'un JDD, page
     organisation) : pas de texte illisible (texte sombre sur fond sombre, ou clair sur clair) sur
     les zones **non listées** en section 10. Les zones listées en section 10 peuvent rester en
     apparence "mode clair" — attendu, pas un bug.
   - Ouvrir un dialogue Material (ex. depuis un bouton d'action) et un menu (ex. "Mon compte") en
     mode sombre : fond et texte basculent correctement (thème Material scopé, §8.2).
4. **Non-régression du mode clair** : avec la préférence système à clair et aucun choix stocké, le
   portail démarre en clair, identique à l'état actuel (aucune régression visuelle sur l'existant).

**Commit** : `feat(front): add dark mode toggle to the header`
**Branche** : `feat/dark-mode-toggle`

---

## 12. Checklist de contrôle (pour la revue, à cocher par le relecteur)

- [ ] `ThemeService` créé, applique la classe sur `document.documentElement`, persiste en
      `localStorage`, repli sur `prefers-color-scheme` si rien de stocké.
- [ ] `ThemeToggleComponent` créé (standalone, `mat-icon-button`, icônes `light_mode`/`dark_mode`,
      `aria-label`/tooltip traduits).
- [ ] Bouton câblé dans `header.component.html`, visible en haut à droite, mobile et desktop.
- [ ] `header.component.scss` : plus aucune occurrence de `#FFFFFF`/`#ffffff`/`#1D1D1B`/`#d5d8de`
      (vérifié par le `grep` de la fin de §6.3).
- [ ] `_colors.scss` : uniquement les variables listées en §3.2/§3.3/§7 inversées sous
      `:root.dark-theme` — **aucune** des variables listées en §3.1 n'a été touchée.
- [ ] `app.component.scss` : fond de `.app` piloté par `var(--app-background-color)`.
- [ ] `_mat-theming.scss` : thème Material sombre ajouté, scopé sous `.dark-theme`, couleurs
      seulement (pas de redéfinition de la typographie/densité).
- [ ] `fr.json` : clés `header.themeToggle.switchToDark`/`switchToLight` ajoutées, fichier toujours
      un JSON valide.
- [ ] Aucun des ~33 fichiers listés en section 10 n'a été modifié dans cette PR.
- [ ] Aucun fichier de `moissonneuse-batteuse` modifié.
- [ ] Build (`ng build`) exécuté et réussi avec le Node épinglé v20.15.1.
- [ ] Validation §11 exécutée (test visuel réel, pas seulement lecture du diff).
- [ ] Commit unique, message conventionnel anglais, **sans** trailer d'IA.

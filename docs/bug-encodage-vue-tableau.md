# Bug : accents corrompus dans la vue tableau du portail RUDI

## Statut au 2026-07-27 — CORRIGÉ ET VALIDÉ

Implémenté par un agent d'exécution sur `fix/front-table-view-utf8-encoding` (rebasé sur
`main` v3.4.1, commit `91bcd306` dans `rudi-portal-source`) — diff conforme au correctif
proposé ci-dessous, vérifié par relecture (`tsc`/`eslint` propres) et par simulation Node du
vrai module `xlsx` sur 3 cas (UTF-8 sans BOM corrigé, cp1252 réel et UTF-8+BOM inchangés).
Mergé dans `integration/tableau-carte` (commit `fb06de40`) pour test local combiné avec les
autres correctifs front déjà en place (couleurs, tooltip, flicker catalogue, carte EPSG:3857).
Rebuild + redéploiement (`docker cp` dans `rudiplatform-portail-1`) validés bout-en-bout par
Simon sur le portail réel. Prêt pour push et ouverture de PR par Simon (branche locale non
poussée, convention `fix/*` de ce dépôt).

Ce document reste la spec de référence de l'investigation et du correctif, destinée à un
agent qui n'a **aucun** contexte de l'enquête qui a mené à ce diagnostic — utile pour la
description de PR et pour toute relecture future. Ne pas rouvrir les pistes déjà écartées
(voir « Comment on sait que c'est ça » plus bas).

## Résumé du bug

Dans le portail RUDI (front-office Angular), l'onglet **« Données tabulaires »** (vue tableau)
d'une fiche de jeu de données affiche les caractères accentués corrompus : `é`/`è`/`à`/`ç`
apparaissent comme `Ã©`/`Ã¨`/`Ã \`/`Ã§` (mojibake classique UTF-8 lu en Latin-1), ou parfois
comme `�` (U+FFFD). Ceci touche tout CSV encodé en UTF-8 **sans BOM** (byte-order-mark) —
c'est-à-dire la quasi-totalité des CSV produits par des outils Linux/Python/psql, dont tous les
CSV moissonnés par le pipeline `moissonneuse-batteuse`.

La vue **carte** n'est pas concernée (autre code, autre bug déjà traité séparément — voir
`PLAN_PR_RUDI_TABLEAU_CARTE.md`).

## Cause racine

Le front-office Angular du portail décode le fichier CSV avec la librairie **SheetJS
(`xlsx`, v0.18.5)**, sans jamais préciser que le contenu est de l'UTF-8. En l'absence de BOM en
tête de fichier, SheetJS décode les octets bruts en **Latin-1/CP1252** par défaut. Un `é`
encodé en UTF-8 (octets `C3 A9`) devient alors deux caractères Latin-1 (`Ã©`) au lieu d'un seul
caractère correct.

### Fichier et lignes exacts à corriger

Dépôt : `/media/simon/DATA4T/Dev/rudi-portal-source` (base `main`, correspond à v3.4.0).

Fichier :
```
rudi-application/rudi-application-front-office/angular-project/src/app/core/services/data-set/display-table.service.ts
```

Méthode `downloadTableFile()`, lignes 61-65 (état actuel, à remplacer) :

```ts
            // parse du blob en XLSX ou CSV
            map((arrayBuffer: ArrayBuffer) => {
                return read(arrayBuffer, {
                    type: 'array'
                });
            })
```

### Chaîne d'appel (pour comprendre le contexte, ne rien changer ici)

1. `downloadTableFile()` (même fichier, ligne 43) télécharge le média via
   `konsultMetierService.downloadMetadataMedia(mediaUrl)` avec `responseType: 'blob'` (dans
   `konsult-metier.service.ts`) — correct, ceci récupère les octets bruts sans que le
   navigateur ne tente de deviner un encodage.
2. `readFile(blob)` (`display.function.ts:8-23`) fait
   `reader.readAsArrayBuffer(blob)` — toujours des octets bruts, rien n'est décodé ici.
3. Le `map()` ligne 61-65 (ci-dessus) appelle `read(arrayBuffer, {type: 'array'})` de SheetJS.
   C'est **ici** que le décodage erroné a lieu.

Dans `node_modules/xlsx/xlsx.js` (v0.18.5), `read(arrayBuffer, {type:'array'})` route un CSV
(fichier texte, ni ZIP ni OLE binaire) vers `prn_to_sheet()` :

```js
// xlsx.js ~L8397-8416
function prn_to_sheet(d, opts) {
    var str = "", bytes = ...;
    switch(opts.type) {
        ...
        case 'array': str = cc2str(d); break;   // décodage Latin-1 des octets bruts
        ...
    }
    if (bytes[0]==0xEF && bytes[1]==0xBB && bytes[2]==0xBF) str = utf8read(str.slice(3));                          // seulement si BOM UTF-8 présent
    else if (opts.type != 'string' && opts.type != 'buffer' && opts.codepage == 65001) str = utf8read(str);        // seulement si codepage:65001 fourni explicitement
    ...
}
```

Sans BOM et sans `codepage: 65001` passé en option (ce que fait le code actuel), le contenu
reste Latin-1 pour toujours, quel que soit l'encodage réel du fichier.

## Comment on sait que c'est ça (ne pas rouvrir ces pistes)

Trois autres causes possibles ont été explorées et écartées :

1. **Le pipeline de moisson produit-il un CSV mal encodé ?** Non. Tous les CSV moissonnés par
   `moissonneuse-batteuse` sont écrits en UTF-8 explicite (`src/filters/csv.py:24`,
   `src/main.py:121`). Un bug similaire (détection cp1252 trop permissive) a déjà existé et a
   été corrigé côté pipeline en 2026-07-23 (commit `16d00fc`) — mais il ne concerne que la
   phase de *lecture* des sources externes par le pipeline, pas la génération finale, qui est
   toujours UTF-8 propre.
2. **Le header HTTP `Content-Type`/charset servi par le nœud RUDI est-il en cause ?** Non — et
   ça ne peut structurellement pas l'être ici. Le front télécharge le fichier avec
   `responseType: 'blob'` puis `FileReader.readAsArrayBuffer()` (étapes 1-2 ci-dessus) : un
   `Blob`/`ArrayBuffer` ignore totalement le paramètre `charset` du header HTTP — il n'y a
   **aucun décodage géré par le navigateur** à ce stade. Le seul décodage a lieu ensuite, en
   JS, dans SheetJS (étape 3). Notez pour information (hors-scope de ce fix, voir
   « Hors-scope » plus bas) qu'un gap existe bien côté service de stockage du nœud
   (`httpService.js:811`, `res.type(mimetype)` sans charset), mais il n'a aucun impact sur ce
   bug précis.
3. **Un bug dans `catalogue.py` (visionneuse propre au pipeline, indépendante du portail) ?**
   Non — cette visionneuse locale gère déjà l'encodage correctement via
   `_detecter_encodage_bytes()`. Elle est de toute façon un artefact différent (fichier
   `*_viewer.html` généré localement), pas la vue tableau du portail RUDI qui est le sujet de
   ce bug.

## Correctif à appliquer

Remplacer le bloc `map()` (lignes 61-65 citées plus haut) par un pré-décodage UTF-8 strict,
avec repli sur le comportement actuel si le contenu n'est pas de l'UTF-8 valide :

```ts
            // parse du blob en XLSX ou CSV
            map((arrayBuffer: ArrayBuffer) => {
                try {
                    const text = new TextDecoder('utf-8', {fatal: true}).decode(arrayBuffer);
                    return read(text, {type: 'string'});
                } catch {
                    return read(arrayBuffer, {type: 'array'});
                }
            })
```

Aucun import supplémentaire n'est nécessaire : `TextDecoder` est une API globale du navigateur
(disponible nativement — voir « Pourquoi c'est sûr » ci-dessous), et `read` de `xlsx` est déjà
importé ligne 11 du fichier (`import {read, utils, WorkBook} from 'xlsx';`).

### Pourquoi ce fix et pas `codepage: 65001` directement

Une alternative plus simple serait de passer `codepage: 65001` à
`read(arrayBuffer, {type: 'array', codepage: 65001})` (SheetJS route alors vers `utf8read()`
inconditionnellement). **Ne pas faire ça** : le portail RUDI est une base de code partagée
(`rudi-platform`), utilisée par d'autres producteurs qui peuvent y déposer de vrais CSV
Windows-1252/Latin-1 (l'ancien comportement, bien que faux pour l'UTF-8-sans-BOM, était
*correct* pour ces fichiers-là). Forcer `codepage: 65001` casserait leur affichage au lieu de
le réparer.

Le correctif proposé (try/catch sur un décodage UTF-8 **strict**, `fatal: true`) ne change rien
dans ces deux cas :
- **Fichier XLS/XLSX binaire** (archive ZIP ou format OLE) : le décodage UTF-8 strict échoue
  systématiquement sur du contenu binaire → tombe dans le `catch` → chemin
  `read(arrayBuffer, {type: 'array'})` actuel, **inchangé**.
- **CSV réellement Windows-1252/Latin-1** (accents encodés sur 1 octet, hors plage ASCII) :
  le décodage UTF-8 strict échoue aussi (un octet Latin-1 seul du type `0xE9` pour `é` n'est
  pas une séquence UTF-8 valide) → même repli, **comportement actuel préservé**.

Seul le cas aujourd'hui cassé change : un CSV UTF-8 valide sans BOM est maintenant décodé
correctement.

### Pourquoi c'est sûr (compatibilité navigateurs)

`TextDecoder` est nativement supporté par toutes les cibles de ce projet : le fichier
`angular-project/.browserslistrc` exclut explicitement IE11 (`not IE 11`) et cible
Chrome/Firefox/Edge/Safari récents ; `tsconfig.json` compile en `target: "es2022"`. Aucun
polyfill n'est nécessaire.

## Tests automatisés

Aucun test unitaire n'existe aujourd'hui pour `display-table.service.ts` (vérifié : pas de
fichier `*.spec.ts` correspondant dans le projet). La validation est donc uniquement
fonctionnelle (voir ci-dessous) — ne pas bloquer le correctif sur l'absence de test unitaire,
mais un test unitaire simple est un plus s'il est facile à ajouter (mock d'un
`ArrayBuffer` encodant un CSV UTF-8 avec accents → vérifier que `convertToDisplayableData()`
produit les bonnes chaînes ; et un `ArrayBuffer` non-UTF-8 → vérifier que le comportement de
repli s'exécute sans exception).

## Conventions de branche et commit

Cohérent avec les branches déjà présentes dans ce dépôt (`fix/front-inject-override-css`,
`fix/front-map-guard-on-displayed-media`, `fix/front-card-title-tooltip`, etc.) :

- Nouvelle branche : `fix/front-table-view-utf8-encoding`, basée sur `main`.
- Un seul commit, message suggéré :
  ```
  fix(front): decode CSV table view as UTF-8 before SheetJS's Latin-1 default

  SheetJS decodes plain-text CSV as Latin-1 unless a BOM is present or
  `codepage: 65001` is passed explicitly. UTF-8 CSV without a BOM (produced by
  most Linux/Python tooling, including every dataset harvested by the
  moissonneuse-batteuse pipeline) was silently mis-decoded, turning accented
  characters into mojibake in the table view. Try a strict UTF-8 decode first;
  fall back to the previous behavior (unchanged) for anything that isn't valid
  UTF-8, so real binary XLS/XLSX and genuine Windows-1252 CSV keep working
  exactly as before.
  ```
- Ne pas pousser la branche soi-même (convention du dépôt : c'est Simon qui pousse et ouvre la
  PR une fois la branche prête localement — voir les autres branches `fix/*` non poussées dans
  ce même dépôt).

## Plan de validation fonctionnelle

Même méthodologie que pour le bug `overrideCssFile` déjà corrigé
(`docs/bug-overrideCssFile.md`) :

1. **Builder le front** avec le Node épinglé du projet (`node_installation/node`, v20.15.1) —
   **pas** le Node système (échoue sur `@ampproject/remapping` avec un Node trop récent) :
   ```bash
   cd /media/simon/DATA4T/Dev/rudi-portal-source
   mvn -pl rudi-application/rudi-application-front-office -am install
   ```
   Vérifier que le `dist/` a bien été régénéré (ne pas se fier au seul exit code de `mvn`).
2. **Déployer** le nouveau bundle dans le conteneur front en place (méthode éphémère
   documentée dans `PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md` §3, `docker cp` du `dist/` dans
   `rudiplatform-portail-1`), puis re-poser la config Traefik complète si le conteneur a été
   recréé (voir `PROCEDURE_PATCH_IMAGE_PORTAIL_RUDI.md` — piège récurrent connu).
3. **Tester** : ouvrir la fiche d'un jeu de données CSV moissonné par le pipeline (n'importe
   lequel contenant des données avec accents — communes, adresses, etc.), onglet « Données
   tabulaires ». Vérifier que les caractères accentués s'affichent correctement (`é`, `è`,
   `à`…), plus de `Ã©`/`�`.
4. **Non-régression** : si un jeu de données avec média XLS/XLSX est disponible sur le nœud,
   vérifier que sa vue tableau s'affiche toujours normalement (chemin de code binaire
   inchangé).
5. **Capture d'écran** avant/après de la vue tableau, à la manière de
   `docs/portail-couleurs-validation-2026-07-27.png`, comme preuve visuelle jointe à la PR.

## Hors-scope (ne pas traiter dans cette PR)

Deux observations annexes relevées pendant l'investigation, sans rapport direct avec ce bug —
à ne pas corriger ici pour garder la PR atomique (candidats à un ticket séparé si Simon le
souhaite) :

- `rudi-node-build/.../rudi-storage/src/httpService.js:786-811` (`sendFile`) : le charset
  capturé à l'upload (`basicfile.js:58-61`) n'est jamais réémis dans le `Content-Type` au
  moment du téléchargement (`res.type(mimetype)` sans charset). Sans impact sur ce bug précis
  (voir « Comment on sait que c'est ça », point 2) mais pourrait affecter d'autres consommateurs
  qui, eux, dépendent du header HTTP pour deviner l'encodage (ex. ouverture directe de l'URL du
  fichier dans un nouvel onglet du navigateur).
- `moissonneuse-batteuse/src/catalogue.py:110` (`_entetes_rapides`) : décodage figé en
  `utf-8-sig` au lieu de passer par `_detecter_encodage_bytes()` comme les autres fonctions de
  lecture CSV du même fichier. Impact limité (sert uniquement à la détection de format
  dictionnaire de colonnes, jamais à la donnée affichée) et sans rapport avec le portail.

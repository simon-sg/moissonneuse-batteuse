# Bug : `overrideCssFile` non chargé par le front-office Angular

## Résumé

Le champ `overrideCssFile` de `customization.json` est correctement traité par le backend
Konsult (lecture, mapping UUID, servitude via `/konsult/v1/customizations/resources/`),
mais le front-office Angular **ne demande jamais le fichier CSS** et ne l'injecte pas dans
la page.

## Versions concernées

- `rudiplatform/rudi-application-front-office` : **v3.3.12** (testé), **v3.4.0** (vérifié via Docker Hub — le champ n'est pas non plus utilisé)
- `rudiplatform/rudi-microservice-konsult` : v3.3.12

## Comportement attendu

D'après la doc et la structure de `customization.json`, le front-office devrait :

1. Appeler `GET /konsult/v1/customizations/description?lang=fr`
2. Récupérer le champ `override_css_file` (valeur : `"overrideCssFile.css"`)
3. Appeler `GET /konsult/v1/customizations/resources/overrideCssFile.css`
4. Injecter un `<link rel="stylesheet">` dans `document.head`

## Comportement observé

- L'étape 1-2 fonctionne : l'API retourne bien `override_css_file: "overrideCssFile.css"`
- L'étape 3 **n'est jamais appelée** : le navigateur ne fait aucune requête vers `/konsult/v1/customizations/resources/overrideCssFile.css`
- Le fichier CSS n'est donc jamais chargé, les variables CSS restent aux valeurs par défaut

## Preuve technique

Le bundle JS Angular (`main.*.js`) ne contient aucune référence à :
- `overrideCssFile`
- `override_css_file`
- `overrideCss`
- `style-override`

Le champ est présent dans le modèle OpenAPI (`rudi-konsult-model.json`) et retourné par
l'API, mais le composant Angular ne le consomme pas.

## Fichier affecté côté Konsult

`CustomizationHelper.java` — la méthode `fillResourceMappingAndReplaceData()` mappe bien
le chemin `/style-override.css` vers la clé `"overrideCssFile.css"` et le backend le sert
via `ResourcesHelper.loadResources()`. Le backend fonctionne correctement.

## Impact

Impossible de personnaliser les couleurs du portail (palette `--primary-color`,
`--accent-color`, etc.) sans modifier le bundle Angular compilé ou les fichiers CSS
Magnolia directement.

## Suggestion de correction

Dans le composant Angular qui consomme la `CustomizationDescription` (probablement
`HeaderComponent` ou `AppComponent`), ajouter :

```typescript
// Après réception de CustomizationDescription
if (description.override_css_file) {
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = `/konsult/v1/customizations/resources/${description.override_css_file}`;
  document.head.appendChild(link);
}
```

Ou, dans `index.html` :

```html
<link rel="stylesheet" href="/konsult/v1/customizations/resources/overrideCssFile.css">
```

# Indexation Google des pages JDD — rudi.rennesmetropole.fr

> **Problème** : Google n'indexe pas les pages dataset du portail RUDI.
> 549 URLs dans le sitemap → **0 pages dataset indexées**.

---

## Diagnostic

Ce que Googlebot reçoit pour chaque URL (avant exécution JS) :

```html
<title>Rudi</title>
<body class="mat-typography">
  <app-root></app-root>
</body>
```

Zéro contenu. Zéro meta tag. Le portail est un **Angular SPA en CSR pur** — tout le rendu est côté client.

### Ce qui existe déjà

| Élément | État | Problème |
|---------|------|----------|
| `robots.txt` | OK (Allow: /, Sitemap) | — |
| Sitemap `/konsult/v1/sitemap/sitemap` | 549 URLs, XML valide | Pas de `<lastmod>` |
| API catalogue `/konsult/v1/datasets/metadatas` | 492 JDD, métadonnées riches | Non exploité pour le rendu |
| Angular app | CSR pur, `<app-root>` vide | **Pas de SSR ni prerender** |
| `<title>` | Toujours `"Rudi"` (identique sur toutes les pages) | — |
| `<meta description>` | Absente | — |
| `<link rel="canonical">` | Absent | — |
| OpenGraph / Twitter Cards | Absents | — |
| JSON-LD schema.org/Dataset | Absent | — |

---

## Solutions (par coût croissant)

### 1. robots.txt + sitemap HTTPS — 5 min

Corriger `config/konsult/robots.txt` :

```
User-agent: *
Allow: /

Sitemap: https://rudi.rennesmetropole.fr/konsult/v1/sitemap/sitemap
```

### 2. Sitemap avec `<lastmod>` — 30 min

Créer `config/konsult/sitemap/sitemap.json` (doc : `documentation/cookbook/configuration-sitemap.md`) :

```json
{
    "maxUrlCount": 50000,
    "maxUrlSize": 2048,
    "staticSitemapEntries": {
        "urlList": [
            {"location": "/home", "isRelative": true},
            {"location": "/catalogue", "isRelative": true},
            {"location": "/projets", "isRelative": true},
            {"location": "/organization", "isRelative": true}
        ]
    },
    "sitemapEntries": [
        {"type": "CMS"},
        {"type": "DATASETS"},
        {"type": "PROJECTS"}
    ]
}
```

Puis régénérer : `curl -H "Authorization: Bearer <token>" -X POST http://rudi.rennesmetropole.fr/konsult/v1/sitemap`

**Manque** : Konsult n'injecte pas `<lastmod>` dans le XML. À demander en amélioration — c'est le champ `dataset_dates.updated` des métadonnées.

### 3. Dynamic Rendering (Rendertron) — 1 jour, impact immédiat

**Quick win critique.** Intercale un proxy headless Chromium qui pré-rend les pages pour les crawlers.

```
Requête crawler (Googlebot)
  → Traefik détecte User-Agent
  → Rendertron charge la page Angular, exécute le JS, renvoie le HTML complet
  → Googlebot reçoit le contenu indexable

Requête humaine
  → Traefik → portail Angular (SPA normale)
```

**Conteneur** : `rendertron/rendertron` (Docker, ~200 Mo, Chromium headless)

**Config Traefik** (middleware) : route les UA crawlers vers Rendertron, le reste vers le portail.

**Avantages** :
- Aucune modification du code Angular
- Le HTML pré-rendu contient titre, description, contenu texte
- Indexation immédiate après déploiement

**Limites** :
- Conteneur supplémentaire à maintenir
- Latence ~2-5s pour les crawlers (négligeable)
- Le rendu dépend de la disponibilité de l'API Konsult côté serveur

**Alternative** : prerender.io (SaaS payant, même principe, zéro ops).

### 4. Angular SSR — 3-5 jours, solution root cause

Modifier `rudi-platform/rudi-portal` pour activer `@angular/ssr` :

**`app.routes.server.ts`** :

```typescript
import {RenderMode, ServerRoute} from '@angular/ssr';

export const serverRoutes: ServerRoute[] = [
    {path: '', renderMode: RenderMode.Client},
    {path: 'catalogue', renderMode: RenderMode.Server},
    {path: 'catalogue/detail/:id/**', renderMode: RenderMode.Server},
    {path: 'cms/**', renderMode: RenderMode.Server},
    {path: 'projets/**', renderMode: RenderMode.Server},
    {path: '**', renderMode: RenderMode.Client},
];
```

**Meta tags dynamiques** (dans chaque composant de page dataset) :

```typescript
import {Title, Meta} from '@angular/platform-browser';

this.title.setTitle(`${this.dataset.resource_title} - Rennes Métropole | Rudi`);
this.meta.updateTag({name: 'description', content: this.dataset.synopsis[0].text});
this.meta.updateTag({property: 'og:title', content: this.dataset.resource_title});
this.meta.updateTag({property: 'og:description', content: this.dataset.synopsis[0].text});
```

Les données sont déjà disponibles via l'API Konsult (`/konsult/v1/datasets/metadatas`) — le SSR les consomme côté serveur et les injecte dans le HTML.

### 5. JSON-LD `schema.org/Dataset` — 1 jour (complément)

Ajouter un bloc structuré dans la page de chaque JDD :

```json
{
    "@context": "https://schema.org",
    "@type": "Dataset",
    "name": "...",
    "description": "...",
    "url": "https://rudi.rennesmetropole.fr/catalogue/detail/...",
    "license": "https://opendatacommons.org/licenses/odbl/",
    "creator": {"@type": "Organization", "name": "Rennes Métropole"}
}
```

Permet les **rich results** Google (pas indispensable pour l'indexation, mais améliore la visibilité).

---

## Priorisation

| Solution | Impact | Coût | Délai |
|----------|--------|------|-------|
| 1. robots.txt + HTTPS | Faible | 5 min | Immédiat |
| 2. sitemap + `<lastmod>` | Modéré | 30 min | Immédiat |
| 3. Dynamic Rendering (Rendertron) | **Élevé** | 1 j | 1-2 sem |
| 4. Angular SSR | **Maximum** | 3-5 j | 1-2 mois |
| 5. JSON-LD Dataset | Bonus | 1 j | 1-2 mois |

**Recommandation** : implémenter 1+2+3 en court terme (le Rendertron résout le problème sans toucher au code Angular), puis 4+5 en moyen terme comme solution définitive.

---

## Données exploitables dès maintenant

L'API catalogue expose déjà tout ce qu'il faut pour le rendu serveur :

```json
GET /konsult/v1/datasets/metadatas?limit=1
{
    "global_id": "d82ec590-...",
    "resource_title": "Accidents corporels sur Rennes Métropole",
    "synopsis": [{"lang": "fr", "text": "..."}],
    "summary": [{"lang": "fr", "text": "..."}],
    "theme": "location",
    "keywords": ["accident de la route", "..."],
    "producer": {"organization_name": "Rennes Métropole"},
    "dataset_dates": {"created": "2022-06-08", "updated": "2023-01-19"}
}
```

Aucune modification de l'API n'est nécessaire pour le SSR ni le Rendertron.

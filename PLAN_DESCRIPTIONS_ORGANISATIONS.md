# Enrichissement des descriptions d'organisations (producteurs) — côté nœud RUDI

> Spécification d'implémentation. Périmètre volontairement **limité au nœud RUDI**
> (`organization_caption` + `organization_summary`). Logo et lien externe = concepts **portail**,
> hors périmètre ici (traités plus tard côté rudi-portal).

## Context

Le pipeline enrichit déjà les **descriptions de jeux de données** vides via
`src/translation/description_secours.py` (`generer_complement()`), câblé dans les voies de moisson
et rattrapable par `src/enrichir_descriptions.py`. Les **organisations productrices**, elles, n'ont
**aucune description** : le `producer` publié est un dict à un seul champ,
`{"organization_name": ...}`. Sur le portail RUDI, la fiche producteur est donc nue.

Objectif : générer une **synthèse courte du producteur** (Wikipédia/Wikidata, sinon repli factuel)
et l'attacher à l'organisation sur le nœud RUDI, avec un rattrapage pour les producteurs déjà
publiés. On calque le schéma du système de descriptions (module pur + injection + rattrapage
idempotent).

## Faits établis (vérifiés en session)

- **Champs supportés par le nœud** : l'objet organisation RUDI accepte `organization_caption`
  (phrase courte) et `organization_summary` (2-3 phrases) — cf.
  `rudi_node_write/rudi_types/rudi_org.py` (`RudiOrganization`). Le repo ne les remplit nulle part.
  Le schéma org du nœud n'a **ni logo ni URL** (confirmé : les 87 orgs du nœud ne portent
  aujourd'hui que `organization_id` + `organization_name`).
- **Wikipédia FR sans clé** : `GET https://fr.wikipedia.org/api/rest_v1/page/summary/<titre>` →
  `description` = phrase Wikidata courte (**CC0**) ; `extract` = 2-4 phrases (**CC BY-SA**).
  Vérifié sur « Insee ». (`thumbnail`/`originalimage` existent aussi mais on ne les utilise pas ici.)
- **Point de convergence unique** : toutes les voies de moisson construisent le `producer` au même
  endroit — `src/translation/rudi_builder.py:212` (`"producer": {"organization_name": producteur_nom}`).
  La voie data.gouv (`datagouv_to_rudi.py`) et les voies INSEE/OEB/BDNB/géo passent **toutes** par
  `construire_rudi_metadata(...)`. → **un seul point d'injection** suffit à couvrir tout le pipeline.
- **Réalité nœud à la publication** : `publier_dataset` (`rudi_node.py:174-184`) réutilise l'org
  existante par nom et **remplace** `rudi_metadata["producer"]` par le dict stocké sur le nœud
  (ligne 181-182) → pour une org déjà présente, les champs qu'on ajoute au `producer` sont perdus.
  Il faut donc, pour les orgs existantes sans résumé, un **`PUT organizations`** explicite
  (`writer.connector.put_admin_api("organizations", {…})`).

## Décisions produit (validées)

1. **Champs cibles** : `organization_caption` (courte) **et** `organization_summary` (2-3 phrases).
2. **Source/licence** : **Wikidata d'abord** (CC0) pour `caption`, repli sur la 1re phrase de
   l'`extract` Wikipédia (CC BY-SA) pour `summary`, avec suffixe d'attribution `" (source : Wikipédia)"`.
3. **Repli si pas de Wikipédia** : phrase factuelle **sans compteur de JDD**, référençant la
   plateforme source, ex. « Producteur de jeux de données moissonnés sur data.gouv.fr. »
   (+ page producteur en texte si disponible). Aucune org sans résumé.
4. **Logo / lien externe** : hors périmètre (portail, plus tard).

## Risque principal : correspondance nom → article Wikipédia

Les noms portent acronymes/préfixes (`"…(Insee)"`, `"Observatoire de l'Environnement en Bretagne (OEB)"`,
`"data.gouv.fr / communes-fr"`) qui ne matchent pas les titres Wikipédia. Stratégie **conservatrice** :
carte d'alias curée d'abord → recherche → sinon repli factuel (jamais un mauvais article).

---

## Implémentation

### 1. `src/connectors/wikipedia.py` (nouveau)

`resumer_wikipedia(nom_nettoye: str) -> dict | None`
- Recherche du meilleur titre (API opensearch/REST search FR), puis
  `GET /api/rest_v1/page/summary/<titre>` via la **session partagée** `connectors/http.py`
  (timeout explicite ~15 s — jamais `requests` direct, cf. « Conventions réseau »).
- Rejette désambiguïsations (`type != "standard"`) et `extract` vide.
- Retour : `{"caption": <description Wikidata>, "summary": <extract>, "url": <page url>}` ou `None`.
- **Cache disque** calqué sur `filters/discovery.py::_lire/_ecrire_cache_api`, sous
  `data/cache/wikipedia/`, clé = nom normalisé, TTL long (~30 j), **cache négatif inclus** (les noms
  se répètent sur des centaines de JDD → un seul hit réseau par producteur).

### 2. `src/conf/organisations.py` (nouveau, committé)

`ALIAS_ORGANISATIONS: dict[str, str | None | dict]` : nom normalisé →
- titre Wikipédia exact (`"insee" → "Institut national de la statistique et des études économiques"`), ou
- `None` pour forcer le repli factuel (ex. `"data.gouv.fr / communes-fr"`), ou
- dict `{caption, summary}` d'override manuel (cas sans article).
Amorcer avec les producteurs connus : Insee, OEB, CSTB/BDNB, Cerema, IGN, Rennes Métropole, data.gouv.fr, Etalab…

### 3. `src/translation/organisation_secours.py` (nouveau, module pur, pas de réseau propre)

Analogue de `description_secours.py` :
- `_normaliser_nom_producteur(nom)` : retire le `"(ACRONYME)"` final et le préfixe `"fournisseur / "`,
  trim/espaces. (Ne **pas** réutiliser `geographic.normaliser` — trop destructif pour la recherche/alias.)
- `enrichir_organisation(nom, *, source_label=None, page_url=None) -> {"organization_caption", "organization_summary"} | None` :
  1. carte d'alias (`ALIAS_ORGANISATIONS`) — override direct ou titre forcé ;
  2. `resumer_wikipedia()` → `caption` = description Wikidata, `summary` = 1re phrase(s) de l'extract
     + `" (source : Wikipédia)"` ;
  3. **repli factuel** : `summary = f"Producteur de jeux de données moissonnés sur {source_label}."`
     (+ `f" Page producteur : {page_url}"` si `page_url`) ; `caption` = variante courte
     (ex. `"Producteur de données ouvertes"`). `source_label` par défaut générique si absent
     (ex. `"les portails open data"`).
- **Idempotence** : gate simple = « ne rien produire d'écrasant si l'org a déjà un
  `organization_summary` non vide » (le champ part vide, contrairement au summary des JDD → pas
  besoin de marqueur textuel).

### 4. Injection inline — point unique `rudi_builder.py` + mécanique nœud `rudi_node.py`

**a) `src/translation/rudi_builder.py::construire_rudi_metadata`** (producer construit ligne 212)
- Ajouter deux params keyword-only : `source_producteur: str | None = None`, `page_producteur: str | None = None`.
- À la construction du `producer`, appeler `enrichir_organisation(producteur_nom, source_label=source_producteur, page_url=page_producteur)`
  et fusionner le résultat dans le dict `producer` (`organization_caption`/`organization_summary`).
- Best-effort : `try/except` qui n'échoue jamais la traduction.

**b) Callers** — passer le `source_label` (et `page_url` pour data.gouv) :
- `datagouv_to_rudi.py` (tabulaire, ~ligne 273) : `source_producteur="data.gouv.fr"`,
  `page_producteur=org.get("page")` (l'objet org data.gouv expose `page`, ex.
  `https://www.data.gouv.fr/organizations/etalab`). Voie géo (~ligne 461) : `source_producteur` =
  domaine du service / `config.get("producteur")`.
- `harvest_insee.py` : `source_producteur="insee.fr"`.
- `harvest_oeb.py` / `harvest_bdnb.py` : `source_producteur` = plateforme/URL source respective.

**c) `src/connectors/rudi_node.py::publier_dataset`** (bloc 174-184, déjà sous verrou de publication)
- Org **nouvelle** : le `producer` porte déjà caption/summary → rien à faire, `put_metadata` la crée enrichie.
- Org **existante sans `organization_summary`** : avant le remplacement ligne 181-182, si le `producer`
  entrant a un summary, faire `writer.connector.put_admin_api("organizations", {**org_existante, "organization_caption": …, "organization_summary": …})`,
  puis utiliser le dict mis à jour. Best-effort `try/except`, ne bloque jamais la publication.

### 5. `src/enrichir_organisations.py` (nouveau — rattrapage node-side)

Mirroir de `enrichir_contacts.py` (gabarit `--dry-run` par défaut), mais **contre le nœud** (les orgs
vivent sur le nœud, pas dans les `rudi_metadata.json`) :
- `charger_conf_rudi()` → writer ; itère `writer.organization_list` (87 orgs aujourd'hui).
- Pour chaque org à `organization_summary` vide : `enrichir_organisation(nom)` (repli à `source_label`
  générique — le nom seul est connu ici), puis `put_admin_api("organizations", {**org, caption, summary})`.
- `--dry-run` (défaut) : affiche ce qui serait posé, sans mutation. `--force` : ré-enrichit même les non vides.
- Traite les producteurs déjà publiés en un run, sans re-moissonner ni re-publier les JDD.

### 6. Câblage CLI/dashboard + doc

- Ajouter une action « Enrichir les descriptions de producteurs » à la section Maintenance de
  `src/cli.py` (à côté des rattrapages descriptions/contacts), et la refléter dans `ACTIONS`
  (`dashboard.py`) **et** le tableau JS `src/static/dashboard.js` (cf. « zéro logique dupliquée »).
- `CLAUDE.md` : nouvelle sous-section « Fallback descriptions d'organisations » (parallèle à
  « Fallback descriptions ») ; MAJ des lignes `commands`, du tableau `Key files`, note licence
  (caption CC0 Wikidata / summary CC BY-SA + attribution « source : Wikipédia »).

## Fichiers

**Créés** : `src/connectors/wikipedia.py`, `src/conf/organisations.py`,
`src/translation/organisation_secours.py`, `src/enrichir_organisations.py`,
`tests/test_organisation_secours.py`.

**Modifiés** : `src/translation/rudi_builder.py` (2 params + appel), ses callers
(`src/translation/datagouv_to_rudi.py`, `src/harvest_insee.py`, `src/harvest_oeb.py`,
`src/harvest_bdnb.py`), `src/connectors/rudi_node.py` (bloc org de `publier_dataset`),
`src/cli.py`, `src/dashboard.py`, `src/static/dashboard.js`, `CLAUDE.md`.

**Réutilisés** : `connectors/http.py` (session), `filters/discovery.py` (motif de cache),
`connectors/rudi_node.py::charger_conf_rudi` + `put_admin_api`/`organization_list`,
`enrichir_contacts.py` (gabarit `--dry-run`).

## Vérification

1. **Tests unitaires (sans réseau)** : `python3 -m unittest tests.test_organisation_secours` —
   normalisation (`"…(Insee)"`→`"Insee"`, `"data.gouv.fr / communes-fr"`→alias/skip), priorité
   alias > Wikipédia > repli, forme du repli (avec/sans `page_url`, sans compteur de JDD), gate
   d'idempotence (summary non vide → no-op). Fetch Wikipédia injecté par un faux.
2. **Connecteur en direct** : `resumer_wikipedia("Insee")`, `("Observatoire de l'Environnement en Bretagne")`,
   `("data.gouv.fr")` — vérifier caption/summary + cache (2e appel = 0 requête) + cache négatif sur un nom bidon.
3. **Rattrapage dry-run** : nœud RUDI démarré (Podman), `python3 src/enrichir_organisations.py --dry-run`
   → liste des orgs enrichies + textes proposés, sans mutation.
4. **Rattrapage réel (échantillon)** : sans `--dry-run`, puis contrôler via `writer.organization_list`
   / portail que `organization_summary`/`caption` sont posés.
5. **Inline** : re-publier un JDD d'un producteur sans résumé → l'org est créée/mise à jour enrichie ;
   couper le réseau (Wikipédia injoignable) → repli factuel, publication jamais bloquée.
6. **Régression** : `python3 -m unittest discover tests/`.

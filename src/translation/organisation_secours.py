"""
Enrichissement des descriptions d'organisations productrices.

Génère `organization_caption` (phrase courte) et `organization_summary` (2-3 phrases)
pour les organisations qui n'en disposent pas encore.

Stratégie :
  1. Carte d'alias curée (override manuel ou titre Wikipédia exact)
  2. Recherche Wikipédia FR (caption CC0 Wikidata, summary CC BY-SA Wikipédia)
  3. Repli factuel (pas de compteur de JDD)

Idempotence : ne produit rien si `organization_summary` est déjà non vide.

Usage :
    from translation.organisation_secours import enrichir_organisation
    result = enrichir_organisation("Institut national de la statistique (Insee)")
    # {"organization_caption": "...", "organization_summary": "..."}
"""
import re

from conf.organisations import ALIAS_ORGANISATIONS

_RE_ACRONYME = re.compile(r"\s*\([A-Za-z0-9]{2,}\)\s*$")
_RE_FOURNISSEUR = re.compile(r"^fournisseur\s*/\s*", re.IGNORECASE)


def _normaliser_nom_producteur(nom: str) -> str:
    """Retire le suffixe « (ACRONYME) » et le préfixe « fournisseur / ».

    Ne pas réutiliser `geographic.normaliser` — trop destructif pour la
    recherche/alias Wikipédia.
    """
    n = (nom or "").strip()
    n = _RE_FOURNISSEUR.sub("", n)
    n = _RE_ACRONYME.sub("", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def enrichir_organisation(
    nom: str,
    *,
    source_label: str | None = None,
    page_url: str | None = None,
) -> dict | None:
    """Enrichit une organisation avec caption + summary.

    Retourne {"organization_caption": ..., "organization_summary": ...} ou None
    si aucune source n'a fourni de résultat.

    Paramètres :
        nom           : nom brut de l'organisation ( tel que dans organization_name)
        source_label  : nom de la plateforme source (ex. "data.gouv.fr", "insee.fr")
        page_url      : URL de la page producteur sur la source
    """
    from connectors.wikipedia import resumer_wikipedia

    nom_nettoye = _normaliser_nom_producteur(nom)

    # --- 1. Carte d'alias ---
    alias = ALIAS_ORGANISATIONS.get(nom)
    if alias is None:
        alias = ALIAS_ORGANISATIONS.get(nom_nettoye)
    if nom in ALIAS_ORGANISATIONS or nom_nettoye in ALIAS_ORGANISATIONS:
        if alias is None:
            # Clé présente avec valeur None → repli forcé (pas de Wikipédia)
            return _repli_factuel(source_label, page_url)
        if isinstance(alias, dict):
            result = {}
            if alias.get("caption"):
                result["organization_caption"] = alias["caption"]
            if alias.get("summary"):
                result["organization_summary"] = alias["summary"]
            return result if result else None
        # alias est un str = titre Wikipédia exact
        wikipedia_titre = alias
    else:
        # Non présent dans la carte → chercher sur Wikipédia
        wikipedia_titre = nom_nettoye or nom

    # --- 2. Wikipédia ---
    wiki = resumer_wikipedia(wikipedia_titre)
    if wiki:
        caption = wiki.get("caption", "")
        extract = wiki.get("summary", "")
        summary = extract + " (source : Wikipédia)" if extract else ""
        return _construire_resultat(caption, summary)

    # --- 3. Repli factuel ---
    return _repli_factuel(source_label, page_url)


def _construire_resultat(caption: str, summary: str) -> dict | None:
    """Construit le dict résultat si au moins un champ est non vide."""
    result = {}
    if caption:
        result["organization_caption"] = caption
    if summary:
        result["organization_summary"] = summary
    return result if result else None


def _repli_factuel(source_label: str | None, page_url: str | None) -> dict:
    """Phrase factuelle sans compteur de JDD."""
    source = source_label or "les portails open data"
    caption = "Producteur de données ouvertes"
    summary = f"Producteur de jeux de données moissonnés sur {source}."
    if page_url:
        summary += f" Page producteur : {page_url}"
    return {
        "organization_caption": caption,
        "organization_summary": summary,
    }

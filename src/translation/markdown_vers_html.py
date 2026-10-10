"""
Convertit en HTML minimal le sous-ensemble de Markdown réellement utilisé par les
descriptions sources (data.gouv.fr en particulier écrit ses descriptions en Markdown :
gras, listes à puces, liens, paragraphes séparés par une ligne vide).

Alimente `summary[].html` des métadonnées RUDI (voir rudi_builder.py::construire_rudi_metadata).
Le portail (depuis sa v3.3.2) injecte ce champ en HTML (innerHTML) à la place de
`summary[].text` quand il est présent. Sans lui, le Markdown source s'affichait tel
quel sur la fiche catalogue : texte littéral « **gras** »/« - puce », et retours à la
ligne perdus (le rendu HTML ignore les \n hors balises).

Volontairement pas un parseur Markdown complet : aucune dépendance ajoutée (voir
CLAUDE.md « Dépendances » — stdlib + requests pour tout le pipeline de moisson), et
seuls les éléments réellement observés dans les sources moissonnées sont gérés — gras,
italique, liens, listes (à puces ou numérotées), paragraphes, retours à la ligne simples
au sein d'un paragraphe, et pseudo-titres `# `/`## `. Tout le texte est échappé avant
insertion : aucune balise ni attribut de la source n'est jamais reproduit tel quel dans
la sortie (liens restreints aux schémas http/https, pour éviter une injection via un
schéma `javascript:`/`data:`).
"""
import re

_RE_GRAS = re.compile(r"\*\*(.+?)\*\*")
_RE_ITALIQUE_ETOILE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_RE_ITALIQUE_TIRET = re.compile(r"(?<!_)_([^_\n]+?)_(?!_)")
_RE_LIEN = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_RE_PUCE = re.compile(r"^[-*+]\s+(.*)")
_RE_NUMEROTE = re.compile(r"^\d+[.)]\s+(.*)")
_RE_TITRE = re.compile(r"^(#{1,6})\s+(.*)")


def _echapper(texte: str) -> str:
    """Échappe les caractères qui ont un sens en HTML — appliqué avant toute mise en
    forme, pour que le Markdown source ne puisse jamais injecter de balise/attribut."""
    return texte.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _lien_securise(url: str) -> str | None:
    """N'autorise que http(s) — évite l'injection via un schéma javascript:/data: etc.
    None si le schéma n'est pas autorisé (le lien est alors dégradé en texte simple)."""
    if re.match(r"^https?://", url, re.IGNORECASE):
        return url.replace('"', "&quot;")
    return None


def _inline(texte_echappe: str) -> str:
    """Applique liens/gras/italique sur un texte déjà échappé (HTML-safe)."""

    def _remplacer_lien(m: re.Match) -> str:
        url = _lien_securise(m.group(2))
        if url is None:
            return m.group(1)
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{m.group(1)}</a>'

    texte = _RE_LIEN.sub(_remplacer_lien, texte_echappe)
    texte = _RE_GRAS.sub(r"<strong>\1</strong>", texte)
    texte = _RE_ITALIQUE_ETOILE.sub(r"<em>\1</em>", texte)
    texte = _RE_ITALIQUE_TIRET.sub(r"<em>\1</em>", texte)
    return texte


def _ligne_liste(ligne: str) -> tuple[str, str] | None:
    """Retourne (type_liste, contenu) si `ligne` est un item de liste ("ul"/"ol"), None sinon."""
    m = _RE_PUCE.match(ligne)
    if m:
        return "ul", m.group(1)
    m = _RE_NUMEROTE.match(ligne)
    if m:
        return "ol", m.group(1)
    return None


def markdown_vers_html(texte: str | None) -> str:
    """Convertit le sous-ensemble de Markdown décrit en tête de module en HTML.

    Chaîne vide si `texte` est vide/absent. Chaque bloc source (séparé par une ligne
    vide) devient une liste (<ul>/<ol>) si toutes ses lignes sont des items ; si seule
    une phrase d'intro précède des items collés sans ligne vide (style data.gouv.fr
    courant), elle devient un paragraphe d'intro suivi de la liste. Un bloc qui tient
    en une ligne commençant par `#` devient un titre (<p><strong>). Sinon, paragraphe
    (<p>, les retours à la ligne simples internes devenant <br>).
    """
    if not (texte or "").strip():
        return ""

    blocs_html = []
    for bloc in re.split(r"\n\s*\n", texte.strip()):
        lignes = [l.strip() for l in bloc.splitlines() if l.strip()]
        if not lignes:
            continue

        items = [_ligne_liste(l) for l in lignes]
        if all(items):
            type_liste = items[0][0]
            puces = "".join(f"<li>{_inline(_echapper(contenu))}</li>" for _, contenu in items)
            blocs_html.append(f"<{type_liste}>{puces}</{type_liste}>")
            continue

        # Phrase d'intro collée à une liste sans ligne vide entre les deux (style
        # data.gouv.fr courant, ex. "... sont de trois types :\n- item\n- item") :
        # découpée en paragraphe d'intro + liste, plutôt que de tout retomber en
        # paragraphe <br> faute de bloc 100% liste.
        premier_item = next((i for i, it in enumerate(items) if it is not None), None)
        if premier_item and all(items[premier_item:]) and len({it[0] for it in items[premier_item:]}) == 1:
            intro = "<br>".join(_inline(_echapper(l)) for l in lignes[:premier_item])
            type_liste = items[premier_item][0]
            puces = "".join(
                f"<li>{_inline(_echapper(contenu))}</li>" for _, contenu in items[premier_item:]
            )
            blocs_html.append(f"<p>{intro}</p>")
            blocs_html.append(f"<{type_liste}>{puces}</{type_liste}>")
            continue

        m_titre = _RE_TITRE.match(lignes[0]) if len(lignes) == 1 else None
        if m_titre:
            blocs_html.append(f"<p><strong>{_inline(_echapper(m_titre.group(2)))}</strong></p>")
            continue

        paragraphe = "<br>".join(_inline(_echapper(l)) for l in lignes)
        blocs_html.append(f"<p>{paragraphe}</p>")

    return "\n".join(blocs_html)

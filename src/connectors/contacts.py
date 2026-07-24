"""
Extraction et résolution de contacts pour les métadonnées RUDI.

Fournit des contacts exploitables depuis les sources de données
(data.gouv.fr contact_points, GetCapabilities WFS/WMS) avec fallback
sur l'organisation productrice quand la source ne fournit pas de contact.
"""
import re
import unicodedata

# Domaine fallback quand aucune source ne fournit d'email valide.
# RFC 2606 : domaine réservé, ne recevra jamais de courrier.
_DOMAINE_DEFAUT = "example.org"
_EMAIL_DEFAUT = "contact@" + _DOMAINE_DEFAUT


def _slug_email(nom: str) -> str:
    """Slug ASCII minuscule utilisable comme local-part d'email (sans accents/espaces)."""
    n = unicodedata.normalize("NFKD", nom or "").encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z0-9]+", "-", n).strip("-").lower()
    return n[:64] or "contact"


def email_par_defaut(nom: str) -> str:
    """Email de repli **unique par nom** sous le domaine réservé example.org (RFC 2606).

    À utiliser partout où un email fallback est nécessaire, pour ne pas faire collapser
    les contacts dédupliqués par email côté nœud (voir contacter_pardefaut)."""
    return f"{_slug_email(nom)}@{_DOMAINE_DEFAUT}"


def extraire_contacts_datagouv(metadata_source: dict) -> list[dict]:
    """Extrait les contacts depuis le champ contact_points d'un dataset data.gouv.fr.

    Chaque entry contient : name, email, role, contact_form, organization.
    Retourne une liste de dicts {"contact_name": ..., "email": ..., "role": ...}
    filtrée pour ne garder que les entrées avec au moins un nom.
    Les contacts sans email valide sont exclus (le nœud RUDI en exige un).
    """
    contacts = []
    for cp in metadata_source.get("contact_points", []):
        name = (cp.get("name") or "").strip()
        email = (cp.get("email") or "").strip()
        role = (cp.get("role") or "").strip()
        if not name and not email:
            continue
        if not _email_valide(email):
            continue
        if not name:
            # Utiliser le nom de l'org imbriquée si pas de name direct
            org = cp.get("organization") or {}
            name = org.get("name", "").strip()
        if name:
            contacts.append({
                "contact_name": name,
                "email": email,
                "role": role or "contact",
            })
    return contacts


def contacter_pardefaut(nom_org: str, email_defaut: str | None = None) -> dict:
    """Construit un contact fallback à partir du nom de l'organisation productrice.

    L'email de repli est **unique par producteur** (`<slug-org>@example.org`), et non
    un `contact@example.org` partagé : le nœud RUDI déduplique les contacts par email,
    donc un email partagé fait collapser TOUS les fallbacks sur un seul contact — celui
    du premier producteur créé — et affiche son nom (ex. « SDIS de l'Essonne ») sur des
    centaines de fiches sans rapport. Un local-part dérivé du nom d'org donne un contact
    distinct et correctement nommé par producteur, sans jamais envoyer de courrier réel.
    """
    email = email_defaut or email_par_defaut(nom_org)
    return {
        "contact_name": nom_org or "Contact",
        "email": email,
    }


def resoudre_contacts(contacts_source: list[dict], nom_org: str,
                      email_defaut: str | None = None) -> list[dict]:
    """Retourne au moins un contact valide pour un dataset.

    Si contacts_source contient des contacts exploitables, retourne le premier.
    Sinon, construit un contact fallback avec le nom de l'organisation (email unique
    par producteur — voir contacter_pardefaut). `email_defaut` reste surchargeable.
    Le nœud RUDI exige un email valide — aucun contact sans email n'est retourné.
    """
    if contacts_source:
        premier = contacts_source[0]
        if _email_valide(premier.get("email", "")):
            return [premier]
    return [contacter_pardefaut(nom_org, email_defaut)]


def _email_valide(email: str) -> bool:
    """Vérifie si une chaîne est un email syntaxiquement valide (RFC simple)."""
    if not email or not isinstance(email, str):
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip()))

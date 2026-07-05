"""
Connecteur INSEE direct — téléchargement depuis https://www.insee.fr/fr/statistiques/.

Stratégie failsafe :
  1. HEAD sur l'url_direct → si 200, téléchargement direct
  2. Si 404 / timeout, scraping de l'url_page pour découvrir la nouvelle URL
     (les pages de statistiques INSEE ont des IDs stables, seuls les noms de
     fichiers changent d'un millésime à l'autre)
  3. Extraction du membre CSV via membre_pattern (regex sur le nom seul)
     Si aucun match, liste les membres disponibles pour diagnostic

La configuration des publications est dans conf/datasets.py (DATASETS_INSEE).
"""
import html.parser
import re
import zipfile

from connectors.http import session

BASE_INSEE = "https://www.insee.fr"
_HEADERS = {"User-Agent": "moissonneuse-batteuse/1.0 (projet open-data Rennes Métropole)"}
_TIMEOUT_HEAD = 15
_TIMEOUT_GET  = 20
_RE_ZIP = re.compile(r'/fr/statistiques/fichier/\d+/[^"\'<>\s]+\.zip', re.IGNORECASE)


class _ExtractZipLinks(html.parser.HTMLParser):
    """Parse les balises <a href="..."> d'une page INSEE pour trouver les liens ZIP."""
    def __init__(self):
        super().__init__()
        self.liens: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and _RE_ZIP.match(value):
                self.liens.append(value)


# ---------------------------------------------------------------------------
# Résolution d'URL (avec fallback scraping)
# ---------------------------------------------------------------------------


def _scraper_url_zip(url_page: str) -> str | None:
    """Scrape la page INSEE et retourne l'URL du ZIP CSV le plus approprié.
    Préfère les ZIPs '_csv.zip' aux autres, exclut les fichiers historiques."""
    try:
        r = session.get(url_page, headers=_HEADERS, timeout=_TIMEOUT_GET)
        r.raise_for_status()
    except Exception as e:
        print(f"  [insee] Scraping de {url_page} impossible : {e}")
        return None

    finder = _ExtractZipLinks()
    finder.feed(r.text)
    liens = finder.liens
    if not liens:
        return None

    # Supprimer les doublons en gardant l'ordre
    liens = list(dict.fromkeys(liens))
    def _priorite(lien: str) -> int:
        l = lien.lower()
        if "_histo" in l or "histo." in l:
            return -1   # exclure les archives historiques
        if "_csv.zip" in l:
            return 2    # préférence maximale : CSV
        return 1        # n'importe quel autre ZIP

    liens_valides = [l for l in liens if _priorite(l) >= 0]
    if not liens_valides:
        return None

    best = max(liens_valides, key=_priorite)
    return BASE_INSEE + best


def resoudre_url(pub: dict) -> str | None:
    """Retourne l'URL de téléchargement valide (directe ou découverte par scraping).

    Retourne None si aucune URL n'est accessible.
    """
    url_direct = pub.get("url_direct")

    if url_direct:
        try:
            r = session.head(url_direct, headers=_HEADERS, timeout=_TIMEOUT_HEAD,
                              allow_redirects=True)
            if r.status_code == 200:
                return url_direct
            print(f"  [{pub['id']}] URL directe : HTTP {r.status_code} — essai scraping...")
        except Exception as e:
            print(f"  [{pub['id']}] URL directe inaccessible ({e}) — essai scraping...")

    url_page = pub.get("url_page")
    if url_page:
        url = _scraper_url_zip(url_page)
        if url:
            print(f"  [{pub['id']}] URL trouvée par scraping : {url}")
            return url

    print(f"  [{pub['id']}] Impossible de trouver une URL valide.")
    return None


# ---------------------------------------------------------------------------
# Extraction ZIP
# ---------------------------------------------------------------------------

def extraire_membres(pub: dict, chemin_zip: str) -> list[tuple[str, bytes]]:
    """Extrait les membres CSV correspondant à membre_pattern du ZIP (lu depuis le disque).

    Retourne [(nom_membre, contenu_csv)].
    En cas d'absence de correspondance, affiche un diagnostic et retourne [].
    """
    pattern_str = pub.get("membre_pattern", r".*\.csv$")
    pattern = re.compile(pattern_str, re.IGNORECASE)

    try:
        with zipfile.ZipFile(chemin_zip) as zf:
            tous = zf.namelist()
            correspondances = [
                n for n in tous
                # Comparer uniquement le nom de fichier (sans chemin de dossier)
                if pattern.match(n.rsplit("/", 1)[-1])
                and not n.startswith("__MACOSX")
                and not n.endswith("/")    # ignorer les entrées de répertoire
            ]

            if correspondances:
                return [(nom, zf.read(nom)) for nom in correspondances]

            # Aucun match : diagnostic
            tous_csv = [n for n in tous if n.lower().endswith(".csv") and not n.endswith("/")]
            print(f"  [{pub['id']}] Aucun membre ne correspond au pattern '{pattern_str}'")
            if tous_csv:
                print(f"  [{pub['id']}] Membres CSV disponibles : {tous_csv}")
            else:
                print(f"  [{pub['id']}] Membres ZIP disponibles : {tous[:15]}")
            return []

    except zipfile.BadZipFile as e:
        print(f"  [{pub['id']}] Archive ZIP invalide : {e}")
        return []


# ---------------------------------------------------------------------------
# Extraction du dictionnaire des variables
# ---------------------------------------------------------------------------

def extraire_dictionnaire(pub: dict, chemin_zip: str) -> list[tuple[str, bytes]]:
    """Extrait le(s) fichier(s) dictionnaire des variables si dict_pattern est défini dans pub.

    Retourne [] si dict_pattern absent (ex: BPE dont le ZIP ne contient pas de dict).
    """
    if not pub.get("dict_pattern"):
        return []
    pub_dict = {**pub, "membre_pattern": pub["dict_pattern"]}
    return extraire_membres(pub_dict, chemin_zip)

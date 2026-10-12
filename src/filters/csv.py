"""
Utilitaires CSV partagés entre les différents scripts de moisson (harvest_batch,
harvest_insee, etc.).
"""

import csv
import os
import re

from filters.geographic import normaliser


def slugifier(titre: str) -> str:
    """Convertit un titre en slug de fichier (max 50 chars)."""
    titre = re.sub(r"\.[a-zA-Z0-9]{2,5}$", "", titre.strip())
    s = normaliser(titre)
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return s[:50] or "fichier"


def tronquer_slug(slug: str, max_len: int) -> str:
    """Tronque un slug à max_len caractères sans jamais couper un mot en deux.

    Ne recule jusqu'au dernier '-' que si la coupure dure tombe vraiment en plein milieu
    d'un mot (le caractère suivant n'est pas une frontière) — sinon un slug qui s'arrête
    déjà pile sur un '-' garde son dernier mot complet au lieu de le perdre pour rien.
    Ex: "compilation-de-3-963-donnees-insee-portant-sur" tronqué à 45 donne
    "compilation-de-3-963-donnees-insee-portant" (coupe "sur" en "su" sinon)."""
    if len(slug) <= max_len:
        return slug
    tronque = slug[:max_len]
    if slug[max_len] != "-" and "-" in tronque:
        tronque = tronque.rsplit("-", 1)[0]
    return tronque.strip("-") or slug[:max_len]


def sauvegarder_csv(lignes: list[dict], chemin: str) -> None:
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=lignes[0].keys())
        writer.writeheader()
        writer.writerows(lignes)

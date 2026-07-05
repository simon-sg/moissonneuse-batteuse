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


def sauvegarder_csv(lignes: list[dict], chemin: str) -> None:
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=lignes[0].keys())
        writer.writeheader()
        writer.writerows(lignes)

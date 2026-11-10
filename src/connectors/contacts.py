"""Shim de réexportation — contenu migré vers ``rudi_harvest_toolkit.contacts``
(v3 de rudi-commune-starterkit, Axe 2, 2026-11-10). Générique, réutilisable par
n'importe quel harvester du toolkit partagé ; ce fichier ne change aucun des 6
appelants existants de ce repo."""

from rudi_harvest_toolkit.contacts import (
    _desobfusquer_email,
    _email_valide,
    contacter_pardefaut,
    extraire_contacts_datagouv,
    resoudre_contacts,
)
from rudi_harvest_toolkit.rudi_client import email_par_defaut

__all__ = [
    "contacter_pardefaut",
    "email_par_defaut",
    "extraire_contacts_datagouv",
    "resoudre_contacts",
]

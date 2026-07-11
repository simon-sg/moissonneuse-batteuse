"""
Tee — écrit simultanément sur deux flux de sortie.

Utilisé par dashboard.py (buffer web + stdout) et harvest_auto.py (fichier log + stdout).
"""


class Tee:
    """Écrit à la fois sur deux cibles (fichier/buffer + sortie standard d'origine)."""

    def __init__(self, cible, original):
        self._cible = cible
        self._original = original

    def write(self, s):
        self._cible.write(s)
        self._original.write(s)
        return len(s)

    def flush(self):
        self._cible.flush()
        self._original.flush()

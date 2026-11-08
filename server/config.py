"""Configuration du serveur, lue dans l'environnement (fichier .env chargé par systemd)."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

# Jeton protégeant la télécommande et l'administration du quiz.
# Généré au démarrage s'il est absent : il est alors affiché dans le journal.
JETON: str = os.environ.get("AMPHI_JETON") or secrets.token_urlsafe(16)
JETON_GENERE: bool = "AMPHI_JETON" not in os.environ

# URL publique (celle du QR code), sans barre oblique finale.
URL_PUBLIQUE: str = os.environ.get("AMPHI_URL_PUBLIQUE", "http://localhost:8000").rstrip("/")

# Base SQLite des réponses au quiz.
CHEMIN_BDD: Path = Path(os.environ.get("AMPHI_BDD", RACINE / "data" / "quiz.sqlite"))

# Dossier du runtime du deck (fichiers statiques communs servis sous /deck/ : index.html, sync.js, éditeur…).
DOSSIER_DECK: Path = Path(os.environ.get("AMPHI_DECK", RACINE / "deck"))

# Bibliothèque de présentations : un sous-dossier par présentation (voir docs/presentations.md).
DOSSIER_PRESENTATIONS: Path = Path(os.environ.get("AMPHI_PRESENTATIONS", RACINE / "presentations"))

# Présentation active par défaut (slug) quand data/active.json n'existe pas ; sinon le premier slug trié.
PRESENTATION_DEFAUT: str | None = os.environ.get("AMPHI_PRESENTATION") or None

# État d'exécution : la présentation active, à côté de la base SQLite (pas dans git).
FICHIER_ACTIVE: Path = CHEMIN_BDD.parent / "active.json"

# Fichiers statiques du serveur (pages participant / résultats).
DOSSIER_STATIQUE: Path = RACINE / "server" / "static"

"""Questions des participants et séquence questions-réponses (voir docs/questions.md).

Table SQLite `questions_participants` dans la base du quiz (`CHEMIN_BDD`) : une ligne par question posée
depuis la page participant, rattachée à la diapositive affichée au moment de l'envoi (index `h`/`v`,
titre lu par `journal.titres_presentation`). Anonymat identique au quiz : identifiant de cookie et pseudo,
jamais d'identité. `purger()` efface tout (appelé aussi par `quiz/purger`).

La séquence questions-réponses (`SequenceQR`) est un état en mémoire : active ou non, question en cours,
position du deck à laquelle revenir une fois terminée.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

ETATS = ("en_attente", "en_cours", "repondue", "ecartee")
LONGUEUR_MAX = 280
MAX_PAR_PARTICIPANT = 20


class Questions:
    """Accès à la table `questions_participants`."""

    def __init__(self, chemin_bdd: Path) -> None:
        self.chemin_bdd = Path(chemin_bdd)
        self.chemin_bdd.parent.mkdir(parents=True, exist_ok=True)
        self._verrou = threading.Lock()
        with self._connexion() as cnx:
            cnx.executescript(
                """CREATE TABLE IF NOT EXISTS questions_participants (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       presentation TEXT NOT NULL,
                       participant TEXT NOT NULL,
                       pseudo TEXT NOT NULL DEFAULT '',
                       texte TEXT NOT NULL,
                       h INTEGER NOT NULL DEFAULT 0,
                       v INTEGER NOT NULL DEFAULT 0,
                       titre_diapo TEXT NOT NULL DEFAULT '',
                       generale INTEGER NOT NULL DEFAULT 0,
                       horodatage REAL NOT NULL,
                       etat TEXT NOT NULL DEFAULT 'en_attente',
                       ordre INTEGER NOT NULL DEFAULT 0);
                   CREATE INDEX IF NOT EXISTS questions_participants_ordre ON questions_participants (etat, ordre, id);"""
            )

    def _connexion(self) -> sqlite3.Connection:
        cnx = sqlite3.connect(self.chemin_bdd, check_same_thread=False)
        cnx.execute("PRAGMA journal_mode=WAL")
        return cnx

    # --- Écriture -------------------------------------------------------------------------

    def ajouter(self, presentation: str | None, participant: str, pseudo: str, texte: str, h: int, v: int,
                titre_diapo: str, generale: bool = False) -> dict:
        """Enregistre une question ; ValueError si le texte est vide ou trop long, PermissionError au-delà de la limite par participant."""
        texte = re.sub(r"[ \t]+", " ", (texte or "")).strip()
        texte = re.sub(r"\n{3,}", "\n\n", texte)
        if not texte:
            raise ValueError("question vide")
        if len(texte) > LONGUEUR_MAX:
            raise ValueError(f"question trop longue ({LONGUEUR_MAX} caractères maximum)")
        with self._verrou, self._connexion() as cnx:
            n = cnx.execute("SELECT COUNT(*) FROM questions_participants WHERE participant = ?", (participant,)).fetchone()[0]
            if n >= MAX_PAR_PARTICIPANT:
                raise PermissionError(f"limite atteinte : {MAX_PAR_PARTICIPANT} questions par participant")
            curseur = cnx.execute(
                """INSERT INTO questions_participants (presentation, participant, pseudo, texte, h, v, titre_diapo, generale, horodatage, etat, ordre)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'en_attente', 0)""",
                (presentation or "", participant, pseudo or "", texte, int(h), int(v), titre_diapo or "", 1 if generale else 0, time.time()),
            )
            id = int(curseur.lastrowid)
            cnx.execute("UPDATE questions_participants SET ordre = ? WHERE id = ?", (id, id))  # tri manuel : par défaut l'ordre d'arrivée
        return self.lire(id)  # type: ignore[return-value]

    def changer_etat(self, id: int, etat: str) -> dict:
        if etat not in ETATS:
            raise ValueError(f"état inconnu : {etat!r}")
        with self._verrou, self._connexion() as cnx:
            if cnx.execute("UPDATE questions_participants SET etat = ? WHERE id = ?", (etat, id)).rowcount == 0:
                raise KeyError(id)
        return self.lire(id)  # type: ignore[return-value]

    def reordonner(self, ids: list[int]) -> None:
        """Les questions listées prennent l'ordre 1..n dans l'ordre donné ; les autres ne bougent pas."""
        with self._verrou, self._connexion() as cnx:
            for position, id in enumerate(ids, start=1):
                cnx.execute("UPDATE questions_participants SET ordre = ? WHERE id = ?", (position, int(id)))

    def supprimer(self, id: int) -> bool:
        with self._verrou, self._connexion() as cnx:
            return cnx.execute("DELETE FROM questions_participants WHERE id = ?", (id,)).rowcount > 0

    def purger(self) -> None:
        with self._verrou, self._connexion() as cnx:
            cnx.execute("DELETE FROM questions_participants")

    # --- Lecture ----------------------------------------------------------------------------

    @staticmethod
    def _dict(ligne: tuple, avec_pseudo: bool = True) -> dict:
        id, participant, pseudo, texte, h, v, titre, generale, horodatage, etat, ordre = ligne
        return {
            "id": id, "texte": texte, "pseudo": pseudo if avec_pseudo else None, "h": h, "v": v, "numero": h + 1, "titre": titre,
            "generale": bool(generale), "etat": etat, "horodatage": horodatage, "ordre": ordre, **({"participant": participant} if avec_pseudo else {}),
        }

    def lire(self, id: int) -> dict | None:
        with self._connexion() as cnx:
            ligne = cnx.execute(
                "SELECT id, participant, pseudo, texte, h, v, titre_diapo, generale, horodatage, etat, ordre FROM questions_participants WHERE id = ?", (id,)
            ).fetchone()
        return self._dict(ligne) if ligne else None

    def lister(self, etat: str | None = None, avec_pseudo: bool = True) -> list[dict]:
        """Toutes les questions triées par `ordre` puis identifiant ; `etat` filtre."""
        requete = "SELECT id, participant, pseudo, texte, h, v, titre_diapo, generale, horodatage, etat, ordre FROM questions_participants"
        params: list = []
        if etat:
            requete += " WHERE etat = ?"
            params.append(etat)
        requete += " ORDER BY ordre, id"
        with self._connexion() as cnx:
            lignes = cnx.execute(requete, params).fetchall()
        return [self._dict(l, avec_pseudo) for l in lignes]

    def en_attente(self) -> int:
        with self._connexion() as cnx:
            return cnx.execute("SELECT COUNT(*) FROM questions_participants WHERE etat = 'en_attente'").fetchone()[0]

    def premiere_en_attente(self) -> dict | None:
        suivantes = self.lister("en_attente")
        return suivantes[0] if suivantes else None

    def en_cours(self) -> dict | None:
        courantes = self.lister("en_cours")
        return courantes[0] if courantes else None

    def ids_de(self, participant: str) -> list[int]:
        with self._connexion() as cnx:
            return [r[0] for r in cnx.execute("SELECT id FROM questions_participants WHERE participant = ? ORDER BY id", (participant,))]

    def export_csv(self) -> str:
        """CSV sans pseudo : numéro de diapositive, titre, question, générale, état, horodatage ISO."""
        lignes = ["numero;titre_diapo;question;generale;etat;horodatage"]
        for q in self.lister():
            quand = datetime.fromtimestamp(q["horodatage"]).replace(microsecond=0).isoformat()
            lignes.append(f"{q['numero']};{_csv(q['titre'])};{_csv(q['texte'])};{'oui' if q['generale'] else 'non'};{q['etat']};{quand}")
        return "\n".join(lignes) + "\n"


def _csv(texte: str) -> str:
    texte = texte.replace("\r", "").replace("\n", " ")
    return '"' + texte.replace('"', '""') + '"' if any(c in texte for c in ';"') else texte


# -------------------------------------------------------------------------------------
# Séquence questions-réponses
# -------------------------------------------------------------------------------------


class SequenceQR:
    """État en mémoire : `{actif, question_id, retour}`. `retour` est la position du deck au moment de l'ouverture."""

    def __init__(self) -> None:
        self.actif = False
        self.question_id: int | None = None
        self.retour: dict = {"h": 0, "v": 0, "f": -1}

    def ouvrir(self, indices: dict) -> None:
        if not self.actif:
            self.retour = {"h": int(indices.get("h", 0)), "v": int(indices.get("v", 0)), "f": int(indices.get("f", -1))}
        self.actif = True
        self.question_id = None

    def fermer(self) -> dict:
        self.actif = False
        self.question_id = None
        return dict(self.retour)

    def etat(self) -> dict:
        return {"actif": self.actif, "question_id": self.question_id, "retour": dict(self.retour)}


def question_publique(q: dict | None) -> dict | None:
    """Ce qui est affiché à l'écran et aux participants : jamais de pseudo."""
    if q is None:
        return None
    return {"id": q["id"], "texte": q["texte"], "numero": q["numero"], "titre": q["titre"], "generale": q["generale"], "pseudo": None}

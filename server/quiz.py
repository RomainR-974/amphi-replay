"""Quiz participants : questions déclarées dans un fichier, réponses en SQLite.

Un seul type de question en v1 : QCM à choix unique, 2 à 5 options.
Une seule question ouverte à la fois. Anonymat strict : l'identifiant du
participant est un jeton aléatoire posé en cookie, purgé par `purger()`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    options: list[str]
    diapositive: dict | None = None  # {"h": 3, "v": 0} : diapositive associée dans le deck

    def valider(self) -> None:
        if not self.id or not self.question:
            raise ValueError(f"question {self.id!r} : id et question obligatoires")
        if not 2 <= len(self.options) <= 5:
            raise ValueError(f"question {self.id!r} : 2 à 5 options attendues, {len(self.options)} reçues")

    def public(self) -> dict:
        return {"id": self.id, "question": self.question, "options": self.options, "diapositive": self.diapositive}


def charger_questions(chemin: Path) -> dict[str, Question]:
    """Lit `quiz.json` : une liste d'objets {id, question, options, diapositive?}."""
    if not chemin.exists():
        return {}
    brut = json.loads(chemin.read_text(encoding="utf-8"))
    questions: dict[str, Question] = {}
    for q in brut:
        question = Question(
            id=str(q["id"]),
            question=q["question"],
            options=list(q["options"]),
            diapositive=q.get("diapositive"),
        )
        question.valider()
        if question.id in questions:
            raise ValueError(f"identifiant de question en double : {question.id}")
        questions[question.id] = question
    return questions


@dataclass
class Quiz:
    questions: dict[str, Question]
    chemin_bdd: Path
    ouverte: str | None = None
    _verrou: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.chemin_bdd.parent.mkdir(parents=True, exist_ok=True)
        with self._connexion() as cnx:
            cnx.execute(
                """CREATE TABLE IF NOT EXISTS reponses (
                       question_id TEXT NOT NULL,
                       participant TEXT NOT NULL,
                       option_index INTEGER NOT NULL,
                       horodatage REAL NOT NULL,
                       PRIMARY KEY (question_id, participant)
                   )"""
            )

    def _connexion(self) -> sqlite3.Connection:
        cnx = sqlite3.connect(self.chemin_bdd, check_same_thread=False)
        cnx.execute("PRAGMA journal_mode=WAL")
        return cnx

    # --- Administration (orateur) -------------------------------------------------

    def ouvrir(self, question_id: str) -> None:
        if question_id not in self.questions:
            raise KeyError(question_id)
        with self._verrou:
            self.ouverte = question_id

    def fermer(self) -> None:
        with self._verrou:
            self.ouverte = None

    def reinitialiser(self, question_id: str) -> None:
        if question_id not in self.questions:
            raise KeyError(question_id)
        with self._verrou, self._connexion() as cnx:
            cnx.execute("DELETE FROM reponses WHERE question_id = ?", (question_id,))

    def purger(self) -> None:
        """Efface toutes les réponses (fin de session, après export)."""
        with self._verrou, self._connexion() as cnx:
            cnx.execute("DELETE FROM reponses")

    # --- Participants ---------------------------------------------------------------

    def voter(self, question_id: str, participant: str, option_index: int) -> None:
        """Enregistre ou remplace la réponse d'un participant à la question ouverte."""
        if question_id != self.ouverte:
            raise PermissionError("question fermée")
        question = self.questions[question_id]
        if not 0 <= option_index < len(question.options):
            raise ValueError("option inconnue")
        with self._verrou, self._connexion() as cnx:
            cnx.execute(
                """INSERT INTO reponses (question_id, participant, option_index, horodatage)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(question_id, participant)
                   DO UPDATE SET option_index = excluded.option_index, horodatage = excluded.horodatage""",
                (question_id, participant, option_index, time.time()),
            )

    def reponse_de(self, question_id: str, participant: str) -> int | None:
        with self._connexion() as cnx:
            ligne = cnx.execute(
                "SELECT option_index FROM reponses WHERE question_id = ? AND participant = ?",
                (question_id, participant),
            ).fetchone()
        return ligne[0] if ligne else None

    # --- Résultats ------------------------------------------------------------------

    def comptes(self, question_id: str) -> list[int]:
        question = self.questions[question_id]
        comptes = [0] * len(question.options)
        with self._connexion() as cnx:
            for option_index, n in cnx.execute(
                "SELECT option_index, COUNT(*) FROM reponses WHERE question_id = ? GROUP BY option_index",
                (question_id,),
            ):
                if 0 <= option_index < len(comptes):
                    comptes[option_index] = n
        return comptes

    def etat(self, question_id: str | None = None) -> dict:
        """État publié aux vues participant, résultats et télécommande.

        Sans `question_id`, décrit la question ouverte (ou l'absence de question).
        Avec `question_id`, décrit cette question même fermée (résultats figés).
        """
        cible = question_id or self.ouverte
        if cible is None or cible not in self.questions:
            return {"question": None, "ouverte": False, "comptes": [], "total": 0}
        comptes = self.comptes(cible)
        return {
            "question": self.questions[cible].public(),
            "ouverte": cible == self.ouverte,
            "comptes": comptes,
            "total": sum(comptes),
        }

    def export_agrege(self) -> str:
        """CSV agrégé : une ligne par option, aucune donnée individuelle."""
        lignes = ["question_id;question;option;reponses"]
        for q in self.questions.values():
            for option, n in zip(q.options, self.comptes(q.id)):
                lignes.append(f"{q.id};{_csv(q.question)};{_csv(option)};{n}")
        return "\n".join(lignes) + "\n"


def _csv(texte: str) -> str:
    return '"' + texte.replace('"', '""') + '"' if any(c in texte for c in ';"\n') else texte

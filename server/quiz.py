"""Quiz participants : 12 types de questions, points façon Kahoot, classement par pseudo.

Types : qcm, multi, vrai_faux, emoji, rapidite, buzzer, nuage, texte_libre, trou,
echelle, classement, nombre (voir docs/format-deck.md, section 3).

Anonymat : l'identifiant du participant est un jeton aléatoire en cookie ; le pseudo
est choisi librement (ou généré) et n'est jamais lié à une identité. `purger()` efface
réponses, pseudos et scores à la fin de la session.
"""

from __future__ import annotations

import json
import math
import random
import re
import sqlite3
import statistics
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TYPES = ("qcm", "multi", "vrai_faux", "emoji", "rapidite", "buzzer", "nuage", "texte_libre", "trou", "echelle", "classement", "nombre")
TYPES_A_OPTIONS = ("qcm", "multi", "emoji", "rapidite", "classement")
POINTS_MAX = 1000
IDENTIFIANT = re.compile(r"^[a-z0-9_-]{1,40}$")

ANIMAUX = ("Panda", "Lynx", "Koala", "Manchot", "Tortue", "Renard", "Dauphin", "Hibou", "Loutre", "Caméléon", "Zèbre", "Colibri", "Castor", "Phoque", "Gecko")


@dataclass(frozen=True)
class Question:
    id: str
    type: str
    question: str
    options: list[str] = field(default_factory=list)
    bonne: Any = None            # index, booléen, nombre, ou ordre d'index selon le type
    bonnes: list = field(default_factory=list)  # multi : index ; trou : réponses acceptées
    chrono: int = 0              # secondes, 0 = sans limite
    points: bool | None = None   # None : vrai s'il existe une bonne réponse
    texte: str = ""              # trou : phrase avec ___
    max_mots: int = 3
    max_longueur: int = 25
    min: int = 1
    max: int = 5
    libelle_min: str = ""
    libelle_max: str = ""
    unite: str = ""
    diapositive: dict | None = None

    # --- Validation --------------------------------------------------------------

    def valider(self) -> None:
        if not IDENTIFIANT.match(self.id):
            raise ValueError(f"identifiant invalide : {self.id!r} (lettres minuscules, chiffres, - et _)")
        if self.type not in TYPES:
            raise ValueError(f"question {self.id} : type inconnu {self.type!r}")
        if not self.question and self.type != "trou":
            raise ValueError(f"question {self.id} : énoncé obligatoire")
        if self.type in TYPES_A_OPTIONS:
            maxi = 8 if self.type == "emoji" else 6
            if not 2 <= len(self.options) <= maxi:
                raise ValueError(f"question {self.id} : 2 à {maxi} options attendues")
        if self.type in ("qcm", "emoji", "rapidite") and self.bonne is not None and not (isinstance(self.bonne, int) and 0 <= self.bonne < len(self.options)):
            raise ValueError(f"question {self.id} : `bonne` doit être un index d'option")
        if self.type == "rapidite" and (self.bonne is None or self.chrono <= 0):
            raise ValueError(f"question {self.id} : une question de rapidité a une bonne réponse et un chrono")
        if self.type == "multi" and any(not isinstance(i, int) or not 0 <= i < len(self.options) for i in self.bonnes):
            raise ValueError(f"question {self.id} : `bonnes` doit lister des index d'options")
        if self.type == "vrai_faux" and self.bonne is not None and not isinstance(self.bonne, bool):
            raise ValueError(f"question {self.id} : `bonne` doit être true ou false")
        if self.type == "trou" and "___" not in self.texte:
            raise ValueError(f"question {self.id} : le texte doit contenir ___")
        if self.type == "echelle" and not (self.min < self.max and self.max - self.min <= 10):
            raise ValueError(f"question {self.id} : échelle de 2 à 11 valeurs")
        if self.type == "classement" and self.bonne is not None and sorted(self.bonne) != list(range(len(self.options))):
            raise ValueError(f"question {self.id} : `bonne` doit être une permutation des index")
        if self.type == "nombre" and self.bonne is not None and not isinstance(self.bonne, (int, float)):
            raise ValueError(f"question {self.id} : `bonne` doit être un nombre")

    @property
    def notee(self) -> bool:
        """Donne-t-elle des points ?"""
        if self.points is not None:
            return self.points
        if self.type == "buzzer":
            return True
        if self.type == "multi":
            return bool(self.bonnes)
        if self.type == "trou":
            return bool(self.bonnes)
        return self.bonne is not None

    # --- Sérialisation ---------------------------------------------------------------

    def public(self) -> dict:
        """Vue participant : sans les bonnes réponses."""
        d = {"id": self.id, "type": self.type, "question": self.question, "chrono": self.chrono, "notee": self.notee, "diapositive": self.diapositive}
        if self.options:
            d["options"] = self.options
        if self.type == "trou":
            d["texte"] = self.texte
        if self.type == "nuage":
            d.update(max_mots=self.max_mots, max_longueur=self.max_longueur)
        if self.type == "texte_libre":
            d["max_longueur"] = self.max_longueur
        if self.type == "echelle":
            d.update(min=self.min, max=self.max, libelle_min=self.libelle_min, libelle_max=self.libelle_max)
        if self.type == "nombre":
            d["unite"] = self.unite
        return d

    def complet(self) -> dict:
        d = self.public()
        d.pop("notee")
        d.update(bonne=self.bonne, bonnes=self.bonnes, points=self.points)
        if self.type == "trou":
            d["bonnes"] = self.bonnes
        return {k: v for k, v in d.items() if v not in (None, [], "", 0) or k in ("id", "type", "question", "chrono")}

    def corrige(self) -> dict:
        """Ce qui est publié à la révélation."""
        return {"bonne": self.bonne, "bonnes": self.bonnes}

    @staticmethod
    def depuis(q: dict) -> "Question":
        connus = {f for f in Question.__dataclass_fields__}
        champs = {k: v for k, v in q.items() if k in connus}
        champs["id"] = str(champs.get("id", ""))
        champs["type"] = str(champs.get("type", "qcm"))
        if "options" in champs:
            champs["options"] = [str(o) for o in champs["options"]]
        if champs["type"] == "trou":
            champs["bonnes"] = [str(b) for b in champs.get("bonnes", [])]
        for entier in ("chrono", "max_mots", "max_longueur", "min", "max"):
            if entier in champs and champs[entier] is not None:
                champs[entier] = int(champs[entier])
        question = Question(**champs)
        question.valider()
        return question


def charger_questions(chemin: Path) -> dict[str, Question]:
    if not chemin.exists():
        return {}
    return questions_depuis_liste(json.loads(chemin.read_text(encoding="utf-8")))


def questions_depuis_liste(brut: list[dict]) -> dict[str, Question]:
    questions: dict[str, Question] = {}
    for q in brut:
        question = Question.depuis(q)
        if question.id in questions:
            raise ValueError(f"identifiant de question en double : {question.id}")
        questions[question.id] = question
    return questions


# -------------------------------------------------------------------------------------
# Normalisation de texte (nuage, trou)
# -------------------------------------------------------------------------------------


def normaliser(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", texte.lower()).strip()


def pseudo_aleatoire() -> str:
    return f"{random.choice(ANIMAUX)}-{random.randint(10, 99)}"


# -------------------------------------------------------------------------------------
# Quiz
# -------------------------------------------------------------------------------------


@dataclass
class Quiz:
    questions: dict[str, Question]
    chemin_bdd: Path
    ouverte: str | None = None
    ouverte_a: float = 0.0
    revelee: str | None = None
    _verrou: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.chemin_bdd.parent.mkdir(parents=True, exist_ok=True)
        with self._connexion() as cnx:
            cnx.executescript(
                """CREATE TABLE IF NOT EXISTS reponses (
                       question_id TEXT NOT NULL,
                       participant TEXT NOT NULL,
                       reponse TEXT NOT NULL,
                       horodatage REAL NOT NULL,
                       temps REAL NOT NULL DEFAULT 0,
                       points REAL NOT NULL DEFAULT 0,
                       PRIMARY KEY (question_id, participant));
                   CREATE TABLE IF NOT EXISTS participants (
                       participant TEXT PRIMARY KEY,
                       pseudo TEXT NOT NULL,
                       cree REAL NOT NULL);
                   CREATE TABLE IF NOT EXISTS archives_resultats (
                       presentation TEXT NOT NULL,
                       question_id TEXT NOT NULL,
                       horodatage REAL NOT NULL,
                       question TEXT NOT NULL,
                       resultat TEXT NOT NULL);
                   CREATE INDEX IF NOT EXISTS archives_par_prez ON archives_resultats (presentation, horodatage);"""
            )

    def _connexion(self) -> sqlite3.Connection:
        cnx = sqlite3.connect(self.chemin_bdd, check_same_thread=False)
        cnx.execute("PRAGMA journal_mode=WAL")
        return cnx

    def remplacer_questions(self, questions: dict[str, Question]) -> None:
        with self._verrou:
            self.questions = questions
            if self.ouverte not in questions:
                self.ouverte = None
            if self.revelee not in questions:
                self.revelee = None

    # --- Administration ----------------------------------------------------------------

    def ouvrir(self, question_id: str) -> None:
        if question_id not in self.questions:
            raise KeyError(question_id)
        with self._verrou:
            self.ouverte, self.ouverte_a, self.revelee = question_id, time.time(), None

    def fermer(self) -> str | None:
        with self._verrou:
            derniere, self.ouverte = self.ouverte, None
        return derniere

    def reveler(self, question_id: str) -> None:
        """Ferme si besoin, calcule les points et marque la question révélée."""
        if question_id not in self.questions:
            raise KeyError(question_id)
        with self._verrou:
            if self.ouverte == question_id:
                self.ouverte = None
            self.revelee = question_id
            self._noter(question_id)

    def reinitialiser(self, question_id: str) -> None:
        if question_id not in self.questions:
            raise KeyError(question_id)
        with self._verrou, self._connexion() as cnx:
            cnx.execute("DELETE FROM reponses WHERE question_id = ?", (question_id,))
            if self.revelee == question_id:
                self.revelee = None

    def purger(self) -> None:
        with self._verrou, self._connexion() as cnx:
            cnx.execute("DELETE FROM reponses")
            cnx.execute("DELETE FROM participants")
            self.ouverte, self.revelee = None, None

    @property
    def reste(self) -> float | None:
        """Secondes restantes sur la question ouverte, None si pas de chrono."""
        if not self.ouverte:
            return None
        q = self.questions[self.ouverte]
        if q.chrono <= 0:
            return None
        return max(0.0, q.chrono - (time.time() - self.ouverte_a))

    # --- Participants --------------------------------------------------------------------

    def pseudo(self, participant: str, souhaite: str | None = None) -> str:
        """Pseudo du participant ; le crée (choisi ou généré) s'il n'existe pas."""
        souhaite = re.sub(r"\s+", " ", (souhaite or "")).strip()[:20]
        with self._connexion() as cnx:
            ligne = cnx.execute("SELECT pseudo FROM participants WHERE participant = ?", (participant,)).fetchone()
            if ligne and not souhaite:
                return ligne[0]
            pseudo = souhaite or pseudo_aleatoire()
            cnx.execute(
                "INSERT INTO participants (participant, pseudo, cree) VALUES (?, ?, ?) ON CONFLICT(participant) DO UPDATE SET pseudo = excluded.pseudo",
                (participant, pseudo, time.time()),
            )
        return pseudo

    def voter(self, question_id: str, participant: str, reponse: Any) -> Any:
        """Enregistre ou remplace la réponse ; renvoie la réponse nettoyée."""
        if question_id != self.ouverte:
            raise PermissionError("question fermée")
        q = self.questions[question_id]
        if q.chrono > 0 and self.reste == 0.0:
            raise PermissionError("temps écoulé")
        propre = self._valider_reponse(q, reponse)
        maintenant = time.time()
        with self._verrou, self._connexion() as cnx:
            if q.type == "buzzer" and cnx.execute("SELECT 1 FROM reponses WHERE question_id = ? AND participant = ?", (question_id, participant)).fetchone():
                raise PermissionError("déjà buzzé")
            cnx.execute(
                """INSERT INTO reponses (question_id, participant, reponse, horodatage, temps)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(question_id, participant)
                   DO UPDATE SET reponse = excluded.reponse, horodatage = excluded.horodatage, temps = excluded.temps""",
                (question_id, participant, json.dumps(propre, ensure_ascii=False), maintenant, maintenant - self.ouverte_a),
            )
        return propre

    def _valider_reponse(self, q: Question, r: Any) -> Any:
        t = q.type
        if t in ("qcm", "emoji", "rapidite"):
            if not isinstance(r, int) or not 0 <= r < len(q.options):
                raise ValueError("option inconnue")
            return r
        if t == "multi":
            if not isinstance(r, list) or any(not isinstance(i, int) or not 0 <= i < len(q.options) for i in r):
                raise ValueError("options inconnues")
            return sorted(set(r))
        if t == "vrai_faux":
            if not isinstance(r, bool):
                raise ValueError("vrai ou faux attendu")
            return r
        if t == "buzzer":
            return True
        if t == "nuage":
            if not isinstance(r, list):
                raise ValueError("liste de mots attendue")
            mots = []
            for m in r[: q.max_mots]:
                m = re.sub(r"\s+", " ", str(m)).strip()[: q.max_longueur]
                if m and normaliser(m):
                    mots.append(m)
            if not mots:
                raise ValueError("aucun mot")
            return mots
        if t in ("texte_libre", "trou"):
            texte = re.sub(r"\s+", " ", str(r)).strip()[: (q.max_longueur if t == "texte_libre" else 60)]
            if not texte:
                raise ValueError("texte vide")
            return texte
        if t == "echelle":
            if not isinstance(r, int) or not q.min <= r <= q.max:
                raise ValueError("valeur hors échelle")
            return r
        if t == "classement":
            if not isinstance(r, list) or sorted(r) != list(range(len(q.options))):
                raise ValueError("ordre invalide")
            return r
        if t == "nombre":
            if isinstance(r, bool) or not isinstance(r, (int, float)) or not math.isfinite(r):
                raise ValueError("nombre attendu")
            return r
        raise ValueError("type inconnu")

    def reponse_de(self, question_id: str, participant: str) -> dict | None:
        with self._connexion() as cnx:
            ligne = cnx.execute("SELECT reponse, points FROM reponses WHERE question_id = ? AND participant = ?", (question_id, participant)).fetchone()
        return {"reponse": json.loads(ligne[0]), "points": ligne[1]} if ligne else None

    # --- Notation ---------------------------------------------------------------------------

    def _noter(self, question_id: str) -> None:
        q = self.questions[question_id]
        if not q.notee:
            return
        with self._connexion() as cnx:
            lignes = cnx.execute("SELECT participant, reponse, temps FROM reponses WHERE question_id = ? ORDER BY horodatage", (question_id,)).fetchall()
            for rang, (participant, brut, temps) in enumerate(lignes):
                points = self._points(q, json.loads(brut), temps, rang)
                cnx.execute("UPDATE reponses SET points = ? WHERE question_id = ? AND participant = ?", (points, question_id, participant))

    def _points(self, q: Question, r: Any, temps: float, rang: int) -> float:
        """Barème : POINTS_MAX × justesse × facteur de vitesse (comme Kahoot : jusqu'à −50 % sur la durée du chrono)."""
        t = q.type
        justesse = 0.0
        if t in ("qcm", "emoji", "rapidite"):
            justesse = 1.0 if r == q.bonne else 0.0
        elif t == "vrai_faux":
            justesse = 1.0 if r == q.bonne else 0.0
        elif t == "multi":
            attendu = set(q.bonnes)
            if attendu:
                donne = set(r)
                justesse = 1.0 if donne == attendu else max(0.0, (len(donne & attendu) - len(donne - attendu)) / len(attendu)) * 0.5
        elif t == "trou":
            justesse = 1.0 if normaliser(r) in {normaliser(b) for b in q.bonnes} else 0.0
        elif t == "classement":
            if q.bonne is not None:
                justesse = sum(1 for a, b in zip(r, q.bonne) if a == b) / len(q.bonne)
        elif t == "nombre":
            if q.bonne is not None:
                ecart = abs(r - q.bonne) / (abs(q.bonne) or 1.0)
                justesse = max(0.0, 1.0 - ecart) if r != q.bonne else 1.0
        elif t == "buzzer":
            # Le premier prend tout, puis dégressif : 1000, 800, 640…
            return round(POINTS_MAX * 0.8**rang) if rang < 10 else 0
        if justesse <= 0:
            return 0.0
        vitesse = 1.0
        if q.chrono > 0:
            vitesse = 1.0 - min(1.0, max(0.0, temps / q.chrono)) * (1.0 if t == "rapidite" else 0.5)
        return round(POINTS_MAX * justesse * vitesse)

    # --- Résultats ----------------------------------------------------------------------------

    def _reponses(self, question_id: str, depuis: float | None = None, jusqua: float | None = None) -> list[tuple[str, Any, float, float, str]]:
        """(participant, réponse, temps, points, pseudo) par ordre d'arrivée ; bornées dans le temps si demandé (epoch, inclusif)."""
        requete = """SELECT r.participant, r.reponse, r.temps, r.points, p.pseudo FROM reponses r
                     LEFT JOIN participants p ON p.participant = r.participant WHERE r.question_id = ?"""
        params: list = [question_id]
        if depuis is not None:
            requete += " AND r.horodatage >= ?"
            params.append(depuis)
        if jusqua is not None:
            requete += " AND r.horodatage <= ?"
            params.append(jusqua)
        with self._connexion() as cnx:
            return [(p, json.loads(r), t, pts, pseudo or "") for p, r, t, pts, pseudo in cnx.execute(requete + " ORDER BY r.horodatage", params)]

    # --- Archives des résultats par séance (docs/seance.md §F) ---------------------------

    def archiver(self, presentation: str | None, question_id: str) -> None:
        """Instantané du résultat d'une question (à sa fermeture ou à sa révélation) : la page « séance » et l'export PDF
        le retrouvent même après une remise à zéro des réponses ou une autre séance."""
        if not presentation or question_id not in self.questions:
            return
        q = self.questions[question_id]
        with self._verrou, self._connexion() as cnx:
            cnx.execute("INSERT INTO archives_resultats VALUES (?, ?, ?, ?, ?)",
                        (presentation, question_id, time.time(), json.dumps(q.complet(), ensure_ascii=False),
                         json.dumps(self.resultat(question_id), ensure_ascii=False)))

    def archives(self, presentation: str, depuis: float, jusqua: float) -> dict[str, dict]:
        """{question_id: {question, resultat, horodatage}} : le dernier instantané de chaque question dans l'intervalle."""
        with self._connexion() as cnx:
            lignes = cnx.execute("""SELECT question_id, horodatage, question, resultat FROM archives_resultats
                                    WHERE presentation = ? AND horodatage BETWEEN ? AND ? ORDER BY horodatage""",
                                 (presentation, depuis, jusqua)).fetchall()
        return {qid: {"question": json.loads(q), "resultat": json.loads(r), "horodatage": t} for qid, t, q, r in lignes}

    def resultat(self, question_id: str, depuis: float | None = None, jusqua: float | None = None, question: "Question | None" = None) -> dict:
        """Agrégat affichable, dépendant du type ; jamais de donnée individuelle hors pseudo.

        `depuis` / `jusqua` : seulement les réponses de cet intervalle (une séance) ; `question` : définition à utiliser
        (une question d'une présentation qui n'est pas l'active)."""
        q = question or self.questions[question_id]
        reponses = self._reponses(question_id, depuis, jusqua)
        total = len(reponses)
        t = q.type
        res: dict[str, Any] = {"total": total}
        if t in ("qcm", "emoji", "rapidite", "multi", "classement"):
            comptes = [0] * len(q.options)
            for _, r, *_ in reponses:
                for i in (r if isinstance(r, list) else [r]):
                    if t != "classement":
                        comptes[i] += 1
            if t == "classement":
                rangs = [[] for _ in q.options]
                for _, ordre, *_ in reponses:
                    for position, i in enumerate(ordre):
                        rangs[i].append(position + 1)
                res["rang_moyen"] = [round(statistics.fmean(x), 2) if x else None for x in rangs]
                res["ordre"] = sorted(range(len(q.options)), key=lambda i: (res["rang_moyen"][i] is None, res["rang_moyen"][i] or 0))
            else:
                res["comptes"] = comptes
            if t == "rapidite":
                res["rapides"] = [{"pseudo": pseudo, "temps": round(tp, 2)} for _, r, tp, _, pseudo in sorted(reponses, key=lambda x: x[2]) if r == q.bonne][:5]
        elif t == "vrai_faux":
            res["comptes"] = [sum(1 for _, r, *_ in reponses if r is True), sum(1 for _, r, *_ in reponses if r is False)]
        elif t == "buzzer":
            res["premiers"] = [{"pseudo": pseudo, "temps": round(tp, 2)} for _, _, tp, _, pseudo in sorted(reponses, key=lambda x: x[2])[:5]]
        elif t == "nuage":
            compteur: Counter[str] = Counter()
            affichage: dict[str, str] = {}
            for _, mots, *_ in reponses:
                for m in mots:
                    cle = normaliser(m)
                    compteur[cle] += 1
                    affichage.setdefault(cle, m)
            res["mots"] = [{"mot": affichage[cle], "n": n} for cle, n in compteur.most_common(60)]
        elif t == "texte_libre":
            res["textes"] = [{"pseudo": pseudo, "texte": r} for _, r, _, _, pseudo in reversed(reponses)][:40]
        elif t == "trou":
            compteur = Counter(normaliser(r) for _, r, *_ in reponses)
            affichage = {}
            for _, r, *_ in reponses:
                affichage.setdefault(normaliser(r), r)
            acceptees = {normaliser(b) for b in q.bonnes}
            res["frequentes"] = [{"texte": affichage[cle], "n": n, "bonne": cle in acceptees} for cle, n in compteur.most_common(8)]
            res["bonnes_n"] = sum(n for cle, n in compteur.items() if cle in acceptees)
        elif t == "echelle":
            valeurs = [r for _, r, *_ in reponses]
            res["comptes"] = [valeurs.count(v) for v in range(q.min, q.max + 1)]
            res["moyenne"] = round(statistics.fmean(valeurs), 2) if valeurs else None
        elif t == "nombre":
            valeurs = sorted(r for _, r, *_ in reponses)
            res["mediane"] = statistics.median(valeurs) if valeurs else None
            res["min"], res["max"] = (valeurs[0], valeurs[-1]) if valeurs else (None, None)
            res["classes"] = _histogramme(valeurs, 8)
            if q.bonne is not None:
                res["proches"] = [{"pseudo": pseudo, "valeur": r} for _, r, _, _, pseudo in sorted(reponses, key=lambda x: abs(x[1] - q.bonne))[:5]]
        return res

    def etat(self, question_id: str | None = None) -> dict:
        cible = question_id or self.ouverte or self.revelee
        if cible is None or cible not in self.questions:
            return {"question": None, "ouverte": False, "revele": False, "reste": None, "resultat": {"total": 0}}
        q = self.questions[cible]
        revele = self.revelee == cible
        etat = {
            "question": q.public(),
            "ouverte": cible == self.ouverte,
            "revele": revele,
            "reste": self.reste if cible == self.ouverte else None,
            "resultat": self.resultat(cible),
        }
        if revele:
            etat["correction"] = q.corrige()
        return etat

    def classement(self, nombre: int = 10) -> list[dict]:
        with self._connexion() as cnx:
            lignes = cnx.execute(
                """SELECT p.pseudo, COALESCE(SUM(r.points), 0) AS pts, COUNT(r.points) AS n
                   FROM participants p LEFT JOIN reponses r ON r.participant = p.participant
                   GROUP BY p.participant HAVING pts > 0 ORDER BY pts DESC, n ASC LIMIT ?""",
                (max(1, min(nombre, 1000)),),
            ).fetchall()
        return [{"rang": i + 1, "pseudo": pseudo, "points": int(pts)} for i, (pseudo, pts, _) in enumerate(lignes)]

    def export_agrege(self) -> str:
        """CSV agrégé : une ligne par option / mot / valeur, aucune donnée individuelle."""
        lignes = ["question_id;type;question;cle;valeur"]
        for q in self.questions.values():
            r = self.resultat(q.id)
            lignes.append(f"{q.id};{q.type};{_csv(q.question or q.texte)};total;{r['total']}")
            if "comptes" in r:
                libelles = q.options or ["vrai", "faux"] if q.type != "echelle" else [str(v) for v in range(q.min, q.max + 1)]
                for libelle, n in zip(libelles, r["comptes"]):
                    lignes.append(f"{q.id};{q.type};;{_csv(libelle)};{n}")
            for m in r.get("mots", []):
                lignes.append(f"{q.id};{q.type};;{_csv(m['mot'])};{m['n']}")
            for f in r.get("frequentes", []):
                lignes.append(f"{q.id};{q.type};;{_csv(f['texte'])};{f['n']}")
            for cle in ("moyenne", "mediane"):
                if r.get(cle) is not None:
                    lignes.append(f"{q.id};{q.type};;{cle};{r[cle]}")
        return "\n".join(lignes) + "\n"


def _histogramme(valeurs: list[float], classes: int) -> list[dict]:
    if not valeurs:
        return []
    lo, hi = valeurs[0], valeurs[-1]
    if lo == hi:
        return [{"de": lo, "a": hi, "n": len(valeurs)}]
    pas = (hi - lo) / classes
    resultat = []
    for i in range(classes):
        de, a = lo + i * pas, lo + (i + 1) * pas
        n = sum(1 for v in valeurs if de <= v < a or (i == classes - 1 and v == hi))
        resultat.append({"de": round(de, 2), "a": round(a, 2), "n": n})
    return resultat


def _csv(texte: str) -> str:
    return '"' + texte.replace('"', '""') + '"' if any(c in texte for c in ';"\n') else texte

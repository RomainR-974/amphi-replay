"""Journal de séance : ce qui s'est passé sur le deck et quand (voir docs/postprod.md, section 1).

Table SQLite `journal` dans la base du quiz (`CHEMIN_BDD`) : une ligne par changement de diapositive
(rôle `remote`), par ouverture / fermeture / révélation de question, par activation de présentation et par
ouverture / fermeture de la séquence questions-réponses (`qr_ouvrir` / `qr_fermer`, où la post-production coupe).
La post-production s'en sert pour chapitrer le replay. `Quiz.purger()` ne touche pas à cette table.

Les titres des diapositives sont extraits de `slides.html` par expressions régulières : premier
`h1`/`h2`/`h3`/`.dsfr-titre`/`.el.texte` non vide de chaque `<section>` de premier niveau ; une
section `data-quiz` prend « Quiz : <libellé de la question> ».
"""

from __future__ import annotations

import html as module_html
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

TYPES = ("diapositive", "quiz_ouvrir", "quiz_fermer", "quiz_reveler", "activation", "qr_ouvrir", "qr_fermer")
ECART_SEANCE_S = 3 * 3600  # deux événements séparés de plus de 3 h appartiennent à deux séances
LONGUEUR_TITRE = 80

BALISE_SECTION = re.compile(r"<(/?)section\b[^>]*>", re.I)
BALISE = re.compile(r"<[^>]+>")
ASIDE_NOTES = re.compile(r"<aside\b[^>]*class=\"[^\"]*\bnotes\b[^\"]*\"[^>]*>.*?</aside>", re.I | re.S)
DATA_QUIZ = re.compile(r"data-quiz=\"([^\"]*)\"", re.I)
TITRE_HN = re.compile(r"<h([123])\b[^>]*>(.*?)</h\1>", re.I | re.S)
OUVRANTE_CLASSE = re.compile(r"<(\w+)\b[^>]*class=\"([^\"]*)\"[^>]*>", re.I)
TAILLE_POLICE = re.compile(r"font-size:\s*(\d+(?:\.\d+)?)px", re.I)


class Journal:
    """Accès à la table `journal`. Sans dépendance au serveur : les horodatages sont des `time.time()`."""

    def __init__(self, chemin_bdd: Path) -> None:
        self.chemin_bdd = Path(chemin_bdd)
        self.chemin_bdd.parent.mkdir(parents=True, exist_ok=True)
        self._verrou = threading.Lock()
        with self._connexion() as cnx:
            cnx.executescript(
                """CREATE TABLE IF NOT EXISTS journal (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       horodatage REAL NOT NULL,
                       presentation TEXT NOT NULL,
                       type TEXT NOT NULL,
                       h INTEGER, v INTEGER, f INTEGER,
                       question TEXT);
                   CREATE INDEX IF NOT EXISTS journal_prez ON journal (presentation, horodatage);"""
            )

    def _connexion(self) -> sqlite3.Connection:
        cnx = sqlite3.connect(self.chemin_bdd, check_same_thread=False)
        cnx.execute("PRAGMA journal_mode=WAL")
        return cnx

    def ajouter(self, presentation: str | None, type: str, h: int | None = None, v: int | None = None, f: int | None = None,
                question: str | None = None, horodatage: float | None = None) -> bool:
        """Ajoute une ligne ; une diapositive identique à la précédente (même présentation) est ignorée. Renvoie True si écrite."""
        if not presentation or type not in TYPES:
            return False
        with self._verrou, self._connexion() as cnx:
            if type == "diapositive":
                derniere = cnx.execute(
                    "SELECT h, v, f FROM journal WHERE presentation = ? AND type = 'diapositive' ORDER BY horodatage DESC, id DESC LIMIT 1",
                    (presentation,),
                ).fetchone()
                if derniere is not None and tuple(derniere) == (h, v, f):
                    return False
            cnx.execute(
                "INSERT INTO journal (horodatage, presentation, type, h, v, f, question) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (horodatage if horodatage is not None else time.time(), presentation, type, h, v, f, question),
            )
        return True

    def lire(self, presentation: str, depuis: float | None = None, jusqua: float | None = None) -> list[dict]:
        """Événements d'une présentation, triés, bornés par `depuis` / `jusqua` (epoch, inclusifs)."""
        requete = "SELECT horodatage, type, h, v, f, question FROM journal WHERE presentation = ?"
        params: list = [presentation]
        if depuis is not None:
            requete += " AND horodatage >= ?"
            params.append(depuis)
        if jusqua is not None:
            requete += " AND horodatage <= ?"
            params.append(jusqua)
        requete += " ORDER BY horodatage, id"
        with self._connexion() as cnx:
            lignes = cnx.execute(requete, params).fetchall()
        return [{"horodatage": t, "type": ty, "h": h, "v": v, "f": f, "question": q} for t, ty, h, v, f, q in lignes]

    def seances(self, presentation: str, ecart: float = ECART_SEANCE_S) -> list[dict]:
        """Regroupe le journal en séances : suites d'événements séparés de moins de `ecart` secondes, la plus récente d'abord."""
        groupes: list[list[dict]] = []
        for ev in self.lire(presentation):
            if groupes and ev["horodatage"] - groupes[-1][-1]["horodatage"] < ecart:
                groupes[-1].append(ev)
            else:
                groupes.append([ev])
        seances = []
        for evenements in groupes:
            diapositives = {(e["h"], e["v"]) for e in evenements if e["type"] == "diapositive"}
            seances.append({"debut": evenements[0]["horodatage"], "fin": evenements[-1]["horodatage"], "evenements": evenements, "diapositives": len(diapositives)})
        seances.reverse()
        return seances

    def purger(self, presentation: str | None = None) -> None:
        with self._verrou, self._connexion() as cnx:
            if presentation:
                cnx.execute("DELETE FROM journal WHERE presentation = ?", (presentation,))
            else:
                cnx.execute("DELETE FROM journal")


# -------------------------------------------------------------------------------------
# Titres des diapositives
# -------------------------------------------------------------------------------------


def _texte(fragment: str) -> str:
    texte = module_html.unescape(BALISE.sub(" ", fragment))
    texte = re.sub(r"\s+", " ", texte).strip()
    if len(texte) <= LONGUEUR_TITRE:
        return texte
    coupe = texte[: LONGUEUR_TITRE - 1]
    if " " in coupe[40:]:
        coupe = coupe[: coupe.rfind(" ")]
    return coupe.rstrip(" ,;:-–") + "…"


def _sections_premier_niveau(html: str) -> list[str]:
    """Contenu (balise ouvrante comprise) de chaque <section> de premier niveau ; les sections imbriquées restent dedans."""
    sections, profondeur, debut = [], 0, 0
    for m in BALISE_SECTION.finditer(html):
        if m.group(1) == "":
            if profondeur == 0:
                debut = m.start()
            profondeur += 1
        else:
            profondeur = max(0, profondeur - 1)
            if profondeur == 0:
                sections.append(html[debut : m.end()])
    if profondeur > 0:  # section non fermée : on prend jusqu'à la fin
        sections.append(html[debut:])
    return sections


def _contenu_element(html: str, ouvrante: re.Match) -> str:
    """Contenu d'un élément dont on a la balise ouvrante, en comptant les balises imbriquées du même nom."""
    nom = ouvrante.group(1)
    motif = re.compile(rf"<(/?){nom}\b[^>]*>", re.I)
    profondeur = 1
    for m in motif.finditer(html, ouvrante.end()):
        profondeur += -1 if m.group(1) else 1
        if profondeur == 0:
            return html[ouvrante.end() : m.start()]
    return html[ouvrante.end() :]


def _titre_section(section: str) -> str:
    section = ASIDE_NOTES.sub("", section)
    for m in TITRE_HN.finditer(section):
        t = _texte(m.group(2))
        if t:
            return t
    candidats_titre, candidats_texte = [], []
    for m in OUVRANTE_CLASSE.finditer(section):
        classes = m.group(2).split()
        if "dsfr-titre" in classes:
            candidats_titre.append(m)
        elif "el" in classes and "texte" in classes:
            candidats_texte.append(m)
    for m in candidats_titre:
        t = _texte(_contenu_element(section, m))
        if t:
            return t
    # Parmi les blocs de texte libres, le plus gros corps de police est le titre le plus probable ; à égalité, le premier.
    meilleur, meilleure_taille = "", -1.0
    for m in candidats_texte:
        contenu = _contenu_element(section, m)
        t = _texte(contenu)
        if not t:
            continue
        tailles = [float(x) for x in TAILLE_POLICE.findall(m.group(0) + contenu)]
        taille = max(tailles) if tailles else 0.0
        if taille > meilleure_taille:
            meilleur, meilleure_taille = t, taille
    return meilleur


def titres_diapositives(html: str, questions: dict[str, str] | None = None) -> list[str]:
    """Titre de chaque diapositive (index `h`) à partir du fragment slides.html.

    `questions` : libellé par identifiant (quiz.json) ; une section `data-quiz="id"` donne « Quiz : <libellé> ».
    Une section sans titre identifiable donne « Diapositive N ».
    """
    questions = questions or {}
    titres = []
    for i, section in enumerate(_sections_premier_niveau(html)):
        ouvrante = section[: section.find(">") + 1]
        quiz = DATA_QUIZ.search(ouvrante)
        titre = ""
        if quiz:
            titre = "Quiz : " + (questions.get(quiz.group(1)) or quiz.group(1))
        else:
            titre = _titre_section(section)
        titres.append(titre or f"Diapositive {i + 1}")
    return titres


def questions_diapositives(html: str) -> list[str | None]:
    """Identifiant de la question (`data-quiz`) de chaque diapositive de premier niveau (index `h`), None sinon.

    Même découpage en sections que `titres_diapositives` : l'export SCORM s'en sert pour rattacher les questions aux chapitres.
    """
    identifiants: list[str | None] = []
    for section in _sections_premier_niveau(html):
        ouvrante = section[: section.find(">") + 1]
        quiz = DATA_QUIZ.search(ouvrante)
        identifiants.append(quiz.group(1).strip() or None if quiz else None)
    return identifiants


def libelles_questions(fichier_quiz: Path) -> dict[str, str]:
    """{id: libellé} depuis un quiz.json (tolérant : fichier absent ou invalide → {})."""
    try:
        liste = json.loads(Path(fichier_quiz).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(liste, list):
        return {}
    libelles = {}
    for q in liste:
        if isinstance(q, dict) and q.get("id"):
            libelles[str(q["id"])] = _texte(str(q.get("question") or q.get("texte") or q["id"]))
    return libelles


def titres_presentation(dossier: Path) -> list[str]:
    """Titres des diapositives d'un dossier de présentation (slides.html + quiz.json)."""
    dossier = Path(dossier)
    slides = dossier / "slides.html"
    if not slides.is_file():
        return []
    return titres_diapositives(slides.read_text(encoding="utf-8"), libelles_questions(dossier / "quiz.json"))

"""Historique des séances d'une présentation (docs/seance.md §F) : page « séance » et export PDF.

Une séance = un groupe d'événements du journal (journal.py, `seances`). On lui rattache :
  - la copie figée prise au début de l'enregistrement OBS (presentations/<slug>/seances/<id>/), sinon la version actuelle ;
  - le replay dont la vidéo couvre la séance, s'il est sur ce serveur (replays/<id>/journal.json) ;
  - les résultats de chaque question : instantanés archivés à la fermeture / révélation (quiz.archives), sinon recalculés
    à partir des réponses enregistrées pendant la séance (une réponse remplacée lors d'une séance suivante est alors perdue).

Identifiant d'une séance = l'epoch entier de son premier événement.
"""

from __future__ import annotations

import json
from pathlib import Path

from .journal import titres_diapositives, libelles_questions
from .quiz import Question, charger_questions

MARGE_COPIE_S = 15 * 60   # une copie figée prise jusqu'à 15 min avant le premier événement appartient à la séance
MARGE_REPONSES_S = 10 * 60  # réponses arrivées jusqu'à 10 min après le dernier événement


def _copie(biblio, slug: str, debut: float, fin: float) -> dict | None:
    candidates = [c for c in biblio.seances_figees(slug) if c.get("debut_enregistrement") and debut - MARGE_COPIE_S <= c["debut_enregistrement"] <= fin]
    return min(candidates, key=lambda c: abs(c["debut_enregistrement"] - debut)) if candidates else None


def _replay(replays, slug: str, debut: float, fin: float) -> dict | None:
    """Replay dont la vidéo recouvre la séance : {id, titre, debut_video, duree} (debut_video = epoch de l'instant 0 de replay.mp4)."""
    try:
        dossier = replays.dossier(slug)
    except KeyError:
        return None
    meilleur = None
    for d in sorted(dossier.iterdir()) if dossier.is_dir() else []:
        if not (d / "replay.mp4").is_file():
            continue
        try:
            j = json.loads((d / "journal.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        origine = j.get("debut_enregistrement")
        duree = float(j.get("duree") or 0)
        if origine is None or not duree:
            continue
        debut_video = float(origine) + float(j.get("decalage") or 0)
        recouvrement = min(fin, debut_video + duree) - max(debut, debut_video)
        if recouvrement > 0 and (meilleur is None or recouvrement > meilleur["recouvrement"]):
            meilleur = {"id": d.name, "titre": j.get("titre") or d.name, "debut_video": debut_video, "duree": duree, "recouvrement": recouvrement}
    if meilleur:
        meilleur.pop("recouvrement")
    return meilleur


def liste(biblio, journal_bd, replays, slug: str) -> list[dict]:
    """Séances de la présentation, la plus récente d'abord : [{id, debut, fin, diapositives, questions, copie, replay}]."""
    resultat = []
    for s in journal_bd.seances(slug):
        if not any(e["type"] == "diapositive" for e in s["evenements"]):
            continue  # simple activation sans séance jouée
        copie = _copie(biblio, slug, s["debut"], s["fin"])
        replay = _replay(replays, slug, s["debut"], s["fin"])
        resultat.append({"id": int(s["debut"]), "debut": s["debut"], "fin": s["fin"], "diapositives": s["diapositives"],
                         "questions": len({e["question"] for e in s["evenements"] if e["type"] == "quiz_ouvrir" and e["question"]}),
                         "copie": copie["id"] if copie else None, "replay": replay["id"] if replay else None})
    return resultat


def detail(biblio, journal_bd, replays, quiz, slug: str, id_seance: int) -> dict:
    """Une séance complète ; KeyError si elle n'existe pas."""
    seance = next((s for s in journal_bd.seances(slug) if int(s["debut"]) == int(id_seance)), None)
    if seance is None:
        raise KeyError(id_seance)
    debut, fin = seance["debut"], seance["fin"]
    copie = _copie(biblio, slug, debut, fin)
    dossier = biblio.seance_figee(slug, copie["id"]) if copie else biblio.exiger(slug)
    slides = dossier / "slides.html"
    titres = titres_diapositives(slides.read_text(encoding="utf-8"), libelles_questions(dossier / "quiz.json")) if slides.is_file() else []
    try:
        questions: dict[str, Question] = charger_questions(dossier / "quiz.json")
    except (OSError, ValueError):
        questions = {}

    # Résultats : instantané archivé, sinon recalcul sur les réponses de la séance.
    archives = quiz.archives(slug, debut, fin + MARGE_REPONSES_S)
    posees = []
    for e in seance["evenements"]:
        if e["type"] in ("quiz_ouvrir", "quiz_reveler", "quiz_fermer") and e["question"] and e["question"] not in posees:
            posees.append(e["question"])
    resultats = {}
    for qid in posees:
        if qid in archives:
            resultats[qid] = {"question": archives[qid]["question"], "resultat": archives[qid]["resultat"], "source": "archive"}
        elif qid in questions:
            r = quiz.resultat(qid, debut - 60, fin + MARGE_REPONSES_S, question=questions[qid])
            resultats[qid] = {"question": questions[qid].complet(), "resultat": r, "source": "reponses"}
    return {"id": int(debut), "prez": slug, "titre": biblio.lire(slug)["titre"], "debut": debut, "fin": fin,
            "evenements": seance["evenements"], "titres": titres, "copie": copie["id"] if copie else None,
            "replay": _replay(replays, slug, debut, fin), "resultats": resultats}


def fichier_copie(biblio, slug: str, id_copie: str, chemin: str) -> Path | None:
    """Fichier public d'une copie figée (slides.html, quiz.json, medias/…) ou None ; confiné au dossier de la copie."""
    try:
        racine = biblio.seance_figee(slug, id_copie).resolve()
    except KeyError:
        return None
    if chemin not in ("slides.html", "quiz.json") and not chemin.startswith("medias/"):
        return None
    f = (racine / chemin).resolve()
    if racine not in f.parents or not f.is_file():
        return None
    return f

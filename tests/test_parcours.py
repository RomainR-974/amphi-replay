"""Parcours de formation (docs/postprod.md §6) : séquences, texte des diapositives, GIFT, sous-titres, production."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from server import parcours
from server.parcours import diapositives, fichier_gift, question_gift, sequences
from server.replays import CHEMIN_PARCOURS

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "postprod"))

import transcrire  # noqa: E402

FFMPEG = shutil.which("ffmpeg") is not None

SLIDES = """
<section class="libre dsfr" data-id="s1"><h2 class="dsfr-titre">Bienvenue</h2><p>Le programme</p><aside class="notes">Accueillir.</aside></section>
<section class="libre dsfr" data-quiz="q1"><h2>Question</h2></section>
<section class="libre dsfr dsfr-chapitre" data-id="s3"><h2 class="dsfr-titre">Comprendre l’IA</h2></section>
<section class="libre dsfr"><h2 class="dsfr-titre">Un outil</h2><ul><li>Prédit le mot suivant</li><li>Ne comprend pas</li></ul>
  <div class="el texte dsfr-pied"><span>PIED</span></div></section>
<section class="libre dsfr" data-partie><h2 class="dsfr-titre">Les règles</h2></section>
<section class="libre dsfr" data-quiz="q2"><h2>Vrai ou faux</h2></section>
"""
TITRES = ["Bienvenue", "Quiz : Question", "Comprendre l’IA", "Un outil", "Les règles", "Quiz : Vrai ou faux"]


def test_diapositives_texte_notes_parties_quiz():
    d = diapositives(SLIDES, TITRES)
    assert [x["partie"] for x in d] == [False, False, True, False, True, False]
    assert [x["quiz"] for x in d] == [None, "q1", None, None, None, "q2"]
    assert d[0]["notes"] == "Accueillir." and "Le programme" in d[0]["lignes"] and "Accueillir." not in d[0]["lignes"]
    assert d[3]["lignes"] == ["Prédit le mot suivant", "Ne comprend pas"]  # titre et pied de page retirés


def test_sequences_coupees_aux_parties():
    d = diapositives(SLIDES, TITRES)
    chapitres = [{"debut": 0, "titre": "Bienvenue", "h": 0}, {"debut": 60, "titre": "Quiz : Question", "h": 1},
                 {"debut": 120, "titre": "Comprendre l’IA", "h": 2}, {"debut": 180, "titre": "Un outil", "h": 3},
                 {"debut": 400, "titre": "Les règles", "h": 4}, {"debut": 500, "titre": "Quiz : Vrai ou faux", "h": 5}]
    s = sequences(chapitres, 600, d)
    assert [x["titre"] for x in s] == ["Introduction", "Comprendre l’IA", "Les règles"]
    assert [(x["debut"], x["fin"]) for x in s] == [(0, 120), (120, 400), (400, 600)]
    assert [x["diapositives"] for x in s] == [[0, 1], [2, 3], [4, 5]]


def test_sequence_quand_la_diapositive_de_partie_a_ete_passee_vite():
    """Diapositive de partie restée moins de 20 s : fusionnée dans le chapitre suivant, la séquence commence quand même là."""
    d = diapositives(SLIDES, TITRES)
    chapitres = [{"debut": 0, "titre": "Bienvenue", "h": 0}, {"debut": 120, "titre": "Un outil", "h": 3}, {"debut": 400, "titre": "Quiz : Vrai ou faux", "h": 5}]
    s = sequences(chapitres, 600, d)
    assert [x["titre"] for x in s] == ["Introduction", "Comprendre l’IA", "Les règles"]
    assert [x["diapositives"] for x in s] == [[0, 1], [2, 3], [4, 5]]


def test_sequences_sans_partie_par_blocs_de_huit_minutes():
    d = [{"titre": f"D{i}", "lignes": [], "notes": "", "partie": False, "quiz": None} for i in range(5)]
    chapitres = [{"debut": t, "titre": f"D{i}", "h": i} for i, t in enumerate((0, 200, 500, 700, 1000))]
    s = sequences(chapitres, 1100, d)
    assert [x["debut"] for x in s] == [0, 500, 1000]
    assert sequences([], 90, d)[0]["fin"] == 90


def test_gift_par_type():
    assert question_gift({"type": "qcm", "question": "Combien : 2+2 ?", "options": ["3", "4"], "bonne": 1}, "Q1") == "::Q1::Combien \\: 2+2 ? {~3 =4}"
    multi = question_gift({"type": "multi", "question": "Q", "options": ["a", "b", "c"], "bonnes": [0, 2]}, "Q2")
    assert "~%50%a" in multi and "~%-100%b" in multi and "~%50%c" in multi
    assert question_gift({"type": "vrai_faux", "question": "Q", "bonne": False}, "Q3").endswith("{FALSE}")
    assert question_gift({"type": "trou", "question": "", "texte": "Le ___ du jour", "bonnes": ["mot", "terme"]}, "Q4") == "::Q4::Le {=mot =terme} du jour"
    assert question_gift({"type": "nombre", "question": "Année ?", "bonne": 1956, "unite": ""}, "Q5") == "::Q5::Année ? {#1956}"
    fichier = fichier_gift([("Intro", [{"type": "vrai_faux", "question": "Q", "bonne": True}]), ("Vide", [])], "Cours / test")
    assert "$CATEGORY: $course$/top/Cours - test/01 Intro" in fichier and "Vide" not in fichier


def test_chemins_publics_du_parcours():
    for bon in ("parcours/cours.html", "parcours/questions.gift", "parcours/01-intro/video.mp4", "parcours/02-comprendre-l-ia/images/04-un-outil.jpg"):
        assert CHEMIN_PARCOURS.match(bon), bon
    for mauvais in ("parcours/../journal.json", "parcours/01-intro/autre.mp4", "parcours/x/video.mp4", "parcours/01-intro/images/a.png"):
        assert not CHEMIN_PARCOURS.match(mauvais), mauvais


def test_sous_titres_vtt_et_paragraphes():
    mots = [{"debut": i * 0.5, "fin": i * 0.5 + 0.4, "mot": (" Bonjour." if i == 3 else " mot")} for i in range(30)]
    cues = transcrire.sous_titres(mots)
    assert all(len(c["texte"]) <= transcrire.MAX_CARACTERES and c["fin"] - c["debut"] <= transcrire.MAX_DUREE_S + 0.5 for c in cues)
    assert cues[0]["texte"].endswith("Bonjour.")
    v = transcrire.vtt(cues, debut=5.0, fin=10.0)
    assert v.startswith("WEBVTT") and "00:00:00.000 -->" in v and "00:00:05.000 --> 00:00:10" not in v
    paras = transcrire.paragraphes(mots, 0, None)
    assert paras[0]["texte"].startswith("mot") and "".join(p["texte"] for p in paras).count("mot") == 29


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg absent")
def test_production_sans_transcription(tmp_path):
    prez = tmp_path / "prez"
    prez.mkdir()
    (prez / "slides.html").write_text(SLIDES, encoding="utf-8")
    (prez / "quiz.json").write_text(json.dumps([{"id": "q1", "type": "qcm", "question": "Question", "options": ["a", "b"], "bonne": 0},
                                                {"id": "q2", "type": "vrai_faux", "question": "Vrai ou faux", "bonne": True}]), encoding="utf-8")
    rep = prez / "replays" / "r1"
    rep.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:r=30:d=24", "-f", "lavfi", "-i", "sine=d=24",
                    "-c:v", "libx264", "-g", "30", "-c:a", "aac", "-shortest", str(rep / "replay.mp4")], check=True)
    chapitres = [{"debut": 0, "titre": "Bienvenue", "h": 0}, {"debut": 6, "titre": "Comprendre l’IA", "h": 2},
                 {"debut": 12, "titre": "Un outil", "h": 3}, {"debut": 18, "titre": "Les règles", "h": 4}, {"debut": 21, "titre": "Quiz : Vrai ou faux", "h": 5}]
    (rep / "journal.json").write_text(json.dumps({"titre": "Essai", "duree": 24.0, "chapitres": chapitres}), encoding="utf-8")
    p = parcours.produire_parcours(prez, rep, ["videos", "images", "textes", "questions", "page", "synopsis"])
    import openpyxl

    classeur = openpyxl.load_workbook(rep / "parcours" / "synopsis.xlsx")
    feuille = classeur["synopsis"]
    assert feuille["A6"].value == "Blocs de compétences" and feuille["F6"].value == "Description de l'activité"
    sequences_vues = {feuille.cell(row=r, column=2).value for r in range(7, feuille.max_row + 1)} - {None}
    assert sequences_vues == {"S1 – Introduction", "S2 – Comprendre l’IA", "S3 – Les règles"}
    types = {feuille.cell(row=r, column=9).value for r in range(7, feuille.max_row + 1)}
    assert {"Vidéo pédagogique (cours filmé, motion design, démonstration)", "Quiz / QCM / Vrai-Faux"} <= types
    assert "synthèse" in classeur.sheetnames
    assert [s["titre"] for s in p["sequences"]] == ["Introduction", "Comprendre l’IA", "Les règles"]
    s1, s2, s3 = p["sequences"]
    assert (rep / "parcours" / s2["video"]).is_file() and s2["essentiel"] and s2["images"]
    assert [q["id"] for q in s1["questions"]] == ["q1"] and [q["id"] for q in s3["questions"]] == ["q2"]
    assert (rep / "parcours" / "questions.gift").is_file() and (rep / "parcours" / "cours.html").is_file()
    page = (rep / "parcours" / "cours.html").read_text(encoding="utf-8")
    assert "{{PARCOURS_JSON}}" not in page and "Comprendre l’IA" in page
    assert "Un outil" in (rep / "parcours" / s2["essentiel"]).read_text(encoding="utf-8")

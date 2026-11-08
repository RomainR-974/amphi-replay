"""Replay interactif (docs/replay-interactif.md) : instant de la vidéo, chronologie, fabrication, paquet SCORM, cadrage."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from server import interactif
from server.interactif import chronologie, choisir_ancre, construire_replay_interactif, decalage_obs, paquet_scorm

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "postprod"))
sys.path.insert(0, str(RACINE / "tests"))

import cadrage  # noqa: E402
from test_serveur import creer_presentation  # noqa: E402

FFMPEG = shutil.which("ffmpeg") is not None


# -------------------------------------------------------------------------------------
# Instant de la vidéo dans le journal
# -------------------------------------------------------------------------------------


def test_decalage_lu_dans_le_journal_d_obs(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-11-09 10-00-00.txt").write_text(
        "10:15:00.100: ==== Recording Start ===\n"
        "10:15:00.120: [ffmpeg muxer: 'simple_file_output'] Writing file 'C:/V/2026-11-09 10-15-00.mkv'...\n"
        "10:15:01.904: [ffmpeg muxer: 'Enregistrement caméra (replay interactif)'] Writing file 'C:\\V/2026-11-09 10-15-00 camera.mkv'...\n",
        encoding="utf-8")
    assert decalage_obs(Path("x/2026-11-09 10-15-00 camera.mkv"), logs) == pytest.approx(1.784)
    assert decalage_obs(Path("x/2026-11-09 10-15-00.mkv"), logs) == 0.0  # l'enregistrement principal lui-même
    assert decalage_obs(Path("x/inconnu camera.mkv"), logs) is None
    assert decalage_obs(Path("x/a.mkv"), tmp_path / "absent") is None


def test_ancre_la_plus_proche_et_bornee():
    ev = [{"type": "enregistrement_debut", "horodatage": 1000.0}, {"type": "enregistrement_debut", "horodatage": 5000.0},
          {"type": "diapositive", "horodatage": 1001.0, "h": 1}]
    assert choisir_ancre(ev, 1003.5) == 1000.0
    assert choisir_ancre(ev, 4990.0) == 5000.0
    assert choisir_ancre(ev, 3000.0) is None  # trop loin de toute ancre
    assert choisir_ancre([], 1000.0) is None


def test_chronologie_etat_initial_puis_changements():
    ev = [
        {"type": "diapositive", "horodatage": 90.0, "h": 1, "v": 0, "f": -1},
        {"type": "diapositive", "horodatage": 99.0, "h": 2, "v": 0, "f": -1},   # avant la vidéo : état initial
        {"type": "quiz_ouvrir", "horodatage": 101.0, "question": "q1"},
        {"type": "diapositive", "horodatage": 105.5, "h": 2, "v": 0, "f": 0},
        {"type": "diapositive", "horodatage": 105.6, "h": 2, "v": 0, "f": 0},  # doublon
        {"type": "diapositive", "horodatage": 130.0, "h": 3, "v": 1, "f": None},
        {"type": "diapositive", "horodatage": 200.0, "h": 9, "v": 0, "f": -1},  # après la fin de la vidéo
    ]
    assert chronologie(ev, 100.0, 60.0) == [[0.0, 2, 0, -1], [5.5, 2, 0, 0], [30.0, 3, 1, -1]]
    assert chronologie([], 100.0, 60.0) == [[0.0, 0, 0, -1]]


# -------------------------------------------------------------------------------------
# Cadrage : lissage de la trajectoire
# -------------------------------------------------------------------------------------


def test_cadrage_zone_morte_trous_et_absence():
    tremble = [(0.5 + (0.01 if i % 2 else -0.01), 0.4, 0.2) for i in range(40)]
    lisse = cadrage.lisser(tremble)
    assert max(p[0] for p in lisse) - min(p[0] for p in lisse) < 0.005  # petits mouvements de tête : cadre immobile
    deplace = [(0.3, 0.4, 0.2)] * 30 + [(0.7, 0.4, 0.2)] * 30
    l2 = cadrage.lisser(deplace)
    assert l2[0][0] < 0.36 and l2[-1][0] > 0.64 and all(abs(b[0] - a[0]) < 0.08 for a, b in zip(l2, l2[1:]))  # suit, sans saut
    trous = [None, None, (0.6, 0.5, 0.25), None, None, (0.6, 0.5, 0.25)]
    assert all(abs(p[0] - 0.6) < 1e-6 for p in cadrage.lisser(trous))  # trous comblés par la position connue
    assert cadrage.lisser([None] * 5) == [(0.5, 0.42, 0.22)] * 5  # personne : plan centré
    assert cadrage.lisser([]) == []


# -------------------------------------------------------------------------------------
# Fabrication complète et paquet SCORM (ffmpeg)
# -------------------------------------------------------------------------------------


SLIDES = """<section data-id="s1"><h2>Accueil</h2></section>
<section data-quiz="q1" data-id="s2"></section>
<section class="libre" data-id="s3" data-interactif><div class="el texte" data-id="e1" style="left:96px;top:96px;width:800px;height:100px"><h2>Essai</h2></div></section>
"""
QUIZ = json.dumps([{"id": "q1", "type": "qcm", "question": "Combien ?", "options": ["un", "deux"], "bonne": 1},
                   {"id": "q2", "type": "nuage", "question": "Un mot ?"}])


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg requis")
def test_fabrication_et_paquet_scorm(tmp_path, monkeypatch):
    deck = creer_presentation(tmp_path / "prez", "demo", "Démo interactive", slides=SLIDES, quiz=QUIZ)
    (deck / "medias" / "image.png").write_bytes(b"\x89PNG")
    video = tmp_path / "2026-11-09 10-15-00 camera.mkv"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=8", "-f", "lavfi", "-i", "sine=f=220:d=8",
                    "-filter_complex", "[1]volume=0.05[a]", "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(video)],
                   check=True)
    debut = video.stat().st_mtime - 8.0
    journal = [
        {"type": "enregistrement_debut", "horodatage": debut - 1.2},
        {"type": "diapositive", "horodatage": debut - 30, "h": 0, "v": 0, "f": -1},
        {"type": "diapositive", "horodatage": debut + 2.0, "h": 1, "v": 0, "f": -1},
        {"type": "diapositive", "horodatage": debut + 5.0, "h": 2, "v": 0, "f": -1},
    ]
    vu = {}

    def lire(slug, depuis, jusqua):
        vu.update(slug=slug, depuis=depuis, jusqua=jusqua)
        return journal

    monkeypatch.setattr(interactif, "DOSSIER_LOGS_OBS", tmp_path / "pas-de-logs")
    sortie = tmp_path / "replay"
    d = construire_replay_interactif(video, "demo", sortie, deck=deck, decalage=1.2, voix="podcast", cadrage=False,
                                     encodeur="libx264", lire_journal=lire)
    assert vu["slug"] == "demo" and vu["depuis"] < debut < vu["jusqua"]
    assert d["source_debut"] == "ancre du pont OBS" and d["debut"] == pytest.approx(debut, abs=0.01)  # ancre + décalage
    assert [e[1] for e in d["timeline"]] == [0, 1, 2]
    assert d["evaluation"] == {"q1": {"id": "q1", "type": "qcm", "question": "Combien ?", "options": ["un", "deux"], "bonne": 1}}
    assert all("bonne" not in q for q in d["questions"])  # vue publique pour le runtime du deck
    for f in ("index.html", "donnees.js", "lecteur.js", "lecteur.css", "scorm.js", "quiz-deck.js", "teachable.js", "camera.js",
              "theme.css", "vendor/reveal/reveal.js", "vendor/reveal/theme/white.css", "medias/image.png", "video/camera.mp4"):
        assert (sortie / f).is_file(), f
    page = (sortie / "index.html").read_text(encoding="utf-8")
    assert "{{" not in page and "Démo interactive" in page
    donnees = (sortie / "donnees.js").read_text(encoding="utf-8")
    assert donnees.startswith("window.REPLAY_INTERACTIF = ") and json.loads(donnees[len("window.REPLAY_INTERACTIF = "):-2])["presentation"] == "demo"
    sonde = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,r_frame_rate", "-of", "json", str(sortie / "video" / "camera.mp4")],
                                      capture_output=True, text=True, check=True).stdout)["streams"]
    assert {s["codec_name"] for s in sonde} == {"h264", "aac"} and any(s["r_frame_rate"] == "30/1" for s in sonde)

    archive = paquet_scorm(sortie, tmp_path / "demo-interactif-scorm.zip")
    with zipfile.ZipFile(archive) as z:
        noms = set(z.namelist())
        manifeste = z.read("imsmanifest.xml").decode("utf-8")
    assert {"imsmanifest.xml", "index.html", "video/camera.mp4", "donnees.js"} <= noms and "interactif.json" not in noms
    assert 'href="index.html"' in manifeste and '<file href="video/camera.mp4"/>' in manifeste and d["identifiant"] in manifeste


def test_runtime_absent_signale(tmp_path):
    with pytest.raises(RuntimeError, match="fetch_reveal"):
        interactif.copier_runtime(tmp_path, tmp_path / "sortie", None, "")

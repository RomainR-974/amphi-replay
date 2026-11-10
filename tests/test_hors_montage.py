"""Diapositives « hors montage » (data-obs="hors-montage") : passages coupés du replay en post-production (docs/postprod.md §3)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from server.journal import diapositives_hors_montage, marqueurs_obs
from server.replays import chapitres_apres_coupes, duree_fichier, retirer_segments, segments_hors_montage, temps_apres_coupes
from test_serveur import JETON, client  # noqa: F401  (fixture)

FFMPEG = shutil.which("ffmpeg") is not None
SLIDES = """<section data-obs="demarrer"><h2>Début</h2></section>
<section data-obs="hors-montage"><h2>Pause</h2></section>
<section><h2>Cours</h2></section>
<section data-obs="hors-montage"><h2>Démo privée</h2></section>
<section data-obs="arreter"><h2>Fin</h2></section>"""


def ev(t, h):
    return {"type": "diapositive", "horodatage": 1000.0 + t, "h": h}


def test_lecture_des_marqueurs():
    assert diapositives_hors_montage(SLIDES) == [1, 3]
    assert marqueurs_obs(SLIDES) == {0: "demarrer", 4: "arreter"}  # le pont OBS ne voit pas « hors montage »


def test_segments_et_chapitres():
    evenements = [ev(0, 0), ev(30, 1), ev(40, 1), ev(90, 2), ev(200, 3), ev(260, 4), {"type": "quiz_ouvrir", "horodatage": 1100.0}]
    segments = segments_hors_montage(evenements, 1000.0, 300.0, [1, 3])
    assert segments == [[30.0, 90.0], [200.0, 260.0]]
    assert segments_hors_montage(evenements, 1000.0, 300.0, []) == []
    assert segments_hors_montage([ev(0, 0), ev(250, 1)], 1000.0, 300.0, [1]) == [[250.0, 300.0]]  # jusqu'à la fin de la vidéo
    assert temps_apres_coupes(100.0, segments) == 40.0 and temps_apres_coupes(50.0, segments) is None
    chapitres = [{"debut": 0.0, "titre": "Début", "h": 0}, {"debut": 30.0, "titre": "Pause", "h": 1}, {"debut": 90.0, "titre": "Cours", "h": 2},
                 {"debut": 200.0, "titre": "Démo privée", "h": 3}, {"debut": 260.0, "titre": "Fin", "h": 4}]
    assert [(c["titre"], c["debut"]) for c in chapitres_apres_coupes(chapitres, segments)] == [("Début", 0.0), ("Cours", 30.0), ("Fin", 140.0)]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg absent")
def test_coupe_reelle(tmp_path):
    video = tmp_path / "replay.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:r=30:d=12", "-f", "lavfi", "-i", "sine=d=12",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video)], check=True)
    retirer_segments(video, [[4.0, 7.0]])
    assert abs(duree_fichier(video) - 9.0) < 0.3


def test_api_seances_donne_les_diapositives_hors_montage(client):  # noqa: F811
    (client.deck / "slides.html").write_text(SLIDES, encoding="utf-8")
    r = client.get(f"/api/journal/seances?prez=demo&token={JETON}").json()
    assert r["hors_montage"] == [1, 3]

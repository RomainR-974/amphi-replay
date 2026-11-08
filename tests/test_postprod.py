"""Tests du journal de séance, du chapitrage et de la post-production (docs/postprod.md)."""

from __future__ import annotations

import json
import shutil
import subprocess
import time

import numpy as np
import pytest

from server import journal as module_journal
from server import replays as module_replays
from tests.test_assembler import ecrire_wav
from tests.test_serveur import JETON, creer_presentation  # noqa: F401
from tests.test_serveur import client  # noqa: F401

SLIDES_TITRES = """<!-- test -->
<section class="libre" data-id="s1" data-qr><div class="el texte" data-id="e1" style="left:0;top:0;font-size:84px">  Bienvenue &amp; <b>bonjour</b> </div>
  <div class="el texte" data-id="e2" style="font-size:20px">Pied de page</div>
  <aside class="notes"><p>Ne pas lire</p></aside></section>
<section data-id="s2"><h2 class="dsfr-titre">Le <em>cadre</em><br>réglementaire</h2><p>texte</p></section>
<section data-quiz="q1" data-id="s3"></section>
<section data-id="s4"><section><h3>Verticale A</h3></section><section><h3>Verticale B</h3></section></section>
<section data-id="s5"><img src="medias/x.png"></section>
<section data-id="s6"><div class="el texte">""" + "x" * 120 + """ fin</div></section>
"""


def jeton(chemin: str) -> str:
    return f"{chemin}{'&' if '?' in chemin else '?'}token={JETON}"


# ---------------------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------------------


def test_journal_alimente_par_remote_et_quiz(client):
    with client.websocket_connect(f"/ws/deck?role=remote&token={JETON}") as remote:
        remote.receive_json()
        remote.send_json({"type": "slide", "h": 1, "v": 0, "f": -1})
        remote.send_json({"type": "slide", "h": 1, "v": 0, "f": -1})  # doublon : ignoré
        remote.send_json({"type": "slide", "h": 1, "v": 0, "f": 0})  # fragment : une ligne
        remote.send_json({"type": "slide", "h": 2, "v": 0, "f": -1})
        remote.send_json({"type": "ping"})
        assert remote.receive_json()["type"] == "pong"  # garantit le traitement des messages précédents
    # Une scène ne peut pas écrire dans le journal.
    with client.websocket_connect("/ws/deck?role=scene") as scene:
        scene.receive_json()
        scene.send_json({"type": "slide", "h": 7, "v": 0, "f": -1})
        scene.send_json({"type": "ping"})
        scene.receive_json()
    assert client.post(jeton("/api/quiz/ouvrir"), json={"question_id": "q1"}).status_code == 200
    assert client.post(jeton("/api/quiz/reveler"), json={"question_id": "q1"}).status_code == 200
    assert client.post(jeton("/api/quiz/fermer")).status_code == 200  # rien d'ouvert : pas de ligne

    assert client.get("/api/journal?prez=demo").status_code == 403
    assert client.get(jeton("/api/journal?prez=inconnue")).status_code == 404
    r = client.get(jeton("/api/journal?prez=demo"))
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*"
    evenements = r.json()
    assert [(e["type"], e["h"], e["f"], e["question"]) for e in evenements] == [
        ("diapositive", 1, -1, None), ("diapositive", 1, 0, None), ("diapositive", 2, -1, None), ("quiz_ouvrir", None, None, "q1"), ("quiz_reveler", None, None, "q1"),
    ]
    assert all(abs(e["horodatage"] - time.time()) < 60 for e in evenements)
    # Bornes depuis / jusqua.
    milieu = evenements[2]["horodatage"]
    assert len(client.get(jeton(f"/api/journal?prez=demo&depuis={milieu}")).json()) == 3
    assert len(client.get(jeton(f"/api/journal?prez=demo&jusqua={milieu}")).json()) == 3
    # Préflight CORS.
    o = client.options("/api/journal")
    assert o.status_code == 204 and "Access-Control-Allow-Headers" in o.headers and o.headers["access-control-allow-origin"] == "*"
    # L'activation est journalisée sur la présentation activée ; la purge du quiz ne touche pas au journal.
    creer_presentation(client.presentations, "autre", "Autre")
    assert client.post(jeton("/api/presentations/autre/activer")).status_code == 200
    assert [e["type"] for e in client.get(jeton("/api/journal?prez=autre")).json()] == ["activation"]
    assert client.post(jeton("/api/quiz/purger")).status_code == 200
    assert len(client.get(jeton("/api/journal?prez=demo")).json()) == 5


def test_journal_seances_regroupe(client):
    j = client.app.state.journal
    t0 = time.time() - 6 * 3600
    for i in range(3):
        j.ajouter("demo", "diapositive", i, 0, -1, horodatage=t0 + i * 60)
    j.ajouter("demo", "quiz_ouvrir", question="q1", horodatage=t0 + 200)
    t1 = t0 + 4 * 3600
    for i in range(2):
        j.ajouter("demo", "diapositive", i, 0, -1, horodatage=t1 + i * 30)
    j.ajouter("demo", "diapositive", 1, 0, -1, horodatage=t1 + 90)  # doublon consécutif : ignoré
    assert client.get("/api/journal/seances?prez=demo").status_code == 403
    r = client.get(jeton("/api/journal/seances?prez=demo"))
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*"
    corps = r.json()
    assert corps["prez"] == "demo" and corps["titres"] == ["Démo", "Quiz : a"]
    seances = corps["seances"]
    assert len(seances) == 2
    # La plus récente d'abord.
    assert seances[0]["debut"] == pytest.approx(t1) and seances[0]["fin"] == pytest.approx(t1 + 30)
    assert seances[0]["diapositives"] == 2 and len(seances[0]["evenements"]) == 2
    assert seances[1]["debut"] == pytest.approx(t0) and seances[1]["fin"] == pytest.approx(t0 + 200)
    assert seances[1]["diapositives"] == 3 and len(seances[1]["evenements"]) == 4


def test_titres_diapositives():
    titres = module_journal.titres_diapositives(SLIDES_TITRES, {"q1": "Quelle est la réponse ?"})
    assert titres[0] == "Bienvenue & bonjour"
    assert titres[1] == "Le cadre réglementaire"
    assert titres[2] == "Quiz : Quelle est la réponse ?"
    assert titres[3] == "Verticale A"  # pile verticale : une seule entrée `h`
    assert titres[4] == "Diapositive 5"
    assert len(titres[5]) <= 80 and titres[5].endswith("…")
    assert len(titres) == 6
    assert module_journal.titres_diapositives("", {}) == []


# ---------------------------------------------------------------------------------------
# Chapitrage
# ---------------------------------------------------------------------------------------


def test_calcul_chapitres_fusion_et_premier_a_zero():
    debut = 1_000_000.0
    titres = ["Intro", "Cadre", "Quiz : Q", "Principes", "Fin"]
    ev = lambda t, h, f=-1: {"horodatage": debut + t, "type": "diapositive", "h": h, "v": 0, "f": f}  # noqa: E731
    evenements = [
        ev(-30, 0),             # avant l'enregistrement : filtré
        ev(5, 0),               # → ramené à 00:00
        ev(12, 1),              # < 20 s après le précédent : fusionné (titre « Cadre » l'emporte)
        ev(40, 1, 0),           # fragment : pas de chapitre
        ev(60, 2),
        {"horodatage": debut + 70, "type": "quiz_ouvrir", "question": "q", "h": None, "v": None, "f": None},
        ev(100, 3),
        ev(400, 4),             # après la fin : filtré
    ]
    chapitres = module_replays.calculer_chapitres(evenements, debut, 300.0, titres)
    assert [(c["debut"], c["titre"]) for c in chapitres] == [(0.0, "Cadre"), (60.0, "Quiz : Q"), (100.0, "Principes")]
    # Décalage manuel : +50 s avance tous les instants ; le chapitre à 60 passe à 10 et fusionne avec le premier.
    chapitres = module_replays.calculer_chapitres(evenements, debut, 300.0, titres, decalage=50.0)
    assert [(c["debut"], c["titre"]) for c in chapitres] == [(0.0, "Quiz : Q"), (50.0, "Principes")]
    # Indice hors liste : titre de repli.
    assert module_replays.calculer_chapitres([ev(10, 9)], debut, 300.0, titres)[0]["titre"] == "Diapositive 10"
    assert module_replays.calculer_chapitres([], debut, 300.0, titres) == []

    assert module_replays.formater_temps(0) == "00:00"
    assert module_replays.formater_temps(65) == "01:05"
    assert module_replays.formater_temps(3725) == "1:02:05"
    txt = module_replays.texte_chapitres([{"debut": 0.0, "titre": "Intro"}, {"debut": 3725.4, "titre": "Fin"}])
    assert txt == "00:00 Intro\n1:02:05 Fin\n"
    vtt = module_replays.vtt_chapitres([{"debut": 0.0, "titre": "Intro"}, {"debut": 65.5, "titre": "Fin"}], 120.0)
    assert vtt.startswith("WEBVTT\n\n1\n00:00:00.000 --> 00:01:05.500\nIntro\n\n2\n00:01:05.500 --> 00:02:00.000\nFin")
    meta = module_replays.ffmetadata([{"debut": 0.0, "titre": "A = B; C"}], 10.0, "Titre")
    assert meta.startswith(";FFMETADATA1\ntitle=Titre\n") and "title=A \\= B\\; C" in meta and "START=0\nEND=10000" in meta
    assert module_replays.slugifier("Quiz : L'IA générative, été 2026 !") == "quiz-l-ia-generative-ete-2026"


# ---------------------------------------------------------------------------------------
# Cycle complet avec ffmpeg
# ---------------------------------------------------------------------------------------


def fabriquer_captation(dossier, duree=30.0, freq=48000):
    """Vidéo noire `duree` s avec deux pistes audio (comme OBS) et un wav « téléphone » de la même scène, démarré 0,8 s plus tôt."""
    rng = np.random.default_rng(3)
    reference = rng.normal(0, 0.05, int(duree * freq))
    for pos in (1.5, duree - 2.0):
        i = int(pos * freq)
        reference[i : i + freq // 50] += 0.9
    avance = 0.8
    telephone = np.concatenate([rng.normal(0, 0.05, int(avance * freq)), reference])
    ref_wav, tel_wav, obs = dossier / "ref.wav", dossier / "telephone.wav", dossier / "obs.mkv"
    ecrire_wav(ref_wav, reference, freq)
    ecrire_wav(tel_wav, telephone, freq)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s=320x180:r=30:d={duree}",
         "-i", str(ref_wav), "-i", str(ref_wav), "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-c:a", "aac", "-shortest", str(obs)],
        check=True,
    )
    ref_wav.unlink()
    return obs, tel_wav


def attendre_tache(client, id, delai=120.0):
    fin = time.time() + delai
    while time.time() < fin:
        t = client.get(jeton(f"/api/postprod/taches/{id}")).json()
        if t["etat"] != "en_cours":
            return t
        time.sleep(0.5)
    raise AssertionError("tâche trop longue")


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg absent")
def test_cycle_complet_postprod(client, tmp_path, monkeypatch):
    import os

    captations = tmp_path / "captations"
    captations.mkdir()
    # Le serveur est déjà démarré : on redirige son objet Captations vers le dossier temporaire.
    from server import app as module_app

    module_app.captations.dossier = captations
    obs, tel = fabriquer_captation(captations, duree=30.0)

    # Séance : la vidéo « a été enregistrée » de t0−5 à t0+25 ; diapositives à t0, t0+10 (fusionnée), t0+21.
    t0 = time.time() - 120
    os.utime(obs, (t0 + 25, t0 + 25))
    j = client.app.state.journal
    j.ajouter("demo", "diapositive", 0, 0, -1, horodatage=t0)
    j.ajouter("demo", "diapositive", 1, 0, -1, horodatage=t0 + 2)
    j.ajouter("demo", "diapositive", 0, 0, -1, horodatage=t0 + 10)
    j.ajouter("demo", "diapositive", 1, 0, -1, horodatage=t0 + 21)

    r = client.get(jeton("/api/postprod/captations"))
    assert r.status_code == 200
    fichiers = {f["nom"]: f for f in r.json()}
    assert fichiers["obs.mkv"]["genre"] == "video" and fichiers["obs.mkv"]["duree"] == pytest.approx(30.0, abs=0.5)
    assert fichiers["telephone.wav"]["genre"] == "audio"
    assert client.get(jeton("/api/postprod/configuration")).json()["ffmpeg"] is True

    # Refus : fichier hors dossier, jeton absent.
    assert client.post("/api/postprod/assembler", json={"prez": "demo", "video": "obs.mkv"}).status_code == 403
    assert client.post(jeton("/api/postprod/assembler"), json={"prez": "demo", "video": "../obs.mkv"}).status_code == 422
    assert client.post(jeton("/api/postprod/assembler"), json={"prez": "demo", "video": "absent.mkv"}).status_code == 422
    assert client.post(jeton("/api/postprod/assembler"), json={"prez": "zzz", "video": "obs.mkv"}).status_code == 404

    seance = client.get(jeton("/api/journal/seances?prez=demo")).json()["seances"][0]
    r = client.post(jeton("/api/postprod/assembler"), json={
        "prez": "demo", "video": "obs.mkv", "audio": "telephone.wav", "seance_debut": seance["debut"], "seance_fin": seance["fin"],
        "titre": "Séance test", "extraits": True,
    })
    assert r.status_code == 200, r.text
    tache = attendre_tache(client, r.json()["tache"])
    assert tache["etat"] == "terminee", "\n".join(tache["journal"])
    assert [e["etat"] for e in tache["etapes"]] == ["terminee"] * 5  # préparation, assemblage, coupe (sans effet ici), chapitrage, extraits
    assert "pas de questions-réponses dans la séance" in "\n".join(tache["journal"])
    assert tache["replay"]["slug"] == "demo"
    id = tache["replay"]["id"]

    liste = client.get("/api/presentations/demo/replays").json()
    assert len(liste) == 1 and liste[0]["id"] == id and liste[0]["titre"] == "Séance test"
    assert liste[0]["duree"] == pytest.approx(30.0, abs=1.0) and liste[0]["chapitres"] == 2 and liste[0]["taille"] > 0
    detail = client.get(f"/api/presentations/demo/replays/{id}").json()
    # t0 → 5 s (ramené à 0, titre de t0+2 qui fusionne : « Quiz : a »), t0+10 → 15 s (< 20 s : fusionné), t0+21 → 26 s.
    assert [c["titre"] for c in detail["chapitres"]] == ["Démo", "Quiz : a"]
    assert detail["chapitres"][0]["debut"] == 0.0 and detail["chapitres"][1]["debut"] == pytest.approx(26.0, abs=0.2)
    assert detail["journal"]["mesures"]["decalage_debut_s"] == pytest.approx(0.8, abs=0.05)
    assert detail["journal"]["source_video"] == "obs.mkv" and detail["journal"]["coupe_qr_s"] is None

    dossier = client.presentations / "demo" / "replays" / id
    assert (dossier / "replay.mp4").is_file() and (dossier / "journal.json").is_file()
    assert (dossier / "chapitres.txt").read_text(encoding="utf-8") == "00:00 Démo\n00:26 Quiz : a\n"
    vtt = client.get(f"/p/demo/replays/{id}/chapitres.vtt")
    assert vtt.status_code == 200 and vtt.headers["content-type"].startswith("text/vtt") and vtt.text.count("-->") == 2
    assert client.get(f"/p/demo/replays/{id}/chapitres.txt").status_code == 200
    assert client.get(f"/p/demo/replays/{id}/journal.json").json()["titre"] == "Séance test"
    # Chapitres embarqués dans le MP4.
    sonde = module_replays.sonder(dossier / "replay.mp4")
    assert [c["titre"] for c in sonde["chapitres"]] == ["Démo", "Quiz : a"] and sonde["audio"] == 1

    # Lecture partielle (Range) du MP4.
    r = client.get(f"/p/demo/replays/{id}/replay.mp4", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100 and r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-range"].startswith("bytes 0-99/")
    assert client.get(f"/p/demo/replays/{id}/replay.mp4").headers["content-type"] == "video/mp4"

    # Extraits : un par chapitre, ≥ 1 s.
    extraits = detail["extraits"]
    assert [e["nom"] for e in extraits] == ["01-demo.mp4", "02-quiz-a.mp4"]
    r = client.get(f"/p/demo/replays/{id}/extraits/02-quiz-a.mp4")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    assert module_replays.sonder(dossier / "extraits" / "02-quiz-a.mp4")["duree"] == pytest.approx(4.0, abs=1.5)
    # Anti-traversée et noms inconnus.
    for chemin in ("..%2F..%2Fslides.html", "extraits%2F..%2Fjournal.json", "extraits/..%2Fjournal.json", "extraits/x.txt", "autre.mp4", "%2e%2e/journal.json"):
        assert client.get(f"/p/demo/replays/{id}/{chemin}").status_code == 404, chemin
    assert client.get("/p/demo/replays/../replays/journal.json").status_code == 404

    # Renommage, découpe a posteriori, suppression.
    assert client.put(f"/api/presentations/demo/replays/{id}", json={"titre": "X"}).status_code == 403
    r = client.put(jeton(f"/api/presentations/demo/replays/{id}"), json={"titre": "  Nouveau titre  "})
    assert r.status_code == 200 and r.json()["titre"] == "Nouveau titre"
    assert json.loads((dossier / "journal.json").read_text(encoding="utf-8"))["titre"] == "Nouveau titre"
    assert client.put(jeton(f"/api/presentations/demo/replays/{id}"), json={"titre": "  "}).status_code == 422
    shutil.rmtree(dossier / "extraits")
    r = client.post(jeton(f"/api/presentations/demo/replays/{id}/extraits"), json={"chapitres": [1]})
    assert r.status_code == 200
    t = attendre_tache(client, r.json()["tache"])
    assert t["etat"] == "terminee" and t["replay"]["extraits"] == ["02-quiz-a.mp4"]
    assert client.get("/api/presentations/demo/replays").json()[0]["extraits"] == 1

    # Un dossier déposé à la main (replay.mp4 seul) : journal reconstitué par ffprobe, chapitres MP4 relus.
    manuel = client.presentations / "demo" / "replays" / "2026-01-01_0900"
    manuel.mkdir()
    shutil.copy(dossier / "replay.mp4", manuel / "replay.mp4")
    liste = client.get("/api/presentations/demo/replays").json()
    assert [x["id"] for x in liste] == [id, "2026-01-01_0900"] if id > "2026-01-01_0900" else ["2026-01-01_0900", id]
    reconstitue = next(x for x in liste if x["id"] == "2026-01-01_0900")
    assert reconstitue["chapitres"] == 2 and reconstitue["duree"] == pytest.approx(30.0, abs=1.0)
    assert (manuel / "journal.json").is_file() and (manuel / "chapitres.vtt").is_file()
    assert json.loads((manuel / "journal.json").read_text(encoding="utf-8"))["reconstitue"] is True

    assert client.delete(f"/api/presentations/demo/replays/{id}").status_code == 403
    assert client.delete(jeton(f"/api/presentations/demo/replays/{id}")).status_code == 200
    assert not dossier.exists()
    assert client.delete(jeton(f"/api/presentations/demo/replays/{id}")).status_code == 404
    assert client.delete(jeton("/api/presentations/demo/replays/%2e%2e")).status_code == 404
    assert [x["id"] for x in client.get("/api/presentations/demo/replays").json()] == ["2026-01-01_0900"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg absent")
def test_assemblage_sans_audio_et_erreurs(client, tmp_path):
    from server import app as module_app

    captations = tmp_path / "captations"
    captations.mkdir()
    module_app.captations.dossier = captations
    obs, _ = fabriquer_captation(captations, duree=12.0)
    r = client.post(jeton("/api/postprod/assembler"), json={"prez": "demo", "video": "obs.mkv", "garder_reference": True})
    assert r.status_code == 200
    t = attendre_tache(client, r.json()["tache"])
    assert t["etat"] == "terminee", "\n".join(t["journal"])
    id = t["replay"]["id"]
    sonde = module_replays.sonder(client.presentations / "demo" / "replays" / id / "replay.mp4")
    assert sonde["audio"] == 2 and sonde["duree"] == pytest.approx(12.0, abs=0.5)
    assert client.get("/api/presentations/demo/replays").json()[0]["chapitres"] == 0
    assert client.get(f"/p/demo/replays/{id}/chapitres.txt").text == ""
    # Serveur de séance injoignable : erreur lisible, aucun dossier laissé derrière.
    r = client.post(jeton("/api/postprod/assembler"), json={"prez": "demo", "video": "obs.mkv", "serveur": "http://127.0.0.1:9", "jeton_serveur": "x"})
    t = attendre_tache(client, r.json()["tache"])
    assert t["etat"] == "erreur" and "injoignable" in t["erreur"]
    assert len(client.get("/api/presentations/demo/replays").json()) == 1
    assert client.get(jeton("/api/postprod/taches/inconnue")).status_code == 404


def test_instant_qr():
    debut = 1_000.0
    ev = [{"horodatage": debut + 20, "type": "qr_ouvrir", "h": 3, "v": 0, "f": -1}, {"horodatage": debut + 25, "type": "qr_ouvrir"}]
    assert module_replays.instant_qr(ev, debut) == 20.0
    assert module_replays.instant_qr(ev, debut, decalage=5.0) == 15.0
    assert module_replays.instant_qr([{"horodatage": debut, "type": "diapositive", "h": 0}], debut) is None


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg absent")
def test_coupe_avant_questions_reponses(client, tmp_path):
    """Séance avec `qr_ouvrir` à 20 s dans une vidéo de 30 s → replay d'environ 20 s, chapitres bornés, `coupe_qr_s` dans journal.json."""
    import os

    from server import app as module_app

    captations = tmp_path / "captations"
    captations.mkdir()
    module_app.captations.dossier = captations
    obs, _ = fabriquer_captation(captations, duree=30.0)
    t0 = time.time() - 300
    os.utime(obs, (t0 + 30, t0 + 30))  # enregistrée de t0 à t0+30
    j = client.app.state.journal
    j.ajouter("demo", "diapositive", 0, 0, -1, horodatage=t0 + 1)
    j.ajouter("demo", "qr_ouvrir", 0, 0, -1, horodatage=t0 + 20)
    j.ajouter("demo", "diapositive", 1, 0, -1, horodatage=t0 + 22)  # saut vers la question : après la coupe, pas de chapitre
    j.ajouter("demo", "qr_fermer", 0, 0, -1, horodatage=t0 + 28)

    seance = client.get(jeton("/api/journal/seances?prez=demo")).json()["seances"][0]
    assert [e["type"] for e in seance["evenements"]] == ["diapositive", "qr_ouvrir", "diapositive", "qr_fermer"]
    assert seance["evenements"][1]["horodatage"] == pytest.approx(t0 + 20)

    r = client.post(jeton("/api/postprod/assembler"), json={"prez": "demo", "video": "obs.mkv", "seance_debut": seance["debut"], "seance_fin": seance["fin"], "titre": "Coupé"})
    assert r.status_code == 200, r.text
    tache = attendre_tache(client, r.json()["tache"])
    assert tache["etat"] == "terminee", "\n".join(tache["journal"])
    assert [e["nom"] for e in tache["etapes"]] == ["préparation", "assemblage", "coupe", "chapitrage"]
    assert any("coupé avant les questions-réponses à 00:20" in l for l in tache["journal"])
    id = tache["replay"]["id"]
    dossier = client.presentations / "demo" / "replays" / id
    sonde = module_replays.sonder(dossier / "replay.mp4")
    assert sonde["duree"] == pytest.approx(20.0, abs=1.0)
    journal_replay = json.loads((dossier / "journal.json").read_text(encoding="utf-8"))
    assert journal_replay["coupe_qr_s"] == pytest.approx(20.0, abs=0.1) and journal_replay["duree"] == pytest.approx(20.0, abs=1.0)
    assert [c["titre"] for c in journal_replay["chapitres"]] == ["Démo"]
    assert not (dossier / "questions.csv").exists()  # pas de serveur de séance distant

    # Sans coupe : vidéo complète, chapitre du saut conservé.
    r = client.post(jeton("/api/postprod/assembler"), json={"prez": "demo", "video": "obs.mkv", "seance_debut": seance["debut"], "seance_fin": seance["fin"], "couper_qr": False})
    tache = attendre_tache(client, r.json()["tache"])
    assert tache["etat"] == "terminee" and [e["nom"] for e in tache["etapes"]] == ["préparation", "assemblage", "chapitrage"]
    detail = client.get(f"/api/presentations/demo/replays/{tache['replay']['id']}").json()
    assert detail["duree"] == pytest.approx(30.0, abs=1.0) and len(detail["chapitres"]) == 2 and detail["journal"]["coupe_qr_s"] is None

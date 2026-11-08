"""Replay interactif, étape 1 (docs/seance.md §E) : ancre de début d'enregistrement et copie figée de la présentation."""

from __future__ import annotations

import io
import sys
import time
import zipfile
from pathlib import Path

from test_serveur import JETON, client  # noqa: F401  (fixture)

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "obs"))

from obs_quiz_bridge import SuiviEnregistrement  # noqa: E402


def signaler(client, actif=True, duree_ms=0, jeton=JETON):  # noqa: F811
    """Envoie un signal d'enregistrement comme le pont OBS ; renvoie la réponse (ou None sans jeton)."""
    url = "/ws/quiz" + (f"?token={jeton}" if jeton else "")
    with client.websocket_connect(url) as ws:
        assert ws.receive_json()["type"] == "quiz"
        ws.send_json({"type": "enregistrement", "actif": actif, "duree_ms": duree_ms})
        ws.send_json({"type": "ping"})
        reponse = ws.receive_json()
        if reponse["type"] == "pong":
            return None
        assert ws.receive_json()["type"] == "pong"
        return reponse


def journal(client, types=("enregistrement_debut", "enregistrement_fin")):  # noqa: F811
    r = client.get(f"/api/journal?prez=demo&token={JETON}")
    assert r.status_code == 200
    return [e for e in r.json() if e["type"] in types]


def test_suivi_enregistrement_detecte_debuts_et_fins():
    s = SuiviEnregistrement()
    assert s.observer(False, 0) is None
    assert s.observer(True, 300) == "debut"
    assert s.observer(True, 1300) is None
    assert s.observer(False, 0) == "fin"
    assert s.observer(False, 0) is None
    # déjà en cours au lancement du pont (F9 avant) : signalé comme début, avec la durée déjà enregistrée
    s2 = SuiviEnregistrement()
    assert s2.observer(True, 60_000) == "debut"
    # arrêt puis relance entre deux lectures : la durée recule → nouveau début
    assert s2.observer(True, 61_000) is None
    assert s2.observer(True, 400) == "debut"


def test_ancre_datee_par_le_serveur_et_presentation_figee(client):  # noqa: F811
    avant = time.time()
    r = signaler(client, True, duree_ms=5000)
    assert r["ok"] and r["actif"] and r["presentation"] == "demo"
    assert avant - 5.5 <= r["debut"] <= time.time() - 4.5  # début = maintenant - durée déjà enregistrée
    debuts = journal(client)
    assert [e["type"] for e in debuts] == ["enregistrement_debut"]
    assert abs(debuts[0]["horodatage"] - r["debut"]) < 1e-6

    copie = client.deck / "seances" / r["seance"]
    assert (copie / "slides.html").read_text(encoding="utf-8") == (client.deck / "slides.html").read_text(encoding="utf-8")
    assert (copie / "quiz.json").is_file() and (copie / "presentation.json").is_file() and (copie / "seance.json").is_file()

    # la présentation modifiée ensuite : la copie figée ne bouge pas
    (client.deck / "slides.html").write_text("<section>modifiée</section>", encoding="utf-8")
    assert "modifiée" not in (copie / "slides.html").read_text(encoding="utf-8")

    # fin d'enregistrement
    assert signaler(client, False)["actif"] is False
    assert [e["type"] for e in journal(client)] == ["enregistrement_debut", "enregistrement_fin"]


def test_ancre_renvoyee_apres_reconnexion_non_dupliquee(client):  # noqa: F811
    premier = signaler(client, True, duree_ms=2000)
    second = signaler(client, True, duree_ms=2400)  # le pont s'est reconnecté 0,4 s plus tard : même enregistrement
    assert second["seance"] == premier["seance"]
    assert len(journal(client, ("enregistrement_debut",))) == 1


def test_signal_ignore_sans_jeton(client):  # noqa: F811
    assert signaler(client, True, jeton="") is None
    assert signaler(client, True, jeton="faux") is None
    assert journal(client) == []
    assert not (client.deck / "seances").exists()


def test_routes_des_copies_figees(client):  # noqa: F811
    (client.deck / "medias" / "image.png").write_bytes(b"\x89PNG test")
    id_seance = signaler(client, True)["seance"]

    assert client.get("/api/presentations/demo/seances").status_code == 403
    liste = client.get(f"/api/presentations/demo/seances?token={JETON}").json()
    assert [s["id"] for s in liste] == [id_seance] and liste[0]["taille"] > 0

    r = client.get(f"/api/presentations/demo/seances/{id_seance}.zip?token={JETON}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    noms = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {"slides.html", "quiz.json", "presentation.json", "seance.json", "medias/image.png"} <= noms

    assert client.get(f"/api/presentations/demo/seances/{id_seance}.zip").status_code == 403
    assert client.get(f"/api/presentations/demo/seances/..%2F..%2Fdata.zip?token={JETON}").status_code == 404
    assert client.get(f"/api/presentations/demo/seances/2000-01-01_000000.zip?token={JETON}").status_code == 404


def test_boucle_du_pont_envoie_debut_et_fin(monkeypatch):
    """La tâche du pont lit GetRecordStatus chaque période et n'envoie que les transitions."""
    import asyncio
    import json as _json

    import obs_quiz_bridge as pont

    lectures = iter([(False, 0), (True, 800), (True, 1800), (False, 0)])

    class FauxObs:
        async def etat_enregistrement(self):
            try:
                return next(lectures)
            except StopIteration:
                raise asyncio.CancelledError

    class FauxServeur:
        def __init__(self):
            self.envois = []

        async def send(self, texte):
            self.envois.append(_json.loads(texte))

    monkeypatch.setattr(pont, "PERIODE_ANCRE_S", 0)
    serveur = FauxServeur()
    try:
        asyncio.run(pont.suivre_enregistrement(FauxObs(), serveur))
    except asyncio.CancelledError:
        pass
    assert serveur.envois == [
        {"type": "enregistrement", "actif": True, "duree_ms": 800.0},
        {"type": "enregistrement", "actif": False, "duree_ms": 0.0},
    ]

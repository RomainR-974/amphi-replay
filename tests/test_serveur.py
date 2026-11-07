"""Tests du serveur : synchronisation du deck, quiz, protection par jeton."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RACINE = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPHI_JETON", "secret-test")
    monkeypatch.setenv("AMPHI_BDD", str(tmp_path / "quiz.sqlite"))
    monkeypatch.setenv("AMPHI_URL_PUBLIQUE", "https://q.test")
    # Recharger config et app avec cet environnement.
    import importlib

    from server import config

    importlib.reload(config)
    from server import app as module_app

    importlib.reload(module_app)
    with TestClient(module_app.app) as c:
        yield c


def test_etat_initial(client):
    r = client.get("/etat")
    assert r.status_code == 200
    assert r.json()["h"] == 0 and r.json()["f"] == -1


def test_sync_remote_vers_scene_et_obs_avec_acks(client):
    with client.websocket_connect("/ws/deck?role=scene") as scene, \
         client.websocket_connect("/ws/deck?role=obs") as obs, \
         client.websocket_connect("/ws/deck?role=remote&token=secret-test") as remote:
        for ws in (scene, obs, remote):
            assert ws.receive_json()["type"] == "slide"  # état initial

        remote.send_json({"type": "slide", "h": 3, "v": 0, "f": 1})
        for ws in (scene, obs):
            m = ws.receive_json()
            assert (m["type"], m["h"], m["v"], m["f"]) == ("slide", 3, 0, 1)
            ws.send_json({"type": "ack", "h": 3, "v": 0, "f": 1})

        roles = {remote.receive_json()["role"], remote.receive_json()["role"]}
        assert roles == {"scene", "obs"}

    etat = client.get("/etat").json()
    assert (etat["h"], etat["f"]) == (3, 1)
    assert set(etat["acks"]) == {"scene", "obs"}


def test_remote_sans_jeton_refuse(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/deck?role=remote&token=faux") as ws:
            ws.receive_json()


def test_scene_ne_peut_pas_commander(client):
    with client.websocket_connect("/ws/deck?role=scene") as scene:
        scene.receive_json()
        scene.send_json({"type": "slide", "h": 9, "v": 0, "f": -1})
    assert client.get("/etat").json()["h"] == 0


def test_quiz_cycle_complet(client):
    questions = client.get("/api/quiz/questions").json()
    assert questions and questions[0]["id"] == "q1"

    # Fermée : vote refusé.
    r = client.post("/api/quiz/vote", json={"question_id": "q1", "option": 0})
    assert r.status_code == 409

    # Admin sans jeton refusé.
    assert client.post("/api/quiz/ouvrir", json={"question_id": "q1"}).status_code == 403
    assert client.post("/api/quiz/ouvrir?token=secret-test", json={"question_id": "q1"}).status_code == 200

    with client.websocket_connect("/ws/quiz") as resultats:
        assert resultats.receive_json()["ouverte"] is True

        # Deux participants distincts (cookies séparés), l'un change d'avis.
        a = TestClient(client.app)
        b = TestClient(client.app)
        assert a.post("/api/quiz/vote", json={"question_id": "q1", "option": 0}).status_code == 200
        assert b.post("/api/quiz/vote", json={"question_id": "q1", "option": 1}).status_code == 200
        assert a.post("/api/quiz/vote", json={"question_id": "q1", "option": 1}).status_code == 200
        assert a.get("/api/quiz/etat").json()["ma_reponse"] == 1

        etat = client.get("/api/quiz/etat").json()
        assert etat["total"] == 2 and etat["comptes"] == [0, 2, 0, 0]

        # Les votes sont poussés par WebSocket.
        pousse = None
        for _ in range(3):
            pousse = resultats.receive_json()
        assert pousse["total"] == 2

    assert client.post("/api/quiz/vote", json={"question_id": "q1", "option": 7}).status_code == 422

    client.post("/api/quiz/fermer?token=secret-test")
    assert client.get("/api/quiz/etat").json()["question"] is None
    fige = client.get("/api/quiz/etat?question_id=q1").json()
    assert fige["ouverte"] is False and fige["total"] == 2

    export = client.get("/api/quiz/export?token=secret-test").text
    assert "q1;" in export and ";2" in export

    client.post("/api/quiz/reinitialiser?token=secret-test", json={"question_id": "q1"})
    assert client.get("/api/quiz/etat?question_id=q1").json()["total"] == 0


def test_pages_et_qr(client):
    assert client.get("/q").status_code == 200
    assert client.get("/q/resultats").status_code == 200
    assert client.get("/deck/").status_code == 200
    assert client.get("/deck/remote.html").status_code == 200
    r = client.get("/qr.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert client.get("/remote").status_code == 403
    assert client.get("/remote?token=secret-test", follow_redirects=False).status_code == 307
    assert client.get("/sante").json()["ok"] is True


def test_quiz_json_valide():
    from server.quiz import charger_questions

    questions = charger_questions(RACINE / "deck" / "quiz.json")
    assert len(questions) >= 1
    for q in questions.values():
        assert 2 <= len(q.options) <= 5

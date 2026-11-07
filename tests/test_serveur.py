"""Tests du serveur : synchronisation du deck, quiz (12 types), points, classement, éditeur, jeton."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RACINE = Path(__file__).resolve().parent.parent
JETON = "secret-test"


@pytest.fixture
def client(tmp_path, monkeypatch):
    deck = tmp_path / "deck"
    deck.mkdir()
    for f in ("slides.html", "quiz.json", "index.html", "remote.html"):
        (deck / f).write_text((RACINE / "deck" / f).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("AMPHI_JETON", JETON)
    monkeypatch.setenv("AMPHI_BDD", str(tmp_path / "quiz.sqlite"))
    monkeypatch.setenv("AMPHI_URL_PUBLIQUE", "https://q.test")
    monkeypatch.setenv("AMPHI_DECK", str(deck))
    from server import config

    importlib.reload(config)
    from server import app as module_app

    importlib.reload(module_app)
    with TestClient(module_app.app) as c:
        c.deck = deck
        yield c


def admin(client, chemin, corps=None):
    r = client.post(f"{chemin}?token={JETON}", json=corps or {})
    assert r.status_code == 200, r.text
    return r.json()


def participant(client, pseudo=""):
    p = TestClient(client.app)
    p.post("/api/quiz/pseudo", json={"pseudo": pseudo})
    return p


def voter(p, question_id, reponse):
    return p.post("/api/quiz/vote", json={"question_id": question_id, "reponse": reponse})


# ---------------------------------------------------------------------------------------
# Deck
# ---------------------------------------------------------------------------------------


def test_etat_initial(client):
    r = client.get("/etat")
    assert r.status_code == 200 and r.json()["h"] == 0 and r.json()["f"] == -1


def test_sync_remote_vers_scene_et_obs_avec_acks(client):
    with client.websocket_connect("/ws/deck?role=scene") as scene, \
         client.websocket_connect("/ws/deck?role=obs") as obs, \
         client.websocket_connect(f"/ws/deck?role=remote&token={JETON}") as remote:
        for ws in (scene, obs, remote):
            assert ws.receive_json()["type"] == "slide"
        remote.send_json({"type": "slide", "h": 3, "v": 0, "f": 1})
        for ws in (scene, obs):
            m = ws.receive_json()
            assert (m["type"], m["h"], m["v"], m["f"]) == ("slide", 3, 0, 1)
            ws.send_json({"type": "ack", "h": 3, "v": 0, "f": 1})
        assert {remote.receive_json()["role"], remote.receive_json()["role"]} == {"scene", "obs"}
    etat = client.get("/etat").json()
    assert (etat["h"], etat["f"]) == (3, 1) and set(etat["acks"]) == {"scene", "obs"}


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


def test_editeur_slides_et_medias(client):
    html = client.get("/api/deck/slides").text
    assert "<section" in html
    assert client.put("/api/deck/slides", json={"html": "x"}).status_code == 403
    with client.websocket_connect("/ws/deck?role=scene") as scene:
        scene.receive_json()
        nouveau = '<section class="libre" data-id="s1"><div class="el texte" data-id="e1" style="left:0;top:0">Bonjour</div></section>'
        assert client.put(f"/api/deck/slides?token={JETON}", json={"html": nouveau}).status_code == 200
        assert scene.receive_json()["type"] == "recharger"
    assert client.get("/api/deck/slides").text == nouveau
    sauvegardes = client.get(f"/api/deck/sauvegardes?token={JETON}").json()
    assert len(sauvegardes) == 1 and "<section" in client.get(f"/api/deck/sauvegardes/{sauvegardes[0]['nom']}?token={JETON}").text

    r = client.post(f"/api/deck/medias?token={JETON}", files={"fichier": ("mon image.png", b"\x89PNG fake", "image/png")})
    assert r.status_code == 200 and r.json()["chemin"] == "medias/mon_image.png"
    assert (client.deck / "medias" / "mon_image.png").read_bytes() == b"\x89PNG fake"
    r = client.post(f"/api/deck/medias?token={JETON}", files={"fichier": ("mon image.png", b"autre", "image/png")})
    assert r.json()["chemin"] == "medias/mon_image-2.png"
    assert client.post(f"/api/deck/medias?token={JETON}", files={"fichier": ("x.exe", b"", "application/octet-stream")}).status_code == 415
    assert [m["chemin"] for m in client.get(f"/api/deck/medias?token={JETON}").json()] == ["medias/mon_image-2.png", "medias/mon_image.png"]


# ---------------------------------------------------------------------------------------
# Quiz
# ---------------------------------------------------------------------------------------


def test_questions_publiques_sans_bonnes_reponses(client):
    publiques = client.get("/api/quiz/questions").json()
    assert len(publiques) == 12 and all("bonne" not in q and "bonnes" not in q for q in publiques)
    completes = client.get(f"/api/quiz/questions?token={JETON}").json()
    assert next(q for q in completes if q["id"] == "q1")["bonne"] == 1


def test_qcm_points_et_classement(client):
    assert voter(participant(client), "q1", 1).status_code == 409  # fermée
    assert client.post("/api/quiz/ouvrir", json={"question_id": "q1"}).status_code == 403
    admin(client, "/api/quiz/ouvrir", {"question_id": "q1"})

    a, b, c = participant(client, "Alice"), participant(client, "Bob"), participant(client)
    with client.websocket_connect("/ws/quiz") as resultats:
        assert resultats.receive_json()["ouverte"] is True
        assert voter(a, "q1", 1).status_code == 200
        assert voter(b, "q1", 0).status_code == 200
        assert voter(b, "q1", 1).status_code == 200  # change d'avis
        assert voter(c, "q1", 3).status_code == 200
        assert voter(c, "q1", 7).status_code == 400
        # Publication regroupée : un ou plusieurs messages, le dernier porte l'état complet.
        pousse = resultats.receive_json()
        while pousse["resultat"]["total"] < 3:
            pousse = resultats.receive_json()
        assert pousse["resultat"]["comptes"] == [0, 2, 0, 1]

    assert b.get("/api/quiz/etat").json()["ma_reponse"]["reponse"] == 1
    assert client.get("/api/quiz/classement").json() == []

    etat = admin(client, "/api/quiz/reveler", {"question_id": "q1"})
    assert etat["ouverte"] is False and etat["revele"] is True and etat["correction"]["bonne"] == 1
    assert a.get("/api/quiz/etat").json()["ma_reponse"]["points"] >= 500
    assert c.get("/api/quiz/etat").json()["ma_reponse"]["points"] == 0
    classement = client.get("/api/quiz/classement").json()
    assert [p["pseudo"] for p in classement] == ["Alice", "Bob"] and classement[0]["rang"] == 1
    assert voter(a, "q1", 2).status_code == 409  # révélée = fermée

    export = client.get(f"/api/quiz/export?token={JETON}").text
    assert "q1;qcm" in export and "La répétition générale;2" in export
    admin(client, "/api/quiz/reinitialiser", {"question_id": "q1"})
    assert client.get("/api/quiz/etat?question_id=q1").json()["resultat"]["total"] == 0
    admin(client, "/api/quiz/purger")
    assert client.get("/api/quiz/classement").json() == []


def test_pseudo_genere_et_choisi(client):
    p = TestClient(client.app)
    r = p.post("/api/quiz/pseudo", json={"pseudo": ""}).json()
    assert "-" in r["pseudo"]
    assert p.post("/api/quiz/pseudo", json={"pseudo": "  Zoé   la  Rapide "}).json()["pseudo"] == "Zoé la Rapide"
    assert p.get("/api/quiz/etat").json()["pseudo"] == "Zoé la Rapide"


def test_nuage_de_mots(client):
    admin(client, "/api/quiz/ouvrir", {"question_id": "q2"})
    assert voter(participant(client), "q2", ["Méthode", "rigueur"]).status_code == 200
    assert voter(participant(client), "q2", ["methode", "  Calme ", "x" * 80, "trop", "de", "mots"]).status_code == 200
    assert voter(participant(client), "q2", ["   "]).status_code == 400
    mots = client.get("/api/quiz/etat").json()["resultat"]["mots"]
    assert mots[0] == {"mot": "Méthode", "n": 2}
    assert len(mots) == 4  # 3 mots max par participant


def test_rapidite_vrai_faux_multi(client):
    admin(client, "/api/quiz/ouvrir", {"question_id": "q3"})
    rapide, lent = participant(client, "Rapide"), participant(client, "Lent")
    assert voter(rapide, "q3", 2).status_code == 200
    assert voter(lent, "q3", 2).status_code == 200
    etat = client.get("/api/quiz/etat").json()
    assert etat["reste"] is not None and 0 < etat["reste"] <= 15
    admin(client, "/api/quiz/reveler", {"question_id": "q3"})
    res = client.get("/api/quiz/etat?question_id=q3").json()["resultat"]
    assert [p["pseudo"] for p in res["rapides"]] == ["Rapide", "Lent"]
    pr, pl = rapide.get("/api/quiz/etat").json()["ma_reponse"]["points"], lent.get("/api/quiz/etat").json()["ma_reponse"]["points"]
    assert pr >= pl > 900

    admin(client, "/api/quiz/ouvrir", {"question_id": "q4"})
    assert voter(rapide, "q4", False).status_code == 200
    assert voter(lent, "q4", "faux").status_code == 400
    admin(client, "/api/quiz/reveler", {"question_id": "q4"})
    assert client.get("/api/quiz/etat?question_id=q4").json()["resultat"]["comptes"] == [0, 1]

    admin(client, "/api/quiz/ouvrir", {"question_id": "q6"})
    assert voter(rapide, "q6", [2, 0, 1]).status_code == 200
    assert voter(lent, "q6", [0, 3]).status_code == 200
    admin(client, "/api/quiz/reveler", {"question_id": "q6"})
    assert rapide.get("/api/quiz/etat").json()["ma_reponse"]["points"] > 0
    assert lent.get("/api/quiz/etat").json()["ma_reponse"]["points"] == 0
    assert client.get("/api/quiz/etat?question_id=q6").json()["resultat"]["comptes"] == [2, 1, 1, 1]


def test_buzzer_trou_echelle_classement_nombre_texte(client):
    admin(client, "/api/quiz/ouvrir", {"question_id": "q7"})
    a, b = participant(client, "A"), participant(client, "B")
    assert voter(a, "q7", True).status_code == 200
    assert voter(a, "q7", True).status_code == 409  # un seul buzz
    assert voter(b, "q7", True).status_code == 200
    admin(client, "/api/quiz/reveler", {"question_id": "q7"})
    res = client.get("/api/quiz/etat?question_id=q7").json()["resultat"]
    assert [p["pseudo"] for p in res["premiers"]] == ["A", "B"]
    assert a.get("/api/quiz/etat").json()["ma_reponse"]["points"] == 1000
    assert b.get("/api/quiz/etat").json()["ma_reponse"]["points"] == 800

    admin(client, "/api/quiz/ouvrir", {"question_id": "q8"})
    assert voter(a, "q8", "  CROISÉE ").status_code == 200
    assert voter(b, "q8", "linéaire").status_code == 200
    admin(client, "/api/quiz/reveler", {"question_id": "q8"})
    res = client.get("/api/quiz/etat?question_id=q8").json()["resultat"]
    assert res["bonnes_n"] == 1 and {f["texte"]: f["bonne"] for f in res["frequentes"]} == {"CROISÉE": True, "linéaire": False}

    admin(client, "/api/quiz/ouvrir", {"question_id": "q9"})
    assert voter(a, "q9", 4).status_code == 200 and voter(b, "q9", 2).status_code == 200
    assert voter(b, "q9", 9).status_code == 400
    res = client.get("/api/quiz/etat").json()["resultat"]
    assert res["comptes"] == [0, 1, 0, 1, 0] and res["moyenne"] == 3

    admin(client, "/api/quiz/ouvrir", {"question_id": "q10"})
    assert voter(a, "q10", [0, 1, 2, 3]).status_code == 200
    assert voter(b, "q10", [3, 1, 2, 0]).status_code == 200
    assert voter(b, "q10", [0, 0, 1, 2]).status_code == 400
    admin(client, "/api/quiz/reveler", {"question_id": "q10"})
    res = client.get("/api/quiz/etat?question_id=q10").json()["resultat"]
    assert res["ordre"][0] in (0, 1) and res["rang_moyen"][1] == 2
    assert a.get("/api/quiz/etat").json()["ma_reponse"]["points"] == 1000
    assert b.get("/api/quiz/etat").json()["ma_reponse"]["points"] == 500

    admin(client, "/api/quiz/ouvrir", {"question_id": "q11"})
    assert voter(a, "q11", 300).status_code == 200 and voter(b, "q11", 150.5).status_code == 200
    assert voter(b, "q11", "beaucoup").status_code == 400
    admin(client, "/api/quiz/reveler", {"question_id": "q11"})
    res = client.get("/api/quiz/etat?question_id=q11").json()["resultat"]
    assert res["mediane"] == 225.25 and res["proches"][0]["pseudo"] == "A" and len(res["classes"]) == 8
    assert a.get("/api/quiz/etat").json()["ma_reponse"]["points"] == 1000

    admin(client, "/api/quiz/ouvrir", {"question_id": "q12"})
    assert voter(a, "q12", "Merci !").status_code == 200
    assert voter(b, "q12", "x" * 500).status_code == 200
    res = client.get("/api/quiz/etat").json()["resultat"]
    assert res["textes"][0]["texte"] == "x" * 140 and res["textes"][1] == {"pseudo": "A", "texte": "Merci !"}

    classement = client.get("/api/quiz/classement?nombre=5").json()
    assert classement[0]["pseudo"] == "A" and classement[0]["points"] > classement[1]["points"]


def test_emoji_et_remplacement_des_questions(client):
    admin(client, "/api/quiz/ouvrir", {"question_id": "q5"})
    assert voter(participant(client), "q5", 4).status_code == 200
    assert client.get("/api/quiz/etat").json()["resultat"]["comptes"][4] == 1

    nouvelles = [{"id": "z1", "type": "qcm", "question": "?", "options": ["a", "b"], "bonne": 0}]
    r = client.put(f"/api/quiz/questions?token={JETON}", json=nouvelles)
    assert r.status_code == 200 and r.json()["questions"] == 1
    assert client.get("/api/quiz/etat").json()["question"] is None  # q5 n'existe plus, donc fermée
    assert [q["id"] for q in client.get("/api/quiz/questions").json()] == ["z1"]
    assert '"z1"' in (client.deck / "quiz.json").read_text(encoding="utf-8")

    invalides = [{"id": "Z 1", "type": "qcm", "question": "?", "options": ["a"]}]
    r = client.put(f"/api/quiz/questions?token={JETON}", json=invalides)
    assert r.status_code == 422 and "identifiant" in r.json()["detail"]
    assert client.put(f"/api/quiz/questions?token={JETON}", json=[{"id": "t", "type": "trou", "question": "x", "texte": "sans trou"}]).status_code == 422
    assert client.put(f"/api/quiz/questions?token={JETON}", json=[{"id": "r", "type": "rapidite", "question": "x", "options": ["a", "b"], "bonne": 0}]).status_code == 422


def test_chrono_ferme_automatiquement(client, monkeypatch):
    from server import quiz as module_quiz

    nouvelles = [{"id": "c", "type": "qcm", "question": "?", "options": ["a", "b"], "bonne": 0, "chrono": 1}]
    client.put(f"/api/quiz/questions?token={JETON}", json=nouvelles)
    admin(client, "/api/quiz/ouvrir", {"question_id": "c"})
    p = participant(client)
    assert voter(p, "c", 0).status_code == 200
    # Simuler l'écoulement : la question ouverte il y a 2 s est refusée.
    client.app.state.quiz.ouverte_a -= 2
    assert voter(p, "c", 1).status_code == 409


def test_pages_et_qr(client):
    for chemin in ("/q", "/q/resultats", "/q/classement", "/deck/", "/deck/remote.html"):
        assert client.get(chemin).status_code == 200, chemin
    r = client.get("/qr.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert client.get("/remote").status_code == 403
    assert client.get(f"/remote?token={JETON}", follow_redirects=False).status_code == 307
    assert client.get(f"/editeur?token={JETON}", follow_redirects=False).status_code == 307
    assert client.get("/sante").json()["questions"] == 12


def test_quiz_json_valide():
    from server.quiz import TYPES, charger_questions

    questions = charger_questions(RACINE / "deck" / "quiz.json")
    assert {q.type for q in questions.values()} == set(TYPES)

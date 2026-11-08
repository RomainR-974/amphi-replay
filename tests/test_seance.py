"""Tests du déroulé de séance (docs/seance.md, A et C) : marqueurs OBS, fin de séance, reprise, contact."""

from __future__ import annotations

import json

from server.journal import marqueurs_obs
from tests.test_questions import jeton, recevoir
from tests.test_serveur import JETON, admin, creer_presentation, participant, voter  # noqa: F401
from tests.test_serveur import client  # noqa: F401

SLIDES_SEANCE = """<!-- séance -->
<section data-id="s1"><h2>Titre</h2></section>
<section data-id="s2"><h2>Avant de commencer</h2></section>
<section class="libre dsfr" data-id="s2b" data-transition="fade" data-obs="demarrer"><div class="el texte" data-id="e1" style="left:0;top:0">Qui suis-je</div><div class="el texte fragment" data-id="e2" style="left:0;top:100px">Suite</div></section>
<section data-quiz="q1" data-id="s4" data-obs="arreter"></section>
<section data-id="s5" data-obs="n-importe-quoi"><h2>À retenir</h2><section data-obs="demarrer"><p>imbriquée : ignorée</p></section></section>
"""

CONTACT = {"nom": "Romain Ringwald", "fonction": "DRAFPIC", "courriel": "romain.ringwald@ac-reunion.fr", "linkedin": "https://www.linkedin.com/in/romain-ringwald-b64b653a/"}


def test_marqueurs_obs_extraits_du_slides_html():
    """Un marqueur par section de premier niveau (index h), valeurs inconnues et sections imbriquées ignorées."""
    assert marqueurs_obs(SLIDES_SEANCE) == {2: "demarrer", 3: "arreter"}
    assert marqueurs_obs("") == {}
    assert marqueurs_obs('<section data-obs="Arreter"></section>') == {0: "arreter"}


def activer_seance(client, contact: dict | None = CONTACT) -> None:
    d = creer_presentation(client.presentations, "seance", "Séance", slides=SLIDES_SEANCE)
    if contact is not None:
        meta = json.loads((d / "presentation.json").read_text(encoding="utf-8"))
        meta["contact"] = contact
        (d / "presentation.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    assert client.post(jeton("/api/presentations/seance/activer")).status_code == 200


def test_message_obs_a_l_arrivee_sur_la_diapositive_marquee(client):
    activer_seance(client)
    with client.websocket_connect("/ws/quiz") as quiz_ws, client.websocket_connect(f"/ws/deck?role=remote&token={JETON}") as remote:
        quiz_ws.receive_json()
        remote.receive_json()
        remote.send_json({"type": "slide", "h": 1, "v": 0, "f": -1})  # pas de marqueur : rien
        remote.send_json({"type": "slide", "h": 2, "v": 0, "f": -1})  # arrivée sur « Qui suis-je »
        m = recevoir(quiz_ws, "obs")
        assert m == {"type": "obs", "action": "StartRecord", "h": 2}
        remote.send_json({"type": "slide", "h": 2, "v": 0, "f": 0})  # fragment suivant : pas de nouvelle diffusion
        remote.send_json({"type": "slide", "h": 3, "v": 0, "f": -1})  # « arreter »
        m = recevoir(quiz_ws, "obs")
        assert m == {"type": "obs", "action": "StopRecord", "h": 3}
        remote.send_json({"type": "slide", "h": 4, "v": 0, "f": -1})  # valeur inconnue : rien
        remote.send_json({"type": "slide", "h": 2, "v": 0, "f": -1})  # nouvelle arrivée : rediffusé
        assert recevoir(quiz_ws, "obs")["action"] == "StartRecord"
        remote.send_json({"type": "ping"})
        assert remote.receive_json()["type"] == "pong"

    # L'enregistrement de slides.html par l'éditeur recalcule les marqueurs de l'active.
    sans = SLIDES_SEANCE.replace(' data-obs="demarrer"', "")
    assert client.put(jeton("/api/deck/slides"), json={"html": sans}).status_code == 200
    with client.websocket_connect("/ws/quiz") as quiz_ws, client.websocket_connect(f"/ws/deck?role=remote&token={JETON}") as remote:
        quiz_ws.receive_json()
        remote.receive_json()
        remote.send_json({"type": "slide", "h": 1, "v": 0, "f": -1})
        remote.send_json({"type": "slide", "h": 2, "v": 0, "f": -1})
        remote.send_json({"type": "slide", "h": 3, "v": 0, "f": -1})
        assert recevoir(quiz_ws, "obs") == {"type": "obs", "action": "StopRecord", "h": 3}


def test_message_obs_quand_la_sequence_qr_deplace_le_deck(client):
    activer_seance(client)
    alice = participant(client, "Alice")
    with client.websocket_connect(f"/ws/deck?role=remote&token={JETON}") as remote:
        remote.receive_json()
        remote.send_json({"type": "slide", "h": 2, "v": 0, "f": -1})
        remote.send_json({"type": "ping"})
        remote.receive_json()
    assert alice.post("/api/questions", json={"texte": "Sur qui suis-je ?"}).status_code == 200
    # Retour sur la première diapositive, puis la séquence Q/R saute à la question (h = 2).
    with client.websocket_connect(f"/ws/deck?role=remote&token={JETON}") as remote:
        remote.receive_json()
        remote.send_json({"type": "slide", "h": 0, "v": 0, "f": -1})
        remote.send_json({"type": "ping"})
        remote.receive_json()
    with client.websocket_connect("/ws/quiz") as quiz_ws:
        quiz_ws.receive_json()
        admin(client, "/api/qr/ouvrir")
        admin(client, "/api/qr/question", {"id": 1})
        assert recevoir(quiz_ws, "obs") == {"type": "obs", "action": "StartRecord", "h": 2}
        admin(client, "/api/qr/fermer")


def test_terminer_puis_reprendre(client):
    activer_seance(client)
    assert client.get("/api/seance").json() == {"terminee": False, "contact": CONTACT, "presentation": "seance"}
    assert client.get("/sante").json()["terminee"] is False
    assert client.post("/api/seance/terminer").status_code == 403

    alice = participant(client, "Alice")
    admin(client, "/api/quiz/ouvrir", {"question_id": "q1"})
    assert voter(alice, "q1", 1).status_code == 200
    admin(client, "/api/quiz/reveler", {"question_id": "q1"})
    admin(client, "/api/quiz/ouvrir", {"question_id": "q1"})  # question rouverte : Quitter doit la fermer
    admin(client, "/api/qr/ouvrir")
    assert client.get("/sante").json()["qr"] is True

    with client.websocket_connect("/ws/quiz") as quiz_ws:
        quiz_ws.receive_json()
        etat = admin(client, "/api/seance/terminer")
        assert etat["terminee"] is True and etat["contact"] == CONTACT
        recus = []
        while not recus or recus[-1]["type"] != "fin":
            recus.append(quiz_ws.receive_json())
        types = [m["type"] for m in recus]
        assert types[-1] == "fin" and "quiz" in types and "qr" in types and "obs" in types
        assert [m for m in recus if m["type"] == "quiz"][0]["ouverte"] is False
        assert [m for m in recus if m["type"] == "qr"][-1]["actif"] is False
        assert [m for m in recus if m["type"] == "obs"][-1]["action"] == "StopRecord"
        fin = recus[-1]
        assert fin["terminee"] is True and fin["contact"] == CONTACT and fin["classement_total"] == 1

        sante = client.get("/sante").json()
        assert sante["terminee"] is True and sante["question_ouverte"] is None and sante["qr"] is False
        assert client.get("/api/seance").json()["terminee"] is True
        assert client.get("/api/quiz/classement?nombre=1000").json()[0]["pseudo"] == "Alice"

        # Reprendre : les participants reviennent à « En attente ».
        assert admin(client, "/api/seance/reprendre")["terminee"] is False
        assert recevoir(quiz_ws, "fin") == {"type": "fin", "terminee": False}
        assert client.get("/api/seance").json()["terminee"] is False
        assert admin(client, "/api/seance/reprendre")["terminee"] is False  # idempotent : rien de diffusé

    types = [e["type"] for e in client.get(jeton("/api/journal?prez=seance")).json()]
    assert "fin" in types and types.index("quiz_fermer") < types.index("fin") and types.index("qr_fermer") < types.index("fin")


def test_activation_remet_la_seance_a_zero(client):
    activer_seance(client)
    admin(client, "/api/seance/terminer")
    assert client.get("/api/seance").json()["terminee"] is True
    with client.websocket_connect("/ws/quiz") as quiz_ws:
        quiz_ws.receive_json()
        admin(client, "/api/presentations/demo/activer")
        assert recevoir(quiz_ws, "fin") == {"type": "fin", "terminee": False}
    etat = client.get("/api/seance").json()
    assert etat == {"terminee": False, "presentation": "demo", "contact": {"nom": "", "fonction": "", "courriel": "", "linkedin": ""}}
    # Le contact survit à un enregistrement (toucher) et un contact vide n'est pas écrit.
    assert client.put(jeton("/api/deck/slides?prez=seance"), json={"html": SLIDES_SEANCE}).status_code == 200
    assert json.loads((client.presentations / "seance" / "presentation.json").read_text(encoding="utf-8"))["contact"] == CONTACT
    assert "contact" not in json.loads((client.presentations / "demo" / "presentation.json").read_text(encoding="utf-8"))

"""Séances jouées (docs/seance.md §F et §G) : historique, résultats archivés, copie figée, retour caméra de la télécommande."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from test_serveur import JETON, admin, client, participant, voter  # noqa: F401  (fixture)

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "obs"))

from obs_quiz_bridge import niveaux_micro  # noqa: E402


def jouer_seance(client):  # noqa: F811
    """Une petite séance : deux diapositives, la question q1 ouverte, deux votes, révélée."""
    with client.websocket_connect(f"/ws/deck?role=remote&token={JETON}") as remote:
        remote.receive_json()
        remote.send_json({"type": "slide", "h": 0, "v": 0, "f": -1})
        remote.send_json({"type": "slide", "h": 1, "v": 0, "f": -1})
        remote.send_json({"type": "ping"})
        while remote.receive_json()["type"] != "pong":
            pass
    admin(client, "/api/quiz/ouvrir", {"question_id": "q1"})
    assert voter(participant(client, "Alice"), "q1", 1).status_code == 200
    assert voter(participant(client, "Bob"), "q1", 2).status_code == 200
    admin(client, "/api/quiz/reveler", {"question_id": "q1"})


def test_liste_et_detail_d_une_seance(client):  # noqa: F811
    jouer_seance(client)
    assert client.get("/api/presentations/demo/historique").status_code == 403
    seances = client.get(f"/api/presentations/demo/historique?token={JETON}").json()
    assert len(seances) == 1
    s = seances[0]
    assert s["diapositives"] == 2 and s["questions"] == 1 and s["copie"] is None and s["replay"] is None
    d = client.get(f"/api/presentations/demo/historique/{s['id']}?token={JETON}").json()
    assert [e["h"] for e in d["evenements"] if e["type"] == "diapositive"] == [0, 1]
    r = d["resultats"]["q1"]
    assert r["source"] == "archive" and r["resultat"]["total"] == 2 and r["resultat"]["comptes"][1:3] == [1, 1]
    assert r["question"]["options"][1] == "La répétition générale"
    assert client.get(f"/api/presentations/demo/historique/123?token={JETON}").status_code == 404


def test_resultats_conserves_apres_remise_a_zero(client):  # noqa: F811
    jouer_seance(client)
    admin(client, "/api/quiz/reinitialiser", {"question_id": "q1"})  # les réponses sont effacées…
    s = client.get(f"/api/presentations/demo/historique?token={JETON}").json()[0]
    d = client.get(f"/api/presentations/demo/historique/{s['id']}?token={JETON}").json()
    assert d["resultats"]["q1"]["resultat"]["total"] == 2  # … l'instantané de la séance reste


def test_copie_figee_rattachee_et_servie(client):  # noqa: F811
    with client.websocket_connect(f"/ws/quiz?token={JETON}") as pont:  # début d'enregistrement : la présentation est figée
        pont.receive_json()
        pont.send_json({"type": "enregistrement", "actif": True, "duree_ms": 0})
        copie = pont.receive_json()["seance"]
    jouer_seance(client)
    s = client.get(f"/api/presentations/demo/historique?token={JETON}").json()[0]
    assert s["copie"] == copie
    (client.deck / "slides.html").write_text("<section>modifiée après la séance</section>", encoding="utf-8")
    r = client.get(f"/p/demo/seances/{copie}/slides.html")
    assert r.status_code == 200 and "Démo" in r.text and "modifiée" not in r.text
    assert client.get(f"/p/demo/seances/{copie}/quiz.json").status_code == 200
    assert client.get(f"/p/demo/seances/{copie}/presentation.json").status_code == 404  # liste fermée
    assert client.get(f"/p/demo/seances/{copie}/../presentation.json").status_code == 404


def test_retour_camera_relaye_du_pont_a_la_telecommande(client):  # noqa: F811
    with client.websocket_connect(f"/ws/deck?role=remote&token={JETON}") as remote:
        remote.receive_json()
        remote.send_json({"type": "apercu", "actif": True})
        assert remote.receive_json()["erreur"].startswith("pont OBS non connecté")
        with client.websocket_connect(f"/ws/quiz?token={JETON}") as pont, client.websocket_connect("/ws/quiz") as public:
            pont.receive_json(), public.receive_json()
            remote.send_json({"type": "apercu", "actif": True})
            assert pont.receive_json() == {"type": "apercu_demande", "actif": True}
            pont.send_json({"type": "apercu", "image": "data:image/jpg;base64,AAAA", "niveau": -20.5, "crete": -9.0, "camera": "Webcam", "micro": "Mic/Aux"})
            m = remote.receive_json()
            assert m["type"] == "apercu" and m["image"].startswith("data:image/") and m["niveau"] == -20.5
            public.send_json({"type": "apercu", "image": "data:image/jpg;base64,BBBB"})  # un participant ne peut rien injecter
            public.send_json({"type": "ping"})
            assert public.receive_json()["type"] == "pong"
            remote.send_json({"type": "ping"})
            assert remote.receive_json()["type"] == "pong"  # rien d'autre n'est arrivé à la télécommande


def test_niveaux_micro():
    evenement = {"inputs": [{"inputName": "Bureau", "inputLevelsMul": [[0.5, 0.9, 0.9]]},
                            {"inputName": "Mic/Aux", "inputLevelsMul": [[0.1, 0.5, 0.5], [0.05, 0.25, 0.25]]}]}
    assert niveaux_micro(evenement, "Mic/Aux") == (-20.0, -6.0)
    assert niveaux_micro(evenement, "Absent") is None
    assert niveaux_micro({"inputs": [{"inputName": "Mic/Aux", "inputLevelsMul": []}]}, "Mic/Aux") == (-120.0, -120.0)

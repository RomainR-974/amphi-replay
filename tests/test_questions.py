"""Tests de l'espace questions des participants et de la séquence questions-réponses (docs/questions.md)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.test_serveur import JETON, admin, creer_presentation, participant  # noqa: F401
from tests.test_serveur import client  # noqa: F401

SLIDES_QUESTIONS = """<!-- test -->
<section data-id="s1"><h2>Introduction</h2></section>
<section data-id="s2"><h2>Le cadre réglementaire</h2></section>
<section data-id="s3"><h2>Les usages permis</h2></section>
<section data-quiz="q1" data-id="s4"></section>
"""


def jeton(chemin: str) -> str:
    return f"{chemin}{'&' if '?' in chemin else '?'}token={JETON}"


def aller_a(client, h: int, v: int = 0, f: int = -1) -> None:
    """Déplace le deck comme la télécommande."""
    with client.websocket_connect(f"/ws/deck?role=remote&token={JETON}") as remote:
        remote.receive_json()
        remote.send_json({"type": "slide", "h": h, "v": v, "f": f})
        remote.send_json({"type": "ping"})
        assert remote.receive_json()["type"] == "pong"


def activer_slides(client) -> None:
    creer_presentation(client.presentations, "cours", "Cours", slides=SLIDES_QUESTIONS)
    assert client.post(jeton("/api/presentations/cours/activer")).status_code == 200


def poser(p, texte, generale=False):
    return p.post("/api/questions", json={"texte": texte, "generale": generale})


# ---------------------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------------------


def test_contexte_envoi_et_limites(client):
    activer_slides(client)
    aller_a(client, 1)
    contexte = client.get("/api/questions/contexte").json()
    assert contexte == {"h": 1, "v": 0, "numero": 2, "total": 4, "titre": "Le cadre réglementaire"}

    alice = participant(client, "Alice")
    with client.websocket_connect("/ws/quiz") as ws:
        ws.receive_json()  # état du quiz
        r = poser(alice, "  Quelle   est la  règle ?  ")
        assert r.status_code == 200, r.text
        assert r.json() == {"id": 1, "numero": 2, "titre": "Le cadre réglementaire", "generale": False}
        assert ws.receive_json() == {"type": "questions", "en_attente": 1}

    aller_a(client, 2)
    assert poser(alice, "Et pour les examens ?", generale=True).json()["numero"] == 3
    # Limites : vide, trop long.
    assert poser(alice, "   ").status_code == 422
    assert poser(alice, "x" * 281).status_code == 422
    assert poser(alice, "x" * 280).status_code == 200
    for i in range(17):
        assert poser(alice, f"question {i}").status_code == 200
    assert poser(alice, "une de trop").status_code == 429
    assert poser(participant(client, "Bob"), "Un autre participant peut encore poser").status_code == 200

    # Liste avec jeton : pseudos ; publique : sans pseudo ; texte normalisé ; contexte conservé.
    assert client.get("/api/questions").status_code == 403
    liste = client.get(jeton("/api/questions")).json()
    assert len(liste) == 21 and liste[0]["texte"] == "Quelle est la règle ?" and liste[0]["pseudo"] == "Alice"
    assert (liste[0]["h"], liste[0]["numero"], liste[0]["titre"], liste[0]["generale"], liste[0]["etat"]) == (1, 2, "Le cadre réglementaire", False, "en_attente")
    assert liste[1]["generale"] is True and liste[1]["numero"] == 3 and liste[1]["titre"] == "Les usages permis"
    assert liste[-1]["pseudo"] == "Bob" and all("participant" not in q for q in liste)
    publiques = client.get("/api/questions/publiques").json()
    assert len(publiques) == 21 and all(q["pseudo"] is None and "participant" not in q for q in publiques)
    assert all(abs(q["horodatage"] - time.time()) < 60 for q in publiques)
    assert client.get("/sante").json()["questions_participants"] == 21


def test_etats_ordre_export_et_purge(client):
    activer_slides(client)
    a, b = participant(client, "A"), participant(client, "B")
    aller_a(client, 2)
    assert poser(a, "Troisième diapo, première posée").status_code == 200  # id 1
    aller_a(client, 0)
    assert poser(b, "Première diapo ; \"avec\" des ; points-virgules").status_code == 200  # id 2
    aller_a(client, 1)
    assert poser(a, "Deuxième diapo").status_code == 200  # id 3

    # Changement d'état.
    assert client.post(jeton("/api/questions/2/etat"), json={"etat": "ecartee"}).status_code == 200
    assert client.post(jeton("/api/questions/2/etat"), json={"etat": "bizarre"}).status_code == 422
    assert client.post(jeton("/api/questions/99/etat"), json={"etat": "repondue"}).status_code == 404
    assert client.post("/api/questions/1/etat", json={"etat": "repondue"}).status_code == 403
    assert [q["id"] for q in client.get(jeton("/api/questions?etat=en_attente")).json()] == [1, 3]
    assert [q["id"] for q in client.get(jeton("/api/questions?etat=ecartee")).json()] == [2]

    # Réordonnancement : l'ordre donné l'emporte ; une nouvelle question arrive à la fin.
    assert client.post(jeton("/api/questions/ordre"), json={"ids": [3, 1]}).status_code == 200
    assert [q["id"] for q in client.get(jeton("/api/questions?etat=en_attente")).json()] == [3, 1]
    assert poser(b, "Quatrième").status_code == 200
    assert [q["id"] for q in client.get(jeton("/api/questions?etat=en_attente")).json()] == [3, 1, 4]

    # Export CSV : numéro, titre, question, générale, état, horodatage ; jamais de pseudo.
    assert client.get("/api/questions/export").status_code == 403
    r = client.get(jeton("/api/questions/export"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lignes = r.text.strip().split("\n")
    assert lignes[0] == "numero;titre_diapo;question;generale;etat;horodatage"
    assert len(lignes) == 5 and "A" not in [l.split(";")[0] for l in lignes[1:]]
    assert lignes[1].startswith("2;Le cadre réglementaire;Deuxième diapo;non;en_attente;")
    assert '1;Introduction;"Première diapo ; ""avec"" des ; points-virgules";non;ecartee;' in r.text
    assert "B" not in r.text.replace("Bob", "")  # pseudos absents

    # Suppression, puis purge (via quiz/purger aussi).
    assert client.delete(jeton("/api/questions/4")).status_code == 200
    assert client.delete(jeton("/api/questions/4")).status_code == 404
    assert len(client.get(jeton("/api/questions")).json()) == 3
    with client.websocket_connect("/ws/quiz") as ws:
        ws.receive_json()
        admin(client, "/api/quiz/purger")
        messages = [ws.receive_json()["type"] for _ in range(2)]
        assert "questions" in messages
    assert client.get(jeton("/api/questions")).json() == []
    assert poser(a, "Après la purge").status_code == 200
    admin(client, "/api/questions/purger")
    assert client.get("/api/questions/publiques").json() == []


# ---------------------------------------------------------------------------------------
# Séquence questions-réponses
# ---------------------------------------------------------------------------------------


def recevoir(ws, type_: str) -> dict:
    """Prochain message de ce type (les autres sont ignorés)."""
    for _ in range(20):
        m = ws.receive_json()
        if m.get("type") == type_:
            return m
    raise AssertionError(f"pas de message {type_}")


def test_sequence_questions_reponses(client):
    activer_slides(client)
    a, b = participant(client, "A"), participant(client, "B")
    aller_a(client, 2)
    assert poser(a, "Sur la troisième").status_code == 200  # id 1, h=2
    aller_a(client, 0)
    assert poser(b, "Sur la première").status_code == 200  # id 2, h=0
    aller_a(client, 3, 0, 1)  # position de départ : diapo 4, fragment 1

    assert client.get("/api/qr").json()["actif"] is False
    assert client.post(jeton("/api/qr/question"), json={"id": 1}).status_code == 409  # séquence non ouverte
    assert client.post("/api/qr/ouvrir").status_code == 403

    with client.websocket_connect("/ws/deck?role=scene") as scene, client.websocket_connect("/ws/quiz") as quiz_ws:
        scene.receive_json()
        quiz_ws.receive_json()

        # Ouvrir : mémorise le retour, journalise, diffuse qr (deck et quiz).
        etat = admin(client, "/api/qr/ouvrir")
        assert etat["actif"] is True and etat["retour"] == {"h": 3, "v": 0, "f": 1} and etat["question"] is None and etat["restantes"] == 2
        m = recevoir(scene, "qr")
        assert (m["actif"], m["question"], m["restantes"]) == (True, None, 2)
        assert recevoir(quiz_ws, "qr")["actif"] is True
        assert admin(client, "/api/qr/ouvrir")["retour"] == {"h": 3, "v": 0, "f": 1}  # idempotent
        recevoir(scene, "qr")
        recevoir(quiz_ws, "qr")

        # Question : le deck saute (slide puis qr), la question passe en cours.
        etat = admin(client, "/api/qr/question", {"id": 1})
        assert etat["question_id"] == 1 and etat["question"]["texte"] == "Sur la troisième" and etat["question"]["pseudo"] is None
        assert etat["question"]["numero"] == 3 and etat["restantes"] == 1
        m = scene.receive_json()
        assert (m["type"], m["h"], m["v"], m["f"]) == ("slide", 2, 0, -1)
        m = scene.receive_json()
        assert m["type"] == "qr" and m["question"]["id"] == 1 and m["question"]["titre"] == "Les usages permis"
        assert client.get("/etat").json()["h"] == 2
        assert client.get(jeton("/api/questions?etat=en_cours")).json()[0]["id"] == 1
        assert recevoir(quiz_ws, "qr")["question"]["id"] == 1

        # Suivante : la précédente est répondue, la suivante en attente devient en cours (deck → diapo 1).
        etat = admin(client, "/api/qr/suivante")
        assert etat["fin"] is False and etat["question"]["id"] == 2 and etat["restantes"] == 0
        assert recevoir(scene, "slide")["h"] == 0
        assert recevoir(scene, "qr")["question"]["id"] == 2
        etats = {q["id"]: q["etat"] for q in client.get(jeton("/api/questions")).json()}
        assert etats == {1: "repondue", 2: "en_cours"}

        # Retour à la liste sans marquer répondue, puis reprise, puis la dernière → fin.
        etat = admin(client, "/api/qr/liste", {"repondue": False})
        assert etat["question"] is None and etat["restantes"] == 1
        assert recevoir(scene, "qr")["question"] is None
        assert client.get("/etat").json()["h"] == 0  # le deck reste sur la diapositive
        admin(client, "/api/qr/question", {"id": 2})
        recevoir(scene, "qr")
        etat = admin(client, "/api/qr/suivante")
        assert etat["fin"] is True and etat["question"] is None and etat["restantes"] == 0
        assert recevoir(scene, "qr")["question"] is None
        assert {q["id"]: q["etat"] for q in client.get(jeton("/api/questions")).json()} == {1: "repondue", 2: "repondue"}

        # Fermer : retour à la position de départ, journal.
        etat = admin(client, "/api/qr/fermer")
        assert etat["actif"] is False
        m = recevoir(scene, "slide")
        assert (m["h"], m["v"], m["f"]) == (3, 0, 1)
        assert recevoir(scene, "qr")["actif"] is False
        # /ws/quiz a reçu chaque état intermédiaire (pont OBS, participants) ; le dernier est la fermeture.
        actifs = [recevoir(quiz_ws, "qr")["actif"]]
        while actifs[-1]:
            actifs.append(recevoir(quiz_ws, "qr")["actif"])
        assert actifs[-1] is False and len(actifs) >= 5
        assert client.get("/etat").json()["h"] == 3

    types = [(e["type"], e["h"]) for e in client.get(jeton("/api/journal?prez=cours")).json()]
    assert ("qr_ouvrir", 3) in types and ("qr_fermer", 3) in types
    i_ouvrir, i_fermer = types.index(("qr_ouvrir", 3)), types.index(("qr_fermer", 3))
    assert types[i_ouvrir + 1 :i_fermer] == [("diapositive", 2), ("diapositive", 0), ("diapositive", 3)]  # sauts de la séquence, puis retour
    seance = client.get(jeton("/api/journal/seances?prez=cours")).json()["seances"][0]
    qr_ouvrir = [e for e in seance["evenements"] if e["type"] == "qr_ouvrir"]
    assert len(qr_ouvrir) == 1 and abs(qr_ouvrir[0]["horodatage"] - time.time()) < 60
    assert admin(client, "/api/qr/fermer")["actif"] is False  # déjà fermée : sans effet


def test_questions_api_sans_presentation_active_ni_titres(client):
    """Sans titre identifiable, la diapositive s'appelle « Diapositive N »."""
    aller_a(client, 1)
    contexte = client.get("/api/questions/contexte").json()
    assert contexte["numero"] == 2 and contexte["total"] == 2 and contexte["titre"] == "Quiz : a"
    p = TestClient(client.app)
    r = poser(p, "Sans pseudo préalable")
    assert r.status_code == 200
    q = client.get(jeton("/api/questions")).json()[0]
    assert q["pseudo"] and "-" in q["pseudo"]  # pseudo généré, comme au quiz

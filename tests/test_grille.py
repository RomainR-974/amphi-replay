"""Question « grille » : une réponse par ligne (ex. extraits A, B, C : humain ou IA ?), docs/format-deck.md §3."""

from __future__ import annotations

import pytest

from server.quiz import Question
from test_serveur import JETON, admin, client, participant, voter  # noqa: F401  (fixture)

GRILLE = {"id": "hia", "type": "grille", "question": "Humain ou IA ?", "options": ["✍️ Humain", "🤖 IA"], "lignes": ["A", "B", "C"], "bonnes": [1, 0, 1], "chrono": 0}


def test_validation():
    Question.depuis(GRILLE)
    Question.depuis({**GRILLE, "bonnes": [1, None, None]})
    assert not Question.depuis({k: v for k, v in GRILLE.items() if k != "bonnes"}).notee
    with pytest.raises(ValueError):
        Question.depuis({**GRILLE, "bonnes": [1, 0]})  # une bonne réponse par ligne
    with pytest.raises(ValueError):
        Question.depuis({**GRILLE, "bonnes": [1, 0, 2]})  # colonne inconnue
    with pytest.raises(ValueError):
        Question.depuis({**GRILLE, "lignes": []})


def test_votes_resultats_et_points(client):  # noqa: F811
    questions = client.get(f"/api/quiz/questions?token={JETON}").json()
    assert client.put(f"/api/quiz/questions?token={JETON}", json=questions + [GRILLE]).status_code == 200
    publique = next(q for q in client.get("/api/quiz/questions").json() if q["id"] == "hia")
    assert publique["lignes"] == ["A", "B", "C"] and "bonnes" not in publique
    admin(client, "/api/quiz/ouvrir", {"question_id": "hia"})
    alice, bob, chloe = participant(client, "Alice"), participant(client, "Bob"), participant(client, "Chloé")
    assert voter(alice, "hia", [1, None, None]).status_code == 200      # une ligne à la fois…
    assert voter(alice, "hia", [1, 0, 1]).status_code == 200            # … puis tout : la réponse est remplacée
    assert voter(bob, "hia", [0, 0, 0]).status_code == 200
    assert voter(chloe, "hia", [1, 1, None]).status_code == 200
    for mauvaise in ([1, 0], [1, 0, 2], [None, None, None], "A", [True, 0, 0]):
        assert voter(chloe, "hia", mauvaise).status_code == 400, mauvaise  # réponse invalide refusée
    etat = client.get("/api/quiz/etat?question_id=hia").json()
    r = etat["resultat"]
    assert r["total"] == 3 and r["comptes"] == [[1, 2], [2, 1], [1, 1]] and r["repondus"] == [3, 3, 2]
    admin(client, "/api/quiz/reveler", {"question_id": "hia"})
    points = {l["pseudo"]: l["points"] for l in client.get("/api/quiz/classement?nombre=10").json()}
    assert points["Alice"] == 1000          # 3 lignes justes sur 3, sans chrono
    assert points["Bob"] == 333             # 1 sur 3 (B)
    assert points["Chloé"] == 333           # A juste, B faux, C sans réponse

"""Cérémonie du classement (docs/seance.md, D) : page, déclencheur du deck, balisage des présentations."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RACINE = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(tmp_path, monkeypatch):
    presentations = tmp_path / "presentations"
    d = presentations / "demo"
    (d / "medias").mkdir(parents=True)
    (d / "slides.html").write_text('<section class="libre" data-id="s1"></section>', encoding="utf-8")
    (d / "quiz.json").write_text("[]", encoding="utf-8")
    (d / "presentation.json").write_text(json.dumps({"titre": "Démo", "description": "", "theme": ""}), encoding="utf-8")
    monkeypatch.setenv("AMPHI_JETON", "secret-test")
    monkeypatch.setenv("AMPHI_BDD", str(tmp_path / "data" / "quiz.sqlite"))
    monkeypatch.setenv("AMPHI_PRESENTATIONS", str(presentations))
    monkeypatch.delenv("AMPHI_PRESENTATION", raising=False)
    from server import config

    importlib.reload(config)
    from server import app as module_app

    importlib.reload(module_app)
    with TestClient(module_app.app) as c:
        yield c


def test_page_ceremonie(client):
    r = client.get("/q/classement?ceremonie=1")
    assert r.status_code == 200
    assert 'id="ceremonie"' in r.text and "type === 'ceremonie'" in r.text
    assert "prefers-reduced-motion" in r.text
    assert "/api/quiz/classement?nombre=1000" in r.text
    # Le classement vide répond bien (la page affichera « Pas encore de points »).
    assert client.get("/api/quiz/classement?nombre=1000").json() == []


def test_declencheur_dans_le_runtime_du_deck():
    js = (RACINE / "deck" / "quiz-deck.js").read_text(encoding="utf-8")
    assert "&ceremonie=1" in js and "dataset.ceremonie === '1'" in js
    assert "dataset.amphi === 'ceremonie'" in js and "postMessage({ type: 'ceremonie' }, '*')" in js


@pytest.mark.parametrize("slug", ["iag-v1", "iag-v2"])
def test_balisage_des_presentations(slug):
    html = (RACINE / "presentations" / slug / "slides.html").read_text(encoding="utf-8")
    assert 'data-ceremonie="1"' in html
    assert 'data-amphi="ceremonie"' in html


def test_gabarit_inserer_quiz():
    src = (RACINE / "tools" / "inserer_quiz.py").read_text(encoding="utf-8")
    assert 'data-ceremonie="1"' in src and 'data-amphi="ceremonie"' in src

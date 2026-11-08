"""Lanceur de séance (PC 2) : lecture des secrets locaux, état des connexions du pont OBS."""

import sys
from pathlib import Path
from types import SimpleNamespace

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "obs"))
sys.path.insert(0, str(RACINE / "tools"))

from lanceur_seance import lire_secrets  # noqa: E402
from obs_quiz_bridge import connexion_ouverte  # noqa: E402


def test_lire_secrets_formats_bat(tmp_path):
    f = tmp_path / "secrets.bat"
    f.write_text('@echo off\r\nrem commentaire\r\nset "OBS_MDP=abc=d"\r\nset amphi_jeton=XYZ_1\r\nset "VIDE="\r\n', encoding="utf-8")
    assert lire_secrets(f) == {"OBS_MDP": "abc=d", "AMPHI_JETON": "XYZ_1", "VIDE": ""}


def test_lire_secrets_fichier_absent(tmp_path):
    assert lire_secrets(tmp_path / "absent.bat") == {}


def test_connexion_ouverte_websockets_recent_et_ancien():
    assert not connexion_ouverte(None)
    # websockets ≥ 14 : plus d'attribut `.closed`, seulement `.state`
    assert connexion_ouverte(SimpleNamespace(state=SimpleNamespace(name="OPEN")))
    assert not connexion_ouverte(SimpleNamespace(state=SimpleNamespace(name="CLOSED")))
    # ancienne API
    assert connexion_ouverte(SimpleNamespace(closed=False))
    assert not connexion_ouverte(SimpleNamespace(closed=True))

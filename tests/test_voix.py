"""Traitement de la voix (postprod/voix.py) : volume final à -16 LUFS, vidéo copiée sans réencodage."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "postprod"))

import voix  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg requis")


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", *args], check=True)


def volume_integre(fichier: Path) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(fichier), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    return float(json.loads(err[err.rindex("{"):err.rindex("}") + 1])["input_i"])


def flux(fichier: Path) -> list[dict]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,sample_rate", "-of", "json", str(fichier)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)["streams"]


@pytest.fixture
def voix_faible(tmp_path) -> Path:
    """« Voix » de synthèse trop faible (-35 dBFS environ), modulée pour avoir une dynamique."""
    f = tmp_path / "voix.wav"
    ffmpeg("-f", "lavfi", "-i", "sine=f=220:d=12", "-f", "lavfi", "-i", "anoisesrc=d=12:a=0.01",
           "-filter_complex", "[0]volume=0.03,tremolo=f=3:d=0.6[v];[v][1]amix=2:normalize=0", "-ar", "44100", str(f))
    return f


@pytest.mark.parametrize("preset", ["podcast", "radio"])
def test_audio_normalise_a_moins_16_lufs(tmp_path, voix_faible, preset):
    assert volume_integre(voix_faible) < -25
    sortie = voix.traiter(voix_faible, tmp_path / "sortie", preset, debruitage=False, journal=lambda *_: None)
    assert sortie.suffix == ".wav"
    assert abs(volume_integre(sortie) - voix.CIBLE_I) < 1.5
    assert flux(sortie)[0]["sample_rate"] == "48000"


def test_video_image_copiee_son_traite(tmp_path, voix_faible):
    video = tmp_path / "captation.mkv"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=12", "-i", str(voix_faible),
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video))
    sortie = voix.traiter(video, tmp_path / "sortie", "podcast", debruitage=False, journal=lambda *_: None)
    assert sortie.name == "captation.mp4"
    genres = {s["codec_type"]: s["codec_name"] for s in flux(sortie)}
    assert genres == {"video": "h264", "audio": "aac"}
    assert abs(volume_integre(sortie) - voix.CIBLE_I) < 1.5


def test_debruitage_absent_signale_et_saute(tmp_path, voix_faible, monkeypatch):
    monkeypatch.setattr(voix, "debruitage_disponible", lambda: False)
    messages = []
    voix.traiter(voix_faible, tmp_path / "sortie", "podcast", debruitage=True, journal=messages.append)
    assert any("DeepFilterNet absent" in m for m in messages)


def test_ligne_de_commande_dossier(tmp_path, voix_faible):
    (tmp_path / "notes.txt").write_text("ignoré", encoding="utf-8")
    assert voix.main([str(tmp_path), "--preset", "radio", "--sans-debruitage"]) == 0
    assert (tmp_path / "traite_radio" / "voix.wav").is_file()
    assert not (tmp_path / "traite_radio" / "notes.wav").exists()

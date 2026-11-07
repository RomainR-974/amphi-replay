"""Tests du recalage audio : corrélation pure numpy, puis pipeline complet avec ffmpeg si disponible."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "postprod"))

import assembler  # noqa: E402

FREQ = assembler.FREQUENCE_ANALYSE


def bruit(duree_s: float, graine: int = 0) -> np.ndarray:
    rng = np.random.default_rng(graine)
    return rng.normal(0, 0.1, int(duree_s * FREQ))


def avec_clap(signal: np.ndarray, position_s: float) -> np.ndarray:
    signal = signal.copy()
    i = int(position_s * FREQ)
    signal[i : i + FREQ // 50] += 0.9  # 20 ms de clap
    return signal


@pytest.mark.parametrize("decalage_s", [0.0, 0.25, -0.4, 3.137])
def test_decalage_par_correlation(decalage_s):
    source = avec_clap(bruit(20), 2.0)
    reference = source
    # Le signal du téléphone est la même scène, décalée de `decalage_s` : il "démarre plus tard" si > 0.
    n = int(abs(decalage_s) * FREQ)
    signal = source[n:] if decalage_s > 0 else np.concatenate([bruit(abs(decalage_s), 9), source])
    lag, confiance = assembler.decalage_par_correlation(reference, signal, FREQ)
    assert abs(lag - decalage_s) < 1.5 / FREQ, (lag, decalage_s)
    assert confiance > 20


def ecrire_wav(chemin: Path, signal: np.ndarray, freq: int) -> None:
    with wave.open(str(chemin), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(freq)
        w.writeframes((np.clip(signal, -1, 1) * 32767).astype(np.int16).tobytes())


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg absent")
def test_pipeline_complet_avec_derive(tmp_path):
    """Vidéo noire 40 s avec son de référence ; téléphone démarré 1,5 s plus tôt avec une dérive de 60 ms."""
    freq = 48000
    rng = np.random.default_rng(1)
    duree = 40.0
    reference = rng.normal(0, 0.05, int(duree * freq))
    for pos in (2.0, duree - 3.0):
        i = int(pos * freq)
        reference[i : i + freq // 50] += 0.9

    avance = 1.5
    derive = 0.120  # mesurée sur ~2/3 de la durée → 80 ms, au-dessus du seuil de correction de 40 ms
    # Le téléphone est "lent" : sa durée pour la même scène est plus longue de `derive` → rééchantillonnage.
    t_tel = np.arange(0, duree + avance, 1 / freq)
    t_scene = (t_tel - avance) * duree / (duree + derive)
    telephone = np.interp(t_scene, np.arange(len(reference)) / freq, reference, left=0.0, right=0.0)
    telephone[t_scene < 0] = rng.normal(0, 0.05, int((t_scene < 0).sum()))

    ref_wav, tel_wav, obs = tmp_path / "ref.wav", tmp_path / "tel.wav", tmp_path / "obs.mkv"
    ecrire_wav(ref_wav, reference, freq)
    ecrire_wav(tel_wav, telephone, freq)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s=320x180:r=30:d={duree}",
         "-i", str(ref_wav), "-i", str(ref_wav), "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(obs)],
        check=True,
    )

    sortie = tmp_path / "replay.mp4"
    code = assembler.principal([str(obs), str(tel_wav), "--sortie", str(sortie)])
    assert code == 0 and sortie.exists()
    mesures = json.loads(sortie.with_suffix(".json").read_text())
    assert abs(mesures["decalage_debut_s"] - avance) < 0.02, mesures
    # La dérive est mesurée entre le début de la vidéo et le début de la dernière fenêtre, pas sur toute la durée.
    fenetre = min(assembler.FENETRE_S, duree / 3, (duree + avance) / 3)
    fin_obs = duree - fenetre
    assert abs(mesures["derive_s"] - derive * fin_obs / duree) < 0.01, mesures
    assert mesures["derive_corrigee"] is True
    assert abs(mesures["facteur_vitesse"] - (1 + derive / duree)) < 2e-4, mesures

    # La sortie a bien une vidéo copiée et une piste AAC, d'une durée proche de la vidéo.
    sonde = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name:format=duration", "-of", "json", str(sortie)],
        capture_output=True, text=True, check=True).stdout)
    codecs = {s["codec_type"]: s["codec_name"] for s in sonde["streams"]}
    assert codecs == {"video": "h264", "audio": "aac"}
    assert abs(float(sonde["format"]["duration"]) - duree) < 0.5

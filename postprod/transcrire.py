#!/usr/bin/env python3
"""Transcription automatique de la voix (sous-titres WebVTT et texte), par faster-whisper, en local.

    python postprod/transcrire.py "<replay.mp4 | son.wav>" [--modele small] [--sortie <dossier>]

Produit `<nom>.vtt` (sous-titres : 2 lignes de 42 caractères au plus, 6 s au plus par sous-titre) et `<nom>.txt`
(transcription en paragraphes). Rien ne quitte le PC : le modèle est téléchargé une fois (cache Hugging Face) puis
tourne sur le processeur (≈ 30 s par minute de son avec `small` sur le PC 2 : 19 min pour 41 min), ou sur la carte
NVIDIA si les bibliothèques CUDA sont présentes.

Dépendance facultative : `pip install faster-whisper` (requirements-postprod.txt). Le son est décodé par ffmpeg
(et non par PyAV, dont certaines versions sont incompatibles avec faster-whisper).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

MODELE_DEFAUT = os.environ.get("AMPHI_WHISPER_MODELE", "small")
TAUX = 16000
MAX_CARACTERES = 84   # deux lignes de 42
MAX_LIGNE = 42
MAX_DUREE_S = 6.0
PAUSE_PARAGRAPHE_S = 1.5


def disponible() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        return False


def _son(source: Path):
    """Son mono 16 kHz en float32 (numpy), décodé par ffmpeg."""
    import numpy as np

    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-map", "0:a:0", "-f", "s16le", "-ac", "1", "-ar", str(TAUX), "-"],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg : son illisible ({r.stderr.decode(errors='replace').strip()[-200:]})")
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def _modele(nom: str, consigner: Callable[[str], None]):
    import numpy as np
    from faster_whisper import WhisperModel

    try:  # carte NVIDIA si CUDA est installé, sinon processeur ; essai réel (cuBLAS n'est chargé qu'au premier calcul)
        modele = WhisperModel(nom, device="cuda", compute_type="float16")
        list(modele.transcribe(np.zeros(TAUX, dtype=np.float32), language="fr")[0])
        consigner(f"transcription : modèle {nom} sur la carte graphique")
        return modele
    except Exception:  # noqa: BLE001 - toute erreur CUDA (bibliothèque absente, pilote) : repli processeur
        consigner(f"transcription : modèle {nom} sur le processeur")
        return WhisperModel(nom, device="cpu", compute_type="int8")


def transcrire(source: Path, *, modele: str = MODELE_DEFAUT, vocabulaire: str = "", consigner: Callable[[str], None] = lambda _l: None) -> list[dict]:
    """Mots horodatés de toute la piste : [{debut, fin, mot}] (mot avec son espace initial, comme whisper le rend)."""
    if not disponible():
        raise RuntimeError("faster-whisper absent : pip install faster-whisper (requirements-postprod.txt)")
    son = _son(Path(source))
    duree = len(son) / TAUX
    consigner(f"transcription de {duree / 60:.0f} min de son…")
    segments, _info = _modele(modele, consigner).transcribe(
        son, language="fr", vad_filter=True, word_timestamps=True, initial_prompt=vocabulaire[:400] or None, condition_on_previous_text=False)
    mots: list[dict] = []
    palier = 0
    for seg in segments:
        for w in seg.words or []:
            if w.word.strip():
                mots.append({"debut": round(float(w.start), 3), "fin": round(float(w.end), 3), "mot": w.word})
        if duree and seg.end / duree * 10 >= palier + 1:
            palier = int(seg.end / duree * 10)
            consigner(f"transcription : {palier * 10} %")
    consigner(f"transcription : {len(mots)} mots")
    return mots


def sous_titres(mots: list[dict]) -> list[dict]:
    """Regroupe les mots en sous-titres : [{debut, fin, texte}] (≤ 84 caractères, ≤ 6 s, coupure après une ponctuation forte ou une pause)."""
    cues: list[dict] = []
    courant: list[dict] = []

    def fermer() -> None:
        if courant:
            cues.append({"debut": courant[0]["debut"], "fin": courant[-1]["fin"], "texte": "".join(m["mot"] for m in courant).strip()})
            courant.clear()

    for m in mots:
        if courant:
            texte = "".join(x["mot"] for x in courant) + m["mot"]
            trop = len(texte.strip()) > MAX_CARACTERES or m["fin"] - courant[0]["debut"] > MAX_DUREE_S
            pause = m["debut"] - courant[-1]["fin"] > 0.8
            if trop or pause:
                fermer()
        courant.append(m)
        if re.search(r"[.?!…]$", m["mot"].strip()) and m["fin"] - courant[0]["debut"] > 1.5:
            fermer()
    fermer()
    return cues


def _temps_vtt(s: float) -> str:
    s = max(0.0, s)
    h, reste = divmod(s, 3600)
    m, sec = divmod(reste, 60)
    return f"{int(h):02d}:{int(m):02d}:{sec:06.3f}"


def _deux_lignes(texte: str) -> str:
    if len(texte) <= MAX_LIGNE:
        return texte
    milieu = len(texte) // 2
    espaces = [i for i, c in enumerate(texte) if c == " "]
    if not espaces:
        return texte
    coupe = min(espaces, key=lambda i: abs(i - milieu))
    return texte[:coupe] + "\n" + texte[coupe + 1 :]


def vtt(cues: list[dict], debut: float = 0.0, fin: float | None = None) -> str:
    """WebVTT des sous-titres compris dans [debut, fin[, ramenés à 0 au début de l'intervalle."""
    lignes = ["WEBVTT", ""]
    n = 0
    for c in cues:
        if c["fin"] <= debut or (fin is not None and c["debut"] >= fin):
            continue
        a = max(c["debut"], debut) - debut
        b = (min(c["fin"], fin) if fin is not None else c["fin"]) - debut
        if b - a < 0.2:
            continue
        n += 1
        lignes += [str(n), f"{_temps_vtt(a)} --> {_temps_vtt(b)}", _deux_lignes(c["texte"]), ""]
    return "\n".join(lignes)


def paragraphes(mots: list[dict], debut: float = 0.0, fin: float | None = None) -> list[dict]:
    """Texte courant de [debut, fin[ en paragraphes (coupés aux pauses après une phrase, ou vers 600 caractères) : [{debut, texte}]."""
    choisis = [m for m in mots if m["debut"] >= debut and (fin is None or m["debut"] < fin)]
    resultat: list[dict] = []
    courant: list[dict] = []
    for i, m in enumerate(choisis):
        courant.append(m)
        texte = "".join(x["mot"] for x in courant).strip()
        suivant = choisis[i + 1] if i + 1 < len(choisis) else None
        fin_phrase = re.search(r"[.?!…]$", m["mot"].strip())
        if suivant is None or (fin_phrase and (suivant["debut"] - m["fin"] > PAUSE_PARAGRAPHE_S or len(texte) > 600)):
            resultat.append({"debut": round(courant[0]["debut"] - debut, 3), "texte": texte})
            courant = []
    return resultat


def texte_brut(paras: list[dict]) -> str:
    return "\n\n".join(p["texte"] for p in paras) + "\n"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", type=Path)
    p.add_argument("--modele", default=MODELE_DEFAUT, help="tiny, base, small (défaut), medium, large-v3")
    p.add_argument("--sortie", type=Path, help="dossier de sortie (défaut : celui de la source)")
    a = p.parse_args(argv)
    sortie = a.sortie or a.source.parent
    sortie.mkdir(parents=True, exist_ok=True)
    mots = transcrire(a.source, modele=a.modele, consigner=print)
    (sortie / f"{a.source.stem}.vtt").write_text(vtt(sous_titres(mots)), encoding="utf-8")
    (sortie / f"{a.source.stem}.txt").write_text(texte_brut(paragraphes(mots)), encoding="utf-8")
    print(f"→ {sortie / a.source.stem}.vtt / .txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Voix podcast / radio : débruitage IA (DeepFilterNet, facultatif) + coupe-bas + égalisation + de-esser + compression
+ normalisation du volume en deux passes à -16 LUFS (mesure EBU R128 par ebur128, gain fixe, limiteur à -1,5 dBTP).

Accepte un fichier audio (wav, m4a, mp3, flac…) ou vidéo (mp4, mkv, mov…) ou un dossier entier :
  - audio → <dossier>/traite_<preset>/<nom>.wav (48 kHz) ;
  - vidéo → <dossier>/traite_<preset>/<nom>.mp4, image copiée sans réencodage, son traité en AAC 192 kb/s.

    python postprod/voix.py "C:\\Users\\moi\\Videos\\Captations\\2026-11-08 21-29-03.mkv" --preset podcast
    python postprod/voix.py "C:\\Users\\moi\\Videos\\Captations" --preset radio
    python postprod/voix.py <fichier ou dossier> --sans-debruitage

Préréglages : `podcast` (naturel et chaleureux) et `radio` (plus dense, plus « présent »).
Débruitage : commande `deepFilter` (pip install deepfilternet) ; absente → étape sautée avec un avertissement.
Voir docs/postprod.md §4.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PRESETS = {
    "podcast": ",".join([
        "highpass=f=80",
        "equalizer=f=120:t=q:w=1:g=2",       # chaleur
        "equalizer=f=300:t=q:w=1.2:g=-3",    # retire la « boue »
        "equalizer=f=4000:t=q:w=1.5:g=2.5",  # présence, intelligibilité
        "highshelf=f=10000:g=2",             # air
        "deesser=i=0.4",
        "acompressor=threshold=-20dB:ratio=3:attack=10:release=150:makeup=2dB",
    ]),
    "radio": ",".join([
        "highpass=f=90",
        "lowshelf=f=110:g=4",                # voix « grave radio »
        "equalizer=f=300:t=q:w=1.2:g=-4",
        "equalizer=f=3500:t=q:w=1.5:g=4",
        "highshelf=f=9000:g=3",
        "deesser=i=0.6",
        "acompressor=threshold=-24dB:ratio=5:attack=5:release=100:makeup=4dB",
        "acompressor=threshold=-10dB:ratio=8:attack=1:release=50",  # 2e étage, très dense
    ]),
}
CIBLE_I, CIBLE_TP = -16.0, -1.5  # LUFS intégrés, crête max (dBTP)
EXT_AUDIO = {".wav", ".m4a", ".mp3", ".flac", ".aac", ".ogg", ".opus"}
EXT_VIDEO = {".mp4", ".mkv", ".mov", ".m4v", ".webm"}


def executer(cmd: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} a échoué ({r.returncode}) :\n{r.stderr[-1500:]}")
    return r


def debruitage_disponible() -> bool:
    return shutil.which("deepFilter") is not None


def extraire_wav(entree: Path, sortie: Path) -> Path:
    """Piste son → WAV 48 kHz (format attendu par DeepFilterNet)."""
    executer(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-i", str(entree), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(sortie)])
    return sortie


def debruiter(source: Path, temporaire: Path) -> Path:
    """DeepFilterNet ; renvoie le WAV débruité (le plus récent écrit dans le dossier temporaire)."""
    sortie = temporaire / "debruite"
    sortie.mkdir(exist_ok=True)
    executer(["deepFilter", str(source), "--output-dir", str(sortie)])
    produits = sorted(sortie.glob(f"{source.stem}*.wav"), key=lambda p: p.stat().st_mtime)
    if not produits:
        raise RuntimeError("DeepFilterNet n'a produit aucun fichier")
    return produits[-1]


def mesurer(source: Path, chaine: str) -> dict:
    """Passe 1 : volume intégré (LUFS) et crête vraie (dBTP) du son après la chaîne, par le filtre ebur128.

    ebur128 mesure comme loudnorm (EBU R128) mais sans suréchantillonner à 192 kHz : environ 10 fois plus rapide.
    """
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(source), "-af", f"{chaine},ebur128=peak=true:framelog=quiet",
                          "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    resume = err[err.rfind("Summary:"):] if "Summary:" in err else ""
    integre = re.search(r"\bI:\s*(-?[\d.]+|-inf)\s*LUFS", resume)
    crete = re.search(r"Peak:\s*(-?[\d.]+|-inf)\s*dBFS", resume)
    if not integre:
        raise RuntimeError(f"mesure ebur128 illisible :\n{err[-1500:]}")
    lire = lambda m, defaut: defaut if m is None or "inf" in m.group(1) else float(m.group(1))  # noqa: E731
    return {"I": lire(integre, -70.0), "TP": lire(crete, -70.0)}


GAIN_MAX_DB = 30.0  # un son presque muet n'est pas poussé au-delà
TOLERANCE_LU = 0.5


def filtre_final(mesures: dict, chaine: str, correction_db: float = 0.0) -> str:
    """Passe 2 : la chaîne, un gain fixe qui amène le volume intégré à CIBLE_I, puis un limiteur de crêtes sous CIBLE_TP.

    Équivaut au loudnorm « linéaire » en deux passes (même gain sur tout le fichier, pas de pompage), en bien plus rapide.
    `correction_db` compense le volume perdu quand le limiteur rabote beaucoup de crêtes (son saturé à l'origine).
    """
    gain = min(GAIN_MAX_DB, CIBLE_I - mesures["I"] + correction_db)
    plafond = 10 ** ((CIBLE_TP - 0.5) / 20)  # 0,5 dB de marge : le limiteur travaille sur les échantillons, pas sur la crête vraie
    return f"{chaine},volume={gain:.2f}dB,alimiter=limit={plafond:.4f}:level=disabled:latency=enabled"


_ENCODEUR_AAC: str | None = None


def encodeur_aac() -> str:
    """`aac_mf` (Media Foundation, Windows) quand il existe : environ 5 fois plus rapide que l'encodeur `aac` de ffmpeg."""
    global _ENCODEUR_AAC
    if _ENCODEUR_AAC is None:
        liste = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, errors="replace").stdout
        _ENCODEUR_AAC = "aac_mf" if re.search(r"^\s*A\S*\s+aac_mf\b", liste, re.M) else "aac"
    return _ENCODEUR_AAC


def traiter(entree: Path, dossier_sortie: Path, preset: str = "podcast", debruitage: bool = True, journal=print) -> Path:
    """Traite un fichier audio ou vidéo ; renvoie le fichier produit."""
    entree = Path(entree)
    if preset not in PRESETS:
        raise ValueError(f"préréglage inconnu : {preset}")
    est_video = entree.suffix.lower() in EXT_VIDEO
    dossier_sortie.mkdir(parents=True, exist_ok=True)
    sortie = dossier_sortie / (entree.stem + (".mp4" if est_video else ".wav"))
    chaine = PRESETS[preset]
    with tempfile.TemporaryDirectory(prefix="voix-") as t:
        temporaire = Path(t)
        son = extraire_wav(entree, temporaire / "source.wav")
        if debruitage:
            if debruitage_disponible():
                journal("  débruitage (DeepFilterNet)…")
                son = debruiter(son, temporaire)
            else:
                journal("  ⚠ DeepFilterNet absent (pip install deepfilternet) : débruitage sauté")
        journal("  mesure du volume…")
        mesures = mesurer(son, chaine)
        filtre = filtre_final(mesures, chaine)
        ecart = CIBLE_I - mesurer(son, filtre)["I"]  # contrôle après le limiteur
        if abs(ecart) > TOLERANCE_LU:
            journal(f"  correction de {ecart:+.1f} dB (crêtes rabotées par le limiteur)…")
            filtre = filtre_final(mesures, chaine, ecart)
        journal("  traitement et normalisation…")
        if est_video:
            executer(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-i", str(entree), "-i", str(son),
                      "-filter_complex", f"[1:a]{filtre},aresample=48000[voix]", "-map", "0:v:0", "-map", "[voix]",
                      "-c:v", "copy", "-c:a", encodeur_aac(), "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(sortie)])
        else:
            executer(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-i", str(son), "-af", filtre, "-ar", "48000", str(sortie)])
    return sortie


def fichiers_a_traiter(chemin: Path) -> list[Path]:
    if chemin.is_file():
        return [chemin]
    return sorted(f for f in chemin.iterdir() if f.is_file() and f.suffix.lower() in EXT_AUDIO | EXT_VIDEO)


def main(argv: list[str] | None = None) -> int:
    for flux in (sys.stdout, sys.stderr):  # console Windows en cp1252 : pas de plantage sur « → », « ✔ », « ⚠ »
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("chemin", type=Path, help="fichier audio / vidéo, ou dossier")
    p.add_argument("--preset", choices=sorted(PRESETS), default="podcast")
    p.add_argument("--sans-debruitage", action="store_true", help="ne pas passer par DeepFilterNet")
    p.add_argument("--sortie", type=Path, help="dossier de sortie (défaut : <dossier>/traite_<preset>)")
    a = p.parse_args(argv)
    if not a.chemin.exists():
        print(f"introuvable : {a.chemin}", file=sys.stderr)
        return 1
    if shutil.which("ffmpeg") is None:
        print("ffmpeg est requis (winget install Gyan.FFmpeg)", file=sys.stderr)
        return 1
    base = a.chemin.parent if a.chemin.is_file() else a.chemin
    sortie = a.sortie or base / f"traite_{a.preset}"
    fichiers = fichiers_a_traiter(a.chemin)
    if not fichiers:
        print(f"aucun fichier audio ou vidéo dans {a.chemin}", file=sys.stderr)
        return 1
    for f in fichiers:
        print(f"→ {f.name}")
        produit = traiter(f, sortie, a.preset, not a.sans_debruitage)
        print(f"  ✔ {produit}")
    print(f"Terminé : {sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

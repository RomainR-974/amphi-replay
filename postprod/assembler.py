#!/usr/bin/env python3
"""Recalage audio et assemblage du replay.

Entrées : l'enregistrement OBS (MKV ou MP4, piste de référence = micro DSLR/webcam)
et l'enregistrement du téléphone (WAV ou AAC, lavalier). Sortie : un MP4 dont la
vidéo est copiée sans réencodage et dont le son est la piste téléphone recalée,
corrigée en dérive, normalisée à -16 LUFS.

    python postprod/assembler.py obs.mkv telephone.wav [--sortie replay_2026-11-12.mp4]

Étapes :
  1. extraction de la piste de référence OBS en WAV mono ;
  2. décalage par corrélation croisée sur les 60 premières secondes (clap de début) ;
  3. contrôle sur les 60 dernières secondes (clap de fin) : la différence donne la dérive ;
  4. correction de la dérive par rééchantillonnage si elle dépasse 40 ms ;
  5. loudnorm -16 LUFS + passe-haut 80 Hz ;
  6. remux : vidéo copiée, audio AAC 192 kb/s, -shortest.
Un journal JSON des valeurs calculées accompagne la sortie.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

journal = logging.getLogger("assembler")

FREQUENCE_ANALYSE = 8000  # Hz : suffisant pour un clap, et 6× plus rapide que 48 kHz
FENETRE_S = 60.0
SEUIL_DERIVE_S = 0.040
PISTE_REFERENCE_OBS = 1  # index de la piste audio OBS servant au recalage (piste 2 = index 1)


@dataclass
class Mesures:
    duree_video_s: float
    duree_telephone_s: float
    decalage_debut_s: float
    decalage_fin_s: float
    derive_s: float
    facteur_vitesse: float
    confiance_debut: float
    confiance_fin: float
    derive_corrigee: bool


# ---------------------------------------------------------------------------------
# Corrélation croisée (pur numpy, testable sans ffmpeg)
# ---------------------------------------------------------------------------------


def decalage_par_correlation(reference: np.ndarray, signal: np.ndarray, frequence: int) -> tuple[float, float]:
    """Décalage (en secondes) à appliquer à `signal` pour l'aligner sur `reference`.

    Convention : signal[t] ≈ reference[t + d]. Le résultat d est positif quand le
    signal commence plus tard dans la scène que la référence (son début a été coupé),
    négatif quand il contient du son en plus avant le début de la référence.
    Renvoie aussi un indice de confiance (pic / médiane de l'enveloppe de corrélation).
    """
    if len(reference) == 0 or len(signal) == 0:
        raise ValueError("fenêtre audio vide : vérifier les durées et les pistes des deux fichiers")
    reference = _normaliser(reference)
    signal = _normaliser(signal)
    taille = 1 << (len(reference) + len(signal) - 1).bit_length()
    fr = np.fft.rfft(reference, taille)
    fs = np.fft.rfft(signal, taille)
    # correlation[k] = Σ_t reference[t + k] · signal[t] ; les k négatifs sont repliés en fin de tableau.
    correlation = np.fft.irfft(fr * np.conj(fs), taille)
    k = int(np.argmax(np.abs(correlation)))
    if k > taille // 2:
        k -= taille
    pic = float(np.abs(correlation).max())
    mediane = float(np.median(np.abs(correlation))) or 1e-12
    return k / frequence, pic / mediane


def _normaliser(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float64)
    x -= x.mean()
    ecart = x.std()
    return x / ecart if ecart > 0 else x


# ---------------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------------


def executer(commande: list[str]) -> str:
    journal.debug("$ %s", " ".join(commande))
    resultat = subprocess.run(commande, capture_output=True, text=True)
    if resultat.returncode != 0:
        raise RuntimeError(f"commande échouée ({resultat.returncode}) : {' '.join(commande)}\n{resultat.stderr[-2000:]}")
    return resultat.stdout


def duree(fichier: Path) -> float:
    sortie = executer(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(fichier)])
    return float(json.loads(sortie)["format"]["duration"])


def nb_pistes_audio(fichier: Path) -> int:
    sortie = executer(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "json", str(fichier)])
    return len(json.loads(sortie).get("streams", []))


def extraire_wav(source: Path, destination: Path, *, piste: int | None, debut: float | None, duree_s: float | None) -> None:
    commande = ["ffmpeg", "-y", "-v", "error"]
    if debut is not None:
        commande += ["-ss", f"{debut:.3f}"]
    commande += ["-i", str(source)]
    if duree_s is not None:
        commande += ["-t", f"{duree_s:.3f}"]
    if piste is not None:
        commande += ["-map", f"0:a:{piste}"]
    commande += ["-ac", "1", "-ar", str(FREQUENCE_ANALYSE), "-c:a", "pcm_s16le", str(destination)]
    executer(commande)


def lire_wav(fichier: Path) -> np.ndarray:
    with wave.open(str(fichier), "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


# ---------------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------------


def mesurer(obs: Path, telephone: Path, dossier: Path, piste_ref: int) -> Mesures:
    duree_video = duree(obs)
    duree_tel = duree(telephone)
    fenetre = min(FENETRE_S, duree_video / 3, duree_tel / 3)

    def correler(debut_obs: float, debut_tel: float, nom: str) -> tuple[float, float]:
        ref, sig = dossier / f"ref_{nom}.wav", dossier / f"tel_{nom}.wav"
        extraire_wav(obs, ref, piste=piste_ref, debut=debut_obs, duree_s=fenetre)
        # Le téléphone a pu démarrer jusqu'à ±fenêtre/2 avant ou après OBS : fenêtre élargie.
        marge = fenetre / 2
        extraire_wav(telephone, sig, piste=None, debut=max(0.0, debut_tel - marge), duree_s=fenetre + 2 * marge)
        debut_sig = max(0.0, debut_tel - marge)
        d, confiance = decalage_par_correlation(lire_wav(ref), lire_wav(sig), FREQUENCE_ANALYSE)
        # sig[t] ≈ ref[t + d] : l'instant `debut_sig` du téléphone correspond à l'instant `debut_obs + d` d'OBS.
        # Décalage absolu D = temps téléphone − temps OBS pour un même événement (> 0 : téléphone lancé plus tôt).
        return debut_sig - (debut_obs + d), confiance

    # Hypothèse initiale : les deux fichiers ont démarré à peu près en même temps.
    dec_debut, conf_debut = correler(0.0, 0.0, "debut")
    fin_obs = max(0.0, duree_video - fenetre)
    dec_fin, conf_fin = correler(fin_obs, max(0.0, fin_obs + dec_debut), "fin")

    # Dérive : si D augmente, le téléphone "retarde" (son horloge est lente) et doit être accéléré.
    derive = dec_fin - dec_debut
    facteur = 1.0 + derive / max(fin_obs, 1.0) if abs(derive) > SEUIL_DERIVE_S else 1.0
    return Mesures(
        duree_video_s=round(duree_video, 3),
        duree_telephone_s=round(duree_tel, 3),
        decalage_debut_s=round(dec_debut, 4),
        decalage_fin_s=round(dec_fin, 4),
        derive_s=round(derive, 4),
        facteur_vitesse=facteur,
        confiance_debut=round(conf_debut, 1),
        confiance_fin=round(conf_fin, 1),
        derive_corrigee=facteur != 1.0,
    )


def assembler(obs: Path, telephone: Path, sortie: Path, mesures: Mesures, *, garder_reference: bool) -> None:
    """Remux : vidéo OBS copiée, piste téléphone décalée, corrigée, normalisée."""
    # `decalage_debut_s` > 0 : le son du téléphone est en avance dans son fichier par rapport à la vidéo
    # (il a été lancé plus tôt) → on le découpe ; < 0 → on insère un silence.
    filtres = []
    if mesures.facteur_vitesse != 1.0:
        # asetrate accélère (facteur > 1) ou ralentit la lecture ; la hauteur change de façon imperceptible
        # pour une dérive < 0,1 %. aresample ramène ensuite à 48 kHz.
        filtres.append(f"asetrate=48000*{mesures.facteur_vitesse:.9f},aresample=48000")
    else:
        filtres.append("aresample=48000")
    if mesures.decalage_debut_s < 0:
        filtres.append(f"adelay={int(round(-mesures.decalage_debut_s * 1000))}:all=1")
    filtres += ["highpass=f=80", "loudnorm=I=-16:TP=-1.5:LRA=11"]

    commande = ["ffmpeg", "-y", "-v", "error", "-stats", "-i", str(obs)]
    if mesures.decalage_debut_s > 0:
        commande += ["-ss", f"{mesures.decalage_debut_s:.4f}"]
    commande += ["-i", str(telephone)]
    commande += ["-filter_complex", f"[1:a]{','.join(filtres)}[voix]"]
    commande += ["-map", "0:v:0", "-map", "[voix]"]
    if garder_reference:
        commande += ["-map", f"0:a:{PISTE_REFERENCE_OBS}?"]
    commande += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(sortie)]
    executer(commande)


def principal(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    analyseur.add_argument("obs", type=Path, help="enregistrement OBS (MKV ou MP4)")
    analyseur.add_argument("telephone", type=Path, help="enregistrement du téléphone (WAV, M4A…)")
    analyseur.add_argument("--sortie", type=Path, default=None, help="MP4 de sortie (défaut : replay_AAAA-MM-JJ.mp4)")
    analyseur.add_argument("--piste-reference", type=int, default=PISTE_REFERENCE_OBS, help="index de la piste audio OBS de référence (défaut 1 = piste 2)")
    analyseur.add_argument("--garder-reference", action="store_true", help="conserver la piste de référence OBS en seconde piste")
    analyseur.add_argument("--mesurer-seulement", action="store_true", help="afficher les mesures sans produire de fichier")
    analyseur.add_argument("-v", "--verbeux", action="store_true")
    args = analyseur.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbeux else logging.INFO, format="%(levelname)s %(message)s")

    for outil in ("ffmpeg", "ffprobe"):
        if not shutil.which(outil):
            journal.error("%s introuvable dans le PATH", outil)
            return 2
    for f in (args.obs, args.telephone):
        if not f.exists():
            journal.error("fichier introuvable : %s", f)
            return 2

    piste = args.piste_reference
    if nb_pistes_audio(args.obs) <= piste:
        journal.warning("OBS n'a que %d piste(s) audio : utilisation de la piste 0 comme référence", nb_pistes_audio(args.obs))
        piste = 0

    sortie = args.sortie or Path(f"replay_{dt.date.today().isoformat()}.mp4")
    with tempfile.TemporaryDirectory(prefix="assembler_") as tmp:
        journal.info("mesure du décalage et de la dérive…")
        mesures = mesurer(args.obs, args.telephone, Path(tmp), piste)
        journal.info("décalage début %.3f s, fin %.3f s, dérive %.3f s (confiance %.0f / %.0f)",
                     mesures.decalage_debut_s, mesures.decalage_fin_s, mesures.derive_s, mesures.confiance_debut, mesures.confiance_fin)
        if mesures.confiance_debut < 8:
            journal.warning("confiance faible sur le clap de début : vérifier la synchro à l'oreille")
        if mesures.derive_corrigee:
            journal.info("dérive > %d ms : correction par rééchantillonnage (facteur %.8f)", SEUIL_DERIVE_S * 1000, mesures.facteur_vitesse)
        if args.mesurer_seulement:
            print(json.dumps(asdict(mesures), indent=2, ensure_ascii=False))
            return 0
        journal.info("assemblage → %s", sortie)
        assembler(args.obs, args.telephone, sortie, mesures, garder_reference=args.garder_reference)

    journal_sortie = sortie.with_suffix(".json")
    journal_sortie.write_text(json.dumps({"obs": str(args.obs), "telephone": str(args.telephone), "sortie": str(sortie), **asdict(mesures)}, indent=2, ensure_ascii=False))
    journal.info("terminé : %s (journal %s)", sortie, journal_sortie)
    return 0


if __name__ == "__main__":
    sys.exit(principal())

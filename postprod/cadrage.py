#!/usr/bin/env python3
"""Suivi de l'intervenant dans la vidéo caméra (replay interactif, étape 5 ; docs/replay-interactif.md §5).

Analyse la vidéo quelques fois par seconde avec le détecteur de visages YuNet d'OpenCV, garde le visage principal,
puis lisse la trajectoire comme un cadreur : zone morte (les petits mouvements de tête ne font pas bouger le cadre),
suivi doux, aucun retard (lissage aller-retour, possible puisqu'on travaille après coup), position gardée quand le
visage est perdu un moment. Le lecteur recadre ensuite la vidéo dans le navigateur, selon la forme de l'écran
(vignette ronde, portrait de téléphone) : aucun réencodage, une seule vidéo pour tous les formats.

    python postprod/cadrage.py "<camera.mkv>" [--sortie cadrage.json]

Sortie (JSON) : {"version": 1, "pas": 0.4, "source": [L, H], "points": [[cx, cy, h], …]}
  point i = instant i × pas (s) ; cx, cy = centre du visage, h = hauteur du visage, en fractions de l'image (0 à 1).
Dépendances : opencv-python-headless, numpy, ffmpeg. Modèle : data/modeles/face_detection_yunet_2023mar.onnx
(téléchargé au premier usage depuis github.com/opencv/opencv_zoo).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
MODELE = RACINE / "data" / "modeles" / "face_detection_yunet_2023mar.onnx"
URL_MODELE = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
PAS_S = 0.4                 # une analyse toutes les 0,4 s (2,5 images/s) : largement assez pour un cadrage doux
LARGEUR_ANALYSE = 640       # image réduite pour la détection (le visage d'un intervenant y fait > 30 px)
ZONE_MORTE = 0.035          # écart (fraction de l'image) en dessous duquel le cadre ne bouge pas
LISSAGE = 0.18              # coefficient du lissage exponentiel (par pas), appliqué à l'aller puis au retour
PERTE_MAX_S = 20.0          # au-delà, sans visage, le cadre revient doucement au centre de la dernière zone connue


def assurer_modele(journal=print) -> Path:
    if not MODELE.is_file():
        MODELE.parent.mkdir(parents=True, exist_ok=True)
        journal(f"téléchargement du détecteur de visages YuNet ({URL_MODELE})…")
        urllib.request.urlretrieve(URL_MODELE, MODELE)
    return MODELE


def dimensions(video: Path) -> tuple[int, int, float]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                        "-of", "json", str(video)], capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    return int(d["streams"][0]["width"]), int(d["streams"][0]["height"]), float(d["format"]["duration"])


def detecter(video: Path, journal=print) -> tuple[list[tuple[float, float, float] | None], tuple[int, int]]:
    """Une mesure par pas : (cx, cy, h) du visage principal en fractions de l'image, ou None."""
    import cv2  # noqa: PLC0415 (dépendance facultative de la post-production)
    import numpy as np  # noqa: PLC0415

    largeur, hauteur, duree = dimensions(video)
    l = LARGEUR_ANALYSE
    h = int(round(hauteur * l / largeur / 2)) * 2
    detecteur = cv2.FaceDetectorYN.create(str(assurer_modele(journal)), "", (l, h), 0.6, 0.3, 50)
    commande = ["ffmpeg", "-v", "error", "-hwaccel", "auto", "-i", str(video), "-an",
                "-vf", f"fps={1 / PAS_S},scale={l}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(commande, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    taille = l * h * 3
    mesures: list[tuple[float, float, float] | None] = []
    precedent: tuple[float, float, float] | None = None
    attendu = max(1, int(duree / PAS_S))
    while True:
        brut = proc.stdout.read(taille)
        if len(brut) < taille:
            break
        image = np.frombuffer(brut, np.uint8).reshape(h, l, 3)
        _, visages = detecteur.detect(image)
        mesure = None
        if visages is not None and len(visages):
            # Visage principal : le plus grand, avec une prime de continuité (on ne saute pas sur un visage du public).
            def note(v):
                cx, cy, hv = float((v[0] + v[2] / 2) / l), float((v[1] + v[3] / 2) / h), float(v[3] / h)
                prime = 1.0
                if precedent is not None:
                    prime = 1.0 / (1.0 + 6 * ((cx - precedent[0]) ** 2 + (cy - precedent[1]) ** 2) ** 0.5)
                return v[2] * v[3] * float(v[14]) * prime, (cx, cy, hv)
            _, mesure = max((note(v) for v in visages), key=lambda x: x[0])
            precedent = mesure
        mesures.append(mesure)
        if len(mesures) % 250 == 0:
            journal(f"  suivi : {min(100, round(100 * len(mesures) / attendu))} %")
    proc.wait()
    return mesures, (largeur, hauteur)


def _mediane(valeurs: list[float], rayon: int) -> list[float]:
    sortie = []
    for i in range(len(valeurs)):
        fenetre = sorted(valeurs[max(0, i - rayon): i + rayon + 1])
        sortie.append(fenetre[len(fenetre) // 2])
    return sortie


def _zone_morte(valeurs: list[float], seuil: float) -> list[float]:
    """Élastique : la position ne bouge que si la mesure s'en écarte de plus de `seuil`, et seulement de l'excédent."""
    sortie, p = [], valeurs[0]
    for x in valeurs:
        if x > p + seuil:
            p = x - seuil
        elif x < p - seuil:
            p = x + seuil
        sortie.append(p)
    return sortie


def _aller_retour(valeurs: list[float], alpha: float) -> list[float]:
    """Lissage exponentiel à l'aller puis au retour : doux et sans retard."""
    aller, p = [], valeurs[0]
    for x in valeurs:
        p += alpha * (x - p)
        aller.append(p)
    retour, p = [], aller[-1]
    for x in reversed(aller):
        p += alpha * (x - p)
        retour.append(p)
    return retour[::-1]


def lisser(mesures: list[tuple[float, float, float] | None]) -> list[tuple[float, float, float]]:
    """Trajectoire du cadre : trous comblés, aberrations écartées, zone morte, lissage aller-retour."""
    if not mesures:
        return []
    connus = [m for m in mesures if m is not None]
    if not connus:
        return [(0.5, 0.42, 0.22)] * len(mesures)  # personne : plan centré, un peu haut (tête et épaules)
    perte_max = int(PERTE_MAX_S / PAS_S)
    rempli: list[tuple[float, float, float]] = []
    dernier, absent = connus[0], 0
    for m in mesures:
        if m is None:
            absent += 1
            if absent > perte_max:  # longue absence : on desserre vers un plan plus large autour de la dernière zone
                dernier = (dernier[0], dernier[1], min(0.35, dernier[2] * 1.01))
            rempli.append(dernier)
        else:
            absent, dernier = 0, m
            rempli.append(m)
    rayon = 3  # médiane sur ±1,2 s : un faux positif isolé disparaît
    cx = _aller_retour(_zone_morte(_mediane([p[0] for p in rempli], rayon), ZONE_MORTE), LISSAGE)
    cy = _aller_retour(_zone_morte(_mediane([p[1] for p in rempli], rayon), ZONE_MORTE), LISSAGE)
    hv = _aller_retour(_mediane([p[2] for p in rempli], rayon * 3), LISSAGE / 3)  # la taille bouge encore moins
    return list(zip(cx, cy, hv))


def cadrage(video: Path, journal=print) -> dict:
    mesures, source = detecter(Path(video), journal)
    trouves = sum(1 for m in mesures if m is not None)
    journal(f"  visage trouvé sur {trouves}/{len(mesures)} images analysées ({round(100 * trouves / max(1, len(mesures)))} %)")
    points = [[round(x, 4), round(y, 4), round(t, 4)] for x, y, t in lisser(mesures)]
    return {"version": 1, "pas": PAS_S, "source": list(source), "detection": round(trouves / max(1, len(mesures)), 3), "points": points}


def main(argv: list[str] | None = None) -> int:
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video", type=Path)
    p.add_argument("--sortie", type=Path, help="défaut : <vidéo>.cadrage.json")
    a = p.parse_args(argv)
    sortie = a.sortie or a.video.with_suffix(".cadrage.json")
    resultat = cadrage(a.video)
    sortie.write_text(json.dumps(resultat, separators=(",", ":")), encoding="utf-8")
    print(f"✔ {sortie} ({len(resultat['points'])} points)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

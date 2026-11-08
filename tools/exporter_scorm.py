#!/usr/bin/env python3
"""Export SCORM 1.2 d'un replay en ligne de commande (même code que le serveur : server/scorm.py ; voir docs/scorm.md).

    python tools/exporter_scorm.py presentations/iag-v1/replays/2026-11-10_0902 [--sans-quiz] [--video-unique] [--titre …]

Le dossier de la présentation est déduit du chemin du replay (presentations/<slug>/replays/<id>). Le zip est écrit dans
<replay>/scorm/<slug>-<id>-scorm.zip. ffmpeg et ffprobe sont requis (extraits manquants, affiche).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from server.scorm import construire_scorm  # noqa: E402


def principal(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(description="Construit le paquet SCORM 1.2 d'un replay chapitré.")
    parseur.add_argument("replay", help="dossier du replay : presentations/<slug>/replays/<id>")
    parseur.add_argument("--sans-quiz", action="store_true", help="ne pas inclure les questions des quiz")
    parseur.add_argument("--video-unique", action="store_true", help="une seule vidéo (replay.mp4) et des chapitres par instants, au lieu d'une vidéo par chapitre")
    parseur.add_argument("--titre", default=None, help="titre du module (défaut : titre du replay)")
    parseur.add_argument("--presentation", default=None, help="dossier de la présentation (défaut : déduit du chemin du replay)")
    parseur.add_argument("-q", "--silencieux", action="store_true", help="n'afficher que le chemin du zip")
    args = parseur.parse_args(argv)

    replay = Path(args.replay).resolve()
    if not (replay / "replay.mp4").is_file():
        print(f"erreur : {replay} ne contient pas de replay.mp4", file=sys.stderr)
        return 2
    presentation = Path(args.presentation).resolve() if args.presentation else replay.parent.parent
    if not (presentation / "slides.html").is_file() and not (presentation / "presentation.json").is_file():
        print(f"attention : {presentation} ne ressemble pas à un dossier de présentation (slides.html / presentation.json absents) : pas de questions", file=sys.stderr)
    consigner = (lambda ligne: None) if args.silencieux else (lambda ligne: print(ligne, file=sys.stderr))
    try:
        zip_path = construire_scorm(presentation, replay, titre=args.titre, inclure_quiz=not args.sans_quiz, video_unique=args.video_unique, journal=consigner)
    except (ValueError, RuntimeError) as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 1
    print(zip_path)
    return 0


if __name__ == "__main__":
    sys.exit(principal())

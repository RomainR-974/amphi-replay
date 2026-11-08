#!/usr/bin/env python3
"""Fabrique un replay interactif et son paquet SCORM 1.2 (docs/replay-interactif.md), sur le PC 2.

    .venv\\Scripts\\python tools\\replay_interactif.py "C:\\Users\\moi\\Videos\\Captations\\2026-11-09 14-02-11 camera.mkv" --prez iag-v2

Par défaut : journal et copie figée lus sur https://q.phare360.com (jeton : AMPHI_JETON de lancer-seance.local.bat),
voix « podcast », cadrage de l'intervenant, sortie dans presentations/<prez>/replays-interactifs/<AAAA-MM-JJ_HHMM>/
et paquet <prez>-<AAAA-MM-JJ_HHMM>-interactif-scorm.zip à côté (à déposer dans Moodle : activité « Paquetage SCORM »).

Options utiles :
  --deck <dossier>       présentation à utiliser si le serveur n'a pas de copie figée de la séance
  --debut <epoch>        instant (horloge du serveur) de la première image de la vidéo, si l'ancre manque
  --decalage <s>         retard du fichier caméra sur l'enregistrement principal, si le journal d'OBS manque
  --voix podcast|radio|aucune   --sans-cadrage   --titre "…"   --encodeur h264_nvenc|h264_qsv|libx264
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from server.interactif import construire_replay_interactif, paquet_scorm  # noqa: E402

SERVEUR_DEFAUT = "https://q.phare360.com"


def secret(nom: str) -> str:
    fichier = RACINE / "lancer-seance.local.bat"
    m = re.search(rf'{nom}=([^"\r\n]*)', fichier.read_text(encoding="utf-8")) if fichier.exists() else None
    return m.group(1) if m else ""


def main(argv: list[str] | None = None) -> int:
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video", type=Path, help="vidéo caméra (Source Record : « <date> camera.mkv »)")
    p.add_argument("--prez", required=True, help="présentation (slug), ex. iag-v2")
    p.add_argument("--seance", default=SERVEUR_DEFAUT, help=f"serveur de la séance (défaut : {SERVEUR_DEFAUT})")
    p.add_argument("--jeton", default=None, help="défaut : AMPHI_JETON de lancer-seance.local.bat")
    p.add_argument("--deck", type=Path, default=None)
    p.add_argument("--debut", type=float, default=None)
    p.add_argument("--decalage", type=float, default=None)
    p.add_argument("--voix", choices=["podcast", "radio", "aucune"], default="podcast")
    p.add_argument("--sans-cadrage", action="store_true")
    p.add_argument("--titre", default=None)
    p.add_argument("--encodeur", default=None)
    p.add_argument("--sortie", type=Path, default=None, help="dossier du replay (défaut : presentations/<prez>/replays-interactifs/<date>)")
    p.add_argument("--sans-scorm", action="store_true", help="ne pas fabriquer le zip SCORM")
    a = p.parse_args(argv)
    if not a.video.is_file():
        print(f"vidéo introuvable : {a.video}", file=sys.stderr)
        return 1

    id_replay = datetime.fromtimestamp(a.video.stat().st_mtime).strftime("%Y-%m-%d_%H%M")
    base = RACINE / "presentations" / a.prez / "replays-interactifs"
    sortie = a.sortie or base / id_replay
    debut = time.time()
    donnees = construire_replay_interactif(
        a.video, a.prez, sortie, serveur=a.seance, jeton=a.jeton if a.jeton is not None else secret("AMPHI_JETON"),
        deck=a.deck, dossier_presentation=RACINE / "presentations" / a.prez, debut=a.debut, decalage=a.decalage,
        voix=a.voix, cadrage=not a.sans_cadrage, titre=a.titre, encodeur=a.encodeur, consigner=print)
    if not a.sans_scorm:
        paquet_scorm(sortie, sortie.parent / f"{a.prez}-{sortie.name}-interactif-scorm.zip", consigner=print)
    print(f"Terminé en {time.time() - debut:.0f} s · « {donnees['titre']} »")
    print(f"Aperçu : ouvrir {sortie / 'index.html'} dans un navigateur")
    return 0


if __name__ == "__main__":
    sys.exit(main())

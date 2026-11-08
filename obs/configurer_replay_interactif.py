#!/usr/bin/env python3
"""Configure OBS (PC 2) pour le replay interactif : enregistre la caméra seule, en plus de l'enregistrement habituel.

Prérequis : plugin Source Record (Exeldro, ≥ 0.4) installé, OBS ouvert, obs-websocket activé.

    python obs/configurer_replay_interactif.py                 # configure (sans rien enregistrer)
    python obs/configurer_replay_interactif.py --tester        # configure puis enregistre 6 s pour vérifier
    python obs/configurer_replay_interactif.py --retirer       # retire le filtre (retour à l'état d'avant)

Ce que fait le script (idempotent, rien n'est créé en double) :
  - pose un filtre Source Record « Enregistrement caméra (replay interactif) » sur la source caméra, en tête de ses
    filtres (avant un éventuel masque) : il démarre et s'arrête avec l'enregistrement principal (F9, diapositives
    data-obs) et écrit « <date> camera.mkv » dans le dossier des captations, encodé en matériel (QuickSync / NVENC) ;
  - le fichier caméra démarre 0,5 à 2 s après le principal (initialisation de l'encodeur) : le journal d'OBS
    (« Writing file », à la milliseconde) donne ce décalage à la post-production ;
  - son de ce fichier : piste audio 2 d'OBS, réservée au micro (le son du bureau est retiré de la piste 2) ;
    l'enregistrement habituel (mode simple : piste 1) n'est pas modifié.
Les scènes, sources et réglages de l'enregistrement composite ne sont pas touchés. Voir obs/README.md.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from obs_quiz_bridge import Obs  # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
NOM_FILTRE = "Enregistrement caméra (replay interactif)"
SUFFIXE = "camera"
PISTE_MICRO = 2
# Clés du filtre Source Record 0.5 (record_mode 3 = « Recording » : suit l'enregistrement principal).
MODE_ENREGISTREMENT = 3


def reglages_filtre(dossier: str, encodeur: str) -> dict:
    return {
        "record_mode": MODE_ENREGISTREMENT,
        "path": dossier,
        "filename_formatting": f"%CCYY-%MM-%DD %hh-%mm-%ss {SUFFIXE}",
        "rec_format": "mkv",            # survit à un plantage, comme l'enregistrement principal
        "encoder": encodeur,
        "rate_control": "VBR",
        "bitrate": 3000,                # caméra seule (personne qui parle) : ≈ 2,7 Go pour 2 h
        "keyint_sec": 2,
        "audio_track": PISTE_MICRO,
        "audio_encoder": "ffmpeg_aac",
        "audio_bitrate": 192,
        "split_file": False,
    }


def encodeur_par_defaut() -> str:
    """NVENC si le pilote NVIDIA fournit son API d'encodage, sinon QuickSync (Intel Iris Xe du PC 2)."""
    return "nvenc" if (Path("C:/Windows/System32") / "nvEncodeAPI64.dll").exists() else "qsv"


def lire_mdp() -> str:
    fichier = RACINE / "lancer-seance.local.bat"
    m = re.search(r'OBS_MDP=([^"\r\n]*)', fichier.read_text(encoding="utf-8")) if fichier.exists() else None
    return m.group(1) if m else ""


async def source_camera(o: Obs, nom: str | None) -> str:
    entrees = (await o.requete("GetInputList"))["inputs"]
    if nom:
        if not any(i["inputName"] == nom for i in entrees):
            raise SystemExit(f"source « {nom} » introuvable")
        return nom
    cameras = [i["inputName"] for i in entrees if i["inputKind"] == "dshow_input"]
    if len(cameras) != 1:
        raise SystemExit(f"{len(cameras)} source(s) caméra trouvée(s) {cameras} : préciser --camera")
    return cameras[0]


async def configurer(o: Obs, camera: str, encodeur: str) -> None:
    sortie = (await o.requete("GetRecordDirectory"))["recordDirectory"]
    filtres = (await o.requete("GetSourceFilterList", {"sourceName": camera}))["filters"]
    reglages = reglages_filtre(sortie, encodeur)
    if any(f["filterName"] == NOM_FILTRE for f in filtres):
        await o.requete("SetSourceFilterSettings", {"sourceName": camera, "filterName": NOM_FILTRE, "filterSettings": reglages, "overlay": True})
        print(f"filtre mis à jour sur « {camera} »")
    else:
        await o.requete("CreateSourceFilter", {"sourceName": camera, "filterName": NOM_FILTRE, "filterKind": "source_record_filter", "filterSettings": reglages})
        print(f"filtre créé sur « {camera} »")
    await o.requete("SetSourceFilterIndex", {"sourceName": camera, "filterName": NOM_FILTRE, "filterIndex": 0})
    await o.requete("SetSourceFilterEnabled", {"sourceName": camera, "filterName": NOM_FILTRE, "filterEnabled": True})
    print(f"  → « <date> {SUFFIXE}.mkv » dans {sortie}, encodeur {encodeur}, son = piste {PISTE_MICRO}")

    # Piste 2 = micro seul : le son du bureau (et toute autre capture de sortie) en est retiré.
    for entree in (await o.requete("GetInputList"))["inputs"]:
        try:
            pistes = (await o.requete("GetInputAudioTracks", {"inputName": entree["inputName"]}))["inputAudioTracks"]
        except RuntimeError:
            continue  # pas de son
        voulu = entree["inputKind"] == "wasapi_input_capture"
        if pistes.get(str(PISTE_MICRO)) != voulu:
            await o.requete("SetInputAudioTracks", {"inputName": entree["inputName"], "inputAudioTracks": {str(PISTE_MICRO): voulu}})
            print(f"  piste {PISTE_MICRO} : « {entree['inputName']} » {'ajouté' if voulu else 'retiré'}")


async def tester(o: Obs) -> int:
    sortie = Path((await o.requete("GetRecordDirectory"))["recordDirectory"])
    if (await o.requete("GetRecordStatus"))["outputActive"]:
        print("un enregistrement est déjà en cours : test annulé")
        return 1
    debut = time.time()
    await o.requete("StartRecord")
    print("test : enregistrement de 6 s…")
    await asyncio.sleep(6)
    principal = (await o.requete("StopRecord")).get("outputPath")
    await asyncio.sleep(2)
    cameras = [f for f in sortie.glob(f"*{SUFFIXE}.mkv") if f.stat().st_mtime >= debut - 1]
    print("  principal :", principal)
    print("  caméra    :", cameras[0] if cameras else "AUCUN FICHIER")
    return 0 if cameras else 1


async def retirer(o: Obs, camera: str) -> None:
    filtres = (await o.requete("GetSourceFilterList", {"sourceName": camera}))["filters"]
    if any(f["filterName"] == NOM_FILTRE for f in filtres):
        await o.requete("RemoveSourceFilter", {"sourceName": camera, "filterName": NOM_FILTRE})
        print(f"filtre retiré de « {camera} »")
    else:
        print("aucun filtre à retirer")


async def principal(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--obs", default="ws://127.0.0.1:4455")
    p.add_argument("--obs-mdp", default=None, help="défaut : OBS_MDP de lancer-seance.local.bat")
    p.add_argument("--camera", default=None, help="nom de la source caméra (défaut : la seule source dshow)")
    p.add_argument("--encodeur", default=encodeur_par_defaut(), help="qsv (Intel QuickSync), nvenc (NVIDIA), x264 (processeur)… ; défaut : nvenc si le pilote NVIDIA l'offre, sinon qsv")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--tester", action="store_true", help="enregistre 6 s pour vérifier que le fichier caméra est produit")
    g.add_argument("--retirer", action="store_true")
    a = p.parse_args(argv)
    o = Obs(a.obs, a.obs_mdp if a.obs_mdp is not None else lire_mdp())
    await o.connecter()
    try:
        kinds = (await o.requete("GetSourceFilterKindList"))["sourceFilterKinds"]
        if "source_record_filter" not in kinds:
            print("plugin Source Record absent : https://obsproject.com/forum/resources/source-record.1285/", file=sys.stderr)
            return 1
        camera = await source_camera(o, a.camera)
        if a.retirer:
            await retirer(o, camera)
            return 0
        await configurer(o, camera, a.encodeur)
        return await tester(o) if a.tester else 0
    finally:
        await o.ws.close()


if __name__ == "__main__":
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(principal()))

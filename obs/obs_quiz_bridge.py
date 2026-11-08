#!/usr/bin/env python3
"""Pont serveur → OBS : force la scène « Diapositives + vignette » tant qu'une question de quiz est ouverte.

Tourne sur le PC 2, à côté d'OBS (obs-websocket v5 activé dans Outils → Paramètres du serveur WebSocket).
Le serveur du VPS ne peut pas joindre OBS derrière le hotspot : c'est donc ce script, côté PC 2,
qui écoute /ws/quiz et parle à OBS en local.

    python obs/obs_quiz_bridge.py wss://q.exemple.fr --obs-mdp MOTDEPASSE --scene "Diapositives + vignette"

À l'ouverture d'une question : sauvegarde la scène courante, bascule sur la scène de quiz.
À la fermeture : revient à la scène sauvegardée. Advanced Scene Switcher reprend ensuite sa boucle.
Si ce script tombe, rien ne casse : raccourcis F1/F2/F3 en secours.

Séquence questions-réponses (docs/questions.md) : le serveur relaie `{"type": "qr", "actif": true}` sur /ws/quiz ;
le pont arrête alors l'enregistrement OBS (`StopRecord`) après 2 s, une fois par séquence, sauf `--sans-arret`.
À la fermeture de la séquence, l'enregistrement n'est pas relancé : les questions-réponses ne sont pas dans le replay.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import sys

import websockets


class Obs:
    """Client minimal obs-websocket v5 (identification + requêtes)."""

    def __init__(self, url: str, mot_de_passe: str) -> None:
        self.url, self.mot_de_passe, self.ws, self._id = url, mot_de_passe, None, 0

    async def connecter(self) -> None:
        self.ws = await websockets.connect(self.url, open_timeout=5)
        bonjour = json.loads(await self.ws.recv())
        identification: dict = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = bonjour["d"].get("authentication")
        if auth:
            secret = base64.b64encode(hashlib.sha256((self.mot_de_passe + auth["salt"]).encode()).digest()).decode()
            identification["authentication"] = base64.b64encode(hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
        await self.ws.send(json.dumps({"op": 1, "d": identification}))
        reponse = json.loads(await self.ws.recv())
        if reponse.get("op") != 2:
            raise RuntimeError(f"identification OBS refusée : {reponse}")

    async def requete(self, type_: str, donnees: dict | None = None) -> dict:
        self._id += 1
        await self.ws.send(json.dumps({"op": 6, "d": {"requestType": type_, "requestId": str(self._id), "requestData": donnees or {}}}))
        while True:
            m = json.loads(await self.ws.recv())
            if m.get("op") == 7 and m["d"]["requestId"] == str(self._id):
                if not m["d"]["requestStatus"]["result"]:
                    raise RuntimeError(m["d"]["requestStatus"].get("comment", type_))
                return m["d"].get("responseData", {})

    async def scene_courante(self) -> str:
        return (await self.requete("GetCurrentProgramScene"))["currentProgramSceneName"]

    async def changer_scene(self, nom: str) -> None:
        await self.requete("SetCurrentProgramScene", {"sceneName": nom})

    async def arreter_enregistrement(self) -> bool:
        """StopRecord ; False si OBS n'enregistrait pas (requête refusée), sans faire tomber le pont."""
        try:
            await self.requete("StopRecord")
        except RuntimeError as e:
            print("OBS : enregistrement non arrêté :", e, file=sys.stderr)
            return False
        return True


DELAI_ARRET_S = 2.0


async def arreter_apres_delai(obs: Obs) -> None:
    await asyncio.sleep(DELAI_ARRET_S)
    if await obs.arreter_enregistrement():
        print("enregistrement arrêté avant les questions-réponses")


async def principal() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("serveur", help="URL WebSocket du serveur, ex. wss://q.exemple.fr")
    p.add_argument("--obs", default="ws://127.0.0.1:4455")
    p.add_argument("--obs-mdp", default="")
    p.add_argument("--scene", default="Diapositives + vignette", help="scène forcée pendant une question ouverte")
    p.add_argument("--sans-arret", action="store_true", help="ne pas arrêter l'enregistrement OBS à l'ouverture des questions-réponses")
    args = p.parse_args()
    url_quiz = args.serveur.rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + "/ws/quiz"

    obs = Obs(args.obs, args.obs_mdp)
    scene_avant: str | None = None
    qr_arretee = False  # StopRecord déjà demandé pour la séquence Q/R en cours
    delai = 1
    while True:
        try:
            if obs.ws is None or obs.ws.closed:
                await obs.connecter()
                print("OBS connecté, scène courante :", await obs.scene_courante())
            async with websockets.connect(url_quiz, open_timeout=10) as ws:
                print("serveur connecté :", url_quiz)
                delai = 1
                async for brut in ws:
                    m = json.loads(brut)
                    if m.get("type") == "qr":
                        if m.get("actif") and not qr_arretee:
                            qr_arretee = True
                            if args.sans_arret:
                                print("questions-réponses ouvertes (enregistrement laissé tel quel : --sans-arret)")
                            else:
                                print(f"questions-réponses ouvertes → arrêt de l'enregistrement OBS dans {DELAI_ARRET_S:.0f} s")
                                asyncio.create_task(arreter_apres_delai(obs))
                        elif not m.get("actif"):
                            qr_arretee = False  # séquence terminée ; l'enregistrement n'est pas relancé
                        continue
                    if m.get("type") != "quiz":
                        continue
                    if m.get("ouverte") and scene_avant is None:
                        scene_avant = await obs.scene_courante()
                        await obs.changer_scene(args.scene)
                        print(f"question {m['question']['id']} ouverte → scène « {args.scene} » (avant : {scene_avant})")
                    elif not m.get("ouverte") and scene_avant is not None:
                        await obs.changer_scene(scene_avant)
                        print(f"question fermée → retour à « {scene_avant} »")
                        scene_avant = None
        except (OSError, websockets.WebSocketException, RuntimeError) as e:
            print("connexion perdue :", e, f"— nouvelle tentative dans {delai} s", file=sys.stderr)
            await asyncio.sleep(delai)
            delai = min(delai * 2, 10)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(principal()))
    except KeyboardInterrupt:
        pass

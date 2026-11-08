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

Enregistrement piloté par les diapositives (docs/seance.md) : le serveur diffuse `{"type": "obs", "action": "StartRecord" |
"StopRecord", "h": n}` à l'arrivée sur une diapositive marquée `data-obs` (et un `StopRecord` à la fin de séance) ; le pont
vérifie d'abord `GetRecordStatus` (rien si l'état voulu est déjà atteint). Option `--sans-enregistrement` pour ignorer ces messages.

Ancre du replay interactif (docs/seance.md §E) : avec `--jeton`, le pont lit `GetRecordStatus` chaque seconde et signale au
serveur chaque début d'enregistrement (`{"type": "enregistrement", "actif": true, "duree_ms": n}`, y compris un F9 manuel)
et chaque fin ; le serveur date le début avec sa propre horloge et fige la présentation active.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import sys

import websockets


def connexion_ouverte(ws) -> bool:
    """Vrai si la connexion WebSocket est ouverte (websockets ≥ 14 n'a plus `.closed`, seulement `.state`)."""
    if ws is None:
        return False
    etat = getattr(ws, "state", None)
    if etat is not None:
        return getattr(etat, "name", "") == "OPEN"
    return not getattr(ws, "closed", True)


class Obs:
    """Client minimal obs-websocket v5 (identification + requêtes)."""

    def __init__(self, url: str, mot_de_passe: str) -> None:
        self.url, self.mot_de_passe, self.ws, self._id = url, mot_de_passe, None, 0
        self._verrou = asyncio.Lock()  # une requête à la fois : plusieurs tâches partagent la connexion

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
        async with self._verrou:
            self._id += 1
            ident = str(self._id)
            await self.ws.send(json.dumps({"op": 6, "d": {"requestType": type_, "requestId": ident, "requestData": donnees or {}}}))
            while True:
                m = json.loads(await self.ws.recv())
                if m.get("op") == 7 and m["d"]["requestId"] == ident:
                    if not m["d"]["requestStatus"]["result"]:
                        raise RuntimeError(m["d"]["requestStatus"].get("comment", type_))
                    return m["d"].get("responseData", {})

    async def scene_courante(self) -> str:
        return (await self.requete("GetCurrentProgramScene"))["currentProgramSceneName"]

    async def changer_scene(self, nom: str) -> None:
        await self.requete("SetCurrentProgramScene", {"sceneName": nom})

    async def enregistrement_actif(self) -> bool:
        """GetRecordStatus → outputActive."""
        return bool((await self.requete("GetRecordStatus")).get("outputActive"))

    async def etat_enregistrement(self) -> tuple[bool, float]:
        """GetRecordStatus → (outputActive, outputDuration en ms)."""
        r = await self.requete("GetRecordStatus")
        return bool(r.get("outputActive")), float(r.get("outputDuration") or 0)

    async def demarrer_enregistrement(self) -> bool:
        """StartRecord ; False si OBS enregistrait déjà ou a refusé (sans faire tomber le pont)."""
        try:
            if await self.enregistrement_actif():
                return False
            await self.requete("StartRecord")
        except RuntimeError as e:
            print("OBS : enregistrement non démarré :", e, file=sys.stderr)
            return False
        return True

    async def arreter_enregistrement(self) -> bool:
        """StopRecord ; False si OBS n'enregistrait pas (ou requête refusée), sans faire tomber le pont."""
        try:
            if not await self.enregistrement_actif():
                return False
            await self.requete("StopRecord")
        except RuntimeError as e:
            print("OBS : enregistrement non arrêté :", e, file=sys.stderr)
            return False
        return True


DELAI_ARRET_S = 2.0
PERIODE_ANCRE_S = 1.0


class SuiviEnregistrement:
    """Détecte les débuts et fins d'enregistrement à partir des lectures successives de GetRecordStatus."""

    def __init__(self) -> None:
        self.actif = False
        self.duree_ms = 0.0

    def observer(self, actif: bool, duree_ms: float) -> str | None:
        """'debut', 'fin' ou None. Une durée qui recule = un nouvel enregistrement entre deux lectures."""
        evenement = None
        if actif and (not self.actif or duree_ms + 500 < self.duree_ms):
            evenement = "debut"
        elif not actif and self.actif:
            evenement = "fin"
        self.actif, self.duree_ms = actif, (duree_ms if actif else 0.0)
        return evenement


async def suivre_enregistrement(obs: Obs, serveur) -> None:
    """Signale au serveur chaque début / fin d'enregistrement (ancre du replay interactif)."""
    suivi = SuiviEnregistrement()
    while True:
        actif, duree_ms = await obs.etat_enregistrement()
        evenement = suivi.observer(actif, duree_ms)
        if evenement:
            await serveur.send(json.dumps({"type": "enregistrement", "actif": evenement == "debut", "duree_ms": duree_ms}))
            detail = f" (déjà {duree_ms / 1000:.1f} s enregistrées)" if evenement == "debut" else ""
            print(f"enregistrement : {evenement} signalé au serveur{detail}")
        await asyncio.sleep(PERIODE_ANCRE_S)


async def arreter_apres_delai(obs: Obs) -> None:
    await asyncio.sleep(DELAI_ARRET_S)
    if await obs.arreter_enregistrement():
        print("enregistrement arrêté avant les questions-réponses")


async def executer_action_obs(obs: Obs, action: str, h) -> None:
    """Action demandée par une diapositive : StartRecord / StopRecord, après vérification de l'état réel d'OBS."""
    numero = f"la diapositive {int(h) + 1}" if isinstance(h, int) or (isinstance(h, str) and h.isdigit()) else "la séance"
    if action == "StartRecord":
        if await obs.demarrer_enregistrement():
            print(f"enregistrement démarré par {numero}")
        else:
            print(f"enregistrement déjà en cours ({numero} : rien à faire)")
    elif action == "StopRecord":
        if await obs.arreter_enregistrement():
            print(f"enregistrement arrêté par {numero}")
        else:
            print(f"aucun enregistrement en cours ({numero} : rien à faire)")
    else:
        print("action OBS inconnue ignorée :", action, file=sys.stderr)


async def principal() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("serveur", help="URL WebSocket du serveur, ex. wss://q.exemple.fr")
    p.add_argument("--obs", default="ws://127.0.0.1:4455")
    p.add_argument("--obs-mdp", default="")
    p.add_argument("--jeton", default="", help="jeton du serveur : active le signalement des débuts / fins d'enregistrement (replay interactif)")
    p.add_argument("--scene", default="Diapositives + vignette", help="scène forcée pendant une question ouverte")
    p.add_argument("--sans-arret", action="store_true", help="ne pas arrêter l'enregistrement OBS à l'ouverture des questions-réponses")
    p.add_argument("--sans-enregistrement", action="store_true", help="ignorer les messages d'enregistrement envoyés par les diapositives (data-obs) et la fin de séance")
    args = p.parse_args()
    url_quiz = args.serveur.rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + "/ws/quiz"
    url_connexion = url_quiz + (f"?token={args.jeton}" if args.jeton else "")

    obs = Obs(args.obs, args.obs_mdp)
    scene_avant: str | None = None
    qr_arretee = False  # StopRecord déjà demandé pour la séquence Q/R en cours
    delai = 1
    while True:
        try:
            if not connexion_ouverte(obs.ws):
                await obs.connecter()
                print("OBS connecté, scène courante :", await obs.scene_courante())
            async with websockets.connect(url_connexion, open_timeout=10) as ws:
                print("serveur connecté :", url_quiz)
                delai = 1

                async def ecouter() -> None:
                    nonlocal scene_avant, qr_arretee
                    async for brut in ws:
                        m = json.loads(brut)
                        if m.get("type") == "enregistrement":
                            if m.get("ok") and m.get("actif"):
                                print(f"serveur : ancre enregistrée, présentation « {m.get('presentation')} » figée ({m.get('seance')})")
                            elif not m.get("ok"):
                                print("serveur : ancre refusée :", m.get("raison"), file=sys.stderr)
                            continue
                        if m.get("type") == "obs":
                            if args.sans_enregistrement:
                                print(f"diapositive : {m.get('action')} ignoré (--sans-enregistrement)")
                            else:
                                await executer_action_obs(obs, str(m.get("action")), m.get("h"))
                            continue
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

                taches = {asyncio.create_task(ecouter())}
                if args.jeton:
                    taches.add(asyncio.create_task(suivre_enregistrement(obs, ws)))
                finies, restantes = await asyncio.wait(taches, return_when=asyncio.FIRST_COMPLETED)
                for t in restantes:
                    t.cancel()
                for t in finies:
                    t.result()  # relance l'erreur éventuelle (OBS ou serveur perdu) vers la reconnexion
        except (OSError, websockets.WebSocketException, RuntimeError) as e:
            cote = "serveur" if connexion_ouverte(obs.ws) else "OBS"
            print(f"connexion perdue ({cote}) :", e, f"— nouvelle tentative dans {delai} s", file=sys.stderr)
            await asyncio.sleep(delai)
            delai = min(delai * 2, 10)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(principal()))
    except KeyboardInterrupt:
        pass

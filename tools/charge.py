#!/usr/bin/env python3
"""Test de charge du quiz : N clients WebSocket simultanés et N votes en quelques secondes.

    python tools/charge.py https://q.exemple.fr --token JETON --question q1 --clients 300 --duree 10

Ouvre la question (à choix unique), lance un processus séparé qui tient N WebSockets /ws/quiz,
fait voter N participants (cookies distincts) répartis sur `--duree` secondes, mesure la latence
des votes et le délai entre le dernier vote et la réception de l'état complet par chaque
WebSocket, puis ferme la question. Cible PRD : 300 + 300 en 10 s, diffusion < 1 s, CPU < 50 %
sur 1 vCPU. Les écouteurs tournent dans un second processus pour ne pas être ralentis par les votes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time

import httpx
import websockets


# ---------------------------------------------------------------------------------
# Processus écouteur : N WebSockets, imprime l'horodatage de réception de l'état complet
# ---------------------------------------------------------------------------------


async def ecouter(url_ws: str, n: int, total_attendu: int) -> None:
    prets = 0
    resultats: list[float] = []

    async def un_client() -> None:
        nonlocal prets
        try:
            async with websockets.connect(url_ws, open_timeout=30) as ws:
                prets += 1
                if prets == n:
                    print("pret", flush=True)
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                    if m.get("type") == "quiz" and (m.get("resultat") or {}).get("total", 0) >= total_attendu:
                        resultats.append(time.time())
                        return
        except Exception as e:  # noqa: BLE001
            print("erreur", str(e), flush=True)

    await asyncio.gather(*(un_client() for _ in range(n)))
    print("fin " + json.dumps(resultats), flush=True)


# ---------------------------------------------------------------------------------
# Processus principal : votes
# ---------------------------------------------------------------------------------


async def voter(client: httpx.AsyncClient, base: str, question: str, options: int, attente: float, latences: list[float], fins: list[float], erreurs: list[str]) -> None:
    await asyncio.sleep(attente)
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{base}/api/quiz/vote", json={"question_id": question, "reponse": random.randrange(options)})
        latences.append(time.perf_counter() - t0)
        fins.append(time.time())
        if r.status_code != 200:
            erreurs.append(f"{r.status_code} {r.text[:80]}")
    except Exception as e:  # noqa: BLE001
        erreurs.append(str(e))


async def principal() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("base", help="URL du serveur, ex. https://q.exemple.fr")
    p.add_argument("--token")
    p.add_argument("--question", default="q1")
    p.add_argument("--clients", type=int, default=300)
    p.add_argument("--duree", type=float, default=10.0, help="fenêtre (s) sur laquelle répartir les votes")
    p.add_argument("--ecoute", type=int, metavar="TOTAL", help="(interne) mode écouteur")
    args = p.parse_args()
    base = args.base.rstrip("/")
    url_ws = base.replace("https://", "wss://").replace("http://", "ws://") + "/ws/quiz"

    if args.ecoute is not None:
        await ecouter(url_ws, args.clients, args.ecoute)
        return 0
    if not args.token:
        p.error("--token est obligatoire")

    async with httpx.AsyncClient(timeout=30) as admin:
        r = await admin.post(f"{base}/api/quiz/reinitialiser", params={"token": args.token}, json={"question_id": args.question})
        r.raise_for_status()
        r = await admin.post(f"{base}/api/quiz/ouvrir", params={"token": args.token}, json={"question_id": args.question})
        r.raise_for_status()
        question = r.json()["question"]
        if question["type"] not in ("qcm", "emoji", "rapidite"):
            print("le test de charge vote sur une question à choix unique (qcm, emoji, rapidite)", file=sys.stderr)
            return 2
        options = len(question["options"])

        ecouteur = await asyncio.create_subprocess_exec(
            sys.executable, __file__, base, "--clients", str(args.clients), "--ecoute", str(args.clients),
            stdout=asyncio.subprocess.PIPE,
        )
        ligne = (await ecouteur.stdout.readline()).decode().strip()
        while ligne.startswith("erreur"):
            print("écouteur :", ligne, file=sys.stderr)
            ligne = (await ecouteur.stdout.readline()).decode().strip()
        assert ligne == "pret", ligne
        print(f"{args.clients} WebSockets connectés, votes sur {args.duree:.0f} s…")

        latences: list[float] = []
        fins: list[float] = []
        erreurs: list[str] = []
        attentes = [random.uniform(0, args.duree) for _ in range(args.clients)]
        clients = [httpx.AsyncClient(timeout=30) for _ in range(args.clients)]  # un cookie par participant
        try:
            await asyncio.gather(*(voter(c, base, args.question, options, a, latences, fins, erreurs) for c, a in zip(clients, attentes)))
        finally:
            await asyncio.gather(*(c.aclose() for c in clients))
        dernier_vote = max(fins) if fins else time.time()

        recus: list[float] = []
        try:
            sortie = (await asyncio.wait_for(ecouteur.stdout.read(), timeout=30)).decode()
            for ligne in sortie.splitlines():
                if ligne.startswith("fin "):
                    recus = json.loads(ligne[4:])
                elif ligne.startswith("erreur"):
                    print("écouteur :", ligne, file=sys.stderr)
        except asyncio.TimeoutError:
            ecouteur.kill()
        await ecouteur.wait()

        etat = (await admin.get(f"{base}/api/quiz/etat")).json()
        await admin.post(f"{base}/api/quiz/fermer", params={"token": args.token})

    def q(xs: list[float], pct: float) -> float:
        return sorted(xs)[min(len(xs) - 1, int(pct * len(xs)))] * 1000 if xs else float("nan")

    total = (etat.get("resultat") or {}).get("total")
    delais = [max(0.0, t - dernier_vote) for t in recus]
    print(f"votes : {len(latences)} ok, {len(erreurs)} erreurs ; total côté serveur : {total}")
    if latences:
        print(f"latence vote  : médiane {statistics.median(latences) * 1000:.0f} ms, p95 {q(latences, .95):.0f} ms, max {max(latences) * 1000:.0f} ms")
    if delais:
        print(f"diffusion WS  : {len(delais)}/{args.clients} clients ont reçu l'état complet ; p95 {q(delais, .95):.0f} ms, max {max(delais) * 1000:.0f} ms après le dernier vote (cible < 1000 ms)")
    else:
        print("diffusion WS  : aucun client n'a reçu l'état complet — vérifier le serveur")
    for e in erreurs[:5]:
        print("  erreur :", e)
    ok = not erreurs and total == args.clients and len(delais) == args.clients and q(delais, .95) < 1000
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))

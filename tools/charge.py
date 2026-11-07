#!/usr/bin/env python3
"""Test de charge du quiz : N clients WebSocket simultanés et N votes en quelques secondes.

    python tools/charge.py https://q.exemple.fr --token JETON --question q1 --clients 300 --duree 10

Ouvre la question, connecte N WebSockets /ws/quiz, fait voter N participants (cookies distincts)
répartis sur `--duree` secondes, mesure la latence des votes et le délai de réception du dernier
état par les WebSockets, puis ferme la question. Cible PRD : 300 + 300 en 10 s, CPU < 50 % sur 1 vCPU.
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


async def client_ws(url_ws: str, total_attendu: int, pret: asyncio.Event, termine: asyncio.Event, delais: list[float], t_dernier_vote: dict) -> None:
    try:
        async with websockets.connect(url_ws, open_timeout=20) as ws:
            pret.set()
            while not termine.is_set():
                try:
                    brut = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                m = json.loads(brut)
                if m.get("type") == "quiz" and m.get("total", 0) >= total_attendu and "t" in t_dernier_vote:
                    delais.append(time.perf_counter() - t_dernier_vote["t"])
                    return
    except Exception as e:  # noqa: BLE001
        print("ws erreur :", e, file=sys.stderr)


async def voter(client: httpx.AsyncClient, base: str, question: str, options: int, attente: float, latences: list[float], erreurs: list[str]) -> None:
    await asyncio.sleep(attente)
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{base}/api/quiz/vote", json={"question_id": question, "option": random.randrange(options)})
        latences.append(time.perf_counter() - t0)
        if r.status_code != 200:
            erreurs.append(f"{r.status_code} {r.text[:80]}")
    except Exception as e:  # noqa: BLE001
        erreurs.append(str(e))


async def principal() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("base", help="URL du serveur, ex. https://q.exemple.fr")
    p.add_argument("--token", required=True)
    p.add_argument("--question", default="q1")
    p.add_argument("--clients", type=int, default=300)
    p.add_argument("--duree", type=float, default=10.0, help="fenêtre (s) sur laquelle répartir les votes")
    args = p.parse_args()
    base = args.base.rstrip("/")
    url_ws = base.replace("https://", "wss://").replace("http://", "ws://") + "/ws/quiz"

    async with httpx.AsyncClient(timeout=30) as admin:
        r = await admin.post(f"{base}/api/quiz/reinitialiser", params={"token": args.token}, json={"question_id": args.question})
        r.raise_for_status()
        r = await admin.post(f"{base}/api/quiz/ouvrir", params={"token": args.token}, json={"question_id": args.question})
        r.raise_for_status()
        options = len(r.json()["question"]["options"])

        pret = asyncio.Event()
        termine = asyncio.Event()
        delais_ws: list[float] = []
        t_dernier: dict = {}
        taches_ws = [asyncio.create_task(client_ws(url_ws, args.clients, pret, termine, delais_ws, t_dernier)) for _ in range(args.clients)]
        await asyncio.wait_for(pret.wait(), 30)
        await asyncio.sleep(1.0)
        print(f"{args.clients} WebSockets connectés, votes sur {args.duree:.0f} s…")

        latences: list[float] = []
        erreurs: list[str] = []
        t0 = time.perf_counter()
        attentes = sorted(random.uniform(0, args.duree) for _ in range(args.clients))
        t_dernier["t"] = t0 + attentes[-1]
        async with httpx.AsyncClient(timeout=30) as _:
            clients = [httpx.AsyncClient(timeout=30) for _ in range(args.clients)]  # un cookie par participant
            try:
                await asyncio.gather(*(voter(c, base, args.question, options, a, latences, erreurs) for c, a in zip(clients, attentes)))
            finally:
                await asyncio.gather(*(c.aclose() for c in clients))
        t_dernier["t"] = t0 + attentes[-1]
        await asyncio.sleep(3.0)
        termine.set()
        await asyncio.gather(*taches_ws, return_exceptions=True)

        etat = (await admin.get(f"{base}/api/quiz/etat")).json()
        await admin.post(f"{base}/api/quiz/fermer", params={"token": args.token})

    def q(xs: list[float], p: float) -> float:
        return sorted(xs)[min(len(xs) - 1, int(p * len(xs)))] * 1000 if xs else float("nan")

    print(f"votes : {len(latences)} ok, {len(erreurs)} erreurs ; total côté serveur : {etat.get('total')}")
    if latences:
        print(f"latence vote  : médiane {statistics.median(latences) * 1000:.0f} ms, p95 {q(latences, .95):.0f} ms, max {max(latences) * 1000:.0f} ms")
    if delais_ws:
        print(f"diffusion WS  : {len(delais_ws)} clients ont reçu le dernier état ; p95 {q(delais_ws, .95):.0f} ms après le dernier vote (cible < 1000 ms)")
    else:
        print("diffusion WS  : aucun client n'a reçu l'état complet — vérifier le serveur")
    for e in erreurs[:5]:
        print("  erreur :", e)
    return 0 if not erreurs and etat.get("total") == args.clients else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))

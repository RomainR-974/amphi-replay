"""Serveur unique amphi-replay : synchronisation du deck, télécommande, quiz, résultats.

Le serveur ne transporte aucune vidéo : il relaie un état (index de diapositive,
question ouverte) et chaque machine rend le deck localement. Le téléphone
(rôle `remote`) est l'unique émetteur de commandes ; `scene` et `obs` suivent.

Lancement :  uvicorn server.app:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
import io
import logging
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .quiz import Quiz, charger_questions

journal = logging.getLogger("amphi")

Role = Literal["scene", "obs", "remote"]
ROLES: tuple[str, ...] = ("scene", "obs", "remote")


# ---------------------------------------------------------------------------------
# Diffusion WebSocket
# ---------------------------------------------------------------------------------


class Diffuseur:
    """Ensemble de WebSockets auxquels on diffuse le même message JSON."""

    def __init__(self) -> None:
        self.clients: dict[WebSocket, str] = {}

    async def ajouter(self, ws: WebSocket, role: str) -> None:
        await ws.accept()
        self.clients[ws] = role

    def retirer(self, ws: WebSocket) -> None:
        self.clients.pop(ws, None)

    def compter(self, role: str | None = None) -> int:
        return sum(1 for r in self.clients.values() if role is None or r == role)

    async def diffuser(self, message: dict, *, roles: tuple[str, ...] | None = None, sauf: WebSocket | None = None) -> None:
        morts: list[WebSocket] = []
        for ws, role in list(self.clients.items()):
            if ws is sauf or (roles is not None and role not in roles):
                continue
            try:
                await ws.send_json(message)
            except Exception:  # client parti sans fermer proprement
                morts.append(ws)
        for ws in morts:
            self.retirer(ws)


class EtatDeck:
    """Dernier état connu du deck et derniers accusés de réception par rôle."""

    def __init__(self) -> None:
        self.indices: dict = {"h": 0, "v": 0, "f": -1}
        self.horodatage: float = 0.0
        self.acks: dict[str, float] = {}  # rôle -> dernier ack (time.time())

    def appliquer(self, h: int, v: int, f: int) -> None:
        self.indices = {"h": h, "v": v, "f": f}
        self.horodatage = time.time()

    def public(self) -> dict:
        maintenant = time.time()
        return {
            **self.indices,
            "horodatage": self.horodatage,
            "acks": {role: round(maintenant - t, 1) for role, t in self.acks.items()},
        }


deck_ws = Diffuseur()
quiz_ws = Diffuseur()
etat_deck = EtatDeck()
quiz: Quiz


@asynccontextmanager
async def cycle_de_vie(app: FastAPI):
    global quiz
    quiz = Quiz(charger_questions(config.FICHIER_QUIZ), config.CHEMIN_BDD)
    app.state.quiz = quiz
    if config.JETON_GENERE:
        journal.warning("AMPHI_JETON absent : jeton de session généré = %s", config.JETON)
    journal.info("%d question(s) de quiz chargée(s) depuis %s", len(quiz.questions), config.FICHIER_QUIZ)
    yield


app = FastAPI(title="amphi-replay", lifespan=cycle_de_vie)
app.mount("/deck", StaticFiles(directory=config.DOSSIER_DECK, html=True), name="deck")
app.mount("/static", StaticFiles(directory=config.DOSSIER_STATIQUE), name="static")


# ---------------------------------------------------------------------------------
# Sécurité
# ---------------------------------------------------------------------------------


def verifier_jeton(token: str = Query(default="")) -> None:
    if not secrets.compare_digest(token, config.JETON):
        raise HTTPException(status_code=403, detail="jeton invalide")


# ---------------------------------------------------------------------------------
# Deck : état et synchronisation
# ---------------------------------------------------------------------------------


@app.get("/")
async def accueil() -> RedirectResponse:
    return RedirectResponse("/deck/?role=scene")


@app.get("/etat")
async def lire_etat() -> dict:
    """État courant du deck ; lu par chaque client à la connexion et à la reconnexion."""
    return etat_deck.public()


@app.get("/sante")
async def sante() -> dict:
    return {
        "ok": True,
        "deck": {role: deck_ws.compter(role) for role in ROLES},
        "quiz_clients": quiz_ws.compter(),
        "question_ouverte": quiz.ouverte,
    }


@app.websocket("/ws/deck")
async def ws_deck(ws: WebSocket, role: str = Query(default="scene"), token: str = Query(default="")) -> None:
    """Relais : tout message du rôle `remote` est diffusé aux autres clients et mémorisé.

    Messages du remote : {"type": "slide", "h": int, "v": int, "f": int}
    Messages de scene/obs : {"type": "ack"} après application d'un état.
    Le serveur renvoie les acks au remote sous {"type": "ack", "role": ..., "h", "v", "f"}.
    """
    if role not in ROLES:
        await ws.close(code=1008)
        return
    if role == "remote" and not secrets.compare_digest(token, config.JETON):
        await ws.close(code=1008)
        return

    await deck_ws.ajouter(ws, role)
    try:
        await ws.send_json({"type": "slide", **etat_deck.indices, "initial": True})
        while True:
            message = await ws.receive_json()
            genre = message.get("type")
            if role == "remote" and genre == "slide":
                try:
                    h, v, f = int(message["h"]), int(message.get("v", 0)), int(message.get("f", -1))
                except (KeyError, TypeError, ValueError):
                    continue
                etat_deck.appliquer(h, v, f)
                await deck_ws.diffuser({"type": "slide", "h": h, "v": v, "f": f}, sauf=ws)
            elif role in ("scene", "obs") and genre == "ack":
                etat_deck.acks[role] = time.time()
                await deck_ws.diffuser(
                    {"type": "ack", "role": role, "h": message.get("h"), "v": message.get("v"), "f": message.get("f")},
                    roles=("remote",),
                )
            elif genre == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        deck_ws.retirer(ws)


# ---------------------------------------------------------------------------------
# Quiz : vues
# ---------------------------------------------------------------------------------


@app.get("/q", response_class=HTMLResponse)
async def page_participant() -> FileResponse:
    return FileResponse(config.DOSSIER_STATIQUE / "participant.html")


@app.get("/q/resultats", response_class=HTMLResponse)
async def page_resultats() -> FileResponse:
    return FileResponse(config.DOSSIER_STATIQUE / "resultats.html")


@app.get("/remote")
async def page_remote(token: str = Query(default="")) -> Response:
    """Raccourci vers la télécommande : /remote?token=... → /deck/remote.html?token=..."""
    verifier_jeton(token)
    return RedirectResponse(f"/deck/remote.html?token={token}")


@app.get("/qr.png")
async def qr_code() -> Response:
    """QR code de l'URL participant, affiché sur la première diapositive et les diapositives de quiz."""
    import qrcode

    image = qrcode.make(f"{config.URL_PUBLIQUE}/q", box_size=12, border=2)
    tampon = io.BytesIO()
    image.save(tampon, format="PNG")
    return Response(tampon.getvalue(), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------------------------
# Quiz : API
# ---------------------------------------------------------------------------------


COOKIE_PARTICIPANT = "amphi_pid"


def identifiant_participant(request: Request, response: Response) -> str:
    pid = request.cookies.get(COOKIE_PARTICIPANT)
    if not pid or len(pid) > 64:
        pid = secrets.token_urlsafe(16)
        response.set_cookie(COOKIE_PARTICIPANT, pid, max_age=6 * 3600, httponly=True, samesite="lax")
    return pid


async def publier_quiz() -> None:
    await quiz_ws.diffuser({"type": "quiz", **quiz.etat()})


@app.get("/api/quiz/etat")
async def quiz_etat(request: Request, response: Response, question_id: str | None = None) -> dict:
    etat = quiz.etat(question_id)
    pid = identifiant_participant(request, response)
    if etat["question"]:
        etat["ma_reponse"] = quiz.reponse_de(etat["question"]["id"], pid)
    etat["url_publique"] = config.URL_PUBLIQUE
    return etat


@app.get("/api/quiz/questions")
async def quiz_questions() -> list[dict]:
    return [q.public() for q in quiz.questions.values()]


class Vote(BaseModel):
    question_id: str
    option: int = Field(ge=0, le=4)


@app.post("/api/quiz/vote")
async def quiz_vote(vote: Vote, request: Request, response: Response) -> dict:
    pid = identifiant_participant(request, response)
    try:
        quiz.voter(vote.question_id, pid, vote.option)
    except PermissionError:
        raise HTTPException(status_code=409, detail="question fermée")
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="question ou option inconnue")
    asyncio.create_task(publier_quiz())
    return {"ok": True, "ma_reponse": vote.option}


class Cible(BaseModel):
    question_id: str


@app.post("/api/quiz/ouvrir", dependencies=[Depends(verifier_jeton)])
async def quiz_ouvrir(cible: Cible) -> dict:
    try:
        quiz.ouvrir(cible.question_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="question inconnue")
    await publier_quiz()
    return quiz.etat()


@app.post("/api/quiz/fermer", dependencies=[Depends(verifier_jeton)])
async def quiz_fermer() -> dict:
    derniere = quiz.ouverte
    quiz.fermer()
    # Les vues résultats gardent la dernière question affichée, figée.
    await quiz_ws.diffuser({"type": "quiz", **quiz.etat(derniere)})
    return quiz.etat(derniere)


@app.post("/api/quiz/reinitialiser", dependencies=[Depends(verifier_jeton)])
async def quiz_reinitialiser(cible: Cible) -> dict:
    try:
        quiz.reinitialiser(cible.question_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="question inconnue")
    await quiz_ws.diffuser({"type": "quiz", **quiz.etat(cible.question_id)})
    return quiz.etat(cible.question_id)


@app.get("/api/quiz/export", dependencies=[Depends(verifier_jeton)])
async def quiz_export() -> PlainTextResponse:
    return PlainTextResponse(
        quiz.export_agrege(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="quiz_resultats.csv"'},
    )


@app.post("/api/quiz/purger", dependencies=[Depends(verifier_jeton)])
async def quiz_purger() -> dict:
    quiz.fermer()
    quiz.purger()
    await publier_quiz()
    return {"ok": True}


@app.websocket("/ws/quiz")
async def ws_quiz(ws: WebSocket) -> None:
    """Pousse l'état du quiz aux participants, à la page de résultats et à la télécommande."""
    await quiz_ws.ajouter(ws, "quiz")
    try:
        await ws.send_json({"type": "quiz", **quiz.etat()})
        while True:
            message = await ws.receive_json()
            if message.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        quiz_ws.retirer(ws)


@app.exception_handler(404)
async def introuvable(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": "introuvable"}, status_code=404)

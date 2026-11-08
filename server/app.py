"""Serveur unique amphi-replay : synchronisation du deck, télécommande, quiz, résultats, éditeur.

Le serveur ne transporte aucune vidéo : il relaie un état (index de diapositive,
question ouverte) et chaque machine rend le deck localement. Le téléphone
(rôle `remote`) est l'unique émetteur de commandes ; `scene` et `obs` suivent.

Lancement :  uvicorn server.app:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .presentations import Bibliotheque, valider_slug
from .quiz import Quiz, charger_questions, questions_depuis_liste

journal = logging.getLogger("amphi")

Role = Literal["scene", "obs", "remote"]
ROLES: tuple[str, ...] = ("scene", "obs", "remote")
EXTENSIONS_MEDIAS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".json", ".mp4", ".webm", ".mp3", ".ogg", ".wav", ".woff2", ".ttf"}


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
        texte = json.dumps(message, ensure_ascii=False)
        morts: list[WebSocket] = []
        for ws, role in list(self.clients.items()):
            if ws is sauf or (roles is not None and role not in roles):
                continue
            try:
                await ws.send_text(texte)
            except Exception:  # client parti sans fermer proprement
                morts.append(ws)
        for ws in morts:
            self.retirer(ws)


class EtatDeck:
    """Dernier état connu du deck et derniers accusés de réception par rôle."""

    def __init__(self) -> None:
        self.indices: dict = {"h": 0, "v": 0, "f": -1}
        self.horodatage: float = 0.0
        self.acks: dict[str, float] = {}

    def appliquer(self, h: int, v: int, f: int) -> None:
        self.indices = {"h": h, "v": v, "f": f}
        self.horodatage = time.time()

    def public(self) -> dict:
        maintenant = time.time()
        return {**self.indices, "horodatage": self.horodatage, "acks": {role: round(maintenant - t, 1) for role, t in self.acks.items()}}


deck_ws = Diffuseur()
quiz_ws = Diffuseur()
etat_deck = EtatDeck()
biblio = Bibliotheque(config.DOSSIER_PRESENTATIONS, config.FICHIER_ACTIVE, config.PRESENTATION_DEFAUT)
quiz: Quiz
_publication = asyncio.Event()  # levé par les votes : l'état est publié, regroupé, par la boucle de fond
_fond: asyncio.Task | None = None


@asynccontextmanager
async def cycle_de_vie(app: FastAPI):
    global quiz
    active = biblio.active()
    fichier_quiz = biblio.fichier_quiz(active) if active else None
    quiz = Quiz(charger_questions(fichier_quiz) if fichier_quiz else {}, config.CHEMIN_BDD)
    app.state.quiz = quiz
    app.state.biblio = biblio
    if config.JETON_GENERE:
        journal.warning("AMPHI_JETON absent : jeton de session généré = %s", config.JETON)
    if active:
        biblio.activer(active)  # fige le choix par défaut dans active.json : créer une présentation ne change pas l'active
        biblio.dossier_medias(active).mkdir(exist_ok=True)
        journal.info("présentation active : %s ; %d question(s) de quiz chargée(s) depuis %s", active, len(quiz.questions), fichier_quiz)
    else:
        journal.warning("aucune présentation dans %s", config.DOSSIER_PRESENTATIONS)
    global _fond
    _fond = asyncio.create_task(boucle_de_fond())
    yield
    _fond.cancel()


app = FastAPI(title="amphi-replay", lifespan=cycle_de_vie)


# ---------------------------------------------------------------------------------
# Présentations : fichiers servis (déclarés AVANT le montage statique de deck/, qui les masquerait)
# ---------------------------------------------------------------------------------


def slug_actif_ou_404() -> str:
    actif = biblio.active()
    if not actif:
        raise HTTPException(status_code=404, detail="aucune présentation")
    return actif


def resoudre_prez(prez: str | None) -> str:
    """Slug visé par un paramètre `prez` (sinon la présentation active) ; 404 s'il est inconnu."""
    if not prez:
        return slug_actif_ou_404()
    if not biblio.existe(prez):
        raise HTTPException(status_code=404, detail="présentation inconnue")
    return prez


def reponse_slides(slug: str) -> Response:
    fichier = biblio.fichier_slides(slug)
    if not fichier.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(fichier, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


def reponse_media(slug: str, chemin: str) -> Response:
    fichier = biblio.media(slug, chemin)
    if fichier is None:
        raise HTTPException(status_code=404)
    return FileResponse(fichier)


@app.get("/deck/slides.html")
async def deck_slides_actif() -> Response:
    return reponse_slides(slug_actif_ou_404())


@app.get("/deck/medias/{chemin:path}")
async def deck_media_actif(chemin: str) -> Response:
    return reponse_media(slug_actif_ou_404(), chemin)


@app.get("/p/{slug}/slides.html")
async def prez_slides(slug: str) -> Response:
    return reponse_slides(resoudre_prez(slug))


@app.get("/p/{slug}/medias/{chemin:path}")
async def prez_media(slug: str, chemin: str) -> Response:
    return reponse_media(resoudre_prez(slug), chemin)


app.mount("/deck", StaticFiles(directory=config.DOSSIER_DECK, html=True), name="deck")
app.mount("/static", StaticFiles(directory=config.DOSSIER_STATIQUE), name="static")


# ---------------------------------------------------------------------------------
# Sécurité
# ---------------------------------------------------------------------------------


def jeton_valide(token: str) -> bool:
    return secrets.compare_digest(token, config.JETON)


def verifier_jeton(token: str = Query(default="")) -> None:
    if not jeton_valide(token):
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
        "questions": len(quiz.questions),
        "presentation": biblio.active(),
    }


@app.websocket("/ws/deck")
async def ws_deck(ws: WebSocket, role: str = Query(default="scene"), token: str = Query(default="")) -> None:
    """Relais : tout message du rôle `remote` est diffusé aux autres clients et mémorisé."""
    if role not in ROLES or (role == "remote" and not jeton_valide(token)):
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
# Deck : contenu (éditeur)
# ---------------------------------------------------------------------------------


class Slides(BaseModel):
    html: str


@app.get("/api/deck/slides", response_class=PlainTextResponse)
async def deck_slides_lire(prez: str | None = None) -> str:
    fichier = biblio.fichier_slides(resoudre_prez(prez))
    return fichier.read_text(encoding="utf-8") if fichier.exists() else ""


@app.put("/api/deck/slides", dependencies=[Depends(verifier_jeton)])
async def deck_slides_ecrire(slides: Slides, prez: str | None = None) -> dict:
    slug = resoudre_prez(prez)
    fichier = biblio.fichier_slides(slug)
    sauvegardes = biblio.dossier_sauvegardes(slug)
    sauvegardes.mkdir(exist_ok=True)
    if fichier.exists():
        ancien = sauvegardes / f"slides-{datetime.now():%Y-%m-%d_%H%M%S}.html"
        ancien.write_text(fichier.read_text(encoding="utf-8"), encoding="utf-8")
        # Ne garder que les 50 dernières sauvegardes.
        for vieux in sorted(sauvegardes.glob("slides-*.html"))[:-50]:
            vieux.unlink()
    fichier.write_text(slides.html, encoding="utf-8")
    biblio.toucher(slug)
    if slug == biblio.active():
        await deck_ws.diffuser({"type": "recharger"}, roles=("scene", "obs"))
    return {"ok": True, "taille": len(slides.html), "prez": slug}


@app.get("/api/deck/sauvegardes", dependencies=[Depends(verifier_jeton)])
async def deck_sauvegardes(prez: str | None = None) -> list[dict]:
    dossier = biblio.dossier_sauvegardes(resoudre_prez(prez))
    return [{"nom": f.name, "taille": f.stat().st_size} for f in sorted(dossier.glob("slides-*.html"), reverse=True)] if dossier.exists() else []


@app.get("/api/deck/sauvegardes/{nom}", dependencies=[Depends(verifier_jeton)], response_class=PlainTextResponse)
async def deck_sauvegarde_lire(nom: str, prez: str | None = None) -> str:
    if not re.fullmatch(r"slides-[0-9_-]+\.html", nom):
        raise HTTPException(status_code=404)
    fichier = biblio.dossier_sauvegardes(resoudre_prez(prez)) / nom
    if not fichier.exists():
        raise HTTPException(status_code=404)
    return fichier.read_text(encoding="utf-8")


@app.get("/api/deck/medias", dependencies=[Depends(verifier_jeton)])
async def deck_medias(prez: str | None = None) -> list[dict]:
    dossier = biblio.dossier_medias(resoudre_prez(prez))
    return [{"chemin": f"medias/{f.name}", "taille": f.stat().st_size} for f in sorted(dossier.iterdir()) if f.is_file()] if dossier.exists() else []


@app.post("/api/deck/medias", dependencies=[Depends(verifier_jeton)])
async def deck_media_televerser(fichier: UploadFile = File(...), prez: str | None = None) -> dict:
    nom = re.sub(r"[^A-Za-z0-9._-]+", "_", fichier.filename or "fichier").strip("._") or "fichier"
    suffixe = ("." + nom.rsplit(".", 1)[-1].lower()) if "." in nom else ""
    if suffixe not in EXTENSIONS_MEDIAS:
        raise HTTPException(status_code=415, detail=f"extension refusée ({', '.join(sorted(EXTENSIONS_MEDIAS))})")
    dossier = biblio.dossier_medias(resoudre_prez(prez))
    dossier.mkdir(exist_ok=True)
    destination = dossier / nom
    base, n = nom[: -len(suffixe)] if suffixe else nom, 1
    while destination.exists():
        n += 1
        destination = dossier / f"{base}-{n}{suffixe}"
    contenu = await fichier.read()
    if len(contenu) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="fichier trop volumineux (50 Mo)")
    destination.write_bytes(contenu)
    return {"chemin": f"medias/{destination.name}", "taille": len(contenu)}


# ---------------------------------------------------------------------------------
# Quiz : vues
# ---------------------------------------------------------------------------------


@app.get("/q")
async def page_participant() -> FileResponse:
    return FileResponse(config.DOSSIER_STATIQUE / "participant.html")


@app.get("/q/resultats")
async def page_resultats() -> FileResponse:
    return FileResponse(config.DOSSIER_STATIQUE / "resultats.html")


@app.get("/q/classement")
async def page_classement() -> FileResponse:
    return FileResponse(config.DOSSIER_STATIQUE / "classement.html")


@app.get("/remote")
async def page_remote(token: str = Query(default="")) -> Response:
    verifier_jeton(token)
    return RedirectResponse(f"/deck/remote.html?token={token}")


@app.get("/editeur")
async def page_editeur(token: str = Query(default="")) -> Response:
    verifier_jeton(token)
    return RedirectResponse(f"/deck/editeur.html?token={token}")


@app.get("/presentations")
async def page_presentations(token: str = Query(default="")) -> Response:
    verifier_jeton(token)
    return RedirectResponse(f"/deck/presentations.html?token={token}")


# ---------------------------------------------------------------------------------
# Présentations : API
# ---------------------------------------------------------------------------------


class NouvellePresentation(BaseModel):
    slug: str
    titre: str = ""
    description: str = ""
    theme: str = ""
    depuis: str | None = None


class ModificationPresentation(BaseModel):
    titre: str | None = None
    description: str | None = None
    theme: str | None = None


def slug_ou_404(slug: str) -> str:
    if not biblio.existe(slug):
        raise HTTPException(status_code=404, detail="présentation inconnue")
    return slug


@app.get("/api/presentations")
async def presentations_lister() -> list[dict]:
    return biblio.lister()


@app.get("/api/presentations/active")
async def presentations_active() -> dict:
    actif = biblio.active()
    if not actif:
        return {"slug": None, "titre": "", "theme": ""}
    meta = biblio.lire(actif)
    return {"slug": actif, "titre": meta["titre"], "theme": meta["theme"]}


@app.post("/api/presentations", dependencies=[Depends(verifier_jeton)])
async def presentations_creer(corps: NouvellePresentation) -> dict:
    try:
        return biblio.creer(corps.slug, corps.titre, corps.description, corps.theme, corps.depuis)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except FileExistsError:
        raise HTTPException(status_code=409, detail="ce slug existe déjà")
    except KeyError:
        raise HTTPException(status_code=404, detail="présentation source inconnue")


@app.post("/api/presentations/importer", dependencies=[Depends(verifier_jeton)])
async def presentations_importer(fichier: UploadFile = File(...), slug: str = Form(...), titre: str = Form(default="")) -> dict:
    nom = fichier.filename or "presentation.pptx"
    if not nom.lower().endswith(".pptx"):
        raise HTTPException(status_code=415, detail="un fichier .pptx est attendu")
    try:
        valider_slug(slug)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if biblio.existe(slug):
        raise HTTPException(status_code=409, detail="ce slug existe déjà")
    contenu = await fichier.read()
    if len(contenu) > 200 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="fichier trop volumineux (200 Mo)")
    try:
        # Conversion dans un fil d'exécution : LibreOffice peut prendre plusieurs dizaines de secondes.
        resultat = await asyncio.to_thread(biblio.importer_pptx, slug, titre, contenu, nom)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="ce slug existe déjà")
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return resultat


@app.get("/api/presentations/{slug}")
async def presentations_lire(slug: str) -> dict:
    return biblio.resume(slug_ou_404(slug))


@app.post("/api/presentations/{slug}/activer", dependencies=[Depends(verifier_jeton)])
async def presentations_activer(slug: str) -> dict:
    slug_ou_404(slug)
    try:
        questions = charger_questions(biblio.fichier_quiz(slug))
    except (ValueError, TypeError, KeyError) as e:
        raise HTTPException(status_code=422, detail=f"quiz.json invalide : {e}")
    biblio.activer(slug)
    biblio.dossier_medias(slug).mkdir(exist_ok=True)
    quiz.remplacer_questions(questions)
    quiz.fermer()
    quiz.revelee = None
    await publier_quiz()
    # Tous les rôles rechargent le deck, télécommande comprise (sa miniature affiche la nouvelle présentation).
    await deck_ws.diffuser({"type": "recharger", "prez": slug})
    return biblio.resume(slug)


@app.put("/api/presentations/{slug}", dependencies=[Depends(verifier_jeton)])
async def presentations_modifier(slug: str, corps: ModificationPresentation) -> dict:
    slug_ou_404(slug)
    try:
        return biblio.modifier(slug, corps.titre, corps.description, corps.theme)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.delete("/api/presentations/{slug}", dependencies=[Depends(verifier_jeton)])
async def presentations_supprimer(slug: str) -> dict:
    slug_ou_404(slug)
    try:
        biblio.supprimer(slug)
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


@app.get("/qr.png")
async def qr_code() -> Response:
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


async def publier_quiz(question_id: str | None = None) -> None:
    await quiz_ws.diffuser({"type": "quiz", **quiz.etat(question_id)})




async def boucle_de_fond() -> None:
    """Tâche unique démarrée avec l'application (une tâche créée dans une requête peut être annulée avec elle).

    - Publication regroupée : sous une rafale de votes, un seul état toutes les 200 ms pour tous les clients.
    - Chrono : ferme automatiquement la question ouverte quand son temps est écoulé.
    """
    while True:
        try:
            await asyncio.wait_for(_publication.wait(), timeout=0.25)
        except asyncio.TimeoutError:
            pass
        try:
            if _publication.is_set():
                await asyncio.sleep(0.2)
                _publication.clear()
                await publier_quiz()
            if quiz.ouverte and quiz.reste == 0.0:
                derniere = quiz.fermer()
                await publier_quiz(derniere)
        except asyncio.CancelledError:
            raise
        except Exception:  # jamais laisser mourir la boucle
            journal.exception("boucle de fond")


@app.get("/api/quiz/etat")
async def quiz_etat(request: Request, response: Response, question_id: str | None = None) -> dict:
    etat = quiz.etat(question_id)
    pid = identifiant_participant(request, response)
    if etat["question"]:
        etat["ma_reponse"] = quiz.reponse_de(etat["question"]["id"], pid)
    etat["url_publique"] = config.URL_PUBLIQUE
    etat["pseudo"] = quiz.pseudo(pid) if request.cookies.get(COOKIE_PARTICIPANT) else None
    return etat


@app.get("/api/quiz/questions")
async def quiz_questions(token: str = Query(default=""), prez: str | None = None) -> list[dict]:
    """Sans jeton : vue publique (sans les bonnes réponses). Avec jeton : complète. `prez` : celles d'une autre présentation."""
    questions = quiz.questions
    if prez and prez != biblio.active():
        try:
            questions = charger_questions(biblio.fichier_quiz(resoudre_prez(prez)))
        except (ValueError, TypeError, KeyError) as e:
            raise HTTPException(status_code=422, detail=f"quiz.json invalide : {e}")
    if token and jeton_valide(token):
        return [q.complet() for q in questions.values()]
    return [q.public() for q in questions.values()]


@app.put("/api/quiz/questions", dependencies=[Depends(verifier_jeton)])
async def quiz_questions_ecrire(liste: list[dict], prez: str | None = None) -> dict:
    slug = resoudre_prez(prez)
    try:
        questions = questions_depuis_liste(liste)
    except (ValueError, TypeError, KeyError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    biblio.fichier_quiz(slug).write_text(json.dumps([q.complet() for q in questions.values()], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    biblio.toucher(slug)
    if slug == biblio.active():
        quiz.remplacer_questions(questions)
        await publier_quiz()
    return {"ok": True, "questions": len(questions), "prez": slug}


class Pseudo(BaseModel):
    pseudo: str = ""


@app.post("/api/quiz/pseudo")
async def quiz_pseudo(corps: Pseudo, request: Request, response: Response) -> dict:
    pid = identifiant_participant(request, response)
    return {"pseudo": quiz.pseudo(pid, corps.pseudo)}


class Vote(BaseModel):
    question_id: str
    reponse: Any


@app.post("/api/quiz/vote")
async def quiz_vote(vote: Vote, request: Request, response: Response) -> dict:
    pid = identifiant_participant(request, response)
    quiz.pseudo(pid)  # garantit un pseudo pour le classement
    try:
        propre = quiz.voter(vote.question_id, pid, vote.reponse)
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e) or "question ou réponse invalide")
    _publication.set()
    return {"ok": True, "ma_reponse": {"reponse": propre, "points": 0}}


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
    derniere = quiz.fermer()
    await publier_quiz(derniere)
    return quiz.etat(derniere)


@app.post("/api/quiz/reveler", dependencies=[Depends(verifier_jeton)])
async def quiz_reveler(cible: Cible) -> dict:
    try:
        quiz.reveler(cible.question_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="question inconnue")
    await publier_quiz(cible.question_id)
    return quiz.etat(cible.question_id)


@app.post("/api/quiz/reinitialiser", dependencies=[Depends(verifier_jeton)])
async def quiz_reinitialiser(cible: Cible) -> dict:
    try:
        quiz.reinitialiser(cible.question_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="question inconnue")
    await publier_quiz(cible.question_id)
    return quiz.etat(cible.question_id)


@app.get("/api/quiz/classement")
async def quiz_classement(nombre: int = 10) -> list[dict]:
    return quiz.classement(nombre)


@app.get("/api/quiz/export", dependencies=[Depends(verifier_jeton)])
async def quiz_export() -> PlainTextResponse:
    return PlainTextResponse(quiz.export_agrege(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="quiz_resultats.csv"'})


@app.post("/api/quiz/purger", dependencies=[Depends(verifier_jeton)])
async def quiz_purger() -> dict:
    quiz.purger()
    await publier_quiz()
    return {"ok": True}


@app.websocket("/ws/quiz")
async def ws_quiz(ws: WebSocket) -> None:
    """Pousse l'état du quiz aux participants, aux pages de résultats et à la télécommande."""
    await quiz_ws.ajouter(ws, "quiz")
    try:
        await ws.send_json({"type": "quiz", **quiz.etat()})
        while True:
            message = await ws.receive_json()
            if message.get("type") == "ping":
                await ws.send_json({"type": "pong", "reste": quiz.reste})
    except WebSocketDisconnect:
        pass
    finally:
        quiz_ws.retirer(ws)


@app.exception_handler(404)
async def introuvable(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": "introuvable"}, status_code=404)

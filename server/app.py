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
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel

from . import config
from .journal import ACTIONS_OBS, Journal, marqueurs_obs, titres_presentation
from .presentations import Bibliotheque, normaliser_contact, valider_slug
from .questions import Questions, SequenceQR, question_publique
from .quiz import Quiz, charger_questions, questions_depuis_liste
from .replays import Captations, Replays, Taches, assembler_replay, outils_presents
from .scorm import construire_scorm

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


class EtatSeance:
    """Fin de séance et marqueurs OBS de la présentation active (docs/seance.md, A et C)."""

    def __init__(self) -> None:
        self.terminee = False
        self.marqueurs: dict[int, str] = {}  # index h → "demarrer" | "arreter"
        self.dernier_h: int | None = None  # dernière diapositive (h) signalée : un fragment n'est pas une nouvelle arrivée

    def reinitialiser(self) -> None:
        self.terminee = False
        self.dernier_h = None


deck_ws = Diffuseur()
quiz_ws = Diffuseur()
etat_deck = EtatDeck()
seance = EtatSeance()
biblio = Bibliotheque(config.DOSSIER_PRESENTATIONS, config.FICHIER_ACTIVE, config.PRESENTATION_DEFAUT)
quiz: Quiz
journal_bd: Journal  # journal de séance (table `journal` de la même base SQLite ; voir docs/postprod.md)
questions_bd: Questions  # questions des participants (table `questions_participants` ; voir docs/questions.md)
qr = SequenceQR()  # séquence questions-réponses en cours
replays = Replays(biblio)
captations = Captations(config.DOSSIER_CAPTATIONS)
taches = Taches()
_publication = asyncio.Event()  # levé par les votes : l'état est publié, regroupé, par la boucle de fond
_fond: asyncio.Task | None = None


@asynccontextmanager
async def cycle_de_vie(app: FastAPI):
    global quiz, journal_bd, questions_bd
    active = biblio.active()
    fichier_quiz = biblio.fichier_quiz(active) if active else None
    quiz = Quiz(charger_questions(fichier_quiz) if fichier_quiz else {}, config.CHEMIN_BDD)
    journal_bd = Journal(config.CHEMIN_BDD)
    questions_bd = Questions(config.CHEMIN_BDD)
    captations.preparer()
    app.state.quiz = quiz
    app.state.biblio = biblio
    app.state.journal = journal_bd
    app.state.questions = questions_bd
    app.state.qr = qr
    app.state.replays = replays
    if config.JETON_GENERE:
        journal.warning("AMPHI_JETON absent : jeton de session généré = %s", config.JETON)
    if active:
        biblio.activer(active)  # fige le choix par défaut dans active.json : créer une présentation ne change pas l'active
        biblio.dossier_medias(active).mkdir(exist_ok=True)
        recharger_marqueurs_obs()
        journal.info("présentation active : %s ; %d question(s) de quiz chargée(s) depuis %s", active, len(quiz.questions), fichier_quiz)
    else:
        journal.warning("aucune présentation dans %s", config.DOSSIER_PRESENTATIONS)
    global _fond
    _fond = asyncio.create_task(boucle_de_fond())
    yield
    _fond.cancel()


app = FastAPI(title="amphi-replay", lifespan=cycle_de_vie)


@app.middleware("http")
async def sans_cache_pour_les_pages(request: Request, call_next):
    """Pages et scripts de l'application : toujours revalidés (ETag), pour qu'un téléphone ou OBS
    ne garde jamais une ancienne télécommande ou un ancien deck après un déploiement."""
    reponse = await call_next(request)
    chemin = request.url.path
    if (chemin.startswith(("/deck/", "/q", "/static/", "/remote", "/editeur", "/presentations"))
            and not chemin.startswith(("/deck/vendor/", "/deck/medias/"))
            and "cache-control" not in reponse.headers):
        reponse.headers["Cache-Control"] = "no-cache"
    return reponse


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
        "questions_participants": questions_bd.en_attente(),
        "qr": qr.actif,
        "presentation": biblio.active(),
        "terminee": seance.terminee,
    }


def recharger_marqueurs_obs() -> None:
    """Relit les marqueurs `data-obs` de la présentation active (à l'activation et à chaque enregistrement de slides.html)."""
    actif = biblio.active()
    fichier = biblio.fichier_slides(actif) if actif else None
    seance.marqueurs = marqueurs_obs(fichier.read_text(encoding="utf-8")) if fichier and fichier.is_file() else {}


async def signaler_obs(h: int) -> None:
    """À l'arrivée sur une diapositive marquée `data-obs`, diffuse `{"type":"obs","action":…,"h":h}` sur /ws/quiz (pont OBS) ; une fois par arrivée."""
    if h == seance.dernier_h:
        return
    seance.dernier_h = h
    marqueur = seance.marqueurs.get(h)
    if marqueur:
        await quiz_ws.diffuser({"type": "obs", "action": ACTIONS_OBS[marqueur], "h": h})


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
                journal_bd.ajouter(biblio.active(), "diapositive", h, v, f)
                await deck_ws.diffuser({"type": "slide", "h": h, "v": v, "f": f}, sauf=ws)
                await signaler_obs(h)
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
        recharger_marqueurs_obs()
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
        return {"slug": None, "titre": "", "theme": "", "camera": None}
    meta = biblio.lire(actif)
    return {"slug": actif, "titre": meta["titre"], "theme": meta["theme"], "camera": meta["camera"]}


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
    journal_bd.ajouter(slug, "activation")
    etait_terminee = seance.terminee
    seance.reinitialiser()
    recharger_marqueurs_obs()
    await publier_quiz()
    if etait_terminee:
        await quiz_ws.diffuser({"type": "fin", "terminee": False})
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


# ---------------------------------------------------------------------------------
# Journal de séance (docs/postprod.md §1) : CORS ouvert, la page de post-production du PC 2 l'interroge sur le VPS
# ---------------------------------------------------------------------------------


CORS_JOURNAL = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS", "Access-Control-Allow-Headers": "Content-Type, Authorization", "Access-Control-Max-Age": "600"}


def reponse_cors(contenu, status_code: int = 200) -> JSONResponse:
    return JSONResponse(contenu, status_code=status_code, headers=CORS_JOURNAL)


@app.options("/api/journal")
@app.options("/api/journal/seances")
async def journal_options() -> Response:
    return Response(status_code=204, headers=CORS_JOURNAL)


@app.get("/api/journal")
async def journal_lire(prez: str, token: str = Query(default=""), depuis: float | None = None, jusqua: float | None = None) -> JSONResponse:
    if not jeton_valide(token):
        return reponse_cors({"detail": "jeton invalide"}, 403)
    if not biblio.existe(prez):
        return reponse_cors({"detail": "présentation inconnue"}, 404)
    return reponse_cors(journal_bd.lire(prez, depuis, jusqua))


@app.get("/api/journal/seances")
async def journal_seances(prez: str, token: str = Query(default="")) -> JSONResponse:
    """Séances (événements séparés de moins de 3 h), la plus récente d'abord, avec les titres des diapositives (aperçu des chapitres)."""
    if not jeton_valide(token):
        return reponse_cors({"detail": "jeton invalide"}, 403)
    if not biblio.existe(prez):
        return reponse_cors({"detail": "présentation inconnue"}, 404)
    titres = titres_presentation(biblio.exiger(prez))
    return reponse_cors({"prez": prez, "titre": biblio.lire(prez)["titre"], "titres": titres, "seances": journal_bd.seances(prez)})


# ---------------------------------------------------------------------------------
# Replays (docs/postprod.md §2)
# ---------------------------------------------------------------------------------


class ModificationReplay(BaseModel):
    titre: str


class DemandeExtraits(BaseModel):
    chapitres: list[int] | None = None


class DemandeScorm(BaseModel):
    titre: str | None = None
    inclure_quiz: bool = True
    video_unique: bool = False


def replay_ou_404(slug: str, id: str) -> None:
    slug_ou_404(slug)
    try:
        replays.chemin(slug, id)
    except KeyError:
        raise HTTPException(status_code=404, detail="replay inconnu")


# ---------------------------------------------------------------------------------
# Copies figées des séances (docs/seance.md §E) : relues par la post-production du PC 2
# ---------------------------------------------------------------------------------


@app.get("/api/presentations/{slug}/seances", dependencies=[Depends(verifier_jeton)])
async def seances_figees(slug: str) -> list[dict]:
    slug_ou_404(slug)
    return await asyncio.to_thread(biblio.seances_figees, slug)


@app.get("/api/presentations/{slug}/seances/{id_seance}.zip", dependencies=[Depends(verifier_jeton)])
async def seance_figee_zip(slug: str, id_seance: str) -> FileResponse:
    slug_ou_404(slug)
    try:
        dossier = biblio.seance_figee(slug, id_seance)
    except KeyError:
        raise HTTPException(status_code=404, detail="séance inconnue")

    def zipper() -> str:
        fd, chemin = tempfile.mkstemp(suffix=".zip")
        with open(fd, "wb") as brut, zipfile.ZipFile(brut, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(dossier.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(dossier).as_posix())
        return chemin

    chemin = await asyncio.to_thread(zipper)
    return FileResponse(chemin, media_type="application/zip", filename=f"{slug}-{id_seance}.zip",
                        background=BackgroundTask(lambda: Path(chemin).unlink(missing_ok=True)))


@app.get("/api/presentations/{slug}/replays")
async def replays_lister(slug: str) -> list[dict]:
    return await asyncio.to_thread(replays.lister, slug_ou_404(slug))


@app.get("/api/presentations/{slug}/replays/{id}")
async def replays_lire(slug: str, id: str) -> dict:
    replay_ou_404(slug, id)
    try:
        return await asyncio.to_thread(replays.detail, slug, id)
    except KeyError:
        raise HTTPException(status_code=404, detail="replay inconnu")


@app.put("/api/presentations/{slug}/replays/{id}", dependencies=[Depends(verifier_jeton)])
async def replays_renommer(slug: str, id: str, corps: ModificationReplay) -> dict:
    replay_ou_404(slug, id)
    try:
        return replays.renommer(slug, id, corps.titre)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail="replay inconnu")


@app.delete("/api/presentations/{slug}/replays/{id}", dependencies=[Depends(verifier_jeton)])
async def replays_supprimer(slug: str, id: str) -> dict:
    replay_ou_404(slug, id)
    replays.supprimer(slug, id)
    return {"ok": True}


@app.post("/api/presentations/{slug}/replays/{id}/extraits", dependencies=[Depends(verifier_jeton)])
async def replays_extraits(slug: str, id: str, corps: DemandeExtraits | None = None) -> dict:
    """Découpe a posteriori : tâche d'arrière-plan `{tache}`, à suivre sur /api/postprod/taches/{id}."""
    replay_ou_404(slug, id)
    if not outils_presents():
        raise HTTPException(status_code=503, detail="ffmpeg introuvable sur ce serveur")
    indices = corps.chapitres if corps else None

    def travail(tache):
        tache.etape("extraits")
        noms = replays.decouper(slug, id, indices, consigner=tache.consigner)
        tache.consigner(f"{len(noms)} extrait(s)")
        return {"slug": slug, "id": id, "extraits": noms}

    return {"tache": taches.lancer(["extraits"], travail).id}


@app.post("/api/presentations/{slug}/replays/{id}/scorm", dependencies=[Depends(verifier_jeton)])
async def replays_scorm_construire(slug: str, id: str, corps: DemandeScorm | None = None) -> dict:
    """Paquet SCORM 1.2 (docs/scorm.md) : tâche d'arrière-plan `{tache}` (extraits si besoin, affiche, zip), à suivre sur /api/postprod/taches/{id}."""
    replay_ou_404(slug, id)
    if not outils_presents():
        raise HTTPException(status_code=503, detail="ffmpeg introuvable sur ce serveur")
    options = corps or DemandeScorm()
    etapes = ([] if options.video_unique else ["extraits"]) + ["scorm"]

    def travail(tache):
        zip_path = construire_scorm(biblio.exiger(slug), replays.chemin(slug, id), titre=options.titre, inclure_quiz=options.inclure_quiz,
                                    video_unique=options.video_unique, journal=tache.consigner, etape=tache.etape)
        return {"slug": slug, "id": id, "scorm": replays.scorm(slug, id), "fichier": zip_path.name}

    return {"tache": taches.lancer(etapes, travail).id}


@app.delete("/api/presentations/{slug}/replays/{id}/scorm", dependencies=[Depends(verifier_jeton)])
async def replays_scorm_supprimer(slug: str, id: str) -> dict:
    replay_ou_404(slug, id)
    if not replays.supprimer_scorm(slug, id):
        raise HTTPException(status_code=404, detail="pas de paquet SCORM pour ce replay")
    return {"ok": True}


TYPES_REPLAY = {".mp4": "video/mp4", ".vtt": "text/vtt; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".json": "application/json", ".zip": "application/zip"}


@app.get("/p/{slug}/replays/{id}/{chemin:path}")
async def replay_fichier(slug: str, id: str, chemin: str) -> Response:
    """replay.mp4 (Range géré par FileResponse), chapitres.vtt, chapitres.txt, journal.json, extraits/<nom>.mp4, scorm/<nom>.zip (attachment)."""
    if not biblio.existe(slug):
        raise HTTPException(status_code=404)
    if chemin == "journal.json":
        try:
            replays.lire_journal(slug, id)  # reconstitue le journal si seul replay.mp4 existe
        except KeyError:
            raise HTTPException(status_code=404)
    fichier = replays.fichier(slug, id, chemin)
    if fichier is None:
        raise HTTPException(status_code=404)
    entetes = {"Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
    if chemin.startswith(("extraits/", "scorm/")) or chemin == "chapitres.txt":
        entetes["Content-Disposition"] = f'attachment; filename="{fichier.name}"'
    return FileResponse(fichier, media_type=TYPES_REPLAY.get(fichier.suffix.lower(), "application/octet-stream"), headers=entetes)


# ---------------------------------------------------------------------------------
# Post-production (docs/postprod.md §3) : sur le PC 2, serveur local
# ---------------------------------------------------------------------------------


class DemandeAssemblage(BaseModel):
    prez: str
    video: str
    audio: str | None = None
    seance_debut: float | None = None
    seance_fin: float | None = None
    decalage: float = 0.0
    titre: str | None = None
    garder_reference: bool = False
    extraits: bool = False
    serveur: str | None = None
    jeton_serveur: str | None = None
    couper_qr: bool = True  # tronquer la vidéo au `qr_ouvrir` de la séance (séquence questions-réponses hors replay)
    scorm: bool = True  # paquet SCORM 1.2 après les extraits (docs/scorm.md) ; les extraits sont alors découpés même si `extraits` est faux


@app.get("/api/postprod/configuration", dependencies=[Depends(verifier_jeton)])
async def postprod_configuration() -> dict:
    """Ce que la page de post-production préremplit : dossier de captations, URL du serveur de séance, présence de ffmpeg."""
    return {"captations": str(config.DOSSIER_CAPTATIONS), "seance_url": config.SEANCE_URL, "ffmpeg": outils_presents()}


@app.get("/api/postprod/captations", dependencies=[Depends(verifier_jeton)])
async def postprod_captations() -> list[dict]:
    return await asyncio.to_thread(captations.lister)


@app.post("/api/postprod/assembler", dependencies=[Depends(verifier_jeton)])
async def postprod_assembler(corps: DemandeAssemblage) -> dict:
    slug_ou_404(corps.prez)
    if not outils_presents():
        raise HTTPException(status_code=503, detail="ffmpeg / ffprobe introuvables : installer ffmpeg et l'ajouter au PATH")
    try:
        captations.fichier(corps.video)
        if corps.audio:
            captations.fichier(corps.audio)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    etapes = ["préparation", "assemblage"] + (["coupe"] if corps.couper_qr else []) + ["chapitrage"] + (["extraits"] if corps.extraits or corps.scorm else []) + (["scorm"] if corps.scorm else [])
    serveur = (corps.serveur or "").strip() or None

    def travail(tache):
        return assembler_replay(
            tache, replays, captations, journal_bd.lire,
            prez=corps.prez, video=corps.video, audio=corps.audio or None, seance_debut=corps.seance_debut, seance_fin=corps.seance_fin,
            decalage=corps.decalage or 0.0, titre=corps.titre, garder_reference=corps.garder_reference, extraits=corps.extraits,
            serveur=serveur, jeton_serveur=corps.jeton_serveur, couper_qr=corps.couper_qr, scorm=corps.scorm,
        )

    return {"tache": taches.lancer(etapes, travail).id}


@app.get("/api/postprod/taches/{id}", dependencies=[Depends(verifier_jeton)])
async def postprod_tache(id: str) -> dict:
    tache = taches.lire(id)
    if tache is None:
        raise HTTPException(status_code=404, detail="tâche inconnue")
    return tache.public()


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
    journal_bd.ajouter(biblio.active(), "quiz_ouvrir", question=cible.question_id)
    await publier_quiz()
    return quiz.etat()


@app.post("/api/quiz/fermer", dependencies=[Depends(verifier_jeton)])
async def quiz_fermer() -> dict:
    derniere = quiz.fermer()
    if derniere:
        journal_bd.ajouter(biblio.active(), "quiz_fermer", question=derniere)
    await publier_quiz(derniere)
    return quiz.etat(derniere)


@app.post("/api/quiz/reveler", dependencies=[Depends(verifier_jeton)])
async def quiz_reveler(cible: Cible) -> dict:
    try:
        quiz.reveler(cible.question_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="question inconnue")
    journal_bd.ajouter(biblio.active(), "quiz_reveler", question=cible.question_id)
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
    questions_bd.purger()
    await publier_quiz()
    await publier_questions()
    return {"ok": True}


# ---------------------------------------------------------------------------------
# Questions des participants (docs/questions.md)
# ---------------------------------------------------------------------------------


def contexte_diapositive() -> dict:
    """Diapositive courante : {h, v, numero, total, titre} (état du deck + titres de la présentation active)."""
    actif = biblio.active()
    titres = titres_presentation(biblio.exiger(actif)) if actif else []
    h, v = int(etat_deck.indices.get("h", 0)), int(etat_deck.indices.get("v", 0))
    titre = titres[h] if 0 <= h < len(titres) else f"Diapositive {h + 1}"
    return {"h": h, "v": v, "numero": h + 1, "total": len(titres), "titre": titre}


def message_qr() -> dict:
    question = questions_bd.lire(qr.question_id) if qr.question_id is not None else None
    return {"type": "qr", "actif": qr.actif, "question": question_publique(question), "restantes": questions_bd.en_attente()}


async def publier_questions() -> None:
    """Compteur des questions en attente, pour la télécommande (badge) et les participants."""
    await quiz_ws.diffuser({"type": "questions", "en_attente": questions_bd.en_attente()})


async def publier_qr() -> None:
    """État de la séquence Q/R : écran et télécommande (/ws/deck), pont OBS et participants (/ws/quiz)."""
    message = message_qr()
    await deck_ws.diffuser(message)
    await quiz_ws.diffuser(message)


class NouvelleQuestion(BaseModel):
    texte: str
    generale: bool = False


class EtatQuestion(BaseModel):
    etat: str


class OrdreQuestions(BaseModel):
    ids: list[int]


@app.get("/api/questions/contexte")
async def questions_contexte() -> dict:
    return contexte_diapositive()


@app.post("/api/questions")
async def questions_poser(corps: NouvelleQuestion, request: Request, response: Response) -> dict:
    pid = identifiant_participant(request, response)
    pseudo = quiz.pseudo(pid)
    contexte = contexte_diapositive()
    try:
        q = questions_bd.ajouter(biblio.active(), pid, pseudo, corps.texte, contexte["h"], contexte["v"], contexte["titre"], corps.generale)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=429, detail=str(e))
    await publier_questions()
    return {"id": q["id"], "numero": q["numero"], "titre": q["titre"], "generale": q["generale"]}


@app.get("/api/questions", dependencies=[Depends(verifier_jeton)])
async def questions_lister(etat: str | None = None) -> list[dict]:
    return [{k: v for k, v in q.items() if k != "participant"} for q in questions_bd.lister(etat or None)]


@app.get("/api/questions/publiques")
async def questions_publiques(etat: str | None = None) -> list[dict]:
    return questions_bd.lister(etat or None, avec_pseudo=False)


@app.get("/api/questions/export", dependencies=[Depends(verifier_jeton)])
async def questions_export() -> PlainTextResponse:
    return PlainTextResponse(questions_bd.export_csv(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="questions.csv"', **CORS_JOURNAL})


@app.options("/api/questions/export")
async def questions_export_options() -> Response:
    return Response(status_code=204, headers=CORS_JOURNAL)


@app.post("/api/questions/ordre", dependencies=[Depends(verifier_jeton)])
async def questions_ordre(corps: OrdreQuestions) -> dict:
    questions_bd.reordonner(corps.ids)
    return {"ok": True}


@app.post("/api/questions/purger", dependencies=[Depends(verifier_jeton)])
async def questions_purger() -> dict:
    questions_bd.purger()
    qr.question_id = None
    await publier_questions()
    if qr.actif:
        await publier_qr()
    return {"ok": True}


@app.post("/api/questions/{id}/etat", dependencies=[Depends(verifier_jeton)])
async def questions_etat(id: int, corps: EtatQuestion) -> dict:
    try:
        q = questions_bd.changer_etat(id, corps.etat)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail="question inconnue")
    if qr.question_id == id and corps.etat != "en_cours":
        qr.question_id = None
    await publier_questions()
    if qr.actif:
        await publier_qr()
    return {k: v for k, v in q.items() if k != "participant"}


@app.delete("/api/questions/{id}", dependencies=[Depends(verifier_jeton)])
async def questions_supprimer(id: int) -> dict:
    if not questions_bd.supprimer(id):
        raise HTTPException(status_code=404, detail="question inconnue")
    if qr.question_id == id:
        qr.question_id = None
    await publier_questions()
    if qr.actif:
        await publier_qr()
    return {"ok": True}


# --- Séquence questions-réponses ---------------------------------------------------------


async def deplacer_deck(h: int, v: int, f: int) -> None:
    """Comme un message `slide` du remote, mais émis par le serveur : tous les rôles le reçoivent (la miniature de la télécommande suit)."""
    etat_deck.appliquer(h, v, f)
    journal_bd.ajouter(biblio.active(), "diapositive", h, v, f)
    await deck_ws.diffuser({"type": "slide", "h": h, "v": v, "f": f, "source": "qr"})
    await signaler_obs(h)


async def passer_a_la_question(q: dict) -> None:
    courante = questions_bd.en_cours()
    if courante and courante["id"] != q["id"]:
        questions_bd.changer_etat(courante["id"], "repondue")
    questions_bd.changer_etat(q["id"], "en_cours")
    qr.question_id = q["id"]
    await deplacer_deck(int(q["h"]), int(q["v"]), -1)
    await publier_qr()
    await publier_questions()


def etat_qr() -> dict:
    return {**qr.etat(), "question": message_qr()["question"], "restantes": questions_bd.en_attente()}


class CibleQuestion(BaseModel):
    id: int


class RetourListe(BaseModel):
    repondue: bool = True


@app.get("/api/qr")
async def qr_lire() -> dict:
    return etat_qr()


@app.post("/api/qr/ouvrir", dependencies=[Depends(verifier_jeton)])
async def qr_ouvrir() -> dict:
    if not qr.actif:
        qr.ouvrir(etat_deck.indices)
        journal_bd.ajouter(biblio.active(), "qr_ouvrir", **qr.retour)
    await publier_qr()
    return etat_qr()


@app.post("/api/qr/question", dependencies=[Depends(verifier_jeton)])
async def qr_question(cible: CibleQuestion) -> dict:
    if not qr.actif:
        raise HTTPException(status_code=409, detail="séquence questions-réponses non ouverte")
    q = questions_bd.lire(cible.id)
    if q is None:
        raise HTTPException(status_code=404, detail="question inconnue")
    await passer_a_la_question(q)
    return etat_qr()


@app.post("/api/qr/suivante", dependencies=[Depends(verifier_jeton)])
async def qr_suivante() -> dict:
    if not qr.actif:
        raise HTTPException(status_code=409, detail="séquence questions-réponses non ouverte")
    courante = questions_bd.en_cours()
    if courante:
        questions_bd.changer_etat(courante["id"], "repondue")
    qr.question_id = None
    suivante = questions_bd.premiere_en_attente()
    if suivante is None:
        await publier_qr()
        await publier_questions()
        return {"fin": True, **etat_qr()}
    await passer_a_la_question(suivante)
    return {"fin": False, **etat_qr()}


@app.post("/api/qr/liste", dependencies=[Depends(verifier_jeton)])
async def qr_liste(corps: RetourListe | None = None) -> dict:
    courante = questions_bd.en_cours()
    if courante:
        questions_bd.changer_etat(courante["id"], "repondue" if corps is None or corps.repondue else "en_attente")
    qr.question_id = None
    await publier_qr()
    await publier_questions()
    return etat_qr()


@app.post("/api/qr/fermer", dependencies=[Depends(verifier_jeton)])
async def qr_fermer() -> dict:
    if not qr.actif:
        return etat_qr()
    courante = questions_bd.en_cours()
    if courante:
        questions_bd.changer_etat(courante["id"], "repondue")
    retour = qr.fermer()
    await deplacer_deck(retour["h"], retour["v"], retour["f"])
    journal_bd.ajouter(biblio.active(), "qr_fermer", **retour)
    await publier_qr()
    await publier_questions()
    return etat_qr()


# ---------------------------------------------------------------------------------
# Fin de séance (docs/seance.md, C)
# ---------------------------------------------------------------------------------


def etat_seance() -> dict:
    actif = biblio.active()
    return {"terminee": seance.terminee, "contact": biblio.lire(actif)["contact"] if actif else normaliser_contact(None), "presentation": actif}


@app.get("/api/seance")
async def seance_lire() -> dict:
    return etat_seance()


@app.post("/api/seance/terminer", dependencies=[Depends(verifier_jeton)])
async def seance_terminer() -> dict:
    """Bouton Quitter de la télécommande : ferme la question ouverte, termine la séquence Q/R, arrête OBS, journalise `fin`, prévient les participants."""
    derniere = quiz.fermer()
    if derniere:
        journal_bd.ajouter(biblio.active(), "quiz_fermer", question=derniere)
        await publier_quiz(derniere)
    if qr.actif:
        courante = questions_bd.en_cours()
        if courante:
            questions_bd.changer_etat(courante["id"], "repondue")
        retour = qr.fermer()
        journal_bd.ajouter(biblio.active(), "qr_fermer", **retour)
        await publier_qr()
        await publier_questions()
    await quiz_ws.diffuser({"type": "obs", "action": "StopRecord", "h": int(etat_deck.indices.get("h", 0))})  # sécurité : l'enregistrement s'arrête avec la séance
    seance.terminee = True
    journal_bd.ajouter(biblio.active(), "fin", **etat_deck.indices)
    etat = etat_seance()
    await quiz_ws.diffuser({"type": "fin", "terminee": True, "contact": etat["contact"], "classement_total": len(quiz.classement(1000))})
    return etat


@app.post("/api/seance/reprendre", dependencies=[Depends(verifier_jeton)])
async def seance_reprendre() -> dict:
    """Bouton « Revenir au début » : la séance reprend, les participants reviennent à « En attente »."""
    if seance.terminee:
        seance.terminee = False
        await quiz_ws.diffuser({"type": "fin", "terminee": False})
    return etat_seance()


ECART_ANCRE_S = 2.0  # deux débuts d'enregistrement à moins de 2 s l'un de l'autre sont le même (le pont le renvoie à chaque reconnexion)


def signaler_enregistrement(actif: bool, duree_ms: float) -> dict:
    """Ancre du replay interactif (docs/seance.md §E) : le pont OBS signale l'état de l'enregistrement.

    `duree_ms` = durée déjà enregistrée selon OBS (GetRecordStatus.outputDuration) : le début est daté avec l'horloge du
    serveur, comme le reste du journal, à la latence du réseau près. Au début, la présentation active est figée.
    """
    slug = biblio.active()
    if not slug:
        return {"type": "enregistrement", "ok": False, "raison": "aucune présentation active"}
    maintenant = time.time()
    if not actif:
        journal_bd.ajouter(slug, "enregistrement_fin", horodatage=maintenant)
        return {"type": "enregistrement", "ok": True, "actif": False}
    debut = maintenant - max(0.0, float(duree_ms)) / 1000
    deja = [e for e in journal_bd.lire(slug, debut - ECART_ANCRE_S, debut + ECART_ANCRE_S) if e["type"] == "enregistrement_debut"]
    if deja:
        debut = deja[0]["horodatage"]
    else:
        journal_bd.ajouter(slug, "enregistrement_debut", horodatage=debut)
    try:
        id_seance = biblio.figer(slug, debut)
    except OSError as e:
        journal.warning("copie figée de %s impossible : %s", slug, e)
        id_seance = None
    return {"type": "enregistrement", "ok": True, "actif": True, "debut": debut, "seance": id_seance, "presentation": slug}


@app.websocket("/ws/quiz")
async def ws_quiz(ws: WebSocket, token: str = Query(default="")) -> None:
    """Pousse l'état du quiz aux participants, aux pages de résultats et à la télécommande.

    Avec le jeton (pont OBS), accepte aussi `{"type": "enregistrement", "actif": bool, "duree_ms": n}`.
    """
    autorise = bool(token) and jeton_valide(token)
    await quiz_ws.ajouter(ws, "quiz")
    try:
        await ws.send_json({"type": "quiz", **quiz.etat()})  # l'état des questions / de la séquence Q/R est relu par HTTP à la connexion (GET /api/qr)
        while True:
            message = await ws.receive_json()
            if message.get("type") == "ping":
                await ws.send_json({"type": "pong", "reste": quiz.reste})
            elif message.get("type") == "enregistrement" and autorise:
                try:
                    duree_ms = float(message.get("duree_ms") or 0)
                except (TypeError, ValueError):
                    continue
                reponse = await asyncio.to_thread(signaler_enregistrement, bool(message.get("actif")), duree_ms)
                await ws.send_json(reponse)
    except WebSocketDisconnect:
        pass
    finally:
        quiz_ws.retirer(ws)


@app.exception_handler(404)
async def introuvable(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": "introuvable"}, status_code=404)

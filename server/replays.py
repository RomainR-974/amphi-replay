"""Replays d'une présentation et post-production (voir docs/postprod.md, sections 2 et 3).

    presentations/<slug>/replays/<AAAA-MM-JJ_HHMM>/
      replay.mp4            vidéo finale (chapitres MP4 embarqués)
      journal.json          métadonnées (titre, date, durée, sources, séance, chapitres, coupe_qr_s) + mesures du recalage
      questions.csv         questions des participants exportées du serveur de séance (si fournies)
      chapitres.txt         format YouTube « 00:00 Titre »
      chapitres.vtt         WebVTT (piste de chapitres du lecteur)
      extraits/NN-titre.mp4 capsules par chapitre (optionnel)
      scorm/<slug>-<id>-scorm.zip + scorm.json   paquet SCORM 1.2 (optionnel ; voir docs/scorm.md et server/scorm.py)

Tout chemin manipulé est résolu et confiné au dossier de la présentation (anti-traversée). Les traitements
longs (assemblage, chapitrage, extraits) tournent dans une tâche d'arrière-plan : un fil d'exécution qui
lance des sous-processus ffmpeg / assembler.py et consigne leurs lignes.

Aucune dépendance au serveur : ValueError (entrée invalide), KeyError (replay inconnu), RuntimeError (outil
ou commande en échec) ; app.py traduit en HTTP.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from .journal import titres_presentation

journal = logging.getLogger("amphi.replays")

RACINE = Path(__file__).resolve().parent.parent
ASSEMBLEUR = RACINE / "postprod" / "assembler.py"
ID_REPLAY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
NOM_EXTRAIT = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}\.mp4$")
NOM_SCORM = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,120}\.zip$")
EXTENSIONS_VIDEO = {".mkv", ".mp4", ".mov", ".webm", ".m4v", ".ts", ".avi"}
EXTENSIONS_AUDIO = {".wav", ".m4a", ".mp3", ".aac", ".flac", ".ogg", ".opus", ".aiff"}
FUSION_CHAPITRES_S = 20.0
DUREE_MIN_EXTRAIT_S = 1.0


# -------------------------------------------------------------------------------------
# Utilitaires
# -------------------------------------------------------------------------------------


def outils_presents() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def executer(commande: list[str], *, consigner: Callable[[str], None] | None = None, cwd: Path | None = None) -> None:
    """Lance une commande et pousse chaque ligne de sa sortie (stdout + stderr) dans `consigner`. RuntimeError si elle échoue."""
    if consigner:
        consigner("$ " + " ".join(commande))
    try:
        proc = subprocess.Popen(commande, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", cwd=str(cwd) if cwd else None)
    except FileNotFoundError:
        raise RuntimeError(f"outil introuvable : {commande[0]} (installer ffmpeg et l'ajouter au PATH)")
    dernieres: list[str] = []
    assert proc.stdout is not None
    for ligne in proc.stdout:
        ligne = ligne.rstrip("\r\n")
        if not ligne.strip():
            continue
        dernieres.append(ligne)
        dernieres = dernieres[-8:]
        if consigner:
            consigner(ligne)
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"commande échouée (code {code}) : {Path(commande[0]).name}" + (" — " + dernieres[-1] if dernieres else ""))


def sonder(fichier: Path) -> dict:
    """ffprobe : {duree, chapitres: [{debut, titre}], video: bool, audio: int}. Lève RuntimeError si l'outil manque ou échoue."""
    commande = ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type:chapter=start_time:chapter_tags=title",
                "-of", "json", str(fichier)]
    try:
        resultat = subprocess.run(commande, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError("ffprobe introuvable (installer ffmpeg et l'ajouter au PATH)")
    if resultat.returncode != 0:
        raise RuntimeError(f"ffprobe : {resultat.stderr.strip()[-300:] or 'échec'}")
    donnees = json.loads(resultat.stdout or "{}")
    try:
        duree = float(donnees.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duree = 0.0
    chapitres = []
    for c in donnees.get("chapters", []):
        try:
            chapitres.append({"debut": round(float(c.get("start_time", 0.0)), 3), "titre": str((c.get("tags") or {}).get("title") or "")})
        except (TypeError, ValueError):
            continue
    flux = [s.get("codec_type") for s in donnees.get("streams", [])]
    return {"duree": round(duree, 3), "chapitres": chapitres, "video": "video" in flux, "audio": flux.count("audio")}


def duree_fichier(fichier: Path) -> float | None:
    try:
        return sonder(fichier)["duree"]
    except (RuntimeError, ValueError):
        return None


def slugifier(texte: str, longueur: int = 40) -> str:
    texte = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode()
    texte = re.sub(r"[^a-z0-9]+", "-", texte.lower()).strip("-")
    return texte[:longueur].rstrip("-") or "chapitre"


def formater_temps(secondes: float) -> str:
    """Format YouTube : MM:SS, ou H:MM:SS au-delà d'une heure."""
    s = max(0, int(round(secondes)))
    h, reste = divmod(s, 3600)
    m, sec = divmod(reste, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def _vtt_temps(secondes: float) -> str:
    s = max(0.0, secondes)
    h, reste = divmod(int(s), 3600)
    m, sec = divmod(reste, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}.{int(round((s - int(s)) * 1000)) % 1000:03d}"


def _confiner(racine: Path, chemin: str | Path) -> Path | None:
    """Chemin résolu à l'intérieur de `racine`, ou None s'il en sort."""
    racine = racine.resolve()
    try:
        cible = (racine / chemin).resolve()
    except (OSError, ValueError):
        return None
    if cible != racine and racine not in cible.parents:
        return None
    return cible


# -------------------------------------------------------------------------------------
# Chapitrage
# -------------------------------------------------------------------------------------


def calculer_chapitres(evenements: list[dict], debut_enregistrement: float, duree: float, titres: list[str], decalage: float = 0.0) -> list[dict]:
    """Chapitres [{debut, titre, h}] à partir des événements `diapositive` d'une séance.

    - instant = horodatage − (début de l'enregistrement + décalage), gardé s'il est dans [0, durée] ;
    - un changement de fragment (même `h` que le précédent) ne crée pas de chapitre ;
    - deux changements à moins de 20 s sont fusionnés (le dernier titre l'emporte) ;
    - le premier chapitre est ramené à 00:00.
    """
    origine = debut_enregistrement + decalage
    bruts: list[dict] = []
    dernier_h: int | None = None
    for ev in sorted(evenements, key=lambda e: e.get("horodatage", 0.0)):
        if ev.get("type") != "diapositive" or ev.get("h") is None:
            continue
        h = int(ev["h"])
        instant = float(ev["horodatage"]) - origine
        if instant < 0 or instant > duree:
            continue
        if h == dernier_h:
            continue
        dernier_h = h
        titre = titres[h] if 0 <= h < len(titres) else f"Diapositive {h + 1}"
        bruts.append({"debut": round(instant, 3), "titre": titre, "h": h})
    chapitres: list[dict] = []
    for c in bruts:
        if chapitres and c["debut"] - chapitres[-1]["debut"] < FUSION_CHAPITRES_S:
            chapitres[-1]["titre"], chapitres[-1]["h"] = c["titre"], c["h"]
        else:
            chapitres.append(dict(c))
    if chapitres:
        chapitres[0]["debut"] = 0.0
    return chapitres


def instant_qr(evenements: list[dict], debut_enregistrement: float, decalage: float = 0.0) -> float | None:
    """Instant (s depuis le début de la vidéo) du premier `qr_ouvrir` de la séance, ou None s'il n'y en a pas."""
    origine = debut_enregistrement + decalage
    instants = [float(ev["horodatage"]) - origine for ev in evenements if ev.get("type") == "qr_ouvrir" and ev.get("horodatage") is not None]
    return round(min(instants), 3) if instants else None


def couper_video(video: Path, instant: float, consigner: Callable[[str], None] | None = None) -> None:
    """Tronque `video` à `instant` (remux `-c copy -to`), vers un fichier temporaire puis remplacement."""
    with tempfile.TemporaryDirectory(prefix="amphi-coupe-", dir=str(video.parent)) as tmp:
        sortie = Path(tmp) / "coupe.mp4"
        executer(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-to", f"{instant:.3f}", "-map", "0", "-c", "copy", "-movflags", "+faststart", str(sortie)], consigner=consigner)
        os.replace(sortie, video)


def texte_chapitres(chapitres: list[dict]) -> str:
    return "".join(f"{formater_temps(c['debut'])} {c['titre']}\n" for c in chapitres)


def vtt_chapitres(chapitres: list[dict], duree: float) -> str:
    lignes = ["WEBVTT", ""]
    for i, c in enumerate(chapitres):
        fin = chapitres[i + 1]["debut"] if i + 1 < len(chapitres) else max(duree, c["debut"] + 1)
        lignes += [str(i + 1), f"{_vtt_temps(c['debut'])} --> {_vtt_temps(fin)}", c["titre"], ""]
    return "\n".join(lignes)


def ffmetadata(chapitres: list[dict], duree: float, titre: str = "") -> str:
    def echapper(t: str) -> str:
        return re.sub(r"([=;#\\\n])", r"\\\1", t)

    lignes = [";FFMETADATA1"]
    if titre:
        lignes.append("title=" + echapper(titre))
    for i, c in enumerate(chapitres):
        fin = chapitres[i + 1]["debut"] if i + 1 < len(chapitres) else max(duree, c["debut"] + 1)
        lignes += ["", "[CHAPTER]", "TIMEBASE=1/1000", f"START={int(round(c['debut'] * 1000))}", f"END={int(round(fin * 1000))}", "title=" + echapper(c["titre"])]
    return "\n".join(lignes) + "\n"


def embarquer_chapitres(video: Path, chapitres: list[dict], duree: float, titre: str = "", consigner: Callable[[str], None] | None = None) -> None:
    """Réécrit les chapitres MP4 : remux `-map_metadata 1 -c copy` vers un fichier temporaire, puis remplacement."""
    with tempfile.TemporaryDirectory(prefix="amphi-chapitres-", dir=str(video.parent)) as tmp:
        meta = Path(tmp) / "ffmetadata.txt"
        meta.write_text(ffmetadata(chapitres, duree, titre), encoding="utf-8")
        sortie = Path(tmp) / "avec-chapitres.mp4"
        executer(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-f", "ffmetadata", "-i", str(meta),
                  "-map", "0", "-map_metadata", "1", "-map_chapters", "1", "-c", "copy", "-movflags", "+faststart", str(sortie)], consigner=consigner)
        os.replace(sortie, video)


def ecrire_fichiers_chapitres(dossier: Path, chapitres: list[dict], duree: float) -> None:
    (dossier / "chapitres.txt").write_text(texte_chapitres(chapitres), encoding="utf-8")
    (dossier / "chapitres.vtt").write_text(vtt_chapitres(chapitres, duree), encoding="utf-8")


def decouper_extraits(dossier: Path, chapitres: list[dict], duree: float, indices: list[int] | None = None,
                      consigner: Callable[[str], None] | None = None) -> list[str]:
    """Découpe `replay.mp4` par chapitre (copie des flux) dans extraits/NN-titre.mp4 ; renvoie les noms créés."""
    video = dossier / "replay.mp4"
    if not video.is_file():
        raise RuntimeError("replay.mp4 absent")
    cibles = range(len(chapitres)) if indices is None else [i for i in indices if 0 <= i < len(chapitres)]
    sortie_dossier = dossier / "extraits"
    sortie_dossier.mkdir(exist_ok=True)
    noms = []
    for i in cibles:
        debut = chapitres[i]["debut"]
        fin = chapitres[i + 1]["debut"] if i + 1 < len(chapitres) else duree
        if fin - debut < DUREE_MIN_EXTRAIT_S:
            if consigner:
                consigner(f"extrait {i + 1} ignoré : trop court ({fin - debut:.1f} s)")
            continue
        nom = f"{i + 1:02d}-{slugifier(chapitres[i]['titre'])}.mp4"
        executer(["ffmpeg", "-y", "-v", "error", "-ss", f"{debut:.3f}", "-to", f"{fin:.3f}", "-i", str(video),
                  "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(sortie_dossier / nom)], consigner=consigner)
        noms.append(nom)
    return noms


# -------------------------------------------------------------------------------------
# Bibliothèque des replays
# -------------------------------------------------------------------------------------


class Replays:
    """Replays rangés dans presentations/<slug>/replays/<id>/."""

    def __init__(self, biblio) -> None:
        self.biblio = biblio

    # --- Chemins ---------------------------------------------------------------------

    def dossier(self, slug: str) -> Path:
        return self.biblio.exiger(slug) / "replays"

    def chemin(self, slug: str, id: str) -> Path:
        """Dossier d'un replay existant ; KeyError sinon. L'identifiant est validé et confiné."""
        if not ID_REPLAY.match(id or ""):
            raise KeyError(id)
        racine = self.dossier(slug)
        d = _confiner(racine, id)
        if d is None or d.parent != racine.resolve() or not d.is_dir():
            raise KeyError(id)
        return d

    def fichier(self, slug: str, id: str, chemin: str) -> Path | None:
        """Fichier public d'un replay (replay.mp4, chapitres.vtt/txt, journal.json, extraits/<nom>.mp4, scorm/<nom>.zip) ou None."""
        try:
            d = self.chemin(slug, id)
        except KeyError:
            return None
        if chemin not in ("replay.mp4", "chapitres.vtt", "chapitres.txt", "journal.json"):
            extrait = chemin.startswith("extraits/") and NOM_EXTRAIT.match(chemin[len("extraits/") :])
            paquet = chemin.startswith("scorm/") and NOM_SCORM.match(chemin[len("scorm/") :])
            if not extrait and not paquet:
                return None
        f = _confiner(d, chemin)
        return f if f is not None and f.is_file() else None

    def nouvel_identifiant(self, slug: str, instant: float) -> str:
        base = datetime.fromtimestamp(instant).strftime("%Y-%m-%d_%H%M")
        racine = self.dossier(slug)
        id, n = base, 1
        while (racine / id).exists():
            n += 1
            id = f"{base}-{n}"
        return id

    # --- Métadonnées -----------------------------------------------------------------

    def lire_journal(self, slug: str, id: str) -> dict:
        """journal.json du replay ; reconstitué par ffprobe (et chapitres MP4) s'il manque et que replay.mp4 existe."""
        d = self.chemin(slug, id)
        fichier = d / "journal.json"
        if fichier.is_file():
            try:
                donnees = json.loads(fichier.read_text(encoding="utf-8"))
                if isinstance(donnees, dict):
                    return donnees
            except (OSError, ValueError):
                pass
        video = d / "replay.mp4"
        if not video.is_file():
            raise KeyError(id)
        return self.reconstituer(slug, id)

    def reconstituer(self, slug: str, id: str) -> dict:
        d = self.chemin(slug, id)
        video = d / "replay.mp4"
        try:
            sonde = sonder(video)
        except RuntimeError as e:
            journal.warning("replay %s/%s : %s", slug, id, e)
            sonde = {"duree": 0.0, "chapitres": []}
        mtime = video.stat().st_mtime
        date = datetime.fromtimestamp(mtime - sonde["duree"]).replace(microsecond=0).isoformat()
        titre_prez = self.biblio.lire(slug)["titre"]
        donnees = {
            "titre": f"{titre_prez} – {date[:10]}",
            "date": date,
            "duree": sonde["duree"],
            "source_video": None,
            "source_audio": None,
            "seance": None,
            "decalage": 0.0,
            "chapitres": sonde["chapitres"],
            "mesures": None,
            "reconstitue": True,
            "cree": datetime.now().replace(microsecond=0).isoformat(),
        }
        if sonde["chapitres"] and not (d / "chapitres.vtt").is_file():
            ecrire_fichiers_chapitres(d, sonde["chapitres"], sonde["duree"])
        (d / "journal.json").write_text(json.dumps(donnees, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return donnees

    def ecrire_journal(self, slug: str, id: str, donnees: dict) -> None:
        (self.chemin(slug, id) / "journal.json").write_text(json.dumps(donnees, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def extraits(self, slug: str, id: str) -> list[dict]:
        d = self.chemin(slug, id) / "extraits"
        if not d.is_dir():
            return []
        return [{"nom": f.name, "taille": f.stat().st_size} for f in sorted(d.iterdir()) if f.is_file() and NOM_EXTRAIT.match(f.name)]

    def scorm(self, slug: str, id: str) -> dict | None:
        """Métadonnées du paquet SCORM ({fichier, taille, genere, chapitres, questions, inclure_quiz, video_unique}) ou None."""
        from .scorm import lire_scorm

        return lire_scorm(self.chemin(slug, id))

    def supprimer_scorm(self, slug: str, id: str) -> bool:
        from .scorm import supprimer_scorm

        return supprimer_scorm(self.chemin(slug, id))

    def resume(self, slug: str, id: str) -> dict:
        d = self.chemin(slug, id)
        donnees = self.lire_journal(slug, id)
        video = d / "replay.mp4"
        paquet = self.scorm(slug, id)
        return {
            "id": id,
            "titre": str(donnees.get("titre") or id),
            "date": str(donnees.get("date") or ""),
            "duree": float(donnees.get("duree") or 0.0),
            "chapitres": len(donnees.get("chapitres") or []),
            "extraits": len(self.extraits(slug, id)),
            "taille": video.stat().st_size if video.is_file() else 0,
            "scorm": paquet["fichier"] if paquet else None,
        }

    def detail(self, slug: str, id: str) -> dict:
        donnees = self.lire_journal(slug, id)
        return {**self.resume(slug, id), "chapitres": donnees.get("chapitres") or [], "extraits": self.extraits(slug, id), "scorm": self.scorm(slug, id), "journal": donnees}

    def lister(self, slug: str) -> list[dict]:
        racine = self.dossier(slug)
        if not racine.is_dir():
            return []
        resumes = []
        for d in sorted(racine.iterdir(), reverse=True):
            if not d.is_dir() or not ID_REPLAY.match(d.name) or not (d / "replay.mp4").is_file():
                continue
            try:
                resumes.append(self.resume(slug, d.name))
            except (KeyError, OSError) as e:
                journal.warning("replay %s/%s ignoré : %s", slug, d.name, e)
        return resumes

    # --- Actions ---------------------------------------------------------------------

    def renommer(self, slug: str, id: str, titre: str) -> dict:
        titre = re.sub(r"\s+", " ", titre or "").strip()
        if not titre:
            raise ValueError("titre vide")
        donnees = self.lire_journal(slug, id)
        donnees["titre"] = titre[:200]
        self.ecrire_journal(slug, id, donnees)
        return self.resume(slug, id)

    def supprimer(self, slug: str, id: str) -> None:
        shutil.rmtree(self.chemin(slug, id))

    def decouper(self, slug: str, id: str, indices: list[int] | None = None, consigner: Callable[[str], None] | None = None) -> list[str]:
        d = self.chemin(slug, id)
        donnees = self.lire_journal(slug, id)
        chapitres = donnees.get("chapitres") or []
        if not chapitres:
            raise ValueError("ce replay n'a pas de chapitres")
        return decouper_extraits(d, chapitres, float(donnees.get("duree") or 0.0), indices, consigner)


# -------------------------------------------------------------------------------------
# Dossier de captations
# -------------------------------------------------------------------------------------


class Captations:
    """Fichiers du dossier où OBS enregistre (vidéos) et où l'on copie l'audio du téléphone."""

    def __init__(self, dossier: Path) -> None:
        self.dossier = Path(dossier)
        self._durees: dict[tuple[str, float, int], float | None] = {}

    def preparer(self) -> None:
        try:
            self.dossier.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            journal.warning("dossier de captations %s non créé : %s", self.dossier, e)

    def fichier(self, nom: str) -> Path:
        """Fichier du dossier (nom simple, extension vidéo ou audio) ; ValueError sinon."""
        if not nom or nom != Path(nom).name or Path(nom).suffix.lower() not in EXTENSIONS_VIDEO | EXTENSIONS_AUDIO:
            raise ValueError(f"fichier de captation invalide : {nom!r}")
        f = _confiner(self.dossier, nom)
        if f is None or f.parent != self.dossier.resolve() or not f.is_file():
            raise ValueError(f"fichier introuvable dans le dossier de captations : {nom}")
        return f

    def lister(self) -> list[dict]:
        if not self.dossier.is_dir():
            return []
        fichiers = []
        for f in self.dossier.iterdir():
            suffixe = f.suffix.lower()
            if not f.is_file() or suffixe not in EXTENSIONS_VIDEO | EXTENSIONS_AUDIO:
                continue
            st = f.stat()
            cle = (f.name, st.st_mtime, st.st_size)
            if cle not in self._durees:
                self._durees[cle] = duree_fichier(f) if outils_presents() else None
            fichiers.append({
                "nom": f.name,
                "taille": st.st_size,
                "modifie": st.st_mtime,
                "duree": self._durees[cle],
                "genre": "video" if suffixe in EXTENSIONS_VIDEO else "audio",
            })
        fichiers.sort(key=lambda x: x["modifie"], reverse=True)
        return fichiers


# -------------------------------------------------------------------------------------
# Tâches d'arrière-plan
# -------------------------------------------------------------------------------------


class Tache:
    def __init__(self, id: str, etapes: list[str]) -> None:
        self.id = id
        self.etat = "en_cours"
        self.etapes = [{"nom": e, "etat": "attente"} for e in etapes]
        self.journal: list[str] = []
        self.replay: dict | None = None
        self.erreur: str | None = None
        self.cree = time.time()
        self._verrou = threading.Lock()

    def consigner(self, ligne: str) -> None:
        with self._verrou:
            self.journal.append(ligne)
            if len(self.journal) > 500:
                del self.journal[: len(self.journal) - 500]

    def etape(self, nom: str, etat: str = "en_cours") -> None:
        with self._verrou:
            for e in self.etapes:
                if e["nom"] == nom:
                    e["etat"] = etat
                elif etat == "en_cours" and e["etat"] == "en_cours":
                    e["etat"] = "terminee"
        if etat == "en_cours":
            self.consigner(f"== {nom}")

    def public(self) -> dict:
        with self._verrou:
            return {"id": self.id, "etat": self.etat, "etapes": [dict(e) for e in self.etapes], "journal": list(self.journal),
                    "replay": self.replay, "erreur": self.erreur}


class Taches:
    """Registre des tâches ; chacune tourne dans un fil démon."""

    def __init__(self) -> None:
        self.taches: dict[str, Tache] = {}
        self._verrou = threading.Lock()

    def lancer(self, etapes: list[str], travail: Callable[[Tache], dict | None]) -> Tache:
        tache = Tache(uuid.uuid4().hex[:12], etapes)
        with self._verrou:
            # Oublier les tâches terminées depuis plus d'une heure.
            for id, t in list(self.taches.items()):
                if t.etat != "en_cours" and time.time() - t.cree > 3600:
                    del self.taches[id]
            self.taches[tache.id] = tache

        def courir() -> None:
            try:
                tache.replay = travail(tache)
                for e in tache.etapes:
                    if e["etat"] == "en_cours":
                        e["etat"] = "terminee"
                tache.etat = "terminee"
                tache.consigner("terminé")
            except Exception as e:  # toute erreur est rendue lisible dans la tâche
                journal.exception("tâche %s", tache.id)
                tache.erreur = str(e)
                tache.etat = "erreur"
                for et in tache.etapes:
                    if et["etat"] == "en_cours":
                        et["etat"] = "erreur"
                tache.consigner("ERREUR : " + str(e))

        threading.Thread(target=courir, name=f"tache-{tache.id}", daemon=True).start()
        return tache

    def lire(self, id: str) -> Tache | None:
        return self.taches.get(id)


# -------------------------------------------------------------------------------------
# Journal distant
# -------------------------------------------------------------------------------------


def charger_journal_distant(serveur: str, jeton: str, prez: str, depuis: float | None, jusqua: float | None) -> list[dict]:
    """GET <serveur>/api/journal?prez=…&depuis=…&jusqua=…&token=… ; RuntimeError lisible en cas d'échec."""
    serveur = (serveur or "").strip().rstrip("/")
    if not re.match(r"^https?://", serveur):
        raise RuntimeError(f"URL du serveur de séance invalide : {serveur!r}")
    params = {"prez": prez, "token": jeton or ""}
    if depuis is not None:
        params["depuis"] = f"{depuis:.3f}"
    if jusqua is not None:
        params["jusqua"] = f"{jusqua:.3f}"
    url = f"{serveur}/api/journal?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=20) as reponse:
            donnees = json.loads(reponse.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"serveur de séance : HTTP {e.code} ({'jeton refusé' if e.code == 403 else e.reason})")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(f"serveur de séance injoignable ({serveur}) : {getattr(e, 'reason', e)}")
    if not isinstance(donnees, list):
        raise RuntimeError("serveur de séance : réponse inattendue")
    return donnees


def telecharger_questions(serveur: str, jeton: str, destination: Path, consigner: Callable[[str], None] | None = None) -> bool:
    """GET <serveur>/api/questions/export?token=… → `destination` (questions.csv). 404 (serveur sans cet espace) : ignoré en silence ; autre échec : consigné."""
    serveur = (serveur or "").strip().rstrip("/")
    url = f"{serveur}/api/questions/export?{urllib.parse.urlencode({'token': jeton or ''})}"
    try:
        with urllib.request.urlopen(url, timeout=20) as reponse:
            contenu = reponse.read()
    except urllib.error.HTTPError as e:
        if e.code != 404 and consigner:
            consigner(f"questions des participants non récupérées : HTTP {e.code}")
        return False
    except (urllib.error.URLError, OSError) as e:
        if consigner:
            consigner(f"questions des participants non récupérées : {getattr(e, 'reason', e)}")
        return False
    destination.write_bytes(contenu)
    if consigner:
        consigner(f"questions des participants : {destination.name} ({max(0, contenu.count(b'\n') - 1)} question(s))")
    return True


# -------------------------------------------------------------------------------------
# Pipeline d'assemblage
# -------------------------------------------------------------------------------------


def assembler_replay(tache: Tache, replays: Replays, captations: Captations, lire_journal_local: Callable[[str, float | None, float | None], list[dict]],
                     *, prez: str, video: str, audio: str | None = None, seance_debut: float | None = None, seance_fin: float | None = None,
                     decalage: float = 0.0, titre: str | None = None, garder_reference: bool = False, extraits: bool = False,
                     serveur: str | None = None, jeton_serveur: str | None = None, couper_qr: bool = True, scorm: bool = False) -> dict:
    """Corps d'une tâche : assemblage (ou remux), coupe avant les questions-réponses, chapitrage, VTT/TXT, extraits, paquet SCORM. Renvoie {slug, id}.

    `scorm` : construit le paquet SCORM 1.2 (docs/scorm.md) après les extraits ; ceux-ci sont alors découpés même si `extraits` est faux.

    `couper_qr` : si la séance contient un événement `qr_ouvrir` qui tombe dans la vidéo, celle-ci est tronquée à cet instant
    (la séquence questions-réponses n'est pas dans le replay) ; journal.json porte alors `coupe_qr_s`.
    """
    if not outils_presents():
        raise RuntimeError("ffmpeg / ffprobe introuvables : installer ffmpeg et l'ajouter au PATH")
    tache.etape("préparation")
    fichier_video = captations.fichier(video)
    fichier_audio = captations.fichier(audio) if audio else None
    sonde = sonder(fichier_video)
    if not sonde["video"]:
        raise RuntimeError(f"{video} ne contient pas de piste vidéo")
    duree = sonde["duree"]
    mtime = fichier_video.stat().st_mtime
    debut_enregistrement = mtime - duree
    tache.consigner(f"vidéo : {video} ({duree:.1f} s, {sonde['audio']} piste(s) audio) ; début estimé {datetime.fromtimestamp(debut_enregistrement):%d/%m/%Y %H:%M:%S}")
    if fichier_audio:
        tache.consigner(f"audio : {audio}")

    # Journal de la séance : serveur distant ou base locale.
    if serveur:
        tache.consigner(f"journal : {serveur}")
        evenements = charger_journal_distant(serveur, jeton_serveur or "", prez, seance_debut, seance_fin)
    else:
        evenements = lire_journal_local(prez, seance_debut, seance_fin)
    diapos = [e for e in evenements if e.get("type") == "diapositive"]
    tache.consigner(f"{len(evenements)} événement(s) dont {len(diapos)} diapositive(s)")

    titres = titres_presentation(replays.biblio.exiger(prez))
    chapitres = calculer_chapitres(evenements, debut_enregistrement, duree, titres, decalage)
    coupe = instant_qr(evenements, debut_enregistrement, decalage) if couper_qr else None
    if coupe is not None and not 0.5 < coupe < duree:
        tache.consigner(f"questions-réponses ouvertes à {coupe:.0f} s : hors de la vidéo, pas de coupe")
        coupe = None
    if coupe is not None:
        chapitres = [c for c in chapitres if c["debut"] < coupe]
    if not chapitres:
        tache.consigner("aucun chapitre : vérifier la séance choisie et le décalage")
    else:
        tache.consigner(f"{len(chapitres)} chapitre(s)")

    meta_prez = replays.biblio.lire(prez)
    instant_replay = seance_debut if seance_debut else debut_enregistrement
    id = replays.nouvel_identifiant(prez, instant_replay)
    dossier = replays.dossier(prez) / id
    dossier.mkdir(parents=True)
    sortie = dossier / "replay.mp4"
    titre_final = (titre or "").strip() or f"{meta_prez['titre']} – {datetime.fromtimestamp(instant_replay):%d/%m/%Y}"
    mesures = None
    try:
        tache.etape("assemblage")
        if fichier_audio:
            commande = [sys.executable, str(ASSEMBLEUR), str(fichier_video), str(fichier_audio), "--sortie", str(sortie)]
            if garder_reference:
                commande.append("--garder-reference")
            executer(commande, consigner=tache.consigner, cwd=RACINE)
            journal_assembleur = sortie.with_suffix(".json")
            if journal_assembleur.is_file():
                try:
                    mesures = json.loads(journal_assembleur.read_text(encoding="utf-8"))
                except ValueError:
                    mesures = None
                journal_assembleur.unlink()
        else:
            tache.consigner("pas de recalage audio : remux (copie des flux)")
            commande = ["ffmpeg", "-y", "-v", "error", "-i", str(fichier_video), "-map", "0:v:0", "-map", "0:a:0?"]
            if garder_reference:
                commande += ["-map", "0:a:1?"]
            commande += ["-c", "copy", "-movflags", "+faststart", str(sortie)]
            executer(commande, consigner=tache.consigner)
        duree_sortie = duree_fichier(sortie) or duree

        if couper_qr:
            tache.etape("coupe")
            if coupe is not None and coupe < duree_sortie:
                couper_video(sortie, coupe, consigner=tache.consigner)
                duree_sortie = duree_fichier(sortie) or coupe
                tache.consigner(f"coupé avant les questions-réponses à {formater_temps(coupe)} ({duree_sortie:.1f} s)")
            else:
                coupe = None
                tache.consigner("pas de questions-réponses dans la séance : vidéo complète")

        tache.etape("chapitrage")
        chapitres = [c for c in chapitres if c["debut"] <= duree_sortie]
        if chapitres:
            embarquer_chapitres(sortie, chapitres, duree_sortie, titre_final, consigner=tache.consigner)
        ecrire_fichiers_chapitres(dossier, chapitres, duree_sortie)
        donnees = {
            "titre": titre_final,
            "date": datetime.fromtimestamp(instant_replay).replace(microsecond=0).isoformat(),
            "duree": duree_sortie,
            "source_video": video,
            "source_audio": audio,
            "seance": {"debut": seance_debut, "fin": seance_fin, "serveur": serveur or None} if seance_debut or seance_fin or serveur else None,
            "debut_enregistrement": debut_enregistrement,
            "decalage": decalage,
            "garder_reference": garder_reference,
            "coupe_qr_s": coupe,
            "chapitres": chapitres,
            "mesures": mesures,
            "cree": datetime.now().replace(microsecond=0).isoformat(),
        }
        replays.ecrire_journal(prez, id, donnees)

        if serveur:
            telecharger_questions(serveur, jeton_serveur or "", dossier / "questions.csv", consigner=tache.consigner)

        if extraits or scorm:
            tache.etape("extraits")
            noms = decouper_extraits(dossier, chapitres, duree_sortie, consigner=tache.consigner)
            tache.consigner(f"{len(noms)} extrait(s)")
        paquet = None
        if scorm:
            from .scorm import construire_scorm

            tache.etape("scorm")
            paquet = construire_scorm(replays.biblio.exiger(prez), dossier, titre=titre_final, journal=tache.consigner).name
    except Exception:
        shutil.rmtree(dossier, ignore_errors=True)
        raise
    return {"slug": prez, "id": id, "titre": titre_final, "chapitres": len(chapitres), "scorm": paquet}

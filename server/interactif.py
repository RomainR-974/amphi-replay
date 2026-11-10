"""Replay interactif (docs/replay-interactif.md) : vidéo de l'intervenant + présentation redessinée d'après le journal.

Fabrication (post-production, PC 2), à partir de la vidéo caméra (« <date> camera.mkv », Source Record) :
  1. instant de la vidéo dans le journal : ancre `enregistrement_debut` (pont OBS) + décalage du fichier caméra par
     rapport à l'enregistrement principal (lignes « Writing file » du journal d'OBS) ; à défaut, date du fichier ;
  2. chronologie [t, h, v, f] des diapositives et chapitres, depuis le journal du serveur de séance ;
  3. présentation telle qu'elle était : copie figée du serveur (seances/<id>.zip), sinon dossier fourni ;
  4. vidéo pour le web : 30 i/s, H.264 matériel si possible (NVENC, QuickSync), voix traitée (postprod/voix.py) ;
  5. cadrage de l'intervenant (postprod/cadrage.py, facultatif) ;
  6. dossier autonome du lecteur (interactif_lecteur/ + runtime du deck + médias + vidéo) et paquet SCORM 1.2.

Le replay vidéo existant (presentations/<slug>/replays/, replays.py) et son SCORM ne sont pas concernés : les replays
interactifs sont rangés à part, dans presentations/<slug>/replays-interactifs/<id>/ (hors git).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from .journal import titres_presentation
from .quiz import charger_questions
from .replays import calculer_chapitres, charger_journal_distant
from .scorm import _identifiant_xml, manifeste, question_exportable

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_LECTEUR = Path(__file__).resolve().parent / "interactif_lecteur"
DOSSIER_SCORM = Path(__file__).resolve().parent / "scorm_lecteur"
DOSSIER_DECK = RACINE / "deck"
DOSSIER_LOGS_OBS = Path.home() / "AppData" / "Roaming" / "obs-studio" / "logs"
SEUIL_REUSSITE = 50
ECART_ANCRE_MAX_S = 90.0      # l'ancre retenue est le début d'enregistrement le plus proche de la date du fichier
ECART_COPIE_MAX_S = 5.0       # la copie figée retenue est celle de cette ancre
LIGNE_OBS = re.compile(r"^(\d\d):(\d\d):(\d\d)\.(\d{3}): .*Writing file '(.+)'\.\.\.\s*$")

Journal = Callable[[str], None]


def _rien(_: str) -> None:
    pass


# -------------------------------------------------------------------------------------
# Vidéo : durée, date, décalage du fichier caméra (journal d'OBS)
# -------------------------------------------------------------------------------------


def sonder(video: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", str(video)],
                       capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    flux = d.get("streams", [])
    v = next((s for s in flux if s.get("codec_type") == "video"), {})
    return {"duree": float(d["format"]["duration"]), "largeur": v.get("width"), "hauteur": v.get("height"),
            "son": any(s.get("codec_type") == "audio" for s in flux)}


def _secondes(m: re.Match) -> float:
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000


def decalage_obs(video: Path, dossier_logs: Path | None = None) -> float | None:
    """Retard (s) du début du fichier `video` sur l'enregistrement principal démarré juste avant, d'après les lignes
    « Writing file '…' » du journal d'OBS (à la milliseconde). None si le fichier n'y figure pas."""
    dossier = Path(dossier_logs) if dossier_logs else DOSSIER_LOGS_OBS
    if not dossier.is_dir():
        return None
    nom = Path(video).name
    for log in sorted(dossier.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        principal_t: float | None = None
        for ligne in log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = LIGNE_OBS.match(ligne)
            if not m:
                continue
            chemin = m.group(5).replace("\\", "/")
            if chemin.rsplit("/", 1)[-1] == nom:
                if principal_t is None:
                    return 0.0  # c'est l'enregistrement principal lui-même
                ecart = (_secondes(m) - principal_t) % 86400  # passage de minuit
                return round(ecart, 3) if ecart < 30 else None
            if "simple_file_output" in ligne or "adv_file_output" in ligne:
                principal_t = _secondes(m)
    return None


# -------------------------------------------------------------------------------------
# Journal : ancre et chronologie
# -------------------------------------------------------------------------------------


def choisir_ancre(evenements: list[dict], approx: float, ecart_max: float = ECART_ANCRE_MAX_S) -> float | None:
    """Horodatage de l'`enregistrement_debut` le plus proche de `approx` (à moins de `ecart_max` s), ou None."""
    candidats = [float(e["horodatage"]) for e in evenements if e.get("type") == "enregistrement_debut"]
    if not candidats:
        return None
    meilleur = min(candidats, key=lambda t: abs(t - approx))
    return meilleur if abs(meilleur - approx) <= ecart_max else None


def chronologie(evenements: list[dict], debut: float, duree: float) -> list[list]:
    """[[t, h, v, f], …] : la diapositive affichée au début de la vidéo (t = 0), puis chaque changement pendant la vidéo."""
    diapos = sorted((e for e in evenements if e.get("type") == "diapositive" and e.get("h") is not None), key=lambda e: e["horodatage"])
    etat = [0, 0, -1]
    for e in diapos:
        if e["horodatage"] <= debut:
            etat = [int(e["h"]), int(e.get("v") or 0), -1 if e.get("f") is None else int(e["f"])]
    resultat = [[0.0, *etat]]
    for e in diapos:
        t = float(e["horodatage"]) - debut
        if 0 < t <= duree:
            suivant = [int(e["h"]), int(e.get("v") or 0), -1 if e.get("f") is None else int(e["f"])]
            if suivant != resultat[-1][1:]:
                resultat.append([round(t, 3), *suivant])
    return resultat


def _requete_json(url: str, timeout: float = 20) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def telecharger_copie_figee(serveur: str, jeton: str, slug: str, ancre: float, dossier: Path, consigner: Journal = _rien) -> Path | None:
    """Copie figée de la séance (presentations/<slug>/seances/<id>/ sur le serveur) prise à `ancre`, extraite dans `dossier`."""
    base = serveur.rstrip("/")
    q = urllib.parse.urlencode({"token": jeton})
    try:
        copies = _requete_json(f"{base}/api/presentations/{urllib.parse.quote(slug)}/seances?{q}")
    except Exception as e:  # noqa: BLE001
        consigner(f"copies figées indisponibles : {e}")
        return None
    proches = [c for c in copies if c.get("debut_enregistrement") and abs(float(c["debut_enregistrement"]) - ancre) <= ECART_COPIE_MAX_S]
    if not proches:
        return None
    choisie = min(proches, key=lambda c: abs(float(c["debut_enregistrement"]) - ancre))
    dossier.mkdir(parents=True, exist_ok=True)
    archive = dossier / "copie.zip"
    urllib.request.urlretrieve(f"{base}/api/presentations/{urllib.parse.quote(slug)}/seances/{choisie['id']}.zip?{q}", archive)
    with zipfile.ZipFile(archive) as z:
        for nom in z.namelist():
            if nom.startswith("/") or ".." in Path(nom).parts:
                raise RuntimeError(f"chemin suspect dans la copie figée : {nom}")
        z.extractall(dossier / "copie")
    archive.unlink()
    consigner(f"présentation : copie figée {choisie['id']} du serveur")
    return dossier / "copie"


# -------------------------------------------------------------------------------------
# Vidéo pour le web
# -------------------------------------------------------------------------------------

ENCODEURS = {
    "h264_nvenc": ["-preset", "p5", "-rc", "vbr", "-cq", "27", "-b:v", "1500k", "-maxrate", "2500k", "-bufsize", "5000k"],
    "h264_qsv": ["-preset", "medium", "-b:v", "1500k", "-maxrate", "2500k", "-bufsize", "5000k"],
    "libx264": ["-preset", "veryfast", "-crf", "25", "-maxrate", "2500k", "-bufsize", "5000k"],
}
# Qualité « légère » (replay de la post-production, parcours Moodle) : qualité constante, débit selon l'image ; sur des
# diapositives, ≈ 4 fois plus petit que la captation OBS (chapitre caméra de 3 min 48 : 101 → 27 Mo en QuickSync).
ENCODEURS_LEGERS = {
    "h264_nvenc": ["-preset", "p5", "-rc", "vbr", "-cq", "32", "-b:v", "0", "-maxrate", "2500k", "-bufsize", "5000k"],
    "h264_qsv": ["-preset", "medium", "-global_quality", "31", "-look_ahead", "0"],
    "libx264": ["-preset", "veryfast", "-crf", "30", "-tune", "stillimage", "-maxrate", "2500k", "-bufsize", "5000k"],
}


def encodeur_disponible(nom: str) -> bool:
    fmt = "nv12" if nom == "h264_qsv" else "yuv420p"
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=320x240:d=0.3", "-vf", f"format={fmt}",
                        "-c:v", nom, "-f", "null", "-"], capture_output=True, text=True)
    return r.returncode == 0


def choisir_encodeur(prefere: str | None = None) -> str:
    for nom in ([prefere] if prefere else []) + ["h264_nvenc", "h264_qsv", "libx264"]:
        if nom in ENCODEURS and encodeur_disponible(nom):
            return nom
    return "libx264"


def _module_postprod(nom: str):
    dossier = str(RACINE / "postprod")
    if dossier not in sys.path:
        sys.path.insert(0, dossier)
    return __import__(nom)


def encoder_video(source: Path, sortie: Path, *, voix: str | None = "podcast", encodeur: str | None = None, qualite: str = "standard",
                  image: bool = True, consigner: Journal = _rien) -> str:
    """source → MP4 web : 30 i/s, ≤ 1080 lignes, H.264 (matériel si possible), voix traitée (AAC 128 kb/s). Renvoie l'encodeur.

    `qualite` : "standard" (débit visé 1,5 Mb/s) ou "leger" (qualité constante, ENCODEURS_LEGERS).
    `image=False` : image copiée telle quelle (seul le son est traité) ; renvoie alors "copie".
    """
    info = sonder(source)
    if not image:
        return _traiter_son_seul(source, sortie, info, voix, consigner)
    nom = choisir_encodeur(encodeur)
    reglages = (ENCODEURS_LEGERS if qualite == "leger" else ENCODEURS)[nom]
    fmt = "nv12" if nom == "h264_qsv" else "yuv420p"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    commande = ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-stats", "-i", str(source)]
    filtre_audio = None
    with tempfile.TemporaryDirectory(prefix="interactif-") as t:
        if info["son"] and voix and voix != "aucune":
            son, filtre = _preparer_voix(source, Path(t), voix, consigner)
            commande += ["-i", str(son)]
            filtre_audio = f"[1:a]{filtre},aresample=48000[voix]"
        commande += ["-map", "0:v:0", "-vf", f"fps=30,scale=-2:'min(1080,ih)',format={fmt}", "-c:v", nom, *reglages,
                     "-g", "60", "-profile:v", "high"]
        if filtre_audio:
            aac = _module_postprod("voix").encodeur_aac()
            commande += ["-filter_complex", filtre_audio, "-map", "[voix]", "-c:a", aac, "-b:a", "128k"]
        elif info["son"]:
            commande += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "128k"]
        commande += ["-movflags", "+faststart", str(sortie)]
        consigner(f"vidéo : encodage {nom} → {sortie.name}")
        r = subprocess.run(commande, capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            if nom != "libx264":  # encodeur matériel indisponible au dernier moment : repli logiciel
                consigner(f"{nom} a échoué, repli libx264 : {r.stderr.strip()[-300:]}")
                return encoder_video(source, sortie, voix=voix, encodeur="libx264", qualite=qualite, consigner=consigner)
            raise RuntimeError(f"ffmpeg a échoué :\n{r.stderr[-1500:]}")
    return nom


def _preparer_voix(source: Path, temporaire: Path, voix: str, consigner: Journal) -> tuple[Path, str]:
    """Son extrait (débruité si DeepFilterNet est là) et chaîne de filtres postprod/voix.py réglée sur -16 LUFS."""
    v = _module_postprod("voix")
    son = v.extraire_wav(source, temporaire / "son.wav")
    if v.debruitage_disponible():
        consigner("voix : débruitage (DeepFilterNet)…")
        son = v.debruiter(son, temporaire)
    chaine = v.PRESETS[voix]
    mesures = v.mesurer(son, chaine)
    filtre = v.filtre_final(mesures, chaine)
    ecart = v.CIBLE_I - v.mesurer(son, filtre)["I"]
    if abs(ecart) > v.TOLERANCE_LU:
        filtre = v.filtre_final(mesures, chaine, ecart)
    consigner(f"voix : préréglage {voix}, {mesures['I']:.1f} → {v.CIBLE_I:.0f} LUFS")
    return son, filtre


def _traiter_son_seul(source: Path, sortie: Path, info: dict, voix: str | None, consigner: Journal) -> str:
    """Image copiée, son traité (ou copié s'il n'y a rien à faire)."""
    sortie.parent.mkdir(parents=True, exist_ok=True)
    commande = ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-i", str(source)]
    with tempfile.TemporaryDirectory(prefix="voix-") as t:
        if info["son"] and voix and voix != "aucune":
            son, filtre = _preparer_voix(source, Path(t), voix, consigner)
            aac = _module_postprod("voix").encodeur_aac()
            commande += ["-i", str(son), "-filter_complex", f"[1:a]{filtre},aresample=48000[voix]", "-map", "0:v:0", "-map", "[voix]",
                         "-c:v", "copy", "-c:a", aac, "-b:a", "128k"]
        else:
            commande += ["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy"]
        commande += ["-movflags", "+faststart", str(sortie)]
        r = subprocess.run(commande, capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg a échoué :\n{r.stderr[-1500:]}")
    return "copie"


# -------------------------------------------------------------------------------------
# Lecteur et paquet
# -------------------------------------------------------------------------------------


def _copier(source: Path, cible: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, cible, dirs_exist_ok=True)
    elif source.is_file():
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, cible)


def copier_runtime(deck: Path, sortie: Path, theme: str | None, slides: str) -> list[str]:
    """Runtime du deck à l'identique (mêmes chemins relatifs que /deck/) ; renvoie les balises <script> facultatives."""
    vendor = deck / "vendor"
    if not (vendor / "reveal" / "reveal.js").is_file():
        raise RuntimeError("deck/vendor/reveal absent : lancer `bash tools/fetch_reveal.sh`")
    for f in ("reveal.js", "reveal.css", "theme/white.css", "theme/fonts/source-sans-pro"):
        _copier(vendor / "reveal" / f, sortie / "vendor" / "reveal" / f)
    for f in ("theme.css", "camera.js", "quiz-deck.js", "teachable.js"):
        _copier(deck / f, sortie / f)
    scripts = []
    if theme:
        _copier(deck / "themes" / f"{theme}.css", sortie / "themes" / f"{theme}.css")
        if theme == "dsfr":
            _copier(vendor / "dsfr" / "fonts", sortie / "vendor" / "dsfr" / "fonts")
    if 'data-amphi="lottie"' in slides:
        _copier(vendor / "lottie" / "lottie.min.js", sortie / "vendor" / "lottie" / "lottie.min.js")
        scripts.append('<script src="vendor/lottie/lottie.min.js"></script>')
    if 'data-amphi="teachable"' in slides:
        _copier(vendor / "tm", sortie / "vendor" / "tm")
    return scripts


def construire_replay_interactif(video: Path, slug: str, sortie: Path, *, serveur: str | None = None, jeton: str | None = None,
                                 deck: Path | None = None, dossier_presentation: Path | None = None, debut: float | None = None,
                                 decalage: float | None = None, logs_obs: Path | None = None, voix: str | None = "podcast",
                                 cadrage: bool = True, titre: str | None = None, encodeur: str | None = None,
                                 lire_journal: Callable[[str, float, float], list[dict]] | None = None,
                                 consigner: Journal = _rien) -> dict:
    """Fabrique le dossier du replay interactif (lecteur autonome) ; renvoie ses données (dont `identifiant`)."""
    video = Path(video)
    info = sonder(video)
    duree = info["duree"]
    consigner(f"vidéo : {video.name}, {duree:.1f} s, {info['largeur']}×{info['hauteur']}")

    # 1. Instant de la vidéo dans le journal
    if decalage is None:
        decalage = decalage_obs(video, logs_obs)
        consigner(f"décalage sur l'enregistrement principal (journal d'OBS) : {decalage:.3f} s" if decalage is not None
                  else "décalage sur l'enregistrement principal : introuvable dans le journal d'OBS (0 retenu)")
    debut_fichier = video.stat().st_mtime - duree
    approx_principal = debut_fichier - (decalage or 0.0)
    depuis, jusqua = approx_principal - 6 * 3600, approx_principal + duree + 600
    if lire_journal:
        evenements = lire_journal(slug, depuis, jusqua)
    elif serveur:
        evenements = charger_journal_distant(serveur, jeton or "", slug, depuis, jusqua)
    else:
        raise RuntimeError("journal de séance : indiquer le serveur de séance (--seance)")
    ancre = choisir_ancre(evenements, approx_principal)
    if debut is not None:
        debut_video, source_debut = float(debut), "imposé"
    elif ancre is not None:
        debut_video, source_debut = ancre + (decalage or 0.0), "ancre du pont OBS"
    else:
        debut_video, source_debut = debut_fichier, "date du fichier (précision ≈ 1 s)"
    consigner(f"début de la vidéo : {datetime.fromtimestamp(debut_video).strftime('%d/%m/%Y %H:%M:%S.%f')[:-3]} ({source_debut})")

    # 2. Présentation de la séance
    sortie = Path(sortie)
    if sortie.exists():
        shutil.rmtree(sortie)
    sortie.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="interactif-deck-") as t:
        dossier_deck = Path(deck) if deck else None
        if dossier_deck is None and serveur and ancre is not None:
            dossier_deck = telecharger_copie_figee(serveur, jeton or "", slug, ancre, Path(t), consigner)
        if dossier_deck is None:
            if not dossier_presentation:
                raise RuntimeError("présentation de la séance introuvable : pas de copie figée, indiquer --deck ou le dossier de la présentation")
            dossier_deck = Path(dossier_presentation)
            consigner(f"⚠ pas de copie figée de la séance : version actuelle de {dossier_deck} (vérifier que les diapositives n'ont pas changé)")
        slides = (dossier_deck / "slides.html").read_text(encoding="utf-8")
        meta = json.loads((dossier_deck / "presentation.json").read_text(encoding="utf-8")) if (dossier_deck / "presentation.json").is_file() else {}
        titres = titres_presentation(dossier_deck)
        questions = charger_questions(dossier_deck / "quiz.json") if (dossier_deck / "quiz.json").is_file() else {}
        _copier(dossier_deck / "medias", sortie / "medias")

    # 3. Chronologie, chapitres, questions
    timeline = chronologie(evenements, debut_video, duree)
    chapitres = calculer_chapitres(evenements, debut_video, duree, titres)
    evaluation = {i: e for i, e in ((i, question_exportable(q)) for i, q in questions.items()) if e}
    consigner(f"journal : {len(timeline)} états de diapositive, {len(chapitres)} chapitres, {len(evaluation)} questions auto-évaluables")

    # 4. Vidéo web
    nom_encodeur = encoder_video(video, sortie / "video" / "camera.mp4", voix=voix, encodeur=encodeur, consigner=consigner)

    # 5. Cadrage
    donnees_cadrage = None
    if cadrage:
        try:
            donnees_cadrage = _module_postprod("cadrage").cadrage(video, consigner)
        except ImportError:
            consigner("cadrage sauté : opencv-python-headless absent (pip install opencv-python-headless)")

    # 6. Lecteur
    scripts = copier_runtime(DOSSIER_DECK, sortie, meta.get("theme") or None, slides)
    for f in ("lecteur.js", "lecteur.css"):
        _copier(DOSSIER_LECTEUR / f, sortie / f)
    _copier(DOSSIER_SCORM / "scorm.js", sortie / "scorm.js")
    titre = titre or f"{meta.get('titre') or slug} — replay du {datetime.fromtimestamp(debut_video).strftime('%d/%m/%Y')}"
    identifiant = _identifiant_xml(f"amphi-interactif-{slug}-{datetime.fromtimestamp(debut_video).strftime('%Y%m%d-%H%M')}")
    donnees = {
        "version": 1, "identifiant": identifiant, "titre": titre, "presentation": slug, "theme": meta.get("theme") or "",
        "camera": meta["camera"] if "camera" in meta else {}, "seuil": SEUIL_REUSSITE,
        "genere": datetime.now().isoformat(timespec="seconds"), "debut": debut_video, "source_debut": source_debut,
        "duree": round(duree, 3), "encodeur": nom_encodeur, "slides": slides, "titres": titres,
        "questions": [q.public() for q in questions.values()], "evaluation": evaluation,
        "timeline": timeline, "chapitres": chapitres, "cadrage": donnees_cadrage,
    }
    (sortie / "donnees.js").write_text("window.REPLAY_INTERACTIF = " + json.dumps(donnees, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + ";\n",
                                       encoding="utf-8")
    theme_css = f'<link rel="stylesheet" href="themes/{donnees["theme"]}.css">' if donnees["theme"] and (sortie / "themes").is_dir() else ""
    page = (DOSSIER_LECTEUR / "index.html").read_text(encoding="utf-8")
    page = (page.replace("{{TITRE}}", _echapper(titre)).replace("{{THEME}}", theme_css)
            .replace("{{VIDEO}}", "video/camera.mp4").replace("{{SCRIPTS}}", "\n".join(scripts)))
    (sortie / "index.html").write_text(page, encoding="utf-8")
    (sortie / "interactif.json").write_text(json.dumps({k: v for k, v in donnees.items() if k not in ("slides", "questions")}, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    consigner(f"✔ replay interactif : {sortie}")
    return donnees


def _echapper(texte: str) -> str:
    return texte.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def paquet_scorm(dossier: Path, archive: Path, consigner: Journal = _rien) -> Path:
    """Zip SCORM 1.2 du dossier du lecteur (un seul SCO, index.html), avec imsmanifest.xml."""
    dossier, archive = Path(dossier), Path(archive)
    donnees = json.loads((dossier / "interactif.json").read_text(encoding="utf-8"))
    fichiers = sorted(f.relative_to(dossier).as_posix() for f in dossier.rglob("*") if f.is_file() and f.name != "interactif.json")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("imsmanifest.xml", manifeste(donnees["identifiant"], donnees["titre"], fichiers))
        for nom in fichiers:
            # Vidéo et polices déjà compressées : stockées telles quelles (plus rapide, même taille).
            mode = zipfile.ZIP_STORED if nom.endswith((".mp4", ".woff2", ".png", ".jpg", ".jpeg", ".webp", ".bin")) else zipfile.ZIP_DEFLATED
            z.write(dossier / nom, nom, compress_type=mode)
    consigner(f"✔ paquet SCORM : {archive} ({archive.stat().st_size / 1e6:.1f} Mo)")
    return archive

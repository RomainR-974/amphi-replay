"""Export SCORM 1.2 d'un replay chapitré (voir docs/scorm.md).

    <replay>/scorm/<slug>-<id>-scorm.zip      le paquet
    <replay>/scorm/scorm.json                 {fichier, taille, genere, chapitres, questions, inclure_quiz, video_unique}

Le paquet contient un seul SCO : `imsmanifest.xml`, `index.html` (lecteur autonome, JS vanilla), `scorm.js`
(API SCORM 1.2 avec repli localStorage), `module.json`, une vidéo par chapitre dans `chapitres/` (les extraits du
replay, découpés s'ils manquent) ou `video/replay.mp4` en mode « vidéo unique », et `medias/affiche.jpg`.

Aucune dépendance au serveur : ValueError (entrée invalide), RuntimeError (ffmpeg en échec) ; app.py traduit en HTTP.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape as _xml_escape

from .journal import questions_diapositives
from .quiz import Question, charger_questions
from .replays import DUREE_MIN_EXTRAIT_S, decouper_extraits, executer, slugifier, sonder

DOSSIER_LECTEUR = Path(__file__).resolve().parent / "scorm_lecteur"
TYPES_NOTES = ("qcm", "multi", "vrai_faux", "trou", "nombre")
SEUIL_REUSSITE = 50
NOM_PAQUET = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,120}\.zip$")


def _xml(texte: str) -> str:
    return _xml_escape(str(texte), {'"': "&quot;"})


def _identifiant_xml(texte: str) -> str:
    """Identifiant XML valide (lettres, chiffres, - _ .), commençant par une lettre."""
    ident = re.sub(r"[^A-Za-z0-9_.-]+", "-", texte).strip("-.")
    if not ident or not re.match(r"[A-Za-z_]", ident):
        ident = "amphi-" + ident
    return ident


# -------------------------------------------------------------------------------------
# Questions
# -------------------------------------------------------------------------------------


def question_exportable(q: Question) -> dict | None:
    """Vue d'une question pour le lecteur (type noté et bonne réponse connue), ou None si elle n'est pas auto-évaluable."""
    if q.type not in TYPES_NOTES:
        return None
    if q.type in ("multi", "trou"):
        if not q.bonnes:
            return None
    elif q.bonne is None:
        return None
    d: dict = {"id": q.id, "type": q.type, "question": q.question}
    if q.type in ("qcm", "multi"):
        d["options"] = list(q.options)
    if q.type == "multi":
        d["bonnes"] = sorted(int(i) for i in q.bonnes)
    elif q.type == "trou":
        d["texte"] = q.texte
        d["bonnes"] = [str(b) for b in q.bonnes]
    else:
        d["bonne"] = q.bonne
    if q.type == "nombre":
        d["unite"] = q.unite
    return d


def questions_par_chapitre(chapitres: list[dict], dossier_presentation: Path, consigner: Callable[[str], None] | None = None) -> list[list[dict]]:
    """Pour chaque chapitre, les questions auto-évaluables des diapositives `[h, h du chapitre suivant[` (dernier : jusqu'à la fin).

    Une question n'est rattachée qu'une fois (au premier chapitre qui couvre sa diapositive) ; un chapitre sans `h` n'en a pas.
    """
    slides = dossier_presentation / "slides.html"
    if not slides.is_file():
        return [[] for _ in chapitres]
    par_diapo = questions_diapositives(slides.read_text(encoding="utf-8"))
    try:
        questions = charger_questions(dossier_presentation / "quiz.json")
    except (OSError, ValueError) as e:
        if consigner:
            consigner(f"quiz.json illisible, pas de questions : {e}")
        return [[] for _ in chapitres]
    resultat: list[list[dict]] = []
    deja: set[str] = set()
    for i, c in enumerate(chapitres):
        h = c.get("h")
        liste: list[dict] = []
        if isinstance(h, int) and 0 <= h < len(par_diapo):
            suivant = next((x.get("h") for x in chapitres[i + 1 :] if isinstance(x.get("h"), int)), None)
            if suivant is None:
                fin = len(par_diapo)  # dernier chapitre : jusqu'à la fin
            else:
                fin = suivant if suivant > h else h + 1  # retour en arrière : seulement sa diapositive
            for identifiant in par_diapo[h : min(fin, len(par_diapo))]:
                if not identifiant or identifiant in deja or identifiant not in questions:
                    continue
                q = question_exportable(questions[identifiant])
                if q is not None:
                    deja.add(identifiant)
                    liste.append(q)
        resultat.append(liste)
    return resultat


# -------------------------------------------------------------------------------------
# Manifeste et lecteur
# -------------------------------------------------------------------------------------


def manifeste(identifiant: str, titre: str, fichiers: list[str]) -> str:
    lignes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<manifest identifier="{_xml(identifiant)}" version="1.0"',
        '          xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2"',
        '          xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2"',
        '          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        '          xsi:schemaLocation="http://www.imsproject.org/xsd/imscp_rootv1p1p2 imscp_rootv1p1p2.xsd',
        '                              http://www.imsglobal.org/xsd/imsmd_rootv1p2p1 imsmd_rootv1p2p1.xsd',
        '                              http://www.adlnet.org/xsd/adlcp_rootv1p2 adlcp_rootv1p2.xsd">',
        "  <metadata>",
        "    <schema>ADL SCORM</schema>",
        "    <schemaversion>1.2</schemaversion>",
        "  </metadata>",
        f'  <organizations default="{_xml(identifiant)}-org">',
        f'    <organization identifier="{_xml(identifiant)}-org">',
        f"      <title>{_xml(titre)}</title>",
        f'      <item identifier="{_xml(identifiant)}-item" identifierref="{_xml(identifiant)}-res" isvisible="true">',
        f"        <title>{_xml(titre)}</title>",
        f"        <adlcp:masteryscore>{SEUIL_REUSSITE}</adlcp:masteryscore>",
        "      </item>",
        "    </organization>",
        "  </organizations>",
        "  <resources>",
        f'    <resource identifier="{_xml(identifiant)}-res" type="webcontent" adlcp:scormtype="sco" href="index.html">',
    ]
    lignes += [f'      <file href="{_xml(f)}"/>' for f in fichiers]
    lignes += ["    </resource>", "  </resources>", "</manifest>", ""]
    return "\n".join(lignes)


def page_lecteur(module: dict) -> str:
    """index.html : gabarit + JSON du module embarqué (fetch échoue en file:// dans Chrome)."""
    gabarit = (DOSSIER_LECTEUR / "index.html").read_text(encoding="utf-8")
    json_module = json.dumps(module, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return gabarit.replace("{{TITRE}}", html.escape(module["titre"])).replace("{{MODULE_JSON}}", json_module)


# -------------------------------------------------------------------------------------
# Construction du paquet
# -------------------------------------------------------------------------------------


def _lire_journal_replay(dossier_replay: Path) -> dict:
    fichier = dossier_replay / "journal.json"
    if fichier.is_file():
        try:
            donnees = json.loads(fichier.read_text(encoding="utf-8"))
            if isinstance(donnees, dict):
                return donnees
        except (OSError, ValueError):
            pass
    sonde = sonder(dossier_replay / "replay.mp4")
    return {"titre": dossier_replay.name, "duree": sonde["duree"], "chapitres": sonde["chapitres"]}


def _affiche(video: Path, destination: Path, duree: float, consigner: Callable[[str], None] | None) -> bool:
    instant = min(2.0, max(0.0, duree / 2))
    try:
        executer(["ffmpeg", "-y", "-v", "error", "-ss", f"{instant:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", "-vf", "scale='min(1280,iw)':-2", str(destination)], consigner=consigner)
    except RuntimeError as e:
        if consigner:
            consigner(f"affiche non générée : {e}")
        return False
    return destination.is_file()


def construire_scorm(dossier_presentation: Path, dossier_replay: Path, *, titre: str | None = None, inclure_quiz: bool = True,
                     video_unique: bool = False, journal: Callable[[str], None] | None = None, etape: Callable[[str], None] | None = None) -> Path:
    """Construit `<replay>/scorm/<slug>-<id>-scorm.zip` et renvoie son chemin. `journal` reçoit les lignes de progression,
    `etape` (optionnel) les noms d'étape « extraits » / « scorm » d'une tâche d'arrière-plan."""
    dossier_presentation, dossier_replay = Path(dossier_presentation), Path(dossier_replay)
    consigner = journal or (lambda ligne: None)
    video = dossier_replay / "replay.mp4"
    if not video.is_file():
        raise ValueError(f"replay.mp4 absent dans {dossier_replay}")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg / ffprobe introuvables : installer ffmpeg et l'ajouter au PATH")
    donnees = _lire_journal_replay(dossier_replay)
    duree = float(donnees.get("duree") or 0.0) or sonder(video)["duree"]
    titre_final = re.sub(r"\s+", " ", titre or "").strip() or str(donnees.get("titre") or dossier_replay.name)
    slug, id_replay = dossier_presentation.name, dossier_replay.name

    chapitres = [dict(c) for c in (donnees.get("chapitres") or []) if isinstance(c, dict) and "debut" in c]
    chapitres.sort(key=lambda c: float(c["debut"]))
    if not chapitres:
        consigner("replay sans chapitres : un seul chapitre couvrant toute la vidéo")
        chapitres = [{"debut": 0.0, "titre": titre_final, "h": None}]
    for i, c in enumerate(chapitres):
        c["debut"] = float(c["debut"])
        c["fin"] = float(chapitres[i + 1]["debut"]) if i + 1 < len(chapitres) else max(duree, c["debut"])
        c["titre"] = str(c.get("titre") or f"Chapitre {i + 1}")
        c["nom"] = f"{i + 1:02d}-{slugifier(c['titre'])}.mp4"

    # Vidéos : extraits par chapitre (découpés s'ils manquent) ou vidéo unique.
    if video_unique:
        if etape:
            etape("scorm")
        consigner("vidéo unique : replay.mp4 copié, chapitres par instants")
    else:
        if etape:
            etape("extraits")
        extraits = dossier_replay / "extraits"
        if len(chapitres) == 1 and chapitres[0]["debut"] <= 0.0:
            consigner("chapitre unique : la vidéo complète sert de chapitre")
        else:
            manquants = [i for i, c in enumerate(chapitres) if not (extraits / c["nom"]).is_file() and c["fin"] - c["debut"] >= DUREE_MIN_EXTRAIT_S]
            if manquants:
                consigner(f"{len(manquants)} extrait(s) à découper")
                decouper_extraits(dossier_replay, [{"debut": c["debut"], "titre": c["titre"]} for c in chapitres], duree, manquants, consigner)
            else:
                consigner("extraits déjà présents")
        if etape:
            etape("scorm")

    # Questions.
    questions = questions_par_chapitre(chapitres, dossier_presentation, consigner) if inclure_quiz else [[] for _ in chapitres]

    sortie_dossier = dossier_replay / "scorm"
    sortie_dossier.mkdir(exist_ok=True)
    identifiant = _identifiant_xml(f"amphi-{slug}-{id_replay}")
    nom_zip = f"{slugifier(slug, 40)}-{re.sub(r'[^a-z0-9_.-]+', '-', id_replay.lower()).strip('-.')[:60] or 'replay'}-scorm.zip"
    genere = datetime.now().replace(microsecond=0).isoformat()

    with tempfile.TemporaryDirectory(prefix="amphi-scorm-", dir=str(sortie_dossier)) as tmp:
        racine = Path(tmp) / "paquet"
        racine.mkdir()
        module_chapitres: list[dict] = []
        if video_unique:
            (racine / "video").mkdir()
            shutil.copyfile(video, racine / "video" / "replay.mp4")
        else:
            (racine / "chapitres").mkdir()
        for i, c in enumerate(chapitres):
            entree = {"numero": i + 1, "titre": c["titre"], "duree": round(c["fin"] - c["debut"], 3), "debut": round(c["debut"], 3), "fin": round(c["fin"], 3), "questions": questions[i]}
            if video_unique:
                entree["video"] = "video/replay.mp4"
            else:
                source = video if len(chapitres) == 1 and c["debut"] <= 0.0 else dossier_replay / "extraits" / c["nom"]
                if not source.is_file():
                    consigner(f"chapitre {i + 1} « {c['titre']} » sans vidéo (trop court) : ignoré")
                    continue
                shutil.copyfile(source, racine / "chapitres" / c["nom"])
                entree["video"] = "chapitres/" + c["nom"]
            module_chapitres.append(entree)
        for n, c in enumerate(module_chapitres):
            c["numero"] = n + 1
        if not module_chapitres:
            raise RuntimeError("aucun chapitre exploitable")

        (racine / "medias").mkdir()
        affiche = _affiche(video, racine / "medias" / "affiche.jpg", duree, consigner)
        nb_questions = sum(len(c["questions"]) for c in module_chapitres)
        module = {
            "identifiant": identifiant,
            "titre": titre_final,
            "chapitres": module_chapitres,
            "quiz": inclure_quiz and nb_questions > 0,
            "video_unique": video_unique,
            "affiche": "medias/affiche.jpg" if affiche else None,
            "seuil": SEUIL_REUSSITE,
            "genere": genere,
            "source": {"presentation": slug, "replay": id_replay, "duree": round(duree, 3)},
        }
        (racine / "module.json").write_text(json.dumps(module, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (racine / "index.html").write_text(page_lecteur(module), encoding="utf-8")
        shutil.copyfile(DOSSIER_LECTEUR / "scorm.js", racine / "scorm.js")
        fichiers = sorted(p.relative_to(racine).as_posix() for p in racine.rglob("*") if p.is_file())
        (racine / "imsmanifest.xml").write_text(manifeste(identifiant, titre_final, ["imsmanifest.xml"] + fichiers), encoding="utf-8")

        zip_tmp = Path(tmp) / nom_zip
        with zipfile.ZipFile(zip_tmp, "w") as z:
            z.write(racine / "imsmanifest.xml", "imsmanifest.xml", compress_type=zipfile.ZIP_DEFLATED)
            for f in fichiers:
                z.write(racine / f, f, compress_type=zipfile.ZIP_STORED if f.endswith((".mp4", ".jpg")) else zipfile.ZIP_DEFLATED)
        for ancien in sortie_dossier.glob("*.zip"):
            ancien.unlink()
        destination = sortie_dossier / nom_zip
        shutil.move(str(zip_tmp), destination)

    (sortie_dossier / "scorm.json").write_text(json.dumps({
        "fichier": nom_zip, "taille": destination.stat().st_size, "genere": genere, "titre": titre_final,
        "chapitres": len(module_chapitres), "questions": nb_questions, "inclure_quiz": inclure_quiz, "video_unique": video_unique,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    consigner(f"paquet SCORM : {nom_zip} ({destination.stat().st_size / 1e6:.1f} Mo, {len(module_chapitres)} chapitre(s), {nb_questions} question(s))")
    return destination


def lire_scorm(dossier_replay: Path) -> dict | None:
    """Métadonnées du paquet existant (scorm/scorm.json) si le zip est bien là, sinon None."""
    fichier = Path(dossier_replay) / "scorm" / "scorm.json"
    if not fichier.is_file():
        return None
    try:
        donnees = json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    nom = str(donnees.get("fichier") or "")
    zip_path = fichier.parent / nom
    if not NOM_PAQUET.match(nom) or not zip_path.is_file():
        return None
    donnees["taille"] = zip_path.stat().st_size
    return donnees


def supprimer_scorm(dossier_replay: Path) -> bool:
    d = Path(dossier_replay) / "scorm"
    if not d.is_dir():
        return False
    shutil.rmtree(d)
    return True

"""Parcours de formation à partir d'un replay (docs/postprod.md §6) : une séquence par partie de la présentation.

    <replay>/parcours/
      cours.html                page de cours autonome (aperçu du parcours, s'ouvre aussi depuis le disque)
      parcours.json             description de tout ce qui a été produit
      questions.gift            toutes les questions, une catégorie par séquence (import dans la banque de questions Moodle)
      sous-titres.vtt · transcription.txt   pour le replay complet
      NN-titre/                 une séquence = une section du cours Moodle
        video.mp4               vidéo de la séquence
        sous-titres.vtt         sous-titres (transcription automatique, postprod/transcrire.py)
        transcription.txt       texte de la vidéo
        essentiel.md            brouillon de l'apport (texte des diapositives et notes du présentateur), à relire
        images/NN-titre.jpg     une image par diapositive
        questions.gift          questions auto-évaluables de la séquence

Séquences : la présentation est coupée à chaque diapositive de partie (section `dsfr-chapitre`, ou attribut
`data-partie`) ; sans partie, les chapitres sont regroupés par blocs d'au moins 8 minutes.

Aucune dépendance au serveur : ValueError / RuntimeError ; app.py traduit en HTTP.
"""

from __future__ import annotations

import html as module_html
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from .journal import ASIDE_NOTES, _sections_premier_niveau, titres_presentation
from .quiz import charger_questions
from .replays import DUREE_MIN_EXTRAIT_S, RACINE, executer, slugifier
from .scorm import question_exportable

ELEMENTS = ("videos", "sous_titres", "transcription", "images", "textes", "questions", "page", "synopsis")
DECRIRE = {"page", "synopsis"}  # éléments qui décrivent toute la séquence (diapositives et questions calculées même sans « textes » / « questions »)
DUREE_SEQUENCE_S = 8 * 60
DOSSIER_GABARIT = Path(__file__).resolve().parent / "parcours_lecteur"
CLASSE_PARTIE = re.compile(r"\bclass=\"[^\"]*\bdsfr-chapitre\b|\bdata-partie\b", re.I)
PIED = re.compile(r"<div\b[^>]*\bdsfr-pied\b[^>]*>.*?</div>", re.I | re.S)
BLOC = re.compile(r"</(p|li|h[1-6]|div|tr|figcaption|blockquote)>|<br\s*/?>", re.I)
BALISE = re.compile(r"<[^>]+>")
INVISIBLE = re.compile(r"<(script|style|svg)\b.*?</\1>", re.I | re.S)

Journal = Callable[[str], None]


def _rien(_ligne: str) -> None:
    pass


# -------------------------------------------------------------------------------------
# Diapositives : parties, texte, notes
# -------------------------------------------------------------------------------------


def _lignes(fragment: str) -> list[str]:
    texte = module_html.unescape(BALISE.sub(" ", BLOC.sub("\n", INVISIBLE.sub(" ", fragment))))
    lignes = []
    for ligne in texte.split("\n"):
        ligne = re.sub(r"\s+", " ", ligne).strip()
        if ligne and (not lignes or lignes[-1] != ligne):
            lignes.append(ligne)
    return lignes


def diapositives(slides_html: str, titres: list[str]) -> list[dict]:
    """Pour chaque diapositive (index h) : {titre, lignes, notes, partie, quiz}."""
    resultat = []
    for h, section in enumerate(_sections_premier_niveau(slides_html)):
        ouvrante = section[: section.find(">") + 1]
        notes = " ".join(" ".join(_lignes(m.group(0))) for m in ASIDE_NOTES.finditer(section)).strip()
        corps = PIED.sub(" ", ASIDE_NOTES.sub(" ", section))
        titre = titres[h] if h < len(titres) else f"Diapositive {h + 1}"
        lignes = [l for l in _lignes(corps) if l != titre]
        quiz = re.search(r"data-quiz=\"([^\"]*)\"", ouvrante)
        resultat.append({"titre": titre, "lignes": lignes, "notes": notes, "partie": bool(CLASSE_PARTIE.search(ouvrante)),
                         "quiz": quiz.group(1).strip() if quiz and quiz.group(1).strip() else None})
    return resultat


def sequences(chapitres: list[dict], duree: float, diapos: list[dict]) -> list[dict]:
    """Regroupe les chapitres en séquences : [{numero, titre, debut, fin, chapitres: [index], diapositives: [h]}]."""
    if not chapitres:
        return [{"numero": 1, "titre": "Replay complet", "debut": 0.0, "fin": duree, "chapitres": [], "diapositives": list(range(len(diapos)))}]
    def partie(i: int) -> int | None:
        """Diapositive de partie franchie en arrivant au chapitre i (elle a pu être fusionnée dans ce chapitre si on l'a passée vite)."""
        h = chapitres[i].get("h")
        if not isinstance(h, int):
            return None
        avant = chapitres[i - 1].get("h") if i > 0 else None
        debut = avant + 1 if isinstance(avant, int) and avant < h else h
        franchies = [p for p in range(max(0, debut), min(h + 1, len(diapos))) if diapos[p]["partie"]]
        return franchies[-1] if franchies else None

    parties = {i: partie(i) for i in range(len(chapitres))}
    avec_parties = any(parties[i] is not None for i in range(1, len(chapitres)))
    groupes: list[list[int]] = []
    for i, c in enumerate(chapitres):
        nouveau = not groupes
        if groupes and avec_parties and parties[i] is not None:
            nouveau = True
        if groupes and not avec_parties and c["debut"] - chapitres[groupes[-1][0]]["debut"] >= DUREE_SEQUENCE_S:
            nouveau = True
        if nouveau:
            groupes.append([i])
        else:
            groupes[-1].append(i)
    resultat = []
    for n, g in enumerate(groupes):
        premier = chapitres[g[0]]
        fin = chapitres[groupes[n + 1][0]]["debut"] if n + 1 < len(groupes) else duree
        if avec_parties and parties[g[0]] is not None:
            titre = diapos[parties[g[0]]]["titre"]
        elif avec_parties and n == 0:
            titre = "Introduction"
        else:
            titre = premier["titre"]
        hs = [chapitres[i]["h"] for i in g if isinstance(chapitres[i].get("h"), int)]
        h_suivant = next((chapitres[i]["h"] for i in (groupes[n + 1] if n + 1 < len(groupes) else []) if isinstance(chapitres[i].get("h"), int)), None)
        if parties[g[0]] is not None and hs:
            hs.append(parties[g[0]])  # la diapositive de partie ouvre la séquence, même fusionnée dans le chapitre suivant
        h_suivant = parties[groupes[n + 1][0]] if n + 1 < len(groupes) and parties[groupes[n + 1][0]] is not None else h_suivant
        if hs and h_suivant is not None and h_suivant > min(hs):
            plage = list(range(min(hs), h_suivant))  # diapositives passées trop vite pour faire un chapitre comprises
        elif hs and n + 1 == len(groupes):
            plage = list(range(min(hs), max(hs) + 1))  # pas au-delà : les diapositives suivantes n'ont pas été montrées
        else:
            plage = sorted(set(hs))
        resultat.append({"numero": n + 1, "titre": titre, "debut": round(premier["debut"], 3), "fin": round(fin, 3), "chapitres": g, "diapositives": plage})
    return resultat


# -------------------------------------------------------------------------------------
# Questions (GIFT, format d'import de Moodle)
# -------------------------------------------------------------------------------------


def _gift(texte: str) -> str:
    return re.sub(r"([~=#{}:\\])", r"\\\1", str(texte)).replace("\n", " ")


def _pourcent(x: float) -> str:
    return f"{x:.5f}".rstrip("0").rstrip(".")


def question_gift(q: dict, numero: str) -> str:
    """Une question exportable (scorm.question_exportable) au format GIFT."""
    titre = f"::{_gift(numero)}::"
    t = q["type"]
    if t == "qcm":
        options = " ".join(("=" if i == q["bonne"] else "~") + _gift(o) for i, o in enumerate(q["options"]))
        return f"{titre}{_gift(q['question'])} {{{options}}}"
    if t == "multi":
        bonnes = set(q["bonnes"])
        plus, moins = 100 / len(bonnes), -100 / max(1, len(q["options"]) - len(bonnes))
        options = " ".join(f"~%{_pourcent(plus if i in bonnes else moins)}%{_gift(o)}" for i, o in enumerate(q["options"]))
        return f"{titre}{_gift(q['question'])} {{{options}}}"
    if t == "vrai_faux":
        return f"{titre}{_gift(q['question'])} {{{'TRUE' if q['bonne'] else 'FALSE'}}}"
    if t == "trou":
        reponses = " ".join("=" + _gift(b) for b in q["bonnes"])
        avant, _, apres = q["texte"].partition("___")
        question = (q.get("question") or "").strip()
        enonce = _gift(question + ("" if question[-1:] in ".?!:…" else " —")) + " " if question else ""
        return f"{titre}{enonce}{_gift(avant)}{{{reponses}}}{_gift(apres)}"
    if t == "nombre":
        unite = f" ({_gift(q['unite'])})" if q.get("unite") else ""
        return f"{titre}{_gift(q['question'])}{unite} {{#{q['bonne']}}}"
    raise ValueError(f"type non exportable : {t}")


def fichier_gift(blocs: list[tuple[str, list[dict]]], titre_parcours: str) -> str:
    """[(titre de la séquence, questions)] → GIFT, une catégorie par séquence."""
    lignes = [f"// {titre_parcours} : questions du parcours (amphi-replay). Moodle : Banque de questions > Importer > GIFT.", ""]
    for n, (titre, questions) in enumerate(blocs, 1):
        if not questions:
            continue
        lignes += [f"$CATEGORY: $course$/top/{titre_parcours.replace('/', '-')}/{n:02d} {titre.replace('/', '-')}", ""]
        lignes += [question_gift(q, f"S{n:02d}-Q{i:02d}") + "\n" for i, q in enumerate(questions, 1)]
    return "\n".join(lignes)


def questions_sequence(diapos: list[dict], plage: list[int], questions: dict, deja: set[str]) -> list[dict]:
    resultat = []
    for h in plage:
        ident = diapos[h]["quiz"] if 0 <= h < len(diapos) else None
        if not ident or ident in deja or ident not in questions:
            continue
        q = question_exportable(questions[ident])
        if q is not None:
            deja.add(ident)
            resultat.append(q)
    return resultat


# -------------------------------------------------------------------------------------
# Vidéo, images
# -------------------------------------------------------------------------------------


def image_cle_avant(video: Path, instant: float) -> float:
    """Instant de l'image clé qui précède `instant` (là où commence une découpe sans réencodage)."""
    if instant <= 0:
        return 0.0
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
                        "-read_intervals", f"{max(0.0, instant - 10):.3f}%{instant + 0.05:.3f}", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True)
    instants = []
    for l in r.stdout.split():
        try:
            instants.append(float(l.strip(",")))
        except ValueError:
            continue
    avant = [t for t in instants if t <= instant + 0.05]
    return round(max(avant), 3) if avant else instant


def decouper(video: Path, debut: float, fin: float, sortie: Path, consigner: Journal) -> float:
    """Découpe sans réencodage ; renvoie l'instant réel du début (image clé)."""
    reel = image_cle_avant(video, debut)
    executer(["ffmpeg", "-y", "-v", "error", "-ss", f"{debut:.3f}", "-to", f"{fin:.3f}", "-i", str(video), "-map", "0:v:0", "-map", "0:a:0?",
              "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(sortie)], consigner=consigner)
    return reel


def capture(video: Path, instant: float, sortie: Path, consigner: Journal) -> bool:
    try:
        executer(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, instant):.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "3",
                  "-vf", "scale='min(1280,iw)':-2", str(sortie)], consigner=None)
    except RuntimeError as e:
        consigner(f"image non produite ({sortie.name}) : {e}")
        return False
    return sortie.is_file()


# -------------------------------------------------------------------------------------
# Textes
# -------------------------------------------------------------------------------------


def essentiel_md(numero: int, titre: str, diapos: list[dict], plage: list[int]) -> str:
    lignes = [f"# Séquence {numero} : {titre}", "",
              "> Brouillon produit à partir du texte des diapositives et des notes du présentateur : à relire et à réécrire avant publication.", ""]
    for h in plage:
        d = diapos[h] if 0 <= h < len(diapos) else None
        if not d or d["quiz"] or d["partie"]:
            continue
        lignes += [f"## {d['titre']}", ""]
        lignes += [f"- {l}" for l in d["lignes"]]
        if d["notes"]:
            lignes += ["", f"*Notes du présentateur :* {d['notes']}"]
        lignes.append("")
    return "\n".join(lignes)


def _modele_transcription():
    import sys

    dossier = str(RACINE / "postprod")
    if dossier not in sys.path:
        sys.path.insert(0, dossier)
    import transcrire

    return transcrire


def transcription_disponible() -> bool:
    try:
        return _modele_transcription().disponible()
    except ImportError:
        return False


# -------------------------------------------------------------------------------------
# Production
# -------------------------------------------------------------------------------------


def produire_parcours(dossier_presentation: Path, dossier_replay: Path, elements: list[str], *, titre: str | None = None,
                      consigner: Journal = _rien, etape: Callable[[str], None] | None = None) -> dict:
    """Produit `<replay>/parcours/` avec les éléments demandés (ELEMENTS) ; renvoie le contenu de parcours.json."""
    dossier_presentation, dossier_replay = Path(dossier_presentation), Path(dossier_replay)
    elements = [e for e in ELEMENTS if e in set(elements)]
    video = dossier_replay / "replay.mp4"
    if not video.is_file():
        raise ValueError(f"replay.mp4 absent dans {dossier_replay}")
    donnees = json.loads((dossier_replay / "journal.json").read_text(encoding="utf-8")) if (dossier_replay / "journal.json").is_file() else {}
    duree = float(donnees.get("duree") or 0.0)
    if not duree:
        from .replays import sonder

        duree = sonder(video)["duree"]
    titre_final = (titre or donnees.get("titre") or dossier_replay.name).strip()
    chapitres = sorted((dict(c) for c in donnees.get("chapitres") or [] if isinstance(c, dict) and "debut" in c), key=lambda c: float(c["debut"]))
    titres = titres_presentation(dossier_presentation)
    slides = dossier_presentation / "slides.html"
    diapos = diapositives(slides.read_text(encoding="utf-8"), titres) if slides.is_file() else []
    seqs = sequences(chapitres, duree, diapos)
    consigner(f"parcours : {len(seqs)} séquence(s) ; éléments : {', '.join(elements) or 'aucun'}")

    sortie = dossier_replay / "parcours"
    if sortie.exists():
        shutil.rmtree(sortie)
    sortie.mkdir()

    # Transcription (une fois, sur tout le replay)
    mots: list[dict] | None = None
    if {"sous_titres", "transcription"} & set(elements):
        if etape:
            etape("transcription")
        t = _modele_transcription()
        if not t.disponible():
            consigner("transcription impossible : faster-whisper absent (pip install -r requirements-postprod.txt) ; sous-titres et transcription sautés")
        else:
            vocabulaire = ", ".join([titre_final] + [d["titre"] for d in diapos if d["partie"]][:12])
            mots = t.transcrire(video, vocabulaire=vocabulaire, consigner=consigner)
            cues = t.sous_titres(mots)
            if "sous_titres" in elements:
                (sortie / "sous-titres.vtt").write_text(t.vtt(cues), encoding="utf-8")
            if "transcription" in elements:
                (sortie / "transcription.txt").write_text(t.texte_brut(t.paragraphes(mots)), encoding="utf-8")

    if etape:
        etape("parcours")
    try:
        questions = charger_questions(dossier_presentation / "quiz.json") if "questions" in elements or DECRIRE & set(elements) else {}
    except (OSError, ValueError) as e:
        consigner(f"quiz.json illisible, pas de questions : {e}")
        questions = {}
    deja: set[str] = set()
    blocs_gift: list[tuple[str, list[dict]]] = []
    sequences_json = []
    for s in seqs:
        nom = f"{s['numero']:02d}-{slugifier(s['titre'])}"
        d = sortie / nom
        d.mkdir()
        entree: dict = {"numero": s["numero"], "titre": s["titre"], "dossier": nom, "debut": s["debut"], "fin": s["fin"],
                        "duree": round(s["fin"] - s["debut"], 3), "video": None, "sous_titres": None, "transcription": None,
                        "images": [], "diapositives": None, "essentiel": None, "questions": None, "gift": None}
        debut_video = s["debut"]
        if "videos" in elements and s["fin"] - s["debut"] >= DUREE_MIN_EXTRAIT_S:
            debut_video = decouper(video, s["debut"], s["fin"], d / "video.mp4", consigner)
            entree["video"] = f"{nom}/video.mp4"
        entree["chapitres"] = [{"titre": chapitres[i]["titre"], "debut": round(max(0.0, chapitres[i]["debut"] - debut_video), 3)} for i in s["chapitres"]]
        if mots is not None:
            t = _modele_transcription()
            if "sous_titres" in elements:
                (d / "sous-titres.vtt").write_text(t.vtt(t.sous_titres(mots), debut_video, s["fin"]), encoding="utf-8")
                entree["sous_titres"] = f"{nom}/sous-titres.vtt"
            if "transcription" in elements:
                paras = t.paragraphes(mots, debut_video, s["fin"])
                (d / "transcription.txt").write_text(t.texte_brut(paras) if paras else "", encoding="utf-8")
                entree["transcription"] = paras
                entree["transcription_fichier"] = f"{nom}/transcription.txt"
        if "images" in elements:
            (d / "images").mkdir()
            for i in s["chapitres"]:
                c = chapitres[i]
                fin_c = chapitres[i + 1]["debut"] if i + 1 < len(chapitres) else duree
                if fin_c - c["debut"] < 4 or c.get("titre", "").startswith("Quiz"):
                    continue
                nom_image = f"{i + 1:02d}-{slugifier(c['titre'])}.jpg"
                if capture(video, max(c["debut"] + 1.0, fin_c - 1.5), d / "images" / nom_image, consigner):
                    entree["images"].append({"fichier": f"{nom}/images/{nom_image}", "titre": c["titre"]})
        if "textes" in elements or DECRIRE & set(elements):
            vues = [{"titre": diapos[h]["titre"], "lignes": diapos[h]["lignes"], "notes": diapos[h]["notes"]}
                    for h in s["diapositives"] if 0 <= h < len(diapos) and not diapos[h]["quiz"] and not diapos[h]["partie"]]
            entree["diapositives"] = vues
            if "textes" in elements:
                (d / "essentiel.md").write_text(essentiel_md(s["numero"], s["titre"], diapos, s["diapositives"]), encoding="utf-8")
                entree["essentiel"] = f"{nom}/essentiel.md"
        if "questions" in elements or DECRIRE & set(elements):
            qs = questions_sequence(diapos, s["diapositives"], questions, deja)
            entree["questions"] = qs
            blocs_gift.append((s["titre"], qs))
            if "questions" in elements and qs:
                (d / "questions.gift").write_text(fichier_gift([(s["titre"], qs)], titre_final), encoding="utf-8")
                entree["gift"] = f"{nom}/questions.gift"
        sequences_json.append(entree)
        consigner(f"séquence {s['numero']} « {s['titre']} » : {entree['duree'] / 60:.1f} min" + (f", {len(entree['questions'])} question(s)" if entree["questions"] else ""))

    fichiers = {}
    if "questions" in elements and any(q for _, q in blocs_gift):
        (sortie / "questions.gift").write_text(fichier_gift(blocs_gift, titre_final), encoding="utf-8")
        fichiers["gift"] = "questions.gift"
    if (sortie / "sous-titres.vtt").is_file():
        fichiers["sous_titres"] = "sous-titres.vtt"
    if (sortie / "transcription.txt").is_file():
        fichiers["transcription"] = "transcription.txt"
    parcours = {
        "titre": titre_final,
        "presentation": dossier_presentation.name,
        "replay": dossier_replay.name,
        "duree": round(duree, 3),
        "genere": datetime.now().replace(microsecond=0).isoformat(),
        "elements": elements,
        "fichiers": fichiers,
        "sequences": sequences_json,
    }
    (sortie / "parcours.json").write_text(json.dumps(parcours, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if "page" in elements:
        gabarit = (DOSSIER_GABARIT / "cours.html").read_text(encoding="utf-8")
        donnees_page = json.dumps(parcours, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        (sortie / "cours.html").write_text(gabarit.replace("{{TITRE}}", module_html.escape(titre_final)).replace("{{PARCOURS_JSON}}", donnees_page), encoding="utf-8")
        consigner("page de cours : parcours/cours.html")
    if "synopsis" in elements:
        from .synopsis import disponible, ecrire_synopsis

        if disponible():
            ecrire_synopsis(parcours, sortie / "synopsis.xlsx")
            consigner("synopsis Excel : parcours/synopsis.xlsx")
        else:
            consigner("synopsis impossible : openpyxl absent (pip install -r requirements-postprod.txt)")
    taille = sum(f.stat().st_size for f in sortie.rglob("*") if f.is_file())
    consigner(f"parcours prêt : {len(sequences_json)} séquence(s), {taille / 1e6:.0f} Mo")
    return parcours


def lire_parcours(dossier_replay: Path) -> dict | None:
    f = Path(dossier_replay) / "parcours" / "parcours.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
    except (OSError, ValueError):
        return None

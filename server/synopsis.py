"""Synopsis Excel d'un parcours de formation (docs/postprod.md §6) : `<replay>/parcours/synopsis.xlsx`.

Même grille que les synopsis de digitalisation des formations (bloc, séquence, durée, session, objectifs, activité,
durée d'activité, modalité, type d'activité, ressource existante, modification, ressource à créer, temps d'hybridation,
livrable, compétences), une ligne par activité de chaque séquence, et un onglet de synthèse (heures par type).

Construit à partir de `parcours/parcours.json` (server/parcours.py). Dépendance : openpyxl (requirements-postprod.txt).
"""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

COLONNES = [
    ("Blocs de compétences", 22), ("Séquences", 28), ("Durée de la séquence (h)", 11), ("Session", 10),
    ("Objectifs de la séquence", 36), ("Description de l'activité", 48), ("Durée activité (h)", 10), ("Modalité", 16),
    ("Type d'activités", 30), ("Ressource existante", 34), ("Modification à apporter", 30),
    ("Ressources à créer / Format de la ressource", 30), ("Temps hybridation", 11), ("Travail attendu / livrable pour l'apprenant", 28),
    ("Compétences théoriques (connaissances)", 24), ("Compétences pratiques (habiletés)", 24),
]
MODALITE = "Distanciel asynchrone"
TYPE_VIDEO = "Vidéo pédagogique (cours filmé, motion design, démonstration)"
TYPE_PAGE = "Page de cours en ligne (HTML / Moodle)"
TYPE_QUIZ = "Quiz / QCM / Vrai-Faux"
MOTS_PAR_MINUTE = 200  # lecture
MINUTES_PAR_QUESTION = 1.0
MINUTES_PAR_IMAGE = 0.5


def _h(minutes: float) -> float:
    """Heures au centième (une vidéo de 18 min 36 = 0,31 h), au moins 0,05 h."""
    return max(0.05, round(minutes / 60, 2))


def _duree(s: float) -> str:
    s = int(round(s))
    return f"{s // 3600} h {s % 3600 // 60:02d}" if s >= 3600 else f"{s // 60} min {s % 60:02d}"


def activites(seq: dict) -> list[dict]:
    """Les activités d'une séquence, dans l'ordre du cours : [{description, minutes, type, ressource, modification, format, livrable}]."""
    liste = []
    chapitres: list[str] = []
    for c in seq.get("chapitres") or []:
        if not chapitres or chapitres[-1] != c["titre"]:  # une diapositive revue deux fois de suite = un seul chapitre
            chapitres.append(c["titre"])
    if seq.get("video"):
        detail = f" ; {len(chapitres)} chapitres : " + " · ".join(chapitres) if len(chapitres) > 1 else ""
        liste.append({"description": f"Vidéo de la séquence ({_duree(seq['duree'])}){detail}", "minutes": seq["duree"] / 60, "type": TYPE_VIDEO,
                      "ressource": seq["video"] + (f" + {seq['sous_titres']}" if seq.get("sous_titres") else ""),
                      "modification": "Relire les sous-titres (transcription automatique)" if seq.get("sous_titres") else "",
                      "format": "MP4" + (" + sous-titres WebVTT" if seq.get("sous_titres") else "") + " ; Moodle : Fichier ou H5P Vidéo interactive",
                      "livrable": ""})
    if seq.get("transcription"):
        mots = sum(len(p["texte"].split()) for p in seq["transcription"])
        liste.append({"description": f"Transcription écrite de la vidéo ({mots} mots), pour l'accessibilité et la révision", "minutes": 0, "type": TYPE_PAGE,
                      "ressource": seq.get("transcription_fichier") or "", "modification": "Relire (transcription automatique)",
                      "format": "Texte ; Moodle : Page", "livrable": ""})
    diapos = seq.get("diapositives") or []
    if diapos:
        mots = sum(len(" ".join(d["lignes"]).split()) for d in diapos)
        liste.append({"description": "L'essentiel : " + " · ".join(d["titre"] for d in diapos), "minutes": mots / MOTS_PAR_MINUTE, "type": TYPE_PAGE,
                      "ressource": seq.get("essentiel") or "texte des diapositives et notes du présentateur",
                      "modification": "Réécrire le brouillon en texte suivi", "format": "Page Moodle (texte et images)", "livrable": ""})
    if seq.get("images"):
        liste.append({"description": f"Images des diapositives ({len(seq['images'])})", "minutes": len(seq["images"]) * MINUTES_PAR_IMAGE, "type": TYPE_PAGE,
                      "ressource": seq["dossier"] + "/images/", "modification": "Choisir celles qui illustrent la page", "format": "JPEG, dans la Page Moodle",
                      "livrable": ""})
    if seq.get("questions"):
        n = len(seq["questions"])
        liste.append({"description": f"Quiz d'auto-évaluation : {n} question{'s' if n > 1 else ''} posées pendant la séance", "minutes": n * MINUTES_PAR_QUESTION,
                      "type": TYPE_QUIZ, "ressource": seq.get("gift") or "questions de la présentation (quiz.json)", "modification": "",
                      "format": "GIFT ; Moodle : Banque de questions + Test", "livrable": "Score au test (achèvement : réussir)"})
    return liste


def ecrire_synopsis(parcours: dict, fichier: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    bleu, gris = PatternFill("solid", fgColor="000091"), PatternFill("solid", fgColor="E8EDF5")
    trait = Side(style="thin", color="C9D1DE")
    bord = Border(left=trait, right=trait, top=trait, bottom=trait)
    haut = Alignment(vertical="top", wrap_text=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "synopsis"
    ws["B2"] = "SYNOPSIS"
    ws["B2"].font = Font(bold=True, size=16, color="000091")
    ws["B3"] = parcours["titre"]
    ws["B3"].font = Font(bold=True, size=12)
    ws["B4"] = f"Parcours produit depuis le replay {parcours['presentation']} / {parcours['replay']} (amphi-replay), {parcours['genere'][:10]}"
    ws["B4"].font = Font(italic=True, color="55627A")
    ligne_titre = 6
    for i, (titre, largeur) in enumerate(COLONNES, 1):
        c = ws.cell(row=ligne_titre, column=i, value=titre)
        c.font, c.fill, c.alignment, c.border = Font(bold=True, color="FFFFFF"), bleu, Alignment(vertical="center", wrap_text=True), bord
        ws.column_dimensions[get_column_letter(i)].width = largeur
    ws.row_dimensions[ligne_titre].height = 45
    ws.freeze_panes = ws.cell(row=ligne_titre + 1, column=3)

    r = ligne_titre + 1
    par_type: "OrderedDict[str, float]" = OrderedDict()
    total_h = 0.0
    for seq in parcours["sequences"]:
        acts = activites(seq)
        if not acts:
            continue
        heures = [_h(a["minutes"]) if a["minutes"] else 0 for a in acts]
        duree_seq = round(sum(heures), 2)
        total_h += duree_seq
        debut = r
        for a, h in zip(acts, heures):
            valeurs = [parcours["titre"] if r == ligne_titre + 1 else None, f"S{seq['numero']} – {seq['titre']}", duree_seq if r == debut else None, None,
                       None, a["description"], h or None, MODALITE, a["type"], a["ressource"], a["modification"] or None, a["format"], None,
                       a["livrable"] or None, None, None]
            for col, v in enumerate(valeurs, 1):
                c = ws.cell(row=r, column=col, value=v)
                c.alignment, c.border = haut, bord
                if col in (3, 7):
                    c.number_format = "0.00"
                if col == 2:
                    c.font = Font(bold=r == debut)
            par_type[a["type"]] = par_type.get(a["type"], 0.0) + h
            r += 1
        for col in range(1, len(COLONNES) + 1):  # séparation entre séquences
            ws.cell(row=debut, column=col).border = Border(left=trait, right=trait, bottom=trait, top=Side(style="medium", color="000091"))
    ws.cell(row=r + 1, column=6, value="Total parcours à distance (h)").font = Font(bold=True)
    ws.cell(row=r + 1, column=7, value=round(total_h, 2)).font = Font(bold=True)
    ws.cell(row=r + 1, column=7).number_format = "0.00"
    ws.auto_filter.ref = f"A{ligne_titre}:{get_column_letter(len(COLONNES))}{max(r - 1, ligne_titre)}"

    s = wb.create_sheet("synthèse")
    s["A1"], s["A1"].font = "Synthèse du parcours", Font(bold=True, size=14, color="000091")
    lignes = [("Titre", parcours["titre"]), ("Séquences", len(parcours["sequences"])), ("Durée des vidéos", _duree(parcours["duree"])),
              ("Heures à distance (asynchrone)", round(total_h, 2)), ("Questions d'auto-évaluation", sum(len(q.get("questions") or []) for q in parcours["sequences"]))]
    for i, (k, v) in enumerate(lignes, 3):
        s.cell(row=i, column=1, value=k).font = Font(bold=True)
        s.cell(row=i, column=2, value=v)
    base = len(lignes) + 5
    for col, t in enumerate(("Type d'activité", "Heures"), 1):
        c = s.cell(row=base, column=col, value=t)
        c.font, c.fill = Font(bold=True), gris
    for i, (t, h) in enumerate(par_type.items(), base + 1):
        s.cell(row=i, column=1, value=t)
        s.cell(row=i, column=2, value=round(h, 2)).number_format = "0.00"
    s.column_dimensions["A"].width, s.column_dimensions["B"].width = 58, 40
    s.cell(row=base + len(par_type) + 2, column=1,
           value=f"Durées en heures décimales : vidéo réelle ; lecture à {MOTS_PAR_MINUTE} mots/min ; {MINUTES_PAR_QUESTION:g} min par question ; {MINUTES_PAR_IMAGE:g} min par image.").font = Font(italic=True, color="55627A")

    fichier.parent.mkdir(parents=True, exist_ok=True)
    wb.save(fichier)
    return fichier


def synopsis_depuis_replay(dossier_replay: Path) -> Path:
    """Écrit (ou réécrit) parcours/synopsis.xlsx à partir de parcours/parcours.json."""
    dossier = Path(dossier_replay) / "parcours"
    parcours = json.loads((dossier / "parcours.json").read_text(encoding="utf-8"))
    return ecrire_synopsis(parcours, dossier / "synopsis.xlsx")


def disponible() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("openpyxl") is not None
    except (ImportError, ValueError):
        return False

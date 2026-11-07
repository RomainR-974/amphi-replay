#!/usr/bin/env python3
"""Conversion d'un PDF en diapositives reveal.js : une <section> par page, texte repris en HTML.

    python tools/pdf2reveal.py support.pdf --sortie deck/slides.html [--notes notes.md] [--quiz q1:4 --quiz q2:7]

- Le premier bloc de texte de la page (le plus haut, en plus gros) devient le titre <h2>.
- Les lignes suivantes deviennent des paragraphes ; les lignes commençant par un tiret ou une puce deviennent des <li>.
- Les images de page ne sont pas reprises (le PRD impose du texte HTML) ; les images embarquées sont
  extraites dans deck/images/ et insérées en <img> si --images est passé.
- --quiz ID:PAGE insère une <section data-quiz="ID"> après la page indiquée.
- --notes prend un Markdown dont chaque bloc « ## Page N » devient les notes de l'orateur de la page N.
- La première section reçoit data-qr (QR code du quiz).

Le résultat est un point de départ à retoucher à la main : fragments, iframes, mise en page.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

PUCE = re.compile(r"^\s*(?:[-–•▪◦●*]|\d+[.)])\s+")


def extraire_pages(pdf: Path, dossier_images: Path | None) -> list[dict]:
    import fitz  # PyMuPDF

    pages = []
    doc = fitz.open(pdf)
    for numero, page in enumerate(doc, start=1):
        blocs = page.get_text("dict")["blocks"]
        textes: list[tuple[float, float, str]] = []  # (y, taille police, texte)
        for bloc in blocs:
            if bloc.get("type") != 0:
                continue
            for ligne in bloc["lines"]:
                texte = "".join(s["text"] for s in ligne["spans"]).strip()
                if not texte:
                    continue
                taille = max(s["size"] for s in ligne["spans"])
                textes.append((ligne["bbox"][1], taille, texte))
        textes.sort(key=lambda t: t[0])
        images: list[str] = []
        if dossier_images is not None:
            dossier_images.mkdir(parents=True, exist_ok=True)
            for i, img in enumerate(page.get_images(full=True)):
                pix = fitz.Pixmap(doc, img[0])
                if pix.n - pix.alpha >= 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                nom = f"page{numero:03d}_{i + 1}.png"
                pix.save(dossier_images / nom)
                images.append(nom)
        pages.append({"numero": numero, "lignes": textes, "images": images})
    return pages


def page_vers_section(page: dict, notes: str | None, premiere: bool) -> str:
    lignes = page["lignes"]
    parties: list[str] = []
    attr = " data-qr" if premiere else ""
    if lignes:
        # Titre : la ligne de plus grande taille parmi le tiers supérieur, sinon la première.
        candidates = lignes[: max(1, len(lignes) // 3)]
        titre = max(candidates, key=lambda t: t[1])
        parties.append(f"  <{'h1' if premiere else 'h2'}>{html.escape(titre[2])}</{'h1' if premiere else 'h2'}>")
        corps = [t for t in lignes if t is not titre]
        liste_ouverte = False
        for _, _, texte in corps:
            if PUCE.match(texte):
                if not liste_ouverte:
                    parties.append("  <ul>")
                    liste_ouverte = True
                parties.append(f"    <li>{html.escape(PUCE.sub('', texte))}</li>")
            else:
                if liste_ouverte:
                    parties.append("  </ul>")
                    liste_ouverte = False
                parties.append(f"  <p>{html.escape(texte)}</p>")
        if liste_ouverte:
            parties.append("  </ul>")
    for img in page["images"]:
        parties.append(f'  <img src="images/{img}" alt="">')
    if notes:
        parties.append(f"  <aside class=\"notes\">{html.escape(notes)}</aside>")
    return f"<!-- page {page['numero']} -->\n<section{attr}>\n" + "\n".join(parties) + "\n</section>\n"


def lire_notes(fichier: Path | None) -> dict[int, str]:
    if not fichier:
        return {}
    notes: dict[int, str] = {}
    courante: int | None = None
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s*Page\s+(\d+)", ligne, re.IGNORECASE)
        if m:
            courante = int(m.group(1))
            notes[courante] = ""
        elif courante is not None:
            notes[courante] += ligne + "\n"
    return {k: v.strip() for k, v in notes.items()}


def principal(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pdf", type=Path)
    p.add_argument("--sortie", type=Path, default=Path("deck/slides.html"))
    p.add_argument("--notes", type=Path, default=None, help="Markdown de notes (« ## Page N »)")
    p.add_argument("--quiz", action="append", default=[], metavar="ID:PAGE", help="insérer une diapositive de quiz après la page")
    p.add_argument("--images", action="store_true", help="extraire les images embarquées dans deck/images/")
    args = p.parse_args(argv)

    quiz_apres: dict[int, list[str]] = {}
    for spec in args.quiz:
        ident, _, page = spec.partition(":")
        quiz_apres.setdefault(int(page), []).append(ident)

    pages = extraire_pages(args.pdf, args.sortie.parent / "images" if args.images else None)
    notes = lire_notes(args.notes)
    sections = [f"<!-- Généré par tools/pdf2reveal.py depuis {html.escape(args.pdf.name)} : {len(pages)} pages. À retoucher à la main. -->\n"]
    for i, page in enumerate(pages):
        sections.append(page_vers_section(page, notes.get(page["numero"]), premiere=(i == 0)))
        for ident in quiz_apres.get(page["numero"], []):
            sections.append(f'<section data-quiz="{html.escape(ident)}"></section>\n')
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text("\n".join(sections), encoding="utf-8")
    print(f"{len(pages)} pages → {args.sortie} ({sum(len(v) for v in quiz_apres.values())} diapositive(s) de quiz)")
    return 0


if __name__ == "__main__":
    sys.exit(principal())

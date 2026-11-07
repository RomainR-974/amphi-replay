#!/usr/bin/env python3
"""Insère des diapositives de quiz dans un deck/slides.html, après des diapositives données.

    python tools/inserer_quiz.py deck/slides.html --apres s3:sondage1 --apres s30:avis1 --libre s39:classement

- `--apres sN:ID` : insère `<section data-quiz="ID">` juste après la section `data-id="sN"` (ou le N-ième
  `<section>` de premier niveau si aucun data-id ne correspond, N commençant à 1).
- `--libre sN:classement` : insère une section libre « Classement » (podium) après sN.
- `--qr sN` : ajoute `data-qr` (QR code) à la section sN.
Les identifiants de quiz existants dans le fichier ne sont pas dupliqués (relance sans effet).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SECTION = re.compile(r"<section\b[^>]*>.*?</section>", re.S)
CLASSEMENT = """<section class="libre" data-id="{id}" data-background-color="#2e3e7a">
  <div class="el texte" data-id="e1" style="left:120px;top:50px;width:1680px;height:110px;font-size:64px;font-weight:800;color:#ffffff;text-align:center">Classement</div>
  <div class="el classement" data-amphi="classement" data-nombre="10" data-id="e2" style="left:120px;top:180px;width:1680px;height:860px;color:#ffffff"></div>
  <aside class="notes">Podium animé : mis à jour à chaque « Révéler ».</aside>
</section>"""


def principal(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("fichier", type=Path)
    p.add_argument("--apres", action="append", default=[], metavar="sN:ID")
    p.add_argument("--libre", action="append", default=[], metavar="sN:classement")
    p.add_argument("--qr", action="append", default=[], metavar="sN")
    args = p.parse_args(argv)

    html = args.fichier.read_text(encoding="utf-8")
    sections = list(SECTION.finditer(html))
    existants = set(re.findall(r'data-quiz="([^"]+)"', html))

    def trouver(cle: str) -> re.Match | None:
        for m in sections:
            if re.search(rf'data-id="{re.escape(cle)}"', m.group(0).split(">", 1)[0]):
                return m
        n = re.fullmatch(r"s?(\d+)", cle)
        return sections[int(n.group(1)) - 1] if n and 0 < int(n.group(1)) <= len(sections) else None

    insertions: list[tuple[int, str]] = []
    for spec in args.apres:
        cle, _, ident = spec.partition(":")
        m = trouver(cle)
        if not m:
            print(f"section introuvable : {cle}", file=sys.stderr)
            return 2
        if ident in existants:
            print(f"quiz {ident} déjà présent, ignoré")
            continue
        insertions.append((m.end(), f'\n\n<section data-quiz="{ident}" data-id="quiz-{ident}"></section>'))
    for spec in args.libre:
        cle, _, genre = spec.partition(":")
        m = trouver(cle)
        if not m:
            print(f"section introuvable : {cle}", file=sys.stderr)
            return 2
        if genre == "classement" and 'data-amphi="classement"' not in html:
            insertions.append((m.end(), "\n\n" + CLASSEMENT.format(id="classement")))
    ajouts_qr = []
    for cle in args.qr:
        m = trouver(cle)
        if m and "data-qr" not in m.group(0).split(">", 1)[0]:
            ajouts_qr.append(m.start())

    # Appliquer de la fin vers le début pour garder les positions valides ; à position égale,
    # la dernière demandée est insérée d'abord pour que l'ordre des options soit respecté.
    for _, pos, texte in sorted(((i, p, t) for i, (p, t) in enumerate(insertions)), key=lambda x: (-x[1], -x[0])):
        html = html[:pos] + texte + html[pos:]
    for pos in sorted(ajouts_qr, reverse=True):
        html = html[:pos] + "<section data-qr" + html[pos + len("<section"):]
    args.fichier.write_text(html, encoding="utf-8")
    print(f"{len(insertions)} insertion(s), {len(ajouts_qr)} QR → {args.fichier}")
    return 0


if __name__ == "__main__":
    sys.exit(principal())

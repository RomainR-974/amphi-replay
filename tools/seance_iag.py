#!/usr/bin/env python3
"""Recette de composition du deck « IA générative et règlement intérieur » (séance du 9 novembre 2026).

Part d'un slides.html produit par pptx2reveal.py (41 sections) et y intègre les quiz DANS les
diapositives, ajoute des animations et des transitions, puis insère les quiz autonomes.

    python tools/pptx2reveal.py prez.pptx --sortie presentations/iag-v1/slides.html --medias presentations/iag-v1/medias --qr-premiere
    python tools/seance_iag.py presentations/iag-v1/slides.html

Rejouable après une modification du PowerPoint. Chaque étape est une fonction courte, pour servir
d'exemple à une autre séance (voir docs/format-deck.md pour le format).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SECTION = re.compile(r"<section\b[^>]*>.*?</section>", re.S)
QR = '<div class="el qr" data-amphi="qr" data-id="{id}" style="left:{l}px;top:{t}px;width:{w}px;height:{h}px"></div>'
RESULTATS = '<div class="el resultats" data-amphi="resultats" data-sans-titre="1" data-question="{q}" data-id="{id}" style="left:{l}px;top:{t}px;width:{w}px;height:{h}px{style}"></div>'
MINUTEUR = '<div class="el minuteur" data-amphi="minuteur" data-question="{q}" data-duree="{d}" data-id="{id}" style="left:{l}px;top:{t}px;width:{w}px;height:{h}px;font-size:{fs}px"></div>'
TEXTE = '<div class="el texte" data-id="{id}" style="left:{l}px;top:{t}px;width:{w}px;height:{h}px;{style}">{html}</div>'


class Deck:
    def __init__(self, chemin: Path) -> None:
        self.chemin = chemin
        self.html = chemin.read_text(encoding="utf-8")

    # --- accès aux sections ---------------------------------------------------------------
    def section(self, sid: str) -> re.Match:
        for m in SECTION.finditer(self.html):
            if re.search(rf'\bdata-id="{re.escape(sid)}"', m.group(0).split(">", 1)[0]):
                return m
        raise KeyError(sid)

    def remplacer_section(self, sid: str, nouveau: str) -> None:
        m = self.section(sid)
        self.html = self.html[: m.start()] + nouveau + self.html[m.end():]

    def modifier(self, sid: str, f) -> None:
        self.remplacer_section(sid, f(self.section(sid).group(0)))

    # --- opérations sur une section (chaînes) ---------------------------------------------
    @staticmethod
    def attribut(sec: str, nom: str, valeur: str) -> str:
        tete, reste = sec.split(">", 1)
        tete = re.sub(rf'\s{nom}="[^"]*"', "", tete)
        return f'{tete} {nom}="{valeur}">{reste}'

    @staticmethod
    def ajouter(sec: str, element: str) -> str:
        """Insère un élément avant les notes (ou la fin de section)."""
        i = sec.rfind("<aside")
        if i < 0:
            i = sec.rfind("</section>")
        return sec[:i] + "  " + element + "\n" + sec[i:]

    @staticmethod
    def retirer(sec: str, did: str) -> str:
        """Retire l'élément data-id=did (img autonome ou div avec ses enfants)."""
        m = re.search(rf'<(div|img)\b[^>]*\bdata-id="{re.escape(did)}"[^>]*>', sec)
        if not m:
            return sec
        if m.group(1) == "img":
            return sec[: m.start()] + sec[m.end():]
        profondeur, pos = 1, m.end()
        for t in re.finditer(r"<div\b[^>]*>|</div>", sec[m.end():]):
            profondeur += 1 if t.group(0).startswith("<div") else -1
            if profondeur == 0:
                pos = m.end() + t.end()
                break
        return sec[: m.start()] + sec[pos:]

    @staticmethod
    def style(sec: str, did: str, **props: str) -> str:
        """Modifie des propriétés de style inline de l'élément data-id=did."""
        def rempl(m: re.Match) -> str:
            tag = m.group(0)
            st = re.search(r'style="([^"]*)"', tag)
            regles = dict(x.split(":", 1) for x in (st.group(1) if st else "").split(";") if ":" in x)
            regles.update({k.replace("_", "-"): v for k, v in props.items()})
            nouveau = ";".join(f"{k}:{v}" for k, v in regles.items())
            return re.sub(r'style="[^"]*"', f'style="{nouveau}"', tag) if st else tag[:-1] + f' style="{nouveau}">'
        return re.sub(rf'<(div|img)\b[^>]*\bdata-id="{re.escape(did)}"[^>]*>', rempl, sec, count=1)

    @staticmethod
    def texte(sec: str, did: str, ancien: str, nouveau: str) -> str:
        m = re.search(rf'<div\b[^>]*\bdata-id="{re.escape(did)}"[^>]*>', sec)
        if not m:
            return sec
        fin = sec.find("</div>", m.end())
        return sec[: fin] .replace(ancien, nouveau) if False else sec[: m.end()] + sec[m.end():fin].replace(ancien, nouveau) + sec[fin:]

    @staticmethod
    def fragmenter(sec: str, did: str, balise: str = "li", effet: str = "fade-up", depart: int = 0) -> str:
        """Fait apparaître un à un les <li> (ou <p>) de l'élément data-id=did, sans toucher aux fragments existants."""
        m = re.search(rf'<div\b[^>]*\bdata-id="{re.escape(did)}"[^>]*>', sec)
        if not m:
            return sec
        fin = sec.find("</div>", m.end())
        corps = sec[m.end():fin]
        n = [depart]

        def rempl(t: re.Match) -> str:
            tag = t.group(0)
            if "fragment" in tag:
                return tag
            idx = n[0]
            n[0] += 1
            if 'class="' in tag:
                tag = tag.replace('class="', f'class="fragment {effet} ', 1)
            else:
                tag = tag[:-1] + f' class="fragment {effet}">'
            return tag[:-1] + f' data-fragment-index="{idx}">'

        corps = re.sub(rf"<{balise}\b[^>]*>", rempl, corps)
        return sec[: m.end()] + corps + sec[fin:]

    @staticmethod
    def fragment_element(sec: str, did: str, index: int, effet: str = "fade-up") -> str:
        return re.sub(rf'<(div|img)\b([^>]*)\bdata-id="{re.escape(did)}"', lambda m: f'<{m.group(1)}{m.group(2)}class="fragment {effet}" data-fragment-index="{index}" data-id="{did}"' if "class=" not in m.group(2) else f'<{m.group(1)}{m.group(2).replace("class=\"", f"class=\"fragment {effet} ", 1)}data-fragment-index="{index}" data-id="{did}"', sec, count=1)


# -------------------------------------------------------------------------------------------
# Les étapes
# -------------------------------------------------------------------------------------------


def sondages(d: Deck) -> None:
    """Diapos 3 à 6 : les sondages à main levée deviennent des votes en direct, résultats sur la diapo."""
    for n, q in zip((3, 4, 5, 6), ("sondage1", "sondage2", "sondage3", "sondage4")):
        def f(sec: str, q=q) -> str:
            sec = Deck.attribut(sec, "data-quiz", q)
            sec = Deck.texte(sec, "e7", "À MAIN LEVÉE", "SUR VOTRE TÉLÉPHONE")
            sec = Deck.style(sec, "e8", top="300px")
            sec = Deck.ajouter(sec, RESULTATS.format(q=q, id="quiz-res", l=168, t=358, w=1250, h=298, style=""))
            return Deck.ajouter(sec, QR.format(id="quiz-qr", l=1560, t=300, w=240, h=240))
        d.modifier(f"s{n}", f)


def defi(d: Deck) -> None:
    """Diapo 8 : le défi « complétez la phrase » lance un texte libre avec compte à rebours de 2 min."""
    def f(sec: str) -> str:
        sec = Deck.attribut(sec, "data-quiz", "defi")
        sec = Deck.ajouter(sec, MINUTEUR.format(q="defi", d=120, id="quiz-min", l=1230, t=282, w=300, h=110, fs=72))
        return Deck.ajouter(sec, QR.format(id="quiz-qr", l=1600, t=282, w=200, h=200))
    d.modifier("s8", f)


def biais(d: Deck) -> None:
    """Diapo 16 : nuage de mots des biais perçus, directement sous la question."""
    def f(sec: str) -> str:
        sec = Deck.attribut(sec, "data-quiz", "biais")
        sec = Deck.style(sec, "e9", top="545px")
        sec = Deck.ajouter(sec, RESULTATS.format(q="biais", id="quiz-res", l=100, t=680, w=1110, h=340, style=""))
        return Deck.ajouter(sec, QR.format(id="quiz-qr", l=1040, t=360, w=180, h=180))
    d.modifier("s16", f)


def avis(d: Deck) -> None:
    """Diapos 30 à 37 : le chronomètre dessiné devient un vrai compte à rebours de 20 s lié au vote,
    les résultats s'affichent en direct à sa place."""
    for n, q in zip(range(30, 38), (f"avis{i}" for i in range(1, 9))):
        def f(sec: str, q=q) -> str:
            sec = Deck.attribut(sec, "data-quiz", q)
            # Retirer les objets rastérisés de la zone droite (chronomètre et aiguille).
            for m in list(re.finditer(r'<img\b[^>]*data-raster="1"[^>]*>', sec))[::-1]:
                tag = m.group(0)
                gauche = int(re.search(r"left:(\d+)px", tag).group(1))
                if gauche > 1000:
                    sec = sec[: m.start()] + sec[m.end():]
            sec = Deck.ajouter(sec, MINUTEUR.format(q=q, d=20, id="quiz-min", l=1250, t=290, w=560, h=180, fs=120))
            sec = Deck.ajouter(sec, RESULTATS.format(q=q, id="quiz-res", l=1200, t=500, w=660, h=430, style=""))
            return Deck.ajouter(sec, QR.format(id="quiz-qr", l=1000, t=830, w=160, h=160))
        d.modifier(f"s{n}", f)


def questions_du_public(d: Deck) -> None:
    """Diapo 40 « ? » : les questions envoyées par les étudiants s'affichent en cartes."""
    def f(sec: str) -> str:
        sec = Deck.attribut(sec, "data-quiz", "questions")
        sec = Deck.retirer(sec, "e6")
        sec = Deck.ajouter(sec, TEXTE.format(id="quiz-titre", l=160, t=215, w=1200, h=90, style="font-size:64px;font-weight:700;color:#ffffff", html="Vos questions"))
        sec = Deck.ajouter(sec, RESULTATS.format(q="questions", id="quiz-res", l=160, t=320, w=1700, h=690, style=";color:#ffffff"))
        return Deck.ajouter(sec, QR.format(id="quiz-qr", l=1600, t=200, w=220, h=220))
    d.modifier("s40", f)


def animations(d: Deck) -> None:
    """Listes qui apparaissent puce par puce, mots-clés un à un, transitions de chapitre."""
    d.modifier("s2", lambda s: Deck.fragment_element(Deck.fragment_element(Deck.fragment_element(s, "e10", 0), "e9", 1), "e11", 2))
    d.modifier("s9", lambda s: Deck.fragmenter(s, "e10"))
    d.modifier("s12", lambda s: Deck.fragmenter(s, "e8") if 'data-id="e8"' in s else s)
    d.modifier("s17", lambda s: Deck.fragmenter(s, "e9") if 'data-id="e9"' in s else s)
    for sid in ("s22", "s23"):
        def f(sec: str) -> str:
            # Zone de texte principale : celle qui contient le plus de paragraphes.
            cibles = [(len(re.findall(r"<p\b", sec[m.end(): sec.find('</div>', m.end())])), m.group(1)) for m in re.finditer(r'<div\b[^>]*class="el texte[^"]*"[^>]*data-id="(e\d+)"', sec)]
            if not cibles:
                return sec
            return Deck.fragmenter(sec, max(cibles)[1], balise="p")
        d.modifier(sid, f)
    for sid in ("s7", "s10", "s20", "s29"):
        d.modifier(sid, lambda s: Deck.attribut(s, "data-transition", "zoom"))
    d.modifier("s1", lambda s: Deck.attribut(s, "data-transition", "fade"))


def quiz_autonomes(d: Deck) -> None:
    """Quiz qui gardent leur propre diapositive (résultats pleine page) + classement."""
    # Le défi est lancé depuis la diapo 8 (minuteur + QR) et ses réponses s'affichent en cartes sur la suivante.
    m = d.section("s8")
    d.html = d.html[: m.end()] + '\n\n<section data-quiz="defi" data-id="quiz-defi"></section>' + d.html[m.end():]
    d.chemin.write_text(d.html, encoding="utf-8")
    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("inserer_quiz.py")), str(d.chemin),
         "--apres", "s21:principes", "--apres", "s27:mentions", "--apres", "s28:seuil",
         "--libre", "s38:classement", "--apres", "s39:bilan"],
        check=True,
    )
    d.html = d.chemin.read_text(encoding="utf-8")


def principal(argv: list[str] | None = None) -> int:
    chemin = Path((argv or sys.argv[1:] or ["presentations/iag-v1/slides.html"])[0])
    d = Deck(chemin)
    if 'data-quiz="sondage1"' in d.html:
        print("déjà composé : repartir d'une conversion fraîche de pptx2reveal.py", file=sys.stderr)
        return 2
    for etape in (sondages, defi, biais, avis, questions_du_public, animations, quiz_autonomes):
        etape(d)
    chemin.write_text(d.html, encoding="utf-8")
    print(f"{len(SECTION.findall(d.html))} sections → {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(principal())

#!/usr/bin/env python3
"""Réserve la zone de la vignette caméra d'OBS sur les diapositives libres (docs/zone-camera.md).

    python tools/reserver_camera.py presentations/<slug>/slides.html
        [--largeur 300 --hauteur 300 --position bas-droite --marge 20] [--toutes] [--section s42 …]

Par défaut les réglages viennent de `camera` dans le presentation.json voisin (défaut 300 × 300 en bas à droite,
marge 20 ; `"camera": null` → rien à faire) ; les options les remplacent. Sections visées : celles qui portent
`data-quiz`, plus celles nommées par `--section`, ou toutes avec `--toutes`. Dans chaque section, chaque `.el`
dont la boîte (style inline left/top/width/height) chevauche la zone est réagencé :

    qr        → coin opposé (bas-gauche pour une caméra en bas à droite) s'il est libre, sinon règles ci-dessous
    1. hauteur réduite (resultats, classement, iframe, minuteur, forme, texte, nuage ; ≥ 60 % de l'originale)
    2. décalé horizontalement à l'opposé de la zone si l'espace est libre (dans la scène, sans chevaucher un autre .el)
    3. largeur réduite (≥ 60 %)
    4. décalé verticalement si libre
    5. laissé en place et signalé

Même algorithme que deck/camera.js (reagencerBoites) : toute modification ici doit y être reportée. Le script
n'écrit que les valeurs left/top/width/height modifiées, ne touche à rien d'autre et est rejouable (idempotent).
Le rapport (section · élément · action) est imprimé sur la sortie standard.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SCENE_L, SCENE_H = 1920, 1080
DEFAUT = {"largeur": 300, "hauteur": 300, "position": "bas-droite", "marge": 20}
POSITIONS = ("bas-droite", "bas-gauche", "haut-droite", "haut-gauche")
REDUCTIBLES = ("resultats", "classement", "iframe", "minuteur", "forme", "texte", "nuage")
TYPES = ("texte", "image", "forme", "iframe", "video", "lottie", "minuteur", "resultats", "classement", "nuage", "qr", "declencheur")
MIN = 0.6

SECTION = re.compile(r"<section\b[^>]*>.*?</section>", re.S)
BALISE_EL = re.compile(r'<(div|img|iframe|video)\b[^>]*\bclass="(?:[^"]*\s)?el(?:\s[^"]*)?"[^>]*>', re.S)
PROPRIETE = {p: re.compile(rf"(^|;)(\s*{p}\s*:\s*)(-?\d+(?:\.\d+)?)(px)?(\s*)(?=;|$)") for p in ("left", "top", "width", "height")}


@dataclass
class Zone:
    x: int
    y: int
    l: int
    h: int
    position: str
    marge: int

    @property
    def droite(self) -> bool:
        return self.position.endswith("droite")

    @property
    def bas(self) -> bool:
        return self.position.startswith("bas")


@dataclass
class Boite:
    id: str
    type: str
    x: float
    y: float
    l: float
    h: float

    def rect(self) -> dict:
        return {"x": self.x, "y": self.y, "l": self.l, "h": self.h}


def normaliser_camera(valeur) -> dict | None:
    """Comme server.presentations.normaliser_camera (recopié : le script n'importe pas le serveur)."""
    if valeur is None:
        return None
    if not isinstance(valeur, dict):
        return dict(DEFAUT)

    def entier(v, d, minimum=1):
        try:
            n = int(round(float(v)))
        except (TypeError, ValueError):
            return d
        return n if n >= minimum else d

    position = valeur.get("position")
    return {
        "largeur": entier(valeur.get("largeur"), DEFAUT["largeur"]),
        "hauteur": entier(valeur.get("hauteur"), DEFAUT["hauteur"]),
        "position": position if position in POSITIONS else DEFAUT["position"],
        "marge": entier(valeur.get("marge"), DEFAUT["marge"], 0),
    }


def zone_camera(camera: dict | None) -> Zone | None:
    c = normaliser_camera(camera) if camera is not None else None
    if c is None:
        return None
    droite, bas = c["position"].endswith("droite"), c["position"].startswith("bas")
    # Caméra calée dans le coin : la zone = caméra + marge du côté intérieur (défaut : x ≥ 1600, y ≥ 760).
    l, h = min(SCENE_L, c["largeur"] + c["marge"]), min(SCENE_H, c["hauteur"] + c["marge"])
    return Zone(SCENE_L - l if droite else 0, SCENE_H - h if bas else 0, l, h, c["position"], c["marge"])


def chevauche(a, b) -> bool:
    """Rectangles (objets ou dicts avec x, y, l, h) qui se recouvrent, bords exclus."""
    if a is None or b is None:
        return False
    ax, ay, al, ah = _xylh(a)
    bx, by, bl, bh = _xylh(b)
    return ax < bx + bl and ax + al > bx and ay < by + bh and ay + ah > by


def _xylh(r):
    if isinstance(r, dict):
        return r["x"], r["y"], r["l"], r["h"]
    return r.x, r.y, r.l, r.h


def dans_scene(r: dict) -> bool:
    return r["x"] >= 0 and r["y"] >= 0 and r["x"] + r["l"] <= SCENE_L and r["y"] + r["h"] <= SCENE_H


def reagencer_boites(boites: list[Boite], zone: Zone | None) -> list[dict]:
    """Réimplémentation de AmphiCamera.reagencerBoites (deck/camera.js) ; modifie les boîtes en place."""
    actions: list[dict] = []
    if zone is None:
        return actions
    origine = {id(b): b.rect() for b in boites}  # les empilements d'origine ne bloquent pas, quel que soit l'ordre de traitement
    ordre = [b for b in boites if b.type == "qr"] + [b for b in boites if b.type != "qr"]
    for b in ordre:
        if not chevauche(b, zone):
            continue
        avant = b.rect()
        autres = [o for o in boites if o is not b and not chevauche(origine[id(o)], origine[id(b)])]

        def libre(r: dict) -> bool:
            return dans_scene(r) and not chevauche(r, zone) and not any(chevauche(r, o) for o in autres)

        def appliquer(action: str, **n) -> None:
            for k, v in n.items():
                setattr(b, k, v)
            actions.append({"id": b.id, "type": b.type, "action": action, "avant": avant, "apres": b.rect()})

        if b.type == "qr":
            coin = {"x": zone.marge if zone.droite else SCENE_L - zone.marge - b.l, "y": SCENE_H - zone.marge - b.h if zone.bas else zone.marge, "l": b.l, "h": b.h}
            if libre(coin):
                appliquer(f"coin {'bas' if zone.bas else 'haut'}-{'gauche' if zone.droite else 'droite'}", x=coin["x"], y=coin["y"])
                continue
        # 1. hauteur
        if b.type in REDUCTIBLES:
            y = b.y if zone.bas else zone.y + zone.h
            h = zone.y - b.y if zone.bas else b.y + b.h - (zone.y + zone.h)
            if h > 0 and h >= MIN * b.h:
                appliquer("hauteur réduite", y=y, h=h)
                continue
        # 2. décalage horizontal
        dx = {"x": zone.x - b.l if zone.droite else zone.x + zone.l, "y": b.y, "l": b.l, "h": b.h}
        if libre(dx):
            appliquer(f"décalé vers la {'gauche' if zone.droite else 'droite'}", x=dx["x"])
            continue
        # 3. largeur
        x = b.x if zone.droite else zone.x + zone.l
        l = zone.x - b.x if zone.droite else b.x + b.l - (zone.x + zone.l)
        if l > 0 and l >= MIN * b.l:
            appliquer("largeur réduite", x=x, l=l)
            continue
        # 4. décalage vertical
        dy = {"x": b.x, "y": zone.y - b.h if zone.bas else zone.y + zone.h, "l": b.l, "h": b.h}
        if libre(dy):
            appliquer(f"décalé vers le {'haut' if zone.bas else 'bas'}", y=dy["y"])
            continue
        actions.append({"id": b.id, "type": b.type, "action": "laissé en place (aucune règle applicable)", "avant": avant, "apres": avant})
    return actions


# --- Lecture / écriture du HTML (regex sur les attributs, comme tools/seance_iag.py) -------------------


def type_de(balise: str) -> str:
    classes = re.search(r'\bclass="([^"]*)"', balise)
    for c in (classes.group(1).split() if classes else []):
        if c in TYPES:
            return c
    tag = re.match(r"<(\w+)", balise).group(1).lower()
    return {"img": "image", "iframe": "iframe", "video": "video"}.get(tag, "texte")


def boite_de(balise: str) -> dict | None:
    st = re.search(r'\bstyle="([^"]*)"', balise)
    if not st:
        return None
    valeurs = {}
    for p, rx in PROPRIETE.items():
        m = rx.search(st.group(1))
        if not m:
            return None
        valeurs[p] = float(m.group(3))
    if valeurs["width"] <= 0 or valeurs["height"] <= 0:
        return None
    return {"x": valeurs["left"], "y": valeurs["top"], "l": valeurs["width"], "h": valeurs["height"]}


def _fmt(v: float) -> str:
    return str(int(round(v)))


def poser(balise: str, b: Boite) -> str:
    """Réécrit left/top/width/height dans le style de la balise, sans toucher au reste."""
    st = re.search(r'\bstyle="([^"]*)"', balise)
    style = st.group(1)
    for p, v in (("left", b.x), ("top", b.y), ("width", b.l), ("height", b.h)):
        style = PROPRIETE[p].sub(lambda m, v=v: f"{m.group(1)}{m.group(2)}{_fmt(v)}px{m.group(5)}", style, count=1)
    return balise[: st.start(1)] + style + balise[st.end(1):]


def id_section(sec: str) -> str:
    m = re.search(r'\bdata-id="([^"]*)"', sec.split(">", 1)[0])
    return m.group(1) if m else "?"


def section_visee(sec: str, toutes: bool, sections: set[str]) -> bool:
    tete = sec.split(">", 1)[0]
    if toutes:
        return True
    if re.search(r"\bdata-quiz=", tete):
        return True
    return id_section(sec) in sections


def reagencer_section(sec: str, zone: Zone) -> tuple[str, list[dict]]:
    """Réagence les .el d'une section ; renvoie (section modifiée, actions)."""
    balises = list(BALISE_EL.finditer(sec))
    boites, index = [], []
    for i, m in enumerate(balises):
        b = boite_de(m.group(0))
        if b is None:
            continue
        did = re.search(r'\bdata-id="([^"]*)"', m.group(0))
        boites.append(Boite(did.group(1) if did else f"#{i}", type_de(m.group(0)), **b))
        index.append(i)
    actions = reagencer_boites(boites, zone)
    if not actions:
        return sec, actions
    modifies = {a["id"] for a in actions if a["apres"] != a["avant"]}
    morceaux, pos = [], 0
    for b, i in zip(boites, index):
        if b.id not in modifies:
            continue
        m = balises[i]
        morceaux.append(sec[pos:m.start()])
        morceaux.append(poser(m.group(0), b))
        pos = m.end()
    morceaux.append(sec[pos:])
    return "".join(morceaux), actions


def reserver(html: str, zone: Zone | None, toutes: bool = False, sections: set[str] | None = None) -> tuple[str, list[tuple[str, dict]]]:
    """Applique l'algorithme aux sections visées d'un slides.html ; renvoie (html, [(id_section, action)])."""
    rapport: list[tuple[str, dict]] = []
    if zone is None:
        return html, rapport
    sections = sections or set()
    sortie, pos = [], 0
    for m in SECTION.finditer(html):
        sec = m.group(0)
        if section_visee(sec, toutes, sections):
            nouveau, actions = reagencer_section(sec, zone)
            rapport.extend((id_section(sec), a) for a in actions)
            sec = nouveau
        sortie.append(html[pos:m.start()])
        sortie.append(sec)
        pos = m.end()
    sortie.append(html[pos:])
    return "".join(sortie), rapport


def camera_du_dossier(slides: Path) -> dict | None:
    """`camera` du presentation.json voisin (défaut si absent, None si null)."""
    fichier = slides.parent / "presentation.json"
    if not fichier.is_file():
        return dict(DEFAUT)
    try:
        meta = json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAUT)
    if not isinstance(meta, dict) or "camera" not in meta:
        return dict(DEFAUT)
    return normaliser_camera(meta["camera"])


def _rect(r: dict) -> str:
    return f"{_fmt(r['x'])},{_fmt(r['y'])} {_fmt(r['l'])}×{_fmt(r['h'])}"


def principal(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Réserve la zone caméra d'OBS sur les diapositives (docs/zone-camera.md).")
    p.add_argument("slides", type=Path, help="presentations/<slug>/slides.html")
    p.add_argument("--largeur", type=int)
    p.add_argument("--hauteur", type=int)
    p.add_argument("--position", choices=POSITIONS)
    p.add_argument("--marge", type=int)
    p.add_argument("--toutes", action="store_true", help="toutes les sections, pas seulement celles qui portent data-quiz")
    p.add_argument("--section", action="append", default=[], metavar="ID", help="section supplémentaire (data-id), répétable")
    p.add_argument("--simuler", action="store_true", help="imprime le rapport sans écrire le fichier")
    a = p.parse_args(argv)

    camera = camera_du_dossier(a.slides)
    if any(v is not None for v in (a.largeur, a.hauteur, a.position, a.marge)):
        camera = dict(camera or DEFAUT)
        for cle in ("largeur", "hauteur", "position", "marge"):
            if getattr(a, cle) is not None:
                camera[cle] = getattr(a, cle)
        camera = normaliser_camera(camera)
    zone = zone_camera(camera)
    if zone is None:
        print(f"{a.slides} : caméra désactivée (camera: null), rien à faire")
        return 0
    html = a.slides.read_text(encoding="utf-8")
    nouveau, rapport = reserver(html, zone, a.toutes, set(a.section))
    print(f"{a.slides} : zone réservée x ≥ {zone.x}, y ≥ {zone.y} ({zone.l}×{zone.h}, {zone.position}, marge {zone.marge})")
    for sid, act in rapport:
        fleche = "" if act["apres"] == act["avant"] else f" : {_rect(act['avant'])} → {_rect(act['apres'])}"
        print(f"  {sid} · {act['id']} ({act['type']}) : {act['action']}{fleche}")
    if not rapport:
        print("  aucun élément ne chevauche la zone")
    laisses = [r for r in rapport if r[1]["apres"] == r[1]["avant"]]
    if nouveau != html and not a.simuler:
        a.slides.write_text(nouveau, encoding="utf-8")
        print(f"  {len(rapport) - len(laisses)} élément(s) réagencé(s), fichier écrit")
    elif nouveau != html:
        print(f"  {len(rapport) - len(laisses)} élément(s) à réagencer (simulation, fichier inchangé)")
    else:
        print("  fichier inchangé")
    if laisses:
        print(f"  {len(laisses)} élément(s) laissé(s) en place : à régler à la main dans l'éditeur", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(principal())

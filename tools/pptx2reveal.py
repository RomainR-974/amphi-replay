#!/usr/bin/env python3
"""Conversion d'un PowerPoint (.pptx) en sections « libres » reveal.js (voir docs/format-deck.md § 1.1).

    python tools/pptx2reveal.py presentation.pptx [--sortie deck/slides.html] [--medias deck/medias]
                                [--prefixe-medias medias] [--qr-premiere]

- Une <section class="libre"> par diapositive ; chaque forme devient un `.el` positionné en px
  dans une scène de 1920 × 1080 (facteur = 1920 / largeur de la diapositive).
- Texte riche (paragraphes, puces, gras/italique/souligné, couleurs, polices), images extraites dans
  le dossier médias (`s<diapo>_<n>.<ext>`), formes pleines, tableaux, notes de l'orateur, fond.
- Transitions (`p:transition`) et animations (`p:timing`) traduites en `data-transition` et en
  fragments reveal.js (`fragment fade-in`, `fade-up`, `zoom-in`…) avec `data-fragment-index`.
- Objets complexes (groupes, SmartArt, graphiques, formes libres, flèches…) rastérisés en PNG via
  LibreOffice (`soffice`, un seul export PDF pour toute la présentation) : `medias/s<diapo>_r<n>.png`.
  Le fond de chaque diapositive est remplacé par une couleur de chroma key dans la copie envoyée à
  LibreOffice (deux rendus, sur #ff00fe puis #00ff01, dont la différence donne la transparence exacte de
  chaque pixel : ombres et anticrénelage compris) ; lignes et connecteurs sont rastérisés de la même façon. `--sans-raster` désactive ; sans `soffice`
  dans le PATH, ces objets sont ignorés avec un avertissement.
- Tout ce qui n'est pas géré devient un commentaire `<!-- non converti : … -->` et un avertissement
  sur stderr ; aucune exception n'interrompt la conversion.

Le résultat est un point de départ : à retoucher dans l'éditeur du deck.
"""

from __future__ import annotations

import argparse
import html
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.enum.dml import MSO_COLOR_TYPE, MSO_FILL
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

LARGEUR_SCENE = 1920
HAUTEUR_SCENE = 1080

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

# Correspondance p:clrMap par défaut (tx1 → dk1, bg1 → lt1…) complétée par celle du masque.
CLRMAP_DEFAUT = {"tx1": "dk1", "tx2": "dk2", "bg1": "lt1", "bg2": "lt2"}

# Classe reveal.js pour un effet d'entrée PowerPoint (presetID → classe ou dict par presetSubtype).
EFFETS_ENTREE: dict[int, str | dict[int, str]] = {
    1: "",  # appear : apparition sèche
    10: "fade-in",
    2: {4: "fade-up", 1: "fade-down", 8: "fade-right", 2: "fade-left"},  # fly in
    22: "fade-in", 3: "fade-in", 5: "fade-in", 6: "fade-in", 7: "fade-in", 16: "fade-in",
    17: "fade-in", 21: "fade-in", 26: "fade-in", 4: "fade-in", 13: "fade-in", 14: "fade-in",
    23: "zoom-in", 53: "zoom-in", 9: "zoom-in", 12: "zoom-in",
    42: "fade-up", 47: "fade-up",
}

# Classe reveal.js pour un effet d'accentuation (presetID) ; 6 = agrandir/rétrécir, 9 = transparence.
EFFETS_EMPH: dict[int, str] = {6: "grow", 9: "semi-fade-out"}
EMPH_DEFAUT = "grow"

TRANSITIONS = {
    "fade": "fade",
    "push": "slide", "wipe": "slide", "cover": "slide", "pull": "slide",
    "zoom": "zoom", "newsflash": "zoom",
}

TAILLES_DEFAUT_PT = {"titre": 44, "corps": 24, "autre": 18}

# Formes automatiques rendues en CSS ; toute autre forme automatique est rastérisée.
FORMES_SIMPLES = (MSO_SHAPE.RECTANGLE, MSO_SHAPE.ROUNDED_RECTANGLE, MSO_SHAPE.OVAL)
MARGE_RASTER = 4  # px autour de la boîte englobante d'un objet rastérisé
TAILLE_MIN_RASTER = 8  # px : largeur/hauteur minimale de la découpe (lignes fines)
CHROMA_KEY = (0xFF, 0x00, 0xFE)  # fond imposé aux diapositives envoyées à LibreOffice (premier rendu)
CHROMA_KEY_2 = (0x00, 0xFF, 0x01)  # second rendu : les deux fonds donnent la transparence exacte (ombres…)
SEUIL_TRANSPARENCE = 24  # distance RVB au chroma key en deçà de laquelle un pixel devient transparent


# --------------------------------------------------------------------------------------------------
# Contexte de conversion
# --------------------------------------------------------------------------------------------------


class Contexte:
    """État partagé : facteur d'échelle, dossier médias, compteurs, avertissements."""

    def __init__(self, prs, medias: Path, prefixe: str):
        self.prs = prs
        self.largeur = int(prs.slide_width)
        self.hauteur = int(prs.slide_height)
        # 1920 / largeur, sauf si la diapositive (4:3 par exemple) déborderait en hauteur : on la fait tenir.
        self.facteur = min(LARGEUR_SCENE / self.largeur, HAUTEUR_SCENE / self.hauteur)
        self.decalage_x = (LARGEUR_SCENE - self.largeur * self.facteur) / 2
        self.decalage_y = (HAUTEUR_SCENE - self.hauteur * self.facteur) / 2
        self.medias = medias
        self.prefixe = prefixe.rstrip("/")
        self.avertissements: list[str] = []
        self.nb_images = 0
        self.nb_animations = 0
        self.themes: dict[int, dict[str, str]] = {}
        self.diapo = 0  # numéro de la diapositive en cours
        self.compteur_images = 0  # par diapositive
        self.compteur_fonds = 0
        self._part_courante = None  # partie OPC de la forme en cours (résolution des liens)
        # Objets rastérisés : (diapo, shape_id) → (chemin HTML, (left, top, width, height) px, nom)
        self.rasters: dict[tuple[int, int], tuple[str, tuple[int, int, int, int], str]] = {}
        self.nb_rasters = 0

    def px(self, emu) -> int:
        return round(int(emu) * self.facteur)

    def avertir(self, message: str) -> str:
        """Enregistre un avertissement et renvoie le commentaire HTML correspondant."""
        self.avertissements.append(f"diapo {self.diapo} : {message}" if self.diapo else message)
        print(f"avertissement : {self.avertissements[-1]}", file=sys.stderr)
        return f"<!-- non converti : {echapper_commentaire(message)} -->"

    def ecrire_media(self, nom: str, blob: bytes) -> str:
        self.medias.mkdir(parents=True, exist_ok=True)
        (self.medias / nom).write_bytes(blob)
        return f"{self.prefixe}/{nom}" if self.prefixe else nom

    def nouvelle_image(self, blob: bytes, ext: str) -> str:
        self.compteur_images += 1
        self.nb_images += 1
        ext = (ext or "bin").lower().lstrip(".")
        return self.ecrire_media(f"s{self.diapo}_{self.compteur_images}.{ext}", blob)


def xpath(element, chemin: str) -> list:
    """XPath lxml avec nos espaces de noms (les éléments python-pptx redéfinissent .xpath sans `namespaces`)."""
    return etree._Element.xpath(element, chemin, namespaces=NS)


def echapper_commentaire(texte: str) -> str:
    return texte.replace("--", "—")


def attr(valeur: str) -> str:
    """Valeur d'attribut HTML entre guillemets doubles (les apostrophes restent lisibles)."""
    return html.escape(valeur, quote=False).replace('"', "&quot;")


def couleur_hex(rgb) -> str:
    return f"#{str(rgb).lower()}"


def ajuster_luminosite(hexa: str, brillance: float) -> str:
    """Éclaircit (brillance > 0) ou assombrit (< 0) une couleur #rrggbb."""
    if not brillance:
        return hexa
    r, g, b = (int(hexa[i:i + 2], 16) for i in (1, 3, 5))
    if brillance > 0:
        r, g, b = (round(c + (255 - c) * brillance) for c in (r, g, b))
    else:
        r, g, b = (round(c * (1 + brillance)) for c in (r, g, b))
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, c)) for c in (r, g, b)))


# --------------------------------------------------------------------------------------------------
# Thème et couleurs
# --------------------------------------------------------------------------------------------------


def theme_du_masque(ctx: Contexte, masque) -> dict[str, str]:
    """Couleurs du thème (a:clrScheme) du masque, indexées par nom (dk1, lt1, accent1, …) et par alias."""
    cle = id(masque)
    if cle in ctx.themes:
        return ctx.themes[cle]
    couleurs: dict[str, str] = {}
    try:
        from pptx.opc.constants import RELATIONSHIP_TYPE as RT

        theme = masque.part.part_related_by(RT.THEME)
        racine = etree.fromstring(theme.blob)
        for element in racine.find(".//a:clrScheme", NS):
            nom = etree.QName(element).localname
            srgb = element.find("a:srgbClr", NS)
            sysclr = element.find("a:sysClr", NS)
            if srgb is not None:
                couleurs[nom] = "#" + srgb.get("val").lower()
            elif sysclr is not None and sysclr.get("lastClr"):
                couleurs[nom] = "#" + sysclr.get("lastClr").lower()
    except Exception as exc:  # noqa: BLE001
        ctx.avertir(f"thème illisible ({exc})")
    clrmap = dict(CLRMAP_DEFAUT)
    try:
        element_map = masque._element.find("p:clrMap", NS)
        if element_map is not None:
            clrmap.update({k: v for k, v in element_map.attrib.items() if k in CLRMAP_DEFAUT})
    except Exception:  # noqa: BLE001
        pass
    for alias, cible in clrmap.items():
        if cible in couleurs:
            couleurs[alias] = couleurs[cible]
    ctx.themes[cle] = couleurs
    return couleurs


def couleur_element(ctx: Contexte, theme: dict[str, str], fill) -> str | None:
    """Couleur CSS d'un élément a:solidFill (ou a:srgbClr/a:schemeClr), avec lumMod/lumOff approximés."""
    if fill is None:
        return None
    clr = fill if etree.QName(fill).localname in ("srgbClr", "schemeClr", "sysClr") else None
    if clr is None:
        for balise in ("a:srgbClr", "a:schemeClr", "a:sysClr"):
            clr = fill.find(balise, NS)
            if clr is not None:
                break
    if clr is None:
        return None
    nom = etree.QName(clr).localname
    if nom == "srgbClr":
        base = "#" + clr.get("val", "000000").lower()
    elif nom == "sysClr":
        base = "#" + clr.get("lastClr", "000000").lower()
    else:
        base = theme.get(clr.get("val", ""))
        if base is None:
            return None
    lum_mod = clr.find("a:lumMod", NS)
    lum_off = clr.find("a:lumOff", NS)
    if lum_mod is not None or lum_off is not None:
        mod = int(lum_mod.get("val", "100000")) / 100000 if lum_mod is not None else 1.0
        off = int(lum_off.get("val", "0")) / 100000 if lum_off is not None else 0.0
        # Approximation : lumMod < 1 assombrit, lumOff > 0 éclaircit.
        base = ajuster_luminosite(base, mod - 1)
        base = ajuster_luminosite(base, off)
    return base


def couleur_format(ctx: Contexte, theme: dict[str, str], color_format) -> str | None:
    """Couleur CSS d'un ColorFormat python-pptx (RGB ou couleur de thème), sinon None."""
    try:
        if color_format.type is None:
            return None
        if color_format.type == MSO_COLOR_TYPE.RGB:
            base = couleur_hex(color_format.rgb)
        elif color_format.type == MSO_COLOR_TYPE.SCHEME:
            base = theme.get(color_format.theme_color.xml_value)
            if base is None:
                return None
        else:
            return None
        return ajuster_luminosite(base, color_format.brightness or 0)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------------------------------
# Héritage des propriétés de texte (forme → disposition → masque)
# --------------------------------------------------------------------------------------------------


def placeholder_de_base(forme):
    """Le placeholder dont hérite `forme` (disposition ou masque), ou None."""
    try:
        return forme._base_placeholder if forme.is_placeholder else None
    except Exception:  # noqa: BLE001
        return None


def chaine_heritage(forme, masque) -> list:
    """Liste des éléments XML a:lstStyle ou style de masque à consulter, du plus proche au plus lointain."""
    chaine = []
    try:
        if forme.has_text_frame:
            lst = forme._element.find(".//p:txBody/a:lstStyle", NS)
            if lst is not None:
                chaine.append(lst)
    except Exception:  # noqa: BLE001
        pass
    base = placeholder_de_base(forme)
    genre_ph = None
    try:
        genre_ph = forme.placeholder_format.type if forme.is_placeholder else None
    except Exception:  # noqa: BLE001
        pass
    while base is not None:
        try:
            lst = base._element.find(".//p:txBody/a:lstStyle", NS)
            if lst is not None:
                chaine.append(lst)
        except Exception:  # noqa: BLE001
            pass
        base = placeholder_de_base(base)
    try:
        styles = masque._element.find("p:txStyles", NS)
        if styles is not None:
            if genre_ph is None:
                style = styles.find("p:otherStyle", NS)
            elif genre_ph in (1, 3):  # TITLE, CENTER_TITLE
                style = styles.find("p:titleStyle", NS)
            else:
                style = styles.find("p:bodyStyle", NS)
            if style is not None:
                chaine.append(style)
    except Exception:  # noqa: BLE001
        pass
    return chaine


def propriete_niveau(chaine: list, niveau: int, chemin: str, pPr_local=None):
    """Première valeur trouvée pour `chemin` (relatif à a:lvlNpPr) dans le pPr local puis la chaîne."""
    if pPr_local is not None:
        trouve = xpath(pPr_local, chemin)
        if trouve:
            return trouve[0]
    for lst in chaine:
        lvl = lst.find(f"a:lvl{niveau + 1}pPr", NS)
        if lvl is None:
            continue
        trouve = xpath(lvl, chemin)
        if trouve:
            return trouve[0]
    return None


def genre_placeholder(forme) -> str:
    try:
        if forme.is_placeholder:
            return "titre" if forme.placeholder_format.type in (1, 3) else "corps"
    except Exception:  # noqa: BLE001
        pass
    return "autre"


def taille_defaut(ctx: Contexte, chaine: list, forme, niveau: int, pPr) -> int:
    """Taille de police par défaut (px) d'un paragraphe de niveau donné."""
    sz = propriete_niveau(chaine, niveau, "a:defRPr/@sz", pPr)
    if sz is not None:
        return ctx.px(Emu(int(sz) * 127))
    return ctx.px(Pt(TAILLES_DEFAUT_PT[genre_placeholder(forme)]))


def type_puce(chaine: list, niveau: int, pPr) -> str | None:
    """'ul', 'ol' ou None selon a:buChar / a:buAutoNum / a:buNone (local puis hérité)."""
    sources = [pPr] if pPr is not None else []
    for lst in chaine:
        lvl = lst.find(f"a:lvl{niveau + 1}pPr", NS)
        if lvl is not None:
            sources.append(lvl)
    for source in sources:
        for enfant in source:
            nom = etree.QName(enfant).localname
            if nom == "buNone":
                return None
            if nom == "buChar" or nom == "buBlip":
                return "ul"
            if nom == "buAutoNum":
                return "ol"
    return None


def alignement(chaine: list, niveau: int, pPr) -> str | None:
    algn = propriete_niveau(chaine, niveau, "@algn", pPr)
    return {"l": "left", "ctr": "center", "r": "right", "just": "justify", "dist": "justify"}.get(algn)


def retraits(ctx: Contexte, chaine: list, niveau: int, pPr) -> list[str]:
    """Retrait gauche (a:pPr/@marL → padding-left) et retrait de première ligne (@indent → text-indent)
    d'un paragraphe sans puce, locaux ou hérités de a:lstStyle / du masque."""
    props = []
    marL = propriete_niveau(chaine, niveau, "@marL", pPr)
    if marL is not None and int(marL):
        props.append(f"padding-left:{ctx.px(Emu(int(marL)))}px")
    indent = propriete_niveau(chaine, niveau, "@indent", pPr)
    if indent is not None and int(indent):
        props.append(f"text-indent:{ctx.px(Emu(int(indent)))}px")
    return props


def body_pr_herite(forme, chemin: str):
    """Attribut/élément de a:bodyPr sur la forme puis ses placeholders de base."""
    courant = forme
    while courant is not None:
        try:
            body = courant._element.find(".//p:txBody/a:bodyPr", NS)
            if body is not None:
                trouve = xpath(body, chemin)
                if trouve:
                    return trouve[0]
        except Exception:  # noqa: BLE001
            pass
        courant = placeholder_de_base(courant)
    return None


# --------------------------------------------------------------------------------------------------
# Texte
# --------------------------------------------------------------------------------------------------


def style_run(ctx: Contexte, theme: dict[str, str], rPr, echelle: float) -> str:
    """Propriétés CSS explicitement définies sur un a:rPr (ou a:endParaRPr)."""
    if rPr is None:
        return ""
    props = []
    sz = rPr.get("sz")
    if sz:
        props.append(f"font-size:{round(ctx.px(Emu(int(sz) * 127)) * echelle)}px")
    if rPr.get("b") == "1":
        props.append("font-weight:700")
    elif rPr.get("b") == "0":
        props.append("font-weight:400")
    if rPr.get("i") == "1":
        props.append("font-style:italic")
    u = rPr.get("u")
    if u and u != "none":
        props.append("text-decoration:underline")
    if rPr.get("strike") in ("sngStrike", "dblStrike"):
        props.append("text-decoration:line-through")
    couleur = couleur_element(ctx, theme, rPr.find("a:solidFill", NS))
    if couleur:
        props.append(f"color:{couleur}")
    latin = rPr.find("a:latin", NS)
    latin_nom = latin.get("typeface", "") if latin is not None else ""
    # Un nom de police sans lettre (« 22 ») est une valeur aberrante : on ne la reprend pas.
    if latin_nom and not latin_nom.startswith("+") and re.search(r"[A-Za-z]", latin_nom):
        props.append(f"font-family:'{latin.get('typeface')}'")
    return ";".join(props)


def contenu_paragraphe(ctx: Contexte, theme: dict[str, str], p, echelle: float) -> str:
    """HTML interne d'un a:p : runs en <span style>, a:br en <br>, champs (a:fld) comme des runs."""
    parties = []
    for enfant in p:
        nom = etree.QName(enfant).localname
        if nom in ("r", "fld"):
            t = enfant.find("a:t", NS)
            texte = t.text if t is not None and t.text else ""
            if not texte:
                continue
            style = style_run(ctx, theme, enfant.find("a:rPr", NS), echelle)
            texte_html = html.escape(texte)
            lien = enfant.find("a:rPr/a:hlinkClick", NS)
            if lien is not None and lien.get(qn("r:id")):
                try:
                    url = ctx._part_courante.rels[lien.get(qn("r:id"))].target_ref
                    texte_html = f'<a href="{attr(url)}">{texte_html}</a>'
                except Exception:  # noqa: BLE001
                    pass
            parties.append(f'<span style="{attr(style)}">{texte_html}</span>' if style else texte_html)
        elif nom == "br":
            parties.append("<br>")
    return "".join(parties)


def paragraphes_html(ctx: Contexte, forme, masque, fragments_par: dict[int, tuple[str, int]] | None,
                     indent: str) -> list[str]:
    """Lignes HTML du texte d'une forme : <p> et listes <ul>/<ol> imbriquées selon le niveau.

    `fragments_par` associe un index de paragraphe à (classes fragment, data-fragment-index).
    """
    theme = theme_du_masque(ctx, masque)
    chaine = chaine_heritage(forme, masque)
    txBody = forme._element.find(".//p:txBody", NS)
    if txBody is None:
        return []
    echelle = 1.0
    autofit = txBody.find("a:bodyPr/a:normAutofit", NS)
    if autofit is not None and autofit.get("fontScale"):
        echelle = int(autofit.get("fontScale")) / 100000
    lignes: list[str] = []
    pile: list[tuple[str, bool]] = []  # listes ouvertes par niveau : (balise, ouverte dans un <li> parent)
    fragments_par = fragments_par or {}

    def fermer_jusqua(niveau: int):
        while len(pile) > niveau:
            balise, dans_li = pile.pop()
            lignes.append(indent + "  " * len(pile) + f"</{balise}>" + ("</li>" if dans_li else ""))

    def ouvrir(balise: str):
        dans_li = bool(pile) and lignes and lignes[-1].endswith("</li>")
        if dans_li:
            lignes[-1] = lignes[-1][: -len("</li>")]  # la sous-liste s'imbrique dans le dernier <li>
        lignes.append(indent + "  " * len(pile) + f"<{balise}>")
        pile.append((balise, dans_li))

    for i, p in enumerate(txBody.findall("a:p", NS)):
        pPr = p.find("a:pPr", NS)
        niveau = int(pPr.get("lvl", "0")) if pPr is not None else 0
        contenu = contenu_paragraphe(ctx, theme, p, echelle)
        puce = type_puce(chaine, niveau, pPr) if contenu.strip() else None
        props = []
        taille = taille_defaut(ctx, chaine, forme, niveau, pPr)
        props.append(f"font-size:{round(taille * echelle)}px")
        algn = alignement(chaine, niveau, pPr)
        if algn:
            props.append(f"text-align:{algn}")
        if not puce:
            props += retraits(ctx, chaine, niveau, pPr)
        fragment = fragments_par.get(i)
        attributs = f' style="{attr(";".join(props))}"'
        if fragment:
            classes, index = fragment
            attributs = f' class="{attr(classes)}" data-fragment-index="{index}"' + attributs
        if puce:
            fermer_jusqua(niveau + 1)
            while len(pile) < niveau + 1:
                ouvrir(puce)
            lignes.append(indent + "  " * len(pile) + f"<li{attributs}>{contenu}</li>")
        else:
            fermer_jusqua(0)
            lignes.append(indent + f"<p{attributs}>{contenu or '&nbsp;'}</p>")
    fermer_jusqua(0)
    return lignes


def style_zone_texte(ctx: Contexte, forme) -> list[str]:
    """Alignement vertical, marges internes et retour à la ligne de la zone de texte."""
    props = []
    anchor = body_pr_herite(forme, "@anchor")
    if anchor in ("ctr", "b"):
        props.append("display:flex;flex-direction:column;justify-content:" + ("center" if anchor == "ctr" else "flex-end"))
    marges = []
    for nom, defaut in (("tIns", 45720), ("rIns", 91440), ("bIns", 45720), ("lIns", 91440)):
        valeur = body_pr_herite(forme, f"@{nom}")
        marges.append(ctx.px(Emu(int(valeur) if valeur is not None else defaut)))
    if any(marges):
        props.append("padding:" + " ".join(f"{m}px" for m in marges) + ";box-sizing:border-box")
    if body_pr_herite(forme, "@wrap") == "none":
        props.append("white-space:nowrap")
    return props


# --------------------------------------------------------------------------------------------------
# Géométrie
# --------------------------------------------------------------------------------------------------


class Repere:
    """Transformation affine (échelle + décalage, en EMU) du repère d'un groupe vers la diapositive."""

    def __init__(self, sx=1.0, sy=1.0, dx=0.0, dy=0.0):
        self.sx, self.sy, self.dx, self.dy = sx, sy, dx, dy

    def point(self, x, y):
        return self.dx + x * self.sx, self.dy + y * self.sy

    def taille(self, w, h):
        return w * self.sx, h * self.sy

    def compose(self, groupe) -> "Repere":
        """Repère des enfants d'un groupe (xfrm off/ext vs chOff/chExt), exprimé dans la diapositive."""
        xfrm = groupe._element.find("p:grpSpPr/a:xfrm", NS)
        if xfrm is None:
            return self
        off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
        choff, chext = xfrm.find("a:chOff", NS), xfrm.find("a:chExt", NS)
        if off is None or ext is None or choff is None or chext is None:
            return self
        cw, ch = int(chext.get("cx")), int(chext.get("cy"))
        sx = int(ext.get("cx")) / cw if cw else 1.0
        sy = int(ext.get("cy")) / ch if ch else 1.0
        dx = int(off.get("x")) - int(choff.get("x")) * sx
        dy = int(off.get("y")) - int(choff.get("y")) * sy
        # Composition avec le repère parent : parent(dx + x·sx) = parent.dx + (dx + x·sx)·parent.sx
        return Repere(self.sx * sx, self.sy * sy, self.dx + dx * self.sx, self.dy + dy * self.sy)


def geometrie(ctx: Contexte, forme, repere: Repere) -> list[str] | None:
    """Propriétés CSS de position/taille/rotation, ou None si la forme n'a pas de position connue."""
    left, top, width, height = forme.left, forme.top, forme.width, forme.height
    if left is None or top is None:
        return None
    x, y = repere.point(int(left), int(top))
    w, h = repere.taille(int(width or 0), int(height or 0))
    props = [
        f"left:{round(x * ctx.facteur + ctx.decalage_x)}px",
        f"top:{round(y * ctx.facteur + ctx.decalage_y)}px",
        f"width:{ctx.px(w)}px",
        f"height:{ctx.px(h)}px",
    ]
    try:
        rotation = forme.rotation
    except Exception:  # noqa: BLE001
        rotation = 0
    if rotation:
        props.append(f"transform:rotate({rotation:g}deg)")
    return props


# --------------------------------------------------------------------------------------------------
# Formes
# --------------------------------------------------------------------------------------------------


def genre_forme(forme) -> str:
    """'image', 'groupe', 'tableau', 'graphique', 'graphicFrame', 'connecteur', 'ligne', 'texte' ou 'forme'."""
    nom = etree.QName(forme._element).localname
    if nom == "pic":
        return "image"
    if nom == "grpSp":
        return "groupe"
    if nom == "graphicFrame":
        if getattr(forme, "has_table", False):
            return "tableau"
        if getattr(forme, "has_chart", False):
            return "graphique"
        return "graphicFrame"
    if nom == "cxnSp":
        return "connecteur"
    if nom == "sp":
        prst = forme._element.find("p:spPr/a:prstGeom", NS)
        if prst is not None and prst.get("prst") in ("line", "straightConnector1", "bentConnector3", "curvedConnector3"):
            return "ligne"
        spPr = forme._element.find("p:spPr", NS)
        a_fond = spPr is not None and any(spPr.find(b, NS) is not None for b in ("a:solidFill", "a:gradFill", "a:blipFill", "a:pattFill"))
        ln = spPr.find("a:ln", NS) if spPr is not None else None
        a_contour = ln is not None and ln.find("a:noFill", NS) is None and len(ln) > 0
        a_style_forme = forme._element.find("p:style", NS) is not None and not forme.is_placeholder
        if a_fond or a_contour or a_style_forme:
            return "forme"
        return "texte"
    return nom


def style_remplissage(ctx: Contexte, theme: dict[str, str], forme) -> tuple[list[str], list[str]]:
    """(propriétés CSS, commentaires) pour le fond et le contour d'une forme automatique."""
    props: list[str] = []
    commentaires: list[str] = []
    spPr = forme._element.find("p:spPr", NS)
    try:
        type_fill = forme.fill.type
    except Exception:  # noqa: BLE001
        type_fill = None
    if type_fill == MSO_FILL.SOLID:
        couleur = couleur_format(ctx, theme, forme.fill.fore_color)
        if couleur:
            props.append(f"background:{couleur}")
    elif type_fill == MSO_FILL.GRADIENT:
        stops = spPr.findall("a:gradFill/a:gsLst/a:gs", NS) if spPr is not None else []
        couleurs = [couleur_element(ctx, theme, s) for s in stops]
        couleurs = [c for c in couleurs if c]
        if len(couleurs) >= 2:
            props.append(f"background:linear-gradient(180deg,{couleurs[0]},{couleurs[-1]})")
        elif couleurs:
            props.append(f"background:{couleurs[0]}")
        commentaires.append("dégradé approximé")
    elif type_fill == MSO_FILL.PICTURE:
        blip = spPr.find("a:blipFill/a:blip", NS) if spPr is not None else None
        rid = blip.get(qn("r:embed")) if blip is not None else None
        if rid:
            try:
                part = forme.part.related_part(rid)
                chemin = ctx.nouvelle_image(part.blob, part.ext if hasattr(part, "ext") else "png")
                props.append(f"background:url('{chemin}') center/cover no-repeat")
            except Exception as exc:  # noqa: BLE001
                commentaires.append(f"remplissage image non extrait ({exc})")
    elif type_fill == MSO_FILL.PATTERNED:
        couleur = couleur_format(ctx, theme, forme.fill.fore_color)
        if couleur:
            props.append(f"background:{couleur}")
        commentaires.append("motif approximé en couleur pleine")
    elif type_fill is None:
        # Pas de remplissage explicite : style de forme du thème (p:style/a:fillRef).
        ref = forme._element.find("p:style/a:fillRef", NS)
        couleur = couleur_element(ctx, theme, ref) if ref is not None and ref.get("idx", "0") != "0" else None
        if couleur:
            props.append(f"background:{couleur}")
    # Contour
    ln = spPr.find("a:ln", NS) if spPr is not None else None
    if ln is not None and ln.find("a:noFill", NS) is None:
        largeur = int(ln.get("w", "9525"))
        couleur = couleur_element(ctx, theme, ln.find("a:solidFill", NS))
        if couleur is None and len(ln) == 0:
            ref = forme._element.find("p:style/a:lnRef", NS)
            couleur = couleur_element(ctx, theme, ref) if ref is not None and ref.get("idx", "0") != "0" else None
        if couleur:
            props.append(f"border:{max(1, ctx.px(largeur))}px solid {couleur}")
    elif ln is None:
        ref = forme._element.find("p:style/a:lnRef", NS)
        couleur = couleur_element(ctx, theme, ref) if ref is not None and ref.get("idx", "0") != "0" else None
        if couleur:
            props.append(f"border:{max(1, ctx.px(9525))}px solid {couleur}")
    # Couleur de texte par défaut du style de forme (p:style/a:fontRef)
    ref = forme._element.find("p:style/a:fontRef", NS)
    couleur = couleur_element(ctx, theme, ref) if ref is not None else None
    if couleur:
        props.append(f"color:{couleur}")
    # Rayon
    try:
        genre = forme.auto_shape_type
    except Exception:  # noqa: BLE001
        genre = None
    if genre == MSO_SHAPE.OVAL:
        props.append("border-radius:50%")
    elif genre in (MSO_SHAPE.ROUNDED_RECTANGLE, MSO_SHAPE.ROUND_1_RECTANGLE, MSO_SHAPE.ROUND_2_SAME_RECTANGLE,
                   MSO_SHAPE.ROUND_2_DIAG_RECTANGLE):
        try:
            ratio = forme.adjustments[0] if len(forme.adjustments) else 0.16667
        except Exception:  # noqa: BLE001
            ratio = 0.16667
        base = min(int(forme.width or 0), int(forme.height or 0))
        props.append(f"border-radius:{ctx.px(base * ratio)}px")
    elif genre is not None and genre not in (MSO_SHAPE.RECTANGLE,):
        commentaires.append(f"forme {genre.name.lower()} rendue en rectangle")
    return props, commentaires


def balise_el(balise: str, classes: str, ident: str, style: list[str], fragment: tuple[str, int] | None,
              extras: str = "") -> str:
    """Balise ouvrante d'un `.el`, avec classes de fragment et data-fragment-index le cas échéant."""
    if fragment:
        classes = f"{classes} {fragment[0]}".strip()
        extras = f' data-fragment-index="{fragment[1]}"' + extras
    return f'<{balise} class="{attr(classes)}"{extras} data-id="{ident}" style="{attr(";".join(style))}"'


def forme_vers_html(ctx: Contexte, forme, masque, repere: Repere, anims: "Animations", ids: list[int],
                    indent: str) -> list[str]:
    """Lignes HTML d'une forme (récursif pour les groupes). Toute erreur devient un commentaire."""
    try:
        return _forme_vers_html(ctx, forme, masque, repere, anims, ids, indent)
    except Exception as exc:  # noqa: BLE001
        nom = getattr(forme, "name", "?")
        return [indent + ctx.avertir(f"forme « {nom} » : {type(exc).__name__} : {exc}")]


def _forme_vers_html(ctx, forme, masque, repere, anims, ids, indent) -> list[str]:
    genre = genre_forme(forme)
    nom = forme.name or ""
    raster = ctx.rasters.get((ctx.diapo, forme.shape_id)) if ctx.rasters else None
    if raster is not None:
        chemin, (left, top, width, height), _ = raster
        ids[0] += 1
        style = [f"left:{left}px", f"top:{top}px", f"width:{width}px", f"height:{height}px"]
        return [indent + f"<!-- rastérisé : {echapper_commentaire(nom)} -->",
                indent + balise_el("img", "el image", f"e{ids[0]}", style, anims.fragment_forme(forme.shape_id),
                                   f' data-raster="1" src="{attr(chemin)}" alt="{attr(nom)}"') + ">"]
    if genre == "groupe":
        lignes = []
        sous_repere = repere.compose(forme)
        if forme.rotation:
            lignes.append(indent + ctx.avertir(f"groupe « {nom} » : rotation du groupe ignorée"))
        for enfant in forme.shapes:
            lignes.extend(forme_vers_html(ctx, enfant, masque, sous_repere, anims.herite(forme.shape_id, enfant.shape_id), ids, indent))
        return lignes
    if genre in ("connecteur", "ligne"):
        return [indent + ctx.avertir(f"ligne/connecteur « {nom} » non rastérisé : ignoré")]

    position = geometrie(ctx, forme, repere)
    if position is None:
        return [indent + ctx.avertir(f"forme « {nom} » sans position connue")]
    ids[0] += 1
    ident = f"e{ids[0]}"
    fragment_forme = anims.fragment_forme(forme.shape_id)
    theme = theme_du_masque(ctx, masque)
    ctx._part_courante = forme.part

    if genre == "image":
        return image_vers_html(ctx, forme, position, ident, fragment_forme, indent)

    if genre == "tableau":
        return tableau_vers_html(ctx, forme, theme, position, ident, fragment_forme, indent)

    if genre == "graphique":
        style = position + ["border:2px dashed #999", "display:flex", "align-items:center", "justify-content:center"]
        return [indent + ctx.avertir(f"graphique « {nom} » non converti (zone de remplacement)"),
                indent + balise_el("div", "el forme", ident, style, fragment_forme) + ">[graphique]</div>"]

    if genre == "graphicFrame":
        uri = forme._element.find(".//a:graphicData", NS)
        uri = uri.get("uri", "?") if uri is not None else "?"
        ids[0] -= 1
        return [indent + ctx.avertir(f"objet « {nom} » non converti ({uri.rsplit('/', 1)[-1]} : SmartArt/OLE ?)")]

    # Texte ou forme automatique
    a_texte = forme.has_text_frame and forme.text_frame.text.strip() != ""
    style = list(position)
    commentaires: list[str] = []
    if genre == "forme":
        props, commentaires = style_remplissage(ctx, theme, forme)
        style += props
        if not props and not a_texte:
            ids[0] -= 1
            return [indent + f"<!-- forme « {echapper_commentaire(nom)} » vide ignorée -->"]
    elif not a_texte:
        ids[0] -= 1
        return []  # placeholder vide ou zone de texte vide : PowerPoint ne l'affiche pas
    if a_texte:
        style += style_zone_texte(ctx, forme)
    classes = "el forme" if genre == "forme" else "el texte"
    lignes = [indent + f"<!-- {echapper_commentaire(c)} -->" for c in commentaires]
    fragments_par = anims.fragments_paragraphes(forme.shape_id, nombre_paragraphes(forme))
    if fragments_par:
        fragment_forme = None  # l'animation est portée par les paragraphes : la zone reste visible
    ouverture = indent + balise_el("div", classes, ident, style, fragment_forme) + ">"
    if not a_texte:
        return lignes + [ouverture + "</div>"]
    lignes.append(ouverture)
    lignes.extend(paragraphes_html(ctx, forme, masque, fragments_par, indent + "  "))
    lignes.append(indent + "</div>")
    return lignes


def nombre_paragraphes(forme) -> int:
    try:
        return len(forme.text_frame.paragraphs)
    except Exception:  # noqa: BLE001
        return 0


def image_vers_html(ctx, forme, position, ident, fragment, indent) -> list[str]:
    lignes = []
    nvPr = forme._element.find("p:nvPicPr/p:nvPr", NS)
    video = nvPr.find("a:videoFile", NS) if nvPr is not None else None
    if video is not None:
        rid = video.get(qn("r:link"))
        try:
            rel = forme.part.rels[rid]
            if rel.is_external:
                raise ValueError("vidéo liée (externe)")
            part = rel.target_part
            ext = Path(str(part.partname)).suffix or ".mp4"
            ctx.compteur_images += 1
            chemin = ctx.ecrire_media(f"s{ctx.diapo}_{ctx.compteur_images}{ext}", part.blob)
            return [indent + balise_el("video", "el video", ident, position, fragment,
                                       f' src="{attr(chemin)}" data-amphi="video" controls') + "></video>"]
        except Exception as exc:  # noqa: BLE001
            lignes.append(indent + ctx.avertir(f"vidéo « {forme.name} » non extraite ({exc}) ; affichage de l'affiche"))
    try:
        image = forme.image
    except Exception as exc:  # noqa: BLE001
        return lignes + [indent + ctx.avertir(f"image « {forme.name} » illisible ({exc})")]
    chemin = ctx.nouvelle_image(image.blob, image.ext)
    rognage = [getattr(forme, c, 0) or 0 for c in ("crop_left", "crop_top", "crop_right", "crop_bottom")]
    if any(abs(r) > 0.001 for r in rognage):
        lignes.append(indent + "<!-- rognage PowerPoint ignoré (gauche/haut/droite/bas : "
                      + ", ".join(f"{r:.0%}" for r in rognage) + ") -->")
    lignes.append(indent + balise_el("img", "el image", ident, position, fragment,
                                     f' src="{attr(chemin)}" alt="{attr(forme.name or "")}"') + ">")
    return lignes


def style_cellule(ctx: Contexte, theme: dict[str, str], cellule) -> str:
    """CSS d'une cellule de tableau : fond uni explicite (a:tcPr/a:solidFill), bordures, alignement vertical."""
    tcPr = cellule._tc.find("a:tcPr", NS)
    props = ["padding:4px 8px"]
    bordures = {}
    if tcPr is not None:
        couleur = couleur_element(ctx, theme, tcPr.find("a:solidFill", NS))
        if couleur:
            props.append(f"background:{couleur}")
        for cote, css in (("lnL", "left"), ("lnR", "right"), ("lnT", "top"), ("lnB", "bottom")):
            ln = tcPr.find(f"a:{cote}", NS)
            if ln is None:
                continue
            if ln.find("a:noFill", NS) is not None:
                bordures[css] = "none"
            else:
                couleur_ln = couleur_element(ctx, theme, ln.find("a:solidFill", NS)) or "#888"
                bordures[css] = f"{max(1, ctx.px(int(ln.get('w', '9525'))))}px solid {couleur_ln}"
        anchor = tcPr.get("anchor")
        if anchor in ("t", "ctr", "b"):
            props.append("vertical-align:" + {"t": "top", "ctr": "middle", "b": "bottom"}[anchor])
    if bordures and len(set(bordures.values())) == 1 and len(bordures) == 4:
        props.append(f"border:{next(iter(bordures.values()))}")
    else:
        props.append("border:1px solid #888")
        props += [f"border-{cote}:{valeur}" for cote, valeur in bordures.items()]
    return ";".join(props)


def tableau_vers_html(ctx, forme, theme, position, ident, fragment, indent) -> list[str]:
    table = forme.table
    lignes = [indent + balise_el("div", "el texte", ident, position, fragment) + ">",
              indent + '  <table style="width:100%;height:100%;border-collapse:collapse">']
    for rangee in table.rows:
        lignes.append(indent + "    <tr>")
        for cellule in rangee.cells:
            if cellule.is_spanned:
                continue
            extras = ""
            if cellule.span_width > 1:
                extras += f' colspan="{cellule.span_width}"'
            if cellule.span_height > 1:
                extras += f' rowspan="{cellule.span_height}"'
            contenu = []
            for p in cellule.text_frame._txBody.findall("a:p", NS):
                contenu.append(contenu_paragraphe(ctx, theme, p, 1.0) or "")
            lignes.append(indent + f'      <td{extras} style="{attr(style_cellule(ctx, theme, cellule))}">' + "<br>".join(contenu) + "</td>")
        lignes.append(indent + "    </tr>")
    lignes += [indent + "  </table>", indent + "</div>"]
    return lignes


# --------------------------------------------------------------------------------------------------
# Rastérisation des objets complexes via LibreOffice
# --------------------------------------------------------------------------------------------------


def forme_automatique(forme):
    """Type MSO_SHAPE d'une forme automatique (prstGeom), ou None (forme libre, image, autre)."""
    try:
        return forme.auto_shape_type
    except Exception:  # noqa: BLE001
        return None


def rotation_de(forme) -> float:
    try:
        return float(forme.rotation or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def groupe_simple(groupe) -> bool:
    """Vrai si le groupe n'est pas tourné et ne contient que des zones de texte, des images et des
    rectangles/ovales non tournés : il reste converti forme par forme (texte éditable)."""
    if rotation_de(groupe):
        return False
    for enfant in groupe.shapes:
        nom = etree.QName(enfant._element).localname
        if nom == "pic":
            continue
        if nom != "sp":
            return False
        if enfant._element.find("p:spPr/a:custGeom", NS) is not None:
            return False
        if forme_automatique(enfant) not in FORMES_SIMPLES or rotation_de(enfant):
            return False
    return True


def est_ligne(forme) -> bool:
    """Connecteur (p:cxnSp) ou forme automatique de type ligne."""
    return genre_forme(forme) in ("connecteur", "ligne")


def marge_raster(ctx: Contexte, forme) -> int:
    """Marge (px) autour de la boîte englobante : élargie pour une ligne selon son épaisseur (pointes de flèche)."""
    if not est_ligne(forme):
        return MARGE_RASTER
    ln = forme._element.find("p:spPr/a:ln", NS)
    largeur = int(ln.get("w", "9525")) if ln is not None else 9525
    return max(MARGE_RASTER, math.ceil(ctx.px(largeur) * 1.5) + 2)


def est_complexe(forme) -> bool:
    """Objet à rastériser : groupe (sauf groupe simple), forme libre, forme automatique non rectangle/ovale,
    ligne ou connecteur, SmartArt, graphique, OLE et tout graphicFrame autre qu'un tableau."""
    nom = etree.QName(forme._element).localname
    if nom == "grpSp":
        return not groupe_simple(forme)
    if nom == "graphicFrame":
        return not getattr(forme, "has_table", False)
    if nom == "cxnSp":
        return True
    if nom != "sp":
        return False
    if genre_forme(forme) == "ligne":
        return True
    if forme._element.find("p:spPr/a:custGeom", NS) is not None:
        return True
    genre = forme_automatique(forme)
    return genre is not None and genre not in FORMES_SIMPLES


def points_forme(forme, repere: Repere) -> list[tuple[float, float]]:
    """Coins (EMU, repère de la diapositive) d'une forme et de ses enfants, rotations comprises."""
    left, top, width, height = forme.left, forme.top, forme.width, forme.height
    if left is None or top is None:
        return []
    x, y = repere.point(int(left), int(top))
    w, h = repere.taille(int(width or 0), int(height or 0))
    points = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if etree.QName(forme._element).localname == "grpSp":
        sous_repere = repere.compose(forme)
        for enfant in forme.shapes:
            points.extend(points_forme(enfant, sous_repere))
    angle = math.radians(rotation_de(forme))
    if angle:
        cx, cy = x + w / 2, y + h / 2
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        points = [(cx + (px - cx) * cos_a - (py - cy) * sin_a, cy + (px - cx) * sin_a + (py - cy) * cos_a)
                  for px, py in points]
    return points


def boite_scene(ctx: Contexte, forme) -> tuple[int, int, int, int] | None:
    """Boîte englobante (x0, y0, x1, y1) en px de scène, avec la marge (au moins TAILLE_MIN_RASTER de côté),
    ou None sans position connue."""
    points = points_forme(forme, Repere())
    if not points:
        return None
    marge = marge_raster(ctx, forme)
    xs = [p[0] * ctx.facteur + ctx.decalage_x for p in points]
    ys = [p[1] * ctx.facteur + ctx.decalage_y for p in points]
    x0, y0 = math.floor(min(xs)) - marge, math.floor(min(ys)) - marge
    x1, y1 = math.ceil(max(xs)) + marge, math.ceil(max(ys)) + marge
    if x1 - x0 < TAILLE_MIN_RASTER:
        manque = TAILLE_MIN_RASTER - (x1 - x0)
        x0, x1 = x0 - manque // 2, x1 + (manque - manque // 2)
    if y1 - y0 < TAILLE_MIN_RASTER:
        manque = TAILLE_MIN_RASTER - (y1 - y0)
        y0, y1 = y0 - manque // 2, y1 + (manque - manque // 2)
    return (x0, y0, x1, y1)


def exporter_pdf(pptx: Path, dossier: Path, soffice: str) -> Path:
    """Convertit `pptx` en PDF dans `dossier` avec LibreOffice (profil utilisateur temporaire)."""
    profil = (dossier / "profil").resolve()
    commande = [soffice, "--headless", "--norestore", "--nologo", f"-env:UserInstallation={profil.as_uri()}",
                "--convert-to", "pdf", "--outdir", str(dossier), str(pptx)]
    resultat = subprocess.run(commande, capture_output=True, text=True, timeout=600, check=False)
    pdf = dossier / (pptx.stem + ".pdf")
    if resultat.returncode != 0 or not pdf.exists():
        raise RuntimeError(f"soffice a échoué (code {resultat.returncode}) : {(resultat.stderr or resultat.stdout).strip()[:300]}")
    return pdf


def matter(np, rvb1, rvb2):
    """Découpe RGBA à partir de deux rendus du même objet sur les deux fonds de chroma key.

    P = α·F + (1 − α)·C pour chaque fond C : α = 1 − |P₁ − P₂| / |C₁ − C₂|, F = (P₁ − (1 − α)·C₁) / α.
    Ombres, anticrénelage et remplissages translucides deviennent de la transparence partielle. Les pixels
    à moins de SEUIL_TRANSPARENCE du premier chroma key sont transparents dans tous les cas, et eux seuls
    le sont quand les deux rendus ne concordent pas (rendu sur un seul fond)."""
    rvb1 = rvb1.astype(np.float32)
    c1 = np.array(CHROMA_KEY, dtype=np.float32)
    distance = np.sqrt(((rvb1 - c1) ** 2).sum(axis=2))
    transparent = distance < SEUIL_TRANSPARENCE
    h, w = transparent.shape
    if rvb2 is None or rvb2.shape != rvb1.shape:
        alpha = np.where(transparent, 0.0, 1.0)
        couleur = rvb1
    else:
        c2 = np.array(CHROMA_KEY_2, dtype=np.float32)
        ecart = np.sqrt(((rvb1 - rvb2.astype(np.float32)) ** 2).sum(axis=2))
        alpha = np.clip(1.0 - ecart / float(np.sqrt(((c1 - c2) ** 2).sum())), 0.0, 1.0)
        alpha = np.where(alpha > 0.97, 1.0, alpha)  # bruit de rendu : pixel franchement opaque
        alpha = np.where(transparent | (alpha < 0.03), 0.0, alpha)
        couleur = np.where((alpha > 0)[:, :, None],
                           np.clip((rvb1 - (1 - alpha)[:, :, None] * c1) / np.maximum(alpha, 1e-3)[:, :, None], 0, 255),
                           rvb1)
    decoupe = np.empty((h, w, 4), dtype=np.uint8)
    decoupe[:, :, :3] = couleur.astype(np.uint8)
    decoupe[:, :, 3] = np.round(alpha * 255).astype(np.uint8)
    return decoupe


def rasteriser(ctx: Contexte, pptx: Path, soffice: str) -> None:
    """Deux passages LibreOffice pour toute la présentation : copies du .pptx réduites aux objets complexes
    sur chacun des deux fonds de chroma key, export PDF, rendu de chaque page à l'échelle de la scène et
    découpe de chaque objet en PNG avec sa transparence."""
    try:
        import numpy as np
        from PIL import Image

        try:
            import pymupdf
        except ImportError:  # noqa: SIM105
            import fitz as pymupdf
    except ImportError as exc:
        ctx.avertir(f"rastérisation impossible ({exc}) : objets complexes ignorés")
        return
    from pptx.dml.color import RGBColor

    cibles: dict[int, list[tuple[int, str, tuple[int, int, int, int]]]] = {}
    for numero, slide in enumerate(ctx.prs.slides, start=1):
        ctx.diapo = numero
        for forme in slide.shapes:
            try:
                if not est_complexe(forme):
                    continue
                boite = boite_scene(ctx, forme)
                if boite is None:
                    continue
                cibles.setdefault(numero, []).append((forme.shape_id, forme.name or "", boite))
            except Exception as exc:  # noqa: BLE001
                ctx.avertir(f"objet « {getattr(forme, 'name', '?')} » : analyse impossible ({exc})")
    ctx.diapo = 0
    if not cibles:
        return
    with tempfile.TemporaryDirectory(prefix="pptx2reveal-") as tmp:
        dossier = Path(tmp)
        docs = []
        prs2 = None
        for suffixe, chroma in (("a", CHROMA_KEY), ("b", CHROMA_KEY_2)):
            copie = dossier / f"raster_{suffixe}.pptx"
            prs2 = Presentation(str(pptx))
            for numero, slide in enumerate(prs2.slides, start=1):
                garder = {sid for sid, _, _ in cibles.get(numero, [])}
                for forme in list(slide.shapes):
                    if forme.shape_id not in garder:
                        el = forme._element
                        el.getparent().remove(el)
                slide._element.attrib.pop("show", None)  # les diapositives masquées doivent être exportées aussi
                # Fond de chroma key explicite (il masque aussi un fond image hérité de la disposition/du masque).
                slide.background.fill.solid()
                slide.background.fill.fore_color.rgb = RGBColor(*chroma)
            prs2.save(str(copie))
            try:
                docs.append(pymupdf.open(str(exporter_pdf(copie, dossier, soffice))))
            except Exception as exc:  # noqa: BLE001
                if not docs:
                    ctx.avertir(f"rastérisation impossible ({exc}) : objets complexes ignorés")
                    return
                ctx.avertir(f"second rendu LibreOffice impossible ({exc}) : transparence par simple chroma key")
        doc = docs[0]
        nb_pages = doc.page_count
        nb_diapos = len(prs2.slides._sldIdLst)
        if nb_pages != nb_diapos:
            ctx.avertir(f"PDF LibreOffice de {nb_pages} page(s) pour {nb_diapos} diapositive(s) : rastérisation partielle")
        largeur_scene = ctx.largeur * ctx.facteur
        for numero, liste in sorted(cibles.items()):
            if numero > nb_pages:
                break
            ctx.diapo = numero
            page = doc[numero - 1]
            echelle = largeur_scene / page.rect.width
            matrice = pymupdf.Matrix(echelle, echelle)
            pix = page.get_pixmap(matrix=matrice, alpha=False)
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
            image2 = None
            if len(docs) > 1 and numero <= docs[1].page_count:
                pix2 = docs[1][numero - 1].get_pixmap(matrix=matrice, alpha=False)
                image2 = np.frombuffer(pix2.samples, dtype=np.uint8).reshape(pix2.height, pix2.width, pix2.n)[:, :, :3]
                if image2.shape != image.shape:
                    image2 = None
            for n, (sid, nom, (x0, y0, x1, y1)) in enumerate(liste, start=1):
                x0, y0 = max(0, round(x0 - ctx.decalage_x)), max(0, round(y0 - ctx.decalage_y))
                x1, y1 = min(pix.width, round(x1 - ctx.decalage_x)), min(pix.height, round(y1 - ctx.decalage_y))
                if x1 - x0 < 1 or y1 - y0 < 1:
                    ctx.avertir(f"objet « {nom} » hors de la diapositive : non rastérisé")
                    continue
                # Le fond réel de la diapositive, lui, est restitué par data-background-* de la section.
                decoupe = matter(np, image[y0:y1, x0:x1], image2[y0:y1, x0:x1] if image2 is not None else None)
                tampon = Image.fromarray(np.ascontiguousarray(decoupe), "RGBA")
                ctx.medias.mkdir(parents=True, exist_ok=True)
                fichier = f"s{numero}_r{n}.png"
                tampon.save(ctx.medias / fichier)
                chemin = f"{ctx.prefixe}/{fichier}" if ctx.prefixe else fichier
                boite = (round(x0 + ctx.decalage_x), round(y0 + ctx.decalage_y), x1 - x0, y1 - y0)
                ctx.rasters[(numero, sid)] = (chemin, boite, nom)
                ctx.nb_rasters += 1
        ctx.diapo = 0
        for d in docs:
            d.close()


# --------------------------------------------------------------------------------------------------
# Animations (p:timing)
# --------------------------------------------------------------------------------------------------


class Animations:
    """Effets d'une diapositive, indexés par (shape_id, index de paragraphe ou None).

    Chaque entrée : {"entree": classe ou "" (apparition sèche), "index_entree": n, "sortie": n, "emph": n}.
    """

    def __init__(self):
        self.effets: dict[tuple[int, int | None], dict] = {}
        self.build_par_paragraphe: set[int] = set()
        self.heritage: dict[int, int] = {}  # enfant → groupe animé

    def herite(self, id_groupe: int, id_enfant: int) -> "Animations":
        """Les enfants d'un groupe animé héritent de l'effet du groupe."""
        if any(cle[0] == id_groupe for cle in self.effets) and not any(cle[0] == id_enfant for cle in self.effets):
            for (spid, par), effet in list(self.effets.items()):
                if spid == id_groupe:
                    self.effets[(id_enfant, par)] = effet
        return self

    def _classe(self, effet: dict) -> tuple[str, int] | None:
        if "entree" in effet:
            index = effet["index_entree"]
            classe = effet["entree"]
            if effet.get("sortie") == index + 1:
                classe = "fade-in-then-out"
            return (f"fragment {classe}".strip(), index)
        if "sortie" in effet:
            return ("fragment fade-out", effet["sortie"])
        if "emph" in effet:
            return (f"fragment {effet.get('classe_emph', EMPH_DEFAUT)}", effet["emph"])
        return None

    def fragment_forme(self, spid: int) -> tuple[str, int] | None:
        effet = self.effets.get((spid, None))
        return self._classe(effet) if effet else None

    def fragments_paragraphes(self, spid: int, nb_paragraphes: int) -> dict[int, tuple[str, int]]:
        resultat = {}
        for (s, par), effet in self.effets.items():
            if s == spid and par is not None and par < nb_paragraphes:
                classe = self._classe(effet)
                if classe:
                    resultat[par] = classe
        if not resultat and spid in self.build_par_paragraphe and (spid, None) in self.effets:
            base = self._classe(self.effets[(spid, None)])
            if base:
                for i in range(nb_paragraphes):
                    resultat[i] = (base[0], base[1] + i)
        return resultat


def lire_animations(ctx: Contexte, slide, nb_paragraphes: dict[int, int]) -> Animations:
    """Parcourt p:timing : chaque p:cTn à presetClass entr/exit/emph devient un effet sur sa cible."""
    anims = Animations()
    timing = slide._element.find("p:timing", NS)
    if timing is None:
        return anims
    for bldP in timing.iterfind(".//p:bldLst/p:bldP", NS):
        if bldP.get("build") == "p":
            try:
                anims.build_par_paragraphe.add(int(bldP.get("spid")))
            except (TypeError, ValueError):
                pass
    index = -1
    for cTn in timing.iter(f"{{{NS['p']}}}cTn"):
        classe_preset = cTn.get("presetClass")
        if classe_preset not in ("entr", "exit", "emph"):
            continue
        type_noeud = cTn.get("nodeType", "clickEffect")
        if type_noeud == "clickEffect" or index < 0:
            index += 1
        cibles = cTn.findall(".//p:spTgt", NS)
        if not cibles:
            continue
        preset_id = int(cTn.get("presetID", "0") or 0)
        sous_type = int(cTn.get("presetSubtype", "0") or 0)
        for spTgt in cibles:
            try:
                spid = int(spTgt.get("spid"))
            except (TypeError, ValueError):
                continue
            paragraphes: list[int | None] = [None]
            pRg = spTgt.find("p:txEl/p:pRg", NS)
            if pRg is not None:
                st, end = int(pRg.get("st", "0")), int(pRg.get("end", pRg.get("st", "0")))
                paragraphes = list(range(st, end + 1))
            elif spid in anims.build_par_paragraphe and spTgt.find("p:txEl", NS) is None and spTgt.find("p:bg", NS) is None:
                # build="p" sans cible de paragraphe : un fragment par paragraphe, index croissants.
                nb = nb_paragraphes.get(spid, 0)
                if nb > 1 and classe_preset == "entr":
                    for i in range(nb):
                        effet = anims.effets.setdefault((spid, i), {})
                        if "entree" not in effet:
                            effet["entree"] = classe_entree(preset_id, sous_type)
                            effet["index_entree"] = index + i
                            ctx.nb_animations += 1
                    index += nb - 1
                    continue
            for par in paragraphes:
                effet = anims.effets.setdefault((spid, par), {})
                if classe_preset == "entr":
                    if "entree" in effet:
                        continue  # on garde le premier effet d'entrée
                    effet["entree"] = classe_entree(preset_id, sous_type)
                    effet["index_entree"] = index
                elif classe_preset == "exit":
                    if "sortie" in effet:
                        continue
                    effet["sortie"] = index
                else:
                    if "entree" in effet or "emph" in effet:
                        continue
                    effet["emph"] = index
                    effet["classe_emph"] = EFFETS_EMPH.get(preset_id, EMPH_DEFAUT)
                ctx.nb_animations += 1
    return anims


def classe_entree(preset_id: int, sous_type: int) -> str:
    classe = EFFETS_ENTREE.get(preset_id, "fade-in")
    if isinstance(classe, dict):
        return classe.get(sous_type, "fade-in")
    return classe


# --------------------------------------------------------------------------------------------------
# Diapositive
# --------------------------------------------------------------------------------------------------


def transition(slide) -> str | None:
    element = slide._element.find(".//p:transition", NS)
    if element is None:
        return None
    for enfant in element:
        nom = etree.QName(enfant).localname
        if nom in ("sndAc", "extLst"):
            continue
        return TRANSITIONS.get(nom, "fade")
    return "fade"


def nature_fond(ctx: Contexte, slide, masque) -> tuple[str, object] | None:
    """Fond de la diapositive (diapositive, puis disposition, puis masque) :
    ("couleur", "#rrggbb"), ("image", (partie source, rId)), ("degrade", "#rrggbb") ou None."""
    theme = theme_du_masque(ctx, masque)
    for source in (slide, slide.slide_layout, masque):
        bg = source._element.find("p:cSld/p:bg", NS)
        if bg is None:
            continue
        bgPr = bg.find("p:bgPr", NS)
        if bgPr is not None:
            solid = bgPr.find("a:solidFill", NS)
            if solid is not None:
                couleur = couleur_element(ctx, theme, solid)
                return ("couleur", couleur) if couleur else None
            blip = bgPr.find("a:blipFill/a:blip", NS)
            if blip is not None and blip.get(qn("r:embed")):
                return ("image", (source.part, blip.get(qn("r:embed"))))
            grad = bgPr.find("a:gradFill/a:gsLst/a:gs", NS)
            if grad is not None:
                couleur = couleur_element(ctx, theme, grad)
                if couleur:
                    return ("degrade", couleur)
            return None
        bgRef = bg.find("p:bgRef", NS)
        if bgRef is not None:
            couleur = couleur_element(ctx, theme, bgRef)
            if couleur and bgRef.get("idx", "0") != "0":
                return ("couleur", couleur)
    return None


def fond(ctx: Contexte, slide, masque) -> str:
    """Attributs data-background-* de la section (couleur de la diapositive, sinon image du layout/masque)."""
    nature = nature_fond(ctx, slide, masque)
    if nature is None:
        return ""
    genre, valeur = nature
    if genre == "couleur":
        return f' data-background-color="{valeur}"'
    if genre == "degrade":
        ctx.avertir("fond dégradé approximé par sa première couleur")
        return f' data-background-color="{valeur}"'
    partie, rid = valeur
    try:
        part = partie.related_part(rid)
        ctx.compteur_fonds += 1
        ext = (getattr(part, "ext", None) or "png").lstrip(".")
        chemin = ctx.ecrire_media(f"fond_{ctx.compteur_fonds}.{ext}", part.blob)
        return f' data-background-image="{attr(chemin)}" data-background-size="cover"'
    except Exception as exc:  # noqa: BLE001
        ctx.avertir(f"fond image non extrait ({exc})")
        return ""


def notes_html(ctx: Contexte, slide, indent: str) -> list[str]:
    try:
        if not slide.has_notes_slide:
            return []
        cadre = slide.notes_slide.notes_text_frame
        if cadre is None:
            return []
        paragraphes = [html.escape(p.text, quote=False) for p in cadre.paragraphs if p.text.strip()]
        if not paragraphes:
            return []
        return [indent + '<aside class="notes">'] + [indent + f"  <p>{t}</p>" for t in paragraphes] + [indent + "</aside>"]
    except Exception as exc:  # noqa: BLE001
        return [indent + ctx.avertir(f"notes illisibles ({exc})")]


def formes_plates(formes) -> list:
    """Toutes les formes, groupes aplatis, pour compter les paragraphes par shape_id."""
    resultat = []
    for forme in formes:
        resultat.append(forme)
        if etree.QName(forme._element).localname == "grpSp":
            resultat.extend(formes_plates(forme.shapes))
    return resultat


def diapositive_vers_section(ctx: Contexte, slide, numero: int, qr: bool) -> str:
    ctx.diapo = numero
    ctx.compteur_images = 0
    masque = slide.slide_layout.slide_master
    attributs = f' data-id="s{numero}"'
    if qr:
        attributs += " data-qr"
    try:
        trans = transition(slide)
        if trans:
            attributs += f' data-transition="{trans}"'
    except Exception as exc:  # noqa: BLE001
        ctx.avertir(f"transition illisible ({exc})")
    try:
        attributs += fond(ctx, slide, masque)
    except Exception as exc:  # noqa: BLE001
        ctx.avertir(f"fond illisible ({exc})")
    nb_paragraphes = {}
    for forme in formes_plates(slide.shapes):
        try:
            nb_paragraphes[forme.shape_id] = nombre_paragraphes(forme) if forme.has_text_frame else 0
        except Exception:  # noqa: BLE001
            pass
    try:
        anims = lire_animations(ctx, slide, nb_paragraphes)
    except Exception as exc:  # noqa: BLE001
        anims = Animations()
        ctx.avertir(f"animations illisibles ({exc})")
    lignes = [f"<!-- diapositive {numero} -->", f'<section class="libre"{attributs}>']
    ids = [0]
    for forme in slide.shapes:
        lignes.extend(forme_vers_html(ctx, forme, masque, Repere(), anims, ids, "  "))
    lignes.extend(notes_html(ctx, slide, "  "))
    lignes.append("</section>")
    return "\n".join(lignes) + "\n"


# --------------------------------------------------------------------------------------------------
# Programme principal
# --------------------------------------------------------------------------------------------------


def convertir(pptx: Path, sortie: Path, medias: Path, prefixe: str, qr_premiere: bool,
              raster: bool = True) -> Contexte:
    prs = Presentation(str(pptx))
    ctx = Contexte(prs, medias, prefixe)
    if raster:
        soffice = shutil.which("soffice")
        if soffice:
            try:
                rasteriser(ctx, pptx, soffice)
            except Exception as exc:  # noqa: BLE001
                ctx.diapo = 0
                ctx.avertir(f"rastérisation impossible ({type(exc).__name__} : {exc}) : objets complexes ignorés")
        else:
            ctx.avertir("LibreOffice (soffice) introuvable : groupes, SmartArt, graphiques et formes complexes ignorés")
    sections = []
    for numero, slide in enumerate(prs.slides, start=1):
        try:
            sections.append(diapositive_vers_section(ctx, slide, numero, qr_premiere and numero == 1))
        except Exception as exc:  # noqa: BLE001
            ctx.diapo = numero
            commentaire = ctx.avertir(f"diapositive entière non convertie : {type(exc).__name__} : {exc}")
            sections.append(f'<section class="libre" data-id="s{numero}">\n  {commentaire}\n</section>\n')
    ctx.diapo = 0
    largeur_in, hauteur_in = ctx.largeur / 914400, ctx.hauteur / 914400
    entete = [
        f"Généré par tools/pptx2reveal.py depuis {echapper_commentaire(pptx.name)} : {len(sections)} diapositive(s),",
        f"taille d'origine {largeur_in:.2f} × {hauteur_in:.2f} pouces ({ctx.largeur} × {ctx.hauteur} EMU),",
        f"facteur {ctx.facteur * 914400:.2f} px/pouce, 1 pt = {ctx.facteur * 12700:.2f} px.",
    ]
    if ctx.nb_rasters:
        entete.append(f"{ctx.nb_rasters} objet(s) rastérisé(s) via LibreOffice (medias/s<diapo>_r<n>.png).")
    if abs(ctx.decalage_y) >= 1 or abs(ctx.decalage_x) >= 1:
        entete.append(f"Ratio différent de 16:9 : contenu centré dans la scène 1920 × 1080 "
                      f"(décalage {ctx.decalage_x:+.0f} px horizontal, {ctx.decalage_y:+.0f} px vertical).")
    if ctx.avertissements:
        entete.append(f"{len(ctx.avertissements)} avertissement(s) :")
        entete += [f"  - {echapper_commentaire(a)}" for a in ctx.avertissements]
    entete.append("À retoucher dans l'éditeur du deck.")
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text("<!--\n  " + "\n  ".join(entete) + "\n-->\n\n" + "\n".join(sections), encoding="utf-8")
    return ctx


def principal(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pptx", type=Path)
    p.add_argument("--sortie", type=Path, default=Path("deck/slides.html"))
    p.add_argument("--medias", type=Path, default=None, help="dossier d'extraction des images (défaut : <sortie>/../medias)")
    p.add_argument("--prefixe-medias", default="medias", help="préfixe des src dans le HTML (défaut : medias)")
    p.add_argument("--qr-premiere", action="store_true", help="ajouter data-qr sur la première section")
    p.add_argument("--sans-raster", action="store_true",
                   help="ne pas rastériser les objets complexes via LibreOffice (groupes, SmartArt, graphiques…)")
    args = p.parse_args(argv)
    medias = args.medias if args.medias is not None else args.sortie.parent / "medias"
    try:
        ctx = convertir(args.pptx, args.sortie, medias, args.prefixe_medias, args.qr_premiere, not args.sans_raster)
    except Exception as exc:  # noqa: BLE001
        print(f"erreur : impossible de lire {args.pptx} : {exc}", file=sys.stderr)
        return 1
    nb = len(ctx.prs.slides._sldIdLst)
    print(f"{nb} diapositive(s), {ctx.nb_images} image(s), {ctx.nb_rasters} objet(s) rastérisé(s), "
          f"{ctx.nb_animations} animation(s), {len(ctx.avertissements)} avertissement(s) → {args.sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(principal())

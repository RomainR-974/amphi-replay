"""Tests du convertisseur PowerPoint → sections libres : .pptx généré avec python-pptx, animations injectées en XML."""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pytest
from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "tools"))

import pptx2reveal  # noqa: E402

P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def image_png() -> bytes:
    from PIL import Image

    tampon = io.BytesIO()
    Image.new("RGB", (64, 32), (200, 30, 30)).save(tampon, format="PNG")
    return tampon.getvalue()


def timing_xml(spid_corps: int, spid_forme: int) -> str:
    """Bloc p:timing minimal : fondu par paragraphe sur le corps, puis entrée volante (du bas) sur la forme."""
    return f"""
<p:timing xmlns:p="{P}">
  <p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>
    <p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>
      <p:par><p:cTn id="3" fill="hold"><p:childTnLst><p:par><p:cTn id="4" fill="hold"><p:childTnLst>
        <p:par><p:cTn id="5" presetID="10" presetClass="entr" presetSubtype="0" fill="hold" nodeType="clickEffect">
          <p:childTnLst><p:set><p:cBhvr><p:cTn id="6" dur="1" fill="hold"/>
            <p:tgtEl><p:spTgt spid="{spid_corps}"/></p:tgtEl>
            <p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr>
            <p:to><p:strVal val="visible"/></p:to></p:set></p:childTnLst>
        </p:cTn></p:par>
      </p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>
      <p:par><p:cTn id="7" fill="hold"><p:childTnLst><p:par><p:cTn id="8" fill="hold"><p:childTnLst>
        <p:par><p:cTn id="9" presetID="2" presetClass="entr" presetSubtype="4" fill="hold" nodeType="clickEffect">
          <p:childTnLst><p:set><p:cBhvr><p:cTn id="10" dur="1" fill="hold"/>
            <p:tgtEl><p:spTgt spid="{spid_forme}"/></p:tgtEl>
            <p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr>
            <p:to><p:strVal val="visible"/></p:to></p:set></p:childTnLst>
        </p:cTn></p:par>
      </p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>
    </p:childTnLst></p:cTn></p:seq>
  </p:childTnLst></p:cTn></p:par></p:tnLst>
  <p:bldLst><p:bldP spid="{spid_corps}" grpId="0" build="p"/></p:bldLst>
</p:timing>"""


def transition_xml() -> str:
    return f'<p:transition xmlns:p="{P}"><p:fade/></p:transition>'


@pytest.fixture(scope="module")
def pptx_test(tmp_path_factory) -> Path:
    dossier = tmp_path_factory.mktemp("pptx")
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

    # 1. Titre + sous-titre
    d1 = prs.slides.add_slide(prs.slide_layouts[0])
    d1.shapes.title.text = "Titre <de> la présentation"
    d1.placeholders[1].text = "Sous-titre"

    # 2. Liste à puces de 3 items + une forme animée en entrée volante
    d2 = prs.slides.add_slide(prs.slide_layouts[1])
    d2.shapes.title.text = "Trois points"
    corps = d2.placeholders[1]
    corps.text_frame.text = "Premier"
    for texte in ("Deuxième", "Troisième"):
        corps.text_frame.add_paragraph().text = texte
    run = corps.text_frame.paragraphs[0].runs[0]
    run.font.bold = True
    run.font.color.rgb = RGBColor(0x11, 0x22, 0x33)
    forme = d2.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(9), Inches(5), Inches(3), Inches(1.5))
    forme.fill.solid()
    forme.fill.fore_color.rgb = RGBColor(0x00, 0x80, 0x00)
    forme.text_frame.text = "Conclusion"
    # Transition + animations injectées dans le XML de la diapositive
    sld = d2._element
    sld.append(etree.fromstring(transition_xml()))
    sld.append(etree.fromstring(timing_xml(corps.shape_id, forme.shape_id)))

    # 3. Image + rectangle + ellipse + tableau + notes
    d3 = prs.slides.add_slide(prs.slide_layouts[6])
    d3.shapes.add_picture(io.BytesIO(image_png()), Inches(1), Inches(1), Inches(4), Inches(2))
    rect = d3.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(6), Inches(1), Inches(2), Inches(1))
    rect.fill.solid()
    rect.fill.fore_color.rgb = RGBColor(0x33, 0x66, 0x99)
    rect.rotation = 15
    ellipse = d3.shapes.add_shape(MSO_SHAPE.OVAL, Inches(9), Inches(1), Inches(2), Inches(2))
    ellipse.fill.solid()
    ellipse.fill.fore_color.rgb = RGBColor(0xCC, 0x00, 0x00)
    tableau = d3.shapes.add_table(2, 2, Inches(1), Inches(4), Inches(6), Inches(2)).table
    tableau.cell(0, 0).text = "A"
    tableau.cell(0, 1).text = "B"
    tableau.cell(1, 0).text = "C"
    tableau.cell(1, 1).text = "D"
    d3.notes_slide.notes_text_frame.text = "Note pour l'orateur"

    chemin = dossier / "test.pptx"
    prs.save(chemin)
    return chemin


@pytest.fixture(scope="module")
def conversion(pptx_test: Path, tmp_path_factory):
    sortie = tmp_path_factory.mktemp("deck") / "slides.html"
    code = pptx2reveal.principal([str(pptx_test), "--sortie", str(sortie), "--qr-premiere"])
    assert code == 0
    return sortie, sortie.read_text(encoding="utf-8")


def test_trois_sections_libres(conversion):
    _, html = conversion
    assert len(re.findall(r'<section class="libre"', html)) == 3
    assert 'data-id="s1" data-qr' in html
    assert "Titre &lt;de&gt; la présentation" in html


def test_positions_coherentes(conversion):
    _, html = conversion
    section1 = html.split('data-id="s2"')[0]
    tops = [int(t) for t in re.findall(r"top:(-?\d+)px", section1)]
    assert tops, "aucune position dans la diapositive 1"
    # Le titre (premier élément) est au-dessus du sous-titre et dans la scène.
    assert tops[0] < tops[1]
    assert all(0 <= t < 1080 for t in tops)
    assert all(0 <= int(l) < 1920 for l in re.findall(r"left:(-?\d+)px", section1))
    # 1 pt ≈ 2 px pour une diapo de 13,333 pouces : le titre (44 pt) fait environ 88 px.
    taille = int(re.search(r'<p[^>]*font-size:(\d+)px', section1).group(1))
    assert 80 <= taille <= 100


def test_puces_et_fragments(conversion):
    _, html = conversion
    section2 = html.split('data-id="s2"')[1].split("</section>")[0]
    assert "<ul>" in section2
    items = re.findall(r'<li class="fragment fade-in" data-fragment-index="(\d+)"', section2)
    assert [int(i) for i in items] == [0, 1, 2]
    assert section2.count("<li") == 3
    assert "font-weight:700" in section2 and "color:#112233" in section2
    assert re.search(r'class="el forme fragment fade-up" data-fragment-index="3"', section2)
    assert "background:#008000" in section2 and "border-radius:" in section2
    assert 'data-transition="fade"' in section2


def test_images_formes_tableau_notes(conversion):
    sortie, html = conversion
    section3 = html.split('data-id="s3"')[1]
    image = sortie.parent / "medias" / "s3_1.png"
    assert image.exists() and image.read_bytes()[:4] == b"\x89PNG"
    assert 'src="medias/s3_1.png"' in section3
    assert "background:#336699" in section3 and "transform:rotate(15deg)" in section3
    assert "background:#cc0000" in section3 and "border-radius:50%" in section3
    assert "<table" in section3 and section3.count("<td") == 4
    assert '<aside class="notes">' in section3 and "Note pour l'orateur" in section3
    assert "non converti" not in html.split("-->", 1)[1]  # aucune forme laissée de côté


def test_fichier_inexistant(tmp_path):
    assert pptx2reveal.principal([str(tmp_path / "absent.pptx"), "--sortie", str(tmp_path / "s.html")]) == 1


def test_groupe_tourne_rasterise(tmp_path):
    """Un groupe tourné contenant une flèche est rastérisé en PNG transparent via LibreOffice."""
    import shutil

    if not shutil.which("soffice"):
        pytest.skip("LibreOffice (soffice) absent")
    from PIL import Image

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    d = prs.slides.add_slide(prs.slide_layouts[6])
    groupe = d.shapes.add_group_shape()
    fleche = groupe.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(3), Inches(2), Inches(4), Inches(1.5))
    fleche.fill.solid()
    fleche.fill.fore_color.rgb = RGBColor(0x00, 0x60, 0xA0)
    groupe.rotation = 30
    chemin = tmp_path / "groupe.pptx"
    prs.save(chemin)
    sortie = tmp_path / "deck" / "slides.html"
    assert pptx2reveal.principal([str(chemin), "--sortie", str(sortie)]) == 0
    html = sortie.read_text(encoding="utf-8")
    assert "<!-- rastérisé : " in html and "1 objet(s) rastérisé(s)" in html
    balise = re.search(r'<img class="el image" data-raster="1" src="medias/s1_r1.png"[^>]*style="([^"]*)"', html)
    assert balise, html
    assert "rotate" not in balise.group(1)
    largeur = int(re.search(r"width:(\d+)px", balise.group(1)).group(1))
    hauteur = int(re.search(r"height:(\d+)px", balise.group(1)).group(1))
    # Boîte tournée de 30° : plus large que haute, et plus haute que la flèche non tournée (1,5 pouce ≈ 216 px).
    assert largeur > hauteur > 216
    png = sortie.parent / "medias" / "s1_r1.png"
    assert png.exists()
    image = Image.open(png).convert("RGBA")
    assert image.width > 20 and image.height > 20 and (image.width, image.height) == (largeur, hauteur)
    alphas = set(image.getchannel("A").tobytes())
    assert 0 in alphas and 255 in alphas, "le PNG doit contenir des pixels transparents et des pixels opaques"
    assert "rotation du groupe ignorée" not in html
    # Sans rastérisation : conversion actuelle (flèche en rectangle, avertissement de rotation).
    sortie2 = tmp_path / "deck2" / "slides.html"
    assert pptx2reveal.principal([str(chemin), "--sortie", str(sortie2), "--sans-raster"]) == 0
    html2 = sortie2.read_text(encoding="utf-8")
    assert "data-raster" not in html2 and "rotation du groupe ignorée" in html2


def test_couleurs_cellules_tableau(tmp_path):
    """Fond uni des cellules (a:tcPr/a:solidFill), couleur et gras des runs, repris sur les <td>."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    d = prs.slides.add_slide(prs.slide_layouts[6])
    tableau = d.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(6), Inches(2)).table
    entete = tableau.cell(0, 0)
    entete.text = "Champ"
    entete.fill.solid()
    entete.fill.fore_color.rgb = RGBColor(0x2E, 0x3A, 0x7C)
    run = entete.text_frame.paragraphs[0].runs[0]
    run.font.bold = True
    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    vert = tableau.cell(1, 0)
    vert.text = "Autorisé"
    vert.fill.solid()
    vert.fill.fore_color.rgb = RGBColor(0xEA, 0xF4, 0xEA)
    rose = tableau.cell(1, 1)
    rose.text = "Interdit"
    rose.fill.solid()
    rose.fill.fore_color.rgb = RGBColor(0xFB, 0xEA, 0xEA)
    chemin = tmp_path / "tableau.pptx"
    prs.save(chemin)
    sortie = tmp_path / "deck" / "slides.html"
    assert pptx2reveal.principal([str(chemin), "--sortie", str(sortie), "--sans-raster"]) == 0
    html = sortie.read_text(encoding="utf-8")
    cellules = re.findall(r"<td[^>]*>.*?</td>", html)
    assert len(cellules) == 4
    assert "background:#2e3a7c" in cellules[0] and "font-weight:700" in cellules[0] and "color:#ffffff" in cellules[0]
    assert "background:#eaf4ea" in cellules[2] and "Autorisé" in cellules[2]
    assert "background:#fbeaea" in cellules[3] and "Interdit" in cellules[3]


def test_retraits_paragraphe(tmp_path):
    """a:pPr/@marL et @indent (locaux ou hérités de a:lstStyle) deviennent padding-left / text-indent."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    d = prs.slides.add_slide(prs.slide_layouts[6])
    zone = d.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(2))
    zone.text_frame.text = "Retrait local"
    pPr = zone.text_frame.paragraphs[0]._p.get_or_add_pPr()
    pPr.set("marL", "457200")  # 0,5 pouce : 72 px à l'échelle 16:9 (144 px/pouce)
    pPr.set("indent", "-228600")
    zone2 = d.shapes.add_textbox(Inches(1), Inches(4), Inches(8), Inches(2))
    zone2.text_frame.text = "Retrait hérité"
    lst = zone2.text_frame._txBody.find(f"{{{pptx2reveal.NS['a']}}}lstStyle")
    lst.append(etree.fromstring(f'<a:lvl1pPr xmlns:a="{pptx2reveal.NS["a"]}" marL="914400"/>'))
    chemin = tmp_path / "retraits.pptx"
    prs.save(chemin)
    sortie = tmp_path / "deck" / "slides.html"
    assert pptx2reveal.principal([str(chemin), "--sortie", str(sortie), "--sans-raster"]) == 0
    html = sortie.read_text(encoding="utf-8")
    local = re.search(r"<p[^>]*>Retrait local</p>", html).group(0)
    assert "padding-left:72px" in local and "text-indent:-36px" in local
    herite = re.search(r"<p[^>]*>Retrait hérité</p>", html).group(0)
    assert "padding-left:144px" in herite and "text-indent" not in herite


def test_accentuation_transparence(tmp_path):
    """Un effet d'accentuation « transparence » (presetID 9) devient semi-fade-out, pas grow (qui agrandit)."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    d = prs.slides.add_slide(prs.slide_layouts[6])
    zone = d.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(2))
    zone.text_frame.text = "Texte estompé"
    timing = timing_xml(zone.shape_id, zone.shape_id).replace('presetID="10" presetClass="entr"', 'presetID="9" presetClass="emph"')
    timing = re.sub(r'<p:par><p:cTn id="7".*?</p:par>\s*</p:childTnLst></p:cTn></p:seq>', "</p:childTnLst></p:cTn></p:seq>", timing, flags=re.S)
    timing = timing.replace(f'<p:bldP spid="{zone.shape_id}" grpId="0" build="p"/>', "")
    d._element.append(etree.fromstring(timing))
    chemin = tmp_path / "emph.pptx"
    prs.save(chemin)
    sortie = tmp_path / "deck" / "slides.html"
    assert pptx2reveal.principal([str(chemin), "--sortie", str(sortie), "--sans-raster"]) == 0
    html = sortie.read_text(encoding="utf-8")
    assert 'class="el texte fragment semi-fade-out" data-fragment-index="0"' in html
    assert "grow" not in html


def test_ligne_boite_minimale():
    """Une ligne horizontale (hauteur 0) est un objet complexe dont la boîte fait au moins 8 px de haut."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    d = prs.slides.add_slide(prs.slide_layouts[6])
    ligne = d.shapes.add_connector(1, Inches(2), Inches(3), Inches(8), Inches(3))  # MSO_CONNECTOR.STRAIGHT
    ctx = pptx2reveal.Contexte(prs, Path("/nonexistent"), "medias")
    assert pptx2reveal.est_complexe(ligne)
    x0, y0, x1, y1 = pptx2reveal.boite_scene(ctx, ligne)
    assert y1 - y0 >= pptx2reveal.TAILLE_MIN_RASTER and x1 - x0 > 800
    assert y0 < 432 < y1  # la ligne (3 pouces → 432 px) est dans la boîte


def test_chroma_key_et_ligne_rasterisees(tmp_path):
    """Rastérisation par chroma key : une flèche blanche sur une diapositive blanche reste opaque (seul le
    fond de chroma key devient transparent) ; une LINE devient un <img data-raster="1"> avec son fragment."""
    import shutil

    if not shutil.which("soffice"):
        pytest.skip("LibreOffice (soffice) absent")
    from PIL import Image

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    d = prs.slides.add_slide(prs.slide_layouts[6])
    d.background.fill.solid()
    d.background.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    fleche = d.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(1), Inches(1), Inches(4), Inches(2))
    fleche.fill.solid()
    fleche.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    fleche.line.fill.background()
    ligne = d.shapes.add_connector(1, Inches(1), Inches(5), Inches(9), Inches(5))
    ligne.line.color.rgb = RGBColor(0xFF, 0x00, 0x00)
    ligne.line.width = Inches(0.05)
    d._element.append(etree.fromstring(timing_xml(fleche.shape_id, ligne.shape_id).replace(
        f'<p:bldP spid="{fleche.shape_id}" grpId="0" build="p"/>', "")))
    chemin = tmp_path / "chroma.pptx"
    prs.save(chemin)
    sortie = tmp_path / "deck" / "slides.html"
    assert pptx2reveal.principal([str(chemin), "--sortie", str(sortie)]) == 0
    html = sortie.read_text(encoding="utf-8")
    assert "2 objet(s) rastérisé(s)" in html
    assert 'data-background-color="#ffffff"' in html
    # Flèche blanche : son centre est opaque et blanc, le coin supérieur gauche (hors de la pointe) transparent.
    png = Image.open(sortie.parent / "medias" / "s1_r1.png").convert("RGBA")
    assert png.getpixel((png.width // 2, png.height // 2)) == (255, 255, 255, 255)
    assert png.getpixel((png.width - 2, 1))[3] == 0  # coin : au-dessus de la pointe de la flèche
    # Ligne : image rastérisée, fragment d'entrée, boîte d'au moins 8 px de haut, pixels rouges opaques.
    balise = re.search(r'<img class="el image fragment fade-up" data-fragment-index="1" data-raster="1" '
                       r'src="medias/s1_r2.png"[^>]*style="([^"]*)"', html)
    assert balise, html
    hauteur = int(re.search(r"height:(\d+)px", balise.group(1)).group(1))
    assert hauteur >= 8
    ligne_png = Image.open(sortie.parent / "medias" / "s1_r2.png").convert("RGBA")
    r, g, b, a = ligne_png.getpixel((ligne_png.width // 2, ligne_png.height // 2))
    assert a == 255 and r > 200 and g < 60 and b < 60
    assert ligne_png.getpixel((ligne_png.width // 2, 0))[3] == 0
    assert "ignoré" not in html

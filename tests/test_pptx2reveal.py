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

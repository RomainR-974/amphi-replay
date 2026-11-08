"""Zone réservée à la caméra OBS (docs/zone-camera.md) : normalisation de `camera`, exposition par l'API,
script tools/reserver_camera.py, parité JS/Python, présentations iag-v1 et iag-v2 sans chevauchement."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from server.presentations import CAMERA_DEFAUT, Bibliotheque, normaliser_camera
from tests.test_serveur import JETON, client, creer_presentation  # noqa: F401

RACINE = Path(__file__).resolve().parent.parent
SCRIPT = RACINE / "tools" / "reserver_camera.py"
sys.path.insert(0, str(RACINE / "tools"))
import reserver_camera as rc  # noqa: E402


# ---------------------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------------------


def biblio(tmp_path: Path, meta: dict) -> Bibliotheque:
    d = tmp_path / "presentations" / "p"
    (d / "medias").mkdir(parents=True)
    (d / "slides.html").write_text('<section class="libre" data-id="s1"></section>', encoding="utf-8")
    (d / "presentation.json").write_text(json.dumps(meta), encoding="utf-8")
    return Bibliotheque(tmp_path / "presentations", tmp_path / "data" / "active.json")


@pytest.mark.parametrize(
    "meta, attendu",
    [
        ({"titre": "x"}, CAMERA_DEFAUT),  # clé absente → défaut
        ({"titre": "x", "camera": None}, None),  # null → désactivée
        ({"titre": "x", "camera": {"position": "milieu"}}, CAMERA_DEFAUT),  # position invalide → défaut
        ({"titre": "x", "camera": {"position": "bas-gauche"}}, {**CAMERA_DEFAUT, "position": "bas-gauche"}),
        ({"titre": "x", "camera": {"largeur": 400, "hauteur": "200", "position": "haut-droite", "marge": 0}}, {"largeur": 400, "hauteur": 200, "position": "haut-droite", "marge": 0}),
        ({"titre": "x", "camera": "oui"}, CAMERA_DEFAUT),  # type inattendu → défaut
        ({"titre": "x", "camera": {"largeur": -5, "hauteur": "abc", "marge": -1}}, CAMERA_DEFAUT),  # valeurs invalides → défaut champ par champ
    ],
)
def test_normalisation_camera(tmp_path, meta, attendu):
    assert normaliser_camera(meta.get("camera", normaliser_camera.__defaults__[0])) == attendu
    b = biblio(tmp_path, meta)
    assert b.lire("p")["camera"] == attendu
    assert b.resume("p")["camera"] == attendu


def test_ecriture_meta_conserve_null_et_reglages_mais_pas_le_defaut(tmp_path):
    b = biblio(tmp_path, {"titre": "x"})
    b.toucher("p")
    assert "camera" not in json.loads((b.dossier / "p" / "presentation.json").read_text(encoding="utf-8"))
    b = biblio(tmp_path / "n", {"titre": "x", "camera": None})
    b.toucher("p")
    assert json.loads((b.dossier / "p" / "presentation.json").read_text(encoding="utf-8"))["camera"] is None
    b = biblio(tmp_path / "r", {"titre": "x", "camera": {"position": "bas-gauche"}})
    b.modifier("p", titre="Y")
    assert json.loads((b.dossier / "p" / "presentation.json").read_text(encoding="utf-8"))["camera"] == {**CAMERA_DEFAUT, "position": "bas-gauche"}


# ---------------------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------------------


def test_api_expose_camera(client):
    assert client.get("/api/presentations/active").json()["camera"] == CAMERA_DEFAUT
    assert client.get("/api/presentations").json()[0]["camera"] == CAMERA_DEFAUT
    assert client.get("/api/presentations/demo").json()["camera"] == CAMERA_DEFAUT
    d = creer_presentation(client.presentations, "sans", "Sans vignette")
    meta = json.loads((d / "presentation.json").read_text(encoding="utf-8"))
    meta["camera"] = None
    (d / "presentation.json").write_text(json.dumps(meta), encoding="utf-8")
    d = creer_presentation(client.presentations, "gauche", "Vignette à gauche")
    meta = json.loads((d / "presentation.json").read_text(encoding="utf-8"))
    meta["camera"] = {"largeur": 480, "hauteur": 270, "position": "bas-gauche", "marge": 40}
    (d / "presentation.json").write_text(json.dumps(meta), encoding="utf-8")
    liste = {p["slug"]: p["camera"] for p in client.get("/api/presentations").json()}
    assert liste["sans"] is None
    assert liste["gauche"] == {"largeur": 480, "hauteur": 270, "position": "bas-gauche", "marge": 40}
    assert client.post(f"/api/presentations/sans/activer?token={JETON}").status_code == 200
    assert client.get("/api/presentations/active").json()["camera"] is None


# ---------------------------------------------------------------------------------------
# Script tools/reserver_camera.py
# ---------------------------------------------------------------------------------------

SLIDES_TEST = """<!-- test -->
<section class="libre" data-id="s1" data-quiz="q1">
  <div class="el texte" data-id="e1" style="left:100px;top:100px;width:800px;height:100px;font-size:60px">Titre</div>
  <div class="el resultats" data-amphi="resultats" data-question="q1" data-id="e2" style="left:100px;top:300px;width:1700px;height:700px;color:#111111"></div>
  <div class="el qr" data-amphi="qr" data-id="e3" style="left:1640px;top:800px;width:240px;height:240px"></div>
  <aside class="notes">Notes</aside>
</section>

<section class="libre" data-id="s2">
  <img class="el image" data-id="e1" src="medias/x.png" style="left:1500px;top:700px;width:300px;height:300px">
</section>

<section data-quiz="q2" data-id="s3"></section>
"""


def lancer(slides: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), str(slides), *args], capture_output=True, text=True, check=True)


def test_zone_par_defaut():
    zone = rc.zone_camera(dict(CAMERA_DEFAUT))
    assert (zone.x, zone.y, zone.l, zone.h) == (1600, 760, 320, 320) and zone.droite and zone.bas
    assert rc.zone_camera(None) is None
    g = rc.zone_camera({**CAMERA_DEFAUT, "position": "haut-gauche"})
    assert (g.x, g.y, g.l, g.h) == (0, 0, 320, 320) and not g.droite and not g.bas
    assert rc.chevauche({"x": 1500, "y": 700, "l": 300, "h": 300}, zone)
    assert not rc.chevauche({"x": 1300, "y": 700, "l": 300, "h": 300}, zone)  # bord commun : pas de chevauchement
    assert not rc.chevauche({"x": 1600, "y": 300, "l": 300, "h": 460}, zone)


def test_script_resultats_hauteur_reduite_et_qr_au_coin(tmp_path):
    slides = tmp_path / "slides.html"
    slides.write_text(SLIDES_TEST, encoding="utf-8")
    r = lancer(slides)
    html = slides.read_text(encoding="utf-8")
    # resultats : 300 → 760, soit 460 px (≥ 60 % de 700) ; le reste du style est intact.
    assert 'data-id="e2" style="left:100px;top:300px;width:1700px;height:460px;color:#111111"' in html
    # qr : coin bas-gauche libre (marge 20) : x = 20, y = 1080 − 20 − 240.
    assert 'data-id="e3" style="left:20px;top:820px;width:240px;height:240px"' in html
    assert "s1 · e2 (resultats) : hauteur réduite : 100,300 1700×700 → 100,300 1700×460" in r.stdout
    assert "s1 · e3 (qr) : coin bas-gauche" in r.stdout
    # Rien d'autre n'a bougé : titre, notes, section sans data-quiz, section de quiz standard, commentaire.
    assert 'data-id="e1" style="left:100px;top:100px;width:800px;height:100px;font-size:60px">Titre' in html
    assert 'style="left:1500px;top:700px;width:300px;height:300px"' in html
    assert "<!-- test -->" in html and '<section data-quiz="q2" data-id="s3"></section>' in html and "<aside class=\"notes\">Notes</aside>" in html
    # Idempotence : un second passage ne change rien.
    r2 = lancer(slides)
    assert slides.read_text(encoding="utf-8") == html
    assert "aucun élément ne chevauche la zone" in r2.stdout and "fichier inchangé" in r2.stdout
    # --toutes : la section s2 est traitée ; une image n'est pas réduite mais décalée vers la gauche (1600 − 300).
    r3 = lancer(slides, "--toutes")
    html3 = slides.read_text(encoding="utf-8")
    assert 'style="left:1300px;top:700px;width:300px;height:300px"' in html3
    assert "s2 · e1 (image) : décalé vers la gauche" in r3.stdout


def test_script_options_et_camera_nulle(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    slides = d / "slides.html"
    slides.write_text(SLIDES_TEST, encoding="utf-8")
    (d / "presentation.json").write_text(json.dumps({"titre": "x", "camera": None}), encoding="utf-8")
    r = lancer(slides)
    assert "caméra désactivée" in r.stdout and slides.read_text(encoding="utf-8") == SLIDES_TEST
    # Les options l'emportent : caméra 400 × 400, marge 0 → zone x ≥ 1520, y ≥ 680 ; hauteur 380 < 60 % → largeur réduite à 1420.
    r = lancer(slides, "--largeur", "400", "--hauteur", "400", "--marge", "0", "--simuler")
    assert "zone réservée x ≥ 1520, y ≥ 680" in r.stdout
    assert "s1 · e2 (resultats) : largeur réduite : 100,300 1700×700 → 100,300 1420×700" in r.stdout
    assert slides.read_text(encoding="utf-8") == SLIDES_TEST  # --simuler n'écrit pas
    # Réglage lu dans presentation.json : caméra en bas à gauche → le qr va au coin bas-droite, les résultats sont réduits en hauteur.
    (d / "presentation.json").write_text(json.dumps({"titre": "x", "camera": {"position": "bas-gauche"}}), encoding="utf-8")
    slides.write_text(SLIDES_TEST.replace("left:1640px;top:800px", "left:40px;top:800px"), encoding="utf-8")
    r = lancer(slides)
    html = slides.read_text(encoding="utf-8")
    assert "zone réservée x ≥ 0, y ≥ 760" in r.stdout
    assert 'data-id="e3" style="left:1660px;top:820px;width:240px;height:240px"' in html
    assert 'data-id="e2" style="left:100px;top:300px;width:1700px;height:460px;color:#111111"' in html
    # --section vise une section sans data-quiz.
    slides.write_text(SLIDES_TEST, encoding="utf-8")
    (d / "presentation.json").unlink()
    r = lancer(slides, "--section", "s2")
    assert "s2 · e1 (image) : décalé vers la gauche" in r.stdout


def test_algorithme_regles_2_a_5():
    zone = rc.zone_camera(dict(CAMERA_DEFAUT))
    # 2. image décalée à gauche si l'espace est libre ; 5. laissée si un autre .el bloque toutes les règles.
    libre = [rc.Boite("a", "image", 1700, 800, 200, 200)]
    assert [x["action"] for x in rc.reagencer_boites(libre, zone)] == ["décalé vers la gauche"] and libre[0].x == 1400
    bloque = [rc.Boite("a", "image", 1700, 800, 200, 200), rc.Boite("b", "texte", 1300, 400, 250, 600), rc.Boite("c", "forme", 1700, 500, 200, 250)]
    actions = rc.reagencer_boites(bloque, zone)
    assert [x["action"] for x in actions] == ["laissé en place (aucune règle applicable)"] and (bloque[0].x, bloque[0].y) == (1700, 800)
    # 3. largeur réduite quand la hauteur ne peut pas l'être (élément déjà sous y = 760) et que la gauche est occupée.
    large = [rc.Boite("pied", "texte", 96, 1004, 1728, 48)]
    assert rc.reagencer_boites(large, zone)[0]["action"] == "largeur réduite" and large[0].l == 1504
    # 4. décalé vers le haut quand il ne reste que cette possibilité.
    haut = [rc.Boite("a", "image", 1650, 800, 250, 250), rc.Boite("b", "texte", 100, 700, 1500, 380)]
    assert rc.reagencer_boites(haut, zone)[0]["action"] == "décalé vers le haut" and haut[0].y == 510
    # Les éléments déjà empilés (point sur une barre) ne bloquent pas le décalage.
    empiles = [rc.Boite("barre", "forme", 95, 1026, 1636, 11), rc.Boite("point", "forme", 1720, 1020, 22, 22)]
    actions = {a["id"]: a["action"] for a in rc.reagencer_boites(empiles, zone)}
    assert actions == {"barre": "largeur réduite", "point": "décalé vers la gauche"} and empiles[1].x == 1578


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent : parité JS/Python non vérifiable")
def test_parite_js_python(tmp_path):
    """deck/camera.js et le script appliquent les mêmes règles aux mêmes boîtes."""
    boites = [
        {"id": "qr", "type": "qr", "x": 1640, "y": 800, "l": 240, "h": 240},
        {"id": "res", "type": "resultats", "x": 100, "y": 300, "l": 1700, "h": 700},
        {"id": "img", "type": "image", "x": 1700, "y": 800, "l": 200, "h": 200},
        {"id": "pied", "type": "texte", "x": 96, "y": 1004, "l": 1728, "h": 48},
        {"id": "barre", "type": "forme", "x": 95, "y": 1026, "l": 1636, "h": 11},
        {"id": "point", "type": "forme", "x": 1720, "y": 1020, "l": 22, "h": 22},
        {"id": "date", "type": "texte", "x": 1674, "y": 1038, "l": 198, "h": 39},
        {"id": "cl", "type": "classement", "x": 160, "y": 250, "l": 1600, "h": 740},
    ]
    script = f"""
      const window = {{}}; {Path(RACINE / 'deck' / 'camera.js').read_text(encoding='utf-8')}
      const C = window.AmphiCamera;
      for (const cam of [C.DEFAUT, {{...C.DEFAUT, position: 'bas-gauche'}}, {{largeur: 480, hauteur: 270, position: 'haut-droite', marge: 40}}]) {{
        const b = JSON.parse(process.argv[2]);
        const actions = C.reagencerBoites(b, C.zoneCamera(cam));
        console.log(JSON.stringify({{ actions: actions.map(a => [a.id, a.action]), boites: b.map(x => [x.id, x.x, x.y, x.l, x.h]) }}));
      }}"""
    js = tmp_path / "parite.js"
    js.write_text(script, encoding="utf-8")
    sortie = subprocess.run(["node", str(js), json.dumps(boites)], capture_output=True, text=True, check=True).stdout.strip().splitlines()
    for ligne, cam in zip(sortie, [dict(CAMERA_DEFAUT), {**CAMERA_DEFAUT, "position": "bas-gauche"}, {"largeur": 480, "hauteur": 270, "position": "haut-droite", "marge": 40}]):
        b = [rc.Boite(**x) for x in boites]
        actions = rc.reagencer_boites(b, rc.zone_camera(cam))
        attendu = {"actions": [[a["id"], a["action"]] for a in actions], "boites": [[x.id, x.x, x.y, x.l, x.h] for x in b]}
        assert json.loads(ligne) == attendu, cam


# ---------------------------------------------------------------------------------------
# Présentations livrées et fichiers du deck
# ---------------------------------------------------------------------------------------

STYLE = re.compile(r'<(?:div|img|iframe|video)\b[^>]*\bclass="(?:[^"]*\s)?el(?:\s[^"]*)?"[^>]*>', re.S)


@pytest.mark.parametrize("slug, sections_extra", [("iag-v1", {"classement"}), ("iag-v2", {"s42"})])
def test_aucun_element_de_quiz_ne_chevauche_la_zone(slug, sections_extra):
    html = (RACINE / "presentations" / slug / "slides.html").read_text(encoding="utf-8")
    zone = rc.zone_camera(dict(CAMERA_DEFAUT))
    verifiees, fautifs = 0, []
    for m in rc.SECTION.finditer(html):
        sec = m.group(0)
        if not re.search(r"\bdata-quiz=", sec.split(">", 1)[0]) and rc.id_section(sec) not in sections_extra:
            continue
        verifiees += 1
        for t in STYLE.finditer(sec):
            b = rc.boite_de(t.group(0))
            if b and rc.chevauche(b, zone):
                fautifs.append((rc.id_section(sec), re.search(r'data-id="([^"]*)"', t.group(0)).group(1), b))
    assert verifiees >= 10 and fautifs == []


def test_fichiers_du_deck_et_de_l_editeur():
    index = (RACINE / "deck" / "index.html").read_text(encoding="utf-8")
    assert 'src="camera.js"' in index and "AmphiCamera.reagencer(" in index and "role === 'obs') reserverCamera()" in index
    quiz = (RACINE / "deck" / "quiz-deck.js").read_text(encoding="utf-8")
    assert "reserverCameraStandard" in quiz and "calc(100% - " in quiz and "/api/presentations/active" in quiz
    editeur = (RACINE / "deck" / "editeur.html").read_text(encoding="utf-8")
    assert 'id="c-camera"' in editeur and 'id="zone-camera"' in editeur and 'src="camera.js"' in editeur
    js = (RACINE / "deck" / "editeur.js").read_text(encoding="utf-8")
    assert "localStorage.getItem('editeur.camera')" in js and "ed-chevauche" in js and "chevauche la zone caméra" in js
    assert "'.ed-cache, .ed-chevauche'" in js  # jamais sérialisé
    camera = (RACINE / "deck" / "camera.js").read_text(encoding="utf-8")
    assert "function reagencerBoites" in camera and "function zoneCamera" in camera and "function chevauche" in camera
    assert "300 × 300" in (RACINE / "obs" / "README.md").read_text(encoding="utf-8") and "300 × 300" in (RACINE / "docs" / "fiche-mise-en-route.md").read_text(encoding="utf-8")
    assert "camera" in (RACINE / "docs" / "format-deck.md").read_text(encoding="utf-8")

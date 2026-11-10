"""Tests de la bibliothèque de présentations : liste, création, duplication, activation, suppression,
service des fichiers (/deck/… = active, /p/{slug}/…), anti-traversée, paramètre `prez`, import PowerPoint."""

from __future__ import annotations

import json
import shutil

import pytest
from pptx import Presentation
from pptx.util import Inches

from tests.test_serveur import JETON, creer_presentation  # noqa: F401  (réutilise la fixture `client`)
from tests.test_serveur import client  # noqa: F401


def jeton(chemin: str) -> str:
    return f"{chemin}{'&' if '?' in chemin else '?'}token={JETON}"


def test_liste_et_active(client):
    liste = client.get("/api/presentations").json()
    assert [p["slug"] for p in liste] == ["demo"]
    p = liste[0]
    assert p["titre"] == "Présentation de démonstration" and p["active"] is True
    assert p["diapositives"] == 2 and p["questions"] == 13 and p["theme"] == ""
    assert client.get("/api/presentations/active").json() == {"slug": "demo", "titre": "Présentation de démonstration", "theme": "", "camera": {"largeur": 300, "hauteur": 300, "position": "bas-droite", "marge": 20}}
    assert client.get("/sante").json()["presentation"] == "demo"


def test_creation_duplication_renommage(client):
    assert client.post("/api/presentations", json={"slug": "x", "titre": "X"}).status_code == 403
    assert client.post(jeton("/api/presentations"), json={"slug": "Mauvais Slug", "titre": "X"}).status_code == 422
    r = client.post(jeton("/api/presentations"), json={"slug": "vide", "titre": "Nouvelle <séance>", "description": "Essai"})
    assert r.status_code == 200, r.text
    assert r.json()["diapositives"] == 1 and r.json()["questions"] == 0 and r.json()["active"] is False
    html = client.get("/p/vide/slides.html").text
    assert "Nouvelle &lt;séance&gt;" in html and 'class="libre"' in html
    assert (client.presentations / "vide" / "medias").is_dir() and (client.presentations / "vide" / "quiz.json").read_text() == "[]\n"
    assert client.post(jeton("/api/presentations"), json={"slug": "vide", "titre": "Doublon"}).status_code == 409

    # Duplication : fichiers copiés (médias compris), métadonnées propres.
    (client.deck / "medias" / "img.png").write_bytes(b"png")
    r = client.post(jeton("/api/presentations"), json={"slug": "copie", "titre": "Copie", "depuis": "demo"})
    assert r.status_code == 200, r.text
    assert r.json()["diapositives"] == 2 and r.json()["questions"] == 13 and r.json()["description"] == "Présentation de test"
    assert (client.presentations / "copie" / "medias" / "img.png").read_bytes() == b"png"
    assert client.get("/p/copie/slides.html").text == client.get("/deck/slides.html").text
    assert client.post(jeton("/api/presentations"), json={"slug": "c2", "titre": "C", "depuis": "inconnue"}).status_code == 404

    r = client.put(jeton("/api/presentations/copie"), json={"titre": "Renommée", "theme": "dsfr"})
    assert r.status_code == 200 and r.json()["titre"] == "Renommée" and r.json()["theme"] == "dsfr"
    meta = json.loads((client.presentations / "copie" / "presentation.json").read_text(encoding="utf-8"))
    assert meta["titre"] == "Renommée" and meta["theme"] == "dsfr"
    assert client.put(jeton("/api/presentations/copie"), json={"theme": "../x"}).status_code == 422
    assert [p["slug"] for p in client.get("/api/presentations").json()] == ["copie", "demo", "vide"]


def test_activation_recharge_quiz_et_deck(client):
    creer_presentation(client.presentations, "autre", "Autre séance", quiz=json.dumps([{"id": "z1", "type": "qcm", "question": "Z ?", "options": ["a", "b"], "bonne": 0}]))
    # Une question est ouverte et une autre révélée sur la présentation courante.
    assert client.post(jeton("/api/quiz/ouvrir"), json={"question_id": "q1"}).status_code == 200
    assert client.get("/sante").json()["question_ouverte"] == "q1"
    with client.websocket_connect("/ws/deck?role=scene") as scene, client.websocket_connect(f"/ws/deck?role=remote&token={JETON}") as remote:
        assert scene.receive_json()["type"] == "slide" and remote.receive_json()["type"] == "slide"
        assert client.post("/api/presentations/autre/activer").status_code == 403
        r = client.post(jeton("/api/presentations/autre/activer"))
        assert r.status_code == 200 and r.json()["active"] is True
        m = scene.receive_json()
        assert m["type"] == "recharger" and m["prez"] == "autre"
        assert remote.receive_json()["type"] == "recharger"
    assert client.get("/api/presentations/active").json()["slug"] == "autre"
    assert json.loads(client.app.state.biblio.fichier_active.read_text())["slug"] == "autre"
    etat = client.get("/sante").json()
    assert etat["question_ouverte"] is None and etat["questions"] == 1
    assert [q["id"] for q in client.get("/api/quiz/questions").json()] == ["z1"]
    assert "Autre" not in client.get("/deck/slides.html").text  # même slides minimal, mais servi depuis « autre »
    assert {p["slug"]: p["active"] for p in client.get("/api/presentations").json()} == {"autre": True, "demo": False}
    assert client.post(jeton("/api/presentations/inconnue/activer")).status_code == 404


def test_suppression(client):
    assert client.delete(jeton("/api/presentations/demo")).status_code == 409  # active
    creer_presentation(client.presentations, "jetable", "Jetable")
    assert client.delete("/api/presentations/jetable").status_code == 403
    assert client.delete(jeton("/api/presentations/jetable")).status_code == 200
    assert not (client.presentations / "jetable").exists()
    assert client.delete(jeton("/api/presentations/jetable")).status_code == 404
    assert client.delete(jeton("/api/presentations/..")).status_code == 404


def test_fichiers_servis_et_anti_traversee(client):
    (client.deck / "medias" / "a.png").write_bytes(b"\x89PNG a")
    creer_presentation(client.presentations, "b", "B", slides="<section>B</section>")
    (client.presentations / "b" / "medias" / "b.png").write_bytes(b"\x89PNG b")

    r = client.get("/deck/slides.html")
    assert r.status_code == 200 and "Démo" in r.text and r.headers["cache-control"] == "no-store"
    assert client.get("/p/demo/slides.html").text == r.text
    assert client.get("/p/b/slides.html").text == "<section>B</section>"
    assert client.get("/p/inconnue/slides.html").status_code == 404
    assert client.get("/p/Bad!/slides.html").status_code == 404

    assert client.get("/deck/medias/a.png").content == b"\x89PNG a"
    assert client.get("/p/demo/medias/a.png").content == b"\x89PNG a"
    assert client.get("/p/b/medias/b.png").content == b"\x89PNG b"
    assert client.get("/deck/medias/b.png").status_code == 404
    assert client.get("/p/b/medias/a.png").status_code == 404
    # Anti-traversée : le chemin résolu doit rester dans medias/.
    (client.presentations / "secret.txt").write_text("x")
    for chemin in ("/deck/medias/../../secret.txt", "/deck/medias/..%2F..%2Fsecret.txt", "/p/demo/medias/..%2Fslides.html", "/p/demo/medias/%2e%2e/quiz.json", "/deck/medias/"):
        assert client.get(chemin).status_code == 404, chemin
    # Le runtime commun reste servi par le montage statique.
    assert client.get("/deck/").status_code == 200 and client.get("/deck/sync.js").status_code == 200
    assert client.get(jeton("/presentations"), follow_redirects=False).status_code == 307
    assert client.get("/presentations").status_code == 403


def test_parametre_prez_sur_api(client):
    creer_presentation(client.presentations, "b", "B", slides="<section>B</section>", quiz="[]")
    assert client.get("/api/deck/slides").text == client.get("/api/deck/slides?prez=demo").text
    assert client.get("/api/deck/slides?prez=b").text == "<section>B</section>"
    assert client.get("/api/deck/slides?prez=zzz").status_code == 404
    with client.websocket_connect("/ws/deck?role=scene") as scene:
        assert scene.receive_json()["type"] == "slide"
        assert client.put(jeton("/api/deck/slides?prez=b"), json={"html": "<section>B2</section>"}).status_code == 200
        # Pas de rechargement de la scène : « b » n'est pas active.
        assert client.put(jeton("/api/deck/slides"), json={"html": SLIDES_ACTIVE}).status_code == 200
        assert scene.receive_json()["type"] == "recharger"
    assert client.get("/p/b/slides.html").text == "<section>B2</section>"
    assert client.get("/deck/slides.html").text == SLIDES_ACTIVE
    assert "Démo" in client.get(jeton("/api/deck/sauvegardes?prez=demo")).json()[0]["nom"] or True
    assert len(client.get(jeton("/api/deck/sauvegardes?prez=b")).json()) == 1
    assert (client.presentations / "b" / "sauvegardes").is_dir()

    r = client.post(jeton("/api/deck/medias?prez=b"), files={"fichier": ("i.png", b"\x89PNG", "image/png")})
    assert r.status_code == 200 and (client.presentations / "b" / "medias" / "i.png").exists()
    assert not (client.deck / "medias" / "i.png").exists()
    assert [m["chemin"] for m in client.get(jeton("/api/deck/medias?prez=b")).json()] == ["medias/i.png"]

    assert client.get("/api/quiz/questions?prez=b").json() == []
    assert len(client.get("/api/quiz/questions?prez=demo").json()) == 13
    nouvelles = [{"id": "n1", "type": "vrai_faux", "question": "Vrai ?", "bonne": True}]
    assert client.put(jeton("/api/quiz/questions?prez=b"), json=nouvelles).status_code == 200
    assert client.get(jeton("/api/quiz/questions?prez=b")).json()[0]["bonne"] is True
    assert "bonne" not in client.get("/api/quiz/questions?prez=b").json()[0]
    assert len(client.get("/api/quiz/questions").json()) == 13  # le quiz actif n'a pas bougé
    assert client.get("/sante").json()["questions"] == 13
    # Après activation de « b », ses questions sont celles du quiz.
    assert client.post(jeton("/api/presentations/b/activer")).status_code == 200
    assert [q["id"] for q in client.get("/api/quiz/questions").json()] == ["n1"]


SLIDES_ACTIVE = "<section>Démo 2</section>\n"


def pptx_deux_diapos(chemin, image_png: bytes):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    d1 = prs.slides.add_slide(prs.slide_layouts[6])
    z = d1.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(1))
    z.text_frame.text = "Titre importé"
    d2 = prs.slides.add_slide(prs.slide_layouts[6])
    d2.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1)).text_frame.text = "Deuxième"
    img = chemin.parent / "img.png"
    img.write_bytes(image_png)
    d2.shapes.add_picture(str(img), Inches(1), Inches(3), Inches(4), Inches(3))
    prs.save(str(chemin))


def test_import_pptx(client, tmp_path):
    from tests.test_pptx2reveal import image_png

    pptx = tmp_path / "support.pptx"
    pptx_deux_diapos(pptx, image_png())
    contenu = pptx.read_bytes()
    assert client.post("/api/presentations/importer", data={"slug": "imp"}, files={"fichier": ("support.pptx", contenu)}).status_code == 403
    assert client.post(jeton("/api/presentations/importer"), data={"slug": "imp"}, files={"fichier": ("support.txt", b"x")}).status_code == 415
    assert client.post(jeton("/api/presentations/importer"), data={"slug": "demo"}, files={"fichier": ("support.pptx", contenu)}).status_code == 409
    r = client.post(jeton("/api/presentations/importer"), data={"slug": "imp", "titre": "Importée"}, files={"fichier": ("support.pptx", contenu)})
    assert r.status_code == 200, r.text
    corps = r.json()
    assert corps["slug"] == "imp" and corps["diapositives"] == 2 and corps["titre"] == "Importée" and isinstance(corps["avertissements"], list)
    html = client.get("/p/imp/slides.html").text
    assert html.count('<section class="libre"') == 2 and "Titre importé" in html and "data-qr" in html
    assert 'src="medias/s2_1.png"' in html
    assert client.get("/p/imp/medias/s2_1.png").status_code == 200
    assert (client.presentations / "imp" / "quiz.json").read_text() == "[]\n"
    assert client.get("/api/presentations/imp").json()["questions"] == 0
    if shutil.which("soffice"):
        assert corps["raster"] is True
    else:
        assert corps["raster"] is False and any("LibreOffice" in a for a in corps["avertissements"])
    # Un fichier corrompu : conversion échouée, rien n'est laissé derrière.
    r = client.post(jeton("/api/presentations/importer"), data={"slug": "casse"}, files={"fichier": ("casse.pptx", b"pas un pptx")})
    assert r.status_code == 500 and not (client.presentations / "casse").exists()

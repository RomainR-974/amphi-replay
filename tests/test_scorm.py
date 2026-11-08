"""Tests de l'export SCORM 1.2 (docs/scorm.md) : construction du paquet, manifeste, questions, modes, routes et CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from server import scorm as module_scorm
from server.journal import questions_diapositives
from tests.test_postprod import attendre_tache, jeton
from tests.test_serveur import creer_presentation  # noqa: F401
from tests.test_serveur import client  # noqa: F401

RACINE = Path(__file__).resolve().parent.parent
FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")

SLIDES_COURS = """<!-- cours -->
<section class="libre" data-id="s1"><div class="el texte" style="font-size:80px">Introduction &amp; cadre</div></section>
<section data-quiz="qcm1" data-id="s2"></section>
<section data-id="s3"><h2>Conclusion</h2><section><h3>Verticale</h3></section></section>
<section data-quiz="vf1" data-id="s4"><h2>Vrai ou faux</h2></section>
"""
QUIZ_COURS = json.dumps([
    {"id": "qcm1", "type": "qcm", "question": "Quelle <couleur> ?", "options": ["Rouge", "Vert", "Bleu"], "bonne": 1},
    {"id": "vf1", "type": "vrai_faux", "question": "Le ciel est bleu", "bonne": True},
    {"id": "sondage", "type": "qcm", "question": "Sans bonne réponse", "options": ["A", "B"]},
    {"id": "nuage", "type": "nuage", "question": "Des mots"},
    {"id": "trou1", "type": "trou", "question": "Complétez", "texte": "L'usage est ___.", "bonnes": ["interdit"]},
    {"id": "nb1", "type": "nombre", "question": "Combien ?", "bonne": 10, "unite": "%"},
    {"id": "multi1", "type": "multi", "question": "Lesquels ?", "options": ["a", "b", "c"], "bonnes": [0, 2]},
], ensure_ascii=False)
CHAPITRES = [
    {"debut": 0.0, "titre": "Introduction & cadre", "h": 0},
    {"debut": 10.0, "titre": "Quiz : Quelle <couleur> ?", "h": 1},
    {"debut": 20.0, "titre": "Conclusion", "h": 2},
]


def fabriquer_replay(dossier_presentation: Path, id: str = "2026-11-10_0902", duree: float = 30.0, chapitres: list | None = None) -> Path:
    """Replay de test : vidéo lavfi (mire + son) de `duree` s, journal.json avec 3 chapitres (h 0, 1, 2)."""
    replay = dossier_presentation / "replays" / id
    replay.mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=s=320x180:r=30:d={duree}", "-f", "lavfi", "-i", f"sine=f=440:d={duree}",
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-c:a", "aac", "-shortest", str(replay / "replay.mp4")],
        check=True,
    )
    (replay / "journal.json").write_text(json.dumps({
        "titre": "Replay <test> & co", "date": "2026-11-10T09:02:00", "duree": duree, "chapitres": chapitres if chapitres is not None else CHAPITRES,
    }, ensure_ascii=False), encoding="utf-8")
    return replay


@pytest.fixture
def cours(tmp_path):
    """Présentation « cours » (4 diapositives dont deux data-quiz) et son replay de test."""
    prez = creer_presentation(tmp_path / "presentations", "cours", "Cours test", slides=SLIDES_COURS, quiz=QUIZ_COURS)
    return prez, fabriquer_replay(prez)


# ---------------------------------------------------------------------------------------
# Rattachement des questions (sans ffmpeg)
# ---------------------------------------------------------------------------------------


def test_questions_diapositives_et_rattachement(tmp_path):
    assert questions_diapositives(SLIDES_COURS) == [None, "qcm1", None, "vf1"]
    assert questions_diapositives("") == []
    prez = creer_presentation(tmp_path / "p", "cours", "Cours", slides=SLIDES_COURS, quiz=QUIZ_COURS)
    # Chapitre h=0 couvre [0,1[ ; h=1 couvre [1,2[ (qcm1) ; h=2 couvre jusqu'à la fin (vf1).
    par_chapitre = module_scorm.questions_par_chapitre(CHAPITRES, prez)
    assert [[q["id"] for q in qs] for qs in par_chapitre] == [[], ["qcm1"], ["vf1"]]
    assert par_chapitre[1][0] == {"id": "qcm1", "type": "qcm", "question": "Quelle <couleur> ?", "options": ["Rouge", "Vert", "Bleu"], "bonne": 1}
    assert par_chapitre[2][0] == {"id": "vf1", "type": "vrai_faux", "question": "Le ciel est bleu", "bonne": True}
    # Un chapitre sans h n'a pas de questions ; un retour en arrière ne couvre que sa diapositive ; pas de doublon.
    chapitres = [{"debut": 0.0, "titre": "A"}, {"debut": 5.0, "titre": "B", "h": 3}, {"debut": 15.0, "titre": "C", "h": 1}, {"debut": 25.0, "titre": "D", "h": 3}]
    assert [[q["id"] for q in qs] for qs in module_scorm.questions_par_chapitre(chapitres, prez)] == [[], ["vf1"], ["qcm1"], []]
    # Types exportables : seuls ceux avec bonne réponse ; trou/nombre/multi gardent leurs champs.
    from server.quiz import questions_depuis_liste

    questions = questions_depuis_liste(json.loads(QUIZ_COURS))
    assert module_scorm.question_exportable(questions["sondage"]) is None
    assert module_scorm.question_exportable(questions["nuage"]) is None
    assert module_scorm.question_exportable(questions["trou1"]) == {"id": "trou1", "type": "trou", "question": "Complétez", "texte": "L'usage est ___.", "bonnes": ["interdit"]}
    assert module_scorm.question_exportable(questions["nb1"]) == {"id": "nb1", "type": "nombre", "question": "Combien ?", "bonne": 10, "unite": "%"}
    assert module_scorm.question_exportable(questions["multi1"]) == {"id": "multi1", "type": "multi", "question": "Lesquels ?", "options": ["a", "b", "c"], "bonnes": [0, 2]}
    # Pas de slides.html : aucune question, pas d'erreur.
    assert module_scorm.questions_par_chapitre(CHAPITRES, tmp_path / "absent") == [[], [], []]


def test_manifeste_echappe_et_bien_forme():
    xml = module_scorm.manifeste("amphi-x-1", 'Titre <a> & "b"', ["imsmanifest.xml", "index.html", "chapitres/01-a&b.mp4"])
    racine = ET.fromstring(xml)
    ns = {"cp": "http://www.imsproject.org/xsd/imscp_rootv1p1p2", "adlcp": "http://www.adlnet.org/xsd/adlcp_rootv1p2"}
    assert racine.tag == "{http://www.imsproject.org/xsd/imscp_rootv1p1p2}manifest" and racine.get("identifier") == "amphi-x-1"
    assert racine.find("cp:metadata/cp:schemaversion", ns).text == "1.2"
    assert racine.find("cp:organizations/cp:organization/cp:item/cp:title", ns).text == 'Titre <a> & "b"'
    assert racine.find("cp:organizations/cp:organization/cp:item/adlcp:masteryscore", ns).text == "50"
    assert [f.get("href") for f in racine.findall("cp:resources/cp:resource/cp:file", ns)] == ["imsmanifest.xml", "index.html", "chapitres/01-a&b.mp4"]
    assert module_scorm._identifiant_xml("2026 x/y") == "amphi-2026-x-y"


# ---------------------------------------------------------------------------------------
# Construction du paquet (ffmpeg)
# ---------------------------------------------------------------------------------------


def verifier_paquet(zip_path: Path) -> tuple[list[str], dict, str]:
    """Manifeste bien formé (schemaversion 1.2), <file href> ⇔ fichiers du zip ; renvoie (fichiers, module, index.html)."""
    with zipfile.ZipFile(zip_path) as z:
        noms = sorted(n for n in z.namelist() if not n.endswith("/"))
        racine = ET.fromstring(z.read("imsmanifest.xml"))
        module = json.loads(z.read("module.json"))
        index = z.read("index.html").decode("utf-8")
    ns = {"cp": "http://www.imsproject.org/xsd/imscp_rootv1p1p2", "adlcp": "http://www.adlnet.org/xsd/adlcp_rootv1p2"}
    assert racine.find("cp:metadata/cp:schema", ns).text == "ADL SCORM"
    assert racine.find("cp:metadata/cp:schemaversion", ns).text == "1.2"
    ressource = racine.find("cp:resources/cp:resource", ns)
    assert ressource.get("href") == "index.html" and ressource.get("{http://www.adlnet.org/xsd/adlcp_rootv1p2}scormtype") == "sco"
    assert racine.find("cp:organizations/cp:organization/cp:item/adlcp:masteryscore", ns).text == "50"
    assert sorted(f.get("href") for f in ressource.findall("cp:file", ns)) == noms
    assert '<script id="module" type="application/json">' in index
    debut = index.index('<script id="module" type="application/json">') + len('<script id="module" type="application/json">')
    assert json.loads(index[debut : index.index("</script>", debut)]) == module
    return noms, module, index


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg absent")
def test_construire_scorm_par_chapitre(cours):
    prez, replay = cours
    lignes = []
    zip_path = module_scorm.construire_scorm(prez, replay, journal=lignes.append)
    assert zip_path == replay / "scorm" / "cours-2026-11-10_0902-scorm.zip" and zip_path.is_file()
    noms, module, index = verifier_paquet(zip_path)
    assert [n for n in noms if n.endswith(".mp4")] == ["chapitres/01-introduction-cadre.mp4", "chapitres/02-quiz-quelle-couleur.mp4", "chapitres/03-conclusion.mp4"]
    assert "medias/affiche.jpg" in noms and "scorm.js" in noms and "video/replay.mp4" not in noms
    assert len([n for n in noms if n.endswith(".mp4")]) == len(module["chapitres"]) == 3
    assert module["titre"] == "Replay <test> & co" and module["quiz"] is True and module["video_unique"] is False
    assert module["identifiant"] == "amphi-cours-2026-11-10_0902"
    assert [c["numero"] for c in module["chapitres"]] == [1, 2, 3]
    assert [c["video"] for c in module["chapitres"]] == ["chapitres/01-introduction-cadre.mp4", "chapitres/02-quiz-quelle-couleur.mp4", "chapitres/03-conclusion.mp4"]
    assert module["chapitres"][1]["duree"] == 10.0 and module["chapitres"][2]["fin"] == 30.0
    # La qcm est sur le chapitre qui couvre sa diapositive (h=1), la vrai_faux sur le dernier (h=2 jusqu'à la fin).
    assert [[q["id"] for q in c["questions"]] for c in module["chapitres"]] == [[], ["qcm1"], ["vf1"]]
    assert module["chapitres"][1]["questions"][0]["bonne"] == 1 and module["chapitres"][2]["questions"][0]["bonne"] is True
    # Titres échappés dans le HTML et le XML.
    assert "<title>Replay &lt;test&gt; &amp; co</title>" in index
    with zipfile.ZipFile(zip_path) as z:
        assert "<title>Replay &lt;test&gt; &amp; co</title>" in z.read("imsmanifest.xml").decode("utf-8")
    # Les extraits ont été découpés dans le replay et sont réutilisés au second passage.
    assert sorted(p.name for p in (replay / "extraits").iterdir()) == ["01-introduction-cadre.mp4", "02-quiz-quelle-couleur.mp4", "03-conclusion.mp4"]
    assert any("3 extrait(s) à découper" in l for l in lignes)
    lignes.clear()
    module_scorm.construire_scorm(prez, replay, titre="  Module   SCORM ", journal=lignes.append)
    assert any("extraits déjà présents" in l for l in lignes)
    meta = json.loads((replay / "scorm" / "scorm.json").read_text(encoding="utf-8"))
    assert meta["fichier"] == "cours-2026-11-10_0902-scorm.zip" and meta["taille"] == zip_path.stat().st_size and meta["titre"] == "Module SCORM"
    assert meta["chapitres"] == 3 and meta["questions"] == 2 and meta["inclure_quiz"] is True and meta["video_unique"] is False and meta["genere"]
    assert module_scorm.lire_scorm(replay)["fichier"] == meta["fichier"]
    assert [p.name for p in (replay / "scorm").iterdir() if p.suffix == ".zip"] == [meta["fichier"]]  # un seul zip conservé
    assert module_scorm.supprimer_scorm(replay) is True and not (replay / "scorm").exists() and module_scorm.lire_scorm(replay) is None
    assert module_scorm.supprimer_scorm(replay) is False


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg absent")
def test_construire_scorm_sans_quiz_et_video_unique(cours):
    prez, replay = cours
    zip_path = module_scorm.construire_scorm(prez, replay, video_unique=True)
    noms, module, _ = verifier_paquet(zip_path)
    assert "video/replay.mp4" in noms and not any(n.startswith("chapitres/") for n in noms)
    assert module["video_unique"] is True and all(c["video"] == "video/replay.mp4" for c in module["chapitres"])
    assert [(c["debut"], c["fin"]) for c in module["chapitres"]] == [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)]
    assert [[q["id"] for q in c["questions"]] for c in module["chapitres"]] == [[], ["qcm1"], ["vf1"]]
    assert not (replay / "extraits").exists()  # la vidéo unique ne découpe rien
    assert json.loads((replay / "scorm" / "scorm.json").read_text(encoding="utf-8"))["video_unique"] is True

    zip_path = module_scorm.construire_scorm(prez, replay, inclure_quiz=False)
    noms, module, _ = verifier_paquet(zip_path)
    assert sum(len(c["questions"]) for c in module["chapitres"]) == 0 and module["quiz"] is False
    assert json.loads((replay / "scorm" / "scorm.json").read_text(encoding="utf-8"))["questions"] == 0
    assert (replay / "extraits").is_dir()  # le mode par chapitre découpe les extraits


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg absent")
def test_construire_scorm_sans_chapitres(tmp_path):
    prez = creer_presentation(tmp_path / "presentations", "cours", "Cours", slides=SLIDES_COURS, quiz=QUIZ_COURS)
    replay = fabriquer_replay(prez, duree=6.0, chapitres=[])
    zip_path = module_scorm.construire_scorm(prez, replay, titre="Sans chapitres")
    noms, module, _ = verifier_paquet(zip_path)
    assert len(module["chapitres"]) == 1 and module["chapitres"][0]["titre"] == "Sans chapitres" and module["chapitres"][0]["questions"] == []
    assert [n for n in noms if n.endswith(".mp4")] == ["chapitres/01-sans-chapitres.mp4"] and not (replay / "extraits").exists()
    # Sans replay.mp4 : erreur lisible.
    (replay / "replay.mp4").unlink()
    with pytest.raises(ValueError):
        module_scorm.construire_scorm(prez, replay)


# ---------------------------------------------------------------------------------------
# Routes et ligne de commande
# ---------------------------------------------------------------------------------------


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg absent")
def test_routes_scorm(client):
    prez = creer_presentation(client.presentations, "cours", "Cours test", slides=SLIDES_COURS, quiz=QUIZ_COURS)
    fabriquer_replay(prez)
    id = "2026-11-10_0902"
    detail = client.get(f"/api/presentations/cours/replays/{id}").json()
    assert detail["scorm"] is None and client.get("/api/presentations/cours/replays").json()[0]["scorm"] is None
    assert client.post(f"/api/presentations/cours/replays/{id}/scorm", json={}).status_code == 403
    assert client.post(jeton("/api/presentations/cours/replays/inconnu/scorm"), json={}).status_code == 404
    assert client.delete(jeton(f"/api/presentations/cours/replays/{id}/scorm")).status_code == 404  # pas encore de paquet

    r = client.post(jeton(f"/api/presentations/cours/replays/{id}/scorm"), json={"titre": "Module du cours", "inclure_quiz": True, "video_unique": False})
    assert r.status_code == 200, r.text
    t = attendre_tache(client, r.json()["tache"])
    assert t["etat"] == "terminee", "\n".join(t["journal"])
    assert [(e["nom"], e["etat"]) for e in t["etapes"]] == [("extraits", "terminee"), ("scorm", "terminee")]
    assert t["replay"]["scorm"]["chapitres"] == 3 and t["replay"]["scorm"]["questions"] == 2 and t["replay"]["fichier"] == "cours-2026-11-10_0902-scorm.zip"

    detail = client.get(f"/api/presentations/cours/replays/{id}").json()
    scorm = detail["scorm"]
    assert scorm["fichier"] == "cours-2026-11-10_0902-scorm.zip" and scorm["taille"] > 0 and scorm["genere"] and scorm["chapitres"] == 3 and scorm["questions"] == 2
    assert scorm["titre"] == "Module du cours" and scorm["inclure_quiz"] is True and scorm["video_unique"] is False
    assert detail["extraits"] and len(detail["extraits"]) == 3
    assert client.get("/api/presentations/cours/replays").json()[0]["scorm"] == scorm["fichier"]

    r = client.get(f"/p/cours/replays/{id}/scorm/{scorm['fichier']}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == f'attachment; filename="{scorm["fichier"]}"'
    assert len(r.content) == scorm["taille"] and zipfile.ZipFile(__import__("io").BytesIO(r.content)).testzip() is None
    for chemin in ("scorm/autre.zip", "scorm/scorm.json", "scorm/..%2Fjournal.json", "scorm%2F..%2Fjournal.json", "scorm/x.zip/../journal.json"):
        assert client.get(f"/p/cours/replays/{id}/{chemin}").status_code == 404, chemin

    # Vidéo unique, sans quiz : une seule étape.
    r = client.post(jeton(f"/api/presentations/cours/replays/{id}/scorm"), json={"inclure_quiz": False, "video_unique": True})
    t = attendre_tache(client, r.json()["tache"])
    assert t["etat"] == "terminee" and [e["nom"] for e in t["etapes"]] == ["scorm"]
    scorm = client.get(f"/api/presentations/cours/replays/{id}").json()["scorm"]
    assert scorm["questions"] == 0 and scorm["video_unique"] is True

    assert client.delete(f"/api/presentations/cours/replays/{id}/scorm").status_code == 403
    assert client.delete(jeton(f"/api/presentations/cours/replays/{id}/scorm")).status_code == 200
    assert client.get(f"/api/presentations/cours/replays/{id}").json()["scorm"] is None
    assert not (prez / "replays" / id / "scorm").exists()
    assert client.get(f"/p/cours/replays/{id}/scorm/cours-2026-11-10_0902-scorm.zip").status_code == 404


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg absent")
def test_cli_exporter_scorm(cours):
    prez, replay = cours
    r = subprocess.run([sys.executable, str(RACINE / "tools" / "exporter_scorm.py"), str(replay), "--sans-quiz", "--video-unique", "--titre", "Via CLI", "-q"],
                       capture_output=True, text=True, cwd=str(RACINE))
    assert r.returncode == 0, r.stderr
    zip_path = Path(r.stdout.strip())
    assert zip_path == replay / "scorm" / "cours-2026-11-10_0902-scorm.zip" and zip_path.is_file()
    noms, module, _ = verifier_paquet(zip_path)
    assert module["titre"] == "Via CLI" and module["video_unique"] is True and sum(len(c["questions"]) for c in module["chapitres"]) == 0
    r = subprocess.run([sys.executable, str(RACINE / "tools" / "exporter_scorm.py"), str(replay.parent / "absent")], capture_output=True, text=True, cwd=str(RACINE))
    assert r.returncode == 2 and "replay.mp4" in r.stderr

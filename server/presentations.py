"""Bibliothèque de présentations : un dossier par présentation, une seule active (voir docs/presentations.md).

    presentations/<slug>/presentation.json   { titre, description, theme, cree, modifie, contact? }
    presentations/<slug>/slides.html         fragment HTML du deck (docs/format-deck.md)
    presentations/<slug>/quiz.json           questions du quiz
    presentations/<slug>/medias/             images, Lottie, vidéos
    data/active.json                         { "slug": … } : état d'exécution, hors git

Aucune dépendance au serveur : les fonctions lèvent ValueError (entrée invalide), KeyError (slug
inconnu), FileExistsError (slug déjà pris) ou RuntimeError (conversion échouée) ; app.py traduit en HTTP.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SLUG = re.compile(r"^[a-z0-9-]{1,40}$")
RACINE = Path(__file__).resolve().parent.parent
CONVERTISSEUR = RACINE / "tools" / "pptx2reveal.py"
SECTION_OUVRANTE = re.compile(r"<section\b", re.I)
SECTION_TITRE = (
    '<section class="libre" data-id="s1" data-transition="fade" data-background-color="#ffffff">\n'
    '  <div class="el texte" data-id="e1" style="left:160px;top:400px;width:1600px;height:200px;font-size:84px;font-weight:700;text-align:center;color:#1a1a1a">{titre}</div>\n'
    '  <div class="el texte" data-id="e2" style="left:160px;top:620px;width:1600px;height:100px;font-size:40px;text-align:center;color:#555555">{description}</div>\n'
    "</section>\n"
)


def _echapper(texte: str) -> str:
    return texte.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


CLES_CONTACT = ("nom", "fonction", "courriel", "linkedin")


def normaliser_contact(valeur) -> dict:
    """Contact de la présentation (docs/seance.md, C) : {nom, fonction, courriel, linkedin}, chaînes vides par défaut."""
    valeur = valeur if isinstance(valeur, dict) else {}
    return {cle: str(valeur.get(cle) or "").strip() for cle in CLES_CONTACT}


def _maintenant() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def valider_slug(slug: str) -> str:
    """Un slug est un nom de dossier sûr : [a-z0-9-]{1,40}. Lève ValueError sinon."""
    if not isinstance(slug, str) or not SLUG.match(slug):
        raise ValueError("slug invalide : lettres minuscules, chiffres et tirets (1 à 40 caractères)")
    return slug


def compter_diapositives(html: str) -> int:
    """Nombre de <section> (approximation : les sections imbriquées — rares dans ce format — sont comptées)."""
    return len(SECTION_OUVRANTE.findall(html))


class Bibliotheque:
    """Accès au dossier des présentations et à la présentation active."""

    def __init__(self, dossier: Path, fichier_active: Path, defaut: str | None = None) -> None:
        self.dossier = dossier
        self.fichier_active = fichier_active
        self.defaut = defaut
        self.dossier.mkdir(parents=True, exist_ok=True)

    # --- Chemins ---------------------------------------------------------------------

    def chemin(self, slug: str) -> Path:
        return self.dossier / valider_slug(slug)

    def existe(self, slug: str) -> bool:
        try:
            d = self.chemin(slug)
        except ValueError:
            return False
        return d.is_dir() and ((d / "presentation.json").is_file() or (d / "slides.html").is_file())

    def exiger(self, slug: str) -> Path:
        if not self.existe(slug):
            raise KeyError(slug)
        return self.dossier / slug

    def fichier_slides(self, slug: str) -> Path:
        return self.exiger(slug) / "slides.html"

    def fichier_quiz(self, slug: str) -> Path:
        return self.exiger(slug) / "quiz.json"

    def dossier_medias(self, slug: str) -> Path:
        return self.exiger(slug) / "medias"

    def dossier_sauvegardes(self, slug: str) -> Path:
        return self.exiger(slug) / "sauvegardes"

    def media(self, slug: str, chemin: str) -> Path | None:
        """Fichier d'un média, ou None s'il sort du dossier (anti-traversée) ou n'existe pas."""
        medias = self.dossier_medias(slug).resolve()
        try:
            fichier = (medias / chemin).resolve()
        except (OSError, ValueError):
            return None
        if medias not in fichier.parents or not fichier.is_file():
            return None
        return fichier

    # --- Métadonnées ------------------------------------------------------------------

    def slugs(self) -> list[str]:
        return sorted(d.name for d in self.dossier.iterdir() if d.is_dir() and SLUG.match(d.name) and self.existe(d.name))

    def lire(self, slug: str) -> dict:
        d = self.exiger(slug)
        meta: dict = {}
        fichier = d / "presentation.json"
        if fichier.is_file():
            try:
                meta = json.loads(fichier.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        horodatage = datetime.fromtimestamp(d.stat().st_mtime).replace(microsecond=0).isoformat()
        return {
            "titre": str(meta.get("titre") or slug),
            "description": str(meta.get("description") or ""),
            "theme": str(meta.get("theme") or ""),
            "cree": str(meta.get("cree") or horodatage),
            "modifie": str(meta.get("modifie") or horodatage),
            "contact": normaliser_contact(meta.get("contact")),
        }

    def ecrire_meta(self, slug: str, meta: dict) -> None:
        if "contact" in meta and not any(normaliser_contact(meta["contact"]).values()):
            meta = {k: v for k, v in meta.items() if k != "contact"}  # un contact vide n'est pas écrit
        (self.exiger(slug) / "presentation.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def toucher(self, slug: str) -> None:
        """Met à jour la date de modification (après un enregistrement de slides.html ou de quiz.json)."""
        meta = self.lire(slug)
        meta["modifie"] = _maintenant()
        self.ecrire_meta(slug, meta)

    def compter_questions(self, slug: str) -> int:
        fichier = self.fichier_quiz(slug)
        if not fichier.is_file():
            return 0
        try:
            liste = json.loads(fichier.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        return len(liste) if isinstance(liste, list) else 0

    def resume(self, slug: str) -> dict:
        meta = self.lire(slug)
        slides = self.fichier_slides(slug)
        html = slides.read_text(encoding="utf-8") if slides.is_file() else ""
        return {
            "slug": slug,
            **meta,
            "active": slug == self.active(),
            "diapositives": compter_diapositives(html),
            "questions": self.compter_questions(slug),
        }

    def lister(self) -> list[dict]:
        return [self.resume(s) for s in self.slugs()]

    # --- Présentation active ---------------------------------------------------------

    def active(self) -> str | None:
        """Slug actif : active.json s'il désigne une présentation existante, sinon AMPHI_PRESENTATION, sinon le premier slug."""
        if self.fichier_active.is_file():
            try:
                slug = json.loads(self.fichier_active.read_text(encoding="utf-8")).get("slug")
            except (OSError, ValueError, AttributeError):
                slug = None
            if slug and self.existe(slug):
                return slug
        if self.defaut and self.existe(self.defaut):
            return self.defaut
        slugs = self.slugs()
        return slugs[0] if slugs else None

    def activer(self, slug: str) -> str:
        self.exiger(slug)
        self.fichier_active.parent.mkdir(parents=True, exist_ok=True)
        self.fichier_active.write_text(json.dumps({"slug": slug}) + "\n", encoding="utf-8")
        return slug

    # --- Création, duplication, renommage, suppression ---------------------------------

    def creer(self, slug: str, titre: str, description: str = "", theme: str = "", depuis: str | None = None) -> dict:
        valider_slug(slug)
        titre = (titre or "").strip() or slug
        if self.existe(slug) or (self.dossier / slug).exists():
            raise FileExistsError(slug)
        if depuis is not None:
            source = self.exiger(depuis)
            shutil.copytree(source, self.dossier / slug, ignore=shutil.ignore_patterns("sauvegardes", "presentation.json"))
            meta_source = self.lire(depuis)
            description = description if description else meta_source["description"]
            theme = theme if theme else meta_source["theme"]
        else:
            d = self.dossier / slug
            (d / "medias").mkdir(parents=True)
            (d / "slides.html").write_text(SECTION_TITRE.format(titre=_echapper(titre), description=_echapper(description)), encoding="utf-8")
            (d / "quiz.json").write_text("[]\n", encoding="utf-8")
        (self.dossier / slug / "medias").mkdir(exist_ok=True)
        maintenant = _maintenant()
        self.ecrire_meta(slug, {"titre": titre, "description": description, "theme": theme, "cree": maintenant, "modifie": maintenant})
        return self.resume(slug)

    def modifier(self, slug: str, titre: str | None = None, description: str | None = None, theme: str | None = None) -> dict:
        meta = self.lire(slug)
        if titre is not None and titre.strip():
            meta["titre"] = titre.strip()
        if description is not None:
            meta["description"] = description.strip()
        if theme is not None:
            if theme and not re.fullmatch(r"[a-z0-9_-]{1,40}", theme):
                raise ValueError("nom de thème invalide")
            meta["theme"] = theme
        meta["modifie"] = _maintenant()
        self.ecrire_meta(slug, meta)
        return self.resume(slug)

    def supprimer(self, slug: str) -> None:
        if slug == self.active():
            raise PermissionError("la présentation active ne peut pas être supprimée")
        shutil.rmtree(self.exiger(slug))

    # --- Import PowerPoint --------------------------------------------------------------

    def importer_pptx(self, slug: str, titre: str, contenu: bytes, nom_fichier: str = "presentation.pptx", delai: int = 300) -> dict:
        """Convertit un .pptx avec tools/pptx2reveal.py dans une nouvelle présentation.

        Renvoie {slug, diapositives, avertissements, raster}. Sans LibreOffice, la conversion se fait quand même
        (`--sans-raster`) et un avertissement l'indique. Lève RuntimeError si la conversion échoue.
        """
        valider_slug(slug)
        if self.existe(slug) or (self.dossier / slug).exists():
            raise FileExistsError(slug)
        titre = (titre or "").strip() or Path(nom_fichier).stem or slug
        destination = self.dossier / slug
        raster = shutil.which("soffice") is not None
        avertissements: list[str] = []
        with tempfile.TemporaryDirectory(prefix="amphi-pptx-") as tmp:
            pptx = Path(tmp) / (re.sub(r"[^A-Za-z0-9._-]+", "_", Path(nom_fichier).name) or "presentation.pptx")
            if pptx.suffix.lower() != ".pptx":
                pptx = pptx.with_suffix(".pptx")
            pptx.write_bytes(contenu)
            commande = [sys.executable, str(CONVERTISSEUR), str(pptx), "--sortie", str(destination / "slides.html"),
                        "--medias", str(destination / "medias"), "--prefixe-medias", "medias", "--qr-premiere"]
            if not raster:
                commande.append("--sans-raster")
                avertissements.append("LibreOffice (soffice) absent : les objets complexes (SmartArt, groupes, graphiques) ne sont pas rastérisés")
            try:
                resultat = subprocess.run(commande, capture_output=True, text=True, timeout=delai, cwd=str(RACINE))
            except subprocess.TimeoutExpired:
                shutil.rmtree(destination, ignore_errors=True)
                raise RuntimeError(f"conversion interrompue après {delai} s")
        for ligne in (resultat.stderr or "").splitlines():
            ligne = ligne.strip()
            if ligne.startswith("avertissement : "):
                avertissements.append(ligne[len("avertissement : "):])
            elif ligne and not ligne.startswith(("Traceback", "  ")):
                avertissements.append(ligne)
        if resultat.returncode != 0 or not (destination / "slides.html").is_file():
            shutil.rmtree(destination, ignore_errors=True)
            detail = (resultat.stderr or resultat.stdout or "").strip().splitlines()
            raise RuntimeError("conversion échouée : " + (detail[-1] if detail else f"code {resultat.returncode}"))
        (destination / "medias").mkdir(exist_ok=True)
        (destination / "quiz.json").write_text("[]\n", encoding="utf-8")
        maintenant = _maintenant()
        self.ecrire_meta(slug, {"titre": titre, "description": "", "theme": "", "cree": maintenant, "modifie": maintenant})
        return {**self.resume(slug), "avertissements": avertissements, "raster": raster}

# Bibliothèque de présentations

Plusieurs présentations coexistent ; une seule est **active** (celle que voient l'écran, OBS, la télécommande et les participants).

## Arborescence

```
presentations/
  iag-v1/                 ← slug : [a-z0-9-]{1,40}
    presentation.json     ← { "titre": "…", "description": "…", "theme": "" | "dsfr", "cree": "2026-11-08T…", "modifie": "…",
                              "camera": { "largeur": 300, "hauteur": 300, "position": "bas-droite", "marge": 20 } }  (optionnel, docs/zone-camera.md ; null = pas de vignette)
    slides.html           ← format docs/format-deck.md
    quiz.json             ← questions (format section 3)
    medias/               ← images, JSON Lottie, vidéos
  iag-v2/
    …
data/active.json          ← { "slug": "iag-v1" } (état d'exécution, pas dans git) ; défaut : AMPHI_PRESENTATION ou le premier slug trié
deck/                     ← runtime commun : index.html, sync.js, quiz-deck.js, remote.html, editeur.*, theme.css, themes/*.css, vendor/
```

Le dossier `deck/` ne contient plus de contenu de séance. Les anciens `deck/slides.html`, `deck/quiz.json`, `deck/medias/` sont migrés dans `presentations/iag-v1/`.

## Service des fichiers

| Chemin | Contenu |
| --- | --- |
| `/deck/slides.html`, `/deck/medias/…` | ceux de la présentation **active** (routes explicites, prioritaires sur le montage statique de `deck/`) |
| `/p/{slug}/slides.html`, `/p/{slug}/medias/…` | ceux d'une présentation donnée (lecture publique : sans état, pas de secret) |
| `/deck/themes/{theme}.css` | feuille de thème optionnelle, chargée par `index.html` après `theme.css` si `presentation.json.theme` est non vide |

## API (jeton requis sauf mention)

| Méthode | Chemin | Rôle |
| --- | --- | --- |
| GET | `/api/presentations` | liste `[{slug, titre, description, theme, active, modifie, diapositives, questions}]` — public |
| GET | `/api/presentations/active` | `{slug, titre, theme}` — public (utilisé par `index.html`) |
| POST | `/api/presentations/{slug}/activer` | rend active : recharge les questions du quiz (question ouverte fermée, révélation effacée) et envoie `{"type":"recharger"}` sur `/ws/deck` |
| POST | `/api/presentations` | corps `{slug, titre, description?, theme?, depuis?: slug}` : crée (vide : une section titre) ou duplique `depuis` |
| PUT | `/api/presentations/{slug}` | `{titre?, description?, theme?}` |
| DELETE | `/api/presentations/{slug}` | refusé si active |
| POST | `/api/presentations/importer` | multipart `fichier` (.pptx) + champs `slug`, `titre` : convertit avec `tools/pptx2reveal.py` (sous-processus, `--qr-premiere`), médias dans le dossier de la présentation ; renvoie `{slug, diapositives, avertissements}` ; 503 si LibreOffice absent **uniquement** pour la rastérisation (la conversion se fait quand même avec `--sans-raster`, avertissement renvoyé) |
| GET/PUT | `/api/deck/slides?prez=slug` | sans `prez` : la présentation active |
| GET/POST | `/api/deck/medias?prez=slug` | idem |
| GET/PUT | `/api/quiz/questions?prez=slug` | idem ; la PUT sur la présentation active recharge le quiz |

## Pages

| URL | Rôle |
| --- | --- |
| `/presentations?token=` → `/deck/presentations.html?token=` | accueil : cartes (titre, description, nombre de diapositives et de questions, vignette de la première diapositive, badge « active »), boutons **Lancer**, **Éditer**, **Dupliquer**, **Renommer**, **Supprimer** ; boutons **Nouvelle** et **Importer un PowerPoint** ; liens vers le projecteur, OBS, la télécommande, les participants |
| `/deck/editeur.html?token=&prez=slug` | l'éditeur sur une présentation donnée (sans `prez` : l'active). Les `src="medias/…"` sont affichés via `/p/{slug}/medias/…` mais sérialisés inchangés |
| `/deck/?role=…` | la présentation active, comme avant ; `?prez=slug` permet de prévisualiser une autre sans la rendre active (pas de synchronisation dans ce cas) |

La télécommande affiche le titre de la présentation active dans son en-tête.

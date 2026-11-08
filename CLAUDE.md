# amphi-replay — repères pour Claude Code

Captation solo en amphithéâtre : deck HTML synchronisé (projecteur PC 1, OBS PC 2, télécommande téléphone), quiz en direct type Kahoot/Mentimeter, réalisation OBS automatique, post-production (recalage audio, chapitres, replays, SCORM). Cahier des charges : `PRD.md`. Vue d'ensemble : `docs/logigramme-complet.html`.

Tout est en **français** : code, noms, commentaires, messages, commits.

## Où est quoi

| Dossier | Contenu |
| --- | --- |
| `server/` | FastAPI + SQLite. `app.py` (routes, WebSockets `/ws/deck` et `/ws/quiz`), `quiz.py` (12 types, points, classement), `presentations.py` (bibliothèque), `journal.py` (journal de séance, titres et marqueurs des diapos), `questions.py` (questions des participants, séquence Q/R), `replays.py` (assemblage, chapitres, extraits, tâches), `scorm.py` + `scorm_lecteur/` |
| `server/static/` | pages publiques : `participant.html` (/q), `resultats.html`, `classement.html` (dont la cérémonie) |
| `deck/` | runtime commun du deck : `index.html` (rôles scene/obs/remote), `sync.js`, `quiz-deck.js`, `camera.js`, `remote.html`, `editeur.*`, `presentations.*`, `postprod.*`, `replay.*`, `theme.css`, `themes/dsfr.css` |
| `presentations/<slug>/` | une présentation : `presentation.json`, `slides.html`, `quiz.json`, `medias/`, `replays/` (hors git) |
| `tools/` | `lanceur_seance.py` (fenêtre de lancement PC 2, via `lancer-seance.bat`), `pptx2reveal.py` (import PowerPoint), `pdf2reveal.py`, `inserer_quiz.py`, `seance_iag.py`, `reserver_camera.py`, `exporter_scorm.py`, `charge.py`, `fetch_reveal.sh` |
| `postprod/assembler.py` | recalage son téléphone ↔ vidéo OBS ; `postprod/voix.py` : voix podcast/radio (débruitage, EQ, compression, -16 LUFS) |
| `obs/` | config OBS (`README.md`) et pont `obs_quiz_bridge.py` |
| `deploy/` | VPS : `install.sh`, `deploy.sh`, `sauvegarde.sh`, unité systemd, Nginx/Caddy ; PC 2 : `postprod.sh`, `postprod.ps1` |
| `docs/` | spécifications : `format-deck.md` (format HTML des diapos, API), `presentations.md`, `postprod.md`, `questions.md`, `scorm.md`, `seance.md`, `zone-camera.md`, `fiche-mise-en-route.md` |

Lire la spécification concernée dans `docs/` avant de modifier une fonction.

## Commandes

- Tests : `.venv/bin/python -m pytest -q` (Windows : `.venv\Scripts\python -m pytest -q`) ; ffmpeg requis pour les tests d'assemblage.
- Serveur local : `AMPHI_JETON=test .venv/bin/uvicorn server.app:app --reload`, puis `/presentations?token=test`.
- Post-production sur le PC 2 (Windows) : `powershell -ExecutionPolicy Bypass -File deploy\postprod.ps1 --seance https://q.phare360.com`.
- Pont OBS : `python obs/obs_quiz_bridge.py wss://q.phare360.com --obs-mdp <mot de passe obs-websocket>`.
- Dépendances front (reveal.js, lottie, polices Marianne) : `bash tools/fetch_reveal.sh` (dans `deck/vendor/`, hors git).

## Production

- VPS Hostinger (Ubuntu 24.04), `https://q.phare360.com`, service systemd `amphi-replay`, Nginx déjà présent pour d'autres sites (site dédié + certbot ; ne jamais toucher aux autres sites). Mise à jour : `bash /opt/amphi-replay/deploy/deploy.sh`.
- Le jeton est dans `/etc/amphi-replay.env` sur le VPS : ne jamais l'écrire dans le dépôt ni le demander en clair.
- Les présentations modifiées sur le serveur (éditeur) **font foi** : `deploy.sh` les conserve et met la version GitHub de côté en `*.github`. Avant de modifier un `presentations/*/slides.html` dans le dépôt, demander si une version plus récente existe sur le serveur.

## Pièges connus

- Windows PowerShell 5 : pas de `&&` ; dans les `.ps1`, `$ErrorActionPreference = "Continue"` (sinon stderr des programmes devient fatal).
- Fins de ligne : `.gitattributes` force LF (sauf `.ps1`) ; un `.sh` en CRLF casse `set -o pipefail`.
- Zone caméra OBS : 300×300 en bas à droite (zone réservée `x ≥ 1600, y ≥ 760`) ; rien d'important dedans (`tools/reserver_camera.py`, calque « Caméra » de l'éditeur).
- Le serveur ne transporte jamais de vidéo ; la captation ne doit pas dépendre du serveur (F9 dans OBS reste le secours).
- Après une modification de l'interface, vérifier dans un vrai navigateur (Playwright) en plus des tests.

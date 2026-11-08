# amphi-replay

Captation solo en amphithéâtre : deck HTML synchronisé sur le projecteur et dans OBS, télécommande sur téléphone, quiz en direct par QR code, réalisation automatique à trois plans, et replay MP4 monté moins de dix minutes après la fin. Une seule personne est intervenant, réalisateur et technicien.

Le cahier des charges complet est dans [PRD.md](PRD.md). Le serveur est prévu pour un **VPS Infomaniak** (voir [Déploiement](#déploiement-sur-le-vps-infomaniak)).

## Architecture

```
téléphone (remote, hotspot 4G) ──┐
                                 │  WebSocket {h, v, f} + commandes quiz
                         VPS Infomaniak : FastAPI + SQLite, derrière Caddy (HTTPS)
                                 │
        ┌────────────────────────┼─────────────────────────┐
   PC 1 : deck ?role=scene   PC 2 : OBS, deck ?role=obs    participants : /q
   (vidéoprojecteur)          (DSLR + webcam + deck)        (QR code, anonymes)
```

Le serveur ne transporte aucune vidéo : il relaie un état et chaque machine rend le deck localement. Si le serveur tombe, le cours continue (clavier sur le PC 1, quiz à main levée) et l'enregistrement OBS ne dépend que de ses entrées locales.

## Arborescence

| Dossier | Contenu |
| --- | --- |
| `server/` | FastAPI : `/ws/deck`, `/etat`, `/deck/`, quiz (`/q`, `/q/resultats`, API admin par jeton), `/qr.png` |
| `deck/` | reveal.js : `index.html` (rôles `scene` / `obs` / `remote`), `sync.js`, `remote.html` (télécommande), `slides.html`, `quiz.json` |
| `postprod/assembler.py` | recalage audio téléphone ↔ OBS par corrélation croisée, dérive, `loudnorm`, remux MP4 |
| `obs/` | configuration OBS (scènes, Advanced Scene Switcher, raccourcis) et pont quiz → scène forcée |
| `tools/` | `pptx2reveal.py` (PowerPoint → sections libres, animations comprises), `pdf2reveal.py` (PDF → sections), `charge.py` (300 clients), `fetch_reveal.sh` |
| `deploy/` | `install.sh` (VPS Infomaniak), `deploy.sh`, `sauvegarde.sh`, `Caddyfile`, unité systemd, `env.example` |
| `docs/fiche-mise-en-route.md` | fiche imprimée pour le pupitre : branchements, séquence, gestes de secours |
| `tests/` | pytest : synchronisation, quiz, jeton, recalage audio (avec ffmpeg) |

## Démarrage en local

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
bash tools/fetch_reveal.sh                 # reveal.js dans deck/vendor/ (servi par nous, pas de CDN)
AMPHI_JETON=test .venv/bin/uvicorn server.app:app --reload
```

| Vue | URL |
| --- | --- |
| Projecteur (PC 1) | <http://localhost:8000/deck/?role=scene> |
| Source OBS (PC 2) | <http://localhost:8000/deck/?role=obs> |
| Télécommande (téléphone) | <http://localhost:8000/remote?token=test> |
| Participants | <http://localhost:8000/q> |
| Résultats (iframe du deck) | <http://localhost:8000/q/resultats> |
| Santé | <http://localhost:8000/sante> |

Tests : `.venv/bin/python -m pytest -q` (ffmpeg requis pour le test d'assemblage complet).

## Préparer le deck

1. Convertir le support :
   - PowerPoint : `python tools/pptx2reveal.py support.pptx --sortie deck/slides.html --medias deck/medias --qr-premiere`
     produit une `<section class="libre">` par diapositive (mise en page absolue dans une scène 1920 × 1080, texte riche,
     images extraites dans `deck/medias/s<diapo>_<n>.ext`, formes, tableaux, notes, fond, `data-transition`) et traduit les
     animations PowerPoint en fragments reveal.js (`fade-in`, `fade-up`, `zoom-in`, `fade-in-then-out`… avec `data-fragment-index`
     dans l'ordre des clics, puce par puce). Ce qui n'est pas géré devient `<!-- non converti : … -->` + un avertissement sur stderr.
     Les objets complexes (SmartArt, groupes de formes, formes libres, flèches et autres formes automatiques non
     rectangulaires, graphiques, objets OLE) sont rastérisés en images PNG transparentes (`deck/medias/s<diapo>_r<n>.png`)
     via LibreOffice, en un seul export PDF : installer `apt install libreoffice-impress` (`soffice` dans le PATH) ;
     `--sans-raster` désactive, et sans `soffice` ces objets sont ignorés avec un avertissement. Les objets rastérisés ne
     sont plus éditables en texte dans l'éditeur, mais restent déplaçables/redimensionnables comme des images ; un groupe
     ne contenant que du texte, des images et des rectangles/ovales reste converti forme par forme (texte éditable).
     Limites connues : effets d'animation non mappés (rendus en `fade-in`), déclencheurs/chemins personnalisés, dégradés
     et motifs approximés, rognage d'image ignoré, polices non embarquées (installer la police ou poser `font-family`
     dans l'éditeur).
   - PDF : `python tools/pdf2reveal.py support.pdf --sortie deck/slides.html --quiz q1:4 --quiz q2:9 --notes notes.md`
     (`--quiz ID:PAGE` insère une diapositive de quiz après la page ; `notes.md` contient des blocs `## Page N`).
2. Déclarer les questions dans `deck/quiz.json` (ou dans l'onglet Questions de l'éditeur) : voir [docs/format-deck.md](docs/format-deck.md), section 3.
3. Retoucher le deck dans l'éditeur (ci-dessous) ou à la main dans `deck/slides.html` : fragments, iframes vers des URL publiques sans état, Lottie, minuteurs, résultats de quiz, classement.
4. Vérifier le rendu dans les trois rôles ; le premier `<section data-qr>` affiche le QR code.

Le format HTML du deck (sections libres 1920×1080, éléments `data-amphi`, animations) et l'API sont décrits dans [docs/format-deck.md](docs/format-deck.md).

## Éditeur

Ouvrir <http://localhost:8000/deck/editeur.html?token=test> (jeton `AMPHI_JETON`). Fichiers : `deck/editeur.{html,css,js}`, JS vanilla sans dépendance.

- Colonne gauche : diapositives (vignettes, ajouter libre/titre/quiz, dupliquer, supprimer, monter/descendre, glisser-déposer). Centre : scène 1920×1080 à l'échelle, grille et règles. Droite : propriétés contextuelles.
- Section libre : sélection, déplacement et redimensionnement à la souris (Maj : ratio), double-clic pour éditer un texte en place (gras, listes, taille, couleur, alignement), barre « Ajouter » (texte, titre, image, forme, iframe, vidéo, Lottie, minuteur, résultats, nuage, classement, QR), alignement, ordre, animations (fragment, effet, index, aperçu), HTML brut.
- Section en flux : édition du HTML de chaque enfant, ajout/réordonnancement, conversion en section libre. Section de quiz : choix de la question, transformation en section libre (question + résultats + QR).
- Onglet Questions : éditeur de `quiz.json` (12 types, validation des identifiants et des options, insertion d'une diapositive de quiz).
- Enregistrer écrit `slides.html` (`PUT /api/deck/slides`, sauvegarde automatique de l'ancien) ; Aperçu ouvre `/deck/?role=scene` ; Importer charge un `.html` local.
- Raccourcis : Ctrl+S enregistrer, Ctrl+Z / Ctrl+Y annuler / rétablir (50 niveaux), Suppr supprimer, flèches déplacer de 1 px (Maj : 10 px), Ctrl+D dupliquer, Échap désélectionner.

## Quiz

Douze types de questions, façon Kahoot / Mentimeter, déclarés dans `deck/quiz.json` :

| Type | Participant | Résultat à l'écran | Points |
| --- | --- | --- | --- |
| `qcm` | choix unique (2–6) | barres, bonne réponse en vert à la révélation | oui si `bonne` |
| `multi` | choix multiples | barres | exact = 1000, partiel = moitié |
| `vrai_faux` | deux gros boutons | barres | oui si `bonne` |
| `emoji` | grille d'émoticônes (2–8) | barres avec émoticônes | optionnel |
| `rapidite` | choix unique chronométré | barres + les 5 plus rapides | dégressif sur tout le chrono |
| `buzzer` | un bouton BUZZ | les 5 premiers avec leur temps | 1000, 800, 640… |
| `nuage` | 1 à 3 mots | nuage de mots animé | — |
| `texte_libre` | texte court | cartes | — |
| `trou` | phrase à compléter | % de bonnes réponses + réponses fréquentes | oui (accents/casse ignorés) |
| `echelle` | note de 1 à 5 (réglable) | distribution + moyenne | — |
| `classement` | ordonner des items | rang moyen par item | fraction bien placée |
| `nombre` | estimation numérique | histogramme, médiane, les plus proches | proximité |

Barème (comme Kahoot) : 1000 points par bonne réponse, moins jusqu'à 50 % selon le temps de réponse si la question a un `chrono` (100 % pour `rapidite`). Les points sont attribués au bouton **Révéler** de la télécommande, qui affiche aussi la bonne réponse. Le **classement** (`/q/classement`, ou un élément « Classement » dans une diapositive) montre un podium animé et le top 10.

Déroulé depuis la télécommande : **Ouvrir** (le chrono ferme tout seul) → **Fermer** → **Révéler** → **Réinitialiser** si besoin. Les pages participant, résultats et classement se mettent à jour par WebSocket, avec repli en interrogation toutes les 3 s.

Anonymat : pas de compte, pas de nom réel. Le participant choisit un pseudo (ou en reçoit un au hasard, « Panda-42 ») qui ne sert qu'au classement ; identifiants et pseudos sont purgés par `deploy/sauvegarde.sh` après la séance. Seuls les pseudos des 5 premiers apparaissent à l'écran sur les questions de rapidité, de buzzer et d'estimation.

Charge : `python tools/charge.py https://<domaine> --token … --clients 300 --duree 10` (cible PRD : 300 WebSockets, 300 votes en 10 s, diffusion < 1 s, CPU < 50 % sur 1 vCPU). Le serveur regroupe les diffusions sous rafale (un état toutes les 200 ms) : mesuré en local, 300 votes en 5 s donnent une latence de vote p95 de 12 ms et une diffusion complète en 100 ms.

## Tester sans VPS : GitHub Codespaces

Bouton **Code → Codespaces → Create codespace on main** sur la page du dépôt. Le serveur s'installe et démarre tout seul (`.devcontainer/`, `deploy/codespaces.sh`), le port 8000 est rendu public et `DEMO.md` est réécrit avec les URL réelles (projecteur, télécommande, éditeur, participants). Jeton de démo : `demo`. Suffisant pour une répétition avec de vrais téléphones ; pour le jour J, préférer le VPS (domaine court pour le QR code, pas d'arrêt automatique après 30 minutes d'inactivité).

## Déploiement sur un VPS (Hostinger, Infomaniak…)

Prérequis : un VPS Debian 12 ou Ubuntu 24.04 avec accès root (1 vCPU / 2 Go suffisent), les ports 80 et 443 ouverts dans le pare-feu de l'hébergeur s'il y en a un, et un enregistrement DNS `A` du sous-domaine court (par exemple `q.mondomaine.fr`) vers l'IP du VPS, **propagé avant de lancer l'installation** (le certificat Let's Encrypt en dépend). Si Nginx tourne déjà sur le serveur pour d'autres sites, le script le détecte et ajoute un site dédié avec certbot (`deploy/nginx.conf`) au lieu d'installer Caddy ; les autres sites ne sont pas touchés.

```bash
ssh root@<ip du vps>
git clone https://github.com/RomainR-974/amphi-replay.git /opt/amphi-replay
bash /opt/amphi-replay/deploy/install.sh q.mondomaine.fr
```

Le script installe Python, ffmpeg et Caddy, crée l'utilisateur `amphi`, le venv, le service systemd et `/etc/amphi-replay.env` (jeton généré), puis affiche les URL. Caddy obtient le certificat Let's Encrypt tout seul.

- Mettre à jour : `sudo bash /opt/amphi-replay/deploy/deploy.sh` (`git pull` + redémarrage).
- Après la session : `sudo bash /opt/amphi-replay/deploy/sauvegarde.sh` (export CSV agrégé, copie SQLite, purge).
- Journal : `journalctl -u amphi-replay -f`.

## Post-production

```bash
python postprod/assembler.py enregistrement_obs.mkv telephone.wav --sortie replay_2026-11-12.mp4
```

Le script extrait la piste de référence d'OBS (piste 2 : micro DSLR), mesure le décalage sur les 60 premières secondes et la dérive sur les 60 dernières (les deux claps), corrige la dérive au-delà de 40 ms par rééchantillonnage, normalise à −16 LUFS avec passe-haut 80 Hz, et remuxe la vidéo sans réencodage. Un fichier `.json` à côté de la sortie consigne décalage, dérive et confiance. `--mesurer-seulement` pour vérifier avant d'écrire.

## OBS

Voir [obs/README.md](obs/README.md) : trois scènes (Intervenant 45 s, Diapositives + vignette 120 s, Amphithéâtre 15 s) enchaînées par Advanced Scene Switcher, raccourcis F1–F4, enregistrement MKV avec piste de référence séparée, et pont `obs/obs_quiz_bridge.py` qui force la scène des diapositives pendant une question ouverte.

## Jour J

[docs/fiche-mise-en-route.md](docs/fiche-mise-en-route.md) : ordre des branchements, séquence de démarrage et de fin (deux claps), gestes de secours pour chaque panne.

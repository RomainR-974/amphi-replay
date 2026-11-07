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
| `tools/` | `pdf2reveal.py` (PDF → sections), `charge.py` (300 clients), `fetch_reveal.sh` |
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

1. Convertir le PDF : `python tools/pdf2reveal.py support.pdf --sortie deck/slides.html --quiz q1:4 --quiz q2:9 --notes notes.md`
   (`--quiz ID:PAGE` insère une diapositive de quiz après la page ; `notes.md` contient des blocs `## Page N`).
2. Déclarer les questions dans `deck/quiz.json` : `{ "id", "question", "options" (2 à 5) }`.
3. Retoucher `deck/slides.html` : fragments (`class="fragment"`), iframes vers des URL publiques sans état.
4. Vérifier le rendu dans les trois rôles ; le premier `<section data-qr>` affiche le QR code.

## Quiz

Un seul type en v1 : QCM à choix unique, histogramme mis à jour en direct, anonymat strict (identifiant aléatoire en cookie, purgé après la session). L'orateur ouvre et ferme chaque question depuis la télécommande ; les pages participant et résultats se mettent à jour par WebSocket, avec repli en interrogation toutes les 3 s.

Charge : `python tools/charge.py https://<domaine> --token … --clients 300 --duree 10` (cible PRD : 300 WebSockets, 300 votes en 10 s, CPU < 50 % sur 1 vCPU).

## Déploiement sur le VPS Infomaniak

Prérequis dans le Manager Infomaniak : un VPS (VPS Lite ou Public Cloud, 1 vCPU / 2 Go suffisent, Debian 12 ou Ubuntu 24.04), les ports 80 et 443 ouverts dans le pare-feu du VPS, et un enregistrement DNS `A` du sous-domaine court (par exemple `q.mondomaine.fr`) vers l'IP du VPS.

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

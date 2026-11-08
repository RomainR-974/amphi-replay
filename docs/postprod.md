# Journal de séance, replays et post-production

Le montage se fait en direct dans OBS (trois plans). Après la séance, le PC 2 assemble le son du téléphone et
la vidéo OBS, range le replay dans la bibliothèque et le chapitre automatiquement à partir du journal des
diapositives. Les fichiers vidéo ne montent jamais sur le VPS : la post-production tourne **en local sur le PC 2**
(même code, même serveur, lancé par `deploy/postprod.sh` ou `deploy/postprod.ps1`).

## 1. Journal de séance (sur le serveur de la séance, en général le VPS)

Table SQLite `journal` dans la base existante (`CHEMIN_BDD`) :

| colonne | contenu |
| --- | --- |
| `horodatage` | `time.time()` (UTC) |
| `presentation` | slug actif |
| `type` | `diapositive` · `quiz_ouvrir` · `quiz_fermer` · `quiz_reveler` · `activation` |
| `h`, `v`, `f` | indices (type `diapositive`) |
| `question` | id (types quiz) |

- Chaque message `slide` du rôle `remote` ajoute une ligne `diapositive` (dédoublonnée si identique à la précédente).
- `ouvrir`, `fermer`, `reveler` et l'activation d'une présentation ajoutent leur ligne.
- `GET /api/journal?prez=slug&depuis=<epoch>&jusqua=<epoch>` (jeton) renvoie `[{horodatage, type, h, v, f, question}]` trié. CORS ouvert sur cette route (`Access-Control-Allow-Origin: *`) : la page de post-production du PC 2 l'interroge sur le VPS.
- `GET /api/journal/seances?prez=slug` (jeton) : regroupe le journal en **séances** (une séance = suite d'événements séparés de moins de 3 h) : `[{debut, fin, evenements, diapositives}]`, pour proposer « mardi 10 novembre 09:02–11:15 » dans la page de post-production.
- Les titres des diapositives viennent de `slides.html` de la présentation : premier `h1`/`h2`/`h3`/`.dsfr-titre`/`.el.texte` non vide de chaque `<section>` de premier niveau (texte sans balises, 80 caractères max), par index `h` ; une section `data-quiz` prend le libellé de sa question.

## 2. Replays d'une présentation

```
presentations/<slug>/replays/<AAAA-MM-JJ_HHMM>/
  replay.mp4          vidéo finale (chapitres MP4 embarqués)
  journal.json        mesures du recalage (sortie d'assembler.py) + métadonnées : titre, date, durée, source OBS, source audio, séance
  chapitres.txt       format YouTube : « 00:00 Titre »
  chapitres.vtt       WebVTT (pistes de chapitres pour le lecteur)
  extraits/NN-titre.mp4   capsules découpées par chapitre (optionnel)
  scorm/<slug>-<id>-scorm.zip + scorm.json   paquet SCORM 1.2 (optionnel, voir scorm.md)
```

`presentations/*/replays/` est ignoré par git. Un dossier de replay peut aussi être créé à la main (déposer `replay.mp4` suffit : le journal est reconstitué par ffprobe).

API (jeton requis sauf lecture) :

| Méthode | Chemin | Rôle |
| --- | --- | --- |
| GET | `/api/presentations/{slug}/replays` | `[{id, titre, date, duree, chapitres, extraits, taille}]` — public |
| GET | `/p/{slug}/replays/{id}/replay.mp4` | flux vidéo avec gestion de `Range` (FileResponse de Starlette la gère) — public |
| GET | `/p/{slug}/replays/{id}/chapitres.vtt` · `chapitres.txt` · `journal.json` · `extraits/<nom>.mp4` · `scorm/<nom>.zip` | public |
| PUT | `/api/presentations/{slug}/replays/{id}` | `{titre}` |
| DELETE | `/api/presentations/{slug}/replays/{id}` | supprime le dossier |

Pages :
- `/deck/presentations.html` : chaque carte liste ses replays (date, durée, nombre de chapitres) avec **Lire** et **Supprimer**, et un bouton **Post-production…** vers `/deck/postprod.html?token=&prez=slug`.
- `/deck/replay.html?prez=slug&id=…` : lecteur `<video>` (piste de chapitres VTT), liste des chapitres cliquables, liens de téléchargement (MP4, chapitres YouTube), liste des extraits.

## 3. Post-production (PC 2, serveur local)

Configuration : `AMPHI_CAPTATIONS` = dossier où OBS enregistre et où l'on copie l'audio du téléphone (défaut : `~/Vidéos/Captations` sous Linux/macOS, `%USERPROFILE%\Videos\Captations` sous Windows, sinon `data/captations`). `AMPHI_SEANCE_URL` (optionnel) = URL du serveur de la séance (ex. `https://q.phare360.com`) pour aller chercher le journal ; sinon le journal local.

`/deck/postprod.html?token=&prez=slug` :
1. Présentation (liste déroulante), **enregistrement OBS** et **audio du téléphone** choisis parmi les fichiers du dossier de captations (nom, taille, durée par ffprobe, date), ou uniquement la vidéo si le son OBS suffit (case « pas de recalage audio »).
2. Serveur de la séance (URL + jeton, préremplis depuis `AMPHI_SEANCE_URL` / le jeton courant) → bouton **Charger les séances** → liste des séances du journal ; sélection d'une séance. Le début de l'enregistrement est estimé = `mtime(fichier) − durée` et peut être corrigé par un **décalage manuel** (secondes, ±) ; un aperçu des chapitres calculés s'affiche avant lancement.
3. Options : titre du replay (défaut : titre de la présentation + date de la séance), garder la piste de référence OBS, générer les extraits par chapitre, générer le paquet SCORM (cochée ; étape « scorm » après les extraits, qui sont alors découpés dans tous les cas ; voir [scorm.md](scorm.md)).
4. **Assembler** → `POST /api/postprod/assembler` `{prez, video, audio?, seance_debut?, seance_fin?, decalage?, titre?, garder_reference?, extraits?, serveur?, jeton_serveur?}` → `{tache}` ; la tâche tourne en arrière-plan (thread + sous-processus) : `postprod/assembler.py` (ou remux simple sans audio) → chapitrage (ffmpeg `-i replay.mp4 -i ffmetadata -map_metadata 1 -c copy`) → VTT/TXT → extraits (`ffmpeg -ss -to -c copy`). `GET /api/postprod/taches/{id}` renvoie `{etat: en_cours|terminee|erreur, etapes: [...], journal: [lignes], replay?: {slug, id}}` ; la page interroge toutes les 2 s et affiche la progression (étapes et lignes du journal) puis un lien **Lire le replay**.
5. `GET /api/postprod/captations` (jeton) liste les fichiers du dossier (`{nom, taille, modifie, duree, genre: video|audio}`), triés du plus récent au plus ancien.
6. `POST /api/presentations/{slug}/replays/{id}/extraits` `{chapitres: [indices]}` découpe a posteriori.

Chapitrage : chapitres = événements `diapositive` de la séance ramenés en secondes depuis le début de l'enregistrement, filtrés aux instants dans `[0, durée]`, fusionnés si deux changements sont à moins de 20 s (on garde le dernier titre), premier chapitre forcé à 00:00. Les diapositives de quiz donnent des chapitres « Quiz : <question> ». Les `fragmentshown` (même `h`) ne créent pas de chapitre.

Lancement local : `deploy/postprod.sh` (Linux/macOS) et `deploy/postprod.ps1` (Windows) : créent le venv si besoin, installent les dépendances, vérifient ffmpeg, lancent uvicorn sur 127.0.0.1:8000 avec `AMPHI_JETON=local`, `AMPHI_BDD=data/local.sqlite`, `AMPHI_CAPTATIONS`, `AMPHI_SEANCE_URL` passés en argument, et ouvrent `http://127.0.0.1:8000/deck/postprod.html?token=local` dans le navigateur. Le dépôt sur le PC 2 est un simple `git clone` ; `git pull` pour mettre à jour.

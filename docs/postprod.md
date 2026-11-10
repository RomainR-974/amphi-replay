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
3. **Ce que je veux produire** (§5 bis) : formule (replay simple, chapitré, chapitré + activités, parcours complet), qualité (voix, vidéo allégée), titre du replay (défaut : titre de la présentation + date de la séance), coupe avant les questions-réponses, piste de référence OBS. Paquet SCORM : voir [scorm.md](scorm.md).
4. **Assembler** → `POST /api/postprod/assembler` `{prez, video, audio?, seance_debut?, seance_fin?, decalage?, titre?, garder_reference?, extraits?, serveur?, jeton_serveur?}` → `{tache}` ; la tâche tourne en arrière-plan (thread + sous-processus) : `postprod/assembler.py` (ou remux simple sans audio) → chapitrage (ffmpeg `-i replay.mp4 -i ffmetadata -map_metadata 1 -c copy`) → VTT/TXT → extraits (`ffmpeg -ss -to -c copy`). `GET /api/postprod/taches/{id}` renvoie `{etat: en_cours|terminee|erreur, etapes: [...], journal: [lignes], replay?: {slug, id}}` ; la page interroge toutes les 2 s et affiche la progression (étapes et lignes du journal) puis un lien **Lire le replay**.
5. `GET /api/postprod/captations` (jeton) liste les fichiers du dossier (`{nom, taille, modifie, duree, genre: video|audio}`), triés du plus récent au plus ancien.
6. `POST /api/presentations/{slug}/replays/{id}/extraits` `{chapitres: [indices]}` découpe a posteriori.

**Hors montage** : les diapositives marquées `data-obs="hors-montage"` dans l'éditeur sont coupées du replay. `GET /api/journal/seances` renvoie leurs index (`hors_montage`, version du serveur de séance), la page les transmet à l'assemblage (sinon : `slides.html` local). Passage coupé = de l'arrivée sur une diapositive hors montage à l'arrivée sur une diapositive qui ne l'est pas (ou la fin de la vidéo). Étape de tâche « hors montage », après la coupe Q/R et avant « vidéo » : `trim`/`atrim` des passages gardés puis `concat` (réencodage haute qualité, coupe à l'image près) ; chapitres recalés (ceux des passages coupés disparaissent) ; `journal.json` garde `hors_montage` (secondes de la vidéo d'origine) pour la page séance. L'aperçu des chapitres montre « ✂ hors montage … coupées ».

Chapitrage : chapitres = événements `diapositive` de la séance ramenés en secondes depuis le début de l'enregistrement, filtrés aux instants dans `[0, durée]`, fusionnés si deux changements sont à moins de 20 s (on garde le dernier titre), premier chapitre forcé à 00:00. Les diapositives de quiz donnent des chapitres « Quiz : <question> ». Les `fragmentshown` (même `h`) ne créent pas de chapitre.

Lancement local : `deploy/postprod.sh` (Linux/macOS) et `deploy/postprod.ps1` (Windows) : créent le venv si besoin, installent les dépendances, vérifient ffmpeg, lancent uvicorn sur 127.0.0.1:8000 avec `AMPHI_JETON=local`, `AMPHI_BDD=data/local.sqlite`, `AMPHI_CAPTATIONS`, `AMPHI_SEANCE_URL` passés en argument, et ouvrent `http://127.0.0.1:8000/deck/postprod.html?token=local` dans le navigateur. Le dépôt sur le PC 2 est un simple `git clone` ; `git pull` pour mettre à jour.

## 4. Traitement de la voix (`postprod/voix.py`)

Rend la voix « podcast » ou « radio » sur un fichier audio ou vidéo, ou sur tout un dossier :

    python postprod/voix.py "<captation.mkv | son.wav | dossier>" --preset podcast|radio [--sans-debruitage] [--sortie <dossier>]

Chaîne : débruitage IA DeepFilterNet (commande `deepFilter`, `pip install deepfilternet` ; absente → étape sautée avec un
avertissement) → coupe-bas 80 Hz → égalisation (chaleur, moins de « boue », présence, air) → de-esser → compression
(deux étages en `radio`) → gain fixe jusqu'à **-16 LUFS** intégrés et limiteur de crêtes à -2 dBFS.

- Le volume est mesuré par `ebur128` (EBU R128, comme `loudnorm`, mais sans suréchantillonnage à 192 kHz : ≈ 10× plus rapide),
  puis contrôlé après le limiteur ; si un son saturé à l'origine perd du volume au limiteur, le gain est corrigé une fois.
- Vidéo : image copiée sans réencodage, son en AAC 192 kb/s par `aac_mf` (Media Foundation, Windows, ≈ 5× plus rapide que
  l'encodeur `aac` de ffmpeg ; `aac` ailleurs). Sortie : `<dossier>/traite_<preset>/<nom>.mp4` (audio seul : `.wav` 48 kHz).
- Mesuré sur une captation de 7 min 50 (PC 2) : 35 s, -16,7 LUFS, crête -1,7 dBFS (sans débruitage) ; ≈ 9 min pour 2 h.

## 5 bis. Ce que je veux produire (étape 3 de la page)

Quatre formules ; le choix (et les cases cochées) est mémorisé dans le navigateur.

| Formule | Produit | Requête `POST /api/postprod/assembler` |
| --- | --- | --- |
| Replay simple | `replay.mp4` (chapitres intégrés) | `extraits: false, scorm: false, parcours: []` |
| Replay chapitré | + une vidéo par partie (`parcours/NN-titre/video.mp4`) ou par diapositive (`extraits/`) | `parcours: ["videos"]` ou `extraits: true` |
| Chapitré + activités | + questions Moodle (GIFT) + paquet SCORM | `parcours: ["videos", "questions"], scorm: true` |
| Parcours complet | éléments cochés un à un (bouton « Tout sélectionner ») | `parcours: [...]`, `extraits`, `scorm` |

Qualité, pour toutes les formules : **Voix traitée** (`voix: true`, chaîne podcast de §4, -16 LUFS) et **Vidéo allégée**
(`alleger: true`, 30 i/s à qualité constante : QuickSync `-global_quality 31`, NVENC `-cq 32` ou x264 CRF 30 ; ≈ 4 fois plus
léger que la captation OBS, pour rester sous les 512 Mo de dépôt de Moodle). Étape de tâche « vidéo », juste après la coupe ;
`replay.mp4` est remplacé (la piste de référence OBS n'est alors pas gardée).

## 6. Parcours de formation (`server/parcours.py`)

Une **séquence** par partie de la présentation : coupure à chaque diapositive de partie (section `dsfr-chapitre`, ou
attribut `data-partie`) ; avant la première partie, séquence « Introduction » ; sans partie, blocs d'au moins 8 minutes.
Une séquence = une section du cours Moodle.

```
<replay>/parcours/
  synopsis.xlsx       synopsis Excel (une ligne par activité)
  cours.html          page de cours autonome (aperçu du parcours ; tableau « fichiers à déposer dans Moodle »)
  parcours.json       description de ce qui a été produit
  questions.gift      toutes les questions, une catégorie par séquence (Banque de questions > Importer > GIFT)
  sous-titres.vtt · transcription.txt   replay complet
  NN-titre/           video.mp4 · sous-titres.vtt · transcription.txt · essentiel.md · images/NN-titre.jpg · questions.gift
```

| Élément (`parcours`) | Contenu |
| --- | --- |
| `videos` | découpe sans réencodage, du début de la séquence (image clé précédente) à la suivante |
| `sous_titres`, `transcription` | `postprod/transcrire.py` : faster-whisper (modèle `small`, `AMPHI_WHISPER_MODELE`), en local ; ≈ 30 s par minute de son sur le processeur du PC 2 (mesuré : 19 min pour 41 min ; ≈ 1 h pour 2 h). Étape de tâche « transcription ». Absent → éléments sautés, cases grisées dans la page |
| `textes` | `essentiel.md` : texte des diapositives et notes du présentateur, **brouillon à réécrire** |
| `images` | une image par chapitre (≥ 4 s, hors quiz), prise 1,5 s avant la fin du chapitre (diapositive entièrement affichée) |
| `questions` | questions auto-évaluables (mêmes types que le SCORM) des diapositives de la séquence, en GIFT |
| `synopsis` | `synopsis.xlsx` (server/synopsis.py) : grille du synopsis de digitalisation (bloc, séquence, durée, modalité, type d'activité, ressource, modification, format, livrable…), une ligne par activité de chaque séquence, onglet « synthèse » (heures par type). Durées : vidéo réelle, lecture à 200 mots/min, 1 min par question, 0,5 min par image, en heures au centième. Les formules « chapitré » et « chapitré + activités » l'incluent |
| `page` | `cours.html` : vidéo sous-titrée, chapitres, transcription synchronisée, l'essentiel, images, questions corrigées, progression |

Les fichiers sont servis par `GET /p/{slug}/replays/{id}/parcours/...` (liste fermée de noms, `CHEMIN_PARCOURS` dans
`server/replays.py`) ; la post-production affiche **Ouvrir la page de cours** à la fin. Ouverte depuis le disque, la page
fonctionne, mais Chrome n'y charge pas les sous-titres (`file://`).

## 5. Bouton « Fini » de la fenêtre de lancement (PC 2)

`tools/lanceur_seance.py`, bouton **✔ Fini** (après confirmation) : arrête l'enregistrement OBS s'il tourne encore
(StopRecord, attente de la fermeture du fichier), puis ouvre la post-production locale préremplie :
`deploy/postprod.ps1 --seance <serveur> --prez <présentation active> --auto`, dans une console visible (la fermer arrête
le serveur local), avec `AMPHI_SEANCE_JETON` = jeton du serveur de séance (passé par l'environnement, jamais dans l'URL ;
rendu à la page par `GET /api/postprod/configuration`, protégé par le jeton local). Si la post-production tourne déjà
(port 8000), la page est seulement rouverte.

Page `postprod.html?…&prez=<slug>&auto=1` : jeton du serveur de séance prérempli, séances chargées d'office (la plus
récente choisie), vidéo principale présélectionnée (pas le fichier `… camera.mkv` du replay interactif), « pas de recalage
audio » coché s'il n'y a aucun fichier audio dans le dossier de captations.

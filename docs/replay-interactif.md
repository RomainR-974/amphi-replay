# Replay interactif

Au lieu de filmer l'écran, on enregistre **l'intervenant seul** (caméra + micro USB) et on **rejoue la présentation en HTML**
d'après le journal de la séance. Résultat : texte des diapositives net à toute taille, mise en page adaptée (ordinateur,
téléphone en portrait), questions auxquelles l'apprenant répond lui-même, diapositives interactives vivantes (Teachable
Machine), vidéo légère. Coexiste avec le replay vidéo habituel (`docs/postprod.md`) : rien de l'existant n'est remplacé.

## 1. Pendant la séance (docs/seance.md §E, obs/README.md)

- OBS enregistre comme d'habitude **et**, par le filtre Source Record posé par `obs/configurer_replay_interactif.py`,
  un second fichier `<date> camera.mkv` : la caméra seule, le son du micro (Mic/Aux). Il démarre 0,5 à 2 s après le
  principal ; l'heure exacte de chaque fichier est dans le journal d'OBS (« Writing file », à la milliseconde).
- Le pont OBS (`--jeton`, passé par la fenêtre de lancement) signale chaque début d'enregistrement : le serveur
  note `enregistrement_debut` dans le journal (horloge du serveur) et fige la présentation (`seances/<id>/`).

## 2. Fabrication (PC 2) : `tools/replay_interactif.py` → `server/interactif.py`

    .venv\Scripts\python tools\replay_interactif.py "C:\Users\…\Captations\<date> camera.mkv" --prez iag-v2

1. **Instant de la vidéo** dans le journal : ancre `enregistrement_debut` la plus proche de la date du fichier
   (± 90 s) + décalage du fichier caméra lu dans le journal d'OBS. À défaut : date du fichier (≈ 1 s) ;
   `--debut` / `--decalage` pour imposer.
2. **Journal** du serveur de séance (`/api/journal`, jeton) : chronologie `[t, h, v, f]` (diapositive affichée à
   t = 0, puis chaque changement d'état, étapes d'animation comprises) et chapitres (`calculer_chapitres`).
3. **Présentation de la séance** : copie figée du serveur prise à l'ancre (`/api/presentations/{slug}/seances/{id}.zip`) ;
   sinon `--deck <dossier>` ; sinon la version actuelle (avertissement : les diapositives ont pu changer).
4. **Vidéo web** `video/camera.mp4` : 30 i/s, ≤ 1080 lignes, H.264 matériel si possible (NVENC, puis QuickSync, puis
   libx264), ≈ 1,5 Mb/s ; voix traitée par `postprod/voix.py` (`--voix podcast|radio|aucune`), AAC 128 kb/s.
5. **Cadrage** (`postprod/cadrage.py`, §5) sauf `--sans-cadrage`.
6. **Lecteur autonome** dans `presentations/<slug>/replays-interactifs/<AAAA-MM-JJ_HHMM>/` (hors git) et paquet
   **SCORM 1.2** `<slug>-<id>-interactif-scorm.zip` à côté (`--sans-scorm` pour s'en passer).

Mesuré (séance de 7 min 50, PC 2, QuickSync) : 2 min de fabrication, paquet de 80 Mo. Pour 2 h : ≈ 30 min
(cadrage ≈ 15 min, encodage, voix) et ≈ 1,3 Go — vérifier la taille maximale de dépôt de Moodle.

## 3. Contenu du dossier / du paquet

```
index.html            page du lecteur (gabarit server/interactif_lecteur/index.html)
donnees.js            window.REPLAY_INTERACTIF = { identifiant, titre, duree, slides, questions (vue publique),
                      evaluation (questions auto-évaluables, avec la bonne réponse), titres, timeline, chapitres,
                      cadrage, camera, theme, seuil, debut, source_debut, encodeur }  (JSON embarqué : marche en file://)
lecteur.js, lecteur.css
theme.css, themes/<thème>.css, camera.js, quiz-deck.js, teachable.js   runtime du deck, à l'identique
vendor/reveal/…, vendor/dsfr/fonts/…, vendor/lottie/… et vendor/tm/… (si utilisés)
medias/…              médias de la présentation (copie figée)
video/camera.mp4
scorm.js              couche SCORM 1.2 (server/scorm_lecteur/scorm.js, repli localStorage)
imsmanifest.xml       (paquet seulement) un SCO, masteryscore 50
```

## 4. Lecteur

- **Synchronisation** : à chaque image, la diapositive (h, v, f) du journal à l'instant de la vidéo (`Reveal.slide`),
  en lecture comme après un déplacement.
- **Runtime du deck réutilisé** avec le rôle `replay` (`window.AmphiSync = {role: 'replay'}`) : `quiz-deck.js` construit
  les diapositives de quiz sans se connecter au serveur, sans résultats de la salle ni QR de vote (emplacements
  `[data-replay-salle]`) ; ses appels `/api/quiz/questions`, `/api/quiz/etat`, `/api/presentations` sont servis depuis
  `donnees.js`. Les légendes « Scannez pour voter / participer » des diapositives de vote sont retirées (le QR LinkedIn
  du contact reste). `camera.js` réagence les sections libres comme pour OBS.
- **Mise en page** : paysage → la présentation occupe l'écran, l'intervenant dans une **vignette ronde** posée sur la zone
  caméra (`camera` de presentation.json, défaut 300 × 300 en bas à droite) ; portrait (largeur/hauteur < 1,05) →
  l'intervenant en haut (≤ 46 vh), la présentation en dessous (16:9), la barre de lecture, la liste des chapitres.
- **Questions** : la vidéo s'arrête 1,5 s après la première apparition de chaque diapositive de quiz auto-évaluable
  (types `qcm`, `multi`, `vrai_faux`, `trou`, `nombre` avec bonne réponse ; mêmes règles que le SCORM vidéo,
  `nombre` ± 5 %) ; l'apprenant répond, la correction s'affiche, « Continuer la vidéo ». « Plus tard » la laisse en
  attente ; le bouton « N questions à faire » y ramène. Pas de résultats de la salle (choix de l'auteur).
- **Diapositives `data-interactif`** : arrêt et bandeau « À vous d'essayer » ; l'élément Teachable Machine affiche
  « Activer ma caméra » (la caméra de l'apprenant ne s'allume qu'après son clic, puis à chaque retour sur la diapositive).
  Dans un LMS, la caméra dépend de l'autorisation donnée au cadre par le LMS (même domaine : en général accordée).
- **Barre** : lecture/pause (Espace), ± 10 s (flèches), position avec repères de chapitres et de questions, chapitre,
  vitesse (1 à 2×), plein écran.
- **SCORM 1.2** : `cmi.suspend_data` = `{p: position, v: tranches de 10 s vues (6 bits par caractère), r: réponses, a: arrêts
  faits}` (≤ 4 000 caractères : 2 h = 120 caractères pour v), `cmi.core.lesson_location` = position (s) ; reprise à la
  position. Terminé quand ≥ 90 % des tranches ont été vues et toutes les questions répondues : `passed`/`failed` selon le
  seuil (50 %), ou `completed` sans question ; `incomplete` avant ; `score.raw` = % de bonnes réponses.

## 5. Cadrage de l'intervenant (`postprod/cadrage.py`)

- Détection : YuNet (OpenCV `FaceDetectorYN`, modèle `data/modeles/face_detection_yunet_2023mar.onnx`, téléchargé au
  premier usage) sur la vidéo réduite à 640 px, une image toutes les 0,4 s. Visage principal : le plus grand, avec une
  prime de continuité.
- Lissage « cadreur » : médiane (± 1,2 s), zone morte élastique de 3,5 % de l'image, lissage exponentiel aller-retour
  (sans retard) ; trous comblés par la dernière position ; après 20 s sans visage, le plan s'élargit doucement ;
  personne du tout → plan centré.
- Sortie : `{version, pas: 0.4, source: [L, H], detection, points: [[cx, cy, h], …]}` (fractions de l'image).
- Lecteur : zoom pour que le visage occupe ≈ 40 % de la vignette (30 % du bandeau portrait), borné à 3× le plein cadre,
  visage un peu au-dessus du centre, cadre jamais hors de l'image ; interpolation entre deux points.
- Mesuré : 58 s de calcul pour 7 min 50 de vidéo (≈ 15 min pour 2 h), visage trouvé sur 92 % des images.
- Dépendance : `pip install -r requirements-postprod.txt` (opencv-python-headless) ; absente → cadrage sauté.

# Export SCORM d'un replay

Produit, à partir d'un replay chapitré, un paquet **SCORM 1.2** (le plus largement accepté : Moodle, 360Learning, Rise, Docebo…) : un module d'e-learning où chaque chapitre du replay est une étape avec sa vidéo et, en option, les questions de quiz de cette partie en auto-évaluation. Le LMS reçoit la progression, la complétion et le score.

## Contenu du paquet

```
<slug>-<replay>-scorm.zip
  imsmanifest.xml           SCORM 1.2, un seul SCO (organisation à un item), masteryscore 50, titre du replay
  index.html                lecteur autonome (JS vanilla, aucune dépendance), fonctionne aussi hors LMS
  scorm.js                  recherche de l'API SCORM 1.2 (window.API dans les parents/opener, 500 niveaux max),
                            LMSInitialize / LMSGetValue / LMSSetValue / LMSCommit / LMSFinish, repli localStorage
  module.json               { titre, chapitres: [{numero, titre, duree, video, questions: [...]}], quiz: bool, genere, source }
  chapitres/01-titre.mp4    une vidéo par chapitre (les extraits ; découpés s'ils manquent), H.264/AAC copiés sans réencodage
  (ou video/replay.mp4 + chapitres par instants, option « vidéo unique »)
  medias/affiche.jpg        image du premier chapitre (ffmpeg, 1 image à 2 s) comme affiche des vidéos
```

Les fichiers `.xsd` du SCORM ne sont pas inclus (Moodle et la plupart des LMS ne les exigent pas) ; `imsmanifest.xml` les référence par les noms standard.

## Lecteur (`index.html`)

- Colonne gauche : liste des chapitres avec état (à voir · en cours · vu ✔), durée, et score du quiz du chapitre.
- Centre : titre du chapitre, lecteur `<video controls playsinline>` avec affiche, puis, si le chapitre a des questions, le bloc **Vérifiez vos connaissances** : questions une à une (types notés uniquement : `qcm`, `multi`, `vrai_faux`, `trou`, `nombre` avec tolérance ±5 % ; les autres types sont ignorés), bouton Valider, retour vert/rouge avec la bonne réponse, puis **Chapitre suivant**.
- Un chapitre est « vu » quand la vidéo a été lue à ≥ 90 % (ou terminée) et que ses questions ont été validées.
- Progression sauvegardée en continu dans `cmi.suspend_data` (JSON compact : chapitres vus, réponses) et `cmi.core.lesson_location` (chapitre courant) ; restaurée à la reprise.
- Quand tous les chapitres sont vus : `cmi.core.lesson_status = passed` si score ≥ 50 % (ou `completed` s'il n'y a pas de quiz), sinon `failed` ; `cmi.core.score.raw` = % de bonnes réponses (`min` 0, `max` 100) ; `cmi.core.session_time` à la sortie ; `LMSCommit` après chaque changement, `LMSFinish` sur `beforeunload`.
- Hors LMS (ouvert depuis le zip décompressé) : même comportement, progression en `localStorage`, bandeau discret « mode autonome ».
- Accessibilité : navigation clavier, contrastes, `lang="fr"`, sous-titres si un `chapitres/NN.vtt` existe (non généré en v1).

## Rattachement des questions aux chapitres

Un chapitre couvre les diapositives `[h_debut, h_fin[` (h de l'événement qui l'a créé jusqu'au h du chapitre suivant ; le dernier va jusqu'à la fin). Les sections `data-quiz` du `slides.html` dont l'index h est dans l'intervalle donnent les questions du chapitre (depuis `quiz.json`, avec `bonne`/`bonnes`). Les questions sans bonne réponse ne sont pas incluses. `calculer_chapitres` doit conserver `h` dans chaque chapitre (ajouter le champ si absent, sans casser les replays existants : `h` absent → pas de questions).

## API et pages

| Méthode | Chemin | Rôle |
| --- | --- | --- |
| POST | `/api/presentations/{slug}/replays/{id}/scorm` | jeton ; `{titre?, inclure_quiz: true, video_unique: false}` → `{tache}` (tâche en arrière-plan : extraits si besoin, affiche, zip) |
| GET | `/api/presentations/{slug}/replays/{id}` | ajoute `scorm: {fichier, taille, genere, chapitres, questions} \| null` |
| GET | `/p/{slug}/replays/{id}/scorm/<fichier>.zip` | téléchargement (`Content-Disposition: attachment`) |
| DELETE | `/api/presentations/{slug}/replays/{id}/scorm` | jeton |

- `deck/replay.html` : carte **Export SCORM** : options (inclure les quiz, vidéo unique ou par chapitre, titre), bouton **Générer**, progression, puis lien de téléchargement avec taille, date, nombre de chapitres et de questions, et un texte d'aide « Moodle : Ajouter une activité → Paquet SCORM → déposer le zip ».
- `deck/postprod.html` : case « Générer le paquet SCORM » (cochée par défaut) : étape « scorm » après les extraits.
- `deck/presentations.js` : sur la ligne d'un replay, lien **SCORM** quand le paquet existe.

## Ligne de commande

`python tools/exporter_scorm.py presentations/iag-v1/replays/2026-11-10_0902 [--sans-quiz] [--video-unique] [--titre …]` : même code (`server/scorm.py`), pour un usage hors serveur.

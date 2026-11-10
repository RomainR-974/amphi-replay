# Déroulé de séance : enregistrement piloté par les diapositives, cérémonie du classement, fin de séance

## A. Enregistrement OBS déclenché par les diapositives

Marqueurs sur une section de `slides.html` : `data-obs="demarrer"` ou `data-obs="arreter"`.

- Quand l'état du deck change (message `slide` du remote, ou saut par la séquence Q/R), le serveur regarde la section d'index `h` de la présentation active ; si elle porte `data-obs`, il diffuse sur `/ws/quiz` : `{"type":"obs","action":"StartRecord"|"StopRecord","h":h}`. Une seule diffusion par arrivée sur la diapositive (pas à chaque fragment). Le serveur cache les marqueurs de la présentation active (relus à l'activation et à chaque enregistrement de `slides.html`).
- `obs/obs_quiz_bridge.py` exécute l'action (`StartRecord` ignoré si déjà en cours, `StopRecord` ignoré si rien ne tourne ; messages console « enregistrement démarré par la diapositive n »). Option `--sans-enregistrement` pour ignorer ces messages. L'arrêt aux Q/R reste.
- Éditeur, panneau de la diapositive : champ « OBS » (aucun · démarrer l'enregistrement · arrêter l'enregistrement · hors montage) qui pose/retire `data-obs` ; une seule diapositive porte « démarrer » et « arrêter », autant qu'on veut « hors montage » (pastille « ✂ hors montage », miniature hachurée ; ignoré par le pont OBS, voir docs/postprod.md §3). Dans la liste, les diapositives enregistrées ont une bordure rouge et les pastilles « ⏺ début » / « ⏹ fin ».
- `docs/format-deck.md` : attribut documenté. `docs/fiche-mise-en-route.md` : phrase « l'enregistrement démarre tout seul à la diapositive X si le pont tourne ; F9 reste le secours ».

Dans `presentations/iag-v2/slides.html` : `data-obs="demarrer"` sur la diapositive qui suit « Avant de commencer » (la nouvelle diapositive « Qui suis-je », voir B) et `data-obs="arreter"` sur « À retenir ». Idem dans `presentations/iag-v1/slides.html` (diapositive 3 « Pour commencer », et « À retenir »).

## B. Diapositive « Qui suis-je » (v2, après « Avant de commencer »)

Section libre `class="libre dsfr"` au style DSFR de la v2 : surtitre « QUI SUIS-JE », nom **Romain Ringwald**, fonction (DRAFPIC, Région académique La Réunion — Délégation Régionale Académique à la Formation Professionnelle Initiale et Continue), une photo de substitution (cercle avec initiales « RR », remplaçable dans l'éditeur par une image), trois tags (à remplacer : « Formation professionnelle », « Numérique et IA », « Accompagnement des instituts »), le courriel romain.ringwald@ac-reunion.fr et le lien LinkedIn `https://www.linkedin.com/in/romain-ringwald-b64b653a/` écrit en clair avec un QR code du lien (image PNG générée par `qrcode`, dans `medias/qr-linkedin.png`). Notes de l'orateur : « Se présenter en 30 secondes ». Mettre à jour la numérotation du pied de page (n / 47) de toutes les diapositives de la v2 si elle est écrite en dur.

## C. Contact de la présentation et fin de séance

`presentation.json` reçoit `contact: {"nom", "fonction", "courriel", "linkedin"}` (rempli pour iag-v1 et iag-v2 avec les valeurs ci-dessus). Éditeur : non requis (fichier JSON).

Télécommande : sur la **dernière diapositive** (et dernier fragment), le bouton **Suivant** devient **Quitter** (rouge). Au tap : confirmation « Terminer la séance ? » → `POST /api/seance/terminer` (jeton). Le serveur : ferme la question de quiz ouverte, termine la séquence Q/R si active, diffuse `{"type":"obs","action":"StopRecord"}` (sécurité), journalise `fin`, puis diffuse sur `/ws/quiz` `{"type":"fin","contact":{…},"classement_total":n}`. La télécommande affiche alors un écran « Séance terminée » avec un bouton « Revenir au début » (`Reveal.slide(0)`), et le chronomètre s'arrête.

Page participant, sur `{"type":"fin"}` (et à la connexion si `GET /api/seance` répond `terminee: true`) : écran final « Merci pour votre participation » avec le **rang et les points du participant** (depuis `/api/quiz/classement?nombre=1000`, trouvé par pseudo ; « Vous n'avez pas marqué de points » sinon), puis la carte de contact : nom, fonction, courriel sélectionnable, **bouton LinkedIn** (lien `https://www.linkedin.com/in/…`, ouvre dans un nouvel onglet). Le bouton « ? » reste disponible. `POST /api/seance/reprendre` (jeton) ou l'activation d'une présentation remet `terminee: false` et les participants reviennent à « En attente ».

## D. Cérémonie du classement

`/q/classement?ceremonie=1` : la page affiche seulement le titre **« Classement »** (sans aucun score) et attend un message `postMessage({type:"ceremonie"})` de sa fenêtre parente (ou `?ceremonie=auto` pour démarrer après 1 s, pour les tests). Elle charge tout le classement (`/api/quiz/classement?nombre=1000`, tous les participants ayant des points).

Déroulé, aucune action de l'orateur ensuite :
1. **Défilé des rangs** : les participants apparaissent du dernier au premier, une ligne à la fois en haut d'une liste qui pousse les précédentes vers le bas (rang, pseudo, points), **0,3 s** par participant jusqu'au 16e.
2. Du **15e au 4e** : ralentissement progressif, chaque apparition en fondu doux, durée interpolée de **0,3 s à 1 s** (ease-out).
3. **Suspense** : pause de **2 s** avec un effet de **vibration** (secousses légères de la liste et du titre).
4. **Podium** : la liste s'efface en fondu, trois marches vides apparaissent. La marche du **3e** monte, puis son nom et ses points apparaissent en **fondu doux 1 s** (médaille bronze). Même chose pour le **2e** (argent).
5. **Avant le 1er** : attente de **2 s** avec un **wobbling** (oscillation) sur les deux marches déjà révélées. Puis la marche du 1er monte, le nom apparaît en fondu 1 s avec une **couronne** 👑 animée au-dessus (petit rebond), médaille or, et une courte pluie de confettis (Canvas, 2 s, respect de `prefers-reduced-motion`).
6. **Tableau final** sous le podium : tous les participants avec rang, pseudo et points, en **plusieurs colonnes** (grille auto : colonnes de 10 lignes, lisible à 1920×1080 : 26 px minimum), apparition en fondu. Le podium reste visible au-dessus (réduit si nécessaire pour que le tout tienne).

Moins de 3 participants : passer directement aux marches disponibles. Aucun point : « Pas encore de points ». Un `postMessage({type:"ceremonie"})` reçu pendant ou après la cérémonie la rejoue depuis le début.

Déclenchement depuis le deck : une section de classement contient un élément invisible `<div class="el declencheur fragment" data-amphi="ceremonie" data-id="…" style="left:0;top:0;width:1px;height:1px"></div>` ; `quiz-deck.js`, sur `fragmentshown` d'un `[data-amphi="ceremonie"]`, envoie `postMessage({type:"ceremonie"}, "*")` à toutes les iframes de la section. L'élément classement de la section porte `data-ceremonie="1"` → iframe `…/q/classement?ceremonie=1&…`. Ainsi « Suivant » sur la télécommande lance la cérémonie, synchronisée sur le projecteur et dans OBS. `tools/inserer_quiz.py` (gabarit CLASSEMENT) et les sections classement de iag-v1 et iag-v2 adoptent ce balisage ; la diapositive de classement de la v2 est placée juste avant « À retenir » (déjà le cas) ; s'il n'y en a pas en fin de v1, en ajouter une avant « À retenir ».

Éditeur : l'élément « Classement » propose une case « cérémonie animée (déclenchée par Suivant) » qui ajoute `data-ceremonie="1"` et l'élément déclencheur.

## E. Ancre d'enregistrement et copie figée (replay interactif, étape 1)

Préalable du **replay interactif** : la vidéo de la caméra (et le micro USB) est rejouée avec la présentation redessinée en HTML
à partir du journal, au lieu de filmer l'écran. Il faut pour cela savoir **à quel instant du journal** commence l'enregistrement,
et garder **la présentation telle qu'elle était** pendant la séance. Le replay vidéo actuel (`replay.mp4`) n'est pas modifié.

- **Pont OBS** (`obs/obs_quiz_bridge.py --jeton …`, passé automatiquement par la fenêtre de lancement) : lit `GetRecordStatus`
  chaque seconde ; à chaque début d'enregistrement (déclenché par une diapositive `data-obs` ou par F9) il envoie sur `/ws/quiz`
  `{"type": "enregistrement", "actif": true, "duree_ms": <durée déjà enregistrée selon OBS>}`, et `actif: false` à l'arrêt.
  Sans `--jeton`, rien n'est envoyé (comportement précédent).
- **Serveur** (`/ws/quiz?token=…` ; sans jeton valide le message est ignoré) : `début = maintenant − duree_ms` daté avec
  **l'horloge du serveur** (celle du journal ; précision ≈ latence réseau) → événement `enregistrement_debut` (ou
  `enregistrement_fin`) dans le journal de la présentation active. Un même début renvoyé après une reconnexion du pont
  (à moins de 2 s) n'est pas dupliqué. Réponse : `{"type": "enregistrement", "ok", "actif", "debut", "seance", "presentation"}`.
- **Copie figée** : au début, `slides.html`, `quiz.json`, `presentation.json` et `medias/` sont copiés dans
  `presentations/<slug>/seances/<AAAA-MM-JJ_HHMMSS>/` (+ `seance.json` : `{presentation, id, debut_enregistrement}`),
  une seule fois par début. Hors git (`.gitignore`) ; non copiée quand on duplique une présentation.
- **Routes** (jeton) : `GET /api/presentations/{slug}/seances` → `[{id, debut_enregistrement, taille}]` (plus récente d'abord) ;
  `GET /api/presentations/{slug}/seances/{id}.zip` → la copie figée en zip (relue par la post-production du PC 2).

## F. Séances jouées : page séance et export PDF

- Page Présentations : chaque carte a une liste déroulante des séances jouées (`GET /api/presentations/{slug}/historique`, jeton) :
  date, durée, diapositives, questions, 🎬 si un replay est sur ce serveur. Une séance = un groupe du journal (`journal.seances`)
  contenant au moins un changement de diapositive ; identifiant = epoch entier de son premier événement.
- `/deck/seance.html?prez=&id=&token=` (`GET /api/presentations/{slug}/historique/{id}`) :
  - la présentation **telle qu'elle était** : copie figée prise au début de l'enregistrement (§E, rattachée si prise de 15 min
    avant le premier événement à la fin), servie par `/p/{slug}/seances/{copie}/slides.html | quiz.json | medias/…` ; sans copie,
    la version actuelle (signalé) ;
  - rejouée au rythme du journal dans le deck en rôle `seance` (iframe pilotée par `postMessage` : `aller`, `resultats`) ; lecture,
    curseur, vitesse 1× à 16×, déroulé cliquable (diapositives, Q/R, début / fin d'enregistrement) ;
  - la vidéo du replay si elle est sur ce serveur (celui dont la vidéo recouvre le plus la séance ; `journal.json` :
    `debut_enregistrement + decalage` = epoch de l'instant 0) : elle sert alors d'horloge ;
  - les résultats de chaque question posée, dans la diapositive (emplacement « salle » des quiz générés, ou élément
    `data-amphi="resultats"`) et dans le panneau de droite (`deck/resultats-statiques.js`, `deck/resultats.css`).
- Résultats : instantané archivé à chaque fermeture (bouton, chrono, fin de séance) et révélation (table `archives_resultats`,
  `Quiz.archiver`) ; il survit à « Réinitialiser » et aux séances suivantes. Pour une séance antérieure à cet archivage : recalcul
  à partir des réponses enregistrées pendant la séance (une réponse remplacée depuis par le même participant est perdue).
- **Exporter en PDF** : `/deck/?role=impression&prez=&copie=&historique=&token=` : une page par diapositive affichée pendant la
  séance (`&toutes=1` : toutes), résultats placés dans les diapositives de quiz, pied « n / total · affichée à HH:MM · durée »,
  puis la boîte d'impression du navigateur (« Enregistrer au format PDF »). Rendu reveal.js `view: 'print'`.

## G. Retour caméra et niveau du micro sur la télécommande

- Sur la première diapositive, la télécommande affiche le retour de la caméra OBS (≈ 10 images/s, JPEG 480 px) avec un trait
  vertical au centre (et un repère à hauteur des yeux) pour se placer, et un vumètre du micro OBS (zone conseillée -40 à -12 dB
  en pointillés ; « trop bas » sous -40 dB de crête, « sature » au-dessus de -3 dB). Rien sur les autres diapositives.
- Chemin : télécommande → `/ws/deck` `{"type":"apercu","actif":true}` toutes les 2,5 s → serveur → `/ws/quiz` (seulement les
  connexions avec le jeton, rôle « pont ») `{"type":"apercu_demande"}` → pont OBS (`obs/obs_quiz_bridge.py`, classe `Apercu`) :
  seconde connexion obs-websocket abonnée à `InputVolumeMeters`, `GetSourceScreenshot` toutes les 100 ms → `{"type":"apercu",
  "image","niveau","crete","camera","micro"}` → serveur → télécommande uniquement. Arrêt 6 s après la dernière demande.
- Source caméra : `--camera` (défaut : la première webcam d'OBS) ; micro : `--micro` (défaut « Mic/Aux », sinon le premier micro).
  Il faut le pont lancé avec `--jeton` (fenêtre de lancement). Sans pont : « pont OBS non connecté ».
- Seule exception à « le serveur ne transporte jamais de vidéo » : quelques images basse définition, seulement sur la
  première diapositive et seulement vers la télécommande ; la captation n'en dépend pas.

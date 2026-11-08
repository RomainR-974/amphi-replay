# Espace questions des participants et séquence questions-réponses

## Côté participant (`/q`)

- Un gros bouton rond « ? » fixé en bas à droite de la page, toujours visible (quiz ouvert ou non).
- Au clic : une boîte de dialogue « Poser une question » qui indique la **diapositive en cours** (numéro `n / total` + titre, lus depuis `GET /api/questions/contexte`), une zone de texte (280 caractères max), un bouton **Envoyer**. Le participant peut aussi choisir « cette question ne concerne pas la diapositive en cours » (la question est alors rattachée à la diapositive, mais marquée `generale`).
- Après envoi : confirmation « Question envoyée », le bouton affiche un petit compteur des questions envoyées par ce participant (session). Un participant peut envoyer plusieurs questions ; anonymat identique au quiz (pseudo du participant joint, jamais d'identité).
- Pendant la séquence questions-réponses : la page participant affiche la question en cours de traitement (« On répond à : … ») et le total restant.

## Serveur

Table SQLite `questions_participants` : `id`, `presentation`, `participant`, `pseudo`, `texte`, `h`, `v`, `titre_diapo`, `generale` (0/1), `horodatage`, `etat` (`en_attente` | `en_cours` | `repondue` | `ecartee`), `ordre` (entier pour le tri manuel, défaut = id).

API :

| Méthode | Chemin | Rôle |
| --- | --- | --- |
| GET | `/api/questions/contexte` | public : `{h, v, numero, total, titre}` de la diapositive courante (état du deck + titres via `server/journal.titres_presentation`) |
| POST | `/api/questions` | public, cookie participant : `{texte, generale?}` → la question est rattachée à la diapositive courante ; renvoie `{id, numero, titre}` ; limite : 20 questions par participant et 280 caractères |
| GET | `/api/questions` | jeton : liste complète `[{id, texte, pseudo, h, v, numero, titre, generale, etat, horodatage}]` triée par `ordre` ; `?etat=` filtre |
| GET | `/api/questions/publiques` | public : la même liste sans les pseudos, pour l'écran |
| POST | `/api/questions/{id}/etat` | jeton : `{etat}` |
| POST | `/api/questions/ordre` | jeton : `{ids: [...]}` réordonne |
| DELETE | `/api/questions/{id}` | jeton |
| POST | `/api/questions/purger` | jeton (appelé aussi par `quiz/purger`) |
| GET | `/api/questions/export` | jeton : CSV (numéro, titre de diapo, question, état, horodatage, sans pseudo) |

**Séquence questions-réponses (état serveur `qr`)** : `{actif: bool, question_id: int|null, retour: {h, v, f}}`.

| Méthode | Chemin | Rôle |
| --- | --- | --- |
| POST | `/api/qr/ouvrir` | jeton : `actif = true`, mémorise la position courante du deck dans `retour`, journalise `qr_ouvrir`, diffuse sur `/ws/deck` `{"type":"qr","actif":true,"question":null}` |
| POST | `/api/qr/question` | jeton : `{id}` → `etat = en_cours`, l'état du deck passe à `{h, v, f:-1}` de la question (comme un message `slide` du remote : diffusé à scene/obs, journalisé), diffuse `{"type":"qr","actif":true,"question":{id, texte, numero, titre, pseudo:null}}` |
| POST | `/api/qr/suivante` | jeton : marque la question en cours `repondue`, passe à la suivante `en_attente` (selon `ordre`) ; s'il n'y en a plus, renvoie `{fin: true}` |
| POST | `/api/qr/liste` | jeton : marque la question en cours `repondue` (si `{repondue: false}` : la laisse `en_attente`), `question = null`, diffuse l'état ; le deck reste sur la diapositive |
| POST | `/api/qr/fermer` | jeton : `actif = false`, revient à `retour`, journalise `qr_fermer`, diffuse |
| GET | `/api/qr` | public : état courant |

Le journal reçoit `qr_ouvrir` / `qr_fermer` : la post-production s'arrête au `qr_ouvrir` (voir plus bas).

## Télécommande (`deck/remote.html`)

- En haut, à côté du chrono : un badge « **? n** » (nombre de questions en attente, mis à jour par `/ws/quiz` : le serveur y pousse aussi `{"type":"questions","en_attente": n}` à chaque nouvelle question).
- Un bouton **Questions-réponses** (dans la barre du bas, à gauche de Précédent, ou un troisième bouton plus étroit) ouvre le **mode Q/R** plein écran dans la télécommande :
  - rappel « ⏺ Arrête l'enregistrement OBS avant de commencer » avec un bouton **Commencer** (appelle `/api/qr/ouvrir`) ; si le pont OBS est connecté (voir plus bas), l'arrêt est automatique et le rappel le dit ;
  - la **liste des questions** en attente (numéro de diapo, titre court, texte, pseudo), triable par diapositive ou par ordre d'arrivée, avec glisser pour réordonner et une croix pour écarter ;
  - au tap sur une question : vue **question** — le deck saute à la diapositive, la question est affichée en grand sur l'écran (overlay) ; boutons **→ Suivante** (`/api/qr/suivante`), **✕ Retour à la liste** (`/api/qr/liste`), et **Précédent/Suivant** restent utilisables pour naviguer dans le deck pendant la réponse ;
  - bouton **Terminer** (`/api/qr/fermer`) : revient à la diapositive d'où l'on était parti.
- Quand il n'y a plus de question, « Toutes les questions ont été traitées » + Terminer.

## Écran (`deck/index.html` + `deck/quiz-deck.js`)

Sur `{"type":"qr"}` reçu par `sync.js` (émettre un événement `qr`) :
- `actif && question` : afficher un **bandeau overlay** en bas de l'écran (toutes présentations) : « Question · diapositive n » + le texte de la question en grand (≥ 40 px), fond semi-opaque sombre, bord bleu ; animation d'entrée ; style DSFR si `section.dsfr`.
- `actif && !question` : overlay discret en haut à droite « Questions-réponses » + le nombre de questions restantes.
- `!actif` : retirer les overlays.
- Les rôles `scene` et `obs` affichent l'overlay ; `remote` non.

## OBS

`obs/obs_quiz_bridge.py` : écoute aussi les messages `{"type":"qr"}` sur `/ws/deck` (il faut qu'il s'y connecte en rôle `obs`, ou que le serveur relaie aussi `qr` sur `/ws/quiz` : choisir le relais sur `/ws/quiz`, plus simple, le pont y est déjà connecté). À `qr_ouvrir` : **arrête l'enregistrement OBS** (`StopRecord`) après 2 s de délai, affiche dans la console « enregistrement arrêté avant les questions-réponses ». Option `--sans-arret` pour désactiver. À `qr_fermer` : ne redémarre pas (la séquence Q/R n'est pas enregistrée, c'est voulu).

## Post-production

`calculer_chapitres` et l'assemblage : si la séance contient un événement `qr_ouvrir`, la vidéo finale est **coupée** à l'instant correspondant (`-to`) quand cet instant est dans la durée de l'enregistrement, et le journal du replay le mentionne (« coupé avant les questions-réponses à HH:MM:SS »). Case à cocher dans la page de post-production « Couper avant les questions-réponses » (cochée par défaut quand l'événement existe). Les questions des participants sont exportées dans le dossier du replay (`questions.csv`) si le serveur de séance les fournit (`/api/questions/export`).

## Fiche de mise en route

Ajouter dans docs/fiche-mise-en-route.md : à la fin de l'exposé, **clap n°2 puis arrêt d'OBS (F9)** AVANT d'appuyer sur « Questions-réponses » (ou laisser le pont arrêter OBS) ; la séquence Q/R n'est pas dans le replay.

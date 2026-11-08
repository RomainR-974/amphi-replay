# Format du deck et contrats d'API

Référence commune à `tools/pptx2reveal.py`, à l'éditeur (`deck/editeur.html`) et au runtime du deck (`deck/index.html`, `deck/quiz-deck.js`). Tout ce qui est produit ou édité respecte ce format.

## 1. Fichier `slides.html` (dans `presentations/<slug>/`, voir docs/presentations.md)

Un fragment HTML (pas de `<html>`, pas de `<head>`), une `<section>` par diapositive, dans l'ordre. Le runtime l'injecte dans `.reveal .slides`. Deux sortes de sections :

### 1.1 Section « libre » (positionnement absolu, issue de PowerPoint ou de l'éditeur)

```html
<section class="libre" data-id="s3" data-transition="fade" data-background-color="#ffffff" data-background-image="medias/fond.png">
  <div class="el texte" data-id="e1" style="left:120px;top:80px;width:1680px;height:160px;font-size:60px;color:#1a1a1a;font-weight:700;text-align:left">
    Titre de la diapositive
  </div>
  <img class="el image" data-id="e2" src="medias/s3_1.png" style="left:960px;top:300px;width:800px;height:600px">
  <div class="el texte fragment fade-up" data-fragment-index="0" data-id="e3" style="left:120px;top:320px;width:700px;height:500px;font-size:32px">
    <ul><li>Première puce</li><li>Deuxième puce</li></ul>
  </div>
</section>
```

Règles :

- La scène fait **1920 × 1080 px** ; `left/top/width/height` sont en px dans ce repère, en `style` inline. Le runtime pose `position:absolute` sur `.libre > .el`, pas besoin de le répéter.
- Chaque élément porte `class="el <type>"` et un `data-id` unique dans la section (l'éditeur en génère : `e` + nombre).
- Types d'éléments (`<type>` dans la classe) et balise attendue :

| type | balise | contenu / attributs |
| --- | --- | --- |
| `texte` | `div` | HTML riche : `p`, `ul/ol/li`, `b`, `i`, `u`, `span style=…`, `br`, `a` |
| `image` | `img` | `src` relatif au dossier de la présentation (`medias/…`) ou URL absolue |
| `forme` | `div` | fond/bordure/rayon en `style` (`background`, `border`, `border-radius`) ; peut contenir du texte |
| `iframe` | `iframe` | `src` publique sans état |
| `video` | `video` | `src`, jamais d'`autoplay` ; lancée par `data-amphi="video"` sur commande |
| `lottie` | `div` | `data-amphi="lottie" data-src="medias/anim.json" data-boucle="1"` ; rendu par lottie-web au runtime |
| `minuteur` | `div` | `data-amphi="minuteur" data-duree="60"` (s) ; démarre à l'affichage de la diapositive ; `data-auto="1"` sinon démarre au fragment |
| `resultats` | `div` | `data-amphi="resultats" data-question="q1"` ; le runtime y insère une iframe `/q/resultats?question=q1` |
| `classement` | `div` | `data-amphi="classement" data-nombre="10"` ; iframe `/q/classement?nombre=10`. Avec `data-ceremonie="1"` : iframe `/q/classement?ceremonie=1&…`, la page n'affiche que le titre puis joue la cérémonie du classement (défilé des rangs, podium 3e/2e/1er, tableau final ; voir `docs/seance.md`, D) quand elle reçoit `postMessage({type:"ceremonie"})` |
| `declencheur` | `div` | `data-amphi="ceremonie"` sur un fragment invisible (`class="el declencheur fragment"`, `style="left:0;top:0;width:1px;height:1px"`) : au `fragmentshown`, le runtime envoie `{type:"ceremonie"}` à toutes les iframes de la section, donc « Suivant » sur la télécommande lance la cérémonie, synchronisée sur le projecteur et dans OBS. L'éditeur le pose via la case « Cérémonie animée » de l'élément classement |
| `nuage` | `div` | `data-amphi="resultats" data-question="qN"` (un nuage est un résultat de question de type `nuage`) |
| `qr` | `div` | `data-amphi="qr"` ; QR code + URL participant |
| `teachable` | `div` | `data-amphi="teachable" data-src="medias/<dossier>/"` : modèle d'images Teachable Machine (export TensorFlow.js : `model.json`, `metadata.json`, `weights.bin` dans le dossier). Rôle `scene` : webcam du poste + barre de probabilité par classe, allumée seulement pendant l'affichage de la diapositive ; rôles `obs` et `remote` : encadré « Démonstration en direct », sans caméra (celle du PC 2 est prise par OBS). Bibliothèques chargées à la demande depuis `deck/vendor/tm/` (`tools/fetch_reveal.sh`, tfjs 1.3.1 + @teachablemachine/image 0.8.5). La section porte `data-interactif` (le futur replay interactif y proposera « À vous d'essayer ») |

- Animations : classes reveal.js sur l'élément, `fragment` + une parmi `fade-in` (défaut), `fade-out`, `fade-up`, `fade-down`, `fade-left`, `fade-right`, `fade-in-then-out`, `fade-in-then-semi-out`, `zoom-in`, `grow`, `shrink`, `strike`, `highlight-red`, `highlight-blue`, `highlight-green`, `semi-fade-out`. `data-fragment-index="n"` ordonne ; même index = même clic.
- Transition de diapositive : `data-transition` parmi `none`, `fade`, `slide`, `convex`, `concave`, `zoom`. Le rôle `obs` ignore les transitions (le runtime les retire).
- Fond : `data-background-color`, `data-background-image`, `data-background-size`, `data-background-video` (évitée).
- Notes de l'orateur : `<aside class="notes">…</aside>` en dernier enfant de la section, HTML simple.
- Enregistrement OBS (docs/seance.md) : `data-obs="demarrer"` ou `data-obs="arreter"` sur la `<section>` (libre, flux ou quiz). À l'arrivée sur la diapositive, le serveur diffuse `{"type":"obs","action":"StartRecord"|"StopRecord","h":n}` sur `/ws/quiz`, une fois par arrivée (pas à chaque fragment) ; le pont `obs/obs_quiz_bridge.py` exécute l'action. Éditeur : champ « OBS » du panneau de la diapositive.
- Police : `font-family` inline si nécessaire ; sinon celle du thème.
- Zone caméra (docs/zone-camera.md) : la vignette OBS est incrustée dans un coin de la scène (défaut 300 × 300 en bas à droite, marge 20 : zone réservée `x ≥ 1600, y ≥ 760`). Aucun `.el` important ne doit y déborder : `tools/reserver_camera.py` réagence les sections de quiz à la source, l'éditeur affiche la zone (calque « Caméra », contour orange sur les éléments qui la chevauchent) et le rôle `obs` du runtime ajuste en secours, en mémoire, les éléments des sections libres (`deck/camera.js`). Le réglage vient de `camera` dans `presentation.json` (`{"largeur", "hauteur", "position": "bas-droite|bas-gauche|haut-droite|haut-gauche", "marge"}`, `null` pour désactiver), exposé résolu par `GET /api/presentations` et `/api/presentations/active`.

### 1.2 Section « flux » (reveal.js classique, issue de `pdf2reveal.py` ou écrite à la main)

Sans `class="libre"`. Contenu en flux : `h1/h2`, `p`, `ul`, `img`, `iframe`. L'éditeur les présente en mode texte (édition du HTML de chaque enfant direct, ajout/suppression/réordonnancement d'enfants) et propose la conversion en section libre (chaque enfant devient un `.el texte` empilé).

### 1.3 Diapositive de quiz

`<section data-quiz="q1" data-id="s7"></section>` : le runtime y construit question + options statiques, QR code et résultats (voir `quiz-deck.js`) ; la colonne des résultats est raccourcie (`height: calc(100% - n px)`, le « padding-bottom » de docs/zone-camera.md) de la hauteur de la zone caméra qui dépasse dans la grille (idem pour l'iframe d'un classement en section de flux). Une section libre peut aussi contenir un `.el resultats` ou `.el classement` pour une mise en page personnalisée ; dans ce cas elle porte aussi `data-quiz="q1"` pour que la télécommande affiche le bloc d'administration.

## 2. Médias

Dossier `presentations/<slug>/medias/` (images, JSON Lottie, vidéos). Noms de fichiers : lettres, chiffres, `-`, `_`, `.`. Le serveur sert celui de la présentation active sous `/deck/medias/` et chacun sous `/p/<slug>/medias/`.

## 3. Fichier `quiz.json` (dans `presentations/<slug>/`)

Liste de questions. Champs communs : `id` (unique, `[a-z0-9_-]+`), `type`, `question`, `chrono` (secondes, optionnel, 0 = sans limite), `points` (booléen, défaut vrai si une bonne réponse est déclarée).

| type | champs spécifiques | réponse participant | résultat affiché |
| --- | --- | --- | --- |
| `qcm` | `options` (2–6), `bonne` (index, optionnel) | index | barres |
| `multi` | `options`, `bonnes` (liste d'index, optionnel) | liste d'index | barres |
| `vrai_faux` | `bonne` (`true`/`false`, optionnel) | booléen | barres |
| `emoji` | `options` (emojis, 2–8), `bonne` optionnel | index | barres avec emojis |
| `rapidite` | `options`, `bonne`, `chrono` (obligatoire) | index | barres + les 5 plus rapides |
| `buzzer` | — | horodatage (appui) | les 5 premiers avec leur temps |
| `nuage` | `max_mots` (défaut 3), `max_longueur` (défaut 25) | liste de mots | nuage de mots |
| `texte_libre` | `max_longueur` (défaut 140) | texte | cartes |
| `trou` | `texte` avec `___`, `bonnes` (liste de réponses acceptées) | texte | % de bonnes réponses + réponses les plus fréquentes |
| `echelle` | `min` (1), `max` (5), `libelle_min`, `libelle_max` | entier | distribution + moyenne |
| `classement` | `options` (2–6), `bonne` (ordre d'index, optionnel) | ordre d'index | rang moyen par option |
| `nombre` | `bonne` (nombre, optionnel), `unite` | nombre | histogramme + médiane + les plus proches |

## 4. API du serveur (jeton requis sauf mention)

| Méthode | Chemin | Rôle |
| --- | --- | --- |
| GET | `/api/presentations`, `/api/presentations/active` | liste / présentation active, avec `camera` résolue (docs/presentations.md, docs/zone-camera.md) — public |
| GET | `/api/deck/slides` | contenu de `slides.html` (texte) — public |
| PUT | `/api/deck/slides?token=` | corps `{ "html": "…" }` ; écrit `slides.html`, sauvegarde l'ancien dans `presentations/<slug>/sauvegardes/slides-<horodatage>.html` ; `?prez=<slug>` vise une présentation donnée (défaut : l'active) |
| GET | `/api/deck/sauvegardes?token=` | liste des sauvegardes |
| POST | `/api/deck/medias?token=` | multipart `fichier` ; renvoie `{ "chemin": "medias/nom.ext" }` |
| GET | `/api/deck/medias?token=` | liste des médias |
| GET | `/api/quiz/questions` | questions (public, sans les bonnes réponses) |
| GET | `/api/quiz/questions?token=` | questions complètes |
| PUT | `/api/quiz/questions?token=` | corps : liste complète ; valide, écrit `quiz.json`, recharge |
| POST | `/api/quiz/ouvrir?token=` | `{ "question_id" }` |
| POST | `/api/quiz/fermer?token=` | — |
| POST | `/api/quiz/reveler?token=` | `{ "question_id" }` : calcule les points, publie la bonne réponse |
| POST | `/api/quiz/reinitialiser?token=` | `{ "question_id" }` |
| GET | `/api/quiz/classement?nombre=10` | public : top N `{ rang, pseudo, points }` (N ≤ 1000) |
| GET | `/api/seance` | public : `{ terminee, contact, presentation }` (docs/seance.md) |
| POST | `/api/seance/terminer?token=` | bouton Quitter : ferme la question ouverte, termine la séquence Q/R, `StopRecord`, journal `fin`, diffuse `{ type:"fin", contact, classement_total }` |
| POST | `/api/seance/reprendre?token=` | la séance reprend (`terminee: false`) ; l'activation d'une présentation fait de même |
| WS | `/ws/quiz` | état poussé : `{ type:"quiz", question, ouverte, revele, reste, resultat }`, `{ type:"obs", action, h }`, `{ type:"fin", terminee, contact }` |
| WS | `/ws/deck?role=` | synchronisation `{h, v, f}` |

Vues : `/q` (participant), `/q/resultats?question=` (résultats d'une question), `/q/classement?nombre=` (podium + top N ; `?ceremonie=1` : cérémonie animée déclenchée par `postMessage({type:"ceremonie"})`, `?ceremonie=auto` pour les tests), `/deck/?role=scene|obs|remote`, `/deck/remote.html?token=`, `/deck/editeur.html?token=`.

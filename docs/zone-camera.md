# Zone réservée à la caméra (vignette OBS)

Dans la scène OBS « Diapositives + vignette », la caméra est incrustée **en bas à droite, 300 × 300 px** (sur 1920 × 1080). Rien d'important ne doit s'y trouver sur les diapositives.

## Réglage

`presentation.json` : `"camera": {"largeur": 300, "hauteur": 300, "position": "bas-droite", "marge": 20}` (défaut si absent : exactement ces valeurs ; `position` parmi `bas-droite`, `bas-gauche`, `haut-droite`, `haut-gauche` ; `"camera": null` désactive). La zone réservée = rectangle de la caméra agrandi de `marge` de chaque côté : par défaut `x ≥ 1600, y ≥ 760`.

`GET /api/presentations/active` et la liste exposent `camera` (valeurs résolues).

## 1. Réagencement à la source (projecteur et OBS identiques)

`tools/reserver_camera.py presentations/<slug>/slides.html [--largeur 300 --hauteur 300 --position bas-droite --marge 20] [--toutes]` : pour chaque section `data-quiz` (ou toutes avec `--toutes`), chaque `.el` dont la boîte chevauche la zone est réagencé, dans cet ordre de préférence :
1. **réduire la hauteur** si l'élément est un `resultats`, `classement`, `iframe`, `minuteur`, `forme` ou `texte` et que sa hauteur reste ≥ 60 % de l'originale ;
2. sinon **décaler vers la gauche** si l'espace libre à gauche le permet (sans sortir de la scène ni chevaucher un autre `.el`) ;
3. sinon **réduire la largeur** (minimum 60 %) ;
4. sinon **décaler vers le haut** si possible ;
5. sinon le laisser et l'inscrire dans le rapport.
Les éléments `qr` sont déplacés en priorité vers le coin **bas-gauche** s'il est libre. Le script imprime un rapport (section, élément, action) et ne touche à rien d'autre. Rejouable (idempotent).

Appliquer aux sections de quiz de `presentations/iag-v1/slides.html` et `presentations/iag-v2/slides.html`, et aussi aux sections de classement et « Vos questions » de la v2 (le tableau final de la cérémonie doit s'arrêter avant la zone : la cérémonie limite son tableau à la hauteur disponible de l'iframe, donc réduire l'iframe suffit).

Les diapositives de quiz **standard** (`<section data-quiz>` construites par le runtime) : `quiz-deck.js` donne à la grille une hauteur de 920 px ; avec une caméra en bas à droite, la colonne de droite (résultats) reçoit `padding-bottom` = hauteur de la zone qui dépasse dans la grille, calculé au chargement à partir de `camera`. Même règle pour la diapositive de classement standard.

## 2. Éditeur

Calque « **Zone caméra OBS** » sur la scène (rectangle en pointillés avec l'icône 🎥 et « caméra 300×300 »), activé par défaut (case dans la barre, mémorisée en localStorage), rendu depuis `camera` de la présentation. Non sérialisé. Un élément qui chevauche la zone reçoit un contour orange et une infobulle « chevauche la zone caméra » dans la liste/panneau.

## 3. Secours au rendu OBS

Dans `deck/index.html`, rôle `obs` uniquement, après chaque chargement du deck : pour chaque section `.libre`, les `.el` qui chevauchent la zone (rectangles inline `left/top/width/height`) sont ajustés **en mémoire** (jamais réécrits dans le fichier) par le même algorithme que le script (une fonction commune en JS, `deck/camera.js`, et le script Python la réimplémente ; mêmes règles, mêmes seuils). Rôle `scene` : rien (le projecteur n'a pas de vignette). Sur le rechargement à chaud, l'ajustement est rejoué.

## Fiche OBS

`obs/README.md` : la vignette DSLR de la scène « Diapositives + vignette » est **en bas à droite, 300 × 300** (et non 25 % en bas à gauche comme avant) ; `docs/fiche-mise-en-route.md` ajuste la ligne correspondante.

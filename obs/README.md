# OBS sur le PC 2 : réalisation automatique

OBS joue le rôle du mélangeur : trois sources, trois scènes enchaînées par minuterie, enregistrement du programme déjà monté. Le replay sort d'OBS, pas d'un logiciel de montage.

## Sources

| Source | Entrée OBS | Réglage |
| --- | --- | --- |
| `DSLR` | Périphérique de capture vidéo (boîtier HDMI-USB) | 1080p, « utiliser les paramètres personnalisés » désactivé, audio du boîtier capturé |
| `Webcam` | Périphérique de capture vidéo | plan large depuis l'avant, résolution native |
| `Deck` | Source Navigateur | URL `https://<domaine>/deck/?role=obs`, 1920×1080, 30 i/s ; **décocher** « Arrêter la source quand non visible » et « Rafraîchir le navigateur quand la scène devient active » |
| Audio de référence | micro du DSLR (ou de la webcam) | Paramètres → Audio avancé : piste **2** uniquement ; sert au recalage par `postprod/assembler.py` |

Le deck est chargé une seule fois au démarrage d'OBS et reste synchronisé par WebSocket. Si le serveur tombe, la source garde la dernière diapositive affichée.

## Scènes

| Scène | Composition | Durée par passage |
| --- | --- | --- |
| `Intervenant` | DSLR plein cadre | 45 s |
| `Diapositives + vignette` | Deck plein écran, DSLR en vignette **300 × 300 en bas à droite** (recadrée au carré, calée dans le coin) | 120 s |
| `Amphithéâtre` | Webcam plein cadre | 15 s |

Vignette de secours : une scène `Diapositives + webcam` identique à la deuxième avec la webcam en vignette, pour le cas où le DSLR surchauffe.

La zone des diapositives sous la vignette (x ≥ 1600, y ≥ 760, soit la vignette plus 20 px de marge) est **réservée** : rien d'important ne s'y trouve (docs/zone-camera.md). Les decks sont réagencés à la source par `tools/reserver_camera.py`, l'éditeur affiche la zone (calque « Caméra ») et le rôle `obs` du deck ajuste en secours les éléments qui déborderaient. Si la vignette change de taille ou de coin dans OBS, régler `camera` dans `presentation.json` (`{"largeur": 300, "hauteur": 300, "position": "bas-droite", "marge": 20}` ; `null` si la scène n'a pas de vignette).

## Advanced Scene Switcher (séquence en boucle)

Plugin : <https://obsproject.com/forum/resources/advanced-scene-switcher.395/>. Onglet **Macros**, trois macros en chaîne :

1. `Vers diapos` — condition : scène courante = `Intervenant` depuis 45 s → action : passer à `Diapositives + vignette`.
2. `Vers amphi` — condition : scène courante = `Diapositives + vignette` depuis 120 s → action : passer à `Amphithéâtre`.
3. `Vers intervenant` — condition : scène courante = `Amphithéâtre` depuis 15 s → action : passer à `Intervenant`.

Onglet **Général** : « Démarrer le plugin au lancement d'OBS » coché. Une macro supplémentaire `Pause quiz` peut mettre les trois autres en pause tant que la scène de quiz est forcée (condition : scène = `Diapositives + vignette` **et** macro `Pont quiz` active) ; sinon le pont ci-dessous suffit, puisque les macros ne font que relancer la boucle après le retour.

## Raccourcis clavier (Paramètres → Raccourcis)

| Touche | Action |
| --- | --- |
| F1 | Passer à `Intervenant` |
| F2 | Passer à `Diapositives + vignette` |
| F3 | Passer à `Amphithéâtre` |
| F4 | Passer à `Diapositives + webcam` (secours DSLR) |
| F9 | Démarrer / arrêter l'enregistrement |

## Scène forcée pendant un quiz

Le serveur du VPS ne peut pas joindre OBS derrière le hotspot. Le pont `obs/obs_quiz_bridge.py` tourne sur le PC 2, écoute `/ws/quiz` et parle à obs-websocket en local :

```
pip install websockets
python obs/obs_quiz_bridge.py wss://<domaine> --obs-mdp <mot de passe obs-websocket>
```

obs-websocket : Outils → Paramètres du serveur WebSocket → activer, port 4455, mot de passe. À l'ouverture d'une question, la scène `Diapositives + vignette` est forcée ; à la fermeture, retour à la scène précédente. Si le pont tombe : F2 à la main.

Le même pont **arrête l'enregistrement** (`StopRecord`) 2 s après l'ouverture de la séquence questions-réponses depuis la télécommande (bouton Q/R → Commencer), et affiche « enregistrement arrêté avant les questions-réponses ». Il ne le relance pas à la fin : les questions-réponses ne font pas partie du replay (voir docs/questions.md).
Option `--sans-arret` pour désactiver cet arrêt automatique (il faut alors appuyer sur F9 soi-même avant Commencer).

Le pont **démarre et arrête aussi l'enregistrement sur ordre des diapositives** (docs/seance.md) : une section de `slides.html` portant `data-obs="demarrer"` ou `data-obs="arreter"` fait diffuser par le serveur `{"type":"obs","action":"StartRecord"|"StopRecord","h":n}` à l'arrivée dessus (champ « OBS » du panneau de diapositive de l'éditeur).
Avant d'agir, le pont lit `GetRecordStatus` : `StartRecord` est ignoré si OBS enregistre déjà, `StopRecord` si rien ne tourne ; console « enregistrement démarré par la diapositive n ».
Le bouton **Quitter** de la télécommande (dernière diapositive) envoie aussi un `StopRecord` de sécurité.
Option `--sans-enregistrement` pour ignorer tous ces messages (F9 à la main, comme avant).

## Enregistrement

Paramètres → Sortie, mode avancé, onglet Enregistrement :

- Format **MKV** (survit à un plantage), remux en MP4 après la session (Fichier → Remultiplexer les enregistrements) ou directement par `assembler.py`.
- Encodeur **NVENC H.264** (NVIDIA) ou **QuickSync H.264** (Intel), CBR 12 000 kb/s, 1080p, 30 i/s, intervalle d'images-clés 2 s.
- Pistes audio **1 et 2** cochées : piste 1 = mixage (secours), piste 2 = référence DSLR seule.
- Paramètres → Vidéo : base 1920×1080, sortie 1920×1080, 30 i/s.

Démarrer l'enregistrement **avant** le premier clap, l'arrêter **après** le dernier.

## Hygiène du PC 2

Pas de navigateur ouvert, mises à jour et veille désactivées, alimentation secteur, 50 Go libres. Surveiller dans la barre d'état d'OBS : images perdues (doit rester à 0) et charge CPU (< 70 %).

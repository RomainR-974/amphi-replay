# PRD – Captation solo et deck interactif en amphi

Nov 7, 2026 · @romain

## Contexte, problème et objectifs

L'intervention (formation-atelier en amphithéâtre, université, semaine du 9 novembre 2026) doit produire un replay monté multi-sources quelques minutes après la fin, sans service communication ni équipe technique. Une seule personne est à la fois intervenant, réalisateur et technicien.

Le support n'est aujourd'hui qu'un PDF. Il est converti en deck HTML pour devenir interactif (iframes, quiz en direct) et pour être rendu à l'identique sur le vidéoprojecteur et dans l'enregistrement.

Objectifs mesurables :

1. Replay disponible en fichier MP4 monté moins de 10 minutes après la fin de l'intervention.
2. Trois plans alternés automatiquement pendant la session : intervenant plein cadre, vue amphithéâtre, diapositives avec intervenant en vignette.
3. Diapositives identiques, à moins de 500 ms d'écart, entre le vidéoprojecteur et la source OBS.
4. Quiz en direct joignable par QR code, résultats visibles sur le projecteur et dans le replay.
5. Aucune dépendance de l'intervention elle-même au système de captation ou au serveur : si tout tombe, le cours continue.

Contraintes fermes : aucun achat de matériel, pas de câble Ethernet entre les machines, pas d'accès au wifi inter-postes de l'université, enregistrement audio sur téléphone recalé après coup.

## Périmètre et hors périmètre

Dans le périmètre de la version 1 :

- Deck HTML (reveal.js) converti depuis le PDF, avec fragments, iframes et diapositives de quiz.
- Serveur unique (FastAPI) hébergé sur un VPS : synchronisation du deck, télécommande, quiz, page de résultats.
- Télécommande sur téléphone avec notes de l'orateur et commandes du quiz.
- Quiz à un seul type de question (QCM à choix unique), résultats en histogramme mis à jour en direct.
- Réalisation OBS sur le PC 2 : trois scènes, changement automatique par minuterie, enregistrement local.
- Recalage audio et assemblage final par script.

Hors périmètre, explicitement :

- Diffusion en direct (streaming) : enregistrement uniquement.
- Réalisation pilotée par la voix (Gabin) : évaluée, non retenue pour un orateur unique.
- Questions ouvertes, nuages de mots, classement, score par participant.
- Comptes participants, authentification, export nominatif des réponses.
- Montage manuel dans un logiciel de montage (Kdenlive, DaVinci Resolve, FilmCraft) : réservé au secours si l'enregistrement OBS est inutilisable.
- Captation du flux vidéo du PC 1 vers le PC 2 (HDMI, NDI) : remplacée par le rendu local du deck.

## Rôles et utilisateurs

| Rôle | Qui | Appareil | Ce qu'il fait |
| --- | --- | --- | --- |
| Orateur / régisseur | l'intervenant | téléphone | avance les diapositives, lit ses notes, ouvre et ferme les quiz, surveille l'état des deux PC |
| Écran projecteur | PC 1 | navigateur plein écran sur le vidéoprojecteur | suit l'état du deck ; reste autonome si le serveur tombe |
| Source OBS | PC 2 | source Navigateur dans OBS | suit l'état du deck, rend les mêmes diapositives à 1920×1080 |
| Participant | étudiants, 50 à 300 | leur téléphone, via QR code | répondent aux quiz, anonymement, sans compte |
| Post-production | l'intervenant, après la session | PC 2 | lance le script de recalage audio et d'assemblage |

Un seul être humain pour quatre rôles : chaque interface doit tenir en un écran, sans menu, et chaque action critique doit se faire d'un seul geste pendant qu'il parle.

## Architecture générale

Le serveur ne transporte aucune vidéo : il relaie un état (index de diapositive, question ouverte) et chaque machine rend le deck localement. Le téléphone est l'unique émetteur de commandes ; PC 1 et PC 2 ne font que suivre.

&#91;embedded content: architecture · 1 serveur, 3 machines, participants\]

Le serveur vit sur un VPS parce que les participants arrivent par leur propre réseau : il doit être joignable depuis internet, ce qu'un serveur sur le PC 1 derrière le hotspot ne permet pas. Les captures DSLR et webcam, l'enregistrement OBS et l'audio du téléphone restent entièrement locaux ; le serveur peut tomber sans interrompre la captation.

## Composant 1 : deck HTML et synchronisation

Le deck est un reveal.js servi par le serveur, ouvert en trois rôles par paramètre d'URL ; l'état partagé est le triplet `{h, v, f}` (diapositive horizontale, verticale, fragment).

Exigences fonctionnelles :

1. Conversion du PDF en reveal.js : une section par page, texte repris en HTML (pas d'images de pages), thème unique 16:9.
2. Rôle `scene` (PC 1) : plein écran, contrôles masqués, suit l'état reçu, clavier actif en secours.
3. Rôle `obs` (PC 2) : `controls`, `progress` et `keyboard` désactivés, aucune transition d'entrée (fondu 0 ms) pour que les deux rendus soient simultanés.
4. Tout changement reçu est appliqué par `Reveal.slide(h, v, f)` ; les fragments sont synchronisés (`fragmentshown` / `fragmenthidden`), pas seulement les diapositives.
5. À la connexion et à chaque reconnexion, le client lit `GET /etat` et se recale avant d'écouter le WebSocket.
6. Reconnexion automatique du WebSocket avec délai croissant (1 s, 2 s, 4 s, plafond 10 s), sans rechargement de page.
7. Les iframes embarquées pointent sur des URL publiques et sans état (page de résultats, démonstrations) : jamais une vue connectée.
8. Les vidéos embarquées sont évitées en v1 ; si une est indispensable, sa lecture est déclenchée par un message dédié et non par l'autoplay.

Serveur, pour ce composant : un endpoint WebSocket `/ws/deck` qui relaie tout message reçu du rôle `remote` à tous les autres clients et mémorise le dernier état ; `GET /etat` renvoie cet état ; fichiers statiques du deck sous `/deck/`.

## Composant 2 : télécommande téléphone / vue présentateur

Le téléphone (rôle `remote`) est la seule source de vérité : il embarque le même deck en miniature, applique chaque commande localement puis publie `Reveal.getIndices()` au serveur. Il remplace la vue présentateur de PowerPoint.

Contenu d'un seul écran, en portrait :

- Miniature de la diapositive courante et numéro `n / total`.
- Notes de l'orateur (reveal `<aside class="notes">`) en texte lisible à bout de bras : 18 px minimum.
- Deux boutons larges Précédent / Suivant sur toute la largeur du bas de l'écran, plus balayage latéral.
- Bloc quiz, visible uniquement sur une diapositive de quiz : Ouvrir, Fermer, nombre de réponses reçues, Réinitialiser (avec confirmation).
- Indicateur d'état : connexion au serveur, dernier accusé de réception de `scene` et de `obs` (vert si reçu depuis moins de 5 s).
- Chronomètre démarré à la première diapositive.

Exigences :

1. Écran maintenu allumé (Wake Lock API) ; le hotspot et l'enregistreur audio tournent en même temps, téléphone branché sur secteur.
2. Toute commande est appliquée localement avant l'envoi : la télécommande réagit même sans réseau, et l'état est renvoyé à la reconnexion.
3. Aucun geste accidentel : pas de bouton Réinitialiser sans confirmation, pas de navigation par simple toucher sur la miniature.
4. Accès protégé par un jeton dans l'URL, suffisant pour une session d'une heure.

## Composant 3 : quiz participants

Un seul type de question en v1 : QCM à choix unique, 2 à 5 options, résultats en histogramme mis à jour en direct. Les questions sont déclarées dans un fichier de configuration du deck, pas saisies pendant la session.

Trois vues :

| Vue | URL | Qui | Contenu |
| --- | --- | --- | --- |
| Participant | `/q` (QR code, URL courte) | étudiants | question ouverte ou message d'attente ; boutons de réponse ; confirmation « réponse enregistrée » |
| Résultats | `/q/resultats` (lecture seule, publique) | embarquée dans le deck, donc PC 1 et PC 2 | histogramme par option, nombre de réponses, question en titre ; aucune action possible |
| Admin | bloc quiz de la télécommande | l'orateur | ouvrir, fermer, compteur, réinitialiser |

Exigences :

1. Anonymat strict : aucun nom, aucun compte, aucune adresse. Un identifiant aléatoire en cookie empêche le double vote sur une question ; une réponse peut être modifiée tant que la question est ouverte.
2. Une seule question ouverte à la fois ; l'ouverture est une action explicite de l'orateur, jamais liée au changement de diapositive.
3. La page participant se recharge d'elle-même à chaque ouverture ou fermeture (WebSocket ou repli en interrogation toutes les 3 s) : l'étudiant n'a rien à rafraîchir.
4. La page de résultats s'actualise au plus 1 s après chaque vote ; les barres sont lisibles depuis le fond de l'amphi (police 32 px minimum à 1920×1080).
5. QR code présent sur la première diapositive et sur chaque diapositive de quiz, avec l'URL écrite en clair à côté.
6. Les réponses sont journalisées en SQLite (question, option, horodatage, identifiant anonyme) pour un export agrégé après la session.
7. Charge cible : 300 connexions WebSocket simultanées et 300 votes en 10 s sur un VPS à 1 vCPU, sans dégradation visible.

Page de secours : chaque diapositive de quiz existe aussi sous forme statique (question + options) pour un vote à main levée si le serveur est injoignable.

## Composant 4 : captation OBS et réalisation automatique

OBS sur le PC 2 joue le rôle du mélangeur : il reçoit les trois sources, enchaîne les scènes seul et enregistre le programme déjà monté. Le replay sort d'OBS, pas d'un logiciel de montage.

Sources :

| Source | Entrée OBS | Réglage |
| --- | --- | --- |
| DSLR, plan intervenant | sortie HDMI propre vers boîtier de capture USB | 1080p, autofocus continu, fausse batterie ou secteur, cadrage large pour tolérer les déplacements |
| Webcam, plan amphithéâtre | USB | plan large depuis l'avant, pas de visage en gros plan |
| Diapositives | source Navigateur, `…/deck?role=obs` | 1920×1080, 30 i/s, « arrêter la source quand non visible » désactivé, rafraîchissement à l'activation désactivé |
| Audio de référence | micro du DSLR ou de la webcam | piste 2 de l'enregistrement, sert uniquement au recalage |

Scènes et réalisation automatique (plugin Advanced Scene Switcher, séquence en boucle) :

| Scène | Composition | Durée par passage |
| --- | --- | --- |
| Intervenant | DSLR plein cadre | 45 s |
| Diapositives + vignette | deck plein écran, DSLR en vignette 25 % en bas à gauche | 120 s |
| Amphithéâtre | webcam plein cadre | 15 s |

Règles :

1. Une diapositive de quiz ouverte force la scène « Diapositives + vignette » tant que la question est ouverte (déclenchement par un message du serveur vers OBS WebSocket, ou à défaut par raccourci clavier).
2. Raccourcis clavier sur le PC 2 pour forcer une scène à la main : F1 intervenant, F2 diapositives, F3 amphi.
3. Enregistrement en MKV, encodeur matériel (NVENC ou QuickSync), 1080p30, 12 Mb/s, pistes audio séparées ; remux en MP4 après la session.
4. Enregistrement démarré avant le premier clap et arrêté après le dernier.
5. Le PC 2 ne fait rien d'autre : pas de navigateur ouvert, pas de mises à jour, alimentation secteur, veille désactivée.

Abandonné après évaluation : Gabin (One Click Studio), régie automatique pilotée par le micro qui parle. Avec un seul orateur, elle reste sur le même plan ; la minuterie donne le même résultat sans dépendance supplémentaire.

## Composant 5 : audio et post-production

Le son principal est enregistré sur le téléphone avec le lavalier filaire, indépendamment d'OBS, puis recalé et assemblé par un script : trois minutes de traitement, aucune ouverture de logiciel de montage.

Prise de son :

1. Application d'enregistrement en WAV 48 kHz mono (Easy Voice Recorder ou RecForge sur Android) ; à défaut AAC 256 kb/s.
2. Enregistrement démarré avant le premier clap, arrêté après le dernier ; le téléphone reste en poche, lavalier au col.
3. Deux claps devant le DSLR : un au début, un à la fin. Le premier donne le décalage, le second mesure la dérive d'horloge entre le téléphone et le PC.
4. Piste de référence conservée dans l'enregistrement OBS (micro DSLR ou webcam) pour le recalage automatique.

Script `assembler.py`, lancé sur le PC 2 avec les deux fichiers en entrée :

1. Extraction de la piste de référence OBS en WAV.
2. Calcul du décalage par corrélation croisée (bibliothèque `audio-offset-finder` ou numpy) sur les 60 premières secondes ; contrôle sur les 60 dernières pour mesurer la dérive.
3. Correction de la dérive par rééchantillonnage (`asetrate` / `aresample`) si elle dépasse 40 ms sur la durée.
4. Normalisation du niveau à −16 LUFS (`loudnorm`), filtre passe-haut à 80 Hz.
5. Remux : vidéo OBS copiée sans réencodage, piste téléphone en AAC 192 kb/s, `-shortest`.
6. Sortie `replay_AAAA-MM-JJ.mp4` et journal des valeurs calculées (décalage, dérive, durée).

Critère d'acceptation : désynchronisation lèvres/voix inférieure à 60 ms au début comme à la fin du replay, vérifiée sur les deux claps.

## Exigences non fonctionnelles

| Domaine | Exigence | Mesure |
| --- | --- | --- |
| Latence de synchronisation | diapositive identique sur projecteur et OBS | < 500 ms entre les deux rendus, mesuré à la caméra |
| Résilience deck | perte du serveur sans effet sur l'amphi | la page `scene` continue d'avancer au clavier du PC 1 ; resynchronisation automatique au retour |
| Résilience captation | perte du serveur ou du réseau sans effet sur l'enregistrement | OBS ne dépend que des entrées USB et de sa propre source Navigateur déjà chargée |
| Charge quiz | 300 participants | 300 WebSocket + 300 votes en 10 s, CPU < 50 % sur 1 vCPU |
| Charge PC 2 | encodage + 2 captures + navigateur | CPU < 70 %, aucune image perdue sur 60 min (compteur OBS) |
| Sécurité | télécommande et admin inaccessibles au public | jeton dans l'URL, HTTPS obligatoire (certificat Let's Encrypt), rôles `remote` et admin refusés sans jeton |
| Données personnelles | aucune donnée identifiante | pas de nom, pas d'adresse IP conservée, identifiant anonyme purgé à la fin de la session ; droit à l'image annoncé oralement en début de séance, plan amphi large uniquement |
| Autonomie | 90 min de session + 30 min de marge | PC 1, PC 2 et téléphone sur secteur ; DSLR sur secteur ou fausse batterie |
| Observabilité | savoir en 2 s si une machine a décroché | accusés de réception `scene` / `obs` affichés sur la télécommande |

## Infrastructure, réseau et matériel

Réseau : le téléphone de l'orateur crée un point d'accès 4G ; PC 1 et PC 2 s'y connectent et joignent le serveur par internet. Le wifi de l'université n'est utilisé par aucune des trois machines. Les participants utilisent leur propre réseau (wifi université ou 4G).

Serveur :

| Élément | Choix |
| --- | --- |
| Hébergement | VPS 1 vCPU, 2 Go, Debian ou Ubuntu, dans un centre proche (métropole acceptable : 200 ms de latence vers La Réunion restent invisibles pour des index de diapositives) |
| Pile | Python 3.12, FastAPI, uvicorn, SQLite ; servi derrière Caddy pour le HTTPS automatique |
| Domaine | un sous-domaine court pour le QR code, du type `q.<domaine>` |
| Déploiement | `git pull` + redémarrage du service systemd ; pas de conteneur en v1 |
| Sauvegarde | copie de la base SQLite après la session |

Matériel, tout déjà en possession :

| Élément | Rôle | Point de vigilance |
| --- | --- | --- |
| PC 1 | projecteur, navigateur plein écran | sortie HDMI vers le vidéoprojecteur, mode « étendre » désactivé : un seul écran, celui de l'amphi |
| PC 2 | OBS, enregistrement, assemblage | encodeur matériel disponible, 50 Go libres, veille et mises à jour désactivées |
| DSLR | plan intervenant | sortie HDMI propre (sans affichage), limite 30 min contournée par la capture HDMI, surchauffe testée sur 60 min |
| Boîtier de capture HDMI-USB | DSLR → PC 2 | déjà en possession ; sinon enregistrement interne du DSLR en secours (voir modes dégradés) |
| Webcam | plan amphi | USB, câble assez long pour l'avant de la salle |
| Téléphone | télécommande, hotspot, enregistreur | sur secteur, écran maintenu allumé, notifications coupées, mode « ne pas déranger » |
| Lavalier filaire | micro principal | testé sur la prise du téléphone (TRRS ou USB-C) avant le jour J |
| Vidéoprojecteur de l'amphi | affichage | résolution native vérifiée à l'arrivée, deck en 16:9 |

À confirmer avant la session : disponibilité du boîtier de capture, et réponse du service technique de l'amphi sur une sortie ligne de la sonorisation (si oui, elle remplace avantageusement le téléphone comme micro principal).

## Modes dégradés et secours

Principe : chaque panne a un geste de secours connu d'avance, exécutable en moins de 30 secondes sans arrêter de parler. L'intervention passe avant le replay, le replay passe avant le quiz.

| Panne | Effet | Geste de secours |
| --- | --- | --- |
| Serveur injoignable | télécommande et quiz inactifs, OBS ne suit plus | PC 1 piloté au clavier ; quiz à main levée sur la diapositive statique ; OBS reste en scène « Intervenant », diapositives recalées après coup si besoin |
| Hotspot tombé | même effet que ci-dessus | redémarrer le hotspot ; sinon idem serveur injoignable |
| Téléphone HS | plus de télécommande ni de son principal | clavier du PC 1 ; le son de référence OBS devient le son final (qualité moindre, replay conservé) |
| PC 2 ou OBS plante | plus de captation | enregistrement interne du DSLR lancé en parallèle depuis le début (carte SD, segments de 30 min) ; montage multicam après coup |
| Boîtier de capture absent ou défaillant | pas de flux DSLR dans OBS | DSLR enregistre en interne ; OBS ne garde que diapositives + webcam |
| DSLR surchauffe | perte du plan intervenant | scène forcée sur « Diapositives + vignette » avec la webcam en vignette (F2) |
| PC 1 plante | plus d'image en amphi | le PC 2 bascule sur la sortie HDMI de l'amphi avec `?role=scene` ; captation sacrifiée |
| Quiz saturé ou bugué | résultats faux ou absents | fermer la question, passer à main levée ; ne jamais attendre devant l'amphi |
| Dérive audio non corrigeable | décalage en fin de replay | découper le replay en deux parties recalées séparément |

Systématiquement, quelles que soient les pannes : enregistrement interne du DSLR et enregistrement audio du téléphone tournent du premier au dernier clap. Ce sont les deux filets qui garantissent un replay, même dégradé.

## Plan de test et répétition

Rien n'est considéré prêt sans une répétition générale de 60 minutes en conditions réelles, chaîne complète, chronomètre en main. Les tests unitaires des composants ne remplacent pas cette répétition.

- [ ] Deck : les 100 % des diapositives rendues à l'identique sur PC 1 et PC 2 (capture d'écran comparée), fragments compris.
- [ ] Synchronisation : 50 changements de diapositive depuis le téléphone, écart PC 1 / PC 2 filmé et mesuré, aucun décrochage.
- [ ] Reconnexion : couper le hotspot 30 s en pleine séquence ; PC 1 continue au clavier ; PC 2 se recale seul au retour.
- [ ] Quiz : 10 téléphones réels (pas 2), un vote chacun, double vote refusé, résultats identiques sur PC 1 et PC 2 en moins d'1 s.
- [ ] Charge quiz : 300 clients simulés (script `websockets` + `httpx`) sur le VPS, CPU et latence relevés.
- [ ] OBS : 60 minutes d'enregistrement continu, zéro image perdue, température du DSLR et charge CPU notées toutes les 10 minutes.
- [ ] Réalisation automatique : la séquence des trois scènes tourne 20 minutes sans intervention ; le forçage F1/F2/F3 fonctionne.
- [ ] Audio : enregistrement téléphone de 60 minutes, deux claps, script d'assemblage exécuté ; décalage et dérive relevés ; vérification lèvres/voix au début et à la fin.
- [ ] Modes dégradés : chaque ligne du tableau précédent jouée une fois, chronomètre en main, geste de secours en moins de 30 s.
- [ ] Terrain : visite de l'amphi, résolution du vidéoprojecteur, réception 4G mesurée à l'estrade et au fond, longueur des câbles, prises secteur, réponse du service technique sur la sonorisation.
- [ ] Répétition générale : intervention jouée en entier devant une personne, replay produit et visionné dans les 10 minutes.

Sortie de la répétition : une fiche de mise en route (ordre des branchements, URL, raccourcis, numéros de scène) imprimée et posée sur le pupitre le jour J.

## Planning sur une semaine

La répétition générale occupe toute la journée J-1 : ce qui n'est pas prêt à J-2 au soir sort du périmètre, le quiz en premier.

&#91;embedded content: planning · 5 chantiers sur 6 jours, répétition à J-1\]

Ordre de priorité si le temps manque : 1. captation OBS et audio (le replay), 2. deck et synchronisation, 3. télécommande, 4. quiz. Le mail au service technique de l'amphi part dès J-5 : sa réponse sur la sonorisation peut supprimer tout le chantier audio téléphone.

## Risques

Le premier risque n'est pas technique : c'est le volume de développement pour une seule personne en cinq jours, avec une intervention à préparer par ailleurs.

| Risque | Probabilité | Impact | Parade |
| --- | --- | --- | --- |
| Calendrier trop chargé, quiz non terminé | forte | moyen | périmètre coupé à J-2 au soir ; quiz remplacé par main levée sans honte |
| Réception 4G insuffisante dans l'amphi | moyenne | fort | mesurée à la visite J-2 ; sinon serveur de secours sur le PC 1 pour la synchro seule, quiz abandonné |
| Boîtier de capture HDMI absent | moyenne | fort | enregistrement interne du DSLR et montage multicam après coup : replay à J+1 au lieu de J+10 min |
| DSLR en surchauffe ou arrêt à 30 min | moyenne | moyen | test de 60 min à J-2 ; webcam en vignette de secours |
| Charge CPU du PC 2 avec deux captures et le navigateur | moyenne | fort | encodeur matériel, 30 i/s, test de 60 min ; au pire, webcam amphi retirée |
| Dérive d'horloge téléphone / PC supérieure à la correction | faible | moyen | deuxième clap obligatoire ; découpage en deux parties |
| Geste raté sur la télécommande pendant l'exposé | moyenne | faible | boutons larges, confirmation sur les actions destructives, clavier PC 1 en repli |
| Gel de l'orateur devant une panne en direct | moyenne | fort | fiche de secours imprimée, chaque panne répétée une fois à J-1 |
| Droit à l'image des étudiants | faible | moyen | annonce orale en début de séance, plan amphi large, replay diffusé en interne uniquement |

## Évolutions ultérieures

Après une première session réussie, le système devient un kit réutilisable pour toute intervention en amphi ou en salle. Les évolutions, dans l'ordre de valeur :

1. Synchronisation deck → OBS : le serveur pilote les scènes par OBS WebSocket (scène forcée sur les diapositives de quiz, plan intervenant sur les diapositives « parole »), ce qui remplace la minuterie aveugle par une réalisation guidée par le contenu.
2. Micro sans fil ou sortie ligne de la salle en entrée directe d'OBS : suppression du recalage audio, replay disponible à l'arrêt de l'enregistrement.
3. Chapitrage automatique du replay à partir du journal des changements de diapositive (titres de sections en horodatage YouTube ou en chapitres MP4).
4. Types de questions supplémentaires : choix multiples, échelle 1 à 5, nuage de mots ; export agrégé en CSV pour un bilan de séance.
5. Diffusion en direct en plus de l'enregistrement, si une salle distante doit suivre.
6. Montage scripté à partir du journal des scènes (`ffmpeg` ou `melt`) comme alternative au programme OBS, pour ré-éditer un replay sans refaire la session ; FilmCraft réévalué quand il aura dépassé ses premières versions expérimentales.
7. Télécommande installable (PWA) avec mode hors ligne complet, pour s'affranchir de toute dépendance au serveur côté orateur.

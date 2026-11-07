# Fiche de mise en route — jour J

À imprimer et poser sur le pupitre. Remplir les blancs après la répétition générale.

**Domaine** : `________________`  **Jeton** : `________________`

## Ordre des branchements (J-0, 45 min avant)

1. Téléphone sur secteur, mode « ne pas déranger », notifications coupées. Lancer le **hotspot 4G**.
2. **PC 2** sur secteur, connecté au hotspot. Brancher boîtier HDMI-USB (DSLR) et webcam. Lancer OBS. Vérifier les trois sources, piste audio 2 = DSLR.
3. **DSLR** sur secteur ou fausse batterie, sortie HDMI propre, autofocus continu, carte SD vide → enregistrement interne lancé en parallèle.
4. **PC 1** sur secteur, connecté au hotspot, HDMI vers le vidéoprojecteur, mode « dupliquer » (un seul écran). Navigateur plein écran sur `https://<domaine>/deck/?role=scene` (F11).
5. PC 2 : source Navigateur `https://<domaine>/deck/?role=obs` chargée, scène `Intervenant` affichée. Lancer `obs_quiz_bridge.py` (facultatif).
6. Téléphone : ouvrir `https://<domaine>/remote?token=…`. Vérifier les **trois voyants verts** (serveur, scene, obs). Avancer d'une diapositive et revenir : les deux PC suivent.
7. Lavalier au col, enregistreur du téléphone en WAV 48 kHz mono. Tester 10 s, réécouter.
8. Dernière vérification : `https://<domaine>/sante` depuis le téléphone, `https://<domaine>/q` depuis un second téléphone.

## Séquence de démarrage

1. OBS : **F9** (enregistrement) — vérifier le compteur rouge.
2. Téléphone : démarrer l'**enregistreur audio**.
3. **Clap n°1** devant le DSLR, bien audible.
4. Annonce orale du droit à l'image (plan amphi large, replay interne).
5. Première diapositive : le chronomètre de la télécommande démarre.

## Pendant l'intervention

- Suivant / Précédent : boutons du bas ou balayage sur la miniature.
- Diapositive de quiz : **Ouvrir** → attendre 60 à 90 s en regardant le compteur → **Fermer** → commenter.
- Voyant orange ou rouge pendant plus de 10 s : regarder le PC concerné, sans s'arrêter de parler.

## Séquence de fin

1. Dernière diapositive, questions du public.
2. **Clap n°2** devant le DSLR.
3. Arrêter l'enregistreur du téléphone, puis OBS (**F9**), puis le DSLR.

## Post-production (PC 2, < 10 min)

```
python postprod/assembler.py "<fichier OBS .mkv>" "<fichier téléphone .wav>"
```

Vérifier lèvres/voix sur les deux claps. Puis `sudo bash deploy/sauvegarde.sh` sur le VPS (export CSV, purge).

## Secours (geste en moins de 30 s)

| Panne | Geste |
| --- | --- |
| Serveur injoignable | PC 1 au clavier (→ / ←). Quiz à main levée. OBS reste sur `Intervenant` (F1). |
| Hotspot tombé | Redémarrer le hotspot. Sinon : ligne ci-dessus. |
| Téléphone HS | Clavier PC 1. Le son OBS devient le son final. |
| PC 2 / OBS plante | Rien à faire en direct : le DSLR enregistre en interne. Montage à J+1. |
| DSLR surchauffe | **F4** (diapositives + webcam). |
| PC 1 plante | HDMI de l'amphi sur le PC 2, navigateur `?role=scene`. Captation sacrifiée. |
| Quiz saturé | Fermer la question, main levée. Ne jamais attendre devant l'amphi. |

Raccourcis OBS : **F1** intervenant · **F2** diapositives · **F3** amphi · **F4** diapos + webcam · **F9** enregistrement.

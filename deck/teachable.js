/*
 * Élément « teachable » : un modèle d'images Teachable Machine (export TensorFlow.js) en direct dans une diapositive.
 *
 *   <div class="el teachable" data-amphi="teachable" data-src="medias/tm-objets/" data-id="e7" style="…"></div>
 *
 * data-src : dossier contenant model.json, metadata.json et weights.bin (fichiers de l'export « TensorFlow.js »).
 *
 * data-mode="entrainement" : simulation pédagogique d'un entraînement, en trois temps.
 *   1. Collecte : un bouton par classe du modèle (« Objet 1 · gourde », « Objet 2 · stylo », « Vide ») ; chaque appui
 *      prend 300 images de la webcam en 5 secondes, affichées en mosaïque.
 *   2. Entraînement : animation d'une usine (engrenages, fumée, images qui entrent sur un tapis) et barre de progression.
 *   3. Test : la webcam et les barres de probabilité, comme le mode normal.
 *   L'entraînement est simulé : la prédiction utilise le modèle déjà entraîné de data-src (mêmes classes, mêmes objets).
 *
 * Rôle scene (projecteur, PC 1) et replay : webcam du poste, uniquement pendant que la diapositive est affichée (caméra
 * libérée dès qu'on la quitte ; la simulation repart de zéro au retour). Bibliothèques chargées à la demande depuis
 * deck/vendor/tm/ (tools/fetch_reveal.sh) : aucune dépendance à un CDN pendant la séance.
 * Rôles obs et remote : encadré « démonstration en direct » — la caméra du PC 2 est déjà prise par OBS, et la
 * miniature de la télécommande n'a pas à ouvrir de caméra. Voir docs/format-deck.md.
 */
window.AmphiTeachable = (() => {
  const SCRIPTS = ['vendor/tm/tf.min.js', 'vendor/tm/teachablemachine-image.min.js'];
  const PERIODE_MS = 120; // une prédiction toutes les 120 ms suffit et laisse respirer le projecteur
  const IMAGES_PAR_CLASSE = 300;
  const DUREE_COLLECTE_MS = 5000;
  const DUREE_ENTRAINEMENT_MS = 8000;
  const ETAPES_ENTRAINEMENT = ['Lecture des images…', 'Recherche de motifs…', 'Ajustement des poids du modèle…', 'Vérification sur des images de test…', 'Modèle prêt !'];
  const role = () => (window.AmphiSync && window.AmphiSync.role) || 'scene';
  const actif = () => role() === 'scene' || role() === 'replay';
  const simulation = el => el.dataset.mode === 'entrainement';
  let chargement = null;

  function chargerScripts() {
    if (window.tmImage) return Promise.resolve();
    if (!chargement) {
      chargement = SCRIPTS.reduce((suite, src) => suite.then(() => new Promise((ok, ko) => {
        const s = document.createElement('script');
        s.src = src; s.onload = ok; s.onerror = () => ko(new Error('bibliothèque introuvable : ' + src + ' (lancer tools/fetch_reveal.sh)'));
        document.head.appendChild(s);
      })), Promise.resolve());
      chargement.catch(() => { chargement = null; });
    }
    return chargement;
  }

  const ICONE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7h3l2-3h8l2 3h3v13H3z"/><circle cx="12" cy="13" r="4"/></svg>';

  // Usine de l'étape 2 : tapis d'images qui entrent, engrenages qui tournent, fumée qui sort des cheminées.
  const ENGRENAGE = 'M0,-30 L6,-30 L8,-20 L16,-16 L24,-22 L28,-17 L21,-9 L24,0 L34,2 L34,9 L24,11 L21,19 L28,26 L23,30 L16,23 L8,27 L6,37 L-1,37 L-3,27 L-11,23 L-18,30 L-23,26 L-16,19 L-19,11 L-29,9 L-29,2 L-19,0 L-16,-9 L-23,-17 L-19,-22 L-11,-16 L-3,-20 Z';
  const USINE = '<svg class="tm-usine-svg" viewBox="0 0 800 420" aria-hidden="true">'
    + '<g class="tm-fumees"><circle class="tm-fumee f1" cx="470" cy="70" r="22"/><circle class="tm-fumee f2" cx="470" cy="70" r="26"/><circle class="tm-fumee f3" cx="470" cy="70" r="20"/>'
    + '<circle class="tm-fumee f1" cx="580" cy="40" r="24"/><circle class="tm-fumee f2" cx="580" cy="40" r="20"/><circle class="tm-fumee f3" cx="580" cy="40" r="26"/></g>'
    + '<g class="tm-tapis"><rect x="0" y="292" width="34" height="34" rx="4" fill="#c3992a"/><rect x="60" y="292" width="34" height="34" rx="4" fill="#009081"/><rect x="120" y="292" width="34" height="34" rx="4" fill="#3a3ad8"/><rect x="180" y="292" width="34" height="34" rx="4" fill="#c3992a"/><rect x="240" y="292" width="34" height="34" rx="4" fill="#009081"/></g>'
    + '<rect x="450" y="80" width="40" height="120" fill="#3a3a3a"/><rect x="560" y="50" width="40" height="150" fill="#3a3a3a"/>'
    + '<path d="M300,330 L300,200 L380,150 L380,200 L460,150 L460,200 L540,150 L540,200 L640,200 L640,330 Z" fill="#000091"/>'
    + '<rect x="330" y="230" width="60" height="44" rx="4" fill="#f5f5fe"/><rect x="420" y="230" width="60" height="44" rx="4" fill="#f5f5fe"/><rect x="510" y="230" width="60" height="44" rx="4" fill="#f5f5fe"/>'
    + '<g transform="translate(355,252)"><path class="tm-roue" d="' + ENGRENAGE + '" fill="#6a6af4" transform="scale(.55)"/></g>'
    + '<g transform="translate(445,252)"><path class="tm-roue inverse" d="' + ENGRENAGE + '" fill="#e1000f" transform="scale(.55)"/></g>'
    + '<g transform="translate(535,252)"><path class="tm-roue" d="' + ENGRENAGE + '" fill="#6a6af4" transform="scale(.55)"/></g>'
    + '<rect x="20" y="330" width="760" height="16" rx="8" fill="#929292"/>'
    + '<g transform="translate(700,290)"><rect x="-38" y="-30" width="76" height="40" rx="8" fill="#18753c"/><text x="0" y="-3" text-anchor="middle" font-size="20" font-weight="700" fill="#fff">modèle</text></g>'
    + '</svg>';

  /** Prépare les éléments d'une racine (deck chargé ou rechargé). */
  function preparer(racine) {
    racine.querySelectorAll('[data-amphi="teachable"]').forEach(el => {
      arreter(el);
      el.classList.add('teachable');
      el.classList.toggle('tm-simulation', simulation(el));
      el.classList.remove('tm-en-usine', 'tm-teste');
      if (!actif()) {
        el.innerHTML = '<div class="tm-camera tm-absent">' + ICONE + '<span>' + (simulation(el) ? 'Entraînement en direct' : 'Démonstration en direct') + '</span></div><ol class="tm-classes"></ol>';
        return;
      }
      el.innerHTML = simulation(el)
        ? '<div class="tm-camera"><div class="tm-attente">' + ICONE + '<span>La caméra s’allume à l’affichage de la diapositive</span></div></div>'
          + '<div class="tm-panneau"><div class="tm-collecte"></div><button type="button" class="tm-entrainer" disabled>Entraîner le modèle</button></div>'
          + '<div class="tm-usine" hidden>' + USINE + '<p class="tm-etape"></p><div class="tm-progression"><span></span></div></div>'
          + '<ol class="tm-classes" hidden></ol>'
        : '<div class="tm-camera"><div class="tm-attente">' + ICONE + '<span>La caméra s’allume à l’affichage de la diapositive</span></div></div><ol class="tm-classes"></ol>';
    });
  }

  function surDiapositive(section) {
    if (!actif()) return;
    section.querySelectorAll('[data-amphi="teachable"]').forEach(demarrer);
  }

  function horsDiapositive(section) {
    section.querySelectorAll('[data-amphi="teachable"]').forEach(arreter);
  }

  function message(el, texte) {
    const zone = el.querySelector('.tm-camera');
    if (zone) zone.innerHTML = '<div class="tm-attente">' + ICONE + '<span></span></div>';
    const span = zone && zone.querySelector('span');
    if (span) span.textContent = texte;
  }

  async function demarrer(el) {
    if (el._tm) return;
    const etat = el._tm = { arret: false, raf: 0, webcam: null, minuteries: [] };
    try {
      message(el, 'Chargement du modèle…');
      await chargerScripts();
      const base = (el.dataset.src || '').replace(/\/?$/, '/');
      if (!el._modele) el._modele = await window.tmImage.load(base + 'model.json', base + 'metadata.json');
      if (etat.arret) return;
      message(el, 'Ouverture de la caméra…');
      const webcam = new window.tmImage.Webcam(400, 400, true); // carré, retourné comme un miroir
      await webcam.setup();
      if (etat.arret) { webcam.stop(); return; }
      await webcam.play();
      etat.webcam = webcam;
      const zone = el.querySelector('.tm-camera');
      zone.innerHTML = '';
      zone.appendChild(webcam.canvas);
      if (simulation(el)) collecte(el, etat);
      else predire(el, etat);
    } catch (e) {
      console.warn('teachable :', e);
      if (!etat.arret) message(el, 'Caméra ou modèle indisponible : ' + (e && e.message ? e.message : e));
      el._tm = null;
    }
  }

  /** Mode normal (et dernière étape de la simulation) : une barre de probabilité par classe, en continu. */
  function predire(el, etat) {
    const webcam = etat.webcam;
    const liste = el.querySelector('.tm-classes');
    liste.hidden = false;
    liste.innerHTML = '';
    const barres = el._modele.getClassLabels().map(nom => {
      const li = document.createElement('li');
      li.innerHTML = '<span class="tm-nom"></span><span class="tm-barre"><span></span></span><span class="tm-pct">0 %</span>';
      li.querySelector('.tm-nom').textContent = nom;
      liste.appendChild(li);
      return li;
    });
    let derniere = 0;
    const boucle = async t => {
      if (etat.arret) return;
      webcam.update();
      if (t - derniere >= PERIODE_MS) {
        derniere = t;
        const predictions = await el._modele.predict(webcam.canvas);
        if (etat.arret) return;
        const meilleure = predictions.reduce((m, p, i) => (p.probability > predictions[m].probability ? i : m), 0);
        predictions.forEach((p, i) => {
          const pct = Math.round(p.probability * 100);
          barres[i].querySelector('.tm-barre > span').style.width = pct + '%';
          barres[i].querySelector('.tm-pct').textContent = pct + ' %';
          barres[i].classList.toggle('tm-meilleure', i === meilleure);
        });
      }
      etat.raf = requestAnimationFrame(boucle);
    };
    etat.raf = requestAnimationFrame(boucle);
  }

  /** Simulation, étape 1 : boutons de collecte (300 images en 5 s par classe), mosaïque des images prises. */
  function collecte(el, etat) {
    const webcam = etat.webcam;
    const noms = el._modele.getClassLabels();
    const zone = el.querySelector('.tm-collecte');
    const entrainer = el.querySelector('.tm-entrainer');
    zone.innerHTML = '';
    let enCours = null; // { mosaique, ctx, compteur, debut, prises }
    const faites = new Set();
    const lignes = noms.map((nom, i) => {
      const ligne = document.createElement('div');
      ligne.className = 'tm-ligne';
      const vide = /^(vide|rien|fond|aucun)/i.test(nom);
      ligne.innerHTML = '<button type="button" class="tm-prendre"></button><div class="tm-images"><canvas width="600" height="90"></canvas><span class="tm-compte">0 / ' + IMAGES_PAR_CLASSE + ' images</span></div>';
      ligne.querySelector('.tm-prendre').textContent = (vide ? 'Vide' : 'Objet ' + (i + 1) + ' · ' + nom);
      ligne.querySelector('.tm-prendre').addEventListener('click', e => {
        e.currentTarget.blur(); // la barre d'espace doit rester à reveal.js
        if (enCours || etat.arret) return;
        const mosaique = ligne.querySelector('canvas');
        const ctx = mosaique.getContext('2d');
        ctx.clearRect(0, 0, mosaique.width, mosaique.height);
        ligne.classList.add('tm-en-cours');
        enCours = { i, ligne, ctx, compteur: ligne.querySelector('.tm-compte'), debut: performance.now(), prises: 0 };
      });
      zone.appendChild(ligne);
      return ligne;
    });
    entrainer.disabled = true;
    entrainer.onclick = e => { e.currentTarget.blur(); if (!enCours && faites.size === noms.length) entrainement(el, etat); };

    // Mosaïque : 30 colonnes × 10 lignes de vignettes 20 × 9 px (300 images), remplie au fil de la prise.
    const COLS = 30, LV = 20, HV = 9;
    const boucle = t => {
      if (etat.arret) return;
      webcam.update();
      if (enCours) {
        const voulu = Math.min(IMAGES_PAR_CLASSE, Math.floor((t - enCours.debut) / DUREE_COLLECTE_MS * IMAGES_PAR_CLASSE));
        while (enCours.prises < voulu) {
          const k = enCours.prises++;
          enCours.ctx.drawImage(webcam.canvas, 50, 120, 300, 135, (k % COLS) * LV, Math.floor(k / COLS) * HV, LV - 1, HV - 1);
        }
        enCours.compteur.textContent = enCours.prises + ' / ' + IMAGES_PAR_CLASSE + ' images';
        if (enCours.prises >= IMAGES_PAR_CLASSE) {
          enCours.ligne.classList.remove('tm-en-cours');
          enCours.ligne.classList.add('tm-fait');
          faites.add(enCours.i);
          enCours = null;
          entrainer.disabled = faites.size < noms.length;
        }
      }
      etat.raf = requestAnimationFrame(boucle);
    };
    etat.raf = requestAnimationFrame(boucle);
    etat.lignes = lignes;
  }

  /** Simulation, étape 2 : l'usine tourne pendant DUREE_ENTRAINEMENT_MS, puis étape 3 (prédiction en direct). */
  function entrainement(el, etat) {
    cancelAnimationFrame(etat.raf);
    const usine = el.querySelector('.tm-usine');
    const etape = usine.querySelector('.tm-etape');
    const barre = usine.querySelector('.tm-progression > span');
    el.classList.add('tm-en-usine');
    usine.hidden = false;
    // Chauffe : la première prédiction compile les shaders WebGL (plusieurs secondes sur un petit PC) ; autant la faire pendant l'animation.
    el._modele.predict(etat.webcam.canvas).catch(() => {});
    const total = el._modele.getClassLabels().length * IMAGES_PAR_CLASSE;
    const debut = performance.now();
    const boucle = t => {
      if (etat.arret) return;
      const avance = Math.min(1, (t - debut) / DUREE_ENTRAINEMENT_MS);
      barre.style.width = Math.round(avance * 100) + '%';
      const n = Math.min(ETAPES_ENTRAINEMENT.length - 1, Math.floor(avance * (ETAPES_ENTRAINEMENT.length - 1)));
      etape.textContent = ETAPES_ENTRAINEMENT[n] + (avance < 1 ? '  ·  ' + total + ' images, passe ' + Math.max(1, Math.ceil(avance * 50)) + ' / 50' : '');
      if (avance < 1) { etat.raf = requestAnimationFrame(boucle); return; }
      etat.minuteries.push(setTimeout(() => {
        if (etat.arret) return;
        usine.hidden = true;
        el.classList.remove('tm-en-usine');
        el.classList.add('tm-teste');
        predire(el, etat);
      }, 900));
    };
    etat.raf = requestAnimationFrame(boucle);
  }

  function arreter(el) {
    const etat = el._tm;
    if (!etat) return;
    etat.arret = true;
    cancelAnimationFrame(etat.raf);
    etat.minuteries.forEach(clearTimeout);
    if (etat.webcam) etat.webcam.stop();
    el._tm = null;
    if (actif()) {
      if (simulation(el)) { preparer(el.parentNode); return; } // la simulation repart de zéro
      message(el, 'La caméra s’allume à l’affichage de la diapositive');
    }
  }

  return { preparer, surDiapositive, horsDiapositive };
})();

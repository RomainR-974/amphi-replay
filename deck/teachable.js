/*
 * Élément « teachable » : un modèle d'images Teachable Machine (export TensorFlow.js) dans une diapositive.
 *
 *   Mode simple :  <div class="el teachable" data-amphi="teachable" data-src="medias/tm-objets/" …></div>
 *                  → caméra + probabilité par classe dès l'affichage de la diapositive.
 *   Mode atelier : <div class="el teachable" data-amphi="teachable" data-scenario="atelier" data-classes="gourde,stylo,vide"
 *                       data-src="medias/tm-objets/" …></div>
 *                  + autant de déclencheurs que d'étapes (n classes + 2), fragments invisibles de la section :
 *                  <div class="el declencheur fragment" data-amphi="tm-etape" data-fragment-index="0" …></div>
 *                  → simule la démarche Teachable Machine, une étape par « Suivant » de la télécommande :
 *                    0 : caméra prête ; 1…n : 300 images de la classe i en 5 s (compteur, miniatures) ;
 *                    n + 1 : entraînement (usine : images sur un tapis roulant, engrenages, époques, précision) ;
 *                    n + 2 : test (caméra, probabilité par classe, avec le vrai modèle exporté).
 *                  L'étape est le nombre de déclencheurs visibles : projecteur, OBS, télécommande et replay restent
 *                  synchrones, y compris après un saut (le journal enregistre les fragments).
 *
 * data-src : dossier contenant model.json, metadata.json et weights.bin (fichiers de l'export « TensorFlow.js »).
 * Caméra : rôle scene (projecteur, PC 1) dès l'affichage de la diapositive ; rôle replay (replay interactif, SCORM)
 * seulement après un clic sur « Activer ma caméra » ; rôles obs et remote jamais (la caméra du PC 2 est prise par OBS) :
 * ils montrent les mêmes étapes avec des miniatures de couleur. Caméra libérée dès qu'on quitte la diapositive.
 * Bibliothèques chargées à la demande depuis deck/vendor/tm/ (tools/fetch_reveal.sh). Voir docs/format-deck.md.
 */
window.AmphiTeachable = (() => {
  const SCRIPTS = ['vendor/tm/tf.min.js', 'vendor/tm/teachablemachine-image.min.js'];
  const PERIODE_MS = 120;        // une prédiction toutes les 120 ms suffit et laisse respirer le projecteur
  const IMAGES = 300;            // images « enregistrées » par classe
  const CAPTURE_MS = 5000;       // … en 5 s
  const ENTRAINEMENT_MS = 6500;  // durée de l'animation d'entraînement
  const EPOQUES = 50;
  const MINIATURES = 24;         // miniatures montrées par classe
  const TEINTES = [214, 28, 145, 330, 268, 190];
  const role = () => (window.AmphiSync && window.AmphiSync.role) || 'scene';
  const cameraPermise = el => role() === 'scene' || (role() === 'replay' && !!el.dataset.autorise);
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
  function chargerModele(el) {
    if (!el._modele) {
      const base = (el.dataset.src || '').replace(/\/?$/, '/');
      el._modele = chargerScripts().then(() => window.tmImage.load(base + 'model.json', base + 'metadata.json'));
      el._modele.catch(() => { el._modele = null; });
    }
    return el._modele;
  }

  const ICONE = '<svg class="tm-icone" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7h3l2-3h8l2 3h3v13H3z"/><circle cx="12" cy="13" r="4"/></svg>';
  const atelier = el => el.dataset.scenario === 'atelier';
  const classes = el => (el.dataset.classes || '').split(',').map(s => s.trim()).filter(Boolean);
  const etapeDe = section => section ? section.querySelectorAll('[data-amphi="tm-etape"].visible').length : 0;
  const echap = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ===============================================================================================================
  // Préparation
  // ===============================================================================================================
  function preparer(racine) {
    racine.querySelectorAll('[data-amphi="teachable"]').forEach(el => {
      arreter(el);
      el.classList.add('teachable');
      construire(el);
    });
  }
  function construire(el) {
    if (atelier(el)) construireAtelier(el); else el.innerHTML = '<div class="tm-camera">' + zoneAttente(el) + '</div><ol class="tm-classes"></ol>';
    const bouton = el.querySelector('.tm-activer');
    if (bouton) bouton.addEventListener('click', () => { el.dataset.autorise = '1'; demarrerCamera(el).then(() => { if (atelier(el) && el.dataset.phase === 'test') activerPredictions(el); }); });
  }

  function zoneAttente(el) {
    if (role() === 'replay' && !el.dataset.autorise) {
      return '<div class="tm-attente">' + ICONE + '<span>À vous d’essayer avec votre caméra</span><button type="button" class="tm-activer">Activer ma caméra</button></div>';
    }
    if (role() === 'scene' || role() === 'replay') return '<div class="tm-attente">' + ICONE + '<span>La caméra s’allume…</span></div>';
    return '<div class="tm-attente tm-absent">' + ICONE + '<span>Caméra du projecteur</span></div>';
  }

  function construireAtelier(el) {
    const noms = classes(el);
    el.innerHTML =
      '<ol class="tm-phases"><li data-phase="donnees">1 · Données</li><li data-phase="entrainement">2 · Entraînement</li><li data-phase="test">3 · Test</li></ol>' +
      '<div class="tm-scene"><div class="tm-camera">' + zoneAttente(el) + '</div><div class="tm-rec" hidden>● REC</div>' + usine() + '</div>' +
      '<div class="tm-consigne"></div>' +
      '<div class="tm-bacs">' + noms.map((n, i) =>
        `<div class="tm-bac" data-i="${i}" style="--teinte:${TEINTES[i % TEINTES.length]}"><div class="tm-bac-tete"><b>${echap(n)}</b><span class="tm-compte">0 / ${IMAGES} images</span></div>` +
        '<div class="tm-jauge"><span></span></div><div class="tm-vignettes"></div></div>').join('') + '</div>' +
      '<ol class="tm-classes" hidden></ol>';
    el._atelier = { etape: -1, timers: [] };
  }

  // ----- Usine d'entraînement (SVG) ------------------------------------------------------------------------------
  function engrenage(cx, cy, r, dents) {
    const pts = [], int = r * 0.78;
    for (let i = 0; i < dents * 2; i++) {
      const a0 = (Math.PI * i) / dents, a1 = (Math.PI * (i + 1)) / dents, rr = i % 2 ? int : r;
      pts.push([cx + rr * Math.cos(a0 + 0.08), cy + rr * Math.sin(a0 + 0.08)], [cx + rr * Math.cos(a1 - 0.08), cy + rr * Math.sin(a1 - 0.08)]);
    }
    const t = r * 0.32;
    return 'M' + pts.map(p => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('L') + 'Z' +
      `M${(cx + t).toFixed(1)} ${cy}A${t.toFixed(1)} ${t.toFixed(1)} 0 1 0 ${(cx - t).toFixed(1)} ${cy}A${t.toFixed(1)} ${t.toFixed(1)} 0 1 0 ${(cx + t).toFixed(1)} ${cy}Z`;
  }
  function usine() {
    const tuiles = Array.from({ length: 7 }, (_, i) =>
      `<rect class="tm-tuile" x="-56" y="236" width="44" height="44" rx="6" style="--i:${i};--teinte:${TEINTES[i % 3]}"/>`).join('');
    const fumees = [0, 1, 2].map(i => `<circle class="tm-fumee" cx="488" cy="66" r="14" style="--i:${i}"/>`).join('');
    return '<div class="tm-usine" hidden><svg viewBox="0 0 580 420" aria-hidden="true">' +
      fumees +
      '<rect x="474" y="66" width="28" height="94" fill="#1b1b35"/>' +
      '<path d="M290 360 V180 l45 -40 v40 l45 -40 v40 l45 -40 v40 l45 -40 v220 Z" fill="#000091"/>' +
      '<rect x="290" y="226" width="34" height="64" fill="#1b1b35"/>' +
      '<rect x="0" y="282" width="322" height="16" rx="8" fill="#3a3a3a"/>' +
      [20, 80, 140, 200, 260].map(x => `<circle class="tm-rouleau" cx="${x}" cy="290" r="5" fill="#9a9a9a"/>`).join('') +
      '<g class="tm-tapis">' + tuiles + '</g>' +
      `<path class="tm-roue tm-roue-a" d="${engrenage(402, 250, 46, 10)}" fill="#ffffff" fill-rule="evenodd"/>` +
      `<path class="tm-roue tm-roue-b" d="${engrenage(472, 302, 30, 8)}" fill="#e3e3fd" fill-rule="evenodd"/>` +
      `<path class="tm-roue tm-roue-c" d="${engrenage(462, 202, 22, 7)}" fill="#cacafb" fill-rule="evenodd"/>` +
      '<text x="380" y="340" text-anchor="middle" class="tm-usine-texte">MODÈLE</text>' +
      '</svg><div class="tm-progression"><div class="tm-jauge"><span></span></div><div class="tm-info">Entraînement…</div></div>' +
      '<div class="tm-pret" hidden>✔ Modèle prêt</div></div>';
  }

  // ===============================================================================================================
  // Vie de la diapositive
  // ===============================================================================================================
  function surDiapositive(section) {
    section.querySelectorAll('[data-amphi="teachable"]').forEach(el => {
      if (atelier(el)) {
        el._atelier.etape = -1;
        appliquerEtape(el, etapeDe(section), false);
        if (role() === 'scene' || role() === 'replay') chargerModele(el).catch(() => {}); // préchargé pour l'étape de test
      }
      if (cameraPermise(el)) demarrerCamera(el).then(() => { if (atelier(el) && el.dataset.phase === 'test') activerPredictions(el); });
    });
  }
  /** fragmentshown / fragmenthidden : l'atelier suit les déclencheurs visibles de la section. */
  function surFragment(fragment) {
    const section = fragment && fragment.closest ? fragment.closest('section') : null;
    if (!section) return;
    section.querySelectorAll('[data-amphi="teachable"]').forEach(el => { if (atelier(el)) appliquerEtape(el, etapeDe(section), true); });
  }
  function horsDiapositive(section) {
    section.querySelectorAll('[data-amphi="teachable"]').forEach(el => { arreter(el); construire(el); });
  }

  // ===============================================================================================================
  // Caméra et prédictions
  // ===============================================================================================================
  function message(el, texte) {
    const zone = el.querySelector('.tm-camera');
    if (!zone) return;
    zone.innerHTML = '<div class="tm-attente">' + ICONE + '<span></span></div>';
    zone.querySelector('span').textContent = texte;
  }

  async function demarrerCamera(el) {
    if (el._tm) return;
    const etat = el._tm = { arret: false, raf: 0, webcam: null, barres: null, predire: false };
    try {
      if (!atelier(el)) message(el, 'Chargement du modèle…');
      await chargerScripts();
      if (!atelier(el)) await chargerModele(el);
      if (etat.arret) return;
      const webcam = new window.tmImage.Webcam(400, 400, true); // carré, retourné comme un miroir
      await webcam.setup();
      if (etat.arret) { webcam.stop(); return; }
      await webcam.play();
      etat.webcam = webcam;
      const zone = el.querySelector('.tm-camera');
      zone.innerHTML = '';
      zone.appendChild(webcam.canvas);
      if (!atelier(el)) await activerPredictions(el);
      let derniere = 0;
      const boucle = async t => {
        if (etat.arret) return;
        webcam.update();
        if (etat.predire && t - derniere >= PERIODE_MS) {
          derniere = t;
          await predire(el);
        }
        etat.raf = requestAnimationFrame(boucle);
      };
      etat.raf = requestAnimationFrame(boucle);
    } catch (e) {
      console.warn('teachable :', e);
      if (!etat.arret) message(el, 'Caméra ou modèle indisponible : ' + (e && e.message ? e.message : e));
      el._tm = null;
    }
  }

  async function activerPredictions(el) {
    const etat = el._tm;
    if (!etat || !etat.webcam) return;
    const modele = await chargerModele(el);
    if (etat.arret) return;
    const liste = el.querySelector('.tm-classes');
    liste.hidden = false;
    liste.innerHTML = '';
    etat.modele = modele;
    etat.barres = modele.getClassLabels().map(nom => {
      const li = document.createElement('li');
      li.innerHTML = '<span class="tm-nom"></span><span class="tm-barre"><span></span></span><span class="tm-pct">0 %</span>';
      li.querySelector('.tm-nom').textContent = nom;
      liste.appendChild(li);
      return li;
    });
    etat.predire = true;
  }

  async function predire(el) {
    const etat = el._tm;
    if (!etat || !etat.modele || !etat.webcam) return;
    const predictions = await etat.modele.predict(etat.webcam.canvas);
    if (etat.arret || !etat.predire) return;
    const meilleure = predictions.reduce((m, p, i) => (p.probability > predictions[m].probability ? i : m), 0);
    predictions.forEach((p, i) => {
      const pct = Math.round(p.probability * 100);
      etat.barres[i].querySelector('.tm-barre > span').style.width = pct + '%';
      etat.barres[i].querySelector('.tm-pct').textContent = pct + ' %';
      etat.barres[i].classList.toggle('tm-meilleure', i === meilleure);
    });
  }

  function arreter(el) {
    const a = el._atelier;
    if (a) { a.timers.forEach(t => cancelAnimationFrame(t)); a.timers = []; }
    const etat = el._tm;
    if (!etat) return;
    etat.arret = true;
    cancelAnimationFrame(etat.raf);
    if (etat.webcam) etat.webcam.stop();
    el._tm = null;
  }

  // ===============================================================================================================
  // Atelier : étapes
  // ===============================================================================================================
  function appliquerEtape(el, etape, animer) {
    const a = el._atelier;
    if (!a || etape === a.etape) return;
    a.timers.forEach(t => cancelAnimationFrame(t));
    a.timers = [];
    const noms = classes(el), n = noms.length;
    a.etape = etape;
    const phase = etape <= n ? 'donnees' : etape === n + 1 ? 'entrainement' : 'test';
    el.dataset.phase = phase;
    el.querySelectorAll('.tm-phases li').forEach(li => li.classList.toggle('actif', li.dataset.phase === phase));
    el.querySelector('.tm-usine').hidden = phase !== 'entrainement';
    el.querySelector('.tm-camera').hidden = phase === 'entrainement';
    el.querySelector('.tm-bacs').hidden = phase === 'test';
    el.querySelector('.tm-rec').hidden = true;
    if (el._tm) el._tm.predire = false;
    const liste = el.querySelector('.tm-classes');
    if (phase !== 'test') liste.hidden = true;

    // Bacs : classes déjà enregistrées pleines, classe en cours animée, suivantes vides.
    el.querySelectorAll('.tm-bac').forEach((bac, i) => {
      const enCours = phase === 'donnees' && i === etape - 1;
      bac.classList.toggle('actif', enCours);
      if (phase !== 'donnees' || i < etape - 1) remplirBac(el, bac, IMAGES);
      else if (!enCours) viderBac(bac);
    });
    const consigne = el.querySelector('.tm-consigne');
    if (phase === 'donnees' && etape === 0) consigne.textContent = 'Suivant : enregistrer « ' + noms[0] + ' »';
    else if (phase === 'donnees') {
      consigne.textContent = 'Enregistrement de « ' + noms[etape - 1] + ' »…';
      capturer(el, etape - 1, animer);
    } else if (phase === 'entrainement') {
      consigne.textContent = n * IMAGES + ' images · ' + n + ' classes';
      entrainer(el, animer);
    } else {
      consigne.textContent = 'Montrez un objet à la caméra';
      if (el._tm && el._tm.webcam) activerPredictions(el);
      else if (!cameraPermise(el)) {
        // OBS, télécommande, replay sans caméra : les classes du modèle, sans mesure.
        liste.hidden = false;
        liste.innerHTML = noms.map(nm => `<li><span class="tm-nom">${echap(nm)}</span><span class="tm-barre"><span></span></span><span class="tm-pct">—</span></li>`).join('');
      }
    }
  }

  function viderBac(bac) {
    bac.classList.remove('plein');
    bac.querySelector('.tm-compte').textContent = '0 / ' + IMAGES + ' images';
    bac.querySelector('.tm-jauge > span').style.width = '0%';
    bac.querySelector('.tm-vignettes').innerHTML = '';
  }
  function remplirBac(el, bac, total) {
    bac.classList.add('plein');
    bac.querySelector('.tm-compte').textContent = total + ' / ' + IMAGES + ' images';
    bac.querySelector('.tm-jauge > span').style.width = Math.round(100 * total / IMAGES) + '%';
    const zone = bac.querySelector('.tm-vignettes');
    while (zone.children.length < MINIATURES) zone.appendChild(vignette(el));
  }
  /** Miniature : image de la caméra s'il y en a une, sinon carré de couleur (OBS, télécommande, replay sans caméra). */
  function vignette(el) {
    const webcam = el._tm && el._tm.webcam;
    if (webcam) {
      const c = document.createElement('canvas');
      c.width = c.height = 48;
      c.className = 'tm-vignette';
      c.getContext('2d').drawImage(webcam.canvas, 0, 0, 48, 48);
      return c;
    }
    const d = document.createElement('span');
    d.className = 'tm-vignette tm-vignette-couleur';
    d.style.setProperty('--nuance', String(Math.round(Math.random() * 30 - 15)));
    return d;
  }
  function capturer(el, i, animer) {
    const bac = el.querySelector('.tm-bac[data-i="' + i + '"]');
    viderBac(bac);
    const rec = el.querySelector('.tm-rec');
    const fin = () => {
      bac.classList.add('plein');
      rec.hidden = true;
      const suivante = classes(el)[i + 1];
      el.querySelector('.tm-consigne').textContent = suivante ? 'Suivant : enregistrer « ' + suivante + ' »' : 'Suivant : entraîner le modèle';
    };
    if (!animer) { remplirBac(el, bac, IMAGES); fin(); return; }
    rec.hidden = false;
    const debut = performance.now(), zone = bac.querySelector('.tm-vignettes'), a = el._atelier;
    const pas = () => {
      const p = Math.min(1, (performance.now() - debut) / CAPTURE_MS);
      bac.querySelector('.tm-compte').textContent = Math.round(p * IMAGES) + ' / ' + IMAGES + ' images';
      bac.querySelector('.tm-jauge > span').style.width = (100 * p).toFixed(1) + '%';
      while (zone.children.length < Math.round(p * MINIATURES)) zone.appendChild(vignette(el));
      if (p < 1) a.timers.push(requestAnimationFrame(pas)); else fin();
    };
    a.timers.push(requestAnimationFrame(pas));
  }
  function entrainer(el, animer) {
    const u = el.querySelector('.tm-usine'), info = u.querySelector('.tm-info'), jauge = u.querySelector('.tm-jauge > span'), pret = u.querySelector('.tm-pret');
    const finir = () => {
      u.classList.remove('marche');
      jauge.style.width = '100%';
      pret.hidden = false;
      info.textContent = 'Époque ' + EPOQUES + ' / ' + EPOQUES + ' · précision 97 %';
      el.querySelector('.tm-consigne').textContent = 'Suivant : tester le modèle';
    };
    pret.hidden = true;
    if (!animer) { finir(); return; }
    u.classList.add('marche');
    const debut = performance.now(), a = el._atelier;
    const pas = () => {
      const p = Math.min(1, (performance.now() - debut) / ENTRAINEMENT_MS);
      const epoque = Math.max(1, Math.ceil(p * EPOQUES)), precision = Math.round(33 + 64 * (1 - Math.exp(-4 * p)));
      jauge.style.width = (100 * p).toFixed(1) + '%';
      info.textContent = 'Entraînement… époque ' + epoque + ' / ' + EPOQUES + ' · précision ' + precision + ' %';
      if (p < 1) a.timers.push(requestAnimationFrame(pas)); else finir();
    };
    a.timers.push(requestAnimationFrame(pas));
  }

  return { preparer, surDiapositive, horsDiapositive, surFragment };
})();

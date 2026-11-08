/*
 * Élément « teachable » : un modèle d'images Teachable Machine (export TensorFlow.js) en direct dans une diapositive.
 *
 *   <div class="el teachable" data-amphi="teachable" data-src="medias/tm-objets/" data-id="e7" style="…"></div>
 *
 * data-src : dossier contenant model.json, metadata.json et weights.bin (fichiers de l'export « TensorFlow.js »).
 *
 * Rôle scene (projecteur, PC 1) et replay : webcam du poste + barre de probabilité par classe, uniquement pendant
 * que la diapositive est affichée (caméra libérée dès qu'on la quitte). Bibliothèques chargées à la demande depuis
 * deck/vendor/tm/ (tools/fetch_reveal.sh) : aucune dépendance à un CDN pendant la séance.
 * Rôles obs et remote : encadré « démonstration en direct » — la caméra du PC 2 est déjà prise par OBS, et la
 * miniature de la télécommande n'a pas à ouvrir de caméra. Voir docs/format-deck.md.
 */
window.AmphiTeachable = (() => {
  const SCRIPTS = ['vendor/tm/tf.min.js', 'vendor/tm/teachablemachine-image.min.js'];
  const PERIODE_MS = 120; // une prédiction toutes les 120 ms suffit et laisse respirer le projecteur
  const role = () => (window.AmphiSync && window.AmphiSync.role) || 'scene';
  const actif = () => role() === 'scene' || role() === 'replay';
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

  /** Prépare les éléments d'une racine (deck chargé ou rechargé). */
  function preparer(racine) {
    racine.querySelectorAll('[data-amphi="teachable"]').forEach(el => {
      arreter(el);
      el.classList.add('teachable');
      el.innerHTML = actif()
        ? '<div class="tm-camera"><div class="tm-attente">' + ICONE + '<span>La caméra s’allume à l’affichage de la diapositive</span></div></div><ol class="tm-classes"></ol>'
        : '<div class="tm-camera tm-absent">' + ICONE + '<span>Démonstration en direct</span></div><ol class="tm-classes"></ol>';
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
    const etat = el._tm = { arret: false, raf: 0, webcam: null };
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
      const liste = el.querySelector('.tm-classes');
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
    } catch (e) {
      console.warn('teachable :', e);
      if (!etat.arret) message(el, 'Caméra ou modèle indisponible : ' + (e && e.message ? e.message : e));
      el._tm = null;
    }
  }

  function arreter(el) {
    const etat = el._tm;
    if (!etat) return;
    etat.arret = true;
    cancelAnimationFrame(etat.raf);
    if (etat.webcam) etat.webcam.stop();
    el._tm = null;
    if (actif()) message(el, 'La caméra s’allume à l’affichage de la diapositive');
  }

  return { preparer, surDiapositive, horsDiapositive };
})();

/*
 * Lecteur du replay interactif (docs/replay-interactif.md §4) : vidéo de l'intervenant + présentation redessinée.
 *
 * Données (donnees.js) : window.REPLAY_INTERACTIF = { identifiant, titre, duree, slides, questions, evaluation,
 *   timeline: [[t, h, v, f], …], chapitres: [{debut, titre, h}], cadrage: {pas, points: [[cx, cy, h], …]} | null, camera, seuil }
 *
 * - La diapositive affichée est celle du journal à l'instant de la vidéo (Reveal.slide), au fil de la lecture comme
 *   après un déplacement dans la vidéo.
 * - Le runtime du deck est réutilisé tel quel (quiz-deck.js, camera.js, teachable.js) avec le rôle « replay » :
 *   ses appels à l'API du serveur sont servis depuis les données embarquées ; résultats de la salle et QR de vote retirés.
 * - Arrêts : la vidéo s'arrête sur chaque question auto-évaluable (l'apprenant répond, la correction s'affiche) et propose
 *   « À vous d'essayer » sur les diapositives data-interactif.
 * - Cadrage : la vidéo est recadrée sur l'intervenant (vignette ronde en paysage, bandeau en portrait) d'après cadrage.points.
 * - SCORM 1.2 (scorm.js, repli localStorage hors LMS) : position, tranches de 10 s vues, réponses ; terminé quand 90 % de la
 *   vidéo a été vue et toutes les questions répondues → passed / failed (seuil) ou completed sans question.
 */
(function () {
  'use strict';
  var D = window.REPLAY_INTERACTIF;
  var $ = function (s) { return document.querySelector(s); };
  var lecteur = $('#lecteur'), video = $('#video'), cadre = $('#cadre-camera'), scene = $('#scene');
  var TRANCHE_S = 10, SEUIL_VU = 0.9, AVANCE_ARRET_S = 1.5;

  // ---------------------------------------------------------------------------------------------------------------
  // Le runtime du deck interroge l'API du serveur : réponses servies depuis les données embarquées (aucun réseau).
  // ---------------------------------------------------------------------------------------------------------------
  var fetchReseau = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function (url, options) {
    var u = String(url && url.url ? url.url : url);
    var json = function (objet) { return Promise.resolve(new Response(JSON.stringify(objet), { status: 200, headers: { 'Content-Type': 'application/json' } })); };
    if (u.indexOf('/api/quiz/questions') === 0) return json(D.questions || []);
    if (u.indexOf('/api/quiz/etat') === 0) return json({ url_publique: '' });
    if (u.indexOf('/api/presentations') === 0) return json({ camera: D.camera, theme: D.theme });
    return fetchReseau ? fetchReseau(url, options) : Promise.reject(new Error('fetch indisponible'));
  };

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function formater(s) {
    s = Math.max(0, Math.floor(s || 0));
    var hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
    return hh ? hh + ':' + pad(mm) + ':' + pad(ss) : pad(mm) + ':' + pad(ss);
  }
  function normaliser(texte) {
    return String(texte || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  }
  function h(tag, attrs) {
    var el = document.createElement(tag), a = attrs || {};
    Object.keys(a).forEach(function (k) {
      if (k === 'onclick' || k === 'onchange' || k === 'oninput') el[k] = a[k];
      else if (k === 'class') el.className = a[k];
      else if (a[k] !== null && a[k] !== undefined && a[k] !== false) el.setAttribute(k, a[k] === true ? '' : a[k]);
    });
    for (var i = 2; i < arguments.length; i++) {
      var e = arguments[i];
      (Array.isArray(e) ? e : [e]).forEach(function (x) { if (x !== null && x !== undefined && x !== false) el.appendChild(x instanceof Node ? x : document.createTextNode(String(x))); });
    }
    return el;
  }
  var duree = function () { return isFinite(video.duration) && video.duration > 0 ? video.duration : (D.duree || 0); };

  // ---------------------------------------------------------------------------------------------------------------
  // Présentation : slides embarquées, runtime du deck, résultats de la salle retirés
  // ---------------------------------------------------------------------------------------------------------------
  var slides = $('#slides');
  slides.innerHTML = D.slides || '';

  function retirerSalle() {
    // Résultats en direct, classement, nuages, QR de vote : propres à la séance, jamais rejoués (choix de l'auteur).
    // quiz-deck.js, en rôle replay, pose des emplacements [data-replay-salle] au lieu des iframes du serveur.
    slides.querySelectorAll('[data-replay-salle]').forEach(function (el) {
      var section = el.closest('section');
      var q = section && section.dataset.quiz && D.evaluation && D.evaluation[section.dataset.quiz];
      el.classList.add('replay-sans-salle');
      el.textContent = el.dataset.replaySalle === 'classement' ? 'Classement de la salle pendant la séance.'
        : q ? 'À vous de répondre : la vidéo s’arrête sur cette question.' : 'Question posée à la salle pendant la séance.';
    });
    slides.querySelectorAll('.qr-libre, [data-amphi="qr"], [data-amphi="qr-fixe"]').forEach(function (el) { el.remove(); });
    // Légende « Scannez pour voter / participer » des diapositives de vote (le QR LinkedIn du contact reste).
    slides.querySelectorAll('section[data-quiz] .dsfr-legende-qr, section[data-qr] .dsfr-legende-qr').forEach(function (el) { (el.closest('.el') || el).remove(); });
  }

  // ---------------------------------------------------------------------------------------------------------------
  // Chronologie : diapositive (h, v, f) à l'instant t de la vidéo
  // ---------------------------------------------------------------------------------------------------------------
  var tl = (D.timeline && D.timeline.length) ? D.timeline : [[0, 0, 0, -1]];
  function indiceA(t) {
    var bas = 0, haut = tl.length - 1, r = 0;
    while (bas <= haut) {
      var m = (bas + haut) >> 1;
      if (tl[m][0] <= t + 0.001) { r = m; bas = m + 1; } else haut = m - 1;
    }
    return r;
  }
  var cleCourante = null;
  function synchroniser() {
    var e = tl[indiceA(video.currentTime)];
    var cle = e[1] + ':' + e[2] + ':' + e[3];
    if (cle === cleCourante) return;
    cleCourante = cle;
    window.Reveal.slide(e[1], e[2] || 0, e[3] === null || e[3] === undefined ? -1 : e[3]);
  }

  // ---------------------------------------------------------------------------------------------------------------
  // Cadrage : la vidéo recadrée sur l'intervenant, dans la vignette ou le bandeau
  // ---------------------------------------------------------------------------------------------------------------
  function pointA(t) {
    var c = D.cadrage;
    if (!c || !c.points || !c.points.length) return [0.5, 0.42, 0.22];
    var p = c.points, i = Math.max(0, t / c.pas), a = Math.min(p.length - 1, Math.floor(i)), b = Math.min(p.length - 1, a + 1), k = Math.min(1, i - a);
    return [0, 1, 2].map(function (j) { return p[a][j] + (p[b][j] - p[a][j]) * k; });
  }
  function cadrer() {
    var cw = cadre.clientWidth, ch = cadre.clientHeight, vw = video.videoWidth || 1920, vh = video.videoHeight || 1080;
    if (!cw || !ch) return;
    var p = pointA(video.currentTime), portrait = lecteur.classList.contains('portrait');
    var couvre = Math.max(cw / vw, ch / vh);
    // Le visage occupe ≈ 40 % de la hauteur de la vignette (tête et épaules), ≈ 30 % du bandeau portrait ; zoom ≤ 3×.
    var cible = (portrait ? 0.30 : 0.40) * ch / Math.max(0.04, p[2] * vh);
    var s = Math.max(couvre, Math.min(cible, couvre * 3));
    var w = vw * s, hh = vh * s;
    var x = Math.min(0, Math.max(cw - w, cw / 2 - p[0] * w));
    var y = Math.min(0, Math.max(ch - hh, ch * 0.46 - p[1] * hh));
    video.style.width = w + 'px';
    video.style.height = hh + 'px';
    video.style.transform = 'translate(' + x.toFixed(1) + 'px,' + y.toFixed(1) + 'px)';
  }

  // ---------------------------------------------------------------------------------------------------------------
  // Disposition : paysage (vignette ronde sur la zone caméra) ou portrait (intervenant en haut)
  // ---------------------------------------------------------------------------------------------------------------
  var zone = (window.AmphiCamera && D.camera !== null) ? window.AmphiCamera.zoneCamera(D.camera) : null;
  var boiteVignette = (function () {
    var c = zone ? zone.camera : { largeur: 300, hauteur: 300 }, droite = zone ? zone.droite : true, bas = zone ? zone.bas : true;
    var cote = Math.min(c.largeur, c.hauteur);
    return { x: droite ? 1920 - cote - 24 : 24, y: bas ? 1080 - cote - 24 : 24, cote: cote };
  })();
  function disposer() {
    var r = lecteur.getBoundingClientRect();
    var portrait = r.width / Math.max(1, r.height) < 1.05;
    lecteur.classList.toggle('portrait', portrait);
    lecteur.classList.toggle('paysage', !portrait);
    if (window.Reveal && window.Reveal.isReady()) window.Reveal.layout();
    if (!portrait) {
      var s = slides.getBoundingClientRect(), echelle = s.width / 1920;
      cadre.style.left = (s.left - r.left + boiteVignette.x * echelle) + 'px';
      cadre.style.top = (s.top - r.top + boiteVignette.y * echelle) + 'px';
      cadre.style.width = cadre.style.height = Math.max(90, boiteVignette.cote * echelle) + 'px';
    } else {
      cadre.style.left = cadre.style.top = cadre.style.width = cadre.style.height = '';
    }
    cadrer();
  }

  // ---------------------------------------------------------------------------------------------------------------
  // Arrêts : questions auto-évaluables et diapositives interactives (première apparition de chaque diapositive)
  // ---------------------------------------------------------------------------------------------------------------
  var arrets = [];
  function calculerArrets() {
    var vus = {};
    tl.forEach(function (e) {
      var cle = e[1] + ':' + (e[2] || 0);
      if (vus[cle]) return;
      vus[cle] = true;
      var section = window.Reveal.getSlide(e[1], e[2] || 0);
      if (!section) return;
      var t = Math.min(e[0] + AVANCE_ARRET_S, Math.max(0, duree() - 0.5));
      var q = section.dataset.quiz && D.evaluation ? D.evaluation[section.dataset.quiz] : null;
      if (q && !arrets.some(function (a) { return a.question && a.question.id === q.id; })) arrets.push({ id: 'q:' + q.id, t: t, question: q, titre: D.titres[e[1]] || '' });
      else if (section.hasAttribute('data-interactif')) arrets.push({ id: 'i:' + cle, t: t, interactif: true });
    });
    arrets.sort(function (a, b) { return a.t - b.t; });
  }

  // ---------------------------------------------------------------------------------------------------------------
  // État, SCORM
  // ---------------------------------------------------------------------------------------------------------------
  var etat = { vues: [], reponses: {}, faits: {} };
  var termine = false;
  function nbTranches() { return Math.max(1, Math.ceil(duree() / TRANCHE_S)); }
  function questions() { return arrets.filter(function (a) { return a.question; }); }
  function score() {
    var qs = questions();
    if (!qs.length) return null;
    var bonnes = qs.filter(function (a) { return etat.reponses[a.question.id] && etat.reponses[a.question.id].ok; }).length;
    return Math.round(100 * bonnes / qs.length);
  }
  function ratioVu() { var n = nbTranches(), v = 0; for (var i = 0; i < n; i++) if (etat.vues[i]) v++; return v / n; }
  function restantes() { return questions().filter(function (a) { return !etat.reponses[a.question.id]; }); }
  function fini() { return ratioVu() >= SEUIL_VU && !restantes().length; }

  function bitsVersTexte() {
    var n = nbTranches(), s = '';
    for (var i = 0; i < n; i += 6) {
      var v = 0;
      for (var j = 0; j < 6; j++) if (etat.vues[i + j]) v |= 1 << j;
      s += 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'.charAt(v);
    }
    return s;
  }
  function texteVersBits(s) {
    var alpha = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
    for (var i = 0; i < (s || '').length; i++) {
      var v = alpha.indexOf(s.charAt(i));
      for (var j = 0; j < 6; j++) if (v >= 0 && (v >> j) & 1) etat.vues[i * 6 + j] = true;
    }
  }
  function serialiser() {
    var r = {};
    Object.keys(etat.reponses).forEach(function (id) { r[id] = [etat.reponses[id].r, etat.reponses[id].ok ? 1 : 0]; });
    var d = { p: Math.floor(video.currentTime || 0), v: bitsVersTexte(), r: r, a: Object.keys(etat.faits) };
    var texte = JSON.stringify(d);
    if (texte.length > 4000) { Object.keys(r).forEach(function (id) { r[id] = [null, r[id][1]]; }); d.a = []; texte = JSON.stringify(d); } // limite SCORM 1.2
    return texte;
  }
  function restaurer() {
    var brut = window.SCORM.lire('cmi.suspend_data'), pos = 0;
    if (brut) {
      try {
        var d = JSON.parse(brut);
        texteVersBits(d.v);
        Object.keys(d.r || {}).forEach(function (id) { etat.reponses[id] = { r: d.r[id][0], ok: d.r[id][1] === 1 }; });
        (d.a || []).forEach(function (id) { etat.faits[id] = true; });
        pos = d.p || 0;
      } catch (e) { /* illisible : on repart de zéro */ }
    }
    var lieu = parseInt(window.SCORM.lire('cmi.core.lesson_location'), 10);
    if (lieu > 0) pos = lieu;
    termine = /^(passed|completed|failed)$/.test(window.SCORM.lire('cmi.core.lesson_status'));
    return pos;
  }
  function sauvegarder() {
    window.SCORM.ecrire('cmi.suspend_data', serialiser());
    window.SCORM.ecrire('cmi.core.lesson_location', String(Math.floor(video.currentTime || 0)));
    var s = score();
    if (s !== null && Object.keys(etat.reponses).length) {
      window.SCORM.ecrire('cmi.core.score.min', '0');
      window.SCORM.ecrire('cmi.core.score.max', '100');
      window.SCORM.ecrire('cmi.core.score.raw', String(s));
    }
    if (fini()) {
      window.SCORM.ecrire('cmi.core.lesson_status', s === null ? 'completed' : (s >= (D.seuil || 50) ? 'passed' : 'failed'));
      window.SCORM.ecrire('cmi.core.exit', '');
      if (!termine) { termine = true; montrerFin(); }
    } else {
      if (!termine) window.SCORM.ecrire('cmi.core.lesson_status', 'incomplete');
      window.SCORM.ecrire('cmi.core.exit', 'suspend');
    }
    window.SCORM.valider();
    rendreTaches();
  }

  // ---------------------------------------------------------------------------------------------------------------
  // Fenêtres : question, tâches restantes, fin
  // ---------------------------------------------------------------------------------------------------------------
  var fenetre = $('#fenetre'), contenu = $('#fenetre-contenu');
  var repriseApres = false;
  function ouvrir(elements, reprendre) {
    repriseApres = !!reprendre;
    contenu.innerHTML = '';
    elements.forEach(function (e) { if (e) contenu.appendChild(e); });
    fenetre.hidden = false;
    var premier = contenu.querySelector('input, button');
    if (premier) premier.focus();
  }
  function fermer() {
    fenetre.hidden = true;
    if (repriseApres) video.play().catch(function () {});
  }

  function texteBonne(q) {
    if (q.type === 'qcm') return q.options[q.bonne];
    if (q.type === 'multi') return q.bonnes.map(function (i) { return q.options[i]; }).join(', ');
    if (q.type === 'vrai_faux') return q.bonne ? 'Vrai' : 'Faux';
    if (q.type === 'trou') return q.bonnes[0];
    if (q.type === 'nombre') return String(q.bonne) + (q.unite ? ' ' + q.unite : '') + ' (±5 %)';
    return '';
  }
  function corriger(q, r) {
    if (q.type === 'qcm' || q.type === 'vrai_faux') return r === q.bonne;
    if (q.type === 'multi') { var a = (r || []).slice().sort(function (x, y) { return x - y; }); return a.length === q.bonnes.length && a.every(function (v, i) { return v === q.bonnes[i]; }); }
    if (q.type === 'trou') { var n = normaliser(r); return !!n && q.bonnes.some(function (b) { return normaliser(b) === n; }); }
    if (q.type === 'nombre') { if (typeof r !== 'number' || isNaN(r)) return false; return q.bonne === 0 ? r === 0 : Math.abs(r - q.bonne) <= 0.05 * Math.abs(q.bonne); }
    return false;
  }

  function fenetreQuestion(arret, reprendre) {
    var q = arret.question, deja = etat.reponses[q.id];
    var choix = [], champ = null, valider, retour = h('div', { class: 'retour', hidden: true });
    var options = h('div', { class: 'options' });
    var nom = 'q-' + q.id;
    if (q.type === 'qcm' || q.type === 'multi' || q.type === 'vrai_faux') {
      var libelles = q.type === 'vrai_faux' ? ['Vrai', 'Faux'] : q.options;
      libelles.forEach(function (texte, i) {
        var valeur = q.type === 'vrai_faux' ? (i === 0) : i;
        var entree = h('input', { type: q.type === 'multi' ? 'checkbox' : 'radio', name: nom, value: String(i) });
        entree.onchange = function () { valider.disabled = !choix.some(function (c) { return c.entree.checked; }); };
        var label = h('label', {}, entree, h('span', {}, texte));
        choix.push({ entree: entree, label: label, valeur: valeur });
        options.appendChild(label);
      });
    } else {
      champ = h('input', { type: q.type === 'nombre' ? 'number' : 'text', step: 'any', 'aria-label': 'Votre réponse', placeholder: q.type === 'nombre' ? 'Votre estimation' + (q.unite ? ' (' + q.unite + ')' : '') : 'Votre réponse' });
      champ.oninput = function () { valider.disabled = !champ.value.trim(); };
    }
    function reponse() {
      if (champ) return q.type === 'nombre' ? parseFloat(champ.value.replace(',', '.')) : champ.value;
      var coches = choix.filter(function (c) { return c.entree.checked; }).map(function (c) { return c.valeur; });
      return q.type === 'multi' ? coches : coches[0];
    }
    function afficherCorrection(ok) {
      retour.hidden = false;
      retour.className = 'retour ' + (ok ? 'ok' : 'ko');
      retour.textContent = ok ? 'Bonne réponse !' : 'Pas tout à fait. Réponse attendue : ' + texteBonne(q);
      choix.forEach(function (c) {
        c.entree.disabled = true;
        var bonne = q.type === 'multi' ? q.bonnes.indexOf(c.valeur) >= 0 : c.valeur === q.bonne;
        if (bonne) c.label.classList.add('bonne');
        else if (c.entree.checked) c.label.classList.add('fausse');
      });
      if (champ) champ.disabled = true;
      valider.hidden = true;
      continuer.hidden = false;
      continuer.focus();
    }
    valider = h('button', { type: 'button', class: 'principal', disabled: true }, 'Valider');
    var continuer = h('button', { type: 'button', class: 'principal', hidden: true }, reprendre ? 'Continuer la vidéo' : 'Fermer');
    var passer = h('button', { type: 'button' }, 'Plus tard');
    valider.onclick = function () {
      var r = reponse(), ok = corriger(q, r);
      etat.reponses[q.id] = { r: r, ok: ok };
      etat.faits[arret.id] = true;
      passer.hidden = true;
      afficherCorrection(ok);
      sauvegarder();
    };
    continuer.onclick = fermer;
    passer.onclick = function () { etat.faits[arret.id] = true; fermer(); sauvegarder(); };
    var enonce = q.type === 'trou' ? (q.texte || q.question).replace('___', '______') : q.question;
    ouvrir([h('h2', {}, 'À vous de répondre'), h('p', { class: 'question' }, enonce), choix.length ? options : champ, retour,
      h('div', { class: 'actions' }, passer, valider, continuer)], reprendre);
    if (deja) { // déjà répondu : on montre la correction
      choix.forEach(function (c) { c.entree.checked = q.type === 'multi' ? (deja.r || []).indexOf(c.valeur) >= 0 : c.valeur === deja.r; });
      if (champ) champ.value = deja.r === null || deja.r === undefined ? '' : deja.r;
      passer.hidden = true;
      afficherCorrection(deja.ok);
    }
  }

  var bandeau = $('#bandeau');
  function bandeauInteractif() {
    bandeau.innerHTML = '';
    var bouton = h('button', { type: 'button' }, 'Continuer la vidéo');
    bouton.onclick = function () { bandeau.hidden = true; video.play().catch(function () {}); };
    bandeau.appendChild(h('span', {}, 'À vous d’essayer : la diapositive est interactive.'));
    bandeau.appendChild(bouton);
    bandeau.hidden = false;
  }

  function aller(t, jouer) {
    video.currentTime = Math.max(0, Math.min(duree() - 0.2, t));
    synchroniser();
    if (jouer) video.play().catch(function () {});
  }
  function rendreTaches() {
    var b = $('#b-taches'), r = restantes();
    b.hidden = !r.length || termine;
    b.textContent = r.length + ' question' + (r.length > 1 ? 's' : '') + ' à faire';
  }
  $('#b-taches').onclick = function () {
    video.pause();
    var liste = h('ul', { class: 'taches' });
    restantes().forEach(function (a) {
      var lien = h('button', { type: 'button' }, formater(a.t) + ' · ' + (a.titre || a.question.question));
      lien.onclick = function () { aller(a.t - AVANCE_ARRET_S); fenetreQuestion(a, false); };
      liste.appendChild(h('li', {}, lien));
    });
    var fermerB = h('button', { type: 'button', class: 'principal' }, 'Fermer');
    fermerB.onclick = fermer;
    ouvrir([h('h2', {}, 'Questions à faire'), h('p', {}, 'Le module est terminé quand la vidéo a été vue et que chaque question a reçu une réponse.'), liste,
      h('div', { class: 'actions' }, fermerB)], false);
  };
  function montrerFin() {
    var s = score();
    var fermerB = h('button', { type: 'button', class: 'principal' }, 'Fermer');
    fermerB.onclick = fermer;
    ouvrir([h('h2', {}, 'Module terminé'), h('p', { class: 'question' }, 'Vous avez suivi ce replay jusqu’au bout.'),
      s !== null ? h('p', {}, 'Score : ' + s + ' % de bonnes réponses (seuil ' + (D.seuil || 50) + ' %).') : null,
      h('div', { class: 'actions' }, fermerB)], false);
  }

  // ---------------------------------------------------------------------------------------------------------------
  // Barre de lecture et chapitres
  // ---------------------------------------------------------------------------------------------------------------
  var position = $('#position'), temps = $('#temps'), bLecture = $('#b-lecture'), selChapitre = $('#chapitre'), listeChapitres = $('#liste-chapitres');
  var chapitres = D.chapitres && D.chapitres.length ? D.chapitres : [{ debut: 0, titre: D.titre }];
  function chapitreA(t) { var i = 0; chapitres.forEach(function (c, j) { if (c.debut <= t + 0.01) i = j; }); return i; }
  function construireBarre() {
    var d = duree();
    position.max = String(d);
    selChapitre.innerHTML = '';
    listeChapitres.innerHTML = '';
    var reperes = $('#reperes');
    reperes.innerHTML = '';
    chapitres.forEach(function (c, i) {
      selChapitre.appendChild(h('option', { value: String(i) }, formater(c.debut) + ' · ' + c.titre));
      var li = h('li', {}, h('button', { type: 'button', onclick: function () { aller(c.debut, true); } }, h('span', { class: 't' }, formater(c.debut)), h('span', {}, c.titre)));
      listeChapitres.appendChild(li);
      if (i > 0 && d) reperes.appendChild(h('span', { style: 'left:' + (100 * c.debut / d) + '%' }));
    });
    questions().forEach(function (a) { if (d) reperes.appendChild(h('span', { class: 'question', style: 'left:' + (100 * a.t / d) + '%', title: 'Question' })); });
  }
  selChapitre.onchange = function () { aller(chapitres[Number(selChapitre.value)].debut, !video.paused); };
  position.oninput = function () { aller(Number(position.value), false); };
  bLecture.onclick = function () { if (video.paused) video.play().catch(function () {}); else video.pause(); };
  $('#vitesse').onchange = function () { video.playbackRate = Number(this.value); };
  $('#b-plein').onclick = function () {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (lecteur.requestFullscreen) lecteur.requestFullscreen().catch(function () {});
  };
  document.addEventListener('keydown', function (e) {
    if (!fenetre.hidden || /^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test((e.target || {}).tagName)) return;
    if (e.key === ' ' || e.key === 'k') { e.preventDefault(); bLecture.click(); }
    else if (e.key === 'ArrowRight') aller(video.currentTime + 10, !video.paused);
    else if (e.key === 'ArrowLeft') aller(video.currentTime - 10, !video.paused);
  });

  function rafraichir() {
    var t = video.currentTime;
    temps.textContent = formater(t) + ' / ' + formater(duree());
    if (document.activeElement !== position) position.value = String(t);
    bLecture.textContent = video.paused ? '▶' : '❚❚';
    bLecture.setAttribute('aria-label', video.paused ? 'Lecture' : 'Pause');
    var i = chapitreA(t);
    if (selChapitre.value !== String(i)) selChapitre.value = String(i);
    Array.prototype.forEach.call(listeChapitres.children, function (li, j) { li.classList.toggle('courant', j === i); });
  }

  // ---------------------------------------------------------------------------------------------------------------
  // Boucle : diapositive, cadrage, tranches vues, arrêts
  // ---------------------------------------------------------------------------------------------------------------
  var dernierT = 0, derniereSauvegarde = 0;
  function verifierArrets(avant, apres) {
    if (apres - avant > 2 || apres < avant) return; // déplacement dans la vidéo : pas d'arrêt sauté en route
    for (var i = 0; i < arrets.length; i++) {
      var a = arrets[i];
      if (a.t > avant && a.t <= apres && !etat.faits[a.id] && !(a.question && etat.reponses[a.question.id])) {
        video.pause();
        if (a.question) fenetreQuestion(a, true);
        else { etat.faits[a.id] = true; bandeauInteractif(); }
        return;
      }
    }
  }
  function boucle() {
    var t = video.currentTime;
    if (!video.paused && !video.seeking) {
      etat.vues[Math.floor(t / TRANCHE_S)] = true;
      verifierArrets(dernierT, t);
      if (Date.now() - derniereSauvegarde > 15000) { derniereSauvegarde = Date.now(); sauvegarder(); }
    }
    dernierT = t;
    synchroniser();
    cadrer();
    rafraichir();
    requestAnimationFrame(boucle);
  }
  video.addEventListener('pause', function () { sauvegarder(); });
  video.addEventListener('ended', function () { sauvegarder(); });
  video.addEventListener('seeked', function () { dernierT = video.currentTime; });
  video.addEventListener('play', function () { bandeau.hidden = true; });

  // ---------------------------------------------------------------------------------------------------------------
  // Démarrage
  // ---------------------------------------------------------------------------------------------------------------
  (async function demarrer() {
    if (D.theme) document.title = D.titre || document.title;
    await window.AmphiQuizDeck.preparer(slides, null, D.camera);
    window.AmphiTeachable.preparer(slides);
    retirerSalle();
    if (zone) slides.querySelectorAll('section.libre').forEach(function (s) { window.AmphiCamera.reagencer(s, zone); });
    await window.Reveal.initialize({
      width: 1920, height: 1080, margin: 0.02, embedded: true, hash: false, history: false, center: true,
      controls: false, progress: false, keyboard: false, touch: false, slideNumber: false, transition: 'fade', transitionSpeed: 'fast',
      preloadIframes: false, autoPlayMedia: false
    });
    window.Reveal.on('slidechanged', function (e) {
      if (e.previousSlide) { window.AmphiQuizDeck.horsDiapositive(e.previousSlide); window.AmphiTeachable.horsDiapositive(e.previousSlide); }
      window.AmphiQuizDeck.surDiapositive(e.currentSlide); window.AmphiTeachable.surDiapositive(e.currentSlide);
    });
    window.Reveal.on('fragmentshown', function (e) { window.AmphiQuizDeck.surFragment(e.fragment); });

    calculerArrets();
    window.SCORM.initialiser(D.identifiant);
    var depart = restaurer();
    var pret = function () {
      construireBarre();
      if (depart > 0 && depart < duree() - 5) video.currentTime = depart;
      dernierT = video.currentTime;
      synchroniser();
      disposer();
      rendreTaches();
      requestAnimationFrame(boucle);
    };
    if (video.readyState >= 1) pret(); else video.addEventListener('loadedmetadata', pret, { once: true });
    new ResizeObserver(disposer).observe(lecteur);
    window.addEventListener('pagehide', function () { sauvegarder(); window.SCORM.terminer(termine ? '' : 'suspend'); });
    window.addEventListener('beforeunload', function () { sauvegarder(); });
  })();
})();

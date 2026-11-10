/*
 * Éditeur du deck (presentations/<slug>/slides.html) et des questions (presentations/<slug>/quiz.json).
 *
 * ?prez=slug désigne la présentation éditée (sinon l'active, résolue au démarrage) ; le slug est propagé à tous les
 * appels d'API (&prez=). Les médias relatifs (src="medias/…") sont affichés via /p/<slug>/medias/ grâce à une balise
 * <base>, mais sérialisés inchangés.
 *
 * Modèle : un tableau de <section> vivantes, parsées depuis /api/deck/slides. Toute modification
 * s'applique sur ce DOM ; la scène affiche la section courante elle-même (mise à l'échelle par
 * transform) et la sérialisation (outerHTML de chaque section) produit le fichier.
 * Voir docs/format-deck.md pour le format et les contrats d'API.
 */
(() => {
  'use strict';

  // ======================================================================
  // Utilitaires
  // ======================================================================
  const $ = s => document.querySelector(s);
  const $$ = s => Array.from(document.querySelectorAll(s));
  const echap = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const attendre = ms => new Promise(r => setTimeout(r, ms));
  const SCENE_L = 1920, SCENE_H = 1080;
  const ANIMATIONS = ['fade-in', 'fade-out', 'fade-up', 'fade-down', 'fade-left', 'fade-right', 'fade-in-then-out',
    'fade-in-then-semi-out', 'zoom-in', 'grow', 'shrink', 'strike', 'highlight-red', 'highlight-blue', 'highlight-green', 'semi-fade-out'];
  const TRANSITIONS = ['none', 'fade', 'slide', 'convex', 'concave', 'zoom'];
  // Enregistrement OBS déclenché par la diapositive (docs/seance.md) : valeur de data-obs → libellé.
  const OBS_OPTIONS = [{ v: '', l: 'aucun' }, { v: 'demarrer', l: 'démarrer l\'enregistrement' }, { v: 'arreter', l: 'arrêter l\'enregistrement' }];
  const TYPES_EL = ['texte', 'image', 'forme', 'iframe', 'video', 'lottie', 'minuteur', 'resultats', 'classement', 'nuage', 'qr'];
  const TYPES_QUESTION = ['qcm', 'multi', 'vrai_faux', 'emoji', 'rapidite', 'buzzer', 'nuage', 'texte_libre', 'trou', 'echelle', 'classement', 'nombre', 'grille'];
  const AVEC_OPTIONS = { qcm: [2, 6], multi: [2, 6], emoji: [2, 8], rapidite: [2, 6], classement: [2, 6], grille: [2, 4] };

  const parametres = new URLSearchParams(location.search);
  const token = parametres.get('token') || '';
  const prezDemandee = /^[a-z0-9-]{1,40}$/.test(parametres.get('prez') || '') ? parametres.get('prez') : null;
  let prez = prezDemandee; // slug de la présentation éditée (résolu au démarrage si absent)
  const apiUrl = chemin => chemin + (chemin.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token) + (prez ? '&prez=' + encodeURIComponent(prez) : '');
  async function apiJson(chemin, options) {
    const r = await fetch(apiUrl(chemin), options);
    if (!r.ok) {
      let detail = r.status + ' ' + r.statusText;
      try { detail = (await r.json()).detail || detail; } catch (e) { /* corps non JSON */ }
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return r.json();
  }

  /** Construit un élément : h('div', { class: 'a', onclick: f }, 'texte', autreNoeud). Les chaînes deviennent du texte (jamais du HTML). */
  function h(tag, attrs, ...enfants) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith('on')) el[k] = v;
      else if (k === 'class') el.className = v;
      else if (k === 'value') el.value = v;
      else if (k === 'checked' || k === 'selected' || k === 'disabled' || k === 'hidden') el[k] = !!v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    const ajouter = e => {
      if (e === null || e === undefined || e === false) return;
      if (Array.isArray(e)) e.forEach(ajouter);
      else el.append(e instanceof Node ? e : document.createTextNode(String(e)));
    };
    enfants.forEach(ajouter);
    return el;
  }

  /** Convertit une couleur CSS quelconque en #rrggbb (chaîne vide si transparente ou invalide). */
  function versHex(couleur) {
    if (!couleur) return '';
    if (/^#[0-9a-f]{6}$/i.test(couleur)) return couleur.toLowerCase();
    const sonde = $('#sonde');
    sonde.style.color = '';
    sonde.style.color = couleur;
    if (!sonde.style.color) return '';
    const m = getComputedStyle(sonde).color.match(/\d+(\.\d+)?/g);
    if (!m || (m.length > 3 && parseFloat(m[3]) === 0)) return '';
    return '#' + m.slice(0, 3).map(n => Math.round(parseFloat(n)).toString(16).padStart(2, '0')).join('');
  }
  const px = n => Math.round(n) + 'px';
  const num = (s, defaut = 0) => { const n = parseFloat(s); return Number.isFinite(n) ? n : defaut; };

  // ======================================================================
  // État
  // ======================================================================
  const etat = {
    sections: [],      // <section> vivantes, dans l'ordre
    entete: [],        // nœuds hors section (commentaires d'en-tête), sérialisés
    courant: 0,        // index de la diapositive affichée
    selection: null,   // data-id de l'élément sélectionné (section libre)
    questions: [],     // quiz.json complet
    questionsModifiees: false,
    questionCourante: 0,
    modifie: false,
    edition: null,     // élément en édition contenteditable
    echelle: 0.5,
    nomImport: null,
    camera: undefined, // `camera` de la présentation (docs/zone-camera.md) : undefined → défaut, null → désactivée
  };
  const historique = { passe: [], futur: [] };
  const LIMITE_HISTORIQUE = 50;

  const sectionCourante = () => etat.sections[etat.courant];
  const elSelectionne = () => {
    const s = sectionCourante();
    if (!s || !etat.selection) return null;
    return Array.from(s.children).find(c => c.classList.contains('el') && c.dataset.id === etat.selection) || null;
  };
  const estLibre = s => !!s && s.classList.contains('libre');
  const estQuiz = s => !!s && !estLibre(s) && s.hasAttribute('data-quiz');
  const typeSection = s => estLibre(s) ? 'libre' : estQuiz(s) ? 'quiz' : 'flux';
  function typeEl(el) {
    const t = TYPES_EL.find(x => el.classList.contains(x));
    if (t) return t;
    return { IMG: 'image', IFRAME: 'iframe', VIDEO: 'video' }[el.tagName] || 'texte';
  }
  const questionParId = id => etat.questions.find(q => q.id === id);

  // ======================================================================
  // Chargement, normalisation, sérialisation
  // ======================================================================
  function parserDeck(texte) {
    const gabarit = document.createElement('template');
    gabarit.innerHTML = texte;
    const sections = [], entete = [];
    for (const n of Array.from(gabarit.content.childNodes)) {
      if (n.nodeType === Node.ELEMENT_NODE && n.tagName === 'SECTION') sections.push(document.adoptNode(n));
      else if (n.nodeType === Node.COMMENT_NODE) entete.push('<!--' + n.data + '-->');
      else if (n.nodeType === Node.ELEMENT_NODE) entete.push(n.outerHTML); // élément hors section : conservé tel quel en tête
    }
    return { sections, entete };
  }

  /** Attribue un data-id unique à chaque section (s+n) et à chaque .el dans sa section (e+n). */
  function normaliserIds(sections) {
    const vus = new Set();
    let max = 0;
    sections.forEach(s => { const m = /^s(\d+)$/.exec(s.dataset.id || ''); if (m) max = Math.max(max, +m[1]); });
    sections.forEach(s => {
      if (!s.dataset.id || vus.has(s.dataset.id)) s.dataset.id = 's' + (++max);
      vus.add(s.dataset.id);
      normaliserIdsElements(s);
    });
  }
  function normaliserIdsElements(section) {
    const els = Array.from(section.children).filter(c => c.classList.contains('el'));
    const vus = new Set();
    let max = 0;
    els.forEach(e => { const m = /^e(\d+)$/.exec(e.dataset.id || ''); if (m) max = Math.max(max, +m[1]); });
    els.forEach(e => {
      if (!e.dataset.id || vus.has(e.dataset.id)) e.dataset.id = 'e' + (++max);
      vus.add(e.dataset.id);
    });
  }
  function nouvelIdElement(section) {
    let max = 0;
    Array.from(section.children).forEach(e => { const m = /^e(\d+)$/.exec(e.dataset.id || ''); if (m) max = Math.max(max, +m[1]); });
    return 'e' + (max + 1);
  }
  function nouvelIdSection() {
    let max = 0;
    etat.sections.forEach(s => { const m = /^s(\d+)$/.exec(s.dataset.id || ''); if (m) max = Math.max(max, +m[1]); });
    return 's' + (max + 1);
  }

  /** Clone « propre » d'une section : sans les artefacts de l'éditeur. */
  function sectionPropre(section) {
    const c = section.cloneNode(true);
    c.querySelectorAll('[contenteditable]').forEach(n => n.removeAttribute('contenteditable'));
    c.querySelectorAll('.ed-cache, .ed-chevauche').forEach(n => { n.classList.remove('ed-cache', 'ed-chevauche'); if (!n.className) n.removeAttribute('class'); });
    c.querySelectorAll('[style]').forEach(compacterStyle);
    if (c.hasAttribute('style')) compacterStyle(c);
    return c;
  }
  /** Le navigateur réécrit les styles modifiés (« left: 10px; color: rgb(1, 2, 3); ») : on revient à la forme compacte du format. */
  function compacterStyle(el) {
    const brut = el.getAttribute('style') || '';
    if (!brut.trim()) { el.removeAttribute('style'); return; }
    if (/url\(|["']/.test(brut)) return; // valeurs complexes : on ne touche pas
    const decl = brut.split(';').map(d => d.trim()).filter(Boolean).map(d => {
      const i = d.indexOf(':');
      if (i < 0) return d;
      let v = d.slice(i + 1).trim().replace(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/g, (_, r, g, b) => '#' + [r, g, b].map(n => (+n).toString(16).padStart(2, '0')).join(''));
      return d.slice(0, i).trim() + ':' + v;
    });
    el.setAttribute('style', decl.join(';'));
  }
  function balise(el) {
    return '<' + el.tagName.toLowerCase() + Array.from(el.attributes).map(a => ' ' + a.name + (a.value === '' ? '' : '="' + a.value.replace(/&/g, '&amp;').replace(/"/g, '&quot;') + '"')).join('') + '>';
  }
  /** Une section = balise ouvrante, chaque enfant direct sur sa ligne indenté de 2 espaces, balise fermante. Le HTML interne des enfants est gardé verbatim (sérialisation stable). */
  function serialiserSection(section) {
    const c = sectionPropre(section);
    const lignes = [balise(c)];
    const div = document.createElement('div');
    for (const n of Array.from(c.childNodes)) {
      if (n.nodeType === Node.TEXT_NODE) {
        if (!n.data.trim()) continue;
        div.textContent = n.data.trim();
        lignes.push('  ' + div.innerHTML);
      } else if (n.nodeType === Node.COMMENT_NODE) lignes.push('  <!--' + n.data + '-->');
      else lignes.push('  ' + n.outerHTML);
    }
    if (lignes.length === 1) return lignes[0] + '</section>'; // section vide (quiz) : une seule ligne
    lignes.push('</section>');
    return lignes.join('\n');
  }
  function serialiser() {
    return [...etat.entete, ...etat.sections.map(serialiserSection)].join('\n\n') + '\n';
  }

  function chargerDepuisTexte(texte) {
    const { sections, entete } = parserDeck(texte);
    normaliserIds(sections);
    etat.sections = sections;
    etat.entete = entete;
    etat.courant = 0;
    etat.selection = null;
    historique.passe = []; historique.futur = [];
    if (!etat.sections.length) ajouterSection('libre', -1);
    rendreTout();
  }

  /** Résout la présentation éditée (paramètre prez ou l'active), affiche son titre et fait pointer les médias relatifs vers /p/<slug>/. */
  async function resoudrePresentation() {
    let meta = null;
    try {
      if (prez) meta = (await (await fetch('/api/presentations', { cache: 'no-store' })).json()).find(p => p.slug === prez) || null;
      else { meta = await (await fetch('/api/presentations/active', { cache: 'no-store' })).json(); prez = meta && meta.slug ? meta.slug : null; }
    } catch (e) { console.warn('présentation indisponible', e); }
    etat.camera = meta ? meta.camera : undefined;
    if (prez) {
      const base = document.createElement('base');
      base.href = '/p/' + prez + '/';
      document.head.appendChild(base);
    }
    // Feuille de thème de la présentation (deck/themes/<theme>.css), pour un rendu identique à l'écran.
    if (meta && meta.theme && /^[a-z0-9_-]{1,40}$/.test(meta.theme)) {
      const lien = document.createElement('link');
      lien.rel = 'stylesheet'; lien.href = '/deck/themes/' + meta.theme + '.css';
      document.head.appendChild(lien);
    }
    $('#titre-prez').textContent = meta && meta.titre ? meta.titre : (prez || 'présentation introuvable');
    $('#titre-prez').title = prez ? 'presentations/' + prez : '';
    $('#lien-presentations').href = '/deck/presentations.html?token=' + encodeURIComponent(token);
    if (prezDemandee && !meta) etatSauvegarde('présentation « ' + prezDemandee + ' » introuvable', 'erreur');
  }

  async function charger() {
    await resoudrePresentation();
    try {
      const [rSlides, rQuestions] = await Promise.allSettled([
        fetch(apiUrl('/api/deck/slides'), { cache: 'no-store' }).then(r => { if (!r.ok) throw new Error(r.status); return r.text(); }),
        apiJson('/api/quiz/questions'),
      ]);
      if (rQuestions.status === 'fulfilled' && Array.isArray(rQuestions.value)) etat.questions = rQuestions.value;
      else console.warn('questions indisponibles', rQuestions.reason);
      if (rSlides.status !== 'fulfilled') throw rSlides.reason;
      chargerDepuisTexte(rSlides.value);
      etatSauvegarde('enregistré', 'enregistre');
    } catch (e) {
      etatSauvegarde('erreur de chargement : ' + e.message, 'erreur');
      chargerDepuisTexte('');
    }
    rendreQuestions();
  }

  // ======================================================================
  // Historique (sur le HTML de la section courante) et marquage « modifié »
  // ======================================================================
  function pousserHistorique() {
    const s = sectionCourante();
    if (!s) return;
    historique.passe.push({ i: etat.courant, html: sectionPropre(s).outerHTML });
    if (historique.passe.length > LIMITE_HISTORIQUE) historique.passe.shift();
    historique.futur = [];
    majBoutonsHistorique();
  }
  function remplacerSection(i, html) {
    const { sections } = parserDeck(html);
    if (!sections.length) return;
    etat.sections[i] = sections[0];
  }
  function annuler() {
    const e = historique.passe.pop();
    if (!e) return;
    terminerEdition(false);
    if (e.supprimee) { // diapositive supprimée : on la remet à sa place
      const { sections } = parserDeck(e.html);
      if (sections.length) etat.sections.splice(e.i, 0, sections[0]);
      historique.futur.push(e);
      etat.courant = e.i;
      etat.selection = null;
      marquer();
      rendreTout();
      return;
    }
    historique.futur.push({ i: e.i, html: sectionPropre(etat.sections[e.i]).outerHTML });
    remplacerSection(e.i, e.html);
    etat.courant = e.i;
    marquer();
    rendreTout();
  }
  function retablir() {
    const e = historique.futur.pop();
    if (!e) return;
    terminerEdition(false);
    if (e.supprimee) { // rétablir la suppression
      etat.sections.splice(e.i, 1);
      historique.passe.push(e);
      etat.courant = Math.max(0, Math.min(e.i, etat.sections.length - 1));
      etat.selection = null;
      marquer();
      rendreTout();
      return;
    }
    historique.passe.push({ i: e.i, html: sectionPropre(etat.sections[e.i]).outerHTML });
    remplacerSection(e.i, e.html);
    etat.courant = e.i;
    marquer();
    rendreTout();
  }
  function majBoutonsHistorique() {
    $('#b-annuler').disabled = !historique.passe.length;
    $('#b-retablir').disabled = !historique.futur.length;
  }
  function etatSauvegarde(texte, classe) {
    const el = $('#etat-sauvegarde');
    el.textContent = texte;
    el.className = 'etat ' + (classe || '');
  }
  function marquer() {
    etat.modifie = true;
    etatSauvegarde('modifié', 'modifie');
  }
  /** À appeler après une modification : marque, rafraîchit la vignette courante et la boîte de sélection. */
  function modifie() {
    marquer();
    rendreVignette(etat.courant);
    rafraichirSelection();
    rafraichirCalque();
  }
  /** Geste (saisie, glissement) : un seul point d'historique par geste. */
  let gesteEnCours = false;
  function debutGeste() { if (!gesteEnCours) { pousserHistorique(); gesteEnCours = true; } }
  function finGeste() { gesteEnCours = false; modifie(); }

  // ======================================================================
  // Rendu général
  // ======================================================================
  function rendreTout() {
    rendreListe();
    rendreScene();
    rendrePanneau();
    majBoutonsHistorique();
    $('#nom-fichier').textContent = 'slides.html' + (etat.nomImport ? ' (importé : ' + etat.nomImport + ')' : '');
  }

  // ---- Liste des diapositives (gauche) ----
  function rendreListe() {
    const ol = $('#liste-diapos');
    ol.textContent = '';
    requestAnimationFrame(redimensionnerVignettes);
    const debut = indexObs('demarrer'), fin = indexObs('arreter');
    etat.sections.forEach((s, i) => {
      const enregistree = debut >= 0 && i >= debut && (fin < 0 || fin <= debut || i <= fin);
      const li = h('li', { class: (i === etat.courant ? 'courante' : '') + (enregistree ? ' enregistree' : ''), draggable: 'true', 'data-index': i,
        onclick: () => { if (i !== etat.courant) { terminerEdition(true); etat.courant = i; etat.selection = null; rendreTout(); } } },
        h('div', { class: 'numero' }, String(i + 1)),
        h('div', {}, vignette(s), h('div', { class: 'type' }, libelleSection(s), badgeCamera(s), badgeObs(s))));
      li.addEventListener('contextmenu', e => menuDiapo(e, i));
      li.addEventListener('dragstart', e => { e.dataTransfer.setData('text/plain', String(i)); e.dataTransfer.effectAllowed = 'move'; });
      li.addEventListener('dragover', e => { e.preventDefault(); li.classList.add('cible'); });
      li.addEventListener('dragleave', () => li.classList.remove('cible'));
      li.addEventListener('drop', e => {
        e.preventDefault(); li.classList.remove('cible');
        const de = parseInt(e.dataTransfer.getData('text/plain'), 10);
        if (Number.isInteger(de) && de !== i) deplacerSection(de, i);
      });
      ol.append(li);
    });
  }
  function libelleSection(s) {
    const t = typeSection(s);
    if (t === 'quiz') return 'quiz · ' + s.dataset.quiz;
    return t + ' · ' + s.dataset.id;
  }
  function vignette(s) {
    const r = h('div', { class: 'reveal ' + typeSection(s) });
    r.append(rendreApercu(s));
    const v = h('div', { class: 'vignette' }, r);
    const fond = s.dataset.backgroundColor;
    if (fond) v.style.background = fond;
    if (s.dataset.backgroundImage) { v.style.backgroundImage = 'url("' + s.dataset.backgroundImage.replace(/"/g, '') + '")'; v.style.backgroundSize = 'cover'; }
    return v;
  }
  function rendreVignette(i) {
    const li = $('#liste-diapos li[data-index="' + i + '"]');
    if (!li) return;
    const ancien = li.querySelector('.vignette');
    if (ancien) ancien.replaceWith(vignette(etat.sections[i]));
    const type = li.querySelector('.type');
    if (type) { type.textContent = libelleSection(etat.sections[i]); type.append(badgeCamera(etat.sections[i]) || '', badgeObs(etat.sections[i]) || ''); }
  }

  // ---- Enregistrement OBS : diapositive de démarrage et d'arrêt (data-obs, docs/seance.md) ----
  function indexObs(action) { return etat.sections.findIndex(s => s.dataset.obs === action); }
  function badgeObs(s) {
    if (s.dataset.obs === 'demarrer') return h('span', { class: 'badge-obs debut', title: 'L\'enregistrement OBS démarre à l\'arrivée sur cette diapositive' }, '⏺ début');
    if (s.dataset.obs === 'arreter') return h('span', { class: 'badge-obs fin', title: 'L\'enregistrement OBS s\'arrête à l\'arrivée sur cette diapositive' }, '⏹ fin');
    return null;
  }
  function titreCourt(s) {
    const t = s.querySelector('h1, h2, h3, .dsfr-titre');
    // Un espace entre les blocs du titre (un <br> ou une balise ne doivent pas coller deux mots).
    const mots = t ? [...t.childNodes].map(n => n.textContent).join(' ') : '';
    const texte = mots.replace(/\s+/g, ' ').replace(/\s+([,.])/g, '$1').trim() || libelleSection(s);
    return texte.length > 42 ? texte.slice(0, 41) + '…' : texte;
  }

  /** Badge « 🎥 n » : n éléments de la section chevauchent la zone caméra (calque activé). */
  function badgeCamera(s) {
    const zone = zoneCameraActive();
    const n = zone && estLibre(s) ? AmphiCamera.elementsChevauchant(s, zone).length : 0;
    if (!n) return null;
    return h('span', { class: 'badge-camera', title: n + ' élément' + (n > 1 ? 's chevauchent' : ' chevauche') + ' la zone caméra OBS' }, '🎥 ' + n);
  }
  /** Rendu non vivant d'une section (pour les vignettes) ; les sections de quiz ont un aperçu généré. */
  function rendreApercu(s) {
    if (estQuiz(s)) return apercuQuiz(s);
    return sectionPropre(s);
  }
  /** Aperçu d'une diapositive de quiz, dans la disposition de quiz-deck.js. */
  function apercuQuiz(s) {
    const q = questionParId(s.dataset.quiz);
    const sec = h('section', { class: 'quiz' });
    const statique = h('div', { class: 'quiz-statique' },
      h('h2', {}, q ? q.question : 'Question « ' + s.dataset.quiz + ' » absente de quiz.json'),
      q && Array.isArray(q.options) ? h('ol', { class: 'quiz-options' }, q.options.map(o => h('li', {}, String(o)))) : null,
      h('div', { class: 'quiz-qr' }, h('div', { class: 'substitut' }, 'QR'), h('div', { class: 'quiz-url' }, location.host + '/q')));
    sec.append(h('div', { class: 'quiz-grille' }, statique, h('div', { class: 'quiz-resultats substitut' }, '📊 résultats en direct')));
    return sec;
  }

  // ---- Scène (centre) ----
  function rendreScene() {
    const s = sectionCourante();
    const rendu = $('#rendu');
    rendu.textContent = '';
    rendu.className = 'reveal ' + typeSection(s);
    const scene = $('#scene');
    scene.style.background = s.dataset.backgroundColor || '#fff';
    scene.style.backgroundImage = s.dataset.backgroundImage ? 'url("' + s.dataset.backgroundImage.replace(/"/g, '') + '")' : '';
    scene.style.backgroundSize = s.dataset.backgroundSize || 'cover';
    if (estQuiz(s)) rendu.append(apercuQuiz(s));
    else rendu.append(s); // la section vivante elle-même
    $('#barre-ajout').style.visibility = estLibre(s) ? 'visible' : 'hidden';
    rafraichirCalque();
    rafraichirSelection();
  }
  // ---- Zone caméra OBS (docs/zone-camera.md) ----
  /** Zone réservée si le calque est coché (case mémorisée) et la caméra de la présentation non désactivée. */
  function zoneCameraActive() {
    if (!window.AmphiCamera || !$('#c-camera').checked) return null;
    return AmphiCamera.zoneCamera(etat.camera);
  }
  /** Rectangle en pointillés de la zone réservée, vignette 🎥 dans le coin ; hors DOM des sections (jamais sérialisé). */
  function rendreZoneCamera() {
    const bloc = $('#zone-camera');
    const zone = zoneCameraActive();
    const s = sectionCourante();
    if (!zone || !estLibre(s)) { bloc.hidden = true; return; }
    bloc.hidden = false;
    Object.assign(bloc.style, { left: px(zone.x), top: px(zone.y), width: px(zone.l), height: px(zone.h) });
    bloc.textContent = '';
    const c = zone.camera;
    const cam = h('div', { class: 'camera' }, '🎥 caméra ' + c.largeur + '×' + c.hauteur);
    Object.assign(cam.style, { left: px(zone.droite ? zone.l - c.largeur : 0), top: px(zone.bas ? zone.h - c.hauteur : 0), width: px(c.largeur), height: px(c.hauteur) });
    bloc.append(cam);
  }
  /** Contour orange (classe ed-chevauche, retirée à la sérialisation) sur les éléments qui chevauchent la zone ; alerte du panneau. */
  function marquerChevauchements() {
    const s = sectionCourante();
    if (!s) return;
    const zone = zoneCameraActive();
    const touches = new Set(zone && estLibre(s) ? AmphiCamera.elementsChevauchant(s, zone) : []);
    Array.from(s.children).forEach(el => { if (el.classList.contains('el')) el.classList.toggle('ed-chevauche', touches.has(el)); });
    const alerte = $('#alerte-camera');
    if (alerte) alerte.hidden = !touches.has(elSelectionne());
  }

  /** Calque transparent au-dessus des iframes et vidéos (sinon elles avalent la souris) ; rafraîchit aussi la zone caméra. */
  function rafraichirCalque() {
    rendreZoneCamera();
    marquerChevauchements();
    const calque = $('#calque');
    calque.textContent = '';
    const s = sectionCourante();
    if (!estLibre(s)) return;
    Array.from(s.children).filter(c => c.classList.contains('el') && (c.tagName === 'IFRAME' || c.tagName === 'VIDEO')).forEach(el => {
      const b = boite(el);
      const d = h('div', { 'data-pour': el.dataset.id, title: 'iframe/vidéo : ' + (el.getAttribute('src') || '') });
      Object.assign(d.style, { left: px(b.x), top: px(b.y), width: px(b.l), height: px(b.h), transform: b.rot ? 'rotate(' + b.rot + 'deg)' : '' });
      calque.append(d);
    });
  }
  function rafraichirSelection() {
    const sel = $('#selection');
    const el = elSelectionne();
    if (!el || !estLibre(sectionCourante())) { sel.hidden = true; return; }
    const b = boite(el);
    sel.hidden = false;
    Object.assign(sel.style, { left: px(b.x), top: px(b.y), width: px(b.l), height: px(b.h), transform: b.rot ? 'rotate(' + b.rot + 'deg)' : '' });
    sel.classList.toggle('edition', etat.edition === el);
  }
  function redimensionnerScene() {
    const zone = $('#zone-scene');
    const marge = 36, regle = 16;
    const l = zone.clientWidth - marge * 2 - regle, hh = zone.clientHeight - marge * 2 - regle;
    if (l <= 0 || hh <= 0) return;
    const e = Math.min(l / SCENE_L, hh / SCENE_H);
    etat.echelle = e;
    document.documentElement.style.setProperty('--echelle', e);
    const cadre = $('#cadre');
    const x = regle + marge + (l - SCENE_L * e) / 2, y = regle + marge + (hh - SCENE_H * e) / 2;
    Object.assign(cadre.style, { left: px(x), top: px(y), width: px(SCENE_L * e), height: px(SCENE_H * e) });
    Object.assign($('#regle-h').style, { left: px(x), top: px(y - regle - 2), width: px(SCENE_L * e) });
    Object.assign($('#regle-v').style, { top: px(y), left: px(x - regle - 2), height: px(SCENE_H * e) });
  }
  new ResizeObserver(redimensionnerScene).observe($('#zone-scene'));
  // Échelle des vignettes : largeur disponible dans la colonne de gauche / 1920
  function redimensionnerVignettes() {
    const v = $('#liste-diapos .vignette');
    if (v && v.clientWidth) $('#liste-diapos').style.setProperty('--echelle-vignette', v.clientWidth / SCENE_L);
  }
  new ResizeObserver(redimensionnerVignettes).observe($('#liste-diapos'));

  // ======================================================================
  // Géométrie des éléments
  // ======================================================================
  function rotationDe(el) {
    const m = /rotate\(\s*(-?[\d.]+)deg\s*\)/.exec(el.style.transform || '');
    return m ? parseFloat(m[1]) : 0;
  }
  function boite(el) {
    return {
      x: num(el.style.left), y: num(el.style.top),
      l: el.style.width ? num(el.style.width) : (el.offsetWidth || 200),
      h: el.style.height ? num(el.style.height) : (el.offsetHeight || 100),
      rot: rotationDe(el),
    };
  }
  function poser(el, b) {
    if (b.x !== undefined) el.style.left = px(b.x);
    if (b.y !== undefined) el.style.top = px(b.y);
    if (b.l !== undefined) el.style.width = px(Math.max(10, b.l));
    if (b.h !== undefined) el.style.height = px(Math.max(10, b.h));
    if (b.rot !== undefined) {
      const autres = (el.style.transform || '').replace(/rotate\([^)]*\)/, '').trim();
      el.style.transform = (b.rot ? 'rotate(' + Math.round(b.rot * 10) / 10 + 'deg) ' : '') + autres;
      if (!el.style.transform.trim()) el.style.removeProperty('transform');
    }
  }
  /** Coordonnées de scène (1920×1080) d'un événement souris. */
  function pointScene(e) {
    const r = $('#scene').getBoundingClientRect();
    return { x: (e.clientX - r.left) / etat.echelle, y: (e.clientY - r.top) / etat.echelle };
  }

  // ======================================================================
  // Sélection, déplacement, redimensionnement (section libre)
  // ======================================================================
  function selectionner(id) {
    if (etat.edition && etat.edition.dataset.id !== id) terminerEdition(true);
    etat.selection = id;
    rafraichirSelection();
    rendrePanneau();
  }

  let geste = null; // { type: 'deplacer'|'redim', depart, boite, poignee, el }
  $('#scene').addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    const s = sectionCourante();
    if (!estLibre(s)) return;
    const poignee = e.target.closest('.poignee');
    if (poignee) {
      const el = elSelectionne();
      if (!el) return;
      e.preventDefault();
      geste = { type: 'redim', depart: pointScene(e), boite: boite(el), poignee: poignee.dataset.p, el, bouge: false };
      return;
    }
    let cible = e.target.closest('.el');
    if (e.target.parentElement === $('#calque')) cible = Array.from(s.children).find(c => c.dataset.id === e.target.dataset.pour) || null;
    if (cible && cible.parentElement !== s) cible = cible.closest('section.libre > .el'); // .el imbriqué : on prend l'enfant direct
    if (!cible) { if (etat.edition) terminerEdition(true); etat.selection = null; rafraichirSelection(); rendrePanneau(); return; }
    if (etat.edition === cible) return; // en édition : laisser la souris au texte
    if (etat.selection !== cible.dataset.id) selectionner(cible.dataset.id);
    e.preventDefault();
    geste = { type: 'deplacer', depart: pointScene(e), boite: boite(cible), el: cible, bouge: false };
  });
  window.addEventListener('mousemove', e => {
    if (!geste) return;
    const p = pointScene(e);
    let dx = p.x - geste.depart.x, dy = p.y - geste.depart.y;
    if (!geste.bouge) { if (Math.abs(dx) < 2 && Math.abs(dy) < 2) return; geste.bouge = true; debutGeste(); }
    const b = geste.boite, pas = $('#c-grille').checked ? 10 : 1;
    const arrondir = v => Math.round(v / pas) * pas;
    if (geste.type === 'deplacer') {
      poser(geste.el, { x: arrondir(b.x + dx), y: arrondir(b.y + dy) });
    } else {
      const n = { x: b.x, y: b.y, l: b.l, h: b.h };
      const pg = geste.poignee;
      if (pg.includes('e')) n.l = b.l + dx;
      if (pg.includes('s')) n.h = b.h + dy;
      if (pg.includes('w')) { n.l = b.l - dx; n.x = b.x + dx; }
      if (pg.includes('n')) { n.h = b.h - dy; n.y = b.y + dy; }
      if (e.shiftKey && pg.length === 2 && b.l && b.h) { // coin + Maj : ratio conservé
        const ratio = b.l / b.h;
        if (Math.abs(n.l - b.l) / b.l > Math.abs(n.h - b.h) / b.h) n.h = n.l / ratio; else n.l = n.h * ratio;
        if (pg.includes('w')) n.x = b.x + b.l - n.l;
        if (pg.includes('n')) n.y = b.y + b.h - n.h;
      }
      n.l = Math.max(10, arrondir(n.l)); n.h = Math.max(10, arrondir(n.h));
      poser(geste.el, n);
    }
    rafraichirSelection();
    rafraichirCalque();
    rafraichirChampsGeometrie();
  });
  window.addEventListener('mouseup', () => {
    if (!geste) return;
    if (geste.bouge) finGeste();
    geste = null;
  });
  $('#scene').addEventListener('dragstart', e => e.preventDefault());

  // Double-clic : édition en place des textes et formes
  $('#scene').addEventListener('dblclick', e => {
    const s = sectionCourante();
    if (!estLibre(s)) return;
    const el = e.target.closest('section.libre > .el');
    if (!el || el.parentElement !== s) return;
    const t = typeEl(el);
    if (t !== 'texte' && t !== 'forme') return;
    commencerEdition(el);
  });

  // ======================================================================
  // Édition en place (contenteditable + mini barre)
  // ======================================================================
  function commencerEdition(el) {
    if (etat.edition === el) return;
    terminerEdition(true);
    etat.selection = el.dataset.id;
    pousserHistorique();
    etat.edition = el;
    el.contentEditable = 'true';
    el.focus();
    const sel = window.getSelection();
    if (sel && el.lastChild) { sel.selectAllChildren(el); sel.collapseToEnd(); }
    rafraichirSelection();
    placerMini();
  }
  function placerMini() {
    const mini = $('#mini');
    if (!etat.edition) { mini.hidden = true; return; }
    mini.hidden = false;
    const r = etat.edition.getBoundingClientRect();
    const zone = $('#zone-scene').getBoundingClientRect();
    let x = Math.max(zone.left + 4, Math.min(r.left, zone.right - mini.offsetWidth - 4));
    let y = r.top - mini.offsetHeight - 8;
    if (y < zone.top + 4) y = Math.min(r.bottom + 8, zone.bottom - mini.offsetHeight - 4);
    mini.style.left = px(x); mini.style.top = px(y);
    const c = versHex(getComputedStyle(etat.edition).color);
    if (c) $('#mini-couleur').value = c;
  }
  /** Fin de l'édition : nettoyage du HTML interne (pas de style vide, pas de span vide, <font> → span). */
  function terminerEdition(conserver) {
    const el = etat.edition;
    if (!el) return;
    etat.edition = null;
    el.removeAttribute('contenteditable');
    $('#mini').hidden = true;
    if (conserver) {
      nettoyerHtml(el);
      modifie();
      rendrePanneau();
    }
    rafraichirSelection();
  }
  function nettoyerHtml(el) {
    el.querySelectorAll('font').forEach(f => {
      const s = document.createElement('span');
      if (f.color) s.style.color = f.color;
      if (f.face) s.style.fontFamily = f.face;
      while (f.firstChild) s.append(f.firstChild);
      f.replaceWith(s);
    });
    el.querySelectorAll('*').forEach(n => {
      if (n.hasAttribute('style') && !n.getAttribute('style').trim()) n.removeAttribute('style');
      if (n.hasAttribute('class') && !n.getAttribute('class').trim()) n.removeAttribute('class');
      n.removeAttribute('contenteditable');
    });
    let encore = true;
    while (encore) {
      encore = false;
      el.querySelectorAll('span').forEach(s => {
        if (!s.attributes.length) { s.replaceWith(...s.childNodes); encore = true; }
        else if (!s.textContent.trim() && !s.querySelector('img, br')) { s.remove(); encore = true; }
      });
    }
    el.querySelectorAll('p, div, li').forEach(b => { if (!b.textContent.trim() && !b.children.length && b !== el) b.remove(); });
    if (el.innerHTML.trim() === '<br>') el.innerHTML = '';
  }
  // Mini barre : les mousedown ne doivent pas retirer le focus du texte
  $('#mini').addEventListener('mousedown', e => { if (e.target.tagName !== 'INPUT') e.preventDefault(); });
  $('#mini').addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b || !etat.edition) return;
    if (b.dataset.cmd) document.execCommand(b.dataset.cmd, false, null);
    if (b.dataset.taille) {
      const el = etat.edition;
      const t = num(el.style.fontSize) || num(getComputedStyle(el).fontSize) || 40;
      el.style.fontSize = px(Math.max(8, t + parseInt(b.dataset.taille, 10)));
      rafraichirSelection();
    }
    etat.edition.focus();
  });
  $('#mini-couleur').addEventListener('input', e => { if (etat.edition) { document.execCommand('foreColor', false, e.target.value); } });
  document.addEventListener('mousedown', e => {
    if (!etat.edition) return;
    if (e.target.closest('#mini') || etat.edition.contains(e.target)) return;
    terminerEdition(true);
  });
  $('#scene').addEventListener('input', () => { if (etat.edition) { rafraichirSelection(); placerMini(); } });

  // ======================================================================
  // Opérations sur l'élément sélectionné
  // ======================================================================
  function supprimerElement() {
    const el = elSelectionne();
    if (!el) return;
    terminerEdition(false);
    pousserHistorique();
    el.remove();
    etat.selection = null;
    modifie();
    rendrePanneau();
  }
  function dupliquerElement() {
    const el = elSelectionne();
    if (!el) return;
    terminerEdition(true);
    pousserHistorique();
    const c = el.cloneNode(true);
    c.dataset.id = nouvelIdElement(sectionCourante());
    const b = boite(el);
    poser(c, { x: b.x + 20, y: b.y + 20 });
    el.after(c);
    notesEnDernier(sectionCourante());
    modifie();
    selectionner(c.dataset.id);
  }
  function deplacerElement(dx, dy) {
    const el = elSelectionne();
    if (!el) return;
    pousserHistorique();
    const b = boite(el);
    poser(el, { x: b.x + dx, y: b.y + dy });
    modifie();
    rafraichirChampsGeometrie();
  }
  function ordreZ(sens) {
    const el = elSelectionne();
    if (!el) return;
    pousserHistorique();
    if (sens > 0 && el.nextElementSibling && !el.nextElementSibling.matches('aside.notes')) el.nextElementSibling.after(el);
    if (sens < 0 && el.previousElementSibling) el.previousElementSibling.before(el);
    modifie();
  }
  function aligner(mode) {
    const el = elSelectionne();
    if (!el) return;
    pousserHistorique();
    const b = boite(el);
    const n = {};
    if (mode === 'gauche') n.x = 0;
    if (mode === 'centreH') n.x = (SCENE_L - b.l) / 2;
    if (mode === 'droite') n.x = SCENE_L - b.l;
    if (mode === 'haut') n.y = 0;
    if (mode === 'centreV') n.y = (SCENE_H - b.h) / 2;
    if (mode === 'bas') n.y = SCENE_H - b.h;
    poser(el, n);
    modifie();
    rafraichirChampsGeometrie();
  }
  function ajusterAuTexte() {
    const el = elSelectionne();
    if (!el) return;
    pousserHistorique();
    el.style.height = 'auto';
    const hh = el.scrollHeight || el.offsetHeight;
    el.style.height = px(Math.max(10, hh));
    modifie();
    rafraichirChampsGeometrie();
  }
  /** L'aside des notes doit rester le dernier enfant. */
  function notesEnDernier(section) {
    const a = section.querySelector(':scope > aside.notes');
    if (a && a !== section.lastElementChild) section.append(a);
  }

  // ======================================================================
  // Ajout d'éléments (section libre)
  // ======================================================================
  function creerElement(html) {
    const t = document.createElement('template');
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }
  function inserer(el, largeur, hauteur) {
    const s = sectionCourante();
    terminerEdition(true);
    pousserHistorique();
    el.dataset.id = nouvelIdElement(s);
    const l = largeur ?? num(el.style.width, 400), hh = hauteur ?? num(el.style.height, 200);
    poser(el, { x: (SCENE_L - l) / 2, y: (SCENE_H - hh) / 2, l, h: hh });
    const notes = s.querySelector(':scope > aside.notes');
    if (notes) notes.before(el); else s.append(el);
    modifie();
    selectionner(el.dataset.id);
  }
  async function ajouterElement(type, variante) {
    switch (type) {
      case 'texte': return inserer(creerElement('<div class="el texte" style="font-size:40px">Nouveau texte</div>'), 800, 120);
      case 'titre': {
        const el = creerElement('<div class="el texte" style="font-size:72px;font-weight:700;text-align:left">Titre</div>');
        inserer(el, 1680, 160); poser(el, { x: 120, y: 80 }); modifie(); rendrePanneau(); return;
      }
      case 'image': {
        const v = await dialogue('Ajouter une image', [{ nom: 'src', label: 'URL ou chemin', type: 'texte', valeur: '' }, { nom: 'fichier', label: 'ou téléverser', type: 'fichier', accept: 'image/*' }]);
        if (!v) return;
        const src = await sourceDepuis(v); if (!src) return;
        const el = creerElement('<img class="el image" alt="">');
        el.setAttribute('src', src);
        return inserer(el, 800, 600);
      }
      case 'forme': {
        const styles = { rectangle: 'background:#3b82f6', arrondi: 'background:#3b82f6;border-radius:24px', cercle: 'background:#3b82f6;border-radius:50%' };
        const el = creerElement('<div class="el forme" style="' + (styles[variante] || styles.rectangle) + '"></div>');
        return inserer(el, variante === 'cercle' ? 400 : 600, variante === 'cercle' ? 400 : 300);
      }
      case 'iframe': {
        const v = await dialogue('Ajouter une iframe', [{ nom: 'src', label: 'URL publique', type: 'texte', valeur: 'https://' }]);
        if (!v || !v.src) return;
        const el = creerElement('<iframe class="el iframe" title="Contenu embarqué"></iframe>');
        el.setAttribute('src', v.src);
        return inserer(el, 1600, 700);
      }
      case 'video': {
        const v = await dialogue('Ajouter une vidéo', [{ nom: 'src', label: 'URL ou chemin', type: 'texte', valeur: '' }, { nom: 'fichier', label: 'ou téléverser', type: 'fichier', accept: 'video/*' }]);
        if (!v) return;
        const src = await sourceDepuis(v); if (!src) return;
        const el = creerElement('<video class="el video" data-amphi="video" preload="metadata"></video>');
        el.setAttribute('src', src);
        return inserer(el, 1280, 720);
      }
      case 'lottie': {
        const v = await dialogue('Ajouter une animation Lottie', [{ nom: 'src', label: 'URL ou chemin du .json', type: 'texte', valeur: '' }, { nom: 'fichier', label: 'ou téléverser', type: 'fichier', accept: '.json,application/json' }, { nom: 'boucle', label: 'En boucle', type: 'case', valeur: true }]);
        if (!v) return;
        const src = await sourceDepuis(v); if (!src) return;
        const el = creerElement('<div class="el lottie" data-amphi="lottie"></div>');
        el.dataset.src = src;
        if (v.boucle) el.dataset.boucle = '1';
        return inserer(el, 600, 600);
      }
      case 'minuteur': {
        const v = await dialogue('Ajouter un minuteur', [{ nom: 'duree', label: 'Durée (s)', type: 'nombre', valeur: 60 }, { nom: 'auto', label: 'Démarre avec la diapositive', type: 'case', valeur: true }]);
        if (!v) return;
        const el = creerElement('<div class="el minuteur" data-amphi="minuteur"></div>');
        el.dataset.duree = String(Math.max(1, Math.round(v.duree || 60)));
        if (v.auto) el.dataset.auto = '1';
        return inserer(el, 400, 200);
      }
      case 'resultats':
      case 'nuage': {
        const liste = etat.questions.filter(q => type === 'nuage' ? q.type === 'nuage' : true);
        if (!liste.length) { alert(type === 'nuage' ? 'Aucune question de type « nuage » dans quiz.json.' : 'Aucune question dans quiz.json.'); return; }
        const v = await dialogue(type === 'nuage' ? 'Nuage de mots' : 'Résultats de quiz', [{ nom: 'question', label: 'Question', type: 'select', options: liste.map(q => ({ v: q.id, l: q.id + ' – ' + q.question })) }]);
        if (!v) return;
        const el = creerElement('<div class="el ' + type + '" data-amphi="resultats"></div>');
        el.dataset.question = v.question;
        const s = sectionCourante();
        if (!s.dataset.quiz) s.dataset.quiz = v.question; // la télécommande affichera le bloc d'administration
        inserer(el, 960, 720);
        rendreVignette(etat.courant);
        return;
      }
      case 'classement': {
        const v = await dialogue('Classement', [{ nom: 'nombre', label: 'Nombre de participants', type: 'nombre', valeur: 10 }]);
        if (!v) return;
        const el = creerElement('<div class="el classement" data-amphi="classement"></div>');
        el.dataset.nombre = String(Math.max(1, Math.round(v.nombre || 10)));
        return inserer(el, 960, 800);
      }
      case 'qr': return inserer(creerElement('<div class="el qr" data-amphi="qr"></div>'), 700, 320);
    }
  }
  /** Valeurs d'un dialogue « URL ou fichier » : téléverse le fichier s'il y en a un. */
  async function sourceDepuis(v) {
    if (v.fichier) {
      try { return await televerser(v.fichier); } catch (e) { alert('Téléversement impossible : ' + e.message); return null; }
    }
    return (v.src || '').trim() || null;
  }
  async function televerser(fichier) {
    const fd = new FormData();
    fd.append('fichier', fichier, fichier.name);
    const r = await apiJson('/api/deck/medias', { method: 'POST', body: fd });
    return r.chemin;
  }

  $('#barre-ajout').addEventListener('click', e => {
    const b = e.target.closest('button[data-ajout]');
    if (!b) return;
    fermerMenus();
    ajouterElement(b.dataset.ajout, b.dataset.variante);
  });
  $('#c-grille').addEventListener('change', e => { $('#grille').hidden = !e.target.checked; });
  // Calque « Zone caméra OBS » : activé par défaut, choix mémorisé.
  try { $('#c-camera').checked = localStorage.getItem('editeur.camera') !== '0'; } catch (e) { /* stockage indisponible */ }
  $('#c-camera').addEventListener('change', e => {
    try { localStorage.setItem('editeur.camera', e.target.checked ? '1' : '0'); } catch (err) { /* stockage indisponible */ }
    rafraichirCalque();
    rendreListe();
  });

  // Menus déroulants génériques
  function fermerMenus() { $$('.menu.ouvert').forEach(m => m.classList.remove('ouvert')); }
  document.addEventListener('click', e => {
    const menu = e.target.closest('.menu');
    if (menu && e.target.closest('button') && e.target.closest('button').parentElement === menu) {
      const ouvert = menu.classList.contains('ouvert');
      fermerMenus();
      if (!ouvert) menu.classList.add('ouvert');
      return;
    }
    if (!e.target.closest('.menu-contenu')) fermerMenus();
    else setTimeout(fermerMenus, 0);
  });

  // ======================================================================
  // Opérations sur les diapositives
  // ======================================================================
  function ajouterSection(type, apres, questionId) {
    let s;
    if (type === 'quiz') s = creerElement('<section data-quiz="' + echap(questionId) + '"></section>');
    else if (type === 'titre') s = creerElement('<section class="libre"><div class="el texte" data-id="e1" style="left:120px;top:360px;width:1680px;height:200px;font-size:96px;font-weight:700;text-align:center">Titre</div><div class="el texte" data-id="e2" style="left:120px;top:600px;width:1680px;height:100px;font-size:48px;color:#555;text-align:center">Sous-titre</div></section>');
    else s = creerElement('<section class="libre"></section>');
    s.dataset.id = nouvelIdSection();
    const i = Math.min(etat.sections.length, (apres ?? etat.courant) + 1);
    etat.sections.splice(i, 0, s);
    etat.courant = i;
    etat.selection = null;
    changementStructurel();
    return s;
  }
  function dupliquerSection() {
    terminerEdition(true);
    const c = sectionCourante().cloneNode(true);
    c.dataset.id = nouvelIdSection();
    delete c.dataset.obs; // une seule diapositive démarre / arrête l'enregistrement OBS
    etat.sections.splice(etat.courant + 1, 0, c);
    etat.courant++;
    etat.selection = null;
    changementStructurel();
  }
  /** Fenêtre de confirmation de l'éditeur (miniature de la diapositive) ; résout true sur « Supprimer ». */
  function confirmerSuppression(i) {
    return new Promise(resoudre => {
      const s = etat.sections[i];
      const fin = ok => { d.close(); d.remove(); resoudre(ok); };
      const annuler = h('button', { onclick: () => fin(false) }, 'Annuler');
      const supprimer = h('button', { class: 'danger plein', onclick: () => fin(true) }, '🗑 Supprimer');
      const d = h('dialog', { class: 'confirmation-suppression', 'aria-label': 'Supprimer la diapositive' },
        h('h3', {}, 'Supprimer la diapositive ' + (i + 1) + ' ?'),
        h('p', {}, titreCourt(s)),
        h('div', { class: 'apercu-suppression' }, vignette(s)),
        h('p', { class: 'aide' }, 'Elle disparaît de la présentation à l\'enregistrement. « Annuler » (Ctrl+Z) la rétablit tant que la page est ouverte.'),
        h('div', { class: 'actions' }, annuler, supprimer));
      d.addEventListener('cancel', e => { e.preventDefault(); fin(false); });
      document.body.append(d);
      d.showModal();
      const v = d.querySelector('.vignette'); // échelle propre à la fenêtre (celle de la liste est plus petite)
      if (v && v.clientWidth) d.style.setProperty('--echelle-vignette', v.clientWidth / SCENE_L);
      supprimer.focus();
    });
  }
  async function supprimerSection() {
    if (!(await confirmerSuppression(etat.courant))) return;
    terminerEdition(false);
    historique.passe.push({ i: etat.courant, html: sectionPropre(sectionCourante()).outerHTML, supprimee: true });
    etat.sections.splice(etat.courant, 1);
    if (!etat.sections.length) { ajouterSection('libre', -1); return; }
    etat.courant = Math.min(etat.courant, etat.sections.length - 1);
    etat.selection = null;
    // Pas de changementStructurel() : il viderait l'historique, et la suppression doit pouvoir s'annuler.
    while (historique.passe.length > LIMITE_HISTORIQUE) historique.passe.shift();
    historique.futur = [];
    marquer();
    rendreTout();
  }
  // ---- Clic droit sur une diapositive de la liste : dupliquer, copier, coller, monter, descendre, supprimer ----
  // Le presse-papiers est gardé dans le navigateur : on peut coller dans une autre présentation.
  const CLE_PRESSE_PAPIERS = 'amphi-editeur-diapositive';
  function lirePressePapiers() { try { return localStorage.getItem(CLE_PRESSE_PAPIERS); } catch (e) { return etat.pressePapiers || null; } }
  function copierSection(i) {
    const html = sectionPropre(etat.sections[i]).outerHTML;
    etat.pressePapiers = html;
    try { localStorage.setItem(CLE_PRESSE_PAPIERS, html); } catch (e) { /* stockage indisponible : copie gardée pour cette page */ }
  }
  function collerSection() {
    const html = lirePressePapiers();
    if (!html) return;
    const { sections } = parserDeck(html);
    if (!sections.length) return;
    terminerEdition(true);
    const c = sections[0];
    c.dataset.id = nouvelIdSection();
    delete c.dataset.obs;
    etat.sections.splice(etat.courant + 1, 0, c);
    etat.courant++;
    etat.selection = null;
    changementStructurel();
  }
  function fermerMenuDiapo() {
    const m = document.getElementById('menu-diapo');
    if (m) m.remove();
    document.removeEventListener('keydown', echapMenuDiapo);
  }
  function echapMenuDiapo(e) { if (e.key === 'Escape') fermerMenuDiapo(); }
  function menuDiapo(e, i) {
    e.preventDefault();
    fermerMenuDiapo();
    if (i !== etat.courant) { terminerEdition(true); etat.courant = i; etat.selection = null; rendreTout(); }
    const dernier = etat.sections.length - 1;
    const entrees = [
      ['⧉ Dupliquer', dupliquerSection],
      ['📋 Copier', () => copierSection(i)],
      ['📥 Coller après', collerSection, !lirePressePapiers()],
      null,
      ['↑ Monter', () => deplacerSection(i, i - 1), i === 0],
      ['↓ Descendre', () => deplacerSection(i, i + 1), i === dernier],
      null,
      ['🗑 Supprimer', supprimerSection, false, 'danger'],
    ];
    const menu = h('div', { id: 'menu-diapo', class: 'menu-diapo', role: 'menu', 'aria-label': 'Diapositive ' + (i + 1) },
      h('div', { class: 'menu-diapo-titre' }, 'Diapositive ' + (i + 1)),
      entrees.map(x => x ? h('button', { role: 'menuitem', class: x[3] || '', disabled: !!x[2], onclick: () => { fermerMenuDiapo(); x[1](); } }, x[0]) : h('hr')));
    document.body.append(menu);
    const r = menu.getBoundingClientRect();
    menu.style.left = Math.min(e.clientX, innerWidth - r.width - 8) + 'px';
    menu.style.top = Math.min(e.clientY, innerHeight - r.height - 8) + 'px';
    const premier = menu.querySelector('button:not(:disabled)');
    if (premier) premier.focus();
    setTimeout(() => document.addEventListener('click', fermerMenuDiapo, { once: true }));
    document.addEventListener('keydown', echapMenuDiapo);
  }

  function deplacerSection(de, vers) {
    terminerEdition(true);
    const [s] = etat.sections.splice(de, 1);
    etat.sections.splice(vers, 0, s);
    etat.courant = vers;
    changementStructurel();
  }
  /** Ajout/suppression/réordonnancement : l'historique par section devient incohérent, on le vide. */
  function changementStructurel() {
    historique.passe = []; historique.futur = [];
    marquer();
    rendreTout();
  }
  $('#b-dupliquer-diapo').onclick = dupliquerSection;
  $('#b-supprimer-diapo').onclick = supprimerSection;
  $('#b-monter-diapo').onclick = () => { if (etat.courant > 0) deplacerSection(etat.courant, etat.courant - 1); };
  $('#b-descendre-diapo').onclick = () => { if (etat.courant < etat.sections.length - 1) deplacerSection(etat.courant, etat.courant + 1); };
  $('#menu-ajouter-diapo').addEventListener('click', async e => {
    const b = e.target.closest('button[data-type]');
    if (!b) return;
    if (b.dataset.type === 'quiz') {
      if (!etat.questions.length) { alert('Aucune question dans quiz.json : créez-en dans l\'onglet Questions.'); return; }
      const v = await dialogue('Diapositive de quiz', [{ nom: 'question', label: 'Question', type: 'select', options: etat.questions.map(q => ({ v: q.id, l: q.id + ' – ' + q.question })) }]);
      if (!v) return;
      ajouterSection('quiz', etat.courant, v.question);
    } else ajouterSection(b.dataset.type, etat.courant);
  });

  // ======================================================================
  // Conversions (flux → libre, quiz → libre)
  // ======================================================================
  function hauteurEstimee(enfant) {
    const t = enfant.tagName;
    if (t === 'H1') return 160;
    if (t === 'H2') return 120;
    if (t === 'H3') return 100;
    if (t === 'UL' || t === 'OL') return 20 + 60 * Math.max(1, enfant.querySelectorAll('li').length);
    if (t === 'IMG') return num(enfant.getAttribute('height'), 500);
    if (t === 'IFRAME') return num(enfant.getAttribute('height'), 700);
    return 60 + 50 * Math.ceil((enfant.textContent || '').length / 70);
  }
  function convertirEnLibre() {
    const s = sectionCourante();
    pousserHistorique();
    const enfants = Array.from(s.children).filter(c => !c.matches('aside.notes'));
    let y = 80, n = 0;
    enfants.forEach(c => {
      const hh = hauteurEstimee(c);
      let el;
      if (c.tagName === 'IMG') { el = c; el.removeAttribute('width'); el.removeAttribute('height'); el.className = 'el image'; }
      else if (c.tagName === 'IFRAME') { el = c; el.removeAttribute('width'); el.removeAttribute('height'); el.className = 'el iframe'; }
      else { el = document.createElement('div'); el.className = 'el texte'; el.append(c.cloneNode(true)); c.replaceWith(el); }
      el.dataset.id = 'e' + (++n);
      poser(el, { x: 120, y, l: 1680, h: hh });
      if (c.tagName === 'H1' || c.tagName === 'H2' || c.tagName === 'H3') el.style.textAlign = 'center';
      y += hh + 30;
    });
    s.classList.add('libre');
    Array.from(s.childNodes).forEach(n => { if (n.nodeType === Node.TEXT_NODE && !n.data.trim()) n.remove(); });
    notesEnDernier(s);
    etat.selection = null;
    marquer();
    rendreTout();
  }
  function convertirQuizEnLibre() {
    const s = sectionCourante();
    const q = questionParId(s.dataset.quiz);
    pousserHistorique();
    const texte = document.createElement('div');
    texte.className = 'el texte'; texte.dataset.id = 'e1';
    texte.style.cssText = 'left:80px;top:80px;width:760px;height:600px;font-size:48px;text-align:left';
    texte.append(h('h2', {}, q ? q.question : 'Question ' + s.dataset.quiz));
    if (q && Array.isArray(q.options)) texte.append(h('ol', { style: 'list-style:upper-alpha' }, q.options.map(o => h('li', {}, String(o)))));
    const res = creerElement('<div class="el resultats" data-amphi="resultats" style="left:880px;top:80px;width:960px;height:920px"></div>');
    res.dataset.id = 'e2'; res.dataset.question = s.dataset.quiz;
    const qr = creerElement('<div class="el qr" data-amphi="qr" style="left:80px;top:720px;width:760px;height:280px"></div>');
    qr.dataset.id = 'e3';
    const notes = s.querySelector(':scope > aside.notes');
    Array.from(s.childNodes).forEach(n => { if (n !== notes) n.remove(); });
    s.prepend(texte, res, qr);
    s.classList.add('libre');
    etat.selection = null;
    marquer();
    rendreTout();
  }

  // ======================================================================
  // Panneau de propriétés (droite)
  // ======================================================================
  function champ(label, controle, classe) { return h('div', { class: 'champ ' + (classe || '') }, h('label', {}, label), controle); }
  /** Champ numérique lié : lire() → valeur, ecrire(v) applique. */
  function champNombre(label, lire, ecrire, opts = {}) {
    const inp = h('input', { type: 'number', value: lire(), step: opts.step || 1, min: opts.min, max: opts.max });
    inp.addEventListener('focus', () => { gesteEnCours = false; });
    inp.addEventListener('input', () => { if (inp.value === '') return; debutGeste(); ecrire(parseFloat(inp.value)); rafraichirSelection(); rafraichirCalque(); });
    inp.addEventListener('change', () => { if (gesteEnCours) finGeste(); });
    inp.dataset.lie = opts.cle || '';
    return champ(label, inp, opts.classe);
  }
  function champTexte(label, lire, ecrire, opts = {}) {
    const inp = h('input', { type: 'text', value: lire(), placeholder: opts.placeholder || '' });
    inp.addEventListener('focus', () => { gesteEnCours = false; });
    inp.addEventListener('input', () => { debutGeste(); ecrire(inp.value); rafraichirSelection(); rafraichirCalque(); });
    inp.addEventListener('change', () => { if (gesteEnCours) finGeste(); });
    return opts.sansLabel ? inp : champ(label, inp);
  }
  function champSelect(label, lire, options, ecrire) {
    const sel = h('select', {}, options.map(o => h('option', { value: o.v, selected: o.v === lire() }, o.l)));
    sel.addEventListener('change', () => { pousserHistorique(); ecrire(sel.value); modifie(); });
    return champ(label, sel);
  }
  function champCase(label, lire, ecrire) {
    const inp = h('input', { type: 'checkbox', checked: !!lire() });
    inp.addEventListener('change', () => { pousserHistorique(); ecrire(inp.checked); modifie(); });
    return champ(label, h('div', {}, inp));
  }
  /** Couleur : pastille + champ texte (accepte toute valeur CSS, vide = aucune). */
  function champCouleur(label, lire, ecrire) {
    const texte = h('input', { type: 'text', value: versHex(lire()) || lire() || '', placeholder: 'aucune' });
    const pastille = h('input', { type: 'color', value: versHex(lire()) || '#000000' });
    pastille.addEventListener('input', () => { debutGeste(); texte.value = pastille.value; ecrire(pastille.value); });
    pastille.addEventListener('change', () => { if (gesteEnCours) finGeste(); });
    texte.addEventListener('input', () => { debutGeste(); ecrire(texte.value); const hx = versHex(texte.value); if (hx) pastille.value = hx; });
    texte.addEventListener('change', () => { if (gesteEnCours) finGeste(); });
    const effacer = h('button', { type: 'button', title: 'Aucune couleur', onclick: () => { pousserHistorique(); texte.value = ''; ecrire(''); modifie(); } }, '×');
    return champ(label, h('div', { class: 'rangee' }, pastille, texte, effacer));
  }
  /** Champ « source » avec bouton Téléverser. */
  function champSource(label, lire, ecrire, accept) {
    const texte = champTexte(label, lire, ecrire, { sansLabel: true });
    const fichier = h('input', { type: 'file', accept, hidden: true });
    fichier.addEventListener('change', async () => {
      if (!fichier.files[0]) return;
      try {
        const chemin = await televerser(fichier.files[0]);
        pousserHistorique(); ecrire(chemin); texte.value = chemin; modifie(); rendreScene();
      } catch (e) { alert('Téléversement impossible : ' + e.message); }
    });
    return champ(label, h('div', { class: 'rangee' }, texte, h('button', { type: 'button', onclick: () => fichier.click() }, 'Téléverser…'), fichier));
  }
  function rafraichirChampsGeometrie() {
    const el = elSelectionne();
    if (!el) return;
    const b = boite(el);
    const v = { x: b.x, y: b.y, l: b.l, h: b.h, rot: b.rot };
    $$('#proprietes input[data-lie]').forEach(inp => { if (inp.dataset.lie in v && document.activeElement !== inp) inp.value = Math.round(v[inp.dataset.lie] * 10) / 10; });
  }

  function rendrePanneau() {
    const p = $('#proprietes');
    p.textContent = '';
    const s = sectionCourante();
    if (!s) return;
    const el = estLibre(s) ? elSelectionne() : null;
    if (el) p.append(...panneauElement(el, s));
    else if (estLibre(s)) p.append(...panneauSection(s));
    else if (estQuiz(s)) p.append(...panneauQuiz(s));
    else p.append(...panneauFlux(s));
  }

  // ---- Panneau d'un élément ----
  function panneauElement(el, s) {
    const type = typeEl(el);
    const st = el.style;
    const blocs = [];
    blocs.push(h('h3', {}, 'Élément ' + type + ' · ' + el.dataset.id));
    blocs.push(h('div', { class: 'alerte', id: 'alerte-camera', title: 'chevauche la zone caméra', hidden: !el.classList.contains('ed-chevauche') }, '🎥 Chevauche la zone caméra OBS (bas à droite) : déplacez ou réduisez l\'élément'));
    blocs.push(h('div', { class: 'boutons actions' },
      h('button', { onclick: dupliquerElement, title: 'Ctrl+D' }, 'Dupliquer'),
      h('button', { class: 'danger', onclick: supprimerElement, title: 'Suppr' }, 'Supprimer'),
      h('button', { onclick: () => ordreZ(-1), title: 'Reculer dans l\'ordre' }, '↓ Reculer'),
      h('button', { onclick: () => ordreZ(1), title: 'Avancer dans l\'ordre' }, '↑ Avancer')));
    // Géométrie
    const g = h('div', { class: 'grille-xy' },
      champNombre('X', () => boite(el).x, v => poser(el, { x: v }), { cle: 'x' }),
      champNombre('Y', () => boite(el).y, v => poser(el, { y: v }), { cle: 'y' }),
      champNombre('L', () => boite(el).l, v => poser(el, { l: v }), { cle: 'l', min: 10 }),
      champNombre('H', () => boite(el).h, v => poser(el, { h: v }), { cle: 'h', min: 10 }));
    blocs.push(h('h4', {}, 'Position et taille'), g);
    blocs.push(champNombre('Rotation (°)', () => boite(el).rot, v => poser(el, { rot: v }), { cle: 'rot', step: 1 }));
    blocs.push(champNombre('Opacité', () => st.opacity === '' ? 1 : num(st.opacity, 1), v => { if (v >= 1) st.removeProperty('opacity'); else st.opacity = Math.max(0, v); }, { step: 0.05, min: 0, max: 1 }));
    blocs.push(h('div', { class: 'boutons' },
      ...[['gauche', '⇤'], ['centreH', '↔'], ['droite', '⇥'], ['haut', '⤒'], ['centreV', '↕'], ['bas', '⤓']].map(([m, l]) => h('button', { onclick: () => aligner(m), title: 'Aligner : ' + m }, l))));
    if (type === 'texte' || type === 'forme') blocs.push(h('button', { onclick: ajusterAuTexte }, 'Ajuster la hauteur au texte'));

    // Propriétés par type
    if (type === 'texte' || type === 'forme') {
      blocs.push(h('h4', {}, 'Texte'));
      blocs.push(champNombre('Taille (px)', () => num(st.fontSize) || num(getComputedStyle(el).fontSize, 40), v => { st.fontSize = px(v); }, { min: 8 }));
      blocs.push(champCouleur('Couleur', () => st.color, v => { if (v) st.color = v; else st.removeProperty('color'); }));
      blocs.push(champSelect('Alignement', () => st.textAlign || '', [{ v: '', l: 'défaut' }, { v: 'left', l: 'gauche' }, { v: 'center', l: 'centré' }, { v: 'right', l: 'droite' }, { v: 'justify', l: 'justifié' }], v => { if (v) st.textAlign = v; else st.removeProperty('text-align'); }));
      blocs.push(champNombre('Interligne', () => num(st.lineHeight, 1.3), v => { st.lineHeight = String(v); }, { step: 0.1, min: 0.5 }));
      blocs.push(h('p', { class: 'aide' }, 'Double-clic sur l\'élément pour éditer le texte en place.'));
    }
    if (type === 'forme') {
      blocs.push(h('h4', {}, 'Forme'));
      blocs.push(champCouleur('Fond', () => st.background || st.backgroundColor, v => { if (v) st.background = v; else { st.removeProperty('background'); st.removeProperty('background-color'); } }));
      blocs.push(champNombre('Bordure (px)', () => num(st.borderWidth), v => { st.borderWidth = px(v); if (!st.borderStyle) st.borderStyle = 'solid'; if (v === 0) { st.removeProperty('border'); } }, { min: 0 }));
      blocs.push(champCouleur('Couleur bordure', () => st.borderColor, v => { if (v) { st.borderColor = v; if (!st.borderStyle) st.borderStyle = 'solid'; if (!st.borderWidth) st.borderWidth = '2px'; } else st.removeProperty('border-color'); }));
      blocs.push(champNombre('Rayon (px)', () => num(st.borderRadius), v => { if (v) st.borderRadius = px(v); else st.removeProperty('border-radius'); }, { min: 0 }));
    }
    if (type === 'image' || type === 'video') {
      blocs.push(h('h4', {}, type === 'image' ? 'Image' : 'Vidéo'));
      blocs.push(champSource('Source', () => el.getAttribute('src') || '', v => el.setAttribute('src', v), type === 'image' ? 'image/*' : 'video/*'));
      blocs.push(champSelect('Ajustement', () => st.objectFit || '', [{ v: '', l: 'défaut (contenir)' }, { v: 'contain', l: 'contenir' }, { v: 'cover', l: 'couvrir' }, { v: 'fill', l: 'étirer' }, { v: 'none', l: 'aucun' }], v => { if (v) st.objectFit = v; else st.removeProperty('object-fit'); }));
    }
    if (type === 'iframe') {
      blocs.push(h('h4', {}, 'Iframe'));
      blocs.push(champTexte('Source', () => el.getAttribute('src') || '', v => el.setAttribute('src', v)));
      blocs.push(h('p', { class: 'aide' }, 'URL publique et sans état uniquement.'));
    }
    if (type === 'lottie') {
      blocs.push(h('h4', {}, 'Lottie'));
      blocs.push(champSource('Fichier .json', () => el.dataset.src || '', v => { el.dataset.src = v; }, '.json,application/json'));
      blocs.push(champCase('Boucle', () => el.dataset.boucle === '1', v => { if (v) el.dataset.boucle = '1'; else delete el.dataset.boucle; }));
    }
    if (type === 'minuteur') {
      blocs.push(h('h4', {}, 'Minuteur'));
      blocs.push(champNombre('Durée (s)', () => num(el.dataset.duree, 60), v => { el.dataset.duree = String(Math.max(1, Math.round(v))); }, { min: 1 }));
      blocs.push(champCase('Démarre avec la diapositive', () => el.dataset.auto === '1', v => { if (v) el.dataset.auto = '1'; else delete el.dataset.auto; }));
    }
    if (type === 'resultats' || type === 'nuage') {
      blocs.push(h('h4', {}, type === 'nuage' ? 'Nuage de mots' : 'Résultats'));
      const liste = etat.questions.filter(q => type === 'nuage' ? q.type === 'nuage' : true);
      const options = liste.map(q => ({ v: q.id, l: q.id + ' – ' + q.question }));
      if (!liste.some(q => q.id === el.dataset.question)) options.unshift({ v: el.dataset.question || '', l: (el.dataset.question || '(aucune)') + ' (inconnue)' });
      blocs.push(champSelect('Question', () => el.dataset.question || '', options, v => { el.dataset.question = v; }));
    }
    if (type === 'classement') {
      blocs.push(h('h4', {}, 'Classement'));
      blocs.push(champNombre('Nombre', () => num(el.dataset.nombre, 10), v => { el.dataset.nombre = String(Math.max(1, Math.round(v))); }, { min: 1 }));
      // Cérémonie (docs/seance.md, D) : data-ceremonie="1" sur l'élément + un déclencheur invisible (fragment) dans la section.
      blocs.push(champCase('Cérémonie animée (déclenchée par Suivant)', () => el.dataset.ceremonie === '1', v => {
        const declencheurs = Array.from(s.querySelectorAll(':scope > .el[data-amphi="ceremonie"]'));
        if (v) {
          el.dataset.ceremonie = '1';
          if (!declencheurs.length) {
            const d = creerElement('<div class="el declencheur fragment" data-amphi="ceremonie" style="left:0;top:0;width:1px;height:1px"></div>');
            d.dataset.id = nouvelIdElement(s);
            const notes = s.querySelector(':scope > aside.notes');
            if (notes) notes.before(d); else s.append(d);
          }
        } else {
          delete el.dataset.ceremonie;
          if (!s.querySelector(':scope > .el[data-amphi="classement"][data-ceremonie="1"]')) declencheurs.forEach(d => d.remove());
        }
        rendreScene();
      }));
      blocs.push(h('p', { class: 'aide' }, 'Défilé des rangs, podium puis tableau final, lancés par « Suivant » sur la télécommande.'));
    }

    // Animation (fragment)
    blocs.push(h('h4', {}, 'Animation'));
    const estFragment = el.classList.contains('fragment');
    blocs.push(champCase('Animer (fragment)', () => estFragment, v => {
      if (v) { el.classList.add('fragment'); if (!ANIMATIONS.some(a => el.classList.contains(a))) el.classList.add('fade-in'); }
      else { el.classList.remove('fragment', ...ANIMATIONS); el.removeAttribute('data-fragment-index'); }
      rendrePanneau();
    }));
    if (estFragment) {
      blocs.push(champSelect('Effet', () => ANIMATIONS.find(a => el.classList.contains(a)) || 'fade-in', ANIMATIONS.map(a => ({ v: a, l: a })), v => { el.classList.remove(...ANIMATIONS); el.classList.add(v); }));
      const index = h('input', { type: 'number', min: 0, value: el.dataset.fragmentIndex ?? '' , placeholder: 'auto' });
      index.addEventListener('change', () => { pousserHistorique(); if (index.value === '') el.removeAttribute('data-fragment-index'); else el.dataset.fragmentIndex = String(Math.max(0, parseInt(index.value, 10) || 0)); modifie(); });
      const decaler = d => { pousserHistorique(); const i = Math.max(0, (parseInt(el.dataset.fragmentIndex, 10) || 0) + d); el.dataset.fragmentIndex = String(i); index.value = i; modifie(); };
      blocs.push(champ('Index', h('div', { class: 'rangee' }, index, h('button', { type: 'button', title: 'Un cran avant', onclick: () => decaler(-1) }, '− avant'), h('button', { type: 'button', title: 'Un cran après', onclick: () => decaler(1) }, 'après +'))));
    }
    blocs.push(h('button', { onclick: apercuAnimation }, '▶ Aperçu de l\'animation'));

    // HTML brut
    blocs.push(h('h4', {}, 'HTML de l\'élément'));
    blocs.push(zoneHtml(() => sectionPropre(s).querySelector('[data-id="' + el.dataset.id + '"]').outerHTML, html => {
      const nouveau = creerElement(html);
      if (!nouveau) throw new Error('HTML vide');
      if (!nouveau.classList.contains('el')) nouveau.classList.add('el');
      if (!nouveau.dataset.id) nouveau.dataset.id = el.dataset.id;
      pousserHistorique();
      el.replaceWith(nouveau);
      normaliserIdsElements(s);
      etat.selection = nouveau.dataset.id;
      modifie();
      rendreScene();
      rendrePanneau();
    }));
    return blocs;
  }
  /** Textarea de HTML brut avec Appliquer / Annuler. */
  function zoneHtml(lire, appliquer) {
    const ta = h('textarea', { rows: 8, spellcheck: 'false' });
    ta.value = lire();
    const erreur = h('div', { class: 'etat erreur' });
    const bAppliquer = h('button', { class: 'principal', onclick: () => { try { appliquer(ta.value); } catch (e) { erreur.textContent = e.message; } } }, 'Appliquer');
    const bAnnuler = h('button', { onclick: () => { ta.value = lire(); erreur.textContent = ''; } }, 'Annuler');
    return h('div', { class: 'champ large' }, ta, h('div', { class: 'boutons' }, bAppliquer, bAnnuler), erreur);
  }

  /** Aperçu : masque tous les fragments puis les révèle dans l'ordre des index (400 ms de transition). */
  let apercuEnCours = false;
  async function apercuAnimation() {
    const s = sectionCourante();
    if (apercuEnCours || !s) return;
    const frags = Array.from(s.querySelectorAll('.fragment'));
    if (!frags.length) { alert('Aucun fragment dans cette diapositive.'); return; }
    apercuEnCours = true;
    // Même règle que reveal : les fragments sans index reçoivent les index suivants dans l'ordre du DOM.
    let prochain = 0;
    frags.forEach(f => { const i = parseInt(f.dataset.fragmentIndex, 10); if (Number.isInteger(i)) prochain = Math.max(prochain, i + 1); });
    const indices = frags.map(f => { const i = parseInt(f.dataset.fragmentIndex, 10); return Number.isInteger(i) ? i : prochain++; });
    frags.forEach(f => f.classList.add('ed-cache'));
    await attendre(500);
    for (const i of [...new Set(indices)].sort((a, b) => a - b)) {
      frags.forEach((f, k) => { if (indices[k] === i) f.classList.remove('ed-cache'); });
      await attendre(700);
    }
    frags.forEach(f => f.classList.remove('ed-cache'));
    apercuEnCours = false;
  }

  // ---- Panneau de la section (rien de sélectionné) ----
  function panneauSection(s) {
    const blocs = [h('h3', {}, 'Diapositive ' + (etat.courant + 1) + ' (libre)')];
    blocs.push(champTexte('Identifiant', () => s.dataset.id, v => { if (v.trim()) s.dataset.id = v.trim(); }));
    blocs.push(champSelect('Transition', () => s.dataset.transition || '', [{ v: '', l: 'défaut' }, ...TRANSITIONS.map(t => ({ v: t, l: t }))], v => { if (v) s.dataset.transition = v; else delete s.dataset.transition; rendreScene(); }));
    blocs.push(champCouleur('Fond', () => s.dataset.backgroundColor || '', v => { if (v) s.dataset.backgroundColor = v; else delete s.dataset.backgroundColor; rendreScene(); }));
    blocs.push(champSource('Image de fond', () => s.dataset.backgroundImage || '', v => { if (v) s.dataset.backgroundImage = v; else delete s.dataset.backgroundImage; rendreScene(); }, 'image/*'));
    blocs.push(champObs(s));
    blocs.push(...blocsCommunsSection(s));
    return blocs;
  }
  /** Champ « OBS » : pose ou retire data-obs (démarrer / arrêter l'enregistrement à l'arrivée sur la diapositive). */
  function champObs(s) {
    return champSelect('OBS', () => s.dataset.obs || '', OBS_OPTIONS, v => {
      if (v) etat.sections.forEach(x => { if (x !== s && x.dataset.obs === v) delete x.dataset.obs; });  // une seule diapositive par action
      if (v) s.dataset.obs = v; else delete s.dataset.obs;
      rendreListe(); // bordure rouge et pastilles de la liste
    });
  }
  function blocsCommunsSection(s) {
    const blocs = [h('h4', {}, 'Notes de l\'orateur')];
    const notes = h('textarea', { rows: 4 });
    notes.value = (s.querySelector(':scope > aside.notes') || {}).innerHTML || '';
    notes.addEventListener('focus', () => { gesteEnCours = false; });
    notes.addEventListener('input', () => {
      debutGeste();
      let a = s.querySelector(':scope > aside.notes');
      if (!notes.value.trim()) { if (a) a.remove(); return; }
      if (!a) { a = document.createElement('aside'); a.className = 'notes'; s.append(a); }
      a.innerHTML = notes.value;
    });
    notes.addEventListener('change', () => { if (gesteEnCours) finGeste(); });
    blocs.push(h('div', { class: 'champ large' }, notes));
    blocs.push(h('h4', {}, 'HTML de la diapositive'));
    blocs.push(zoneHtml(() => serialiserSection(s), html => {
      const { sections } = parserDeck(html);
      if (!sections.length) throw new Error('Il faut exactement une <section>.');
      pousserHistorique();
      const n = sections[0];
      if (!n.dataset.id) n.dataset.id = s.dataset.id;
      normaliserIdsElements(n);
      etat.sections[etat.courant] = n;
      etat.selection = null;
      marquer();
      rendreTout();
    }));
    return blocs;
  }

  // ---- Panneau d'une section en flux ----
  function panneauFlux(s) {
    const blocs = [h('h3', {}, 'Diapositive ' + (etat.courant + 1) + ' (flux)')];
    blocs.push(h('button', { class: 'principal', onclick: convertirEnLibre }, 'Convertir en section libre'));
    blocs.push(champSelect('Transition', () => s.dataset.transition || '', [{ v: '', l: 'défaut' }, ...TRANSITIONS.map(t => ({ v: t, l: t }))], v => { if (v) s.dataset.transition = v; else delete s.dataset.transition; }));
    blocs.push(champCouleur('Fond', () => s.dataset.backgroundColor || '', v => { if (v) s.dataset.backgroundColor = v; else delete s.dataset.backgroundColor; rendreScene(); }));
    blocs.push(champObs(s));
    blocs.push(h('h4', {}, 'Éléments'));
    const enfants = Array.from(s.children).filter(c => !c.matches('aside.notes'));
    enfants.forEach((c, i) => {
      const ta = h('textarea', { rows: 3, spellcheck: 'false' });
      ta.value = c.outerHTML;
      ta.addEventListener('change', () => {
        const n = creerElement(ta.value);
        if (!n) { c.remove(); } else { c.replaceWith(n); }
        pousserHistorique(); modifie(); rendreScene(); rendrePanneau();
      });
      const extrait = (c.textContent || c.getAttribute('src') || '').trim().slice(0, 40);
      blocs.push(h('div', { class: 'enfant' },
        h('div', { class: 'entete' }, h('code', {}, '<' + c.tagName.toLowerCase() + '>'), h('span', {}, extrait),
          h('button', { title: 'Monter', disabled: i === 0, onclick: () => { pousserHistorique(); enfants[i - 1].before(c); modifie(); rendrePanneau(); } }, '↑'),
          h('button', { title: 'Descendre', disabled: i === enfants.length - 1, onclick: () => { pousserHistorique(); enfants[i + 1].after(c); modifie(); rendrePanneau(); } }, '↓'),
          h('button', { title: 'Supprimer', class: 'danger', onclick: () => { pousserHistorique(); c.remove(); modifie(); rendrePanneau(); } }, '×')),
        ta));
    });
    const ajout = h('select', { class: 'ajout-flux' }, h('option', { value: '' }, '+ Ajouter un élément…'),
      ...[['titre', 'Titre'], ['paragraphe', 'Paragraphe'], ['liste', 'Liste'], ['image', 'Image'], ['iframe', 'Iframe']].map(([v, l]) => h('option', { value: v }, l)));
    ajout.addEventListener('change', async () => {
      const type = ajout.value; ajout.value = '';
      if (!type) return;
      let html = { titre: '<h2>Titre</h2>', paragraphe: '<p>Paragraphe</p>', liste: '<ul><li>Premier point</li><li>Deuxième point</li></ul>' }[type];
      if (type === 'image') {
        const v = await dialogue('Ajouter une image', [{ nom: 'src', label: 'URL ou chemin', type: 'texte' }, { nom: 'fichier', label: 'ou téléverser', type: 'fichier', accept: 'image/*' }]);
        if (!v) return; const src = await sourceDepuis(v); if (!src) return;
        const img = document.createElement('img'); img.src = src; img.alt = ''; html = img.outerHTML;
      }
      if (type === 'iframe') {
        const v = await dialogue('Ajouter une iframe', [{ nom: 'src', label: 'URL publique', type: 'texte', valeur: 'https://' }]);
        if (!v || !v.src) return;
        const f = document.createElement('iframe'); f.src = v.src; f.width = '1600'; f.height = '700'; f.title = 'Contenu embarqué'; html = f.outerHTML;
      }
      pousserHistorique();
      const n = creerElement(html);
      const notes = s.querySelector(':scope > aside.notes');
      if (notes) notes.before(n); else s.append(n);
      modifie(); rendreScene(); rendrePanneau();
    });
    blocs.push(ajout);
    blocs.push(...blocsCommunsSection(s));
    return blocs;
  }

  // ---- Panneau d'une section de quiz ----
  function panneauQuiz(s) {
    const q = questionParId(s.dataset.quiz);
    const blocs = [h('h3', {}, 'Diapositive ' + (etat.courant + 1) + ' (quiz)')];
    const options = etat.questions.map(x => ({ v: x.id, l: x.id + ' – ' + x.question }));
    if (!q) options.unshift({ v: s.dataset.quiz, l: s.dataset.quiz + ' (absente de quiz.json)' });
    blocs.push(champSelect('Question', () => s.dataset.quiz, options, v => { s.dataset.quiz = v; rendreScene(); rendreVignette(etat.courant); rendrePanneau(); }));
    blocs.push(champObs(s));
    if (q) {
      blocs.push(h('div', { class: 'enfant' }, h('div', {}, h('b', {}, q.question)), h('div', { class: 'aide' }, 'type : ' + (q.type || 'qcm') + (q.chrono ? ' · chrono ' + q.chrono + ' s' : '')),
        Array.isArray(q.options) ? h('ol', {}, q.options.map(o => h('li', {}, String(o)))) : null));
    }
    blocs.push(h('button', { onclick: () => { basculerVue('questions'); const i = etat.questions.indexOf(q); if (i >= 0) { etat.questionCourante = i; rendreQuestions(); } } }, 'Modifier la question…'));
    blocs.push(h('button', { class: 'principal', onclick: convertirQuizEnLibre }, 'Transformer en section libre'));
    blocs.push(h('p', { class: 'aide' }, 'La section libre obtenue garde data-quiz et contient le titre de la question, un bloc résultats et un QR code, dans la disposition du runtime.'));
    blocs.push(champSelect('Transition', () => s.dataset.transition || '', [{ v: '', l: 'défaut' }, ...TRANSITIONS.map(t => ({ v: t, l: t }))], v => { if (v) s.dataset.transition = v; else delete s.dataset.transition; }));
    blocs.push(...blocsCommunsSection(s));
    return blocs;
  }

  // ======================================================================
  // Enregistrer, aperçu, import
  // ======================================================================
  async function enregistrer() {
    terminerEdition(true);
    etatSauvegarde('enregistrement…');
    try {
      await apiJson('/api/deck/slides', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ html: serialiser() }) });
      etat.modifie = false;
      etatSauvegarde('enregistré', 'enregistre');
    } catch (e) {
      etatSauvegarde('erreur : ' + e.message, 'erreur');
    }
  }
  $('#b-enregistrer').onclick = enregistrer;
  // Aperçu : la présentation éditée en prévisualisation (sans synchronisation) si elle est désignée, sinon la scène.
  $('#b-apercu').onclick = () => window.open(prezDemandee ? '/deck/?prez=' + encodeURIComponent(prezDemandee) + '&role=scene' : '/deck/?role=scene', '_blank');
  $('#b-annuler').onclick = annuler;
  $('#b-retablir').onclick = retablir;
  $('#b-importer').onclick = () => $('#fichier-import').click();
  $('#fichier-import').addEventListener('change', async e => {
    const f = e.target.files[0];
    e.target.value = '';
    if (!f) return;
    if (etat.modifie && !confirm('Des modifications non enregistrées seront perdues. Importer quand même ?')) return;
    const texte = await f.text();
    etat.nomImport = f.name;
    chargerDepuisTexte(texte);
    marquer();
  });
  window.addEventListener('beforeunload', e => { if (etat.modifie || etat.questionsModifiees) { e.preventDefault(); e.returnValue = ''; } });

  // ======================================================================
  // Raccourcis clavier
  // ======================================================================
  document.addEventListener('keydown', e => {
    const cible = e.target;
    const dansSaisie = cible.matches('input, textarea, select') || cible.isContentEditable;
    if (e.key === 'Escape') {
      if (etat.edition) { terminerEdition(true); return; }
      if (dansSaisie) { cible.blur(); return; }
      if (etat.selection) { etat.selection = null; rafraichirSelection(); rendrePanneau(); }
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); if (!$('#vue-questions').hidden) enregistrerQuestions(); else enregistrer(); return; }
    if (dansSaisie) return;
    if (!$('#vue-deck').hidden) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? retablir() : annuler(); return; }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') { e.preventDefault(); retablir(); return; }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'd') { e.preventDefault(); dupliquerElement(); return; }
      if (e.key === 'Delete' || e.key === 'Backspace') { if (etat.selection) { e.preventDefault(); supprimerElement(); } return; }
      const pas = e.shiftKey ? 10 : 1;
      const fleches = { ArrowLeft: [-pas, 0], ArrowRight: [pas, 0], ArrowUp: [0, -pas], ArrowDown: [0, pas] };
      if (fleches[e.key] && etat.selection) { e.preventDefault(); deplacerElement(...fleches[e.key]); }
    }
  });

  // ======================================================================
  // Dialogue générique → Promise des valeurs (null si annulé)
  // ======================================================================
  function dialogue(titre, champs) {
    const dlg = $('#dialogue');
    $('#dialogue-titre').textContent = titre;
    const zone = $('#dialogue-champs');
    zone.textContent = '';
    const controles = {};
    champs.forEach((c, i) => {
      let ctrl;
      if (c.type === 'select') ctrl = h('select', {}, (c.options || []).map(o => h('option', { value: o.v }, o.l)));
      else if (c.type === 'fichier') ctrl = h('input', { type: 'file', accept: c.accept || '' });
      else if (c.type === 'case') ctrl = h('input', { type: 'checkbox', checked: !!c.valeur });
      else if (c.type === 'nombre') ctrl = h('input', { type: 'number', value: c.valeur ?? '' });
      else ctrl = h('input', { type: 'text', value: c.valeur ?? '' });
      if (i === 0) ctrl.autofocus = true;
      controles[c.nom] = ctrl;
      zone.append(champ(c.label, ctrl));
    });
    return new Promise(resolve => {
      const fin = ok => {
        dlg.removeEventListener('close', surFermeture);
        if (!ok) return resolve(null);
        const v = {};
        for (const [nom, ctrl] of Object.entries(controles)) {
          v[nom] = ctrl.type === 'file' ? (ctrl.files[0] || null) : ctrl.type === 'checkbox' ? ctrl.checked : ctrl.type === 'number' ? parseFloat(ctrl.value) : ctrl.value;
        }
        resolve(v);
      };
      const surFermeture = () => fin(dlg.returnValue === 'ok');
      dlg.addEventListener('close', surFermeture);
      $('#dialogue-annuler').onclick = () => { dlg.close('annuler'); };
      dlg.showModal();
    });
  }

  // ======================================================================
  // Onglets
  // ======================================================================
  function basculerVue(vue) {
    terminerEdition(true);
    $('#vue-deck').hidden = vue !== 'deck';
    $('#vue-questions').hidden = vue !== 'questions';
    $$('.onglet').forEach(b => b.classList.toggle('actif', b.dataset.vue === vue));
    if (vue === 'deck') redimensionnerScene();
  }
  $$('.onglet').forEach(b => b.onclick = () => basculerVue(b.dataset.vue));

  // ======================================================================
  // Onglet Questions (quiz.json)
  // ======================================================================
  const Q = () => etat.questions[etat.questionCourante];
  function rendreQuestions() {
    const ol = $('#liste-questions');
    ol.textContent = '';
    const erreurs = validerQuestions();
    etat.questions.forEach((q, i) => {
      const li = h('li', { class: (i === etat.questionCourante ? 'courante' : '') + (erreurs.parIndex[i] ? ' invalide' : ''), draggable: 'true',
        onclick: () => { etat.questionCourante = i; rendreQuestions(); } },
        h('span', { class: 'id' }, q.id || '(sans id)'), h('span', { class: 'type' }, q.type || 'qcm'), h('span', { class: 'texte' }, q.question || ''));
      li.addEventListener('dragstart', e => e.dataTransfer.setData('text/plain', String(i)));
      li.addEventListener('dragover', e => e.preventDefault());
      li.addEventListener('drop', e => {
        e.preventDefault();
        const de = parseInt(e.dataTransfer.getData('text/plain'), 10);
        if (Number.isInteger(de) && de !== i) { const [q2] = etat.questions.splice(de, 1); etat.questions.splice(i, 0, q2); etat.questionCourante = i; questionsModifiees(); }
      });
      ol.append(li);
    });
    const zoneErr = $('#q-erreurs');
    zoneErr.hidden = !erreurs.liste.length;
    zoneErr.textContent = '';
    if (erreurs.liste.length) zoneErr.append('Corrigez avant d\'enregistrer :', h('ul', {}, erreurs.liste.map(x => h('li', {}, x))));
    rendreFormulaireQuestion();
  }
  function questionsModifiees() {
    etat.questionsModifiees = true;
    $('#q-etat').textContent = 'modifié'; $('#q-etat').className = 'etat modifie';
    rendreQuestions();
  }
  /** Validation côté client : id unique et bien formé, nombre d'options, champs spécifiques. */
  function validerQuestions() {
    const liste = [], parIndex = {};
    const ids = new Map();
    const err = (i, m) => { liste.push('Question ' + (i + 1) + (etat.questions[i].id ? ' (' + etat.questions[i].id + ')' : '') + ' : ' + m); parIndex[i] = true; };
    etat.questions.forEach((q, i) => {
      const type = q.type || 'qcm';
      if (!q.id || !/^[a-z0-9_-]+$/.test(q.id)) err(i, 'identifiant requis, caractères autorisés a-z 0-9 _ -');
      else if (ids.has(q.id)) err(i, 'identifiant déjà utilisé par la question ' + (ids.get(q.id) + 1)); else ids.set(q.id, i);
      if (!TYPES_QUESTION.includes(type)) err(i, 'type inconnu « ' + type + ' »');
      if (!(q.question || '').trim() && type !== 'trou') err(i, 'énoncé vide');
      if (AVEC_OPTIONS[type]) {
        const [min, max] = AVEC_OPTIONS[type];
        const opts = Array.isArray(q.options) ? q.options : [];
        if (opts.length < min || opts.length > max) err(i, 'entre ' + min + ' et ' + max + ' options (' + opts.length + ' actuellement)');
        if (opts.some(o => !String(o).trim())) err(i, 'option vide');
        if (type === 'rapidite' && !(q.chrono > 0)) err(i, 'chrono obligatoire pour une question de rapidité');
        if (type === 'rapidite' && !Number.isInteger(q.bonne)) err(i, 'bonne réponse obligatoire pour une question de rapidité');
        if (type === 'classement' && Array.isArray(q.bonne) && (q.bonne.length !== opts.length || [...q.bonne].sort().join() !== opts.map((_, k) => k).sort().join())) err(i, 'le bon ordre doit contenir chaque index une fois');
      }
      if (type === 'trou' && !(q.texte || '').includes('___')) err(i, 'le texte doit contenir ___ (le trou)');
      if (type === 'echelle' && !(num(q.min, 1) < num(q.max, 5))) err(i, 'min doit être inférieur à max');
    });
    return { liste, parIndex };
  }

  function rendreFormulaireQuestion() {
    const f = $('#formulaire-question');
    f.textContent = '';
    const q = Q();
    if (!q) { f.append(h('p', { class: 'aide' }, 'Aucune question. Cliquez sur « + Ajouter ».')); return; }
    const type = q.type || 'qcm';
    const maj = () => { questionsModifiees(); };
    const texte = (label, cle, opts = {}) => {
      const inp = h(opts.multiligne ? 'textarea' : 'input', { type: 'text', value: q[cle] ?? '', rows: opts.multiligne ? 3 : null, placeholder: opts.placeholder || '' });
      inp.addEventListener('input', () => { q[cle] = inp.value; etat.questionsModifiees = true; majListe(); });
      inp.addEventListener('change', maj);
      return champ(label, inp);
    };
    const nombre = (label, cle, defaut, opts = {}) => {
      const inp = h('input', { type: 'number', value: q[cle] ?? '', placeholder: defaut !== undefined ? String(defaut) : '', min: opts.min, step: opts.step || 1 });
      inp.addEventListener('change', () => { if (inp.value === '') delete q[cle]; else q[cle] = parseFloat(inp.value); maj(); });
      return champ(label, inp);
    };
    const majListe = () => {
      const li = $('#liste-questions li.courante');
      if (li) { li.querySelector('.id').textContent = q.id || '(sans id)'; li.querySelector('.texte').textContent = q.question || ''; }
      $('#q-etat').textContent = 'modifié'; $('#q-etat').className = 'etat modifie';
    };

    f.append(h('h3', {}, 'Question ' + (etat.questionCourante + 1)));
    f.append(h('div', { class: 'boutons' }, h('button', { class: 'principal', onclick: () => insererDiapoQuiz(q.id) }, 'Insérer une diapositive de quiz après la diapositive courante')));
    f.append(texte('Identifiant', 'id', { placeholder: 'q1' }));
    const selType = h('select', {}, TYPES_QUESTION.map(t => h('option', { value: t, selected: t === type }, t)));
    selType.addEventListener('change', () => {
      const ancienType = q.type;
      q.type = selType.value;
      if (q.type === 'grille' && ancienType !== 'grille') {
        // Grille : une réponse par ligne (A, B, C…) parmi les colonnes ; les anciennes bonnes réponses ne valent plus.
        if (!Array.isArray(q.options) || !q.options.length) q.options = ['✍️ Humain', '🤖 IA'];
        q.options = q.options.slice(0, 4);
        if (!Array.isArray(q.lignes) || !q.lignes.length) q.lignes = ['A', 'B', 'C'];
        delete q.bonne; delete q.bonnes;
      }
      if (q.type !== 'grille') delete q.lignes;
      if (AVEC_OPTIONS[q.type] && !Array.isArray(q.options)) q.options = ['Option A', 'Option B'];
      if (q.type === 'classement' && !Array.isArray(q.bonne)) delete q.bonne;
      if (q.type !== 'classement' && Array.isArray(q.bonne)) delete q.bonne;
      maj();
    });
    f.append(champ('Type', selType));
    f.append(texte('Question', 'question', { multiligne: true }));
    f.append(nombre('Chrono (s, 0 = sans limite)', 'chrono', 0, { min: 0 }));
    const points = h('input', { type: 'checkbox', checked: q.points !== false });
    points.addEventListener('change', () => { if (points.checked) delete q.points; else q.points = false; maj(); });
    f.append(champ('Compte des points', h('div', {}, points, ' ', h('span', { class: 'aide' }, 'défaut : oui si une bonne réponse est déclarée'))));

    // Champs spécifiques
    if (AVEC_OPTIONS[type]) f.append(h('h4', {}, type === 'grille' ? 'Colonnes (choix proposés à chaque ligne)' : 'Options'), blocOptions(q, type, maj));
    if (type === 'grille') {
      if (!Array.isArray(q.lignes) || !q.lignes.length) q.lignes = ['A', 'B', 'C'];
      const ta = h('textarea', { rows: 4, placeholder: 'une ligne par élément : A, B, C…' });
      ta.value = q.lignes.join('\n');
      ta.addEventListener('change', () => {
        q.lignes = ta.value.split('\n').map(x => x.trim()).filter(Boolean).slice(0, 8);
        if (!q.lignes.length) q.lignes = ['A'];
        if (Array.isArray(q.bonnes)) { q.bonnes = q.lignes.map((_, i) => q.bonnes[i] ?? null); if (q.bonnes.every(b => b === null)) delete q.bonnes; }
        maj();
      });
      f.append(champ('Lignes', ta));
      const bonnes = h('div', { class: 'champ large' });
      q.lignes.forEach((l, i) => {
        const sel = h('select', {}, h('option', { value: '' }, '— pas de bonne réponse'),
          q.options.map((o, k) => h('option', { value: String(k), selected: Array.isArray(q.bonnes) && q.bonnes[i] === k }, o)));
        sel.addEventListener('change', () => {
          const b = q.lignes.map((_, k) => (Array.isArray(q.bonnes) ? q.bonnes[k] : null) ?? null);
          b[i] = sel.value === '' ? null : Number(sel.value);
          if (b.every(x => x === null)) delete q.bonnes; else q.bonnes = b;
          maj();
        });
        bonnes.append(h('div', { class: 'option' }, h('b', { style: 'min-width:2em' }, l), sel));
      });
      f.append(h('h4', {}, 'Bonne réponse de chaque ligne'), bonnes,
        h('p', { class: 'aide' }, 'Sur le téléphone : une ligne par élément, un bouton par colonne. Points : part des lignes justes, plus vite = plus de points. Dans une diapositive, un élément « résultats » montre une colonne de barres par ligne.'));
    }
    if (type === 'vrai_faux') {
      const sel = h('select', {}, [['', '— pas de bonne réponse'], ['true', 'Vrai'], ['false', 'Faux']].map(([v, l]) => h('option', { value: v, selected: String(q.bonne) === v || (v === '' && typeof q.bonne !== 'boolean') }, l)));
      sel.addEventListener('change', () => { if (sel.value === '') delete q.bonne; else q.bonne = sel.value === 'true'; maj(); });
      f.append(champ('Bonne réponse', sel));
    }
    if (type === 'nuage') { f.append(nombre('Mots max. par participant', 'max_mots', 3, { min: 1 }), nombre('Longueur max. d\'un mot', 'max_longueur', 25, { min: 1 })); }
    if (type === 'texte_libre') f.append(nombre('Longueur max.', 'max_longueur', 140, { min: 1 }));
    if (type === 'trou') {
      f.append(texte('Texte avec ___', 'texte', { multiligne: true, placeholder: 'La capitale de la France est ___.' }));
      const ta = h('textarea', { rows: 3, placeholder: 'une réponse acceptée par ligne' });
      ta.value = (q.bonnes || []).join('\n');
      ta.addEventListener('change', () => { q.bonnes = ta.value.split('\n').map(x => x.trim()).filter(Boolean); if (!q.bonnes.length) delete q.bonnes; maj(); });
      f.append(champ('Bonnes réponses', ta));
    }
    if (type === 'echelle') {
      f.append(nombre('Min', 'min', 1), nombre('Max', 'max', 5), texte('Libellé min', 'libelle_min'), texte('Libellé max', 'libelle_max'));
    }
    if (type === 'nombre') {
      f.append(nombre('Bonne valeur', 'bonne', undefined, { step: 'any' }), texte('Unité', 'unite', { placeholder: 'km, %, €…' }));
    }
    if (type === 'buzzer') f.append(h('p', { class: 'aide' }, 'Buzzer : les participants appuient le plus vite possible ; les 5 premiers sont affichés avec leur temps.'));

    // Aperçu JSON
    f.append(h('h4', {}, 'JSON'));
    const pre = h('textarea', { rows: 6, readonly: true });
    pre.value = JSON.stringify(q, null, 2);
    f.append(pre);
  }
  /** Liste d'options avec ajout/suppression et marquage de la ou des bonnes réponses. */
  function blocOptions(q, type, maj) {
    if (!Array.isArray(q.options)) q.options = [];
    const conteneur = h('div', { class: 'champ large' });
    const [min, max] = AVEC_OPTIONS[type];
    q.options.forEach((o, i) => {
      let marque;
      if (type === 'multi') {
        marque = h('input', { type: 'checkbox', title: 'Bonne réponse', checked: Array.isArray(q.bonnes) && q.bonnes.includes(i) });
        marque.addEventListener('change', () => { const b = new Set(q.bonnes || []); marque.checked ? b.add(i) : b.delete(i); q.bonnes = [...b].sort((a, c) => a - c); if (!q.bonnes.length) delete q.bonnes; maj(); });
      } else if (type === 'classement') {
        marque = h('input', { type: 'number', min: 1, max: q.options.length, title: 'Rang dans le bon ordre', value: Array.isArray(q.bonne) ? (q.bonne.indexOf(i) + 1 || '') : '', style: 'width:52px' });
        marque.addEventListener('change', () => {
          const rangs = q.options.map((_, k) => Array.isArray(q.bonne) ? q.bonne.indexOf(k) : -1);
          rangs[i] = marque.value === '' ? -1 : parseInt(marque.value, 10) - 1;
          if (rangs.every(r => r < 0)) delete q.bonne;
          else { // bonne = ordre d'index : tri des options par rang ; celles sans rang vont à la fin
            q.bonne = q.options.map((_, k) => k).sort((a, b) => (rangs[a] < 0 ? 99 : rangs[a]) - (rangs[b] < 0 ? 99 : rangs[b]));
          }
          maj();
        });
      } else if (type === 'grille') {
        marque = h('span', { class: 'aide', style: 'min-width:16px' }, String(i + 1)); // bonne réponse : par ligne, plus bas
      } else {
        marque = h('input', { type: 'radio', name: 'bonne', title: 'Bonne réponse', checked: q.bonne === i });
        marque.addEventListener('change', () => { q.bonne = i; maj(); });
      }
      const inp = h('input', { type: 'text', value: String(o) });
      inp.addEventListener('input', () => { q.options[i] = inp.value; etat.questionsModifiees = true; });
      inp.addEventListener('change', maj);
      const suppr = h('button', { title: 'Supprimer l\'option', disabled: q.options.length <= min, onclick: () => {
        q.options.splice(i, 1);
        if (type === 'multi' && Array.isArray(q.bonnes)) q.bonnes = q.bonnes.filter(b => b !== i).map(b => b > i ? b - 1 : b);
        else if (type === 'classement' && Array.isArray(q.bonne)) q.bonne = q.bonne.filter(b => b !== i).map(b => b > i ? b - 1 : b);
        else if (type === 'grille' && Array.isArray(q.bonnes)) { q.bonnes = q.bonnes.map(b => b === i ? null : b !== null && b > i ? b - 1 : b); if (q.bonnes.every(b => b === null)) delete q.bonnes; }
        else if (q.bonne === i) delete q.bonne; else if (Number.isInteger(q.bonne) && q.bonne > i) q.bonne--;
        maj();
      } }, '×');
      conteneur.append(h('div', { class: 'option' }, marque, inp, suppr));
    });
    conteneur.append(h('div', { class: 'rangee' },
      h('button', { disabled: q.options.length >= max, onclick: () => { q.options.push(type === 'emoji' ? '🙂' : 'Option ' + String.fromCharCode(65 + q.options.length)); maj(); } }, '+ Option'),
      h('button', { disabled: !(Number.isInteger(q.bonne) || Array.isArray(q.bonne) || Array.isArray(q.bonnes)), onclick: () => { delete q.bonne; delete q.bonnes; maj(); } }, 'Aucune bonne réponse'),
      h('span', { class: 'aide' }, type === 'multi' ? 'cochez les bonnes réponses' : type === 'classement' ? 'saisissez le rang de chaque option' : 'cochez la bonne réponse (facultatif)')));
    return conteneur;
  }
  function nouvelIdQuestion() {
    let n = etat.questions.length + 1;
    while (etat.questions.some(q => q.id === 'q' + n)) n++;
    return 'q' + n;
  }
  $('#q-ajouter').onclick = () => {
    etat.questions.splice(etat.questionCourante + 1, 0, { id: nouvelIdQuestion(), type: 'qcm', question: 'Nouvelle question', options: ['Option A', 'Option B', 'Option C'] });
    etat.questionCourante = Math.min(etat.questionCourante + 1, etat.questions.length - 1);
    questionsModifiees();
  };
  $('#q-dupliquer').onclick = () => {
    const q = Q(); if (!q) return;
    const c = JSON.parse(JSON.stringify(q)); c.id = nouvelIdQuestion();
    etat.questions.splice(etat.questionCourante + 1, 0, c);
    etat.questionCourante++;
    questionsModifiees();
  };
  $('#q-supprimer').onclick = () => {
    const q = Q(); if (!q) return;
    if (!confirm('Supprimer la question « ' + q.id + ' » ?')) return;
    etat.questions.splice(etat.questionCourante, 1);
    etat.questionCourante = Math.max(0, Math.min(etat.questionCourante, etat.questions.length - 1));
    questionsModifiees();
  };
  $('#q-monter').onclick = () => { const i = etat.questionCourante; if (i > 0) { [etat.questions[i - 1], etat.questions[i]] = [etat.questions[i], etat.questions[i - 1]]; etat.questionCourante--; questionsModifiees(); } };
  $('#q-descendre').onclick = () => { const i = etat.questionCourante; if (i < etat.questions.length - 1) { [etat.questions[i + 1], etat.questions[i]] = [etat.questions[i], etat.questions[i + 1]]; etat.questionCourante++; questionsModifiees(); } };
  async function enregistrerQuestions() {
    const erreurs = validerQuestions();
    rendreQuestions();
    if (erreurs.liste.length) { $('#q-etat').textContent = 'corrigez les erreurs'; $('#q-etat').className = 'etat erreur'; return; }
    $('#q-etat').textContent = 'enregistrement…'; $('#q-etat').className = 'etat';
    try {
      await apiJson('/api/quiz/questions', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(etat.questions) });
      etat.questionsModifiees = false;
      $('#q-etat').textContent = 'enregistré'; $('#q-etat').className = 'etat enregistre';
      rendreListe(); // les vignettes de quiz reflètent les nouveaux énoncés
    } catch (e) {
      $('#q-etat').textContent = 'erreur : ' + e.message; $('#q-etat').className = 'etat erreur';
    }
  }
  $('#q-enregistrer').onclick = enregistrerQuestions;
  function insererDiapoQuiz(id) {
    if (!id) { alert('Donnez d\'abord un identifiant à la question.'); return; }
    ajouterSection('quiz', etat.courant, id);
    basculerVue('deck');
  }

  // ======================================================================
  // Démarrage
  // ======================================================================
  if (!token) etatSauvegarde('jeton manquant : ouvrir /deck/editeur.html?token=…', 'erreur');
  redimensionnerScene();
  charger();
})();

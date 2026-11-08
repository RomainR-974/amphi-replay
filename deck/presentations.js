/*
 * Bibliothèque de présentations (voir docs/presentations.md) : cartes avec vignette de la première diapositive,
 * badge « active », actions (Lancer, Éditer, Dupliquer, Renommer, Supprimer), création, import PowerPoint.
 * JS vanilla, aucune dépendance ; l'API est protégée par le jeton (?token=).
 */
(() => {
  'use strict';

  const $ = s => document.querySelector(s);
  const token = new URLSearchParams(location.search).get('token') || '';
  const avecJeton = chemin => chemin + (chemin.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token);
  const SLUG = /^[a-z0-9-]{1,40}$/;
  const SCENE_L = 1920;
  let presentations = [];

  async function api(chemin, options) {
    const r = await fetch(avecJeton(chemin), options);
    if (!r.ok) {
      let detail = r.status + ' ' + r.statusText;
      try { const j = await r.json(); detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail || j); } catch (e) { /* corps non JSON */ }
      throw new Error(detail);
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
      else if (k === 'disabled' || k === 'hidden' || k === 'checked' || k === 'required') el[k] = !!v;
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

  function etat(texte, classe) {
    const el = $('#etat');
    el.textContent = texte;
    el.className = 'etat' + (classe ? ' ' + classe : '');
    if (classe === 'ok') setTimeout(() => { if (el.textContent === texte) el.textContent = ''; }, 3000);
  }

  /** « Mon titre : été 2026 » → « mon-titre-ete-2026 ». */
  function slugifier(texte) {
    return String(texte || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
      .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40).replace(/-+$/g, '');
  }
  const pluriel = (n, mot) => n + ' ' + mot + (n > 1 ? 's' : '');

  // ======================================================================
  // Chargement et rendu des cartes
  // ======================================================================
  async function charger() {
    try {
      presentations = await api('/api/presentations');
      etat('');
    } catch (e) {
      etat('erreur : ' + e.message, 'erreur');
      presentations = [];
    }
    rendre();
  }

  function rendre() {
    const conteneur = $('#cartes');
    conteneur.textContent = '';
    $('#vide').hidden = presentations.length > 0;
    presentations.forEach(p => conteneur.append(carte(p)));
    requestAnimationFrame(redimensionnerVignettes);
    presentations.forEach(p => chargerVignette(p.slug));
    presentations.forEach(p => chargerReplays(p.slug));
  }

  // ---- Replays de la carte (docs/postprod.md §2) : Lire, Supprimer ----
  function formaterDuree(s) {
    s = Math.max(0, Math.round(s || 0));
    const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
    const pad = n => String(n).padStart(2, '0');
    return hh ? hh + ':' + pad(mm) + ':' + pad(ss) : pad(mm) + ':' + pad(ss);
  }
  async function chargerReplays(slug) {
    const zone = document.querySelector('.carte[data-slug="' + slug + '"] .replays');
    if (!zone) return;
    let replays;
    try {
      replays = await (await fetch('/api/presentations/' + encodeURIComponent(slug) + '/replays', { cache: 'no-store' })).json();
      if (!Array.isArray(replays)) throw new Error();
    } catch (e) {
      zone.textContent = '';
      zone.append(h('span', { class: 'sourd' }, 'replays indisponibles'));
      return;
    }
    zone.textContent = '';
    if (!replays.length) { zone.append(h('span', { class: 'sourd' }, 'Aucun replay')); return; }
    zone.append(h('div', { class: 'replays-titre' }, pluriel(replays.length, 'replay')));
    replays.forEach(r => {
      const urlLire = '/deck/replay.html?prez=' + encodeURIComponent(slug) + '&id=' + encodeURIComponent(r.id) + '&token=' + encodeURIComponent(token);
      zone.append(h('div', { class: 'replay' },
        h('a', { class: 'replay-titre', href: urlLire, title: r.titre }, r.titre),
        h('span', { class: 'replay-infos' }, formaterDate(r.date), ' · ', formaterDuree(r.duree), ' · ', pluriel(r.chapitres, 'chapitre'), r.extraits ? ' · ' + pluriel(r.extraits, 'extrait') : ''),
        h('span', { class: 'replay-actions' },
          h('button', { onclick: () => { location.href = urlLire; } }, '▶ Lire'),
          h('button', { class: 'danger', title: 'Supprimer le dossier du replay', onclick: () => supprimerReplay(slug, r) }, 'Supprimer'))));
    });
  }
  async function supprimerReplay(slug, r) {
    if (!confirm('Supprimer le replay « ' + r.titre + ' » (' + r.id + ') ?\nLa vidéo, les chapitres et les extraits seront effacés.')) return;
    try {
      await api('/api/presentations/' + encodeURIComponent(slug) + '/replays/' + encodeURIComponent(r.id), { method: 'DELETE' });
      etat('replay « ' + r.titre + ' » supprimé', 'ok');
    } catch (e) { etat('erreur : ' + e.message, 'erreur'); }
    await chargerReplays(slug);
  }

  function carte(p) {
    const urlEditeur = '/deck/editeur.html?token=' + encodeURIComponent(token) + '&prez=' + encodeURIComponent(p.slug);
    return h('article', { class: 'carte' + (p.active ? ' active' : ''), 'data-slug': p.slug },
      h('a', { class: 'vignette', href: '/deck/?prez=' + encodeURIComponent(p.slug) + '&role=scene', target: '_blank', title: 'Prévisualiser (sans synchronisation)' },
        h('div', { class: 'reveal' }, h('div', { class: 'slides' }))),
      h('div', { class: 'corps' },
        h('h2', {}, h('span', {}, p.titre), p.active ? h('span', { class: 'badge' }, 'active') : null),
        h('div', { class: 'description' }, p.description || ''),
        h('div', { class: 'infos' },
          h('span', {}, pluriel(p.diapositives, 'diapositive')),
          h('span', {}, pluriel(p.questions, 'question')),
          p.theme ? h('span', {}, 'thème ' + p.theme) : null,
          h('code', { title: 'presentations/' + p.slug }, p.slug),
          h('span', { title: 'Dernière modification' }, formaterDate(p.modifie))),
        h('div', { class: 'replays' }, h('span', { class: 'sourd' }, 'replays…'))),
      h('div', { class: 'actions' },
        h('button', { class: 'lancer', disabled: p.active, title: p.active ? 'Déjà active' : 'Rendre active : écran, OBS, télécommande et participants basculent dessus', onclick: () => lancer(p) }, p.active ? '✔ Active' : '▶ Lancer'),
        h('button', { onclick: () => { location.href = urlEditeur; } }, 'Éditer'),
        h('button', { onclick: () => dupliquer(p) }, 'Dupliquer'),
        h('button', { onclick: () => renommer(p) }, 'Renommer'),
        h('button', { class: 'postprod', title: 'Assembler un replay (son du téléphone + vidéo OBS, chapitrage)', onclick: () => { location.href = '/deck/postprod.html?token=' + encodeURIComponent(token) + '&prez=' + encodeURIComponent(p.slug); } }, '🎬 Post-production…'),
        h('span', { class: 'espace' }),
        h('button', { class: 'danger', disabled: p.active, title: p.active ? 'La présentation active ne peut pas être supprimée' : 'Supprimer définitivement', onclick: () => supprimer(p) }, 'Supprimer')));
  }

  function formaterDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' }) + ' ' + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  }

  // ---- Vignette : première <section> de slides.html, médias via /p/<slug>/medias/ ----
  async function chargerVignette(slug) {
    const slides = document.querySelector('.carte[data-slug="' + slug + '"] .vignette .slides');
    if (!slides) return;
    const reveal = slides.parentElement;
    try {
      const html = await (await fetch('/p/' + encodeURIComponent(slug) + '/slides.html', { cache: 'no-store' })).text();
      const gabarit = document.createElement('template');
      gabarit.innerHTML = html;
      const section = gabarit.content.querySelector('section');
      if (!section) { slides.append(h('div', { class: 'substitut' }, 'Présentation vide')); return; }
      // Scripts et iframes ne sont jamais exécutés dans une vignette ; les médias relatifs pointent sur la présentation.
      section.querySelectorAll('script, iframe, video, audio').forEach(n => n.replaceWith(h('div', { class: n.className + ' substitut' }, n.tagName.toLowerCase())));
      const prefixe = '/p/' + encodeURIComponent(slug) + '/';
      section.querySelectorAll('[src^="medias/"], [data-src^="medias/"], [data-background-image^="medias/"]').forEach(el => {
        for (const attr of ['src', 'data-src', 'data-background-image']) {
          const v = el.getAttribute(attr);
          if (v && v.startsWith('medias/')) el.setAttribute(attr, prefixe + v);
        }
      });
      section.querySelectorAll('aside.notes').forEach(n => n.remove());
      const libre = section.classList.contains('libre');
      const quiz = !libre && section.hasAttribute('data-quiz');
      reveal.classList.add(libre ? 'libre' : quiz ? 'quiz' : 'flux');
      const vignette = reveal.parentElement;
      if (section.dataset.backgroundColor) vignette.style.background = section.dataset.backgroundColor;
      if (section.dataset.backgroundImage) { vignette.style.backgroundImage = 'url("' + section.dataset.backgroundImage.replace(/"/g, '') + '")'; vignette.style.backgroundSize = 'cover'; }
      if (quiz) {
        slides.append(h('div', { class: 'quiz-apercu' }, h('h2', {}, 'Quiz ' + section.dataset.quiz), h('div', { class: 'substitut' }, '📊 résultats en direct')));
      } else {
        slides.append(document.adoptNode(section));
      }
    } catch (e) {
      slides.append(h('div', { class: 'substitut' }, 'aperçu indisponible'));
    }
  }
  function redimensionnerVignettes() {
    const v = document.querySelector('.vignette');
    if (v && v.clientWidth) document.documentElement.style.setProperty('--echelle', v.clientWidth / SCENE_L);
  }
  window.addEventListener('resize', redimensionnerVignettes);

  // ======================================================================
  // Dialogue générique
  // ======================================================================
  /** Ouvre le dialogue avec des champs [{nom, label, type: texte|zone|fichier|select, valeur, placeholder, aide, accept, options, requis}].
   *  `valider(valeurs)` renvoie un message d'erreur (le dialogue reste ouvert) ou lance l'action ; le dialogue se ferme sur succès. */
  function dialogue(titre, champs, action, options = {}) {
    const dlg = $('#dialogue'), zone = $('#dialogue-champs'), erreur = $('#dialogue-erreur'), progression = $('#dialogue-progression');
    $('#dialogue-titre').textContent = titre;
    $('#dialogue-ok').textContent = options.ok || 'OK';
    zone.textContent = '';
    erreur.hidden = true; progression.hidden = true;
    const entrees = {};
    champs.forEach(c => {
      let inp;
      if (c.type === 'zone') inp = h('textarea', { rows: 2, placeholder: c.placeholder || '' }, c.valeur || '');
      else if (c.type === 'fichier') inp = h('input', { type: 'file', accept: c.accept || '' });
      else if (c.type === 'select') inp = h('select', {}, c.options.map(o => h('option', { value: o.v, selected: o.v === c.valeur }, o.l)));
      else inp = h('input', { type: 'text', value: c.valeur || '', placeholder: c.placeholder || '', autocomplete: 'off' });
      inp.name = c.nom;
      entrees[c.nom] = inp;
      zone.append(h('div', { class: 'champ' }, h('label', {}, c.label), inp, c.aide ? h('small', {}, c.aide) : null));
      if (c.surChangement) inp.addEventListener('input', () => c.surChangement(inp, entrees));
    });
    const lire = () => Object.fromEntries(Object.entries(entrees).map(([k, inp]) => [k, inp.type === 'file' ? inp.files[0] || null : inp.value]));
    return new Promise(resolve => {
      const form = $('#dialogue-form');
      let enCours = false;
      const fermer = (valeur) => { dlg.close(); form.onsubmit = null; $('#dialogue-annuler').onclick = null; resolve(valeur); };
      $('#dialogue-annuler').onclick = () => { if (!enCours) fermer(null); };
      dlg.oncancel = e => { if (enCours) e.preventDefault(); };
      form.onsubmit = async e => {
        e.preventDefault();
        if (enCours) return;
        const v = lire();
        erreur.hidden = true;
        try {
          enCours = true;
          $('#dialogue-ok').disabled = true;
          if (options.progression) progression.hidden = false;
          const resultat = await action(v);
          if (typeof resultat === 'string') { erreur.textContent = resultat; erreur.hidden = false; return; }
          fermer(resultat === undefined ? true : resultat);
        } catch (err) {
          erreur.textContent = err.message; erreur.hidden = false;
        } finally {
          enCours = false;
          $('#dialogue-ok').disabled = false;
          progression.hidden = true;
        }
      };
      dlg.showModal();
      const premier = Object.values(entrees)[0];
      if (premier) premier.focus();
    });
  }

  const champsSlugTitre = (titreDefaut, slugDefaut) => [
    { nom: 'titre', label: 'Titre', valeur: titreDefaut, placeholder: 'Titre de la séance', surChangement: (inp, e) => { if (!e.slug.dataset.saisi) e.slug.value = slugifier(inp.value); } },
    { nom: 'slug', label: 'Identifiant', valeur: slugDefaut, placeholder: 'ma-seance', aide: 'Nom du dossier : lettres minuscules, chiffres et tirets (1 à 40).', surChangement: inp => { inp.dataset.saisi = inp.value ? '1' : ''; } },
  ];
  function verifierSlug(slug) {
    if (!SLUG.test(slug)) return 'Identifiant invalide : lettres minuscules, chiffres et tirets (1 à 40 caractères).';
    if (presentations.some(p => p.slug === slug)) return 'Cet identifiant existe déjà.';
    return null;
  }

  // ======================================================================
  // Actions
  // ======================================================================
  async function lancer(p) {
    try {
      etat('activation de « ' + p.titre + ' »…');
      await api('/api/presentations/' + encodeURIComponent(p.slug) + '/activer', { method: 'POST' });
      etat('« ' + p.titre + ' » est active : écran, OBS et télécommande rechargés', 'ok');
    } catch (e) { etat('erreur : ' + e.message, 'erreur'); }
    await charger();
  }

  async function nouvelle() {
    const r = await dialogue('Nouvelle présentation', [
      ...champsSlugTitre('', ''),
      { nom: 'description', label: 'Description', type: 'zone', placeholder: 'Contexte, date, public…' },
      { nom: 'theme', label: 'Thème', placeholder: 'vide = thème standard', aide: 'Nom d\'une feuille deck/themes/<thème>.css (optionnel).' },
    ], async v => {
      const erreur = verifierSlug(v.slug); if (erreur) return erreur;
      if (!v.titre.trim()) return 'Le titre est obligatoire.';
      return api('/api/presentations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ slug: v.slug, titre: v.titre.trim(), description: v.description.trim(), theme: v.theme.trim() }) });
    }, { ok: 'Créer' });
    if (r) { etat('« ' + r.titre + ' » créée', 'ok'); await charger(); }
  }

  async function dupliquer(p) {
    const base = slugifier(p.slug + '-copie');
    let slug = base, n = 2;
    while (presentations.some(x => x.slug === slug)) slug = slugifier(base + '-' + (n++));
    const r = await dialogue('Dupliquer « ' + p.titre + ' »', champsSlugTitre(p.titre + ' (copie)', slug).map(c => c.nom === 'slug' ? { ...c, surChangement: inp => { inp.dataset.saisi = '1'; } } : c), async v => {
      const erreur = verifierSlug(v.slug); if (erreur) return erreur;
      return api('/api/presentations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ slug: v.slug, titre: v.titre.trim() || p.titre, depuis: p.slug }) });
    }, { ok: 'Dupliquer' });
    if (r) { etat('« ' + r.titre + ' » créée (copie de ' + p.slug + ')', 'ok'); await charger(); }
  }

  async function renommer(p) {
    const r = await dialogue('Renommer « ' + p.titre + ' »', [
      { nom: 'titre', label: 'Titre', valeur: p.titre },
      { nom: 'description', label: 'Description', type: 'zone', valeur: p.description },
      { nom: 'theme', label: 'Thème', valeur: p.theme, placeholder: 'vide = thème standard', aide: 'Nom d\'une feuille deck/themes/<thème>.css (optionnel).' },
    ], async v => {
      if (!v.titre.trim()) return 'Le titre est obligatoire.';
      return api('/api/presentations/' + encodeURIComponent(p.slug), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ titre: v.titre.trim(), description: v.description.trim(), theme: v.theme.trim() }) });
    }, { ok: 'Enregistrer' });
    if (r) { etat('« ' + r.titre + ' » mise à jour', 'ok'); await charger(); }
  }

  async function supprimer(p) {
    if (!confirm('Supprimer définitivement « ' + p.titre + ' » (' + p.slug + ') ?\nDiapositives, questions et médias seront effacés.')) return;
    try {
      await api('/api/presentations/' + encodeURIComponent(p.slug), { method: 'DELETE' });
      etat('« ' + p.titre + ' » supprimée', 'ok');
    } catch (e) { etat('erreur : ' + e.message, 'erreur'); }
    await charger();
  }

  async function importer() {
    const r = await dialogue('Importer un PowerPoint', [
      { nom: 'fichier', label: 'Fichier .pptx', type: 'fichier', accept: '.pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation',
        surChangement: (inp, e) => { const f = inp.files[0]; if (f && !e.titre.value) { e.titre.value = f.name.replace(/\.pptx$/i, ''); if (!e.slug.dataset.saisi) e.slug.value = slugifier(e.titre.value); } } },
      ...champsSlugTitre('', ''),
    ], async v => {
      if (!v.fichier) return 'Choisissez un fichier .pptx.';
      if (!/\.pptx$/i.test(v.fichier.name)) return 'Seuls les fichiers .pptx sont acceptés.';
      const erreur = verifierSlug(v.slug); if (erreur) return erreur;
      const fd = new FormData();
      fd.append('fichier', v.fichier, v.fichier.name);
      fd.append('slug', v.slug);
      fd.append('titre', v.titre.trim() || v.fichier.name.replace(/\.pptx$/i, ''));
      return api('/api/presentations/importer', { method: 'POST', body: fd });
    }, { ok: 'Importer', progression: true });
    if (!r) return;
    etat('« ' + r.titre + ' » importée', 'ok');
    await charger();
    // Rapport : nombre de diapositives et avertissements de la conversion.
    $('#rapport-resume').textContent = pluriel(r.diapositives, 'diapositive') + ' importée' + (r.diapositives > 1 ? 's' : '') + ' dans « ' + r.titre + ' »' +
      (r.raster === false ? ' — LibreOffice absent : objets complexes non rastérisés.' : '.');
    const ul = $('#rapport-avertissements');
    ul.textContent = '';
    (r.avertissements || []).forEach(a => ul.append(h('li', {}, a)));
    $('#rapport-editer').onclick = () => { location.href = '/deck/editeur.html?token=' + encodeURIComponent(token) + '&prez=' + encodeURIComponent(r.slug); };
    $('#rapport').showModal();
  }

  // ======================================================================
  // Démarrage
  // ======================================================================
  $('#b-nouvelle').onclick = nouvelle;
  $('#b-importer').onclick = importer;
  $('#l-remote').href = '/deck/remote.html?token=' + encodeURIComponent(token);
  if (!token) etat('jeton manquant : ouvrir /presentations?token=…', 'erreur');
  charger();
})();

/*
 * Lecteur de replay (voir docs/postprod.md §2) : vidéo avec piste de chapitres VTT, liste des chapitres cliquables,
 * téléchargements (MP4, chapitres YouTube, VTT), extraits ; découpe a posteriori si le jeton est fourni.
 * URL : /deck/replay.html?prez=slug&id=…[&token=…]. JS vanilla, aucune dépendance.
 */
(() => {
  'use strict';

  const $ = s => document.querySelector(s);
  const params = new URLSearchParams(location.search);
  const token = params.get('token') || '';
  const prez = params.get('prez') || '';
  const id = params.get('id') || '';
  const base = '/p/' + encodeURIComponent(prez) + '/replays/' + encodeURIComponent(id) + '/';
  let chapitres = [];
  let minuteur = null;

  function h(tag, attrs, ...enfants) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith('on')) el[k] = v;
      else if (k === 'class') el.className = v;
      else if (k === 'hidden' || k === 'disabled') el[k] = !!v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    enfants.flat().forEach(e => { if (e !== null && e !== undefined && e !== false) el.append(e instanceof Node ? e : document.createTextNode(String(e))); });
    return el;
  }

  const pad = n => String(n).padStart(2, '0');
  function formaterDuree(s) {
    s = Math.max(0, Math.round(s || 0));
    const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
    return hh ? hh + ':' + pad(mm) + ':' + pad(ss) : pad(mm) + ':' + pad(ss);
  }
  function formaterTaille(o) {
    if (o >= 1e9) return (o / 1e9).toFixed(2) + ' Go';
    if (o >= 1e6) return (o / 1e6).toFixed(1) + ' Mo';
    return Math.round(o / 1e3) + ' ko';
  }
  function formaterDate(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso || '';
    return d.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }) + ' ' + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  }
  function erreur(message) {
    const el = $('#erreur');
    el.textContent = message;
    el.hidden = false;
  }

  async function api(chemin, options) {
    const url = chemin + (chemin.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token);
    const r = await fetch(url, options);
    if (!r.ok) {
      let detail = r.status + ' ' + r.statusText;
      try { const j = await r.json(); detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail || j); } catch (e) { /* corps non JSON */ }
      throw new Error(detail);
    }
    return r.json();
  }

  // ======================================================================
  // Rendu
  // ======================================================================
  function rendre(detail) {
    const j = detail.journal || {};
    document.title = detail.titre + ' – Replay';
    $('#titre').textContent = detail.titre;
    chapitres = detail.chapitres || [];

    const video = $('#video');
    video.src = base + 'replay.mp4';
    const piste = $('#piste-chapitres');
    if (chapitres.length) piste.src = base + 'chapitres.vtt';
    else piste.remove();

    const meta = $('#meta');
    meta.textContent = '';
    meta.append(h('span', {}, formaterDate(detail.date)));
    meta.append(h('span', {}, 'durée ' + formaterDuree(detail.duree)));
    meta.append(h('span', {}, formaterTaille(detail.taille)));
    if (j.source_video) meta.append(h('span', { title: 'Enregistrement OBS' }, 'source ' + j.source_video));
    if (j.source_audio) meta.append(h('span', { title: 'Audio du téléphone' }, 'audio ' + j.source_audio));
    if (j.mesures && typeof j.mesures.decalage_debut_s === 'number') {
      meta.append(h('span', { title: 'Recalage audio mesuré par assembler.py' }, 'recalage ' + j.mesures.decalage_debut_s.toFixed(3) + ' s' + (j.mesures.derive_corrigee ? ', dérive corrigée' : '')));
    }
    if (j.reconstitue) meta.append(h('span', { title: 'Dossier déposé à la main : journal reconstitué par ffprobe' }, 'journal reconstitué'));

    // Chapitres cliquables.
    const liste = $('#chapitres');
    liste.textContent = '';
    $('#chapitres-vide').hidden = chapitres.length > 0;
    $('#nb-chapitres').textContent = chapitres.length ? '(' + chapitres.length + ')' : '';
    chapitres.forEach((c, i) => {
      liste.append(h('li', { 'data-index': i, onclick: () => { video.currentTime = c.debut; video.play().catch(() => {}); } },
        h('span', { class: 'temps' }, formaterDuree(c.debut)), h('span', {}, c.titre)));
    });
    $('#youtube').value = chapitres.map(c => formaterDuree(c.debut) + ' ' + c.titre).join('\n');

    // Téléchargements.
    const liens = $('#liens');
    liens.textContent = '';
    liens.append(h('a', { href: base + 'replay.mp4', download: (slugifier(detail.titre) || 'replay') + '.mp4' }, '⬇ Vidéo MP4 (' + formaterTaille(detail.taille) + ')'));
    if (chapitres.length) {
      liens.append(h('a', { href: base + 'chapitres.txt', download: 'chapitres.txt' }, '⬇ Chapitres YouTube (chapitres.txt)'));
      liens.append(h('a', { href: base + 'chapitres.vtt', download: 'chapitres.vtt' }, '⬇ Chapitres WebVTT'));
    }
    liens.append(h('a', { href: base + 'journal.json', target: '_blank' }, 'Journal de l\'assemblage (JSON)'));

    rendreExtraits(detail.extraits || []);
  }

  function rendreExtraits(extraits) {
    const ul = $('#extraits');
    ul.textContent = '';
    $('#extraits-vide').hidden = extraits.length > 0;
    $('#nb-extraits').textContent = extraits.length ? '(' + extraits.length + ')' : '';
    extraits.forEach(e => ul.append(h('li', {}, h('a', { href: base + 'extraits/' + encodeURIComponent(e.nom), download: e.nom }, e.nom), h('span', {}, formaterTaille(e.taille)))));
    const bouton = $('#b-decouper');
    bouton.hidden = !(token && chapitres.length && !extraits.length);
  }

  function slugifier(texte) {
    return String(texte || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60);
  }

  // Surligne le chapitre en cours de lecture.
  function suivreLecture() {
    const t = $('#video').currentTime;
    let courant = -1;
    chapitres.forEach((c, i) => { if (t >= c.debut) courant = i; });
    document.querySelectorAll('#chapitres li').forEach((li, i) => li.classList.toggle('courant', i === courant));
  }

  // ======================================================================
  // Découpe a posteriori
  // ======================================================================
  async function decouper() {
    const bouton = $('#b-decouper');
    bouton.disabled = true;
    $('#decoupe-etat').textContent = 'découpe en cours…';
    try {
      const r = await api('/api/presentations/' + encodeURIComponent(prez) + '/replays/' + encodeURIComponent(id) + '/extraits', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
      suivre(r.tache);
    } catch (e) {
      bouton.disabled = false;
      $('#decoupe-etat').textContent = 'erreur : ' + e.message;
    }
  }

  async function suivre(tache) {
    clearTimeout(minuteur);
    let t;
    try {
      t = await api('/api/postprod/taches/' + encodeURIComponent(tache));
    } catch (e) {
      $('#decoupe-etat').textContent = 'serveur injoignable, nouvel essai…';
      minuteur = setTimeout(() => suivre(tache), 2000);
      return;
    }
    if (t.etat === 'en_cours') {
      const derniere = (t.journal || []).filter(l => !l.startsWith('$')).slice(-1)[0] || '';
      $('#decoupe-etat').textContent = 'découpe en cours… ' + derniere;
      minuteur = setTimeout(() => suivre(tache), 2000);
      return;
    }
    $('#b-decouper').disabled = false;
    if (t.etat === 'terminee') {
      $('#decoupe-etat').textContent = (t.replay && t.replay.extraits ? t.replay.extraits.length : 0) + ' extrait(s) créé(s)';
      await charger();
    } else {
      $('#decoupe-etat').textContent = 'erreur : ' + (t.erreur || 'échec');
    }
  }

  // ======================================================================
  // Démarrage
  // ======================================================================
  async function charger() {
    const r = await fetch('/api/presentations/' + encodeURIComponent(prez) + '/replays/' + encodeURIComponent(id), { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status === 404 ? 'replay introuvable (' + prez + '/' + id + ')' : 'HTTP ' + r.status);
    rendre(await r.json());
  }

  async function demarrer() {
    if (!/^[a-z0-9-]{1,40}$/.test(prez) || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(id)) {
      erreur('URL incomplète : /deck/replay.html?prez=<slug>&id=<replay>');
      return;
    }
    $('#l-presentations').href = '/deck/presentations.html' + (token ? '?token=' + encodeURIComponent(token) : '');
    if (token) {
      const l = $('#l-postprod');
      l.href = '/deck/postprod.html?token=' + encodeURIComponent(token) + '&prez=' + encodeURIComponent(prez);
      l.hidden = false;
    }
    $('#pied-info').textContent = 'presentations/' + prez + '/replays/' + id + '/';
    try {
      await charger();
    } catch (e) {
      erreur(e.message);
      $('#titre').textContent = 'Replay introuvable';
    }
    const video = $('#video');
    video.addEventListener('timeupdate', suivreLecture);
    video.addEventListener('error', () => erreur('la vidéo ne peut pas être lue (fichier replay.mp4 absent ou illisible)'));
  }

  $('#b-decouper').onclick = decouper;
  demarrer();
})();

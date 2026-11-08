/*
 * Post-production (voir docs/postprod.md §3) : choix des fichiers du dossier de captations, séance du journal
 * (serveur de la séance ou ce serveur), aperçu des chapitres, lancement de l'assemblage et suivi de la tâche.
 * JS vanilla, aucune dépendance ; l'API locale est protégée par le jeton (?token=).
 */
(() => {
  'use strict';

  const $ = s => document.querySelector(s);
  const params = new URLSearchParams(location.search);
  const token = params.get('token') || '';
  const avecJeton = chemin => chemin + (chemin.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token);
  const FUSION_S = 20;

  const etatPage = {
    presentations: [],
    captations: [],
    video: null,          // nom du fichier vidéo choisi
    audio: null,          // nom du fichier audio choisi
    seances: [],
    titres: [],
    titrePrez: '',
    seance: null,         // index dans seances
    tache: null,
    minuteur: null,
    seanceCoupe: undefined, // séance pour laquelle la case « couper avant les questions-réponses » a été préréglée
  };

  // ======================================================================
  // Utilitaires
  // ======================================================================
  async function api(chemin, options) {
    let r;
    try {
      r = await fetch(avecJeton(chemin), options);
    } catch (e) {
      throw new Error('serveur local injoignable (' + e.message + ')');
    }
    if (!r.ok) {
      let detail = r.status + ' ' + r.statusText;
      try { const j = await r.json(); detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail || j); } catch (e) { /* corps non JSON */ }
      throw new Error(detail);
    }
    return r.json();
  }

  function h(tag, attrs, ...enfants) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith('on')) el[k] = v;
      else if (k === 'class') el.className = v;
      else if (k === 'value') el.value = v;
      else if (k === 'disabled' || k === 'hidden' || k === 'checked' || k === 'selected') el[k] = !!v;
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
    if (classe === 'ok') setTimeout(() => { if (el.textContent === texte) el.textContent = ''; }, 4000);
  }

  const pad = n => String(n).padStart(2, '0');
  function formaterDuree(s) {
    if (s === null || s === undefined || Number.isNaN(s)) return '—';
    s = Math.round(s);
    const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
    return hh ? hh + ':' + pad(mm) + ':' + pad(ss) : pad(mm) + ':' + pad(ss);
  }
  function formaterTaille(o) {
    if (o >= 1e9) return (o / 1e9).toFixed(2) + ' Go';
    if (o >= 1e6) return (o / 1e6).toFixed(1) + ' Mo';
    if (o >= 1e3) return Math.round(o / 1e3) + ' ko';
    return o + ' o';
  }
  function formaterDate(epoch, options) {
    const d = new Date(epoch * 1000);
    return d.toLocaleDateString('fr-FR', options || { weekday: 'long', day: 'numeric', month: 'long' }) + ' ' + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  }
  function formaterHeure(epoch) {
    return new Date(epoch * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  }

  // ======================================================================
  // 1. Présentations et captations
  // ======================================================================
  async function chargerPresentations() {
    etatPage.presentations = await api('/api/presentations');
    const select = $('#prez');
    select.textContent = '';
    etatPage.presentations.forEach(p => select.append(h('option', { value: p.slug }, p.titre + (p.active ? ' (active)' : ''))));
    const voulu = params.get('prez');
    if (voulu && etatPage.presentations.some(p => p.slug === voulu)) select.value = voulu;
    else {
      const active = etatPage.presentations.find(p => p.active);
      if (active) select.value = active.slug;
    }
    surChangementPrez();
  }

  function surChangementPrez() {
    const p = etatPage.presentations.find(x => x.slug === $('#prez').value);
    etatPage.titrePrez = p ? p.titre : '';
    etatPage.seances = []; etatPage.seance = null; etatPage.titres = [];
    rendreSeances();
    majTitreDefaut();
    rendreApercu();
  }

  async function chargerCaptations() {
    try {
      etatPage.captations = await api('/api/postprod/captations');
    } catch (e) {
      etat('erreur : ' + e.message, 'erreur');
      etatPage.captations = [];
    }
    if (etatPage.video && !etatPage.captations.some(f => f.nom === etatPage.video)) etatPage.video = null;
    if (etatPage.audio && !etatPage.captations.some(f => f.nom === etatPage.audio)) etatPage.audio = null;
    // Préselection : la vidéo et l'audio les plus récents.
    if (!etatPage.video) { const v = etatPage.captations.find(f => f.genre === 'video'); if (v) etatPage.video = v.nom; }
    if (!etatPage.audio && !$('#sans-audio').checked) { const a = etatPage.captations.find(f => f.genre === 'audio'); if (a) etatPage.audio = a.nom; }
    rendreCaptations();
    rendreApercu();
  }

  function rendreCaptations() {
    const corps = $('#captations tbody');
    corps.textContent = '';
    const vide = $('#captations-vide');
    if (!etatPage.captations.length) {
      vide.textContent = 'Aucun fichier vidéo ou audio dans le dossier de captations. Copier l\'enregistrement OBS et l\'audio du téléphone dans ' + ($('#dossier-captations').textContent || 'ce dossier') + ' puis rafraîchir.';
      vide.hidden = false;
      return;
    }
    vide.hidden = true;
    const sansAudio = $('#sans-audio').checked;
    etatPage.captations.forEach(f => {
      const estVideo = f.genre === 'video';
      const choisie = f.nom === etatPage.video || (!sansAudio && f.nom === etatPage.audio);
      corps.append(h('tr', { class: 'cliquable' + (choisie ? ' choisie' : ''), onclick: e => { if (e.target.tagName !== 'INPUT') choisir(f); } },
        h('td', { class: 'choix' }, estVideo ? h('input', { type: 'radio', name: 'video', checked: f.nom === etatPage.video, onchange: () => { etatPage.video = f.nom; rendreCaptations(); rendreApercu(); majTitreDefaut(); } }) : null),
        h('td', { class: 'choix' }, !estVideo ? h('input', { type: 'radio', name: 'audio', disabled: sansAudio, checked: !sansAudio && f.nom === etatPage.audio, onchange: () => { etatPage.audio = f.nom; rendreCaptations(); } }) : null),
        h('td', {}, f.nom),
        h('td', {}, h('span', { class: 'badge ' + f.genre }, f.genre === 'video' ? 'vidéo' : 'audio')),
        h('td', { class: 'num' }, formaterDuree(f.duree)),
        h('td', { class: 'num' }, formaterTaille(f.taille)),
        h('td', { class: 'num' }, formaterDate(f.modifie, { day: '2-digit', month: '2-digit', year: 'numeric' }))));
    });
  }

  function choisir(f) {
    if (f.genre === 'video') etatPage.video = f.nom;
    else if (!$('#sans-audio').checked) etatPage.audio = f.nom;
    rendreCaptations();
    rendreApercu();
    majTitreDefaut();
  }

  const fichierVideo = () => etatPage.captations.find(f => f.nom === etatPage.video) || null;

  /** Début estimé de l'enregistrement (epoch) : mtime − durée (+ décalage manuel). */
  function debutEnregistrement() {
    const f = fichierVideo();
    if (!f || f.duree === null || f.duree === undefined) return null;
    return f.modifie - f.duree + (parseFloat($('#decalage').value) || 0);
  }

  // ======================================================================
  // 2. Séances et aperçu des chapitres
  // ======================================================================
  async function chargerSeances() {
    const prez = $('#prez').value;
    if (!prez) { etat('choisir une présentation', 'erreur'); return; }
    let base = ($('#serveur').value || '').trim().replace(/\/+$/, '');
    const jeton = $('#jeton-serveur').value || token;
    if (base && !/^https?:\/\//i.test(base)) base = 'https://' + base;
    const url = (base || '') + '/api/journal/seances?prez=' + encodeURIComponent(prez) + '&token=' + encodeURIComponent(jeton);
    $('#b-seances').disabled = true;
    etat('chargement des séances…');
    try {
      let r;
      try {
        r = await fetch(url, { cache: 'no-store' });
      } catch (e) {
        throw new Error('serveur de séance injoignable (' + (base || location.origin) + ') : vérifier l\'URL, la connexion et que le serveur accepte les requêtes CORS');
      }
      if (r.status === 403) throw new Error('jeton refusé par le serveur de séance');
      if (r.status === 404) throw new Error('présentation « ' + prez + ' » inconnue sur le serveur de séance');
      if (!r.ok) throw new Error('serveur de séance : HTTP ' + r.status);
      const corps = await r.json();
      etatPage.seances = corps.seances || [];
      etatPage.titres = corps.titres || [];
      etatPage.seance = etatPage.seances.length ? 0 : null;
      etat(etatPage.seances.length ? etatPage.seances.length + ' séance(s) trouvée(s)' : 'aucune séance dans le journal de cette présentation', etatPage.seances.length ? 'ok' : 'erreur');
    } catch (e) {
      etatPage.seances = []; etatPage.seance = null;
      etat('erreur : ' + e.message, 'erreur');
    } finally {
      $('#b-seances').disabled = false;
    }
    rendreSeances();
    majTitreDefaut();
    rendreApercu();
  }

  function rendreSeances() {
    const corps = $('#seances tbody');
    corps.textContent = '';
    const vide = $('#seances-vide');
    vide.hidden = etatPage.seances.length > 0;
    if (!etatPage.seances.length) {
      vide.textContent = etatPage.titres.length || etatPage.seances.length ? 'Aucune séance.' : 'Choisir une présentation puis cliquer sur « Charger les séances ».';
      return;
    }
    etatPage.seances.forEach((s, i) => {
      corps.append(h('tr', { class: 'cliquable' + (i === etatPage.seance ? ' choisie' : ''), onclick: () => { etatPage.seance = i; rendreSeances(); majTitreDefaut(); rendreApercu(); } },
        h('td', { class: 'choix' }, h('input', { type: 'radio', name: 'seance', checked: i === etatPage.seance, onchange: () => { etatPage.seance = i; rendreSeances(); majTitreDefaut(); rendreApercu(); } })),
        h('td', {}, formaterDate(s.debut) + ' – ' + formaterHeure(s.fin)),
        h('td', { class: 'num' }, formaterDuree(s.fin - s.debut)),
        h('td', { class: 'num' }, s.diapositives),
        h('td', { class: 'num' }, s.evenements.length)));
    });
  }

  /** Instant (s dans la vidéo) du premier `qr_ouvrir` de la séance, ou null (même règle que server/replays.py : dans ]0,5 s ; durée[). */
  function instantQR(seance, debut, duree) {
    if (!seance) return null;
    const instants = seance.evenements.filter(e => e.type === 'qr_ouvrir').map(e => e.horodatage - debut);
    if (!instants.length) return null;
    const t = Math.min(...instants);
    return t > 0.5 && t < duree ? t : null;
  }
  function majCoupeQR() {
    const seance = etatPage.seance !== null ? etatPage.seances[etatPage.seance] : null;
    const f = fichierVideo();
    const existe = !!seance && seance.evenements.some(e => e.type === 'qr_ouvrir');
    const t = f && seance ? instantQR(seance, debutEnregistrement(), f.duree || 0) : null;
    const case_ = $('#couper-qr');
    if (etatPage.seanceCoupe !== etatPage.seance) { case_.checked = existe; etatPage.seanceCoupe = etatPage.seance; }
    case_.disabled = !existe;
    $('#couper-qr-aide').textContent = !seance ? '' : !existe ? '(pas de questions-réponses dans cette séance)' : !f ? '(choisir la vidéo pour situer la coupe)' : t !== null ? '(à ' + formaterDuree(t) + ' dans la vidéo)' : '(ouvertes hors de la vidéo : pas de coupe)';
    return case_.checked && t !== null ? t : null;
  }

  /** Même algorithme que server/replays.py : instants dans [0, durée], pas de chapitre sur un fragment, fusion < 20 s, premier à 0. */
  function calculerChapitres(evenements, debut, duree, titres) {
    const bruts = [];
    let dernierH = null;
    [...evenements].sort((a, b) => a.horodatage - b.horodatage).forEach(ev => {
      if (ev.type !== 'diapositive' || ev.h === null || ev.h === undefined) return;
      const instant = ev.horodatage - debut;
      if (instant < 0 || instant > duree) return;
      if (ev.h === dernierH) return;
      dernierH = ev.h;
      bruts.push({ debut: instant, titre: titres[ev.h] || ('Diapositive ' + (ev.h + 1)), h: ev.h });
    });
    const chapitres = [];
    bruts.forEach(c => {
      if (chapitres.length && c.debut - chapitres[chapitres.length - 1].debut < FUSION_S) {
        const d = chapitres[chapitres.length - 1]; d.titre = c.titre; d.h = c.h;
      } else chapitres.push({ ...c });
    });
    if (chapitres.length) chapitres[0].debut = 0;
    return chapitres;
  }

  function rendreApercu() {
    const corps = $('#apercu tbody');
    corps.textContent = '';
    const f = fichierVideo();
    const debut = debutEnregistrement();
    $('#debut-estime').textContent = debut !== null ? formaterDate(debut, { day: '2-digit', month: '2-digit', year: 'numeric' }) + ':' + pad(new Date(debut * 1000).getSeconds()) + (f ? ' (fin du fichier − durée)' : '') : '—';
    const seance = etatPage.seance !== null ? etatPage.seances[etatPage.seance] : null;
    const vide = $('#apercu-vide');
    const resume = $('#apercu-resume');
    if (!f || !seance) {
      majCoupeQR();
      vide.hidden = false; resume.textContent = '';
      vide.textContent = !f ? 'Choisir une vidéo pour estimer le début de l\'enregistrement.' : 'Choisir une séance.';
      $('#lancer-aide').textContent = !f ? 'Choisir d\'abord une vidéo.' : '';
      return;
    }
    const coupe = majCoupeQR();
    const chapitres = calculerChapitres(seance.evenements, debut, f.duree || 0, etatPage.titres).filter(c => coupe === null || c.debut < coupe);
    vide.hidden = chapitres.length > 0;
    if (!chapitres.length) {
      vide.textContent = 'Aucun changement de diapositive ne tombe dans la durée de la vidéo : la séance choisie correspond-elle à cet enregistrement ? Ajuster le décalage manuel si besoin (écart séance → vidéo : ' + Math.round(seance.debut - debut) + ' s).';
    }
    resume.textContent = chapitres.length ? '(' + chapitres.length + ' chapitre' + (chapitres.length > 1 ? 's' : '') + ', diapositives dans la vidéo : ' + seance.evenements.filter(e => e.type === 'diapositive' && e.horodatage - debut >= 0 && e.horodatage - debut <= (f.duree || 0)).length + '/' + seance.evenements.filter(e => e.type === 'diapositive').length + ')' : '';
    chapitres.forEach(c => corps.append(h('tr', {}, h('td', { class: 'temps' }, formaterDuree(c.debut)), h('td', {}, c.titre))));
    if (coupe !== null) corps.append(h('tr', { class: 'coupe' }, h('td', { class: 'temps' }, formaterDuree(coupe)), h('td', { class: 'sourd' }, '✂ fin du replay : questions-réponses (coupées)')));
    $('#lancer-aide').textContent = chapitres.length ? '' : 'Le replay sera assemblé sans chapitres.';
  }

  function majTitreDefaut() {
    const champ = $('#titre');
    const seance = etatPage.seance !== null ? etatPage.seances[etatPage.seance] : null;
    const debut = seance ? seance.debut : debutEnregistrement();
    const date = debut ? new Date(debut * 1000).toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' }) : '';
    champ.placeholder = etatPage.titrePrez ? etatPage.titrePrez + (date ? ' – ' + date : '') : 'Titre du replay';
  }

  // ======================================================================
  // 4. Assemblage et suivi de la tâche
  // ======================================================================
  async function assembler() {
    const prez = $('#prez').value;
    if (!prez) { etat('choisir une présentation', 'erreur'); return; }
    if (!etatPage.video) { etat('choisir l\'enregistrement OBS (vidéo)', 'erreur'); return; }
    const sansAudio = $('#sans-audio').checked;
    if (!sansAudio && !etatPage.audio) { etat('choisir l\'audio du téléphone, ou cocher « pas de recalage audio »', 'erreur'); return; }
    const seance = etatPage.seance !== null ? etatPage.seances[etatPage.seance] : null;
    if (!seance && !confirm('Aucune séance choisie : le replay sera assemblé sans chapitres. Continuer ?')) return;
    let serveur = ($('#serveur').value || '').trim().replace(/\/+$/, '');
    if (serveur && !/^https?:\/\//i.test(serveur)) serveur = 'https://' + serveur;
    const corps = {
      prez, video: etatPage.video, audio: sansAudio ? null : etatPage.audio,
      seance_debut: seance ? seance.debut : null, seance_fin: seance ? seance.fin : null,
      decalage: parseFloat($('#decalage').value) || 0,
      titre: $('#titre').value.trim() || $('#titre').placeholder,
      garder_reference: $('#garder-reference').checked,
      extraits: $('#extraits').checked,
      couper_qr: $('#couper-qr').checked && !$('#couper-qr').disabled,
      serveur: seance && serveur ? serveur : null,
      jeton_serveur: seance && serveur ? ($('#jeton-serveur').value || token) : null,
    };
    $('#b-assembler').disabled = true;
    $('#progression').classList.add('visible');
    $('#etapes').textContent = ''; $('#journal').textContent = ''; $('#resultat').textContent = '';
    try {
      const r = await api('/api/postprod/assembler', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corps) });
      etatPage.tache = r.tache;
      etat('assemblage lancé…');
      suivre();
    } catch (e) {
      $('#b-assembler').disabled = false;
      afficherResultat(false, e.message);
      etat('erreur : ' + e.message, 'erreur');
    }
  }

  async function suivre() {
    clearTimeout(etatPage.minuteur);
    let t;
    try {
      t = await api('/api/postprod/taches/' + encodeURIComponent(etatPage.tache));
    } catch (e) {
      $('#journal').textContent += '\n(serveur local injoignable : ' + e.message + ' ; nouvel essai dans 2 s)';
      etatPage.minuteur = setTimeout(suivre, 2000);
      return;
    }
    rendreTache(t);
    if (t.etat === 'en_cours') { etatPage.minuteur = setTimeout(suivre, 2000); return; }
    $('#b-assembler').disabled = false;
    if (t.etat === 'terminee') {
      afficherResultat(true, 'Replay prêt' + (t.replay && t.replay.chapitres !== undefined ? ' : ' + t.replay.chapitres + ' chapitre' + (t.replay.chapitres > 1 ? 's' : '') : ''), t.replay);
      etat('replay assemblé', 'ok');
    } else {
      afficherResultat(false, t.erreur || 'erreur inconnue');
      etat('erreur : ' + (t.erreur || 'échec'), 'erreur');
    }
  }

  function rendreTache(t) {
    const etapes = $('#etapes');
    etapes.textContent = '';
    (t.etapes || []).forEach(e => etapes.append(h('span', { class: e.etat }, e.nom)));
    const journal = $('#journal');
    const enBas = journal.scrollTop + journal.clientHeight >= journal.scrollHeight - 8;
    journal.textContent = (t.journal || []).join('\n');
    if (enBas) journal.scrollTop = journal.scrollHeight;
  }

  function afficherResultat(ok, message, replay) {
    const zone = $('#resultat');
    zone.textContent = '';
    zone.append(h('span', { class: 'message ' + (ok ? 'ok' : 'erreur') }, (ok ? '✔ ' : '✖ ') + message));
    if (ok && replay && replay.id) {
      zone.append(h('a', { class: 'bouton', href: '/deck/replay.html?prez=' + encodeURIComponent(replay.slug) + '&id=' + encodeURIComponent(replay.id) + '&token=' + encodeURIComponent(token) }, '▶ Lire le replay'));
      zone.append(h('a', { href: '/deck/presentations.html?token=' + encodeURIComponent(token) }, 'Retour aux présentations'));
    }
  }

  // ======================================================================
  // Démarrage
  // ======================================================================
  async function demarrer() {
    if (!token) { etat('jeton manquant : ouvrir /deck/postprod.html?token=…', 'erreur'); return; }
    $('#l-presentations').href = '/deck/presentations.html?token=' + encodeURIComponent(token);
    try {
      const conf = await api('/api/postprod/configuration');
      $('#dossier-captations').textContent = conf.captations;
      $('#dossier-captations').title = 'Dossier de captations (AMPHI_CAPTATIONS)';
      if (conf.seance_url) $('#serveur').value = conf.seance_url;
      $('#jeton-serveur').value = token;
      if (!conf.ffmpeg) {
        etat('ffmpeg introuvable sur ce PC : installer ffmpeg (https://ffmpeg.org) et l\'ajouter au PATH, puis relancer le serveur', 'erreur');
        $('#b-assembler').disabled = true;
        $('#lancer-aide').textContent = 'ffmpeg / ffprobe absents : l\'assemblage est impossible.';
      } else etat('');
      await chargerPresentations();
      await chargerCaptations();
    } catch (e) {
      etat('erreur : ' + e.message, 'erreur');
    }
  }

  $('#prez').onchange = surChangementPrez;
  $('#b-rafraichir').onclick = chargerCaptations;
  $('#b-seances').onclick = chargerSeances;
  $('#b-assembler').onclick = assembler;
  $('#sans-audio').onchange = () => { rendreCaptations(); };
  $('#decalage').oninput = () => { rendreApercu(); };
  $('#couper-qr').onchange = () => { rendreApercu(); };
  $('#serveur').onkeydown = $('#jeton-serveur').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); chargerSeances(); } };
  demarrer();
})();

/*
 * Page « séance » (docs/seance.md §F) : /deck/seance.html?prez=<slug>&id=<epoch du début>&token=<jeton>
 *  - la présentation telle qu'elle était (copie figée, sinon version actuelle) rejouée au rythme du journal, dans le deck
 *    en rôle « seance » (iframe pilotée par postMessage) ;
 *  - la vidéo du replay si elle est sur ce serveur : elle sert alors d'horloge ;
 *  - le déroulé (diapositives, questions, questions-réponses) et les résultats de chaque question ;
 *  - l'export PDF (deck en rôle « impression »).
 * JS vanilla.
 */
(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const params = new URLSearchParams(location.search);
  const token = params.get('token') || '';
  const prez = params.get('prez') || '';
  const id = params.get('id') || '';
  const pad = n => String(n).padStart(2, '0');
  const mmss = s => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600); return (h ? h + ':' + pad(Math.floor(s % 3600 / 60)) : pad(Math.floor(s / 60))) + ':' + pad(s % 60); };
  const esc = t => String(t ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  let S = null;           // détail de la séance (API)
  let diapos = [];        // événements « diapositive » (t relatif au début)
  let duree = 0;          // durée de la séance (s)
  let t = 0;              // position courante (s depuis le début de la séance)
  let lecture = false, minuteur = null, dernier = 0;
  let positionEnvoyee = '';
  const deck = $('#deck'), video = $('#video');

  function etat(texte, classe) { const el = $('#etat'); el.textContent = texte; el.className = 'etat' + (classe ? ' ' + classe : ''); }

  async function charger() {
    if (!token || !prez || !id) { etat('paramètres manquants : prez, id, token', 'erreur'); return; }
    $('#l-presentations').href = '/deck/presentations.html?token=' + encodeURIComponent(token);
    const r = await fetch('/api/presentations/' + encodeURIComponent(prez) + '/historique/' + encodeURIComponent(id) + '?token=' + encodeURIComponent(token), { cache: 'no-store' });
    if (!r.ok) { etat(r.status === 403 ? 'jeton refusé' : 'séance introuvable', 'erreur'); return; }
    S = await r.json();
    duree = Math.max(1, S.fin - S.debut);
    diapos = S.evenements.filter(e => e.type === 'diapositive').map(e => ({ ...e, t: e.horodatage - S.debut }));
    const date = new Date(S.debut * 1000);
    document.title = S.titre + ' – séance du ' + date.toLocaleDateString('fr-FR');
    $('#titre').textContent = S.titre + ' · ' + date.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
      + ' ' + date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    $('#info-version').textContent = S.copie
      ? 'Présentation telle qu\'elle était pendant la séance (copie du ' + S.copie.replace('_', ' à ').replace(/(\d{2})(\d{2})(\d{2})$/, '$1:$2:$3') + ').'
      : 'Pas de copie figée pour cette séance (l\'enregistrement OBS n\'a pas été signalé) : version actuelle de la présentation, elle peut différer de celle projetée.';
    $('#curseur').max = String(Math.round(duree));
    $('#duree').textContent = mmss(duree);
    deck.src = '/deck/?role=seance&prez=' + encodeURIComponent(prez) + (S.copie ? '&copie=' + encodeURIComponent(S.copie) : '');
    if (S.replay) {
      $('#bloc-video').hidden = false;
      video.src = '/p/' + encodeURIComponent(prez) + '/replays/' + encodeURIComponent(S.replay.id) + '/replay.mp4';
      $('#info-video').textContent = 'Replay « ' + S.replay.titre + ' » : la vidéo commande la relecture.';
      video.addEventListener('timeupdate', () => { if (!video.paused || video.seeking) placer(S.replay.debut_video - S.debut + video.currentTime, false); });
      video.addEventListener('play', () => { lecture = true; $('#b-lecture').textContent = '❚❚'; });
      video.addEventListener('pause', () => { lecture = false; $('#b-lecture').textContent = '▶'; });
    }
    rendreDeroule();
    placer(0);
    etat(diapos.length + ' changements de diapositive, ' + Object.keys(S.resultats).length + ' question(s)');
  }

  // ---- Déroulé ----
  function titreDiapo(h) { return S.titres[h] || ('Diapositive ' + (h + 1)); }
  function rendreDeroule() {
    const ol = $('#deroule');
    ol.textContent = '';
    let precedent = null;
    S.evenements.forEach(e => {
      const te = e.horodatage - S.debut;
      if (e.type === 'diapositive') {
        if (precedent === e.h) return;  // fragments d'une même diapositive
        precedent = e.h;
        const quiz = /^Quiz/.test(titreDiapo(e.h));
        const li = document.createElement('li');
        li.className = quiz ? 'quiz' : '';
        li.dataset.t = te; li.dataset.h = e.h;
        li.innerHTML = '<span class="t">' + mmss(te) + '</span><span class="titre">' + (e.h + 1) + ' · ' + esc(titreDiapo(e.h)) + '</span>';
        li.onclick = () => aller(te + 0.01);
        ol.append(li);
      } else if (['qr_ouvrir', 'qr_fermer', 'enregistrement_debut', 'enregistrement_fin', 'fin'].includes(e.type)) {
        const libelle = { qr_ouvrir: 'questions-réponses ouvertes', qr_fermer: 'questions-réponses fermées', enregistrement_debut: '⏺ enregistrement OBS démarré', enregistrement_fin: '⏹ enregistrement OBS arrêté', fin: 'fin de séance' }[e.type];
        const li = document.createElement('li');
        li.className = 'evenement';
        li.innerHTML = '<span class="t">' + mmss(te) + '</span><span>' + libelle + '</span>';
        ol.append(li);
      }
    });
    $('#resume').textContent = '(' + mmss(duree) + ')';
  }

  // ---- Position ----
  function etatA(sec) {
    let d = null;
    for (const e of diapos) { if (e.t <= sec) d = e; else break; }
    return d || diapos[0] || { h: 0, v: 0, f: -1 };
  }
  function placer(sec, depuisCurseur) {
    t = Math.max(0, Math.min(duree, sec));
    if (!depuisCurseur) $('#curseur').value = String(Math.round(t));
    $('#temps').textContent = mmss(t);
    const d = etatA(t);
    const cle = d.h + '/' + (d.v || 0) + '/' + (d.f ?? -1);
    if (cle !== positionEnvoyee && deck.contentWindow) {
      deck.contentWindow.postMessage({ type: 'aller', h: d.h, v: d.v || 0, f: d.f ?? -1 }, location.origin);
      positionEnvoyee = cle;
    }
    let courant = null;
    document.querySelectorAll('#deroule li[data-t]').forEach(li => { if (+li.dataset.t <= t) courant = li; li.classList.remove('courant'); });
    if (courant) { courant.classList.add('courant'); if (lecture) courant.scrollIntoView({ block: 'nearest' }); }
    afficherResultat(d.h);
  }
  function afficherResultat(h) {
    const zone = $('#resultat-courant');
    const qid = (S.questionsParDiapo || {})[h];
    const r = qid && S.resultats[qid];
    zone.hidden = !r;
    if (r && zone.dataset.q !== qid) {
      zone.dataset.q = qid;
      zone.innerHTML = '<h3>' + esc(r.question.question || 'Question') + '</h3>' + AmphiResultats.rendre(r.question, r.resultat)
        + (r.source === 'reponses' ? '<p class="res-note">Recalculé à partir des réponses enregistrées.</p>' : '');
    }
  }
  function aller(sec) {
    if (S.replay) {
      const tv = sec - (S.replay.debut_video - S.debut);
      if (tv >= 0 && tv <= S.replay.duree) { video.currentTime = tv; placer(sec); return; }
    }
    placer(sec);
  }

  // ---- Lecture sans vidéo : horloge interne ----
  function basculer() {
    if (S && S.replay) {
      const tv = t - (S.replay.debut_video - S.debut);
      if (tv >= 0 && tv < S.replay.duree) { if (video.paused) { video.currentTime = tv; video.play(); } else video.pause(); return; }
    }
    lecture = !lecture;
    $('#b-lecture').textContent = lecture ? '❚❚' : '▶';
    clearInterval(minuteur);
    if (lecture) {
      dernier = performance.now();
      minuteur = setInterval(() => {
        const maintenant = performance.now();
        placer(t + (maintenant - dernier) / 1000 * Number($('#vitesse').value));
        dernier = maintenant;
        if (t >= duree) basculer();
      }, 200);
    }
  }

  $('#b-lecture').onclick = basculer;
  $('#curseur').oninput = e => aller(Number(e.target.value));
  $('#vitesse').onchange = () => { video.playbackRate = Number($('#vitesse').value); };
  $('#b-pdf').onclick = () => {
    if (!S) return;
    window.open('/deck/?role=impression&prez=' + encodeURIComponent(prez) + (S.copie ? '&copie=' + encodeURIComponent(S.copie) : '')
      + '&historique=' + encodeURIComponent(id) + '&token=' + encodeURIComponent(token), '_blank');
  };
  window.addEventListener('message', e => {
    if (e.origin !== location.origin || !e.data || e.data.type !== 'deck-pret' || !S) return;
    positionEnvoyee = '';
    deck.contentWindow.postMessage({ type: 'resultats', resultats: S.resultats }, location.origin);
    placer(t);
  });

  // Question de chaque diapositive de quiz : lue dans la copie (quiz) via les titres « Quiz : … » ne suffit pas ; on prend
  // l'ordre des événements : la question ouverte pendant qu'une diapositive est affichée lui appartient.
  function associerQuestions() {
    S.questionsParDiapo = {};
    let h = null;
    S.evenements.forEach(e => {
      if (e.type === 'diapositive') h = e.h;
      else if (e.type === 'quiz_ouvrir' && e.question && h !== null && !(h in S.questionsParDiapo)) S.questionsParDiapo[h] = e.question;
    });
  }

  charger().then(() => { if (S) { associerQuestions(); placer(t); } }).catch(e => etat('erreur : ' + e.message, 'erreur'));
})();

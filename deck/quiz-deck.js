/*
 * Éléments dynamiques du deck (voir docs/format-deck.md) :
 *
 *   <section data-quiz="q1">            diapositive de quiz : question + options statiques (secours
 *                                       à main levée), QR code, résultats en direct.
 *   <section data-qr>                   QR code et URL participant.
 *   [data-amphi="resultats"]            iframe /q/resultats?question=…
 *   [data-amphi="classement"]           iframe /q/classement?nombre=… (data-ceremonie="1" : &ceremonie=1)
 *   [data-amphi="ceremonie"]            fragment invisible : au fragmentshown, lance la cérémonie dans les iframes de la section.
 *   [data-amphi="qr"]                   QR code + URL.
 *   [data-amphi="lottie"]               animation Lottie (lottie-web), jouée à l'affichage.
 *   [data-amphi="minuteur"]             compte à rebours démarré à l'affichage (ou au fragment).
 *   [data-amphi="video"]                vidéo lancée à l'affichage de la diapositive, jamais en autoplay HTML.
 *
 * Séquence questions-réponses (docs/questions.md) : sur l'événement `qr` d'AmphiSync, bandeau de la question en
 * cours en bas de l'écran ou indicateur discret en haut à droite ; rôles scene et obs seulement.
 */
window.AmphiQuizDeck = (function () {
  let questions = {};
  let urlPublique = location.origin;

  /** `prez` : slug d'une présentation prévisualisée (ses questions, pas celles du quiz actif). */
  async function preparer(racine, prez) {
    try {
      const [rq, re] = await Promise.all([
        fetch('/api/quiz/questions' + (prez ? '?prez=' + encodeURIComponent(prez) : ''), { cache: 'no-store' }),
        fetch('/api/quiz/etat', { cache: 'no-store' }),
      ]);
      if (rq.ok) questions = Object.fromEntries((await rq.json()).map(q => [q.id, q]));
      if (re.ok) urlPublique = (await re.json()).url_publique || urlPublique;
    } catch (e) { console.warn('quiz indisponible, diapositives de quiz laissées telles quelles', e); }

    racine.querySelectorAll('section[data-qr]').forEach(s => {
      // Section libre (PowerPoint converti) : QR code en bas à droite, sur fond blanc ; section en flux : centré sous le contenu.
      const libre = s.classList.contains('libre');
      s.insertAdjacentHTML('beforeend', libre
        ? `<div class="el qr-libre" data-amphi="qr-fixe" style="left:1500px;top:720px;width:380px;height:300px">${blocQr()}</div>`
        : blocQr());
    });
    racine.querySelectorAll('section[data-quiz]:not(.libre)').forEach(construireQuiz);
    racine.querySelectorAll('[data-amphi]').forEach(construireElement);
  }

  // ----- Diapositive de quiz standard ---------------------------------------------------------
  function construireQuiz(section) {
    const id = section.dataset.quiz, q = questions[id];
    section.classList.add('quiz');
    if (!q) {
      if (!section.querySelector('.quiz-statique')) section.insertAdjacentHTML('beforeend', `<p class="quiz-erreur">Question « ${echap(id)} » absente de quiz.json</p>`);
      return;
    }
    const notes = section.dataset.notes || section.querySelector('aside.notes')?.innerHTML || `Quiz ${q.id} (${q.type}) : ouvrir depuis la télécommande, laisser le temps, fermer, révéler.`;
    section.innerHTML = `
      <div class="quiz-grille ${q.type}">
        <div class="quiz-statique">
          <h2>${echap(q.type === 'trou' ? q.texte.replace('___', '______') : q.question)}</h2>
          ${optionsStatiques(q)}
          ${blocQr()}
        </div>
        <iframe class="quiz-resultats" src="/q/resultats?question=${encodeURIComponent(q.id)}" title="Résultats" loading="eager"></iframe>
      </div>
      <aside class="notes">${notes}</aside>`;
  }

  function optionsStatiques(q) {
    if (['qcm', 'multi', 'rapidite', 'classement'].includes(q.type)) return `<ol class="quiz-options">${q.options.map(o => `<li>${echap(o)}</li>`).join('')}</ol>`;
    if (q.type === 'emoji') return `<div class="quiz-emojis">${q.options.map(o => `<span>${echap(o)}</span>`).join('')}</div>`;
    if (q.type === 'vrai_faux') return `<ol class="quiz-options"><li>Vrai</li><li>Faux</li></ol>`;
    if (q.type === 'echelle') return `<p class="quiz-aide">De ${q.min} (${echap(q.libelle_min || 'min')}) à ${q.max} (${echap(q.libelle_max || 'max')})</p>`;
    const aides = { buzzer: 'Appuyez le plus vite possible !', nuage: `Jusqu'à ${q.max_mots} mots`, texte_libre: 'Réponse libre', trou: 'Complétez le texte', nombre: `Votre estimation${q.unite ? ' en ' + echap(q.unite) : ''}` };
    return `<p class="quiz-aide">${aides[q.type] || ''}</p>`;
  }

  // ----- Éléments data-amphi ----------------------------------------------------------------
  function construireElement(el) {
    const genre = el.dataset.amphi;
    if (genre === 'resultats') {
      el.innerHTML = `<iframe class="quiz-resultats" src="/q/resultats?question=${encodeURIComponent(el.dataset.question || '')}&couleur=${couleurDe(el)}${el.dataset.sansTitre ? '&sans_titre=1' : ''}" title="Résultats" loading="eager"></iframe>`;
    } else if (genre === 'classement') {
      // data-ceremonie="1" : cérémonie animée, déclenchée par le fragment [data-amphi="ceremonie"] de la section (docs/seance.md, D).
      el.innerHTML = `<iframe class="quiz-resultats" src="/q/classement?nombre=${encodeURIComponent(el.dataset.nombre || 10)}&couleur=${couleurDe(el)}${el.dataset.sansTitre ? '&sans_titre=1' : ''}${el.dataset.ceremonie === '1' ? '&ceremonie=1' : ''}" title="Classement" loading="eager"></iframe>`;
    } else if (genre === 'qr') {
      el.innerHTML = blocQr();
    } else if (genre === 'minuteur') {
      el.classList.add('minuteur');
      if (el.dataset.question) {
        // Minuteur lié à une question : affiche son chrono, démarre à l'ouverture, se fige à la fermeture.
        el.classList.add('lie');
        const q = questions[el.dataset.question];
        if (q && q.chrono) el.dataset.duree = q.chrono;
        minuteursLies.push(el);
      }
      el.textContent = formater(Number(el.dataset.duree) || 60);
    } else if (genre === 'lottie') {
      el.classList.add('lottie');
    }
  }

  // ----- Minuteurs liés au quiz : suivent l'état du serveur (/ws/quiz) -----------------------
  const minuteursLies = [];
  function appliquerEtatQuiz(etat) {
    const id = etat.question && etat.question.id;
    minuteursLies.forEach(el => {
      if (el.dataset.question !== id) return;
      if (etat.ouverte && etat.reste !== null && etat.reste !== undefined) {
        demarrerMinuteur(el, etat.reste);
        el.classList.add('actif');
      } else if (etat.ouverte) {
        el.classList.add('actif');
      } else {
        arreterMinuteur(el);
        el.classList.remove('actif');
        if (etat.revele) { el.classList.add('fini'); el.textContent = '✔'; }
        else if (etat.resultat && etat.resultat.total > 0) { el.textContent = '0:00'; el.classList.add('fini'); }
      }
    });
  }
  (function ecouterQuiz() {
    let delai = 1000;
    function connecter() {
      const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/quiz');
      ws.onopen = () => { delai = 1000; };
      ws.onmessage = e => { const m = JSON.parse(e.data); if (m.type === 'quiz') appliquerEtatQuiz(m); };
      ws.onclose = () => { setTimeout(connecter, delai); delai = Math.min(delai * 2, 10000); };
      ws.onerror = () => ws.close();
    }
    if (AmphiSyncRoleUtile()) connecter();
  })();
  function AmphiSyncRoleUtile() { return !(window.AmphiSync && window.AmphiSync.role === 'remote'); }

  // Lancements à l'affichage d'une diapositive : Lottie, minuteurs, vidéos.
  function surDiapositive(section) {
    section.querySelectorAll('[data-amphi="lottie"]').forEach(jouerLottie);
    section.querySelectorAll('[data-amphi="minuteur"]:not([data-question])').forEach(el => { if (el.dataset.auto !== '0' && !el.classList.contains('fragment')) demarrerMinuteur(el); });
    section.querySelectorAll('[data-amphi="video"] video, video[data-amphi="video"]').forEach(v => { v.currentTime = 0; v.play().catch(() => {}); });
  }
  function horsDiapositive(section) {
    section.querySelectorAll('video').forEach(v => v.pause());
    section.querySelectorAll('[data-amphi="minuteur"]:not([data-question])').forEach(arreterMinuteur);
    section.querySelectorAll('[data-amphi="lottie"]').forEach(el => { if (el._lottie) el._lottie.stop(); });
  }
  function surFragment(fragment) {
    if (fragment.dataset.amphi === 'minuteur') demarrerMinuteur(fragment);
    fragment.querySelectorAll?.('[data-amphi="minuteur"]').forEach(demarrerMinuteur);
    if (fragment.dataset.amphi === 'ceremonie') lancerCeremonie(fragment);
  }
  /** Déclencheur de la cérémonie du classement : toutes les iframes de la section reçoivent {type:"ceremonie"}
   *  (la miniature de la télécommande la rejoue aussi, sans conséquence). */
  function lancerCeremonie(fragment) {
    const section = fragment.closest('section') || fragment.parentElement;
    if (!section) return;
    section.querySelectorAll('iframe').forEach(f => { try { f.contentWindow?.postMessage({ type: 'ceremonie' }, '*'); } catch (e) { /* iframe non chargée */ } });
  }

  function jouerLottie(el) {
    if (!window.lottie) return;
    if (el._lottie) { el._lottie.goToAndPlay(0, true); return; }
    el._lottie = window.lottie.loadAnimation({ container: el, renderer: 'svg', loop: el.dataset.boucle !== '0', autoplay: true, path: el.dataset.src });
  }

  function demarrerMinuteur(el, reste) {
    arreterMinuteur(el);
    const duree = reste !== undefined ? reste : (Number(el.dataset.duree) || 60);
    const fin = Date.now() + duree * 1000;
    el.classList.remove('fini');
    const tic = () => {
      const reste = Math.max(0, Math.ceil((fin - Date.now()) / 1000));
      el.textContent = formater(reste);
      if (reste <= 0) { el.classList.add('fini'); arreterMinuteur(el); }
    };
    tic();
    el._minuteur = setInterval(tic, 250);
  }
  function arreterMinuteur(el) { if (el._minuteur) { clearInterval(el._minuteur); el._minuteur = null; } }
  function formater(s) { return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; }

  // ----- Séquence questions-réponses : overlays posés sur <body>, hors de .reveal (ils survivent aux rechargements du deck)
  let etatQR = { actif: false, question: null, restantes: 0 };
  function appliquerQR(m) {
    if (!AmphiSyncRoleUtile()) return; // la miniature de la télécommande n'affiche pas d'overlay
    etatQR = { actif: !!m.actif, question: m.question || null, restantes: Number(m.restantes) || 0 };
    rendreQR();
  }
  function rendreQR() {
    let bandeau = document.getElementById('qr-bandeau'), indicateur = document.getElementById('qr-indicateur');
    if (!etatQR.actif) { bandeau?.remove(); indicateur?.remove(); return; }
    const dsfr = !!document.querySelector('.reveal .slides section.present.dsfr, .reveal .slides section.present section.dsfr, .reveal .slides section.dsfr.present');
    if (etatQR.question) {
      indicateur?.remove();
      const q = etatQR.question;
      if (!bandeau) {
        bandeau = document.createElement('div');
        bandeau.id = 'qr-bandeau'; bandeau.className = 'qr-bandeau'; bandeau.setAttribute('role', 'status');
        document.body.appendChild(bandeau);
      } else if (bandeau.dataset.id !== String(q.id)) {
        bandeau.classList.remove('qr-entree'); void bandeau.offsetWidth; // rejoue l'animation d'entrée pour une nouvelle question
      }
      bandeau.dataset.id = String(q.id);
      bandeau.classList.toggle('dsfr', dsfr);
      bandeau.classList.add('qr-entree');
      bandeau.innerHTML = `<div class="qr-sur">Question${q.generale ? '' : ' · diapositive ' + echap(q.numero)}${etatQR.restantes ? `<span class="qr-reste">${etatQR.restantes} restante${etatQR.restantes > 1 ? 's' : ''}</span>` : ''}</div><div class="qr-texte">${echap(q.texte)}</div>`;
    } else {
      bandeau?.remove();
      if (!indicateur) {
        indicateur = document.createElement('div');
        indicateur.id = 'qr-indicateur'; indicateur.className = 'qr-indicateur';
        document.body.appendChild(indicateur);
      }
      indicateur.classList.toggle('dsfr', dsfr);
      indicateur.innerHTML = `<span class="qr-point"></span>Questions-réponses <b>${etatQR.restantes}</b> question${etatQR.restantes > 1 ? 's' : ''}`;
    }
  }
  /** Branche les overlays sur AmphiSync et relit l'état courant (écran rechargé pendant une séquence). */
  function demarrerQR() {
    if (!AmphiSyncRoleUtile() || !window.AmphiSync) return;
    window.AmphiSync.on('qr', appliquerQR);
    if (window.Reveal) Reveal.on('slidechanged', () => { if (etatQR.actif) rendreQR(); });
    fetch('/api/qr', { cache: 'no-store' }).then(r => r.ok ? r.json() : null).then(e => { if (e) appliquerQR(e); }).catch(() => {});
  }

  // Couleur du texte de l'élément hôte (style inline `color`), transmise à l'iframe qui ne peut pas en hériter.
  function couleurDe(el) {
    const c = (el.style && el.style.color) || '';
    return encodeURIComponent(c.replace(/^#/, ''));
  }

  function blocQr() {
    const lisible = urlPublique.replace(/^https?:\/\//, '') + '/q';
    return `<div class="quiz-qr"><img src="/qr.png" alt="QR code vers ${echap(lisible)}"><div class="quiz-url">${echap(lisible)}</div></div>`;
  }
  function echap(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

  return { preparer, surDiapositive, horsDiapositive, surFragment, appliquerQR, demarrerQR, get questions() { return questions; } };
})();

/*
 * Diapositives de quiz dans le deck.
 *
 * Une diapositive de quiz est une <section data-quiz="q1">. Ce script y écrit :
 *   - la question et ses options en HTML statique (secours : vote à main levée si le serveur tombe) ;
 *   - le QR code et l'URL participant en clair ;
 *   - une iframe vers /q/resultats?question=q1 (page publique, lecture seule, sans état).
 * Une <section data-qr> (première diapositive) reçoit seulement le QR code et l'URL.
 */
window.AmphiQuizDeck = (function () {
  async function preparer(racine) {
    let questions = [];
    let urlPublique = location.origin;
    try {
      const [rq, re] = await Promise.all([
        fetch('/api/quiz/questions', { cache: 'no-store' }),
        fetch('/api/quiz/etat', { cache: 'no-store' }),
      ]);
      if (rq.ok) questions = await rq.json();
      if (re.ok) urlPublique = (await re.json()).url_publique || urlPublique;
    } catch (e) { console.warn('quiz indisponible, diapositives de quiz laissées telles quelles', e); }
    const parId = Object.fromEntries(questions.map(q => [q.id, q]));

    racine.querySelectorAll('section[data-qr]').forEach(section => {
      section.insertAdjacentHTML('beforeend', blocQr(urlPublique));
    });

    racine.querySelectorAll('section[data-quiz]').forEach(section => {
      const id = section.dataset.quiz;
      const q = parId[id];
      section.classList.add('quiz');
      if (!q) {
        // Question inconnue du serveur : on garde le contenu statique écrit dans slides.html.
        if (!section.querySelector('.quiz-statique')) section.insertAdjacentHTML('beforeend', '<p class="quiz-erreur">Question « ' + echap(id) + ' » absente de quiz.json</p>');
        return;
      }
      section.innerHTML = `
        <div class="quiz-grille">
          <div class="quiz-statique">
            <h2>${echap(q.question)}</h2>
            <ol class="quiz-options">${q.options.map(o => `<li>${echap(o)}</li>`).join('')}</ol>
            ${blocQr(urlPublique)}
          </div>
          <iframe class="quiz-resultats" src="/q/resultats?question=${encodeURIComponent(q.id)}" title="Résultats" loading="eager"></iframe>
        </div>
        <aside class="notes">${echap(section.dataset.notes || 'Quiz ' + q.id + ' : ouvrir la question depuis la télécommande, laisser 60 à 90 s, fermer, commenter l\'histogramme.')}</aside>`;
    });
  }

  function blocQr(url) {
    const lisible = url.replace(/^https?:\/\//, '') + '/q';
    return `<div class="quiz-qr"><img src="/qr.png" alt="QR code vers ${echap(lisible)}"><div class="quiz-url">${echap(lisible)}</div></div>`;
  }

  function echap(s) { return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

  return { preparer };
})();

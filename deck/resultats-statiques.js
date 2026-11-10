/*
 * Résultats d'une question rendus en HTML statique (page « séance » et export PDF, docs/seance.md §F).
 * Entrée : la question complète (quiz.json) et l'agrégat de server/quiz.py `resultat()`. Aucune donnée individuelle
 * hors pseudo (comme l'écran de la salle). JS vanilla ; expose window.AmphiResultats.
 */
(() => {
  'use strict';
  const esc = t => String(t === null || t === undefined ? '' : t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const pct = (n, total) => total ? Math.round(n / total * 100) : 0;

  function barres(libelles, comptes, total, bonnes) {
    const maxi = Math.max(1, ...comptes);
    return '<ol class="res-barres">' + libelles.map((l, i) => {
      const n = comptes[i] || 0, juste = bonnes && bonnes.includes(i);
      return `<li class="${juste ? 'juste' : ''}"><span class="res-libelle">${juste ? '✔ ' : ''}${esc(l)}</span>`
        + `<span class="res-piste"><span class="res-barre" style="width:${(n / maxi * 100).toFixed(1)}%"></span></span>`
        + `<span class="res-valeur">${n} · ${pct(n, total)} %</span></li>`;
    }).join('') + '</ol>';
  }

  function rendre(q, r) {
    if (!q || !r) return '<p class="res-vide">Pas de résultat enregistré.</p>';
    const total = r.total || 0;
    let corps = '';
    switch (q.type) {
      case 'qcm': case 'emoji': case 'rapidite':
        corps = barres(q.options || [], r.comptes || [], total, Number.isInteger(q.bonne) ? [q.bonne] : null);
        if (r.rapides && r.rapides.length) corps += '<p class="res-note">Plus rapides : ' + r.rapides.map(x => esc(x.pseudo) + ' (' + x.temps + ' s)').join(', ') + '</p>';
        break;
      case 'multi':
        corps = barres(q.options || [], r.comptes || [], total, q.bonnes || null);
        break;
      case 'vrai_faux':
        corps = barres(['Vrai', 'Faux'], r.comptes || [], total, typeof q.bonne === 'boolean' ? [q.bonne ? 0 : 1] : null);
        break;
      case 'echelle': {
        const libelles = [];
        for (let v = q.min; v <= q.max; v++) libelles.push(String(v) + (v === q.min && q.libelle_min ? ' – ' + q.libelle_min : v === q.max && q.libelle_max ? ' – ' + q.libelle_max : ''));
        corps = barres(libelles, r.comptes || [], total) + (r.moyenne !== null && r.moyenne !== undefined ? `<p class="res-note">Moyenne : ${r.moyenne}</p>` : '');
        break;
      }
      case 'classement':
        corps = '<ol class="res-classement">' + (r.ordre || []).map(i => `<li>${esc((q.options || [])[i])} <span class="res-note">rang moyen ${r.rang_moyen[i] ?? '–'}</span></li>`).join('') + '</ol>';
        break;
      case 'nuage': {
        const mots = r.mots || [], maxi = Math.max(1, ...mots.map(m => m.n));
        corps = '<p class="res-nuage">' + mots.map(m => `<span style="font-size:${(0.9 + m.n / maxi * 1.6).toFixed(2)}em">${esc(m.mot)}<sup>${m.n}</sup></span>`).join(' ') + '</p>';
        break;
      }
      case 'texte_libre':
        corps = '<ul class="res-textes">' + (r.textes || []).map(t => `<li>${esc(t.texte)}${t.pseudo ? ' <span class="res-note">— ' + esc(t.pseudo) + '</span>' : ''}</li>`).join('') + '</ul>';
        break;
      case 'trou':
        corps = barres((r.frequentes || []).map(f => f.texte), (r.frequentes || []).map(f => f.n), total, (r.frequentes || []).map((f, i) => f.bonne ? i : -1).filter(i => i >= 0))
          + `<p class="res-note">Bonnes réponses : ${r.bonnes_n || 0} sur ${total}</p>`;
        break;
      case 'nombre':
        corps = `<p class="res-note">Médiane : ${r.mediane ?? '–'} ${esc(q.unite || '')} · min ${r.min ?? '–'} · max ${r.max ?? '–'}${q.bonne !== null && q.bonne !== undefined ? ' · réponse attendue : ' + esc(q.bonne) : ''}</p>`;
        if (r.proches && r.proches.length) corps += '<p class="res-note">Plus proches : ' + r.proches.map(p => esc(p.pseudo) + ' (' + esc(p.valeur) + ')').join(', ') + '</p>';
        break;
      case 'grille':
        corps = '<div class="res-grille" style="grid-template-columns:repeat(' + (q.lignes || []).length + ',minmax(0,1fr))">' + (q.lignes || []).map((l, i) =>
          '<div><p class="res-total">' + esc(l) + ' · ' + ((r.repondus || [])[i] || 0) + '</p>'
          + barres(q.options || [], (r.comptes || [])[i] || [], (r.repondus || [])[i] || 0, Array.isArray(q.bonnes) && Number.isInteger(q.bonnes[i]) ? [q.bonnes[i]] : null) + '</div>').join('') + '</div>';
        break;
      case 'buzzer':
        corps = '<ol class="res-classement">' + (r.premiers || []).map(p => `<li>${esc(p.pseudo)} <span class="res-note">${p.temps} s</span></li>`).join('') + '</ol>';
        break;
      default:
        corps = '';
    }
    return `<div class="res"><p class="res-total">${total} réponse${total > 1 ? 's' : ''}</p>${corps}</div>`;
  }

  window.AmphiResultats = { rendre };
})();

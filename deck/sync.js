/*
 * Synchronisation du deck reveal.js avec le serveur amphi-replay.
 *
 * Rôles (paramètre d'URL ?role=) :
 *   scene  : PC 1, vidéoprojecteur. Suit l'état reçu, clavier actif en secours.
 *   obs    : PC 2, source Navigateur d'OBS. Suit l'état reçu, aucune transition.
 *   remote : téléphone. Source de vérité : applique localement puis publie Reveal.getIndices().
 *
 * Événements : connexion, etat, ack, recharger, qr (séquence questions-réponses, voir docs/questions.md),
 * apercu (retour caméra et niveau du micro relayés depuis OBS, télécommande ; docs/seance.md §G).
 *
 * État partagé : {h, v, f}. À la connexion et à chaque reconnexion, le client lit
 * GET /etat et se recale avant d'écouter le WebSocket. Reconnexion à délai croissant
 * (1 s, 2 s, 4 s, plafond 10 s), sans rechargement de page.
 */
window.AmphiSync = (function () {
  const params = new URLSearchParams(location.search);
  const role = ['scene', 'obs', 'remote', 'seance', 'impression'].includes(params.get('role')) ? params.get('role') : 'scene';
  const token = params.get('token') || '';
  const ecouteurs = { ack: [], connexion: [], etat: [], recharger: [], qr: [], apercu: [] };

  let ws = null;
  let delai = 1000;
  let enApplication = false; // évite de republier un état qu'on vient de recevoir
  let dernierEnvoye = null;

  function emettre(nom, donnees) { ecouteurs[nom].forEach(f => { try { f(donnees); } catch (e) { console.error(e); } }); }

  function urlWs() {
    const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
    return `${proto}${location.host}/ws/deck?role=${role}&token=${encodeURIComponent(token)}`;
  }

  function memeIndices(a, b) { return a && b && a.h === b.h && a.v === b.v && (a.f ?? -1) === (b.f ?? -1); }

  function appliquer(etat) {
    const indices = { h: Number(etat.h) || 0, v: Number(etat.v) || 0, f: etat.f === undefined || etat.f === null ? -1 : Number(etat.f) };
    const actuel = Reveal.getIndices();
    if (!memeIndices(indices, { h: actuel.h, v: actuel.v, f: actuel.f ?? -1 })) {
      enApplication = true;
      try { Reveal.slide(indices.h, indices.v, indices.f); } finally { enApplication = false; }
    }
    emettre('etat', indices);
    if (role !== 'remote' && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'ack', ...indices }));
    }
  }

  async function recaler() {
    try {
      const r = await fetch('/etat', { cache: 'no-store' });
      if (r.ok) {
        const etat = await r.json();
        if (role === 'remote') {
          // Le téléphone est la source de vérité : au retour du réseau, il réimpose son état.
          publier(true);
        } else {
          appliquer(etat);
        }
      }
    } catch (e) { /* serveur injoignable : on garde l'état local */ }
  }

  function publier(force) {
    if (role !== 'remote' || enApplication) return;
    const i = Reveal.getIndices();
    const etat = { type: 'slide', h: i.h, v: i.v, f: i.f ?? -1 };
    if (!force && memeIndices(etat, dernierEnvoye)) return;
    dernierEnvoye = etat;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(etat));
  }

  function connecter() {
    ws = new WebSocket(urlWs());
    ws.onopen = async () => {
      delai = 1000;
      emettre('connexion', { connecte: true });
      await recaler();
    };
    ws.onmessage = (e) => {
      let m; try { m = JSON.parse(e.data); } catch { return; }
      if (m.type === 'slide') {
        // Le remote n'obéit à personne… sauf au serveur quand la séquence questions-réponses déplace le deck
        // (source « qr ») : sa miniature suit, et son prochain état publié ne ramènera pas la salle en arrière.
        if (role === 'remote' && m.source !== 'qr') return;
        appliquer(m);
        if (role === 'remote') dernierEnvoye = { type: 'slide', h: Number(m.h) || 0, v: Number(m.v) || 0, f: m.f === undefined || m.f === null ? -1 : Number(m.f) };
      } else if (m.type === 'ack') {
        emettre('ack', m);
      } else if (m.type === 'recharger') {
        emettre('recharger', m);
      } else if (m.type === 'qr') {
        emettre('qr', m);
      } else if (m.type === 'apercu') {
        emettre('apercu', m); // retour caméra + niveau du micro (télécommande, première diapositive)
      }
    };
    ws.onclose = () => {
      emettre('connexion', { connecte: false });
      setTimeout(connecter, delai);
      delai = Math.min(delai * 2, 10000);
    };
    ws.onerror = () => ws.close();
  }

  function demarrer() {
    if (role === 'remote') {
      ['slidechanged', 'fragmentshown', 'fragmenthidden'].forEach(ev => Reveal.on(ev, () => publier(false)));
    }
    connecter();
    // Battement de cœur : maintient le WebSocket ouvert à travers le hotspot 4G.
    setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' })); }, 20000);
  }

  return {
    role, token, demarrer, publier,
    /** Message libre vers le serveur (télécommande : demande du retour caméra). */
    envoyer(m) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m)); },
    on(nom, f) { if (ecouteurs[nom]) ecouteurs[nom].push(f); },
    get connecte() { return !!ws && ws.readyState === WebSocket.OPEN; },
  };
})();

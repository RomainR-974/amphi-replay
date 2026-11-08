/*
 * Zone réservée à la vignette caméra d'OBS (docs/zone-camera.md).
 *
 * Scène 1920 × 1080. `camera` = { largeur, hauteur, position, marge } (valeurs résolues par le serveur,
 * /api/presentations/active) ou null (désactivée). La zone réservée est le rectangle de la caméra agrandi
 * de `marge` de chaque côté, calé dans le coin indiqué.
 *
 *   zoneCamera(camera)            → { x, y, l, h, position, marge, droite, bas } ou null
 *   chevauche(rect, zone)         → vrai si les rectangles { x, y, l, h } se recouvrent (bords exclus)
 *   reagencerBoites(boites, zone) → algorithme pur sur des boîtes { id, type, x, y, l, h } (modifiées en place) ; actions
 *   reagencer(section, zone)      → applique reagencerBoites aux .el (style inline left/top/width/height) ; actions
 *
 * tools/reserver_camera.py réimplémente reagencerBoites à l'identique (mêmes règles, mêmes seuils) : toute
 * modification ici doit y être reportée.
 */
window.AmphiCamera = (function () {
  const SCENE_L = 1920, SCENE_H = 1080;
  const DEFAUT = { largeur: 300, hauteur: 300, position: 'bas-droite', marge: 20 };
  const POSITIONS = ['bas-droite', 'bas-gauche', 'haut-droite', 'haut-gauche'];
  // Types dont on préfère réduire la hauteur ; les autres (image, qr, video, lottie…) gardent leur boîte.
  const REDUCTIBLES = ['resultats', 'classement', 'iframe', 'minuteur', 'forme', 'texte', 'nuage'];
  const TYPES = ['texte', 'image', 'forme', 'iframe', 'video', 'lottie', 'minuteur', 'resultats', 'classement', 'nuage', 'qr', 'declencheur'];
  const MIN = 0.6; // fraction minimale conservée en hauteur (règle 1) et en largeur (règle 3)

  /** Mêmes règles que normaliser_camera côté serveur : undefined/invalide → défaut, null → null. */
  function normaliser(camera) {
    if (camera === null) return null;
    if (!camera || typeof camera !== 'object') return { ...DEFAUT };
    const entier = (v, d, min = 1) => { const n = Math.round(Number(v)); return Number.isFinite(n) && n >= min ? n : d; };
    return {
      largeur: entier(camera.largeur, DEFAUT.largeur),
      hauteur: entier(camera.hauteur, DEFAUT.hauteur),
      position: POSITIONS.includes(camera.position) ? camera.position : DEFAUT.position,
      marge: entier(camera.marge, DEFAUT.marge, 0),
    };
  }

  function zoneCamera(camera) {
    const c = normaliser(camera);
    if (!c) return null;
    const droite = c.position.endsWith('droite'), bas = c.position.startsWith('bas');
    // La caméra est calée dans le coin de la scène : la zone = caméra + marge du côté intérieur (défaut : x ≥ 1600, y ≥ 760).
    const l = Math.min(SCENE_L, c.largeur + c.marge), h = Math.min(SCENE_H, c.hauteur + c.marge);
    const x = droite ? SCENE_L - l : 0, y = bas ? SCENE_H - h : 0;
    return { x, y, l, h, position: c.position, marge: c.marge, droite, bas, camera: c };
  }

  function chevauche(a, b) {
    if (!a || !b) return false;
    return a.x < b.x + b.l && a.x + a.l > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
  }
  function dansScene(r) { return r.x >= 0 && r.y >= 0 && r.x + r.l <= SCENE_L && r.y + r.h <= SCENE_H; }

  /**
   * Réagence les boîtes qui chevauchent la zone. Ordre : les `qr` d'abord (coin opposé s'il est libre), puis les autres.
   * Pour chaque boîte, dans l'ordre : 1. hauteur réduite (types réductibles, ≥ 60 %) ; 2. décalée à l'opposé de la zone
   * horizontalement si l'espace est libre ; 3. largeur réduite (≥ 60 %) ; 4. décalée verticalement si libre ; 5. laissée.
   * « Libre » : dans la scène et sans chevaucher un autre .el — sauf ceux que la boîte chevauchait déjà dans la
   * disposition d'origine (empilements décoratifs : image sur un cadre, point sur une barre de progression).
   */
  function reagencerBoites(boites, zone) {
    const actions = [];
    if (!zone) return actions;
    const origine = new Map(boites.map(b => [b, { x: b.x, y: b.y, l: b.l, h: b.h }]));
    const ordre = [...boites.filter(b => b.type === 'qr'), ...boites.filter(b => b.type !== 'qr')];
    for (const b of ordre) {
      if (!chevauche(b, zone)) continue;
      const avant = { x: b.x, y: b.y, l: b.l, h: b.h };
      const autres = boites.filter(o => o !== b && !chevauche(origine.get(o), origine.get(b)));
      const libre = r => dansScene(r) && !chevauche(r, zone) && !autres.some(o => chevauche(r, o));
      const appliquer = (action, n) => { Object.assign(b, n); actions.push({ id: b.id, type: b.type, action, avant, apres: { x: b.x, y: b.y, l: b.l, h: b.h } }); };

      if (b.type === 'qr') {
        const coin = { x: zone.droite ? zone.marge : SCENE_L - zone.marge - b.l, y: zone.bas ? SCENE_H - zone.marge - b.h : zone.marge, l: b.l, h: b.h };
        if (libre(coin)) { appliquer('coin ' + (zone.bas ? 'bas' : 'haut') + '-' + (zone.droite ? 'gauche' : 'droite'), { x: coin.x, y: coin.y }); continue; }
      }
      // 1. hauteur
      if (REDUCTIBLES.includes(b.type)) {
        const y = zone.bas ? b.y : zone.y + zone.h;
        const h = zone.bas ? zone.y - b.y : b.y + b.h - (zone.y + zone.h);
        if (h > 0 && h >= MIN * b.h) { appliquer('hauteur réduite', { y, h }); continue; }
      }
      // 2. décalage horizontal
      const dx = { x: zone.droite ? zone.x - b.l : zone.x + zone.l, y: b.y, l: b.l, h: b.h };
      if (libre(dx)) { appliquer('décalé vers la ' + (zone.droite ? 'gauche' : 'droite'), { x: dx.x }); continue; }
      // 3. largeur
      {
        const x = zone.droite ? b.x : zone.x + zone.l;
        const l = zone.droite ? zone.x - b.x : b.x + b.l - (zone.x + zone.l);
        if (l > 0 && l >= MIN * b.l) { appliquer('largeur réduite', { x, l }); continue; }
      }
      // 4. décalage vertical
      const dy = { x: b.x, y: zone.bas ? zone.y - b.h : zone.y + zone.h, l: b.l, h: b.h };
      if (libre(dy)) { appliquer('décalé vers le ' + (zone.bas ? 'haut' : 'bas'), { y: dy.y }); continue; }
      actions.push({ id: b.id, type: b.type, action: 'laissé en place (aucune règle applicable)', avant, apres: avant });
    }
    return actions;
  }

  function typeDe(el) {
    const t = TYPES.find(x => el.classList.contains(x));
    if (t) return t;
    return { IMG: 'image', IFRAME: 'iframe', VIDEO: 'video' }[el.tagName] || 'texte';
  }
  const nombre = v => { const n = parseFloat(v); return Number.isFinite(n) ? n : null; };
  /** Boîte inline d'un .el, ou null si left/top/width/height ne sont pas tous posés ou si elle est vide. */
  function boiteDe(el) {
    const x = nombre(el.style.left), y = nombre(el.style.top), l = nombre(el.style.width), h = nombre(el.style.height);
    if (x === null || y === null || l === null || h === null || l <= 0 || h <= 0) return null;
    return { x, y, l, h };
  }

  /** Réagence en mémoire les .el (enfants directs) d'une section libre ; renvoie les actions. */
  function reagencer(section, zone) {
    if (!zone) return [];
    const els = Array.from(section.children).filter(c => c.classList && c.classList.contains('el'));
    const boites = [];
    els.forEach(el => { const b = boiteDe(el); if (b) boites.push({ el, id: el.dataset.id || '', type: typeDe(el), ...b }); });
    const actions = reagencerBoites(boites, zone);
    boites.forEach(b => {
      b.el.style.left = Math.round(b.x) + 'px'; b.el.style.top = Math.round(b.y) + 'px';
      b.el.style.width = Math.round(b.l) + 'px'; b.el.style.height = Math.round(b.h) + 'px';
    });
    return actions;
  }

  /** Éléments d'une section dont la boîte inline chevauche la zone (éditeur : contour orange). */
  function elementsChevauchant(section, zone) {
    if (!zone) return [];
    return Array.from(section.children).filter(c => c.classList && c.classList.contains('el') && chevauche(boiteDe(c), zone));
  }

  return { SCENE_L, SCENE_H, DEFAUT, POSITIONS, REDUCTIBLES, MIN, normaliser, zoneCamera, chevauche, reagencerBoites, reagencer, boiteDe, typeDe, elementsChevauchant };
})();

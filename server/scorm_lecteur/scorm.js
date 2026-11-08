/*
 * Couche SCORM 1.2 du lecteur (docs/scorm.md) : recherche de l'API du LMS (window.API dans les fenêtres parentes,
 * 500 niveaux au plus, puis window.opener), LMSInitialize / LMSGetValue / LMSSetValue / LMSCommit / LMSFinish.
 * Sans LMS (zip décompressé, ouvert en file:// ou servi tel quel) : repli localStorage sous la clé « scorm:<identifiant> ».
 * Toutes les valeurs sont écrites en chaînes. JS vanilla, aucune dépendance.
 */
window.SCORM = (function () {
  'use strict';

  var api = null;
  var initialise = false;
  var termine = false;
  var autonome = false;
  var cleLocale = 'scorm:module';
  var memoire = {};
  var debutSession = Date.now();

  function chercherDans(fenetre) {
    var n = 0;
    while (fenetre && !fenetre.API && fenetre.parent && fenetre.parent !== fenetre && n < 500) {
      n++;
      fenetre = fenetre.parent;
    }
    return fenetre && fenetre.API ? fenetre.API : null;
  }

  function trouverAPI() {
    var trouvee = null;
    try { trouvee = chercherDans(window); } catch (e) { trouvee = null; }
    if (!trouvee && window.opener) {
      try { trouvee = chercherDans(window.opener); } catch (e) { trouvee = null; }
    }
    return trouvee;
  }

  function lireLocal() {
    try {
      var brut = window.localStorage.getItem(cleLocale);
      var obj = brut ? JSON.parse(brut) : {};
      return obj && typeof obj === 'object' ? obj : {};
    } catch (e) {
      return {};
    }
  }

  function ecrireLocal() {
    try { window.localStorage.setItem(cleLocale, JSON.stringify(memoire)); } catch (e) { /* stockage indisponible : on garde la mémoire */ }
  }

  /** Durée de session au format SCORM 1.2 « HHHH:MM:SS.SS ». */
  function formaterSession(ms) {
    var total = Math.max(0, ms) / 1000;
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total - h * 3600 - m * 60;
    var hh = String(h); while (hh.length < 4) hh = '0' + hh;
    var mm = m < 10 ? '0' + m : String(m);
    var ss = s.toFixed(2); if (s < 10) ss = '0' + ss;
    return hh + ':' + mm + ':' + ss;
  }

  /** Démarre la session. Renvoie true si un LMS répond, false en mode autonome. */
  function initialiser(identifiant) {
    if (initialise) return !autonome;
    cleLocale = 'scorm:' + (identifiant || 'module');
    debutSession = Date.now();
    api = trouverAPI();
    if (api) {
      var ok = false;
      try { ok = String(api.LMSInitialize('')) === 'true'; } catch (e) { ok = false; }
      if (!ok) api = null;
    }
    autonome = !api;
    if (autonome) memoire = lireLocal();
    initialise = true;
    return !autonome;
  }

  function lire(cle) {
    if (!initialise) return '';
    if (api) {
      try { var v = api.LMSGetValue(cle); return v === null || v === undefined ? '' : String(v); } catch (e) { return ''; }
    }
    var val = memoire[cle];
    return val === null || val === undefined ? '' : String(val);
  }

  function ecrire(cle, valeur) {
    if (!initialise || termine) return false;
    var texte = valeur === null || valeur === undefined ? '' : String(valeur);
    if (api) {
      try { return String(api.LMSSetValue(cle, texte)) === 'true'; } catch (e) { return false; }
    }
    memoire[cle] = texte;
    return true;
  }

  function valider() {
    if (!initialise || termine) return false;
    if (api) {
      try { return String(api.LMSCommit('')) === 'true'; } catch (e) { return false; }
    }
    ecrireLocal();
    return true;
  }

  /** Clôt la session : durée, mode de sortie, LMSFinish (ou écriture locale). */
  function terminer(sortie) {
    if (!initialise || termine) return false;
    ecrire('cmi.core.session_time', formaterSession(Date.now() - debutSession));
    ecrire('cmi.core.exit', sortie === undefined ? 'suspend' : sortie);
    valider();
    termine = true;
    if (api) {
      try { return String(api.LMSFinish('')) === 'true'; } catch (e) { return false; }
    }
    ecrireLocal();
    return true;
  }

  function derniereErreur() {
    if (!api) return '';
    try { return String(api.LMSGetLastError()) + ' ' + String(api.LMSGetErrorString(api.LMSGetLastError())); } catch (e) { return ''; }
  }

  return {
    initialiser: initialiser,
    lire: lire,
    ecrire: ecrire,
    valider: valider,
    terminer: terminer,
    formaterSession: formaterSession,
    derniereErreur: derniereErreur,
    estAutonome: function () { return autonome; },
    estInitialise: function () { return initialise; },
    cleLocale: function () { return cleLocale; }
  };
})();

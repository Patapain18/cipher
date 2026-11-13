/* ============================================================
   pistes/charger.js — charge une piste « Ma position »
   ============================================================
   L'adresse choisit la piste : ?piste=a, ?piste=b ou ?piste=c.
   Sans ce paramètre, la page ne change pas d'un octet.
   La feuille de la piste passe après style.css ; son script après
   app.js et vue3d.js, et après commun.js (le socle partagé).
   ============================================================ */
(() => {
  const piste = new URLSearchParams(location.search).get('piste');
  if (!/^[abc]$/.test(piste || '')) return;
  document.documentElement.dataset.piste = piste;

  const feuille = document.createElement('link');
  feuille.rel = 'stylesheet';
  feuille.href = `pistes/position-${piste}.css`;
  document.head.appendChild(feuille);

  for (const src of ['pistes/commun.js', `pistes/position-${piste}.js`]) {
    const script = document.createElement('script');
    script.src = src;
    script.async = false;   // dans l'ordre : le socle, puis la piste
    document.body.appendChild(script);
  }
})();

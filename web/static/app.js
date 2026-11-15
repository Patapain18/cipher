/* ============================================================
   app.js — le dashboard côté navigateur
   ============================================================
   Aucune bibliothèque : les données arrivent en JSON, les courbes
   sont du SVG écrit à la main. C'est volontaire — une page qui ne
   dépend de rien s'ouvre encore dans deux ans sans rien réinstaller,
   et les graphiques dont on a besoin ici tiennent en trente lignes.
   ============================================================ */

const $ = (sel) => document.querySelector(sel);

/* Seuil de déclenchement du moteur v2 : BUY à partir de 4/5.
   Il sert à marquer le cran « à partir d'ici, le bot signale » sur
   chaque jauge. */
let BUY_THRESHOLD = 4;

/* ------------------------------------------------------------
   OUTILS
   ------------------------------------------------------------ */

async function api(path) {
  const res = await fetch(path);
  const json = await res.json();
  if (!json.ok) throw new Error(json.error || 'erreur inconnue');
  return json.data;
}

/* Toujours passer par ici pour insérer du texte venu du serveur.
   Un nom de ticker ou un message d'erreur ne doit jamais pouvoir être
   interprété comme du HTML. */
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const nf = (v, d = 2) =>
  v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : Number(v).toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });

/* Un pourcentage porte toujours son signe : « +0,4 % » et « 0,4 % »
   ne disent pas la même chose quand on parcourt une colonne. */
const pct = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v))
  ? '—'
  : (v >= 0 ? '+' : '') + Number(v).toLocaleString('fr-FR',
      { minimumFractionDigits: d, maximumFractionDigits: d }) + ' %';

const trend = (v) => (v > 0.02 ? 'up' : v < -0.02 ? 'down' : 'flat');

const dt = (iso, withDate = true) => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('fr-FR', withDate
    ? { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }
    : { hour: '2-digit', minute: '2-digit' });
};

/* La jauge : cinq segments, remplis jusqu'au score, avec un cran
   souligné au seuil de déclenchement. */
function gauge(score, max, threshold, kind) {
  const cells = [];
  for (let i = 1; i <= max; i++) {
    const classes = [];
    if (i <= score) classes.push('on');
    if (i === threshold) classes.push('threshold');
    cells.push(`<i class="${classes.join(' ')}"></i>`);
  }
  return `<span class="gauge ${kind}">${cells.join('')}</span>`;
}

/* ------------------------------------------------------------
   COURBES SVG
   ------------------------------------------------------------
   Un tracé, c'est une projection : chaque valeur devient un point
   (x = son rang, y = sa position entre le minimum et le maximum).
   `viewBox` + `preserveAspectRatio="none"` laissent le SVG s'étirer
   à la largeur disponible, donc la courbe reste nette à toute taille
   sans qu'on ait à recalculer quoi que ce soit au redimensionnement.
   ------------------------------------------------------------ */

function project(values, w, h, pad = 0) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;   // évite la division par zéro sur une série plate
  return values.map((v, i) => [
    values.length === 1 ? w / 2 : (i / (values.length - 1)) * w,
    pad + (1 - (v - min) / span) * (h - pad * 2),
  ]);
}

function lineChart(points, { label = (p) => p.t, value = (p) => p.price } = {}) {
  if (!points || points.length < 2) {
    return '<p class="empty">Pas assez de points pour tracer une courbe.</p>';
  }

  /* PAD_L réserve la gouttière des étiquettes de valeur, PAD_R évite
     que le dernier point ne vienne se coller au bord de la carte. */
  const W = 600, H = 190, PAD_Y = 14, PAD_L = 46, PAD_R = 8;
  const values = points.map(value);
  const min = Math.min(...values), max = Math.max(...values);
  const inner = W - PAD_L - PAD_R;
  const pts = project(values, inner, H, PAD_Y).map(([x, y]) => [x + PAD_L, y]);

  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`).join(' ');
  const area = `${line} L${pts[pts.length - 1][0].toFixed(2)} ${H} L${pts[0][0].toFixed(2)} ${H} Z`;

  /* Trois repères horizontaux : minimum, milieu, maximum. Assez pour
     situer une valeur, trop peu pour transformer le fond en grille. */
  const HT = H + 16;   // hauteur totale du viewBox, repère des étiquettes

  /* Les traits restent dans le SVG — ils doivent s'étirer avec la
     courbe. `non-scaling-stroke` leur garde leur épaisseur réelle,
     que l'échelle Y de 0,92 rabotait sinon à 0,92 px. */
  const rules = [0, 0.5, 1].map((r) => {
    const y = PAD_Y + r * (H - PAD_Y * 2);
    return `<line class="chart-grid" x1="${PAD_L}" y1="${y.toFixed(1)}" x2="${W - PAD_R}" y2="${y.toFixed(1)}"
                  vector-effect="non-scaling-stroke"/>`;
  }).join('');

  /* Le texte, lui, SORT du SVG : voir le commentaire de .chart-wrap
     dans style.css. Positionné en pourcentages du viewBox, il suit le
     tracé à toute largeur sans jamais se déformer. */
  const ticks = [0, 0.5, 1].map((r) => {
    const y = PAD_Y + r * (H - PAD_Y * 2);
    const v = max - r * (max - min);
    return `<span class="chart-tick y" style="top:${(100 * y / HT).toFixed(2)}%">`
         + `${nf(v, v > 100 ? 0 : 2)}</span>`;
  }).join('');

  const first = esc(label(points[0]));
  const last = esc(label(points[points.length - 1]));

  return `<div class="chart-wrap">
    <svg class="chart" viewBox="0 0 ${W} ${HT}" preserveAspectRatio="none"
         role="img" aria-label="Évolution de ${first} à ${last}">
      ${rules}
      <path class="chart-area" d="${area}"/>
      <path class="chart-line" d="${line}" vector-effect="non-scaling-stroke"/>
    </svg>
    ${ticks}
    <span class="chart-tick x" style="left:${(100 * PAD_L / W).toFixed(2)}%">${first}</span>
    <span class="chart-tick x" style="right:${(100 * PAD_R / W).toFixed(2)}%">${last}</span>
  </div>`;
}

/* ------------------------------------------------------------
   ÉTAT DU BOT
   ------------------------------------------------------------ */

async function renderStatus() {
  try {
    const s = await api('/api/status');
    BUY_THRESHOLD = s.seuil_achat ?? 4;

    const proc = s.stock_bot.process;
    const badges = [
      `<span class="badge safe"><span class="dot"></span>Signaux seulement</span>`,
      proc && proc.running
        ? `<span class="badge safe"><span class="dot"></span>Bot actif</span>`
        : `<span class="badge muted"><span class="dot"></span>Bot à l'arrêt</span>`,
      `<span class="badge muted">moteur ${esc(s.moteur)}</span>`,
    ];
    $('#guards').innerHTML = badges.join('');

    $('#subtitle').textContent = s.stock_bot.last_activity
      ? `${s.tickers.length} valeurs suivies — dernière activité du bot le ${dt(s.stock_bot.last_activity)}`
      : `${s.tickers.length} valeurs suivies`;
  } catch (e) {
    $('#guards').innerHTML = `<span class="badge danger">Serveur injoignable</span>`;
  }
}

/* ------------------------------------------------------------
   FIABILITÉ MESURÉE
   ------------------------------------------------------------
   La section la plus importante de la page. Elle répond à « est-ce
   que ce score a déjà eu raison ? » — sans quoi les recommandations
   du dessous ne sont que des affirmations.
   ------------------------------------------------------------ */

async function renderQuality() {
  let q;
  try {
    q = await api('/api/stocks/quality');
  } catch (e) {
    $('#quality').innerHTML = `<p class="error">${esc(e.message)}</p>`;
    return;
  }

  if (!q) {
    $('#quality').innerHTML = `
      <p class="empty">Le signal n'a pas encore été évalué.</p>
      <p class="hint">Pour mesurer si un score élevé annonce vraiment une hausse :
        <code>python3 stock_eval.py --save</code></p>`;
    return;
  }

  const v2 = q.v2, v1 = q.v1, p = q.parametres;
  const edge = v2.edge || {};
  const beatsReference = (edge.ecart ?? 0) > 0;

  /* L'écart au « ne rien faire » est la mesure qui décide. Positif :
     suivre le signal aurait payé. Négatif : il aurait coûté. On
     l'affiche tel quel, y compris quand il est mauvais — un tableau de
     bord qui ne sait annoncer que de bonnes nouvelles ne sert à rien. */
  $('#quality').innerHTML = `
    <div class="grid-stats" style="margin:0">
      <div class="stat" style="padding:0">
        <div class="label">Écart vs ne rien faire</div>
        <div class="value ${beatsReference ? 'up' : 'down'}">${pct(edge.ecart, 2).replace(' %', ' pt')}</div>
        <div class="foot">${beatsReference
          ? 'suivre le signal aurait payé'
          : 'suivre le signal aurait coûté'}</div>
      </div>
      <div class="stat" style="padding:0">
        <div class="label">Rendement quand déclenché</div>
        <div class="value">${pct(edge.rendement, 2)}</div>
        <div class="foot">à ${p.horizon} j · référence ${pct(v2.reference, 2)}</div>
      </div>
      <div class="stat" style="padding:0">
        <div class="label">Taux de hausse</div>
        <div class="value">${nf(edge.taux_hausse, 0)} %</div>
        <div class="foot">quand le score atteint ${p.seuil}/5</div>
      </div>
      <div class="stat" style="padding:0">
        <div class="label">Information Coefficient</div>
        <div class="value">${v2.ic === null ? '—' : (v2.ic >= 0 ? '+' : '') + nf(v2.ic, 4)}</div>
        <div class="foot">v1 : ${v1.ic === null ? '—' : (v1.ic >= 0 ? '+' : '') + nf(v1.ic, 4)}</div>
      </div>
    </div>

    <div class="hint" style="margin-top:var(--space-4)">
      Mesuré sur ${q.n_seances.toLocaleString('fr-FR')} séances
      (${esc(p.periode)} d'historique, ${p.valeurs.length} valeurs, horizon ${p.horizon} j),
      le ${dt(q.genere_le)}.
      ${qualityVerdict(v2, edge, beatsReference)}
    </div>`;
}

/* Le commentaire s'ajuste à la mesure, jamais l'inverse. Un IC nul et
   un écart nul ne se commentent pas comme un signal « faible mais
   utile » : ce serait laisser croire à une valeur que les chiffres ne
   montrent pas. */
function qualityVerdict(v2, edge, beatsReference) {
  const ic = v2.ic ?? 0;
  if (beatsReference && (edge.ecart ?? 0) > 0.1 && ic > 0.015) return '';

  /* Le seuil porte sur la valeur signée, pas sur sa valeur absolue :
     un IC négatif n'est pas un signal faible, c'est un signal qui
     pointe dans le mauvais sens. Il appartient au même cas que zéro. */
  if (ic <= 0.015) {
    return `<strong>Sur cette période, ce score n'a aucun pouvoir prédictif décelable.</strong>
      Il décrit l'état technique d'une valeur, mais il n'annonce pas la suite —
      ne t'en sers pas pour décider d'un achat. Le moins mauvais critère de ce
      tableau de bord est le classement par momentum 12-1, sans être validé pour autant.`;
  }
  return `<strong>Sur cette période, le signal ne bat pas l'achat-conservation.</strong>
    Il reste utile pour choisir un moment d'entrée quand la décision d'acheter
    est déjà prise, mais il ne justifie pas d'acheter à lui seul.`;
}

/* ------------------------------------------------------------
   TABLEAU DES VALEURS
   ------------------------------------------------------------ */

/* Le régime décide de quels indicateurs le moteur écoute — c'est
   l'apport principal de la v2, donc il est affiché, pas caché. */
const REGIME_LABEL = {
  'tendance': { txt: 'Tendance', hint: 'suivi privilégié' },
  'range': { txt: 'Range', hint: 'retour à la moyenne privilégié' },
  'mixte': { txt: 'Mixte', hint: 'les deux à parts égales' },
  'indéterminé': { txt: '—', hint: '' },
};

/* Prochaine publication de résultats.
   Le seuil de 7 jours n'est pas un signal : il marque le moment où une
   décision d'achat devient un pari sur un écart d'ouverture, quel que
   soit ce que dit le classement. On signale donc la PROXIMITÉ, sans rien
   affirmer sur le sens du mouvement. */
function earningsCell(r) {
  const j = r.resultats_dans;
  if (j === null || j === undefined || j < 0) return '<span class="flat">—</span>';
  const date = r.resultats_le ? ` title="${esc(r.resultats_le)}"` : '';
  const libelle = j === 0 ? "aujourd'hui" : j === 1 ? 'demain' : `dans ${j} j`;
  return `<span class="${j <= 7 ? 'soon' : 'flat'}"${date}>${libelle}</span>`;
}

/* Les filtres travaillent sur les données déjà reçues : changer de vue
   ne doit pas relancer un scan de 49 valeurs. */
let lastStocks = null;

/* Vue par défaut : le moins mauvais critère mesuré, pas les signaux
   techniques. C'est ce qu'on veut voir en ouvrant la page. */
let activeFilter = 'momentum';

/* Ce que le moteur conclut du score technique, dit comme un état. Les codes
   BUY, WATCH, HOLD et SELL restent dans les données ; affichés tels quels,
   ils se lisent comme des ordres, alors que la mesure a montré que ce score
   n'annonce rien. Même traduction dans stock_bot.py. */
const ETAT_TECHNIQUE = {
  BUY: 'favorable',
  WATCH: 'mitigé',
  HOLD: 'neutre',
  SELL: 'défavorable',
};

const FILTERS = {
  momentum: {
    label: 'Top momentum',
    hint: 'les 12 mieux classées — le moins mauvais critère mesuré, pas un signal d\'achat',
    test: (r) => r.rang && r.rang <= 12,
  },
  signaux: {
    label: 'Signaux techniques',
    hint: '« favorable » et « mitigé » selon le moteur — sans valeur prédictive mesurée',
    test: (r) => r.recommendation === 'BUY' || r.recommendation === 'WATCH',
  },
  detenues: {
    label: 'Détenues',
    hint: 'positions enregistrées',
    test: (r) => !!r.position,
  },
  tout: { label: 'Tout', hint: 'les 49 valeurs', test: () => true },
};

async function renderStocks() {
  try {
    lastStocks = await api('/api/stocks');
  } catch (e) {
    $('#stocks-body').innerHTML =
      `<tr><td colspan="13" class="error">Impossible de récupérer les cours : ${esc(e.message)}</td></tr>`;
    return;
  }
  drawStocks();
  /* La vue baie clone ce tableau dans son panneau : on la prévient
     qu'il vient d'être redessiné, pour qu'elle recopie — jamais deux
     versions des mêmes chiffres. */
  document.dispatchEvent(new CustomEvent('stocks:dessine'));
}

function drawStocks() {
  const data = lastStocks;
  if (!data) return;

  const rows = data.rows.filter((r) => !r.error);
  const failed = data.rows.filter((r) => r.error);

  const buys = rows.filter((r) => r.recommendation === 'BUY').length;
  const watch = rows.filter((r) => r.recommendation === 'WATCH').length;
  /* Le carnet du bot suit le journal : une ligne détenue = une position. */
  const held = rows.filter((r) => r.position);

  /* Les cartes suivent la même règle que le tableau : ce qui a été
     mesuré passe devant. Le compte de signaux techniques reste affiché,
     mais en dernier et sans emphase — il informe sans orienter. */
  const top = [...rows].filter((r) => r.rang).sort((a, b) => a.rang - b.rang);
  const leader = top[0];
  const top12 = top.slice(0, 12);
  const top12Sectors = new Set(top12.map((r) => r.secteur)).size;
  const positive = rows.filter((r) => (r.mom_12_1 ?? -1) > 0).length;

  $('#stocks-stats').innerHTML = `
    <div class="card stat">
      <div class="label">Tête de classement</div>
      <div class="value">${leader ? esc(leader.ticker) : '—'}</div>
      <div class="foot">${leader
        ? `<span class="${trend(leader.mom_12_1 * 100)}">${pct(leader.mom_12_1 * 100, 0)}</span>
           de momentum · ${esc(leader.secteur)}`
        : 'momentum indisponible'}</div>
    </div>
    <div class="card stat">
      <div class="label">Sélection top 12</div>
      <div class="value">${top12Sectors}<span class="sub"> secteurs</span></div>
      <div class="foot">répartition des 12 mieux classées</div>
    </div>
    <div class="card stat">
      <div class="label">Momentum positif</div>
      <div class="value">${positive}<span class="sub"> / ${rows.length}</span></div>
      <div class="foot">scan de ${dt(data.scanned_at, false)}</div>
    </div>
    <div class="card stat" id="carte-position">
      <div class="label">${held.length ? 'Ma position' : 'Signaux techniques'}</div>
      <div class="value">${held.length ? '…' : buys + watch}</div>
      <div class="foot">${held.length
        ? 'en euros, frais compris'
        : `${buys} favorables · ${watch} mitigées — sans valeur prédictive mesurée`}</div>
    </div>`;
  /* Déjà connue ? Elle s'affiche tout de suite, sans « … » chaque minute. */
  dessinerCartePosition();

  /* Barre de filtres : chaque bouton porte son propre compte, pour
     qu'on sache ce qu'on va trouver avant de cliquer. Elle est réécrite
     à chaque dessin (au clic comme au rafraîchissement de la minute) :
     si le clavier était sur un filtre, il y revient au lieu de repartir
     du haut de la page. */
  const filtreAvecFocus = document.activeElement?.closest?.('#filters [data-filter]')?.dataset.filter;
  $('#filters').innerHTML = Object.entries(FILTERS).map(([key, f]) => {
    const n = rows.filter(f.test).length;
    return `<button class="tab" data-filter="${key}" title="${esc(f.hint)}"
                    aria-pressed="${key === activeFilter}">${esc(f.label)}
              <span class="tab-count">${n}</span></button>`;
  }).join('');

  $('#filters').querySelectorAll('button').forEach((btn) => {
    btn.addEventListener('click', () => {
      activeFilter = btn.dataset.filter;
      drawStocks();
    });
  });
  if (filtreAvecFocus) $(`#filters [data-filter="${filtreAvecFocus}"]`)?.focus();

  /* Tri par rang de momentum.
     Le tableau était auparavant ordonné par score technique. Depuis que
     la mesure a montré que ce score n'annonce rien, le trier par lui
     revenait à mettre en haut de page ce qui n'a pas de valeur
     démontrée. On ordonne donc par le moins mauvais critère mesuré ; les valeurs
     sans momentum calculable (historique trop court) ferment la marche
     plutôt que d'ouvrir le tableau. */
  const sorted = [...rows].filter(FILTERS[activeFilter].test).sort((a, b) =>
    (a.rang ?? 1e9) - (b.rang ?? 1e9));

  if (!sorted.length) {
    $('#stocks-body').innerHTML =
      `<tr><td colspan="13" class="empty">Aucune valeur dans cette vue.</td></tr>`;
    return;
  }

  $('#stocks-body').innerHTML = sorted.map((r) => {
    const pos = r.position;
    const reg = REGIME_LABEL[r.regime] || { txt: esc(r.regime || '—'), hint: '' };
    const proj = r.projection;

    return `<tr class="${pos ? 'held' : ''}">
      <td class="ticker">${esc(r.ticker)}
        ${pos ? `<span class="held-note">${nf(pos.quantity, 2)} × ${nf(pos.entry_price)} $</span>` : ''}
      </td>
      <td class="sector">${esc(r.secteur || '—')}</td>
      <td class="num-cell num rank primary-col"
          title="Momentum 12-1 et rang du jour. Le moins mauvais critère mesuré : il bat le hasard, pas le simple panier une fois le risque compté. Départage les valeurs ; ne décide pas d'un achat.">${
        r.mom_12_1 === null || r.mom_12_1 === undefined
          ? '—'
          : `<span class="${trend(r.mom_12_1 * 100)}">${pct(r.mom_12_1 * 100, 0)}</span>
             <span class="rank-total">${r.rang
               ? `${r.rang}${r.rang === 1 ? 'ᵉʳ' : 'ᵉ'} / ${data.classes}` : ''}</span>`}</td>
      <td class="num-cell num earnings">${earningsCell(r)}</td>
      <td class="num-cell num">${nf(r.price)} $</td>
      <td class="num-cell num ${trend(r.change_1d)}">${pct(r.change_1d)}</td>
      <td class="num-cell num ${trend(r.change_1w)}">${pct(r.change_1w)}</td>
      <td class="num-cell num ${trend(r.change_1m)}">${pct(r.change_1m)}</td>
      <td>
        <span class="regime">${reg.txt}</span>
        <span class="regime-hint">ADX ${nf(r.adx, 0)}</span>
      </td>
      <td class="secondary-col">
        <span class="score-cell">
          ${gauge(r.buy_score, 5, BUY_THRESHOLD, r.buy_score >= BUY_THRESHOLD ? 'buy' : '')}
          <span class="n num">${r.buy_score}/5</span>
        </span>
      </td>
      <td><span class="reco reco-${esc(r.recommendation)}">${esc(ETAT_TECHNIQUE[r.recommendation] || r.recommendation)}</span></td>
      <td><div class="signals">${(r.drivers || []).slice(0, 2).map((d) =>
            `<span class="chip ${d.poids >= 0 ? 'chip-up' : 'chip-down'}"
                   title="${esc(d.famille)} · poids ${nf(d.poids, 3)}">${esc(d.nom)}</span>`
          ).join('') || '<span class="chip">—</span>'}</div></td>
      <td class="num-cell num">${proj
        ? `${nf(proj.bas)} – ${nf(proj.haut)}<span class="regime-hint">± ${nf(proj.amplitude_pct, 1)} %</span>`
        : '—'}</td>
    </tr>`;
  }).join('') + (activeFilter === 'tout' ? failed.map((r) => `
    <tr><td class="ticker">${esc(r.ticker)}</td>
        <td colspan="12" class="flat">${esc(r.error)}</td></tr>`).join('') : '');
}

/* ------------------------------------------------------------
   MA POSITION, EN RÉSUMÉ
   ------------------------------------------------------------
   Les mêmes chiffres que le carnet de bord (touche P,
   ma-position/carnet.js) : en euros, frais compris, lus dans
   /api/position. L'ancien tableau en dollars, sans les frais ni le
   change, donnait une AUTRE réponse à « est-ce que je gagne ? » : le
   carnet du bot reste en dollars pour le bot, plus à l'écran.
   Rien ne s'écrit ici : une décision se note dans le carnet.
   ------------------------------------------------------------ */

/* La dernière réponse de /api/position : la carte de la synthèse et
   la section du bas la partagent, donc disent la même chose. */
let maPosition = null;

/* « 0,125 » ou « 5 » : les décimales utiles, et rien de plus. */
const quantite = (q) => Number(q).toLocaleString('fr-FR', { maximumFractionDigits: 6 });

/* La 4e carte de la synthèse, dès que la position est connue. */
function dessinerCartePosition() {
  const carte = $('#carte-position');
  if (!carte || !window.PP || !maPosition || !maPosition.lignes.length) return;
  const { total } = maPosition;
  carte.innerHTML = `
    <div class="label">Ma position</div>
    <div class="value">${PP.euros(total.valeur_eur)}</div>
    <div class="foot"><span class="${PP.sens(total.gain_eur)}">${PP.euros(total.gain_eur, { signe: true })}</span>
      depuis l'achat, frais compris</div>
    <div class="foot">${PP.euros(PP.sansFraisTotal(maPosition).eur, { signe: true })} sans les frais</div>`;
}

async function renderPositions() {
  const zone = $('#positions');
  if (!zone || !window.PP) return;
  try {
    maPosition = await PP.position();
  } catch (e) {
    zone.innerHTML = `<p class="error">${esc(e.message)}</p>`;
    return;
  }
  dessinerCartePosition();

  const { lignes, total } = maPosition;
  if (!lignes.length) {
    zone.innerHTML = `
      <p class="empty">Aucune position. Une décision déjà passée chez ton courtier
        se note dans <strong>Ma position</strong> (touche <kbd>P</kbd>).</p>`;
    return;
  }
  /* « Sans les frais » : le chiffre de l'appli du courtier, en retrait. */
  const sansFrais = (sf) => `${PP.euros(sf.eur, { signe: true })}
        <span class="rank-total">${PP.pct(sf.pct)}</span>`;
  const ligne = (l) => `
    <tr>
      <td class="ticker">${esc(l.ticker)}</td>
      <td class="num-cell num">${quantite(l.quantite)}</td>
      <td class="num-cell num">${PP.euros(l.prix_achat_eur)}</td>
      <td class="num-cell num">${PP.euros(l.prix_eur)}</td>
      <td class="num-cell num">${PP.euros(l.valeur_eur)}</td>
      <td class="num-cell num ${PP.sens(l.gain_eur)}">${PP.euros(l.gain_eur, { signe: true })}
        <span class="rank-total">${PP.pct(l.gain_pct)}</span></td>
      <td class="num-cell num sans-frais">${sansFrais(PP.sansFrais(l))}</td>
    </tr>`;
  zone.innerHTML = `
    <div class="table-wrap" style="box-shadow:none">
      <table>
        <thead><tr>
          <th>Action</th>
          <th class="num-cell">Quantité</th>
          <th class="num-cell">Prix d'achat</th>
          <th class="num-cell">Cours</th>
          <th class="num-cell">Valeur</th>
          <th class="num-cell">Depuis l'achat, frais compris</th>
          <th class="num-cell">Sans les frais</th>
        </tr></thead>
        <tbody>${lignes.map(ligne).join('')}</tbody>
        ${lignes.length > 1 ? `
        <tfoot><tr>
          <th scope="row" colspan="4">Total</th>
          <td class="num-cell num">${PP.euros(total.valeur_eur)}</td>
          <td class="num-cell num ${PP.sens(total.gain_eur)}">${PP.euros(total.gain_eur, { signe: true })}
            <span class="rank-total">${PP.pct(total.gain_pct)}</span></td>
          <td class="num-cell num sans-frais">${sansFrais(PP.sansFraisTotal(maPosition))}</td>
        </tr></tfoot>` : ''}
      </table>
    </div>`;
  /* Le panneau 1 de la vue baie EST cette section : il se remesure. */
  document.dispatchEvent(new CustomEvent('stocks:dessine'));
}

/* --- Historique : une courbe par valeur, sélectionnable --- */

let historyData = null;
let historyTicker = null;

async function renderHistory() {
  try {
    historyData = await api('/api/stocks/history');
  } catch (e) {
    $('#history-chart').innerHTML = `<p class="error">${esc(e.message)}</p>`;
    return;
  }

  const tickers = Object.keys(historyData.series);
  if (!tickers.length) {
    $('#history-chart').innerHTML =
      '<p class="empty">Aucun historique pour l\'instant — il se remplit quand le bot tourne.</p>';
    $('#stocks-events').innerHTML = '<li class="empty">Rien à signaler.</li>';
    return;
  }

  historyTicker = historyTicker && tickers.includes(historyTicker) ? historyTicker : tickers[0];

  /* Une liste déroulante, et non une pastille par valeur : l'univers
     est passé à 49 titres, ce qui donnait 52 boutons sur près de 300 px
     de haut — le sélecteur occupait plus de place que le graphique
     qu'il pilote. Tri alphabétique, parce qu'on y cherche une valeur
     précise dont on connaît le nom. */
  const sorted = [...tickers].sort();
  $('#history-picker').innerHTML = `
    <select class="picker" id="history-select" aria-label="Valeur à afficher">
      ${sorted.map((t) => `<option value="${esc(t)}"${
        t === historyTicker ? ' selected' : ''}>${esc(t)}</option>`).join('')}
    </select>`;

  $('#history-select').addEventListener('change', (e) => {
    historyTicker = e.target.value;
    drawHistory();
  });

  drawHistory();
  drawEvents();
}

function drawHistory() {
  const points = historyData.series[historyTicker] || [];
  $('#history-ticker').textContent = historyTicker;
  $('#history-range').textContent =
    ` · ${points.length} relevés du ${dt(historyData.from)} au ${dt(historyData.to)}`;
  $('#history-chart').innerHTML = lineChart(points, { label: (p) => dt(p.t) });
}

function drawEvents() {
  const events = historyData.events || [];
  $('#stocks-events').innerHTML = events.length
    ? events.map((e) => `
        <li>
          <time class="num">${dt(e.t)}</time>
          <span class="who">${esc(e.ticker)}</span>
          <span class="reco reco-${esc(e.reco)}">${esc(ETAT_TECHNIQUE[e.reco] || e.reco)}</span>
          <span class="what">à ${nf(e.price)} $${e.signals && e.signals.length
            ? ' — ' + esc(e.signals.join(', ')) : ''}</span>
        </li>`).join('')
    : '<li class="empty">Aucune bascule enregistrée.</li>';
}

/* ------------------------------------------------------------
   LA SEMAINE
   Le point préparé par le bot chaque dimanche à 19 h
   (point_semaine.py) : des faits sur la semaine écoulée, jamais de
   conseil. La page ne fait que le mettre en forme.
   ------------------------------------------------------------ */
const JOURS_FR = ['dimanche', 'lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi'];
const MOIS_FR = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet',
                 'août', 'septembre', 'octobre', 'novembre', 'décembre'];

/* « 2026-10-30 » → « vendredi 30 octobre ». Midi plutôt que minuit :
   une date seule est lue en UTC, et minuit UTC tombe la veille à l'ouest. */
const jourLong = (iso) => {
  const d = new Date(`${iso}T12:00:00`);
  return `${JOURS_FR[d.getDay()]} ${d.getDate()} ${MOIS_FR[d.getMonth()]}`;
};

/* Une ligne « nom … variation ». La variation arrive en fraction (0,012),
   elle s'affiche en pourcentage (+1,2 %). */
const ligneSemaine = (nom, v) => `
  <li><span>${esc(nom)}</span>
      <span class="num ${trend(v * 100)}">${pct(v * 100)}</span></li>`;

async function renderSemaine() {
  let p;
  try {
    p = await api('/api/semaine');
  } catch (e) {
    return;   // serveur injoignable : la section reste comme elle était
  }
  const section = $('#section-semaine');
  if (!p) {
    section.hidden = true;
    return;
  }

  const s = p.semaine;
  $('#semaine-dates').textContent = `du ${jourLong(s.debut)} au ${jourLong(s.fin)}`;

  const marches = p.marches.map((m) => `
    <li><span>${esc(m.nom)}</span>
        <span class="num ${trend(m.variation_dollars * 100)}">${pct(m.variation_dollars * 100)}</span></li>
    <li class="semaine-euros"><span>en euros</span>
        <span class="num ${trend(m.variation_euros * 100)}">${pct(m.variation_euros * 100)}</span></li>`).join('');
  const e = p.euro_dollar;
  const c = p.classement;
  const avenir = p.resultats_a_venir || [];
  const pos = p.positions && p.positions.lignes && p.positions.lignes.length ? p.positions : null;

  $('#semaine').innerHTML = `
    <p class="semaine-note">${esc(p.avertissement)}</p>
    <div class="semaine-grille">
      <div class="semaine-bloc">
        <h2 class="semaine-titre">Les marchés</h2>
        <ul class="semaine-liste">
          ${marches}
          <li><span>1 € = ${nf(e.cours, 4)} $</span>
              <span class="num ${trend(e.variation * 100)}">${pct(e.variation * 100)}</span></li>
        </ul>
      </div>
      <div class="semaine-bloc">
        <h2 class="semaine-titre">Plus fortes hausses</h2>
        <ul class="semaine-liste">${p.valeurs.hausses.map((x) => ligneSemaine(x.ticker, x.variation)).join('')}</ul>
      </div>
      <div class="semaine-bloc">
        <h2 class="semaine-titre">Plus fortes baisses</h2>
        <ul class="semaine-liste">${p.valeurs.baisses.map((x) => ligneSemaine(x.ticker, x.variation)).join('')}</ul>
      </div>
      <div class="semaine-bloc">
        <h2 class="semaine-titre">Secteurs</h2>
        <ul class="semaine-liste">${p.secteurs.map((x) => ligneSemaine(x.secteur, x.variation)).join('')}</ul>
      </div>
      <div class="semaine-bloc">
        <h2 class="semaine-titre">Classement momentum</h2>
        <p class="semaine-texte">Entrent dans le top 12 : <strong>${esc(c.entrent.join(', ') || 'aucune')}</strong></p>
        <p class="semaine-texte">En sortent : <strong>${esc(c.sortent.join(', ') || 'aucune')}</strong></p>
        <p class="semaine-rappel">${esc(c.rappel)}</p>
      </div>
      <div class="semaine-bloc">
        <h2 class="semaine-titre">Résultats la semaine prochaine</h2>
        ${avenir.length
          ? `<ul class="semaine-liste">${avenir.map((r) => `
              <li><span>${esc(r.ticker)}</span><span>${esc(jourLong(r.date))}</span></li>`).join('')}</ul>
             <p class="semaine-rappel">Une publication annonce un gros mouvement probable, pas son sens.</p>`
          : '<p class="semaine-rappel">Aucune parmi tes 49 valeurs.</p>'}
      </div>
      ${pos ? `
      <div class="semaine-bloc">
        <h2 class="semaine-titre">Tes positions, en euros</h2>
        <ul class="semaine-liste">${pos.lignes.map((x) => `
          <li><span>${esc(x.ticker)}</span>
              <span class="num ${trend(x.gain_euros)}">${x.gain_euros >= 0 ? '+' : ''}${nf(x.gain_euros)} €</span></li>`).join('')}
        </ul>
        <p class="semaine-rappel">${nf(pos.valeur_euros)} € pour ${nf(pos.cout_euros)} € investis.</p>
      </div>` : ''}
    </div>`;
  section.hidden = false;
}

/* ------------------------------------------------------------
   CYCLE DE VIE
   ------------------------------------------------------------ */

function refresh() {
  renderStatus();
  renderQuality();
  renderSemaine();
  renderStocks();
  renderPositions();   // à part : la position n'attend pas le scan des 49 valeurs
  renderHistory();
}

refresh();

/* Rafraîchissement automatique. Le serveur garde ses réponses en cache
   quelques minutes, donc ce cycle ne provoque pas un appel réseau à
   chaque tour — il reprend surtout la main quand le cache a expiré. */
let dernier = Date.now();

setInterval(() => {
  /* Deux gardes plutôt qu'un. `document.hidden` couvre l'onglet passé
     à l'arrière-plan, mais l'application de bureau ne bascule pas
     toujours cet indicateur — une fenêtre réduite dans le Dock reste
     « visible » pour la page. `hasFocus()`, lui, tombe dès qu'une
     autre fenêtre passe devant. Ensemble, ils évitent un fetch et un
     innerHTML de 637 cellules toutes les minutes sur une fenêtre que
     personne ne regarde. */
  if (document.hidden || !document.hasFocus()) return;
  // La vue baie ne bloque plus rien : ses panneaux SONT les sections de
  // cette page, déplacées dans la pièce. Elles se rafraîchissent en place.
  refresh();
  dernier = Date.now();
}, 60_000);

/* Au retour de la vue baie, même règle qu'au retour du focus : un
   rafraîchissement si l'écart le justifie — sous les yeux, jamais en
   cachette pendant qu'on regardait la ville. */
document.addEventListener('vue3d:fermee', () => {
  if (Date.now() - dernier < 60_000) return;
  dernier = Date.now();
  refresh();
});

/* Ne rien faire quand personne ne regarde a un revers : en revenant sur
   la fenêtre, on tomberait sur des cours figés depuis des heures, et
   il faudrait attendre le prochain tour de soixante secondes. On
   rattrape donc à la reprise du focus, et seulement si l'écart le
   justifie — revenir deux fois en dix secondes ne doit pas relancer
   deux scans. */
window.addEventListener('focus', () => {
  if (Date.now() - dernier < 60_000) return;
  dernier = Date.now();
  refresh();
});

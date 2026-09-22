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
      ne t'en sers pas pour décider d'un achat. Le seul critère validé de ce
      tableau de bord est le classement par momentum 12-1.`;
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

/* Vue par défaut : la sélection validée par la mesure, pas les signaux
   techniques. C'est ce qu'on veut voir en ouvrant la page. */
let activeFilter = 'momentum';

const FILTERS = {
  momentum: {
    label: 'Top momentum',
    hint: 'les 12 mieux classées — la sélection mesurée comme gagnante',
    test: (r) => r.rang && r.rang <= 12,
  },
  signaux: {
    label: 'Signaux techniques',
    hint: 'BUY et WATCH du moteur — sans valeur prédictive mesurée',
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
  const held = rows.filter((r) => r.position);
  const heldPnl = held.reduce((sum, r) => sum + (r.position.pnl_eur || 0), 0);

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
      <div class="foot">la répartition mesurée comme gagnante</div>
    </div>
    <div class="card stat">
      <div class="label">Momentum positif</div>
      <div class="value">${positive}<span class="sub"> / ${rows.length}</span></div>
      <div class="foot">scan de ${dt(data.scanned_at, false)}</div>
    </div>
    <div class="card stat">
      <div class="label">${held.length ? 'Positions détenues' : 'Signaux techniques'}</div>
      <div class="value">${held.length || buys + watch}</div>
      <div class="foot">${held.length
        ? `<span class="${trend(heldPnl)}">${heldPnl >= 0 ? '+' : ''}${nf(heldPnl)} €</span> latents`
        : `${buys} BUY · ${watch} WATCH — sans valeur prédictive mesurée`}</div>
    </div>`;

  /* Barre de filtres : chaque onglet porte son propre compte, pour
     qu'on sache ce qu'on va trouver avant de cliquer. */
  $('#filters').innerHTML = Object.entries(FILTERS).map(([key, f]) => {
    const n = rows.filter(f.test).length;
    return `<button class="tab" data-filter="${key}" title="${esc(f.hint)}"
                    aria-selected="${key === activeFilter}">${esc(f.label)}
              <span class="tab-count">${n}</span></button>`;
  }).join('');

  $('#filters').querySelectorAll('button').forEach((btn) => {
    btn.addEventListener('click', () => {
      activeFilter = btn.dataset.filter;
      drawStocks();
    });
  });

  /* Tri par rang de momentum.
     Le tableau était auparavant ordonné par score technique. Depuis que
     la mesure a montré que ce score n'annonce rien, le trier par lui
     revenait à mettre en haut de page ce qui n'a pas de valeur
     démontrée. On ordonne donc par le seul critère validé ; les valeurs
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
          title="Momentum 12-1 et rang du jour. Seul critère de classement validé. Départage les valeurs ; ne décide pas d'un achat à lui seul.">${
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
      <td><span class="reco reco-${esc(r.recommendation)}">${esc(r.recommendation)}</span></td>
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

  renderPositions();
}

/* ------------------------------------------------------------
   MES POSITIONS
   ------------------------------------------------------------
   La seule partie de la page qui écrit. Elle ne passe aucun ordre :
   elle note ce que tu as déjà acheté chez ton courtier, pour que le
   bot connaisse ton portefeuille et puisse en calculer le P&L.
   ------------------------------------------------------------ */

/* Message de la section positions.
   Il vit en dehors du rendu parce que `renderPositions` reconstruit tout
   son HTML : un message écrit directement dans le DOM disparaîtrait au
   premier redessin, c'est-à-dire précisément au moment où l'utilisateur
   attend une confirmation. */
let positionNotice = null;   // { text, kind } ou null

async function postJSON(path, payload) {
  const res = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      /* Cet en-tête est ce qui distingue notre page d'un site tiers
         qui tenterait d'écrire dans le carnet — voir server.py. */
      'X-Dashboard': '1',
    },
    body: JSON.stringify(payload),
  });
  const json = await res.json();
  if (!json.ok) throw new Error(json.error || 'échec de l’enregistrement');
  return json.data;
}

function renderPositions() {
  const data = lastStocks;
  if (!data) return;

  const rows = data.rows.filter((r) => !r.error);
  const held = rows.filter((r) => r.position);
  const total = held.reduce((s, r) => s + r.position.pnl_eur, 0);
  const invested = held.reduce(
    (s, r) => s + r.position.entry_price * r.position.quantity, 0);
  const pctTotal = invested ? (total / invested) * 100 : 0;

  /* La liste des valeurs proposées à l'ajout exclut celles déjà
     détenues : le carnet ne garde qu'une ligne par valeur, en
     proposer une seconde ne ferait qu'écraser la première en silence. */
  const addable = rows
    .filter((r) => !r.position)
    .map((r) => r.ticker)
    .sort();

  const table = held.length ? `
    <div class="table-wrap" style="box-shadow:none;margin-bottom:var(--space-4)">
      <table>
        <thead><tr>
          <th>Valeur</th>
          <th class="num-cell">Quantité</th>
          <th class="num-cell">Prix d'achat</th>
          <th class="num-cell">Cours</th>
          <th class="num-cell">P&L</th>
          <th></th>
        </tr></thead>
        <tbody>
          ${held.map((r) => `
            <tr>
              <td class="ticker">${esc(r.ticker)}
                <span class="sector">${esc(r.secteur || '')}</span></td>
              <td class="num-cell num">${nf(r.position.quantity, 4)}</td>
              <td class="num-cell num">${nf(r.position.entry_price)} $</td>
              <td class="num-cell num">${nf(r.price)} $</td>
              <td class="num-cell num ${trend(r.position.pnl_pct)}">
                ${pct(r.position.pnl_pct, 2)}
                <span class="rank-total">${r.position.pnl_eur >= 0 ? '+' : ''}${nf(r.position.pnl_eur)} $</span>
              </td>
              <td class="num-cell">
                <button class="link-btn" data-remove="${esc(r.ticker)}"
                        title="Retirer ${esc(r.ticker)} du carnet">Retirer</button>
              </td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>` : `
    <p class="empty" style="padding:var(--space-4) 0">
      Aucune position enregistrée. Ajoute ce que tu détiens déjà pour
      suivre ton P&L ici.
    </p>`;

  const summary = held.length ? `
    <div class="grid-stats" style="margin-bottom:var(--space-4)">
      <div class="stat" style="padding:0">
        <div class="label">Valeurs détenues</div>
        <div class="value">${held.length}</div>
        <div class="foot">${nf(invested)} $ investis</div>
      </div>
      <div class="stat" style="padding:0">
        <div class="label">P&L latent</div>
        <div class="value ${trend(total)}">${total >= 0 ? '+' : ''}${nf(total)} $</div>
        <div class="foot">${pct(pctTotal, 2)} sur le montant investi</div>
      </div>
    </div>` : '';

  $('#positions').innerHTML = `
    ${summary}
    ${table}
    <form class="add-position" id="add-position" autocomplete="off">
      <select class="picker" name="ticker" aria-label="Valeur" required>
        <option value="">Choisir une valeur…</option>
        ${addable.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join('')}
      </select>
      <input class="field num" name="quantity" type="number" step="any" min="0"
             placeholder="Quantité" aria-label="Quantité" required>
      <input class="field num" name="price" type="number" step="any" min="0"
             placeholder="Prix d'achat ($)" aria-label="Prix d'achat" required>
      <button class="btn" type="submit">Enregistrer</button>
      <span class="form-msg ${esc(positionNotice?.kind || '')}" id="position-msg">${
        esc(positionNotice?.text || '')}</span>
    </form>
    <p class="hint" style="margin-top:var(--space-3)">
      Ce carnet n'achète rien : il note ce que tu as déjà acheté chez ton
      courtier, pour que le bot suive ton P&L et n'émette un signal de vente
      que sur une valeur que tu détiens réellement.
    </p>`;

  /* Le rechargement qui suit relance un scan des 49 valeurs, soit une
     dizaine de secondes. Sans état d'attente explicite, on croit que
     rien ne s'est passé et on reclique. */
  function notice(text, kind = '') {
    positionNotice = text ? { text, kind } : null;
    const el = $('#position-msg');
    if (el) {
      el.textContent = text;
      el.className = `form-msg ${kind}`;
    }
  }

  $('#add-position').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    const submit = form.querySelector('button[type="submit"]');
    const payload = {
      ticker: form.ticker.value,
      quantity: form.quantity.value,
      price: form.price.value,
    };

    submit.disabled = true;          // pas de double enregistrement
    notice('Enregistrement…');
    try {
      await postJSON('/api/positions/add', payload);
      notice(`${payload.ticker} enregistré — actualisation des cours…`, 'ok');
      await renderStocks();          // recharge le scan, P&L compris
      notice(`${payload.ticker} enregistré`, 'ok');
    } catch (err) {
      submit.disabled = false;
      notice(err.message, 'error');
    }
  });

  $('#positions').querySelectorAll('[data-remove]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const ticker = btn.dataset.remove;
      /* La seule action irréversible de toute l'application, et elle
         était à un clic — juste à côté des lignes qu'on survole pour
         lire son P&L. Rien ne permet de retrouver un prix d'achat et
         une date de saisie effacés. */
      if (!confirm(`Retirer ${ticker} du carnet ?\n\n`
          + `Le prix d'achat et la date de saisie seront perdus : `
          + `il faudra les ressaisir à la main.`)) return;
      btn.disabled = true;
      btn.textContent = '…';
      notice(`Retrait de ${ticker} — actualisation…`);
      try {
        await postJSON('/api/positions/remove', { ticker });
        await renderStocks();
        notice(`${ticker} retiré`, 'ok');
      } catch (err) {
        btn.disabled = false;
        btn.textContent = 'Retirer';
        notice(err.message, 'error');
      }
    });
  });
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
          <span class="reco reco-${esc(e.reco)}">${esc(e.reco)}</span>
          <span class="what">à ${nf(e.price)} $${e.signals && e.signals.length
            ? ' — ' + esc(e.signals.join(', ')) : ''}</span>
        </li>`).join('')
    : '<li class="empty">Aucune bascule enregistrée.</li>';
}

/* ------------------------------------------------------------
   CYCLE DE VIE
   ------------------------------------------------------------ */

/* Le carnet est la seule partie de la page où l'on ÉCRIT, et
   `renderStocks` le reconstruit entièrement par innerHTML. Une saisie
   en cours — un prix tapé à moitié — était donc effacée au tour
   suivant, sans prévenir, jusqu'à une minute après l'avoir commencée.
   On saute le rafraîchissement tant que le curseur est dans le carnet :
   les cours peuvent attendre soixante secondes de plus, pas une saisie
   perdue. */
function saisieEnCours() {
  const actif = document.activeElement;
  const carnet = $('#positions');
  return !!(actif && carnet && carnet.contains(actif)
            && /^(INPUT|SELECT|TEXTAREA)$/.test(actif.tagName));
}

function refresh({ force = false } = {}) {
  renderStatus();
  renderQuality();
  if (force || !saisieEnCours()) {
    renderStocks();   // dessine aussi la section « Mes positions »
  }
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
  if (Date.now() - dernier < 60_000 || saisieEnCours()) return;
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
  if (Date.now() - dernier < 60_000 || saisieEnCours()) return;
  dernier = Date.now();
  refresh();
});

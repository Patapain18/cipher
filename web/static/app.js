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

/* Les cases en direct du tableau (choix de Mathis du 24/11/2026) : le cours et
   les trois variations, pendant la séance normale, d'après /api/valeurs/direct
   (le scan en mémoire et le flux, jamais Yahoo). Les autres colonnes gardent
   le rythme du scan ; l'ordre des lignes (le rang de momentum) aussi. */
const CASES_DIRECT = ['price', 'change_1d', 'change_1w', 'change_1m'];
let valeursDirect = null;          // la dernière réponse de /api/valeurs/direct
function valeurDirect(r) {
  const v = valeursDirect?.valeurs?.[r.ticker];
  const sortie = {};
  for (const cle of CASES_DIRECT) sortie[cle] = v && Number.isFinite(v[cle]) ? v[cle] : r[cle];
  return sortie;
}
const texteCase = (cle, v) => (cle === 'price' ? `${nf(v)} $` : pct(v));
const classeCase = (cle, v) => (cle === 'price' ? 'num-cell num' : `num-cell num ${trend(v)}`);

/* Les cases changent EN PLACE (le tableau et sa copie du panneau 2 de la vue
   baie) : ni redessin des 637 cellules, ni focus perdu, toutes les 2 s. */
function appliquerValeursDirect() {
  if (!lastStocks) return;
  const parTicker = new Map(lastStocks.rows.filter((r) => !r.error).map((r) => [r.ticker, r]));
  document.querySelectorAll('tr[data-direct-ticker]').forEach((tr) => {
    const r = parTicker.get(tr.dataset.directTicker);
    if (!r) return;
    const d = valeurDirect(r);
    tr.querySelectorAll('td[data-direct]').forEach((td) => {
      const cle = td.dataset.direct;
      const texte = texteCase(cle, d[cle]);
      const classe = classeCase(cle, d[cle]);
      if (td.textContent !== texte) td.textContent = texte;
      if (td.className !== classe) td.className = classe;
    });
  });
  majNoteDirect();
}

/* La petite ligne sous le tableau, seulement pendant le direct. */
function majNoteDirect() {
  const note = $('#valeurs-direct-note');
  if (!note || !window.PP) return;
  const actif = Boolean(valeursDirect?.en_direct) && !erreurValeurs;
  const scan = dt(valeursDirect?.scan_de || lastStocks?.scanned_at, false);
  const texte = !actif ? ''
    : PP.enPause()
      ? `Direct en pause : cours et variations relus toutes les minutes ; les autres colonnes au scan de ${scan}.`
      : `Cours et variations en direct ; les autres colonnes au scan de ${scan}.`;
  if (note.textContent !== texte) note.textContent = texte;
  note.hidden = !actif;
}

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
    /* Les quatre cases en direct : les derniers chiffres du direct s'il y en a, sinon ceux
       du scan. Le redessin de la minute ne revient donc jamais une minute en arrière. */
    const d = valeurDirect(r);

    return `<tr class="${pos ? 'held' : ''}" data-direct-ticker="${esc(r.ticker)}">
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
      ${CASES_DIRECT.map((cle) => `<td class="${classeCase(cle, d[cle])}" data-direct="${cle}">${texteCase(cle, d[cle])}</td>`).join('')}
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
  /* Tout vendu : la carte garde le résultat des ventes (relecture du 17/11/2026). */
  if (!carte || !window.PP || !maPosition
      || (!maPosition.lignes.length && !(maPosition.total?.nombre_ventes > 0))) return;
  const { total } = maPosition;
  const dont = PP.TEXTES.dontTotal(total);
  PP.remplacer(carte, `
    <div class="label">Ma position</div>
    <div class="value">${PP.euros(total.valeur_eur)}</div>
    <div class="foot"><span class="${PP.sens(total.gain_eur)}">${PP.euros(total.gain_eur, { signe: true })}</span>
      ${maPosition.lignes.length ? 'depuis l\'achat, frais compris' : 'depuis le début, ventes comprises'}</div>
    ${dont ? `<div class="foot">${esc(dont)}</div>` : ''}
    <div class="foot">${PP.euros(PP.sansFraisTotal(maPosition).eur, { signe: true })} sans les frais</div>`);
}

async function renderPositions() {
  const zone = $('#positions');
  if (!zone || !window.PP) return;
  const hauteurAvant = zone.offsetHeight;
  try {
    maPosition = await PP.position({ delai: planRapide() ? 5_000 : 30_000 });
    erreurDirect = null;
  } catch (e) {
    erreurDirect = e.message;
    majBadgeDirect();
    /* Une panne pendant le direct : les derniers chiffres restent, la ligne dit qu'ils ne sont plus en direct. */
    if (maPosition && zone.querySelector('.position-table')) { majLigneDirect(); return; }
    zone.innerHTML = `<p class="error">${esc(e.message)}</p>`;
    return;
  }
  dessinerCartePosition();
  majBadgeDirect();

  const { lignes, total } = maPosition;
  /* Le total compte l'action offerte et le résultat des ventes (choix de Mathis du 17/11/2026) :
     les lignes ne s'additionnent plus jusqu'à lui, la note dit pourquoi. */
  const dont = PP.TEXTES.dontTotal(total);
  if (!lignes.length) {
    /* Tout vendu : le résultat des ventes reste la réponse (relecture du 17/11/2026). */
    zone.innerHTML = total?.nombre_ventes > 0 ? `
      <p class="empty">Aucune action détenue en ce moment. Depuis le début, ventes comprises :
        <span class="${PP.sens(total.gain_eur)}">${PP.euros(total.gain_eur, { signe: true })}</span>
        (${PP.pct(total.gain_pct)}), frais compris.</p>` : `
      <p class="empty">Aucune position. Une décision déjà passée chez ton courtier
        se note dans <strong>Ma position</strong> (touche <kbd>P</kbd>).</p>`;
    document.dispatchEvent(new CustomEvent('stocks:dessine'));
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
  /* Au téléphone, le tableau défile à l'horizontale : son cadre doit
     pouvoir se prendre au clavier (axe, 17/11/2026), comme le grand
     tableau. Le cadre est créé UNE fois puis gardé : à chaque
     rafraîchissement (60 s), seul son contenu change, et seulement s'il a
     changé. Le focus et le défilement restent donc en place, sans qu'un
     lecteur d'écran relise le cadre chaque minute (relectures du 17/11). */
  const tableau = `
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
        ${lignes.length > 1 || dont ? `
        <tfoot><tr>
          <th scope="row" colspan="4">Total</th>
          <td class="num-cell num">${PP.euros(total.valeur_eur)}</td>
          <td class="num-cell num ${PP.sens(total.gain_eur)}">${PP.euros(total.gain_eur, { signe: true })}
            <span class="rank-total">${PP.pct(total.gain_pct)}</span></td>
          <td class="num-cell num sans-frais">${sansFrais(PP.sansFraisTotal(maPosition))}</td>
        </tr></tfoot>` : ''}
      </table>`;
  /* La note est HORS du cadre qui défile : dans une cellule, elle prenait la
     largeur du tableau et se coupait au téléphone (relecture du 17/11/2026). */
  const note = dont ? `Le total compte aussi ${esc(dont.replace(/^dont /, ''))}.` : '';
  let cadre = zone.querySelector('.position-table');
  if (!cadre) {
    zone.innerHTML = `
      <div class="position-table" tabindex="0" role="region" aria-label="Ta position, action par action"></div>
      <p class="position-dont"></p>
      <p class="position-direct" hidden></p>`;
    cadre = zone.querySelector('.position-table');
  }
  /* En place (le direct, 24/11/2026) : toutes les 2 s, seuls les chiffres changent ; le
     cadre, son défilement et le focus restent. */
  const gauche = cadre.scrollLeft;
  if (PP.remplacer(cadre, tableau)) cadre.scrollLeft = gauche;
  const p = zone.querySelector('.position-dont');
  if (p.innerHTML !== note) p.innerHTML = note;
  p.hidden = !note;
  majLigneDirect();
  /* Le panneau 1 de la vue baie EST cette section : il se remesure, seulement si sa
     hauteur a changé (sinon il le ferait toutes les 2 s pendant le direct). */
  if (zone.offsetHeight !== hauteurAvant) document.dispatchEvent(new CustomEvent('stocks:dessine'));
}

/* « Cours de 19:24, en direct. » sous le tableau de « Ma position » ; rien avec
   un ancien serveur (sans le champ `direct`). */
function majLigneDirect() {
  const ligne = $('#positions .position-direct');
  if (!ligne || !window.PP) return;
  const mot = PP.motDuDirect(maPosition?.direct, { erreur: Boolean(erreurDirect) });
  const dernier = (maPosition?.lignes || []).map((l) => l.cours_a).filter(Boolean).sort().pop();
  const texte = mot && dernier ? `Cours de ${heureOuDate(dernier)}, ${mot}.` : '';
  if (ligne.textContent !== texte) ligne.textContent = texte;
  ligne.hidden = !texte;
}

/* L'heure seule pour aujourd'hui, la date et l'heure sinon (à l'heure de Paris). */
function heureOuDate(iso) {
  const jourParis = (d) => d.toLocaleDateString('fr-FR', { timeZone: 'Europe/Paris' });
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return jourParis(d) === jourParis(new Date()) ? PP.heure(iso) : PP.dateHeure(iso);
}

/* Le badge de l'en-tête (« En direct », « En pause », « Toutes les minutes ») et
   son bouton Pause : cachés tant que le serveur ne parle pas du direct. */
let erreurDirect = null;
let erreurValeurs = null;
function majBadgeDirect() {
  const badge = $('#direct-etat');
  const bouton = $('#direct-pause');
  if (!badge || !bouton || !window.PP) return;
  const connu = Boolean(maPosition?.direct || valeursDirect);
  const enDirect = Boolean((maPosition?.direct?.en_direct && !erreurDirect)
    || (valeursDirect?.en_direct && !erreurValeurs));
  const mot = connu ? PP.motDuDirect({ en_direct: enDirect }) : '';
  const texte = mot ? mot[0].toUpperCase() + mot.slice(1) : '';
  if (badge.textContent !== texte) badge.textContent = texte;
  badge.hidden = !connu;
  bouton.hidden = !connu;
  bouton.setAttribute('aria-pressed', String(PP.enPause()));
}

/* ------------------------------------------------------------
   LE DIRECT DANS LA VUE PLAN (choix de Mathis du 24/11/2026)
   ------------------------------------------------------------
   La section « Ma position », la 4e carte et les cases en direct du
   tableau : toutes les 2 s quand le serveur le dit (le flux en direct, ou
   en train de se connecter), 60 s sinon, en pause, ou après une lecture
   ratée. Rien quand le carnet est ouvert (la vue plan est cachée) : on
   rattrape à sa fermeture. Le reste de la page garde ses 60 s.
   ------------------------------------------------------------ */
const planRapide = () => (!erreurDirect && maPosition?.direct?.rapide === true)
  || (!erreurValeurs && valeursDirect?.rapide === true);

async function renderValeursDirect() {
  if (!window.PP) return;
  try {
    valeursDirect = await PP.lire('/api/valeurs/direct', { delai: planRapide() ? 5_000 : 30_000 });
    erreurValeurs = null;
  } catch (e) {
    /* Un ancien serveur (404) ou une panne : les chiffres du scan, sans rien dire de plus. */
    valeursDirect = null;
    erreurValeurs = e.message;
  }
  appliquerValeursDirect();
  majBadgeDirect();
}

async function tourDirect() {
  if ($('.shell')?.hidden) return;
  await Promise.all([renderPositions(), renderValeursDirect()]);
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
}

function drawHistory() {
  const points = historyData.series[historyTicker] || [];
  $('#history-ticker').textContent = historyTicker;
  $('#history-range').textContent =
    ` · ${points.length} relevés du ${dt(historyData.from)} au ${dt(historyData.to)}`;
  $('#history-chart').innerHTML = lineChart(points, { label: (p) => dt(p.t) });
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
   LE THERMOMÈTRE DU MARCHÉ
   ------------------------------------------------------------
   Trois aiguilles lues avec le seul passé connu (thermometre.py), et
   ce que le thermomètre a valu depuis 1973. Un thermomètre, pas une
   boule de cristal : il dit si le marché est tendu, jamais quand il
   chutera. Chaque aiguille montre sa propre mesure, même quand elle
   ne vaut rien : c'est la règle du projet (comme le score technique).
   ------------------------------------------------------------ */
const NIVEAUX_THERMO = ['calme', 'à surveiller', 'tendu', 'très tendu'];

/* Un taux sans signe : 0,205 → « 21 % », toujours en mono (DA). */
const taux = (x) => PP.pct(x, { signe: false, decimales: 0 });
const tauxNum = (x) => `<span class="num">${taux(x)}</span>`;
const pctNum = (x, d = 0) => `<span class="num">${PP.pct(x, { decimales: d })}</span>`;

/* « 2026-11 » → « nov. 2026 ». */
const moisAnnee = (ym) => {
  const [a, m] = String(ym || '').split('-').map(Number);
  if (!a || !m) return '—';
  return new Date(a, m - 1, 15).toLocaleDateString('fr-FR', { month: 'short', year: 'numeric' });
};

/* La mesure d'une aiguille, en une phrase. Les conclusions suivent les
   chiffres (relecture du 15/11/2026) : trop peu de mois allumés → on ne
   conclut pas ; presque le même taux allumée ou éteinte → elle n'apporte
   rien. */
function phraseAiguille(a) {
  if (!a || !a.allumee || a.allumee.taux == null) return 'Pas encore mesurée.';
  const debut = String(a.depuis || '').slice(0, 4);
  let p = `Depuis ${debut}, allumée, la chute a suivi ${tauxNum(a.allumee.taux)} du temps ; `
    + `éteinte, ${tauxNum(a.eteinte.taux)}.`;
  if (a.trop_peu) {
    p += ` Allumée seulement <span class="num">${a.allumee.mois}</span> mois : trop peu pour conclure.`;
  } else if (Math.abs(a.allumee.taux - a.eteinte.taux) < 0.03) {
    p += ' Presque aucune différence : pour une chute du S&amp;P 500, elle n\'apporte rien.';
  }
  return p;
}

/* Ce que l'alarme ne dit PAS : la suite sur 12 mois après une alarme,
   face à n'importe quel mois. Chaque mot suit les chiffres du jour de la
   mesure : si demain une alarme était suivie de baisses, la phrase
   changerait d'elle-même. */
function phraseApresAlarme(av) {
  const a = av?.apres_alarme;
  const t = av?.tous;
  if (!a || !t) return '';
  const moy = pctNum(a.moyenne, 1);
  const moyTous = pctNum(t.moyenne, 1);
  let p;
  if (a.moyenne > 0) {
    const comme = Math.abs(a.moyenne - t.moyenne) < 0.02 ? 'comme d\'habitude'
      : a.moyenne > t.moyenne ? 'plus que d\'habitude' : 'moins que d\'habitude';
    p = `Une alarme n'annonce pas une baisse : 12&nbsp;mois après un niveau 2 ou plus, `
      + `le S&amp;P 500 a gagné en moyenne ${moy}, ${comme} (${moyTous}).`;
  } else {
    p = `12&nbsp;mois après un niveau 2 ou plus, le S&amp;P 500 a fait en moyenne ${moy}, contre ${moyTous} d'habitude.`;
  }
  const frequence = a.hausse_20 > t.hausse_20 ? 'plus fréquente'
    : a.hausse_20 < t.hausse_20 ? 'moins fréquente' : 'aussi fréquente';
  p += ` Une hausse de 20&nbsp;% ou plus y était ${frequence} (${tauxNum(a.hausse_20)} contre ${tauxNum(t.hausse_20)}).`;
  if (a.ecart_type > t.ecart_type * 1.15) {
    p += a.moyenne > 0 ? ' Ce qui change surtout, c\'est la taille des mouvements, dans les deux sens.'
      : ' Les mouvements y étaient aussi plus grands.';
  }
  return `<p class="thermo-phrase thermo-nedit">${p}</p>`;
}

/* Les chutes « vues », y compris aucune ou une seule. */
function phraseVues(al) {
  const v = al.episodes_vus;
  const n = `<span class="num">${al.episodes_chute}</span>`;
  const fin = 'pendant qu\'il restait au moins 20&nbsp;% à perdre.';
  if (!v) return `Aucune des ${n} chutes n'a eu d'alarme ${fin}`;
  return `<span class="num">${v}</span> ${v > 1 ? 'chutes' : 'chute'} sur ${n} ${v > 1 ? 'ont' : 'a'} eu une alarme ${fin}`;
}

/* Les fausses alarmes, au singulier comme au pluriel, et « baisse déjà
   commencée » seulement si c'est vrai (au moins 3 % sous le plus haut). */
function phraseFausses(al) {
  const n = al.fausses_alarmes;
  if (!n) return 'Aucune alarme n\'a sonné pour rien.';
  let p = n === 1 ? '<span class="num">1</span> alarme n\'a été suivie d\'aucune chute'
    : `<span class="num">${n}</span> alarmes n'ont été suivies d'aucune chute`;
  const dp = al.fausses_deja_perdu;                    // [le plus bas, le plus proche de 0]
  if (dp) {
    const plage = dp[0] === dp[1] ? pctNum(dp[0]) : `de ${pctNum(dp[1])} à ${pctNum(dp[0])}`;
    p += dp[1] <= -0.03
      ? `${n === 1 ? ' ; elle a sonné' : ' ; elles ont toutes sonné'} après une baisse déjà commencée (${plage} depuis le plus haut d'un an)`
      : ` (le S&amp;P 500 était alors entre ${tauxNum(Math.abs(dp[1]))} et ${tauxNum(Math.abs(dp[0]))} sous son plus haut d'un an)`;
  }
  return `${p}.`;
}

/* L'essai de la règle, en points de rendement par an (« point » au
   singulier sous 2), pour chaque comparaison équitable disponible. */
function phraseRegle(r, seuil) {
  const ecart = (v) => v.regle.par_an - v.toujours.par_an;
  const decrire = (x) => {
    const n = Number((Math.abs(x) * 100).toFixed(1));        // le pluriel suit le nombre AFFICHÉ
    if (n < 0.05) return 'le même rendement par an';
    const pts = `<span class="num">${PP.nombre(n, 1)}</span>&nbsp;point${n >= 2 ? 's' : ''}`;
    return x < 0 ? `${pts} de moins par an` : `${pts} de plus par an`;
  };
  const v1 = r.prix_seul;
  const v2 = r.avec_revenus;
  let p = `Être à l'abri dès le niveau ${seuil} (décidé à la fin du mois, fait à la première séance du mois suivant), `
    + `sinon investi : depuis ${esc(String(v1.debut).slice(0, 4))}, sans compter aucun revenu, ${decrire(ecart(v1))}`;
  if (v2) {
    p += ` ; depuis ${esc(String(v2.debut).slice(0, 4))}, en comptant les dividendes quand on est investi `
      + `et les intérêts à l'abri, ${decrire(ecart(v2))}`;
  }
  p += `. Sa pire baisse : ${pctNum(v1.regle.pire_baisse)} au lieu de ${pctNum(v1.toujours.pire_baisse)}. `
    + `Investi ${tauxNum(r.temps_investi)} du temps, <span class="num">${r.changements}</span> changements.`;
  return p;
}

/* Le dernier contenu affiché : s'il n'a pas changé, on ne touche à rien
   (le cache du serveur dure une heure, la page se rafraîchit chaque
   minute : sans ça, les <details> ouverts se refermaient sous les yeux). */
let thermoDernierHtml = '';

function htmlThermometre(t) {
  const l = t.lecture;
  const m = t.mesure;
  const niveau = l.niveau;
  const echelle = [1, 2, 3].map((n) => `<i class="${n <= niveau ? 'on' : ''}"></i>`).join('');
  const parNiveau = m?.niveaux?.[niveau];
  const base = m?.base_complet;
  const al = m?.alarme;
  const depuis = String(m?.periode?.debut_complet || '').slice(0, 4);
  const chute = 'le S&amp;P 500 qui perd au moins 20&nbsp;% dans les 12&nbsp;mois suivants';

  let resume;
  const suivant = m?.niveaux?.[niveau + 1];
  if (l.niveau_minimum && parNiveau && base && suivant) {
    /* Une aiguille est inconnue : le vrai niveau est celui-ci ou le
       suivant. On donne les deux mesures, jamais une seule. */
    const dire = (n) => (n.trop_rare ? 'trop rare pour compter' : `${tauxNum(n.taux)} du temps`);
    resume = `<p class="thermo-phrase">Une aiguille est inconnue : le vrai niveau est ${niveau} ou ${niveau + 1}.
        Depuis ${depuis}, ${chute.replace('qui perd', 'a perdu')} : au niveau ${niveau}, ${dire(parNiveau)} ;
        au niveau ${niveau + 1}, ${dire(suivant)}. Sur tous les mois : ${tauxNum(base.taux)}.</p>`;
  } else if (!parNiveau || !base) {
    resume = '<p class="thermo-phrase">La mesure sur le passé n\'est pas encore faite (python3 thermometre.py --mesurer).</p>';
  } else if (parNiveau.trop_rare) {
    /* Un niveau trop rare ne dit rien : on le dit, avec la chute mesurée
       et, s'il s'agit d'une alarme, le chiffre de toutes les alarmes. */
    resume = `<p class="thermo-phrase">La chute mesurée : ${chute}. Le niveau ${niveau} est trop rare pour en
        tirer quoi que ce soit : <span class="num">${parNiveau.mois}</span> mois depuis ${depuis}${parNiveau.annees?.length
          ? `, tous en ${esc(parNiveau.annees.join(', '))}` : ''}.${al && niveau >= al.seuil && al.chute_apres?.taux != null
          ? ` Pour tous les mois d'alarme (niveau ${al.seuil} ou plus), la chute a suivi ${tauxNum(al.chute_apres.taux)} du temps.` : ''}
        Sur tous les mois : ${tauxNum(base.taux)}.</p>`;
  } else {
    resume = `<p class="thermo-phrase">Depuis ${depuis}, quand il était au niveau ${niveau}, ${chute.replace('qui perd', 'a perdu')}
        ${tauxNum(parNiveau.taux)} du temps (<span class="num">${parNiveau.chutes}</span> mois sur
        <span class="num">${parNiveau.mois}</span>). Sur tous les mois : ${tauxNum(base.taux)}.</p>`;
  }

  const env = l.envolee;
  const manquants = env.manquants || [];
  const absents = manquants.length
    ? `${manquants.length === 1 ? 'Un secteur n\'a' : `${manquants.length} secteurs n'ont`} pas répondu (${esc(manquants.join(', '))})`
    : '';
  const faitEnvolee = env.inconnue
    ? `${absents} : impossible de dire si elle est éteinte, un seul secteur suffit à l'allumer.`
      + (env.secteur ? ` Parmi les autres, ${esc(env.secteur)} : ${pctNum(env.net)} de plus que le marché en deux ans.` : '')
    : `${env.secteur ? esc(env.secteur) : '—'} : ${env.net == null ? '—' : pctNum(env.net)} de plus que le marché en deux ans,
        le plus fort des secteurs. Elle s'allume à +100&nbsp;%, le seuil d'une étude de Harvard sur la chute du secteur
        lui-même, pas du marché.${absents ? ` ${absents}.` : ''}`;

  const aiguilles = [
    { nom: 'La tendance', etat: l.tendance.allumee ? 'allumée' : 'éteinte',
      fait: `Le S&amp;P 500 est à ${pctNum(l.tendance.ecart, 1)} de sa moyenne des 200 dernières séances.
        Elle s'allume quand il passe dessous.`,
      mesure: m?.aiguilles?.tendance },
    { nom: 'L\'agitation', etat: l.agitation.allumee ? 'allumée' : 'éteinte',
      fait: `La volatilité du dernier mois est de <span class="num">${PP.pct(l.agitation.vol, { signe: false, decimales: 1 })}</span>.
        Elle s'allume au-dessus de <span class="num">${PP.pct(l.agitation.seuil, { signe: false, decimales: 1 })}</span>
        (plus haut que 80&nbsp;% de tout le passé connu).`,
      mesure: m?.aiguilles?.agitation },
    { nom: 'L\'envolée', etat: env.allumee ? 'allumée' : env.inconnue ? 'inconnue' : 'éteinte',
      fait: faitEnvolee, mesure: m?.aiguilles?.envolee },
  ];
  const listeAiguilles = aiguilles.map((a) => `
      <li class="thermo-aiguille">
        <p class="thermo-aiguille-tete"><span class="thermo-nom">${a.nom}</span>
          <span class="badge ${a.etat === 'allumée' ? '' : 'muted'} thermo-badge">${a.etat}</span></p>
        <p>${a.fait}</p>
        <p class="thermo-mesure">${phraseAiguille(a.mesure)}</p>
      </li>`).join('');

  const alarmes = al ? `
      <details class="thermo-details" data-cle="alarmes">
        <summary>Ses alarmes depuis ${depuis} (niveau ${al.seuil} ou plus)</summary>
        <p>Après un mois d'alarme, une chute de 20&nbsp;% a suivi ${tauxNum(al.chute_apres.taux)} du temps.${al.intervalle
          ? ` Avec si peu de chutes, ce chiffre est fragile : il pourrait aussi bien valoir ${tauxNum(al.intervalle[0])}
            que ${tauxNum(al.intervalle[1])} (marge d'incertitude à 90&nbsp;%).` : ''}</p>
        <p>${phraseVues(al)} ${phraseFausses(al)}</p>
        <div class="thermo-table">
          <table>
            <thead><tr>
              <th scope="col">Creux de la chute</th>
              <th scope="col">Alarme</th>
              <th scope="col" class="num-cell">Déjà perdu</th>
              <th scope="col" class="num-cell">Puis encore</th>
            </tr></thead>
            <tbody>${(al.chutes || []).map((c) => `
              <tr>
                <td>${esc(moisAnnee(c.creux))}</td>
                <td>${c.vue ? esc(moisAnnee(c.alarme)) : 'manquée'}</td>
                <td class="num-cell num">${c.vue ? PP.pct(c.deja_perdu, { decimales: 0 }) : '—'}</td>
                <td class="num-cell num">${c.vue ? PP.pct(c.encore_perdu, { decimales: 0 }) : '—'}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>
        <p class="thermo-note">« Déjà perdu » : ce que le S&amp;P 500 avait perdu depuis son plus haut
          d'un an quand l'alarme a sonné. « Puis encore » : la suite, dans les 12&nbsp;mois.</p>
      </details>` : '';

  const r = m?.regle;
  const regle = r?.prix_seul ? `
      <details class="thermo-details" data-cle="essai">
        <summary>Un essai sur le passé, pour juger le thermomètre</summary>
        <p>${phraseRegle(r, al ? al.seuil : 2)}</p>
        <p class="thermo-note">Ce n'est pas une règle à suivre : sans frais ni impôts (chaque vente gagnante
          est imposée). Elle sert seulement à mesurer ce que vaut le thermomètre.</p>
      </details>` : '';

  return `
    <div class="thermo-tete">
      <span class="thermo-echelle" aria-hidden="true">${echelle}</span>
      <p class="thermo-niveau">Niveau <span class="num">${niveau}</span>${l.niveau_minimum ? ' au moins' : ''} sur 3 :
        <strong>${NIVEAUX_THERMO[niveau]}</strong></p>
    </div>
    ${resume}
    ${phraseApresAlarme(m?.a_venir)}
    <ul class="thermo-aiguilles">${listeAiguilles}</ul>
    ${alarmes}
    ${regle}
    <p class="thermo-avertissement">${esc(t.avertissement)}${m?.maj ? ` Mesure du ${esc(PP.jour(m.maj.slice(0, 10)))}.` : ''}</p>`;
}

async function renderThermometre() {
  const section = $('#section-thermometre');
  const zone = $('#thermometre');
  const etat = $('#thermo-etat');
  if (!section || !zone || !window.PP) return;
  let t;
  let html;
  try {
    t = await api('/api/thermometre');
    html = htmlThermometre(t);
  } catch (e) {
    /* Une panne de Yahoo ne doit ni effacer la dernière lecture, ni
       passer au rouge (le rouge dit « baisse » dans cette appli). */
    if (thermoDernierHtml) {
      if (etat) etat.textContent = `Mise à jour impossible pour l'instant (${e.message}) : la lecture affichée est la dernière connue.`;
    } else {
      zone.innerHTML = `<p class="thermo-attente">Thermomètre indisponible pour l'instant (${esc(e.message)}). Nouvel essai dans une minute.</p>`;
    }
    return;
  }
  if (etat) etat.textContent = '';
  $('#thermo-date').textContent = `lecture du ${PP.jour(t.lecture.date)}${t.lecture.provisoire ? ', en séance (cours provisoire)' : ''}`;
  if (html === thermoDernierHtml) return;            // rien n'a changé : on ne touche à rien

  // On garde ce que tu as ouvert, et la place du clavier.
  const ouverts = [...zone.querySelectorAll('details[data-cle]')].filter((d) => d.open).map((d) => d.dataset.cle);
  const actif = document.activeElement;
  const focusCle = actif && actif.tagName === 'SUMMARY' ? actif.closest('#thermometre details[data-cle]')?.dataset.cle : null;
  zone.innerHTML = html;
  thermoDernierHtml = html;
  zone.classList.remove('thermo-charge');
  ouverts.forEach((cle) => { const d = zone.querySelector(`details[data-cle="${cle}"]`); if (d) d.open = true; });
  if (focusCle) zone.querySelector(`details[data-cle="${focusCle}"] > summary`)?.focus({ preventScroll: true });
}

/* ------------------------------------------------------------
   LE CREUX DU MARCHÉ
   ------------------------------------------------------------
   De combien le S&P 500 et le Nasdaq Composite sont sous leur record,
   et les baisses de 20 % ou plus du passé (creux.py, /api/creux). Tous
   les textes viennent du serveur, calculés depuis les chiffres : la
   page ne fait que les insérer (par esc). Des faits du passé : jamais
   une date de fin, jamais un conseil, jamais de rouge.
   ------------------------------------------------------------ */
function htmlCreux(c) {
  /* En tête : « S&P 500 −11,6 % de son record » (le chiffre en chiffres, le
     reste en mots), et la date d'une vieille lecture à côté de son chiffre. */
  const suite = (t) => (t ? ` <span class="creux-suite">${esc(t)}</span>` : '');
  const tete = c.indices.map((x) => `
      <p class="creux-chiffre"><span class="creux-indice">${esc(x.nom)}</span>
        <span class="${x.tete_num ? 'num ' : ''}creux-valeur">${esc(x.textes.tete)}</span>${suite(x.textes.tete_suite)}${suite(x.textes.tete_date)}</p>`).join('');
  const blocs = c.indices.map((x) => `
      <li class="creux-bloc">
        <p class="creux-nom">${esc(x.nom)}</p>
        ${x.etat ? `<p class="creux-ancien">${esc(x.etat)}</p>` : ''}
        <p>${esc(x.textes.en_cours)}</p>
        <p>${esc(x.textes.histoire)}</p>
        <p class="creux-barre">${esc(x.textes.barre)}</p>
      </li>`).join('');
  /* Un détail à ouvrir par indice. La légende est HORS du cadre qui
     défile (au téléphone, une <caption> prend la largeur du tableau et
     s'y coupe : piège connu). */
  const details = c.indices.filter((x) => x.textes.detail).map((x) => `
      <details class="creux-details" data-cle="${esc(x.symbole)}">
        <summary>${esc(x.textes.detail)}</summary>
        <div class="creux-table" tabindex="0" role="region" aria-label="${esc(x.textes.detail)}" data-cle="${esc(x.symbole)}">
          <table>
            <thead><tr>
              <th scope="col">Record</th>
              <th scope="col">Creux</th>
              <th scope="col" class="num-cell">Baisse</th>
              <th scope="col" class="num-cell">Descente</th>
              <th scope="col" class="num-cell">Retour depuis le record</th>
              <th scope="col" class="num-cell">Retour depuis le creux</th>
            </tr></thead>
            <tbody>${x.baisses.map((b) => `
              <tr>
                <th scope="row"><span class="num">${esc(b.textes.record)}</span>
                  <span class="num creux-niveau">${esc(b.textes.niveau)}</span></th>
                <td class="num">${esc(b.textes.creux)}</td>
                <td class="num-cell num">${esc(b.textes.baisse)}</td>
                <td class="num-cell">${esc(b.textes.descente)}</td>
                <td class="num-cell">${esc(b.textes.retour_record)}</td>
                <td class="num-cell">${esc(b.textes.retour_creux)}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>
        <p class="creux-note">${esc(c.legende)}</p>
      </details>`).join('');
  return `
    <div class="creux-tete">${tete}</div>
    <ul class="creux-blocs">${blocs}</ul>
    ${details}
    <ul class="creux-notes">${c.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>`;
}

/* Le dernier contenu affiché : s'il n'a pas changé (le cache du serveur
   dure une heure, la page se rafraîchit chaque minute), on ne touche à
   rien. `creuxLu` : une vraie lecture est affichée (pas un message). */
let creuxDernierHtml = '';
let creuxLu = false;

async function renderCreux() {
  const zone = $('#creux');
  const etat = $('#creux-etat');
  if (!zone) return;
  let html;
  let c = null;
  try {
    let res;
    /* Un serveur muet : fetch lève une erreur du navigateur, en anglais
       (« Load failed », « Failed to fetch ») ; la page le dit en français. */
    try { res = await fetch('/api/creux'); } catch { throw new Error('le serveur de l\'appli ne répond pas'); }
    if (res.status === 404) {
      /* Le serveur de l'appli date d'avant cette section : son Python ne
         se relit qu'en relançant l'appli (les pages, elles, se relisent seules). */
      html = '<p class="creux-attente">Le creux du marché s\'affichera quand l\'appli sera relancée&nbsp;: son serveur '
        + 'ne connaît pas encore cette section.</p>';
    } else {
      let json;
      try { json = await res.json(); } catch { throw new Error(`réponse illisible du serveur (${res.status})`); }
      if (!json.ok) throw new Error(json.error || 'erreur inconnue');
      c = json.data;
      html = htmlCreux(c);
    }
  } catch (e) {
    /* Une panne (Yahoo, le serveur) n'efface pas la dernière lecture et ne
       passe pas au rouge (le rouge dit « baisse » dans cette appli). */
    if (creuxLu) {
      if (etat) etat.textContent = `Mise à jour impossible pour l'instant (${e.message}) : la lecture affichée est la dernière connue.`;
      return;
    }
    html = `<p class="creux-attente">Le creux du marché est indisponible pour l'instant (${esc(e.message)}). `
      + 'Nouvel essai dans une minute.</p>';
  }
  if (etat) etat.textContent = '';
  if (c || !creuxLu) $('#creux-date').textContent = c ? c.cloture : '';
  if (!c && creuxLu) return;                          // un 404 après une vraie lecture : on la garde
  if (html === creuxDernierHtml) return;             // rien n'a changé : on ne touche à rien

  // On garde ce que tu as ouvert, la place du clavier et le défilement des tableaux.
  const ouverts = [...zone.querySelectorAll('details[data-cle]')].filter((d) => d.open).map((d) => d.dataset.cle);
  const defile = new Map([...zone.querySelectorAll('.creux-table[data-cle]')].map((t) => [t.dataset.cle, t.scrollLeft]));
  const actif = document.activeElement;
  let focus = null;
  if (actif && zone.contains(actif)) {
    if (actif.tagName === 'SUMMARY') focus = `details[data-cle="${actif.closest('details[data-cle]')?.dataset.cle}"] > summary`;
    else if (actif.classList.contains('creux-table')) focus = `.creux-table[data-cle="${actif.dataset.cle}"]`;
  }
  zone.innerHTML = html;
  creuxDernierHtml = html;
  creuxLu = Boolean(c);
  zone.classList.remove('creux-charge');
  ouverts.forEach((cle) => { const d = zone.querySelector(`details[data-cle="${cle}"]`); if (d) d.open = true; });
  defile.forEach((x, cle) => { const t = zone.querySelector(`.creux-table[data-cle="${cle}"]`); if (t) t.scrollLeft = x; });
  if (focus) zone.querySelector(focus)?.focus({ preventScroll: true });
}

/* ------------------------------------------------------------
   LE BANC DES PRÉVISIONS
   ------------------------------------------------------------
   Le verdict des deux mesures du banc (banc.py, écrites une seule
   fois dans data/banc/, lues par /api/banc). La même grammaire que le
   thermomètre (piste A, choisie par Mathis le 20/11/2026) : une échelle
   et le verdict en tête, les deux critères côte à côte, le détail
   replié. Toutes les phrases sont calculées depuis les chiffres, jamais
   écrites en dur. Des faits sur le passé : jamais une prévision, jamais
   un conseil, jamais de rouge.
   ------------------------------------------------------------ */
const bancNum = (texte) => `<span class="num">${texte}</span>`;
/** 0,4979 → « 49,80 % » */
const bancTaux = (x) => PP.pct(x, { signe: false, decimales: 2 });
/** 0,0074 → « 0,74 » (des points de bonnes réponses) */
const bancPoints = (x) => PP.nombre(x * 100, 2);
/** Un montant rond sans ses centimes : 200 → « 200 € ». */
const bancEurosRond = (x) => (Number.isInteger(x) ? `${PP.nombre(x, 0)}&nbsp;€` : PP.euros(x));
/** « 2020-09-14 » → « 14/09/2020 » (une date seule est lue à midi). */
const bancDateCourte = (iso) => {
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('fr-FR');
};
/** « 2019-11-01 » → « 1ᵉʳ novembre 2019 ». */
const bancDateLongue = (iso) => {
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' }).replace(/^1 /, '1ᵉʳ ');
};
/** « 2020-09-14 » → « septembre 2020 ». */
const bancMois = (iso) => {
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('fr-FR', { month: 'long', year: 'numeric' });
};
/** Le jour et l'heure de Paris d'une mesure : « 20 novembre 2026 », « 17 h 06 ». */
const bancJourMesure = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleDateString('fr-FR', { timeZone: 'Europe/Paris', day: 'numeric', month: 'long', year: 'numeric' });
};
const bancHeureMesure = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleTimeString('fr-FR', { timeZone: 'Europe/Paris', hour: '2-digit', minute: '2-digit' }).replace(':', ' h ');
};
/* La typographie française : jamais « : », « ; » ni « » » en début de
   ligne, jamais « « » en fin de ligne (des espaces insécables). Sur le
   HTML de la section, dont aucun attribut ne contient ces signes. */
const bancInsecables = (html) => html.replace(/ ([:;»])/g, '&nbsp;$1').replace(/« /g, '«&nbsp;');
/** Une liste en français : « a », « a et c », « a, c et d ». */
const bancListe = (mots) => (mots.length < 2 ? mots.join('')
  : `${mots.slice(0, -1).join(', ')} et ${mots[mots.length - 1]}`);
/** « de plus de 0,74 à 1,19 point » (le pluriel suit le nombre affiché, comme le thermomètre). */
const bancFourchette = (valeurs) => {
  const bas = bancPoints(Math.min(...valeurs));
  const haut = bancPoints(Math.max(...valeurs));
  const pluriel = Number((Math.max(...valeurs) * 100).toFixed(2)) >= 2 ? 's' : '';
  return bas === haut ? `de plus de ${bancNum(bas)} point${pluriel}`
    : `de plus de ${bancNum(bas)} à ${bancNum(haut)} point${pluriel}`;
};

/* Les faits du verdict, ou null si une des deux mesures manque : la
   section dit alors laquelle, sans inventer de verdict. */
function faitsDuBanc(v) {
  const m1 = v?.critere1;        // la mesure du critère 1, telle que le serveur la lit
  const m2 = v?.critere2;        // celle du critère 2
  if (!m1 || !m2 || m1.etat !== 'mesure' || m2.etat !== 'mesure') return null;
  const candidates = m1.methodes.filter((m) => m.candidate);
  const n = candidates.length;
  const soir = m2.heures.soir;
  const matin = m2.heures.matin;
  const combien = (liste) => (!liste.length ? 'aucune ne passe'
    : `${liste.length} sur ${n} passe${liste.length > 1 ? 'nt' : ''}`);

  /* Le verdict, en tête. */
  const aucune = !m1.passent.length && !m2.passent.length;
  const parCritere = (k, i) => (!k ? `aucune ne passe le critère ${i}`
    : `${bancNum(k)} sur ${bancNum(n)} passe${k > 1 ? 'nt' : ''} le critère ${i}`);
  const titre = aucune ? `<strong>Aucune</strong> des ${bancNum(n)} méthodes ne passe`
    : `${parCritere(m1.passent.length, 1)} ; ${parCritere(m2.passent.length, 2)}`.replace(/^a/, 'A');
  const resume = `Le banc a testé ${n} façons de prévoir le lendemain des ${m1.valeurs} valeurs, sur les séances `
    + `du ${bancDateLongue(m1.debut)} au ${bancDateLongue(m1.fin)}.`
    + (aucune ? ' Aucune ne passe le critère 1 (avoir raison nettement plus souvent que le hasard et que « ça monte '
      + `toujours ») ni le critère 2 (finir au-dessus de SPY et du panier des ${m1.valeurs} gardés, frais compris).` : '');

  /* Critère 1 : voir quelque chose. « Nettement » : avec 99 % de certitude.
     Le cas qui intrigue : une méthode nettement au-dessus du tirage au sort,
     mais pas de « ça monte toujours », alors que son taux peut dépasser le
     sien. Ce qu'elle fait face à lui se calcule depuis l'écart mesuré. */
  const presque = candidates.filter((m) => m.vu_tirage && !m.vu_monte && !m.passe);
  const points = (x) => {
    const p = Number((Math.abs(x) * 100).toFixed(2));
    return `${bancNum(`${x > 0 ? '+' : '−'}${bancPoints(Math.abs(x))}`)}&nbsp;point${p >= 2 ? 's' : ''}`;
  };
  const phrasePresque = (m) => {
    const ecart = Number((m.ecart_monte * 100).toFixed(2));
    const face = ecart > 0 ? `son avance sur lui (${points(m.ecart_monte)}) reste dans la marge du hasard, et dire `
      + '« hausse » partout fait à peu près aussi bien'
      : ecart < 0 ? `elle fait même moins bien que lui (${points(m.ecart_monte)})`
        : 'elle fait autant que lui, à moins de 0,01 point près';
    return `${esc(m.cle)} (${esc(m.nom)}) a raison nettement plus souvent que le tirage au sort, mais pas nettement `
      + `plus souvent que « ça monte toujours » : ${face}.`;
  };
  const c1 = {
    verdict: combien(m1.passent),
    passe: m1.passent.length > 0,
    regle: `Avoir raison nettement plus souvent qu'un tirage au sort et que « ça monte toujours » (${bancNum(bancTaux(m1.monte))} `
      + `de bonnes réponses), avec ${bancNum(`${PP.nombre(m1.niveau * 100, 0)}&nbsp;%`)} de certitude.`,
    barre: 'La barre de chaque méthode : le taux qu\'il fallait dépasser pour que son avance soit nette, c\'est-à-dire que '
      + `le hasard seul ne la donne qu'environ une fois sur ${PP.nombre(Math.round(1 / (1 - m1.niveau)), 0)}.`,
    porte: m1.porte_a
      ? 'La porte A s\'ouvre : la dernière année, gardée au coffre, peut être regardée.'
      : 'La porte A reste fermée : la dernière année, gardée au coffre, n\'a pas été ouverte.',
    presque: presque.map(phrasePresque).join(' '),
    lignes: candidates,
  };

  /* Ce que le test pouvait voir : un « non » n'est pas une preuve d'absence.
     Un écart vrai égal au seuil n'est vu qu'une fois sur deux (le rapport du
     critère 1). */
  const seuilsTirage = candidates.map((m) => m.seuil_tirage);
  const seuilsMonte = candidates.map((m) => m.seuil_monte);
  const varie = (vals) => bancPoints(Math.min(...vals)) !== bancPoints(Math.max(...vals));
  const voyait = `${m1.passent.length ? 'Ce test a une limite' : 'Ce « non » a une limite'} : `
    + `${varie(seuilsTirage) || varie(seuilsMonte) ? 'selon la méthode, ' : ''}le test ne voyait que les écarts `
    + `${bancFourchette(seuilsTirage)} face au tirage au sort, et ${bancFourchette(seuilsMonte)} face à « ça monte `
    + 'toujours ». Même un avantage vrai de cette taille n\'était vu qu\'une fois sur deux, et un plus petit pouvait '
    + 'passer inaperçu.';

  /* Critère 2 : la stratégie de 200 €. Une méthode arrêtée à 2 € ou moins
     est dite par sa date, jamais par son capital final, qui ne veut plus
     rien dire (règle du rapport du critère 2). Ruinées : arrêtées le soir
     ET le matin ; une méthode arrêtée à une seule heure a sa phrase. */
  const lignes2 = candidates.map((m) => ({ m, soir: soir.methodes[m.cle], matin: matin.methodes[m.cle] }));
  const ruinees = lignes2.filter((l) => l.soir.arret && l.matin.arret);
  const dates = ruinees.flatMap((l) => [l.soir.arret, l.matin.arret]).sort();
  let quand = '';
  if (dates.length) {
    const [d0, d1] = [dates[0], dates[dates.length - 1]];
    quand = bancMois(d0) === bancMois(d1) ? `en ${bancMois(d0)}`
      : d0.slice(0, 4) === d1.slice(0, 4) ? `entre ${bancMois(d0).replace(/ \d{4}$/, '')} et ${bancMois(d1)}`
        : `entre ${bancMois(d0)} et ${bancMois(d1)}`;
  }
  /* D'où vient la barre : le meilleur des deux repères gardés. */
  const source = (h) => (h.barre === h.panier ? `le panier des ${m1.valeurs} gardé`
    : h.barre === h.spy ? 'SPY gardé' : 'le meilleur repère gardé');
  const sourceBarre = source(soir) === source(matin) ? source(soir) : 'le meilleur de SPY et du panier gardés';
  const fallait = `il lui fallait plus de ${bancNum(PP.euros(soir.barre))} et ${bancNum(PP.euros(matin.barre))} (${sourceBarre})`;
  const aHeure = (h, quandAchat) => (h.arret ? `tombe à 2&nbsp;€ ou moins en achetant ${quandAchat}, le ${bancDateCourte(h.arret)}`
    : `finit à ${bancNum(PP.euros(h.final))} en achetant ${quandAchat}`);
  const autres = lignes2.filter((l) => !(l.soir.arret && l.matin.arret)).map((l) => {
    const qui = `${esc(l.m.cle)} (${esc(l.m.nom)})`;
    if (!l.soir.arret && !l.matin.arret) {
      return `${qui} finit à ${bancNum(PP.euros(l.soir.final))} en achetant le soir et ${bancNum(PP.euros(l.matin.final))} `
        + `le matin ; ${fallait}${l.soir.passe && l.matin.passe ? ' : elle passe' : ''}.`;
    }
    return `${qui} ${aHeure(l.soir, 'le soir')}, et ${aHeure(l.matin, 'le matin')} ; ${fallait}.`;
  });
  const NOMBRES = ['Aucune', 'Une', 'Deux', 'Trois', 'Quatre'];
  const c2 = {
    verdict: combien(m2.passent),
    passe: m2.passent.length > 0,
    capital: m2.capital,
    regle: `${bancNum(bancEurosRond(m2.capital))} sur une seule valeur à la fois, ${bancNum(bancEurosRond(m2.frais_ordre))} `
      + 'par ordre et l\'écart du courtier. Il fallait finir au-dessus de SPY gardé et du panier des '
      + `${m1.valeurs} gardé (le soir : ${bancNum(PP.euros(soir.spy))} et ${bancNum(PP.euros(soir.panier))}), `
      + 'en achetant le soir comme le matin.',
    ruine: ruinees.length
      ? `${NOMBRES[ruinees.length] || ruinees.length} ${ruinees.length > 1 ? 'sont ruinées' : 'est ruinée'} par les frais : `
        + `${bancListe(ruinees.map((l) => esc(l.m.cle)))} tomb${ruinees.length > 1 ? 'ent' : 'e'} à 2&nbsp;€ ou moins `
        + `${quand}, le soir comme le matin.`
      : '',
    autres: autres.join(' '),
    lignes: lignes2,
    soir,
    matin,
    sourceBarre,
  };

  /* Le biais du survivant, avec la valeur que chaque méthode jamais
     arrêtée a gardée le plus longtemps (son rendement ces soirs-là,
     avant les frais). */
  const exemples = lignes2.filter((l) => !l.soir.arret && !l.matin.arret)
    .map((l) => ({ cle: l.m.cle, s: m2.survivant[l.m.cle] })).filter((x) => x.s);
  const survivant = `Les ${m1.valeurs} valeurs ont été choisies en 2026 : toutes ont survécu. « Ça monte toujours » et `
    + 'les repères gardés en sont flattés, et une méthode peut l\'être aussi'
    + (exemples.length ? ` : ${exemples.map(({ cle, s }) => `${esc(cle)} a gardé ${esc(s.valeur)} `
      + `${bancNum(PP.nombre(s.soirs, 0))} soir${s.soirs > 1 ? 's' : ''} (${bancNum(PP.pct(s.rendement, { decimales: 1 }))} `
      + `sur ${s.soirs > 1 ? 'ces soirs-là' : 'ce soir-là'}, avant les frais)`).join(', ')}` : '')
    + '. Le coffre, sur les mêmes valeurs, n\'en protège pas.';

  /* Le pied : quand, et ce que ce n'est pas. */
  const quandMesure = bancJourMesure(m1.date_mesure) === bancJourMesure(m2.date_mesure)
    ? `le ${bancJourMesure(m1.date_mesure)} (critère 1 à ${bancHeureMesure(m1.date_mesure)}, critère 2 à `
      + `${bancHeureMesure(m2.date_mesure)})`
    : `le ${bancJourMesure(m1.date_mesure)} (critère 1) et le ${bancJourMesure(m2.date_mesure)} (critère 2)`;
  const pied = `Deux mesures faites une seule fois, ${quandMesure}, en euros, dividendes compris. `
    + 'Des faits sur le passé, pas une prévision ni un conseil.';

  return { n, titre, resume, c1, voyait, c2, survivant, pied };
}

/* Ce que dit la section quand une mesure manque ou est abîmée. */
function bancManque(v) {
  const dire = (partie, nom, fichier) => {
    if (!partie) return `${nom} : pas de réponse du serveur.`;
    if (partie.etat === 'absent') return `${nom} : pas encore mesuré (${fichier} n'existe pas).`;
    if (partie.etat === 'illisible') return `${nom} : le fichier ${fichier} est illisible.`;
    return '';
  };
  return [dire(v?.critere1, 'Le critère 1', 'data/banc/etape1.json'),
          dire(v?.critere2, 'Le critère 2', 'data/banc/critere2.json')].filter(Boolean).join(' ');
}

function htmlBanc(f) {
  const echelle = f.c1.lignes.map((m) => `<i class="${m.passe ? 'on' : ''}"></i>`).join('');
  const chiffres1 = f.c1.lignes.map((m) => `
        <li><span class="banc-lettre">${esc(m.cle)}</span> ${esc(m.nom)} : ${bancNum(bancTaux(m.bonnes))}
          <span class="banc-fallait">(il fallait plus de ${bancNum(bancTaux(m.barre))})</span></li>`).join('');
  /* Arrêtée à 2 € : sa date, jamais son capital final. */
  const resultat = (h) => (h.arret ? `à 2&nbsp;€ le ${bancDateCourte(h.arret)}` : bancNum(PP.euros(h.final)));
  const lignesTable = f.c2.lignes.map((l) => `
            <tr>
              <th scope="row"><span class="banc-lettre">${esc(l.m.cle)}</span> ${esc(l.m.nom)}</th>
              <td class="num-cell">${bancNum(bancTaux(l.m.bonnes))}</td>
              <td class="num-cell">${bancNum(bancTaux(l.m.barre))}</td>
              <td class="num-cell">${resultat(l.soir)}</td>
              <td class="num-cell">${resultat(l.matin)}</td>
            </tr>`).join('');
  return `
    <div class="banc-tete">
      <span class="banc-echelle" aria-hidden="true">${echelle}</span>
      <p class="banc-verdict">${f.titre}</p>
    </div>
    <p class="banc-phrase">${f.resume}</p>

    <ul class="banc-criteres">
      <li class="banc-critere">
        <p class="banc-critere-tete"><span class="banc-nom">Critère 1 · voir quelque chose</span>
          <span class="badge ${f.c1.passe ? '' : 'muted'} banc-badge">${esc(f.c1.verdict)}</span></p>
        <p>${f.c1.regle}</p>
        <ul class="banc-chiffres">${chiffres1}</ul>
        <p class="banc-mesure">${f.c1.porte}${f.c1.presque ? ` ${f.c1.presque}` : ''}</p>
      </li>
      <li class="banc-critere">
        <p class="banc-critere-tete"><span class="banc-nom">Critère 2 · la stratégie de ${bancEurosRond(f.c2.capital)}</span>
          <span class="badge ${f.c2.passe ? '' : 'muted'} banc-badge">${esc(f.c2.verdict)}</span></p>
        <p>${f.c2.regle}</p>
        <p>${[f.c2.ruine, f.c2.autres].filter(Boolean).join(' ')}</p>
      </li>
    </ul>

    <p class="banc-phrase banc-limite">${f.voyait}</p>
    <p class="banc-phrase">${f.survivant}</p>

    <details class="banc-details" data-cle="detail">
      <summary>Méthode par méthode</summary>
      <div class="banc-table" tabindex="0" role="region" aria-label="Le banc, méthode par méthode">
        <table>
          <thead><tr>
            <th scope="col">Méthode</th>
            <th scope="col" class="num-cell">Bonnes réponses</th>
            <th scope="col" class="num-cell">Il fallait plus de</th>
            <th scope="col" class="num-cell">${bancEurosRond(f.c2.capital)} : le soir</th>
            <th scope="col" class="num-cell">${bancEurosRond(f.c2.capital)} : le matin</th>
          </tr></thead>
          <tbody>${lignesTable}</tbody>
        </table>
      </div>
      <p class="banc-note">${f.c1.barre} Au critère 2, il fallait plus de ${bancNum(PP.euros(f.c2.soir.barre))} le soir
        et ${bancNum(PP.euros(f.c2.matin.barre))} le matin (${f.c2.sourceBarre}). « À 2&nbsp;€ le … » : l'argent disponible
        est tombé à 2&nbsp;€ ou moins ce jour-là, et plus aucun ordre n'a suivi.</p>
    </details>

    <p class="banc-avertissement">${f.pied}</p>`;
}

/* Le dernier contenu affiché : s'il n'a pas changé (et il ne change pas :
   les deux mesures sont faites une fois pour toutes), on ne touche à rien,
   et le détail ouvert reste ouvert. */
let bancDernierHtml = '';

async function renderBanc() {
  const zone = $('#banc');
  if (!zone || !window.PP) return;
  let html;
  let v = null;
  try {
    const res = await fetch('/api/banc');
    if (res.status === 404) {
      /* Le serveur de l'appli date d'avant cette section : son Python ne
         se relit qu'en relançant l'appli (les pages, elles, se relisent seules). */
      html = '<p class="banc-attente">Le verdict du banc s\'affichera quand l\'appli sera relancée : son serveur ne '
        + 'connaît pas encore cette section.</p>';
    } else {
      const json = await res.json();
      if (!json.ok) throw new Error(json.error || 'erreur inconnue');
      v = json.data;
      const f = faitsDuBanc(v);
      html = f ? htmlBanc(f) : `<p class="banc-attente">${esc(bancManque(v))}</p>`;
    }
  } catch (e) {
    if (bancDernierHtml) return;        // la dernière lecture reste affichée
    html = `<p class="banc-attente">Le banc n'est pas lisible pour l'instant (${esc(e.message)}). Nouvel essai dans une minute.</p>`;
  }
  html = bancInsecables(html);
  const dates = v?.critere1?.etat === 'mesure' && v?.critere2?.etat === 'mesure'
    ? [bancJourMesure(v.critere1.date_mesure), bancJourMesure(v.critere2.date_mesure)] : [];
  $('#banc-date').textContent = !dates.length ? ''
    : dates[0] === dates[1] ? `mesuré le ${dates[0]}` : `mesuré le ${dates[0]} et le ${dates[1]}`;
  if (html === bancDernierHtml) return;           // rien n'a changé : on ne touche à rien

  // On garde ce que tu as ouvert, et la place du clavier.
  const ouverts = [...zone.querySelectorAll('details[data-cle]')].filter((d) => d.open).map((d) => d.dataset.cle);
  const actif = document.activeElement;
  const focusCle = actif && actif.tagName === 'SUMMARY' ? actif.closest('#banc details[data-cle]')?.dataset.cle : null;
  zone.innerHTML = html;
  bancDernierHtml = html;
  ouverts.forEach((cle) => { const d = zone.querySelector(`details[data-cle="${cle}"]`); if (d) d.open = true; });
  if (focusCle) zone.querySelector(`details[data-cle="${focusCle}"] > summary`)?.focus({ preventScroll: true });
  /* Le panneau 5 de la vue baie EST cette section : il se remesure. */
  document.dispatchEvent(new CustomEvent('stocks:dessine'));
}

/* Le détail ouvert ou refermé change la hauteur de la section : le
   panneau 5 de la vue baie se remesure (toggle ne remonte pas : on
   l'écoute à la descente). */
$('#banc')?.addEventListener('toggle', () => document.dispatchEvent(new CustomEvent('stocks:dessine')), true);

/* ------------------------------------------------------------
   LE MUR DES FRAIS PENDANT LA SÉANCE
   ------------------------------------------------------------
   De combien bouge une des 49 valeurs pendant la séance de New York,
   face au coût d'un achat suivi d'une vente (seance.py, mesuré à la
   main, relu par /api/seance). Tous les textes viennent du serveur,
   calculés depuis les comptes de la mesure : la page ne fait que les
   insérer (par esc). Vue plan seulement. Des faits sur le passé :
   jamais une prévision, jamais un conseil, jamais de rouge.
   ------------------------------------------------------------ */
function htmlSeance(t) {
  /* Le nom d'une fenêtre, et ses heures en dessous, plus discrètes. */
  const nom = (x) => `${esc(x.nom)}<span class="seance-precision">${esc(x.precision)}</span>`;
  const lignes = t.lignes.map((x) => `
            <tr>
              <th scope="row">${nom(x)}</th>
              <td class="num-cell num">${esc(x.mediane)}</td>
              <td class="num-cell num">${esc(x.parts[0])}</td>
              <td class="num-cell num">${esc(x.parts[1])}</td>
            </tr>`).join('');
  const valeurs = t.valeurs.map((v) => `
            <tr>
              <th scope="row">${esc(v.valeur)}</th>
              ${v.medianes.map((x) => `<td class="num-cell num">${esc(x)}</td>`).join('')}
            </tr>`).join('');
  /* Les légendes sont HORS des cadres qui défilent (au téléphone, une
     <caption> prend la largeur du tableau et s'y coupe : piège connu). */
  return `
    <p class="seance-phrase">${esc(t.intro)}</p>
    <p class="seance-legende" id="seance-legende">${esc(t.legende)}</p>
    <div class="seance-table" tabindex="0" role="region" aria-labelledby="seance-legende" data-cle="fenetres">
      <table>
        <thead><tr>${t.colonnes.map((c, i) => `<th scope="col"${i ? ' class="num-cell"' : ''}>${esc(c)}</th>`).join('')}</tr></thead>
        <tbody>${lignes}
        </tbody>
      </table>
    </div>
    <p class="seance-phrase">${esc(t.conclusion)}</p>
    <details class="seance-details" data-cle="valeurs">
      <summary>${esc(t.detail)}</summary>
      <div class="seance-table" tabindex="0" role="region" aria-label="${esc(t.detail)}" data-cle="valeurs">
        <table>
          <thead><tr><th scope="col">${esc(t.entete_valeur)}</th>${t.colonnes_valeurs.map((c) => `
            <th scope="col" class="num-cell">${nom(c)}</th>`).join('')}</tr></thead>
          <tbody>${valeurs}
          </tbody>
        </table>
      </div>
      <p class="seance-note">${esc(t.legende_valeurs)}</p>
    </details>
    <ul class="seance-notes">${t.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>`;
}

/* Le dernier contenu affiché : s'il n'a pas changé (la mesure ne change
   qu'à la main, la page se rafraîchit chaque minute), on ne touche à rien.
   `seanceLue` : une vraie mesure est affichée (pas un message). */
let seanceDernierHtml = '';
let seanceLue = false;

async function renderSeance() {
  const zone = $('#seance');
  const etat = $('#seance-etat');
  if (!zone) return;
  let html;
  let s = null;
  let ancien = false;        // un serveur pas encore relancé (404)
  try {
    let res;
    /* Un serveur muet : fetch lève une erreur du navigateur, en anglais
       (« Load failed », « Failed to fetch ») ; la page le dit en français. */
    try { res = await fetch('/api/seance'); } catch { throw new Error('le serveur de l\'appli ne répond pas'); }
    if (res.status === 404) {
      /* Le serveur de l'appli date d'avant cette section : son Python ne
         se relit qu'en relançant l'appli (les pages, elles, se relisent seules). */
      ancien = true;
      html = '<p class="seance-attente">Le mur des frais pendant la séance s\'affichera quand l\'appli sera '
        + 'relancée&nbsp;: son serveur ne connaît pas encore cette section.</p>';
    } else {
      let json;
      try { json = await res.json(); } catch { throw new Error(`réponse illisible du serveur (${res.status})`); }
      if (!json.ok) throw new Error(json.error || 'erreur inconnue');
      if (json.data?.etat === 'mesure') {
        /* Une réponse d'une autre forme (un serveur qui garde en mémoire une
           ancienne version de seance.py) : un message en français, jamais une
           erreur de JavaScript en anglais, ni une section bloquée. */
        try { html = htmlSeance(json.data.textes); } catch {
          throw new Error('la réponse du serveur n\'a pas la forme attendue : relance l\'appli');
        }
        s = json.data;
      } else {
        /* Une mesure absente ou illisible : le message vient du serveur. */
        html = `<p class="seance-attente">${esc(json.data?.message || 'La mesure n\'est pas lisible pour l\'instant.')}</p>`;
      }
    }
  } catch (e) {
    /* Une panne du serveur n'efface pas la dernière mesure affichée. */
    if (seanceLue) {
      if (etat) etat.textContent = `Mise à jour impossible pour l'instant (${e.message}) : la mesure affichée est la dernière connue.`;
      return;
    }
    html = `<p class="seance-attente">Le mur des frais pendant la séance est indisponible pour l'instant (${esc(e.message)}). `
      + 'Nouvel essai dans une minute.</p>';
  }
  if (etat) etat.textContent = '';
  if (ancien && seanceLue) return;                   // un 404 après une vraie lecture : on la garde
  $('#seance-date').textContent = (s && typeof s.textes.pastille === 'string') ? s.textes.pastille : '';
  if (html === seanceDernierHtml) return;            // rien n'a changé : on ne touche à rien

  // On garde ce que tu as ouvert, la place du clavier et le défilement des tableaux.
  const ouverts = [...zone.querySelectorAll('details[data-cle]')].filter((d) => d.open).map((d) => d.dataset.cle);
  const defile = new Map([...zone.querySelectorAll('.seance-table[data-cle]')].map((t) => [t.dataset.cle, t.scrollLeft]));
  const actif = document.activeElement;
  let focus = null;
  if (actif && zone.contains(actif)) {
    if (actif.tagName === 'SUMMARY') focus = `details[data-cle="${actif.closest('details[data-cle]')?.dataset.cle}"] > summary`;
    else if (actif.classList.contains('seance-table')) focus = `.seance-table[data-cle="${actif.dataset.cle}"]`;
  }
  zone.innerHTML = html;
  seanceDernierHtml = html;
  seanceLue = Boolean(s);
  ouverts.forEach((cle) => { const d = zone.querySelector(`details[data-cle="${cle}"]`); if (d) d.open = true; });
  defile.forEach((x, cle) => { const t = zone.querySelector(`.seance-table[data-cle="${cle}"]`); if (t) t.scrollLeft = x; });
  if (focus) zone.querySelector(focus)?.focus({ preventScroll: true });
}

/* ------------------------------------------------------------
   CYCLE DE VIE
   ------------------------------------------------------------ */

function refresh() {
  renderStatus();
  renderQuality();
  renderSemaine();
  /* Le creux part APRÈS la réponse du thermomètre, pas en même temps : au
     lancement, le scan des 49 valeurs et les 14 demandes du thermomètre
     partent déjà ensemble chez Yahoo. */
  renderThermometre().finally(renderCreux);
  renderStocks();
  /* La position (et les cases en direct du tableau) ont leur propre tour, plus bas :
     toutes les 2 s pendant le direct, 60 s sinon. */
  renderHistory();
  renderBanc();
  renderSeance();      // un fichier relu par le serveur : rien chez Yahoo
}

refresh();

if (window.PP) {
  const surveillanceDirect = PP.surveiller(tourDirect, { vite: planRapide });
  document.addEventListener('carnet:ferme', () => surveillanceDirect.maintenant());
  document.addEventListener('direct:pause', () => { majBadgeDirect(); majLigneDirect(); majNoteDirect(); });
}

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

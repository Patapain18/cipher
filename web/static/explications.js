/* ============================================================
   explications.js
   ============================================================
   Cette page avance des chiffres pour appuyer — ou contredire — une
   décision d'achat. Ils sont donc TOUS lus depuis les rapports de
   mesure, jamais écrits en dur dans le HTML. Une valeur recopiée à la
   main cesse d'être vraie au premier réexamen, sans que personne ne
   s'en aperçoive : ce serait exactement le genre de « preuve » que
   cette page est censée combattre.
   ============================================================ */

const $ = (sel) => document.querySelector(sel);

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const nf = (v, d = 2) =>
  v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : Number(v).toLocaleString('fr-FR', { minimumFractionDigits: d, maximumFractionDigits: d });

const signed = (v, d = 2, unit = ' %') =>
  v === null || v === undefined || Number.isNaN(v)
    ? '—' : (v >= 0 ? '+' : '') + nf(v, d) + unit;

const dt = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
};

async function api(path) {
  const res = await fetch(path);
  const json = await res.json();
  if (!json.ok) throw new Error(json.error || 'erreur');
  return json.data;
}

/* ------------------------------------------------------------
   LE VERDICT, EN HAUT DE PAGE
   ------------------------------------------------------------ */

async function renderVerdict() {
  let q;
  try {
    q = await api('/api/stocks/quality');
  } catch (e) {
    $('#verdict').innerHTML = `<p class="error">${esc(e.message)}</p>`;
    return null;
  }

  if (!q) {
    $('#verdict').innerHTML = `
      <h2>Ce signal n'a pas encore été évalué</h2>
      <p>Tant qu'aucune mesure n'a été faite, rien ne permet de dire ce que
        vaut une notification d'achat. Pour le savoir :</p>
      <p><code>python3 stock_eval.py --period 10y --horizon 10 --save</code></p>`;
    return null;
  }

  const ic = q.v2.ic ?? 0;
  const ecart = q.v2.edge?.ecart ?? 0;
  const p = q.parametres;
  const concluant = ic > 0.015 && ecart > 0.1;

  /* Le ton suit la mesure. Si un jour l'écart devient franchement
     positif, cette même page dira que le signal a fait ses preuves —
     sans qu'on ait à la réécrire. */
  $('#verdict').innerHTML = concluant ? `
    <span class="verdict-tag ok">Signal validé</span>
    <h2>Sur la période mesurée, suivre ce signal aurait rapporté davantage
      que ne rien faire.</h2>
    <p>Écart de <strong>${signed(ecart, 2, ' point')}</strong> par rapport à
      une séance quelconque, pour un Information Coefficient de
      <strong>${signed(ic, 4, '')}</strong>.</p>
  ` : `
    <span class="verdict-tag warn">À lire avant d'acheter</span>
    <h2>Non : les mesures ne justifient pas d'acheter sur la seule foi
      d'une notification.</h2>
    <p>
      Sur <strong>${Number(q.n_seances).toLocaleString('fr-FR')} séances</strong>
      (${esc(p.periode)} d'historique, ${p.valeurs.length} valeurs), les
      moments où ce score atteignait ${p.seuil}/5 ont rapporté
      <strong>${signed(q.v2.edge?.ecart, 2, ' point')}</strong> de plus
      qu'une séance prise au hasard. Autrement dit : rien.
    </p>
    <p>
      Son Information Coefficient — la corrélation entre le score et ce qui
      s'est réellement passé ensuite — vaut
      <strong>${signed(ic, 4, '')}</strong>. Une valeur nulle signifie que le
      score n'ordonne rien ; une valeur négative, qu'il pointe légèrement à
      l'envers.
    </p>
    <p class="hint">
      Ce n'est pas une raison de jeter l'outil : le score décrit fidèlement
      l'état technique d'une valeur, et le classement par momentum (section 4)
      a, lui, fait ses preuves. C'est une raison de ne pas confondre
      « configuration favorable » et « ça va monter ».
    </p>`;

  return q;
}

/* ------------------------------------------------------------
   LES PREUVES : rendement par niveau de score
   ------------------------------------------------------------ */

function renderPreuves(q) {
  if (!q) {
    $('#preuves').innerHTML = '<p class="empty">Mesures indisponibles.</p>';
    return;
  }

  const p = q.parametres;
  const perScore = q.v2.per_score || {};
  const ref = q.v2.reference;
  const scores = Object.keys(perScore).map(Number).sort((a, b) => a - b);

  /* La colonne « rendement moyen » est la preuve la plus parlante : si le
     score valait quelque chose, elle monterait avec lui. On la montre
     telle quelle, sans commentaire flatteur. */
  const rows = scores.map((s) => {
    const d = perScore[s];
    const better = d.moyenne > ref;
    return `<tr${s >= p.seuil ? ' class="held"' : ''}>
      <td class="num"><strong>${s}/5</strong>${s >= p.seuil
        ? ' <span class="rank-total">déclenche</span>' : ''}</td>
      <td class="num-cell num">${Number(d.n).toLocaleString('fr-FR')}</td>
      <td class="num-cell num ${better ? 'up' : 'down'}">${signed(d.moyenne)}</td>
      <td class="num-cell num">${nf(d.taux_hausse, 0)} %</td>
    </tr>`;
  }).join('');

  $('#preuves').innerHTML = `
    <p>
      Pour chaque séance passée, on calcule le score que le bot
      <em>aurait</em> donné ce jour-là — avec les seules données
      disponibles à cette date — puis on regarde ce que la valeur a fait
      pendant les <strong>${p.horizon} séances suivantes</strong>.
    </p>
    <p>
      Si le score annonçait quelque chose, la colonne « rendement moyen »
      devrait monter régulièrement du haut vers le bas.
    </p>

    <div class="table-wrap" style="box-shadow:none">
      <table>
        <thead><tr>
          <th>Score</th>
          <th class="num-cell">Séances</th>
          <th class="num-cell">Rendement moyen à ${p.horizon} j</th>
          <th class="num-cell">% en hausse</th>
        </tr></thead>
        <tbody>
          ${rows}
          <tr class="ref-row">
            <td><strong>Ne rien faire</strong></td>
            <td class="num-cell num">${Number(q.n_seances).toLocaleString('fr-FR')}</td>
            <td class="num-cell num">${signed(ref)}</td>
            <td class="num-cell num">—</td>
          </tr>
        </tbody>
      </table>
    </div>

    <p class="hint">
      Mesuré le ${dt(q.genere_le)} sur ${esc(p.periode)} d'historique et
      ${p.valeurs.length} valeurs. Les séances qui se suivent se ressemblent :
      ces chiffres sont des ordres de grandeur sur une période donnée, pas
      des promesses.
    </p>`;
}

/* ------------------------------------------------------------
   CE QUI MARCHE : le classement par momentum
   ------------------------------------------------------------ */

/* Chaque avantage est affirmé seulement s'il est vérifié dans les
   chiffres. Écrire la phrase à l'avance et y injecter des valeurs, c'est
   s'exposer à annoncer qu'un drawdown de −26,3 % « bat » un −24,4 % —
   l'erreur exacte que cette page existe pour éviter. */
function avantages(bt) {
  const gagne = [], perd = [];

  (bt.perf > bt.perf_benchmark ? gagne : perd).push(
    `en performance (${signed(bt.perf, 0)} contre ${signed(bt.perf_benchmark, 0)})`);
  (bt.sharpe >= bt.sharpe_benchmark ? gagne : perd).push(
    `en rapport rendement/risque (${nf(bt.sharpe, 2)} contre ${nf(bt.sharpe_benchmark, 2)})`);
  // Un drawdown est négatif : le « meilleur » est le moins profond.
  (bt.drawdown > bt.drawdown_benchmark ? gagne : perd).push(
    `en perte maximale (${nf(bt.drawdown, 1)} % contre ${nf(bt.drawdown_benchmark, 1)} %)`);

  const liste = (arr) => arr.length === 1 ? arr[0]
    : arr.slice(0, -1).join(', ') + ' et ' + arr[arr.length - 1];

  let texte = '';
  if (gagne.length === 3) {
    texte = `Il bat le panier complet sur les trois plans à la fois : ${liste(gagne)}.`;
  } else if (gagne.length) {
    texte = `Il bat le panier complet ${liste(gagne)}`;
    texte += perd.length ? `, mais reste derrière ${liste(perd)}.` : '.';
  } else {
    texte = `Il ne bat toutefois pas le panier complet : il reste derrière ${liste(perd)}.`;
  }
  return `${texte} Le tout avec ${nf(bt.rotation, 0)} % de rotation seulement.`;
}

async function renderRanking() {
  let r;
  try {
    r = await api('/api/ranking');
  } catch (e) {
    $('#ranking').innerHTML = `<p class="error">${esc(e.message)}</p>`;
    return;
  }

  if (!r) {
    $('#ranking').innerHTML = `
      <p class="empty">Le classement n'a pas encore été évalué.</p>
      <p class="hint"><code>python3 stock_eval.py --ranking --period 10y --horizon 10 --save</code></p>`;
    return;
  }

  const p = r.parametres;
  const criteres = Object.entries(r.criteres);

  /* On classe les critères par percentile décroissant : le meilleur
     apparaît en tête, et le score du moteur tombe à sa vraie place. */
  const lignes = criteres.map(([key, c]) => {
    const bts = Object.entries(c.backtests);
    if (!bts.length) return null;

    /* On retient la taille de sélection au meilleur SHARPE, pas au
       meilleur percentile. Le percentile dit seulement « mieux que le
       hasard » ; il monte avec la concentration, qui gonfle autant les
       gains que les pertes. Retenir le top 5 sur ce seul critère
       reviendrait à présenter comme exemplaire un portefeuille au
       rendement spectaculaire mais au rapport rendement/risque
       inférieur à celui du panier. */
    const best = bts.reduce((a, b) =>
      (b[1].sharpe ?? -9) > (a[1].sharpe ?? -9) ? b : a);
    const [taille, bt] = best;
    const pc = bt.percentile ?? 0;
    const verdict = pc >= 90 ? ['ok', 'au-dessus du hasard']
      : pc >= 60 ? ['warn', 'indistinct du hasard']
      : ['bad', 'sous le hasard'];
    return {
      key, label: c.label, ic: c.ic?.ic_moyen ?? null,
      taille, bt, pc, verdict,
      bench: pc >= 90 && bt.sharpe >= bt.sharpe_benchmark,
    };
  }).filter(Boolean).sort((a, b) => b.pc - a.pc);

  const champion = lignes[0];

  $('#ranking').innerHTML = `
    <p>
      Le score technique n'est pas la seule façon de choisir. On a mis en
      concurrence plusieurs critères de classement sur les mêmes
      ${Number(p.seances).toLocaleString('fr-FR')} séances
      (${p.valeurs} valeurs, ${esc(p.du)} → ${esc(p.au)}), en les comparant
      à <strong>300 portefeuilles tirés au sort</strong>.
    </p>
    <p class="hint">
      Ce test au hasard est indispensable : sur un univers où une valeur a
      été multipliée par dix, n'importe quelle sélection concentrée a de
      bonnes chances de battre la moyenne. Une belle performance ne prouve
      rien tant qu'on ne l'a pas comparée à ce que la chance produit.
    </p>

    <div class="table-wrap" style="box-shadow:none;margin-top:var(--space-4)">
      <table>
        <thead><tr>
          <th>Critère de classement</th>
          <th class="num-cell">IC</th>
          <th class="num-cell">Performance</th>
          <th class="num-cell">Sharpe</th>
          <th class="num-cell">Percentile</th>
          <th>Verdict</th>
        </tr></thead>
        <tbody>
          ${lignes.map((l) => `
            <tr>
              <td><strong>${esc(l.label)}</strong>
                <span class="rank-total">meilleur en top ${esc(l.taille)}</span></td>
              <td class="num-cell num">${signed(l.ic, 4, '')}</td>
              <td class="num-cell num">${signed(l.bt.perf, 0)}</td>
              <td class="num-cell num ${l.bt.sharpe >= l.bt.sharpe_benchmark ? 'up' : ''}">${nf(l.bt.sharpe, 2)}</td>
              <td class="num-cell num">${nf(l.pc, 0)}ᵉ</td>
              <td><span class="reco reco-${l.verdict[0] === 'ok' ? 'BUY'
                : l.verdict[0] === 'warn' ? 'HOLD' : 'SELL'}">${esc(l.verdict[1])}</span></td>
            </tr>`).join('')}
          <tr class="ref-row">
            <td><strong>Acheter les ${p.valeurs}, à parts égales</strong></td>
            <td class="num-cell">—</td>
            <td class="num-cell num">${signed(champion.bt.perf_benchmark, 0)}</td>
            <td class="num-cell num">${nf(champion.bt.sharpe_benchmark, 2)}</td>
            <td class="num-cell">—</td>
            <td><span class="chip">référence</span></td>
          </tr>
        </tbody>
      </table>
    </div>

    <p style="margin-top:var(--space-4)">
      <strong>${esc(champion.label)}</strong> — le rendement des douze
      derniers mois en excluant le dernier — arrive au
      <strong>${nf(champion.pc, 0)}ᵉ percentile</strong> du hasard, en
      sélectionnant les ${esc(champion.taille)} mieux classées.
      ${avantages(champion.bt)}
    </p>
    <p>
      C'est pourquoi le tableau de bord classe les valeurs par momentum et
      non par score technique : il aurait été absurde de les ordonner avec
      une mesure dont on venait de montrer qu'elle n'ordonne rien.
    </p>
    <p class="hint">
      Mesuré le ${dt(r.genere_le)}. Un backtest reste une reconstitution :
      il suppose que tout s'exécute au cours de clôture et ne teste qu'une
      période. Les frais sont comptés (0,05 % par transaction), pas la
      fiscalité.
    </p>`;
}

(async () => {
  const q = await renderVerdict();
  renderPreuves(q);
  renderRanking();
})();

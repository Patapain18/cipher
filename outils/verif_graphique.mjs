// Le graphique du carnet avec Lightweight Charts (plan du 24/11/2026, fin de BRIEF.md) : à lancer avec
// le serveur d'essai `signaux-essai` (port 8779, copie du journal ; voir CLAUDE.md « Vérifier »).
// Usage : node outils/verif_graphique.mjs <dossier de sortie hors du projet> [--avant]
//   --avant : le nouveau graphique demande encore ?graphe=lc (avant la bascule) ; sans lui, c'est
//             l'état d'après la bascule (le nouveau par défaut, ?graphe=svg pour l'ancien).
// 1. Chrome, WebKit et Firefox, à 1440, 1280, 390, 360 et 320 px et au téléphone à l'horizontale,
//    pour 1 jour, 1 mois et 1 an : chaque flèche comparée à un recalcul à part (le journal et la
//    courbe que la page a reçus, la règle du plan réécrite ici), chaque ligne et son étiquette
//    comparées à /api/graphique et posées dans le cadre, la phrase, la ligne du curseur, le crédit,
//    le balisage (groupe pris au clavier, canvas caché), rien qui déborde, aucune requête hors de
//    127.0.0.1, aucune erreur de console ; axe à 1280 et 390.
// 2. À 1440 : le clavier (Fin, ←, +, 0, Échap en deux temps), la molette (page sans clic, zoom après
//    un clic), la période recliquée, et la valeur offerte (une seule ligne, « Prix du jour du cadeau »).
// 3. Des pannes (Chrome) : /api/graphique en erreur, une période changée pendant une lecture lente,
//    la bibliothèque absente (l'ancien graphique reprend la place), et l'ancien graphique demandé.
// 4. Les calculs de graphe.js sur des cours et des ordres inventés (valeurs écrites à la main) :
//    les ordres hors séance, d'avant la période, après la dernière barre, le week-end ; l'heure de
//    Paris autour des changements d'heure ; les barres en double ou dans le désordre ; la fin de séance.
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(`${homedir()}/.claude/skills/verif/outil/`);
const { chromium, webkit, firefox } = require('playwright');
const { AxeBuilder } = require('@axe-core/playwright');

const sortie = process.argv[2];
if (!sortie || sortie.startsWith('--')) {
  console.error('Usage : node outils/verif_graphique.mjs <dossier de sortie> [--avant]');
  process.exit(2);
}
mkdirSync(sortie, { recursive: true });
const AVANT = process.argv.includes('--avant');
const BASE = 'http://localhost:8779';
const URL_LC = `${BASE}/?plan&vue=carnet${AVANT ? '&graphe=lc' : ''}`;
const URL_SVG = `${BASE}/?plan&vue=carnet${AVANT ? '' : '&graphe=svg'}`;
const TEL = { deviceScaleFactor: 2, isMobile: true, hasTouch: true };
const TAILLES = {
  ordi: { viewport: { width: 1440, height: 900 } },
  'ordi-petit': { viewport: { width: 1280, height: 800 } },
  tel: { viewport: { width: 390, height: 844 }, ...TEL },
  'tel-petit': { viewport: { width: 360, height: 780 }, ...TEL },
  etroit: { viewport: { width: 320, height: 700 }, ...TEL },
  'tel-horizontal': { viewport: { width: 844, height: 390 }, ...TEL },
};
const PERIODES = { '1j': '1 jour', '1m': '1 mois', '1a': '1 an' };
const DUREE = { '1j': 300, '1m': 3600 };

const lignes = [];
const ok = (cond, quoi) => { lignes.push(`${cond ? 'ok ' : 'NON'} ${quoi}`); return cond; };
const note = (quoi) => lignes.push(`note ${quoi}`);          // un cas sauté, dit : pas un échec
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

/* ── Le recalcul à part : la règle du plan, réécrite sans le code de graphe.js ── */
const dateNY = (ms) => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric',
  month: '2-digit', day: '2-digit' }).format(new Date(ms));
function heureNY(ms) {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(ms)).map((x) => [x.type, x.value]));
  return { semaine: p.weekday, minutes: Number(p.hour) * 60 + Number(p.minute) };
}
function attendus(g, decisions) {
  const parTemps = new Map();
  for (const p of g.points) {
    const ms = Date.parse(p.t);
    if (Number.isFinite(ms) && Number.isFinite(p.eur) && p.eur > 0) parTemps.set(Math.floor(ms / 1000), p.eur);
  }
  const barres = [...parTemps.keys()].sort((a, b) => a - b);
  const cle = (s) => (g.periode === '1a' ? dateNY(s * 1000) : s);
  const premier = dateNY(barres[0] * 1000);
  const joursAvecBarre = new Set(barres.map((b) => dateNY(b * 1000)));
  const dernierJour = dateNY(barres[barres.length - 1] * 1000);
  const sortieOrdres = [];
  let hors = 0;
  // Les séances que Yahoo a données au serveur : celle d'un jour décide (une séance courte finit à 13 h).
  const seancesYahoo = (Array.isArray(g.seances) ? g.seances : [])
    .map((x) => ({ d: Date.parse(x?.debut), f: Date.parse(x?.fin) }))
    .filter((x) => Number.isFinite(x.d) && Number.isFinite(x.f) && x.d < x.f);
  for (const d of decisions) {
    if (d.ticker !== g.ticker || !(Number(d.prix_eur) > 0)) continue;
    const ms = Date.parse(d.date_utc || d.date);
    if (!Number.isFinite(ms) || dateNY(ms) < premier) continue;
    const { semaine, minutes } = heureNY(ms);
    const sj = seancesYahoo.find((x) => dateNY(x.d) === dateNY(ms));
    const seance = sj ? ms >= sj.d && ms < sj.f : !['Sat', 'Sun'].includes(semaine) && minutes >= 570 && minutes < 960;
    // Hors séance : hors des heures de New York, ou un jour sans aucune barre (un jour férié).
    const horsSeance = !seance || (dateNY(ms) <= dernierJour && !joursAvecBarre.has(dateNY(ms)));
    if (horsSeance) hors += 1;
    let barre;
    if (g.periode === '1a') {
      const jour = dateNY(ms);
      const apres = !seance && (sj ? ms >= sj.f : minutes >= 960);
      barre = barres.find((b) => (apres ? dateNY(b * 1000) > jour : dateNY(b * 1000) >= jour));
    } else {
      const q = Math.floor(ms / 1000);
      barre = (!horsSeance ? barres.find((b) => b <= q && q < b + DUREE[g.periode]) : undefined) ?? barres.find((b) => b > q);
    }
    barre ??= barres[barres.length - 1];
    sortieOrdres.push(`${cle(barre)}|${Number(d.prix_eur)}|${d.sens === 'achat' ? 'A' : 'V'}`);
  }
  return { ordres: sortieOrdres.sort(), hors };
}

/* ── La page ── */
async function ouvrir(navigateur, config, url, { routes } = {}) {
  const contexte = await navigateur.newContext(config);
  const page = await contexte.newPage();
  const etat = { erreurs: [], externes: [], graphiques: new Map(), journal: null };
  page.on('console', (m) => { if (m.type() === 'error') etat.erreurs.push(m.text()); });
  page.on('pageerror', (e) => etat.erreurs.push(e.message));
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (!['localhost', '127.0.0.1'].includes(u.hostname) && !['data:', 'blob:'].includes(u.protocol)) etat.externes.push(r.url());
  });
  page.on('response', async (r) => {
    try {
      const u = new URL(r.url());
      if (!['/api/graphique', '/api/journal'].includes(u.pathname) || !r.ok()) return;
      const j = await r.json();
      const corps = j && typeof j === 'object' && 'data' in j ? j.data : j;      // { ok, data, cached_at }
      if (u.pathname === '/api/graphique') etat.graphiques.set(`${u.searchParams.get('ticker')}|${u.searchParams.get('periode')}`, corps);
      else etat.journal = corps;
    } catch { /* une réponse coupée par la fermeture */ }
  });
  if (routes) await routes(page);
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  return { contexte, page, etat };
}
const pret = (page, periode, ms = 40000) => page.waitForFunction((p) => window.PCGraphe
  && PCGraphe.essai().periode === p && PCGraphe.essai().barres.length > 1
  && document.getElementById('pc-legende')?.textContent.includes('sur'), periode, { timeout: ms }).then(() => true, () => false);

async function choisirPeriode(page, p) {
  await page.evaluate((p) => document.querySelector(`[data-periode="${p}"]`).click(), p);
  const fait = await pret(page, p);
  await page.waitForTimeout(500);
  return fait;
}

/* Une période : flèches, lignes, étiquettes, phrase, curseur, balisage, débordement. */
async function controlerPeriode(page, etat, nom, p) {
  const vu = await page.evaluate(() => {
    const e = PCGraphe.essai();
    const cadre = document.getElementById('pc-graphe');
    const zone = cadre.querySelector('.pc-lc');
    const h = zone.clientHeight - e.chart.timeScale().height();
    const rc = cadre.getBoundingClientRect();
    const etiquettes = [...cadre.querySelectorAll('.pc-repere-texte')].map((s) => {
      const r = s.getBoundingClientRect();
      return { texte: s.textContent, cachee: s.hidden, dedans: r.left >= rc.left - 1 && r.right <= rc.right + 1
        && r.top >= rc.top - 1 && r.bottom <= rc.bottom + 1 };
    });
    const jourNY = (s) => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric',
      month: '2-digit', day: '2-digit' }).format(new Date(s * 1000));
    return {
      ticker: (document.getElementById('pc-figure-titre').textContent.match(/de (\S+), en euros/) || [])[1],
      ordres: e.ordres.map((o) => `${e.periode === '1a' ? jourNY(o.time) : o.time}|${o.prix}|${o.achat ? 'A' : 'V'}`).sort(),
      ordresDedans: e.ordres.every((o) => { const y = e.serie.priceToCoordinate(o.prix); return y !== null && y >= 0 && y <= h; }),
      reperes: e.reperes,
      reperesDedans: e.reperes.every((r) => { const y = e.serie.priceToCoordinate(r.prix); return y !== null && y >= 0 && y <= h; }),
      etiquettes,
      legende: document.getElementById('pc-legende').textContent,
      curseur: document.getElementById('pc-curseur').textContent,
      derniere: e.barres[e.barres.length - 1].value,
      credit: (() => { const a = document.querySelector('.pc-credit a'); return a && { href: a.href, rel: a.rel, texte: a.closest('p').textContent }; })(),
      balisage: { role: cadre.getAttribute('role'), tab: cadre.tabIndex, decrit: cadre.getAttribute('aria-describedby'),
                  cache: cadre.getAttribute('aria-hidden'), zoneCachee: zone.getAttribute('aria-hidden'),
                  nom: !!cadre.getAttribute('aria-label') },
      deborde: document.documentElement.scrollWidth > innerWidth + 1,
      figureDedans: document.getElementById('pc-figure').getBoundingClientRect().right <= innerWidth + 1,
    };
  });
  const g = etat.graphiques.get(`${vu.ticker}|${p}`);
  if (!ok(!!g && !!etat.journal, `${nom} ${p} : la courbe et le journal reçus par la page`)) return;
  const a = attendus(g, etat.journal.decisions || []);
  ok(JSON.stringify(vu.ordres) === JSON.stringify(a.ordres),
    `${nom} ${p} : les ${a.ordres.length} flèches sont celles du recalcul (barre, prix, sens)${JSON.stringify(vu.ordres) === JSON.stringify(a.ordres) ? '' : ` — vu ${JSON.stringify(vu.ordres)}, attendu ${JSON.stringify(a.ordres)}`}`);
  ok(vu.ordresDedans && vu.reperesDedans, `${nom} ${p} : chaque flèche et chaque ligne dans le cadre`);
  const achat = g.achat;
  const reperesAttendus = !achat ? [] : achat.offerte ? [achat.eur] : [achat.seuil_eur, achat.eur];
  ok(vu.reperes.length === reperesAttendus.length && vu.reperes.every((r, i) => Math.abs(r.prix - reperesAttendus[i]) < 1e-9),
    `${nom} ${p} : les lignes valent celles de /api/graphique`);
  const textes = await page.evaluate((ps) => ps.map((v) => PP.euros(v)), reperesAttendus);
  const motsAttendus = !achat ? [] : achat.offerte ? [`Prix du jour du cadeau : ${textes[0]}`]
    : [`Frais remboursés : ${textes[0]}`, `Ton prix d'achat : ${textes[1]}`];
  ok(JSON.stringify(vu.etiquettes.map((e) => e.texte)) === JSON.stringify(motsAttendus), `${nom} ${p} : les étiquettes des lignes`);
  ok(vu.etiquettes.every((e) => !e.cachee && e.dedans), `${nom} ${p} : les étiquettes visibles, dans le cadre`);
  const n = a.ordres.length;
  const phraseOrdres = n ? `${n} ordre${n > 1 ? 's' : ''} sur la courbe` : null;
  ok(vu.legende.includes(`sur ${PERIODES[p]}`) && (!phraseOrdres || vu.legende.includes(phraseOrdres))
     && (a.hors ? vu.legende.includes(`dont ${a.hors} passé`) : !vu.legende.includes('hors séance')),
    `${nom} ${p} : la phrase (« sur ${PERIODES[p]} », ${n} ordre(s), ${a.hors} hors séance)`);
  const derniere = await page.evaluate((v) => PP.euros(v), vu.derniere);
  ok(vu.curseur.startsWith('Fin de la courbe : ') && vu.curseur.endsWith(derniere), `${nom} ${p} : la ligne du curseur (${vu.curseur})`);
  ok(vu.credit && vu.credit.href === 'https://www.tradingview.com/' && /noopener/.test(vu.credit.rel) && /noreferrer/.test(vu.credit.rel)
     && vu.credit.texte.includes('Lightweight Charts™') && vu.credit.texte.includes('TradingView, Inc.'), `${nom} ${p} : le crédit et son lien`);
  ok(vu.balisage.role === 'group' && vu.balisage.tab === 0 && vu.balisage.decrit === 'pc-legende' && vu.balisage.cache === null
     && vu.balisage.zoneCachee === 'true' && vu.balisage.nom, `${nom} ${p} : un groupe pris au clavier, le canvas caché aux lecteurs d'écran`);
  ok(!vu.deborde && vu.figureDedans, `${nom} ${p} : rien ne déborde`);
}

/* Ce qui se DESSINE (à 1440) : les pixels des flèches, des lignes et de la courbe sur l'image du
   graphique (takeScreenshot), la place et le côté des étiquettes, les formats de l'heure et des prix,
   les réglages de sobriété. La relecture du 25/11/2026 : 25 erreurs volontaires sur 37 passaient,
   le test ne lisait que l'état de graphe.js. */
async function controlerDessin(page, etat, nom, p) {
  const ticker = await page.evaluate(() => (document.getElementById('pc-figure-titre').textContent.match(/de (\S+), en euros/) || [])[1]);
  const g = etat.graphiques.get(`${ticker}|${p}`);
  if (!ok(!!g, `${nom} ${p} : la courbe reçue, pour le dessin`)) return;
  const heureParis = new Intl.DateTimeFormat('fr-FR', { timeZone: 'Europe/Paris', hour: '2-digit', minute: '2-digit' })
    .format(new Date(Date.parse(g.points[0].t)));
  const r = await page.evaluate(({ p, heureParis }) => {
    const e = PCGraphe.essai();
    const zone = document.querySelector('#pc-graphe .pc-lc');
    const toile = e.chart.takeScreenshot();
    const k = toile.width / zone.clientWidth;
    const px = toile.getContext('2d').getImageData(0, 0, toile.width, toile.height).data;
    const css = getComputedStyle(document.documentElement);
    const rgb = (n) => { const h = css.getPropertyValue(n).trim(); return [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)); };
    const [accent, neutre, encre] = [rgb('--accent'), rgb('--neutral'), rgb('--text')];
    const gauche = e.chart.priceScale('left').width();
    const largeur = zone.clientWidth - gauche;
    const hauteur = zone.clientHeight - e.chart.timeScale().height();
    const lignesY = e.reperes.map((rp) => e.serie.priceToCoordinate(rp.prix));
    /* Un pixel de cette couleur dans le rectangle (pixels CSS du graphique), en sautant les lignes. */
    const trouve = (x0, x1, y0, y1, c, horsLignes = false) => {
      for (let y = Math.max(0, Math.floor(y0)); y <= Math.min(hauteur, Math.ceil(y1)); y++) {
        if (horsLignes && lignesY.some((ly) => Math.abs(ly - y) <= 2)) continue;
        for (let x = Math.max(0, Math.floor(x0)); x <= Math.ceil(x1); x++) {
          const i = (Math.round(y * k) * toile.width + Math.round(x * k)) * 4;
          if (px[i + 3] > 100 && Math.hypot(px[i] - c[0], px[i + 1] - c[1], px[i + 2] - c[2]) < 60) return true;
        }
      }
      return false;
    };
    const ts = e.chart.timeScale();
    const fleches = e.ordres.map((o) => {
      const x = gauche + ts.timeToCoordinate(o.time);
      const y = e.serie.priceToCoordinate(o.prix);
      return o.achat ? trouve(x - 3, x + 3, y + 2, y + 16, accent, true) : trouve(x - 3, x + 3, y - 16, y - 2, neutre, true);
    });
    const lignes = e.reperes.map((rp) => {
      const y = e.serie.priceToCoordinate(rp.prix);
      return [0.15, 0.35, 0.55, 0.75, 0.95].filter((f) => trouve(gauche + f * largeur - 6, gauche + f * largeur + 6, y - 1, y + 1, accent)).length >= 3;
    });
    const pas = Math.max(1, Math.floor(e.barres.length / 10));
    const echantillon = e.barres.filter((_, i) => i % pas === 0);
    const courbe = echantillon.filter((b) => {
      const x = gauche + ts.timeToCoordinate(b.time);
      const y = e.serie.priceToCoordinate(b.value);
      return trouve(x - 3, x + 3, y - 3, y + 3, encre);
    }).length / echantillon.length;
    /* Les étiquettes : à 2 px de leur ligne (au-dessus ou en dessous), du côté où le cours n'est pas. */
    const rz = zone.getBoundingClientRect();
    const etiquettes = [...document.querySelectorAll('#pc-graphe .pc-repere-texte')].map((s, i) => {
      const rs = s.getBoundingClientRect();
      const y = rz.top + lignesY[i];
      const place = s.classList.contains('pc-dessus') ? Math.abs(rs.bottom - (y - 2)) <= 3 : Math.abs(rs.top - (y + 2)) <= 3;
      const cote = p === '1j' ? Math.abs(rs.right - (rz.right - 8)) <= 2 : Math.abs(rs.left - (rz.left + gauche + 6)) <= 2;
      return place && cote;
    });
    const o = e.chart.options();
    const so = e.serie.options();
    const m = e.marques;
    return {
      fleches, lignes, courbe, etiquettes,
      heure: p === '1a' ? null : o.timeScale.tickMarkFormatter(e.barres[0].time, 3) === heureParis,
      prix: o.localization.priceFormatter(123.45) === PP.euros(123.45) && so.priceFormat.formatter(1.5) === PP.euros(1.5),
      graduations: JSON.stringify(o.localization.tickmarksPriceFormatter([600, 500]))
          === JSON.stringify([PP.euros(600, { decimales: 0 }), PP.euros(500, { decimales: 0 })])
        && JSON.stringify(o.localization.tickmarksPriceFormatter([372.5, 375])) === JSON.stringify([PP.euros(372.5), PP.euros(375)]),
      sobre: so.priceLineVisible === false && so.lastValueVisible === false && o.layout.attributionLogo === false
        && o.kineticScroll.mouse === false && o.kineticScroll.touch === false && o.handleScroll.vertTouchDrag === false
        && o.leftPriceScale.entireTextOnly === true,
      marques: m.length === e.ordres.length && m.every((x, i) => x.time === e.ordres[i].time && x.price === e.ordres[i].prix
        && x.position === (e.ordres[i].achat ? 'atPriceBottom' : 'atPriceTop') && x.shape === (e.ordres[i].achat ? 'arrowUp' : 'arrowDown')),
    };
  }, { p, heureParis });
  ok(r.fleches.every(Boolean), `${nom} ${p} : chaque flèche dessinée à son prix, du bon côté, de sa couleur (${r.fleches.filter(Boolean).length}/${r.fleches.length})`);
  ok(r.marques, `${nom} ${p} : les flèches données à la bibliothèque (heure, prix, position, forme)`);
  ok(r.lignes.every(Boolean), `${nom} ${p} : chaque ligne dessinée à son prix`);
  ok(r.courbe >= 0.8, `${nom} ${p} : la courbe dessinée sur ses barres (${Math.round(r.courbe * 100)} %)`);
  ok(r.etiquettes.every(Boolean), `${nom} ${p} : chaque étiquette à 2 px de sa ligne, du bon côté`);
  if (r.heure !== null) ok(r.heure, `${nom} ${p} : la première barre graduée à l'heure de Paris (${heureParis})`);
  ok(r.prix && r.graduations, `${nom} ${p} : les prix et les graduations en euros, ronds ou au centime`);
  ok(r.sobre, `${nom} ${p} : ni ligne ni étiquette du dernier prix, ni inertie, ni glisser vertical au doigt, ni logo`);
}

const MOTEURS = { chrome: () => chromium.launch({ channel: 'chrome' }), webkit: () => webkit.launch(), firefox: () => firefox.launch() };

/* ── 1 et 2 : les tailles, les périodes, le clavier, la molette ── */
for (const [moteur, lancer] of Object.entries(MOTEURS)) {
  const navigateur = await lancer();
  for (const [taille, config] of Object.entries(TAILLES)) {
    const nom = `${moteur} ${taille}`;
    const { contexte, page, etat } = await ouvrir(navigateur, config, URL_LC);
    if (!ok(await pret(page, '1j', 60000), `${nom} : le graphique se dessine`)) { await contexte.close(); continue; }
    for (const p of Object.keys(PERIODES)) {
      if (!ok(await choisirPeriode(page, p), `${nom} ${p} : la période se dessine`)) continue;
      await controlerPeriode(page, etat, nom, p);
      if (taille === 'ordi') await controlerDessin(page, etat, nom, p);
      if (taille === 'ordi' || p === '1j') {
        await page.evaluate(() => document.getElementById('pc-figure').scrollIntoView({ block: 'center' }));
        await page.locator('#pc-figure').screenshot({ path: join(sortie, `${moteur}-${taille}-${p}.png`) });
      }
    }
    if (taille === 'ordi-petit' || taille === 'tel') {
      await page.waitForTimeout(1000);
      const res = await new AxeBuilder({ page }).analyze();
      const graves = res.violations.filter((v) => ['critical', 'serious'].includes(v.impact));
      ok(graves.length === 0, `${nom} : axe, 0 problème critique ou sérieux${graves.length ? ` — ${graves.map((v) => v.id).join(', ')}` : ''}`);
    }
    if (taille === 'ordi') {
      /* Le clavier (1 an). */
      await choisirPeriode(page, '1a');
      await page.focus('#pc-graphe');
      const n = await page.evaluate(() => PCGraphe.essai().barres.length);
      await page.keyboard.press('End');
      for (let k = 0; k < 3; k++) await page.keyboard.press('ArrowLeft');
      await page.waitForTimeout(200);
      const avant = await page.evaluate(() => ({ idx: PCGraphe.essai().idx, plage: PCGraphe.essai().chart.timeScale().getVisibleLogicalRange() }));
      ok(avant.idx === n - 4, `${nom} : Fin puis ← ← ← mènent à la 4e barre avant la fin (${avant.idx} pour ${n - 4})`);
      await page.keyboard.press('+');
      await page.waitForTimeout(300);
      const zoom = await page.evaluate(() => { const e = PCGraphe.essai(); const ts = e.chart.timeScale();
        return { idx: e.idx, plage: ts.getVisibleLogicalRange(), curseur: document.getElementById('pc-curseur').textContent,
                 largeur: document.querySelector('#pc-graphe .pc-lc').clientWidth - e.chart.priceScale('left').width(),
                 xn: ts.timeToCoordinate(e.barres[e.barres.length - 1].time) }; });
      ok(zoom.plage.to - zoom.plage.from < avant.plage.to - avant.plage.from - 1 && zoom.idx === avant.idx,
        `${nom} : + zoome et le curseur garde sa barre`);
      ok(zoom.xn <= zoom.largeur - 8 && zoom.xn >= zoom.largeur - 40,
        `${nom} : au zoom près de la fin, la dernière barre garde sa marge, sans vide après elle (x ${Math.round(zoom.xn)} sur ${Math.round(zoom.largeur)})`);
      const ligne = await page.evaluate(({ i }) => {
        const e = PCGraphe.essai(); const b = e.barres[i];
        return `${PCGraphe.outils.texteTemps(b.time, '1a', true)} · ${PP.euros(b.value)}`;
      }, { i: avant.idx });
      ok(zoom.curseur.startsWith(ligne), `${nom} : la ligne du curseur écrit la barre lue (${zoom.curseur})`);
      const plageDe = () => page.evaluate(() => { const e = PCGraphe.essai(); const ts = e.chart.timeScale(); const r = ts.getVisibleLogicalRange();
        const largeur = document.querySelector('#pc-graphe .pc-lc').clientWidth - e.chart.priceScale('left').width();
        return { idx: e.idx, from: r.from, to: r.to, nTotal: e.nTotal, n: e.barres.length, largeur,
                 xn: ts.timeToCoordinate(e.barres[e.barres.length - 1].time) }; });
      await page.keyboard.press('End');
      await page.waitForTimeout(250);
      const fin = await plageDe();
      ok(fin.idx === fin.n - 1 && fin.xn <= fin.largeur - 8 && fin.xn >= fin.largeur - 40,
        `${nom} : Fin après un zoom va à la dernière barre, avec sa marge et sans vide après elle (x ${Math.round(fin.xn)} sur ${Math.round(fin.largeur)})`);
      await page.keyboard.press('Home');
      await page.waitForTimeout(250);
      const debut = await plageDe();
      ok(debut.idx === 0 && debut.from >= -0.51, `${nom} : Début va à la première barre, sans vide avant elle`);
      await page.keyboard.press('ArrowRight');
      await page.waitForTimeout(150);
      ok((await plageDe()).idx === 1, `${nom} : → avance d'une barre`);
      const l0 = await plageDe();
      await page.keyboard.press('-');
      await page.waitForTimeout(300);
      const l1 = await plageDe();
      ok(l1.to - l1.from > l0.to - l0.from + 1, `${nom} : − dézoome`);
      await page.keyboard.press('0');
      await page.waitForTimeout(300);
      const entiere = await page.evaluate(() => PCGraphe.essai().chart.timeScale().getVisibleLogicalRange());
      ok(entiere.from <= 0.5, `${nom} : 0 revient à la vue entière`);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(200);
      const esc1 = await page.evaluate(() => ({ idx: PCGraphe.essai().idx, actif: PCGraphe.essai().actif,
        ouvert: !!document.querySelector('.pc-figure') && document.getElementById('pc-figure').offsetParent !== null }));
      ok(esc1.idx === null && !esc1.actif && esc1.ouvert, `${nom} : un premier Échap rend la molette et garde le carnet ouvert`);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(400);
      const esc2 = await page.evaluate(() => document.getElementById('pc-figure').offsetParent === null);
      ok(esc2, `${nom} : un second Échap ferme le carnet`);
      await contexte.close();

      /* La molette : la page défile sans clic ; après un clic, elle zoome. */
      const m = await ouvrir(navigateur, config, URL_LC);
      if (await pret(m.page, '1j', 60000)) {
        await choisirPeriode(m.page, '1a');
        await m.page.evaluate(() => document.getElementById('pc-figure').scrollIntoView({ block: 'center' }));
        await m.page.waitForTimeout(300);
        const boite = await m.page.locator('#pc-graphe').boundingBox();
        const cx = boite.x + boite.width / 2;
        const cy = boite.y + boite.height / 2;
        const lire = () => m.page.evaluate(() => ({ y: Math.round(document.getElementById('pc-graphe').getBoundingClientRect().top),
          plage: PCGraphe.essai().chart.timeScale().getVisibleLogicalRange() }));
        await m.page.mouse.move(cx, cy);
        const a0 = await lire();
        await m.page.mouse.wheel(0, 240);
        await m.page.waitForTimeout(500);
        const a1 = await lire();
        ok(a1.y !== a0.y && Math.abs(a1.plage.to - a1.plage.from - (a0.plage.to - a0.plage.from)) < 0.01,
          `${nom} : sans clic, la molette fait défiler le carnet, pas le graphique`);
        /* La page a défilé : le graphique n'est plus sous l'ancienne position de la souris. */
        await m.page.evaluate(() => document.getElementById('pc-figure').scrollIntoView({ block: 'center' }));
        await m.page.waitForTimeout(300);
        const boite2 = await m.page.locator('#pc-graphe').boundingBox();
        await m.page.mouse.click(boite2.x + boite2.width / 2, boite2.y + boite2.height / 2);
        /* Le WebKit de Playwright : une molette envoyée 10 ms après le clic fait parfois défiler
           la page avant que le graphique ne la prenne (2 essais sur 3 le 25/11/2026) ; 300 ms
           après, 5 sur 5. Personne ne tourne la molette 10 ms après avoir cliqué. */
        await m.page.waitForTimeout(300);
        const b0 = await lire();
        await m.page.mouse.wheel(0, -240);
        await m.page.waitForTimeout(500);
        const b1 = await lire();
        ok(Math.abs(b1.y - b0.y) <= 2 && Math.abs(b1.plage.to - b1.plage.from - (b0.plage.to - b0.plage.from)) > 0.5,
          `${nom} : après un clic, la molette zoome et le carnet ne défile pas (graphique à y ${b0.y} puis ${b1.y} ; `
          + `${(b0.plage.to - b0.plage.from).toFixed(1)} puis ${(b1.plage.to - b1.plage.from).toFixed(1)} barres)`);
        await m.page.evaluate(() => document.querySelector('[data-periode="1a"]').click());
        await m.page.waitForTimeout(400);
        const re = await m.page.evaluate(() => PCGraphe.essai().chart.timeScale().getVisibleLogicalRange());
        ok(re.from <= 0.5, `${nom} : la période recliquée revient à la vue entière`);
        /* Une valeur offerte, s'il y en a une dans la liste : cherchée par /api/graphique, jamais
           écrite ici (le dépôt est public : rien sur les positions de Mathis). */
        const offerte = await m.page.evaluate(async () => {
          for (const b of document.querySelectorAll('#pc-choix-valeur [data-ticker]')) {
            const r = await fetch(`/api/graphique?ticker=${encodeURIComponent(b.dataset.ticker)}&periode=1a`).then((x) => x.json()).catch(() => null);
            if (r?.data?.achat?.offerte) { b.click(); return b.dataset.ticker; }
          }
          return null;
        });
        if (offerte) {
          await m.page.waitForFunction((t) => window.PCGraphe && PCGraphe.essai().barres.length > 1
            && document.getElementById('pc-figure-titre').textContent.includes(t), offerte, { timeout: 40000 }).catch(() => {});
          await m.page.waitForTimeout(800);
          const t = await m.page.evaluate(() => ({ reperes: PCGraphe.essai().reperes.map((r) => r.texte),
            etiquettes: [...document.querySelectorAll('#pc-graphe .pc-repere-texte')].map((s) => s.textContent) }));
          ok(t.reperes.length === 1 && t.reperes[0].startsWith('Prix du jour du cadeau : ') && t.etiquettes.length === 1,
            `${nom} : la valeur offerte n'a qu'une ligne, « Prix du jour du cadeau »`);
        }
        ok(m.etat.erreurs.length === 0, `${nom} (molette) : aucune erreur de console${m.etat.erreurs.length ? ` — ${m.etat.erreurs[0]}` : ''}`);
      } else ok(false, `${nom} (molette) : le graphique se dessine`);
      await m.contexte.close();
    } else {
      await contexte.close();
    }
    ok(etat.erreurs.length === 0, `${nom} : aucune erreur de console${etat.erreurs.length ? ` — ${etat.erreurs[0]}` : ''}`);
    ok(etat.externes.length === 0, `${nom} : aucune requête hors de 127.0.0.1${etat.externes.length ? ` — ${etat.externes[0]}` : ''}`);
  }
  await navigateur.close();
}

/* ── 3 : les pannes (Chrome) ── */
{
  const navigateur = await chromium.launch({ channel: 'chrome' });
  const ordi = TAILLES.ordi;

  /* /api/graphique en erreur dès le début. */
  {
    const { contexte, page, etat } = await ouvrir(navigateur, ordi, URL_LC, { routes: (pg) => pg.route('**/api/graphique**',
      (r) => r.fulfill({ status: 500, contentType: 'application/json', body: '{"erreur": "panne inventée"}' })) });
    const vu = await page.waitForFunction(() => {
      const m = document.getElementById('pc-graphe-msg');
      return m && !m.hidden && m.textContent.startsWith('Cours indisponible');
    }, null, { timeout: 40000 }).then(() => true, () => false);
    ok(vu, 'chrome panne : /api/graphique en erreur → « Cours indisponible… » à côté du graphique');
    ok(await page.evaluate(() => document.getElementById('pc-graphe').hidden), 'chrome panne : le cadre vide est caché');
    ok(etat.erreurs.filter((e) => !/500|Failed to load resource/.test(e)).length === 0, 'chrome panne : aucune erreur de script');
    await contexte.close();
  }

  /* Une période changée pendant une lecture lente : la dernière choisie gagne. */
  {
    const { contexte, page } = await ouvrir(navigateur, ordi, URL_LC, { routes: (pg) => pg.route('**/api/graphique**', async (r) => {
      if (new URL(r.request().url()).searchParams.get('periode') === '1m') await pause(4000);
      await r.continue();
    }) });
    if (await pret(page, '1j', 60000)) {
      await page.evaluate(() => document.querySelector('[data-periode="1m"]').click());
      await page.waitForTimeout(300);
      await page.evaluate(() => document.querySelector('[data-periode="1a"]').click());
      await pret(page, '1a');
      await page.waitForTimeout(6000);
      const p = await page.evaluate(() => ({ periode: PCGraphe.essai().periode, legende: document.getElementById('pc-legende').textContent }));
      ok(p.periode === '1a' && p.legende.includes('sur 1 an'), 'chrome lecture lente : 1 mois lu lentement, 1 an choisi ensuite → 1 an reste');
    } else ok(false, 'chrome lecture lente : le graphique se dessine');
    await contexte.close();
  }

  /* La bibliothèque absente : l'ancien graphique reprend la place. */
  {
    const { contexte, page } = await ouvrir(navigateur, ordi, URL_LC, { routes: (pg) => pg.route('**/lightweight-charts.standalone.production.js',
      (r) => r.fulfill({ status: 404, body: 'absent' })) });
    const svg = await page.waitForFunction(() => document.querySelector('#pc-graphe .pc-courbe') !== null, null, { timeout: 40000 })
      .then(() => true, () => false);
    const b = await page.evaluate(() => ({ cache: document.getElementById('pc-graphe').getAttribute('aria-hidden'),
      credit: !!document.querySelector('.pc-credit'), role: document.getElementById('pc-graphe').getAttribute('role') }));
    ok(svg && b.cache === 'true' && !b.credit && b.role === null, 'chrome bibliothèque absente : l\'ancien graphique SVG, tel qu\'avant');
    await contexte.close();
  }

  /* L'ancien graphique demandé (ou par défaut avant la bascule) : rien de la bibliothèque n'est chargé. */
  {
    const { contexte, page } = await ouvrir(navigateur, ordi, URL_SVG);
    const svg = await page.waitForFunction(() => document.querySelector('#pc-graphe .pc-courbe') !== null, null, { timeout: 40000 })
      .then(() => true, () => false);
    const charge = await page.evaluate(() => performance.getEntriesByType('resource').some((e) => /lightweight-charts|graphe\.(js|css)/.test(e.name)));
    ok(svg && !charge, `chrome ${AVANT ? 'sans ?graphe=lc' : '?graphe=svg'} : l'ancien graphique, sans charger la bibliothèque`);
    await contexte.close();
  }

  /* ── 4 : les calculs, sur des valeurs écrites à la main ── */
  {
    const { contexte, page } = await ouvrir(navigateur, ordi, URL_LC);
    await page.waitForFunction(() => window.PCGraphe, null, { timeout: 40000 }).catch(() => {});
    const r = await page.evaluate(() => {
      const { barresDe, avecLeReste, placerOrdres, texteTemps, enSeance, seancesDe } = PCGraphe.outils;
      const s = (iso) => Date.parse(iso) / 1000;
      const res = {};
      /* Barres de 5 min du mardi 24/11/2026 à New York (heure d'hiver : 9 h 30 = 14 h 30 UTC). */
      const b5 = [s('2026-11-24T14:30:00Z'), s('2026-11-24T14:35:00Z'), s('2026-11-24T14:40:00Z')].map((time, i) => ({ time, value: 100 + i }));
      const d = (iso, sens = 'achat', prix = 101, ticker = 'AMD') => ({ ticker, sens, prix_eur: prix, date_utc: iso });
      const place = (barres, decisions, periode) => placerOrdres(barres, decisions, 'AMD', periode);
      res.dansUneBarre = place(b5, [d('2026-11-24T14:37:10Z')], '1j').ordres.map((o) => o.i);           // [1]
      res.avantOuverture = place(b5, [d('2026-11-24T14:05:00Z')], '1j');                                // [0], hors 1
      res.veille = place(b5, [d('2026-11-23T20:00:00Z')], '1j').ordres.length;                          // 0
      res.apresDerniere = place(b5, [d('2026-11-24T22:30:00Z')], '1j');                                 // [2], hors 1
      res.autreValeur = place(b5, [d('2026-11-24T14:37:10Z', 'achat', 101, 'NVDA')], '1j').ordres.length;   // 0
      res.deuxSurUne = place(b5, [d('2026-11-24T14:41:00Z', 'vente', 103), d('2026-11-24T14:42:00Z', 'achat', 99)], '1j')
        .ordres.map((o) => `${o.i}${o.achat ? 'A' : 'V'}${o.prix}`);                                      // [2V103, 2A99]
      res.prixIllisible = place(b5, [d('2026-11-24T14:37:10Z', 'achat', 'x')], '1j').ordres.length;     // 0
      /* Barres journalières (1 an) : lundi 23, mardi 24, mercredi 25/11, posées à midi UTC. */
      const bj = ['2026-11-23', '2026-11-24', '2026-11-25'].map((j, i) => ({ time: s(`${j}T12:00:00Z`), value: 100 + i }));
      res.anApresCloture = place(bj, [d('2026-11-23T21:30:00Z')], '1a').ordres.map((o) => o.i);         // 16 h 30 NY → [1]
      res.anAvantOuverture = place(bj, [d('2026-11-24T13:00:00Z')], '1a').ordres.map((o) => o.i);       // 8 h NY → [1]
      res.anWeekEnd = place(bj, [d('2026-11-22T15:00:00Z')], '1a').ordres.length;                        // dimanche 22, avant la période → 0
      const bjSam = [{ time: s('2026-11-20T12:00:00Z'), value: 1 }, { time: s('2026-11-23T12:00:00Z'), value: 2 }];
      res.anSamedi = place(bjSam, [d('2026-11-21T15:00:00Z')], '1a');                                    // samedi → lundi [1], hors 1
      /* Barres d'une heure (1 mois) : lundi 23/11 à 9 h 30 et 15 h 30 à New York (celle-ci court
         jusqu'à 16 h 30), puis mardi 24 à 9 h 30. */
      const bh = ['2026-11-23T14:30:00Z', '2026-11-23T20:30:00Z', '2026-11-24T14:30:00Z'].map((iso, i) => ({ time: s(iso), value: 100 + i }));
      res.heure1610 = place(bh, [d('2026-11-23T21:10:00Z')], '1m');      // 16 h 10 : hors séance → le lendemain [2], hors 1
      res.heure1555 = place(bh, [d('2026-11-23T20:55:00Z')], '1m');      // 15 h 55 : dans la barre de 15 h 30 [1], hors 0
      /* Un trou de Yahoo en pleine séance (le jour a des barres) : la barre qui suit, pas hors séance. */
      res.trou = place([{ time: s('2026-11-24T14:30:00Z'), value: 1 }, { time: s('2026-11-24T17:00:00Z'), value: 2 }],
        [d('2026-11-24T15:00:00Z')], '1j');                                                             // [1], hors 0
      /* Un jour férié (jeudi 26/11, Thanksgiving) aux heures de séance : la séance suivante, hors séance. */
      const bf = ['2026-11-25', '2026-11-27'].map((j, i) => ({ time: s(`${j}T12:00:00Z`), value: 1 + i }));
      res.ferie = place(bf, [d('2026-11-26T16:00:00Z')], '1a');                                         // [1], hors 1
      /* Un ordre en séance un jour que la courbe n'a pas encore (pas encore relue) : la dernière barre, pas hors séance. */
      res.pasEncore = place(bf, [d('2026-11-30T16:00:00Z')], '1a');                                     // [1], hors 0
      /* L'heure de Paris autour des changements d'heure (9 h 30 à New York). */
      res.mars27 = texteTemps(s('2026-03-27T13:30:00Z'), '1j');     // New York en heure d'été, Paris pas encore : 14:30
      res.mars30 = texteTemps(s('2026-03-30T13:30:00Z'), '1j');     // les deux en heure d'été : 15:30
      res.oct30 = texteTemps(s('2026-10-30T13:30:00Z'), '1j');      // Paris revenu à l'heure d'hiver, pas New York : 14:30
      res.nov03 = texteTemps(s('2026-11-03T14:30:00Z'), '1j');      // les deux à l'heure d'hiver : 15:30
      res.seances = [enSeance(s('2026-03-27T13:30:00Z')), enSeance(s('2026-03-27T13:29:00Z')), enSeance(s('2026-10-30T19:59:00Z')),
                     enSeance(s('2026-10-30T20:00:00Z')), enSeance(s('2026-11-21T15:00:00Z'))];      // [true, false, true, false, false]
      /* Les barres : doublon (le dernier gagne), désordre, prix nul ou illisible retiré ; 1 an à midi UTC du jour de New York. */
      const g = { periode: '1j', points: [{ t: '2026-11-24T14:35:00+00:00', eur: 2 }, { t: '2026-11-24T14:30:00+00:00', eur: 1 },
        { t: '2026-11-24T14:35:00+00:00', eur: 3 }, { t: '2026-11-24T14:40:00+00:00', eur: 0 }, { t: 'x', eur: 4 }, { t: '2026-11-24T14:45:00+00:00', eur: null }] };
      res.barres = barresDe(g).map((b) => `${b.time - s('2026-11-24T14:30:00Z')}:${b.value}`);         // ['0:1', '300:3']
      res.an = barresDe({ periode: '1a', points: [{ t: '2026-11-24T00:00:00-05:00', eur: 5 }] }).map((b) => new Date(b.time * 1000).toISOString());
      /* La fin de séance : des cases vides de 5 min jusqu'à 16 h (exclu), après une dernière barre hors grille. */
      const vides = avecLeReste([{ time: s('2026-11-24T14:30:00Z'), value: 1 }, { time: s('2026-11-24T14:42:13Z'), value: 2 }], '1j');
      res.vides = { n: vides.length, premiereVide: vides[2].time - s('2026-11-24T14:30:00Z'), derniere: vides[vides.length - 1].time - s('2026-11-24T14:30:00Z'),
                    monte: vides.every((v, i) => i === 0 || v.time > vides[i - 1].time) };
      res.videsAutres = avecLeReste([{ time: 1, value: 1 }, { time: 2, value: 2 }], '1m').length;    // 2 : rien d'ajouté hors 1 jour
      /* La séance courte du vendredi 27/11/2026 (9 h 30 – 13 h à New York = 14 h 30 – 18 h UTC), donnée par le
         serveur ; les séances abîmées (heure illisible, absente, fin avant le début) sont écartées. */
      const court = seancesDe({ seances: [{ debut: '2026-11-27T14:30:00+00:00', fin: '2026-11-27T18:00:00+00:00' },
        { debut: 'x', fin: '2026-11-27T18:00:00+00:00' }, null, { debut: '2026-11-27T18:00:00+00:00', fin: '2026-11-27T14:30:00+00:00' }] });
      res.courtLu = court.length === 1 && court[0].debut === s('2026-11-27T14:30:00Z') && court[0].fin === s('2026-11-27T18:00:00Z')
        && seancesDe({}).length === 0 && seancesDe(null).length === 0 && seancesDe({ seances: 'abîmé' }).length === 0;
      res.courtSeances = [enSeance(s('2026-11-27T17:59:00Z'), court), enSeance(s('2026-11-27T18:00:00Z'), court),
        enSeance(s('2026-11-27T18:30:00Z'), court), enSeance(s('2026-11-27T18:30:00Z')), enSeance(s('2026-11-24T18:30:00Z'), court)];   // [true, false, false, true, true]
      const b27 = [s('2026-11-27T14:30:00Z'), s('2026-11-27T14:35:00Z')].map((time, i) => ({ time, value: 100 + i }));
      res.courtVides = avecLeReste(b27, '1j', court).length;      // 2 + de 14 h 40 à 17 h 55 UTC : 40 → 42
      res.courtVidesSans = avecLeReste(b27, '1j').length;         // 2 + de 14 h 40 à 20 h 55 UTC : 76 → 78
      res.courtApres = placerOrdres(b27, [d('2026-11-27T18:30:00Z')], 'AMD', '1j', court);   // 13 h 30 NY : la dernière barre [1], hors 1
      res.courtApresSans = placerOrdres(b27, [d('2026-11-27T18:30:00Z')], 'AMD', '1j');      // sans Yahoo : [1], hors 0
      const bj27 = ['2026-11-27', '2026-11-30'].map((j, i) => ({ time: s(`${j}T12:00:00Z`), value: 100 + i }));
      res.courtAn = placerOrdres(bj27, [d('2026-11-27T18:30:00Z')], 'AMD', '1a', court);     // après 13 h → lundi 30 [1], hors 1
      res.courtAnSans = placerOrdres(bj27, [d('2026-11-27T18:30:00Z')], 'AMD', '1a');        // sans Yahoo : le 27 [0], hors 0
      /* Le jour d'une séance se compte à New York : 20 h le 24/11 à New York (1 h UTC le 25), après la
         clôture du 24 → la barre du 25 ; compté en UTC, il tomberait « avant » la séance du 25 → le 24. */
      const s2425 = seancesDe({ seances: [{ debut: '2026-11-24T14:30:00Z', fin: '2026-11-24T21:00:00Z' },
                                          { debut: '2026-11-25T14:30:00Z', fin: '2026-11-25T21:00:00Z' }] });
      const bj2425 = ['2026-11-24', '2026-11-25'].map((j, i) => ({ time: s(`${j}T12:00:00Z`), value: 100 + i }));
      res.jourNY = placerOrdres(bj2425, [d('2026-11-25T01:00:00Z')], 'AMD', '1a', s2425);       // [1], hors 1
      return res;
    });
    ok(JSON.stringify(r.dansUneBarre) === '[1]', 'calcul : un ordre pendant une barre va sur elle');
    ok(JSON.stringify(r.avantOuverture.ordres.map((o) => o.i)) === '[0]' && r.avantOuverture.hors === 1, 'calcul : un ordre avant l\'ouverture va sur la première barre, compté hors séance');
    ok(r.veille === 0, 'calcul : un ordre d\'avant le jour de la première barre n\'est pas montré');
    ok(JSON.stringify(r.apresDerniere.ordres.map((o) => o.i)) === '[2]' && r.apresDerniere.hors === 1, 'calcul : un ordre après la dernière barre va sur elle, compté hors séance');
    ok(r.autreValeur === 0 && r.prixIllisible === 0, 'calcul : un ordre d\'une autre valeur, ou sans prix lisible, n\'est pas montré');
    ok(JSON.stringify(r.deuxSurUne) === '["2V103","2A99"]', 'calcul : deux ordres sur une barre, une flèche chacun, dans l\'ordre des heures');
    ok(JSON.stringify(r.anApresCloture) === '[1]' && JSON.stringify(r.anAvantOuverture) === '[1]', 'calcul 1 an : après la clôture → le jour suivant ; avant l\'ouverture → le jour même');
    ok(r.anWeekEnd === 0 && JSON.stringify(r.anSamedi.ordres.map((o) => o.i)) === '[1]' && r.anSamedi.hors === 1, 'calcul 1 an : un ordre du samedi va au lundi ; d\'avant la période, rien');
    ok(JSON.stringify(r.heure1610.ordres.map((o) => o.i)) === '[2]' && r.heure1610.hors === 1,
      'calcul 1 mois : 16 h 10 à New York, hors séance → la barre du lendemain, pas celle de 15 h 30');
    ok(JSON.stringify(r.heure1555.ordres.map((o) => o.i)) === '[1]' && r.heure1555.hors === 0, 'calcul 1 mois : 15 h 55, dans la barre de 15 h 30');
    ok(JSON.stringify(r.trou.ordres.map((o) => o.i)) === '[1]' && r.trou.hors === 0, 'calcul : un trou de Yahoo en séance → la barre qui suit, pas hors séance');
    ok(JSON.stringify(r.ferie.ordres.map((o) => o.i)) === '[1]' && r.ferie.hors === 1, 'calcul : un jour férié aux heures de séance → la séance suivante, hors séance');
    ok(JSON.stringify(r.pasEncore.ordres.map((o) => o.i)) === '[1]' && r.pasEncore.hors === 0, 'calcul : un ordre en séance un jour que la courbe n\'a pas encore → la dernière barre, pas hors séance');
    ok(r.mars27 === '14:30' && r.mars30 === '15:30' && r.oct30 === '14:30' && r.nov03 === '15:30',
      `calcul : l'heure de Paris autour des changements d'heure (${r.mars27}, ${r.mars30}, ${r.oct30}, ${r.nov03})`);
    ok(JSON.stringify(r.seances) === '[true,false,true,false,false]', 'calcul : la séance de New York, à ses deux bouts et le samedi');
    ok(JSON.stringify(r.barres) === '["0:1","300:3"]', 'calcul : barres triées, doublon (le dernier gagne), prix nul ou illisible retirés');
    ok(JSON.stringify(r.an) === '["2026-11-24T12:00:00.000Z"]', 'calcul 1 an : une barre à midi UTC du jour de New York');
    ok(r.vides.monte && r.vides.premiereVide === 900 && r.vides.derniere === 6.5 * 3600 - 300 && r.vides.n === 2 + (6.5 * 3600 - 900) / 300,
      'calcul : les cases vides de la séance, après une barre hors grille, jusqu\'à 15 h 55 à New York');
    ok(r.videsAutres === 2, 'calcul : pas de cases vides hors de 1 jour');
    ok(r.courtLu === true, 'calcul : les séances de Yahoo lues du serveur, les abîmées écartées (heure illisible, absente, fin avant le début)');
    ok(JSON.stringify(r.courtSeances) === '[true,false,false,true,true]',
      'calcul : séance courte du 27/11, 12 h 59 en séance, 13 h et 13 h 30 hors séance ; sans Yahoo, 13 h 30 restait en séance ; un autre jour garde 9 h 30 – 16 h');
    ok(r.courtVides === 42 && r.courtVidesSans === 78,
      `calcul : un jour de séance courte, les cases vides s'arrêtent à 12 h 55 à New York (${r.courtVides} cases ; ${r.courtVidesSans} jusqu'à 15 h 55 sans Yahoo)`);
    ok(JSON.stringify(r.courtApres.ordres.map((o) => o.i)) === '[1]' && r.courtApres.hors === 1 && r.courtApresSans.hors === 0,
      'calcul : un ordre de 13 h 30 un jour de séance courte est hors séance (en séance sans les séances de Yahoo)');
    ok(JSON.stringify(r.courtAn.ordres.map((o) => o.i)) === '[1]' && r.courtAn.hors === 1
      && JSON.stringify(r.courtAnSans.ordres.map((o) => o.i)) === '[0]' && r.courtAnSans.hors === 0,
      'calcul 1 an : après la clôture de 13 h un jour de séance courte → la séance suivante (le jour même sans Yahoo)');
    ok(JSON.stringify(r.jourNY.ordres.map((o) => o.i)) === '[1]' && r.jourNY.hors === 1,
      'calcul 1 an : le jour d\'une séance se compte à New York (20 h le 24, 1 h UTC le 25 → la barre du 25)');
    await contexte.close();
  }
  await navigateur.close();
}

/* ── 5 : les retours de la relecture du 25/11/2026 ── */

/* 5a. La fenêtre agrandie (390 → 844 → 1440 px) garde toute la courbe dans le cadre (trois moteurs). */
for (const [moteur, lancer] of Object.entries(MOTEURS)) {
  const navigateur = await lancer();
  const { contexte, page } = await ouvrir(navigateur, { viewport: { width: 390, height: 844 } }, URL_LC);
  if (await pret(page, '1j', 60000)) {
    for (const p of ['1j', '1a']) {
      await page.setViewportSize({ width: 390, height: 844 });
      await choisirPeriode(page, p);
      await page.waitForTimeout(600);
      for (const w of [844, 1440]) {
        await page.setViewportSize({ width: w, height: 900 });
        await page.waitForTimeout(800);
        const v = await page.evaluate(() => {
          const e = PCGraphe.essai(); const ts = e.chart.timeScale(); const r = ts.getVisibleLogicalRange();
          const largeur = document.querySelector('#pc-graphe .pc-lc').clientWidth - e.chart.priceScale('left').width();
          return { from: r.from, to: r.to, nTotal: e.nTotal, largeur,
                   x0: ts.timeToCoordinate(e.barres[0].time), xn: ts.timeToCoordinate(e.barres[e.barres.length - 1].time) };
        });
        ok(v.from <= 0.5 && v.to >= v.nTotal - 1.5 && v.x0 >= 0 && v.xn <= v.largeur,
          `${moteur} ${p} : la fenêtre agrandie de 390 à ${w} px garde toute la courbe dans le cadre`);
      }
      /* Glisser loin à droite puis à gauche, et dézoomer à la molette : la courbe reste dans le cadre
         (aucun bord fixé dans la bibliothèque : graphe.js borne la plage, seconde relecture du 25/11). */
      await page.evaluate(() => document.getElementById('pc-figure').scrollIntoView({ block: 'center' }));
      await page.waitForTimeout(300);
      const boite = await page.locator('#pc-graphe').boundingBox();
      const cx = boite.x + boite.width / 2;
      const cy = boite.y + boite.height / 2;
      const dans = () => page.evaluate(() => {
        const e = PCGraphe.essai(); const ts = e.chart.timeScale();
        const largeur = document.querySelector('#pc-graphe .pc-lc').clientWidth - e.chart.priceScale('left').width();
        /* La fin : la dernière case (en séance, les cases vides vont jusqu'à 16 h). */
        const x0 = ts.logicalToCoordinate(0); const xn = ts.logicalToCoordinate(e.nTotal - 1);
        const r = ts.getVisibleLogicalRange();
        return { x0, xn, largeur, barre: largeur / (r.to - r.from) };
      });
      for (const [quoi, dx] of [['à droite', 700], ['à gauche', -700]]) {
        await page.mouse.move(cx, cy);
        await page.mouse.down();
        await page.mouse.move(cx + dx, cy, { steps: 12 });
        await page.mouse.up();
        await page.waitForTimeout(400);
        const v = await dans();
        const borne = dx > 0 ? v.x0 >= -2 && v.x0 <= v.barre + 2 : v.xn <= v.largeur - 8 && v.xn >= v.largeur - 40;
        ok(borne, `${moteur} ${p} : glisser de 700 px ${quoi} s'arrête au bout de la courbe (première barre x ${Math.round(v.x0)}, dernière ${Math.round(v.xn)} sur ${Math.round(v.largeur)})`);
      }
      await page.mouse.click(cx, cy);
      await page.waitForTimeout(300);
      for (let k = 0; k < 8; k++) { await page.mouse.wheel(0, 240); await page.waitForTimeout(120); }
      await page.waitForTimeout(400);
      const z = await dans();
      ok(z.x0 >= -2 && z.x0 <= z.barre + 2 && z.xn <= z.largeur - 8 && z.xn >= z.largeur - 40,
        `${moteur} ${p} : dézoomer à la molette s'arrête à la courbe entière (x ${Math.round(z.x0)} à ${Math.round(z.xn)} sur ${Math.round(z.largeur)})`);
      await page.keyboard.press('Escape');
      await page.mouse.move(2, 2);
    }
  } else ok(false, `${moteur} agrandir : le graphique se dessine`);
  await contexte.close();
  await navigateur.close();
}

/* 5b. Chrome : le carnet ouvert après coup, une seule barre puis la vraie courbe, la fin de séance, le zoom
   gardé au rafraîchissement, le journal changé, le survol, la molette rendue, le retour après une panne,
   une autre valeur en panne. */
{
  const navigateur = await chromium.launch({ channel: 'chrome' });
  const ordi = TAILLES.ordi;

  /* Le carnet ouvert après le chargement (touche P) : le graphique à sa vraie largeur, vue entière. */
  {
    const { contexte, page } = await ouvrir(navigateur, ordi, `${BASE}/?plan${AVANT ? '&graphe=lc' : ''}`);
    await page.waitForFunction(() => window.PP && document.readyState === 'complete', null, { timeout: 40000 }).catch(() => {});
    await page.waitForTimeout(3000);
    await page.keyboard.press('p');
    const dessine = await pret(page, '1j', 60000);
    const v = dessine ? await page.evaluate(() => {
      const e = PCGraphe.essai(); const r = e.chart.timeScale().getVisibleLogicalRange();
      const zone = document.querySelector('#pc-graphe .pc-lc'); const toile = zone.querySelector('canvas');
      return { largeur: zone.clientWidth, toile: toile ? toile.getBoundingClientRect().width : 0, from: r.from, to: r.to, nTotal: e.nTotal };
    }) : null;
    ok(dessine && v.largeur > 300 && v.toile > 0 && v.from <= 0.5 && v.to >= v.nTotal - 1.5,
      'chrome carnet ouvert après coup (P) : le graphique à sa largeur, vue entière');
    await contexte.close();
  }

  /* graphe.js appelé directement, sur la courbe et le journal reçus par la page. */
  {
    const { contexte, page, etat } = await ouvrir(navigateur, ordi, URL_LC);
    if (await pret(page, '1j', 60000)) {
      await page.waitForTimeout(500);
      const ticker = await page.evaluate(() => (document.getElementById('pc-figure-titre').textContent.match(/de (\S+), en euros/) || [])[1]);
      const g = etat.graphiques.get(`${ticker}|1j`);
      const decisions = etat.journal?.decisions || [];
      const r = await page.evaluate(async ({ g, decisions }) => {
        const att = (ms) => new Promise((fin) => setTimeout(fin, ms));
        const e = () => PCGraphe.essai();
        const vu = () => ({ chart: !!e().chart, cadreCache: document.getElementById('pc-graphe').hidden,
                            msg: !document.getElementById('pc-graphe-msg').hidden, barres: e().barres.length });
        const res = {};
        PCGraphe.dessiner({ g: { ...g, points: g.points.slice(0, 1) }, decisions });   // l'ouverture : une seule barre
        await att(200);
        res.uneBarre = vu();
        PCGraphe.dessiner({ g, decisions });
        await att(300);
        res.retour = vu();
        res.debutNY = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit',
          hourCycle: 'h23' }).format(new Date(Date.parse(g.points[0].t)));
        PCGraphe.dessiner({ g: { ...g, points: g.points.slice(0, 20) }, decisions });   // une séance en cours
        await att(200);
        res.cases = e().nTotal;
        PCGraphe.dessiner({ g, decisions });
        await att(200);
        e().chart.timeScale().setVisibleLogicalRange({ from: 10, to: 40 });
        await att(200);
        PCGraphe.dessiner({ g: { ...g }, decisions });                                    // la même courbe, rafraîchie
        await att(300);
        const r1 = e().chart.timeScale().getVisibleLogicalRange();
        res.zoomGarde = Math.abs(r1.from - 10) < 0.6 && Math.abs(r1.to - 40) < 0.6;
        PCGraphe.vueEntiere();
        await att(200);
        const derniere = g.points[g.points.length - 1];
        const loin = Math.max(...g.points.map((p) => p.eur)) * 1.3;
        const avant = e().marques.length;
        PCGraphe.ordres([...decisions, { ticker: g.ticker, sens: 'vente', prix_eur: loin, date_utc: derniere.t }]);
        await att(300);
        const h = document.querySelector('#pc-graphe .pc-lc').clientHeight - e().chart.timeScale().height();
        const y = e().serie.priceToCoordinate(loin);
        res.journal = { plus: e().marques.length === avant + 1, dedans: y !== null && y >= 0 && y <= h };
        PCGraphe.ordres(decisions);
        /* La bibliothèque refuse la courbe rafraîchie (setData remplacé) : rien de l'état ne change. */
        const avantRefus = { barres: e().barres.length, marques: e().marques.length, lignes: e().reperes.length };
        const serie = e().serie;
        const vraiSetData = serie.setData;
        serie.setData = () => { throw new Error('refus inventé'); };
        PCGraphe.dessiner({ g: { ...g, points: g.points.slice(0, g.points.length - 3) }, decisions });
        await att(200);
        res.refusMeme = { garde: !!e().chart && e().barres.length === avantRefus.barres && e().marques.length === avantRefus.marques
          && e().reperes.length === avantRefus.lignes, msg: document.getElementById('pc-graphe-msg').textContent };
        serie.setData = vraiSetData;
        PCGraphe.dessiner({ g, decisions });
        await att(200);
        res.apresRefus = !document.getElementById('pc-graphe-msg').hidden ? 'message encore là' : 'ok';
        /* Une courbe NEUVE (une autre période) refusée : pas de graphique vide laissé derrière. */
        const vraiPoser = PCGraphe.outils.poserDonnees;
        PCGraphe.outils.poserDonnees = () => { throw new Error('refus inventé'); };
        PCGraphe.dessiner({ g: { ...g, periode: '1m' }, decisions });
        await att(200);
        res.refusNeuve = { chart: !!e().chart, cache: document.getElementById('pc-graphe').hidden,
                           msg: !document.getElementById('pc-graphe-msg').hidden };
        PCGraphe.outils.poserDonnees = vraiPoser;
        PCGraphe.dessiner({ g, decisions });
        await att(300);
        res.apresNeuve = !!e().chart && !document.getElementById('pc-graphe').hidden;
        return res;
      }, { g, decisions });
      ok(!r.uneBarre.chart && r.uneBarre.cadreCache && r.uneBarre.msg, 'chrome une seule barre (l\'ouverture) : « Pas assez de points », sans graphique');
      ok(r.retour.chart && !r.retour.cadreCache && !r.retour.msg && r.retour.barres > 1, 'chrome une seule barre puis la vraie courbe : le graphique revient');
      if (r.debutNY === '09:30') ok(r.cases === 78, `chrome une séance en cours : 20 barres → 78 cases jusqu'à 15 h 55 (${r.cases})`);
      else note(`chrome séance en cours : la courbe du jour ne commence pas à 9 h 30 (${r.debutNY}), cas sauté`);
      ok(r.zoomGarde, 'chrome la même courbe rafraîchie garde le zoom');
      ok(r.journal.plus && r.journal.dedans, 'chrome un ordre de plus au journal : sa flèche arrive, et l\'échelle l\'englobe même loin de la courbe');
      ok(r.refusMeme.garde && r.refusMeme.msg.startsWith('Courbe illisible') && r.apresRefus === 'ok',
        'chrome la bibliothèque refuse la courbe rafraîchie : la courbe d\'avant reste entière, puis la suivante passe');
      ok(!r.refusNeuve.chart && r.refusNeuve.cache && r.refusNeuve.msg && r.apresNeuve,
        'chrome la bibliothèque refuse une courbe neuve : pas de graphique vide laissé, puis la suivante passe');
    } else ok(false, 'chrome graphe.js direct : le graphique se dessine');
    await contexte.close();
  }

  /* La souris sur la barre d'un ordre (1 mois), puis la molette prise par un clic et rendue en sortant. */
  {
    const { contexte, page } = await ouvrir(navigateur, ordi, URL_LC);
    if (await pret(page, '1j', 60000) && await choisirPeriode(page, '1m')) {
      await page.evaluate(() => document.getElementById('pc-figure').scrollIntoView({ block: 'center' }));
      await page.waitForTimeout(300);
      const cible = await page.evaluate(() => {
        const e = PCGraphe.essai(); const o = e.ordres[e.ordres.length - 1];
        if (!o) return null;
        const zone = document.querySelector('#pc-graphe .pc-lc').getBoundingClientRect();
        return { x: zone.left + e.chart.priceScale('left').width() + e.chart.timeScale().timeToCoordinate(o.time),
                 y: zone.top + e.serie.priceToCoordinate(e.barres[o.i].value), attendu: `${o.achat ? 'achat' : 'vente'} à ${PP.euros(o.prix)}` };
      });
      if (cible) {
        await page.mouse.move(cible.x, cible.y);
        await page.waitForTimeout(300);
        const ligne = await page.evaluate(() => document.getElementById('pc-curseur').textContent);
        ok(ligne.includes(cible.attendu) && !ligne.startsWith('Fin de la courbe'), `chrome la souris sur la barre d'un ordre : la ligne écrit le point et l'ordre (${ligne})`);
      } else note('chrome survol : aucun ordre sur la courbe d\'un mois, cas sauté');
      const b = await page.locator('#pc-graphe').boundingBox();
      await page.mouse.click(b.x + b.width / 2, b.y + b.height / 2);
      await page.waitForTimeout(200);
      const pris = await page.evaluate(() => PCGraphe.essai().actif);
      await page.mouse.move(5, 5);
      await page.waitForTimeout(200);
      const rendu = await page.evaluate(() => !PCGraphe.essai().actif);
      ok(pris && rendu, 'chrome un clic prend la molette, sortir du graphique la rend à la page');
    } else ok(false, 'chrome survol : le graphique se dessine');
    await contexte.close();
  }

  /* /api/graphique en panne, puis de retour : un clic sur une période redessine le graphique. */
  {
    const { contexte, page } = await ouvrir(navigateur, ordi, URL_LC, { routes: (pg) => pg.route('**/api/graphique**',
      (r) => r.fulfill({ status: 500, contentType: 'application/json', body: '{"erreur": "panne inventée"}' })) });
    await page.waitForFunction(() => { const m = document.getElementById('pc-graphe-msg'); return m && !m.hidden; }, null, { timeout: 40000 }).catch(() => {});
    ok(await page.evaluate(() => !document.querySelector('#pc-graphe .pc-lc').textContent.includes('indisponible')
      && document.getElementById('pc-graphe-msg').textContent.includes('indisponible')), 'chrome panne : le message à côté du graphique, jamais dans son cadre');
    await page.unroute('**/api/graphique**');
    await page.evaluate(() => document.querySelector('[data-periode="1m"]').click());
    const revenu = await pret(page, '1m', 40000);
    const v = await page.evaluate(() => ({ cache: document.getElementById('pc-graphe').hidden, msg: !document.getElementById('pc-graphe-msg').hidden }));
    ok(revenu && !v.cache && !v.msg, 'chrome panne puis retour : un clic sur 1 mois redessine le graphique, sans le message');
    await contexte.close();
  }

  /* Les séances du serveur arrivent jusqu'au dessin (dessiner) et aux flèches (ordres) : une séance
     annoncée jusqu'à 17 h à New York allonge le blanc d'« 1 jour » jusque-là, et un ordre de 16 h 30
     y est en séance (relecture du 27/11/2026 : seuls les calculs étaient vérifiés). */
  {
    let injecte = null;
    const { contexte, page } = await ouvrir(navigateur, ordi, URL_LC, { routes: (pg) => pg.route('**/api/graphique**', async (r) => {
      const u = new URL(r.request().url());
      if (u.searchParams.get('periode') !== '1j') return r.continue();
      const rep = await r.fetch();
      const j = await rep.json();
      const debut = Date.parse(j.data.points[0].t);
      injecte = { ticker: u.searchParams.get('ticker'), debut: new Date(debut).toISOString(),
                  fin: new Date(debut + 7.5 * 3600e3).toISOString() };
      j.data.seances = [{ debut: injecte.debut, fin: injecte.fin }];
      return r.fulfill({ response: rep, json: j });
    }) });
    if (await pret(page, '1j', 60000) && injecte) {
      const v = await page.evaluate((s) => {
        const e = PCGraphe.essai();
        const debut = e.barres[0].time;
        const fin = Date.parse(s.fin) / 1000;
        const derniere = e.barres[e.barres.length - 1].time;
        let vides = 0;
        for (let t = debut + 300 * (Math.floor((derniere - debut) / 300) + 1); t < fin; t += 300) vides += 1;
        const ordre = { ticker: s.ticker, sens: 'achat', prix_eur: e.barres[0].value,
                        date_utc: new Date((debut + 7 * 3600) * 1000).toISOString() };
        return { nTotal: e.nTotal, attendu: e.barres.length + vides, debutJuste: debut === Date.parse(s.debut) / 1000,
                 hors: PCGraphe.ordres([ordre])?.hors };
      }, injecte);
      ok(v.debutJuste && v.nTotal === v.attendu,
        `chrome séances : la séance du serveur arrive jusqu'au dessin (${v.nTotal} cases, ${v.attendu} attendues)`);
      ok(v.hors === 0, 'chrome séances : … et jusqu\'aux flèches (un ordre de 16 h 30 en séance si elle va jusqu\'à 17 h)');
    } else ok(false, 'chrome séances : le graphique se dessine');
    await contexte.close();
  }

  /* Une autre valeur dont la lecture échoue : plus de courbe d'avant sous le nouveau choix. La valeur est
     prise dans la liste de la page, jamais écrite ici (dépôt public). */
  {
    let autre = null;
    const { contexte, page } = await ouvrir(navigateur, ordi, URL_LC, { routes: (pg) => pg.route('**/api/graphique**', (r) => {
      if (autre && new URL(r.request().url()).searchParams.get('ticker') === autre) {
        return r.fulfill({ status: 500, contentType: 'application/json', body: '{"erreur": "panne inventée"}' });
      }
      return r.continue();
    }) });
    if (await pret(page, '1j', 60000)) {
      autre = await page.evaluate(() => {
        const titre = document.getElementById('pc-figure-titre').textContent;
        const b = [...document.querySelectorAll('#pc-choix-valeur [data-ticker]')].find((x) => !titre.includes(` ${x.dataset.ticker},`));
        return b ? b.dataset.ticker : null;
      });
      if (autre) {
        await page.evaluate((t) => document.querySelector(`#pc-choix-valeur [data-ticker="${t}"]`).click(), autre);
        const vu = await page.waitForFunction(() => { const m = document.getElementById('pc-graphe-msg'); return m && !m.hidden; }, null, { timeout: 40000 })
          .then(() => true, () => false);
        const v = await page.evaluate(() => ({ chart: !!PCGraphe.essai().chart, cache: document.getElementById('pc-graphe').hidden,
          titre: document.getElementById('pc-figure-titre').textContent, legende: document.getElementById('pc-legende').textContent }));
        ok(vu && !v.chart && v.cache && v.titre.includes(autre) && v.legende === '',
          'chrome une autre valeur en panne : plus de courbe d\'avant, le titre de la nouvelle, pas de phrase');
      } else note('chrome autre valeur : une seule valeur dans la liste, cas sauté');
    } else ok(false, 'chrome autre valeur : le graphique se dessine');
    await contexte.close();
  }
  await navigateur.close();
}

const non = lignes.filter((l) => l.startsWith('NON')).length;
const controles = lignes.filter((l) => !l.startsWith('note')).length;   // une note n'est pas un contrôle
const texte = `${lignes.join('\n')}\n\n${controles - non} sur ${controles} contrôles passent${non ? ` — ${non} NON` : ''}.`;
writeFileSync(join(sortie, 'rapport.txt'), texte);
console.log(texte);
process.exit(non ? 1 : 0);

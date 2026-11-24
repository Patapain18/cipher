// Le direct (« Ta position » et le tableau des 49 en direct, plan validé le 24/11/2026, fin de BRIEF.md).
// Ce script lance LUI-MÊME un faux Yahoo (outils/faux_flux.py, ports 8790-8791) et un serveur d'essai À PART
// (port 8778, un journal INVENTÉ dans <sortie>/donnees, FLUX_URL vers le faux, FLUX_BOURSE=ouverte), puis les
// ferme. Jamais le port 8770, jamais data/. Il lit quand même Yahoo pour le dollar et le scan des 49.
// Usage : node outils/verif_direct.mjs <dossier de sortie hors du projet>
// Dans Chrome (tout) et WebKit (le carnet, la vue plan, le clavier, axe) :
//  1. le carnet : « en direct » et Pause ; un cours changé par le faux Yahoo est à l'écran en moins de 4 s,
//     égal au serveur ; pendant 30 s de cours qui bougent, le haut du carnet égale le bilan du journal à chaque
//     relevé ; une sélection dans le journal et le focus sur Pause restent pendant les mises à jour ;
//  2. Pause (au clavier) : « en pause », plus d'une lecture toutes les 2 s ; relâchée : relue tout de suite ;
//  3. (Chrome) le faux Yahoo muet → « toutes les minutes » en moins de 40 s, une lecture par minute au plus ;
//     il reparle → « en direct » au tour suivant ;
//  4. (Chrome) un serveur qui ne répond plus (le journal retardé de 10 s) → « en direct » disparaît en moins
//     de 8 s, et la page ralentit ;
//  5. (Chrome) un ancien serveur (réponses sans `direct` ni `position`) : aucun mot, aucun bouton, 60 s ;
//  6. la vue plan : le badge et Pause de l'en-tête, « Cours de …, en direct. », les quatre cases du tableau
//     avec des valeurs fabriquées (/api/valeurs/direct), changées EN PLACE (mêmes nœuds), puis rendues au scan ;
//  7. axe à 1280 et 390 px sur ce qui change, aucune erreur de console.
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const require = createRequire(`${homedir()}/.claude/skills/verif/outil/`);
const { chromium, webkit } = require('playwright');
const { AxeBuilder } = require('@axe-core/playwright');

const sortie = process.argv[2];
if (!sortie) { console.error('Usage : node outils/verif_direct.mjs <dossier de sortie>'); process.exit(2); }
const PROJET = join(dirname(fileURLToPath(import.meta.url)), '..');
if (sortie.startsWith(PROJET)) { console.error('La sortie doit être HORS du projet.'); process.exit(2); }
const DONNEES = join(sortie, 'donnees');
mkdirSync(DONNEES, { recursive: true });
writeFileSync(join(DONNEES, 'journal.json'), '[]');
writeFileSync(join(DONNEES, 'stock_positions.json'), '{}');

const PORT = 8778;
const BASE = `http://127.0.0.1:${PORT}`;
const PILOTE = 'http://127.0.0.1:8791';
const lignes = [];
const ok = (cond, quoi) => { lignes.push(`${cond ? 'ok ' : 'NON'} ${quoi}`); console.log(`${cond ? 'ok ' : 'NON'} ${quoi}`); return cond; };
const dormir = (ms) => new Promise((r) => setTimeout(r, ms));
const espaces = (s) => String(s ?? '').replace(/[\s  ]+/g, ' ').trim();

async function libre(port) {
  try { await fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(800) }); return false; } catch { return true; }
}
async function attendreQue(fn, delai = 10_000, pas = 200) {
  const fin = Date.now() + delai;
  while (Date.now() < fin) {
    try { if (await fn()) return true; } catch { /* pas encore */ }
    await dormir(pas);
  }
  try { return Boolean(await fn()); } catch { return false; }
}
const pilote = (chemin) => fetch(`${PILOTE}${chemin}`).then((r) => r.json());
const serveur = (chemin) => fetch(`${BASE}${chemin}`, { cache: 'no-store' }).then((r) => r.json()).then((j) => j.data);

// ── Les deux serveurs, lancés et fermés ici
const processus = [];
function lancer(args, env) {
  const p = spawn('/usr/bin/python3', args, { cwd: PROJET, env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
  let journal = '';
  p.stdout.on('data', (d) => { journal += d; });
  p.stderr.on('data', (d) => { journal += d; });
  processus.push(p);
  p.journal = () => journal;
  return p;
}
async function fermer() {
  for (const p of processus) { try { p.kill('SIGTERM'); } catch { /* déjà fermé */ } }
  await dormir(500);
}
process.on('exit', () => { for (const p of processus) { try { p.kill('SIGKILL'); } catch { /* rien */ } } });

for (const port of [8790, 8791, PORT]) {
  if (!(await libre(port))) { console.error(`Le port ${port} est déjà pris : fermer ce qui l'occupe d'abord.`); process.exit(2); }
}
lancer(['outils/faux_flux.py', '8790'], {});
ok(await attendreQue(() => pilote('/etat'), 8000), 'le faux Yahoo répond');
const srv = lancer(['web/server.py'], {
  DASHBOARD_PORT: String(PORT),
  MA_POSITION_JOURNAL: join(DONNEES, 'journal.json'),
  MA_POSITION_CARNET: join(DONNEES, 'stock_positions.json'),
  MA_POSITION_PAPIER: join(DONNEES, 'papier.json'),
  FLUX_URL: 'ws://127.0.0.1:8790',
  FLUX_BOURSE: 'ouverte',
});
ok(await attendreQue(() => serveur('/api/status'), 30_000), 'le serveur d\'essai répond (port 8778)');

// Un journal INVENTÉ : deux achats, aucun chiffre de Mathis.
for (const d of [
  { sens: 'achat', ticker: 'AMD', quantite: 0.5, prix_eur: 400, montant_eur: 200, frais_eur: 1, date: '2026-11-20T16:00', raison: 'Essai inventé' },
  { sens: 'achat', ticker: 'MSFT', quantite: 0.25, prix_eur: 380, montant_eur: 95, frais_eur: 1, date: '2026-11-20T16:05', raison: 'Essai inventé, deuxième' },
]) {
  const r = await fetch(`${BASE}/api/journal/ajouter`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Dashboard': '1' }, body: JSON.stringify(d) }).then((x) => x.json());
  ok(r.ok, `journal inventé : ${d.ticker} noté`);
}
let prixAMD = 420;
await pilote(`/prix?AMD=${prixAMD}&MSFT=500&SPY=650`);
ok(await attendreQue(async () => (await serveur('/api/position')).direct?.en_direct === true, 20_000),
  'le serveur d\'essai dit « en direct » avec le faux Yahoo');

async function verifier(nom, type, options, complet) {
  const navigateur = await type.launch(options);
  const erreurs = [];
  try {
    const contexte = await navigateur.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await contexte.newPage();
    page.on('console', (m) => { if (m.type() === 'error') erreurs.push(m.text()); });
    page.on('pageerror', (e) => erreurs.push(String(e)));
    const lectures = [];
    page.on('request', (r) => { if (r.url().includes('/api/journal')) lectures.push(Date.now()); });
    const lecturesDepuis = (t) => lectures.filter((x) => x >= t).length;

    // ── 1. Le carnet
    await page.goto(`${BASE}/?plan&vue=carnet`);
    const mot = () => page.evaluate(() => document.querySelector('#pc-direct .pc-direct-mode')?.textContent || '');
    ok(await attendreQue(async () => (await mot()) === 'en direct', 20_000), `${nom} · carnet : « en direct »`);
    ok(await page.evaluate(() => {
      const b = document.querySelector('#pc-direct [data-direct-pause]');
      return Boolean(b) && b.tagName === 'BUTTON' && b.getAttribute('aria-pressed') === 'false' && b.textContent.trim() === 'Pause';
    }), `${nom} · carnet : un bouton Pause, pas appuyé`);
    await page.waitForTimeout(1200);   // le fondu du carnet (piège connu d'axe)
    await page.screenshot({ path: join(sortie, `${nom}-carnet-direct.png`) });

    // Un cours changé par le faux Yahoo : à l'écran en moins de 4 s, égal au serveur.
    prixAMD = 431.25;
    const t0 = Date.now();
    await pilote(`/prix?AMD=${prixAMD}`);
    let attendu = '';
    const vu = await attendreQue(async () => {
      const p = (await serveur('/api/journal')).position;
      if (p.lignes.find((l) => l.ticker === 'AMD')?.prix_usd !== prixAMD) return false;
      attendu = await page.evaluate((v) => PP.euros(v), p.total.valeur_eur);
      return espaces(await page.evaluate(() => document.querySelector('#pc-direct .pc-direct-valeur')?.textContent)) === espaces(attendu);
    }, 8000, 100);
    const delaiVu = (Date.now() - t0) / 1000;
    ok(vu && delaiVu < 4, `${nom} · carnet : un cours changé est à l'écran en ${delaiVu.toFixed(1)} s (moins de 4 s), égal au serveur (${espaces(attendu)})`);

    // Le haut du carnet égale le bilan du journal, à chaque relevé, pendant 30 s de cours qui bougent.
    const releves = [];
    for (let k = 0; k < 12; k += 1) {
      prixAMD = Math.round((420 + 15 * Math.sin(k)) * 100) / 100;
      await pilote(`/prix?AMD=${prixAMD}&MSFT=${(500 + k).toFixed(2)}`);
      await dormir(2500);
      releves.push(await page.evaluate(() => {
        const haut = document.querySelector('#pc-direct .pc-direct-valeur')?.textContent || '';
        const bilan = document.querySelector('.pc-bilan')?.textContent || '';
        const m = bilan.replace(/[\s  ]+/g, ' ').match(/Ton portefeuille vaut ([\d\s ,]+€)/);
        return { haut: haut.replace(/[\s  ]+/g, ' ').trim(), bilan: m ? m[1].replace(/[\s  ]+/g, ' ').trim() : null };
      }));
    }
    const differents = new Set(releves.map((r) => r.haut)).size;
    ok(releves.every((r) => r.bilan && r.haut === r.bilan) && differents >= 4,
      `${nom} · carnet : le haut égale le bilan du journal à chacun des 12 relevés (${differents} valeurs différentes)`);
    if (!releves.every((r) => r.haut === r.bilan)) console.log('   relevés :', JSON.stringify(releves));

    // Une sélection dans le journal et le focus sur Pause restent pendant les mises à jour.
    await page.evaluate(() => {
      const p = document.querySelector('.pc-bilan');
      const r = document.createRange();
      r.selectNodeContents(p);
      const s = window.getSelection();
      s.removeAllRanges();
      s.addRange(r);
    });
    const choisi = await page.evaluate(() => window.getSelection().toString().length);
    for (let k = 0; k < 3; k += 1) { await pilote(`/prix?AMD=${(421 + k).toFixed(2)}`); await dormir(2200); }
    ok(await page.evaluate((n) => {
      const s = window.getSelection();
      return s.rangeCount === 1 && !s.isCollapsed && s.anchorNode?.isConnected && s.toString().length > n / 2;
    }, choisi), `${nom} · carnet : une sélection dans le bilan reste pendant trois mises à jour`);
    await page.evaluate(() => { window.getSelection().removeAllRanges(); document.querySelector('#pc-direct [data-direct-pause]').focus(); });
    await page.evaluate(() => { window.__pause = document.activeElement; });
    for (let k = 0; k < 3; k += 1) { await pilote(`/prix?AMD=${(425 + k).toFixed(2)}`); await dormir(2200); }
    ok(await page.evaluate(() => document.activeElement === window.__pause && window.__pause.isConnected),
      `${nom} · carnet : le focus reste sur Pause pendant trois mises à jour (même bouton)`);

    // ── 2. Pause, au clavier
    await page.keyboard.press('Space');
    ok(await attendreQue(async () => (await mot()) === 'en pause', 3000)
      && await page.evaluate(() => document.querySelector('#pc-direct [data-direct-pause]').getAttribute('aria-pressed') === 'true'
        && document.activeElement?.matches('[data-direct-pause]')),
    `${nom} · Pause (Espace) : « en pause », aria-pressed, le focus reste sur le bouton`);
    await dormir(2500);
    const tPause = Date.now();
    await dormir(8000);
    ok(lecturesDepuis(tPause) <= 1, `${nom} · en pause : ${lecturesDepuis(tPause)} lecture en 8 s (au plus une)`);
    await page.screenshot({ path: join(sortie, `${nom}-carnet-pause.png`) });
    const tReprise = Date.now();
    await page.keyboard.press('Space');
    ok(await attendreQue(async () => (await mot()) === 'en direct' && lecturesDepuis(tReprise) >= 1, 4000),
      `${nom} · Pause relâchée : relu tout de suite, « en direct »`);
    const t6 = Date.now();
    await dormir(6500);
    ok(lecturesDepuis(t6) >= 2, `${nom} · en direct : ${lecturesDepuis(t6)} lectures en 6,5 s (toutes les 2 s)`);
    ok(await page.evaluate(() => {
      try { return localStorage.getItem('signaux:direct-pause') === '0'; } catch { return true; }
    }), `${nom} · la pause est gardée pour la prochaine ouverture (relâchée : « 0 »)`);

    // ── axe sur la ligne du direct, à 1280 puis 390
    for (const [l, h] of [[1280, 800], [390, 844]]) {
      await page.setViewportSize({ width: l, height: h });
      await page.waitForTimeout(1200);
      const res = await new AxeBuilder({ page }).include('#pc-direct').analyze();
      ok(res.violations.length === 0, `${nom} · axe sur la ligne du carnet à ${l} px : ${res.violations.length} problème(s)`
        + (res.violations.length ? ` (${res.violations.map((v) => v.id).join(', ')})` : ''));
      const deborde = await page.evaluate(() => {
        const z = document.querySelector('#pc-direct');
        return [...z.querySelectorAll('*')].some((e) => e.getBoundingClientRect().right > z.getBoundingClientRect().right + 1);
      });
      ok(!deborde, `${nom} · rien ne dépasse de la ligne du carnet à ${l} px`);
      await page.screenshot({ path: join(sortie, `${nom}-carnet-${l}.png`) });
    }
    await page.setViewportSize({ width: 1440, height: 900 });

    if (complet) {
      // ── 3. Le faux Yahoo muet, puis qui reparle
      await pilote('/muet');
      const tMuet = Date.now();
      ok(await attendreQue(async () => (await mot()) === 'toutes les minutes', 45_000, 500),
        `${nom} · faux Yahoo muet : « toutes les minutes » en ${((Date.now() - tMuet) / 1000).toFixed(0)} s (moins de 40 s)`);
      ok((Date.now() - tMuet) < 40_000, `${nom} · … dans le délai`);
      await dormir(2500);
      const tLent = Date.now();
      await dormir(12_000);
      ok(lecturesDepuis(tLent) <= 1, `${nom} · … puis ${lecturesDepuis(tLent)} lecture en 12 s (une par minute au plus)`);
      await pilote('/parle');
      ok(await attendreQue(async () => (await mot()) === 'en direct', 75_000, 500),
        `${nom} · il reparle : « en direct » au tour suivant (moins d'une minute)`);

      // ── 4. Un serveur qui ne répond plus
      await page.route('**/api/journal', async (route) => { await dormir(10_000); route.continue().catch(() => {}); });
      const tBloque = Date.now();
      ok(await attendreQue(async () => (await mot()) !== 'en direct', 9000, 200) && (Date.now() - tBloque) < 8000,
        `${nom} · serveur bloqué : « en direct » disparaît en ${((Date.now() - tBloque) / 1000).toFixed(1)} s (moins de 8 s)`);
      ok(await page.evaluate(() => /Dernier rafraîchissement impossible/.test(document.querySelector('#pc-direct').textContent)),
        `${nom} · … et la ligne dit que les chiffres datent du tour précédent`);
      await page.unroute('**/api/journal');
      ok(await attendreQue(async () => (await mot()) === 'en direct', 75_000, 500), `${nom} · le serveur revient : « en direct »`);

      // ── 5. Un ancien serveur
      const vieux = await contexte.newPage();
      vieux.on('pageerror', (e) => erreurs.push(String(e)));
      const vieillesLectures = [];
      await vieux.route('**/api/journal', async (route) => {
        vieillesLectures.push(Date.now());
        const rep = await route.fetch();
        const json = await rep.json();
        delete json.data.position;
        await route.fulfill({ response: rep, json });
      });
      await vieux.route('**/api/position', async (route) => {
        const rep = await route.fetch();
        const json = await rep.json();
        delete json.data.direct;
        await route.fulfill({ response: rep, json });
      });
      await vieux.route('**/api/valeurs/direct', (route) => route.fulfill({ status: 404, body: 'introuvable' }));
      await vieux.goto(`${BASE}/?plan&vue=carnet`);
      await attendreQue(() => vieux.evaluate(() => Boolean(document.querySelector('#pc-direct .pc-direct-valeur'))), 20_000);
      const tVieux = Date.now();
      await dormir(7000);
      ok(await vieux.evaluate(() => !document.querySelector('#pc-direct .pc-direct-mode') && !document.querySelector('[data-direct-pause]:not([hidden])')),
        `${nom} · ancien serveur : ni mot du direct ni bouton Pause`);
      ok(vieillesLectures.filter((x) => x >= tVieux).length <= 1, `${nom} · ancien serveur : relu toutes les 60 s`);
      await vieux.close();
    }

    // ── 6. La vue plan
    await page.goto(`${BASE}/?plan`);
    ok(await attendreQue(() => page.evaluate(() => document.querySelector('#direct-etat')?.textContent === 'En direct'
      && !document.querySelector('#direct-pause').hidden), 20_000), `${nom} · vue plan : « En direct » et Pause dans l'en-tête`);
    ok(await attendreQue(() => page.evaluate(() => /^Cours de \d\d:\d\d, en direct\.$/.test(document.querySelector('#positions .position-direct')?.textContent || '')), 10_000),
      `${nom} · vue plan : « Cours de HH:MM, en direct. » sous Ma position`);
    ok(await attendreQue(() => page.evaluate(() => document.querySelectorAll('#stocks-body tr[data-direct-ticker]').length >= 2), 60_000),
      `${nom} · vue plan : le tableau des 49 est là`);
    // Les quatre cases, avec des valeurs fabriquées, changées EN PLACE.
    const tickers = await page.evaluate(() => [...document.querySelectorAll('#stocks-body tr[data-direct-ticker]')].slice(0, 2).map((tr) => tr.dataset.directTicker));
    await page.evaluate((t) => { window.__cases = [...document.querySelector(`#stocks-body tr[data-direct-ticker="${t}"]`).querySelectorAll('td[data-direct]')]; }, tickers[0]);
    let fabrique = { en_direct: true, rapide: true, etat: 'ouvert', raison: null, scan_de: new Date().toISOString(),
      valeurs: { [tickers[0]]: { price: 123.45, change_1d: 1.23, change_1w: -4.56, change_1m: 7.89, heure: new Date().toISOString() },
        [tickers[1]]: { price: 9876.5, change_1d: -0.04, change_1w: 0, change_1m: 12.34, heure: new Date().toISOString() } } };
    await page.route('**/api/valeurs/direct', (route) => route.fulfill({ json: { ok: true, data: fabrique } }));
    // Les espaces comparées une à une : le français groupe les milliers par une espace fine insécable.
    const casesDe = (t) => page.evaluate((x) => [...document.querySelector(`#stocks-body tr[data-direct-ticker="${x}"]`).querySelectorAll('td[data-direct]')]
      .map((td) => `${td.textContent.replace(/[\s\u00a0\u202f]+/g, ' ')}|${td.className}`), t);
    ok(await attendreQue(async () => JSON.stringify(await casesDe(tickers[0])) === JSON.stringify(
      ['123,45 $|num-cell num', '+1,2 %|num-cell num up', '-4,6 %|num-cell num down', '+7,9 %|num-cell num up']), 6000),
    `${nom} · tableau : les quatre cases de ${tickers[0]} montrent le direct (123,45 $ ; +1,2 % ; -4,6 % ; +7,9 %), avec leur couleur`);
    ok(JSON.stringify(await casesDe(tickers[1])) === JSON.stringify(
      ['9 876,50 $|num-cell num', '-0,0 %|num-cell num down', '+0,0 %|num-cell num flat', '+12,3 %|num-cell num up']),
    `${nom} · tableau : celles de ${tickers[1]} aussi (9 876,50 $ ; -0,0 % ; +0,0 % ; +12,3 %)`);
    ok(await page.evaluate((t) => {
      const maintenant = [...document.querySelector(`#stocks-body tr[data-direct-ticker="${t}"]`).querySelectorAll('td[data-direct]')];
      return maintenant.length === 4 && maintenant.every((td, i) => td === window.__cases[i]);
    }, tickers[0]), `${nom} · tableau : les cases ont changé EN PLACE (les mêmes nœuds)`);
    ok(await page.evaluate(() => /^Cours et variations en direct ; les autres colonnes au scan de \d\d:\d\d\.$/.test(document.querySelector('#valeurs-direct-note')?.textContent || '')
      && !document.querySelector('#valeurs-direct-note').hidden), `${nom} · tableau : la ligne « Cours et variations en direct ; … au scan de HH:MM. »`);
    await page.screenshot({ path: join(sortie, `${nom}-plan-direct.png`) });
    fabrique = { ...fabrique, en_direct: false, rapide: false, valeurs: {} };
    // Les chiffres du scan que la page garde (lastStocks) : un nouveau scan a pu arriver entre-temps (vrais cours).
    const duScan = (t) => page.evaluate((x) => {
      const r = lastStocks.rows.find((l) => l.ticker === x);
      return [`${nf(r.price)} $`, pct(r.change_1d), pct(r.change_1w), pct(r.change_1m)].map((s) => s.replace(/[\s\u00a0\u202f]+/g, ' '));
    }, t);
    ok(await attendreQue(async () => {
      const vus = (await casesDe(tickers[0])).map((x) => x.split('|')[0]);
      return !vus.includes('123,45 $') && JSON.stringify(vus) === JSON.stringify(await duScan(tickers[0]));
    }, 70_000, 500), `${nom} · tableau : plus de direct, les cases reviennent aux chiffres du scan`);
    ok(await page.evaluate(() => document.querySelector('#valeurs-direct-note').hidden), `${nom} · tableau : la ligne du direct disparaît`);
    await page.unroute('**/api/valeurs/direct');

    // Pause dans l'en-tête, au clavier (Tab dans Chrome ; WebKit saute les boutons sans le réglage du Mac).
    if (nom === 'chrome') {
      await page.evaluate(() => document.activeElement?.blur());
      let trouve = false;
      for (let k = 0; k < 25 && !trouve; k += 1) {
        await page.keyboard.press('Tab');
        trouve = await page.evaluate(() => document.activeElement?.id === 'direct-pause');
      }
      ok(trouve, `${nom} · en-tête : Pause se prend au clavier (Tab)`);
    } else {
      await page.focus('#direct-pause');
    }
    await page.keyboard.press('Space');
    ok(await attendreQue(() => page.evaluate(() => document.querySelector('#direct-etat').textContent === 'En pause'
      && document.querySelector('#direct-pause').getAttribute('aria-pressed') === 'true'
      && /, en pause\.$/.test(document.querySelector('#positions .position-direct').textContent)), 3000),
    `${nom} · en-tête : Pause (Espace) → « En pause », aria-pressed, « …, en pause. »`);
    await page.keyboard.press('Space');
    ok(await attendreQue(() => page.evaluate(() => document.querySelector('#direct-etat').textContent === 'En direct'), 4000),
      `${nom} · en-tête : relâchée → « En direct »`);
    for (const [l, h] of [[1280, 800], [390, 844]]) {
      await page.setViewportSize({ width: l, height: h });
      await page.waitForTimeout(800);
      const res = await new AxeBuilder({ page }).include('.masthead').include('#section-positions').include('#section-valeurs').analyze();
      ok(res.violations.length === 0, `${nom} · axe sur l'en-tête, Ma position et le tableau à ${l} px : ${res.violations.length} problème(s)`
        + (res.violations.length ? ` (${res.violations.map((v) => `${v.id} ${v.nodes.length}`).join(', ')})` : ''));
      await page.screenshot({ path: join(sortie, `${nom}-plan-${l}.png`) });
    }
    ok(erreurs.length === 0, `${nom} · aucune erreur de console${erreurs.length ? ` (${erreurs.slice(0, 3).join(' | ')})` : ''}`);
  } finally {
    await navigateur.close();
  }
}

try {
  await verifier('chrome', chromium, { channel: 'chrome' }, true);
  await verifier('webkit', webkit, {}, false);
} finally {
  await fermer();
  ok(await libre(PORT) && await libre(8790), 'le serveur d\'essai et le faux Yahoo sont fermés');
  writeFileSync(join(sortie, 'serveur.log'), srv.journal());
  const non = lignes.filter((l) => l.startsWith('NON')).length;
  writeFileSync(join(sortie, 'rapport.txt'), lignes.join('\n') + '\n');
  console.log(`\n${lignes.length - non} ok, ${non} NON — captures et rapport dans ${sortie}`);
  process.exitCode = non ? 1 : 0;
}

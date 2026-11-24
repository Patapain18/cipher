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
  const sortieOrdres = [];
  let hors = 0;
  for (const d of decisions) {
    if (d.ticker !== g.ticker || !(Number(d.prix_eur) > 0)) continue;
    const ms = Date.parse(d.date_utc || d.date);
    if (!Number.isFinite(ms) || dateNY(ms) < premier) continue;
    const { semaine, minutes } = heureNY(ms);
    const seance = !['Sat', 'Sun'].includes(semaine) && minutes >= 570 && minutes < 960;
    if (!seance) hors += 1;
    let barre;
    if (g.periode === '1a') {
      const jour = dateNY(ms);
      const apres = !seance && minutes >= 960;
      barre = barres.find((b) => (apres ? dateNY(b * 1000) > jour : dateNY(b * 1000) >= jour));
    } else {
      const q = Math.floor(ms / 1000);
      barre = barres.find((b) => b <= q && q < b + DUREE[g.periode]) ?? barres.find((b) => b > q);
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
      const zoom = await page.evaluate(() => ({ idx: PCGraphe.essai().idx, plage: PCGraphe.essai().chart.timeScale().getVisibleLogicalRange(),
        nTotal: PCGraphe.essai().nTotal, curseur: document.getElementById('pc-curseur').textContent }));
      ok(zoom.plage.to - zoom.plage.from < avant.plage.to - avant.plage.from - 1 && zoom.idx === avant.idx,
        `${nom} : + zoome et le curseur garde sa barre`);
      ok(zoom.plage.to <= zoom.nTotal + 0.5, `${nom} : pas de vide après la fin de la courbe au zoom`);
      const ligne = await page.evaluate(({ i }) => {
        const e = PCGraphe.essai(); const b = e.barres[i];
        return `${PCGraphe.outils.texteTemps(b.time, '1a', true)} · ${PP.euros(b.value)}`;
      }, { i: avant.idx });
      ok(zoom.curseur.startsWith(ligne), `${nom} : la ligne du curseur écrit la barre lue (${zoom.curseur})`);
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
        const lire = () => m.page.evaluate(() => ({ y: scrollY, plage: PCGraphe.essai().chart.timeScale().getVisibleLogicalRange() }));
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
        ok(b1.y === b0.y && Math.abs(b1.plage.to - b1.plage.from - (b0.plage.to - b0.plage.from)) > 0.5,
          `${nom} : après un clic, la molette zoome et le carnet ne défile pas`);
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
      const { barresDe, avecLeReste, placerOrdres, texteTemps, enSeance } = PCGraphe.outils;
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
    ok(r.mars27 === '14:30' && r.mars30 === '15:30' && r.oct30 === '14:30' && r.nov03 === '15:30',
      `calcul : l'heure de Paris autour des changements d'heure (${r.mars27}, ${r.mars30}, ${r.oct30}, ${r.nov03})`);
    ok(JSON.stringify(r.seances) === '[true,false,true,false,false]', 'calcul : la séance de New York, à ses deux bouts et le samedi');
    ok(JSON.stringify(r.barres) === '["0:1","300:3"]', 'calcul : barres triées, doublon (le dernier gagne), prix nul ou illisible retirés');
    ok(JSON.stringify(r.an) === '["2026-11-24T12:00:00.000Z"]', 'calcul 1 an : une barre à midi UTC du jour de New York');
    ok(r.vides.monte && r.vides.premiereVide === 900 && r.vides.derniere === 6.5 * 3600 - 300 && r.vides.n === 2 + (6.5 * 3600 - 900) / 300,
      'calcul : les cases vides de la séance, après une barre hors grille, jusqu\'à 15 h 55 à New York');
    ok(r.videsAutres === 2, 'calcul : pas de cases vides hors de 1 jour');
    await contexte.close();
  }
  await navigateur.close();
}

const non = lignes.filter((l) => l.startsWith('NON')).length;
const texte = `${lignes.join('\n')}\n\n${lignes.length - non} sur ${lignes.length} contrôles passent${non ? ` — ${non} NON` : ''}.`;
writeFileSync(join(sortie, 'rapport.txt'), texte);
console.log(texte);
process.exit(non ? 1 : 0);

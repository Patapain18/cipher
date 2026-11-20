// Le verdict du banc (#section-banc) : à lancer avec le serveur d'essai `signaux-essai` (port 8779,
// voir CLAUDE.md « Vérifier »). Usage : node outils/verif_verdict_banc.mjs <dossier de sortie hors du projet>
// La vraie section « Le banc des prévisions » (#section-banc), sur signaux-essai :
// 1. Chrome et WebKit, 4 tailles, détail fermé PUIS ouvert : captures, console,
//    débordement, axe sur la section (1280 et 390), chaque chiffre et chaque date
//    retrouvés dans les rapports écrits du banc ;
// 2. des réponses fabriquées (ancien serveur, panne, mesure absente ou abîmée, une
//    méthode qui passerait, toutes ruinées) : la section dit la bonne chose, sans erreur ;
// 3. le clavier (Entrée et Espace sur le détail) et le rafraîchissement qui garde
//    le détail ouvert.
// Usage : node section_banc.mjs <sortie>
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(`${homedir()}/.claude/skills/verif/outil/`);
const { chromium, webkit } = require('playwright');
const { AxeBuilder } = require('@axe-core/playwright');

const sortie = process.argv[2];
mkdirSync(sortie, { recursive: true });
const BASE = 'http://localhost:8779';
const TEL = { deviceScaleFactor: 2, isMobile: true, hasTouch: true };
const TAILLES = {
  ordi: { viewport: { width: 1440, height: 900 } },
  'ordi-petit': { viewport: { width: 1280, height: 800 } },
  tel: { viewport: { width: 390, height: 844 }, ...TEL },
  'tel-petit': { viewport: { width: 360, height: 780 }, ...TEL },
  etroit: { viewport: { width: 320, height: 700 }, ...TEL },
};
const racine = fileURLToPath(new URL('../data/banc/', import.meta.url));
const rapports = ['etape1.json', 'critere2.json'].map((f) => JSON.parse(readFileSync(racine + f, 'utf8')).rapport).join('\n');
const norm = (s) => s.replace(/[   ]/g, '');
const nombresRapport = new Set(norm(rapports).match(/\d+,\d+/g) || []);
const datesRapport = new Set(rapports.match(/\d{2}\/\d{2}\/\d{4}/g) || []);

const lignes = [];
const ok = (cond, quoi) => { lignes.push(`${cond ? 'ok ' : 'NON'} ${quoi}`); return cond; };

async function ouvrir(navigateur, config, route) {
  const contexte = await navigateur.newContext(config);
  const page = await contexte.newPage();
  const erreurs = [];
  page.on('console', (m) => { if (m.type() === 'error') erreurs.push(m.text()); });
  page.on('pageerror', (e) => erreurs.push(String(e)));
  if (route) await page.route('**/api/banc', route);
  await page.goto(`${BASE}/?plan`, { waitUntil: 'load' });
  return { contexte, page, erreurs };
}

const verifierTexte = (texte, ou) => {
  const t = norm(texte);
  const inconnus = (t.match(/\d+,\d+/g) || []).filter((n) => !nombresRapport.has(n));
  const dates = (texte.match(/\d{2}\/\d{2}\/\d{4}/g) || []).filter((d) => !datesRapport.has(d));
  ok(!inconnus.length && !dates.length, `${ou} : chiffres et dates retrouvés dans les rapports${inconnus.length || dates.length ? ` (inconnus : ${[...inconnus, ...dates].join(' ')})` : ''}`);
};

// 1. Les vraies données
for (const moteur of ['chromium', 'webkit']) {
  const navigateur = await (moteur === 'webkit' ? webkit.launch() : chromium.launch({ channel: 'chrome' }));
  try {
    for (const [taille, config] of Object.entries(TAILLES)) {
      const { contexte, page, erreurs } = await ouvrir(navigateur, config);
      await page.waitForSelector('#banc .banc-avertissement', { timeout: 15000 });
      await page.waitForTimeout(1500);
      const section = page.locator('#section-banc');
      for (const etat of ['ferme', 'ouvert']) {
        if (etat === 'ouvert') {
          await page.evaluate(() => { document.querySelector('#banc details').open = true; });
          await page.waitForTimeout(300);
        }
        await section.scrollIntoViewIfNeeded();
        await section.screenshot({ path: join(sortie, `${moteur}-${taille}-${etat}.png`) });
        const m = await page.evaluate(() => {
          const s = document.querySelector('#section-banc');
          const r = s.getBoundingClientRect();
          const carte = document.querySelector('#banc');
          const cs = getComputedStyle(carte);
          const cr = carte.getBoundingClientRect();
          // Dans la carte : rien ne sort de son contenu (padding compris) ; le cadre du
          // tableau est vérifié, pas ce qui défile dedans.
          const gauche = cr.left + parseFloat(cs.paddingLeft) - 1;
          const droite = cr.right - parseFloat(cs.paddingRight) + 1;
          const dehors = [];
          for (const el of carte.querySelectorAll('*')) {
            if (el.parentElement && el.parentElement.closest('.banc-table')) continue;
            const b = el.getBoundingClientRect();
            if (b.width && (b.right > droite || b.left < gauche)) dehors.push(`${el.tagName}.${el.className} +${Math.round(b.right - droite)}`);
          }
          for (const el of s.querySelectorAll('.section-head *')) {
            const b = el.getBoundingClientRect();
            if (b.width && (b.right > r.right + 1 || b.left < r.left - 1)) dehors.push(`${el.tagName}.${el.className}`);
          }
          const cadre = s.querySelector('.banc-table');
          return { h: Math.round(r.height), dehors: dehors.slice(0, 4), texte: s.innerText,
            defile: cadre && cadre.offsetParent ? cadre.scrollWidth > cadre.clientWidth : null };
        });
        const ou = `${moteur} ${taille} ${etat}`;
        ok(!m.dehors.length, `${ou} : rien ne dépasse de la section (h ${m.h} px${m.defile ? ', le tableau défile dans son cadre' : ''})${m.dehors.length ? ' — ' + m.dehors.join(', ') : ''}`);
        if (etat === 'ouvert' && taille === 'ordi') verifierTexte(m.texte, ou);
        if (taille === 'ordi-petit' || taille === 'tel') {
          const res = await new AxeBuilder({ page }).include('#section-banc').analyze();
          const v = res.violations.map((x) => `${x.impact} ${x.id} (${x.nodes.length})`);
          ok(!v.length, `${ou} : axe sur la section, ${v.length ? v.join(', ') : '0 problème'}`);
        }
      }
      ok(!erreurs.length, `${moteur} ${taille} : console sans erreur${erreurs.length ? ' — ' + erreurs.join(' | ').slice(0, 300) : ''}`);
      await contexte.close();
    }

    // 3. Le clavier, et le rafraîchissement qui garde le détail ouvert.
    {
      const { contexte, page, erreurs } = await ouvrir(navigateur, TAILLES['ordi-petit']);
      await page.waitForSelector('#banc .banc-avertissement', { timeout: 15000 });
      await page.focus('#banc summary');
      await page.keyboard.press('Enter');
      const apresEntree = await page.evaluate(() => document.querySelector('#banc details').open);
      await page.keyboard.press('Space');
      const apresEspace = await page.evaluate(() => document.querySelector('#banc details').open);
      ok(apresEntree === true && apresEspace === false, `${moteur} : Entrée ouvre le détail, Espace le referme`);
      const contour = await page.evaluate(() => getComputedStyle(document.querySelector('#banc summary')).outlineStyle);
      ok(contour !== 'none', `${moteur} : le focus du détail se voit (contour ${contour})`);
      await page.keyboard.press('Enter');
      await page.evaluate(() => renderBanc());
      await page.waitForTimeout(500);
      const garde = await page.evaluate(() => document.querySelector('#banc details').open
        && document.activeElement === document.querySelector('#banc summary'));
      ok(garde, `${moteur} : un rafraîchissement (même contenu) garde le détail ouvert et le focus`);
      await page.evaluate(() => { bancDernierHtml = 'autre chose'; return renderBanc(); });
      await page.waitForTimeout(500);
      const garde2 = await page.evaluate(() => document.querySelector('#banc details').open
        && document.activeElement === document.querySelector('#banc summary'));
      ok(garde2, `${moteur} : un contenu redessiné rouvre le détail et rend le focus`);
      ok(!erreurs.length, `${moteur} clavier : console sans erreur${erreurs.length ? ' — ' + erreurs.join(' | ') : ''}`);
      await contexte.close();
    }
  } finally {
    await navigateur.close();
  }
}

// 2. Les réponses fabriquées (Chrome, 1280).
const vraie = await (await fetch(`${BASE}/api/banc`)).json();
const copie = () => JSON.parse(JSON.stringify(vraie.data));
const json = (data, status = 200) => (route) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
const cas = [
  ['ancien serveur (404)', (route) => route.fulfill({ status: 404, contentType: 'text/html', body: '<h1>404</h1>' }), 'quand l\'appli sera relancée'],
  ['panne du serveur', json({ ok: false, error: 'boum' }, 500), 'n\'est pas lisible pour l\'instant (boum)'],
  ['critère 1 absent', json({ ok: true, data: { ...copie(), critere1: { etat: 'absent' } } }), 'Le critère 1 : pas encore mesuré (data/banc/etape1.json n\'existe pas).'],
  ['critère 2 illisible', json({ ok: true, data: { ...copie(), critere2: { etat: 'illisible', detail: 'KeyError' } } }), 'Le critère 2 : le fichier data/banc/critere2.json est illisible.'],
  ['b passe le critère 1', json({ ok: true, data: (() => {
    const d = copie();
    d.critere1.passent = ['b']; d.critere1.porte_a = true;
    d.critere1.methodes.find((m) => m.cle === 'b').passe = true;
    return d;
  })() }), ['1 sur 4 passe le critère 1 ; aucune ne passe le critère 2', 'La porte A s\'ouvre', '1 sur 4 passe', 'Ce test a une limite']],
  ['b passe le critère 2', json({ ok: true, data: (() => {
    const d = copie();
    d.critere2.passent = ['b'];
    for (const h of ['soir', 'matin']) {
      const b = d.critere2.heures[h].methodes.b;
      b.passe = true; b.final = d.critere2.heures[h].barre + 50;
    }
    return d;
  })() }), ['Aucune ne passe le critère 1 ; 1 sur 4 passe le critère 2', '1 sur 4 passe',
    'b (le momentum 12-1) finit à 750,72 € en achetant le soir et 746,46 € le matin ; il lui fallait plus de 700,72 € et 696,46 € (le panier des 49 gardé) : elle passe.',
    'b a gardé NVDA 674 soirs']],
  ['a arrêtée le soir seulement, 1 soir, barre de SPY', json({ ok: true, data: (() => {
    const d = copie();
    const am = d.critere2.heures.matin.methodes.a;
    am.arret = null; am.capital_arret = null; am.final = 6.19;
    for (const h of ['soir', 'matin']) d.critere2.heures[h].barre = d.critere2.heures[h].spy;
    d.critere2.survivant.b.soirs = 1;
    return d;
  })() }), ['a (le score technique) tombe à 2 € ou moins en achetant le soir, le 14/09/2020, et finit à 6,19 € en achetant le matin ; il lui fallait plus de 466,13 € et 464,59 € (SPY gardé).',
    'Deux sont ruinées par les frais : c et d tombent à 2 € ou moins entre avril et mai 2020, le soir comme le matin.',
    'b a gardé NVDA 1 soir (+302,6 % sur ce soir-là, avant les frais)']],
  ['toutes ruinées', json({ ok: true, data: (() => {
    const d = copie();
    for (const h of ['soir', 'matin']) {
      const b = d.critere2.heures[h].methodes.b;
      b.arret = h === 'soir' ? '2021-01-15' : '2021-02-03'; b.capital_arret = 1.5;
    }
    return d;
  })() }), ['Quatre sont ruinées par les frais : a, b, c et d tombent à 2 € ou moins entre avril 2020 et février 2021, le soir comme le matin.',
    'une méthode peut l\'être aussi. Le coffre']],
];
const navigateur = await chromium.launch({ channel: 'chrome' });
try {
  for (const [nom, route, attendus] of cas) {
    const { contexte, page, erreurs } = await ouvrir(navigateur, TAILLES['ordi-petit'], route);
    await page.waitForFunction(() => !document.querySelector('#banc').textContent.includes('Lecture du banc'), null, { timeout: 15000 });
    await page.waitForTimeout(500);
    const texte = (await page.locator('#banc').innerText()).replace(/\s+/g, ' ');
    const manque = [].concat(attendus).filter((a) => !texte.includes(a));
    ok(!manque.length, `fabriqué, ${nom} : ${manque.length ? 'MANQUE « ' + manque.join(' » « ') + ' » — lu : ' + texte.slice(0, 220) : 'la section le dit'}`);
    const sansErreur = erreurs.filter((e) => !e.includes('404') && !e.includes('500'));
    ok(!sansErreur.length, `fabriqué, ${nom} : console sans erreur de script${sansErreur.length ? ' — ' + sansErreur.join(' | ') : ''}`);
    if (nom === 'ancien serveur (404)') {
      const pastille = await page.$eval('#banc-date', (e) => getComputedStyle(e).display);
      ok(pastille === 'none', `fabriqué, ${nom} : pas de pastille de date vide (display ${pastille})`);
    }
    if (nom === 'b passe le critère 1') {
      const allumees = await page.$$eval('#banc .banc-echelle i.on', (l) => l.length);
      ok(allumees === 1, `fabriqué, ${nom} : une case allumée sur l'échelle (${allumees})`);
      await page.locator('#section-banc').screenshot({ path: join(sortie, 'fabrique-b-passe.png') });
    }
    await contexte.close();
  }
  // Une panne APRÈS une première lecture : la dernière lecture reste.
  {
    let panne = false;
    const { contexte, page } = await ouvrir(navigateur, TAILLES['ordi-petit'],
      (route) => (panne ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"ok":false,"error":"boum"}' }) : route.continue()));
    await page.waitForSelector('#banc .banc-avertissement', { timeout: 15000 });
    panne = true;
    await page.evaluate(() => renderBanc());
    await page.waitForTimeout(500);
    ok(await page.$('#banc .banc-avertissement') !== null, 'une panne après une première lecture : la dernière lecture reste affichée');
    await contexte.close();
  }
} finally {
  await navigateur.close();
}
writeFileSync(join(sortie, 'bilan.txt'), lignes.join('\n'));
console.log(lignes.join('\n'));
console.log(`\n${lignes.filter((l) => l.startsWith('NON')).length} NON sur ${lignes.length}`);

// Le panneau 5 de la vue baie avec le verdict du banc : à lancer avec le serveur d'essai `signaux-essai`
// (port 8779). Usage : node outils/verif_verdict_baie.mjs <dossier de sortie hors du projet>
// Le panneau 5 avec la vraie section : 1440 × 900 et 1200 × 690 (taille minimale de la vue baie).
// Le détail ne doit pas s'y voir ; la fin de la section, ni passer sous la note du bas, ni sortir de l'écran ;
// Tab ne parcourt que ce qui se voit ; l'écart vitre/HTML reste minuscule.
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
const require = createRequire(`${homedir()}/.claude/skills/verif/outil/`);
const { chromium } = require('playwright');
const sortie = process.argv[2] || '.';
const n = await chromium.launch({ channel: 'chrome', args: ['--use-angle=metal', '--ignore-gpu-blocklist'] });
let non = 0;
const ok = (c, q) => { if (!c) non++; console.log(`${c ? 'ok ' : 'NON'} ${q}`); };
try {
  // 1440 × 900 ; les écrans des MacBook (Air 13 et 15 pouces, Pro 14 et 16) ; 1200 × 690, la plus petite vue baie.
  // Une fenêtre large et basse (1512 × 800, 1440 × 690…) déborde encore : connu, voir CLAUDE.md.
  for (const [w, h] of [[1440, 900], [1470, 956], [1440, 932], [1512, 982], [1728, 1117], [1200, 690]]) {
    const c = await n.newContext({ viewport: { width: w, height: h } });
    const p = await c.newPage();
    const erreurs = [];
    p.on('console', (m) => { if (m.type() === 'error') erreurs.push(m.text()); });
    p.on('pageerror', (e) => erreurs.push(String(e)));
    await p.goto('http://localhost:8779/?debug', { waitUntil: 'load' });
    await p.waitForFunction(() => window.__vueBaie && window.__vueBaie.etat.ouverte, null, { timeout: 15000 });
    await p.waitForSelector('#banc .banc-avertissement', { timeout: 15000 });
    await p.waitForTimeout(6000);
    // Le détail ouvert dans la vue plan AVANT : il ne doit pas réapparaître dans le panneau.
    await p.evaluate(() => { document.querySelector('#banc details').open = true; });
    await p.evaluate(() => window.__vueBaie.aller(4, true));
    await p.waitForTimeout(2500);
    const m = await p.evaluate(() => {
      const contenu = document.querySelector('.vue3d-panneau[data-panneau="historique"] .vue3d-contenu');
      const r = contenu.getBoundingClientRect();
      const fin = document.querySelector('#section-banc .banc-avertissement').getBoundingClientRect();
      const note = document.querySelector('.vue3d-note').getBoundingClientRect();
      const points = document.querySelector('.vue3d-points').getBoundingClientRect();
      const detail = document.querySelector('#banc details');
      return { haut: Math.round(r.top), bas: Math.round(r.bottom), finBas: Math.round(fin.bottom),
        noteHaut: Math.round(note.top), pointsHaut: Math.round(points.top), noteGauche: Math.round(note.left), noteDroite: Math.round(note.right),
        finGauche: Math.round(fin.left), detailVisible: detail.offsetParent !== null, ecart: window.__vueBaie.ecart().max,
        hPx: contenu.offsetHeight };
    });
    const ou = `${w}×${h}`;
    ok(!m.detailVisible, `${ou} : le détail n'est pas proposé dans le panneau (panneau ${m.hPx} px de haut)`);
    ok(m.ecart < 0.05, `${ou} : écart vitre/HTML ${m.ecart} px`);
    // Le haut du panneau est sa marge intérieure (26 px, soit 16 à 20 px à l'écran) : on tolère ce qu'elle mesure.
    ok(m.haut >= -20 && m.bas <= h + 20, `${ou} : le panneau tient dans la fenêtre, à sa marge près (${m.haut} → ${m.bas})`);
    const sousLaNote = m.finBas > Math.min(m.noteHaut, m.pointsHaut) && m.finGauche < m.noteDroite;
    ok(!sousLaNote, `${ou} : la dernière ligne (bas à ${m.finBas}) ne passe pas sous la note (${m.noteHaut}) ni les points (${m.pointsHaut})`);
    // Tab dans le panneau : seulement ce qui se voit.
    const vus = [];
    for (let i = 0; i < 6; i++) {
      await p.keyboard.press('Tab');
      vus.push(await p.evaluate(() => { const a = document.activeElement; return `${a.tagName.toLowerCase()}${a.id ? '#' + a.id : ''}${a.offsetParent === null ? ' (caché !)' : ''}`; }));
    }
    ok(!vus.some((v) => v.includes('caché')), `${ou} : Tab ne va que sur ce qui se voit (${[...new Set(vus)].join(', ')})`);
    await p.screenshot({ path: join(sortie, `baie2-${w}x${h}.png`) });
    // Échap : retour à la vue plan, le détail est toujours là et ouvert.
    await p.evaluate(() => document.activeElement && document.activeElement.blur && document.activeElement.blur());
    await p.keyboard.press('Escape');
    await p.waitForTimeout(2500);
    const plan = await p.evaluate(() => ({ ouverte: window.__vueBaie.etat.ouverte, detail: document.querySelector('#banc details').open && document.querySelector('#banc details').offsetParent !== null }));
    ok(!plan.ouverte && plan.detail, `${ou} : Échap rend la vue plan, avec le détail ouvert et visible (${JSON.stringify(plan)})`);
    ok(!erreurs.length, `${ou} : console sans erreur${erreurs.length ? ' — ' + erreurs.join(' | ').slice(0, 200) : ''}`);
    await c.close();
  }
} finally { await n.close(); }
console.log(`${non} NON`);

// Le thermomètre pendant une panne de Yahoo (plan de la « v finale », 27/11/2026).
// Le serveur rend alors sa dernière lecture réussie avec son heure (champ `secours`) ; ici, le champ
// est ajouté par interception à la vraie réponse du serveur d'essai. On vérifie que la page montre le
// thermomètre (jamais « indisponible ») et dit l'heure de la lecture, sans phrase quand tout va bien ;
// axe sur la section ; la phrase tient dans un téléphone de 360 px. Chrome, WebKit et Firefox.
//
//   node outils/verif_thermometre_panne.mjs        (le serveur d'essai `signaux-essai`, port 8779, lancé)
import { createRequire } from 'node:module';
import { homedir } from 'node:os';

const require = createRequire(`${homedir()}/.claude/skills/verif/outil/`);
const { chromium, webkit, firefox } = require('playwright');
const { AxeBuilder } = require('@axe-core/playwright');

const BASE = 'http://localhost:8779';
const LUE_LE = '2026-11-27T13:05:00+00:00';                         // 14:05 à Paris
const RAISON = 'aucun cours reçu pour ^GSPC (Yahoo refuse)';
const PHRASE = `Mise à jour impossible pour l'instant (${RAISON}) : la lecture affichée date de 14:05.`;
const MOTEURS = { chrome: () => chromium.launch({ channel: 'chrome' }), webkit: () => webkit.launch(), firefox: () => firefox.launch() };
const TAILLES = { ordi: { viewport: { width: 1440, height: 900 } }, tel: { viewport: { width: 360, height: 780 } } };
const cas = [];
const ok = (c, nom) => { cas.push(Boolean(c)); console.log(`${c ? 'ok ' : 'NON'} ${nom}`); };

for (const [moteur, lancer] of Object.entries(MOTEURS)) {
  const navigateur = await lancer();
  for (const [taille, config] of Object.entries(TAILLES)) {
    for (const panne of (taille === 'ordi' ? [true, false] : [true])) {
      const contexte = await navigateur.newContext(config);
      const page = await contexte.newPage();
      const erreurs = [];
      page.on('pageerror', (e) => erreurs.push(e.message));
      await page.route('**/api/thermometre', async (route) => {
        const reponse = await route.fetch();
        const j = await reponse.json();
        if (panne && j.data) j.data.secours = { lue_le: LUE_LE, raison: RAISON };
        return route.fulfill({ response: reponse, json: j });
      });
      await page.goto(`${BASE}/?plan`, { waitUntil: 'domcontentloaded' });
      const vu = await page.waitForFunction(() => document.querySelector('#thermometre .thermo-niveau'), null, { timeout: 120000 })
        .then(() => true, () => false);
      const e = await page.evaluate(() => {
        const etat = document.getElementById('thermo-etat');
        return { texte: etat?.textContent || '', zone: document.getElementById('thermometre')?.textContent || '',
                 deborde: etat ? etat.scrollWidth > etat.clientWidth + 1 : true };
      });
      const nom = `${moteur} ${taille}`;
      if (panne) {
        ok(vu && e.texte === PHRASE && !/indisponible/.test(e.zone),
          `${nom} · Yahoo en panne, page rechargée : le thermomètre est là, et la phrase dit l'heure de la lecture`);
        ok(!e.deborde, `${nom} · la phrase tient dans sa ligne (aucun débordement)`);
        await page.evaluate(() => document.getElementById('section-thermometre').scrollIntoView());
        await page.waitForTimeout(1200);
        const axe = await new AxeBuilder({ page }).include('#section-thermometre').analyze();
        const graves = axe.violations.filter((v) => ['critical', 'serious'].includes(v.impact));
        ok(graves.length === 0, `${nom} · axe sur la section pendant la panne : ${graves.length} problème(s) critique(s) ou sérieux`
          + (graves.length ? ` (${graves.map((v) => v.id).join(', ')})` : ''));
      } else {
        ok(vu && e.texte === '', `${nom} · sans panne : le thermomètre, et aucune phrase`);
      }
      ok(erreurs.length === 0, `${nom} · aucune erreur de script${erreurs.length ? ` : ${erreurs.join(' | ')}` : ''}`);
      await contexte.close();
    }
  }
  await navigateur.close();
}
console.log(`\n${cas.filter(Boolean).length} sur ${cas.length} contrôles passent.`);
process.exit(cas.every(Boolean) ? 0 : 1);

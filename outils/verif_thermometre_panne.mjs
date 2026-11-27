// Le thermomètre pendant une panne de Yahoo (plan de la « v finale », 27/11/2026).
// Le serveur rend alors sa dernière lecture réussie avec son heure (champ `secours`) ; ici, le champ
// est ajouté par interception à la vraie réponse du serveur d'essai. On vérifie que la page montre le
// thermomètre (jamais « indisponible ») et dit l'heure de la lecture (le jour aussi si elle n'est pas
// d'aujourd'hui), sans phrase quand tout va bien ; que deux rafraîchissements de suite ne réécrivent pas
// la phrase (zone lue par VoiceOver) ; axe sur la section ; la phrase tient dans un téléphone de 360 px.
// Chrome, WebKit et Firefox.
//
//   node outils/verif_thermometre_panne.mjs        (le serveur d'essai `signaux-essai`, port 8779, lancé)
//   APP_JS=<copie de app.js> node outils/…         (sert cette copie à la place : erreurs volontaires)
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { readFileSync } from 'node:fs';

const require = createRequire(`${homedir()}/.claude/skills/verif/outil/`);
const { chromium, webkit, firefox } = require('playwright');
const { AxeBuilder } = require('@axe-core/playwright');

const BASE = 'http://localhost:8779';
const RAISON = 'aucun cours reçu pour ^GSPC (Yahoo refuse)';
const RECENTE = new Date(Date.now() - 60_000).toISOString();          // il y a une minute : l'heure seule
const VIEILLE = '2026-11-25T21:10:00+00:00';                           // 22:10 à Paris, un autre jour
const heureParis = (iso) => new Date(iso).toLocaleTimeString('fr-FR', { timeZone: 'Europe/Paris', hour: '2-digit', minute: '2-digit' });
const PHRASE = `Mise à jour impossible pour l'instant (${RAISON}) : la lecture affichée date de ${heureParis(RECENTE)}.`;
const MOTEURS = { chrome: () => chromium.launch({ channel: 'chrome' }), webkit: () => webkit.launch(), firefox: () => firefox.launch() };
const TAILLES = { ordi: { viewport: { width: 1440, height: 900 } }, tel: { viewport: { width: 360, height: 780 } } };
const cas = [];
const ok = (c, nom) => { cas.push(Boolean(c)); console.log(`${c ? 'ok ' : 'NON'} ${nom}`); };

async function ouvrir(navigateur, config, secours) {
  const contexte = await navigateur.newContext(config);
  const page = await contexte.newPage();
  const erreurs = [];
  page.on('pageerror', (e) => erreurs.push(e.message));
  if (process.env.APP_JS) {
    const copie = readFileSync(process.env.APP_JS, 'utf8');
    await page.route(/\/app\.js(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: 'application/javascript', body: copie }));
  }
  await page.route('**/api/thermometre', async (route) => {
    const reponse = await route.fetch();
    const j = await reponse.json();
    if (secours && j.data) j.data.secours = secours;
    return route.fulfill({ response: reponse, json: j });
  });
  await page.goto(`${BASE}/?plan`, { waitUntil: 'domcontentloaded' });
  const vu = await page.waitForFunction(() => document.querySelector('#thermometre .thermo-niveau'), null, { timeout: 120000 })
    .then(() => true, () => false);
  return { contexte, page, erreurs, vu };
}
const lire = (page) => page.evaluate(() => {
  const etat = document.getElementById('thermo-etat');
  return { texte: etat?.textContent || '', zone: document.getElementById('thermometre')?.textContent || '',
           deborde: etat ? etat.scrollWidth > etat.clientWidth + 1 : true };
});

for (const [moteur, lancer] of Object.entries(MOTEURS)) {
  const navigateur = await lancer();
  for (const [taille, config] of Object.entries(TAILLES)) {
    const nom = `${moteur} ${taille}`;
    // Yahoo en panne, lecture de l'heure qui précède : la phrase, axe, aucun débordement.
    {
      const { contexte, page, erreurs, vu } = await ouvrir(navigateur, config, { lue_le: RECENTE, raison: RAISON });
      const e = await lire(page);
      ok(vu && e.texte === PHRASE && !/indisponible/.test(e.zone),
        `${nom} · Yahoo en panne, page rechargée : le thermomètre est là, et la phrase dit l'heure de la lecture`);
      ok(!e.deborde, `${nom} · la phrase tient dans sa ligne (aucun débordement)`);
      await page.evaluate(() => document.getElementById('section-thermometre').scrollIntoView());
      await page.waitForTimeout(1200);
      const axe = await new AxeBuilder({ page }).include('#section-thermometre').analyze();
      const graves = axe.violations.filter((v) => ['critical', 'serious'].includes(v.impact));
      ok(graves.length === 0, `${nom} · axe sur la section pendant la panne : ${graves.length} problème(s) critique(s) ou sérieux`
        + (graves.length ? ` (${graves.map((v) => v.id).join(', ')})` : ''));
      if (taille === 'ordi') {
        // Deux rafraîchissements de suite avec la même panne : la zone lue par VoiceOver ne bouge pas.
        const changements = await page.evaluate(async () => {
          const etat = document.getElementById('thermo-etat');
          let n = 0;
          const obs = new MutationObserver((liste) => { n += liste.length; });
          obs.observe(etat, { childList: true, characterData: true, subtree: true });
          await renderThermometre();
          await renderThermometre();
          await new Promise((r) => setTimeout(r, 50));
          obs.disconnect();
          return n;
        });
        ok(changements === 0, `${nom} · deux rafraîchissements de suite ne réécrivent pas la phrase (${changements} changement(s))`);
      }
      ok(erreurs.length === 0, `${nom} · aucune erreur de script${erreurs.length ? ` : ${erreurs.join(' | ')}` : ''}`);
      await contexte.close();
    }
    if (taille !== 'ordi') continue;
    // Une lecture d'un autre jour (Mac réveillé après une veille) : la date avec l'heure.
    {
      const { contexte, page, erreurs, vu } = await ouvrir(navigateur, config, { lue_le: VIEILLE, raison: RAISON });
      const e = await lire(page);
      ok(vu && /date de 25 nov\. 2026.*22:10\.$/.test(e.texte), `${nom} · une lecture d'un autre jour donne la date avec l'heure (« ${e.texte.split(' : ')[1]} »)`);
      ok(erreurs.length === 0, `${nom} · aucune erreur de script${erreurs.length ? ` : ${erreurs.join(' | ')}` : ''}`);
      await contexte.close();
    }
    // Sans panne : aucune phrase.
    {
      const { contexte, page, erreurs, vu } = await ouvrir(navigateur, config, null);
      const e = await lire(page);
      ok(vu && e.texte === '', `${nom} · sans panne : le thermomètre, et aucune phrase`);
      ok(erreurs.length === 0, `${nom} · aucune erreur de script${erreurs.length ? ` : ${erreurs.join(' | ')}` : ''}`);
      await contexte.close();
    }
  }
  await navigateur.close();
}
console.log(`\n${cas.filter(Boolean).length} sur ${cas.length} contrôles passent.`);
process.exit(cas.every(Boolean) ? 0 : 1);

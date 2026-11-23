// Le creux du marché (#section-creux) : à lancer avec le serveur d'essai `signaux-essai` (port 8779,
// voir CLAUDE.md « Vérifier »). Usage : node outils/verif_creux.mjs <dossier de sortie hors du projet>
// 1. Chrome et WebKit, à 1440, 1280, 390, 360 et 320 px et au téléphone à l'horizontale, détails
//    fermés PUIS ouverts : captures, console, rien hors du contenu de la carte, axe sur la section
//    (1280 et 390), chaque texte de /api/creux retrouvé tel quel dans la page, les mots du plan
//    visibles, aucun conseil, aucun rouge ;
// 2. le clavier (Entrée et Espace sur chaque détail, le cadre du tableau atteint par Tab) et le
//    rafraîchissement qui garde les détails ouverts, le focus et le défilement du tableau ;
// 3. des réponses fabriquées (ancien serveur, panne avec et sans lecture d'avant, un indice
//    « dernière lecture », un indice à son record, une baisse en cours qui a touché −20 %, aucune
//    baisse finie) : la section dit la bonne chose, sans erreur.
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(`${homedir()}/.claude/skills/verif/outil/`);
const { chromium, webkit } = require('playwright');
const { AxeBuilder } = require('@axe-core/playwright');

const sortie = process.argv[2];
if (!sortie) { console.error('Usage : node outils/verif_creux.mjs <dossier de sortie>'); process.exit(2); }
mkdirSync(sortie, { recursive: true });
const BASE = 'http://localhost:8779';
const TEL = { deviceScaleFactor: 2, isMobile: true, hasTouch: true };
const TAILLES = {
  ordi: { viewport: { width: 1440, height: 900 } },
  'ordi-petit': { viewport: { width: 1280, height: 800 } },
  tel: { viewport: { width: 390, height: 844 }, ...TEL },
  'tel-petit': { viewport: { width: 360, height: 780 }, ...TEL },
  etroit: { viewport: { width: 320, height: 700 }, ...TEL },
  'tel-horizontal': { viewport: { width: 844, height: 390 }, ...TEL },
};

const lignes = [];
const ok = (cond, quoi) => { lignes.push(`${cond ? 'ok ' : 'NON'} ${quoi}`); return cond; };
const espaces = (s) => s.replace(/[\s  ]+/g, ' ').trim();

// La vraie réponse du serveur d'essai (Yahoo lu une fois, puis gardée une heure par le serveur).
const vraie = await (await fetch(`${BASE}/api/creux`)).json();
if (!vraie.ok) { console.error('/api/creux ne répond pas :', vraie.error); process.exit(1); }
const textesServeur = [vraie.data.cloture, vraie.data.legende, ...vraie.data.notes,
  ...vraie.data.indices.flatMap((x) => [x.nom, ...Object.values(x.textes).filter((t) => t && t !== x.textes.source),
    ...x.baisses.flatMap((b) => Object.values(b.textes))])];

async function ouvrir(navigateur, config, route) {
  const contexte = await navigateur.newContext(config);
  const page = await contexte.newPage();
  const erreurs = [];
  page.on('console', (m) => { if (m.type() === 'error') erreurs.push(m.text()); });
  page.on('pageerror', (e) => erreurs.push(String(e)));
  if (route) await page.route('**/api/creux', route);
  await page.goto(`${BASE}/?plan`, { waitUntil: 'load' });
  return { contexte, page, erreurs };
}
const attendreLecture = (page) => page.waitForSelector('#creux .creux-notes', { timeout: 30000 });

// 1. Les vraies données.
for (const moteur of ['chromium', 'webkit']) {
  const navigateur = await (moteur === 'webkit' ? webkit.launch() : chromium.launch({ channel: 'chrome' }));
  try {
    for (const [taille, config] of Object.entries(TAILLES)) {
      const { contexte, page, erreurs } = await ouvrir(navigateur, config);
      await attendreLecture(page);
      await page.waitForTimeout(800);
      const section = page.locator('#section-creux');
      for (const etat of ['fermes', 'ouverts']) {
        if (etat === 'ouverts') {
          await page.evaluate(() => document.querySelectorAll('#creux details').forEach((d) => { d.open = true; }));
          await page.waitForTimeout(300);
        }
        await section.scrollIntoViewIfNeeded();
        await section.screenshot({ path: join(sortie, `${moteur}-${taille}-${etat}.png`) });
        const m = await page.evaluate(() => {
          const s = document.querySelector('#section-creux');
          const carte = document.querySelector('#creux');
          const cs = getComputedStyle(carte);
          const cr = carte.getBoundingClientRect();
          // Rien ne sort du contenu de la carte (sans son padding) ; le cadre des tableaux est
          // vérifié, pas ce qui défile dedans.
          const gauche = cr.left + parseFloat(cs.paddingLeft) - 1;
          const droite = cr.right - parseFloat(cs.paddingRight) + 1;
          const dehors = [];
          for (const el of carte.querySelectorAll('*')) {
            if (el.parentElement && el.parentElement.closest('.creux-table')) continue;
            const b = el.getBoundingClientRect();
            if (b.width && (b.right > droite || b.left < gauche)) dehors.push(`${el.tagName}.${el.className} +${Math.round(b.right - droite)}`);
          }
          const r = s.getBoundingClientRect();
          for (const el of s.querySelectorAll('.section-head *')) {
            const b = el.getBoundingClientRect();
            if (b.width && (b.right > r.right + 1 || b.left < r.left - 1)) dehors.push(`${el.tagName}.${el.className}`);
          }
          const cadres = [...s.querySelectorAll('.creux-table')].filter((c) => c.offsetParent);
          // Aucun rouge : la couleur des baisses de l'appli (--down) n'est nulle part dans la section.
          const rouge = getComputedStyle(document.documentElement).getPropertyValue('--down').trim();
          const sonde = document.createElement('i');
          sonde.style.color = rouge;
          document.body.append(sonde);
          const rougeCalc = getComputedStyle(sonde).color;
          sonde.remove();
          const rouges = [...s.querySelectorAll('*')].filter((el) => getComputedStyle(el).color === rougeCalc).length;
          const resumes = [...s.querySelectorAll('summary')].map((e) => Math.round(e.getBoundingClientRect().height));
          return { h: Math.round(r.height), dehors: dehors.slice(0, 4), texte: s.innerText,
            defile: cadres.some((c) => c.scrollWidth > c.clientWidth), rouges, resumes };
        });
        const ou = `${moteur} ${taille} ${etat}`;
        if (config.hasTouch && etat === 'fermes') {
          ok(m.resumes.length === 2 && m.resumes.every((x) => x >= 44), `${ou} : les résumés sont des cibles de doigt de 44 px au moins (${m.resumes.join(' et ')} px)`);
        }
        ok(!m.dehors.length, `${ou} : rien ne dépasse de la carte (h ${m.h} px${m.defile ? ', un tableau défile dans son cadre' : ''})${m.dehors.length ? ' — ' + m.dehors.join(', ') : ''}`);
        ok(m.rouges === 0, `${ou} : aucun texte rouge (${m.rouges})`);
        if (etat === 'ouverts' && (taille === 'ordi' || taille === 'etroit')) {
          const lu = espaces(m.texte);
          const manquent = textesServeur.filter((t) => !lu.includes(espaces(t)));
          ok(!manquent.length, `${ou} : chacun des ${textesServeur.length} textes du serveur est dans la page, tel quel${manquent.length ? ' — manquent : ' + manquent.slice(0, 3).join(' | ') : ''}`);
          const mots = ['Sur les clôtures', 'prix seul', 'les baisses finies sont revenues', 'une action seule peut ne jamais revenir'];
          ok(mots.every((x) => lu.includes(x)), `${ou} : les mots du plan visibles (${mots.filter((x) => !lu.includes(x)).join(', ') || 'tous'})`);
          // Des mots entiers : « vendredi » n'est pas « vendre ».
          const lignes1 = await page.evaluate(() => [...document.querySelectorAll('#creux .creux-table tbody tr:first-child')]
            .map((tr) => [...tr.querySelectorAll('th > span, td')].map((c) => c.textContent.trim())));
          const attendu = vraie.data.indices.filter((x) => x.baisses.length).map((x) => {
            const t = x.baisses[0].textes;
            return [t.record, t.niveau, t.creux, t.baisse, t.descente, t.retour_record, t.retour_creux];
          });
          ok(JSON.stringify(lignes1) === JSON.stringify(attendu), `${ou} : chaque colonne du tableau a le bon texte (première ligne de chaque indice)${JSON.stringify(lignes1) === JSON.stringify(attendu) ? '' : ' — lu ' + JSON.stringify(lignes1)}`);
          const tete = espaces(await page.locator('#creux .creux-tete').innerText());
          const teteAttendue = vraie.data.indices.map((x) => espaces([x.nom, x.textes.tete, x.textes.tete_suite, x.textes.tete_date].filter(Boolean).join(' ')));
          ok(teteAttendue.every((t) => tete.includes(t)), `${ou} : l'en-tête dit « ${teteAttendue.join(' » et « ')} »`);
          const interdits = /(?<![a-zà-ÿ])(achète|acheter|vends|vendre|prévoi\S*|il reste|reste à|remontera|rebondira|reviendra|finira le)(?![a-zà-ÿ])/i;
          ok(!interdits.test(lu), `${ou} : aucun conseil ni date de fin${interdits.test(lu) ? ' — ' + lu.match(interdits)[0] : ''}`);
        }
        if (taille === 'ordi-petit' || taille === 'tel') {
          const res = await new AxeBuilder({ page }).include('#section-creux').analyze();
          const v = res.violations.map((x) => `${x.impact} ${x.id} (${x.nodes.length})`);
          ok(!v.length, `${ou} : axe sur la section, ${v.length ? v.join(', ') : '0 problème'}`);
        }
      }
      ok(!erreurs.length, `${moteur} ${taille} : console sans erreur${erreurs.length ? ' — ' + erreurs.join(' | ').slice(0, 300) : ''}`);
      await contexte.close();
    }

    // 2. Le clavier, et le rafraîchissement qui garde tout en place.
    {
      const { contexte, page, erreurs } = await ouvrir(navigateur, TAILLES.tel);
      await attendreLecture(page);
      const resumes = await page.$$('#creux summary');
      let clavier = resumes.length === 2;
      for (let i = 0; i < resumes.length; i++) {
        await resumes[i].focus();
        await page.keyboard.press('Enter');
        const ouvert = await page.evaluate((k) => document.querySelectorAll('#creux details')[k].open, i);
        await page.keyboard.press('Space');
        const ferme = await page.evaluate((k) => !document.querySelectorAll('#creux details')[k].open, i);
        clavier = clavier && ouvert && ferme;
      }
      ok(clavier, `${moteur} : Entrée ouvre chaque détail, Espace le referme (${resumes.length} détails)`);
      const contour = await page.evaluate(() => (document.activeElement?.tagName === 'SUMMARY'
        ? getComputedStyle(document.activeElement).outlineStyle : 'pas de focus sur un résumé'));
      ok(contour === 'solid', `${moteur} : le focus d'un détail se voit (contour ${contour})`);
      // Tab depuis le résumé ouvert va au cadre du tableau (WebKit sans « Navigation au clavier »
      // saute les liens et les boutons, pas un élément tabindex=0).
      await page.focus('#creux summary');
      await page.keyboard.press('Enter');
      await page.keyboard.press('Tab');
      const surCadre = await page.evaluate(() => document.activeElement?.classList.contains('creux-table'));
      ok(surCadre, `${moteur} : Tab depuis le détail ouvert va au cadre du tableau`);
      const contourCadre = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
      ok(contourCadre !== 'none', `${moteur} : le focus du cadre se voit (contour ${contourCadre})`);
      await page.keyboard.press('ArrowRight');
      await page.waitForTimeout(700);              // Chrome fait défiler à la flèche en douceur : attendre la fin
      const aLaFleche = await page.evaluate(() => document.querySelector('#creux .creux-table').scrollLeft);
      if (moteur === 'webkit') {
        // Connu (23/11/2026) : le WebKit de Playwright ne fait défiler AUCUN cadre pris au clavier à la
        // flèche, pas plus celui du banc. Noté, pas compté. Pas vu dans le vrai Safari.
        lignes.push(`note webkit : la flèche → fait défiler le tableau pris au clavier ? ${aLaFleche} px (comme le tableau du banc)`);
      } else {
        ok(aLaFleche > 0, `${moteur} : la flèche → fait défiler le tableau pris au clavier (${aLaFleche} px)`);
      }
      await page.evaluate(() => { document.querySelector('#creux .creux-table').scrollLeft = 120; });
      const avant = await page.evaluate(() => document.querySelector('#creux .creux-table').scrollLeft);
      await page.evaluate(() => renderCreux());
      await page.waitForTimeout(400);
      const garde = await page.evaluate(() => ({ ouvert: document.querySelector('#creux details').open,
        focus: document.activeElement === document.querySelector('#creux .creux-table'),
        x: document.querySelector('#creux .creux-table').scrollLeft }));
      ok(garde.ouvert && garde.focus && garde.x === avant, `${moteur} : un rafraîchissement (même contenu) ne touche à rien (ouvert ${garde.ouvert}, focus ${garde.focus}, défilement ${garde.x} / ${avant})`);
      await page.evaluate(() => { creuxDernierHtml = 'autre chose'; return renderCreux(); });
      await page.waitForTimeout(400);
      const garde2 = await page.evaluate(() => ({ ouvert: document.querySelector('#creux details').open,
        focus: document.activeElement === document.querySelector('#creux .creux-table'),
        x: document.querySelector('#creux .creux-table').scrollLeft }));
      ok(garde2.ouvert && garde2.focus && Math.abs(garde2.x - avant) <= 1, `${moteur} : un contenu redessiné rouvre le détail, rend le focus au cadre et son défilement (${garde2.x} / ${avant})`);
      await page.focus('#creux summary');
      await page.evaluate(() => { creuxDernierHtml = 'encore autre chose'; return renderCreux(); });
      await page.waitForTimeout(300);
      const surResume = await page.evaluate(() => document.activeElement === document.querySelector('#creux summary'));
      ok(surResume, `${moteur} : redessiné avec le focus sur un résumé, il y revient`);
      ok(!erreurs.length, `${moteur} clavier : console sans erreur${erreurs.length ? ' — ' + erreurs.join(' | ') : ''}`);
      await contexte.close();
    }
  } finally {
    await navigateur.close();
  }
}

// 3. Les réponses fabriquées (Chrome, 1280).
const copie = () => JSON.parse(JSON.stringify(vraie.data));
const json = (data, status = 200) => (route) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
const cas = [
  ['ancien serveur (404)', (route) => route.fulfill({ status: 404, contentType: 'text/html', body: '<h1>404</h1>' }),
    ['quand l\'appli sera relancée : son serveur ne connaît pas encore cette section.']],
  ['panne sans lecture d\'avant', json({ ok: false, error: 'aucune lecture du Nasdaq Composite pour l\'instant (boum)' }, 500),
    ['Le creux du marché est indisponible pour l\'instant (aucune lecture du Nasdaq Composite pour l\'instant (boum)). Nouvel essai dans une minute.']],
  ['un indice en « dernière lecture »', json({ ok: true, data: (() => {
    const d = copie();
    d.indices[1].frais = false;
    d.indices[1].etat = 'Yahoo n\'a pas donné de nouveaux cours utilisables : dernière lecture du lundi 23 novembre 2026 à 10 h 05.';
    d.indices[1].textes.tete_date = '(clôture du 19/11/2026)';
    return d;
  })() }), ['Yahoo n\'a pas donné de nouveaux cours utilisables : dernière lecture du lundi 23 novembre 2026 à 10 h 05.',
    'Nasdaq Composite −18,0 % de son record (clôture du 19/11/2026)']],
  ['un serveur muet', (route) => route.abort('failed'),
    ['Le creux du marché est indisponible pour l\'instant (le serveur de l\'appli ne répond pas). Nouvel essai dans une minute.']],
  ['une réponse illisible (une page d\'erreur HTML)', (route) => route.fulfill({ status: 502, contentType: 'text/html', body: '<h1>502</h1>' }),
    ['Le creux du marché est indisponible pour l\'instant (réponse illisible du serveur (502)).']],
  ['à son record, aucune baisse finie, une baisse qui a touché −20 %', json({ ok: true, data: (() => {
    const d = copie();
    const sp = d.indices[0];
    sp.textes.tete = 'à son record';
    sp.textes.tete_suite = null;
    sp.tete_num = false;
    sp.en_cours.au_record = true;
    sp.textes.en_cours = 'À la clôture du 20 novembre 2026, le S&P 500 est à son record.';
    const nq = d.indices[1];
    nq.baisses = [];
    nq.textes.detail = null;
    nq.textes.histoire = 'Sur les clôtures, aucune baisse de 20 % ou plus n\'est finie depuis février 1971.';
    nq.textes.en_cours = 'À la clôture du 20 novembre 2026, le Nasdaq Composite est à −22,0 % de son record de clôture du 6 octobre 2026 ; c\'est aussi son creux pour l\'instant. La baisse commencée le 6 octobre 2026 a atteint −20 % le 18 novembre 2026.';
    return d;
  })() }), ['S&P 500 à son record', 'le S&P 500 est à son record.', 'aucune baisse de 20 % ou plus n\'est finie depuis février 1971.',
    'La baisse commencée le 6 octobre 2026 a atteint −20 % le 18 novembre 2026.']],
];
const navigateur = await chromium.launch({ channel: 'chrome' });
try {
  for (const [nom, route, attendus] of cas) {
    const { contexte, page, erreurs } = await ouvrir(navigateur, TAILLES['ordi-petit'], route);
    await page.waitForFunction(() => !document.querySelector('#creux').textContent.includes('Lecture du creux'), null, { timeout: 30000 });
    await page.waitForTimeout(400);
    const texte = espaces(await page.locator('#section-creux').innerText());
    const manque = attendus.filter((a) => !texte.includes(espaces(a)));
    ok(!manque.length, `fabriqué, ${nom} : ${manque.length ? 'MANQUE « ' + manque.join(' » « ') + ' » — lu : ' + texte.slice(0, 260) : 'la section le dit'}`);
    const sansErreur = erreurs.filter((e) => !/404|500|502|ERR_FAILED|Failed to load resource/.test(e));
    ok(!sansErreur.length, `fabriqué, ${nom} : console sans erreur de script${sansErreur.length ? ' — ' + sansErreur.join(' | ') : ''}`);
    if (nom === 'ancien serveur (404)' || nom.startsWith('panne') || nom === 'un serveur muet') {
      const pastille = await page.$eval('#creux-date', (e) => getComputedStyle(e).display);
      ok(pastille === 'none', `fabriqué, ${nom} : pas de pastille de date vide (display ${pastille})`);
    }
    if (nom.startsWith('à son record')) {
      const n = await page.$$eval('#creux details', (l) => l.length);
      ok(n === 1, `fabriqué, ${nom} : un seul détail (pas de tableau vide pour le Nasdaq) — ${n}`);
      await page.locator('#section-creux').screenshot({ path: join(sortie, 'fabrique-record-seuil.png') });
      const police = await page.$eval('#creux .creux-chiffre .creux-valeur', (e) => e.classList.contains('num'));
      ok(!police, `fabriqué, ${nom} : « à son record » en police de texte, pas en chiffres (num : ${police})`);
    }
    await contexte.close();
  }
  // Une panne APRÈS une première lecture : la dernière lecture reste, et c'est dit, sans rouge.
  {
    let panne = false;
    const { contexte, page } = await ouvrir(navigateur, TAILLES['ordi-petit'],
      (route) => (panne ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"ok":false,"error":"boum"}' }) : route.continue()));
    await attendreLecture(page);
    panne = true;
    await page.evaluate(() => renderCreux());
    await page.waitForTimeout(400);
    const r = await page.evaluate(() => ({ reste: !!document.querySelector('#creux .creux-notes'),
      etat: document.querySelector('#creux-etat').textContent, date: document.querySelector('#creux-date').textContent }));
    ok(r.reste && r.etat.includes('la lecture affichée est la dernière connue') && r.date.startsWith('clôture du'),
      `une panne après une première lecture : la lecture et sa date restent, et c'est dit (« ${r.etat} »)`);
    panne = false;
    await page.evaluate(() => renderCreux());
    await page.waitForTimeout(400);
    ok(await page.$eval('#creux-etat', (e) => e.textContent === ''), 'la panne passée : le message s\'efface');
    await contexte.close();
  }
} finally {
  await navigateur.close();
}
writeFileSync(join(sortie, 'bilan.txt'), lignes.join('\n'));
console.log(lignes.join('\n'));
console.log(`\n${lignes.filter((l) => l.startsWith('NON')).length} NON sur ${lignes.length}`);

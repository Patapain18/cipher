// Le mur des frais pendant la séance (#section-seance) : à lancer avec le serveur d'essai `signaux-essai`
// (port 8779, voir CLAUDE.md « Vérifier »), une fois data/seance.json écrit par `python3 seance.py --mesurer`.
// Usage : node outils/verif_seance.mjs <dossier de sortie hors du projet>
// 1. Chrome et WebKit, à 1440, 1280, 390, 360 et 320 px et au téléphone à l'horizontale, détail fermé
//    PUIS ouvert : captures, console, rien hors du contenu de la carte, aucun rouge, axe sur la section
//    (1280 et 390), CHAQUE CASE des deux tableaux égale à SA case de /api/seance (pas seulement « ce
//    nombre existe quelque part » : deux colonnes échangées doivent se voir), chaque phrase et chaque
//    note telles quelles, les mots du plan visibles, aucun conseil ;
// 2. le clavier (Entrée et Espace sur le détail, Tab vers les cadres, la flèche) et un rafraîchissement
//    avec une mesure CHANGÉE qui garde le détail ouvert, le focus et le défilement ;
// 3. des réponses fabriquées (ancien serveur, panne avec et sans lecture d'avant, serveur muet, réponse
//    illisible, mesure absente ou illisible, une donnée piégée) : la section dit la bonne chose ;
// 4. la vue baie (Chrome) : la section n'est dans aucun panneau.
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(`${homedir()}/.claude/skills/verif/outil/`);
const { chromium, webkit } = require('playwright');
const { AxeBuilder } = require('@axe-core/playwright');

const sortie = process.argv[2];
if (!sortie) { console.error('Usage : node outils/verif_seance.mjs <dossier de sortie>'); process.exit(2); }
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

const vraie = await (await fetch(`${BASE}/api/seance`)).json();
if (!vraie.ok || vraie.data?.etat !== 'mesure') {
  console.error('/api/seance ne renvoie pas de mesure :', JSON.stringify(vraie).slice(0, 300));
  process.exit(1);
}
const T = vraie.data.textes;
// Ce que chaque case doit dire, ligne par ligne, dans l'ordre (lu dans la réponse du serveur).
const attenduFenetres = T.lignes.map((x) => [x.nom, x.precision, x.mediane, ...x.parts]);
const attenduValeurs = T.valeurs.map((v) => [v.valeur, ...v.medianes]);
const attenduEntetes = T.colonnes;
const attenduEntetesValeurs = [T.entete_valeur, ...T.colonnes_valeurs.map((c) => `${c.nom}|${c.precision}`)];

async function ouvrir(navigateur, config, route, adresse = `${BASE}/?plan`) {
  const contexte = await navigateur.newContext(config);
  const page = await contexte.newPage();
  const erreurs = [];
  page.on('console', (m) => { if (m.type() === 'error') erreurs.push(m.text()); });
  page.on('pageerror', (e) => erreurs.push(String(e)));
  if (route) await page.route('**/api/seance', route);
  await page.goto(adresse, { waitUntil: 'load' });
  return { contexte, page, erreurs };
}
const attendreLecture = (page) => page.waitForSelector('#seance .seance-notes', { timeout: 30000 });

/* Les cases des deux tableaux, telles que la page les montre. Le nom d'une fenêtre et ses heures
   sont lus à part (le nom est le premier nœud texte, les heures sont dans .seance-precision). */
const lireCases = (page) => page.evaluate(() => {
  const nom = (th) => {
    const p = th.querySelector('.seance-precision');
    return p ? [th.firstChild.textContent.trim(), p.textContent.trim()] : [th.textContent.trim()];
  };
  const [t1, t2] = document.querySelectorAll('#seance .seance-table table');
  return {
    entetes: [...t1.querySelectorAll('thead th')].map((th) => th.textContent.trim()),
    fenetres: [...t1.querySelectorAll('tbody tr')].map((tr) => [...nom(tr.querySelector('th')),
      ...[...tr.querySelectorAll('td')].map((td) => td.textContent.trim())]),
    entetesValeurs: [...t2.querySelectorAll('thead th')].map((th) => nom(th).join('|')),
    valeurs: [...t2.querySelectorAll('tbody tr')].map((tr) => [tr.querySelector('th').textContent.trim(),
      ...[...tr.querySelectorAll('td')].map((td) => td.textContent.trim())]),
  };
});
const pareil = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const premiereDifference = (lu, attendu) => {
  for (let i = 0; i < Math.max(lu.length, attendu.length); i++) {
    if (!pareil(lu[i], attendu[i])) return `ligne ${i + 1} : lu ${JSON.stringify(lu[i])}, attendu ${JSON.stringify(attendu[i])}`;
  }
  return '';
};

// 1. Les vraies données.
for (const moteur of ['chromium', 'webkit']) {
  const navigateur = await (moteur === 'webkit' ? webkit.launch() : chromium.launch({ channel: 'chrome' }));
  try {
    for (const [taille, config] of Object.entries(TAILLES)) {
      const { contexte, page, erreurs } = await ouvrir(navigateur, config);
      await attendreLecture(page);
      await page.waitForTimeout(600);
      const section = page.locator('#section-seance');
      for (const etat of ['ferme', 'ouvert']) {
        if (etat === 'ouvert') {
          await page.evaluate(() => document.querySelectorAll('#seance details').forEach((d) => { d.open = true; }));
          await page.waitForTimeout(300);
        }
        await section.scrollIntoViewIfNeeded();
        await section.screenshot({ path: join(sortie, `${moteur}-${taille}-${etat}.png`) });
        const m = await page.evaluate(() => {
          const s = document.querySelector('#section-seance');
          const carte = document.querySelector('#seance');
          const cs = getComputedStyle(carte);
          const cr = carte.getBoundingClientRect();
          // Rien ne sort du contenu de la carte (sans son padding) ; le cadre des tableaux est
          // vérifié, pas ce qui défile dedans.
          const gauche = cr.left + parseFloat(cs.paddingLeft) - 1;
          const droite = cr.right - parseFloat(cs.paddingRight) + 1;
          const dehors = [];
          for (const el of carte.querySelectorAll('*')) {
            if (el.parentElement && el.parentElement.closest('.seance-table')) continue;
            const b = el.getBoundingClientRect();
            if (b.width && (b.right > droite || b.left < gauche)) dehors.push(`${el.tagName}.${el.className} +${Math.round(b.right - droite)}`);
          }
          const r = s.getBoundingClientRect();
          for (const el of s.querySelectorAll('.section-head *')) {
            const b = el.getBoundingClientRect();
            if (b.width && (b.right > r.right + 1 || b.left < r.left - 1)) dehors.push(`${el.tagName}.${el.className}`);
          }
          const cadres = [...s.querySelectorAll('.seance-table')].filter((c) => c.offsetParent);
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
            defile: cadres.filter((c) => c.scrollWidth > c.clientWidth).length, rouges, resumes };
        });
        const ou = `${moteur} ${taille} ${etat}`;
        if (config.hasTouch && etat === 'ferme') {
          ok(m.resumes.length === 1 && m.resumes.every((x) => x >= 44), `${ou} : le résumé est une cible de doigt de 44 px au moins (${m.resumes.join(' et ')} px)`);
        }
        ok(!m.dehors.length, `${ou} : rien ne dépasse de la carte (h ${m.h} px${m.defile ? `, ${m.defile} tableau(x) défile(nt) dans leur cadre` : ''})${m.dehors.length ? ' — ' + m.dehors.join(', ') : ''}`);
        ok(m.rouges === 0, `${ou} : aucun texte rouge (${m.rouges})`);
        if (etat === 'ouvert' && (taille === 'ordi' || taille === 'etroit')) {
          const c = await lireCases(page);
          ok(pareil(c.entetes, attenduEntetes), `${ou} : les en-têtes du tableau des fenêtres${pareil(c.entetes, attenduEntetes) ? '' : ' — lu ' + JSON.stringify(c.entetes)}`);
          ok(pareil(c.fenetres, attenduFenetres), `${ou} : chaque case des ${attenduFenetres.length} fenêtres est la sienne (nom, heures, médiane, deux parts)${premiereDifference(c.fenetres, attenduFenetres) ? ' — ' + premiereDifference(c.fenetres, attenduFenetres) : ''}`);
          ok(pareil(c.entetesValeurs, attenduEntetesValeurs), `${ou} : les en-têtes du tableau des valeurs${pareil(c.entetesValeurs, attenduEntetesValeurs) ? '' : ' — lu ' + JSON.stringify(c.entetesValeurs)}`);
          ok(pareil(c.valeurs, attenduValeurs), `${ou} : chaque case des ${attenduValeurs.length} valeurs est la sienne (5 médianes chacune)${premiereDifference(c.valeurs, attenduValeurs) ? ' — ' + premiereDifference(c.valeurs, attenduValeurs) : ''}`);
          const phrases = await page.evaluate(() => ({
            phrases: [...document.querySelectorAll('#seance .seance-phrase')].map((p) => p.textContent),
            legende: document.querySelector('#seance .seance-legende').textContent,
            note: document.querySelector('#seance .seance-note').textContent,
            resume: document.querySelector('#seance summary').textContent,
            notes: [...document.querySelectorAll('#seance .seance-notes li')].map((li) => li.textContent),
            pastille: document.querySelector('#seance-date').textContent,
          }));
          const attendues = { phrases: [T.intro, T.conclusion], legende: T.legende, note: T.legende_valeurs, resume: T.detail,
            notes: T.notes, pastille: T.pastille };
          const faux = Object.keys(attendues).filter((k) => !pareil(phrases[k], attendues[k]));
          ok(!faux.length, `${ou} : chaque phrase, la légende, les notes et la pastille telles que le serveur les écrit${faux.length ? ' — différent : ' + faux.join(', ') : ''}`);
          const lu = espaces(m.texte);
          const mots = ['prise au hasard', 'hausses et baisses mêlées', 'savoir d\'avance', 'un sondage'];
          ok(mots.every((x) => lu.includes(x)), `${ou} : les mots du plan visibles (${mots.filter((x) => !lu.includes(x)).join(', ') || 'tous'})`);
          // Des mots entiers : « vendredi » n'est pas « vendre ».
          const interdits = /(?<![a-zà-ÿ])(achète|achetez|acheter|vends|vendez|vendre|prévoi\S*|prédi\S*|conseill\S*|recommand\S*|devrais|devriez)(?![a-zà-ÿ])/i;
          ok(!interdits.test(lu), `${ou} : aucun conseil${interdits.test(lu) ? ' — ' + lu.match(interdits)[0] : ''}`);
        }
        if (taille === 'ordi-petit' || taille === 'tel') {
          const res = await new AxeBuilder({ page }).include('#section-seance').analyze();
          const v = res.violations.map((x) => `${x.impact} ${x.id} (${x.nodes.length})`);
          ok(!v.length, `${ou} : axe sur la section, ${v.length ? v.join(', ') : '0 problème'}`);
        }
      }
      ok(!erreurs.length, `${moteur} ${taille} : console sans erreur${erreurs.length ? ' — ' + erreurs.join(' | ').slice(0, 300) : ''}`);
      await contexte.close();
    }

    // 2. Le clavier, et un rafraîchissement avec une mesure CHANGÉE qui garde tout en place.
    {
      let changee = false;
      const autre = JSON.parse(JSON.stringify(vraie));
      autre.data.textes.pastille = `${T.pastille} (changée)`;
      autre.data.textes.valeurs[0].medianes[0] = '9,99 %';
      const { contexte, page, erreurs } = await ouvrir(navigateur, TAILLES.tel,
        (route) => (changee ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(autre) }) : route.continue()));
      await attendreLecture(page);
      await page.focus('#seance summary');
      await page.keyboard.press('Enter');
      const ouvert = await page.evaluate(() => document.querySelector('#seance details').open);
      await page.keyboard.press('Space');
      const ferme = await page.evaluate(() => !document.querySelector('#seance details').open);
      ok(ouvert && ferme, `${moteur} : Entrée ouvre le détail, Espace le referme`);
      const contour = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
      ok(contour === 'solid', `${moteur} : le focus du résumé se voit (contour ${contour})`);
      // Le cadre du premier tableau se prend au clavier (Shift+Tab depuis le résumé : le cadre est avant lui).
      await page.keyboard.press('Shift+Tab');
      const surPremier = await page.evaluate(() => document.activeElement?.dataset?.cle === 'fenetres');
      ok(surPremier, `${moteur} : Maj+Tab depuis le résumé va au cadre du tableau des fenêtres`);
      await page.focus('#seance summary');
      await page.keyboard.press('Enter');
      await page.keyboard.press('Tab');
      const surCadre = await page.evaluate(() => document.activeElement?.dataset?.cle === 'valeurs');
      ok(surCadre, `${moteur} : Tab depuis le détail ouvert va au cadre du tableau des valeurs`);
      const contourCadre = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
      ok(contourCadre !== 'none', `${moteur} : le focus du cadre se voit (contour ${contourCadre})`);
      await page.keyboard.press('ArrowRight');
      await page.waitForTimeout(700);              // Chrome fait défiler à la flèche en douceur : attendre la fin
      const aLaFleche = await page.evaluate(() => document.querySelector('#seance .seance-table[data-cle="valeurs"]').scrollLeft);
      if (moteur === 'webkit') {
        // Connu (23/11/2026) : le WebKit de Playwright ne fait défiler aucun cadre pris au clavier à la flèche.
        lignes.push(`note webkit : la flèche → fait défiler le tableau pris au clavier ? ${aLaFleche} px (comme ceux du banc et du creux)`);
      } else {
        ok(aLaFleche > 0, `${moteur} : la flèche → fait défiler le tableau pris au clavier (${aLaFleche} px)`);
      }
      await page.evaluate(() => { document.querySelector('#seance .seance-table[data-cle="valeurs"]').scrollLeft = 90; });
      const avant = await page.evaluate(() => document.querySelector('#seance .seance-table[data-cle="valeurs"]').scrollLeft);
      const etatDe = () => page.evaluate(() => ({ ouvert: document.querySelector('#seance details').open,
        focus: document.activeElement === document.querySelector('#seance .seance-table[data-cle="valeurs"]'),
        x: document.querySelector('#seance .seance-table[data-cle="valeurs"]').scrollLeft,
        pastille: document.querySelector('#seance-date').textContent,
        case1: document.querySelector('#seance .seance-table[data-cle="valeurs"] tbody td').textContent }));
      await page.evaluate(() => renderSeance());
      await page.waitForTimeout(400);
      const g1 = await etatDe();
      ok(g1.ouvert && g1.focus && g1.x === avant, `${moteur} : un rafraîchissement (même mesure) ne touche à rien (ouvert ${g1.ouvert}, focus ${g1.focus}, défilement ${g1.x} / ${avant})`);
      changee = true;
      await page.evaluate(() => renderSeance());
      await page.waitForTimeout(400);
      const g2 = await etatDe();
      ok(g2.ouvert && g2.focus && Math.abs(g2.x - avant) <= 1 && g2.pastille.endsWith('(changée)') && g2.case1 === '9,99 %',
        `${moteur} : une mesure CHANGÉE est redessinée, avec le détail ouvert, le focus et le défilement gardés (${g2.x} / ${avant}, « ${g2.pastille} »)`);
      await page.focus('#seance summary');
      changee = false;
      await page.evaluate(() => renderSeance());
      await page.waitForTimeout(400);
      const surResume = await page.evaluate(() => document.activeElement === document.querySelector('#seance summary'));
      ok(surResume, `${moteur} : redessiné avec le focus sur le résumé, il y revient`);
      ok(!erreurs.length, `${moteur} clavier : console sans erreur${erreurs.length ? ' — ' + erreurs.join(' | ') : ''}`);
      await contexte.close();
    }
  } finally {
    await navigateur.close();
  }
}

// 3. Les réponses fabriquées (Chrome, 1280).
const json = (data, status = 200) => (route) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
const piegee = JSON.parse(JSON.stringify(vraie));
piegee.data.textes.notes[0] = '<img src=x onerror="window.__piege=1">';
piegee.data.textes.valeurs[0].valeur = '<b>X</b>';
const cas = [
  ['ancien serveur (404)', (route) => route.fulfill({ status: 404, contentType: 'text/html', body: '<h1>404</h1>' }),
    ['Le mur des frais pendant la séance s\'affichera quand l\'appli sera relancée : son serveur ne connaît pas encore cette section.']],
  ['panne sans lecture d\'avant', json({ ok: false, error: 'boum' }, 500),
    ['Le mur des frais pendant la séance est indisponible pour l\'instant (boum). Nouvel essai dans une minute.']],
  ['un serveur muet', (route) => route.abort('failed'),
    ['Le mur des frais pendant la séance est indisponible pour l\'instant (le serveur de l\'appli ne répond pas).']],
  ['une réponse illisible (une page d\'erreur HTML)', (route) => route.fulfill({ status: 502, contentType: 'text/html', body: '<h1>502</h1>' }),
    ['Le mur des frais pendant la séance est indisponible pour l\'instant (réponse illisible du serveur (502)).']],
  ['mesure absente', json({ ok: true, data: { etat: 'absent', message: 'Le mur des frais pendant la séance n’est pas encore mesuré (data/seance.json n’existe pas).' } }),
    ['n’est pas encore mesuré (data/seance.json n’existe pas).']],
  ['mesure illisible', json({ ok: true, data: { etat: 'illisible', detail: 'ce n\'est pas du JSON valide', message: 'La mesure du mur des frais pendant la séance est illisible (ce n\'est pas du JSON valide).' } }),
    ['La mesure du mur des frais pendant la séance est illisible (ce n\'est pas du JSON valide).']],
  ['une donnée piégée', json(piegee), ['<img src=x onerror="window.__piege=1">', '<b>X</b>']],
];
const navigateur = await chromium.launch({ channel: 'chrome' });
try {
  for (const [nom, route, attendus] of cas) {
    const { contexte, page, erreurs } = await ouvrir(navigateur, TAILLES['ordi-petit'], route);
    await page.waitForFunction(() => !document.querySelector('#seance').textContent.includes('Lecture de la mesure'), null, { timeout: 30000 });
    await page.waitForTimeout(400);
    if (nom === 'une donnée piégée') await page.evaluate(() => { document.querySelector('#seance details').open = true; });
    const texte = espaces(await page.locator('#section-seance').innerText());
    const manque = attendus.filter((a) => !texte.includes(espaces(a)));
    ok(!manque.length, `fabriqué, ${nom} : ${manque.length ? 'MANQUE « ' + manque.join(' » « ') + ' » — lu : ' + texte.slice(0, 260) : 'la section le dit'}`);
    const sansErreur = erreurs.filter((e) => !/404|500|502|ERR_FAILED|Failed to load resource/.test(e));
    ok(!sansErreur.length, `fabriqué, ${nom} : console sans erreur de script${sansErreur.length ? ' — ' + sansErreur.join(' | ') : ''}`);
    if (nom !== 'une donnée piégée') {
      const pastille = await page.$eval('#seance-date', (e) => getComputedStyle(e).display);
      ok(pastille === 'none', `fabriqué, ${nom} : pas de pastille vide (display ${pastille})`);
    } else {
      const r = await page.evaluate(() => ({ piege: window.__piege === 1, img: !!document.querySelector('#seance img'),
        gras: !!document.querySelector('#seance tbody b') }));
      ok(!r.piege && !r.img && !r.gras, `fabriqué, ${nom} : affichée comme du texte, aucun élément créé (${JSON.stringify(r)})`);
    }
    await contexte.close();
  }
  // Une panne APRÈS une première lecture : la mesure reste, et c'est dit, sans rouge.
  {
    let panne = false;
    const { contexte, page } = await ouvrir(navigateur, TAILLES['ordi-petit'],
      (route) => (panne ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"ok":false,"error":"boum"}' }) : route.continue()));
    await attendreLecture(page);
    panne = true;
    await page.evaluate(() => renderSeance());
    await page.waitForTimeout(400);
    const r = await page.evaluate(() => ({ reste: !!document.querySelector('#seance .seance-notes'),
      etat: document.querySelector('#seance-etat').textContent, pastille: document.querySelector('#seance-date').textContent }));
    ok(r.reste && r.etat.includes('la mesure affichée est la dernière connue') && r.pastille === T.pastille,
      `une panne après une première lecture : la mesure et sa pastille restent, et c'est dit (« ${r.etat} »)`);
    panne = false;
    await page.evaluate(() => renderSeance());
    await page.waitForTimeout(400);
    ok(await page.$eval('#seance-etat', (e) => e.textContent === ''), 'la panne passée : le message s\'efface');
    await contexte.close();
  }
  // 4. La vue baie : la section n'est dans aucun panneau (choix de Mathis : la vue plan seule).
  {
    const nav = await chromium.launch({ channel: 'chrome', args: ['--use-angle=metal', '--ignore-gpu-blocklist'] });
    try {
      const c = await nav.newContext({ viewport: { width: 1440, height: 900 } });
      const p = await c.newPage();
      await p.goto(`${BASE}/?debug`, { waitUntil: 'load' });
      await p.waitForFunction(() => window.__vueBaie && window.__vueBaie.etat.ouverte, null, { timeout: 15000 });
      await p.waitForSelector('#seance .seance-notes', { state: 'attached', timeout: 30000 });
      const r = await p.evaluate(() => ({ dansUnPanneau: !!document.querySelector('.vue3d-panneau #section-seance'),
        panneaux: document.querySelectorAll('.vue3d-panneau').length }));
      ok(r.panneaux === 5 && !r.dansUnPanneau, `vue baie : la section n'est dans aucun des ${r.panneaux} panneaux`);
      await c.close();
    } finally {
      await nav.close();
    }
  }
} finally {
  await navigateur.close();
}
writeFileSync(join(sortie, 'bilan.txt'), lignes.join('\n'));
console.log(lignes.join('\n'));
console.log(`\n${lignes.filter((l) => l.startsWith('NON')).length} NON sur ${lignes.length}`);

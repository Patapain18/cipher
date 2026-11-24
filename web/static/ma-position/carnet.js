/* ============================================================
   ma-position/carnet.js — « Ma position », le carnet de bord
   (piste C, choisie par Mathis le 13/11/2026 ; les pistes A et B
   sont rangées hors du dépôt, dans _pistes-ecartees/)
   ============================================================
   Tes décisions racontées une par une, chacune face au S&P 500.

   Une vue à part. Le bouton « Ma position » de l'en-tête ou la
   touche P l'ouvrent ; Échap ou « Fermer » la referment et rendent
   la page telle qu'on l'avait laissée, défilement compris.

   Pourquoi la vue REMPLACE la page au lieu de la couvrir (un
   <dialog> plein écran par-dessus) : c'est une longue page de
   lecture. Elle défile donc comme une page — molette, barre
   d'espace, capture pleine page — et non comme une boîte qui
   défile dans une autre. La vue plan est retirée (display: none) :
   ni le clavier ni un lecteur d'écran ne peuvent s'y égarer, et le
   « fond inerte » d'un dialogue est obtenu sans rien imiter.

   De haut en bas, comme un carnet :
     1. la position, en une ligne ;
     2. le journal : la raison de chaque décision en titre, et son
        duel avec le S&P 500 ;
     3. le formulaire pour noter la décision suivante ;
     4. la valeur : graphique, seuils, rang, résultats ;
     5. tout en bas, en retrait, les indicateurs — qui ne prédisent
        rien, et le disent.

   Règle de sécurité : tout texte venu du serveur (la raison tapée
   comprise) passe par PP.esc() ou textContent, jamais tel quel.
   ============================================================ */
(() => {
  const PP = window.PP;
  if (!PP) return;   // sans le socle, la page reste exactement la vue plan

  const T = PP.TEXTES;
  const racine = document.documentElement;
  const $ = (sel, dans = document) => dans.querySelector(sel);

  /* ------------------------------------------------------------
     OUTILS
     ------------------------------------------------------------ */

  /* Un nombre déjà formaté (par PP), posé en mono. */
  const num = (texte) => `<span class="num">${PP.esc(texte)}</span>`;

  /* La quantité garde ses décimales utiles et rien de plus : 0,125
     reste 0,125, et 5 s'écrit 5. PP.nombre fixe le nombre de
     décimales, ce qui écrirait « 5,000000 ». */
  const quantite = (q) => Number(q).toLocaleString('fr-FR', { maximumFractionDigits: 6 });

  /* Les phrases communes (PP.TEXTES) arrivent en texte brut. On y
     repère les nombres (« 52,10 € », « +0,71 € », « 22 », « 20:34 »),
     puis on échappe CHAQUE morceau séparément : les mots restent
     exactement ceux du socle, seule la police des chiffres change.
     Chercher les nombres APRÈS l'échappement casserait les entités :
     le « 39 » de « &#39; » (une apostrophe échappée) serait pris pour
     un nombre. « S&P 500 » est un nom, pas un chiffre : il garde la
     police du texte. */
  const NOMBRE = /(?<![\d,:])(?<!S&P\s)[+−-]?\d+(?:[\u00a0\u202f]\d{3})*(?:[,:]\d+)?(?:[\u00a0\u202f]?[€$%])?/g;
  const habiller = (phrase) => {
    /* « S&P » et « 500 » ne se séparent pas en fin de ligne : seule
       l'espace change, pas les mots. */
    const texte = String(phrase ?? '').replace(/S&P 500/g, 'S&P\u00a0500');
    let html = '';
    let fin = 0;
    for (const m of texte.matchAll(NOMBRE)) {
      html += PP.esc(texte.slice(fin, m.index)) + `<span class="num">${PP.esc(m[0])}</span>`;
      fin = m.index + m[0].length;
    }
    return html + PP.esc(texte.slice(fin));
  };

  /* Le curseur est dans un champ : P y écrit un « p », il n'ouvre rien. */
  const enSaisie = (el = document.activeElement) =>
    !!(el && (/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName) || el.isContentEditable));

  /* La vue baie pose cette classe tant qu'elle est ouverte. */
  const vueBaieOuverte = () => document.body.classList.contains('vue3d-active');

  /* L'heure du dernier cours. Un cours d'un autre jour (lundi matin, le
     cours de vendredi soir) dit aussi sa date : « 21:59 » tout court
     laisserait croire que ça vient de bouger. */
  const jourParis = (d) => d.toLocaleDateString('en-CA', { timeZone: 'Europe/Paris' });
  const heureDuCours = (iso) => {
    const d = new Date(iso);
    if (!iso || Number.isNaN(d.getTime())) return '—';
    return jourParis(d) === jourParis(new Date()) ? PP.heure(iso) : PP.dateHeure(iso);
  };

  /* La date d'une décision, sur deux lignes dans la marge du carnet :
     le jour (même forme que PP.dateHeure), puis l'heure. */
  /* D'où vient le dernier cours (le serveur le dit, d'après l'heure de New
     York) : le carnet garde le cours d'avant et d'après-séance, que suit
     aussi le courtier, et il le dit (choix de Mathis, 17/11/2026). */
  const SEANCE_DU_COURS = { avant: 'cours d\'avant-séance de', apres: 'cours d\'après-séance de', ouverte: 'cours de' };
  const SEANCE_ENTRE_PARENTHESES = { avant: ' (avant-séance)', apres: ' (après-séance)' };

  const jourCourt = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('fr-FR',
      { timeZone: 'Europe/Paris', day: 'numeric', month: 'short', year: 'numeric' });
  };

  /* Un pas « rond » pour les repères de prix : 1, 2, 2,5 ou 5 fois une
     puissance de dix. 147,13 € ne se lit pas d'un coup d'œil, 145 € oui. */
  const joliPas = (brut) => {
    const p = 10 ** Math.floor(Math.log10(brut));
    const f = brut / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
  };

  /* ------------------------------------------------------------
     LE BOUTON DE L'EN-TÊTE ET LA NOTE DE LA VUE PLAN
     ------------------------------------------------------------ */

  /* À côté de « Vue baie », dans la même allure : une porte, pas une
     annonce. Ajouté au bout du groupe, donc après la vue baie (et
     après son message d'échec, s'il existe déjà). */
  const bouton = document.createElement('button');
  bouton.type = 'button';
  bouton.className = 'badge pc-bouton';
  bouton.id = 'pc-bouton';
  bouton.setAttribute('aria-keyshortcuts', 'p');
  bouton.title = 'Ta position et le carnet de tes décisions — touche P, Échap pour revenir';
  bouton.innerHTML = 'Ma position<kbd>P</kbd>';
  const cote = $('.masthead > .masthead-side');
  if (cote) cote.appendChild(bouton);

  /* Sous le résumé de la vue plan (et du panneau 1 de la vue baie) :
     où se notent les décisions. La note vit HORS de #positions,
     qu'app.js réécrit chaque minute — dedans, elle disparaîtrait au
     premier tour. */
  const sectionPositions = $('#section-positions');
  if (sectionPositions) {
    const note = document.createElement('p');
    note.className = 'hint pc-note';
    note.innerHTML = 'Tes achats et ventes se notent dans '
      + '<strong>« Ma position »</strong> : touche <kbd>P</kbd> ou bouton de l\'en-tête, en vue plan.';
    sectionPositions.appendChild(note);
  }

  /* ------------------------------------------------------------
     LA VUE — son squelette, écrit une fois
     ------------------------------------------------------------
     Les parties qui bougent (position, journal, graphique,
     indicateurs) ont chacune leur zone, redessinée seule. Le
     formulaire et les boutons de période, eux, ne sont jamais
     réécrits : un champ à moitié rempli ou un bouton qui a le focus
     survivent au rafraîchissement de la minute.
     ------------------------------------------------------------ */
  const PERIODES = { '1j': '1 jour', '1m': '1 mois', '1a': '1 an' };

  /* Le graphique avec Lightweight Charts (plan du 24/11/2026, fin de BRIEF.md). Pendant
     le chantier, seulement avec ?graphe=lc : sans lui, rien n'est chargé et le graphique
     SVG ci-dessous reste celui de la vraie appli. graphe.js dessine ; ce fichier lui
     donne la courbe et le journal, et écrit la phrase sous le graphique. */
  const GRAPHE_LC = new URLSearchParams(location.search).get('graphe') === 'lc';
  const grapheLC = GRAPHE_LC ? chargerGrapheLC() : null;
  let signatureOrdres = '';                 // les décisions déjà passées au graphique
  function chargerGrapheLC() {
    const feuille = document.createElement('link');
    feuille.rel = 'stylesheet';
    feuille.href = 'ma-position/graphe.css';
    document.head.append(feuille);
    const script = (src) => new Promise((ok, ko) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = ok;
      s.onerror = () => ko(new Error(`${src} introuvable`));
      document.head.append(s);
    });
    return script('vendor/lightweight-charts-5.3.0/lightweight-charts.standalone.production.js')
      .then(() => script('ma-position/graphe.js'))
      .then(() => window.PCGraphe);
  }

  /* La vue est le <main> de la page tant qu'elle est ouverte (la vue
     plan, masquée, n'en a pas). Le nom de l'appli y reste le titre de
     niveau 1, comme dans la vue plan dont le h1 est masqué avec elle ;
     « Ma position » est le h2, chaque rubrique un h3, chaque entrée du
     carnet un h4. */
  const vue = document.createElement('main');
  vue.className = 'pc-vue';
  vue.id = 'pc-vue';
  vue.hidden = true;
  vue.setAttribute('aria-labelledby', 'pc-titre');
  vue.innerHTML = `
    <div class="pc-colonne">

      <header class="pc-entete">
        <div class="pc-barre">
          <h1 class="eyebrow pc-surtitre">Signaux actions · carnet de bord</h1>
          <button type="button" class="badge pc-fermer" id="pc-fermer"
                  aria-keyshortcuts="Escape">Fermer<kbd>Échap</kbd></button>
        </div>
        <h2 class="pc-titre" id="pc-titre" tabindex="-1">Ma position</h2>
        <p class="pc-chapeau" id="pc-avertissement">${PP.esc(T.avertissement)}</p>
        <div class="pc-direct" id="pc-direct">
          <p class="pc-attente">Chargement de ta position…</p>
        </div>
      </header>

      <section class="pc-section" aria-labelledby="pc-t-journal">
        <div class="pc-rubrique-ligne">
          <h3 class="pc-rubrique" id="pc-t-journal">Tes décisions</h3>
          <p class="pc-rubrique-note" id="pc-nb-decisions"></p>
        </div>
        <div id="pc-journal"><p class="pc-attente">Chargement du carnet…</p></div>
      </section>

      <section class="pc-section" aria-labelledby="pc-t-noter">
        <h3 class="pc-rubrique" id="pc-t-noter">Noter une décision</h3>
        <form class="pc-form" id="pc-form" autocomplete="off">
          <p class="pc-form-intro">Une décision déjà passée chez ton courtier :
            le carnet la garde, il n'envoie aucun ordre.</p>

          <div class="pc-ligne pc-ligne-raison">
            <label class="pc-marge pc-raison-label" for="pc-raison">Pourquoi ce choix&nbsp;?
              <span>En une phrase.</span></label>
            <div>
              <textarea class="pc-raison-champ" id="pc-raison" name="raison" rows="2"
                        maxlength="200" aria-describedby="pc-raison-compte"></textarea>
              <p class="pc-raison-compte" id="pc-raison-compte"><span class="num"
                 id="pc-raison-n">0</span>&nbsp;/&nbsp;<span class="num">200</span> caractères</p>
            </div>
          </div>

          <div class="pc-ligne pc-ligne-detail">
            <p class="pc-marge pc-detail-label" aria-hidden="true">Le détail</p>
            <div>
              <div class="pc-champs">
                <fieldset class="pc-champ pc-sens">
                  <legend>Sens</legend>
                  <div class="pc-pilules">
                    <label class="pc-pilule"><input type="radio" name="sens" value="achat" checked><span>Achat</span></label>
                    <label class="pc-pilule"><input type="radio" name="sens" value="vente"><span>Vente</span></label>
                  </div>
                </fieldset>
                <div class="pc-champ">
                  <label for="pc-ticker">Valeur</label>
                  <select class="picker" id="pc-ticker" name="ticker">
                    <option value="NVDA" selected>NVDA</option>
                  </select>
                </div>
                <div class="pc-champ">
                  <label for="pc-quantite">Quantité</label>
                  <input class="field num" id="pc-quantite" name="quantite" inputmode="decimal" required>
                </div>
                <div class="pc-champ">
                  <label for="pc-prix">Prix d'une action (€)</label>
                  <input class="field num" id="pc-prix" name="prix_eur" inputmode="decimal" required>
                </div>
                <div class="pc-champ">
                  <label for="pc-montant">Montant (€)</label>
                  <input class="field num" id="pc-montant" name="montant_eur" inputmode="decimal"
                         aria-describedby="pc-aide">
                </div>
                <div class="pc-champ">
                  <label for="pc-frais">Frais (€)</label>
                  <input class="field num" id="pc-frais" name="frais_eur" inputmode="decimal" value="1">
                </div>
                <div class="pc-champ pc-champ-date">
                  <label for="pc-date">Date et heure (Paris)</label>
                  <input class="field num" id="pc-date" name="date" type="datetime-local"
                         aria-describedby="pc-aide">
                </div>
              </div>
              <p class="pc-aide" id="pc-aide">Montant vide : quantité × prix, sans les frais.
                Date vide : maintenant. La date et l'heure sont celles de Paris, même en voyage.</p>
              <div class="pc-envoi">
                <button type="submit" class="btn">Noter cette décision</button>
                <p class="form-msg pc-msg" id="pc-msg" role="status" aria-live="polite"></p>
              </div>
            </div>
          </div>
        </form>
      </section>

      <section class="pc-section" aria-labelledby="pc-t-valeur" id="pc-section-valeur">
        <div class="pc-rubrique-ligne">
          <h3 class="pc-rubrique" id="pc-t-valeur">La valeur</h3>
          <div class="pc-choix-valeur" id="pc-choix-valeur"></div>
        </div>
        <div id="pc-valeur"><p class="pc-attente">Chargement…</p></div>
        <figure class="pc-figure" id="pc-figure" hidden>
          <div class="pc-figure-tete">
            <p class="pc-figure-titre" id="pc-figure-titre">Le cours, en euros</p>
            <div class="pc-segments" role="group" aria-label="Période du graphique">
              ${Object.entries(PERIODES).map(([cle, nom]) => `
                <button type="button" data-periode="${cle}"
                        aria-pressed="${cle === '1j'}">${nom}</button>`).join('')}
            </div>
          </div>
          <div class="pc-graphe" id="pc-graphe" aria-hidden="true"></div>
          <figcaption class="pc-legende" id="pc-legende"></figcaption>
        </figure>
        <div id="pc-faits"></div>
      </section>

      <section class="pc-section pc-section-indicateurs" aria-labelledby="pc-t-indicateurs"
               id="pc-section-indicateurs" hidden>
        <h3 class="pc-rubrique" id="pc-t-indicateurs">Les indicateurs techniques</h3>
        <div id="pc-indicateurs"><p class="pc-attente">Chargement…</p></div>
      </section>

    </div>`;
  ($('.shell') || document.body.lastElementChild).after(vue);

  const titre = $('#pc-titre', vue);
  const form = $('#pc-form', vue);

  /* ------------------------------------------------------------
     L'ÉTAT
     ------------------------------------------------------------ */
  const etat = {
    position: null, erreurPosition: null,
    journal: null, erreurJournal: null,
    ticker: null,          // la valeur montrée dans « La valeur »
    periode: '1j',
    graphes: new Map(),    // « NVDA|1j » → { data, a } : rien ne se recharge pour rien
    valeursRemplies: false,
    toutVoir: false,       // les décisions anciennes du carnet, dépliées ou non
  };
  const ENTREES_VISIBLES = 5;
  /* Ce qu'on a déjà dessiné : un tour qui ramène les mêmes chiffres ne
     réécrit rien (un lecteur d'écran en pleine lecture n'est pas
     interrompu toutes les minutes). */
  const deja = { direct: '', journal: '', valeur: '', choix: '', graphe: null, indicateurs: null };

  /* Le serveur garde le graphique 1 min (1 jour), 10 min (1 mois) et
     1 h (1 an) : on ne lui redemande pas plus souvent. */
  const FRAIS_CACHE = { '1j': 55_000, '1m': 600_000, '1a': 3_600_000 };

  /* Un numéro par chargement : si deux tours se croisent (la minute qui
     tombe pendant un ajout), seule la réponse la plus récente s'affiche. */
  const tickets = { position: 0, journal: 0 };

  /* ------------------------------------------------------------
     1. LA POSITION, EN UNE LIGNE
     ------------------------------------------------------------ */
  function dessinerDirect() {
    const p = etat.position;
    const zone = $('#pc-direct');
    let html;
    if (!p) {
      html = `<p class="pc-attente">${etat.erreurPosition
        ? `Position indisponible pour l'instant (${PP.esc(etat.erreurPosition)}). Nouvel essai dans une minute.`
        : 'Chargement de ta position…'}</p>`;
    } else if (!p.lignes.length) {
      /* Tout vendu : le résultat des ventes reste la réponse à « est-ce que je gagne ? ». */
      const t = p.total;
      const resultat = t?.nombre_ventes > 0 && Number.isFinite(t.gain_eur)
        ? `<span class="pc-sep" aria-hidden="true">·</span>
           <span><span class="${PP.sens(t.gain_eur)}">${num(PP.euros(t.gain_eur, { signe: true }))}
             (${num(PP.pct(t.gain_pct))})</span> depuis le début, frais compris, ventes comprises</span>`
        : '';
      html = `<p class="pc-direct-ligne"><span class="pc-direct-quoi">Ta position</span>
                <span>Aucune action détenue en ce moment.</span>${resultat}</p>`;
    } else {
      const t = p.total;
      const dont = T.dontTotal(t);
      const derniere = [...p.lignes].filter((l) => l.cours_a).sort((a, b) => (a.cours_a < b.cours_a ? -1 : 1)).pop();
      const dernierCours = derniere?.cours_a;
      const quelCours = SEANCE_DU_COURS[derniere?.seance_du_cours] || 'cours de';
      html = `
        <p class="pc-direct-ligne">
          <span class="pc-direct-quoi">Ta position</span>
          <span class="pc-direct-valeur">${num(PP.euros(t.valeur_eur))}</span>
          <span><span class="${PP.sens(t.gain_eur)}">${num(PP.euros(t.gain_eur, { signe: true }))}
            (${num(PP.pct(t.gain_pct))})</span> depuis l'achat, frais compris${dont ? `, ${habiller(dont)}` : ''}</span>
          <span class="pc-sep" aria-hidden="true">·</span>
          <span class="pc-sans-frais">${habiller(T.sansFrais(PP.sansFraisTotal(p), { court: true }))}</span>
          <span class="pc-sep" aria-hidden="true">·</span>
          <span>${PP.esc(quelCours)} ${num(heureDuCours(dernierCours))}</span>
          ${motDirect(p)}
          <span class="pc-sep" aria-hidden="true">·</span>
          <span>${PP.esc(PP.BOURSE[p.bourse] || PP.BOURSE[PP.bourse()])}</span>
        </p>
        ${etat.erreurPosition ? `<p class="pc-direct-erreur">Dernier rafraîchissement impossible
          (${PP.esc(etat.erreurPosition)}) : ces chiffres datent du tour précédent.</p>` : ''}`;
    }
    /* En place : toutes les 2 s pendant le direct, seuls les chiffres changent (le bouton Pause garde le focus). */
    if (html !== deja.direct) { PP.remplacer(zone, html); deja.direct = html; }
  }

  /* Le mot du direct (« en direct », « en pause », « toutes les minutes ») et son
     bouton Pause ; rien avec un ancien serveur (sans le champ `direct`). */
  function motDirect(p) {
    const mot = PP.motDuDirect(p?.direct, { erreur: !!etat.erreurPosition });
    return mot ? `<span class="pc-sep" aria-hidden="true">·</span>
          <span class="pc-direct-mode">${PP.esc(mot)}</span>${PP.boutonPause('pc-pause')}` : '';
  }

  /* ------------------------------------------------------------
     2. LE JOURNAL — une entrée par décision, la plus récente en haut
     ------------------------------------------------------------
     La raison est le TITRE de l'entrée : c'est elle qu'on relira dans
     six mois, et c'est elle que le duel avec le S&P 500 met à l'épreuve.
     ------------------------------------------------------------ */
  function entree(d, i, indice) {
    const achat = d.sens === 'achat';
    const raison = String(d.raison || '').trim();
    const titreEntree = raison
      ? `«\u00a0${PP.esc(raison)}\u00a0»`
      : 'Sans raison notée';

    /* Ce qui a été payé (ou reçu), et à quel prix. Pour une vente, les
       frais sont retenus sur le montant : « dont ». */
    const paye = achat
      ? `${num(PP.euros(d.montant_eur))} + ${num(PP.euros(d.frais_eur))} de frais`
      : `${num(PP.euros(d.montant_eur))}, dont ${num(PP.euros(d.frais_eur))} de frais`;
    const prix = `au prix de ${num(PP.euros(d.prix_eur))} l'action`
      + (d.prix_usd ? ` <span class="pc-discret">(${num(PP.dollars(d.prix_usd))})</span>` : '');

    /* Le duel : un petit registre, comme une addition au bas d'une page
       de carnet — les deux montants alignés, l'écart sous un trait. Puis
       la phrase commune, qui dit la même chose en mots. */
    let duel = '';
    if (achat) {
      const phrase = T.duel(d);
      duel = phrase == null
        ? '<p class="pc-duel-absent">Comparaison avec le S&amp;P&nbsp;500 indisponible pour cette date.</p>'
        : `<div class="pc-duel">
            <p class="pc-duel-titre">Face au S&amp;P&nbsp;500</p>
            <dl class="pc-registre">
              <div><dt>Ton choix (${PP.esc(d.ticker)})</dt><dd>${num(PP.euros(d.valeur_choix_eur))}</dd></div>
              <div><dt>${PP.esc(indice || 'S&P 500')}</dt><dd>${num(PP.euros(d.valeur_indice_eur))}</dd></div>
              <div class="pc-registre-ecart"><dt>Écart</dt>
                <dd class="${PP.sens(d.ecart_eur)}">${num(PP.euros(d.ecart_eur, { signe: true }))}</dd></div>
            </dl>
            <p class="pc-duel-phrase">${habiller(phrase)}</p>
          </div>`;
    }

    return `
      <article class="pc-entree pc-ligne" aria-labelledby="pc-e-${i}">
        <p class="pc-marge pc-entree-date">
          <time datetime="${PP.esc(d.date_utc || d.date)}">${PP.esc(jourCourt(d.date_utc || d.date))}<br>${num(PP.heure(d.date_utc || d.date))}</time>
        </p>
        <div class="pc-entree-corps">
          <p class="pc-entree-sens">${achat ? 'Achat' : 'Vente'} de ${num(quantite(d.quantite))} ${PP.esc(d.ticker)}</p>
          <h4 class="pc-raison${!raison ? ' pc-raison-vide' : raison.length > 90 ? ' pc-raison-longue' : ''}"
              id="pc-e-${i}">${titreEntree}</h4>
          <p class="pc-paye">${paye}, ${prix}.</p>
          ${duel}
        </div>
      </article>`;
  }

  /* Le bilan, dès deux achats : la question même du carnet — « mes
     bonnes occasions font-elles mieux que l'indice ? » —, en faits. */
  function bilan(decisions, b) {
    /* Le serveur compte tout le portefeuille (positions + ventes encaissées)
       contre le même argent passé par le S&P 500 aux mêmes dates. */
    if (b) {
      if (b.achats < 2 && !b.ventes) return '';      // un seul achat : son duel suffit
      const qui = b.devant === 0 ? 'aucun ne vaut' : b.devant === 1 ? '1 vaut' : `${b.devant} valent`;
      /* Le portefeuille : les positions, plus l'argent des ventes pas réinvesti,
         contre l'argent versé, placé dans le S&P 500 aux mêmes dates et gardé.
         Une vente rachetée n'est jamais comptée deux fois. */
      /* Une action offerte entre dans le duel comme un achat de sa valeur : la phrase le dit. */
      const somme = b.offert_eur > 0
        ? `les ${PP.euros(b.verse_eur)} (dont ${PP.euros(b.offert_eur)} d'action offerte)`
        : `les ${PP.euros(b.verse_eur)} versés`;
      const portefeuille = b.encaisse_eur > 0
        ? `Ton portefeuille vaut ${PP.euros(b.toi_eur)} (${PP.euros(b.positions_eur)} de positions et `
          + `${PP.euros(b.encaisse_eur)} de ventes pas réinvesties) ; ${somme}, placés dans le `
          + `S&P 500 aux mêmes dates et gardés, vaudraient ${PP.euros(b.indice_eur)}.`
        : `Ton portefeuille vaut ${PP.euros(b.toi_eur)} ; ${somme}, placés dans le S&P 500 `
          + `aux mêmes dates et gardés, vaudraient ${PP.euros(b.indice_eur)}.`;
      return `<p class="pc-bilan">${habiller(`Sur tes ${b.achats} achat${b.achats > 1 ? 's' : ''}, ${qui} aujourd'hui `
        + `plus que la même somme mise dans le S&P 500 le même jour${b.ventes ? ', ventes comprises' : ''}. ${portefeuille}`)}
        Écart : <span class="${PP.sens(b.ecart_eur)}">${num(PP.euros(b.ecart_eur, { signe: true }))}</span>, frais exclus
        des deux côtés.</p>`;
    }
    const achats = decisions.filter((d) => d.sens === 'achat' && d.valeur_indice_eur != null);
    if (achats.length < 2) return '';
    const mieux = achats.filter((d) => PP.sens(d.ecart_eur) === 'up').length;
    const ecart = achats.reduce((s, d) => s + d.ecart_eur, 0);
    const qui = mieux === 0 ? 'aucun ne vaut' : mieux === 1 ? '1 vaut' : `${mieux} valent`;
    return `<p class="pc-bilan">${habiller(`Sur tes ${achats.length} achats, ${qui} aujourd'hui `
      + 'plus que la même somme mise dans le S&P 500 le même jour.')} Écart cumulé :
      <span class="${PP.sens(ecart)}">${num(PP.euros(ecart, { signe: true }))}</span>.</p>`;
  }

  function dessinerJournal() {
    const j = etat.journal;
    const zone = $('#pc-journal');
    let html;
    let nb = '';
    if (!j) {
      html = `<p class="pc-attente">${etat.erreurJournal
        ? `Carnet indisponible pour l'instant (${PP.esc(etat.erreurJournal)}). Nouvel essai dans une minute.`
        : 'Chargement du carnet…'}</p>`;
    } else if (!j.decisions.length) {
      html = `<p class="pc-vide">Ton carnet est vide. La première décision que tu noteras
        ci-dessous s'inscrira ici, avec sa raison et sa comparaison au S&amp;P&nbsp;500.</p>`;
    } else {
      const recentes = [...j.decisions].reverse();   // le serveur les range de la plus ancienne à la plus récente
      nb = `${num(String(recentes.length))} décision${recentes.length > 1 ? 's' : ''}`;
      /* Les cinq plus récentes, puis les autres repliées : dans un an, le
         carnet en comptera des dizaines, et le formulaire comme le
         graphique, rangés après lui, seraient au bout d'un long défilement.
         Le bilan, lui, compte toujours tous les achats. */
      const entrees = recentes.map((d, i) => entree(d, i, j.indice));
      const anciennes = entrees.slice(ENTREES_VISIBLES);
      html = bilan(recentes, j.bilan) + entrees.slice(0, ENTREES_VISIBLES).join('');
      if (anciennes.length) {
        const n = anciennes.length;
        html += `
          <p class="pc-plus"><button type="button" class="badge pc-plus-bouton" id="pc-plus"
              aria-expanded="${etat.toutVoir}" aria-controls="pc-anciennes">${etat.toutVoir
            ? `Replier ${n > 1 ? `les ${n} décisions plus anciennes` : 'la décision plus ancienne'}`
            : `Voir ${n > 1 ? `les ${n} décisions plus anciennes` : 'la décision plus ancienne'}`}</button></p>
          <div id="pc-anciennes"${etat.toutVoir ? '' : ' hidden'}>${anciennes.join('')}</div>`;
      }
    }
    if (html !== deja.journal) {
      /* Le bouton du repli peut avoir le focus : on le lui rend après la
         réécriture. */
      const focus = zone.contains(document.activeElement) ? document.activeElement.id : '';
      PP.remplacer(zone, html);
      $('#pc-nb-decisions').innerHTML = nb;
      deja.journal = html;
      if (focus) document.getElementById(focus)?.focus({ preventScroll: true });
    }
    remplirValeurs();
  }

  /* La liste du formulaire vient du serveur (les 49 valeurs du bot, plus
     celles détenues hors du bot : ma_position.HORS_DU_BOT), NVDA par défaut. Remplie une seule fois : une liste réécrite sous
     le curseur perdrait le choix en cours. */
  function remplirValeurs() {
    const valeurs = etat.journal?.valeurs;
    if (etat.valeursRemplies || !valeurs?.length) return;
    const select = form.elements.ticker;
    const choisi = select.value || 'NVDA';
    select.innerHTML = valeurs.map((v) =>
      `<option value="${PP.esc(v)}"${v === 'NVDA' ? ' selected' : ''}>${PP.esc(v)}</option>`).join('');
    select.value = valeurs.includes(choisi) ? choisi : 'NVDA';
    etat.valeursRemplies = true;
  }

  /* ------------------------------------------------------------
     4. LA VALEUR — ses chiffres, puis ses repères
     ------------------------------------------------------------ */
  const ligneChoisie = () => etat.position?.lignes.find((l) => l.ticker === etat.ticker) || null;

  function dessinerValeur() {
    const p = etat.position;
    const lignes = p?.lignes || [];

    /* La valeur suivie : celle qu'on avait choisie si elle est toujours
       détenue, sinon la plus grosse ligne — le serveur range les lignes
       par date de premier achat, pas par importance. */
    if (!lignes.some((l) => l.ticker === etat.ticker)) {
      etat.ticker = [...lignes].sort((a, b) => b.valeur_eur - a.valeur_eur)[0]?.ticker || null;
    }

    /* Plusieurs lignes : un bouton par valeur. Réécrit seulement si la
       liste change, pour ne pas voler le focus. */
    const choix = lignes.length > 1 ? lignes.map((l) => l.ticker).join(',') : '';
    if (choix !== deja.choix) {
      $('#pc-choix-valeur').innerHTML = lignes.length > 1
        ? `<div class="pc-segments" role="group" aria-label="Valeur affichée">${lignes.map((l) => `
            <button type="button" data-ticker="${PP.esc(l.ticker)}">${PP.esc(l.ticker)}</button>`).join('')}</div>`
        : '';
      deja.choix = choix;
    }
    vue.querySelectorAll('[data-ticker]').forEach((b) =>
      b.setAttribute('aria-pressed', String(b.dataset.ticker === etat.ticker)));

    const l = ligneChoisie();
    const avecValeur = !!l;
    $('#pc-figure').hidden = !avecValeur;
    $('#pc-section-indicateurs').hidden = !avecValeur;

    let html;
    let faits = '';
    if (!p) {
      html = `<p class="pc-attente">${etat.erreurPosition ? 'Position indisponible pour l\'instant.' : 'Chargement…'}</p>`;
    } else if (!l) {
      html = '<p class="pc-vide">Aucune valeur détenue en ce moment : le graphique et ses repères '
        + 'reviendront avec ton prochain achat.</p>';
    } else {
      const s = T.seuils(l);
      const alerte = T.alerteRang(l);
      /* Une ligne toute offerte part du jour du cadeau, pas d'un achat (relecture du 17/11/2026). */
      const offerte = l.offert_eur > 0 && Math.abs(l.offert_eur - l.montant_eur) < 0.005;
      html = `
        <div class="pc-ligne pc-chiffres">
          <p class="pc-marge pc-chiffres-quoi"><strong>${PP.esc(l.ticker)}</strong><br>
            ${num(quantite(l.quantite))} action${l.quantite >= 2 ? 's' : ''}</p>
          <div>
            <p class="pc-valeur-ligne">
              <span class="pc-valeur-euros">${num(PP.euros(l.valeur_eur))}</span>
              <span class="pc-valeur-dollars">${num(PP.dollars(l.valeur_usd))}</span>
            </p>
            <p class="pc-gain-ligne"><span class="${PP.sens(l.gain_eur)}">${num(PP.euros(l.gain_eur, { signe: true }))}
              (${num(PP.pct(l.gain_pct))})</span> ${offerte ? 'depuis le cadeau' : 'depuis l\'achat, frais compris'}</p>
            <p class="pc-sans-frais-ligne">${habiller(T.sansFrais(PP.sansFrais(l)))}</p>
            <p class="pc-cours-ligne">Cours ${num(PP.euros(l.prix_eur))} à ${num(heureDuCours(l.cours_a))}${PP.esc(SEANCE_ENTRE_PARENTHESES[l.seance_du_cours] || '')}
              <span class="pc-sep" aria-hidden="true">·</span>
              ${num(PP.pct(l.variation_prix_pct))} ${offerte ? 'depuis son prix du jour du cadeau' : 'depuis ton prix d\'achat'} (${num(PP.euros(l.prix_achat_eur))})</p>
          </div>
        </div>`;
      faits = `
        <dl class="pc-faits">
          <div class="pc-ligne"><dt class="pc-marge">Les frais</dt>
            <dd><p>${habiller(s.achat)}</p><p>${habiller(s.vente)}</p></dd></div>
          <div class="pc-ligne"><dt class="pc-marge">Le classement</dt>
            <dd><p>${habiller(T.rang(l))}</p>${alerte ? `<p class="pc-alerte">${habiller(alerte)}</p>` : ''}</dd></div>
          <div class="pc-ligne"><dt class="pc-marge">Les résultats</dt>
            <dd><p>${habiller(T.resultats(l))}</p></dd></div>
        </dl>`;
    }
    const tout = html + faits;
    if (tout !== deja.valeur) {
      PP.remplacer($('#pc-valeur'), html);
      PP.remplacer($('#pc-faits'), faits);
      deja.valeur = tout;
    }
  }

  function dessinerPosition() {
    if (etat.position?.avertissement) $('#pc-avertissement').textContent = etat.position.avertissement;
    dessinerDirect();
    dessinerValeur();
  }

  /* ------------------------------------------------------------
     LES COURBES — du SVG écrit à la main, comme dans app.js
     ------------------------------------------------------------
     Le tracé s'étire à la largeur de la colonne (preserveAspectRatio
     « none ») ; les traits gardent leur épaisseur (non-scaling-stroke).
     Le texte, lui, SORT du SVG : étiré avec le tracé, il se
     déformerait. Les étiquettes sont du HTML posé par-dessus, en
     pourcentages du cadre — même méthode que lineChart d'app.js.
     ------------------------------------------------------------ */
  function tracer(cadre, { courbe, series = [], reperes = [], ticksX = [], cote = 'droite' }) {
    const L = Math.round(cadre.clientWidth) || 800;
    const H = Math.round(cadre.clientHeight) || 240;
    const M = { g: 58, d: 10, h: 14, b: 30 };   // la gouttière gauche porte les prix, le bas les heures

    const valeurs = [...courbe, ...series.flatMap((s) => s.points)].map((p) => p.v)
      .concat(reperes.map((r) => r.v)).filter(Number.isFinite);
    if (courbe.filter((p) => Number.isFinite(p.v)).length < 2) {
      cadre.innerHTML = '<p class="pc-attente">Pas assez de points pour tracer une courbe.</p>';
      return;
    }
    /* L'échelle englobe la courbe ET les repères : ta ligne d'achat
       reste visible même quand le cours en est loin. 12 % d'air en haut
       et en bas : les étiquettes des lignes y tiennent sans toucher le
       cadre ni les heures. */
    let min = Math.min(...valeurs);
    let max = Math.max(...valeurs);
    const air = (max - min) * 0.12 || Math.abs(max) * 0.01 || 1;
    min -= air;
    max += air;
    const X = (x) => M.g + x * (L - M.g - M.d);
    const Y = (v) => M.h + (1 - (v - min) / (max - min)) * (H - M.h - M.b);
    const enX = (px) => `${(100 * px / L).toFixed(3)}%`;
    const enY = (py) => `${(100 * py / H).toFixed(3)}%`;

    /* Un trou dans une série (une moyenne pas encore calculable) lève
       le crayon au lieu de tirer un trait à travers. */
    const trace = (pts) => {
      let d = '';
      let leve = true;
      for (const p of pts) {
        if (!Number.isFinite(p.v)) { leve = true; continue; }
        d += `${leve ? 'M' : 'L'}${X(p.x).toFixed(1)} ${Y(p.v).toFixed(1)}`;
        leve = false;
      }
      return d;
    };

    const pas = joliPas((max - min) / 4);
    const decimales = pas < 1 ? 2 : 0;
    const graduations = [];
    for (let k = Math.ceil(min / pas); k * pas <= max; k++) graduations.push(k * pas);

    const ligneH = (v, classe) => `<line class="${classe}" x1="${M.g}" x2="${L - M.d}"
      y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" vector-effect="non-scaling-stroke"/>`;

    const svg = `
      <svg class="pc-svg" viewBox="0 0 ${L} ${H}" preserveAspectRatio="none" focusable="false">
        ${graduations.map((v) => ligneH(v, 'pc-grille')).join('')}
        ${reperes.map((r) => ligneH(r.v, r.classe)).join('')}
        ${series.map((s) => `<path class="${s.classe}" d="${trace(s.points)}" vector-effect="non-scaling-stroke"/>`).join('')}
        <path class="pc-courbe" d="${trace(courbe)}" vector-effect="non-scaling-stroke"/>
      </svg>`;

    const etiquettesY = graduations.map((v) =>
      `<span class="pc-tick pc-tick-y" style="top:${enY(Y(v))}">${PP.esc(PP.euros(v, { decimales }))}</span>`).join('');
    const etiquettesX = ticksX.map((t) => {
      const ancre = t.x <= 0.001 ? 'debut' : t.x >= 0.999 ? 'fin' : 'milieu';
      return `<span class="pc-tick pc-tick-x pc-${ancre}" style="left:${enX(X(t.x))}">${PP.esc(t.texte)}</span>`;
    }).join('');
    /* Les deux repères sont proches (1 € d'écart sur 147 €) : l'étiquette
       du plus haut se pose AU-DESSUS de sa ligne, celle du plus bas
       EN DESSOUS. Elles ne peuvent donc jamais se chevaucher. */
    const etiquettesReperes = reperes.map((r) => {
      const ancrage = cote === 'droite' ? `right:${enX(M.d)}` : `left:${enX(M.g + 6)}`;
      return `<span class="pc-repere-texte pc-${r.position}" style="top:${enY(Y(r.v))};${ancrage}">${r.texte}</span>`;
    }).join('');
    const fin = [...courbe].reverse().find((p) => Number.isFinite(p.v));
    const point = `<span class="pc-point" style="left:${enX(X(fin.x))};top:${enY(Y(fin.v))}"></span>`;

    cadre.innerHTML = svg + etiquettesY + etiquettesX + etiquettesReperes + point;
  }

  /* L'axe des temps, à l'heure de Paris.
     1 jour : une séance de New York (6 h 30) de bout en bout ; la courbe
     s'arrête là où en est la journée, et le blanc qui suit dit « la
     séance n'est pas finie ». 1 mois et 1 an : un point par barre,
     nuits et week-ends retirés, comme sur tous les graphiques de cours. */
  function abscisses(g) {
    const pts = g.points;
    if (g.periode === '1j') {
      const debut = Date.parse(pts[0].t);
      const fin = debut + 6.5 * 3_600_000;
      const duree = fin - debut;
      const x = (i) => Math.min(1, Math.max(0, (Date.parse(pts[i].t) - debut) / duree));
      /* L'ouverture, chaque heure pleine (Paris est à une heure ronde de
         l'UTC : une heure pleine l'est des deux côtés), la clôture. */
      const ticks = [debut];
      for (let h = Math.ceil(debut / 3_600_000) * 3_600_000; h < fin; h += 3_600_000) {
        if (h - debut >= 45 * 60_000 && fin - h >= 45 * 60_000) ticks.push(h);
      }
      ticks.push(fin);
      return { x, ticks: ticks.map((t) => ({ x: (t - debut) / duree, texte: PP.heure(new Date(t).toISOString()) })) };
    }
    const n = pts.length;
    const x = (i) => (n === 1 ? 0.5 : i / (n - 1));
    const forme = g.periode === '1m' ? { day: 'numeric', month: 'short' } : { month: 'short', year: 'numeric' };
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((r) => {
      const i = Math.round(r * (n - 1));
      return { x: x(i), texte: new Date(pts[i].t).toLocaleDateString('fr-FR', { timeZone: 'Europe/Paris', ...forme }) };
    });
    return { x, ticks };
  }

  /* Le nouveau graphique (?graphe=lc) : graphe.js dessine, ici le titre et la phrase.
     La phrase garde « sur 1 jour / 1 mois / 1 an » (verif_direct.mjs la lit). */
  function resumeGraphe(g, r) {
    const pts = g.points || [];
    if (pts.length < 2) return '';
    let resume = `Cours de ${g.ticker} sur ${PERIODES[g.periode]}, de ${PP.euros(pts[0].eur)} `
      + `à ${PP.euros(pts[pts.length - 1].eur)}`;
    if (g.achat) resume += ` ; ton prix d'achat : ${PP.euros(g.achat.eur)}`;
    if (r && r.ordres) {
      resume += ` ; ${r.ordres} ordre${r.ordres > 1 ? 's' : ''} sur la courbe`;
      if (r.hors) resume += `, dont ${r.hors} passé${r.hors > 1 ? 's' : ''} hors séance, `
        + `posé${r.hors > 1 ? 's' : ''} à la barre qui suit`;
    }
    return `${resume}.`;
  }
  /* Si la bibliothèque ne se charge pas, ou si elle échoue en dessinant, l'ancien
     graphique SVG reprend la place pour toute la session (graphe.js rend d'abord son
     cadre tel qu'il l'a trouvé). */
  let lcEnPanne = false;
  function dessinerGrapheLC(g) {
    $('#pc-figure-titre').textContent = `Le cours de ${g.ticker}, en euros`;
    grapheLC.then((lc) => {
      if (deja.graphe !== g || lcEnPanne) return;   // une courbe plus récente est déjà passée
      try {
        const r = lc.dessiner({ g, decisions: etat.journal?.decisions || [] });
        $('#pc-legende').innerHTML = habiller(resumeGraphe(g, r));
      } catch (e) {
        lc.abandonner();
        throw e;
      }
    }).catch(() => {
      if (lcEnPanne) return;
      lcEnPanne = true;
      if (deja.graphe) dessinerGraphe(deja.graphe);
    });
  }

  function dessinerGraphe(g) {
    if (GRAPHE_LC && !lcEnPanne) { dessinerGrapheLC(g); return; }
    const pts = g.points || [];
    const cadre = $('#pc-graphe');
    $('#pc-figure-titre').textContent = `Le cours de ${g.ticker}, en euros`;
    if (pts.length < 2) {
      cadre.innerHTML = '<p class="pc-attente">Pas assez de points pour tracer une courbe.</p>';
      $('#pc-legende').textContent = '';
      return;
    }
    const { x, ticks } = abscisses(g);
    /* Une action offerte n'a rien coûté : pas de frais à rembourser, et son
       repère est le prix du jour du cadeau (relecture du 17/11/2026). */
    const reperes = !g.achat ? [] : g.achat.offerte ? [
      { v: g.achat.eur, classe: 'pc-repere-achat', position: 'dessous',
        texte: `Prix du jour du cadeau : ${num(PP.euros(g.achat.eur))}` },
    ] : [
      { v: g.achat.seuil_eur, classe: 'pc-repere-seuil', position: 'dessus',
        texte: `Frais remboursés : ${num(PP.euros(g.achat.seuil_eur))}` },
      { v: g.achat.eur, classe: 'pc-repere-achat', position: 'dessous',
        texte: `Ton prix d'achat : ${num(PP.euros(g.achat.eur))}` },
    ];
    /* En 1 jour, la droite du graphique est le reste de la séance, vide :
       les étiquettes s'y posent. En 1 mois et 1 an, la droite porte le
       cours récent, proche de tes lignes : elles passent à gauche. */
    tracer(cadre, { courbe: pts.map((p, i) => ({ x: x(i), v: p.eur })), reperes, ticksX: ticks,
                    cote: g.periode === '1j' ? 'droite' : 'gauche' });

    let resume = `Cours de ${g.ticker} sur ${PERIODES[g.periode]}, de ${PP.euros(pts[0].eur)} `
      + `à ${PP.euros(pts[pts.length - 1].eur)}`;
    if (g.achat) resume += ` ; ton prix d'achat : ${PP.euros(g.achat.eur)}`;
    $('#pc-legende').innerHTML = habiller(`${resume}.`);
  }

  /* Le graphique de la valeur et de la période choisies. Les données
     déjà reçues resservent tant que le serveur ne les a pas renouvelées. */
  /* Les courbes lisent Yahoo (plusieurs secondes quand il traîne). Pendant le direct, le tour
     repasse toutes les 2 s : une seule lecture en vol par courbe, et après un échec, pas de
     nouvel essai automatique avant 30 s (relecture du 24/11/2026 : un graphique en panne était
     redemandé à chaque tour). Un clic (une autre valeur, une autre période) relit tout de suite.
     Une courbe se dessine si elle est TOUJOURS celle choisie quand elle arrive. */
  const enVol = new Set();
  const echecs = new Map();          // « NVDA|1j » → heure du dernier échec
  const ATTENTE_APRES_ECHEC = 30_000;
  const enAttente = (cle, demande) => enVol.has(cle)
    || (!demande && Date.now() - (echecs.get(cle) || 0) < ATTENTE_APRES_ECHEC);

  async function chargerGraphe({ demande = false } = {}) {
    const { ticker, periode } = etat;
    if (!ticker) return;
    const cle = `${ticker}|${periode}`;
    const connu = etat.graphes.get(cle);
    const choisie = () => etat.ticker === ticker && etat.periode === periode;
    /* Déjà dessinée avec ces données : on ne retouche à rien. */
    const dessiner = (data) => { if (deja.graphe !== data) { dessinerGraphe(data); deja.graphe = data; } };
    if (connu) dessiner(connu.data);            // la courbe gardée, le temps de la relire
    if (connu && Date.now() - connu.a < FRAIS_CACHE[periode]) return;
    if (enAttente(cle, demande)) return;
    enVol.add(cle);
    $('#pc-graphe').setAttribute('aria-busy', 'true');
    try {
      const data = await PP.graphique(ticker, periode, { delai: 30_000 });
      const entree = { data, a: Date.now() };
      etat.graphes.set(cle, entree);
      echecs.delete(cle);
      if (choisie()) dessiner(data);
      /* La courbe d'un an porte aussi les indicateurs : ils suivent sans seconde lecture. */
      if (periode === '1a' && etat.ticker === ticker && deja.indicateurs !== entree) {
        dessinerIndicateurs(data);
        deja.indicateurs = entree;
      }
    } catch (e) {
      echecs.set(cle, Date.now());
      if (choisie() && !connu) {
        if (GRAPHE_LC && !lcEnPanne) {
          grapheLC.then((lc) => lc.message(`Cours indisponible pour l'instant (${e.message}).`)).catch(() => {
            $('#pc-graphe').innerHTML = `<p class="pc-attente">Cours indisponible pour l'instant (${PP.esc(e.message)}).</p>`;
          });
        } else {
          $('#pc-graphe').innerHTML = `<p class="pc-attente">Cours indisponible pour l'instant (${PP.esc(e.message)}).</p>`;
        }
        deja.graphe = null;
      }
    } finally {
      enVol.delete(cle);
      if (!enVol.has(`${etat.ticker}|${etat.periode}`)) $('#pc-graphe').removeAttribute('aria-busy');
    }
  }

  /* ------------------------------------------------------------
     5. LES INDICATEURS — en retrait, après l'argent
     ------------------------------------------------------------
     Ils viennent avec la courbe d'un an. Chacun : sa dernière valeur
     dans la marge, sa phrase commune (son nom en tête, en titre), et
     l'étiquette « ne prédit rien ». Aucune zone 30/70, aucun
     histogramme vert ou rouge : rien qui ressemble à un feu.
     ------------------------------------------------------------ */
  function phraseAvecTitre(phrase, id) {
    /* « RSI 14 : compare… » → le nom devient le titre (un h4 affiché en
       ligne), et la phrase continue derrière lui : elle reste entière,
       mot pour mot, et le nom n'est pas écrit deux fois. */
    const m = /^(.+?)(\s:\s[\s\S]+)$/.exec(phrase);
    if (!m) return `<h4 class="pc-ind-nom" id="${id}">${PP.esc(phrase)}</h4>`;
    return `<div class="pc-ind-texte"><h4 class="pc-ind-nom" id="${id}">${PP.esc(m[1])}</h4>${PP.esc(m[2])}</div>`;
  }

  function dessinerIndicateurs(g) {
    const zone = $('#pc-indicateurs');
    const ind = g?.indicateurs;
    if (!ind) {
      zone.innerHTML = '<p class="pc-attente">Indicateurs indisponibles pour l\'instant.</p>';
      return;
    }
    const T2 = T.indicateurs;
    const enEuros = PP.indicateursEnEuros(g);
    const derniere = enEuros[enEuros.length - 1] || {};
    const d = ind.derniers || {};
    const etiquette = `<p class="pc-etiquette">${PP.esc(T2.etiquette)}</p>`;

    zone.innerHTML = `
      <p class="pc-ind-avertissement">${PP.esc(ind.avertissement)}</p>

      <article class="pc-ind pc-ligne" aria-labelledby="pc-ind-mm">
        <div class="pc-marge pc-ind-valeurs">
          <p><span class="pc-ind-cle">50 jours</span>${num(PP.euros(derniere.sma50_eur))}</p>
          <p><span class="pc-ind-cle">200 jours</span>${num(PP.euros(derniere.sma200_eur))}</p>
        </div>
        <div class="pc-ind-corps">
          ${phraseAvecTitre(T2.sma, 'pc-ind-mm')}
          ${etiquette}
          <figure class="pc-figure pc-figure-mm">
            <p class="pc-mm-legende" aria-hidden="true">
              <span><i class="pc-trait pc-trait-cours"></i>le cours</span>
              <span><i class="pc-trait pc-trait-mm50"></i>50 jours</span>
              <span><i class="pc-trait pc-trait-mm200"></i>200 jours</span>
            </p>
            <div class="pc-graphe pc-graphe-mm" id="pc-graphe-mm" aria-hidden="true"></div>
            <figcaption class="pc-legende">${habiller(`Cours de ${g.ticker} sur 1 an et ses moyennes mobiles : `
              + `50 jours à ${PP.euros(derniere.sma50_eur)}, 200 jours à ${PP.euros(derniere.sma200_eur)}, `
              + `en euros.`)}</figcaption>
          </figure>
        </div>
      </article>

      <article class="pc-ind pc-ligne" aria-labelledby="pc-ind-rsi">
        <div class="pc-marge pc-ind-valeurs">
          <p><span class="pc-ind-cle">sur 100</span>${num(PP.nombre(d.rsi, 1))}</p>
        </div>
        <div class="pc-ind-corps">
          ${phraseAvecTitre(T2.rsi, 'pc-ind-rsi')}
          ${etiquette}
        </div>
      </article>

      <article class="pc-ind pc-ligne" aria-labelledby="pc-ind-macd">
        <div class="pc-marge pc-ind-valeurs">
          <p><span class="pc-ind-cle">MACD</span>${num(PP.dollars(d.macd))}</p>
          <p><span class="pc-ind-cle">sa moyenne, 9 séances</span>${num(PP.dollars(d.signal))}</p>
        </div>
        <div class="pc-ind-corps">
          ${phraseAvecTitre(T2.macd, 'pc-ind-macd')}
          ${etiquette}
          <p class="pc-discret">Calculé sur le cours en dollars, d'où le $.</p>
        </div>
      </article>`;

    const { x, ticks } = abscisses(g);
    tracer($('#pc-graphe-mm'), {
      courbe: g.points.map((p, i) => ({ x: x(i), v: p.eur })),
      series: [
        { classe: 'pc-mm200', points: enEuros.map((s, i) => ({ x: x(i), v: s.sma200_eur ?? NaN })) },
        { classe: 'pc-mm50', points: enEuros.map((s, i) => ({ x: x(i), v: s.sma50_eur ?? NaN })) },
      ],
      ticksX: ticks,
    });
  }

  /* La courbe d'un an de la valeur suivie : elle porte les indicateurs.
     Même réserve que le graphique (le serveur la garde une heure). */
  async function chargerIndicateurs({ demande = false } = {}) {
    const ticker = etat.ticker;
    if (!ticker) return;
    const cle = `${ticker}|1a`;
    const connu = etat.graphes.get(cle);
    if (connu && deja.indicateurs !== connu) { dessinerIndicateurs(connu.data); deja.indicateurs = connu; }
    if (connu && Date.now() - connu.a < FRAIS_CACHE['1a']) return;
    if (enAttente(cle, demande)) return;       // déjà en vol (la courbe d'un an), ou en échec il y a moins de 30 s
    enVol.add(cle);
    try {
      const data = await PP.graphique(ticker, '1a', { delai: 30_000 });
      const entree = { data, a: Date.now() };
      etat.graphes.set(cle, entree);
      echecs.delete(cle);
      if (etat.ticker === ticker) {
        dessinerIndicateurs(data);
        deja.indicateurs = entree;
      }
      /* « 1 an » cliqué pendant cette lecture : le clic n'a rien lancé (même courbe en vol), c'est
         donc ici qu'elle se dessine (seconde relecture du 24/11/2026). */
      if (etat.ticker === ticker && etat.periode === '1a' && deja.graphe !== data) {
        dessinerGraphe(data);
        deja.graphe = data;
        $('#pc-graphe').removeAttribute('aria-busy');
      }
    } catch (e) {
      echecs.set(cle, Date.now());
      if (etat.ticker === ticker && !connu) {
        $('#pc-indicateurs').innerHTML = `<p class="pc-attente">Indicateurs indisponibles pour l'instant
          (${PP.esc(e.message)}).</p>`;
      }
    } finally {
      enVol.delete(cle);
    }
  }

  /* ------------------------------------------------------------
     LES CHARGEMENTS ET LE TOUR DE LA MINUTE
     ------------------------------------------------------------ */
  async function chargerPosition() {
    const n = ++tickets.position;
    try {
      const p = await PP.position();
      if (n !== tickets.position) return;
      etat.position = p;
      etat.erreurPosition = null;
    } catch (e) {
      if (n !== tickets.position) return;
      etat.erreurPosition = e.message;
    }
    dessinerPosition();
  }

  /* Le journal ET la position qu'il a utilisée, dans UNE réponse (le direct,
     24/11/2026) : le haut du carnet et le bilan comptent avec les mêmes cours,
     même quand ils changent toutes les secondes. Un ancien serveur (pas de
     `position` dans la réponse) : la position est lue à part, comme avant. */
  async function chargerJournal() {
    const n = ++tickets.journal;
    let j = null;
    try {
      j = await PP.journal({ delai: rapide() ? 5_000 : 30_000 });
      if (n !== tickets.journal) return;
      etat.journal = j;
      etat.erreurJournal = null;
      /* Un ordre noté : les flèches suivent. Le journal est relu à chaque tour (2 s pendant
         le direct) : seulement quand ses décisions ont changé. */
      const signature = (j.decisions || []).map((d) => `${d.date_utc || d.date}|${d.ticker}|${d.prix_eur}`).join(';');
      if (GRAPHE_LC && !lcEnPanne && deja.graphe && signature !== signatureOrdres) {
        signatureOrdres = signature;
        const courbe = deja.graphe;
        grapheLC.then((lc) => {
          const r = lc.ordres(j.decisions);
          if (r && deja.graphe === courbe) $('#pc-legende').innerHTML = habiller(resumeGraphe(courbe, r));
        }).catch(() => {});
      }
      if (j.position) {
        ++tickets.position;
        etat.position = j.position;
        etat.erreurPosition = null;
      }
    } catch (e) {
      if (n !== tickets.journal) return;
      etat.erreurJournal = e.message;
      etat.erreurPosition = e.message;
    }
    if (j && !j.position) await chargerPosition();
    else dessinerPosition();
    dessinerJournal();
  }

  /* Le rythme : toutes les 2 s quand le serveur le dit (le direct, ou le flux
     en train de se connecter), jamais après une lecture ratée (une panne
     relue toutes les 2 s remplirait les journaux du serveur), jamais en mode
     « Papier » (le défi a ses 60 s). La pause est gérée par PP.surveiller. */
  const rapide = () => !etat.erreurJournal && !vue.classList.contains('pa-papier')
    && etat.position?.direct?.rapide === true;

  /* Un tour : le journal et sa position, puis ce qui en dépend (la valeur
     suivie vient de la position). Le graphique d'un jour se renouvelle
     quand le serveur l'a renouvelé (55 s) ; ceux d'un mois et d'un an aussi.
     Rien ne tourne quand la vue est fermée. */
  const estOuverte = () => !vue.hidden;
  async function tour() {
    if (!estOuverte()) return;
    await chargerJournal();
    /* Les courbes partent à côté, sans retenir le tour : un graphique lent figeait les chiffres
       sous « en direct » (relecture du 24/11/2026). */
    chargerGraphe();
    chargerIndicateurs();
  }
  let surveillance = null;
  document.addEventListener('direct:pause', () => { if (estOuverte()) dessinerDirect(); });

  /* ------------------------------------------------------------
     3. NOTER UNE DÉCISION
     ------------------------------------------------------------ */
  const raison = form.elements.raison;
  const majCompte = () => { $('#pc-raison-n').textContent = String(raison.value.length); };
  raison.addEventListener('input', majCompte);

  /* Un champ date vide : WebKit (le moteur de l'appli Mac) y écrit en
     gris une date d'exemple, « 13/11/2026 12:30 », qu'on prendrait pour
     une valeur. On marque le champ vide ; la feuille y affiche
     « maintenant », ce que vaut réellement une date vide. */
  const champDate = form.elements.date;
  const majDate = () => champDate.classList.toggle('pc-date-vide', !champDate.value);
  champDate.addEventListener('input', majDate);
  champDate.addEventListener('change', majDate);
  majDate();

  const message = (texte, genre = '') => {
    const zone = $('#pc-msg');
    zone.textContent = texte;
    zone.className = `form-msg pc-msg ${genre}`;
  };

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const envoyer = form.querySelector('button[type="submit"]');
    const decision = PP.decisionDepuis(form);
    /* Le serveur refuserait de toute façon ; autant dire tout de suite
       ce qui ne va pas, dans les mots du formulaire. */
    if (!Number.isFinite(decision.quantite) || !Number.isFinite(decision.prix_eur)) {
      message('Quantité et prix : des nombres, par exemple 0,5 et 120,50.', 'error');
      return;
    }
    envoyer.disabled = true;         // pas de double enregistrement
    message('Enregistrement…');
    let d = null;
    try {
      d = await PP.ajouterDecision(decision);
    } catch (err) {
      message(err.message, 'error');
    }
    /* Le bouton revient dès que le serveur a répondu, sans attendre la
       relecture des cours (plusieurs secondes quand Yahoo traîne). Un
       bouton désactivé perd le focus, qui retombe sur <body> : on le lui
       rend, sinon le clavier repart du haut de la page. */
    envoyer.disabled = false;
    if (document.activeElement === document.body || !document.activeElement) envoyer.focus();
    if (!d) return;

    const vente = d.sens === 'vente';
    const quoi = `${vente ? 'Vente' : 'Achat'} de ${quantite(d.quantite)} ${d.ticker} noté${vente ? 'e' : ''}`;
    message(`${quoi}. Mise à jour du carnet…`, 'ok');
    form.reset();
    majCompte();
    majDate();
    /* Le serveur a vidé ses caches : on relit tout, graphique compris
       (la ligne d'achat a pu bouger). */
    etat.graphes.clear();
    echecs.clear();
    deja.graphe = null;
    deja.indicateurs = null;
    await tour();
    /* Le message ne change que s'il n'a pas été remplacé entre-temps
       (une autre décision envoyée pendant la relecture). */
    if ($('#pc-msg').textContent.startsWith(quoi)) message(`${quoi} en haut de ton carnet.`, 'ok');
  });

  /* ------------------------------------------------------------
     OUVRIR, FERMER
     ------------------------------------------------------------ */
  let defilementAvant = 0;

  function ouvrir() {
    /* Jamais par-dessus la scène 3D : la vue baie a la priorité. */
    if (estOuverte() || vueBaieOuverte()) return;
    defilementAvant = window.scrollY;
    racine.classList.add('pc-carnet-ouvert');
    /* Une seule zone principale visible à la fois : la vue plan (<main
       class="shell">) passe en hidden, le carnet prend sa place. */
    const plan = $('.shell');
    if (plan) plan.hidden = true;
    vue.hidden = false;
    window.scrollTo(0, 0);
    titre.focus({ preventScroll: true });
    /* Le premier passage lance la surveillance (un tour tout de suite,
       puis toutes les 60 s quand la fenêtre est devant) ; les suivants
       rattrapent : le serveur garde ses réponses en cache, c'est bon
       marché. */
    if (!surveillance) surveillance = PP.surveiller(tour, { vite: rapide });
    else surveillance.maintenant();
    /* Le défi papier (papier.js) se range dans cette vue : il apprend
       ainsi qu'elle vient de s'ouvrir. */
    document.dispatchEvent(new CustomEvent('carnet:ouvert'));
  }

  function fermer() {
    if (!estOuverte()) return;
    vue.hidden = true;
    const plan = $('.shell');
    if (plan) plan.hidden = false;
    racine.classList.remove('pc-carnet-ouvert');
    window.scrollTo(0, defilementAvant);
    bouton.focus({ preventScroll: true });
    document.dispatchEvent(new CustomEvent('carnet:ferme'));
  }

  bouton.addEventListener('click', ouvrir);

  vue.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.id === 'pc-fermer') { fermer(); return; }
    if (b.id === 'pc-plus') {
      etat.toutVoir = !etat.toutVoir;
      dessinerJournal();     // réécrit le repli ; le focus reste sur ce bouton
      return;
    }
    if (b.dataset.periode && b.dataset.periode !== etat.periode) {
      etat.periode = b.dataset.periode;
      vue.querySelectorAll('[data-periode]').forEach((x) =>
        x.setAttribute('aria-pressed', String(x.dataset.periode === etat.periode)));
      chargerGraphe({ demande: true });
      return;
    }
    /* La période déjà choisie, recliquée : la vue entière (après un zoom). */
    if (b.dataset.periode && GRAPHE_LC && !lcEnPanne) {
      grapheLC.then((lc) => lc.vueEntiere()).catch(() => {});
      return;
    }
    if (b.dataset.ticker && b.dataset.ticker !== etat.ticker) {
      etat.ticker = b.dataset.ticker;
      deja.valeur = '';
      dessinerValeur();
      chargerGraphe({ demande: true });
      chargerIndicateurs({ demande: true });
    }
  });

  /* ------------------------------------------------------------
     LE CLAVIER
     ------------------------------------------------------------
     P ouvre (sans modificateur, hors d'un champ, jamais quand la vue
     baie est ouverte). Échap ferme et rend le focus au bouton — sauf
     sur une liste déroulante, à qui on laisse Échap, comme la vue baie.
     ------------------------------------------------------------ */
  document.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'Escape' && estOuverte()) {
      if (document.activeElement && document.activeElement.tagName === 'SELECT') return;
      e.preventDefault();
      fermer();
      return;
    }
    if ((e.key === 'p' || e.key === 'P') && !e.repeat && !estOuverte() && !vueBaieOuverte() && !enSaisie()) {
      e.preventDefault();
      ouvrir();
    }
  });

  /* La touche V ouvre la vue baie (vue3d.js écoute le document). Vue
     ouverte, ce serait déplacer dans la scène des sections masquées
     derrière nous : on arrête la touche avant qu'elle y arrive (phase
     de capture, sur window, donc AVANT l'écouteur de vue3d.js). Le
     caractère s'écrit quand même dans un champ : seule la propagation
     est coupée, pas l'action par défaut. */
  window.addEventListener('keydown', (e) => {
    if (estOuverte() && (e.key === 'v' || e.key === 'V') && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.stopPropagation();
    }
  }, true);

  /* ?vue=carnet ouvre la vue directement (vérifications, captures).
     On attend que la vue baie ait décidé de s'ouvrir ou non : elle
     s'ouvre seule sur un grand écran sans ?plan, et elle garde alors la
     priorité. */
  const ouvrirSiDemande = () => {
    if (new URLSearchParams(location.search).get('vue') === 'carnet') ouvrir();
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(ouvrirSiDemande, 0));
  } else {
    setTimeout(ouvrirSiDemande, 0);
  }
})();

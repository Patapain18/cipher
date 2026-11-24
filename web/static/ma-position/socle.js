/* ============================================================
   ma-position/socle.js — le socle de « Ma position »
   ============================================================
   Les routes, la façon d'écrire les nombres et les dates, et les
   phrases communes (des faits, jamais un conseil). Écrit le 13/11/2026
   pour les trois pistes d'affichage ; le carnet de bord (carnet.js),
   choisi par Mathis, et le résumé d'app.js s'en servent tous les deux :
   ils disent donc la même chose.

   Règle : tout texte venu du serveur s'insère par textContent, ou
   par PP.esc() dans un gabarit. Jamais tel quel dans innerHTML.
   ============================================================ */
window.PP = (() => {
  /* ── Texte sûr ─────────────────────────────────────────── */
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* ── Nombres ───────────────────────────────────────────────
     Espace insécable avant l'unité, vrai signe moins (−), et le
     signe se décide sur la valeur ARRONDIE : −0,004 € s'écrit
     « 0,00 € », pas « −0,00 € ». */
  const NBSP = ' ';
  const vide = (v) => v === null || v === undefined || v === '' || Number.isNaN(Number(v));

  const nombre = (v, decimales = 2) => vide(v) ? '—'
    : Number(v).toLocaleString('fr-FR',
        { minimumFractionDigits: decimales, maximumFractionDigits: decimales });

  function signe(v, decimales, toujours) {
    const r = Number(Number(v).toFixed(decimales));
    if (r > 0) return { r, s: toujours ? '+' : '' };
    if (r < 0) return { r, s: '−' };
    return { r, s: '' };
  }

  function unite(v, suffixe, { signe: toujours = false, decimales = 2 } = {}) {
    if (vide(v)) return '—';
    const { r, s } = signe(v, decimales, toujours);
    return s + nombre(Math.abs(r), decimales) + NBSP + suffixe;
  }

  /** 1234.5 → « 1 234,50 € » ; avec { signe: true } : « +2,10 € » / « −0,05 € ». */
  const euros = (v, o) => unite(v, '€', o);
  /** 110.24 → « 110,24 $ ». */
  const dollars = (v, o) => unite(v, '$', o);
  /** Les pourcentages arrivent en FRACTION (0.0096 = 0,96 %) et portent
      toujours leur signe : « +0,96 % » et « 0,96 % » ne disent pas
      la même chose quand on parcourt une colonne. */
  const pct = (v, { decimales = 2, signe: toujours = true } = {}) =>
    vide(v) ? '—' : unite(Number(v) * 100, '%', { decimales, signe: toujours });
  /** 'up' | 'down' | 'flat' — pour colorer un ÉCART (jamais une valeur). */
  const sens = (v, decimales = 2) => {
    if (vide(v)) return 'flat';
    const r = Number(Number(v).toFixed(decimales));
    return r > 0 ? 'up' : r < 0 ? 'down' : 'flat';
  };
  /** « 12,50 » ou « 12.50 » tapé dans un champ → 12.5 (NaN si illisible). */
  const nombreSaisi = (s) => {
    const t = String(s ?? '').trim().replace(/\s/g, '').replace(',', '.');
    return t === '' ? NaN : Number(t);
  };

  /* ── Dates et heures (toujours à l'heure de Paris) ─────────
     Le journal note l'heure de Paris SANS fuseau
     (« 2026-01-15T10:00:00 ») ; le serveur donne l'UTC AVEC fuseau.
     Le Mac est à Paris : les deux se lisent juste. */
  const PARIS = 'Europe/Paris';
  const enDate = (iso) => {
    if (!iso) return null;
    const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? iso + 'T12:00:00' : iso);
    return Number.isNaN(d.getTime()) ? null : d;
  };
  /** « 15 janv. 2026, 10:00 » */
  const dateHeure = (iso) => {
    const d = enDate(iso);
    return d ? d.toLocaleString('fr-FR', { timeZone: PARIS, day: 'numeric', month: 'short',
      year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
  };
  /** « jeudi 15 janvier » (une date seule est lue à midi) */
  const jour = (iso) => {
    const d = enDate(iso);
    return d ? d.toLocaleDateString('fr-FR', { timeZone: PARIS, weekday: 'long',
      day: 'numeric', month: 'long' }) : '—';
  };
  /** « 16:45 » */
  const heure = (iso) => {
    const d = enDate(iso);
    return d ? d.toLocaleTimeString('fr-FR', { timeZone: PARIS, hour: '2-digit', minute: '2-digit' }) : '—';
  };
  /** Jours pleins d'ici une date « AAAA-MM-JJ » (0 = aujourd'hui), ou null. */
  const joursAvant = (isoJour, maintenant = new Date()) => {
    const d = enDate(isoJour);
    if (!d) return null;
    const auj = new Date(maintenant.toLocaleDateString('en-CA', { timeZone: PARIS }) + 'T12:00:00');
    return Math.round((d - auj) / 86_400_000);
  };

  /** L'état de la Bourse de New York, à l'heure de New York.
      'ouverte' (15 h 30 – 22 h à Paris), 'avant' (10 h – 15 h 30),
      'apres' (22 h – 2 h), 'fermee' (nuit et week-end).
      Les jours fériés américains ne sont pas comptés. */
  function bourse(maintenant = new Date()) {
    const ny = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short',
      hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(maintenant);
    const val = (t) => ny.find((p) => p.type === t)?.value;
    const jourNY = val('weekday');
    const minutes = (Number(val('hour')) % 24) * 60 + Number(val('minute'));
    if (jourNY === 'Sat' || jourNY === 'Sun') return 'fermee';
    if (minutes >= 9 * 60 + 30 && minutes < 16 * 60) return 'ouverte';
    if (minutes >= 4 * 60 && minutes < 9 * 60 + 30) return 'avant';
    if (minutes >= 16 * 60 && minutes < 20 * 60) return 'apres';
    return 'fermee';
  }
  const BOURSE = {
    ouverte: 'Bourse de New York ouverte',
    avant: 'Avant la séance de New York',
    apres: 'Après la séance de New York',
    fermee: 'Bourse de New York fermée',
  };

  /* ── Les routes ────────────────────────────────────────── */
  /** `delai` (ms) : au-delà, la lecture est abandonnée (« le serveur ne répond
      pas »). Le direct en a besoin : sans lui, une lecture bloquée figeait les
      chiffres sous « en direct » (relecture du plan du 24/11/2026). */
  async function lire(chemin, { delai = 0 } = {}) {
    const options = { cache: 'no-store' };
    let minuterie = null;
    if (delai > 0) {
      const ctrl = new AbortController();
      minuterie = setTimeout(() => ctrl.abort(), delai);
      options.signal = ctrl.signal;
    }
    let rep;
    try {
      rep = await fetch(chemin, options);
    } catch (e) {
      if (e?.name === 'AbortError') throw new Error(`le serveur ne répond pas (${Math.round(delai / 1000)} s)`);
      throw e;
    } finally {
      if (minuterie) clearTimeout(minuterie);
    }
    let json;
    try { json = await rep.json(); } catch { throw new Error(`réponse illisible (${rep.status})`); }
    if (!json.ok) throw new Error(json.error || `erreur ${rep.status}`);
    return json.data;
  }
  /** { maj, eurusd, lignes[], total, frais_vente_eur, avertissement, bourse, direct } —
      cache serveur 60 s, 1 s pendant le direct. */
  const position = (options) => lire('/api/position', options);
  /** { decisions[], indice, valeurs[], avertissement, bilan, position } : depuis le
      direct (24/11/2026), le journal rend aussi la position qu'il a utilisée. Le
      carnet prend les deux dans cette réponse : une seule photo des cours. */
  const journal = (options) => lire('/api/journal', options);
  /** periode : '1j' | '1m' | '1a'. Les indicateurs ne viennent qu'avec '1a'. */
  const graphique = (ticker = 'NVDA', periode = '1j') =>
    lire(`/api/graphique?ticker=${encodeURIComponent(ticker)}&periode=${encodeURIComponent(periode)}`);

  /** Note une décision déjà passée chez le courtier. Champs :
      sens ('achat' | 'vente'), ticker, quantite, prix_eur, montant_eur
      (sans les frais), frais_eur, date (« AAAA-MM-JJTHH:MM », heure de
      Paris ; maintenant si vide), raison (200 caractères au plus).
      Renvoie la décision enregistrée, ou lève une Error au message
      lisible (« tu ne détiens pas autant de NVDA »…). */
  async function ajouterDecision(decision) {
    const rep = await fetch('/api/journal/ajouter', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Dashboard': '1' },
      body: JSON.stringify(decision),
    });
    const json = await rep.json().catch(() => ({ ok: false, error: `réponse illisible (${rep.status})` }));
    if (!json.ok) throw new Error(json.error || 'échec de l’enregistrement');
    return json.data;
  }

  /** Lit un formulaire dont les champs portent les noms ci-dessus
      (nombres tapés avec une virgule acceptés). */
  function decisionDepuis(form) {
    const v = (nom) => form.elements[nom]?.value ?? '';
    const decision = {
      sens: v('sens') || 'achat',
      ticker: v('ticker'),
      quantite: nombreSaisi(v('quantite')),
      prix_eur: nombreSaisi(v('prix_eur')),
      montant_eur: nombreSaisi(v('montant_eur')),
      frais_eur: nombreSaisi(v('frais_eur')),
      raison: v('raison').trim(),
    };
    if (Number.isNaN(decision.montant_eur)) delete decision.montant_eur;   // le serveur prend quantité × prix
    if (Number.isNaN(decision.frais_eur)) decision.frais_eur = 1;
    if (v('date')) decision.date = v('date');
    return decision;
  }

  /* ── Le direct en pause (choix de Mathis du 24/11/2026) ────
     Une seule pause pour toute l'appli, gardée d'une ouverture à
     l'autre quand le navigateur le permet. En pause, la page relit
     toutes les 60 s et dit « en pause » (jamais « toutes les minutes » :
     ça ressemblerait à une panne). Les boutons portent
     [data-direct-pause] : un bouton à bascule, son nom ne change pas,
     aria-pressed dit s'il est appuyé. */
  const CLE_PAUSE = 'signaux:direct-pause';
  let pause = false;
  try { pause = localStorage.getItem(CLE_PAUSE) === '1'; } catch { /* stockage refusé : pas gardé */ }
  const enPause = () => pause;
  function basculerPause() {
    pause = !pause;
    try { localStorage.setItem(CLE_PAUSE, pause ? '1' : '0'); } catch { /* pas gardé */ }
    document.querySelectorAll('[data-direct-pause]').forEach((b) => b.setAttribute('aria-pressed', String(pause)));
    document.dispatchEvent(new CustomEvent('direct:pause', { detail: { pause } }));
  }
  document.addEventListener('click', (e) => {
    if (e.target.closest?.('[data-direct-pause]')) basculerPause();
  });
  /** Le bouton, à poser à côté du mot du direct. */
  const boutonPause = (classe = '') => `<button type="button" class="badge direct-pause ${classe}" data-direct-pause
    aria-pressed="${pause}" title="Arrêter ou reprendre le direct : en pause, les cours sont relus toutes les minutes">Pause</button>`;
  /** Le mot qui dit quel système tourne : « en direct », « en pause » ou
      « toutes les minutes » ; '' pour un ancien serveur (sans le champ
      `direct`). `erreur` : la dernière lecture a échoué, le direct ne se
      montre pas. */
  function motDuDirect(direct, { erreur = false } = {}) {
    if (!direct) return '';
    if (pause) return 'en pause';
    return direct.en_direct && !erreur ? 'en direct' : 'toutes les minutes';
  }

  /* ── Remplacer sans tout réécrire ──────────────────────────
     Toutes les 2 s, les chiffres changent. Réécrire le bloc entier
     (innerHTML) ferait perdre une sélection de texte, le focus et la
     place d'un lecteur d'écran (VoiceOver). Quand la forme est la même
     (mêmes balises, dans le même ordre), seuls les textes et les
     attributs changent, EN PLACE ; sinon tout est réécrit. Un <details>
     garde son état ouvert ou fermé. Rend true si quelque chose a changé. */
  function memeForme(a, b) {
    if (a.childNodes.length !== b.childNodes.length) return false;
    for (let i = 0; i < a.childNodes.length; i += 1) {
      const x = a.childNodes[i];
      const y = b.childNodes[i];
      if (x.nodeType !== y.nodeType) return false;
      if (x.nodeType === 1 && (x.tagName !== y.tagName || !memeForme(x, y))) return false;
    }
    return true;
  }
  function copierEnPlace(a, b) {
    for (let i = 0; i < a.childNodes.length; i += 1) {
      const x = a.childNodes[i];
      const y = b.childNodes[i];
      if (x.nodeType !== 1) {
        if (x.nodeValue !== y.nodeValue) x.nodeValue = y.nodeValue;
        continue;
      }
      for (const { name } of [...x.attributes]) {
        if (!y.hasAttribute(name) && !(name === 'open' && x.tagName === 'DETAILS')) x.removeAttribute(name);
      }
      for (const { name, value } of [...y.attributes]) {
        if (name === 'open' && x.tagName === 'DETAILS') continue;
        if (x.getAttribute(name) !== value) x.setAttribute(name, value);
      }
      copierEnPlace(x, y);
    }
  }
  function remplacer(zone, html) {
    if (!zone) return false;
    if (zone.__pp_html === html && zone.childNodes.length) return false;
    const modele = document.createElement('template');
    modele.innerHTML = html;
    if (zone.__pp_html !== undefined && memeForme(zone, modele.content)) copierEnPlace(zone, modele.content);
    else zone.replaceChildren(modele.content);
    zone.__pp_html = html;
    return true;
  }

  /* ── Le rafraîchissement ───────────────────────────────────
     Toutes les 60 s, seulement quand la fenêtre est devant : même
     règle que app.js (document.hidden ne suffit pas dans l'appli Mac,
     une fenêtre dans le Dock reste « visible »). Au retour du focus,
     on rattrape si le dernier tour date de plus d'un tour.
     Avec `vite` (le direct, 24/11/2026) : toutes les 2 s tant que
     vite() est vrai et que le direct n'est pas en pause.
     Renvoie { maintenant() } pour forcer un tour (après un ajout). */
  function surveiller(tour, { toutes = 60_000, vite = null, viteToutes = 2_000 } = {}) {
    let dernier = 0;
    let enCours = null;
    let minuterie = null;
    const devant = () => !document.hidden && document.hasFocus();
    const periode = () => {
      try { return vite && !pause && vite() ? viteToutes : toutes; } catch { return toutes; }
    };
    const planifier = () => {
      clearTimeout(minuterie);
      minuterie = setTimeout(() => { if (devant()) lancer(); else planifier(); }, periode());
    };
    const lancer = () => {
      if (enCours) return enCours;
      dernier = Date.now();
      enCours = Promise.resolve().then(tour).catch((e) => console.warn('[pistes]', e))
        .finally(() => { enCours = null; planifier(); });
      return enCours;
    };
    lancer();
    const rattraper = () => { if (devant() && Date.now() - dernier >= periode()) lancer(); };
    window.addEventListener('focus', rattraper);
    document.addEventListener('visibilitychange', rattraper);
    /* La pause relâchée : on relit tout de suite ; appuyée : le prochain tour passe à 60 s. */
    if (vite) document.addEventListener('direct:pause', () => { if (!pause) rattraper(); else if (!enCours) planifier(); });
    return { maintenant: lancer };
  }

  /* ── Les indicateurs en euros ──────────────────────────────
     Le serveur calcule les moyennes mobiles sur le cours en DOLLARS.
     Pour les poser sur une courbe en euros, chaque point est converti
     au change de son propre jour (p.eur / p.usd). Les séries de '1a'
     sont alignées point par point sur `points`. */
  function indicateursEnEuros(g) {
    if (!g?.indicateurs) return [];
    return g.indicateurs.series.map((s, i) => {
      const p = g.points[i];
      const k = p && p.usd ? p.eur / p.usd : null;
      return { ...s, sma50_eur: s.sma50 != null && k ? s.sma50 * k : null,
                     sma200_eur: s.sma200 != null && k ? s.sma200 * k : null };
    });
  }

  /* ── Sans les frais ────────────────────────────────────────
     Le chiffre que montre l'appli du courtier : la valeur comparée à
     ce que les actions ont coûté SANS les frais (le prix moyen
     d'achat). Le chiffre principal, lui, compte les frais : ce sont
     les euros vraiment sortis du compte. Avec le même cours, ce calcul
     redonne le pourcentage du courtier (vérifié le 15/11/2026). */
  const sansFrais = (l) => ({
    /* Le serveur envoie ce montant déjà arrondi : les lignes s'additionnent
       jusqu'au total au centime (relecture du 17/11/2026). */
    eur: Number.isFinite(l.gain_sans_frais_eur) ? l.gain_sans_frais_eur : l.valeur_eur - l.montant_eur,
    pct: l.montant_eur ? l.valeur_eur / l.montant_eur - 1 : null,
  });
  /** Le total sans les frais d'une réponse de /api/position. Comme le
      chiffre principal (choix de Mathis du 17/11/2026), il garde le
      résultat des ventes et compte une action offerte comme un gain : le
      serveur le donne. Sinon (ancienne réponse), le calcul des lignes. */
  const sansFraisTotal = (position) => (Number.isFinite(position.total?.gain_sans_frais_eur)
    ? { eur: position.total.gain_sans_frais_eur, pct: position.total.gain_sans_frais_pct ?? null }
    : sansFrais({
      valeur_eur: position.lignes.reduce((somme, l) => somme + l.valeur_eur, 0),
      montant_eur: position.lignes.reduce((somme, l) => somme + l.montant_eur, 0),
    }));

  /* ── Les phrases communes ──────────────────────────────────
     Des faits, jamais un conseil : aucune ne dit « achète »,
     « vends », « garde », ni ne laisse entendre ce qui va se passer. */
  const ordinal = (n) => (n === 1 ? '1ᵉʳ' : `${n}ᵉ`);
  /** « de NVDA », mais « d'AAPL » : l'élision devant une voyelle. */
  const de = (ticker) => (/^[AEIOUY]/i.test(ticker) ? `d'${ticker}` : `de ${ticker}`);
  const TEXTES = {
    avertissement: 'Des faits sur ton argent. Aucune prédiction, aucun conseil d\'achat ou de vente.',
    /** Les deux prix qui remboursent les frais. Une ligne toute offerte n'a
        rien coûté : ses chiffres partent du prix du jour du cadeau, comme
        chez le courtier (relecture du 17/11/2026). */
    seuils: (l) => {
      const offerte = l.offert_eur > 0;
      if (offerte && Math.abs(l.offert_eur - l.montant_eur) < 0.005) {
        return {
          achat: `Action offerte : elle ne t'a rien coûté. Ses chiffres partent de son prix du jour du cadeau, `
            + `${euros(l.prix_achat_eur)}, comme chez ton courtier.`,
          vente: `La vendre coûterait ${euros(1, { decimales: 0 })} de frais.`,
        };
      }
      return {
        achat: `À ${euros(l.seuil_achat_eur)}, ta position vaut ce qu'elle t'a coûté, frais d'achat compris`
          + (offerte ? ', l\'action offerte comptée à son prix du jour du cadeau.' : '.'),
        vente: `À ${euros(l.seuil_vente_eur)}, elle couvre aussi le ${euros(1, { decimales: 0 })} d'une vente.`,
      };
    },
    /** « 3ᵉ sur 49 au classement momentum du bot » ; une valeur détenue
        hors des 49 (TSLA, TTWO, ANT) n'a pas de rang, et ne l'attend pas. */
    rang: (l) => (l.suivie_par_le_bot === false
      ? `${l.ticker} n'est pas dans les 49 valeurs du bot : pas de rang au classement momentum.`
      : l.rang == null
        ? 'Rang au classement momentum : en attente du scan du bot.'
        : `${ordinal(l.rang)} sur ${l.classees} au classement momentum du bot`),
    /** Le fait « au-delà du 25ᵉ », ou null. */
    alerteRang: (l) => (l.rang != null && l.rang > 25
      ? `${l.ticker} n'est plus dans la première moitié du classement momentum (${ordinal(l.rang)} sur ${l.classees}).`
      : null),
    /** « Prochains résultats de MSFT : mardi 27 janvier (dans 4 jours) » */
    resultats: (l) => {
      if (!l.resultats) {
        return `Prochains résultats ${de(l.ticker)} : date inconnue`
          + `${l.suivie_par_le_bot === false ? ' (le bot ne suit pas cette valeur)' : ''}.`;
      }
      const n = joursAvant(l.resultats);
      const quand = n == null ? '' : n === 0 ? ' (aujourd\'hui)' : n === 1 ? ' (demain)'
        : n > 1 ? ` (dans ${n} jours)` : '';
      return `Prochains résultats ${de(l.ticker)} : ${jour(l.resultats)}${quand}.`;
    },
    /** « +2,10 € (+3,50 %) sans les frais, comme le calcule ton courtier » ;
        avec { court: true }, sans la fin. */
    sansFrais: (sf, { court = false } = {}) =>
      `${euros(sf.eur, { signe: true })} (${pct(sf.pct)}) sans les frais`
      + (court ? '' : ', comme le calcule ton courtier'),
    /** Ce que le total compte en plus des lignes : « dont +10,00 € d'action
        offerte et −2,63 € sur tes ventes », ou rien. */
    dontTotal: (t) => {
      const parts = [];
      if (t?.offert_eur > 0) parts.push(`${euros(t.offert_eur, { signe: true })} d'action offerte`);
      if (t?.nombre_ventes > 0) parts.push(`${euros(t.ventes_eur, { signe: true })} sur tes ventes`);
      return parts.length ? `dont ${parts.join(' et ')}` : '';
    },
    /** Le duel d'une décision d'achat avec le S&P 500. */
    duel: (d) => (d.valeur_indice_eur == null ? null
      : d.ventes_comprises
        ? `La même somme mise dans le S&P 500 le même jour, puis vendue dans les mêmes proportions, vaudrait `
          + `${euros(d.valeur_indice_eur)}. Ton choix vaut ${euros(d.valeur_choix_eur)}, ventes comprises, `
          + `soit ${euros(d.ecart_eur, { signe: true })} d'écart.`
        : `La même somme mise dans le S&P 500 le même jour vaudrait ${euros(d.valeur_indice_eur)}. `
          + `Ton choix vaut ${euros(d.valeur_choix_eur)}, soit ${euros(d.ecart_eur, { signe: true })} d'écart.`),
    indicateurs: {
      etiquette: 'Mesuré sur 10 ans : ne prédit rien.',
      sma: 'Moyennes mobiles 50 et 200 jours : la moyenne des clôtures sur 50 et 200 séances. '
         + 'Elles lissent le cours et le suivent avec retard.',
      rsi: 'RSI 14 : compare les hausses et les baisses des 14 dernières séances, sur une échelle '
         + 'de 0 à 100. Plus il est bas, plus les baisses récentes ont dominé.',
      macd: 'MACD 12-26-9 : l\'écart entre deux moyennes du cours (12 et 26 séances), comparé à sa '
          + 'propre moyenne sur 9 séances. Il dit si le mouvement récent accélère ou ralentit.',
    },
  };

  return { esc, nombre, ordinal, de, euros, dollars, pct, sens, nombreSaisi, dateHeure, jour, heure,
           joursAvant, bourse, BOURSE, lire, position, journal, graphique, ajouterDecision,
           decisionDepuis, surveiller, indicateursEnEuros, sansFrais, sansFraisTotal, TEXTES,
           enPause, basculerPause, boutonPause, motDuDirect, remplacer };
})();

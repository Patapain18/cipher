/* ============================================================
   pistes/commun.js — le socle des pistes « Ma position »
   ============================================================
   Les trois pistes lisent les mêmes routes, écrivent les nombres de
   la même façon et disent les mêmes phrases : on compare des mises
   en page, pas des calculs ni des formulations.

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
  async function lire(chemin) {
    const rep = await fetch(chemin, { cache: 'no-store' });
    let json;
    try { json = await rep.json(); } catch { throw new Error(`réponse illisible (${rep.status})`); }
    if (!json.ok) throw new Error(json.error || `erreur ${rep.status}`);
    return json.data;
  }
  /** { maj, eurusd, lignes[], total, frais_vente_eur, avertissement } — cache serveur 60 s. */
  const position = () => lire('/api/position');
  /** { decisions[], indice, valeurs[], avertissement } — cache serveur 60 s, comme la
      position : le serveur lit alors le même cours pour les deux. */
  const journal = () => lire('/api/journal');
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

  /* ── Le rafraîchissement ───────────────────────────────────
     Toutes les 60 s, seulement quand la fenêtre est devant : même
     règle que app.js (document.hidden ne suffit pas dans l'appli Mac,
     une fenêtre dans le Dock reste « visible »). Au retour du focus,
     on rattrape si le dernier tour date de plus d'une minute.
     Renvoie { maintenant() } pour forcer un tour (après un ajout). */
  function surveiller(tour, { toutes = 60_000 } = {}) {
    let dernier = 0;
    let enCours = null;
    const lancer = () => {
      if (enCours) return enCours;
      dernier = Date.now();
      enCours = Promise.resolve().then(tour).catch((e) => console.warn('[pistes]', e))
        .finally(() => { enCours = null; });
      return enCours;
    };
    const devant = () => !document.hidden && document.hasFocus();
    lancer();
    setInterval(() => { if (devant()) lancer(); }, toutes);
    const rattraper = () => { if (devant() && Date.now() - dernier >= toutes) lancer(); };
    window.addEventListener('focus', rattraper);
    document.addEventListener('visibilitychange', rattraper);
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

  /* ── Les phrases communes ──────────────────────────────────
     Des faits, jamais un conseil : aucune ne dit « achète »,
     « vends », « garde », ni ne laisse entendre ce qui va se passer. */
  const ordinal = (n) => (n === 1 ? '1ᵉʳ' : `${n}ᵉ`);
  /** « de NVDA », mais « d'AAPL » : l'élision devant une voyelle. */
  const de = (ticker) => (/^[AEIOUY]/i.test(ticker) ? `d'${ticker}` : `de ${ticker}`);
  const TEXTES = {
    avertissement: 'Des faits sur ton argent. Aucune prédiction, aucun conseil d\'achat ou de vente.',
    /** Les deux prix qui remboursent les frais. */
    seuils: (l) => ({
      achat: `À ${euros(l.seuil_achat_eur)}, ta position vaut ce qu'elle t'a coûté, frais d'achat compris.`,
      vente: `À ${euros(l.seuil_vente_eur)}, elle couvre aussi le ${euros(1, { decimales: 0 })} d'une vente.`,
    }),
    /** « 3ᵉ sur 49 au classement momentum du bot » */
    rang: (l) => (l.rang == null
      ? 'Rang au classement momentum : en attente du scan du bot.'
      : `${ordinal(l.rang)} sur ${l.classees} au classement momentum du bot`),
    /** Le fait « au-delà du 25ᵉ », ou null. */
    alerteRang: (l) => (l.rang != null && l.rang > 25
      ? `${l.ticker} n'est plus dans la première moitié du classement momentum (${ordinal(l.rang)} sur ${l.classees}).`
      : null),
    /** « Prochains résultats de MSFT : mardi 27 janvier (dans 4 jours) » */
    resultats: (l) => {
      if (!l.resultats) return `Prochains résultats ${de(l.ticker)} : date inconnue.`;
      const n = joursAvant(l.resultats);
      const quand = n == null ? '' : n === 0 ? ' (aujourd\'hui)' : n === 1 ? ' (demain)'
        : n > 1 ? ` (dans ${n} jours)` : '';
      return `Prochains résultats ${de(l.ticker)} : ${jour(l.resultats)}${quand}.`;
    },
    /** Le duel d'une décision d'achat avec le S&P 500. */
    duel: (d) => (d.valeur_indice_eur == null ? null
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
           decisionDepuis, surveiller, indicateursEnEuros, TEXTES };
})();

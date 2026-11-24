/* ============================================================
   LE GRAPHIQUE DU CARNET — avec Lightweight Charts™ (TradingView)
   ============================================================
   Plan du 24/11/2026, validé par Mathis (fin de BRIEF.md).

   Pendant le chantier, ce fichier n'est chargé qu'avec ?graphe=lc
   (carnet.js, GRAPHE_LC) : la vraie appli garde le graphique SVG.

   La bibliothèque : web/static/vendor/lightweight-charts-5.3.0/
   (Apache 2.0). Sa notice et un lien vers tradingview.com sont
   écrits sous le graphique, comme sa licence le demande.

   Ce que fait ce fichier :
   - la courbe en euros, tes deux lignes (« Ton prix d'achat »,
     « Frais remboursés ») et leurs étiquettes en HTML ;
   - tes ordres, une flèche chacun, posée à ton prix ;
   - le zoom et le glisser (la molette seulement après un clic sur
     le graphique), le prix et l'heure sous le curseur, le clavier.
   Il ne lit rien sur le réseau : carnet.js lui donne les cours et
   le journal.
   ============================================================ */
window.PCGraphe = (() => {
  'use strict';

  const LWC = window.LightweightCharts;
  const PARIS = 'Europe/Paris';
  const NY = 'America/New_York';
  const DUREE_BARRE = { '1j': 300, '1m': 3600 };   // en secondes ; 1 an : une barre par jour
  const SEANCE = 6.5 * 3600;                         // 9 h 30 – 16 h à New York
  const PERIODES = { '1j': '1 jour', '1m': '1 mois', '1a': '1 an' };

  /* Les formats sont créés une fois : créer un Intl.DateTimeFormat est lent. */
  const fr = (o) => new Intl.DateTimeFormat('fr-FR', { timeZone: PARIS, ...o });
  const F = {
    heure: fr({ hour: '2-digit', minute: '2-digit' }),
    jourMois: fr({ day: 'numeric', month: 'short' }),
    mois: fr({ month: 'short' }),
    an: fr({ year: 'numeric' }),
    jourCourt: fr({ weekday: 'short', day: 'numeric', month: 'short' }),
    date: fr({ day: 'numeric', month: 'short', year: 'numeric' }),
    dateNY: new Intl.DateTimeFormat('en-CA', { timeZone: NY, year: 'numeric', month: '2-digit', day: '2-digit' }),
    partiesNY: new Intl.DateTimeFormat('en-US', { timeZone: NY, weekday: 'short', hour: '2-digit',
                                                   minute: '2-digit', hourCycle: 'h23' }),
  };
  const enDate = (secondes) => new Date(secondes * 1000);
  const jourNY = (secondes) => F.dateNY.format(enDate(secondes));

  /* L'heure de New York d'un instant : le jour de la semaine et les minutes depuis minuit. */
  function ny(secondes) {
    const p = Object.fromEntries(F.partiesNY.formatToParts(enDate(secondes)).map((x) => [x.type, x.value]));
    return { semaine: p.weekday, minutes: Number(p.hour) * 60 + Number(p.minute) };
  }
  /* En séance normale de New York : en semaine, de 9 h 30 à 16 h (les jours fériés ne sont
     pas connus, comme dans PP.bourse). */
  function enSeance(secondes) {
    const { semaine, minutes } = ny(secondes);
    return semaine !== 'Sat' && semaine !== 'Sun' && minutes >= 570 && minutes < 960;
  }

  /* La bibliothèque reçoit les vraies heures UTC ; ce sont ces formats qui écrivent
     l'heure de Paris. Paris est à une heure ronde de l'UTC, et une séance de New York
     tient dans le même jour des deux côtés : les graduations tombent juste. 1 an : une
     barre par jour, posée à midi UTC du jour de New York. */
  function texteTemps(secondes, periode, long = false) {
    const d = enDate(secondes);
    if (periode === '1a') return F.date.format(d);
    if (periode === '1j') return F.heure.format(d);
    return long ? `${F.jourCourt.format(d)}, ${F.heure.format(d)}` : F.heure.format(d);
  }
  /* TickMarkType de la bibliothèque : 0 année, 1 mois, 2 jour du mois, 3 heure. */
  const graduation = (periode) => (temps, type) => {
    const d = enDate(temps);
    if (periode === '1a') return type === 0 ? F.an.format(d) : type === 1 ? F.mois.format(d) : F.jourMois.format(d);
    return type <= 2 ? F.jourMois.format(d) : F.heure.format(d);
  };

  /* Les couleurs et la police du carnet, lues dans ses variables CSS (pas de mode sombre). */
  function style(cadre) {
    const s = getComputedStyle(document.documentElement);
    const v = (nom, repli) => s.getPropertyValue(nom).trim() || repli;
    return {
      texte: v('--text', '#14120f'),
      discret: v('--text-muted', '#6b6459'),
      bord: v('--border', '#ddd7cb'),
      accent: v('--accent', '#24476e'),
      neutre: v('--neutral', '#8a8172'),
      police: getComputedStyle(cadre).fontFamily || 'system-ui, sans-serif',
    };
  }

  /* ------------------------------------------------------------
     LES DONNÉES
     ------------------------------------------------------------ */

  /* Les points du serveur → des barres triées, sans doublon (le dernier gagne), sans
     prix nul ou illisible : la bibliothèque refuse des heures qui ne montent pas. */
  function barresDe(g) {
    const parTemps = new Map();
    for (const p of g.points || []) {
      const ms = Date.parse(p.t);
      const v = Number(p.eur);
      if (!Number.isFinite(ms) || !Number.isFinite(v) || v <= 0) continue;
      let t = Math.floor(ms / 1000);
      if (g.periode === '1a') {
        const [a, m, j] = jourNY(t).split('-').map(Number);
        t = Date.UTC(a, m - 1, j, 12) / 1000;
      }
      parTemps.set(t, v);
    }
    return [...parTemps].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time, value }));
  }

  /* 1 jour : des points vides jusqu'à 16 h à New York, sur la grille de 5 minutes de la
     première barre. Le blanc qui suit la courbe dit « la séance n'est pas finie ». */
  function avecLeReste(barres, periode) {
    if (periode !== '1j' || !barres.length) return barres;
    const debut = barres[0].time;
    const fin = debut + SEANCE;
    const derniere = barres[barres.length - 1].time;
    const vides = [];
    for (let t = debut + 300 * (Math.floor((derniere - debut) / 300) + 1); t < fin; t += 300) vides.push({ time: t });
    return barres.concat(vides);
  }

  /* Tes ordres de cette valeur, chacun sur sa barre :
     - un ordre passé pendant une barre va sur elle ;
     - un ordre hors séance (avant 15 h 30 ou après 22 h, heure de Paris) va sur la
       première barre qui suit son heure ; s'il n'y en a pas, sur la dernière ;
     - un ordre d'avant le jour de la première barre n'est pas montré (la bibliothèque,
       seule, le collerait sur la première barre).
     Le choix de la barre se fait en UTC, avant tout format d'heure. */
  function placerOrdres(barres, decisions, ticker, periode) {
    const ordres = [];
    let hors = 0;
    if (!barres.length) return { ordres, hors };
    const jours = barres.map((b) => jourNY(b.time));
    const premierJour = jours[0];
    for (const d of decisions || []) {
      if (d.ticker !== ticker) continue;
      const ms = Date.parse(d.date_utc || d.date);
      const prix = Number(d.prix_eur);
      if (!Number.isFinite(ms) || !Number.isFinite(prix) || prix <= 0) continue;
      const q = Math.floor(ms / 1000);
      const jour = jourNY(q);
      if (jour < premierJour) continue;
      const seance = enSeance(q);
      let i = -1;
      if (periode === '1a') {
        const apresLaCloture = !seance && ny(q).minutes >= 960;
        i = jours.findIndex((j) => (apresLaCloture ? j > jour : j >= jour));
      } else {
        let k = -1;
        while (k + 1 < barres.length && barres[k + 1].time <= q) k += 1;
        if (k >= 0 && q < barres[k].time + DUREE_BARRE[periode]) i = k;
        else if (k + 1 < barres.length) i = k + 1;
      }
      if (i < 0) i = barres.length - 1;
      if (!seance) hors += 1;
      ordres.push({ i, time: barres[i].time, prix, achat: d.sens === 'achat', quand: ms });
    }
    ordres.sort((a, b) => a.time - b.time || a.quand - b.quand);
    return { ordres, hors };
  }

  /* ------------------------------------------------------------
     L'ÉTAT ET LE BALISAGE
     ------------------------------------------------------------ */
  let el = null;
  let st = vierge();
  function vierge() {
    return { chart: null, serie: null, marques: null, cle: '', periode: '1j', ticker: '',
             barres: [], nTotal: 0, indexDe: new Map(), ordres: [], hors: 0, reperes: [], cote: 'droite',
             idx: null, clavier: false, actif: false, attente: null, c: null };
  }

  /* Le cadre #pc-graphe devient un groupe pris au clavier ; la zone du canvas, dedans,
     reste cachée aux lecteurs d'écran (le résumé #pc-legende est sa description). */
  function preparer() {
    if (el) return el;
    const cadre = document.getElementById('pc-graphe');
    cadre.classList.add('pc-graphe-lc');
    cadre.removeAttribute('aria-hidden');
    cadre.tabIndex = 0;
    cadre.setAttribute('role', 'group');
    cadre.setAttribute('aria-label',
      'Graphique du cours. Flèches : lire les points ; plus et moins : zoomer ; zéro : vue entière.');
    cadre.setAttribute('aria-describedby', 'pc-legende');
    cadre.textContent = '';
    const zone = document.createElement('div');
    zone.className = 'pc-lc';
    zone.setAttribute('aria-hidden', 'true');
    const etiquettes = document.createElement('div');
    etiquettes.className = 'pc-lc-etiquettes';
    etiquettes.setAttribute('aria-hidden', 'true');
    cadre.append(zone, etiquettes);

    const curseur = document.createElement('p');
    curseur.className = 'pc-curseur';
    curseur.id = 'pc-curseur';
    cadre.before(curseur);

    const msg = document.createElement('p');
    msg.className = 'pc-attente pc-graphe-msg';
    msg.id = 'pc-graphe-msg';
    msg.hidden = true;
    cadre.after(msg);

    /* La notice du fichier NOTICE et le lien vers tradingview.com (licence de la bibliothèque). */
    const credit = document.createElement('p');
    credit.className = 'pc-credit';
    credit.innerHTML = 'Graphique : <a href="https://www.tradingview.com/" target="_blank" '
      + 'rel="noopener noreferrer">TradingView Lightweight Charts™</a>, © 2025 TradingView, Inc.';
    document.getElementById('pc-legende').before(credit);

    cadre.addEventListener('pointerdown', () => activer(true));
    cadre.addEventListener('focus', () => activer(true));
    cadre.addEventListener('blur', () => { activer(false); st.clavier = false; });
    cadre.addEventListener('mouseleave', () => activer(false));
    cadre.addEventListener('keydown', clavier);
    new ResizeObserver(() => {
      if (st.attente && zone.clientWidth > 0) { const a = st.attente; st.attente = null; dessiner(a); }
      requestAnimationFrame(placerEtiquettes);
    }).observe(zone);

    el = { cadre, zone, etiquettes, curseur, msg, credit };
    return el;
  }

  /* La molette zoome seulement quand le graphique est pris (un clic, ou Tab) ; sinon elle
     fait défiler le carnet. Échap, sortir du graphique ou le quitter la rend à la page. */
  function activer(oui) {
    st.actif = oui;
    if (st.chart) st.chart.applyOptions({ handleScale: { mouseWheel: oui }, handleScroll: { mouseWheel: oui } });
  }

  function detruire() {
    if (st.chart) st.chart.remove();
    const { attente } = st;
    st = vierge();
    st.attente = attente;
    if (el) el.etiquettes.textContent = '';
  }

  /* ------------------------------------------------------------
     LE DESSIN
     ------------------------------------------------------------ */

  /* L'échelle englobe la courbe, tes deux lignes et les prix des ordres visibles, comme
     le graphique d'avant : la bibliothèque, seule, les laisserait sortir du cadre. */
  function elargir(origine) {
    if (!origine || !origine.priceRange) return origine;
    let { minValue: min, maxValue: max } = origine.priceRange;
    const plage = st.chart ? st.chart.timeScale().getVisibleLogicalRange() : null;
    const prix = st.reperes.map((r) => r.prix).concat(st.ordres
      .filter((o) => !plage || (o.i >= Math.floor(plage.from) && o.i <= Math.ceil(plage.to)))
      .map((o) => o.prix));
    for (const p of prix) {
      if (p < min) min = p;
      if (p > max) max = p;
    }
    return { ...origine, priceRange: { minValue: min, maxValue: max } };
  }

  function creer(g, c) {
    detruire();
    const chart = LWC.createChart(el.zone, {
      autoSize: true,
      layout: { background: { type: LWC.ColorType.Solid, color: 'rgba(0,0,0,0)' }, textColor: c.discret,
                fontFamily: c.police, fontSize: 11, attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: c.bord } },
      /* entireTextOnly : pas de graduation à moitié coupée en haut ou en bas de l'axe. */
      leftPriceScale: { visible: true, borderVisible: false, entireTextOnly: true,
                        scaleMargins: { top: 0.12, bottom: 0.12 } },
      rightPriceScale: { visible: false },
      /* 12 px après la dernière barre : ses flèches n'y sont pas coupées. Le bord droit
         n'est pas « fixé » : fixé, la bibliothèque refuse toute marge (_maxRightOffset). */
      timeScale: { borderVisible: false, timeVisible: g.periode !== '1a', secondsVisible: false,
                   fixLeftEdge: true, fixRightEdge: false, rightOffsetPixels: 12,
                   lockVisibleTimeRangeOnResize: true, tickMarkFormatter: graduation(g.periode) },
      crosshair: { mode: LWC.CrosshairMode.Magnet,
                   vertLine: { color: c.neutre, width: 1, style: LWC.LineStyle.Dotted, labelVisible: false },
                   horzLine: { visible: false, labelVisible: false } },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: false, axisDoubleClickReset: false },
      kineticScroll: { mouse: false, touch: false },
      /* L'axe des prix : « 600 € » quand toutes ses graduations sont rondes, sinon
         « 372,50 € » pour toutes (jamais un mélange des deux). */
      localization: { locale: 'fr-FR', priceFormatter: (p) => PP.euros(p),
                      tickmarksPriceFormatter: (prix) => {
                        const ronds = prix.every((p) => Math.abs(p - Math.round(p)) < 0.005);
                        return prix.map((p) => PP.euros(p, { decimales: ronds ? 0 : 2 }));
                      },
                      timeFormatter: (t) => texteTemps(t, g.periode, true) },
    });
    const serie = chart.addSeries(LWC.LineSeries, {
      color: c.texte, lineWidth: 2, priceScaleId: 'left',
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: true, crosshairMarkerRadius: 3,
      crosshairMarkerBorderColor: c.texte, crosshairMarkerBackgroundColor: c.texte,
      priceFormat: { type: 'custom', formatter: (p) => PP.euros(p), minMove: 0.01 },
      autoscaleInfoProvider: (origine) => elargir(origine()),
    });
    st.chart = chart;
    st.serie = serie;
    st.marques = LWC.createSeriesMarkers(serie, []);
    st.c = c;
    chart.subscribeCrosshairMove(survol);
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => requestAnimationFrame(placerEtiquettes));
    if (st.actif) activer(true);
  }

  /* Tes deux lignes : dessinées sans titre par la bibliothèque ; leurs étiquettes restent
     en HTML (comme avant), l'une au-dessus et l'autre en dessous de sa ligne. */
  function reperesDe(achat) {
    if (!achat) return [];
    if (achat.offerte) {
      return [{ prix: achat.eur, pointilles: false, position: 'dessous',
                texte: `Prix du jour du cadeau : ${PP.euros(achat.eur)}` }];
    }
    return [
      { prix: achat.seuil_eur, pointilles: true, position: 'dessus', texte: `Frais remboursés : ${PP.euros(achat.seuil_eur)}` },
      { prix: achat.eur, pointilles: false, position: 'dessous', texte: `Ton prix d'achat : ${PP.euros(achat.eur)}` },
    ].filter((r) => Number.isFinite(r.prix));
  }

  function poserLignes() {
    for (const r of st.reperes) {
      r.ligne = st.serie.createPriceLine({
        price: r.prix, color: st.c.accent, lineWidth: 1,
        lineStyle: r.pointilles ? LWC.LineStyle.Dashed : LWC.LineStyle.Solid,
        axisLabelVisible: false, title: '',
      });
    }
    el.etiquettes.textContent = '';
    for (const r of st.reperes) {
      const span = document.createElement('span');
      span.className = `pc-repere-texte pc-${r.position}`;
      span.textContent = r.texte;
      el.etiquettes.append(span);
      r.span = span;
    }
  }

  /* En 1 jour, la droite du graphique est le reste de la séance, vide : les étiquettes s'y
     posent. En 1 mois et 1 an, la droite porte le cours récent : elles passent à gauche. */
  function placerEtiquettes() {
    if (!st.chart || !el) return;
    const hauteur = el.zone.clientHeight - st.chart.timeScale().height();
    const gauche = st.chart.priceScale('left').width() + 6;
    for (const r of st.reperes) {
      if (!r.span) continue;
      const y = st.serie.priceToCoordinate(r.prix);
      const dedans = y !== null && y >= 0 && y <= hauteur;
      r.span.hidden = !dedans;
      if (!dedans) continue;
      r.span.style.top = `${y}px`;
      r.span.style.left = st.cote === 'gauche' ? `${gauche}px` : '';
      r.span.style.right = st.cote === 'droite' ? '8px' : '';
    }
  }

  function marquesDe(ordres) {
    return ordres.map((o) => ({
      time: o.time,
      position: o.achat ? 'atPriceBottom' : 'atPriceTop',
      price: o.prix,
      shape: o.achat ? 'arrowUp' : 'arrowDown',
      color: o.achat ? st.c.accent : st.c.neutre,
      size: 1,
    }));
  }

  /* La ligne au-dessus du graphique : le point sous le curseur, sinon la fin de la courbe. */
  function ecrireCurseur(i) {
    if (!el) return;
    const b = i == null ? st.barres[st.barres.length - 1] : st.barres[i];
    if (!b) { el.curseur.textContent = ''; return; }
    let texte = `${i == null ? 'Fin de la courbe : ' : ''}${texteTemps(b.time, st.periode, true)} · ${PP.euros(b.value)}`;
    const ici = i == null ? [] : st.ordres.filter((o) => o.time === b.time);
    if (ici.length) texte += ` · ${ici.map((o) => `${o.achat ? 'achat' : 'vente'} à ${PP.euros(o.prix)}`).join(', ')}`;
    el.curseur.textContent = texte;
  }

  /* Au clavier, le curseur ne suit que les touches : un zoom ou un rafraîchissement fait
     repasser la bibliothèque sur le curseur (sans événement de souris), ce qui changerait
     sa barre. La vraie souris ou le doigt (sourceEvent) reprennent la main. */
  function survol(p) {
    if (p.sourceEvent) st.clavier = false;
    else if (st.clavier) return;
    const v = p.time !== undefined ? p.seriesData.get(st.serie) : undefined;
    const i = v && Number.isFinite(v.value) ? st.indexDe.get(p.time) : undefined;
    st.idx = i ?? null;
    ecrireCurseur(st.idx);
  }

  /* Remet le curseur du clavier sur sa barre (après un zoom ou un rafraîchissement). */
  function reposerCurseur() {
    if (!st.clavier || st.idx === null || !st.barres[st.idx]) return;
    const b = st.barres[st.idx];
    st.chart.setCrosshairPosition(b.value, b.time, st.serie);
    ecrireCurseur(st.idx);
  }

  /* ------------------------------------------------------------
     LE CLAVIER
     ← → : une barre ; Début, Fin : les bouts ; + et − : zoomer ;
     0 : la vue entière ; Échap : rend la molette à la page (un
     second Échap ferme le carnet, comme ailleurs).
     ------------------------------------------------------------ */
  function poserCurseur(i) {
    const b = st.barres[i];
    if (!b) return;
    st.idx = i;
    st.clavier = true;
    const ts = st.chart.timeScale();
    const plage = ts.getVisibleLogicalRange();
    if (plage && (i < plage.from || i > plage.to)) {
      const demi = (plage.to - plage.from) / 2;
      ts.setVisibleLogicalRange({ from: i - demi, to: i + demi });
    }
    st.chart.setCrosshairPosition(b.value, b.time, st.serie);
    ecrireCurseur(i);
  }

  function zoomer(facteur) {
    const ts = st.chart.timeScale();
    const plage = ts.getVisibleLogicalRange();
    if (!plage) return;
    const centre = st.idx ?? (plage.from + plage.to) / 2;
    const demi = Math.max(5, ((plage.to - plage.from) / 2) * facteur);
    /* Pas de vide après la fin de la courbe (le bord droit n'est pas fixé, pour sa marge). */
    const decalage = Math.max(0, centre + demi - (st.nTotal - 0.5));
    ts.setVisibleLogicalRange({ from: centre - demi - decalage, to: centre + demi - decalage });
    requestAnimationFrame(reposerCurseur);
  }

  function clavier(e) {
    if (!st.chart || e.metaKey || e.ctrlKey || e.altKey) return;
    const n = st.barres.length;
    const ici = st.idx ?? n - 1;
    let fait = true;
    switch (e.key) {
      case 'ArrowLeft': poserCurseur(Math.max(0, ici - 1)); break;
      case 'ArrowRight': poserCurseur(Math.min(n - 1, ici + 1)); break;
      case 'Home': poserCurseur(0); break;
      case 'End': poserCurseur(n - 1); break;
      case '+': case '=': zoomer(0.75); break;
      case '-': case '_': zoomer(1 / 0.75); break;
      case '0': vueEntiere(); break;
      case 'Escape':
        if (st.actif || st.idx !== null) {
          activer(false);
          st.clavier = false;
          st.idx = null;
          st.chart.clearCrosshairPosition();
          ecrireCurseur(null);
        } else fait = false;
        break;
      default: fait = false;
    }
    if (fait) e.preventDefault();
  }

  /* ------------------------------------------------------------
     CE QUE CARNET.JS APPELLE
     ------------------------------------------------------------ */

  /* Dessine la courbe g (réponse de /api/graphique) avec tes ordres. Une autre valeur ou
     une autre période recrée le graphique ; la même courbe, rafraîchie, garde le zoom.
     Rend { ordres, hors } pour la phrase sous le graphique. */
  function dessiner({ g, decisions }) {
    preparer();
    const barres = barresDe(g);
    if (barres.length < 2) {
      detruire();
      message('Pas assez de points pour tracer une courbe.');
      return { ordres: 0, hors: 0 };
    }
    if (el.zone.clientWidth === 0) {            // carnet caché, fenêtre hors de l'écran : plus tard
      st.attente = { g, decisions };
      const { ordres, hors } = placerOrdres(barres, decisions, g.ticker, g.periode);
      return { ordres: ordres.length, hors };
    }
    el.msg.hidden = true;
    el.cadre.hidden = false;
    const cle = `${g.ticker}|${g.periode}`;
    const memeCourbe = st.chart && st.cle === cle;
    const plage = memeCourbe ? st.chart.timeScale().getVisibleLogicalRange() : null;
    const toutVu = !plage || (plage.from <= 0.5 && plage.to >= st.nTotal - 1.5);   // pas de zoom à garder
    if (!memeCourbe) creer(g, style(el.cadre));
    st.cle = cle;
    st.periode = g.periode;
    st.ticker = g.ticker;
    st.cote = g.periode === '1j' ? 'droite' : 'gauche';
    st.barres = barres;
    st.indexDe = new Map(barres.map((b, i) => [b.time, i]));
    ({ ordres: st.ordres, hors: st.hors } = placerOrdres(barres, decisions, g.ticker, g.periode));
    for (const r of st.reperes) if (r.ligne) st.serie.removePriceLine(r.ligne);
    st.reperes = reperesDe(g.achat);
    try {
      const donnees = avecLeReste(barres, g.periode);
      st.serie.setData(donnees);
      st.nTotal = donnees.length;
    } catch (e) {
      message(`Courbe illisible (${e.message}).`);
      return { ordres: st.ordres.length, hors: st.hors };
    }
    st.marques.setMarkers(marquesDe(st.ordres));
    poserLignes();
    if (!memeCourbe || toutVu) st.chart.timeScale().fitContent();
    else if (plage) st.chart.timeScale().setVisibleLogicalRange(plage);
    if (st.idx !== null && st.idx >= barres.length) { st.idx = null; st.clavier = false; }
    if (st.clavier) requestAnimationFrame(reposerCurseur);
    else ecrireCurseur(null);
    requestAnimationFrame(placerEtiquettes);
    return { ordres: st.ordres.length, hors: st.hors };
  }

  /* Le journal a changé (un ordre noté) : les flèches suivent, sans toucher au zoom. */
  function ordres(decisions) {
    if (!st.chart || !st.barres.length) return null;
    ({ ordres: st.ordres, hors: st.hors } = placerOrdres(st.barres, decisions, st.ticker, st.periode));
    st.marques.setMarkers(marquesDe(st.ordres));
    st.serie.applyOptions({ autoscaleInfoProvider: (origine) => elargir(origine()) });
    return { ordres: st.ordres.length, hors: st.hors };
  }

  /* Un message (chargement raté, pas assez de points) : dans un élément voisin, jamais
     dans le cadre du graphique, que la bibliothèque occupe. */
  function message(texte) {
    preparer();
    el.msg.textContent = texte;
    el.msg.hidden = false;
    el.cadre.hidden = !st.chart;
    if (!st.chart) el.curseur.textContent = '';
  }

  /* La vue entière : un clic sur la période déjà choisie, ou la touche 0. */
  function vueEntiere() {
    if (!st.chart) return;
    st.chart.timeScale().fitContent();
    requestAnimationFrame(reposerCurseur);
  }

  /* Rend le cadre tel que carnet.js l'a créé : si la bibliothèque échoue, le graphique
     SVG reprend la place (carnet.js, lcEnPanne). */
  function abandonner() {
    try { if (st.chart) st.chart.remove(); } catch (e) { /* déjà à moitié détruit */ }
    st = vierge();
    if (!el) return;
    el.curseur.remove();
    el.msg.remove();
    el.credit.remove();
    const { cadre } = el;
    cadre.classList.remove('pc-graphe-lc');
    cadre.hidden = false;
    cadre.textContent = '';
    for (const a of ['tabindex', 'role', 'aria-label', 'aria-describedby']) cadre.removeAttribute(a);
    cadre.setAttribute('aria-hidden', 'true');
    el = null;
  }

  /* Pour les vérifications (outils/verif_graphique.mjs) : l'état, en lecture, et les
     fonctions de calcul, essayées sur des cours et des ordres inventés. */
  const essai = () => ({ chart: st.chart, serie: st.serie, barres: st.barres, ordres: st.ordres,
                         reperes: st.reperes.map(({ prix, texte }) => ({ prix, texte })), periode: st.periode,
                         idx: st.idx, clavier: st.clavier, actif: st.actif, nTotal: st.nTotal });
  const outils = { barresDe, avecLeReste, placerOrdres, texteTemps, graduation, enSeance };

  return { dessiner, ordres, message, vueEntiere, abandonner, essai, outils, PERIODES };
})();

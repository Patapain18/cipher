/* ============================================================
   ma-position/papier.js — le défi papier, dans le carnet de bord
   ============================================================
   Un défi de 4 semaines avec 200 € FICTIFS, aux vrais cours de New
   York : l'étape 4 du projet (plan validé par Mathis le 16/11/2026 ;
   partie 1, ce qu'il faut pour passer le premier ordre).

   Un interrupteur « Réel / Papier » sous le titre du carnet. En mode
   papier, les parties réelles du carnet sont retirées (display: none)
   et le défi prend leur place, dans un cadre pointillé ; le mot
   « fictif » suit chaque montant. Les deux formulaires ne sont jamais
   visibles ensemble : un vrai achat ne peut pas se noter dans le défi,
   ni l'inverse.

   Le serveur fait tout le calcul (papier.py). Le prix d'un ordre est
   celui de la dernière minute de séance normale de New York, relu au
   moment de l'ordre ; hors séance, l'ordre est refusé et le formulaire
   dit pourquoi. La page n'invente aucun prix.

   Règle de sécurité : tout texte venu du serveur (la raison tapée
   comprise) passe par PP.esc() ou textContent, jamais tel quel.
   ============================================================ */
(() => {
  const PP = window.PP;
  const vue = document.getElementById('pc-vue');
  if (!PP || !vue) return;   // sans le carnet, rien à ajouter

  const $ = (sel) => vue.querySelector(sel);
  const CLE = 'signaux-carnet-mode';

  /* ------------------------------------------------------------
     OUTILS (les mêmes que le carnet)
     ------------------------------------------------------------ */
  const num = (texte) => `<span class="num">${PP.esc(texte)}</span>`;
  /* Un montant du défi : le mot « fictif » le suit (posé par la feuille). */
  const fictif = (v, o) => `<span class="num pa-somme">${PP.esc(PP.euros(v, o))}</span>`;
  const quantite = (q) => Number(q).toLocaleString('fr-FR', { maximumFractionDigits: 6 });
  const jourParis = (d) => d.toLocaleDateString('en-CA', { timeZone: 'Europe/Paris' });
  /* « 21:59 » le jour même, « 13 nov. 2026, 21:59 » un autre jour. */
  const heureDuCours = (iso) => {
    const d = new Date(iso);
    if (!iso || Number.isNaN(d.getTime())) return '—';
    return jourParis(d) === jourParis(new Date()) ? PP.heure(iso) : PP.dateHeure(iso);
  };
  const jourCourt = (iso) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString('fr-FR',
      { timeZone: 'Europe/Paris', day: 'numeric', month: 'short', year: 'numeric' });
  };
  /* Les nombres d'une phrase en mono, chaque morceau échappé à part
     (même méthode et même motif que le carnet). */
  const NOMBRE = /(?<![\d,:])(?<!S&P\s)[+−-]?\d+(?:[  ]\d{3})*(?:[,:]\d+)?(?:[  ]?[€$%])?/g;
  const habiller = (phrase) => {
    const texte = String(phrase ?? '').replace(/S&P 500/g, 'S&P 500');
    let html = '';
    let fin = 0;
    for (const m of texte.matchAll(NOMBRE)) {
      html += PP.esc(texte.slice(fin, m.index)) + `<span class="num">${PP.esc(m[0])}</span>`;
      fin = m.index + m[0].length;
    }
    return html + PP.esc(texte.slice(fin));
  };
  /* Une requête qui n'aboutit pas : le navigateur dit « Failed to fetch »
     (Chrome) ou « Load failed » (Safari). On le dit en français. */
  const lisible = (e) => (e instanceof TypeError || /fetch|load failed|network/i.test(String(e?.message))
    ? 'le serveur ne répond pas' : String(e?.message || e));

  /* ------------------------------------------------------------
     L'INTERRUPTEUR ET LE CADRE, écrits une fois
     ------------------------------------------------------------ */
  const titre = document.getElementById('pc-titre');
  const interrupteur = document.createElement('div');
  interrupteur.className = 'pa-mode';
  interrupteur.innerHTML = `
    <div class="pc-segments" role="group" aria-label="Ton argent réel, ou le défi papier">
      <button type="button" data-mode="reel" aria-pressed="true">Réel</button>
      <button type="button" data-mode="papier" aria-pressed="false" aria-controls="pa-zone">Papier</button>
    </div>
    <p class="pa-mode-aide">Papier : un défi de 4 semaines avec 200&nbsp;€ fictifs, aux vrais cours de New York.</p>`;
  titre.after(interrupteur);

  /* Les titres suivent ceux du carnet : chaque partie est un h3, chaque
     ordre un h4 (sa raison). */
  const zone = document.createElement('div');
  zone.className = 'pa-zone';
  zone.id = 'pa-zone';
  zone.hidden = true;
  zone.innerHTML = `
    <div class="pa-cadre">
      <p class="pa-tampon" aria-hidden="true">Argent fictif</p>

      <section class="pa-section pa-section-defi" aria-labelledby="pa-t-defi">
        <h3 class="pc-rubrique" id="pa-t-defi">Le défi papier</h3>
        <p class="pa-avertissement" id="pa-avertissement">Défi FICTIF : aucun ordre n'est envoyé,
          aucun euro réel n'est en jeu.</p>
        <div id="pa-resume"><p class="pc-attente">Chargement du défi…</p></div>
        <div id="pa-positions"></div>
      </section>

      <section class="pa-section" aria-labelledby="pa-t-ordre">
        <h3 class="pc-rubrique" id="pa-t-ordre">Passer un ordre fictif</h3>
        <form class="pc-form pa-form" id="pa-form" autocomplete="off" novalidate>
          <p class="pc-form-intro">Au prix de la dernière minute de séance de New York, relu au moment
            de l'ordre, avec ${num(PP.euros(1))} de frais. Rien ne part chez un courtier.</p>

          <div class="pc-ligne pc-ligne-raison">
            <label class="pc-marge pc-raison-label" for="pa-raison">Pourquoi cet ordre&nbsp;?
              <span>En une phrase : obligatoire.</span></label>
            <div>
              <textarea class="pc-raison-champ" id="pa-raison" name="raison" rows="2"
                        maxlength="200" aria-describedby="pa-raison-compte"></textarea>
              <p class="pc-raison-compte" id="pa-raison-compte"><span class="num"
                 id="pa-raison-n">0</span>&nbsp;/&nbsp;<span class="num">200</span> caractères</p>
            </div>
          </div>

          <div class="pc-ligne pc-ligne-detail">
            <p class="pc-marge pc-detail-label" aria-hidden="true">L'ordre</p>
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
                  <label for="pa-ticker">Valeur</label>
                  <select class="picker" id="pa-ticker" name="ticker">
                    <option value="NVDA" selected>NVDA</option>
                  </select>
                </div>
                <div class="pc-champ" data-pour="achat">
                  <label for="pa-montant">Montant (€ fictifs)</label>
                  <input class="field num" id="pa-montant" name="montant_eur" inputmode="decimal"
                         aria-describedby="pa-aide">
                </div>
                <div class="pc-champ" data-pour="vente" hidden>
                  <label for="pa-quantite">Quantité</label>
                  <input class="field num" id="pa-quantite" name="quantite" inputmode="decimal"
                         aria-describedby="pa-aide">
                </div>
                <div class="pc-champ pa-champ-tout" data-pour="vente" hidden>
                  <label class="pa-case"><input type="checkbox" id="pa-tout" name="tout"> Tout vendre</label>
                </div>
              </div>
              <p class="pc-aide" id="pa-aide"></p>
              <p class="pa-ferme" id="pa-ferme" hidden></p>
              <div class="pc-envoi">
                <button type="submit" class="btn" id="pa-envoyer" aria-describedby="pa-ferme">Passer l'ordre fictif</button>
                <p class="form-msg pc-msg" id="pa-msg" role="status" aria-live="polite"></p>
              </div>
            </div>
          </div>
        </form>
      </section>

      <section class="pa-section" aria-labelledby="pa-t-ordres">
        <div class="pc-rubrique-ligne">
          <h3 class="pc-rubrique" id="pa-t-ordres">Tes ordres fictifs</h3>
          <p class="pc-rubrique-note" id="pa-nb"></p>
        </div>
        <div id="pa-ordres"><p class="pc-attente">Chargement…</p></div>
      </section>
    </div>`;
  (vue.querySelector('.pc-entete') || titre).after(zone);

  const form = $('#pa-form');
  const boutons = [...interrupteur.querySelectorAll('[data-mode]')];

  /* ------------------------------------------------------------
     L'ÉTAT
     ------------------------------------------------------------ */
  const etat = { mode: 'reel', donnees: null, erreur: null, envoi: false };
  /* Ce qu'on a déjà dessiné : un tour qui ramène les mêmes chiffres ne
     réécrit rien (le focus et la lecture d'un lecteur d'écran restent). */
  const deja = { resume: '', positions: '', ordres: '', ferme: null, options: '' };
  let ticket = 0;
  let surveillance = null;
  let premiereOuverture = true;

  /* ------------------------------------------------------------
     LE MODE : réel ou papier
     ------------------------------------------------------------
     Gardé d'une ouverture à l'autre si la fenêtre de l'appli garde le
     stockage local (à vérifier dans l'appli elle-même) ; sinon le
     carnet s'ouvre sur « Réel ». ?mode=papier l'impose à la première
     ouverture (vérifications, captures). */
  function modeGarde() {
    if (premiereOuverture) {
      const demande = new URLSearchParams(location.search).get('mode');
      if (demande === 'papier' || demande === 'reel') return demande;
    }
    try { return localStorage.getItem(CLE) === 'papier' ? 'papier' : 'reel'; } catch { return 'reel'; }
  }

  function choisirMode(mode, { garder = true } = {}) {
    etat.mode = mode;
    vue.classList.toggle('pa-papier', mode === 'papier');
    zone.hidden = mode !== 'papier';
    boutons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
    if (garder) { try { localStorage.setItem(CLE, mode); } catch { /* stockage indisponible */ } }
    if (mode === 'papier') {
      if (!surveillance) surveillance = PP.surveiller(tour);
      else tour();
    }
  }

  boutons.forEach((b) => b.addEventListener('click', () => {
    if (b.dataset.mode !== etat.mode) choisirMode(b.dataset.mode);
  }));
  document.addEventListener('carnet:ouvert', () => {
    choisirMode(modeGarde(), { garder: false });
    premiereOuverture = false;
  });

  /* ------------------------------------------------------------
     LE CHARGEMENT
     ------------------------------------------------------------ */
  async function tour() {
    if (vue.hidden || etat.mode !== 'papier') return;
    const n = ++ticket;
    try {
      const d = await PP.lire('/api/papier');
      if (n !== ticket) return;
      etat.donnees = d;
      etat.erreur = null;
    } catch (e) {
      if (n !== ticket) return;
      etat.erreur = lisible(e);
    }
    dessiner();
  }

  function dessiner() {
    dessinerResume();
    dessinerPositions();
    dessinerOrdres();
    remplirValeurs();
    majAide();
    dessinerGuichet();
  }

  /* ------------------------------------------------------------
     1. LE DÉFI EN CHIFFRES
     ------------------------------------------------------------ */
  function dessinerResume() {
    const d = etat.donnees;
    let html;
    if (!d) {
      html = `<p class="pc-attente">${etat.erreur
        ? `Défi indisponible pour l'instant (${PP.esc(etat.erreur)}). Nouvel essai dans une minute.`
        : 'Chargement du défi…'}</p>`;
    } else {
      if (d.avertissement) $('#pa-avertissement').textContent = d.avertissement;
      const jour = !d.debut
        ? '<strong>Pas commencé</strong>'
        : d.fini
          ? '<strong>Défi fini</strong>'
          : `<strong>Jour ${num(String(d.jour))}</strong><br>sur ${num(String(d.jours))}`;
      const total = d.total_eur == null ? num('—') : fictif(d.total_eur);
      const gain = d.gain_eur == null || !d.debut ? ''
        : `<p class="pc-gain-ligne"><span class="${PP.sens(d.gain_eur)}">${fictif(d.gain_eur, { signe: true })}
            (${num(PP.pct(d.gain_pct))})</span> depuis le départ, à ${fictif(d.capital_eur)}</p>`;
      const valeurPositions = d.positions.every((p) => p.valeur_eur != null)
        ? d.positions.reduce((s, p) => s + p.valeur_eur, 0) : null;
      const registre = !d.debut ? '' : `
        <dl class="pc-registre pa-registre">
          <div><dt>Liquidités</dt><dd>${fictif(d.liquidites_eur)}</dd></div>
          <div><dt>Positions</dt><dd>${valeurPositions == null ? num('—') : fictif(valeurPositions)}</dd></div>
          <div><dt><span>Frais payés (${num(String(d.ordres.length))} ordre${d.ordres.length > 1 ? 's' : ''})</span></dt>
            <dd>${fictif(d.frais_payes_eur)}</dd></div>
        </dl>
        <p class="pa-note">${habiller(`Les frais payés font ${PP.pct(d.frais_payes_pct, { signe: false })} du capital de départ.`)}</p>`;
      const regle = d.debut
        ? `<p class="pa-note">${habiller(`Dernier jour du défi : ${PP.jour(d.fin)} (jour ${d.jours}). Son résultat sera `
            + `compté à la clôture de la dernière séance de New York d'ici là, le ${PP.jour(d.derniere_seance)} `
            + '(sauf jour férié).')}</p>`
        : `<p class="pa-regle">${habiller(`Le défi commence à ton premier ordre. Au même instant, l'adversaire met ses `
            + `${PP.euros(d.capital_eur)} dans le S&P 500, avec le même ${PP.euros(d.frais_ordre_eur)} de frais. `
            + `Il dure ${d.jours} jours : le jour 1 est celui du premier ordre.`)}</p>`;
      const manquants = d.cours_manquants?.length
        ? `<p class="pa-note">Cours indisponible pour ${PP.esc(d.cours_manquants.join(', '))} (Yahoo) :
            ${d.total_eur == null ? 'le total attend le prochain essai.' : 'nouvel essai dans une minute.'}</p>` : '';
      const erreur = etat.erreur
        ? `<p class="pa-note">Dernier rafraîchissement impossible (${PP.esc(etat.erreur)}) : ces chiffres datent du tour précédent.</p>` : '';
      html = `
        <div class="pc-ligne pa-chiffres">
          <p class="pc-marge pa-jour">${jour}</p>
          <div>
            <p class="pc-valeur-ligne"><span class="pc-valeur-euros">${total}</span></p>
            ${gain}${registre}${regle}${manquants}${erreur}
          </div>
        </div>`;
    }
    if (html !== deja.resume) { $('#pa-resume').innerHTML = html; deja.resume = html; }
  }

  /* ------------------------------------------------------------
     2. LES POSITIONS — un petit tableau, au cours de séance
     ------------------------------------------------------------ */
  function dessinerPositions() {
    const d = etat.donnees;
    let html = '';
    if (d?.positions.length) {
      const minute = d.positions.map((p) => p.minute).filter(Boolean).sort().pop();
      const lignes = d.positions.map((p) => `
        <tr>
          <th scope="row">${PP.esc(p.ticker)}</th>
          <td>${num(quantite(p.quantite))}</td>
          <td>${num(PP.euros(p.prix_moyen_eur))}</td>
          <td>${p.prix_eur == null ? num('—') : num(PP.euros(p.prix_eur))}</td>
          <td>${p.valeur_eur == null ? num('—') : fictif(p.valeur_eur)}</td>
          <td>${p.gain_eur == null ? num('—') : `<span class="${PP.sens(p.gain_eur)}">${fictif(p.gain_eur, { signe: true })}
            (${num(PP.pct(p.gain_pct))})</span>`}</td>
        </tr>`).join('');
      html = `
        <div class="pa-table-cadre" tabindex="0" role="region" aria-labelledby="pa-legende-positions">
          <table class="pa-table">
            <caption id="pa-legende-positions">Tes positions fictives, au cours de séance de
              ${num(heureDuCours(minute))}</caption>
            <thead><tr>
              <th scope="col">Valeur</th><th scope="col">Quantité</th><th scope="col">Prix moyen</th>
              <th scope="col">Cours</th><th scope="col">Valeur</th><th scope="col">Gain, frais compris</th>
            </tr></thead>
            <tbody>${lignes}</tbody>
          </table>
        </div>`;
    }
    if (html !== deja.positions) {
      const focus = $('#pa-positions').contains(document.activeElement);
      $('#pa-positions').innerHTML = html;
      deja.positions = html;
      if (focus) $('.pa-table-cadre')?.focus({ preventScroll: true });
    }
  }

  /* ------------------------------------------------------------
     3. TES ORDRES FICTIFS — le plus récent en haut, la raison en titre
     ------------------------------------------------------------ */
  function entree(o, i) {
    const achat = o.sens === 'achat';
    const paye = achat
      ? `${fictif(o.montant_eur)} + ${fictif(o.frais_eur)} de frais`
      : `${fictif(o.montant_eur)}, dont ${fictif(o.frais_eur)} de frais`;
    return `
      <article class="pc-entree pc-ligne" aria-labelledby="pa-e-${i}">
        <p class="pc-marge pc-entree-date">
          <time datetime="${PP.esc(o.date)}">${PP.esc(jourCourt(o.date))}<br>${num(PP.heure(o.date))}</time>
        </p>
        <div class="pc-entree-corps">
          <p class="pc-entree-sens">${achat ? 'Achat fictif' : 'Vente fictive'} de ${num(quantite(o.quantite))} ${PP.esc(o.ticker)}</p>
          <h4 class="pc-raison${String(o.raison).length > 90 ? ' pc-raison-longue' : ''}" id="pa-e-${i}">« ${PP.esc(o.raison)} »</h4>
          <p class="pc-paye">${paye}, au prix de ${num(PP.euros(o.prix_eur))} l'action
            <span class="pc-discret">(${num(PP.dollars(o.prix_usd))} au change de ${num(PP.nombre(o.change, 4))} ;
            cours de ${num(PP.heure(o.minute))})</span>.</p>
        </div>
      </article>`;
  }

  function dessinerOrdres() {
    const d = etat.donnees;
    let html;
    let nb = '';
    if (!d) {
      html = '<p class="pc-attente">Chargement…</p>';
    } else if (!d.ordres.length) {
      html = '<p class="pc-vide">Aucun ordre pour l\'instant. Le premier lancera le défi.</p>';
    } else {
      nb = `${num(String(d.ordres.length))} ordre${d.ordres.length > 1 ? 's' : ''}`;
      html = [...d.ordres].reverse().map(entree).join('');
    }
    if (html !== deja.ordres) {
      $('#pa-ordres').innerHTML = html;
      $('#pa-nb').innerHTML = nb;
      deja.ordres = html;
    }
  }

  /* ------------------------------------------------------------
     4. LE FORMULAIRE
     ------------------------------------------------------------ */
  const sens = () => form.elements.sens.value;
  const ligneDetenue = () => etat.donnees?.positions.find((p) => p.ticker === form.elements.ticker.value) || null;

  /* La liste des valeurs : les 49 pour un achat, celles qu'on détient
     pour une vente. Réécrite seulement quand elle change : une liste
     réécrite sous le curseur perdrait le choix en cours. */
  function remplirValeurs() {
    const d = etat.donnees;
    if (!d) return;
    const liste = sens() === 'vente' ? d.positions.map((p) => p.ticker) : d.valeurs;
    const cle = `${sens()}:${liste.join(',')}`;
    if (cle === deja.options) return;
    const select = form.elements.ticker;
    const choisi = select.value;
    select.innerHTML = liste.length
      ? liste.map((v) => `<option value="${PP.esc(v)}">${PP.esc(v)}</option>`).join('')
      : '<option value="">Aucune action détenue</option>';
    select.value = liste.includes(choisi) ? choisi : liste.includes('NVDA') ? 'NVDA' : (liste[0] || '');
    deja.options = cle;
  }

  /* Sous le bouton : ce que coûtent les frais, en chiffres (des faits,
     pas un conseil). */
  function majAide() {
    const d = etat.donnees;
    const frais = d?.frais_ordre_eur ?? 1;
    const minimum = d?.achat_min_eur ?? 1;
    const e = (v, o) => num(PP.euros(v, o));      // une règle (les frais), pas un montant du défi
    let html;
    if (sens() === 'achat') {
      const m = PP.nombreSaisi(form.elements.montant_eur.value);
      html = Number.isFinite(m) && m > 0
        ? `Un aller-retour (cet achat, puis sa vente) coûte ${e(2 * frais)} de frais : sur ${fictif(m)}, `
          + `il faut ${num(PP.pct(2 * frais / m))} pour retrouver ta mise.`
        : `Un aller-retour (un achat, puis sa vente) coûte ${e(2 * frais)} de frais.`;
      if (d) {
        const possible = d.liquidites_eur - frais;
        html += possible >= minimum
          ? ` Il te reste ${fictif(d.liquidites_eur)} : avec le ${e(frais)} de frais, tu peux acheter pour `
            + `${fictif(possible)} au plus.`
          : ` Il te reste ${fictif(d.liquidites_eur)} : pas assez pour un achat (au moins ${e(minimum)}, `
            + `plus ${e(frais)} de frais).`;
      }
    } else {
      const p = ligneDetenue();
      const q = form.elements.tout.checked ? p?.quantite : PP.nombreSaisi(form.elements.quantite.value);
      html = `Une vente coûte ${e(frais)} de frais, retenu sur ce qu'elle rapporte.`;
      if (p) {
        html += ` Tu détiens ${num(quantite(p.quantite))} ${PP.esc(p.ticker)}.`;
        if (Number.isFinite(q) && q > 0 && p.prix_eur != null) {
          /* Les mêmes règles que le serveur (papier.py) : une quantité égale
             à tout ce qu'on détient vaut « Tout vendre » ; une vente
             partielle doit rapporter plus que son frais et laisser au moins
             le minimum. Seule « Tout vendre » peut coûter plus qu'elle ne
             rapporte. */
          const tout = form.elements.tout.checked || Math.abs(q - p.quantite) <= 1e-9;
          const brut = q * p.prix_eur;
          const reste = (p.quantite - q) * p.prix_eur;
          const auCours = `Au dernier cours de séance (${e(p.prix_eur)}, ${num(heureDuCours(p.minute))})`;
          if (!tout && q > p.quantite) {
            html += ' C\'est plus que ce que tu détiens.';
          } else if (!tout && brut <= frais) {
            html += ` ${auCours}, cette vente rapporterait environ ${fictif(brut)}, pas plus que son ${e(frais)} `
              + 'de frais : coche « Tout vendre » pour fermer la ligne.';
          } else if (!tout && reste < minimum) {
            html += ` ${auCours}, il ne resterait qu'environ ${fictif(reste)} ${PP.esc(PP.de(p.ticker))} : `
              + 'vends tout, ou une plus petite part.';
          } else if (brut - frais >= 0) {
            html += ` ${auCours}, cette vente rapporterait environ ${fictif(brut - frais)}, frais déduits.`;
          } else {
            html += ` ${auCours}, cette vente te coûterait environ ${fictif(frais - brut)} : son ${e(frais)} de frais `
              + 'dépasse ce qu\'elle rapporte.';
          }
        }
      } else if (d) {
        html += ' Tu ne détiens aucune action dans ce défi.';
      }
    }
    $('#pa-aide').innerHTML = html;
  }

  /* Le bouton d'envoi : fermé hors séance (ou défi fini), avec la raison
     donnée par le serveur juste au-dessus. */
  function dessinerGuichet() {
    const d = etat.donnees;
    let ferme = '';
    if (!d) ferme = etat.erreur ? `Défi indisponible pour l'instant (${etat.erreur}) : ordres impossibles.` : 'Chargement du défi…';
    else if (!d.ordres_ouverts) ferme = d.ordres_message || 'Ordres impossibles pour l\'instant.';
    else if (sens() === 'vente' && !d.positions.length) ferme = 'Tu ne détiens aucune action dans ce défi : rien à vendre.';
    const envoyer = $('#pa-envoyer');
    const avaitLeFocus = document.activeElement === envoyer;
    envoyer.disabled = !!ferme || etat.envoi;
    if (ferme !== deja.ferme) {
      const p = $('#pa-ferme');
      p.innerHTML = habiller(ferme);
      p.hidden = !ferme;
      deja.ferme = ferme;
    }
    /* Un bouton désactivé perd le focus : on le pose sur la raison du
       refus, plutôt que de le laisser retomber en haut de la page. */
    if (avaitLeFocus && envoyer.disabled && !etat.envoi) form.elements.raison.focus({ preventScroll: true });
  }

  function majChamps() {
    const vente = sens() === 'vente';
    form.querySelectorAll('[data-pour]').forEach((el) => { el.hidden = el.dataset.pour !== (vente ? 'vente' : 'achat'); });
    form.elements.quantite.disabled = vente && form.elements.tout.checked;
    remplirValeurs();
    majAide();
    dessinerGuichet();
  }

  const majCompte = () => { $('#pa-raison-n').textContent = String(form.elements.raison.value.length); };
  form.elements.raison.addEventListener('input', majCompte);
  form.addEventListener('change', (e) => {
    if (e.target.name === 'sens' || e.target.name === 'tout') majChamps();
    else majAide();
  });
  form.addEventListener('input', (e) => {
    if (e.target.name === 'montant_eur' || e.target.name === 'quantite') majAide();
  });

  const message = (texte, genre = '') => {
    const zoneMsg = $('#pa-msg');
    zoneMsg.textContent = texte;
    zoneMsg.className = `form-msg pc-msg ${genre}`;
  };

  async function envoyerOrdre(ordre) {
    const rep = await fetch('/api/papier/ordre', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Dashboard': '1' },
      body: JSON.stringify(ordre),
    });
    const json = await rep.json().catch(() => ({ ok: false, error: `réponse illisible (${rep.status})` }));
    if (!json.ok) throw new Error(json.error || 'ordre refusé');
    return json.data;
  }

  /* « Achat fictif de 0,341234 NVDA à 146,52 € l'action (159,87 $, cours de 15:42) :
     50,00 € + 1,00 € de frais. » */
  const phraseOrdre = (o) => `${o.sens === 'achat' ? 'Achat fictif' : 'Vente fictive'} de ${quantite(o.quantite)} `
    + `${o.ticker} à ${PP.euros(o.prix_eur)} l'action (${PP.dollars(o.prix_usd)}, cours de ${PP.heure(o.minute)}) : `
    + (o.sens === 'achat'
      ? `${PP.euros(o.montant_eur)} + ${PP.euros(o.frais_eur)} de frais.`
      : `${PP.euros(o.montant_eur)}, dont ${PP.euros(o.frais_eur)} de frais.`);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const envoyer = $('#pa-envoyer');
    if (envoyer.disabled) return;
    const v = (nom) => form.elements[nom].value;
    const ordre = { sens: sens(), ticker: v('ticker'), raison: v('raison').trim() };
    /* Ce que le serveur refuserait de toute façon, dit tout de suite. */
    if (!ordre.raison) {
      message('La raison est obligatoire : une phrase, c\'est le cœur du défi.', 'error');
      form.elements.raison.focus();
      return;
    }
    if (ordre.sens === 'achat') {
      ordre.montant_eur = PP.nombreSaisi(v('montant_eur'));
      if (!Number.isFinite(ordre.montant_eur)) {
        message('Le montant : un nombre, par exemple 50 ou 12,50.', 'error');
        form.elements.montant_eur.focus();
        return;
      }
    } else if (form.elements.tout.checked) {
      ordre.tout = true;
    } else {
      ordre.quantite = PP.nombreSaisi(v('quantite'));
      if (!Number.isFinite(ordre.quantite)) {
        message('La quantité : un nombre, par exemple 0,25 (ou coche « Tout vendre »).', 'error');
        form.elements.quantite.focus();
        return;
      }
    }

    etat.envoi = true;          // pas de double ordre
    envoyer.disabled = true;
    message('Ordre fictif en cours…');
    let o = null;
    try {
      o = await envoyerOrdre(ordre);
    } catch (err) {
      message(lisible(err) === 'le serveur ne répond pas'
        ? 'Pas de réponse du serveur : l\'ordre n\'est peut-être pas passé. Regarde tes ordres fictifs ci-dessous avant de réessayer.'
        : err.message, 'error');
    }
    etat.envoi = false;
    dessinerGuichet();
    if (document.activeElement === document.body || !document.activeElement) {
      (envoyer.disabled ? form.elements.raison : envoyer).focus({ preventScroll: true });
    }
    if (!o) {
      /* Refusé, ou sans réponse : on relit le défi. Il dit peut-être
         pourquoi (la séance vient de finir), ou montre l'ordre passé
         malgré la panne. Le message du refus reste affiché. */
      await tour();
      return;
    }

    const phrase = phraseOrdre(o);
    message(`${phrase} Mise à jour du défi…`, 'ok');
    form.elements.raison.value = '';
    form.elements.montant_eur.value = '';
    form.elements.quantite.value = '';
    form.elements.tout.checked = false;
    majCompte();
    majChamps();
    await tour();
    /* « En haut de tes ordres » seulement si la relecture le montre
       vraiment ; et rien ne change si un autre message a pris la place. */
    if (!$('#pa-msg').textContent.startsWith(phrase)) return;
    const present = etat.donnees?.ordres.some((x) => x.date === o.date && x.ticker === o.ticker
      && x.sens === o.sens && x.quantite === o.quantite);
    message(present ? `${phrase} Il est en haut de tes ordres fictifs.`
      : etat.erreur ? `${phrase} L'affichage n'a pas pu se mettre à jour (${etat.erreur}) : il se rafraîchira dans une minute.`
        : `${phrase} Il apparaîtra dans tes ordres fictifs au prochain rafraîchissement.`, 'ok');
  });

  majChamps();
})();

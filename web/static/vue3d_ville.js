/* ============================================================
   vue3d_ville.js — LA VILLE (le générateur, et rien d'autre)
   ============================================================
   Ce fichier fabrique la liste des boîtes qui composent la ville :
   immeubles, détails de toit, repères, sol. Il ne touche ni au DOM ni
   à WebGL — c'est une fonction pure, qu'on peut exécuter sous node
   pour la vérifier (aucune interpénétration, combien d'immeubles dans
   le champ, une fenêtre candidate pour le reflet…) sans ouvrir la
   moindre fenêtre. Voir web/outils/test_ville.js.

   POURQUOI UNE TRAME ET NON DU HASARD
       La première ville était 2 200 boîtes tirées au sort sur un
       anneau : 2 634 paires se traversaient, il n'y avait pas une rue,
       et 81 % des boîtes étaient hors champ ou derrière le panneau.
       Une ville, c'est l'inverse : des VIDES continus (les rues) qui
       découpent des PLEINS (les îlots) dont les façades s'alignent sur
       le vide. Ici tout part d'une grille d'îlots ; les immeubles
       remplissent leurs parcelles et ne peuvent donc pas se
       chevaucher — par construction, pas par test.

   POURQUOI DEUX QUARTIERS
       Une trame unique fait un damier. Deux trames tournées
       différemment, séparées par un grand boulevard en biais, c'est LE
       signe d'une vraie ville : quelque chose a été bâti avant autre
       chose. Le boulevard traverse le champ à mi-distance, où l'œil
       cherche justement une ligne de fuite.

   POURQUOI UN HASH ET NON UN TIRAGE SÉQUENTIEL
       L'ancien générateur consommait ses nombres aléatoires dans
       l'ordre : ajouter une cheminée décalait tous les tirages
       suivants et rebattait toute la ville. Ici chaque parcelle tire
       ses valeurs d'un hash de ses propres indices (îlot, parcelle,
       usage) : on peut retoucher les toits sans changer une seule
       hauteur. La graine est écrite en dur, comme avant — une ville
       qui change à chaque ouverture invite à y chercher un sens.
   ============================================================ */

(function (racine) {
  'use strict';

  /* ── Les paramètres partagés avec le shader ───────────────────
     Le sol de la ville dessine ses rues avec EXACTEMENT ces trames
     (vue3d.js les injecte dans le GLSL) : la ville et son sol ne
     peuvent pas diverger. */
  const PARAMS = {
    graine: 20260908,
    solY: -34.6,          // altitude du sol urbain : l'œil est à 0, au onzième
    etage: 3.2,           // un étage, en mètres — la trame des fenêtres du shader
    dMin: 90,             // rien avant : on est au onzième, pas au balcon
    dMax: 1600,           // au-delà, la brume a tout pris (voir le shader)
    /* L'immeuble de l'observateur : un plateau vitré sur quatre côtés,
       centré ici. Aucune parcelle à moins de `rayonImmeuble` de son
       centre — c'est son propre îlot et la rue qui l'entoure. La ville
       l'entoure sur 360° : la caméra tourne vers cinq panneaux, et
       regarde donc dans toutes les directions. */
    immeuble: { x: 0, z: 9.6, rayon: 60 },
    oeilZ: -2.4,          // les distances (zones, niveaux de détail) se comptent depuis l'œil

    /* Deux quartiers. `angle` tourne la trame (radians, autour de Y) ;
       ni les rues ni les façades ne doivent être parallèles à l'axe de
       vue, sinon on lit un damier. `periode` : une avenue remplace une
       rue toutes les `periode` trames. */
    quartiers: [
      { angle: 0.16,  pasU: 88, pasV: 70, rue: 16, avenue: 32, periode: 4 },
      { angle: -0.35, pasU: 80, pasV: 64, rue: 14, avenue: 28, periode: 5 },
    ],

    /* La couture entre les deux : une droite dans le plan xz, et la
       largeur du boulevard qui la suit. Elle traverse le champ en
       biais, plus proche à gauche : l'asymétrie fait naturel. Le côté
       de la caméra (z plus grand) est le quartier 0. */
    couture: { x0: -1200, z0: -330, x1: 1200, z1: -1010, largeur: 46 },

    /* Le groupe de tours : une ellipse dans le plan xz. Elle est posée
       dans la bande visible de gauche (entre le montant et le bord du
       cadre), là où quelques silhouettes qui crèvent l'horizon donnent
       l'altitude à toute la vue. */
    tours: { x: -440, z: -660, rx: 190, rz: 150 },
    /* Une seconde skyline, fantôme, à droite et très loin : dissoute à
       80-95 % par la brume, elle équilibre le groupe de gauche sans
       jamais devenir un mur — dix tours au plus. */
    toursFantome: { x: 860, z: -1370, rx: 170, rz: 110 },
  };

  /* ── Hash entier : déterministe, sans état, sans ordre ──────────
     Trois entiers (îlot, parcelle, usage) → un réel dans [0, 1). On
     mélange avec Math.imul, en 32 bits : pas de sin(), pas de flottants
     qui se dégradent avec la taille des nombres. */
  function hash(a, b, k) {
    let h = Math.imul(a ^ PARAMS.graine, 0x85EBCA6B)
          ^ Math.imul(b + 0x27D4EB2F, 0xC2B2AE35)
          ^ Math.imul(k + 0x165667B1, 0x9E3779B1);
    h ^= h >>> 15; h = Math.imul(h, 0x2C1B3C6D);
    h ^= h >>> 12; h = Math.imul(h, 0x297A2D39);
    h ^= h >>> 15;
    return (h >>> 0) / 4294967296;
  }

  /* Distance signée à la couture : positif du côté de la caméra. */
  function coteCouture(x, z) {
    const c = PARAMS.couture;
    const dx = c.x1 - c.x0, dz = c.z1 - c.z0;
    const l = Math.hypot(dx, dz);
    // Signe choisi pour que le côté de la caméra (z plus grand) soit positif.
    return ((z - c.z0) * dx - (x - c.x0) * dz) / l;
  }

  function dansEllipse(t, x, z) {
    const ex = (x - t.x) / t.rx, ez = (z - t.z) / t.rz;
    return ex * ex + ez * ez < 1;
  }
  const dansLesTours = (x, z) => dansEllipse(PARAMS.tours, x, z);

  /* Étages par zone. Découplés de la DISTANCE : l'ancienne loi faisait
     monter la hauteur maximale avec r, ce qui produisait une ligne de
     toits parfaitement plate à l'écran. Ici le tissu courant reste
     SOUS l'horizon (l'œil est à 34,6 m, un immeuble de 7 étages fait
     22 m) et seuls le groupe de tours et les repères le dépassent. */
  function etagesDeBase(d, x, z, h) {
    if (dansLesTours(x, z)) {
      // Quatre parcelles sur dix sont des tours ; les autres, du tissu.
      return h < 0.4 ? 15 + Math.floor(h * 50) : 6 + Math.floor(h * 4);
    }
    if (dansEllipse(PARAMS.toursFantome, x, z)) {
      return h < 0.25 ? 18 + Math.floor(h * 60) : 5 + Math.floor(h * 3);
    }
    if (d < 300) return 3 + Math.floor(h * 3);        // faubourg : 3 à 5
    if (d < 1100) return 5 + Math.floor(h * 4);       // tissu courant : 5 à 8
    return 4 + Math.floor(h * 3);                     // le lointain redescend
  }

  /* ── Les repères ───────────────────────────────────────────────
     Trois silhouettes placées à la main, dans les bandes visibles
     (|x| entre 0,48·d et 0,79·d — entre le montant et le bord du
     cadre). Un générateur ne produit pas de forme reconnaissable ; une
     ville se reconnaît à deux ou trois tours qu'on retrouve. Aucune
     antenne, rien qui clignote. */
  const REPERES = [
    // A — la grande tour du groupe : 150 m, retrait à 100 m, couronne.
    { x: -400, z: -640, angle: 0.16, boites: [
      { dx: 0, dz: 0, lx: 20, lz: 20, y0: 0, y1: 100 },
      { dx: 0, dz: 0, lx: 14, lz: 14, y0: 100, y1: 150 },
      { dx: 0, dz: 0, lx: 6, lz: 6, y0: 150, y1: 156 },
    ], exclusion: 42 },
    // B — la barre : 110 m de haut, 60 × 18, tournée d'un demi-radian.
    { x: 440, z: -900, angle: 0.5, boites: [
      { dx: 0, dz: 0, lx: 30, lz: 9, y0: 0, y1: 110 },
      { dx: -8, dz: 0, lx: 6, lz: 5, y0: 110, y1: 115 },
    ], exclusion: 48 },
    // C — au bord du montant : elle apparaît coupée, et c'est un
    // indice de profondeur gratuit.
    { x: -265, z: -560, angle: 0.16, boites: [
      { dx: 0, dz: 0, lx: 15, lz: 15, y0: 0, y1: 95 },
      { dx: 4, dz: -4, lx: 5, lz: 5, y0: 95, y1: 99 },
    ], exclusion: 34 },
  ];

  /* ── Le générateur ─────────────────────────────────────────────
     Retourne { buf, N, STRIDE, fenetre, grue, params }.

     Une instance = 11 flottants :
       0-2  position du centre (x, y, z)     — y au milieu de la boîte
       3-5  demi-tailles (x, y, z)
       6-7  cos, sin de la rotation autour de Y
       8    teinte : multiplicateur de valeur, 0,86 → 1,08
       9    genre : 0 immeuble, 1 détail de toit, 2 sol, 3 repère
       10   motif : un réel dans [0,1) qui règle le rythme des fenêtres */
  const STRIDE = 11;

  function generer(options = {}) {
    const P = Object.assign({}, PARAMS, options);
    const out = [];
    let fenetre = null, grue = null;
    // Les candidats au reflet et à la grue, ramassés pendant la
    // génération et tranchés à la fin (déterministe : ordre de grille).
    const candidatsFenetre = [], candidatsGrue = [];

    const pousser = (x, y, z, hx, hy, hz, c, s, teinte, genre, motif) => {
      out.push(x, y, z, hx, hy, hz, c, s, teinte, genre, motif);
    };

    /* Une boîte posée sur le sol de la ville : y0 → y1 sont des
       hauteurs AU-DESSUS du sol. */
    const poserSurSol = (x, z, hx, hz, y0, y1, c, s, teinte, genre, motif) => {
      const yc = P.solY + (y0 + y1) / 2;
      pousser(x, yc, z, hx, (y1 - y0) / 2, hz, c, s, teinte, genre, motif);
    };

    const exclu = (x, z, rayon) => {
      for (const r of REPERES) {
        if (Math.hypot(x - r.x, z - r.z) < r.exclusion + rayon) return true;
      }
      return false;
    };

    /* ── Les îlots des deux quartiers ── */
    P.quartiers.forEach((q, iq) => {
      const c = Math.cos(q.angle), s = Math.sin(q.angle);
      const versMonde = (u, v) => [u * c - v * s, u * s + v * c];   // (x, z)

      for (let iu = -48; iu <= 48; iu++) {
        // Largeur de la rue à gauche et à droite de l'îlot iu.
        const rueG = (iu % q.periode === 0) ? q.avenue : q.rue;
        const rueD = ((iu + 1) % q.periode === 0) ? q.avenue : q.rue;
        const u0 = iu * q.pasU + rueG / 2, u1 = (iu + 1) * q.pasU - rueD / 2;

        for (let iv = -40; iv <= 40; iv++) {
          // Un boulevard toutes les six trames en profondeur, plus
          // discret : c'est le sens où l'on regarde, les rues y sont
          // vues de face et n'ont pas besoin d'être larges.
          const rueB = (iv % 6 === 0) ? q.avenue * 0.8 : q.rue;
          const rueH = ((iv + 1) % 6 === 0) ? q.avenue * 0.8 : q.rue;
          const v0 = iv * q.pasV + rueB / 2, v1 = (iv + 1) * q.pasV - rueH / 2;

          const [xc, zc] = versMonde((u0 + u1) / 2, (v0 + v1) / 2);
          // Distance depuis l'œil, dans toutes les directions : la ville
          // est un disque, pas un secteur.
          const d = Math.hypot(xc, zc - P.oeilZ);
          if (d < P.dMin || d > P.dMax) continue;
          if (Math.hypot(xc - P.immeuble.x, zc - P.immeuble.z) < P.immeuble.rayon + 50) continue;

          // Le bon quartier, et pas à cheval sur la couture.
          const coins = [[u0, v0], [u1, v0], [u0, v1], [u1, v1]].map(([u, v]) => versMonde(u, v));
          const cotes = coins.map(([x, z]) => coteCouture(x, z));
          const bord = P.couture.largeur / 2;
          const bonCote = iq === 0 ? cotes.every((k) => k > bord) : cotes.every((k) => k < -bord);
          if (!bonCote) continue;

          /* Les parcelles : 2 ou 3 sur le long côté, 1 ou 2 sur le
             court. Chaque parcelle est une boîte alignée sur la rue,
             en retrait de 0,4 à 1,4 m selon le hash — assez pour que
             deux voisins ne partagent pas exactement le même plan. */
          const nU = 2 + Math.floor(hash(iu, iv, 100 + iq) * 2);
          const nV = 1 + Math.floor(hash(iu, iv, 101 + iq) * 2);
          const lu = (u1 - u0) / nU, lv = (v1 - v0) / nV;

          for (let pu = 0; pu < nU; pu++) {
            for (let pv = 0; pv < nV; pv++) {
              const k = 1000 + pu * 10 + pv;            // clé de parcelle
              const ru = hash(iu, iv, k), rv = hash(iu, iv, k + 1);
              const marge = 0.4 + ru * 1.0;
              const pu0 = u0 + pu * lu + marge, pu1 = u0 + (pu + 1) * lu - marge;
              const pv0 = v0 + pv * lv + marge, pv1 = v0 + (pv + 1) * lv - marge;
              const hx = (pu1 - pu0) / 2, hz = (pv1 - pv0) / 2;
              const [x, z] = versMonde((pu0 + pu1) / 2, (pv0 + pv1) / 2);
              if (exclu(x, z, Math.hypot(hx, hz))) continue;

              const dp = Math.hypot(x, z - P.oeilZ);
              if (Math.hypot(x - P.immeuble.x, z - P.immeuble.z) < P.immeuble.rayon) continue;
              const h = hash(iu, iv, k + 2);
              let etages = etagesDeBase(dp, x, z, h);
              // ±1 étage par parcelle : la corniche partagée par un
              // îlot fait « bâti », la variation fait « vivant ».
              etages += Math.floor(rv * 3) - 1;
              etages = Math.max(2, etages);
              const haut = etages * P.etage;

              // Teinte : pierre, béton, enduit — ±8 % de valeur, et un
              // rien plus sombre pour les tours (verre).
              const teinte = 0.90 + hash(iu, iv, k + 3) * 0.16 - (etages > 12 ? 0.05 : 0);
              /* `motif` porte DEUX choses : la matière (le quart entier :
                 0 pierre claire, 1 béton à bandeaux, 2 enduit, 3 verre)
                 et, dans la fraction, le rythme des fenêtres. Les tours
                 sont en verre ; le reste se tire au hash. */
              const matiere = etages > 12 ? 3 : Math.floor(hash(iu, iv, k + 9) * 3);
              const motif = (matiere + hash(iu, iv, k + 4)) / 4;
              const genre = 0;

              /* Les hauts ont une silhouette : retrait du dernier tiers,
                 couronne technique, ou fût plein — tiré au hash. */
              const typo = etages >= 12 ? hash(iu, iv, k + 5) : 1;
              if (typo < 0.35) {
                const bas = haut - 3 * P.etage;
                poserSurSol(x, z, hx, hz, 0, bas, c, s, teinte, genre, motif);
                poserSurSol(x, z, Math.max(3, hx - 3), Math.max(3, hz - 3), bas, haut, c, s, teinte, genre, motif);
              } else if (typo < 0.6) {
                poserSurSol(x, z, hx, hz, 0, haut, c, s, teinte, genre, motif);
                poserSurSol(x, z, hx * 0.55, hz * 0.55, haut, haut + 3.2, c, s, teinte * 0.9, 1, 0);
              } else {
                poserSurSol(x, z, hx, hz, 0, haut, c, s, teinte, genre, motif);
              }
              /* Les détails de toit — là où l'image se joue : depuis
                 34 m on voit 95 % des toits de DESSUS. Niveau de détail
                 décidé ici, une fois, par la distance : rien de ce qui
                 ferait moins de deux pixels. */
              if (dp < 300) {
                /* Près : l'acrotère est un ANNEAU de quatre murets
                   (0,3 m d'épais, 0,9 m de haut) et le toit reste le
                   dessus de l'immeuble — le shader y pose l'occlusion
                   sous le muret et le grain du gravier. Un anneau
                   sous-pixel scintillerait au loin : au-delà, une dalle. */
                const tint = teinte * 1.03;
                const cx = (pu0 + pu1) / 2, cz = (pv0 + pv1) / 2;
                for (const [du, dv, lu, lv] of [[0, hz - 0.15, hx, 0.15], [0, -hz + 0.15, hx, 0.15],
                                                [hx - 0.15, 0, 0.15, hz - 0.3], [-hx + 0.15, 0, 0.15, hz - 0.3]]) {
                  const [xa, za] = versMonde(cx + du, cz + dv);
                  poserSurSol(xa, za, lu, lv, haut, haut + 0.9, c, s, tint, 1, 0);
                }
              } else if (dp < 900) {
                const tint = teinte * 1.03;
                poserSurSol(x, z, hx + 0.35, hz + 0.35, haut, haut + 0.9, c, s, tint, 1, 0);
              }
              if (dp < 800 && etages >= 5) {
                // L'édicule (machinerie d'ascenseur), poussé vers un angle.
                const ex = (hash(iu, iv, k + 6) < 0.5 ? -1 : 1) * Math.max(0, hx - 3.5);
                const ez = (hash(iu, iv, k + 7) < 0.5 ? -1 : 1) * Math.max(0, hz - 2.8);
                const [xe, ze] = versMonde((pu0 + pu1) / 2 + ex, (pv0 + pv1) / 2 + ez);
                poserSurSol(xe, ze, 2.0, 1.5, haut, haut + 2.6, c, s, teinte * 0.93, 1, 0);
              }
              if (dp < 420) {
                // Souches de cheminée, sur la ligne du mur mitoyen.
                const n = Math.floor(hash(iu, iv, k + 8) * 3.99);
                for (let i = 0; i < n; i++) {
                  const t = -0.7 + (i + 0.5) / n * 1.4;
                  const [xs, zs] = versMonde((pu0 + pu1) / 2 + t * hx, (pv0 + pv1) / 2 + hz - 0.9);
                  poserSurSol(xs, zs, 0.35, 0.35, haut, haut + 2.4, c, s, teinte * 0.9, 1, 0);
                }
              }

              // Candidats : la fenêtre qui accroche la lumière, la grue.
              if (z < 0 && dp >= 120 && dp <= 230 && etages >= 5) {
                const p = Math.abs(x) / dp;
                if (p >= 0.50 && p <= 0.70) {
                  candidatsFenetre.push({ x, z, hx, hz, haut, c, s, d: dp });
                }
              }
              /* La grue : entre 280 et 420 m. Plus près, sa flèche
                 barrait tout un carreau à hauteur d'horizon. */
              if (z < 0 && dp >= 280 && dp <= 420 && etages >= 3 && etages <= 7) {
                const p = Math.abs(x) / dp;
                if (p >= 0.50 && p <= 0.72) {
                  candidatsGrue.push({ x, z, hx, hz, haut, c, s, d: dp });
                }
              }
            }
          }
        }
      }
    });

    /* ── Les repères ── */
    for (const r of REPERES) {
      const c = Math.cos(r.angle), s = Math.sin(r.angle);
      for (const b of r.boites) {
        const x = r.x + b.dx * c - b.dz * s, z = r.z + b.dx * s + b.dz * c;
        poserSurSol(x, z, b.lx, b.lz, b.y0, b.y1, c, s, 0.84, 3, 0.5);
      }
    }

    /* ── La fenêtre et la grue ──
       La fenêtre est la première candidate à GAUCHE (x < 0) ; la grue,
       la première à DROITE : les deux bandes vivent. Le reflet se pose
       sur la face qui regarde la caméra, au deuxième tiers de la
       hauteur, 15 cm devant le mur. */
    const gauche = candidatsFenetre.find((f) => f.x < 0) || candidatsFenetre[0];
    if (gauche) {
      const f = gauche;
      // Face avant en local : +z (vers la caméra quand z local ≈ -z monde
      // après rotation, si la rotation est petite). On choisit la face dont
      // la normale tournée pointe vers +z monde.
      const nz = [[0, 1], [0, -1], [1, 0], [-1, 0]].map(([lx, lz]) => ({
        lx, lz, vers: lx * f.s + lz * f.c,   // composante z monde de la normale : vers l'œil si > 0
      })).sort((a, b) => b.vers - a.vers)[0];
      const off = nz.lx !== 0 ? f.hx + 0.15 : f.hz + 0.15;
      const dx = (nz.lx * off) * f.c - (nz.lz * off) * f.s;
      const dz = (nz.lx * off) * f.s + (nz.lz * off) * f.c;
      fenetre = {
        pos: [f.x + dx, P.solY + f.haut * 0.62, f.z + dz],
        ech: nz.lx !== 0 ? [0.06, 1.6, 1.1] : [1.1, 1.6, 0.06],
        cs: [f.c, f.s], d: f.d,
        toit: [f.x, P.solY + f.haut, f.z],       // la vapeur de chaufferie part de là
        demi: [f.hx, f.hz],
      };
    }
    const droite = candidatsGrue.find((g) => g.x > 0) || candidatsGrue[0];
    if (droite) {
      const g = droite;
      grue = { toit: [g.x, P.solY + g.haut, g.z], cs: [g.c, g.s], d: g.d, mat: 28, fleche: 32 };
    }

    /* ── Le sol de la ville : une seule boîte plate, dernière instance.
       Le shader la reconnaît (genre 2) et y dessine les rues avec les
       trames ci-dessus. */
    pousser(0, P.solY - 0.25, P.oeilZ, 2600, 0.25, 2600, 1, 0, 1, 2, 0);

    return {
      buf: new Float32Array(out), N: out.length / STRIDE, STRIDE,
      fenetre, grue, params: P, reperes: REPERES,
    };
  }

  racine.VueBaieVille = { PARAMS, STRIDE, generer, hash, coteCouture, REPERES };
})(typeof window !== 'undefined' ? window : globalThis);

/* ============================================================
   vue3d.js — LA VUE BAIE, qui est le site
   ============================================================
   Un plateau vitré sur quatre côtés au onzième étage, une ville tout
   autour, un soleil qui suit l'heure réelle — et le dashboard réparti
   sur cinq panneaux de verre suspendus dans la pièce. On passe de l'un
   à l'autre en tournant la tête : flèches, touches, ou en tirant le
   fond à la souris. Échap ramène à la vue plan, qui reste derrière.

   Le POURQUOI et toutes les MESURES sont dans le README, section
   « La vue baie ». Ce fichier ne porte que le comment. Les chiffres
   qu'on lit ici ne sont pas des promesses : ce sont des réglages, et
   chacun dit à quoi il sert.

   TABLE DES MATIÈRES
     1. SCÈNE       — les constantes partagées, le soleil, la caméra
     2. PALETTE     — les tokens de la page, lus une fois par ouverture
     3. SHADERS     — la ville et son ombre, le ciel, la pièce, la vignette
     4. MATHS       — persp, ortho, look, mul : trente lignes
     5. CAMÉRA PARTAGÉE — ce qui relie le canvas et la couche CSS
     6. GÉOMÉTRIE   — le cube, la ville (vue3d_ville.js), le plateau, les panneaux
     7. LA VILLE VIVANTE — les événements et leur cadence
     8. RENDU       — programmes, cartes d'ombre, une image
     9. LES PANNEAUX — les sections de la page, déplacées dans la pièce
    10. ENTRÉE, SORTIE, NAVIGATION
    11. COMMANDES   — touches, souris, et le crochet ?debug=1

   Conventions écrites une fois : −z est le NORD, +x l'EST, y monte.
   L'œil ne bouge JAMAIS ; seule la cible tourne. Le lacet ψ > 0 tourne
   vers +x (l'est, la droite de l'écran) : ψ = −60° regarde l'ouest.
   ============================================================ */

(() => {
  'use strict';

  if (!window.VueBaieVille) {
    console.warn('vue baie : vue3d_ville.js manque, la vue reste fermée');
    return;
  }

  /* ============================================================
     1. SCÈNE
     ============================================================ */
  const VILLE_PARAMS = window.VueBaieVille.PARAMS;
  const SCENE = {
    vitrageZ: -7.4,           // le mur-rideau nord : 5 m devant l'œil
    solPiece: -1.72,          // dessus de la dalle à −1,66 (épaisseur 0,06)
    plafondPiece: 1.68,
    module: 1.5,              // trame du mur-rideau : un montant tous les 1,5 m
    demiMontant: 0.022,
    allege: -0.95,            // la traverse intermédiaire
    traverse: 1.59,           // le haut du vitrage
    solVille: VILLE_PARAMS.solY,
    /* Champ HORIZONTAL fixe : 0,79·d de chaque côté ; le fov vertical
       en découle selon le format. À 1440×861 cela redonne 0,88 rad. */
    champH: 0.79,
    brumeK: 0.0009,
    loin: 2600,
  };
  const OEIL = [0, 0, SCENE.vitrageZ + 5.0];
  const DIST_CIBLE = 57.6;
  /* La cible tourne autour de l'œil : c'est le lacet. */
  const cible = (lacet) => [OEIL[0] + DIST_CIBLE * Math.sin(lacet), OEIL[1] - 0.05, OEIL[2] - DIST_CIBLE * Math.cos(lacet)];

  /* Le plateau : un carré de 34 m, vitré sur quatre côtés. Le vitrage
     nord est à 5 m de l'œil ; le sud, jamais dans le champ (il
     faudrait un lacet de 111°), n'est pas dessiné — il ne sert qu'à
     laisser entrer le soleil dans le calcul du sol. */
  const PLATEAU = { demi: 17, centreZ: SCENE.vitrageZ + 17 };

  /* Le vent vient de la gauche : tout ce qui dérive va vers +x. */
  const VENT = +1;

  /* ── LE SOLEIL ────────────────────────────────────────────────
     Il suit l'heure RÉELLE (Paris, 48,85° N). Position par la formule
     de la NOAA — validée contre Meeus à 0,3° près, ce que personne ne
     verra. Jamais la nuit : l'heure est bornée à 7 h–19 h puis ramenée
     vers midi jusqu'à ce que le soleil soit à 6° au-dessus de
     l'horizon (en décembre, 19 h c'est déjà la nuit). */
  function positionSoleil(date, lat = 48.85, lon = 2.35) {
    const rad = Math.PI / 180;
    const debut = Date.UTC(date.getUTCFullYear(), 0, 1);
    const jour = (date - debut) / 86400000;
    const g = 2 * Math.PI / 365 * (jour + (date.getUTCHours() - 12) / 24);
    const eqt = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g)
                - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
    const decl = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g)
               - 0.006758 * Math.cos(2 * g) + 0.000907 * Math.sin(2 * g)
               - 0.002697 * Math.cos(3 * g) + 0.00148 * Math.sin(3 * g);
    const minutesUTC = date.getUTCHours() * 60 + date.getUTCMinutes() + date.getUTCSeconds() / 60;
    const heureSolaire = minutesUTC + eqt + 4 * lon;
    const ha = (heureSolaire / 4 - 180) * rad;
    const la = lat * rad;
    const cosZ = Math.sin(la) * Math.sin(decl) + Math.cos(la) * Math.cos(decl) * Math.cos(ha);
    const zen = Math.acos(Math.min(1, Math.max(-1, cosZ)));
    const elevation = Math.PI / 2 - zen;
    let az = Math.acos(Math.min(1, Math.max(-1,
      (Math.sin(decl) - Math.sin(la) * cosZ) / (Math.cos(la) * Math.sin(zen)))));
    if (ha > 0) az = 2 * Math.PI - az;
    return { elevation, azimut: az,
             dir: [Math.sin(az) * Math.cos(elevation), Math.sin(elevation), -Math.cos(az) * Math.cos(elevation)] };
  }

  function heureDeLaVue(maintenant = new Date()) {
    const d = new Date(maintenant);
    const h = d.getHours() + d.getMinutes() / 60;
    if (h < 7) d.setHours(7, 0, 0, 0);
    else if (h > 19) d.setHours(19, 0, 0, 0);
    const versMidi = d.getHours() < 13 ? 10 : -10;
    for (let i = 0; i < 60 && positionSoleil(d).elevation < 6 * Math.PI / 180; i++) {
      d.setMinutes(d.getMinutes() + versMidi);
    }
    return d;
  }

  /* La couleur du soleil, dérivée du blanc chaud du papier — aucune
     couleur nouvelle : masse d'air de Kasten-Young, transmission par
     canal (Rayleigh + aérosol d'été voilé), normalisée sur le rouge.
     L'intensité s'éteint avec le soleil bas : I = T.r^0,6. */
  function couleurSoleil(elevationRad, papier) {
    const e = Math.max(0.5, elevationRad * 180 / Math.PI);
    const m = 1 / (Math.sin(e * Math.PI / 180) + 0.50572 * Math.pow(e + 6.07995, -1.6364));
    const T = [0.130, 0.193, 0.337].map((tau) => Math.exp(-tau * (m - 1)));
    return { couleur: papier.map((v, i) => v * T[i] / T[0]), intensite: Math.pow(T[0], 0.6) };
  }

  /* ============================================================
     2. PALETTE
     ============================================================
     Les couleurs viennent de la page, lues une fois par ouverture. Les
     mélanges se font en sRGB, comme color-mix() dans le CSS. Le seul
     bleu de la scène est un mélange d'indigo et de papier (t = 0,45,
     choisi sur capture : 0,35 tire vers l'ardoise, 0,55 vers le gris). */
  function couleur(nom, defaut) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(nom).trim() || defaut;
    const m = v.match(/^#([0-9a-f]{6})$/i);
    if (!m) return [0.5, 0.5, 0.5];
    const n = parseInt(m[1], 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }
  const melange = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);

  function lirePalette() {
    const bg = couleur('--bg', '#f4f1ea');
    const eleve = couleur('--bg-elevated', '#fffdf9');
    const creux = couleur('--bg-sunken', '#eae5da');
    const bordure = couleur('--border', '#ddd7cb');
    const neutre = couleur('--neutral', '#8a8172');
    const sombre = couleur('--text-muted', '#6b6459');
    const accent = couleur('--accent', '#24476e');
    const sombreFroid = melange(sombre, accent, 0.22);
    const cielHaut = melange(accent, eleve, 0.45);
    return {
      cielBas: eleve,                       // l'horizon : le blanc chaud du papier
      cielHaut,                             // le zénith : bleu d'été voilé
      // La lumière du ciel, en rapport : ce que le bleu fait à l'ombre.
      teinteCiel: melange([1, 1, 1], cielHaut.map((v, i) => v / eleve[i]), 0.35),
      papier: eleve,
      brume: bg,
      ville: neutre,
      sombre: sombreFroid,
      froid: sombreFroid.map((v, i) => v / sombre[i]),
      plafond: bg,
      sol: melange(creux, bordure, 0.5),
      cadre: melange(neutre, sombre, 0.5),
      verre: eleve,
      neutre,
      texte: couleur('--text', '#14120f'),
      objet: sombre,
      vapeur: bg,
    };
  }

  /* ============================================================
     3. SHADERS
     ============================================================ */
  const PRELUDE = `
float dither(vec2 px) {
  return fract(52.9829189 * fract(dot(px, vec2(0.06711056, 0.00583715)))) - 0.5;
}
float hachage(uvec3 v) {
  uint h = v.x * 747796405u ^ v.y * 2654435761u ^ v.z * 805459861u;
  h ^= h >> 16; h *= 2246822519u; h ^= h >> 13; h *= 3266489917u; h ^= h >> 16;
  return float(h & 0xFFFFFFu) / 16777216.0;
}
float bruit(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  uvec2 c = uvec2(ivec2(i) + 8192);
  float a = hachage(uvec3(c, 7u)), b = hachage(uvec3(c + uvec2(1u, 0u), 7u));
  float d = hachage(uvec3(c + uvec2(0u, 1u), 7u)), e = hachage(uvec3(c + uvec2(1u, 1u), 7u));
  return mix(mix(a, b, f.x), mix(d, e, f.x), f.y);
}
float fbm(vec2 p) {
  float s = 0.0, a = 0.5;
  for (int k = 0; k < 4; k++) { s += a * bruit(p); p = p * 2.03 + 17.0; a *= 0.5; }
  return s;
}
`;
  const fx = (v) => (+v).toFixed(4);

  /* ── LA VILLE ─────────────────────────────────────────────────
     Le sommet calcule aussi sa place dans les deux cartes d'ombre,
     avec un décalage le long de la normale, plus fort en incidence
     rasante : c'est ce qui évite l'acné sans faire décoller les
     ombres (réglé et mesuré : facteur de pente 2 + 0,45 m). */
  const VS_VILLE = `#version 300 es
in vec3 aPos; in vec3 aNrm;
in vec3 iPos; in vec3 iTaille; in vec2 iCS; in vec3 iExtra;
uniform mat4 uVP; uniform mat4 uLuz; uniform mat4 uLuzProche;
uniform vec3 uSoleil; uniform float uDecalN;
out vec3 vNrm; out vec3 vMonde; out vec3 vLocal; out vec3 vTaille; out vec3 vExtra;
out vec3 vOmbre; out vec3 vOmbreP;
flat out int vId;
void main() {
  vec3 q = aPos * iTaille;
  vec3 p = vec3(q.x * iCS.x - q.z * iCS.y, q.y, q.x * iCS.y + q.z * iCS.x) + iPos;
  vec3 n = vec3(aNrm.x * iCS.x - aNrm.z * iCS.y, aNrm.y, aNrm.x * iCS.y + aNrm.z * iCS.x);
  vNrm = n; vMonde = p; vLocal = aPos; vTaille = iTaille; vExtra = iExtra; vId = gl_InstanceID;
  float nl = clamp(dot(n, uSoleil), 0.0, 1.0);
  vec3 po = p + n * uDecalN * (1.0 - nl);
  vOmbre = (uLuz * vec4(po, 1.0)).xyz * 0.5 + 0.5;
  vOmbreP = (uLuzProche * vec4(po, 1.0)).xyz * 0.5 + 0.5;
  gl_Position = uVP * vec4(p, 1.0);
}`;

  /* La passe d'ombre : les mêmes instances, vues du soleil, profondeur seule. */
  const VS_OMBRE = `#version 300 es
in vec3 aPos;
in vec3 iPos; in vec3 iTaille; in vec2 iCS;
uniform mat4 uVP;
void main() {
  vec3 q = aPos * iTaille;
  vec3 p = vec3(q.x * iCS.x - q.z * iCS.y, q.y, q.x * iCS.y + q.z * iCS.x) + iPos;
  gl_Position = uVP * vec4(p, 1.0);
}`;
  const FS_OMBRE = `#version 300 es
precision highp float;
void main() {}`;

  const Q = VILLE_PARAMS.quartiers, CT = VILLE_PARAMS.couture;
  const FS_VILLE = `#version 300 es
precision highp float;
in vec3 vNrm; in vec3 vMonde; in vec3 vLocal; in vec3 vTaille; in vec3 vExtra;
in vec3 vOmbre; in vec3 vOmbreP;
flat in int vId;
uniform vec3 uVille; uniform vec3 uCiel; uniform vec3 uBrume; uniform vec3 uSombre;
uniform vec3 uFroid; uniform vec3 uTeinteCiel;
uniform vec3 uSoleil; uniform vec3 uCoulSoleil; uniform float uIntensite; uniform float uSoleilFacteur;
uniform highp sampler2DShadow uOmbre; uniform highp sampler2DShadow uOmbreProche;
uniform float uBrumeK;
uniform vec4 uTrouee;      // ombre de nuage qui glisse : centre x, z, rayon, force (< 0)
uniform vec3 uNappe;       // nappe de brume basse : centre x, demi-largeur, force
uniform float uDetail;
out vec4 frag;
${PRELUDE}
const float SOL = ${fx(VILLE_PARAMS.solY)};
const float ETAGE = ${fx(VILLE_PARAMS.etage)};

float bande(float f, float a, float b, float fw) {
  return 1.0 - (smoothstep(a - fw, a + fw, f) * smoothstep(b + fw, b - fw, f));
}
float rues(vec2 xz) {
  float cote = ((xz.y - ${fx(CT.z0)}) * ${fx(CT.x1 - CT.x0)} - (xz.x - ${fx(CT.x0)}) * ${fx(CT.z1 - CT.z0)})
               / ${fx(Math.hypot(CT.x1 - CT.x0, CT.z1 - CT.z0))};
  float fwc = fwidth(cote);
  float boulevard = 1.0 - smoothstep(${fx(CT.largeur / 2)} - fwc, ${fx(CT.largeur / 2)} + fwc, abs(cote));
  float ang, pasU, pasV, rue, avenue, periode;
  if (cote > 0.0) { ang = ${fx(Q[0].angle)}; pasU = ${fx(Q[0].pasU)}; pasV = ${fx(Q[0].pasV)}; rue = ${fx(Q[0].rue)}; avenue = ${fx(Q[0].avenue)}; periode = ${fx(Q[0].periode)}; }
  else            { ang = ${fx(Q[1].angle)}; pasU = ${fx(Q[1].pasU)}; pasV = ${fx(Q[1].pasV)}; rue = ${fx(Q[1].rue)}; avenue = ${fx(Q[1].avenue)}; periode = ${fx(Q[1].periode)}; }
  float c = cos(ang), s = sin(ang);
  float u = xz.x * c + xz.y * s, v = -xz.x * s + xz.y * c;
  float iu = floor(u / pasU), iv = floor(v / pasV);
  float fu = u - iu * pasU, fv = v - iv * pasV;
  float rueG = (mod(iu, periode) < 0.5) ? avenue : rue;
  float rueD = (mod(iu + 1.0, periode) < 0.5) ? avenue : rue;
  float rueB = (mod(iv, 6.0) < 0.5) ? avenue * 0.8 : rue;
  float rueH = (mod(iv + 1.0, 6.0) < 0.5) ? avenue * 0.8 : rue;
  float fwu = fwidth(u), fwv = fwidth(v);
  float ilotU = 1.0 - bande(fu, rueG * 0.5, pasU - rueD * 0.5, fwu);
  float ilotV = 1.0 - bande(fv, rueB * 0.5, pasV - rueH * 0.5, fwv);
  return max(1.0 - ilotU * ilotV, boulevard);
}

/* L'ombre : PCF 3×3 sur la carte proche (300 m, 0,29 m par texel) si
   le point y tombe, sinon sur la grande (900 m) ; hors carte, éclairé.
   La comparaison est matérielle (LINEAR), le noyau couvre 4×4 texels. */
float pcf(highp sampler2DShadow s, vec3 c) {
  float r = 0.0;
  r += textureOffset(s, c, ivec2(-1, -1)); r += textureOffset(s, c, ivec2(0, -1)); r += textureOffset(s, c, ivec2(1, -1));
  r += textureOffset(s, c, ivec2(-1,  0)); r += textureOffset(s, c, ivec2(0,  0)); r += textureOffset(s, c, ivec2(1,  0));
  r += textureOffset(s, c, ivec2(-1,  1)); r += textureOffset(s, c, ivec2(0,  1)); r += textureOffset(s, c, ivec2(1,  1));
  return r / 9.0;
}
float ombre() {
  vec3 cp = vOmbreP;
  if (cp.x > 0.002 && cp.x < 0.998 && cp.y > 0.002 && cp.y < 0.998 && cp.z <= 1.0) return pcf(uOmbreProche, cp);
  vec3 c = vOmbre;
  if (c.x < 0.0 || c.x > 1.0 || c.y < 0.0 || c.y > 1.0 || c.z > 1.0) return 1.0;
  return pcf(uOmbre, c);
}

void main() {
  float genre = vExtra.y;
  float d = length(vMonde.xz - vec2(0.0, ${fx(VILLE_PARAMS.oeilZ)}));
  float rue = vMonde.y - SOL;
  vec3 n = normalize(vNrm);

  vec2 uv = abs(vLocal.x) > 0.999 ? vec2(vLocal.z * vTaille.z, rue) : vec2(vLocal.x * vTaille.x, rue);
  float matiere = floor(vExtra.z * 4.0);
  float rythme = fract(vExtra.z * 4.0);
  float largeur = matiere > 2.5 ? 1.4 : 2.4 + rythme * 1.4;
  vec2 g = uv / vec2(largeur, ETAGE);
  vec2 fw = fwidth(g);
  float masqueRues = rues(vMonde.xz);

  // 1. La matière : pierre, béton, enduit, verre — chaque parcelle a sa valeur.
  vec3 col = uVille * vExtra.x;
  col *= matiere < 0.5 ? 1.06 : (matiere < 1.5 ? 0.96 : (matiere < 2.5 ? 1.0 : 0.88));
  if (matiere > 2.5) col *= mix(vec3(1.0), uFroid, 0.5);

  /* 2. La lumière : le ciel (hémisphérique, un peu bleu) et le soleil
     (lambert × ombre × sa couleur × son intensité). Le facteur
     d'événement fait sortir le soleil d'un nuage, ou l'y rentrer. */
  float nl = max(0.0, dot(n, uSoleil));
  float o = ombre();
  vec3 ambiant = (0.38 + 0.30 * (n.y * 0.5 + 0.5)) * uTeinteCiel;
  vec3 direct = uCoulSoleil * (uIntensite * uSoleilFacteur * 0.62 * nl * o);

  // 3. Occlusion de canyon : le pied d'une tour ne voit qu'un ruban de ciel.
  float ao = 0.70 + 0.30 * smoothstep(0.0, 24.0, rue);
  ao += 0.06 * smoothstep(0.35, 0.5, vLocal.y) * (1.0 - abs(n.y));

  if (genre > 1.5 && genre < 2.5) {
    vec3 asphalte = mix(uVille, uSombre, 0.45);
    vec3 coeur = uVille * 0.92;
    float m = mix(masqueRues, 0.45, smoothstep(600.0, 900.0, d));
    col = mix(coeur, asphalte, m);
    ao = 0.72;
  } else if ((genre < 0.5 || genre > 2.5) && abs(n.y) < 0.5 && uDetail > 0.5) {
    vec2 f = fract(g);
    vec2 marge = matiere > 2.5 ? vec2(0.06) : (matiere < 0.5 ? vec2(0.28, 0.20) : vec2(0.18));
    if (matiere > 0.5 && matiere < 1.5) marge.x = 0.04;
    vec2 w = smoothstep(marge - fw, marge + fw, f) * smoothstep(1.0 - marge + fw, 1.0 - marge - fw, f);
    float fen = w.x * w.y;
    float moyenne = (1.0 - 2.0 * marge.x) * (1.0 - 2.0 * marge.y);
    fen = mix(fen, moyenne, smoothstep(0.15, 0.5, max(fw.x, fw.y)));
    float store = hachage(uvec3(uint(vId), uint(int(floor(g.x)) + 4096), uint(int(floor(g.y)) + 4096)));
    float vitrage = matiere > 2.5 ? 0.84 : 0.90 - 0.06 * step(0.7, store);
    float rdc = 1.0 - smoothstep(3.6, 4.6, rue);
    vitrage = mix(vitrage, 0.80, rdc);
    col *= mix(1.0, vitrage, fen);
  } else if (genre < 0.5 && n.y > 0.5) {
    float pres = 1.0 - smoothstep(300.0, 600.0, d);
    float bord = min((1.0 - abs(vLocal.x)) * vTaille.x, (1.0 - abs(vLocal.z)) * vTaille.z);
    col *= 1.0 - 0.18 * pres * (1.0 - smoothstep(0.0, 1.8, bord));
    col *= 1.0 + pres * (bruit(vMonde.xz * 0.6) - 0.5) * 0.08;
  }
  col *= (ambiant + direct) * ao;
  col *= mix(vec3(1.0), uFroid, (1.0 - ao) * 0.5);

  // 4. L'ombre d'un nuage qui glisse (événement) : contour de nuage, jamais un cylindre.
  if (uTrouee.w < -0.001) {
    float e = 1.0 - smoothstep(uTrouee.z * 0.45, uTrouee.z, length(vMonde.xz - uTrouee.xy));
    e *= smoothstep(0.25, 0.55, fbm(vMonde.xz * 0.007 + uTrouee.xy * 0.001));
    col *= 1.0 - 0.22 * e * (-uTrouee.w);
  }

  // 5. La perspective aérienne, vers la couleur du ciel bas.
  vec3 brumeCol = mix(uBrume, uCiel, clamp(vMonde.y * 0.02 + 0.6, 0.0, 1.0));
  brumeCol = mix(brumeCol, uCiel, smoothstep(1200.0, 2000.0, d));
  float brume = 1.0 - exp(-d * uBrumeK);
  brume = max(brume, smoothstep(1300.0, 1600.0, d));
  col = mix(col, brumeCol, clamp(brume, 0.0, 0.97));
  col = mix(col, brumeCol, clamp((8.0 - rue) * 0.045, 0.0, 0.35) * brume);
  if (uNappe.z > 0.001) {
    float nap = uNappe.z * (1.0 - smoothstep(uNappe.y * 0.5, uNappe.y, abs(vMonde.x - uNappe.x)))
                * (1.0 - smoothstep(2.0, 9.0, rue));
    col = mix(col, brumeCol, nap * 0.5);
  }
  frag = vec4(col + dither(gl_FragCoord.xy) / 255.0, 1.0);
}`;

  /* ── LE CIEL ──────────────────────────────────────────────────
     Un rayon par pixel à partir de la base caméra : le ciel est fixé
     au MONDE, pas à l'écran — avec le lacet, un dégradé d'écran aurait
     tourné avec la tête. Blanc du papier à l'horizon, bleu voilé au
     zénith, halo et disque du soleil, cumulus sur un plan à 1 500 m
     éclairés côté soleil. Immobiles entre deux événements ; ils
     dérivent pendant qu'un événement joue. Dessiné en dernier à la
     profondeur 1 : il ne remplit que les pixels vides. */
  const VS_CIEL = `#version 300 es
const vec2 coins[3] = vec2[3](vec2(-1., -1.), vec2(3., -1.), vec2(-1., 3.));
out vec2 vUV;
void main() {
  vec2 p = coins[gl_VertexID];
  vUV = p * 0.5 + 0.5;
  gl_Position = vec4(p, 1.0, 1.0);
}`;

  const FS_CIEL = `#version 300 es
precision highp float;
in vec2 vUV;
uniform vec3 uAvant, uDroite, uHaut; uniform vec2 uTan;
uniform vec3 uCielHaut, uCielBas;
uniform vec3 uSoleil; uniform vec3 uCoulSoleil; uniform float uIntensite;
uniform vec2 uNuages;        // altitude du plan (m), seuil de couverture
uniform vec2 uDerive;        // dérive du plan de nuages (m)
uniform float uOmbreNuage;
out vec4 frag;
${PRELUDE}
void main() {
  vec2 ndc = vUV * 2.0 - 1.0;
  vec3 ray = normalize(uAvant + uDroite * (ndc.x * uTan.x) + uHaut * (ndc.y * uTan.y));
  float el = clamp(ray.y, 0.0, 1.0);
  float g = (1.0 - exp(-3.2 * el)) / (1.0 - exp(-3.2));
  vec3 col = mix(uCielBas, uCielHaut, g);
  float cosS = dot(ray, uSoleil);
  float halo = (0.06 * pow(max(cosS, 0.0), 8.0) + 0.40 * pow(max(cosS, 0.0), 90.0)) * uIntensite;
  float disque = smoothstep(0.99996, 0.999992, cosS);
  col = mix(col, uCoulSoleil, clamp(halo + disque, 0.0, 1.0));
  if (ray.y > 0.015) {
    float t = uNuages.x / ray.y;
    vec2 p = (ray.xz * t + uDerive) / 900.0;
    vec2 versSoleil = normalize(uSoleil.xz + vec2(1e-4)) * 0.08;
    float d = fbm(p), dS = fbm(p + versSoleil);
    float couv = smoothstep(uNuages.y, uNuages.y + 0.16, d);
    couv *= exp(-t / 9000.0) * smoothstep(0.015, 0.14, ray.y);
    float lum = clamp(0.55 + (d - dS) * 7.0, 0.0, 1.0);
    vec3 ombre = mix(uCielHaut, uCielBas, 0.45 - 0.25 * uOmbreNuage);
    vec3 nuage = mix(ombre, mix(uCoulSoleil, uCielBas, 0.45), lum);
    col = mix(col, nuage, couv);
  }
  frag = vec4(col + dither(gl_FragCoord.xy) / 255.0, 1.0);
}`;

  /* ── LA PIÈCE et les objets ───────────────────────────────────
     Matériaux : 0 opaque intérieur, 1 sol, 2 verre, 3 objet dehors,
     4 vapeur, 5 reflet de fenêtre. Le sol reçoit le soleil qui entre
     par le vitrage qui lui fait face — calcul analytique, vérifié
     contre une référence indépendante à 0 pixel près. */
  const VS_PIECE = `#version 300 es
in vec3 aPos; in vec3 aNrm;
uniform mat4 uVP; uniform vec3 uP; uniform vec3 uS; uniform vec2 uCS;
out vec3 vNrm; out vec3 vMonde; out vec3 vLocal;
void main() {
  vec3 q = aPos * uS;
  vec3 p = vec3(q.x * uCS.x - q.z * uCS.y, q.y, q.x * uCS.y + q.z * uCS.x) + uP;
  vNrm = vec3(aNrm.x * uCS.x - aNrm.z * uCS.y, aNrm.y, aNrm.x * uCS.y + aNrm.z * uCS.x);
  vMonde = p; vLocal = aPos;
  gl_Position = uVP * vec4(p, 1.0);
}`;

  const FS_PIECE = `#version 300 es
precision highp float;
in vec3 vNrm; in vec3 vMonde; in vec3 vLocal;
uniform vec3 uOeil; uniform vec3 uCol; uniform float uAlpha; uniform float uMateriau;
uniform vec3 uCiel; uniform vec3 uPlafond; uniform vec3 uSolCol; uniform vec3 uCadre; uniform vec3 uNeutre;
uniform vec3 uBrume; uniform float uBrumeK;
uniform vec3 uSoleil; uniform vec3 uCoulSoleilRel; uniform float uIntensite; uniform float uSoleilFacteur;
out vec4 frag;
${PRELUDE}
const vec2 BOITE_X = vec2(${fx(-PLATEAU.demi)}, ${fx(PLATEAU.demi)});
const vec2 BOITE_Z = vec2(${fx(SCENE.vitrageZ)}, ${fx(PLATEAU.centreZ + PLATEAU.demi)});
const float CENTRE_Z = ${fx(PLATEAU.centreZ)};
const float ALLEGE = ${fx(SCENE.allege)}, TRAVERSE = ${fx(SCENE.traverse)};
const float MODULE = ${fx(SCENE.module)}, DEMI_MONTANT = ${fx(SCENE.demiMontant)};
const float SOL_Y = ${fx(SCENE.solPiece + 0.06)};
const float SOL_OMBRE = 0.22;   // exposition du sol à l'ombre quand le soleil est fort (0 = aucune)
const float SOL_GAIN = 2.4;    // lumière ajoutée par le soleil, en écran

/* Où un rayon partant de p dans la direction D sort de la boîte du
   plateau : le vitrage traversé, et la distance signée au bord de la
   lumière (négatif dans un montant ou hors de la hauteur vitrée). */
float bordVitrage(vec3 p, vec3 D) {
  float tx = (D.x >= 0.0 ? BOITE_X.y - p.x : p.x - BOITE_X.x) / max(abs(D.x), 1e-6);
  float tz = (D.z >= 0.0 ? BOITE_Z.y - p.z : p.z - BOITE_Z.x) / max(abs(D.z), 1e-6);
  bool parX = tx < tz;
  vec3 h = p + min(tx, tz) * D;
  float lat = parX ? h.z - CENTRE_Z : h.x;
  float dM = abs(mod(lat + MODULE * 0.5, MODULE) - MODULE * 0.5) - DEMI_MONTANT;
  float dH = min(h.y - ALLEGE, TRAVERSE - h.y);
  return min(dM, dH);
}

void main() {
  int m = int(uMateriau + 0.5);
  vec3 n = normalize(vNrm);
  vec3 v = normalize(uOeil - vMonde);
  vec3 col = uCol;
  float alpha = uAlpha;
  // Distance au vitrage le plus proche : la lumière entre par là.
  float dMur = min(min(vMonde.x - BOITE_X.x, BOITE_X.y - vMonde.x), min(vMonde.z - BOITE_Z.x, BOITE_Z.y - vMonde.z));
  float versLaBaie = clamp((16.0 - dMur) / 16.0, 0.0, 1.0);
  // Les dérivées, hors de tout branchement (Metal).
  vec3 Em = vec3(uOeil.x, 2.0 * SOL_Y - uOeil.y, uOeil.z);
  float dReflet = bordVitrage(vMonde, normalize(vMonde - Em));
  float wR = max(fwidth(dReflet), 1e-4);
  float dSoleil = uSoleil.y > 0.005 ? bordVitrage(vMonde, uSoleil) : -1.0;
  float wS = max(fwidth(dSoleil), 1e-4);

  if (m == 0 || m == 1) {
    float ecl = 0.90 + 0.10 * (n.y * 0.5 + 0.5) + 0.08 * versLaBaie - 0.06 * abs(n.x);
    col *= ecl;
    if (m == 1) {
      col = mix(col, uCiel, versLaBaie * 0.60);
      /* Le reflet du mur-rideau dans le béton poli : par l'œil miroir
         sous le sol, le rayon réfléchi sort par un vitrage — s'il y
         croise un montant ou la traverse, c'est du profilé, sinon du
         ciel. Schlick F0 = 0,04, rasant. */
      float cosT = abs(dot(v, vec3(0.0, 1.0, 0.0)));
      float F = 0.04 + 0.96 * pow(1.0 - cosT, 5.0);
      float profil = 1.0 - smoothstep(-wR, wR, dReflet);
      vec3 reflet = mix(uCiel, uCadre, profil);
      reflet = mix(reflet, uCadre, (1.0 - smoothstep(0.0, 0.35, dMur)) * 0.6);
      col = mix(col, reflet, F);
      /* Le soleil sur le sol, par le vitrage qui lui fait face, hachuré
         par les montants. Il AJOUTE de la lumière (mélange en écran :
         aucun canal ne peut s'assombrir, l'ombre d'un montant reste donc
         plus sombre que le soleil sur tous les canaux). Le sol à l'ombre
         est exposé un peu plus bas quand le soleil est fort : l'écran ne
         sait pas montrer un soleil plus blanc que le papier, il faut donc
         que l'ombre lui laisse la place. Plancher perceptif : un lambert
         strict (sin 8° = 0,14) effacerait la tache du soir. */
      float k = uIntensite * uSoleilFacteur;
      col *= 1.0 - SOL_OMBRE * k;
      float s = smoothstep(-wS, wS, dSoleil) * k * (0.55 + 0.45 * uSoleil.y);
      vec3 L = clamp(uCoulSoleilRel * (s * SOL_GAIN), 0.0, 1.0);
      col = 1.0 - (1.0 - col) * (1.0 - L);
    }
  } else if (m == 2) {
    float cosT = abs(dot(v, n));
    float F = 0.08 + 0.92 * pow(1.0 - cosT, 5.0);
    vec3 refl = mix(uPlafond, uSolCol, smoothstep(0.2, -0.2, vLocal.y));
    col = mix(refl, uNeutre, 0.25);
    alpha = clamp(F + 0.025, 0.0, 1.0);
  } else {
    float d = length(vMonde.xz);
    vec3 brumeCol = mix(uBrume, uCiel, clamp(vMonde.y * 0.02 + 0.6, 0.0, 1.0));
    float brume = clamp(1.0 - exp(-d * uBrumeK), 0.0, 0.97);
    if (m == 3) {
      float nl = max(0.0, dot(n, uSoleil));
      col = mix(uCol * (0.70 + 0.20 * (n.y * 0.5 + 0.5) + 0.35 * nl * uIntensite * uSoleilFacteur), brumeCol, brume);
    } else if (m == 4) {
      float r = length(vLocal.xy);
      col = mix(uCol, brumeCol, brume * 0.5);
      alpha = uAlpha * smoothstep(1.0, 0.25, r);
    } else {
      col = uCiel;
    }
  }
  frag = vec4(col + dither(gl_FragCoord.xy) / 255.0, alpha);
}`;

  const FS_VIGNETTE = `#version 300 es
precision highp float;
in vec2 vUV;
uniform vec3 uTeinte; uniform float uFormat;
out vec4 frag;
void main() {
  float d = length((vUV - 0.5) * vec2(uFormat, 1.0));
  frag = vec4(uTeinte, 0.12 * smoothstep(0.35, 1.05, d));
}`;

  /* ============================================================
     4. MATHS
     ============================================================ */
  const M = {
    persp(fov, ar, n, f) {
      const t = 1 / Math.tan(fov / 2);
      return [t / ar, 0, 0, 0, 0, t, 0, 0, 0, 0, (f + n) / (n - f), -1,
              0, 0, 2 * f * n / (n - f), 0];
    },
    ortho(l, r, b, t, n, f) {
      return [2 / (r - l), 0, 0, 0, 0, 2 / (t - b), 0, 0, 0, 0, -2 / (f - n), 0,
              -(r + l) / (r - l), -(t + b) / (t - b), -(f + n) / (f - n), 1];
    },
    look(e, c, u) {
      const s = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
      const nr = (v) => { const l = Math.hypot(...v); return [v[0] / l, v[1] / l, v[2] / l]; };
      const cr = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
                            a[0] * b[1] - a[1] * b[0]];
      const z = nr(s(e, c)), x = nr(cr(u, z)), y = cr(z, x);
      return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
              -(x[0] * e[0] + x[1] * e[1] + x[2] * e[2]),
              -(y[0] * e[0] + y[1] * e[1] + y[2] * e[2]),
              -(z[0] * e[0] + z[1] * e[1] + z[2] * e[2]), 1];
    },
    mul(A, B) {
      const o = new Float32Array(16);
      for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) {
        let s = 0;
        for (let k = 0; k < 4; k++) s += A[k * 4 + j] * B[i * 4 + k];
        o[i * 4 + j] = s;
      }
      return o;
    },
    xf(A, p) {
      return [A[0] * p[0] + A[4] * p[1] + A[8] * p[2] + A[12],
              A[1] * p[0] + A[5] * p[1] + A[9] * p[2] + A[13],
              A[2] * p[0] + A[6] * p[1] + A[10] * p[2] + A[14]];
    },
    projeter(vp, p, L, H) {
      const x = vp[0] * p[0] + vp[4] * p[1] + vp[8] * p[2] + vp[12];
      const y = vp[1] * p[0] + vp[5] * p[1] + vp[9] * p[2] + vp[13];
      const w = vp[3] * p[0] + vp[7] * p[1] + vp[11] * p[2] + vp[15];
      return [(x / w + 1) / 2 * L, (1 - y / w) / 2 * H];
    },
  };

  /* ============================================================
     5. LA CAMÉRA PARTAGÉE ENTRE WEBGL ET LE CSS
     ============================================================
     Le panneau ne peut pas être une texture (WebKit souille tout canvas
     ayant touché un foreignObject) : le dashboard est du DOM RÉEL,
     transformé en 3D par le CSS et composé par-dessus le canvas. La
     perspective CSS vaut la focale de la projection, en pixels :
     f = (hauteur/2)/tan(fov/2). Deux inversions de signe : la ligne Y
     pour la caméra, la colonne Y pour l'objet — une conjugaison par
     diag(1,−1,1,1), exacte pour toute vue et toute rotation Y. Mesuré
     avec la caméra en lacet : écart par coin ≤ 0,06 px. */
  const eps = (n) => (Math.abs(n) < 1e-6 ? 0 : +n.toFixed(6));

  function matriceCameraCSS(v) {
    const e = [v[0], -v[1], v[2], v[3], v[4], -v[5], v[6], v[7],
               v[8], -v[9], v[10], v[11], v[12], -v[13], v[14], v[15]];
    return 'matrix3d(' + e.map(eps).join(',') + ')';
  }
  function matriceObjetCSS(m) {
    const e = [m[0], m[1], m[2], m[3], -m[4], -m[5], -m[6], -m[7],
               m[8], m[9], m[10], m[11], m[12], m[13], m[14], m[15]];
    return 'matrix3d(' + e.map(eps).join(',') + ')';
  }
  const PX_PAR_METRE = 400;
  function matriceMonde(pos, rotY, ech) {
    const c = Math.cos(rotY) * ech, s = Math.sin(rotY) * ech;
    return [c, 0, -s, 0, 0, ech, 0, 0, s, 0, c, 0, pos[0], pos[1], pos[2], 1];
  }

  function calculerCamera(L, H, cvL, cvH, lacet) {
    const fov = 2 * Math.atan(SCENE.champH * H / L);
    const proj = M.persp(fov, cvL / cvH, 0.1, SCENE.loin);
    const vue = M.look(OEIL, cible(lacet), [0, 1, 0]);
    const vp = M.mul(proj, vue);
    const focale = proj[5] * H / 2;
    const vueCSS = vue.slice();
    vueCSS[12] *= PX_PAR_METRE; vueCSS[13] *= PX_PAR_METRE; vueCSS[14] *= PX_PAR_METRE;
    // La base de la caméra, pour le ciel : droite, haut, avant.
    const droite = [vue[0], vue[4], vue[8]], haut = [vue[1], vue[5], vue[9]], avant = [-vue[2], -vue[6], -vue[10]];
    return { fov, vp, focale, vueCSS, L, H, lacet, droite, haut, avant,
             tan: [Math.tan(fov / 2) * cvL / cvH, Math.tan(fov / 2)] };
  }

  /* ============================================================
     6. GÉOMÉTRIE
     ============================================================ */
  const CUBE = (() => {
    const faces = [
      [[0, 0, 1], [-1, -1, 1, 1, -1, 1, 1, 1, 1, -1, -1, 1, 1, 1, 1, -1, 1, 1]],
      [[0, 0, -1], [1, -1, -1, -1, -1, -1, -1, 1, -1, 1, -1, -1, -1, 1, -1, 1, 1, -1]],
      [[1, 0, 0], [1, -1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, -1, 1, 1, 1]],
      [[-1, 0, 0], [-1, -1, -1, -1, -1, 1, -1, 1, 1, -1, -1, -1, -1, 1, 1, -1, 1, -1]],
      [[0, 1, 0], [-1, 1, 1, 1, 1, 1, 1, 1, -1, -1, 1, 1, 1, 1, -1, -1, 1, -1]],
      [[0, -1, 0], [-1, -1, -1, 1, -1, -1, 1, -1, 1, -1, -1, -1, 1, -1, 1, -1, -1, 1]],
    ];
    const d = [];
    for (const [n, v] of faces) {
      for (let i = 0; i < 18; i += 3) d.push(v[i], v[i + 1], v[i + 2], n[0], n[1], n[2]);
    }
    return new Float32Array(d);
  })();

  /* La ville (vue3d_ville.js), plus une instance de plus : l'immeuble
     de l'observateur, 40 × 40 × 60 m, qui n'existe que dans la passe
     d'ombre — il projette sur ses voisins, on ne le voit jamais. */
  const VILLE = window.VueBaieVille.generer();
  const IMMEUBLE = VILLE_PARAMS.immeuble;
  const TAMPON_VILLE = (() => {
    const b = new Float32Array(VILLE.buf.length + VILLE.STRIDE);
    b.set(VILLE.buf);
    b.set([IMMEUBLE.x, SCENE.solVille + 30, IMMEUBLE.z, 20, 30, 20, 1, 0, 1, 0, 0], VILLE.buf.length);
    return b;
  })();

  const PIECE = (() => {
    const D = PLATEAU.demi, cz = PLATEAU.centreZ;
    const p = [
      { pos: [0, SCENE.solPiece, cz], ech: [D, 0.06, D], teinte: 'sol', materiau: 1 },
      { pos: [0, SCENE.plafondPiece, cz], ech: [D, 0.06, D], teinte: 'plafond', materiau: 0 },
    ];
    // Trois murs-rideaux dessinés : nord, est, ouest (le sud est hors champ).
    const murs = [
      { axe: 'x', fixe: SCENE.vitrageZ, orig: 0 },
      { axe: 'z', fixe: D, orig: cz },
      { axe: 'z', fixe: -D, orig: cz },
    ];
    for (const m of murs) {
      const pos = (le, y) => (m.axe === 'x' ? [le, y, m.fixe] : [m.fixe, y, le]);
      const ech = (long, ep, h) => (m.axe === 'x' ? [long, h, ep] : [ep, h, long]);
      for (let i = -11; i <= 11; i++) {
        p.push({ pos: pos(m.orig + i * SCENE.module, 0), ech: ech(SCENE.demiMontant, 0.05, 1.7), teinte: 'cadre', materiau: 0 });
      }
      p.push({ pos: pos(m.orig, 1.66), ech: ech(D, 0.10, 0.07), teinte: 'cadre', materiau: 0 });
      p.push({ pos: pos(m.orig, -1.70), ech: ech(D, 0.11, 0.06), teinte: 'cadre', materiau: 0 });
      p.push({ pos: pos(m.orig, SCENE.allege), ech: ech(D, 0.06, 0.03), teinte: 'cadre', materiau: 0 });
    }
    for (const [sx, sz] of [[1, -1], [-1, -1], [1, 1], [-1, 1]]) {
      p.push({ pos: [sx * D, 0, cz + sz * D], ech: [0.06, 1.7, 0.06], teinte: 'cadre', materiau: 0 });
    }
    return p;
  })();
  const VITRAGES = [
    { pos: [0, -0.05, SCENE.vitrageZ], ech: [PLATEAU.demi, 1.68, 0.012] },
    { pos: [PLATEAU.demi, -0.05, PLATEAU.centreZ], ech: [0.012, 1.68, PLATEAU.demi] },
    { pos: [-PLATEAU.demi, -0.05, PLATEAU.centreZ], ech: [0.012, 1.68, PLATEAU.demi] },
  ];

  /* ── LES CINQ PANNEAUX ────────────────────────────────────────
     Sur un arc autour de l'œil, tous les 30°, chacun face à l'œil : le
     courant à 3 m, les autres à 4,5 m (deux tiers de la taille, en
     retrait). Les sections RÉELLES de la page y sont déplacées — pas
     clonées : le formulaire du carnet reste un formulaire, les filtres
     des filtres. 408 px par mètre partout : même taille de texte. */
  const PANNEAUX = [
    { id: 'positions', nom: 'Mes positions', largeurPx: 1020, largeurM: 2.5, sources: ['#section-positions'] },
    { id: 'fiabilite', nom: 'Fiabilité du signal', largeurPx: 1020, largeurM: 2.5, sources: ['#quality-section'] },
    { id: 'synthese', nom: 'Synthèse', largeurPx: 1020, largeurM: 2.5, sources: ['.masthead', '#stocks-stats'], extrait: true },
    { id: 'tableau', nom: 'Valeurs suivies', largeurPx: 1428, largeurM: 3.5, sources: ['#section-valeurs'] },
    { id: 'historique', nom: 'Historique des signaux', largeurPx: 1020, largeurM: 2.5, sources: ['#section-historique', '#section-bascules'] },
  ];
  const R_COURANT = 3.0, R_AUTRE = 4.5, PAS_ARC = Math.PI / 6;
  const angleDe = (i) => (i - 2) * PAS_ARC;

  /* ============================================================
     7. LA VILLE VIVANTE
     ============================================================
     Un seul événement à la fois, le suivant attend 12 à 30 s APRÈS la
     fin du précédent ; entre deux, rien. Chaque type a sa cadence (4 à
     15 im/s, jamais plus : le prix est PAR IMAGE, chaque image fait
     recomposer la fenêtre). Un timer cadence, requestAnimationFrame
     ne fait que poser l'image. Tous météo ou trafic. */
  const ATTENTE_MIN = 12000, ATTENTE_MAX = 30000;
  const plateau = (u) => lisse(0, 0.25, u) * lisse(1, 0.75, u);
  function lisse(a, b, x) { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); }
  const douce = (u) => u * u * (3 - 2 * u);
  let angleGrue = 0.6;
  const SOLEIL_VOILE = 0.75;      // le soleil, un peu voilé, entre deux événements

  const TYPES = {
    /* Le soleil sort d'un nuage : tout s'allume, et la fenêtre du reflet au pic. */
    eclaircie: {
      duree: 30000, ips: 10, poids: 4,
      etat(u) { return { force: plateau(u) }; },
      uniformes(v) { return { uSoleilFacteur: SOLEIL_VOILE + (1 - SOLEIL_VOILE) * v.force }; },
      dessiner(ctx, v) {
        const f = VILLE.fenetre;
        if (!f || v.force < 0.6) return;
        ctx.boite({ pos: f.pos, ech: f.ech, cs: f.cs, col: ctx.palette.cielBas, alpha: (v.force - 0.6) / 0.4 * 0.85, materiau: 5 });
      },
    },
    /* L'ombre d'un nuage qui glisse, et le soleil qui se voile un peu plus. */
    nuage: {
      duree: 55000, ips: 8, poids: 4,
      etat(u) { return { x: (-700 + u * 1400) * VENT, z: -250 - u * 500, r: 340, force: plateau(u) }; },
      uniformes(v) { return { uTrouee: [v.x, v.z, v.r, -v.force], uSoleilFacteur: SOLEIL_VOILE - 0.30 * v.force, uOmbreNuage: v.force }; },
    },
    brume: {
      duree: 150000, ips: 4, poids: 2,
      etat(u) { return { k: SCENE.brumeK + 0.0008 * plateau(u) }; },
      uniformes(v) { return { uBrumeK: v.k }; },
    },
    avion: {
      duree: 26000, ips: 15, poids: 2,
      etat(u) { return { x: (-660 + u * 1320) * VENT, y: 96 + Math.sin(u * Math.PI) * 3, z: -700 }; },
      dessiner(ctx, v) {
        ctx.boite({ pos: [v.x, v.y, v.z], ech: [13, 1.5, 1.5], col: ctx.palette.objet, materiau: 3 });
        ctx.boite({ pos: [v.x, v.y, v.z], ech: [3, 0.7, 9], col: ctx.palette.objet, materiau: 3 });
      },
    },
    grue: {
      duree: 45000, ips: 8, poids: 3,
      etat(u) { return { angle: angleGrue + 0.44 * douce(u) }; },
      fin(v) { angleGrue = v.angle; },
    },
    vapeur: {
      duree: 40000, ips: 12, poids: 2,
      etat(u) {
        const b = [];
        for (let i = 0; i < 5; i++) {
          const t = Math.min(1, Math.max(0, (u - i * 0.12) / 0.55));
          if (t <= 0 || t >= 1) continue;
          b.push({ t, dx: VENT * (0.6 + 1.6 * t) * 9 * t, dy: 0.5 + 9 * t, ech: 1.2 + 4.5 * t, alpha: 0.28 * (1 - t) * (1 - t) });
        }
        return { bouffees: b };
      },
      dessiner(ctx, v) {
        const f = VILLE.fenetre;
        if (!f) return;
        for (const b of v.bouffees) {
          ctx.boite({ pos: [f.toit[0] + b.dx - f.demi[0] * 0.5, f.toit[1] + b.dy, f.toit[2]],
                      ech: [b.ech, b.ech, 0.05], col: ctx.palette.vapeur, alpha: b.alpha, materiau: 4 });
        }
      },
    },
    nappe: {
      duree: 70000, ips: 6, poids: 3,
      etat(u) { return { x: (-520 + u * 1040) * VENT, l: 260, force: plateau(u) * 0.9 }; },
      uniformes(v) { return { uNappe: [v.x, v.l, v.force] }; },
    },
    /* Le plafond de nuages qui se lève : les cumulus s'amincissent puis reviennent. */
    plafond: {
      duree: 240000, ips: 4, poids: 2,
      etat(u) { return { f: Math.sin(u * Math.PI) }; },
      uniformes(v) { return { uNuages: [1500, 0.50 + 0.14 * v.f], uSoleilFacteur: SOLEIL_VOILE + 0.15 * v.f }; },
    },
    oiseaux: {
      duree: 14000, ips: 15, poids: 2,
      etat(u) {
        const x0 = VENT * (-190 + u * 400), z = -215, y0 = 4 - u * 5;
        const p = [];
        for (let i = 0; i < 9; i++) p.push([x0 + i * 2.3 * VENT, y0 + Math.sin(u * 40 + i) * 0.6, z + i * 1.1 - 5]);
        return { pos: p, x: x0 };
      },
      dessiner(ctx, v) {
        for (const p of v.pos) ctx.boite({ pos: p, ech: [1.2, 0.22, 0.32], col: ctx.palette.objet, alpha: 0.75, materiau: 3 });
      },
    },
  };

  let enCours = null, minuterie = 0, tic = 0, rafEnVol = 0;
  let graineEvenements = 0, dernierType = null, images = 0;
  let derive = 0, dernierTemps = 0;          // la dérive des nuages n'avance que pendant un événement
  const tirage = () => ((graineEvenements = (graineEvenements * 1103515245 + 12345) >>> 0) / 4294967296);

  function tirerType() {
    const noms = Object.keys(TYPES).filter((k) => k !== dernierType);
    const total = noms.reduce((n, k) => n + TYPES[k].poids, 0);
    let r = tirage() * total;
    for (const k of noms) { r -= TYPES[k].poids; if (r <= 0) return k; }
    return noms[0];
  }
  const mouvementReduit = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let fenetreVisible = true;
  const animationsPermises = () => ouverte && !mouvementReduit() && !document.hidden && fenetreVisible;

  function planifier(delai) {
    clearTimeout(minuterie); minuterie = 0;
    if (!animationsPermises()) return;
    minuterie = setTimeout(() => lancer(), delai ?? ATTENTE_MIN + tirage() * (ATTENTE_MAX - ATTENTE_MIN));
  }
  function lancer(nom) {
    if (!animationsPermises() || enCours) return;
    nom = nom || tirerType();
    dernierType = nom;
    enCours = { nom, type: TYPES[nom], debut: performance.now() };
    dernierTemps = enCours.debut;
    cadencer();
  }
  function cadencer() {
    clearTimeout(tic); tic = 0;
    if (!enCours) return;
    tic = setTimeout(() => { poserImage(); cadencer(); }, 1000 / enCours.type.ips);
  }
  function poserImage() {
    if (rafEnVol) return;
    rafEnVol = requestAnimationFrame((now) => { rafEnVol = 0; avancer(now); });
  }
  function etatEvenement(now) {
    if (!enCours) return null;
    const u = Math.min(1, (now - enCours.debut) / enCours.type.duree);
    return { nom: enCours.nom, v: enCours.type.etat(u) };
  }
  function avancer(now) {
    if (!enCours || !gl || tourEnCours) return;
    const u = (now - enCours.debut) / enCours.type.duree;
    if (u >= 1) return finir();
    derive += (now - dernierTemps) / 1000 * 3.0;        // 3 m/s à 1 500 m : les cumulus dérivent
    dernierTemps = now;
    const v = enCours.type.etat(u);
    if (enCours.type.visible && !enCours.type.visible(v)) return;
    dessiner({ nom: enCours.nom, v });
  }
  function finir() {
    const e = enCours;
    enCours = null;
    clearTimeout(tic); tic = 0;
    if (e.type.fin) e.type.fin(e.type.etat(1));
    dessiner(null);
    planifier();
  }
  function arreterLesEvenements() {
    clearTimeout(minuterie); minuterie = 0;
    clearTimeout(tic); tic = 0;
    if (rafEnVol) cancelAnimationFrame(rafEnVol);
    rafEnVol = 0;
    const e = enCours;
    enCours = null;
    if (e && e.type.fin) e.type.fin(e.type.etat(Math.min(1, (performance.now() - e.debut) / e.type.duree)));
  }
  function surVisibilite() {
    if (!ouverte) return;
    if (mouvementReduit()) return fermer();
    if (animationsPermises()) {
      if (!enCours && !minuterie) planifier();
    } else if (enCours || minuterie) {
      arreterLesEvenements();
      if (gl) dessiner(null);
    }
  }
  window.fenetreVisible = (visible) => { fenetreVisible = !!visible; surVisibilite(); };

  /* ============================================================
     8. RENDU
     ============================================================ */
  let hote = null, cv = null, gl = null;
  let scene3d = null, cameraCSS = null, note = null, zoneMsg = null, live = null, points = null;
  let P = {};
  let vaoVille = null, vaoOmbre = null, vaoPiece = null, vaoVide = null;
  let carteLoin = null, carteProche = null, luzLoin = null, luzProche = null, ombreValide = false;
  let palette = null, camera = null, soleil = null;
  let lacet = 0, courant = 2, tourEnCours = false;
  let ouverte = false, contextePerdu = false, fantome = false;
  const panneaux = [];        // { def, el, contenu, corps, coins, taille }
  let support = null;
  const supporte = () => {
    if (support === null) {
      try { support = !!document.createElement('canvas').getContext('webgl2'); }
      catch { support = false; }
    }
    return support;
  };

  function creerProgramme(nom, vs, fs) {
    const c = (t, src) => {
      const o = gl.createShader(t);
      gl.shaderSource(o, src); gl.compileShader(o);
      if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) throw new Error(nom + ' : ' + gl.getShaderInfoLog(o));
      return o;
    };
    const prog = gl.createProgram();
    gl.attachShader(prog, c(gl.VERTEX_SHADER, vs));
    gl.attachShader(prog, c(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(nom + ' : ' + gl.getProgramInfoLog(prog));
    const u = {}, a = {};
    for (let i = 0; i < gl.getProgramParameter(prog, gl.ACTIVE_UNIFORMS); i++) {
      const info = gl.getActiveUniform(prog, i);
      u[info.name.replace(/\[0\]$/, '')] = gl.getUniformLocation(prog, info.name);
    }
    for (let i = 0; i < gl.getProgramParameter(prog, gl.ACTIVE_ATTRIBUTES); i++) {
      const info = gl.getActiveAttrib(prog, i);
      a[info.name] = gl.getAttribLocation(prog, info.name);
    }
    return { prog, u, a };
  }
  function poser(prog, valeurs) {
    for (const nom in valeurs) {
      const loc = prog.u[nom];
      if (!loc) continue;
      const v = valeurs[nom];
      if (typeof v === 'number') gl.uniform1f(loc, v);
      else if (v.length === 2) gl.uniform2fv(loc, v);
      else if (v.length === 3) gl.uniform3fv(loc, v);
      else if (v.length === 4) gl.uniform4fv(loc, v);
      else if (v.length === 16) gl.uniformMatrix4fv(loc, false, v);
    }
  }

  /* Une carte d'ombre : profondeur seule, comparaison matérielle. Le
     D24 est stocké en 32 bits flottants sur Apple : 16 Mo par carte. */
  function carteOmbre(taille) {
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texStorage2D(gl.TEXTURE_2D, 1, gl.DEPTH_COMPONENT24, taille, taille);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_COMPARE_MODE, gl.COMPARE_REF_TO_TEXTURE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_COMPARE_FUNC, gl.LEQUAL);
    const fb = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.TEXTURE_2D, tex, 0);
    gl.drawBuffers([gl.NONE]); gl.readBuffer(gl.NONE);
    const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.bindTexture(gl.TEXTURE_2D, null);
    if (!ok) throw new Error('carte d’ombre incomplète');
    return { tex, fb, taille };
  }

  /* Le cadrage de la lumière : orthographique, ajusté sur le DISQUE de
     rayon R (96 points du cercle, du sol au sommet des tours) et non
     sur le carré — projeté dans le repère du soleil, le carré faisait
     un texel de 1,15 m, le disque 0,88 × 0,45. La carte proche garde la
     plage de profondeur de la grande : les tours lointaines projettent
     dedans. */
  function cadrerLumiere(dir, R, plage) {
    const centre = [IMMEUBLE.x, SCENE.solVille + 60, IMMEUBLE.z];
    const oeilL = centre.map((v, i) => v + dir[i] * 3000);
    const V = M.look(oeilL, centre, [0, 1, 0]);
    const mn = [1e9, 1e9, 1e9], mx = [-1e9, -1e9, -1e9];
    for (const y of [SCENE.solVille - 1, SCENE.solVille + 165]) {
      for (let k = 0; k < 96; k++) {
        const a = k / 96 * 2 * Math.PI;
        const q = M.xf(V, [IMMEUBLE.x + R * Math.cos(a), y, IMMEUBLE.z + R * Math.sin(a)]);
        for (let i = 0; i < 3; i++) { mn[i] = Math.min(mn[i], q[i]); mx[i] = Math.max(mx[i], q[i]); }
      }
    }
    let zn = -mx[2], zf = -mn[2];
    if (plage) { zn = plage.zn; zf = plage.zf; }
    const Pr = M.ortho(mn[0], mx[0], mn[1], mx[1], zn, zf);
    return { vp: M.mul(Pr, V), zn, zf, texel: (mx[0] - mn[0]) / 2048 };
  }

  function majSoleil() {
    const date = heureDeLaVue();
    const p = positionSoleil(date);
    const c = couleurSoleil(p.elevation, palette ? palette.papier : [1, 0.99, 0.976]);
    const ancien = soleil;
    soleil = { date, dir: p.dir, elevation: p.elevation, azimut: p.azimut, couleur: c.couleur, intensite: c.intensite,
               couleurRel: c.couleur.map((v, i) => v / (palette ? palette.papier[i] : 1)) };
    // La carte d'ombre n'est refaite que si le soleil a bougé de plus d'un demi-degré.
    if (!ancien || Math.acos(Math.min(1, ancien.dir[0] * p.dir[0] + ancien.dir[1] * p.dir[1] + ancien.dir[2] * p.dir[2])) > 0.5 * Math.PI / 180) {
      ombreValide = false;
    }
  }

  function passeOmbre() {
    luzLoin = cadrerLumiere(soleil.dir, 900);
    luzProche = cadrerLumiere(soleil.dir, 300, luzLoin);
    gl.useProgram(P.ombre.prog);
    gl.bindVertexArray(vaoOmbre);
    gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LESS); gl.depthMask(true);
    gl.disable(gl.BLEND);
    gl.colorMask(false, false, false, false);
    /* Facteur de pente 2, et rien d'autre : le second paramètre est sans
       effet sous Metal (mesuré), un biais constant fait décoller les
       ombres. Le décalage de normale complète (VS_VILLE). */
    gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(2.0, 0.0);
    for (const [carte, luz] of [[carteLoin, luzLoin], [carteProche, luzProche]]) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, carte.fb);
      gl.viewport(0, 0, carte.taille, carte.taille);
      gl.clear(gl.DEPTH_BUFFER_BIT);
      poser(P.ombre, { uVP: luz.vp });
      gl.drawArraysInstanced(gl.TRIANGLES, 0, 36, VILLE.N + 1);   // + l'immeuble de l'observateur
    }
    gl.disable(gl.POLYGON_OFFSET_FILL);
    gl.colorMask(true, true, true, true);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.bindVertexArray(null);
    ombreValide = true;
  }

  function batir() {
    gl = cv.getContext('webgl2', { antialias: true, alpha: false, powerPreference: 'low-power' });
    if (!gl) throw new Error('WebGL2 indisponible');
    P.ciel = creerProgramme('ciel', VS_CIEL, FS_CIEL);
    P.ville = creerProgramme('ville', VS_VILLE, FS_VILLE);
    P.ombre = creerProgramme('ombre', VS_OMBRE, FS_OMBRE);
    P.piece = creerProgramme('pièce', VS_PIECE, FS_PIECE);
    P.vignette = creerProgramme('vignette', VS_CIEL, FS_VIGNETTE);

    const bCube = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, bCube);
    gl.bufferData(gl.ARRAY_BUFFER, CUBE, gl.STATIC_DRAW);
    const bInst = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, bInst);
    gl.bufferData(gl.ARRAY_BUFFER, TAMPON_VILLE, gl.STATIC_DRAW);
    const pas = VILLE.STRIDE * 4;
    const attacherCube = (prog) => {
      gl.bindBuffer(gl.ARRAY_BUFFER, bCube);
      gl.enableVertexAttribArray(prog.a.aPos); gl.vertexAttribPointer(prog.a.aPos, 3, gl.FLOAT, false, 24, 0);
      if (prog.a.aNrm !== undefined && prog.a.aNrm >= 0) {
        gl.enableVertexAttribArray(prog.a.aNrm); gl.vertexAttribPointer(prog.a.aNrm, 3, gl.FLOAT, false, 24, 12);
      }
    };
    const attacherInstances = (prog) => {
      gl.bindBuffer(gl.ARRAY_BUFFER, bInst);
      [['iPos', 3, 0], ['iTaille', 3, 12], ['iCS', 2, 24], ['iExtra', 3, 32]].forEach(([nom, n, off]) => {
        const l = prog.a[nom];
        if (l === undefined || l < 0) return;
        gl.enableVertexAttribArray(l);
        gl.vertexAttribPointer(l, n, gl.FLOAT, false, pas, off);
        gl.vertexAttribDivisor(l, 1);
      });
    };
    vaoVille = gl.createVertexArray(); gl.bindVertexArray(vaoVille); attacherCube(P.ville); attacherInstances(P.ville);
    vaoOmbre = gl.createVertexArray(); gl.bindVertexArray(vaoOmbre); attacherCube(P.ombre); attacherInstances(P.ombre);
    vaoPiece = gl.createVertexArray(); gl.bindVertexArray(vaoPiece); attacherCube(P.piece);
    vaoVide = gl.createVertexArray();
    gl.bindVertexArray(null);

    carteLoin = carteOmbre(2048);
    carteProche = carteOmbre(2048);
    gl.useProgram(P.ville.prog);
    gl.uniform1i(P.ville.u.uOmbre, 0);
    gl.uniform1i(P.ville.u.uOmbreProche, 1);
    ombreValide = false;
  }

  function dimensionner() {
    const d = Math.min(Math.max(1, window.devicePixelRatio || 1), 2);
    const L = hote.clientWidth, H = hote.clientHeight;
    const l = Math.round(L * d), h = Math.round(H * d);
    if (cv.width !== l || cv.height !== h) {
      cv.width = l; cv.height = h;
      cv.style.width = (l / d) + 'px'; cv.style.height = (h / d) + 'px';
    }
    if (gl) gl.viewport(0, 0, cv.width, cv.height);
    return { L, H, d };
  }

  /* Place la couche CSS pour le lacet courant : la caméra, puis chaque
     panneau sur son arc. Appelé à l'ouverture, au redimensionnement et
     à chaque image du tour — dans le MÊME rappel que dessiner(). */
  function placerCouches() {
    const { L, H, d } = dimensionner();
    camera = calculerCamera(L, H, cv.width, cv.height, lacet);
    camera.detail = (2.6 * d * camera.focale / 400 >= 3) ? 1 : 0;
    scene3d.style.perspective = camera.focale + 'px';
    cameraCSS.style.transform =
      `translate(${L / 2}px,${H / 2}px) translateZ(${eps(camera.focale)}px)` + matriceCameraCSS(camera.vueCSS);
    placerPanneaux();
  }

  function positionPanneau(i, R) {
    const phi = angleDe(i);
    return { pos: [OEIL[0] + R * Math.sin(phi), 0.06, OEIL[2] - R * Math.cos(phi)], rotY: -phi, phi };
  }
  function placerPanneaux() {
    panneaux.forEach((p, i) => {
      const R = i === courant ? R_COURANT : R_AUTRE;
      const { pos, rotY, phi } = positionPanneau(i, R);
      const ech = p.def.largeurM * PX_PAR_METRE / p.def.largeurPx;
      p.el.style.transform = matriceObjetCSS(matriceMonde(pos.map((v) => v * PX_PAR_METRE), rotY, ech));
      // Derrière le plan de l'œil : rien à composer.
      p.el.classList.toggle('cache', Math.cos(phi - lacet) < 0.05);
      p.el.classList.toggle('courant', i === courant);
      p.el.classList.toggle('loin', i !== courant);
    });
  }

  function dessiner(evt) {
    if (!gl || !camera) return;
    try {
      if (!ombreValide) passeOmbre();
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, cv.width, cv.height);
      const C = palette, vp = camera.vp, S = soleil;
      const format = cv.width / cv.height;
      images++;

      const uni = { uBrumeK: SCENE.brumeK, uTrouee: [0, 0, 1, 0], uNappe: [0, 1, 0], uNuages: [1500, 0.50],
                    uSoleilFacteur: SOLEIL_VOILE, uOmbreNuage: 0 };
      if (evt && evt.nom && TYPES[evt.nom].uniformes) Object.assign(uni, TYPES[evt.nom].uniformes(evt.v));
      const angle = (evt && evt.nom === 'grue') ? evt.v.angle : angleGrue;

      gl.disable(gl.BLEND);
      gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LESS); gl.depthMask(true);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

      // La ville, avec ses deux cartes d'ombre.
      gl.useProgram(P.ville.prog);
      gl.bindVertexArray(vaoVille);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, carteLoin.tex);
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, carteProche.tex);
      poser(P.ville, { uVP: vp, uLuz: luzLoin.vp, uLuzProche: luzProche.vp, uSoleil: S.dir, uDecalN: 0.45,
                       uVille: C.ville, uCiel: C.cielBas, uBrume: C.brume, uSombre: C.sombre, uFroid: C.froid,
                       uTeinteCiel: C.teinteCiel, uCoulSoleil: S.couleur, uIntensite: S.intensite,
                       uSoleilFacteur: uni.uSoleilFacteur, uBrumeK: uni.uBrumeK, uTrouee: uni.uTrouee,
                       uNappe: uni.uNappe, uDetail: camera.detail });
      gl.drawArraysInstanced(gl.TRIANGLES, 0, 36, VILLE.N);

      // La pièce et tout ce qui se dessine boîte par boîte.
      gl.useProgram(P.piece.prog);
      gl.bindVertexArray(vaoPiece);
      poser(P.piece, { uVP: vp, uOeil: OEIL, uCiel: C.cielBas, uPlafond: C.plafond, uSolCol: C.sol,
                       uCadre: C.cadre, uNeutre: C.neutre, uBrume: C.brume, uBrumeK: uni.uBrumeK,
                       uSoleil: S.dir, uCoulSoleilRel: S.couleurRel, uIntensite: S.intensite, uSoleilFacteur: uni.uSoleilFacteur });
      const boite = ({ pos, ech, cs = [1, 0], col, alpha = 1, materiau = 0 }) => {
        poser(P.piece, { uP: pos, uS: ech, uCS: cs, uCol: col, uAlpha: alpha, uMateriau: materiau });
        gl.drawArrays(gl.TRIANGLES, 0, 36);
      };
      const ctx = { boite, palette: C };
      for (const o of PIECE) boite({ pos: o.pos, ech: o.ech, col: C[o.teinte], materiau: o.materiau });
      if (VILLE.grue) {
        const g = VILLE.grue, [tx, ty, tz] = g.toit;
        const c = Math.cos(angle), s = Math.sin(angle);
        boite({ pos: [tx, ty + g.mat / 2, tz], ech: [0.3, g.mat / 2, 0.3], col: C.objet, materiau: 3 });
        const haut = ty + g.mat;
        boite({ pos: [tx + c * (g.fleche / 2 - 3), haut, tz + s * (g.fleche / 2 - 3)], ech: [g.fleche / 2, 0.25, 0.25], cs: [c, s], col: C.objet, materiau: 3 });
        boite({ pos: [tx - c * 4, haut, tz - s * 4], ech: [4, 0.4, 0.4], cs: [c, s], col: C.objet, materiau: 3 });
        boite({ pos: [tx, haut + 1.1, tz], ech: [0.5, 1.1, 0.5], col: C.objet, materiau: 3 });
      }

      // Le ciel, en dernier, là où rien n'est posé.
      gl.depthFunc(gl.LEQUAL); gl.depthMask(false);
      gl.useProgram(P.ciel.prog);
      gl.bindVertexArray(vaoVide);
      poser(P.ciel, { uAvant: camera.avant, uDroite: camera.droite, uHaut: camera.haut, uTan: camera.tan,
                      uCielHaut: C.cielHaut, uCielBas: C.cielBas, uSoleil: S.dir, uCoulSoleil: S.couleur,
                      uIntensite: S.intensite * uni.uSoleilFacteur / SOLEIL_VOILE, uNuages: uni.uNuages,
                      uDerive: [derive * VENT, 0], uOmbreNuage: uni.uOmbreNuage });
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      gl.depthFunc(gl.LESS);

      // Le mélange : objets de l'événement, fantôme, verres, vignette.
      gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.useProgram(P.piece.prog);
      gl.bindVertexArray(vaoPiece);
      if (evt && evt.nom && TYPES[evt.nom].dessiner) TYPES[evt.nom].dessiner(ctx, evt.v);
      if (fantome) {
        panneaux.forEach((p, i) => {
          const { pos, rotY } = positionPanneau(i, i === courant ? R_COURANT : R_AUTRE);
          const hM = p.taille.h * p.def.largeurM / p.def.largeurPx;
          boite({ pos, ech: [p.def.largeurM / 2, hM / 2, 0.005], cs: [Math.cos(rotY), -Math.sin(rotY)],
                  col: C.sombre, alpha: 0.35, materiau: 0 });
        });
      }
      for (const v of VITRAGES) boite({ pos: v.pos, ech: v.ech, col: C.verre, alpha: 0.06, materiau: 2 });
      gl.disable(gl.DEPTH_TEST);
      gl.useProgram(P.vignette.prog);
      gl.bindVertexArray(vaoVide);
      poser(P.vignette, { uTeinte: C.texte, uFormat: format });
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      gl.depthMask(true);
      gl.bindVertexArray(null);
    } catch (e) {
      console.error('vue baie : rendu interrompu —', e && e.message);
      fermer('erreur de rendu');
    }
  }

  function surRedimension() {
    if (!ouverte || !gl) return;
    if (!tailleSuffisante()) return fermer('trop petite');
    panneaux.forEach((p) => p.el.classList.remove('glisse'));
    placerCouches();
    dessiner(etatEvenement(performance.now()));
  }

  /* ============================================================
     9. LES PANNEAUX — les sections de la page, dans la pièce
     ============================================================ */
  let ordreShell = null;      // les enfants de .shell, dans l'ordre, pour tout remettre en place

  function garnirExtrait(dans) {
    dans.textContent = '';
    dans.setAttribute('aria-hidden', 'true');
    const src = document.querySelector('#section-valeurs table');
    if (!src) return;
    const t = src.cloneNode(false);
    const thead = src.querySelector('thead');
    if (thead) t.appendChild(thead.cloneNode(true));
    const corps = document.createElement('tbody');
    [...src.querySelectorAll('#stocks-body tr')].slice(0, 6).forEach((tr) => corps.appendChild(tr.cloneNode(true)));
    t.appendChild(corps);
    const env = document.createElement('div');
    env.className = 'vue3d-table';
    env.appendChild(t);
    dans.appendChild(env);
    const pied = document.createElement('p');
    pied.className = 'vue3d-pied';
    pied.textContent = 'Seul critère validé : momentum 12-1 — → pour le tableau complet';
    dans.appendChild(pied);
  }

  function mesurerPanneau(p) {
    p.contenu.style.width = p.def.largeurPx + 'px';
    p.contenu.style.left = '0px'; p.contenu.style.top = '0px';
    p.taille = { l: p.contenu.offsetWidth, h: p.contenu.offsetHeight };
    p.contenu.style.left = -p.taille.l / 2 + 'px';
    p.contenu.style.top = -p.taille.h / 2 + 'px';
    const l2 = p.taille.l / 2, h2 = p.taille.h / 2;
    [[-l2, -h2], [l2, -h2], [-l2, h2], [l2, h2]].forEach(([x, y], k) => { p.coins[k].style.left = x + 'px'; p.coins[k].style.top = y + 'px'; });
  }

  function deplacerLesSections() {
    const shell = document.querySelector('.shell');
    ordreShell = [...shell.children];
    for (const p of panneaux) {
      for (const sel of p.def.sources) {
        const el = document.querySelector(sel);
        if (el) p.corps.appendChild(el);
      }
      if (p.def.extrait) garnirExtrait(p.extrait);
      mesurerPanneau(p);
    }
    // Le tableau : défilement piloté en JS (le défilement natif d'un
    // overflow dans cette chaîne 3D vaut 0 dans tous les états, mesuré).
    const tw = document.querySelector('#section-valeurs .table-wrap');
    if (tw) majJauge(tw);
  }
  function rendreLesSections() {
    if (!ordreShell) return;
    const shell = document.querySelector('.shell');
    for (const el of ordreShell) shell.appendChild(el);
    ordreShell = null;
    for (const p of panneaux) { if (p.def.extrait) p.extrait.textContent = ''; }
  }
  function majJauge(tw) {
    const p = panneaux[3];
    const max = tw.scrollHeight - tw.clientHeight;
    p.jauge.hidden = max <= 0;
    if (max > 0) {
      const h = Math.max(24, tw.clientHeight * tw.clientHeight / tw.scrollHeight);
      p.jauge.style.height = h + 'px';
      p.jauge.style.top = (tw.scrollTop / max) * (tw.clientHeight - h) + 'px';
    }
  }
  function defilerTableau(delta) {
    const tw = document.querySelector('#section-valeurs .table-wrap');
    if (!tw) return;
    tw.scrollTop = Math.max(0, Math.min(tw.scrollHeight - tw.clientHeight, tw.scrollTop + delta));
    majJauge(tw);
  }

  /* ============================================================
     10. ENTRÉE, SORTIE, NAVIGATION
     ============================================================ */
  function creerHote() {
    hote = document.createElement('div');
    hote.className = 'vue3d';
    hote.setAttribute('role', 'dialog');
    hote.setAttribute('aria-modal', 'true');
    hote.setAttribute('aria-label', 'Vue baie');
    hote.tabIndex = -1;
    hote.hidden = true;
    cv = document.createElement('canvas');
    hote.appendChild(cv);

    scene3d = document.createElement('div');
    scene3d.className = 'vue3d-scene3d';
    cameraCSS = document.createElement('div');
    cameraCSS.className = 'vue3d-camera';
    PANNEAUX.forEach((def, i) => {
      const el = document.createElement('section');
      el.className = 'vue3d-panneau';
      el.dataset.panneau = def.id;
      el.setAttribute('aria-label', def.nom);
      el.tabIndex = -1;
      const contenu = document.createElement('div');
      contenu.className = 'vue3d-contenu';
      const corps = document.createElement('div');
      corps.className = 'vue3d-corps';
      contenu.appendChild(corps);
      let extrait = null;
      if (def.extrait) { extrait = document.createElement('div'); extrait.className = 'vue3d-extrait'; contenu.appendChild(extrait); }
      // Les quatre coins-témoins, sur le POINT du panneau (pas sur le
      // contenu, dont la bordure décalerait la mesure d'un pixel).
      const coins = [0, 1, 2, 3].map(() => { const c = document.createElement('i'); c.className = 'vue3d-coin'; el.appendChild(c); return c; });
      let jauge = null;
      if (def.id === 'tableau') { jauge = document.createElement('div'); jauge.className = 'vue3d-jauge'; jauge.hidden = true; contenu.appendChild(jauge); }
      el.appendChild(contenu);
      cameraCSS.appendChild(el);
      panneaux.push({ def, el, contenu, corps, extrait, coins, jauge, taille: { l: 0, h: 0 } });
      // Défilement du tableau et clics : sur le panneau, jamais sur la scène.
      el.addEventListener('wheel', (e) => {
        const tw = e.target.closest && e.target.closest('.table-wrap');
        if (!tw) return;
        e.preventDefault();
        defilerTableau(e.deltaY);
      }, { passive: false });
    });
    scene3d.appendChild(cameraCSS);
    hote.appendChild(scene3d);

    // Les flèches, les points, la note, l'annonce.
    for (const [sens, txt, delta] of [['gauche', '←', -1], ['droite', '→', 1]]) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'vue3d-fleche vue3d-fleche-' + sens;
      b.setAttribute('aria-label', delta < 0 ? 'Panneau précédent' : 'Panneau suivant');
      b.setAttribute('aria-keyshortcuts', delta < 0 ? 'ArrowLeft' : 'ArrowRight');
      b.textContent = txt;
      b.addEventListener('click', () => aller(courant + delta));
      hote.appendChild(b);
    }
    points = document.createElement('div');
    points.className = 'vue3d-points';
    points.setAttribute('aria-hidden', 'true');
    PANNEAUX.forEach((def, i) => {
      const s = document.createElement('button');
      s.type = 'button'; s.tabIndex = -1; s.title = def.nom;
      s.addEventListener('click', () => aller(i));
      points.appendChild(s);
    });
    hote.appendChild(points);
    live = document.createElement('p');
    live.className = 'vue3d-live';
    live.setAttribute('aria-live', 'polite');
    hote.appendChild(live);
    note = document.createElement('p');
    note.className = 'vue3d-note';
    note.id = 'vue3d-note';
    note.innerHTML = 'Onzième étage — <kbd>←</kbd> <kbd>→</kbd> pour changer de panneau, <kbd>Échap</kbd> pour la vue plan';
    hote.setAttribute('aria-describedby', note.id);
    hote.appendChild(note);

    const surPerte = (e) => { e.preventDefault(); contextePerdu = true; fermer('contexte perdu'); };
    cv.addEventListener('webglcontextlost', surPerte);
    cv.addEventListener('webglcontextrestored', () => { contextePerdu = false; });

    // Glisser le fond : le lacet suit la souris, puis s'aimante.
    hote.addEventListener('pointerdown', surPointerDown);
    hote.addEventListener('focusin', surFocus);
    document.body.appendChild(hote);
  }

  function remplacerLeCanvas() {
    const neuf = document.createElement('canvas');
    cv.replaceWith(neuf);
    cv = neuf;
    gl = null; P = {}; vaoVille = vaoOmbre = vaoPiece = vaoVide = null; carteLoin = carteProche = null;
    cv.addEventListener('webglcontextlost', (e) => { e.preventDefault(); contextePerdu = true; fermer('contexte perdu'); });
    cv.addEventListener('webglcontextrestored', () => { contextePerdu = false; });
    contextePerdu = false;
  }

  /* La page derrière devient inerte ; les panneaux non courants aussi
     (clavier, souris ET lecteur d'écran : leur texte fait 5 à 7 px). */
  let focusAvant = null, defilementAvant = 0, inertes = [];
  function isolerLaPage() {
    focusAvant = document.activeElement;
    defilementAvant = window.scrollY;
    inertes = [...document.body.children].filter((el) => el !== hote && !el.inert);
    inertes.forEach((el) => { el.inert = true; });
  }
  function rendreLaPage() {
    inertes.forEach((el) => { el.inert = false; });
    inertes = [];
    const cible = document.getElementById('vue3d-bouton') || focusAvant;
    if (cible && cible.focus) cible.focus({ preventScroll: true });
    window.scrollTo(0, defilementAvant);
  }
  function majInert() {
    panneaux.forEach((p, i) => { p.el.inert = i !== courant; });
    [...points.children].forEach((s, i) => s.classList.toggle('actif', i === courant));
    hote.querySelector('.vue3d-fleche-gauche').disabled = courant === 0;
    hote.querySelector('.vue3d-fleche-droite').disabled = courant === PANNEAUX.length - 1;
  }
  function annoncer() {
    live.textContent = `Panneau ${courant + 1} sur ${PANNEAUX.length} : ${PANNEAUX[courant].nom}`;
  }
  const majBouton = () => {
    const b = document.getElementById('vue3d-bouton');
    if (b) b.innerHTML = ouverte ? 'Vue plan<kbd>Échap</kbd>' : 'Vue baie<kbd>V</kbd>';
  };

  const tailleSuffisante = () => window.innerWidth >= 1200 && window.innerHeight >= 690;
  const mqMouvement = window.matchMedia('(prefers-reduced-motion: reduce)');
  let ecouteDensite = null, minuterieSoleil = 0;

  function ouvrir(i = 2) {
    if (ouverte) return;
    if (!hote) creerHote();
    if (contextePerdu) return prevenir('carte graphique');
    if (!tailleSuffisante()) return prevenir('trop petite');
    try {
      if (!gl) batir();
    } catch (e) {
      console.warn('vue baie indisponible :', e.message);
      return prevenir('échec');
    }
    hote.hidden = false;
    document.body.classList.add('vue3d-active');
    ouverte = true;
    palette = lirePalette();
    majSoleil();
    courant = Math.max(0, Math.min(PANNEAUX.length - 1, i));
    lacet = angleDe(courant);

    deplacerLesSections();
    isolerLaPage();
    majInert();
    majBouton();
    placerCouches();
    passeOmbre();                // la compilation Metal se paie ici, pas à la première image visible
    dessiner(null);
    if (!ouverte) return;
    panneaux[courant].el.focus({ preventScroll: true });
    scene3d.scrollLeft = 0; scene3d.scrollTop = 0;
    requestAnimationFrame(() => { if (ouverte) hote.classList.add('est-la'); });
    annoncer();

    graineEvenements = VILLE_PARAMS.graine;
    dernierType = null;
    if (animationsPermises()) minuterie = setTimeout(() => lancer('eclaircie'), 5000);
    minuterieSoleil = setInterval(() => { if (ouverte) { majSoleil(); if (!ombreValide) dessiner(etatEvenement(performance.now())); } }, 5 * 60 * 1000);

    window.addEventListener('resize', surRedimension);
    document.addEventListener('visibilitychange', surVisibilite);
    mqMouvement.addEventListener('change', surVisibilite);
    ecouteDensite = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
    ecouteDensite.addEventListener('change', surRedimension);
    document.addEventListener('stocks:dessine', regarnir);
  }

  function regarnir() {
    if (!ouverte) return;
    const p = panneaux[2];
    if (p.extrait) garnirExtrait(p.extrait);
    panneaux.forEach(mesurerPanneau);
    const tw = document.querySelector('#section-valeurs .table-wrap');
    if (tw) majJauge(tw);
    // innerHTML a pu tuer le focus : on le rend au panneau courant.
    if (document.activeElement === document.body) panneaux[courant].el.focus({ preventScroll: true });
  }

  function prevenir(raison) {
    const messages = {
      'contexte perdu': 'La vue baie s’est fermée : le système a repris la carte graphique. Rouvre-la si tu veux.',
      'carte graphique': 'La carte graphique redémarre — réessaie dans un instant.',
      'erreur de rendu': 'La vue baie n’a pas pu s’afficher — retour au tableau. Le détail est dans la console.',
      'échec': 'La vue baie n’est pas disponible sur cet affichage.',
      'trop petite': 'La fenêtre est trop petite pour la vue baie (1200 × 690 au moins) : vue plan.',
    };
    const texte = messages[raison];
    if (!texte) return;
    console.warn('vue baie :', texte);
    if (!zoneMsg) return;
    zoneMsg.textContent = texte;
    zoneMsg.hidden = false;
    clearTimeout(zoneMsg._t);
    zoneMsg._t = setTimeout(() => { zoneMsg.hidden = true; }, 9000);
  }

  function fermer(raison) {
    if (!ouverte) return;
    window.removeEventListener('resize', surRedimension);
    document.removeEventListener('visibilitychange', surVisibilite);
    mqMouvement.removeEventListener('change', surVisibilite);
    if (ecouteDensite) ecouteDensite.removeEventListener('change', surRedimension);
    document.removeEventListener('stocks:dessine', regarnir);
    clearInterval(minuterieSoleil);
    arreterLesEvenements();
    tourEnCours = false;
    ouverte = false;
    rendreLesSections();
    rendreLaPage();
    majBouton();
    document.body.classList.remove('vue3d-active');
    const finir = () => {
      hote.hidden = true;
      if (raison === 'contexte perdu') remplacerLeCanvas();
      else if (cv) { cv.width = 1; cv.height = 1; }
    };
    hote.classList.remove('est-la');
    if (raison || mouvementReduit()) finir();
    else {
      let fait = false;
      const une = () => { if (!fait) { fait = true; finir(); } };
      hote.addEventListener('transitionend', une, { once: true });
      setTimeout(une, 320);
    }
    if (raison) prevenir(raison);
    document.dispatchEvent(new CustomEvent('vue3d:fermee'));
  }

  /* ── Le tour : la caméra tourne vers le panneau i ─────────────
     800 ms, à 30 images par seconde (une image sur deux du rAF), et à
     CHAQUE image placerCouches puis dessiner — dans le même rappel,
     sinon une image de retard vaut 20 px de glissement entre le panneau
     et le décor. Le panneau qui part recule et s'efface vite ; celui
     qui arrive avance et s'allume tard : les deux se croisent au
     bissecteur pendant ~60 ms, l'opacité le cache. */
  function aller(i, immediat = false) {
    i = Math.max(0, Math.min(PANNEAUX.length - 1, i));
    if (!ouverte || i === courant || tourEnCours) return;
    const sortant = courant;
    courant = i;
    majInert();
    annoncer();
    const de = lacet, vers = angleDe(i);
    if (immediat || mouvementReduit()) {
      lacet = vers;
      placerCouches();
      dessiner(etatEvenement(performance.now()));
      panneaux[courant].el.focus({ preventScroll: true });
      return;
    }
    tourEnCours = true;
    panneaux[sortant].el.classList.add('glisse', 'sortant');
    panneaux[courant].el.classList.add('glisse', 'entrant');
    const debut = performance.now(), duree = 800;
    let n = 0;
    const pas = (now) => {
      const u = Math.min(1, (now - debut) / duree);
      if (n++ % 2 === 0 || u >= 1) {
        lacet = de + (vers - de) * douce(u);
        placerCouches();
        dessiner(etatEvenement(now));
      }
      if (u < 1 && ouverte) requestAnimationFrame(pas);
      else {
        tourEnCours = false;
        setTimeout(() => panneaux.forEach((p) => p.el.classList.remove('glisse', 'sortant', 'entrant')), 750);
        if (ouverte) {
          panneaux[courant].el.focus({ preventScroll: true });
          scene3d.scrollLeft = 0; scene3d.scrollTop = 0;
        }
      }
    };
    requestAnimationFrame(pas);
  }

  /* Glisser le fond : le lacet suit la souris (toute la largeur = tout
     le champ), puis s'aimante au panneau le plus proche. Seuil de 8 px
     avant de considérer un glisser : en deçà, c'est un clic. */
  function surPointerDown(e) {
    if (!ouverte || tourEnCours || e.button !== 0) return;
    if (e.target !== cv) return;
    const x0 = e.clientX, lacet0 = lacet, L = hote.clientWidth;
    const champ = 2 * Math.atan(SCENE.champH);
    let glisse = false, raf = 0;
    const move = (ev) => {
      const dx = ev.clientX - x0;
      if (!glisse && Math.abs(dx) < 8) return;
      if (!glisse) { glisse = true; hote.classList.add('glisser'); }
      lacet = Math.max(angleDe(0) - 0.2, Math.min(angleDe(PANNEAUX.length - 1) + 0.2, lacet0 - dx / L * champ));
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; placerCouches(); dessiner(etatEvenement(performance.now())); });
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      hote.classList.remove('glisser');
      if (!glisse) return;
      const proche = Math.max(0, Math.min(PANNEAUX.length - 1, Math.round(lacet / PAS_ARC) + 2));
      if (proche === courant) { courant = -1; aller(proche); }   // revient au courant depuis un lacet libre
      else aller(proche);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
  }

  function surFocus(e) {
    if (!ouverte) return;
    const p = e.target.closest && e.target.closest('.vue3d-panneau');
    if (p) {
      const i = panneaux.findIndex((q) => q.el === p);
      if (i >= 0 && i !== courant) aller(i);
    }
    scene3d.scrollLeft = 0; scene3d.scrollTop = 0;
  }

  /* ============================================================
     11. COMMANDES
     ============================================================ */
  const enSaisie = () => {
    const a = document.activeElement;
    return !!(a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName));
  };
  const FOCALISABLES = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  document.addEventListener('keydown', (e) => {
    if (!ouverte) {
      if ((e.key === 'v' || e.key === 'V') && !e.metaKey && !e.ctrlKey && !e.altKey && !enSaisie() && supporte()) {
        e.preventDefault(); ouvrir();
      }
      return;
    }
    if (e.key === 'Escape') {
      // Un <select> ouvert se ferme d'abord : on le lui laisse.
      if (document.activeElement && document.activeElement.tagName === 'SELECT') return;
      e.preventDefault(); return fermer();
    }
    if (e.key === 'Tab') {
      e.preventDefault();
      const liste = [...panneaux[courant].el.querySelectorAll(FOCALISABLES)].filter((el) => el.offsetParent !== null);
      if (!liste.length) return panneaux[courant].el.focus({ preventScroll: true });
      const k = liste.indexOf(document.activeElement);
      const suivant = liste[(k + (e.shiftKey ? -1 : 1) + liste.length) % liste.length];
      suivant.focus({ preventScroll: true });
      return;
    }
    if (enSaisie()) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); aller(courant - 1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); aller(courant + 1); }
    else if (e.key === 'Home') { e.preventDefault(); aller(0); }
    else if (e.key === 'End') { e.preventDefault(); aller(PANNEAUX.length - 1); }
    else if (/^[1-5]$/.test(e.key)) { e.preventDefault(); aller(+e.key - 1); }
    else if (e.key === 'v' || e.key === 'V') { if (!e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); fermer(); } }
    else if (courant === 3 && (e.key === 'PageDown' || e.key === ' ')) { e.preventDefault(); defilerTableau(+0.9 * 560); }
    else if (courant === 3 && e.key === 'PageUp') { e.preventDefault(); defilerTableau(-0.9 * 560); }
  });

  function armerLeBouton() {
    const b = document.getElementById('vue3d-bouton');
    if (!b || !supporte()) return;
    b.hidden = false;
    b.addEventListener('click', () => (ouverte ? fermer() : ouvrir()));
    zoneMsg = document.createElement('span');
    zoneMsg.className = 'vue3d-msg';
    zoneMsg.hidden = true;
    b.parentNode.insertBefore(zoneMsg, b.nextSibling);
    /* La baie est le site : on l'ouvre au chargement, sauf si la
       machine ne sait pas, si la fenêtre est trop petite, si le
       système demande moins d'animation, ou si l'URL dit ?plan. */
    const params = new URLSearchParams(location.search);
    if (!params.has('plan') && !mouvementReduit() && tailleSuffisante()) ouvrir();
  }

  if (new URLSearchParams(location.search).has('debug')) {
    window.__vueBaie = {
      ouvrir, fermer, lancer, aller,
      ville: VILLE, types: Object.keys(TYPES),
      get images() { return images; },
      get etat() {
        return { ouverte, courant, lacet, enCours: enCours && enCours.nom, images, canvas: cv && [cv.width, cv.height],
                 focale: camera && camera.focale, fov: camera && camera.fov,
                 soleil: soleil && { elevation: soleil.elevation * 180 / Math.PI, azimut: soleil.azimut * 180 / Math.PI, heure: soleil.date.toString() },
                 texel: luzLoin && luzLoin.texel };
      },
      forcer(nom, u = 0.5) {
        if (!ouverte) return false;
        arreterLesEvenements();
        const v = TYPES[nom].etat(u);
        dessiner({ nom, v });
        return v;
      },
      lacet(psi) { if (!ouverte) return; lacet = psi; placerCouches(); dessiner(null); },
      soleil(heureISO) {
        // Force une heure (ISO avec décalage) : « à quoi ressemble 19 h ? »
        const date = heureDeLaVue(new Date(heureISO));
        const p = positionSoleil(date);
        const c = couleurSoleil(p.elevation, palette.papier);
        soleil = { date, dir: p.dir, elevation: p.elevation, azimut: p.azimut, couleur: c.couleur, intensite: c.intensite,
                   couleurRel: c.couleur.map((v, i) => v / palette.papier[i]) };
        ombreValide = false;
        if (ouverte) dessiner(null);
        return { elevation: p.elevation * 180 / Math.PI, azimut: p.azimut * 180 / Math.PI };
      },
      fantome(on) { fantome = !!on; if (ouverte) dessiner(null); },
      reprendre() { planifier(1000); },
      /* L'écart CSS/GL, par panneau et par coin. */
      ecart() {
        if (!ouverte || !camera) return null;
        const res = {};
        let max = 0;
        panneaux.forEach((p, i) => {
          const { pos, rotY, phi } = positionPanneau(i, i === courant ? R_COURANT : R_AUTRE);
          if (Math.cos(phi - lacet) < 0.3) return;
          const hM = p.taille.h * p.def.largeurM / p.def.largeurPx;
          const ec = [];
          [[-1, -1], [1, -1], [-1, 1], [1, 1]].forEach(([sx, sy], k) => {
            const lx = sx * p.def.largeurM / 2, ly = -sy * hM / 2;
            const monde = [pos[0] + lx * Math.cos(rotY), pos[1] + ly, pos[2] - lx * Math.sin(rotY)];
            const g = M.projeter(camera.vp, monde, camera.L, camera.H);
            const r = p.coins[k].getBoundingClientRect();
            ec.push(Math.hypot(g[0] - r.left, g[1] - r.top));
          });
          res[p.def.id] = ec.map((v) => +v.toFixed(3));
          max = Math.max(max, ...ec);
        });
        return { max: +max.toFixed(3), coins: res };
      },
    };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', armerLeBouton);
  else armerLeBouton();
})();

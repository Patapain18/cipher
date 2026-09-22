/*
  Banc du générateur de ville — sans navigateur.

      node web/outils/test_ville.js

  Vérifie ce qu'une capture ne montre pas : qu'aucune boîte n'en
  traverse une autre, combien d'immeubles tombent vraiment dans le
  champ, que la fenêtre du reflet et la grue existent, et que la ligne
  de toits n'est pas une règle posée à l'horizontale.
*/
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'vue3d_ville.js'), 'utf8');
new Function(src)();                        // définit globalThis.VueBaieVille
const V = globalThis.VueBaieVille;
const ville = V.generer();
const { buf, N, STRIDE } = ville;
const inst = (i) => ({
  x: buf[i * STRIDE], y: buf[i * STRIDE + 1], z: buf[i * STRIDE + 2],
  hx: buf[i * STRIDE + 3], hy: buf[i * STRIDE + 4], hz: buf[i * STRIDE + 5],
  c: buf[i * STRIDE + 6], s: buf[i * STRIDE + 7],
  teinte: buf[i * STRIDE + 8], genre: buf[i * STRIDE + 9], motif: buf[i * STRIDE + 10],
});
const tous = Array.from({ length: N }, (_, i) => inst(i));
const parGenre = {};
tous.forEach((b) => { parGenre[b.genre] = (parGenre[b.genre] || 0) + 1; });
console.log('instances :', N, ' par genre :', JSON.stringify(parGenre), ' octets :', buf.byteLength);

// ── Chevauchements en plan (rectangles orientés, test des axes séparateurs)
function coins(b) {
  const pts = [];
  for (const [sx, sz] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
    const lx = sx * b.hx, lz = sz * b.hz;
    pts.push([b.x + lx * b.c - lz * b.s, b.z + lx * b.s + lz * b.c]);
  }
  return pts;
}
function axes(b) { return [[b.c, b.s], [-b.s, b.c]]; }
function separe(A, B) {
  const ca = coins(A), cb = coins(B);
  for (const [ax, az] of [...axes(A), ...axes(B)]) {
    const pa = ca.map(([x, z]) => x * ax + z * az), pb = cb.map(([x, z]) => x * ax + z * az);
    if (Math.max(...pa) <= Math.min(...pb) + 1e-6 || Math.max(...pb) <= Math.min(...pa) + 1e-6) return true;
  }
  return false;
}
const batis = tous.filter((b) => b.genre === 0 || b.genre === 3);
let chevauchements = 0, exemples = [];
// Tri par x pour ne comparer que les voisins possibles.
const tri = batis.slice().sort((a, b) => a.x - b.x);
for (let i = 0; i < tri.length; i++) {
  for (let j = i + 1; j < tri.length; j++) {
    const A = tri[i], B = tri[j];
    if (B.x - A.x > 80) break;
    if (Math.abs(A.z - B.z) > 80) continue;
    // Deux boîtes du même pied (retrait) se superposent en plan : c'est voulu.
    if (Math.abs(A.x - B.x) < 0.01 && Math.abs(A.z - B.z) < 0.01) continue;
    // Une boîte POSÉE sur une autre (couronne, retrait) n'en traverse aucune :
    // on n'accuse que les paires dont les hauteurs se recouvrent.
    if (A.y - A.hy >= B.y + B.hy - 1e-6 || B.y - B.hy >= A.y + A.hy - 1e-6) continue;
    if (!separe(A, B)) { chevauchements++; if (exemples.length < 3) exemples.push([A, B]); }
  }
}
console.log('chevauchements de bâtis :', chevauchements);
exemples.forEach(([A, B]) => console.log('  ex :', A, '\n       ', B));

// ── Ce que voit la caméra (panneau |x| < 0,417·d, champ ±0,79·d à 1440×861)
let visibles = 0, masques = 0, hors = 0;
for (const b of batis) {
  const d = -b.z, p = Math.abs(b.x) / d;
  if (p < 0.417) masques++; else if (p > 0.79) hors++; else visibles++;
}
console.log('bâtis visibles :', visibles, ' derrière le panneau :', masques, ' hors champ :', hors);

// ── Ligne de toits dans les bandes visibles : élévation max par tranche d'azimut
const bins = new Map();
for (const b of batis) {
  const d = -b.z, p = Math.abs(b.x) / d;
  if (p < 0.44 || p > 0.79) continue;
  const az = Math.round(Math.atan2(b.x, d) * 180 / Math.PI);      // degrés
  const haut = b.y + b.hy;                                        // sommet, monde
  const elev = Math.atan2(haut, d) * 180 / Math.PI;               // au-dessus de l'horizon
  bins.set(az, Math.max(bins.get(az) ?? -99, elev));
}
const elevs = [...bins.entries()].sort((a, b) => a[0] - b[0]);
const vals = elevs.map(([, e]) => e);
const moy = vals.reduce((a, b) => a + b, 0) / vals.length;
const ecart = Math.sqrt(vals.reduce((a, b) => a + (b - moy) ** 2, 0) / vals.length);
console.log('ligne de toits (élévation max par degré d\'azimut) : min', Math.min(...vals).toFixed(1),
            ' max', Math.max(...vals).toFixed(1), ' écart-type', ecart.toFixed(2), '°');
console.log('  au-dessus de l\'horizon :', vals.filter((v) => v > 0).length, '/', vals.length, 'tranches');

// ── Profondeur et lointain
const ds = batis.map((b) => -b.z);
console.log('profondeur : de', Math.min(...ds).toFixed(0), 'à', Math.max(...ds).toFixed(0), 'm ;',
            'au-delà de 1100 m :', ds.filter((d) => d > 1100).length);
const hauts = batis.map((b) => b.y + b.hy - V.PARAMS.solY).sort((a, b) => b - a);
console.log('hauteurs : max', hauts[0].toFixed(0), ' p95', hauts[Math.floor(hauts.length * 0.05)].toFixed(0),
            ' médiane', hauts[Math.floor(hauts.length * 0.5)].toFixed(0), 'm');
console.log('au-dessus de l\'œil (> 34,6 m) :', (100 * hauts.filter((h) => h > 34.6).length / hauts.length).toFixed(1), '%');

// ── Fenêtre du reflet et grue
console.log('fenêtre :', ville.fenetre && JSON.stringify({ pos: ville.fenetre.pos.map((v) => +v.toFixed(1)), d: +ville.fenetre.d.toFixed(0), pente: +(Math.abs(ville.fenetre.pos[0]) / ville.fenetre.d).toFixed(3) }));
console.log('grue    :', ville.grue && JSON.stringify({ toit: ville.grue.toit.map((v) => +v.toFixed(1)), d: +ville.grue.d.toFixed(0), pente: +(Math.abs(ville.grue.toit[0]) / ville.grue.d).toFixed(3) }));

// ── Repères dans la bande visible ?
for (const r of V.REPERES) {
  const d = -r.z, p = Math.abs(r.x) / d;
  console.log('repère', r.x, r.z, ' pente', p.toFixed(3), p > 0.417 && p < 0.79 ? 'visible' : (p < 0.417 ? 'MASQUÉ' : 'HORS CHAMP'));
}
const ok = chevauchements === 0 && ville.fenetre && ville.grue && visibles > 300;
console.log(ok ? '\n✔ banc OK' : '\n✘ banc KO');
process.exit(ok ? 0 : 1);

/*
  Exporte la ville 3D (les boîtes des immeubles et des repères) en JSON,
  pour que web/faire_baie.py projette LA MÊME ville dans le bandeau de
  la page : une seule source de vérité, la 3D.

      node web/outils/exporter_ville.js > /tmp/ville.json
*/
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'vue3d_ville.js'), 'utf8');
new Function(src)();
const V = globalThis.VueBaieVille;
const v = V.generer();
const boites = [];
for (let i = 0; i < v.N; i++) {
  const o = i * v.STRIDE;
  const genre = v.buf[o + 9];
  if (genre !== 0 && genre !== 3) continue;          // ni détails de toit, ni sol
  boites.push([...v.buf.slice(o, o + 8), genre].map((x) => +x.toFixed(2)));
}
process.stdout.write(JSON.stringify({ params: V.PARAMS, boites }));

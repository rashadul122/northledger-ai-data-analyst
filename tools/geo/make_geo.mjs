// Build engine/geo_us.json (metro and state points, projected) and the state outlines for the map drawer.
import { geoAlbersUsa, geoPath, geoCentroid } from 'd3-geo';
import { feature, mesh } from 'topojson-client';
import fs from 'fs';
const [gaz, albers, geo, outDir] = process.argv.slice(2);
const proj = geoAlbersUsa().scale(1300).translate([487.5, 305]);
const r1 = (v) => Math.round(v * 10) / 10;
// metro and micro areas: code -> [name, x, y]
const lines = fs.readFileSync(gaz, 'utf8').split(/\r?\n/).filter(Boolean);
const head = lines[0].split('\t').map((s) => s.trim());
const iG = head.indexOf('GEOID'), iN = head.indexOf('NAME'), iT = head.indexOf('CBSA_TYPE'), iLa = head.indexOf('INTPTLAT'), iLo = head.indexOf('INTPTLONG');
const cbsa = {};
let skipped = 0;
for (const l of lines.slice(1)) {
  const c = l.split('\t').map((s) => s.trim());
  const p = proj([+c[iLo], +c[iLa]]);
  if (!p) { skipped++; continue; }
  cbsa[c[iG]] = [c[iN].replace(/ (Metro|Micro) Area$/, ''), r1(p[0]), r1(p[1]), +c[iT]];
}
// states: fips -> [name, abbr, x, y] (the centroid of the geographic outline, projected)
const ABBR = {"01":"AL","02":"AK","04":"AZ","05":"AR","06":"CA","08":"CO","09":"CT","10":"DE","11":"DC","12":"FL","13":"GA","15":"HI","16":"ID","17":"IL","18":"IN","19":"IA","20":"KS","21":"KY","22":"LA","23":"ME","24":"MD","25":"MA","26":"MI","27":"MN","28":"MS","29":"MO","30":"MT","31":"NE","32":"NV","33":"NH","34":"NJ","35":"NM","36":"NY","37":"NC","38":"ND","39":"OH","40":"OK","41":"OR","42":"PA","44":"RI","45":"SC","46":"SD","47":"TN","48":"TX","49":"UT","50":"VT","51":"VA","53":"WA","54":"WV","55":"WI","56":"WY","72":"PR"};
const g = JSON.parse(fs.readFileSync(geo, 'utf8'));
const states = {};
for (const f of feature(g, g.objects.states).features) {
  const p = proj(geoCentroid(f));
  if (!p) continue;
  states[f.id] = [f.properties.name, ABBR[f.id] || '', r1(p[0]), r1(p[1])];
}
fs.writeFileSync(outDir + '/geo_us.json', JSON.stringify({ source: 'US Census Bureau 2024 Gazetteer (CBSA internal points); us-atlas 3.0.1 states; d3 geoAlbersUsa scale 1300 translate [487.5,305], viewBox 0 0 975 610', cbsa, states }));
// outlines: the pre-projected albers topology, each state a path, simplified (points closer than 1.6 px dropped, integers)
const a = JSON.parse(fs.readFileSync(albers, 'utf8'));
const path = geoPath();
const thin = (d) => d.replace(/(\d+)\.\d+/g, '$1');
const out = [];
for (const f of feature(a, a.objects.states).features) {
  // simplify rings: keep a point when it is at least 1.6 px from the last kept one
  const simp = (ring) => { const o = []; for (const p of ring) { const l = o[o.length - 1]; if (!l || Math.hypot(p[0] - l[0], p[1] - l[1]) >= 1.6) o.push(p); } return o.length >= 3 ? o : null; };
  const geom = f.geometry, polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
  const d = polys.map((poly) => poly.map(simp).filter(Boolean).map((r) => 'M' + r.map((p) => Math.round(p[0]) + ',' + Math.round(p[1])).join('L') + 'Z').join('')).join('');
  out.push([f.id, d]);
}
fs.writeFileSync(outDir + '/us_states_paths.json', JSON.stringify(out));
console.log('cbsa', Object.keys(cbsa).length, 'skipped', skipped, 'states', Object.keys(states).length, 'paths bytes', JSON.stringify(out).length);

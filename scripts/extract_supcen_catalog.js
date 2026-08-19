#!/usr/bin/env node
/**
 * One-time extractor: COMSUPCEN's single 13.9 MB catalog.js -> assets this app can serve.
 *
 *   backend/data/supcen_catalog.json   item rows (~1.3 MB), seeded into SQLite on boot
 *   frontend/static/catalog/<sha>.jpg  2,200 deduplicated thumbnails, cached by the browser
 *
 * The source ships images as base64 inside the JS because it has to run from
 * file://. We have a server, so they become real files and the first paint stops
 * costing 14 MB.
 *
 *   node scripts/extract_supcen_catalog.js /path/to/COMSUPCEN/data/catalog.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');

const src = process.argv[2];
if (!src || !fs.existsSync(src)) {
  console.error('usage: node scripts/extract_supcen_catalog.js <COMSUPCEN/data/catalog.js>');
  process.exit(1);
}

const root = path.resolve(__dirname, '..');
const imgDir = path.join(root, 'frontend/static/catalog');
const outJson = path.join(root, 'backend/data/supcen_catalog.json');
fs.mkdirSync(imgDir, { recursive: true });
fs.mkdirSync(path.dirname(outJson), { recursive: true });

const ctx = { window: {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(src, 'utf8'), ctx);
const CAT = ctx.window.CATALOG;

// ik -> filename, written once even though 2,475 items share 2,200 images
const imgMap = new Map();
let written = 0, skipped = 0;
for (const [key, dataUri] of Object.entries(CAT.images || {})) {
  const m = /^data:image\/(\w+);base64,(.*)$/.exec(String(dataUri));
  if (!m) { skipped++; continue; }
  const ext = m[1] === 'jpeg' ? 'jpg' : m[1];
  const buf = Buffer.from(m[2], 'base64');
  const name = crypto.createHash('sha1').update(buf).digest('hex').slice(0, 16) + '.' + ext;
  const dest = path.join(imgDir, name);
  if (!fs.existsSync(dest)) { fs.writeFileSync(dest, buf); written++; }
  imgMap.set(key, name);
}

const items = CAT.items.map((it, idx) => ({
  id: idx + 1,
  nom: it.nom || '',
  stock_number: it['Stock Number'] || '',
  nsn: it.NSN || '',
  mcn: it.MCN || '',
  lin: it['LIN/NSLIN'] || '',
  aesip: it.AESIP || '',
  unit_price: parseFloat(it['Unit Price']) || null,
  unit_issue: it['Unit Issue'] || '',
  unit_amt: it['Unit Amt'] || '',
  weight: it.Weight || '',
  end_item: it['End Item'] || '',
  orgs: it.Orgs || '',
  shelf_life: it['Shelf Life Code'] || '',
  classification: it['Product Classification'] || '',
  material_code: it['Material Code'] || '',
  certification: it.Certification || '',
  remarks: it['Additional Remarks'] || '',
  other: it['Other Details'] || '',
  cats: it.cats || [],
  image: it.ik ? (imgMap.get(it.ik) || null) : null,
}));

fs.writeFileSync(outJson, JSON.stringify(items));
const mb = n => (n / 1048576).toFixed(2) + ' MB';
console.log(`items      : ${items.length}  -> ${outJson} (${mb(fs.statSync(outJson).size)})`);
console.log(`images     : ${written} written, ${skipped} unparseable -> ${imgDir}`);
console.log(`with image : ${items.filter(i => i.image).length}`);
console.log(`with price : ${items.filter(i => i.unit_price).length}`);

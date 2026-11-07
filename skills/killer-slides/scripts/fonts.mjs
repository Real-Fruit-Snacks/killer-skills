#!/usr/bin/env node
// Self-host web fonts from the killer-skills font pack: no network, and no requests to a
// third party from the finished site.
//
//   node fonts.mjs list [text]                       the families in the pack, with weights or axes
//   node fonts.mjs add <site> "Family" ["Family" ...] [--weights 400,700] [--subsets latin,latin-ext]
//                                                    [--no-italic] [--dir assets/fonts]
//
// add copies each family's .woff2 files and licence into <site>/assets/fonts/<id>/ and writes
// <site>/assets/fonts/fonts.css: one @font-face rule per file, with font-display: swap and the
// subset's unicode-range. Families added before stay; adding one again replaces it.
// --weights picks the weights of static families (variable ones are one file per style).
//
// The pack is in ~/.cache/killer-skills-fonts ($KILLER_SKILLS_CACHE/killer-skills-fonts if
// that's set). The killer-skills installers put it there.
import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const PACK = path.join(process.env.KILLER_SKILLS_CACHE || path.join(os.homedir(), '.cache'), 'killer-skills-fonts');
const HEADER = '/* Self-hosted web fonts, copied from the killer-skills font pack by fonts.mjs.\n   Each family\'s licence is in its folder. */\n';

function die(msg) {
  console.error(msg);
  process.exit(1);
}

async function loadPack() {
  const file = path.join(PACK, 'fonts.json');
  if (!existsSync(file)) {
    die(`the font pack isn't installed (no ${file}).\n`
      + 'Run the killer-skills installer to get it, or load the faces from Google Fonts (references/typography.md).');
  }
  return JSON.parse(await fs.readFile(file, 'utf8'));
}

function describe(f, weights = f.weights) {
  if (f.axes) {
    const axes = Object.entries(f.axes).filter(([a]) => a !== 'ital').map(([a, [lo, hi]]) => `${a} ${lo}-${hi}`);
    return `variable: ${axes.join(', ')}`;
  }
  return `weights ${weights.join(' ')}`;
}

function find(pack, name) {
  const want = name.trim().toLowerCase();
  const hit = pack.families.find((f) => f.family.toLowerCase() === want || f.id === want);
  if (hit) return hit;
  const near = pack.families.filter((f) => f.family.toLowerCase().includes(want)
    || f.family.toLowerCase().split(/\s+/).some((w) => w.length >= 4 && want.includes(w)))
    .map((f) => f.family);
  die(`no family called "${name}" in the font pack.` + (near.length ? ` Close: ${near.slice(0, 6).join(', ')}.` : '')
    + ' `node fonts.mjs list` shows them all.');
}

function rule(f, file, dir) {
  const lines = [
    `  font-family: "${f.family}";`,
    `  font-style: ${file.style};`,
  ];
  if (f.axes) {
    if (f.axes.wght) lines.push(`  font-weight: ${f.axes.wght[0]} ${f.axes.wght[1]};`);
    if (f.axes.wdth) lines.push(`  font-stretch: ${f.axes.wdth[0]}% ${f.axes.wdth[1]}%;`);
  } else {
    lines.push(`  font-weight: ${file.weight};`);
  }
  lines.push('  font-display: swap;');
  lines.push(`  src: url("${dir}/${file.file}") format("woff2");`);
  if (file.unicodeRange) lines.push(`  unicode-range: ${file.unicodeRange};`);
  return `@font-face {\n${lines.join('\n')}\n}\n`;
}

// fonts.css as blocks keyed by family id, so adding a family again replaces only its block.
function splitCss(text) {
  const blocks = new Map();
  const parts = text.split(/^(?=\/\* font: )/m);
  for (const part of parts) {
    const m = part.match(/^\/\* font: ([a-z0-9-]+) /);
    if (m) blocks.set(m[1], part.replace(/\s*$/, '\n'));
  }
  return blocks;
}

function args(argv) {
  const out = { _: [], weights: null, subsets: null, italic: true, dir: 'assets/fonts' };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--weights') out.weights = argv[++i].split(',').map((w) => parseInt(w, 10)).filter(Boolean);
    else if (a === '--subsets') out.subsets = argv[++i].split(',').map((s) => s.trim()).filter(Boolean);
    else if (a === '--no-italic') out.italic = false;
    else if (a === '--dir') out.dir = argv[++i];
    else if (a === '-h' || a === '--help') out.help = true;
    else if (a.startsWith('--')) die(`unknown option ${a}`);
    else out._.push(a);
  }
  return out;
}

async function list(pack, text) {
  const want = (text || '').toLowerCase();
  const rows = pack.families.filter((f) => !want || f.family.toLowerCase().includes(want) || (f.category || '').startsWith(want));
  const w = Math.max(...rows.map((f) => f.family.length), 6);
  for (const f of rows) {
    console.log(`${f.family.padEnd(w)}  ${(f.category || '').padEnd(11)}  ${f.styles.join('+').padEnd(13)}  ${describe(f)}  (${f.license})`);
  }
  console.log(`\n${rows.length} of ${pack.count} families, in ${PACK}`);
}

async function add(pack, site, names, o) {
  if (!site || !names.length) die('usage: node fonts.mjs add <site> "Family" ["Family" ...]');
  if (!existsSync(site)) die(`no folder ${site}`);
  const outDir = path.join(site, o.dir);
  const cssPath = path.join(outDir, 'fonts.css');
  const blocks = existsSync(cssPath) ? splitCss(await fs.readFile(cssPath, 'utf8')) : new Map();
  const subsets = o.subsets || pack.subsets;
  let preload = null;

  // Check every family before copying any, so a bad name or weight leaves the site as it was.
  const picked = names.map((name) => {
    const f = find(pack, name);
    let files = f.files.filter((x) => subsets.includes(x.subset) && (o.italic || x.style !== 'italic'));
    if (o.weights && !f.axes) {
      const missing = o.weights.filter((w) => !f.weights.includes(w));
      if (missing.length) die(`${f.family} has no weight ${missing.join(', ')}; it has ${f.weights.join(' ')}. Nothing was changed.`);
      files = files.filter((x) => o.weights.includes(x.weight));
    }
    if (!files.length) die(`${f.family}: no files match those subsets, weights and styles. Nothing was changed.`);
    return { f, files };
  });

  await fs.mkdir(outDir, { recursive: true });
  for (const { f, files } of picked) {
    const dest = path.join(outDir, f.id);
    await fs.rm(dest, { recursive: true, force: true });
    await fs.mkdir(dest, { recursive: true });
    let bytes = 0;
    for (const x of files) {
      await fs.copyFile(path.join(PACK, f.id, x.file), path.join(dest, x.file));
      bytes += (await fs.stat(path.join(dest, x.file))).size;
    }
    await fs.copyFile(path.join(PACK, f.id, 'LICENSE'), path.join(dest, 'LICENSE'));
    const head = `/* font: ${f.id} | ${f.family} | ${f.license}, see ${f.id}/LICENSE | ${f.source} */\n`;
    blocks.set(f.id, head + files.map((x) => rule(f, x, f.id)).join(''));
    if (!preload) {
      const first = files.find((x) => x.subset === 'latin' && x.style === 'normal' && (f.axes || x.weight === 400)) || files[0];
      preload = `${o.dir.replace(/\\/g, '/')}/${f.id}/${first.file}`;
    }
    const weights = [...new Set(files.map((x) => x.weight))];
    const note = o.weights && f.axes ? ' (variable, so --weights doesn\'t apply)'
      : !o.weights && !f.axes && weights.length > 1 ? ' (every weight: --weights copies only the ones you use)' : '';
    console.log(`added ${f.family}: ${files.length} files, ${Math.round(bytes / 1024)} KB, ${describe(f, weights)}${note}`);
  }

  const css = HEADER + [...blocks.values()].map((b) => '\n' + b).join('');
  await fs.writeFile(cssPath, css);
  const href = `${o.dir.replace(/\\/g, '/')}/fonts.css`;
  const rel = path.relative(process.cwd(), cssPath);
  const shown = rel && !rel.startsWith('..') && !path.isAbsolute(rel) ? rel : cssPath;
  console.log(`\nwrote ${shown} (${blocks.size} famil${blocks.size === 1 ? 'y' : 'ies'}). Link it before your own stylesheet:`);
  console.log(`  <link rel="stylesheet" href="${href}">`);
  console.log('Preload the face the first screen depends on, and only that one:');
  console.log(`  <link rel="preload" href="${preload}" as="font" type="font/woff2" crossorigin>`);
}

const o = args(process.argv.slice(2));
const [cmd, ...rest] = o._;
if (o.help || !cmd || !['list', 'add'].includes(cmd)) {
  console.log('usage:\n  node fonts.mjs list [text]\n  node fonts.mjs add <site> "Family" ["Family" ...] [--weights 400,700] [--subsets latin,latin-ext] [--no-italic] [--dir assets/fonts]');
  process.exit(o.help ? 0 : 1);
}
const pack = await loadPack();
if (cmd === 'list') await list(pack, rest[0]);
else await add(pack, rest[0], rest.slice(1), o);

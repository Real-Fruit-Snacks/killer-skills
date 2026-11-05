#!/usr/bin/env node
/**
 * card.mjs - turn an HTML file into an image: the social preview card, or any
 * designed still the README needs.
 *
 *   node card.mjs <file.html> [--size 1280x640] [--out card.png] [--dark] [--scale 1] [--quality 90]
 *
 * The HTML file's folder is served locally, so relative fonts and images load.
 * --dark also writes a version with prefers-color-scheme: dark (<out>-dark.<ext>).
 * An --out ending in .jpg or .jpeg writes JPEG at --quality.
 * GitHub's social preview takes PNG, JPG or GIF under 1 MB; 1280x640 is the size it recommends.
 */
import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { loadPlaywright, launch, serve, parseSize } from './lib.mjs';

const argv = process.argv.slice(2);
if (!argv.length || argv.includes('-h') || argv.includes('--help')) {
  console.log('usage: node card.mjs <file.html> [--size 1280x640] [--out card.png] [--dark] [--scale 1] [--quality 90]');
  process.exit(argv.length ? 0 : 1);
}
const opt = { size: { width: 1280, height: 640 }, scale: 1, quality: 90, dark: false };
let file;
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--size') opt.size = parseSize(argv[++i], null);
  else if (a === '--out') opt.out = argv[++i];
  else if (a === '--dark') opt.dark = true;
  else if (a === '--scale') opt.scale = Number(argv[++i]);
  else if (a === '--quality') opt.quality = Number(argv[++i]);
  else if (!file) file = a;
  else { console.error(`unknown argument: ${a}`); process.exit(1); }
}
if (!opt.size) { console.error('--size must look like 1280x640'); process.exit(1); }
file = path.resolve(file);
if (!existsSync(file)) { console.error(`not found: ${file}`); process.exit(1); }
const out = path.resolve(opt.out || path.join(path.dirname(file), 'card.png'));
const jpeg = /\.jpe?g$/i.test(out);

const { server, origin } = await serve(path.dirname(file));
const browser = await launch(await loadPlaywright());
const written = [];
try {
  for (const scheme of opt.dark ? ['light', 'dark'] : ['light']) {
    const ctx = await browser.newContext({ viewport: opt.size, deviceScaleFactor: opt.scale, colorScheme: scheme });
    const pg = await ctx.newPage();
    const failed = [];
    pg.on('response', (r) => { if (r.status() >= 400) failed.push(`${r.status()} ${r.url()}`); });
    await pg.goto(`${origin}/${encodeURIComponent(path.basename(file))}`, { waitUntil: 'networkidle' });
    await pg.evaluate(() => document.fonts.ready);
    await pg.waitForTimeout(300);
    const dest = scheme === 'dark' ? out.replace(/(\.[a-z]+)$/i, '-dark$1') : out;
    await pg.screenshot({ path: dest, type: jpeg ? 'jpeg' : 'png', ...(jpeg ? { quality: opt.quality } : {}) });
    for (const f of failed) console.log(`WARN ${scheme}: ${f}`);
    const overflow = await pg.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1 || document.documentElement.scrollHeight > innerHeight + 1);
    if (overflow) console.log(`WARN ${scheme}: the content is bigger than ${opt.size.width}x${opt.size.height}, so part of it is cut off`);
    written.push(dest);
    await ctx.close();
  }
} finally {
  await browser.close();
  server.close();
}
for (const w of written) {
  const { size } = await fs.stat(w);
  console.log(`Wrote ${w} (${opt.size.width * opt.scale}x${opt.size.height * opt.scale}, ${(size / 1024).toFixed(0)} KB)`);
  if (size > 1_000_000) console.log('WARN over 1 MB: too big for a GitHub social preview. Try a .jpg --out, fewer gradients or less noise.');
}

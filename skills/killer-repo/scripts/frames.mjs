#!/usr/bin/env node
/**
 * frames.mjs - turn a few real still frames into one animated image for a README,
 * with nothing but the Chromium Playwright already has (no ffmpeg).
 *
 *   node frames.mjs <frame1.png> <frame2.png> ... --out demo.svg
 *     [--durations 2500,1500,3000]  ms each frame stays up (the last value repeats; default 2000)
 *     [--fade 250]                  cross-fade between frames, ms
 *     [--hold 0]                    the frame shown, still, to readers who turn off motion
 *     [--width 1600]                scale every frame to this width (default: the first frame's)
 *     [--quality 0.85]              WebP quality, 0 to 1
 *     [--title "what it shows"]     the accessible description
 *
 * Frames can come from anywhere: Playwright screenshots of a web app, captures of a
 * desktop window, renders of terminal output, card.mjs stills. Each is re-encoded as
 * WebP and embedded in an SVG that steps through them with CSS, which GitHub plays
 * inside a README <img>. Make one per theme for a <picture> pair.
 */
import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { loadPlaywright, launch } from './lib.mjs';

const argv = process.argv.slice(2);
if (!argv.length || argv.includes('-h') || argv.includes('--help')) {
  console.log('usage: node frames.mjs <frame> <frame> ... --out demo.svg [--durations 2000,...] [--fade 250] [--hold 0] [--width px] [--quality 0.85] [--title text]');
  process.exit(argv.length ? 0 : 1);
}
const opt = { durations: [2000], fade: 250, hold: 0, quality: 0.85, title: 'Animated demo' };
const frames = [];
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--out') opt.out = argv[++i];
  else if (a === '--durations' || a === '--duration') opt.durations = argv[++i].split(',').map(Number).filter((n) => n > 0);
  else if (a === '--fade') opt.fade = Number(argv[++i]);
  else if (a === '--hold') opt.hold = Number(argv[++i]);
  else if (a === '--width') opt.width = Number(argv[++i]);
  else if (a === '--quality') opt.quality = Number(argv[++i]);
  else if (a === '--title') opt.title = argv[++i];
  else frames.push(path.resolve(a));
}
if (!opt.out) { console.error('--out is required'); process.exit(1); }
if (!frames.length) { console.error('give at least one frame'); process.exit(1); }
for (const f of frames) if (!existsSync(f)) { console.error(`not found: ${f}`); process.exit(1); }
if (!opt.durations.length) opt.durations = [2000];
if (opt.hold < 0 || opt.hold >= frames.length) { console.error(`--hold must be 0 to ${frames.length - 1}`); process.exit(1); }

const mime = (f) => ({ '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp' })[path.extname(f).toLowerCase()] || 'image/png';
const inputs = await Promise.all(frames.map(async (f) => `data:${mime(f)};base64,${(await fs.readFile(f)).toString('base64')}`));

// Re-encode in the browser: canvas.toDataURL gives WebP without any native tools.
const browser = await launch(await loadPlaywright());
let encoded;
try {
  const page = await browser.newPage();
  encoded = await page.evaluate(async ({ inputs, width, quality }) => {
    const load = (src) => new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error('could not decode a frame')); i.src = src; });
    const imgs = [];
    for (const s of inputs) imgs.push(await load(s));
    const w = Math.round(width || imgs[0].naturalWidth);
    const h = Math.round((imgs[0].naturalHeight * w) / imgs[0].naturalWidth);
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const ctx = c.getContext('2d');
    return {
      w, h,
      sizes: imgs.map((i) => `${i.naturalWidth}x${i.naturalHeight}`),
      data: imgs.map((i) => { ctx.clearRect(0, 0, w, h); ctx.drawImage(i, 0, 0, w, h); return c.toDataURL('image/webp', quality); }),
    };
  }, { inputs, width: opt.width, quality: opt.quality });
} finally {
  await browser.close();
}
const { w, h, data, sizes } = encoded;
if (new Set(sizes).size > 1) console.log(`WARN frames differ in size (${[...new Set(sizes)].join(', ')}); all were scaled to ${w}x${h}, which may stretch some`);

// Timeline: frame i fades in at its start, stays fully up until the next one has faded in over it,
// then drops out. Later frames sit on top. The first frame comes back under the last as it fades out.
const n = data.length;
const dur = data.map((_, i) => opt.durations[Math.min(i, opt.durations.length - 1)]);
const F = Math.max(0, opt.fade);
const T = dur.reduce((a, b) => a + b, 0);
const pct = (ms) => `${Math.min(100, Math.max(0, (ms / T) * 100)).toFixed(3)}%`;
const after = (ms) => `${Math.min(100, (ms / T) * 100 + 0.001).toFixed(3)}%`;
const starts = dur.map((_, i) => dur.slice(0, i).reduce((a, b) => a + b, 0));
const keyframes = [];
for (let i = 0; i < n && n > 1; i++) {
  const s = starts[i], e = s + dur[i];
  let k;
  if (i === 0) k = `0% { opacity: 1 } ${pct(e + F)} { opacity: 1 } ${after(e + F)} { opacity: 0 } ${pct(T - F)} { opacity: 0 } 100% { opacity: 1 }`;
  else if (i === n - 1) k = `0%, ${pct(s)} { opacity: 0 } ${pct(s + F)} { opacity: 1 } ${pct(T - F)} { opacity: 1 } 100% { opacity: 0 }`;
  else k = `0%, ${pct(s)} { opacity: 0 } ${pct(s + F)} { opacity: 1 } ${pct(e + F)} { opacity: 1 } ${after(e + F)} { opacity: 0 } 100% { opacity: 0 }`;
  keyframes.push(`@keyframes f${i} { ${k} } .f${i} { animation: f${i} ${T}ms linear infinite; }`);
}
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${esc(opt.title)}">
<title>${esc(opt.title)}</title>
<style>
  .f { opacity: 0; } .f${opt.hold} { opacity: 1; }
  @media (prefers-reduced-motion: no-preference) {
    ${keyframes.join('\n    ')}
  }
</style>
${data.map((d, i) => `<image class="f f${i}" width="${w}" height="${h}" href="${d}"/>`).join('\n')}
</svg>
`;
const out = path.resolve(opt.out);
await fs.mkdir(path.dirname(out), { recursive: true });
await fs.writeFile(out, svg);
const kb = (bytes) => Math.round(bytes / 1024);
console.log(`Wrote ${out}: ${n} frame(s), ${w}x${h}, ${(T / 1000).toFixed(1)}s loop, ${kb(svg.length)} KB`);
console.log(`  frames: ${data.map((d, i) => `${path.basename(frames[i])} ${kb((d.length * 3) / 4)} KB`).join(', ')}`);
if (svg.length > 2_000_000) console.log('WARN over 2 MB: heavy for a first screen. Try fewer frames, a smaller --width or a lower --quality.');

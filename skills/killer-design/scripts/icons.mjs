#!/usr/bin/env node
/**
 * icons.mjs - favicons and app icons. Audit the set a site already has, or build a complete set from one SVG.
 *
 *   node icons.mjs audit <site-dir | index.html> [--out dir]
 *   node icons.mjs build <mark.svg> --out <site-dir> [--bg "#123456"] [--name "Site name"] [--theme-color "#123456"] [--force]
 *
 * audit  reads the page's <head> and checks every icon it links (and /favicon.ico, which browsers request anyway):
 *        the files exist, their real pixel sizes, SVG problems (text that needs fonts, external references,
 *        non-square viewBox), a missing dark variant, Apple touch icon transparency, and the manifest's icons.
 *        It renders a preview sheet: the icon in light and dark browser tabs at 1x and 2x, zoomed pixels,
 *        iOS and Android home screens (with the maskable safe zone), and the SVG in light and dark mode.
 * build  renders one square SVG mark into icon.svg, favicon.ico (16/32/48), apple-touch-icon.png (180),
 *        icon-192.png, icon-512.png, icon-maskable-512.png and manifest.webmanifest, prints the <head> tags,
 *        and then audits the result. Existing files are kept unless you pass --force.
 *
 * Output goes to --out (audit default: <os tmp>/killer-design-icons/<label>). Uses the same Playwright install
 * as shoot.mjs (~/.cache/killer-design).
 */
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { execSync, execFileSync } from 'node:child_process';
import fs from 'node:fs/promises';
import { existsSync, statSync, readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const argv = process.argv.slice(2);
const cmd = argv[0];
if (!['audit', 'build'].includes(cmd)) {
  console.log('usage:\n  node icons.mjs audit <site-dir | index.html> [--out dir]\n  node icons.mjs build <mark.svg> --out <site-dir> [--bg "#123456"] [--name "Site"] [--theme-color "#123456"] [--force]');
  process.exit(cmd ? 1 : 0);
}
const opt = { force: false };
let target;
for (let i = 1; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--out') opt.out = argv[++i];
  else if (a === '--bg') opt.bg = argv[++i];
  else if (a === '--name') opt.name = argv[++i];
  else if (a === '--theme-color') opt.theme = argv[++i];
  else if (a === '--force') opt.force = true;
  else if (!target) target = a;
  else { console.error(`unknown argument: ${a}`); process.exit(1); }
}
if (!target) { console.error('missing target'); process.exit(1); }

// ---------- playwright (same cache as shoot.mjs) ----------
const CACHE = path.join(process.env.KILLER_SKILLS_CACHE || path.join(os.homedir(), '.cache'), 'killer-design');
// The runtime the killer-skills installer puts in ~/.cache/killer-skills on Windows:
// Playwright with its own Chromium beside it, so nothing needs downloading.
const RUNTIME = path.join(process.env.KILLER_SKILLS_CACHE || path.join(os.homedir(), '.cache'), 'killer-skills');
async function loadPlaywright() {
  for (const base of [process.cwd(), RUNTIME, CACHE]) {
    try {
      const req = createRequire(path.join(base, '__resolve__.js'));
      req.resolve('playwright/package.json');
      // Point the runtime's Playwright at its own Chromium before it loads.
      if (base === RUNTIME && existsSync(path.join(RUNTIME, 'ms-playwright'))) process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(RUNTIME, 'ms-playwright');
      const mod = await import(pathToFileURL(req.resolve('playwright')).href);
      return { pw: mod.default ?? mod, dir: path.dirname(req.resolve('playwright/package.json')) };
    } catch {}
  }
  console.error(`[icons] Playwright not found - installing it once into ${CACHE} ...`);
  await fs.mkdir(CACHE, { recursive: true });
  const pj = path.join(CACHE, 'package.json');
  if (!existsSync(pj)) await fs.writeFile(pj, '{ "name": "killer-design-tools", "private": true }\n');
  execSync('npm install --prefer-offline --no-audit --no-fund --loglevel=error playwright', { cwd: CACHE, stdio: 'inherit' });
  const req = createRequire(path.join(CACHE, '__resolve__.js'));
  const mod = await import(pathToFileURL(req.resolve('playwright')).href);
  return { pw: mod.default ?? mod, dir: path.dirname(req.resolve('playwright/package.json')) };
}
async function launch({ pw, dir }) {
  try { return await pw.chromium.launch(); } catch (e) {
    if (!/Executable doesn't exist|playwright install/i.test(String(e.message))) throw e;
    execFileSync(process.execPath, [path.join(dir, 'cli.js'), 'install', 'chromium'], { stdio: 'inherit' });
    return await pw.chromium.launch();
  }
}

// ---------- file helpers ----------
const dataUrl = (buf, mime) => `data:${mime};base64,${buf.toString('base64')}`;
const mimeOf = (f) => ({ '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.jpg': 'image/jpeg', '.webp': 'image/webp' }[path.extname(f).toLowerCase()] || 'application/octet-stream');
function pngSize(buf) {
  if (buf.length < 24 || buf.readUInt32BE(0) !== 0x89504e47) return null;
  return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) };
}
function icoEntries(buf) {
  if (buf.length < 6 || buf.readUInt16LE(2) !== 1) return null;
  const n = buf.readUInt16LE(4), out = [];
  for (let i = 0; i < n; i++) {
    const o = 6 + i * 16;
    const w = buf[o] || 256, h = buf[o + 1] || 256, size = buf.readUInt32LE(o + 8), off = buf.readUInt32LE(o + 12);
    const data = buf.subarray(off, off + size);
    out.push({ w, h, png: pngSize(data) !== null, data });
  }
  return out;
}
function writeIco(pngs) {
  const head = Buffer.alloc(6); head.writeUInt16LE(0, 0); head.writeUInt16LE(1, 2); head.writeUInt16LE(pngs.length, 4);
  const dir = Buffer.alloc(16 * pngs.length);
  let off = 6 + 16 * pngs.length;
  pngs.forEach((p, i) => {
    const o = i * 16, s = p.size >= 256 ? 0 : p.size;
    dir.writeUInt8(s, o); dir.writeUInt8(s, o + 1); dir.writeUInt8(0, o + 2); dir.writeUInt8(0, o + 3);
    dir.writeUInt16LE(1, o + 4); dir.writeUInt16LE(32, o + 6); dir.writeUInt32LE(p.buf.length, o + 8); dir.writeUInt32LE(off, o + 12);
    off += p.buf.length;
  });
  return Buffer.concat([head, dir, ...pngs.map((p) => p.buf)]);
}
function svgProblems(text) {
  const issues = [];
  const vb = text.match(/viewBox\s*=\s*["']\s*([-\d.]+)[\s,]+([-\d.]+)[\s,]+([-\d.]+)[\s,]+([-\d.]+)/i);
  if (!vb) issues.push(['WARN', 'SVG has no viewBox, so it may not scale cleanly to 16px and 512px']);
  else if (Math.abs(+vb[3] - +vb[4]) > 0.01) issues.push(['WARN', `SVG viewBox is not square (${vb[3]}x${vb[4]}); icons are square and it will be letterboxed`]);
  if (/<text[\s>]/i.test(text)) issues.push(['WARN', 'SVG uses <text>. Favicons cannot load web fonts, so the letters fall back to a system font; convert text to paths']);
  if (/(?:xlink:)?href\s*=\s*["'](?!#|data:)[^"']+["']/i.test(text)) issues.push(['WARN', 'SVG references external files; favicon SVGs are rendered in isolation and cannot load them']);
  if (/<image[\s>]/i.test(text)) issues.push(['INFO', 'SVG embeds a raster <image>; it will not stay sharp at large sizes']);
  return issues;
}

// ---------- rendering + analysis in the browser ----------
async function renderPng(page, src, size, { bg = null, scale = 1 } = {}) {
  const view = Math.max(size, 64);
  await page.setViewportSize({ width: view, height: view });
  const inner = Math.round(size * scale);
  await page.setContent(`<!doctype html><html><body style="margin:0;background:transparent"><div style="width:${size}px;height:${size}px;display:grid;place-items:center;${bg ? `background:${bg};` : ''}"><img src="${src}" style="width:${inner}px;height:${inner}px;display:block"></div></body></html>`);
  await page.evaluate(() => document.images[0].decode().catch(() => {}));
  return page.screenshot({ clip: { x: 0, y: 0, width: size, height: size }, omitBackground: !bg, type: 'png' });
}
// Fraction of pixels that stand out from each tab background (contrast >= 1.5:1 once composited), corner alpha and colour.
async function analyze(page, pngBuf, bgs) {
  return page.evaluate(async ({ src, bgs }) => {
    const img = new Image(); img.src = src; await img.decode();
    const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
    const x = c.getContext('2d', { willReadFrequently: true }); x.drawImage(img, 0, 0);
    const d = x.getImageData(0, 0, c.width, c.height).data;
    const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    const L = (r, g, b) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
    const out = { visibility: {}, transparentPixels: 0, corner: null };
    let transparent = 0;
    for (let i = 3; i < d.length; i += 4) if (d[i] < 250) transparent++;
    out.transparentPixels = transparent;
    out.corner = { r: d[0], g: d[1], b: d[2], a: d[3] };
    for (const [name, h] of Object.entries(bgs)) {
      const [br, bgc, bb] = hex(h), Lb = L(br, bgc, bb);
      let seen = 0;
      for (let i = 0; i < d.length; i += 4) {
        const a = d[i + 3] / 255;
        const r = d[i] * a + br * (1 - a), g = d[i + 1] * a + bgc * (1 - a), b = d[i + 2] * a + bb * (1 - a);
        const Lp = L(r, g, b), hi = Math.max(Lp, Lb), lo = Math.min(Lp, Lb);
        if ((hi + 0.05) / (lo + 0.05) >= 1.5) seen++;
      }
      out.visibility[name] = Math.round((seen / (d.length / 4)) * 100);
    }
    return out;
  }, { src: dataUrl(pngBuf, 'image/png'), bgs });
}

const TABS = { 'light tab': '#ffffff', 'light strip': '#dee1e6', 'dark tab': '#35363a', 'dark strip': '#202124' };

// ---------- gather the icon set ----------
async function gatherFromSite(page, input) {
  const abs = path.resolve(input);
  const index = statSync(abs).isDirectory() ? path.join(abs, 'index.html') : abs;
  const root = path.dirname(index);
  const html = await fs.readFile(index, 'utf8');
  await page.setContent(html.replace(/<script[\s\S]*?<\/script>/gi, ''), { waitUntil: 'domcontentloaded' });
  const head = await page.evaluate(() => ({
    title: document.title,
    links: [...document.querySelectorAll('link[rel]')].map((l) => ({ rel: l.getAttribute('rel').toLowerCase(), href: l.getAttribute('href'), type: l.getAttribute('type') || '', sizes: l.getAttribute('sizes') || '', media: l.getAttribute('media') || '' })),
    themeColors: [...document.querySelectorAll('meta[name="theme-color"]')].map((m) => ({ content: m.getAttribute('content'), media: m.getAttribute('media') || '' })),
  }));
  const local = (href) => (href && !/^(https?:)?\/\//i.test(href) && !href.startsWith('data:') ? path.join(root, href.replace(/^\//, '').split(/[?#]/)[0]) : null);
  const set = { root, title: head.title, themeColors: head.themeColors, links: head.links, files: {}, notes: [] };
  const icons = head.links.filter((l) => /(^|\s)icon(\s|$)/.test(l.rel));
  for (const l of icons) {
    const f = local(l.href);
    if (!f) { set.notes.push(['INFO', `icon ${l.href} is remote; not checked`]); continue; }
    if (!existsSync(f)) { set.notes.push(['FAIL', `linked icon is missing: ${l.href}`]); continue; }
    const kind = l.type === 'image/svg+xml' || f.endsWith('.svg') ? 'svg' : f.endsWith('.ico') ? 'ico' : 'png';
    if (!set.files[kind]) set.files[kind] = { file: f, href: l.href, sizes: l.sizes };
  }
  if (!set.files.ico && existsSync(path.join(root, 'favicon.ico'))) set.files.ico = { file: path.join(root, 'favicon.ico'), href: '/favicon.ico (not linked)', sizes: '' };
  const apple = head.links.find((l) => l.rel.includes('apple-touch-icon'));
  if (apple) { const f = local(apple.href); if (f && existsSync(f)) set.files.apple = { file: f, href: apple.href }; else set.notes.push(['FAIL', `apple-touch-icon is missing: ${apple.href}`]); }
  else if (existsSync(path.join(root, 'apple-touch-icon.png'))) set.files.apple = { file: path.join(root, 'apple-touch-icon.png'), href: '/apple-touch-icon.png (not linked)' };
  const man = head.links.find((l) => l.rel === 'manifest');
  if (man) {
    const f = local(man.href);
    if (f && existsSync(f)) {
      try {
        const m = JSON.parse(readFileSync(f, 'utf8'));
        set.manifest = { file: f, data: m, icons: (m.icons || []).map((i) => ({ ...i, file: local(new URL(i.src, 'http://x/' + man.href.replace(/^\//, '')).pathname) })) };
      } catch (e) { set.notes.push(['FAIL', `manifest is not valid JSON: ${e.message}`]); }
    } else set.notes.push(['FAIL', `manifest is missing: ${man.href}`]);
  }
  return set;
}

// ---------- checks + preview sheet ----------
async function report(browser, page, set, outDir, label) {
  const notes = [...set.notes];
  const add = (lvl, msg) => notes.push([lvl, msg]);
  const F = set.files;
  const svgText = F.svg ? readFileSync(F.svg.file, 'utf8') : null;
  if (!F.svg && !F.ico && !F.png) add('FAIL', 'no favicon at all; browsers will show a blank page icon');
  if (!F.svg) add('WARN', 'no SVG favicon; it is the one that stays sharp at every size and can adapt to dark mode');
  if (!F.ico) add('WARN', 'no favicon.ico; Safari and some apps fall back to it, and browsers request /favicon.ico anyway');
  if (svgText) for (const [lvl, m] of svgProblems(svgText)) add(lvl, m);

  // 16 and 32px sources, preferring what browsers actually use: the ICO's own entries, then the SVG
  let ico16, ico32;
  if (F.ico) {
    const entries = icoEntries(readFileSync(F.ico.file));
    if (!entries) add('FAIL', `${path.basename(F.ico.file)} is not a valid .ico file`);
    else {
      add('INFO', `favicon.ico sizes: ${entries.map((e) => `${e.w}x${e.h}${e.png ? '' : ' (bmp)'}`).join(', ')}`);
      if (!entries.some((e) => e.w === 32)) add('WARN', 'favicon.ico has no 32x32 image, the size most screens use');
      const pick = (s) => entries.find((e) => e.w === s && e.png)?.data;
      ico16 = pick(16); ico32 = pick(32);
    }
    if (F.svg && F.ico.sizes && F.ico.sizes !== '32x32' && F.ico.sizes !== 'any') add('INFO', `favicon.ico is linked with sizes="${F.ico.sizes}"; sizes="32x32" keeps Chrome from preferring the .ico over the SVG`);
    if (F.svg && !F.ico.sizes && !/not linked/.test(F.ico.href)) add('WARN', 'favicon.ico is linked without sizes; add sizes="32x32" so Chrome prefers the SVG');
  }
  const srcSvg = svgText ? dataUrl(Buffer.from(svgText), 'image/svg+xml') : null;
  const srcAny = srcSvg || (F.png ? dataUrl(readFileSync(F.png.file), 'image/png') : null);
  if (!ico16 && srcAny) ico16 = await renderPng(page, srcAny, 16);
  if (!ico32 && srcAny) ico32 = await renderPng(page, srcAny, 32);

  // SVG in light and dark colour schemes, at the sizes browsers rasterise it (Chrome and Firefox use the SVG; Safari and
  // older clients use the .ico, which can't adapt to dark mode)
  const svgR = {};
  if (srcSvg) {
    for (const scheme of ['light', 'dark']) {
      const ctx = await browser.newContext({ colorScheme: scheme });
      const p = await ctx.newPage();
      for (const s of [16, 32, 64]) svgR[`${scheme}${s}`] = await renderPng(p, srcSvg, s);
      await ctx.close();
    }
    if (svgR.light64.equals(svgR.dark64)) add('INFO', 'the SVG favicon looks the same in light and dark mode (no prefers-color-scheme rule)');
    else add('INFO', 'the SVG favicon has a dark-mode variant');
  }
  const LIGHT = { 'light tab': TABS['light tab'], 'light strip': TABS['light strip'] };
  const DARK = { 'dark tab': TABS['dark tab'], 'dark strip': TABS['dark strip'] };
  const visOf = async (lightBuf, darkBuf) => ({ ...(await analyze(page, lightBuf, LIGHT)).visibility, ...(await analyze(page, darkBuf, DARK)).visibility });
  const fmt = (v) => Object.entries(v).map(([k, n]) => `${k} ${n}%`).join(', ');
  let vis16 = null, visIco = null;
  if (srcSvg) vis16 = await visOf(svgR.light16, svgR.dark16);
  if (ico16) visIco = await visOf(ico16, ico16);
  const primary = vis16 || visIco;
  if (primary) {
    add('INFO', `16px visibility, share of pixels that stand out from the tab (${vis16 ? 'SVG favicon, as Chrome and Firefox show it' : '.ico'}): ${fmt(primary)}`);
    const worst = Object.entries(primary).sort((a, b) => a[1] - b[1])[0];
    if (worst[1] < 12) add('WARN', `at 16px the icon nearly disappears on the ${worst[0]} (${worst[1]}% of pixels stand out). Add a contrasting tile or outline, or a dark-mode rule in the SVG`);
  }
  if (vis16 && visIco) {
    const worstIco = Object.entries(visIco).sort((a, b) => a[1] - b[1])[0];
    if (worstIco[1] < 12) add('INFO', `the .ico fallback (Safari, older clients) can't switch for dark mode and nearly disappears on the ${worstIco[0]} (${worstIco[1]}%). A tile behind the mark in the .ico fixes it`);
  }

  // Apple touch icon
  let appleBuf = F.apple ? readFileSync(F.apple.file) : null;
  if (!appleBuf) add('WARN', 'no apple-touch-icon.png; iOS home screens will show a screenshot of the page instead');
  else {
    const s = pngSize(appleBuf);
    if (!s) add('FAIL', 'apple-touch-icon is not a PNG');
    else {
      if (s.w !== 180 || s.h !== 180) add('WARN', `apple-touch-icon is ${s.w}x${s.h}; use 180x180`);
      const a = await analyze(page, appleBuf, {});
      if (a.transparentPixels > 0) add('WARN', `apple-touch-icon has ${a.transparentPixels} transparent pixel(s); iOS fills transparency with black. Make it opaque and full-bleed (iOS rounds the corners itself)`);
    }
  }

  // Manifest icons
  let maskBuf = null, big = null;
  if (!set.manifest) add('WARN', 'no web app manifest; Android home-screen installs get a generic icon');
  else {
    const icons = set.manifest.icons;
    for (const i of icons) {
      if (!i.file || !existsSync(i.file)) { add('FAIL', `manifest icon is missing: ${i.src}`); continue; }
      const s = pngSize(readFileSync(i.file));
      if (s && i.sizes && i.sizes !== `${s.w}x${s.h}`) add('FAIL', `manifest says ${i.src} is ${i.sizes} but it is ${s.w}x${s.h}`);
    }
    const has = (n) => icons.some((i) => (i.sizes || '').split(/\s+/).includes(n) && !(i.purpose || '').includes('maskable'));
    if (!has('192x192')) add('WARN', 'manifest has no 192x192 icon');
    if (!has('512x512')) add('WARN', 'manifest has no 512x512 icon');
    const mask = icons.find((i) => (i.purpose || '').includes('maskable') && i.file && existsSync(i.file));
    if (!mask) add('WARN', 'manifest has no maskable icon; Android will shrink the icon inside a white circle');
    else maskBuf = readFileSync(mask.file);
    const b = icons.find((i) => (i.sizes || '').includes('512') && !(i.purpose || '').includes('maskable') && i.file && existsSync(i.file));
    if (b) big = readFileSync(b.file);
    const m = set.manifest.data;
    if (!m.name && !m.short_name) add('WARN', 'manifest has no name or short_name');
    if (!m.theme_color) add('INFO', 'manifest has no theme_color');
  }
  if (set.themeColors && !set.themeColors.length) add('INFO', 'no <meta name="theme-color">; mobile browsers use their default chrome colour');
  if (maskBuf) {
    // Content that matters should sit inside the central circle (radius 40% of the size). Flag colour changes near the edges.
    const edge = await page.evaluate(async (src) => {
      const img = new Image(); img.src = src; await img.decode();
      const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
      const x = c.getContext('2d', { willReadFrequently: true }); x.drawImage(img, 0, 0);
      const d = x.getImageData(0, 0, c.width, c.height).data, w = c.width, r = w * 0.4, cx = w / 2;
      const bg = [d[0], d[1], d[2]]; let outside = 0, differs = 0;
      for (let y = 0; y < w; y += 2) for (let xx = 0; xx < w; xx += 2) {
        if (Math.hypot(xx - cx, y - cx) <= r) continue;
        outside++;
        const i = (y * w + xx) * 4;
        if (Math.abs(d[i] - bg[0]) + Math.abs(d[i + 1] - bg[1]) + Math.abs(d[i + 2] - bg[2]) > 40) differs++;
      }
      return Math.round((differs / outside) * 100);
    }, dataUrl(maskBuf, 'image/png'));
    if (edge > 8) add('WARN', `maskable icon: ${edge}% of the area outside the safe zone differs from the background colour, so masks may crop part of the design (keep it inside the central 80% circle)`);
  }

  // Preview sheet
  const u = (b) => (b ? dataUrl(b, 'image/png') : '');
  const title = (set.title || 'Page title').slice(0, 28);
  const tab = (stripBg, tabBg, ink, src) => `<div class="strip" style="background:${stripBg}"><div class="tab" style="background:${tabBg};color:${ink}"><img src="${src}" width="16" height="16" class="px">${title}</div><div class="tab ghost" style="color:${ink}">Other tab</div></div>`;
  // What each tab theme actually shows: the SVG in the matching colour scheme when there is one, else the .ico
  const L16 = svgR.light16 || ico16, D16 = svgR.dark16 || ico16, L32 = svgR.light32 || ico32 || ico16, D32 = svgR.dark32 || ico32 || ico16;
  const sheet = `<!doctype html><meta charset="utf-8"><style>
    body{margin:0;padding:20px;background:#f2f2f2;font:13px system-ui,sans-serif;color:#222}
    h1{font-size:15px;margin:0 0 14px} h2{font-size:13px;margin:22px 0 8px;color:#555;font-weight:600}
    .row{display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start} figure{margin:0} figcaption{margin-top:5px;color:#555;font-size:12px}
    .strip{width:300px;height:34px;display:flex;align-items:flex-end;padding:0 8px;gap:2px;border-radius:6px 6px 0 0}
    .tab{height:28px;display:flex;align-items:center;gap:8px;padding:0 12px;border-radius:8px 8px 0 0;font-size:12px;white-space:nowrap;overflow:hidden;width:170px}
    .tab.ghost{opacity:.6;width:90px} .px{image-rendering:pixelated}
    .zoom{image-rendering:pixelated;display:block} .tile{padding:12px;border-radius:8px}
    .home{width:150px;height:150px;display:grid;place-items:center;border-radius:10px}
    .mask{position:relative;width:96px;height:96px;overflow:hidden} .mask img{width:100%;height:100%;display:block}
    .safe{position:absolute;inset:10%;border:2px dashed #e0245e;border-radius:50%}
  </style><h1>${label}: favicon and app icon preview</h1>
  <h2>Browser tabs on a 1x screen (16px)${srcSvg ? ', Chrome and Firefox using the SVG' : ''}</h2><div class="row">
    ${tab('#dee1e6', '#ffffff', '#202124', u(L16))}${tab('#202124', '#35363a', '#e8eaed', u(D16))}</div>
  <h2>Browser tabs on a 2x screen (32px)${srcSvg ? ', Chrome and Firefox using the SVG' : ''}</h2><div class="row">
    ${tab('#dee1e6', '#ffffff', '#202124', u(L32))}${tab('#202124', '#35363a', '#e8eaed', u(D32))}</div>
  ${srcSvg && ico16 ? `<h2>Fallback .ico (Safari, older clients), 1x</h2><div class="row">${tab('#dee1e6', '#ffffff', '#202124', u(ico16))}${tab('#202124', '#35363a', '#e8eaed', u(ico16))}</div>` : ''}
  <h2>Actual pixels, zoomed</h2><div class="row">
    ${[['#ffffff', 16, L16, 8], ['#202124', 16, D16, 8], ['#ffffff', 32, L32, 4], ['#202124', 32, D32, 4]].filter((r) => r[2]).map(([bg, s, b, z]) => `<figure><div class="tile" style="background:${bg}"><img class="zoom" src="${u(b)}" width="${s * z}" height="${s * z}"></div><figcaption>${s}px x${z} on ${bg}</figcaption></figure>`).join('')}</div>
  ${srcSvg ? `<h2>SVG favicon in light and dark mode</h2><div class="row"><figure><div class="tile" style="background:#fff"><img src="${u(svgR.light64)}" width="64" height="64"></div><figcaption>light</figcaption></figure><figure><div class="tile" style="background:#202124"><img src="${u(svgR.dark64)}" width="64" height="64"></div><figcaption>dark</figcaption></figure></div>` : ''}
  <h2>Home screens</h2><div class="row">
    ${appleBuf ? ['#d9dde4', '#1c1c1e'].map((wall, i) => `<figure><div class="home" style="background:${wall}"><div style="width:60px;height:60px;border-radius:13.5px;overflow:hidden;background:#000"><img src="${u(appleBuf)}" width="60" height="60" style="display:block"></div></div><figcaption>iOS, ${i ? 'dark' : 'light'} wallpaper (transparency shows black, as on iOS)</figcaption></figure>`).join('') : ''}
    ${maskBuf ? `<figure><div class="home" style="background:#e8eaed"><div class="mask" style="border-radius:50%"><img src="${u(maskBuf)}"></div></div><figcaption>Android, circle mask</figcaption></figure><figure><div class="home" style="background:#e8eaed"><div class="mask" style="border-radius:24px"><img src="${u(maskBuf)}"></div></div><figcaption>Android, squircle mask</figcaption></figure><figure><div class="home" style="background:#e8eaed"><div class="mask"><img src="${u(maskBuf)}"><div class="safe"></div></div></div><figcaption>Maskable safe zone (dashed)</figcaption></figure>` : ''}
    ${big ? `<figure><div class="home" style="background:#fff"><img src="${u(big)}" width="120" height="120"></div><figcaption>512px icon</figcaption></figure>` : ''}
  </div>`;
  const sp = await browser.newPage({ viewport: { width: 1100, height: 600 }, deviceScaleFactor: 2 });
  await sp.setContent(sheet, { waitUntil: 'load' });
  await fs.mkdir(outDir, { recursive: true });
  const file = path.join(outDir, `${label}-icons.png`);
  await sp.screenshot({ path: file, fullPage: true });
  await sp.close();

  const order = { FAIL: 0, WARN: 1, INFO: 2 };
  notes.sort((a, b) => order[a[0]] - order[b[0]]);
  console.log(`\npreview sheet -> ${file}\n`);
  for (const [lvl, m] of notes) console.log(`${lvl}  ${m}`);
  if (!notes.some((n) => n[0] === 'FAIL')) console.log('\nNo automated failures. Now read the preview sheet: does the icon read at 16px in both tab colours?');
  await fs.writeFile(path.join(outDir, `${label}-icons.json`), JSON.stringify({ notes, visibility16: vis16, visibilityIco16: visIco }, null, 2));
}

// ---------- build ----------
async function build(browser, page) {
  const svgPath = path.resolve(target);
  if (!existsSync(svgPath) || !svgPath.toLowerCase().endsWith('.svg')) { console.error('build needs an .svg mark'); process.exit(1); }
  if (!opt.out) { console.error('build needs --out <site-dir>'); process.exit(1); }
  const out = path.resolve(opt.out);
  await fs.mkdir(out, { recursive: true });
  const svgText = readFileSync(svgPath, 'utf8');
  for (const [lvl, m] of svgProblems(svgText)) console.log(`${lvl}  ${m}`);
  const src = dataUrl(Buffer.from(svgText), 'image/svg+xml');
  const probe = await analyze(page, await renderPng(page, src, 512), {});
  const fullBleed = probe.corner.a === 255;
  const bg = opt.bg || (fullBleed ? `#${[probe.corner.r, probe.corner.g, probe.corner.b].map((v) => v.toString(16).padStart(2, '0')).join('')}` : '#ffffff');
  if (!opt.bg && !fullBleed) console.log(`WARN  the mark has transparent corners and no --bg was given; Apple and maskable icons get a white background (${bg})`);
  const written = [];
  const put = async (name, buf) => {
    const f = path.join(out, name);
    if (existsSync(f) && !opt.force) { console.log(`INFO  kept existing ${name} (use --force to overwrite)`); return; }
    await fs.writeFile(f, buf); written.push(name);
  };
  await put('icon.svg', Buffer.from(svgText));
  const sizes = [16, 32, 48];
  const pngs = [];
  for (const s of sizes) pngs.push({ size: s, buf: await renderPng(page, src, s) });
  await put('favicon.ico', writeIco(pngs));
  await put('apple-touch-icon.png', await renderPng(page, src, 180, { bg, scale: fullBleed ? 1 : 0.84 }));
  await put('icon-192.png', await renderPng(page, src, 192));
  await put('icon-512.png', await renderPng(page, src, 512));
  await put('icon-maskable-512.png', await renderPng(page, src, 512, { bg, scale: 0.8 }));
  const theme = opt.theme || bg;
  const manifest = { name: opt.name || 'Site', short_name: (opt.name || 'Site').slice(0, 12), icons: [
    { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
    { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
    { src: 'icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
  ], theme_color: theme, background_color: bg };
  await put('manifest.webmanifest', Buffer.from(JSON.stringify(manifest, null, 2) + '\n'));
  console.log(`\nwrote ${written.length ? written.join(', ') : 'nothing new'} in ${out}\n\nPaste into <head>:\n` +
`<link rel="icon" href="favicon.ico" sizes="32x32">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="${theme}">`);
  // Audit what was produced
  const set = { root: out, title: opt.name || 'Site', themeColors: null, links: [], notes: [], files: {
    svg: { file: path.join(out, 'icon.svg'), href: 'icon.svg', sizes: '' },
    ico: { file: path.join(out, 'favicon.ico'), href: 'favicon.ico', sizes: '32x32' },
    apple: { file: path.join(out, 'apple-touch-icon.png'), href: 'apple-touch-icon.png' },
  } };
  const mf = path.join(out, 'manifest.webmanifest');
  const m = JSON.parse(readFileSync(mf, 'utf8'));
  set.manifest = { file: mf, data: m, icons: m.icons.map((i) => ({ ...i, file: path.join(out, i.src) })) };
  return set;
}

// ---------- main ----------
const playwright = await loadPlaywright();
const browser = await launch(playwright);
const page = await browser.newPage();
try {
  let set, label;
  if (cmd === 'audit') {
    set = await gatherFromSite(page, target);
    label = path.basename(set.root).replace(/[^a-z0-9._-]/gi, '_') || 'site';
  } else {
    set = await build(browser, page);
    label = path.basename(path.resolve(opt.out)).replace(/[^a-z0-9._-]/gi, '_') || 'icons';
  }
  const outDir = path.resolve(cmd === 'audit' ? (opt.out || path.join(os.tmpdir(), 'killer-design-icons', label)) : opt.out);
  await report(browser, page, set, cmd === 'build' ? path.join(os.tmpdir(), 'killer-design-icons', label) : outDir, label);
} finally {
  await browser.close();
}

#!/usr/bin/env node
/**
 * shoot.mjs - render a page the way visitors see it, and check the things that
 * are easy to get wrong when you write CSS without looking.
 *
 *   node shoot.mjs <index.html | site-dir | http(s)://url> [options]
 *
 * Options
 *   --out <dir>       where screenshots + report.json go
 *                     (default: <os tmp>/killer-design-shots/<label>-<hash of target>)
 *   --widths <list>   viewport widths, comma separated (default 390,768,1440)
 *   --slices <n>      viewport-sized screenshots per width, top to bottom (default 6, 0 = none)
 *   --no-full         skip the full-page screenshot per width
 *   --dark            also render every width with prefers-color-scheme: dark
 *   --wait <ms>       extra settle time after load and after each scroll (default 350)
 *   --label <name>    filename prefix (default: derived from the target)
 *   --motion          also capture motion: a contact sheet per width (widest + 390) showing the first
 *                     ~4s after load as frames, the pointer at three spots, the main button hovered, and a
 *                     reduced-motion frame; plus when the largest content painted (LCP)
 *
 * Local files and folders are served over a throwaway local http server, so
 * root-relative URLs (/styles.css) behave as they would when deployed.
 *
 * Needs Node 18+. Uses Playwright from the current project if present, otherwise
 * installs it once into ~/.cache/killer-design (and Chromium, if missing).
 */
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { execSync, execFileSync } from 'node:child_process';
import http from 'node:http';
import fs from 'node:fs/promises';
import { existsSync, statSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';

// ---------- args ----------
const argv = process.argv.slice(2);
if (!argv.length || argv.includes('-h') || argv.includes('--help')) {
  console.log(
    'usage: node shoot.mjs <index.html | dir | url> [--out dir] [--widths 390,768,1440] [--slices 6] [--no-full] [--dark] [--wait 350] [--label name]'
  );
  process.exit(argv.length ? 0 : 1);
}
const opt = { widths: [390, 768, 1440], slices: 6, full: true, dark: false, wait: 350 };
let target;
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--out') opt.out = argv[++i];
  else if (a === '--widths') opt.widths = argv[++i].split(',').map(Number).filter(Boolean);
  else if (a === '--slices') opt.slices = Number(argv[++i]);
  else if (a === '--no-full') opt.full = false;
  else if (a === '--dark') opt.dark = true;
  else if (a === '--wait') opt.wait = Number(argv[++i]);
  else if (a === '--label') opt.label = argv[++i];
  else if (a === '--motion') opt.motion = true;
  else if (!target) target = a;
  else { console.error(`unknown argument: ${a}`); process.exit(1); }
}

// ---------- playwright ----------
const CACHE = path.join(process.env.KILLER_SKILLS_CACHE || path.join(os.homedir(), '.cache'), 'killer-design');
// The runtime the killer-skills installer puts in ~/.cache/killer-skills on Windows:
// Playwright with its own Chromium beside it, so nothing needs downloading.
const RUNTIME = path.join(process.env.KILLER_SKILLS_CACHE || path.join(os.homedir(), '.cache'), 'killer-skills');

async function loadPlaywright() {
  for (const base of [process.cwd(), RUNTIME, CACHE]) {
    try {
      const req = createRequire(path.join(base, '__resolve__.js'));
      const pkg = req.resolve('playwright/package.json');
      // Point the runtime's Playwright at its own Chromium before it loads.
      if (base === RUNTIME && existsSync(path.join(RUNTIME, 'ms-playwright'))) process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(RUNTIME, 'ms-playwright');
      const mod = await import(pathToFileURL(req.resolve('playwright')).href);
      return { pw: mod.default ?? mod, dir: path.dirname(pkg) };
    } catch {}
  }
  console.error(`[shoot] Playwright not found - installing it once into ${CACHE} ...`);
  await fs.mkdir(CACHE, { recursive: true });
  const pj = path.join(CACHE, 'package.json');
  if (!existsSync(pj)) await fs.writeFile(pj, '{ "name": "killer-design-tools", "private": true }\n');
  execSync('npm install --prefer-offline --no-audit --no-fund --loglevel=error playwright', { cwd: CACHE, stdio: 'inherit' });
  const req = createRequire(path.join(CACHE, '__resolve__.js'));
  const mod = await import(pathToFileURL(req.resolve('playwright')).href);
  return { pw: mod.default ?? mod, dir: path.dirname(req.resolve('playwright/package.json')) };
}

async function launch({ pw, dir }) {
  try {
    return await pw.chromium.launch();
  } catch (e) {
    if (!/Executable doesn't exist|playwright install/i.test(String(e.message))) throw e;
    console.error('[shoot] Downloading Chromium for Playwright (one-time) ...');
    execFileSync(process.execPath, [path.join(dir, 'cli.js'), 'install', 'chromium'], { stdio: 'inherit' });
    return await pw.chromium.launch();
  }
}

// ---------- local static server ----------
const MIME = {
  '.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.json': 'application/json',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.webp': 'image/webp', '.avif': 'image/avif', '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2',
  '.ttf': 'font/ttf', '.otf': 'font/otf', '.txt': 'text/plain; charset=utf-8', '.xml': 'application/xml',
  '.webmanifest': 'application/manifest+json', '.mp4': 'video/mp4', '.webm': 'video/webm',
};

function serve(root) {
  return new Promise((resolve) => {
    const srv = http.createServer(async (req, res) => {
      try {
        let p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
        let fp = path.normalize(path.join(root, p));
        if (!fp.startsWith(path.normalize(root))) { res.writeHead(403); return res.end(); }
        if (existsSync(fp) && statSync(fp).isDirectory()) fp = path.join(fp, 'index.html');
        if (!existsSync(fp) && !path.extname(fp) && existsSync(fp + '.html')) fp += '.html';
        const body = await fs.readFile(fp);
        res.writeHead(200, { 'content-type': MIME[path.extname(fp).toLowerCase()] || 'application/octet-stream' });
        res.end(body);
      } catch {
        res.writeHead(404, { 'content-type': 'text/plain' });
        res.end('not found');
      }
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

// ---------- in-page checks (runs inside the browser) ----------
function pageChecks(o) {
  const out = {};
  const vw = document.documentElement.clientWidth;
  const css = (el, pseudo) => getComputedStyle(el, pseudo);
  const sel = (el) => {
    const parts = [];
    for (let e = el, d = 0; e && e.nodeType === 1 && d < 4; e = e.parentElement, d++) {
      let s = e.tagName.toLowerCase();
      if (e.id) { parts.unshift(`${s}#${e.id}`); break; }
      const c = [...e.classList].slice(0, 2);
      if (c.length) s += '.' + c.join('.');
      parts.unshift(s);
    }
    return parts.join(' > ');
  };
  const shown = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = css(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const snippet = (t) => t.replace(/\s+/g, ' ').trim().slice(0, 50);
  // Elements driven by a scroll or view timeline are mid-animation by design when checked from the top of the
  // page, so judge them as they look once scrolled into view (full opacity).
  const scrollDriven = (el) => {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      if (e.getAnimations && e.getAnimations().some((a) => a.timeline && a.timeline !== document.timeline)) return true;
    }
    return false;
  };
  const effOpacity = (el) => {
    if (scrollDriven(el)) return 1;
    let o2 = 1;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) o2 *= parseFloat(css(e).opacity);
    return o2;
  };

  // Colors: let the browser resolve any CSS color (oklch, color-mix, ...) by painting one pixel.
  const cvs = document.createElement('canvas');
  cvs.width = cvs.height = 1;
  const ctx = cvs.getContext('2d', { willReadFrequently: true });
  const cache = new Map();
  const rgba = (c) => {
    if (cache.has(c)) return cache.get(c);
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = '#000';
    ctx.fillStyle = c;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    const v = { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
    cache.set(c, v);
    return v;
  };
  const over = (top, under) => {
    const a = top.a + under.a * (1 - top.a);
    if (a === 0) return { r: 0, g: 0, b: 0, a: 0 };
    const mix = (k) => (top[k] * top.a + under[k] * under.a * (1 - top.a)) / a;
    return { r: mix('r'), g: mix('g'), b: mix('b'), a };
  };
  const lum = ({ r, g, b }) => {
    const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const hex = ({ r, g, b }) => '#' + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');
  const darkCanvas = matchMedia('(prefers-color-scheme: dark)').matches && /dark/.test(css(document.documentElement).colorScheme);
  const canvasColor = darkCanvas ? { r: 18, g: 18, b: 18, a: 1 } : { r: 255, g: 255, b: 255, a: 1 };
  const backdrop = (el) => {
    const layers = [];
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const s = css(e);
      const c = rgba(s.backgroundColor);
      if (c.a > 0) layers.push(c);
      if (s.backgroundImage && s.backgroundImage !== 'none' && layers.reduce((acc, l) => acc + l.a, 0) < 0.99) return null;
      if (c.a >= 0.99) break;
    }
    let col = canvasColor;
    for (let i = layers.length - 1; i >= 0; i--) col = over(layers[i], col);
    return col;
  };

  // --- overflow ---
  const sw = document.documentElement.scrollWidth;
  const rootClip = ['hidden', 'clip'].includes(css(document.documentElement).overflowX) || ['hidden', 'clip'].includes(css(document.body).overflowX);
  const beyond = [...document.body.querySelectorAll('*')].filter((el) => {
    if (!shown(el)) return false;
    const r = el.getBoundingClientRect();
    return r.right > vw + 1 || r.left < -1;
  });
  const leafmost = beyond.filter((el) => !beyond.some((o2) => o2 !== el && el.contains(o2)));
  if (sw > vw + 1) {
    out.horizontalOverflow = { scrollWidth: sw, viewport: vw, culprits: leafmost.slice(0, 6).map((el) => ({ el: sel(el), right: Math.round(el.getBoundingClientRect().right) })) };
  }
  if (rootClip) {
    const clippedText = leafmost.filter((el) => el.textContent.trim().length > 0 && !el.closest('[aria-hidden="true"]'));
    if (clippedText.length) out.textCutOff = clippedText.slice(0, 6).map((el) => ({ el: sel(el), text: snippet(el.textContent) }));
  }

  // --- document basics ---
  const metaVp = document.querySelector('meta[name="viewport"]');
  out.doc = {
    title: document.title.trim(),
    lang: document.documentElement.getAttribute('lang') || '',
    metaDescription: !!document.querySelector('meta[name="description"][content]'),
    viewportMeta: !!(metaVp && /width\s*=\s*device-width/.test(metaVp.content)),
    favicon: !!document.querySelector('link[rel~="icon"]'),
    ogImage: !!document.querySelector('meta[property="og:image"]'),
    h1Count: document.querySelectorAll('h1').length,
    pageHeight: document.documentElement.scrollHeight,
  };
  const heads = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(shown);
  out.headingSkips = [];
  heads.forEach((h, i) => {
    const lv = +h.tagName[1];
    const prev = i ? +heads[i - 1].tagName[1] : 0;
    if (lv > prev + 1 && prev !== 0) out.headingSkips.push(`h${prev} -> h${lv} "${snippet(h.textContent)}"`);
  });
  out.headingOutline = heads.slice(0, 30).map((h) => `${h.tagName.toLowerCase()}: ${snippet(h.textContent)}`);

  // --- placeholder copy and dead links ---
  const bodyText = document.body.innerText || '';
  out.placeholderCopy = (bodyText.match(/lorem ipsum|dolor sit amet|your (company|tagline|headline) here|placeholder text/gi) || []).slice(0, 5);
  out.deadLinks = [...document.querySelectorAll('a[href="#"], a[href=""], a:not([href])')].filter((a) => shown(a) && !a.getAttribute('role')).map((a) => snippet(a.textContent) || sel(a)).slice(0, 10);

  // --- images ---
  const imgs = [...document.images];
  out.brokenImages = imgs.filter((i) => i.complete && i.naturalWidth === 0 && i.getAttribute('src')).map((i) => i.getAttribute('src')).slice(0, 10);
  out.imgMissingAlt = imgs.filter((i) => !i.hasAttribute('alt')).map((i) => i.getAttribute('src') || sel(i)).slice(0, 10);

  // --- names for interactive things ---
  const named = (el) => {
    const t = (el.getAttribute('aria-label') || '').trim() || (el.textContent || '').trim() || (el.getAttribute('title') || '').trim();
    if (t) return true;
    const lb = el.getAttribute('aria-labelledby');
    if (lb && lb.split(/\s+/).some((id) => document.getElementById(id)?.textContent.trim())) return true;
    return [...el.querySelectorAll('img[alt], svg title, [aria-label]')].some((n) => (n.getAttribute?.('alt') || n.getAttribute?.('aria-label') || n.textContent || '').trim());
  };
  out.unnamedControls = [...document.querySelectorAll('a[href], button, [role="button"]')].filter((el) => shown(el) && !named(el)).map(sel).slice(0, 10);
  const fields = [...document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea')].filter(shown);
  out.unlabeledFields = [];
  out.placeholderOnlyFields = [];
  for (const f of fields) {
    const has = (f.labels && f.labels.length) || f.getAttribute('aria-label') || f.getAttribute('aria-labelledby') || f.getAttribute('title');
    if (!has) (f.getAttribute('placeholder') ? out.placeholderOnlyFields : out.unlabeledFields).push(sel(f));
  }

  // --- text: contrast, size, fonts, line length, stuck-invisible ---
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const contrastFails = [];
  let checked = 0, unknownBg = 0, invisible = 0;
  const invisibleEx = [];
  const tiny = [];
  const fams = new Map();
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement;
    if (!el || seen.has(el) || !n.textContent.trim()) continue;
    if (el.closest('script,style,noscript,template,svg')) continue;
    seen.add(el);
    if (!shown(el)) continue;
    if (el.closest('[aria-hidden="true"]')) continue;
    const s = css(el);
    const op = effOpacity(el);
    if (op < 0.05) {
      invisible++;
      if (invisibleEx.length < 5) invisibleEx.push(snippet(n.textContent));
      continue;
    }
    const size = parseFloat(s.fontSize);
    const fam = s.fontFamily.split(',')[0].replace(/["']/g, '').trim();
    fams.set(fam, (fams.get(fam) || 0) + 1);
    if (size < 12 && tiny.length < 8) tiny.push({ text: snippet(n.textContent), px: size, el: sel(el) });
    const fg0 = rgba(s.color);
    if (fg0.a === 0 || s.webkitTextFillColor === 'transparent' || /text/.test(s.backgroundClip || s.webkitBackgroundClip || '')) { unknownBg++; continue; }
    const bg = backdrop(el);
    if (!bg) { unknownBg++; continue; }
    const fg = over({ ...fg0, a: fg0.a * op }, bg);
    const cr = ratio(fg, bg);
    checked++;
    const w = parseInt(s.fontWeight, 10) || 400;
    const large = size >= 24 || (size >= 18.66 && w >= 700);
    const need = large ? 3 : 4.5;
    if (cr < need) contrastFails.push({ text: snippet(n.textContent), ratio: +cr.toFixed(2), need, fg: hex(fg), bg: hex(bg), px: size, el: sel(el) });
  }
  contrastFails.sort((a, b) => a.ratio - b.ratio);
  out.contrast = { checked, unknownBackground: unknownBg, fails: contrastFails.length, worst: contrastFails.slice(0, 10) };
  out.invisibleText = invisible ? { count: invisible, examples: invisibleEx } : null;
  out.tinyText = tiny;

  const faces = [...document.fonts];
  out.fonts = [...fams.entries()].sort((a, b) => b[1] - a[1]).map(([family, uses]) => {
    const f = faces.filter((x) => x.family.replace(/["']/g, '') === family);
    const status = !f.length ? 'system/local' : f.some((x) => x.status === 'loaded') ? 'web font loaded' : 'web font NOT loaded (fallback showing)';
    return { family, uses, status };
  });

  if (o.measureLines) {
    const cpl = [];
    for (const p of document.querySelectorAll('p, li, dd, blockquote')) {
      const t = p.textContent.replace(/\s+/g, ' ').trim();
      if (t.length < 160 || !shown(p) || p.querySelector('p, li, div')) continue;
      const s = css(p);
      const lh = s.lineHeight === 'normal' ? parseFloat(s.fontSize) * 1.2 : parseFloat(s.lineHeight);
      const lines = Math.max(1, Math.round(p.getBoundingClientRect().height / lh));
      if (lines < 3) continue;
      cpl.push({ cpl: Math.round(t.length / lines), text: snippet(t), el: sel(p) });
    }
    const sorted = cpl.map((c) => c.cpl).sort((a, b) => a - b);
    out.lineLength = {
      paragraphs: cpl.length,
      medianCharsPerLine: sorted.length ? sorted[Math.floor(sorted.length / 2)] : null,
      tooLong: cpl.filter((c) => c.cpl > 90).slice(0, 5),
    };
  }

  if (o.isMobile) {
    out.smallTapTargets = [...document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, summary, [role="button"]')]
      .filter((el) => {
        if (!shown(el)) return false;
        const r = el.getBoundingClientRect();
        if (r.width >= 23.5 && r.height >= 23.5) return false; // half-pixel tolerance for sub-pixel layout
        const inlineInText = el.tagName === 'A' && css(el).display === 'inline' && el.parentElement &&
          el.parentElement.textContent.trim().length > el.textContent.trim().length + 15;
        return !inlineInText;
      })
      .slice(0, 10)
      .map((el) => { const r = el.getBoundingClientRect(); return { el: sel(el), text: snippet(el.textContent || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.value || '') || el.tagName.toLowerCase(), size: `${Math.round(r.width)}x${Math.round(r.height)}` }; });
  }
  return out;
}

// Snapshot of everything that could make focus visible, for before/after comparison.
function focusSnapshotSetup() {
  const shown = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden'; };
  const snap = (el) => {
    const take = (p) => { const s = getComputedStyle(el, p); return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow, s.backgroundColor, s.color, s.textDecorationLine, s.textDecorationThickness, s.borderColor, s.transform, s.opacity, p ? s.content : ''].join('|'); };
    return take() + '#' + take('::before') + '#' + take('::after');
  };
  window.__snap = snap;
  window.__base = new Map();
  const els = [...document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, summary, [tabindex]:not([tabindex="-1"])')];
  for (const el of els) if (shown(el) || el.matches('a[href^="#"]')) window.__base.set(el, snap(el));
  document.activeElement && document.activeElement.blur && document.activeElement.blur();
  window.scrollTo(0, 0);
  return window.__base.size;
}
function focusSnapshotCurrent() {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const base = window.__base.get(el) ?? (() => { el.blur(); const b = window.__snap(el); el.focus(); return b; })();
  const now = window.__snap(el);
  let s = el.tagName.toLowerCase();
  if (el.id) s += '#' + el.id; else if (el.classList.length) s += '.' + [...el.classList].slice(0, 2).join('.');
  return { el: s, text: (el.textContent || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 40), visible: base !== now };
}

// ---------- motion capture ----------
// Still screenshots can't show an entrance, a pointer effect or a hover. This records them as frames and lays
// the frames out on one contact sheet, so a single image shows what the first seconds feel like.
async function captureMotion(browser, width, scheme, outDir, label, url) {
  const mobile = width < 600;
  const height = mobile ? 844 : width < 1100 ? 1024 : 900;
  const base = { viewport: { width, height }, deviceScaleFactor: 1, isMobile: mobile, hasTouch: mobile, colorScheme: scheme };
  const frames = [];
  const ctx = await browser.newContext(base);
  const page = await ctx.newPage();
  const t0 = Date.now();
  await page.goto(url, { waitUntil: 'commit', timeout: 45000 });
  for (const t of [150, 400, 700, 1100, 1700, 2600, 4000]) {
    const wait = t - (Date.now() - t0);
    if (wait > 0) await page.waitForTimeout(wait);
    frames.push({ kind: 'time', label: `${Date.now() - t0} ms after navigation`, buf: await page.screenshot({ type: 'jpeg', quality: 80 }) });
  }
  const lcp = await page.evaluate(() => new Promise((resolve) => {
    let last = null;
    try { new PerformanceObserver((l) => { const e = l.getEntries(); if (e.length) last = e[e.length - 1].startTime; }).observe({ type: 'largest-contentful-paint', buffered: true }); } catch {}
    setTimeout(() => resolve(last === null ? null : Math.round(last)), 300);
  }));
  if (!mobile) {
    for (const [fx, fy, name] of [[0.2, 0.35, 'pointer at left'], [0.5, 0.55, 'pointer at centre'], [0.8, 0.3, 'pointer at right']]) {
      await page.mouse.move(width * fx, height * fy, { steps: 10 });
      await page.waitForTimeout(400);
      frames.push({ kind: 'pointer', label: name, buf: await page.screenshot({ type: 'jpeg', quality: 80 }) });
    }
    const box = await page.evaluate(() => {
      const el = [...document.querySelectorAll('main a[href], main button, header a[href], button, a[href]')].find((e) => {
        const r = e.getBoundingClientRect();
        return r.width > 40 && r.height > 20 && r.top >= 0 && r.bottom <= innerHeight && getComputedStyle(e).visibility !== 'hidden';
      });
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2, text: (el.textContent || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 30) };
    });
    if (box) {
      await page.mouse.move(box.x, box.y, { steps: 6 });
      await page.waitForTimeout(400);
      frames.push({ kind: 'hover', label: `hovering "${box.text}"`, buf: await page.screenshot({ type: 'jpeg', quality: 80 }) });
    }
  }
  await ctx.close();
  const rctx = await browser.newContext({ ...base, reducedMotion: 'reduce' });
  const rpage = await rctx.newPage();
  await rpage.goto(url, { waitUntil: 'load', timeout: 45000 });
  await rpage.waitForTimeout(1200);
  frames.push({ kind: 'reduced', label: 'prefers-reduced-motion: reduce', buf: await rpage.screenshot({ type: 'jpeg', quality: 80 }) });
  await rctx.close();

  const same = (a, b) => a.buf.equals(b.buf);
  const timeFrames = frames.filter((f) => f.kind === 'time');
  const pointerFrames = frames.filter((f) => f.kind === 'pointer');
  const firstChange = timeFrames.findIndex((f, i) => i > 0 && !same(f, timeFrames[i - 1]));
  const lastChange = timeFrames.reduce((acc, f, i) => (i > 0 && !same(f, timeFrames[i - 1]) ? i : acc), -1);
  const n = timeFrames.length;
  const result = {
    width, scheme, lcpMs: lcp,
    loadMotion: firstChange > 0,
    continuous: n > 2 && !same(timeFrames[n - 1], timeFrames[n - 2]),
    settlesBy: lastChange > 0 ? timeFrames[lastChange].label.split(' ')[0] + ' ms' : null,
    pointerResponse: pointerFrames.length > 1 ? pointerFrames.some((f, i) => i > 0 && !same(f, pointerFrames[i - 1])) : null,
  };

  const cols = mobile ? 4 : 3, cellW = mobile ? 260 : 460;
  const html = `<!doctype html><meta charset="utf-8"><style>body{margin:0;padding:16px;background:#1b1b1b;color:#ddd;font:14px system-ui,sans-serif}
h1{font-size:15px;margin:0 0 12px}div{display:grid;grid-template-columns:repeat(${cols},${cellW}px);gap:14px}figure{margin:0}
img{width:100%;display:block;outline:1px solid #555}figcaption{padding:5px 0 0}</style>
<h1>${label} @${width}${scheme === 'dark' ? ' dark' : ''}: motion contact sheet (frames left to right, top to bottom)</h1><div>` +
    frames.map((f) => `<figure><img src="data:image/jpeg;base64,${f.buf.toString('base64')}"><figcaption>${f.label}</figcaption></figure>`).join('') + '</div>';
  const sheet = await browser.newPage({ viewport: { width: cols * (cellW + 14) + 18, height: 600 } });
  await sheet.setContent(html, { waitUntil: 'load' });
  const file = path.join(outDir, `${label}-${width}${scheme === 'dark' ? '-dark' : ''}-motion.png`);
  await sheet.screenshot({ path: file, fullPage: true });
  await sheet.close();
  return { ...result, sheet: file };
}

// ---------- main ----------
const isUrl = /^https?:\/\//i.test(target);
let server, url, label;
if (isUrl) {
  url = target;
  label = opt.label || new URL(target).hostname.replace(/[^a-z0-9.-]/gi, '_');
} else {
  const abs = path.resolve(target);
  if (!existsSync(abs)) { console.error(`not found: ${abs}`); process.exit(1); }
  const isDir = statSync(abs).isDirectory();
  const root = isDir ? abs : path.dirname(abs);
  if (isDir && !existsSync(path.join(abs, 'index.html'))) { console.error(`no index.html in ${abs}`); process.exit(1); }
  server = await serve(root);
  url = `http://127.0.0.1:${server.address().port}/${isDir ? '' : encodeURIComponent(path.basename(abs))}`;
  const base = isDir ? path.basename(abs) : path.basename(abs, path.extname(abs));
  label = opt.label || (base === 'index' ? path.basename(root) : base);
}
label = label.replace(/[^a-z0-9._-]/gi, '_').slice(0, 40) || 'page';
// Default folder is keyed by label + a hash of the target, so two different sites that are both in a folder called site/ never share (and overwrite) a folder.
const key = createHash('sha1').update(isUrl ? target : path.resolve(target)).digest('hex').slice(0, 6);
const outDir = path.resolve(opt.out || path.join(os.tmpdir(), 'killer-design-shots', `${label}-${key}`));
await fs.mkdir(outDir, { recursive: true });
for (const f of await fs.readdir(outDir)) if (f.startsWith(label + '-') && /\.(png|jpe?g)$/.test(f)) await fs.rm(path.join(outDir, f));

const playwright = await loadPlaywright();
const browser = await launch(playwright);

const report = { target, url, generatedAt: new Date().toISOString(), outDir, runs: [] };
const schemes = opt.dark ? ['light', 'dark'] : ['light'];
const widest = Math.max(...opt.widths);

try {
  for (const scheme of schemes) {
    for (const width of opt.widths) {
      const mobile = width < 600;
      const height = mobile ? 844 : width < 1100 ? 1024 : 900;
      const ctxOpts = { viewport: { width, height }, deviceScaleFactor: mobile ? 2 : 1, isMobile: mobile, hasTouch: mobile, colorScheme: scheme };
      const context = await browser.newContext(ctxOpts);
      const page = await context.newPage();
      const consoleErrors = [], failed = [];
      page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
      page.on('pageerror', (e) => consoleErrors.push(String(e.message || e).slice(0, 200)));
      page.on('requestfailed', (r) => failed.push(`${r.failure()?.errorText || 'failed'} ${r.url().slice(0, 150)}`));
      page.on('response', (r) => { if (r.status() >= 400) failed.push(`${r.status()} ${r.url().slice(0, 150)}`); });

      await page.goto(url, { waitUntil: 'load', timeout: 45000 });
      // Sites with `scroll-behavior: smooth` would otherwise be captured mid-scroll (sticky headers end up halfway down the page).
      await page.addStyleTag({ content: 'html, body { scroll-behavior: auto !important; }' });
      await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
      await page.evaluate(() => document.fonts.ready.then(() => true));
      // Scroll through once so lazy images load and scroll-reveal effects fire, then return to the top.
      await page.evaluate(async () => {
        const step = Math.max(200, innerHeight * 0.7);
        for (let y = 0; y < document.documentElement.scrollHeight; y += step) { scrollTo(0, y); await new Promise((r) => setTimeout(r, 90)); }
        scrollTo(0, 0);
      });
      await page.waitForTimeout(opt.wait);
      // Let finite, time-based animations (entrances, typing effects) finish before checking, so text that is
      // still fading in isn't reported as invisible. Scroll-driven and infinite animations are skipped; capped at 6s.
      await page.evaluate(() => Promise.race([
        Promise.all(document.getAnimations()
          .filter((a) => a.timeline === document.timeline && Number.isFinite(a.effect?.getComputedTiming?.().endTime))
          .map((a) => a.finished.catch(() => {}))),
        new Promise((r) => setTimeout(r, 6000)),
      ]));

      const checks = await page.evaluate(pageChecks, { isMobile: mobile, measureLines: width === widest });

      if (width === widest && scheme === 'light') {
        const count = await page.evaluate(focusSnapshotSetup);
        const results = [];
        const seen = new Set();
        for (let i = 0; i < Math.min(count, 14); i++) {
          await page.keyboard.press('Tab');
          await page.waitForTimeout(120);
          const r = await page.evaluate(focusSnapshotCurrent);
          if (!r || seen.has(r.el + r.text)) continue;
          seen.add(r.el + r.text);
          results.push(r);
        }
        checks.focus = { tabbed: results.length, invisible: results.filter((r) => !r.visible) };
        await page.evaluate(() => { document.activeElement?.blur?.(); scrollTo(0, 0); });
        await page.waitForTimeout(150);
      }

      const tag = `${label}-${width}${scheme === 'dark' ? '-dark' : ''}`;
      const shots = [];
      if (opt.full) {
        const h = await page.evaluate(() => document.documentElement.scrollHeight);
        const limit = Math.floor(14000 / ctxOpts.deviceScaleFactor);
        const file = path.join(outDir, `${tag}-full.jpg`);
        await page.screenshot({ path: file, fullPage: true, type: 'jpeg', quality: 82, ...(h > limit ? { clip: { x: 0, y: 0, width, height: limit } } : {}) });
        shots.push(file);
      }
      if (opt.slices > 0) {
        const total = await page.evaluate(() => document.documentElement.scrollHeight);
        for (let i = 0; i < opt.slices; i++) {
          const y = i * height;
          if (y >= total) break;
          await page.evaluate((yy) => scrollTo(0, yy), y);
          await page.waitForTimeout(opt.wait);
          const file = path.join(outDir, `${tag}-${String(i + 1).padStart(2, '0')}.png`);
          await page.screenshot({ path: file });
          shots.push(file);
        }
        const covered = Math.min(total, opt.slices * height);
        checks.sliceCoverage = `${Math.round((covered / total) * 100)}% of page height`;
      }
      report.runs.push({ width, scheme, viewport: `${width}x${height}`, checks, consoleErrors: [...new Set(consoleErrors)].slice(0, 10), failedRequests: [...new Set(failed)].slice(0, 15), screenshots: shots });
      await context.close();
    }
  }
  if (opt.motion) {
    report.motion = [];
    const scheme = opt.dark ? 'dark' : 'light';
    for (const w of [...new Set([widest, 390])]) report.motion.push(await captureMotion(browser, w, scheme, outDir, label, url));
  }
} finally {
  await browser.close();
  server?.close();
}

// ---------- summary ----------
const grouped = { FAIL: new Map(), WARN: new Map(), INFO: new Map() };
// Messages that start with "@<width> " are merged across widths: "@390,768 page scrolls sideways ..."
const add = (lvl, msg) => {
  const m = msg.match(/^@(\d+(?: dark)?) ([\s\S]*)$/);
  const key = m ? m[2] : msg;
  if (!grouped[lvl].has(key)) grouped[lvl].set(key, []);
  if (m) grouped[lvl].get(key).push(m[1]);
};
for (const r of report.runs) {
  const c = r.checks;
  const at = `@${r.width}${r.scheme === 'dark' ? ' dark' : ''}`;
  if (c.horizontalOverflow) add('FAIL', `${at} page scrolls sideways (${c.horizontalOverflow.scrollWidth}px wide in a ${c.horizontalOverflow.viewport}px viewport). Culprits: ${c.horizontalOverflow.culprits.map((x) => x.el).join(' | ')}`);
  if (c.textCutOff) add('FAIL', `${at} text runs past the viewport edge (hidden by overflow-x on html/body): ${c.textCutOff.map((x) => `"${x.text}" (${x.el})`).join(' | ')}`);
  if (c.contrast.fails) add('FAIL', `${at} ${c.contrast.fails} text element(s) below WCAG AA contrast. Worst: ${c.contrast.worst.slice(0, 4).map((x) => `${x.ratio}:1 "${x.text}" ${x.fg} on ${x.bg}`).join(' | ')}`);
  if (c.brokenImages.length) add('FAIL', `broken images: ${c.brokenImages.join(', ')}`);
  if (c.invisibleText) add('WARN', `${at} ${c.invisibleText.count} text element(s) still invisible (opacity ~0) after scrolling the whole page - stuck reveal animation? e.g. "${c.invisibleText.examples[0]}"`);
  if (c.smallTapTargets?.length) add('FAIL', `${at} ${c.smallTapTargets.length} tap target(s) under 24x24px: ${c.smallTapTargets.slice(0, 5).map((x) => `${x.size} "${x.text}"`).join(' | ')}`);
  if (c.tinyText.length) add('WARN', `${at} text under 12px: ${c.tinyText.slice(0, 4).map((x) => `${x.px}px "${x.text}"`).join(' | ')}`);
  if (c.lineLength?.tooLong.length) add('WARN', `${at} paragraphs over ~90 characters per line (hard to read): ${c.lineLength.tooLong.map((x) => `${x.cpl}cpl "${x.text}"`).join(' | ')}`);
  if (c.focus?.invisible.length) add('FAIL', `no visible focus style on ${c.focus.invisible.length} of ${c.focus.tabbed} tabbed element(s): ${c.focus.invisible.slice(0, 5).map((x) => `${x.el} "${x.text}"`).join(' | ')}`);
  for (const f of c.fonts) if (/NOT loaded/.test(f.status)) add('FAIL', `font "${f.family}" is declared but did not load - visitors see a fallback`);
  if (c.unnamedControls.length) add('FAIL', `links/buttons with no accessible name: ${c.unnamedControls.join(' | ')}`);
  if (c.unlabeledFields.length) add('FAIL', `form fields without a label: ${c.unlabeledFields.join(' | ')}`);
  if (c.placeholderOnlyFields.length) add('WARN', `form fields labelled only by placeholder: ${c.placeholderOnlyFields.join(' | ')}`);
  if (c.imgMissingAlt.length) add('FAIL', `images without an alt attribute (use alt="" if decorative): ${c.imgMissingAlt.join(', ')}`);
  if (c.placeholderCopy.length) add('FAIL', `placeholder copy on the page: ${[...new Set(c.placeholderCopy.map((x) => x.toLowerCase()))].join(', ')}`);
  if (c.deadLinks.length) add('WARN', `${c.deadLinks.length} link(s) go nowhere (href="#" or missing): ${c.deadLinks.slice(0, 6).join(' | ')}`);
  if (c.headingSkips.length) add('WARN', `heading levels skip: ${c.headingSkips.slice(0, 4).join(' | ')}`);
  if (c.contrast.unknownBackground) add('INFO', `${at} ${c.contrast.unknownBackground} text element(s) sit on images/gradients/clipped text - check their contrast by eye`);
  const d = c.doc;
  if (!d.title) add('FAIL', 'missing <title>');
  if (!d.lang) add('FAIL', 'missing lang attribute on <html>');
  if (!d.viewportMeta) add('FAIL', 'missing <meta name="viewport" content="width=device-width, initial-scale=1"> - phones will render a shrunken desktop page');
  if (d.h1Count !== 1) add('WARN', `${d.h1Count} <h1> elements (expected 1)`);
  if (!d.metaDescription) add('WARN', 'missing meta description');
  if (!d.favicon) add('WARN', 'no favicon');
  if (!d.ogImage) add('INFO', 'no og:image (link previews will be plain)');
  for (const e of r.consoleErrors) add('FAIL', `console error: ${e}`);
  for (const e of r.failedRequests) add('FAIL', `request failed: ${e}`);
}
const first = report.runs[0]?.checks;
const wide = report.runs.find((r) => r.width === widest)?.checks;
if (first) add('INFO', `fonts in use: ${first.fonts.slice(0, 5).map((f) => `${f.family} (${f.status}, ${f.uses})`).join('; ')}`);
if (wide?.lineLength?.medianCharsPerLine) add('INFO', `median body line length @${widest}: ~${wide.lineLength.medianCharsPerLine} characters (comfortable: 45-80)`);
if (wide?.focus) add('INFO', `focus: tabbed through ${wide.focus.tabbed} element(s), ${wide.focus.tabbed - wide.focus.invisible.length} showed a visible change`);
for (const m of report.motion || []) {
  const at = `@${m.width}${m.scheme === 'dark' ? ' dark' : ''}`;
  if (m.lcpMs !== null && m.lcpMs > 2500) add('WARN', `${at} largest content painted at ${m.lcpMs} ms - the headline or hero arrives too late (check animation delays and font loading)`);
  add('INFO', `${at} motion: ${!m.loadMotion ? 'nothing moves on load' : m.continuous ? 'the first screen animates on load and keeps moving (continuous animation)' : `the first screen animates on load and settles by ~${m.settlesBy}`}; ${m.pointerResponse === null ? 'pointer not tested (touch)' : m.pointerResponse ? 'responds to the pointer' : 'no response to the pointer'}; LCP ${m.lcpMs ?? '?'} ms. Sheet: ${path.basename(m.sheet)}`);
}
for (const r of report.runs) add('INFO', `@${r.width}${r.scheme === 'dark' ? ' dark' : ''}: page height ${r.checks.doc.pageHeight}px, ${r.screenshots.length} screenshot(s)${r.checks.sliceCoverage ? `, slices cover ${r.checks.sliceCoverage}` : ''}`);

const lines = {};
for (const lvl of ['FAIL', 'WARN', 'INFO']) lines[lvl] = [...grouped[lvl]].map(([k, ws]) => (ws.length ? `@${ws.join(',')} ${k}` : k));
report.summary = lines;
await fs.writeFile(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));

console.log(`\nshots + report.json -> ${outDir}\n`);
for (const lvl of ['FAIL', 'WARN', 'INFO']) for (const m of lines[lvl]) console.log(`${lvl}  ${m}`);
if (!lines.FAIL.length) console.log('\nNo automated failures. That only means the basics hold - now look at the screenshots.');
console.log('\nScreenshots (read the -01 slices first; they are what people see on arrival):');
for (const r of report.runs) console.log(`  ${r.width}${r.scheme === 'dark' ? ' dark' : ''}: ${r.screenshots.map((s) => path.basename(s)).join(', ')}`);
if (report.motion?.length) console.log(`Motion contact sheets (read these to judge the entrance, pointer and hover effects): ${report.motion.map((m) => path.basename(m.sheet)).join(', ')}`);

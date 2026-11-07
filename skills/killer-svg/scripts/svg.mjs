#!/usr/bin/env node
/**
 * svg.mjs - check hand-written SVG graphics in real browser engines, and keep the books for a killer-svg run.
 *
 *   node svg.mjs <command> [options]          node svg.mjs <command> --help shows a command's options
 *
 * Commands
 *   init --project <folder> --kind <kind> --brief "..." [--reads "..." ...] [--files <svg> ...]
 *                                         start a run in ~/.claude/killer-svg/<project>/<date>-<slug>/
 *   add -w <run> <svg or folder> ...      add graphics to the run (logo-dark.svg@dark: only on dark)
 *   lint <svg> ... | lint -w <run>        static checks, no browser: XML, viewBox, scripts, outside
 *                                         references, fonts, ids, metadata, decimals, size, title, motion
 *   render -w <run>                       every file at every size, on each background and a checkerboard,
 *                                         through <img>, in each browser engine Playwright can launch
 *   engines -w <run>                      compare the engines' renders pixel by pixel
 *   contrast -w <run>                     how much of each graphic stands out on each background
 *   github <svg> | github -w <run>        render as an <img> under a strict CSP and opened directly;
 *                                         a difference, or anything it loads, fails
 *   motion <svg> | motion -w <run>        frames over time with and without reduced motion
 *   minify <svg> [--out f] [--decimals 2] a smaller file, written only if it renders the same
 *   export <svg> --png 16,32,180,512 [--out dir]   transparent PNGs through Chromium
 *   check -w <run>                        lint, render, engines, contrast, github and motion as they
 *                                         apply; writes check.json; exits 1 on any FAIL
 *   sheet -w <run>                        one overview image of every file, size and background
 *   audience -w <run>                     renders and a brief for a blind viewer agent
 *   heard -w <run>                        read the viewer's reply; write the matcher's brief
 *   landed -w <run>                       read the matcher's reply: which reads landed
 *   land -w <run> -m "..." [--branch b]   commit the graphics on a local branch (never pushes)
 *   decide -w <run> "<text>"              record a call the user should check
 *   wait -w <run> "<text>" --command "c"  record an outward action left for the user's OK
 *   report -w <run>                       write report.md and summary.json
 *
 * Exit codes: 0 fine, 1 a check failed, 2 a usage or setup error.
 *
 * Environment: KILLER_SVG_HOME moves the runs (default ~/.claude/killer-svg); KILLER_SVG_SANDBOXES
 * moves the viewer folders (default: the temp folder); KILLER_SKILLS_CACHE moves the packs (default
 * ~/.cache); KILLER_SVG_NO_INSTALL=1 never installs Playwright or Chromium and fails instead;
 * KILLER_SVG_BROWSERS lists the folders (separated like PATH) to look in for Playwright's Firefox
 * and WebKit builds, instead of Playwright's own caches.
 *
 * Needs Node 20+. Playwright comes from the current project, then the killer-skills runtime in
 * ~/.cache/killer-skills, then killer-design's cache, then ~/.cache/killer-svg, where the first run
 * installs it if nothing else has it. Chromium is required. Firefox and WebKit are used when this
 * Playwright's own builds of them are installed, and reported "not checked" when they aren't.
 */
import { createRequire } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { execSync, execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs/promises';
import { existsSync, statSync, readFileSync, readdirSync, realpathSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { createHash, randomBytes, randomInt } from 'node:crypto';
import zlib from 'node:zlib';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SKILL = path.resolve(HERE, '..');
const BRIEFS = path.join(SKILL, 'references', 'briefs.md');
// Read before Playwright is loaded: the runtime's copy points PLAYWRIGHT_BROWSERS_PATH at its own folder.
const USER_BROWSERS_PATH = process.env.PLAYWRIGHT_BROWSERS_PATH || '';

const SVG_NS = 'http://www.w3.org/2000/svg';
const XLINK_NS = 'http://www.w3.org/1999/xlink';
const XML_NS = 'http://www.w3.org/XML/1998/namespace';
const XMLNS_NS = 'http://www.w3.org/2000/xmlns/';
const KINDS = ['logo', 'icon', 'icons', 'diagram', 'illustration', 'banner', 'card', 'animation'];
const KIND_PHRASE = {
  logo: ['a logo or mark', 'it'], icon: ['an icon', 'it'], icons: ['a set of icons', 'them'], diagram: ['a diagram', 'it'],
  illustration: ['an illustration', 'it'], banner: ['a banner', 'it'], card: ['a social card', 'it'], animation: ['an animated graphic', 'it'],
};
const TARGETS = ['web', 'github', 'app', 'print'];
const ENGINES = ['chromium', 'firefox', 'webkit'];
const TITLE = { chromium: 'Chromium', firefox: 'Firefox', webkit: 'WebKit' };
const NAMED_BG = { light: '#ffffff', dark: '#0d1117' };
const CHECKER = 'repeating-conic-gradient(#d6d6d6 0 25%, #ffffff 0 50%) 0 0 / 16px 16px';
// Bytes a file of each kind should need; more is a warning, not a failure.
const BUDGET = { icon: 2048, icons: 2048, logo: 8192, diagram: 40960, illustration: 81920, banner: 61440, card: 102400, animation: 30720 };
const SMALL_VARIANT_UP_TO = 32;
const LIMITS = {
  tol: 64,                      // per channel, of 255: closer than this, two pixels count as the same colour
  enginesFailShare: 0.10, enginesWarnShare: 0.03, enginesFailMean: 40, enginesWarnMean: 16, enginesMinPixels: 4,
  sameShare: 0.002, sameMinPixels: 3, sameMean: 3,   // the same engine twice (github, minify): near-identical
  blendRatio: 1.5,              // a pixel within 1.5:1 of the background melts into it
  vanishShare: 0.9, fadeShare: 0.5, fullBleed: 0.9,
  graphicRatio: 3, weakShare: 0.2,   // a colour covering a fifth of the graphic, under WCAG's 3:1 for graphics
};
const GENERIC = new Set(['serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui', 'ui-serif', 'ui-sans-serif',
  'ui-monospace', 'ui-rounded', 'math', 'emoji', 'fangsong', '-apple-system', 'blinkmacsystemfont']);
const EDITOR_NS = [/^http:\/\/www\.inkscape\.org\//, /^http:\/\/sodipodi\.sourceforge\.net\//, /^http:\/\/ns\.adobe\.com\//,
  /^http:\/\/www\.bohemiancoding\.com\/sketch/, /^http:\/\/www\.serif\.com\//, /^http:\/\/www\.figma\.com\//,
  /^http:\/\/purl\.org\/dc\//, /^http:\/\/creativecommons\.org\/ns/, /^http:\/\/www\.w3\.org\/1999\/02\/22-rdf-syntax-ns/];
const SMIL = new Set(['animate', 'animateTransform', 'animateMotion', 'animateColor', 'set']);
const TEXT_EL = new Set(['text', 'tspan', 'textPath']);
const KEEP_SPACE = new Set(['text', 'tspan', 'textPath', 'title', 'desc', 'style', 'script', 'metadata']);
// Attributes whose numbers minify may round. Others (ids, classes, colours, keyTimes) are left alone.
const NUMERIC_ATTRS = new Set(['d', 'points', 'viewBox', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'fx', 'fy', 'fr',
  'width', 'height', 'transform', 'gradientTransform', 'patternTransform', 'stroke-width', 'stroke-dashoffset', 'stroke-dasharray',
  'stroke-miterlimit', 'offset', 'opacity', 'fill-opacity', 'stroke-opacity', 'stop-opacity', 'font-size', 'letter-spacing',
  'word-spacing', 'dx', 'dy', 'refX', 'refY', 'markerWidth', 'markerHeight', 'stdDeviation', 'pathLength']);

class Fail extends Error {
  constructor(msg, code = 2) { super(msg); this.code = code; }
}
const fail = (msg, code = 2) => { throw new Fail(msg, code); };

// ---------------------------------------------------------------- small helpers

const pad2 = (n) => String(n).padStart(2, '0');
const today = () => { const d = new Date(); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; };
const now = () => { const d = new Date(); return `${today()}T${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`; };
const slugify = (s, dflt = 'svg') => (String(s || '').toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40).replace(/-+$/, '')) || dflt;
const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;
const snippet = (t, n = 60) => { const s = String(t || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const slash = (p) => String(p).split(path.sep).join('/');
const pct = (x) => `${Math.round(x * 100)}%`;
const list = (xs) => (xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);
const kb = (n) => (n < 1024 ? `${n} bytes` : `${(n / 1024).toFixed(1)} KB`);
const cacheRoot = () => process.env.KILLER_SKILLS_CACHE || path.join(os.homedir(), '.cache');
const expandHome = (p) => p.replace(/^~(?=$|[\\/])/, os.homedir());
const runsHome = () => path.resolve(expandHome(process.env.KILLER_SVG_HOME || path.join(os.homedir(), '.claude', 'killer-svg')));
const sandboxesHome = () => path.join(path.resolve(expandHome(process.env.KILLER_SVG_SANDBOXES || os.tmpdir())), 'ksv');

function loadJson(file, dflt) {
  if (!existsSync(file)) return dflt;
  return JSON.parse(readFileSync(file, 'utf8').replace(/^﻿/, ''));
}
async function saveJson(file, data) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.${randomBytes(2).toString('hex')}.tmp`;
  await fs.writeFile(tmp, JSON.stringify(data, null, 2) + '\n', 'utf8');
  for (let i = 0; ; i++) {
    try { await fs.rename(tmp, file); return; } catch (e) {
      // Windows refuses the swap while another process has the file open.
      if (i >= 50 || !['EPERM', 'EACCES', 'EBUSY'].includes(e.code)) throw e;
      await new Promise((r) => setTimeout(r, 100));
    }
  }
}

// One spelling of a path, so path.relative can compare two: Windows 8.3 short names (RUNNER~1 for
// runneradmin) and symlinks (macOS's /var is /private/var) resolved, as git spells its top folder. A path
// that doesn't exist yet keeps its missing tail.
export function canonical(p) {
  let head = path.resolve(p);
  const tail = [];
  while (!existsSync(head)) {
    const up = path.dirname(head);
    if (up === head) return path.resolve(p);
    tail.unshift(path.basename(head));
    head = up;
  }
  return path.join(realpathSync.native(head), ...tail);
}
const inside = (child, parent) => { const r = path.relative(canonical(parent), canonical(child)); return r === '' || (!r.startsWith('..') && !path.isAbsolute(r)); };

function git(cwd, args, opts = {}) {
  const r = spawnSync('git', args, { cwd, encoding: 'utf8', ...opts });
  if (r.error) return { ok: false, out: '', err: String(r.error.message) };
  return { ok: r.status === 0, out: r.stdout || '', err: r.stderr || '' };
}
function gitTop(dir) {
  let near = path.resolve(dir);
  while (!existsSync(near) && path.dirname(near) !== near) near = path.dirname(near);
  const r = git(near, ['rev-parse', '--show-toplevel']);
  return r.ok ? canonical(r.out.trim()) : null;
}

// The same <project> folder name as the Python skills' kit.project_key and tools/shared/handoff.mjs,
// so killer-goal finds the run.
export function projectKey(top, isGit) {
  let ident = isGit ? git(top, ['config', '--get', 'remote.origin.url']).out.trim() : '';
  ident = ident || (process.platform === 'win32' ? top.toLowerCase() : top);
  const slug = path.basename(top).replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'project';
  return `${slug}-${createHash('sha1').update(ident).digest('hex').slice(0, 8)}`;
}

// ---------------------------------------------------------------- PNG and colour (no dependencies)

/** Decode a PNG (8-bit greyscale, RGB, palette, grey+alpha or RGBA; not interlaced) to RGBA pixels. */
export function decodePng(buf) {
  if (buf.length < 8 || buf.readUInt32BE(0) !== 0x89504e47 || buf.readUInt32BE(4) !== 0x0d0a1a0a) throw new Error('not a PNG');
  let pos = 8, width = 0, height = 0, depth = 0, ctype = 0, interlace = 0, palette = null, trns = null;
  const idat = [];
  while (pos + 8 <= buf.length) {
    const len = buf.readUInt32BE(pos), type = buf.toString('latin1', pos + 4, pos + 8);
    const body = buf.subarray(pos + 8, pos + 8 + len);
    if (type === 'IHDR') { width = body.readUInt32BE(0); height = body.readUInt32BE(4); depth = body[8]; ctype = body[9]; interlace = body[12]; }
    else if (type === 'PLTE') palette = body;
    else if (type === 'tRNS') trns = body;
    else if (type === 'IDAT') idat.push(body);
    else if (type === 'IEND') break;
    pos += 12 + len;
  }
  if (!width || !height) throw new Error('PNG has no IHDR');
  if (depth !== 8) throw new Error(`PNG bit depth ${depth} isn't supported (only 8)`);
  if (interlace) throw new Error("interlaced PNGs aren't supported");
  const channels = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[ctype];
  if (!channels) throw new Error(`PNG colour type ${ctype} isn't supported`);
  if (ctype === 3 && !palette) throw new Error('palette PNG without a PLTE chunk');
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const bpp = channels, stride = width * channels;
  if (raw.length < height * (stride + 1)) throw new Error('PNG data is short');
  const pix = new Uint8Array(height * stride);
  for (let y = 0; y < height; y++) {
    const f = raw[y * (stride + 1)];
    const src = y * (stride + 1) + 1, dst = y * stride, up = dst - stride;
    if (f === 0) { pix.set(raw.subarray(src, src + stride), dst); continue; }
    if (f === 1) {
      for (let x = 0; x < stride; x++) pix[dst + x] = (raw[src + x] + (x >= bpp ? pix[dst + x - bpp] : 0)) & 255;
    } else if (f === 2) {
      for (let x = 0; x < stride; x++) pix[dst + x] = (raw[src + x] + (y ? pix[up + x] : 0)) & 255;
    } else if (f === 3) {
      for (let x = 0; x < stride; x++) pix[dst + x] = (raw[src + x] + (((x >= bpp ? pix[dst + x - bpp] : 0) + (y ? pix[up + x] : 0)) >> 1)) & 255;
    } else if (f === 4) {
      for (let x = 0; x < stride; x++) {
        const a = x >= bpp ? pix[dst + x - bpp] : 0, b = y ? pix[up + x] : 0, c = x >= bpp && y ? pix[up + x - bpp] : 0;
        const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        pix[dst + x] = (raw[src + x] + (pa <= pb && pa <= pc ? a : pb <= pc ? b : c)) & 255;
      }
    } else throw new Error(`bad PNG filter ${f} on row ${y}`);
  }
  if (ctype === 6) return { width, height, data: pix };
  const data = new Uint8Array(width * height * 4);
  const key = trns && ctype === 2 && trns.length >= 6 ? [trns.readUInt16BE(0), trns.readUInt16BE(2), trns.readUInt16BE(4)]
    : trns && ctype === 0 && trns.length >= 2 ? trns.readUInt16BE(0) : null;
  for (let i = 0, j = 0, o = 0; i < width * height; i++, j += channels, o += 4) {
    if (ctype === 2) {
      data[o] = pix[j]; data[o + 1] = pix[j + 1]; data[o + 2] = pix[j + 2];
      data[o + 3] = key && pix[j] === key[0] && pix[j + 1] === key[1] && pix[j + 2] === key[2] ? 0 : 255;
    } else if (ctype === 3) {
      const k = pix[j];
      data[o] = palette[k * 3]; data[o + 1] = palette[k * 3 + 1]; data[o + 2] = palette[k * 3 + 2];
      data[o + 3] = trns && k < trns.length ? trns[k] : 255;
    } else if (ctype === 4) {
      data[o] = data[o + 1] = data[o + 2] = pix[j]; data[o + 3] = pix[j + 1];
    } else {
      data[o] = data[o + 1] = data[o + 2] = pix[j]; data[o + 3] = key !== null && pix[j] === key ? 0 : 255;
    }
  }
  return { width, height, data };
}

const CRC = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  return t;
})();
function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC[(c ^ buf[i]) & 255] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
/** Encode RGBA pixels as a PNG (no text chunks, so nothing rides along in the file). */
export function encodePng({ width, height, data }) {
  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y++) Buffer.from(data.buffer, data.byteOffset + y * stride, stride).copy(raw, y * (stride + 1) + 1);
  const chunk = (type, body) => {
    const head = Buffer.alloc(8);
    head.writeUInt32BE(body.length, 0);
    head.write(type, 4, 'latin1');
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), body])), 0);
    return Buffer.concat([head, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

export function crop(img, x, y, w, h) {
  const out = new Uint8Array(w * h * 4);
  for (let r = 0; r < h; r++) {
    const s = ((y + r) * img.width + x) * 4;
    out.set(img.data.subarray(s, s + w * 4), r * w * 4);
  }
  return { width: w, height: h, data: out };
}

/** WCAG relative luminance of an sRGB colour with 0-255 channels. */
export function luminance({ r, g, b }) {
  const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
/** WCAG contrast ratio between two colours, 1 to 21. */
export function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
export const hex = ({ r, g, b }) => '#' + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');
export function parseHex(s) {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(s).trim());
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].split('').map((c) => c + c).join('') : m[1];
  return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16) };
}

/**
 * Compare two renders of the same size. Each pixel is matched against the other render's 3x3
 * neighbourhood, from both sides, and its gap is the larger of the two closest matches, so a one-pixel
 * shift and anti-aliased edges cost nothing while a change of colour still counts. A pixel differs when
 * its gap is over `tol` on some channel. `ink` is the pixels that aren't background in either render
 * (with no background given, the pixels that aren't transparent); `share` is differing pixels over ink;
 * `mean` is the average gap over ink, which catches a shift of colour no single pixel shows.
 */
export function compareImages(a, b, bg = null, tol = LIMITS.tol) {
  if (a.width !== b.width || a.height !== b.height) return { sizeMismatch: true, ink: 0, diff: a.width * a.height, share: 1, mean: 255, max: 255, box: null };
  const w = a.width, h = a.height, A = a.data, B = b.data;
  const gap = (P, i, Q, j) => Math.max(Math.abs(P[i] - Q[j]), Math.abs(P[i + 1] - Q[j + 1]), Math.abs(P[i + 2] - Q[j + 2]), Math.abs(P[i + 3] - Q[j + 3]));
  const nearest = (P, i, Q, x, y, start) => {
    let best = start;
    for (let yy = Math.max(0, y - 1); yy <= Math.min(h - 1, y + 1) && best; yy++) {
      for (let xx = Math.max(0, x - 1); xx <= Math.min(w - 1, x + 1) && best; xx++) best = Math.min(best, gap(P, i, Q, (yy * w + xx) * 4));
    }
    return best;
  };
  const off = (P, i) => (bg ? Math.max(Math.abs(P[i] - bg.r), Math.abs(P[i + 1] - bg.g), Math.abs(P[i + 2] - bg.b)) > 8 : P[i + 3] > 8);
  let ink = 0, diff = 0, sum = 0, max = 0, x0 = w, y0 = h, x1 = -1, y1 = -1;
  const mask = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      const direct = gap(A, i, B, i);
      const g = direct ? Math.max(nearest(A, i, B, x, y, direct), nearest(B, i, A, x, y, direct)) : 0;
      if (off(A, i) || off(B, i)) { ink++; sum += g; }
      if (g > max) max = g;
      if (g <= tol) continue;
      diff++;
      mask[y * w + x] = 1;
      if (x < x0) x0 = x; if (y < y0) y0 = y; if (x > x1) x1 = x; if (y > y1) y1 = y;
    }
  }
  return { width: w, height: h, ink, diff, share: ink ? diff / ink : diff ? 1 : 0, mean: ink ? sum / ink : 0, max, box: diff ? { x0, y0, x1, y1 } : null, mask };
}
/** Two engines drawing one file: PASS, WARN or FAIL. */
export function enginesVerdict(c) {
  if ((c.diff >= LIMITS.enginesMinPixels && c.share >= LIMITS.enginesFailShare) || c.mean >= LIMITS.enginesFailMean) return 'FAIL';
  if ((c.diff >= 3 && c.share >= LIMITS.enginesWarnShare) || c.mean >= LIMITS.enginesWarnMean) return 'WARN';
  return 'PASS';
}
/** One engine drawing a file two ways that should match (github, minify). */
export const sameRender = (c) => !(c.diff > Math.max(LIMITS.sameMinPixels, c.ink * LIMITS.sameShare) || c.mean > LIMITS.sameMean);

/** Three panels side by side, enlarged when small: the first render, the second, and the first faded with the differing pixels in red. */
export function diffImage(a, b, cmp) {
  const z = Math.max(1, Math.min(8, Math.floor(160 / Math.max(a.width, a.height))));
  const w = a.width * z, h = a.height * z, sep = 6, W = w * 3 + sep * 2;
  const out = new Uint8Array(W * h * 4).fill(136);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const sx = Math.floor(x / z), sy = Math.floor(y / z), s = (sy * a.width + sx) * 4;
      for (const [panel, img] of [[0, a], [1, b]]) {
        const o = (y * W + panel * (w + sep) + x) * 4;
        out[o] = img.data[s]; out[o + 1] = img.data[s + 1]; out[o + 2] = img.data[s + 2]; out[o + 3] = 255;
      }
      const o = (y * W + 2 * (w + sep) + x) * 4;
      if (cmp.mask && cmp.mask[sy * a.width + sx]) { out[o] = 230; out[o + 1] = 20; out[o + 2] = 20; }
      else { const gr = Math.round((a.data[s] + a.data[s + 1] + a.data[s + 2]) / 3); const v = Math.round(255 - (255 - gr) * 0.3); out[o] = out[o + 1] = out[o + 2] = v; }
      out[o + 3] = 255;
    }
  }
  return { width: W, height: h, data: out };
}

/**
 * How a graphic stands out on a background. `clear` is a render on a transparent background; each
 * pixel at least half opaque is painted over `bg`, and it melts in when it's within 1.5:1 of it.
 * Returns the share that melts in, how much of the box the graphic covers, and its main colours.
 */
export function standOut(clear, bg) {
  const { data } = clear;
  const total = clear.width * clear.height;
  let opaque = 0, blend = 0;
  const buckets = new Map();
  for (let i = 0; i < data.length; i += 4) {
    const a = data[i + 3];
    if (a < 128) continue;
    opaque++;
    const f = a / 255;
    const c = { r: data[i] * f + bg.r * (1 - f), g: data[i + 1] * f + bg.g * (1 - f), b: data[i + 2] * f + bg.b * (1 - f) };
    if (contrast(c, bg) < LIMITS.blendRatio) blend++;
    const k = ((c.r >> 4) << 8) | ((c.g >> 4) << 4) | (c.b >> 4);
    const e = buckets.get(k) || { n: 0, r: 0, g: 0, b: 0 };
    e.n++; e.r += c.r; e.g += c.g; e.b += c.b;
    buckets.set(k, e);
  }
  const sorted = [...buckets.values()].sort((p, q) => q.n - p.n);
  const dominant = [];
  let covered = 0;
  for (const e of sorted) {
    if (dominant.length >= 4 || (dominant.length && covered >= 0.8 * opaque)) break;
    const col = { r: e.r / e.n, g: e.g / e.n, b: e.b / e.n };
    dominant.push({ hex: hex(col), share: e.n / Math.max(1, opaque), ratio: contrast(col, bg) });
    covered += e.n;
  }
  return { opaque, total, coverage: opaque / total, blend, share: opaque ? blend / opaque : 0, dominant };
}

// ---------------------------------------------------------------- a small strict XML parser

export class XmlError extends Error {
  constructor(reason, line, col) { super(`${reason} at line ${line}, column ${col}`); this.reason = reason; this.line = line; this.col = col; }
}

/**
 * Parse an XML document strictly: one root, every tag closed and nested, quoted attributes, no
 * duplicates, only declared entities (the internal DOCTYPE subset may declare literal ones; external
 * entities are refused), namespace prefixes declared. Throws XmlError with a line and column.
 * Returns { decl, doctype, prolog, root, epilog }; elements are { type: 'el', name, ns, attrs, children, line }.
 */
export function parseXml(src) {
  const text = String(src).replace(/^﻿/, '');
  const starts = [0];
  for (let i = 0; i < text.length; i++) if (text.charCodeAt(i) === 10) starts.push(i + 1);
  const where = (p) => {
    let lo = 0, hi = starts.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (starts[mid] <= p) lo = mid; else hi = mid - 1; }
    return { line: lo + 1, col: p - starts[lo] + 1 };
  };
  const err = (msg, p) => { const w = where(Math.max(0, Math.min(p, text.length))); throw new XmlError(msg, w.line, w.col); };
  const bad = /[\u0000-\u0008\u000B\u000C\u000E-\u001F￾￿]/.exec(text);
  if (bad) err(`a control character XML doesn't allow (U+${bad[0].charCodeAt(0).toString(16).toUpperCase().padStart(4, '0')})`, bad.index);
  const declared = {};
  const NAME = /[A-Za-z_:À-￿][-A-Za-z0-9_:.·À-￿]*/y;
  let pos = 0;
  const name = () => { NAME.lastIndex = pos; const m = NAME.exec(text); if (!m) err('expected a name', pos); pos = NAME.lastIndex; return m[0]; };
  const ws = () => { const s = pos; while (pos < text.length && (text[pos] === ' ' || text[pos] === '\t' || text[pos] === '\n' || text[pos] === '\r')) pos++; return pos > s; };
  const PREDEFINED = { lt: '<', gt: '>', amp: '&', quot: '"', apos: "'" };
  let expanded = 0;
  const expand = (raw, at, depth = 0) => {
    if (raw.indexOf('&') < 0) return raw;
    let out = '';
    for (let i = 0; i < raw.length; i++) {
      const c = raw[i];
      if (c !== '&') { out += c; continue; }
      const m = /^&(#x[0-9a-fA-F]+|#[0-9]+|[A-Za-z_:][-A-Za-z0-9_:.]*);/.exec(raw.slice(i, i + 40));
      if (!m) err("a bare '&' (write &amp;)", at + i);
      const ref = m[1];
      if (ref[0] === '#') {
        const cp = ref[1] === 'x' ? parseInt(ref.slice(2), 16) : parseInt(ref.slice(1), 10);
        if (!(cp === 9 || cp === 10 || cp === 13 || (cp >= 32 && cp <= 0xd7ff) || (cp >= 0xe000 && cp <= 0xfffd) || (cp >= 0x10000 && cp <= 0x10ffff))) err(`&${ref}; isn't a character XML allows`, at + i);
        out += String.fromCodePoint(cp);
      } else if (ref in PREDEFINED) out += PREDEFINED[ref];
      else if (Object.prototype.hasOwnProperty.call(declared, ref)) {
        if (depth > 8) err(`entity &${ref}; refers to itself or nests too deeply`, at + i);
        const v = expand(declared[ref], at + i, depth + 1);
        expanded += v.length;
        if (expanded > 1e6) err('entities expand to more than a megabyte', at + i);
        out += v;
      } else err(`unknown entity &${ref}; (only &lt; &gt; &amp; &quot; &apos; and numeric ones are defined)`, at + i);
      i += m[0].length - 1;
    }
    return out;
  };
  const skipDecl = () => {
    // a markup declaration in the DOCTYPE's internal subset, quotes respected
    let q = null;
    for (; pos < text.length; pos++) {
      const c = text[pos];
      if (q) { if (c === q) q = null; } else if (c === '"' || c === "'") q = c; else if (c === '>') { pos++; return; }
    }
    err('unclosed declaration in the DOCTYPE', pos);
  };
  const literal = () => {
    const q = text[pos];
    if (q !== '"' && q !== "'") err('expected a quoted value', pos);
    const end = text.indexOf(q, pos + 1);
    if (end < 0) err('unclosed quoted value', pos);
    const v = text.slice(pos + 1, end);
    pos = end + 1;
    return v;
  };
  const doctype = () => {
    const start = pos;
    pos += 9;
    if (!ws()) err('expected a space after <!DOCTYPE', pos);
    const dn = name();
    ws();
    if (text.startsWith('SYSTEM', pos)) { pos += 6; ws(); literal(); ws(); }
    else if (text.startsWith('PUBLIC', pos)) { pos += 6; ws(); literal(); ws(); literal(); ws(); }
    const ents = [];
    if (text[pos] === '[') {
      pos++;
      for (;;) {
        ws();
        if (pos >= text.length) err('unclosed DOCTYPE', start);
        if (text[pos] === ']') { pos++; break; }
        if (text.startsWith('<!--', pos)) { const e = text.indexOf('-->', pos + 4); if (e < 0) err('unclosed comment', pos); pos = e + 3; continue; }
        if (text.startsWith('<?', pos)) { const e = text.indexOf('?>', pos + 2); if (e < 0) err('unclosed processing instruction', pos); pos = e + 2; continue; }
        if (text[pos] === '%') { const m = /^%[A-Za-z_:][-A-Za-z0-9_:.]*;/.exec(text.slice(pos, pos + 80)); if (!m) err('bad parameter entity reference', pos); pos += m[0].length; continue; }
        if (text.startsWith('<!ENTITY', pos)) {
          const at = pos;
          pos += 8;
          if (!ws()) err('expected a space after <!ENTITY', pos);
          let pe = false;
          if (text[pos] === '%') { pe = true; pos++; ws(); }
          const en = name();
          ws();
          if (text.startsWith('SYSTEM', pos) || text.startsWith('PUBLIC', pos)) err(`entity ${en} loads an outside file, which this check refuses`, at);
          const v = literal();
          ws();
          if (text[pos] !== '>') err('expected > to end the entity declaration', pos);
          pos++;
          if (!pe) { if (!Object.prototype.hasOwnProperty.call(declared, en)) declared[en] = v; ents.push(en); }
          continue;
        }
        if (text.startsWith('<!', pos)) { skipDecl(); continue; }
        err('unexpected content in the DOCTYPE', pos);
      }
      ws();
    }
    if (text[pos] !== '>') err('expected > to end the DOCTYPE', pos);
    pos++;
    return { raw: text.slice(start, pos), name: dn, entities: ents, line: where(start).line };
  };
  const doc = { decl: null, doctype: null, prolog: [], root: null, epilog: [] };
  if (/^<\?xml[\s?]/.test(text)) {
    const e = text.indexOf('?>');
    if (e < 0) err('unclosed XML declaration', 0);
    doc.decl = text.slice(0, e + 2);
    pos = e + 2;
  }
  const comment = () => {
    const e = text.indexOf('-->', pos + 4);
    if (e < 0) err('unclosed comment', pos);
    const body = text.slice(pos + 4, e);
    if (body.includes('--') || body.endsWith('-')) err("'--' inside a comment", pos);
    const node = { type: 'comment', value: body, line: where(pos).line };
    pos = e + 3;
    return node;
  };
  const pi = () => {
    const e = text.indexOf('?>', pos + 2);
    if (e < 0) err('unclosed processing instruction', pos);
    const body = text.slice(pos + 2, e);
    if (/^xml(\s|$)/i.test(body)) err('an XML declaration is only allowed at the very start', pos);
    const node = { type: 'pi', value: body, line: where(pos).line };
    pos = e + 2;
    return node;
  };
  const misc = (into) => {
    for (;;) {
      ws();
      if (text.startsWith('<!--', pos)) into.push(comment());
      else if (text.startsWith('<?', pos)) into.push(pi());
      else return;
    }
  };
  misc(doc.prolog);
  if (text.startsWith('<!DOCTYPE', pos)) { doc.doctype = doctype(); misc(doc.prolog); }
  if (pos >= text.length) err('no root element', pos);
  if (text[pos] !== '<') err('text before the root element', pos);

  const open = () => {
    const at = pos;
    pos++;
    const el = { type: 'el', name: name(), attrs: [], children: [], line: where(at).line, at };
    const seen = new Set();
    for (;;) {
      const gapped = ws();
      if (text.startsWith('/>', pos)) { pos += 2; el.empty = true; return el; }
      if (text[pos] === '>') { pos++; return el; }
      if (pos >= text.length) err(`<${el.name}> is never finished`, at);
      if (!gapped) err('expected a space before the next attribute', pos);
      const ap = pos;
      const an = name();
      ws();
      if (text[pos] !== '=') err(`attribute ${an} has no value (write ${an}="...")`, pos);
      pos++;
      ws();
      const q = text[pos];
      if (q !== '"' && q !== "'") err(`the value of ${an} needs quotes`, pos);
      const vs = pos + 1, ve = text.indexOf(q, vs);
      if (ve < 0) err(`the value of ${an} is never closed`, pos);
      const raw = text.slice(vs, ve);
      const lt = raw.indexOf('<');
      if (lt >= 0) err(`'<' inside the value of ${an}`, vs + lt);
      if (seen.has(an)) err(`attribute ${an} appears twice`, ap);
      seen.add(an);
      el.attrs.push({ name: an, value: expand(raw, vs).replace(/[\t\n\r]/g, ' '), line: where(ap).line });
      pos = ve + 1;
    }
  };
  const root = open();
  doc.root = root;
  const stack = root.empty ? [] : [root];
  while (stack.length) {
    const cur = stack[stack.length - 1];
    if (pos >= text.length) err(`<${cur.name}> (line ${cur.line}) is never closed`, text.length);
    if (text.startsWith('</', pos)) {
      const at = pos;
      pos += 2;
      const en = name();
      ws();
      if (text[pos] !== '>') err(`expected > to end </${en}`, pos);
      pos++;
      if (en !== cur.name) err(`</${en}> closes <${cur.name}> from line ${cur.line}`, at);
      stack.pop();
    } else if (text.startsWith('<!--', pos)) cur.children.push(comment());
    else if (text.startsWith('<![CDATA[', pos)) {
      const e = text.indexOf(']]>', pos + 9);
      if (e < 0) err('unclosed CDATA section', pos);
      cur.children.push({ type: 'text', value: text.slice(pos + 9, e), cdata: true, line: where(pos).line });
      pos = e + 3;
    } else if (text.startsWith('<?', pos)) cur.children.push(pi());
    else if (text.startsWith('<!', pos)) err("unexpected '<!' (only comments and CDATA sections go here)", pos);
    else if (text[pos] === '<') {
      const el = open();
      el.parent = cur;
      cur.children.push(el);
      if (!el.empty) stack.push(el);
    } else {
      const s = pos;
      const e = text.indexOf('<', pos);
      pos = e < 0 ? text.length : e;
      const raw = text.slice(s, pos);
      const b2 = raw.indexOf(']]>');
      if (b2 >= 0) err("']]>' in text (write ]]&gt;)", s + b2);
      cur.children.push({ type: 'text', value: expand(raw, s), line: where(s).line });
    }
  }
  misc(doc.epilog);
  if (pos < text.length) err('content after the root element', pos);

  // Namespaces: every prefix must be declared where it's used.
  const walkNs = (el, scope) => {
    let sc = scope;
    for (const a of el.attrs) {
      if (a.name === 'xmlns' || a.name.startsWith('xmlns:')) {
        if (sc === scope) sc = { ...scope };
        sc[a.name === 'xmlns' ? '' : a.name.slice(6)] = a.value;
      }
    }
    const colon = el.name.indexOf(':');
    const prefix = colon > 0 ? el.name.slice(0, colon) : '';
    if (prefix && !(prefix in sc)) err(`prefix ${prefix}: on <${el.name}> isn't declared (add xmlns:${prefix}="...")`, el.at);
    el.ns = sc[prefix] ?? null;
    el.local = colon > 0 ? el.name.slice(colon + 1) : el.name;
    for (const a of el.attrs) {
      const c = a.name.indexOf(':');
      if (a.name === 'xmlns' || a.name.startsWith('xmlns:')) { a.ns = XMLNS_NS; a.local = a.name; continue; }
      if (c > 0) {
        const p = a.name.slice(0, c);
        if (!(p in sc)) err(`prefix ${p}: on attribute ${a.name} isn't declared (add xmlns:${p}="...")`, el.at);
        a.ns = sc[p];
        a.local = a.name.slice(c + 1);
      } else { a.ns = null; a.local = a.name; }
    }
    for (const ch of el.children) if (ch.type === 'el') walkNs(ch, sc);
  };
  walkNs(root, { xml: XML_NS, xmlns: XMLNS_NS });
  return doc;
}

function* elements(el) {
  yield el;
  for (const ch of el.children) if (ch.type === 'el') yield* elements(ch);
}
const getAttr = (el, local, ns = null) => { const a = el.attrs.find((x) => x.local === local && x.ns === ns); return a ? a.value : undefined; };
const hrefOf = (el) => getAttr(el, 'href') ?? getAttr(el, 'href', XLINK_NS);
const isSvg = (el, local) => el.ns === SVG_NS && (!local || el.local === local);
const ownText = (el) => el.children.filter((c) => c.type === 'text').map((c) => c.value).join('');

const escAttr = (v) => v.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;').replace(/\n/g, '&#10;').replace(/\t/g, '&#9;');
const escText = (v) => v.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
/** Write a parsed document back out. It adds no whitespace of its own: what's in the tree is what's written. */
export function serialize(doc) {
  const out = [];
  const node = (n) => {
    if (n.type === 'text') out.push(n.cdata ? `<![CDATA[${n.value}]]>` : escText(n.value));
    else if (n.type === 'comment') out.push(`<!--${n.value}-->`);
    else if (n.type === 'pi') out.push(`<?${n.value}?>`);
    else {
      out.push(`<${n.name}`);
      for (const a of n.attrs) out.push(` ${a.name}="${escAttr(a.value)}"`);
      if (!n.children.length) { out.push('/>'); return; }
      out.push('>');
      for (const c of n.children) node(c);
      out.push(`</${n.name}>`);
    }
  };
  if (doc.decl) out.push(doc.decl, '\n');
  for (const n of doc.prolog) { node(n); out.push('\n'); }
  if (doc.doctype) out.push(doc.doctype.raw, '\n');
  node(doc.root);
  for (const n of doc.epilog) { out.push('\n'); node(n); }
  return out.join('');
}

// ---------------------------------------------------------------- a small CSS reader

const stripCssComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, ' ');
/** Split on `sep` at the top level: not inside quotes, parentheses or brackets. */
function splitTop(s, sep) {
  const out = [];
  let depth = 0, q = null, cur = '';
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (q) { cur += c; if (c === '\\' && i + 1 < s.length) cur += s[++i]; else if (c === q) q = null; continue; }
    if (c === '"' || c === "'") q = c;
    else if (c === '(' || c === '[') depth++;
    else if (c === ')' || c === ']') depth--;
    else if (c === sep && depth === 0) { out.push(cur); cur = ''; continue; }
    cur += c;
  }
  out.push(cur);
  return out.map((x) => x.trim()).filter(Boolean);
}
function parseDecls(body) {
  const out = {};
  for (const d of splitTop(body, ';')) {
    const c = d.indexOf(':');
    if (c <= 0) continue;
    out[d.slice(0, c).trim().toLowerCase()] = d.slice(c + 1).replace(/!\s*important\s*$/i, '').trim();
  }
  return out;
}
const unquote = (s) => String(s).trim().replace(/^(['"])(.*)\1$/, '$2').trim();

/** Rules (selectors, declarations, the @media they sit in), @font-face, @import, @keyframes and @media preludes. */
export function parseCss(src) {
  const text = stripCssComments(src);
  const out = { rules: [], fontFaces: [], imports: [], keyframes: 0, media: [] };
  const walk = (s, media) => {
    let i = 0;
    while (i < s.length) {
      while (i < s.length && /\s/.test(s[i])) i++;
      if (i >= s.length) break;
      let j = i, depth = 0, q = null;
      for (; j < s.length; j++) {
        const c = s[j];
        if (q) { if (c === '\\') j++; else if (c === q) q = null; continue; }
        if (c === '"' || c === "'") q = c;
        else if (c === '(') depth++;
        else if (c === ')') depth--;
        else if (depth === 0 && (c === '{' || c === ';' || c === '}')) break;
      }
      const prelude = s.slice(i, j).trim();
      if (j >= s.length || s[j] !== '{') { if (/^@import\b/i.test(prelude)) out.imports.push(prelude); i = j + 1; continue; }
      let k = j + 1, d = 1;
      q = null;
      for (; k < s.length && d > 0; k++) {
        const c = s[k];
        if (q) { if (c === '\\') k++; else if (c === q) q = null; continue; }
        if (c === '"' || c === "'") q = c;
        else if (c === '{') d++;
        else if (c === '}') d--;
      }
      const body = s.slice(j + 1, d === 0 ? k - 1 : k);
      if (/^@media\b/i.test(prelude)) { out.media.push(prelude); walk(body, prelude); }
      else if (/^@(supports|layer|container|document)\b/i.test(prelude)) walk(body, media);
      else if (/^@(-[a-z]+-)?keyframes\b/i.test(prelude)) out.keyframes++;
      else if (/^@font-face\b/i.test(prelude)) { const dd = parseDecls(body); out.fontFaces.push({ family: unquote(dd['font-family'] || ''), src: dd.src || '' }); }
      else if (!prelude.startsWith('@')) out.rules.push({ selectors: splitTop(prelude, ','), decls: parseDecls(body), media });
      i = k;
    }
  };
  walk(text, null);
  return out;
}
const cssUrls = (s) => [...String(s).matchAll(/url\(\s*(?:"([^"]*)"|'([^']*)'|([^)]*?))\s*\)/gi)].map((m) => (m[1] ?? m[2] ?? m[3] ?? '').trim());

/** Does a CSS selector match an element? Type, #id, .class, descendant and child combinators are matched; anything fancier counts as a match. */
function selectorMatches(el, selector) {
  const toks = selector.replace(/\s*([>+~])\s*/g, ' $1 ').trim().split(/\s+/);
  const compound = (e, c) => {
    if (!e || e.type !== 'el') return false;
    if (/::/.test(c)) return false;
    const m = /^(\*|[A-Za-z][\w-]*)?/.exec(c);
    if (m[1] && m[1] !== '*' && m[1] !== e.local) return false;
    const id = getAttr(e, 'id'), cls = (getAttr(e, 'class') || '').split(/\s+/);
    for (const part of c.slice(m[0].length).match(/#[\w-]+|\.[\w-]+|\[[^\]]*\]|:[\w-]+(\([^)]*\))?/g) || []) {
      if (part[0] === '#' && part.slice(1) !== id) return false;
      if (part[0] === '.' && !cls.includes(part.slice(1))) return false;
    }
    return true;
  };
  let i = toks.length - 1;
  if (!compound(el, toks[i])) return false;
  let cur = el;
  i--;
  while (i >= 0) {
    let comb = ' ';
    if (['>', '+', '~'].includes(toks[i])) { comb = toks[i]; i--; }
    if (i < 0) break;
    const c = toks[i];
    if (comb === '>') { cur = cur.parent; if (!compound(cur, c)) return false; }
    else if (comb === ' ') {
      let p = cur.parent;
      while (p && !compound(p, c)) p = p.parent;
      if (!p) return false;
      cur = p;
    }
    i--;
  }
  return true;
}

const styleDecls = (el) => { const s = getAttr(el, 'style'); return s ? parseDecls(s) : {}; };
function familyFromFont(v) {
  const m = /(?:^|\s)(?:[\d.]+(?:px|pt|pc|em|rem|ex|ch|%|vw|vh|in|cm|mm|q)|xx-small|x-small|small|medium|large|x-large|xx-large|xxx-large|larger|smaller)(?:\s*\/\s*\S+)?\s+(.+)$/i.exec(v.trim());
  if (m) return m[1];
  return /^(caption|icon|menu|message-box|small-caption|status-bar)$/i.test(v.trim()) ? 'system-ui' : null;
}
const familyIn = (decls) => decls['font-family'] || (decls.font ? familyFromFont(decls.font) : null);
const usable = (v) => v && !/^(inherit|unset|initial|revert)$/i.test(v.trim());
/** The font-family values that could apply to an element: its style attribute, then matching rules, then its attribute, then its parent's. */
function familiesFor(el, rules) {
  for (let e = el; e && e.type === 'el'; e = e.parent) {
    const own = familyIn(styleDecls(e));
    if (usable(own)) return [own];
    const matched = rules.filter((r) => usable(familyIn(r.decls)) && r.selectors.some((s) => selectorMatches(e, s))).map((r) => familyIn(r.decls));
    if (matched.length) return matched;
    const attr = getAttr(e, 'font-family');
    if (usable(attr)) return [attr];
    const fa = getAttr(e, 'font');
    if (fa && familyFromFont(fa)) return [familyFromFont(fa)];
  }
  return [];
}

// ---------------------------------------------------------------- lint: static checks, no browser

function urlKind(u) {
  const s = String(u).trim();
  if (!s) return 'empty';
  if (s.startsWith('#')) return 'fragment';
  if (/^data:/i.test(s)) return 'data';
  if (/^javascript:/i.test(s.replace(/[\s\u0000-\u001f]+/g, ''))) return 'script';
  if (/^(https?:)?\/\//i.test(s)) return 'network';
  if (/^file:/i.test(s)) return 'file';
  if (/^[a-z][a-z0-9+.-]*:/i.test(s)) return 'other';
  return 'relative';
}
const lines = (xs) => { const u = [...new Set(xs)].sort((a, b) => a - b); return `line${u.length === 1 ? '' : 's'} ${list(u.slice(0, 6).map(String))}${u.length > 6 ? ' and more' : ''}`; };

/** Numbers with more than `decimals` places, rounded; the rest left exactly as written. */
export function roundNumbers(value, decimals = 2) {
  const NUM = /[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?/g;
  let out = '', last = 0, m;
  while ((m = NUM.exec(value))) {
    const tok = m[0];
    const mant = tok.replace(/[eE].*$/, '');
    const dot = mant.indexOf('.');
    const places = dot < 0 ? 0 : mant.length - dot - 1;
    let rep = tok;
    if (places > decimals || /[eE]/.test(tok)) {
      rep = Number(tok).toFixed(decimals);
      if (rep.includes('.')) rep = rep.replace(/0+$/, '').replace(/\.$/, '');
      if (rep === '-0') rep = '0';
      rep = rep.replace(/^(-?)0\./, '$1.');
    }
    out += value.slice(last, m.index) + rep;
    last = m.index + tok.length;
    // "1.0.5" is 1.0 then .5: once 1.0 becomes 1, the two would run together as 1.5.
    if (rep !== tok && value[last] === '.' && !rep.includes('.')) out += ' ';
  }
  return out + value.slice(last);
}
const longNumbers = (value) => (value.match(/[-+]?(?:\d+\.\d*|\.\d+)/g) || []).filter((t) => t.length - t.indexOf('.') - 1 > 3).length;
const isEditorNs = (ns) => !!ns && EDITOR_NS.some((r) => r.test(ns));

/**
 * The text transforms minify makes, on a parsed document (changed in place): `metadata` drops comments,
 * processing instructions, the XML declaration (when it says UTF-8 or nothing), the DOCTYPE, <metadata>
 * and everything in an editor's namespace; `round` rounds numbers in geometry attributes to `decimals`
 * places; `whitespace` drops whitespace between elements (never inside text) and collapses it in
 * geometry and <style>. Unused namespace declarations, empty <g> and empty <defs> go too.
 */
export function minifyDoc(doc, { decimals = 2, metadata = true, round = true, whitespace = true } = {}) {
  if (metadata) {
    if (doc.decl && (!/encoding/i.test(doc.decl) || /encoding\s*=\s*["']utf-?8["']/i.test(doc.decl))) doc.decl = null;
    doc.doctype = null;
    doc.prolog = [];
    doc.epilog = [];
  }
  const visit = (el, keep) => {
    const preserve = keep || getAttr(el, 'space', XML_NS) === 'preserve' || (el.ns === SVG_NS && KEEP_SPACE.has(el.local));
    el.children = el.children.filter((c) => {
      if (c.type === 'comment' || c.type === 'pi') return !metadata;
      if (c.type === 'el') return !(metadata && (isEditorNs(c.ns) || isSvg(c, 'metadata')));
      return !(whitespace && !preserve && !c.value.trim());
    });
    if (metadata) el.attrs = el.attrs.filter((a) => (a.ns === XMLNS_NS ? !isEditorNs(a.value) : !isEditorNs(a.ns)));
    for (const a of el.attrs) {
      if (a.ns !== null || !NUMERIC_ATTRS.has(a.local)) continue;
      if (round) a.value = roundNumbers(a.value, decimals);
      if (whitespace) a.value = a.value.replace(/\s+/g, ' ').trim();
    }
    if (whitespace && isSvg(el, 'style')) {
      for (const c of el.children) if (c.type === 'text') c.value = stripCssComments(c.value).replace(/\s+/g, ' ').replace(/\s*([{};])\s*/g, '$1').trim();
    }
    for (const c of el.children) if (c.type === 'el') visit(c, preserve);
    el.children = el.children.filter((c) => !(c.type === 'el' && c.ns === SVG_NS && !c.children.length
      && ((c.local === 'g' && !c.attrs.length) || (c.local === 'defs' && !getAttr(c, 'id')))));
    // A <g> with no attributes does nothing, unless a stylesheet selects it by position or by type.
    if (metadata && !styled) el.children = el.children.flatMap((c) => (c.type === 'el' && c.ns === SVG_NS && c.local === 'g' && !c.attrs.length ? c.children : [c]));
  };
  const styled = [...elements(doc.root)].some((e) => isSvg(e, 'style') || getAttr(e, 'style') !== undefined);
  visit(doc.root, false);
  const used = new Set();
  for (const el of elements(doc.root)) {
    if (el.name.includes(':')) used.add(el.name.split(':')[0]);
    for (const a of el.attrs) if (a.ns !== XMLNS_NS && a.name.includes(':')) used.add(a.name.split(':')[0]);
  }
  for (const el of elements(doc.root)) el.attrs = el.attrs.filter((a) => !(a.ns === XMLNS_NS && a.name.startsWith('xmlns:') && !used.has(a.name.slice(6))));
  return doc;
}
export function minifySvg(text, opts) { return serialize(minifyDoc(parseXml(text), opts)); }

/** The same file with its text taken out, for telling a difference in the type from a difference in the drawing. */
export function withoutText(text) {
  const doc = parseXml(text);
  for (const el of elements(doc.root)) el.children = el.children.filter((c) => !(c.type === 'el' && c.ns === SVG_NS && TEXT_EL.has(c.local)));
  return serialize(doc);
}

const parseLength = (v) => { const m = /^\s*([\d.]+)\s*(px)?\s*$/.exec(v || ''); return m ? Number(m[1]) : null; };

/**
 * Lint one SVG file's text. `kind` sets the size budget and which font rules apply; `target` github
 * turns <foreignObject> into a failure. Returns { issues: [{level, rule, message, line}], ok, parsed,
 * bytes, viewBox, width, height, animated, motion, reducedMotion, colorScheme, hasText }.
 */
export function lintSvg(text, { kind = null, target = 'web' } = {}) {
  const issues = [];
  const add = (level, rule, message, line = null) => issues.push({ level, rule, message, line });
  const bytes = Buffer.byteLength(text, 'utf8');
  const res = { issues, bytes, ok: false, parsed: false, viewBox: null, width: null, height: null, animated: false, motion: [],
    reducedMotion: false, colorScheme: false, hasText: false };
  const done = () => { res.ok = !issues.some((i) => i.level === 'FAIL'); return res; };
  let doc;
  try { doc = parseXml(text); } catch (e) {
    if (!(e instanceof XmlError)) throw e;
    add('FAIL', 'xml', `isn't well-formed XML: ${e.reason} (line ${e.line}, column ${e.col}). As an image, a browser shows nothing for it`, e.line);
    return done();
  }
  res.parsed = true;
  const root = doc.root;
  if (root.local !== 'svg') { add('FAIL', 'root', `the root element is <${root.name}>, not <svg>`, root.line); return done(); }
  if (root.ns !== SVG_NS) add('FAIL', 'root', 'the <svg> element has no xmlns="http://www.w3.org/2000/svg", so shown as an image it draws nothing. Add it', root.line);

  // Size: a viewBox, and whether width and height fix it.
  const vb = getAttr(root, 'viewBox');
  const w = getAttr(root, 'width'), h = getAttr(root, 'height');
  res.width = parseLength(w);
  res.height = parseLength(h);
  if (vb !== undefined) {
    const n = vb.trim().split(/[\s,]+/).map(Number);
    if (n.length === 4 && n.every(Number.isFinite) && n[2] > 0 && n[3] > 0) res.viewBox = { x: n[0], y: n[1], w: n[2], h: n[3] };
    else add('FAIL', 'viewbox', `viewBox="${vb}" isn't four numbers with a positive width and height`, root.line);
  } else if (res.width || res.height) {
    add('FAIL', 'viewbox', `no viewBox, and width="${w ?? ''}" height="${h ?? ''}" fix its size, so it can't scale. Add viewBox="0 0 ${res.width || res.height} ${res.height || res.width}"`, root.line);
  } else add('FAIL', 'viewbox', 'no viewBox and no width or height, so it has no shape to scale. Add a viewBox', root.line);
  if (res.viewBox && (res.width || res.height)) add('INFO', 'fixed-size', `width and height fix it at ${w ?? '?'}×${h ?? '?'}; drop them unless the use needs a fixed size`, root.line);

  // CSS from every <style>.
  const styles = [...elements(root)].filter((el) => isSvg(el, 'style'));
  const css = { rules: [], fontFaces: [], imports: [], keyframes: 0, media: [] };
  for (const s of styles) {
    const c = parseCss(ownText(s));
    css.rules.push(...c.rules); css.fontFaces.push(...c.fontFaces); css.imports.push(...c.imports); css.media.push(...c.media);
    css.keyframes += c.keyframes;
    for (const imp of c.imports) add('FAIL', 'external', `${snippet(imp, 60)} in <style> (line ${s.line}) loads outside CSS, which an SVG shown as an image can't fetch`, s.line);
  }
  res.reducedMotion = css.media.some((m) => /prefers-reduced-motion/i.test(m));
  res.colorScheme = css.media.some((m) => /prefers-color-scheme/i.test(m));

  const ids = new Map(), refs = [], externals = [], scripts = [], rasters = [], foreign = [];
  const checkUrl = (u, el, line, where) => {
    const k = urlKind(u);
    if (k === 'fragment') refs.push({ id: decodeURIComponent(u.trim().slice(1)), line, where });
    else if (k === 'script') scripts.push({ line, what: `a javascript: URL in ${where}` });
    else if (k === 'network' || k === 'file') externals.push({ line, what: `${where} loads ${snippet(u, 50)} from outside the file` });
    else if (k === 'relative') externals.push({ line, what: `${where} points at another file (${snippet(u, 40)})` });
    else if (k === 'data' && el && isSvg(el, 'image') && /^data:image\/(png|jpe?g|gif|webp|avif)/i.test(u.trim())) rasters.push(line);
  };
  const imported = new Set(css.imports.flatMap((imp) => cssUrls(imp)));
  for (const s of styles) for (const u of cssUrls(stripCssComments(ownText(s)))) if (!imported.has(u)) checkUrl(u, null, s.line, '<style>');
  for (const el of elements(root)) {
    if (el.local === 'script') scripts.push({ line: el.line, what: '<script>' });
    if (isSvg(el, 'foreignObject')) foreign.push(el.line);
    if (el.ns === SVG_NS && SMIL.has(el.local)) res.motion.push(`<${el.local}> at line ${el.line}`);
    for (const a of el.attrs) {
      if (a.ns === null && /^on/i.test(a.local)) scripts.push({ line: a.line, what: `${a.name}="${snippet(a.value, 30)}"` });
      if (a.local === 'id' && a.ns === null) ids.set(a.value, [...(ids.get(a.value) || []), a.line]);
      if ((a.local === 'href' && (a.ns === null || a.ns === XLINK_NS)) || (a.local === 'src' && a.ns === null)) {
        if (isSvg(el, 'a')) { if (urlKind(a.value) === 'script') scripts.push({ line: a.line, what: 'a javascript: link' }); }
        else checkUrl(a.value, el, a.line, `${a.name} on <${el.name}>`);
      } else if (/url\(/i.test(a.value)) for (const u of cssUrls(a.value)) checkUrl(u, el, a.line, `${a.name} on <${el.name}>`);
      if (a.local === 'style' && a.ns === null) {
        const d = parseDecls(a.value);
        if ((d.animation && !/^none\b/i.test(d.animation)) || (d['animation-name'] && d['animation-name'] !== 'none')) res.motion.push(`a CSS animation at line ${a.line}`);
      }
    }
  }
  if (css.keyframes || css.rules.some((r) => (r.decls.animation && !/^none\b/i.test(r.decls.animation)) || (r.decls['animation-name'] && r.decls['animation-name'] !== 'none'))) {
    res.motion.push(`a CSS animation in <style> (line ${styles[0]?.line ?? '?'})`);
  }
  res.animated = res.motion.length > 0;

  for (const s of scripts) add('FAIL', 'script', `${s.what} at line ${s.line}: scripts don't run when an SVG is shown as an image, and a file that carries one isn't safe to hand round. Remove it`, s.line);
  for (const e of externals) add('FAIL', 'external', `${e.what} (line ${e.line}); an SVG shown as an image loads nothing, so that part won't appear. Embed it as a data: URL or draw it`, e.line);
  if (foreign.length) {
    if (target === 'github') add('FAIL', 'foreign-object', `<foreignObject> at ${lines(foreign)} puts HTML inside the SVG; engines draw it differently, and the github target doesn't allow it. Draw it with SVG shapes and text`, foreign[0]);
    else add('WARN', 'foreign-object', `<foreignObject> at ${lines(foreign)} puts HTML inside the SVG; engines draw it differently, so check the engines comparison`, foreign[0]);
  }
  for (const [id, at] of ids) if (at.length > 1) add('FAIL', 'ids', `id "${id}" is used ${at.length} times (${lines(at)}): references pick the first, and two copies on one page clash. Make them unique`, at[0]);
  const missing = new Map();
  for (const r of refs) if (!ids.has(r.id)) missing.set(r.id, [...(missing.get(r.id) || []), r.line]);
  for (const [id, at] of missing) add('FAIL', 'ids', `#${id} (${lines(at)}) points at an id the file doesn't have, so whatever uses it draws wrong or not at all`, at[0]);
  if (rasters.length) add('WARN', 'raster', `a raster image is embedded at ${lines(rasters)}: it blurs when the graphic scales up. Draw it as vector shapes if you can`, rasters[0]);

  // Fonts: every <text> must use a family the file embeds, or a generic one.
  const embedded = new Set(css.fontFaces.filter((f) => /url\(\s*['"]?\s*data:/i.test(f.src)).map((f) => f.family.toLowerCase()));
  const bad = new Map(), generic = new Map(), none = [];
  for (const el of elements(root)) {
    if (!(el.ns === SVG_NS && TEXT_EL.has(el.local)) || !ownText(el).trim()) continue;
    res.hasText = true;
    const fams = familiesFor(el, css.rules);
    if (!fams.length) { none.push(el.line); continue; }
    for (const f of fams) {
      const first = unquote(splitTop(f, ',')[0] || '');
      const low = first.toLowerCase();
      if (embedded.has(low)) continue;
      if (GENERIC.has(low)) generic.set(low, [...(generic.get(low) || []), el.line]);
      else bad.set(first, [...(bad.get(first) || []), el.line]);
    }
  }
  for (const [fam, at] of bad) add('FAIL', 'font', `the text at ${lines(at)} is set in "${fam}", which the file doesn't embed: as an image it can't load it, and machines without it show another face. Outline the text to paths, embed the font as a data: URL in @font-face, or use a generic family`, at[0]);
  if (['logo', 'icon', 'icons'].includes(kind)) {
    for (const [fam, at] of generic) add('WARN', 'font', `the text at ${lines(at)} uses the generic family ${fam}, which each system fills with its own face, so the letters change from machine to machine. For a ${kind}, outline them to paths or embed the font`, at[0]);
  }
  if (none.length) add('WARN', 'font', `the text at ${lines(none)} names no font, so each browser uses its default (usually a serif). Name a generic family or embed one`, none[0]);

  if (res.animated && !res.reducedMotion) {
    add('FAIL', 'motion', `it animates (${list(res.motion.slice(0, 3))}) but has no @media (prefers-reduced-motion) rule, so it moves for people who asked for less motion. Stop or hide the motion under prefers-reduced-motion: reduce`, null);
  }

  // Hygiene: editor metadata, long decimals, size, a title.
  const comments = [...doc.prolog, ...doc.epilog].filter((n) => n.type === 'comment').length
    + [...elements(root)].reduce((n, el) => n + el.children.filter((c) => c.type === 'comment').length, 0);
  const editorBits = [...elements(root)].filter((el) => isEditorNs(el.ns) || isSvg(el, 'metadata') || el.attrs.some((a) => isEditorNs(a.ns))).length;
  if (comments || editorBits || doc.doctype) {
    const plain = serialize(parseXml(text));
    const saved = Buffer.byteLength(plain) - Buffer.byteLength(minifySvg(text, { metadata: true, round: false, whitespace: false }));
    const what = [comments && plural(comments, 'comment'), editorBits && plural(editorBits, 'element') + ' with editor metadata', doc.doctype && 'a DOCTYPE'].filter(Boolean);
    if (saved > 0) add('WARN', 'metadata', `${list(what)}: ${kb(saved)} that minify removes`, null);
  }
  let long = 0;
  for (const el of elements(root)) for (const a of el.attrs) if (a.ns === null && NUMERIC_ATTRS.has(a.local)) long += longNumbers(a.value);
  if (long) {
    const plain = serialize(parseXml(text));
    const saved = Buffer.byteLength(plain) - Buffer.byteLength(minifySvg(text, { metadata: false, round: true, whitespace: false, decimals: 2 }));
    add('WARN', 'decimals', `${plural(long, 'number')} with more than 3 decimals; rounding to 2 saves about ${kb(saved)}, and minify checks the render stays the same`, null);
  }
  if (kind && BUDGET[kind] && bytes > BUDGET[kind]) add('WARN', 'size', `${kb(bytes)}, more than the ${kb(BUDGET[kind])} a ${kind === 'icons' ? 'set icon' : kind} should need: look for detail nobody sees at its sizes, embedded fonts or images, and run minify`, null);
  const decorative = getAttr(root, 'aria-hidden') === 'true';
  const kids = root.children.filter((c) => c.type === 'el');
  if (!decorative && !kids.some((c) => isSvg(c, 'title'))) add('WARN', 'title', 'no <title>: inline, a screen reader announces nothing for it. Add <title> as the first child (with role="img" on the <svg>), or aria-hidden="true" if it\'s decorative', root.line);
  if (kind === 'diagram' && !kids.some((c) => isSvg(c, 'desc'))) add('WARN', 'title', 'no <desc>: a diagram needs a sentence or two saying what it shows', root.line);
  add('INFO', 'file', `${kb(bytes)}${res.viewBox ? `, viewBox ${res.viewBox.x} ${res.viewBox.y} ${res.viewBox.w} ${res.viewBox.h}` : ''}${res.animated ? ', animated' : ''}${res.colorScheme ? ', changes with the colour scheme' : ''}${res.hasText ? ', has text' : ''}`, null);
  return done();
}

// ---------------------------------------------------------------- Playwright and the engines

let PW = null;
/**
 * Find Playwright, the way killer-design's shoot.mjs and killer-slides' deck.mjs do: the current
 * project, the killer-skills runtime in ~/.cache/killer-skills (which brings its own Chromium),
 * killer-design's cache, then this skill's ~/.cache/killer-svg, where it's installed if nothing has it,
 * unless `install` is false or KILLER_SVG_NO_INSTALL is set.
 */
async function loadPlaywright({ install = true } = {}) {
  if (PW) return PW;
  const root = cacheRoot();
  const runtime = path.join(root, 'killer-skills');
  const own = path.join(root, 'killer-svg');
  const allowed = install && !process.env.KILLER_SVG_NO_INSTALL;
  let found = null;
  for (const base of [process.cwd(), runtime, path.join(root, 'killer-design'), own]) {
    try {
      const req = createRequire(path.join(base, '__resolve__.js'));
      const pkg = req.resolve('playwright/package.json');
      if (base === runtime && existsSync(path.join(runtime, 'ms-playwright'))) process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(runtime, 'ms-playwright');
      found = { entry: req.resolve('playwright'), dir: path.dirname(pkg), base };
      break;
    } catch { /* not here */ }
  }
  if (!found) {
    if (!allowed) throw new Fail("Playwright isn't installed: not in this project, ~/.cache/killer-skills, ~/.cache/killer-design or ~/.cache/killer-svg. Run the killer-skills installer, or run again without KILLER_SVG_NO_INSTALL to install it into ~/.cache/killer-svg.");
    console.error(`[svg] Playwright not found - installing it once into ${own} ...`);
    await fs.mkdir(own, { recursive: true });
    const pj = path.join(own, 'package.json');
    if (!existsSync(pj)) await fs.writeFile(pj, '{ "name": "killer-svg-tools", "private": true }\n');
    try {
      execSync('npm install --prefer-offline --no-audit --no-fund --loglevel=error playwright', { cwd: own, stdio: ['ignore', 'inherit', 'inherit'] });
    } catch (e) { throw new Fail(`installing Playwright into ${own} failed (it needs the network the first time): ${e.message}`); }
    const req = createRequire(path.join(own, '__resolve__.js'));
    found = { entry: req.resolve('playwright'), dir: path.dirname(req.resolve('playwright/package.json')), base: own };
  }
  const mod = await import(pathToFileURL(found.entry).href);
  let version = '?';
  try { version = JSON.parse(readFileSync(path.join(found.dir, 'package.json'), 'utf8')).version; } catch { /* keep ? */ }
  PW = { pw: mod.default ?? mod, ...found, version, allowed };
  return PW;
}

// Chromium runs transform animations on its compositor thread, which keeps its own clock: an animation paused
// and seeked from the page can still paint at the moment it was paused. This flag keeps them on the main
// thread, so a seeked frame is the frame asked for. Firefox and WebKit seek exactly without it.
const CHROMIUM_ARGS = ['--disable-threaded-animation'];

/** Chromium, which every command that renders needs; downloaded once if it's missing and installs are allowed. */
export async function launchChromium({ install = true } = {}) {
  const P = await loadPlaywright({ install });
  try {
    return await P.pw.chromium.launch({ args: CHROMIUM_ARGS });
  } catch (e) {
    if (!/Executable doesn't exist|playwright install/i.test(String(e.message))) throw e;
    if (!(install && P.allowed)) throw new Fail(`Playwright is in ${P.base} but its Chromium isn't installed, and KILLER_SVG_NO_INSTALL is set.`);
    console.error('[svg] Downloading Chromium for Playwright (one-time) ...');
    execFileSync(process.execPath, [path.join(P.dir, 'cli.js'), 'install', 'chromium'], { stdio: ['ignore', 'inherit', 'inherit'] });
    return await P.pw.chromium.launch({ args: CHROMIUM_ARGS });
  }
}

function defaultBrowsersDir() {
  if (process.platform === 'win32') return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'ms-playwright');
  if (process.platform === 'darwin') return path.join(os.homedir(), 'Library', 'Caches', 'ms-playwright');
  return path.join(process.env.XDG_CACHE_HOME || path.join(os.homedir(), '.cache'), 'ms-playwright');
}
/** Where to look for Firefox and WebKit builds: KILLER_SVG_BROWSERS if set, else Playwright's own caches. */
function browserDirs(registry) {
  if (process.env.KILLER_SVG_BROWSERS !== undefined) {
    return process.env.KILLER_SVG_BROWSERS.split(path.delimiter).map((s) => s.trim()).filter(Boolean).map((s) => path.resolve(expandHome(s)));
  }
  const dirs = [registry, USER_BROWSERS_PATH && USER_BROWSERS_PATH !== '0' ? USER_BROWSERS_PATH : null,
    path.join(cacheRoot(), 'killer-skills', 'ms-playwright'), defaultBrowsersDir()].filter(Boolean).map((p) => path.resolve(p));
  return [...new Set(dirs)];
}

/**
 * Launch Firefox or WebKit if this Playwright's own build of it is installed somewhere it can be found.
 * The runtime pack points Playwright at a folder with only Chromium in it, so the build is looked up by
 * its revision folder (firefox-1555, webkit-2370) in each folder browserDirs lists, and launched by path.
 */
async function launchOther(P, e) {
  let exe;
  try { exe = P.pw[e].executablePath(); } catch { return { status: 'not checked', reason: `this Playwright has no ${TITLE[e]}` }; }
  let rev = null;
  for (let d = exe; path.dirname(d) !== d; d = path.dirname(d)) if (new RegExp(`^${e}-[0-9]+$`).test(path.basename(d))) { rev = d; break; }
  if (!rev) return { status: 'not checked', reason: `this Playwright doesn't say where its ${TITLE[e]} lives` };
  const rel = path.relative(path.dirname(rev), exe);
  const dirs = browserDirs(path.dirname(rev));
  const hit = dirs.map((d) => path.join(d, rel)).find((p) => existsSync(p));
  if (!hit) return { status: 'not checked', install: true, reason: `${TITLE[e]} isn't installed for Playwright ${P.version} (it needs ${path.basename(rev)}; looked in ${dirs.length ? dirs.join(', ') : 'no folders'})` };
  try {
    const browser = await P.pw[e].launch({ executablePath: hit });
    return { status: 'checked', browser, version: browser.version() };
  } catch (err) {
    return { status: 'not checked', reason: `${TITLE[e]} is installed but didn't start: ${snippet(String(err.message).split('\n')[0], 160)}` };
  }
}

/** Chromium (required) and whichever of Firefox and WebKit the run asked for and can launch. */
async function openEngines(want, run = null) {
  let chromium;
  try {
    chromium = await launchChromium();
  } catch (e) {
    if (run) { run.cfg.blocked = `the browser couldn't start: ${e.message}`; await run.save(); }
    throw e instanceof Fail ? e : new Fail(`couldn't start Chromium: ${e.message}`);
  }
  if (run && run.cfg.blocked) { run.cfg.blocked = null; await run.save(); }
  const env = { browsers: { chromium }, status: { chromium: { status: 'checked', version: chromium.version() } }, missing: [] };
  await Promise.all(['firefox', 'webkit'].map(async (e) => {
    if (!want.includes(e)) { env.status[e] = { status: 'not checked', reason: 'not asked for' }; return; }
    const r = await launchOther(PW, e);
    if (r.browser) env.browsers[e] = r.browser;
    env.status[e] = { status: r.status, reason: r.reason || null, version: r.version || null };
    if (r.install) env.missing.push(e);
  }));
  env.installCommand = env.missing.length ? `node "${path.join(PW.dir, 'cli.js')}" install ${env.missing.join(' ')}` : null;
  env.close = () => Promise.all(Object.values(env.browsers).map((b) => b.close().catch(() => {})));
  return env;
}

// ---------------------------------------------------------------- a local server for the renders

const MIME = { '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.webp': 'image/webp', '.avif': 'image/avif', '.css': 'text/css', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.otf': 'font/otf' };
/** Pages kept in memory, and each graphic's own folder, so engines load the files the same way and relative references resolve. */
async function startServer() {
  const pages = new Map(), roots = [];
  const srv = http.createServer(async (req, res) => {
    try {
      const p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
      const pg = pages.get(p);
      if (pg) {
        res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store', ...(pg.csp ? { 'content-security-policy': pg.csp } : {}) });
        return res.end(pg.html);
      }
      const m = /^\/d\/(\d+)\/(.+)$/.exec(p);
      if (m && roots[+m[1]]) {
        const root = roots[+m[1]], fp = path.resolve(root, m[2]), rel = path.relative(root, fp);
        if (rel.startsWith('..') || path.isAbsolute(rel)) { res.writeHead(403); return res.end(); }
        const body = await fs.readFile(fp);
        res.writeHead(200, { 'content-type': MIME[path.extname(fp).toLowerCase()] || 'application/octet-stream', 'cache-control': 'no-store' });
        return res.end(body);
      }
      res.writeHead(404);
      res.end();
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  let n = 0;
  return {
    base,
    page(html, csp = null) { const p = `/p/${++n}.html`; pages.set(p, { html, csp }); return base + p; },
    file(abs) {
      const dir = canonical(path.dirname(abs));
      let i = roots.indexOf(dir);
      if (i < 0) { roots.push(dir); i = roots.length - 1; }
      return `${base}/d/${i}/${encodeURIComponent(path.basename(abs))}`;
    },
    close: () => new Promise((r) => srv.close(() => r())),
  };
}
/** Block every request that leaves this machine, and note it. */
async function guard(context, server, log = null) {
  await context.route('**/*', (route) => {
    const u = route.request().url();
    if (u.startsWith(server.base + '/') || /^(data|blob|about):/i.test(u)) return route.continue();
    if (log) log.push(u.slice(0, 200));
    return route.abort();
  });
}

class RenderError extends Error {}
const BOX_GAP = 8;
function layout(boxes) {
  let x = 0;
  const placed = boxes.map((b) => { const p = { ...b, x }; x += b.width + BOX_GAP; return p; });
  return { placed, width: Math.max(1, x - BOX_GAP), height: Math.max(1, ...boxes.map((b) => b.height)) };
}

/** One graphic at each size, side by side through <img> on one background, cropped apart. */
async function imgShots(page, server, file, boxes, { bg, clear = false, csp = null }) {
  const { placed, width, height } = layout(boxes);
  const src = server.file(file);
  const html = `<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:${clear ? 'transparent' : bg}}img{position:absolute;top:0;display:block}</style>`
    + placed.map((p) => `<img src="${src}" alt="" style="left:${p.x}px;width:${p.width}px;height:${p.height}px">`).join('');
  await page.setViewportSize({ width: Math.max(width, 64), height: Math.max(height, 64) });
  await page.goto(server.page(html, csp), { waitUntil: 'load' });
  const ok = await page.evaluate(() => Promise.all([...document.images].map((i) => i.decode().then(() => true, () => false))));
  if (ok.includes(false)) throw new RenderError("the browser can't draw it as an image");
  const shot = decodePng(await page.screenshot({ clip: { x: 0, y: 0, width, height }, omitBackground: clear }));
  return placed.map((p) => crop(shot, p.x, 0, p.width, p.height));
}

/* In the page: size the root, paint its background, stop every animation at 0 and say how long they run. */
function DOC_PREP(bg) {
  const r = document.documentElement;
  if (!r || r.namespaceURI !== 'http://www.w3.org/2000/svg' || r.localName !== 'svg') return { ok: false };
  r.style.display = 'block';
  r.style.background = bg;
  if (r.pauseAnimations) r.pauseAnimations();
  if (r.setCurrentTime) r.setCurrentTime(0);
  const anims = document.getAnimations();
  let end = 0, loops = false;
  for (const a of anims) {
    a.pause();
    a.currentTime = 0;
    const t = a.effect && a.effect.getComputedTiming ? a.effect.getComputedTiming().endTime : 0;
    if (!Number.isFinite(t)) loops = true; else end = Math.max(end, t / 1000);
  }
  const smil = [...document.querySelectorAll('animate, animateTransform, animateMotion, animateColor, set')];
  for (const el of smil) {
    let begin = 0, dur = 0, count = 1;
    try { begin = el.getStartTime(); } catch { continue; }
    try { dur = el.getSimpleDuration(); } catch { dur = 0; }
    const rc = el.getAttribute('repeatCount'), rd = el.getAttribute('repeatDur');
    if (rc === 'indefinite' || rd === 'indefinite') { loops = true; continue; }
    if (rc && Number.isFinite(Number(rc))) count = Number(rc);
    let e = begin + dur * count;
    const m = rd ? /^([\d.]+)(ms|s|min|h)?$/.exec(rd.trim()) : null;
    if (m) e = Math.max(e, begin + Number(m[1]) * ({ ms: 0.001, s: 1, min: 60, h: 3600 }[m[2] || 's']));
    end = Math.max(end, e);
  }
  return { ok: true, css: anims.length, smil: smil.length, end, loops };
}
function DOC_SIZE([w, h]) { const r = document.documentElement; r.style.width = `${w}px`; r.style.height = `${h}px`; return true; }
/* In the page: every animation, CSS and SMIL, to the same moment, then two frames so it's painted. */
function DOC_SEEK(t) {
  const r = document.documentElement;
  if (r.setCurrentTime) r.setCurrentTime(t);
  for (const a of document.getAnimations()) { a.pause(); a.currentTime = t * 1000; }
  return new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(() => res(true))));
}
/** The still an animated file shows at the end: past its last finite animation, or a fixed late moment if something loops. */
const posterTime = (info) => (info.loops ? 10.77 : Math.min(120, info.end + 0.25));

/** One graphic opened as a document at each size, with its animations seeked to `t` ('end' for the poster). */
async function docShots(page, server, file, boxes, { bg, clear = false, t = 'end' }) {
  await page.setViewportSize({ width: Math.max(64, ...boxes.map((b) => b.width)), height: Math.max(64, ...boxes.map((b) => b.height)) });
  await page.goto(server.file(file), { waitUntil: 'load' });
  const info = await page.evaluate(DOC_PREP, clear ? 'transparent' : bg);
  if (!info.ok) throw new RenderError('opened as a document, it has no <svg> root in the SVG namespace');
  const at = t === 'end' ? posterTime(info) : t;
  const shots = [];
  for (const b of boxes) {
    await page.evaluate(DOC_SIZE, [b.width, b.height]);
    await page.evaluate(DOC_SEEK, at);
    shots.push(decodePng(await page.screenshot({ clip: { x: 0, y: 0, width: b.width, height: b.height }, omitBackground: clear })));
  }
  return { shots, info, at };
}

/** Sample times for the motion check: early ones to see an entrance, and three irregular ones at the end, so a loop can't look still. */
export function motionTimes(end, loops) {
  let T = Math.min(60, Math.max(5, end + 1));
  if (loops) T = Math.max(T, 10);
  T = Math.ceil(T) + 0.77;
  const early = [0, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 5, 7, 10, 15, 20, 30, 45].filter((t) => t < T - 1.3);
  return { T, times: [...early, +(T - 1.23).toFixed(2), +(T - 0.61).toFixed(2), T] };
}
const frameHash = (img) => createHash('sha1').update(img.data).digest('hex');
/** Moves at all, when it last changes, and whether the last three frames are identical. */
export function settle(hashes, times) {
  let last = -1;
  for (let i = 1; i < hashes.length; i++) if (hashes[i] !== hashes[i - 1]) last = i;
  const n = hashes.length;
  return { moves: last > 0, settlesAt: last > 0 ? times[last] : 0, still: n >= 3 && hashes[n - 1] === hashes[n - 2] && hashes[n - 2] === hashes[n - 3] };
}

/** Frames over time with and without reduced motion, the file opened as a document and seeked to each moment. */
async function motionFrames(browser, server, file, box, { until = null } = {}) {
  const modes = {};
  for (const mode of ['no-preference', 'reduce']) {
    const context = await browser.newContext({ viewport: { width: Math.max(64, box.width), height: Math.max(64, box.height) }, deviceScaleFactor: 1, reducedMotion: mode });
    await guard(context, server);
    const page = await context.newPage();
    await page.goto(server.file(file), { waitUntil: 'load' });
    const info = await page.evaluate(DOC_PREP, '#ffffff');
    if (!info.ok) { await context.close(); throw new RenderError('opened as a document, it has no <svg> root in the SVG namespace'); }
    await page.evaluate(DOC_SIZE, [box.width, box.height]);
    modes[mode] = { context, page, info };
  }
  const end = Math.max(modes['no-preference'].info.end, modes.reduce.info.end);
  const loops = modes['no-preference'].info.loops || modes.reduce.info.loops;
  const { T, times } = motionTimes(until ?? end, loops);
  const out = { T, times, loops, box };
  for (const [mode, m] of Object.entries(modes)) {
    const frames = [];
    for (const t of times) {
      await m.page.evaluate(DOC_SEEK, t);
      frames.push(decodePng(await m.page.screenshot({ clip: { x: 0, y: 0, width: box.width, height: box.height } })));
    }
    await m.context.close();
    out[mode === 'reduce' ? 'reduced' : 'normal'] = { ...settle(frames.map(frameHash), times), frames, info: m.info };
  }
  return out;
}

const dataUrl = (img) => `data:image/png;base64,${encodePng(img).toString('base64')}`;
const escHtml = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
/** Lay out an HTML page in Chromium and save it as one PNG. */
async function htmlToPng(browser, html, out, width = 1200) {
  const page = await browser.newPage({ viewport: { width, height: 400 }, deviceScaleFactor: 1 });
  try {
    await page.setContent(html, { waitUntil: 'load' });
    // The body shrinks to its content (SHEET_CSS makes it inline-block), so the image has no empty margin.
    const size = await page.evaluate(() => { const r = document.body.getBoundingClientRect(); return { w: Math.ceil(r.width), h: Math.ceil(document.documentElement.scrollHeight) }; });
    await page.setViewportSize({ width: Math.max(320, size.w), height: Math.min(16000, Math.max(100, size.h)) });
    await fs.mkdir(path.dirname(out), { recursive: true });
    await page.screenshot({ path: out, fullPage: true });
    return pngSizeOf(readFileSync(out));
  } finally { await page.close(); }
}
const pngSizeOf = (buf) => ({ width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) });
const SHEET_CSS = 'html{background:#26282b}body{display:inline-block;margin:0;padding:24px;background:#26282b;color:#e8e8e8;font:14px/1.35 system-ui,sans-serif}h1{font-size:18px;margin:0 0 4px}h2{font-size:15px;margin:22px 0 6px}'
  + '.sub{color:#a9adb3;margin:0 0 10px}.row{display:flex;gap:18px;align-items:flex-end;margin:6px 0 2px}.lab{width:120px;flex:none;color:#a9adb3;font-size:12px}'
  + 'figure{margin:0;display:flex;flex-direction:column;align-items:center;gap:4px}figcaption{font-size:11px;color:#a9adb3;white-space:nowrap}img{display:block}'
  + '.bad{outline:3px solid #ff6b5a;outline-offset:6px}.fails{color:#ff8f80}.zoom img{image-rendering:pixelated}';

// ---------------------------------------------------------------- arguments

const COMMON = { workspace: 's', help: 'b' };
const SPECS = {
  init: { opts: { project: 's', kind: 's', brief: 's', reads: 'l', files: 'L', sizes: 's', on: 's', target: 's', engines: 's', 'no-blind': 'b', slug: 's', branch: 's' }, free: true,
    usage: 'init --project <folder> --kind <logo|icon|icons|diagram|illustration|banner|card|animation> --brief "<what it is for>" [--reads "<what a viewer must get>" ...] [--files <svg or folder>[@light|@dark] ...] [--sizes 16,32,...] [--on light,dark] [--target web|github|app|print] [--engines chromium,firefox,webkit] [--no-blind] [--slug <name>] [--branch <name>]' },
  add: { rest: 'files', opts: {}, usage: 'add -w <run> <svg or folder>[@light|@dark] ...' },
  lint: { rest: 'files', opts: { kind: 's', target: 's' }, free: true, usage: 'lint <svg> ... [--kind <kind>] [--target web|github|app|print]   or   lint -w <run>' },
  render: { opts: {}, usage: 'render -w <run>' },
  engines: { opts: {}, usage: 'engines -w <run>' },
  contrast: { opts: {}, usage: 'contrast -w <run>' },
  github: { rest: 'files', opts: { sizes: 's', kind: 's' }, free: true, usage: 'github <svg> [--sizes 16,64,...]   or   github -w <run>' },
  motion: { rest: 'files', opts: { until: 'n', size: 'n', out: 's' }, free: true, usage: 'motion <svg> [--size 256] [--until <seconds>] [--out <sheet.png>]   or   motion -w <run>' },
  minify: { pos: ['svg'], opts: { out: 's', decimals: 'i', sizes: 's', kind: 's' }, free: true, usage: 'minify <svg> [--out <file>] [--decimals 2] [--sizes 16,32,...] [-w <run>]' },
  export: { pos: ['svg'], opts: { png: 's', out: 's' }, free: true, usage: 'export <svg> --png 16,32,180,512 [--out <folder>] [-w <run>]' },
  check: { opts: {}, usage: 'check -w <run>' },
  sheet: { opts: { out: 's' }, usage: 'sheet -w <run> [--out <file.png>]' },
  audience: { opts: {}, usage: 'audience -w <run>' },
  heard: { opts: { reply: 's' }, usage: 'heard -w <run> [--reply <file with the agent\'s reply, if it wrote no reply.json>]' },
  landed: { opts: { reply: 's' }, usage: 'landed -w <run> [--reply <file with the agent\'s reply, if it wrote no reply.json>]' },
  land: { opts: { message: 'l', branch: 's' }, usage: 'land -w <run> -m "<commit message>" [--branch <name>]' },
  decide: { pos: ['text'], opts: {}, usage: 'decide -w <run> "<a call the user should check>"' },
  wait: { pos: ['text'], opts: { command: 's' }, usage: 'wait -w <run> "<what is waiting>" --command "<the exact command>"' },
  report: { opts: {}, usage: 'report -w <run>' },
};

function parseArgs(cmd, argv) {
  const spec = SPECS[cmd];
  const types = { ...COMMON, ...spec.opts };
  const out = { _: [] };
  const usage = `usage: node svg.mjs ${spec.usage}`;
  for (let i = 0; i < argv.length; i++) {
    let a = argv[i];
    if (a === '-w') a = '--workspace';
    else if (a === '-m') a = '--message';
    else if (a === '-h') a = '--help';
    if (a.startsWith('--') && a.length > 2) {
      let key = a.slice(2), val = null;
      const eq = key.indexOf('=');
      if (eq >= 0) { val = key.slice(eq + 1); key = key.slice(0, eq); }
      const t = types[key];
      if (!t) fail(`unknown option --${key} for ${cmd}.\n${usage}`);
      if (t === 'b') { out[key] = true; continue; }
      if (t === 'L') {
        const vals = val !== null ? [val] : [];
        while (val === null && i + 1 < argv.length && !argv[i + 1].startsWith('--') && argv[i + 1] !== '-w') vals.push(argv[++i]);
        if (!vals.length) fail(`--${key} needs at least one value.\n${usage}`);
        (out[key] ||= []).push(...vals);
        continue;
      }
      if (val === null) {
        if (i + 1 >= argv.length) fail(`--${key} needs a value.\n${usage}`);
        val = argv[++i];
      }
      if (t === 'n') {
        const v = Number(val);
        if (!Number.isFinite(v) || v <= 0) fail(`--${key} needs a positive number, not "${val}"`);
        out[key] = v;
      } else if (t === 'i') {
        const v = Number(val);
        if (!Number.isInteger(v) || v < 0 || v > 6) fail(`--${key} needs a whole number from 0 to 6, not "${val}"`);
        out[key] = v;
      } else if (t === 'l') (out[key] ||= []).push(val);
      else out[key] = val;
    } else out._.push(a);
  }
  if (out.help) { console.log(usage); process.exit(0); }
  const pos = spec.pos || [];
  if (spec.rest) out[spec.rest] = out._;
  else {
    if (out._.length > pos.length) fail(`unexpected argument "${out._[pos.length]}".\n${usage}`);
    pos.forEach((name, i) => { out[name] = out._[i]; });
    for (const name of pos) if (!out[name]) fail(`${cmd} needs <${name}>.\n${usage}`);
  }
  if (!spec.free && !out.workspace) fail(`${cmd} needs -w <run folder> (from init).\n${usage}`);
  return out;
}

// ---------------------------------------------------------------- runs

function bgFrom(name) {
  const n = String(name).trim().toLowerCase();
  if (NAMED_BG[n]) { const rgb = parseHex(NAMED_BG[n]); return { name: n, css: NAMED_BG[n], rgb, scheme: n }; }
  const rgb = parseHex(n);
  if (!rgb) fail(`backgrounds are light, dark or #rrggbb colours, not "${name}"`);
  const h = hex(rgb);
  return { name: h.slice(1), css: h, rgb, scheme: luminance(rgb) < 0.18 ? 'dark' : 'light' };
}
const CHECKER_BG = { name: 'checker', css: CHECKER, rgb: null, scheme: 'light', checker: true };
const parseOn = (s) => { const out = []; for (const b of String(s).split(',').filter((x) => x.trim()).map(bgFrom)) if (!out.some((o) => o.name === b.name)) out.push(b); if (!out.length) fail('--on needs at least one background'); return out; };
function parseSizes(s, what = '--sizes') {
  const v = String(s).split(',').map((x) => x.trim()).filter(Boolean).map(Number);
  if (!v.length || v.some((n) => !Number.isInteger(n) || n < 8 || n > 4096)) fail(`${what} takes whole pixel sizes from 8 to 4096, like 16,32,64, not "${s}"`);
  return [...new Set(v)].sort((a, b) => a - b);
}
function parseEngines(s) {
  const v = String(s).split(',').map((x) => x.trim().toLowerCase()).filter(Boolean);
  for (const e of v) if (!ENGINES.includes(e)) fail(`--engines takes chromium, firefox and webkit, not "${e}"`);
  if (!v.includes('chromium')) fail('--engines must include chromium: every check needs it');
  return ENGINES.filter((e) => v.includes(e));
}
/** "logo-dark.svg@dark" is a file meant only for dark backgrounds; "icon@2x.svg" is just a file name. */
function parseFileArg(s, on) {
  const at = s.lastIndexOf('@');
  const tail = at > 0 ? s.slice(at + 1) : '';
  if (!tail || /[\\/.]/.test(tail)) return { path: path.resolve(s), on: null };
  const p = path.resolve(s.slice(0, at));
  const names = tail.split(',').map((x) => bgFrom(x).name);
  for (const n of names) if (!on.some((b) => b.name === n)) fail(`${s}: ${n} isn't one of the run's backgrounds (${on.map((b) => b.name).join(', ')})`);
  return { path: p, on: names };
}

/** The run's graphics as they are now: folders expanded to their .svg files, each with its key, place in the project and backgrounds. */
function resolveFiles(cfg) {
  const out = [], seen = new Set();
  for (const f of cfg.files) {
    let paths = [f.path];
    if (existsSync(f.path) && statSync(f.path).isDirectory()) paths = readdirSync(f.path).filter((n) => /\.svg$/i.test(n)).sort().map((n) => path.join(f.path, n));
    for (const p of paths) {
      const c = canonical(p);
      const id = process.platform === 'win32' ? c.toLowerCase() : c;
      if (seen.has(id)) continue;
      seen.add(id);
      out.push({ abs: p, canon: c, rel: slash(path.relative(cfg.project, c)) || path.basename(c), exists: existsSync(p) && statSync(p).isFile(),
        on: f.on ? cfg.on.filter((b) => f.on.includes(b.name)) : cfg.on });
    }
  }
  const used = new Set();
  for (const it of out) {
    const k = slugify(it.rel.replace(/\.svg$/i, '').replace(/^(\.\.\/)+/, '').replace(/\//g, '-'), 'graphic');
    let kk = k;
    for (let i = 2; used.has(kk); i++) kk = `${k}-${i}`;
    used.add(kk);
    it.key = kk;
  }
  for (const it of out) {
    const m = /^(.*)-small\.svg$/i.exec(it.canon);
    const big = m && out.find((o) => o !== it && o.canon.toLowerCase() === `${m[1]}.svg`.toLowerCase());
    if (big) { it.smallFor = big.key; big.small = it.key; }
  }
  return out;
}

function natural(lint) {
  const vb = lint.viewBox;
  if (lint.width && lint.height) return [lint.width, lint.height];
  if (vb && lint.width) return [lint.width, lint.width * vb.h / vb.w];
  if (vb && lint.height) return [lint.height * vb.w / vb.h, lint.height];
  if (vb) return [vb.w, vb.h];
  return [300, 150];
}
function defaultSizes(kind, lint) {
  if (kind === 'icon' || kind === 'icons') return [16, 24, 32, 48, 96];
  if (kind === 'logo') return [16, 32, 64, 256, 1024];
  if (kind === 'animation') return [48, 128, 512];
  const [w, h] = natural(lint);
  const real = Math.round(Math.min(2048, Math.max(64, w, h)));
  return [...new Set([Math.round(real / 2), real])].sort((a, b) => a - b);
}
/** A size is the graphic's longer side in CSS pixels; the other side follows its viewBox. */
function boxFor(size, lint) {
  const [w, h] = lint.viewBox ? [lint.viewBox.w, lint.viewBox.h] : natural(lint);
  return w >= h ? { size, width: size, height: Math.max(1, Math.round(size * h / w)) } : { size, width: Math.max(1, Math.round(size * w / h)), height: size };
}
const sizesFor = (cfg, lint) => cfg.sizes || defaultSizes(cfg.kind, lint);

function fingerprint(cfg, files) {
  const h = createHash('sha1');
  h.update(JSON.stringify([cfg.kind, cfg.target, cfg.sizes, cfg.on.map((b) => b.name), cfg.engines]));
  for (const it of files) {
    h.update(`${it.rel}\0${it.on.map((b) => b.name).join(',')}\0`);
    h.update(it.exists ? readFileSync(it.abs) : 'missing');
    h.update('\0');
  }
  return h.digest('hex').slice(0, 16);
}

async function loadRun(ws) {
  const dir = path.resolve(ws);
  const cfg = loadJson(path.join(dir, 'config.json'), null);
  if (!cfg || cfg.skill !== 'killer-svg') fail(`no killer-svg run in ${dir}; run init first`);
  return { ws: dir, cfg, save: () => saveJson(path.join(dir, 'config.json'), cfg) };
}
function gitStatus(cfg, files) {
  if (!cfg.git.top) return null;
  const rels = files.filter((it) => inside(it.canon, cfg.git.top)).map((it) => path.relative(cfg.git.top, it.canon));
  if (!rels.length) return '';
  const r = git(cfg.git.top, ['status', '--porcelain=v1', '--untracked-files=all', '--', ...rels]);
  return r.ok ? r.out : null;
}
function recordWait(cfg, text, command) {
  if (!cfg.waiting.some((w) => w.command === command)) cfg.waiting.push({ text, command });
}

// ---------------------------------------------------------------- init, add

async function cmdInit(a) {
  const project = path.resolve(a.project || '.');
  if (!existsSync(project) || !statSync(project).isDirectory()) fail(`no folder ${project}`);
  if (!a.kind) fail(`init needs --kind: one of ${KINDS.join(', ')}`);
  if (!KINDS.includes(a.kind)) fail(`--kind is one of ${KINDS.join(', ')}, not "${a.kind}"`);
  const brief = (a.brief || '').trim();
  if (!brief) fail('init needs --brief "<what the graphic is for, in the user\'s words>"');
  const reads = (a.reads || []).map((r) => r.trim()).filter(Boolean);
  if (reads.length > 8) fail(`${reads.length} reads is too many to test; keep the ones that matter (at most 8)`);
  const target = a.target || 'web';
  if (!TARGETS.includes(target)) fail(`--target is one of ${TARGETS.join(', ')}, not "${target}"`);
  const on = parseOn(a.on || (target === 'print' ? 'light' : 'light,dark'));
  const sizes = a.sizes ? parseSizes(a.sizes) : null;
  const engines = parseEngines(a.engines || ENGINES.join(','));
  const shown = git(project, ['rev-parse', '--show-toplevel']);
  const isGit = shown.ok && !!shown.out.trim();
  const top = isGit ? canonical(shown.out.trim()) : canonical(project);
  const head = isGit ? git(top, ['rev-parse', '--verify', '-q', 'HEAD^{commit}']).out.trim() || null : null;
  const startBranch = isGit ? git(top, ['symbolic-ref', '-q', '--short', 'HEAD']).out.trim() || null : null;
  const name = slugify(a.slug || a.kind);
  const home = path.join(runsHome(), projectKey(top, isGit));
  let ws = path.join(home, `${today()}-${name}`);
  for (let i = 2; existsSync(path.join(ws, 'config.json')); i++) ws = path.join(home, `${today()}-${name}-${i}`);
  const cfg = {
    skill: 'killer-svg', version: 1, run_id: randomBytes(3).toString('hex'), created: now(), project: top,
    kind: a.kind, brief, reads, target, on, sizes, engines, blind: !a['no-blind'],
    files: (a.files || []).map((f) => parseFileArg(f, on)),
    git: { top: isGit ? top : null, head, startBranch },
    branch: a.branch || null, branchNamed: !!a.branch, commits: [], decisions: [], waiting: [],
    checks: [], render: null, rounds: [], outputs: [], minified: [], replies: [], blocked: null,
  };
  await saveJson(path.join(ws, 'config.json'), cfg);
  const files = resolveFiles(cfg);
  console.log(`Run folder: ${ws}`);
  console.log(`Project: ${top}${head ? ` (${startBranch || 'detached'} at ${head.slice(0, 10)})` : isGit ? ' (no commits yet)' : ' (not a git repository)'}`);
  console.log(`Kind: ${a.kind} · target ${target} · on ${on.map((b) => b.name).join(', ')} · sizes ${sizes ? sizes.join(', ') : `by kind (${a.kind})`} · engines ${engines.join(', ')} · blind viewer ${cfg.blind ? 'on' : 'off'}`);
  console.log(`Brief: ${brief}`);
  console.log(reads.length ? `Reads (what a viewer must get):\n${reads.map((r, i) => `  ${i + 1}. ${r}`).join('\n')}` : `Reads: none set${cfg.blind ? ' (the blind viewer check needs them: what must someone who has never seen it get from it?)' : ''}`);
  if (files.length) for (const it of files) console.log(`  ${it.rel}${it.exists ? '' : ' (not written yet)'}${inside(it.canon, top) ? '' : ' (outside the project: land won\'t commit it)'}`);
  else console.log(`Files: none yet. Add them with: node svg.mjs add -w "${ws}" <svg or folder>`);
  if (isGit) console.log(`Git: land commits on ${a.branch || `killer-svg/${today()}`}`);
}

async function cmdAdd(a) {
  const run = await loadRun(a.workspace);
  if (!a.files.length) fail(`add needs at least one svg or folder.\nusage: node svg.mjs ${SPECS.add.usage}`);
  for (const f of a.files) {
    const p = parseFileArg(f, run.cfg.on);
    if (!run.cfg.files.some((x) => canonical(x.path) === canonical(p.path))) run.cfg.files.push(p);
    else run.cfg.files = run.cfg.files.map((x) => (canonical(x.path) === canonical(p.path) ? p : x));
  }
  await run.save();
  const files = resolveFiles(run.cfg);
  console.log(`The run has ${plural(files.length, 'file')}:`);
  for (const it of files) console.log(`  ${it.rel}${it.exists ? '' : ' (not written yet)'}${it.on.length < run.cfg.on.length ? ` (on ${it.on.map((b) => b.name).join(', ')} only)` : ''}${it.smallFor ? ' (the small-size variant)' : ''}`);
}

// ---------------------------------------------------------------- lint

function lintFiles(cfg, files) {
  const infos = {}, issues = [];
  for (const it of files) {
    if (!it.exists) { issues.push({ file: it.rel, check: 'lint', rule: 'missing', level: 'FAIL', message: "isn't written yet" }); continue; }
    const r = lintSvg(readFileSync(it.abs, 'utf8'), { kind: cfg.kind, target: cfg.target });
    infos[it.key] = r;
    for (const i of r.issues) issues.push({ file: it.rel, check: 'lint', ...i });
  }
  return { infos, issues };
}

function printIssues(issues, { infos = true } = {}) {
  for (const lvl of ['FAIL', 'WARN', ...(infos ? ['INFO'] : [])]) {
    for (const i of issues.filter((x) => x.level === lvl)) console.log(`${lvl.padEnd(5)} ${i.file ? `${i.file}: ` : ''}${i.message}`);
  }
}

async function cmdLint(a) {
  let issues;
  if (a.workspace) {
    const run = await loadRun(a.workspace);
    const files = resolveFiles(run.cfg);
    if (!files.length) fail('the run has no files yet: add them with svg.mjs add');
    issues = lintFiles(run.cfg, files).issues;
  } else {
    if (!a.files.length) fail(`lint needs svg files, or -w <run>.\nusage: node svg.mjs ${SPECS.lint.usage}`);
    if (a.kind && !KINDS.includes(a.kind)) fail(`--kind is one of ${KINDS.join(', ')}`);
    if (a.target && !TARGETS.includes(a.target)) fail(`--target is one of ${TARGETS.join(', ')}`);
    issues = [];
    for (const f of a.files) {
      const p = path.resolve(f);
      if (!existsSync(p)) fail(`no file ${p}`);
      for (const i of lintSvg(readFileSync(p, 'utf8'), { kind: a.kind || null, target: a.target || 'web' }).issues) issues.push({ file: path.basename(p), ...i });
    }
  }
  printIssues(issues);
  const fails = issues.filter((i) => i.level === 'FAIL').length;
  console.log(fails ? `\n${plural(fails, 'failure')}.` : '\nNo failures.');
  return fails ? 1 : 0;
}

// ---------------------------------------------------------------- render

const pngPath = (ws, engine, key, name) => path.join(ws, 'renders', engine, key, `${name}.png`);
const readPng = (p) => decodePng(readFileSync(p));
async function writePng(p, img) { await fs.mkdir(path.dirname(p), { recursive: true }); await fs.writeFile(p, encodePng(img)); }

/** What each file needs rendering: its sizes and boxes, backgrounds, whether it moves, and its text-free twin. */
async function renderItems(run, files, infos) {
  const items = [], issues = [];
  for (const it of files) {
    const lint = infos[it.key];
    if (!lint) continue;
    if (!lint.parsed || lint.issues.some((i) => i.rule === 'root')) {
      issues.push({ file: it.rel, check: 'render', level: 'INFO', message: 'not rendered: fix the XML first' });
      continue;
    }
    const sizes = sizesFor(run.cfg, lint);
    const item = { ...it, lint, sizes, boxes: sizes.map((s) => boxFor(s, lint)), animated: lint.animated, text: lint.hasText };
    if (lint.hasText) {
      item.notext = path.join(run.ws, 'work', `${it.key}-notext.svg`);
      await fs.mkdir(path.dirname(item.notext), { recursive: true });
      await fs.writeFile(item.notext, withoutText(readFileSync(it.abs, 'utf8')), 'utf8');
    }
    items.push(item);
  }
  return { items, issues };
}

async function renderEngine(engine, browser, server, ws, items) {
  const context = await browser.newContext({ deviceScaleFactor: 1, viewport: { width: 256, height: 256 } });
  await guard(context, server);
  const page = await context.newPage();
  const results = {};
  try {
    for (const it of items) {
      const dir = path.join(ws, 'renders', engine, it.key);
      await fs.rm(dir, { recursive: true, force: true });
      try {
        for (const bg of [...it.on, CHECKER_BG]) {
          await page.emulateMedia({ colorScheme: bg.scheme, reducedMotion: it.animated ? 'reduce' : 'no-preference' });
          const shots = it.animated ? (await docShots(page, server, it.abs, it.boxes, { bg: bg.css })).shots : await imgShots(page, server, it.abs, it.boxes, { bg: bg.css });
          for (let i = 0; i < shots.length; i++) await writePng(pngPath(ws, engine, it.key, `${it.sizes[i]}-${bg.name}`), shots[i]);
          if (it.notext && !bg.checker) {
            const nt = it.animated ? (await docShots(page, server, it.notext, it.boxes, { bg: bg.css })).shots : await imgShots(page, server, it.notext, it.boxes, { bg: bg.css });
            for (let i = 0; i < nt.length; i++) await writePng(pngPath(ws, engine, it.key, `notext-${it.sizes[i]}-${bg.name}`), nt[i]);
          }
        }
        if (engine === 'chromium') {
          for (const scheme of [...new Set(it.on.map((b) => b.scheme))]) {
            await page.emulateMedia({ colorScheme: scheme, reducedMotion: it.animated ? 'reduce' : 'no-preference' });
            const clear = it.animated ? (await docShots(page, server, it.abs, it.boxes, { clear: true })).shots : await imgShots(page, server, it.abs, it.boxes, { clear: true });
            for (let i = 0; i < clear.length; i++) await writePng(pngPath(ws, engine, it.key, `${it.sizes[i]}-clear-${scheme}`), clear[i]);
          }
        }
        results[it.key] = { ok: true };
      } catch (e) {
        results[it.key] = { ok: false, error: snippet(String(e.message).split('\n')[0], 200) };
      }
    }
  } finally { await context.close(); }
  return results;
}

/** Contact sheet for one file: each engine and background in a row, every size at 1x, and the small sizes enlarged. */
async function fileSheet(browser, ws, it, engines) {
  const cell = (engine, name, b) => {
    const p = pngPath(ws, engine, it.key, `${b.size}-${name}`);
    if (!existsSync(p)) return '';
    const big = Math.max(b.width, b.height) > 256, k = big ? 256 / Math.max(b.width, b.height) : 1;
    return `<figure><img src="data:image/png;base64,${readFileSync(p).toString('base64')}" width="${Math.round(b.width * k)}" height="${Math.round(b.height * k)}"><figcaption>${b.size} px${big ? ' (shown at 256)' : ''}</figcaption></figure>`;
  };
  let html = `<!doctype html><meta charset="utf-8"><style>${SHEET_CSS}</style><h1>${escHtml(it.rel)}</h1><p class="sub">${escHtml(it.sizes.join(', '))} px · ${escHtml(engines.map((e) => TITLE[e]).join(', '))}${it.animated ? ' · animated: the reduced-motion still' : ''}</p>`;
  for (const e of engines) for (const bg of [...it.on, CHECKER_BG]) html += `<div class="row"><div class="lab">${TITLE[e]} · ${escHtml(bg.name)}</div>${it.boxes.map((b) => cell(e, bg.name, b)).join('')}</div>`;
  const small = it.boxes.filter((b) => b.size <= 48);
  if (small.length) {
    html += `<h2>Small sizes enlarged ${6}x (Chromium)</h2>`;
    for (const bg of it.on) {
      html += `<div class="row zoom"><div class="lab">${escHtml(bg.name)}</div>${small.map((b) => {
        const p = pngPath(ws, 'chromium', it.key, `${b.size}-${bg.name}`);
        return existsSync(p) ? `<figure><img src="data:image/png;base64,${readFileSync(p).toString('base64')}" width="${b.width * 6}" height="${b.height * 6}"><figcaption>${b.size} px</figcaption></figure>` : '';
      }).join('')}</div>`;
    }
  }
  const out = path.join(ws, 'renders', `${it.key}-sheet.png`);
  await htmlToPng(browser, html, out);
  return out;
}

/** Render every file in every engine that starts. Returns the issues; records cfg.render. */
async function doRender(run, files, infos, env, server, { quiet = false } = {}) {
  const { cfg, ws } = run;
  const { items, issues } = await renderItems(run, files, infos);
  const engines = ENGINES.filter((e) => env.browsers[e]);
  const results = {};
  await Promise.all(engines.map(async (e) => { results[e] = await renderEngine(e, env.browsers[e], server, ws, items); }));
  const record = { at: now(), fingerprint: fingerprint(cfg, files), engines: env.status, files: {} };
  for (const it of items) {
    const per = Object.fromEntries(engines.map((e) => [e, results[e][it.key]]));
    const ok = per.chromium?.ok;
    for (const e of engines) if (!per[e].ok) issues.push({ file: it.rel, check: 'render', level: 'FAIL', message: `${TITLE[e]} can't draw it: ${per[e].error}` });
    let sheet = null;
    if (ok) sheet = await fileSheet(env.browsers.chromium, ws, it, engines.filter((e) => per[e].ok));
    record.files[it.key] = { rel: it.rel, ok: !!ok, sizes: it.sizes, boxes: it.boxes, animated: it.animated, text: it.text, on: it.on.map((b) => b.name), engines: per, sheet };
  }
  for (const e of ['firefox', 'webkit']) {
    const s = env.status[e];
    if (s.status !== 'checked') issues.push({ file: null, check: 'engines', level: s.reason === 'not asked for' ? 'INFO' : 'WARN', message: `${TITLE[e]}: not checked (${s.reason})` });
  }
  if (env.installCommand) recordWait(cfg, `Install Playwright's own ${list(env.missing.map((e) => TITLE[e]))} so the graphics can be checked in ${env.missing.length > 1 ? 'them' : 'it'} too (needs the network; installed, Firefox takes about 350 MB and WebKit about 170 MB)`, env.installCommand);
  cfg.render = record;
  await run.save();
  if (!quiet) {
    const n = items.length;
    console.log(`render: ${plural(n, 'file')} in ${list(engines.map((e) => `${TITLE[e]} ${env.status[e].version}`))} -> ${path.join(ws, 'renders')}`);
    for (const it of items) if (record.files[it.key].sheet) console.log(`  ${it.rel}: ${it.sizes.join(', ')} px on ${it.on.map((b) => b.name).join(', ')} and a checkerboard; sheet ${record.files[it.key].sheet}`);
    printIssues(issues);
  }
  return { issues, items };
}

async function withEnv(run, fn) {
  const env = await openEngines(run.cfg.engines, run);
  const server = await startServer();
  try { return await fn(env, server); } finally { await env.close(); await server.close(); }
}
async function chromiumOnly(fn, run = null) {
  let browser;
  try { browser = await launchChromium(); } catch (e) {
    if (run) { run.cfg.blocked = `the browser couldn't start: ${e.message}`; await run.save(); }
    throw e instanceof Fail ? e : new Fail(`couldn't start Chromium: ${e.message}`);
  }
  const server = await startServer();
  try { return await fn(browser, server); } finally { await browser.close().catch(() => {}); await server.close(); }
}

function needFiles(cfg) {
  const files = resolveFiles(cfg);
  if (!files.length) fail('the run has no files yet: add them with svg.mjs add -w <run> <svg>');
  return files;
}

async function cmdRender(a) {
  const run = await loadRun(a.workspace);
  const files = needFiles(run.cfg);
  const { infos, issues } = lintFiles(run.cfg, files);
  const missing = issues.filter((i) => i.rule === 'missing');
  printIssues(missing);
  const r = await withEnv(run, (env, server) => doRender(run, files, infos, env, server));
  return r.issues.some((i) => i.level === 'FAIL') || missing.length ? 1 : 0;
}

/** engines and contrast read the renders; if they're missing or older than the files, render first. */
async function currentRenders(run) {
  const files = needFiles(run.cfg);
  if (run.cfg.render && run.cfg.render.fingerprint === fingerprint(run.cfg, files)) return { files, rendered: [] };
  console.log('The renders are missing or older than the files: rendering first.');
  const { infos } = lintFiles(run.cfg, files);
  const rendered = (await withEnv(run, (env, server) => doRender(run, files, infos, env, server, { quiet: true }))).issues;
  return { files, rendered };
}

// ---------------------------------------------------------------- engines: pixel comparison

const sizesText = (xs) => `${list([...new Set(xs)].map(String))} px`;
async function compareEngines(run, files) {
  const { cfg, ws } = run;
  const R = cfg.render;
  const issues = [];
  const others = ['firefox', 'webkit'].filter((e) => R.engines[e]?.status === 'checked');
  for (const it of files) {
    const rf = R.files[it.key];
    if (!rf || !rf.ok) continue;
    for (const e of others) {
      if (!rf.engines[e]?.ok) continue;
      const recs = [];
      for (const b of rf.boxes) {
        for (const bg of it.on) {
          const A = readPng(pngPath(ws, 'chromium', it.key, `${b.size}-${bg.name}`)), B = readPng(pngPath(ws, e, it.key, `${b.size}-${bg.name}`));
          const c = compareImages(A, B, bg.rgb);
          let v = enginesVerdict(c), textOnly = false;
          if (v !== 'PASS' && rf.text) {
            const c2 = compareImages(readPng(pngPath(ws, 'chromium', it.key, `notext-${b.size}-${bg.name}`)), readPng(pngPath(ws, e, it.key, `notext-${b.size}-${bg.name}`)), bg.rgb);
            if (enginesVerdict(c2) === 'PASS') { textOnly = true; v = 'WARN'; }
          }
          recs.push({ size: b.size, bg: bg.name, v, c, textOnly, A, B });
        }
      }
      const fails = recs.filter((r) => r.v === 'FAIL'), text = recs.filter((r) => r.textOnly), warns = recs.filter((r) => r.v === 'WARN' && !r.textOnly);
      const diffOf = async (r) => {
        const p = path.join(ws, 'engines', `${it.key}-${e}-${r.size}-${r.bg}.png`);
        await writePng(p, diffImage(r.A, r.B, r.c));
        return p;
      };
      if (fails.length) {
        const w = fails.reduce((x, y) => (y.c.share > x.c.share ? y : x));
        const where = w.c.box ? `, around x ${w.c.box.x0}-${w.c.box.x1}, y ${w.c.box.y0}-${w.c.box.y1} of the ${w.size} px render` : '';
        issues.push({ file: it.rel, check: 'engines', level: 'FAIL', engine: e, message: `${TITLE[e]} draws it differently from Chromium at ${sizesText(fails.map((r) => r.size))} (${list([...new Set(fails.map((r) => r.bg))])}): at ${w.size} px ${pct(w.c.share)} of its pixels differ${where}. Usually a feature ${TITLE[e]} doesn't support; look at what's drawn there. Diff (Chromium, ${TITLE[e]}, the differences in red): ${await diffOf(w)}` });
      }
      if (warns.length) {
        const w = warns.reduce((x, y) => (y.c.mean > x.c.mean ? y : x));
        issues.push({ file: it.rel, check: 'engines', level: 'WARN', engine: e, message: `${TITLE[e]} differs a little from Chromium at ${sizesText(warns.map((r) => r.size))}: at ${w.size} px on ${w.bg}, ${pct(w.c.share)} of pixels differ and the colours are ${Math.round(w.c.mean)} levels apart on average (blur, shadows and blending often do this at small sizes). Diff: ${await diffOf(w)}` });
      }
      if (text.length) {
        issues.push({ file: it.rel, check: 'engines', level: 'WARN', engine: e, message: `its text draws differently in ${TITLE[e]} at ${sizesText(text.map((r) => r.size))}, though the shapes match: that engine fills the font with a face of its own. Outline the text to paths or embed the font if the exact letters matter` });
      }
      if (!fails.length && !warns.length && !text.length) {
        const worst = Math.max(...recs.map((r) => r.c.share));
        issues.push({ file: it.rel, check: 'engines', level: 'INFO', engine: e, message: `${TITLE[e]} matches Chromium at every size and background (at most ${pct(worst)} of pixels apart)` });
      }
    }
  }
  return issues;
}

async function cmdEngines(a) {
  const run = await loadRun(a.workspace);
  const { files, rendered } = await currentRenders(run);
  const issues = [...rendered.filter((i) => i.check === 'engines' || i.level === 'FAIL'), ...(await compareEngines(run, files))];
  const R = run.cfg.render;
  console.log(`engines: ${ENGINES.map((e) => `${TITLE[e]} ${R.engines[e].status === 'checked' ? R.engines[e].version : 'not checked'}`).join(' · ')}`);
  printIssues(issues);
  if (!['firefox', 'webkit'].some((e) => R.engines[e].status === 'checked')) console.log('Only Chromium was available, so nothing was compared: the other engines are not checked, not passed.');
  return issues.some((i) => i.level === 'FAIL') ? 1 : 0;
}

// ---------------------------------------------------------------- contrast

async function measureContrast(run, files) {
  const { cfg, ws } = run;
  const issues = [];
  for (const it of files) {
    const rf = cfg.render.files[it.key];
    if (!rf || !rf.ok) continue;
    const largest = rf.boxes[rf.boxes.length - 1];
    for (const bg of it.on) {
      let worst = null, seen = 0, coverage = 0, main = [];
      for (const b of rf.boxes) {
        const s = standOut(readPng(pngPath(ws, 'chromium', it.key, `${b.size}-clear-${bg.scheme}`)), bg.rgb);
        seen += s.opaque;
        // The main colours come from the largest render: at small sizes most pixels are anti-aliased blends.
        if (b === largest) { coverage = s.coverage; main = s.dominant; }
        if (s.opaque && (!worst || s.share > worst.share)) worst = { ...s, size: b.size };
      }
      const on = `${bg.name}${bg.name === 'light' || bg.name === 'dark' ? ` (${bg.css})` : ''}`;
      if (!seen) { issues.push({ file: it.rel, check: 'contrast', level: 'FAIL', message: `nothing of it is visible on ${on} at any size: every pixel is transparent or nearly so` }); continue; }
      const colours = (main.length ? main : worst.dominant).map((d) => `${d.hex} ${d.ratio.toFixed(1)}:1`).join(', ');
      const full = coverage >= LIMITS.fullBleed;
      if (!full && worst.share >= LIMITS.vanishShare) {
        issues.push({ file: it.rel, check: 'contrast', level: 'FAIL', bg: bg.name, message: `disappears on ${on}: at ${worst.size} px, ${pct(worst.share)} of it is within ${LIMITS.blendRatio}:1 of the background (its main colours: ${colours}). Give it a colour that stands out there, an outline or a tile, or make a version for ${bg.name} backgrounds and add it as <file>@${bg.name}` });
      } else if (worst.share >= LIMITS.fadeShare) {
        issues.push({ file: it.rel, check: 'contrast', level: 'WARN', bg: bg.name, message: full
          ? `its edge melts into ${on}: it fills its box, and ${pct(worst.share)} of it is within ${LIMITS.blendRatio}:1 of the background at ${worst.size} px (main colours ${colours}). A border or a slightly different tile brings the edge back`
          : `${pct(worst.share)} of it melts into ${on} at ${worst.size} px (main colours ${colours}); only the rest stands out. Check the sheet` });
      } else {
        // WCAG 1.4.11: the parts of a graphic needed to understand it want 3:1 against what's next to them.
        const weak = full ? [] : main.filter((c) => c.share >= LIMITS.weakShare && c.ratio < LIMITS.graphicRatio);
        if (weak.length) {
          issues.push({ file: it.rel, check: 'contrast', level: 'WARN', bg: bg.name, message: `${list(weak.map((c) => `${c.hex} (${pct(c.share)} of it)`))} ${weak.length === 1 ? 'is' : 'are'} only ${list(weak.map((c) => `${c.ratio.toFixed(1)}:1`))} against ${on}, under the 3:1 graphics need (WCAG 1.4.11): that part sinks into the background, worst at small sizes. ${bg.scheme === 'dark' ? 'Lighten' : 'Darken'} it, outline it, or make a version for ${bg.name} backgrounds` });
        } else {
          issues.push({ file: it.rel, check: 'contrast', level: 'INFO', bg: bg.name, message: `stands out on ${on}: at worst (${worst.size} px) ${pct(1 - worst.share)} of it is clear of the background; main colours ${colours}` });
        }
      }
    }
  }
  return issues;
}

async function cmdContrast(a) {
  const run = await loadRun(a.workspace);
  const { files, rendered } = await currentRenders(run);
  const issues = [...rendered.filter((i) => i.level === 'FAIL'), ...(await measureContrast(run, files))];
  printIssues(issues);
  return issues.some((i) => i.level === 'FAIL') ? 1 : 0;
}

// ---------------------------------------------------------------- github: as an <img> under a strict CSP, and opened directly

const GITHUB_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'";
async function githubOne(browser, server, file, lint, boxes, bgs, outDir, label) {
  const issues = [];
  const external = [], local = [];
  const self = server.file(file);
  const context = await browser.newContext({ deviceScaleFactor: 1 });
  await guard(context, server, external);
  const page = await context.newPage();
  page.on('request', (r) => { const u = r.url(); if (u.startsWith(server.base + '/d/') && u !== self && !/\/favicon\.ico$/i.test(u)) local.push(decodeURIComponent(u.split('/').pop())); });
  const diffs = [];
  try {
    for (const bg of bgs) {
      await page.emulateMedia({ colorScheme: bg.scheme });
      const A = await imgShots(page, server, file, boxes, { bg: bg.css, csp: GITHUB_CSP });
      const { shots: B } = await docShots(page, server, file, boxes, { bg: bg.css, t: 0 });
      if (lint.animated) continue;
      for (let i = 0; i < boxes.length; i++) {
        const c = compareImages(A[i], B[i], bg.rgb, 8);
        if (!sameRender(c)) diffs.push({ size: boxes[i].size, bg: bg.name, c, A: A[i], B: B[i] });
      }
    }
  } finally { await context.close(); }
  for (const u of [...new Set(external)]) issues.push({ level: 'FAIL', message: `it loads ${snippet(u, 80)} from the network; GitHub shows an SVG through <img>, which loads nothing, so that part is missing there` });
  for (const u of [...new Set(local)]) issues.push({ level: 'FAIL', message: `it loads ${u}, a file beside it; through <img> that never loads. Embed it as a data: URL or draw it` });
  if (diffs.length) {
    const w = diffs.reduce((x, y) => (y.c.diff > x.c.diff ? y : x));
    const p = path.join(outDir, `${label}-${w.size}-${w.bg}.png`);
    await writePng(p, diffImage(w.B, w.A, w.c));
    issues.push({ level: 'FAIL', message: `shown through <img> under GitHub's rules it looks different from the file opened directly, at ${sizesText(diffs.map((d) => d.size))} (${w.c.diff} pixels at ${w.size} px on ${w.bg}): it depends on something an image can't use. Diff (opened directly, as an image, the differences in red): ${p}` });
  }
  if (!issues.length) issues.push({ level: 'INFO', message: lint.animated ? 'loads nothing outside itself (it animates, so the two renders were compared by what they load, not by pixels)' : 'renders the same through <img> under a strict Content-Security-Policy as opened directly, and loads nothing outside itself' });
  return issues;
}

async function githubFiles(browser, server, run, items) {
  const issues = [];
  for (const it of items) {
    const out = run ? path.join(run.ws, 'github') : path.join(os.tmpdir(), 'killer-svg-github');
    for (const i of await githubOne(browser, server, it.abs, it.lint, it.boxes, it.on, out, it.key)) issues.push({ file: it.rel, check: 'github', ...i });
  }
  return issues;
}

/** A file given on the command line, shaped like a run's item. */
function looseItem(file, { kind = null, target = 'web', sizes = null } = {}) {
  const abs = path.resolve(file);
  if (!existsSync(abs)) fail(`no file ${abs}`);
  const lint = lintSvg(readFileSync(abs, 'utf8'), { kind, target });
  if (!lint.parsed || lint.issues.some((i) => i.rule === 'root')) { printIssues(lint.issues.filter((i) => i.level === 'FAIL').map((i) => ({ ...i, file: path.basename(abs) }))); fail(`${abs} can't be rendered until its XML is fixed`, 1); }
  const sz = sizes || defaultSizes(kind || 'logo', lint);
  const on = lint.colorScheme ? [bgFrom('light'), bgFrom('dark')] : [bgFrom('light')];
  return { abs, canon: canonical(abs), rel: path.basename(abs), key: slugify(path.basename(abs).replace(/\.svg$/i, '')), lint, sizes: sz, boxes: sz.map((s) => boxFor(s, lint)), on, animated: lint.animated };
}

async function runItems(run, filter) {
  const files = needFiles(run.cfg);
  const { infos } = lintFiles(run.cfg, files);
  const { items } = await renderItems(run, files, infos);
  return items.filter(filter);
}

async function cmdGithub(a) {
  let issues;
  if (a.workspace) {
    const run = await loadRun(a.workspace);
    const items = await runItems(run, () => true);
    issues = await chromiumOnly((b, s) => githubFiles(b, s, run, items), run);
    for (const it of items) for (const i of it.lint.issues) if (i.level === 'FAIL' && ['script', 'external', 'font', 'foreign-object'].includes(i.rule)) issues.push({ file: it.rel, check: 'lint', ...i });
    if (run.cfg.target !== 'github') {
      const extra = items.flatMap((it) => lintSvg(readFileSync(it.abs, 'utf8'), { kind: run.cfg.kind, target: 'github' }).issues.filter((i) => i.rule === 'foreign-object' && i.level === 'FAIL').map((i) => ({ file: it.rel, ...i })));
      issues.push(...extra);
    }
  } else {
    if (a.files.length !== 1) fail(`github takes one svg, or -w <run>.\nusage: node svg.mjs ${SPECS.github.usage}`);
    const it = looseItem(a.files[0], { kind: a.kind || null, target: 'github', sizes: a.sizes ? parseSizes(a.sizes) : null });
    issues = await chromiumOnly((b, s) => githubFiles(b, s, null, [it]));
    for (const i of it.lint.issues) if (i.level === 'FAIL') issues.push({ file: it.rel, check: 'lint', ...i });
  }
  printIssues(issues);
  return issues.some((i) => i.level === 'FAIL') ? 1 : 0;
}

// ---------------------------------------------------------------- motion

function motionBox(it, size) {
  const want = size || Math.min(512, Math.max(...it.sizes));
  return boxFor(want, it.lint);
}
async function motionSheet(browser, m, out, label) {
  const k = Math.min(1, 120 / Math.max(m.box.width, m.box.height));
  const row = (name, r) => `<div class="row"><div class="lab">${name}</div>${r.frames.map((f, i) => `<figure><img src="${dataUrl(f)}" width="${Math.round(m.box.width * k)}" height="${Math.round(m.box.height * k)}"><figcaption>${m.times[i]}s</figcaption></figure>`).join('')}</div>`;
  const html = `<!doctype html><meta charset="utf-8"><style>${SHEET_CSS}.row{flex-wrap:wrap}</style><h1>${escHtml(label)}: frames over time</h1><p class="sub">Opened as a document with every animation seeked to each moment. Top: motion allowed; bottom: prefers-reduced-motion: reduce.</p>${row('motion allowed', m.normal)}${row('reduced motion', m.reduced)}`;
  await htmlToPng(browser, html, out);
  return out;
}
async function motionIssues(browser, server, it, { until = null, size = null, out }) {
  const m = await motionFrames(browser, server, it.abs, motionBox(it, size), { until });
  const sheet = await motionSheet(browser, m, out, it.rel);
  const issues = [];
  const tail = m.times.slice(-3).map((t) => `${t}s`);
  if (!m.reduced.still) issues.push({ level: 'FAIL', message: `with reduced motion on it never stops: the frames at ${list(tail)} still differ. Stop or hide every animation under @media (prefers-reduced-motion: reduce). Frames: ${sheet}` });
  else if (m.reduced.moves && m.reduced.settlesAt > 1) issues.push({ level: 'WARN', message: `with reduced motion on it still moves for about ${m.reduced.settlesAt}s before it stops; keep the reduced-motion version still, or to a short fade. Frames: ${sheet}` });
  const normal = !m.normal.moves ? 'nothing visibly moves' : m.normal.still ? `it moves and settles by about ${m.normal.settlesAt}s` : 'it moves and keeps moving (it loops)';
  const reduced = !m.reduced.moves ? 'it stays still' : m.reduced.still ? `it stops by about ${m.reduced.settlesAt}s` : 'it keeps moving';
  if (!m.normal.moves && (it.kind === 'animation' || it.lint.animated)) issues.push({ level: it.kind === 'animation' ? 'WARN' : 'INFO', message: `nothing visibly moves over ${m.T}s, though ${it.lint.animated ? 'it has animations' : 'it\'s meant to be animated'}. Frames: ${sheet}` });
  issues.push({ level: 'INFO', message: `motion: ${normal}; with reduced motion on, ${reduced}. Frames: ${sheet}` });
  return { issues, m: { T: m.T, loops: m.loops, normal: { moves: m.normal.moves, settlesAt: m.normal.settlesAt, still: m.normal.still }, reduced: { moves: m.reduced.moves, settlesAt: m.reduced.settlesAt, still: m.reduced.still }, sheet } };
}

async function cmdMotion(a) {
  let issues = [];
  if (a.workspace) {
    const run = await loadRun(a.workspace);
    const items = await runItems(run, (it) => it.animated || run.cfg.kind === 'animation');
    if (!items.length) { console.log('No file in the run animates, so there is no motion to check.'); return 0; }
    await chromiumOnly(async (b, s) => {
      for (const it of items) for (const i of (await motionIssues(b, s, { ...it, kind: run.cfg.kind }, { until: a.until, size: a.size, out: path.join(run.ws, 'motion', `${it.key}.png`) })).issues) issues.push({ file: it.rel, check: 'motion', ...i });
    }, run);
  } else {
    if (a.files.length !== 1) fail(`motion takes one svg, or -w <run>.\nusage: node svg.mjs ${SPECS.motion.usage}`);
    const it = looseItem(a.files[0], { kind: 'animation' });
    const out = path.resolve(a.out || path.join(os.tmpdir(), 'killer-svg-motion', `${it.key}-${createHash('sha1').update(it.canon).digest('hex').slice(0, 6)}.png`));
    await chromiumOnly(async (b, s) => { for (const i of (await motionIssues(b, s, { ...it, kind: null }, { until: a.until, size: a.size, out })).issues) issues.push({ file: it.rel, check: 'motion', ...i }); });
  }
  printIssues(issues);
  return issues.some((i) => i.level === 'FAIL') ? 1 : 0;
}

// ---------------------------------------------------------------- minify

const whereInViewBox = (box, b, lint) => {
  const vb = lint.viewBox || { x: 0, y: 0, w: b.width, h: b.height };
  const fx = (x) => +(vb.x + (x / b.width) * vb.w).toFixed(2), fy = (y) => +(vb.y + (y / b.height) * vb.h).toFixed(2);
  return `around x ${fx(box.x0)} to ${fx(box.x1 + 1)}, y ${fy(box.y0)} to ${fy(box.y1 + 1)} in viewBox units`;
};

async function cmdMinify(a) {
  const file = path.resolve(a.svg);
  if (!existsSync(file)) fail(`no file ${file}`);
  const run = a.workspace ? await loadRun(a.workspace) : null;
  const text = readFileSync(file, 'utf8');
  const lint = lintSvg(text, { kind: run?.cfg.kind || a.kind || null });
  if (!lint.parsed) fail(`can't minify ${file}: ${lint.issues[0].message}`);
  const decimals = a.decimals ?? 2;
  const out = path.resolve(a.out || file);
  const min = minifySvg(text, { decimals });
  const before = Buffer.byteLength(text), after = Buffer.byteLength(min);
  if (after >= before) { console.log(`minify: ${file} is already as small as minify makes it (${kb(before)}). Nothing was written.`); return 0; }
  let sizes = a.sizes ? parseSizes(a.sizes) : null;
  if (!sizes && run) sizes = sizesFor(run.cfg, lint);
  if (!sizes) sizes = defaultSizes(a.kind || 'logo', lint);
  const boxes = sizes.map((s) => boxFor(s, lint));
  const bgs = run ? run.cfg.on : lint.colorScheme ? [bgFrom('light'), bgFrom('dark')] : [bgFrom('light')];
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'ksv-min-'));
  const cand = path.join(tmp, path.basename(file));
  await fs.writeFile(cand, min, 'utf8');
  const differs = [];
  try {
    await chromiumOnly(async (browser, server) => {
      const context = await browser.newContext({ deviceScaleFactor: 1 });
      await guard(context, server);
      const page = await context.newPage();
      const moments = lint.animated ? [['no-preference', 0], ['no-preference', 0.73], ['no-preference', 2.11], ['reduce', 'end']] : [['no-preference', null]];
      for (const bg of bgs) {
        for (const [mode, t] of moments) {
          await page.emulateMedia({ colorScheme: bg.scheme, reducedMotion: mode });
          const shot = (f) => (lint.animated ? docShots(page, server, f, boxes, { bg: bg.css, t }).then((r) => r.shots) : imgShots(page, server, f, boxes, { bg: bg.css }));
          const A = await shot(file), B = await shot(cand);
          for (let i = 0; i < boxes.length; i++) {
            const c = compareImages(A[i], B[i], bg.rgb, 16);
            if (!sameRender(c)) differs.push({ b: boxes[i], bg: bg.name, c, t });
          }
        }
      }
      await context.close();
    }, run);
  } finally { await fs.rm(tmp, { recursive: true, force: true }); }
  if (differs.length) {
    const w = differs.reduce((x, y) => (y.c.diff > x.c.diff ? y : x));
    console.log(`minify: refused. The smaller file renders differently at ${sizesText(differs.map((d) => d.b.size))}: at ${w.b.size} px on ${w.bg}${w.t !== null && w.t !== undefined ? ` (at ${w.t === 'end' ? 'the reduced-motion still' : `${w.t}s`})` : ''}, ${plural(w.c.diff, 'pixel')} differ, ${whereInViewBox(w.c.box || { x0: 0, y0: 0, x1: 0, y1: 0 }, w.b, lint)}.`);
    console.log(`Nothing was written. Rounding to ${plural(decimals, 'decimal')} moves something there; try --decimals ${decimals + 1}.`);
    return 1;
  }
  await fs.mkdir(path.dirname(out), { recursive: true });
  await fs.writeFile(out, min, 'utf8');
  console.log(`minify: ${kb(before)} -> ${kb(after)} (${kb(before - after)} saved, ${pct((before - after) / before)}). The renders match at ${sizesText(sizes)} on ${list(bgs.map((b) => b.name))}.`);
  console.log(`Wrote ${out}`);
  if (run) {
    run.cfg.minified.push({ at: now(), file, out, before, after, decimals });
    if (canonical(out) !== canonical(file) && !run.cfg.outputs.includes(out)) run.cfg.outputs.push(out);
    await run.save();
  }
  return 0;
}

// ---------------------------------------------------------------- export

async function cmdExport(a) {
  const file = path.resolve(a.svg);
  if (!existsSync(file)) fail(`no file ${file}`);
  if (!a.png) fail(`export needs --png <sizes>, like --png 16,32,180,512.\nusage: node svg.mjs ${SPECS.export.usage}`);
  const run = a.workspace ? await loadRun(a.workspace) : null;
  const sizes = parseSizes(a.png, '--png');
  const lint = lintSvg(readFileSync(file, 'utf8'), {});
  if (!lint.parsed || lint.issues.some((i) => i.rule === 'root')) fail(`can't export ${file} until its XML is fixed: ${lint.issues.find((i) => i.level === 'FAIL').message}`, 1);
  const outDir = path.resolve(a.out || path.dirname(file));
  const stem = path.basename(file).replace(/\.svg$/i, '');
  const boxes = sizes.map((s) => boxFor(s, lint));
  const written = [], blank = [];
  await chromiumOnly(async (browser, server) => {
    const context = await browser.newContext({ deviceScaleFactor: 1, reducedMotion: 'reduce' });
    await guard(context, server);
    const page = await context.newPage();
    const shots = lint.animated ? (await docShots(page, server, file, boxes, { clear: true })).shots : await imgShots(page, server, file, boxes, { clear: true });
    await context.close();
    for (let i = 0; i < boxes.length; i++) {
      const p = path.join(outDir, `${stem}-${sizes[i]}.png`);
      await writePng(p, shots[i]);
      const back = readPng(p);
      let opaque = 0;
      for (let k = 3; k < back.data.length; k += 4) if (back.data[k] > 0) opaque++;
      if (back.width !== boxes[i].width || back.height !== boxes[i].height || !opaque) blank.push(p);
      written.push({ p, w: back.width, h: back.height });
    }
  }, run);
  for (const w of written) console.log(`  ${w.w}x${w.h}  ${w.p}`);
  if (run) { for (const w of written) if (!run.cfg.outputs.includes(w.p)) run.cfg.outputs.push(w.p); await run.save(); }
  console.log(`export: ${plural(written.length, 'PNG')} with a transparent background${lint.animated ? ' (the reduced-motion still)' : ''}.`);
  console.log("For a site's full favicon and app icon set (favicon.ico, apple-touch-icon.png, the manifest), use killer-design's icons.mjs build on the SVG.");
  if (blank.length) { console.log(`FAIL  ${plural(blank.length, 'PNG')} came out empty or the wrong size: ${blank.join(', ')}`); return 1; }
  return 0;
}

// ---------------------------------------------------------------- check

const fileLine = (i) => `${i.file ? `${i.file}: ` : ''}${i.message}`;
async function cmdCheck(a) {
  const run = await loadRun(a.workspace);
  const { cfg, ws } = run;
  const files = needFiles(cfg);
  const { infos, issues } = lintFiles(cfg, files);
  const fp = fingerprint(cfg, files);
  const motions = {};
  await withEnv(run, async (env, server) => {
    const r = await doRender(run, files, infos, env, server, { quiet: true });
    issues.push(...r.issues);
    issues.push(...await compareEngines(run, files));
    issues.push(...await measureContrast(run, files));
    if (cfg.target === 'github') issues.push(...await githubFiles(env.browsers.chromium, server, run, r.items));
    for (const it of r.items.filter((x) => x.animated || cfg.kind === 'animation')) {
      const m = await motionIssues(env.browsers.chromium, server, { ...it, kind: cfg.kind }, { out: path.join(ws, 'motion', `${it.key}.png`) });
      motions[it.key] = m.m;
      issues.push(...m.issues.map((i) => ({ file: it.rel, check: 'motion', ...i })));
    }
  });
  const R = cfg.render;
  const by = (lvl) => issues.filter((i) => i.level === lvl);
  const checked = ENGINES.filter((e) => R.engines[e]?.status === 'checked');
  const result = {
    skill: 'killer-svg', generatedAt: now(), fingerprint: fp, ok: !by('FAIL').length, kind: cfg.kind, target: cfg.target, on: cfg.on.map((b) => b.name),
    engines: R.engines,
    files: files.map((it) => ({
      file: it.rel, key: it.key, exists: it.exists, sizes: R.files[it.key]?.sizes || null, animated: !!infos[it.key]?.animated,
      sheet: R.files[it.key]?.sheet || null, motion: motions[it.key] || null,
      fails: issues.filter((i) => i.file === it.rel && i.level === 'FAIL').map((i) => ({ check: i.check, rule: i.rule || null, message: i.message })),
      warns: issues.filter((i) => i.file === it.rel && i.level === 'WARN').map((i) => ({ check: i.check, rule: i.rule || null, message: i.message })),
      infos: issues.filter((i) => i.file === it.rel && i.level === 'INFO').map((i) => ({ check: i.check, rule: i.rule || null, message: i.message })),
    })),
    summary: {
      FAIL: by('FAIL').map(fileLine), WARN: by('WARN').map(fileLine), INFO: by('INFO').map(fileLine),
      files: files.length, failingFiles: [...new Set(by('FAIL').map((i) => i.file).filter(Boolean))],
      enginesChecked: checked, enginesNotChecked: ENGINES.filter((e) => !checked.includes(e)),
    },
  };
  await saveJson(path.join(ws, 'check.json'), result);
  cfg.checks.push({ at: result.generatedAt, fingerprint: fp, ok: result.ok, fails: result.summary.FAIL.length, warns: result.summary.WARN.length });
  await run.save();
  console.log(`check: ${plural(files.length, 'file')} · ${cfg.kind} · on ${cfg.on.map((b) => b.name).join(', ')} · ${checked.map((e) => `${TITLE[e]} ${R.engines[e].version}`).join(', ')}${result.summary.enginesNotChecked.length ? ` (${result.summary.enginesNotChecked.map((e) => TITLE[e]).join(' and ')} not checked)` : ''}`);
  printIssues(issues);
  console.log(`\ncheck.json -> ${path.join(ws, 'check.json')}`);
  for (const f of result.files) if (f.sheet) console.log(`Contact sheet: ${f.sheet}`);
  if (result.ok) console.log('No failures. That only means the checks hold: now look at the sheets at every size, like someone who has never seen it.');
  else console.log(`${plural(result.summary.FAIL.length, 'failure')} in ${plural(result.summary.failingFiles.length || 1, 'file')}. Fix the graphics and run check again.`);
  return result.ok ? 0 : 1;
}

// ---------------------------------------------------------------- sheet

async function cmdSheet(a) {
  const run = await loadRun(a.workspace);
  const { files } = await currentRenders(run);
  const { cfg, ws } = run;
  const fp = fingerprint(cfg, files);
  const check = loadJson(path.join(ws, 'check.json'), null);
  const marks = check && check.fingerprint === fp ? check : null;
  const out = path.resolve(a.out || path.join(ws, 'sheet.png'));
  let html = `<!doctype html><meta charset="utf-8"><style>${SHEET_CSS}</style><h1>${escHtml(cfg.kind)}: ${plural(files.length, 'file')}</h1><p class="sub">${marks ? (marks.ok ? 'every check passed' : `the last check failed on ${plural(marks.summary.failingFiles.length, 'file')}`) : check ? 'the files changed since the last check' : "check hasn't run yet"} · Chromium, at 1x</p>`;
  for (const it of files) {
    const rf = cfg.render.files[it.key];
    const fails = marks ? marks.files.find((f) => f.file === it.rel)?.fails || [] : [];
    html += `<h2>${escHtml(it.rel)}${fails.length ? ` <span class="fails">FAIL: ${escHtml([...new Set(fails.map((f) => f.rule || f.check))].join(', '))}</span>` : ''}</h2>`;
    if (!rf || !rf.ok) { html += '<p class="sub">not rendered</p>'; continue; }
    html += `<div class="${fails.length ? 'bad' : ''}">`;
    for (const bg of [...it.on, CHECKER_BG]) {
      html += `<div class="row"><div class="lab">${escHtml(bg.name)}</div>${rf.boxes.map((b) => {
        const p = pngPath(ws, 'chromium', it.key, `${b.size}-${bg.name}`);
        if (!existsSync(p)) return '';
        const k = Math.max(b.width, b.height) > 192 ? 192 / Math.max(b.width, b.height) : 1;
        return `<figure><img src="data:image/png;base64,${readFileSync(p).toString('base64')}" width="${Math.round(b.width * k)}" height="${Math.round(b.height * k)}"><figcaption>${b.size} px${k < 1 ? ' (shown smaller)' : ''}</figcaption></figure>`;
      }).join('')}</div>`;
    }
    html += '</div>';
  }
  const size = await chromiumOnly((browser) => htmlToPng(browser, html, out, 1400), run);
  cfg.sheet = { at: now(), path: out, fingerprint: fp, ...size };
  await run.save();
  console.log(`sheet: ${size.width}x${size.height} px -> ${out}`);
  if (!marks) console.log(check ? 'No failure marks: the files changed since the last check.' : "No failure marks: check hasn't run yet.");
  else if (marks.ok) console.log('Every file passed the last check.');
  else console.log(`Files that failed the last check are outlined in red: ${marks.summary.failingFiles.join(', ')}.`);
  return 0;
}

// ---------------------------------------------------------------- the blind viewer

function template(name) {
  if (!existsSync(BRIEFS)) fail(`missing ${BRIEFS}: reinstall the skill`);
  const text = readFileSync(BRIEFS, 'utf8').replace(/\r\n/g, '\n');
  const at = text.indexOf(`\n## ${name}\n`);
  if (at < 0) fail(`${BRIEFS} has no "## ${name}" section`);
  const start = text.indexOf('````markdown\n', at);
  const end = text.indexOf('\n````', start + 12);
  if (start < 0 || end < 0) fail(`${BRIEFS}: the "## ${name}" section has no \`\`\`\`markdown block`);
  return text.slice(start + 12, end).trim() + '\n';
}
const PLACEHOLDERS = ['what', 'them', 'images folder', 'listing', 'sizes', 'letters', 'example', 'scratch folder', 'count line', 'reads', 'answers'];
function fill(tpl, values) {
  const out = tpl.replace(/<([a-z][a-z ]*[a-z])>/g, (m, k) => (k in values ? values[k] : m));
  const left = PLACEHOLDERS.find((p) => out.includes(`<${p}>`));
  if (left) fail(`internal: the brief still has <${left}>`);
  return out;
}
const newBox = async () => {
  const box = path.join(sandboxesHome(), randomBytes(5).toString('hex'));
  await fs.mkdir(path.join(box, 'scratch'), { recursive: true });
  return box;
};
const rmBox = async (box) => { if (box && box.startsWith(sandboxesHome())) await fs.rm(box, { recursive: true, force: true }).catch(() => {}); };
function readReply(file) {
  if (!existsSync(file)) return { error: `no reply at ${file}` };
  const text = readFileSync(file, 'utf8').replace(/^﻿/, '');
  try { return { data: JSON.parse(text), text }; } catch { /* a saved message: take its last JSON block */ }
  const blocks = [...text.matchAll(/```(?:json)?[ \t]*\n([\s\S]*?)```/g)].map((m) => m[1]).reverse();
  for (const b of blocks) { try { return { data: JSON.parse(b), text }; } catch { /* try the next */ } }
  return { error: `${file} has no JSON that parses`, text };
}
/** Count each distinct reply an agent wrote, valid or not: it's how many agents the run really used. */
function countAgent(cfg, text) {
  if (!text) return;
  const h = createHash('sha1').update(text).digest('hex').slice(0, 16);
  if (!cfg.replies.includes(h)) cfg.replies.push(h);
}
/** A read written "<file>.svg: <text>" is about that file; any other read is about the graphics as a whole. */
function scopeReads(reads, graphics) {
  return reads.map((text, i) => {
    const m = /^([^:\n]+?\.svg)\s*:\s*(.+)$/i.exec(text);
    if (!m) return { id: i + 1, text, key: null };
    const g = graphics.find((x) => path.basename(x.rel).toLowerCase() === path.basename(m[1]).toLowerCase() || x.rel.toLowerCase() === slash(m[1]).toLowerCase());
    if (!g) fail(`read ${i + 1} is about ${m[1]}, which isn't one of the run's graphics (${graphics.map((x) => x.rel).join(', ')})`);
    return { id: i + 1, text: m[2].trim(), key: g.key };
  });
}

async function viewerImages(browser, server, dir, g, items) {
  const names = [];
  const small = g.small ? items.find((x) => x.key === g.small) : null;
  const pick = (b) => (small && b.size <= SMALL_VARIANT_UP_TO ? small.abs : g.abs);
  const shown = g.boxes.filter((b) => b.size <= 512);
  const row = (bg) => `<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:${bg.css}}body{display:inline-block}</style><div style="display:flex;gap:28px;align-items:flex-end;padding:28px 32px 18px">${shown.map((b) => `<figure style="margin:0;display:flex;flex-direction:column;align-items:center;gap:10px"><img src="${server.file(pick(b))}" alt="" style="display:block;width:${b.width}px;height:${b.height}px"><figcaption style="font:12px system-ui,sans-serif;color:${bg.scheme === 'dark' ? '#b9bec5' : '#5c6168'}">${b.size} px</figcaption></figure>`).join('')}</div>`;
  const context = await browser.newContext({ deviceScaleFactor: 1, viewport: { width: 800, height: 400 } });
  await guard(context, server);
  const page = await context.newPage();
  try {
    // The board: one row per background, every size up to 512 px at its real size. Each row is its own
    // page, so a graphic that changes with the colour scheme shows its dark version on the dark row.
    const rows = [];
    for (const bg of g.on) {
      await page.emulateMedia({ colorScheme: bg.scheme });
      await page.goto(server.page(row(bg)), { waitUntil: 'load' });
      await page.evaluate(() => Promise.all([...document.images].map((i) => i.decode().catch(() => null))));
      const size = await page.evaluate(() => ({ w: document.body.scrollWidth, h: document.body.scrollHeight }));
      await page.setViewportSize({ width: Math.max(200, size.w), height: Math.max(100, size.h) });
      rows.push(decodePng(await page.screenshot({ clip: { x: 0, y: 0, width: size.w, height: size.h } })));
    }
    const W = Math.max(...rows.map((r) => r.width)), H = rows.reduce((n, r) => n + r.height, 0);
    const board = { width: W, height: H, data: new Uint8Array(W * H * 4).fill(255) };
    let y = 0;
    for (const r of rows) {
      for (let k = 0; k < r.height; k++) board.data.set(r.data.subarray(k * r.width * 4, (k + 1) * r.width * 4), ((y + k) * W) * 4);
      y += r.height;
    }
    await writePng(path.join(dir, `${g.letter}-board.png`), board);
    names.push({ file: `${g.letter}-board.png`, what: `graphic ${g.letter} at ${list(shown.map((b) => String(b.size)))} px, real size, on ${list(g.on.map((b) => b.name))}` });
    const big = g.boxes[g.boxes.length - 1].size > 1024 ? boxFor(1024, g.lint) : g.boxes[g.boxes.length - 1];
    for (const bg of g.on) {
      await page.emulateMedia({ colorScheme: bg.scheme, reducedMotion: g.animated ? 'reduce' : 'no-preference' });
      const [img] = g.animated ? (await docShots(page, server, g.abs, [big], { bg: bg.css })).shots : await imgShots(page, server, g.abs, [big], { bg: bg.css });
      await writePng(path.join(dir, `${g.letter}-large-${bg.name}.png`), img);
      names.push({ file: `${g.letter}-large-${bg.name}.png`, what: `graphic ${g.letter} at ${big.size} px on ${bg.name}` });
    }
  } finally { await context.close(); }
  if (g.animated) {
    const m = await motionFrames(browser, server, g.abs, boxFor(Math.min(512, g.boxes[g.boxes.length - 1].size), g.lint));
    const early = m.times.map((t, i) => [t, i]).filter(([t]) => t < m.T - 1.3);
    const picks = [...new Set([0, Math.floor(early.length / 3), Math.floor((2 * early.length) / 3), m.times.length - 1])];
    for (const [n, i] of picks.entries()) {
      const p = path.join(dir, `${g.letter}-moving-${n + 1}.png`);
      await writePng(p, m.normal.frames[i]);
      names.push({ file: path.basename(p), what: `graphic ${g.letter} as it moves, frame ${n + 1} of ${picks.length}` });
    }
  }
  return names;
}

function openRound(cfg, want) {
  const r = cfg.rounds[cfg.rounds.length - 1];
  if (!r || r.status === 'done' || r.status === 'abandoned') fail('no viewer round is open: run audience first');
  if (want && r.status !== want) fail(want === 'open' ? `round ${r.n} already has the viewer's reply; run landed next, or audience again for a new round` : `round ${r.n} has no viewer reply yet: run heard first`);
  return r;
}

async function cmdAudience(a) {
  const run = await loadRun(a.workspace);
  const { cfg, ws } = run;
  const files = needFiles(cfg);
  const sb = sandboxesHome();
  for (const p of [ws, cfg.project]) if (inside(sb, p)) fail(`the viewer folders (${sb}) would sit inside ${p}, where the viewer could find the files and the brief. Point KILLER_SVG_SANDBOXES somewhere else.`);
  const { infos, issues } = lintFiles(cfg, files);
  const missing = issues.filter((i) => i.rule === 'missing');
  if (missing.length) fail(`${missing.map((i) => i.file).join(', ')} ${missing.length === 1 ? "isn't" : "aren't"} written yet`);
  const { items } = await renderItems(run, files, infos);
  const graphics = items.filter((it) => !it.smallFor);
  if (!graphics.length) fail('nothing to show: fix the files so they render (run check)');
  const reads = scopeReads(cfg.reads, graphics);
  for (const r of cfg.rounds) if (r.status !== 'done') { r.status = 'abandoned'; await rmBox(r.box); await rmBox(r.matchBox); }
  const order = graphics.map((g) => [randomInt(1 << 30), g]).sort((x, y) => x[0] - y[0]).map((x) => x[1]);
  order.forEach((g, i) => { g.letter = String.fromCharCode(65 + i); });
  const n = cfg.rounds.length + 1;
  const box = await newBox();
  const imagesDir = path.join(box, 'images');
  await fs.mkdir(imagesDir, { recursive: true });
  const listing = [];
  await chromiumOnly(async (browser, server) => {
    for (const g of order) listing.push(...(await viewerImages(browser, server, imagesDir, g, items)));
  }, run);
  const sizes = [...new Set(order.flatMap((g) => g.boxes.filter((b) => b.size <= 512).map((b) => b.size)))].sort((x, y) => x - y);
  const [what, them] = KIND_PHRASE[cfg.kind];
  const values = {
    'count line': order.length === 1 ? `one graphic (${what}), labelled A` : `${order.length} graphics (${what}), labelled ${list(order.map((g) => g.letter))}`,
    them: order.length === 1 ? them : 'them',
    'images folder': slash(imagesDir),
    listing: listing.map((l) => `- \`${l.file}\`: ${l.what}`).join('\n'),
    sizes: list(sizes.map(String)),
    letters: list(order.map((g) => g.letter)),
    example: order[0].letter,
    'scratch folder': slash(path.join(box, 'scratch')),
  };
  const brief = fill(template('Viewer brief'), values);
  const filled = Object.entries(values).filter(([k]) => k !== 'count line' && k !== 'them').map(([, v]) => v).join('\n').toLowerCase();
  const leaks = [cfg.project, ws, cfg.brief, ...cfg.reads, ...files.map((f) => f.canon), ...files.map((f) => f.rel)].filter((x) => x && x.length >= 4);
  for (const leak of leaks) if (brief.toLowerCase().includes(slash(leak).toLowerCase())) fail(`internal: the viewer brief gives away "${snippet(leak, 40)}"`);
  for (const f of files) {
    const stem = path.basename(f.canon).replace(/\.svg$/i, '').toLowerCase();
    if (stem.length >= 3 && filled.includes(stem)) fail(`internal: the viewer's folder or listing gives away the file name "${stem}"`);
  }
  const shownFiles = readdirSync(imagesDir);
  if (shownFiles.some((f) => !/^[A-Z]-(board|large-[a-z0-9]+|moving-\d)\.png$/.test(f))) fail(`internal: the viewer's folder holds something other than the renders: ${shownFiles.join(', ')}`);
  const rdir = path.join(ws, 'audience', `round-${n}`);
  await fs.mkdir(rdir, { recursive: true });
  await fs.writeFile(path.join(rdir, 'brief.md'), brief, 'utf8');
  cfg.rounds.push({
    n, at: now(), status: 'open', box, matchBox: null, images: shownFiles.length,
    graphics: order.map((g) => ({ letter: g.letter, key: g.key, rel: g.rel, small: g.small || null, sizes: g.boxes.filter((b) => b.size <= 512).map((b) => b.size), need: Math.min(...g.sizes) })),
    reads: reads.map((r) => ({ ...r, letter: r.key ? order.find((g) => g.key === r.key).letter : null })),
    fingerprint: fingerprint(cfg, files), git: gitStatus(cfg, files),
  });
  await run.save();
  console.log(`Viewer round ${n}: ${plural(shownFiles.length, 'image')} of ${plural(order.length, 'graphic')} in ${imagesDir}`);
  console.log(`Brief: ${path.join(rdir, 'brief.md')}`);
  console.log('Send it, exactly as written, to one fresh agent (general-purpose, never fork). It writes reply.json in');
  console.log(`  ${path.join(box, 'scratch')}`);
  console.log(`then run: node svg.mjs heard -w "${ws}"`);
  return 0;
}

const norm = (t) => String(t || '').toLowerCase().replace(/[‘’`´]/g, "'").replace(/[“”]/g, '"').replace(/[–—‐-]/g, '-').replace(/\s+/g, ' ').replace(/^[\s"'.,;:!?-]+|[\s"'.,;:!?-]+$/g, '').trim();
const wordsOf = (g) => norm([g.what, g.suggests, g.name, g.unreadable].join(' \n '));
function sizeResults(r) {
  return r.graphics.map((g) => {
    const v = r.viewer.graphics.find((x) => x.id === g.letter);
    return { letter: g.letter, rel: g.rel, smallest: v.smallest, need: g.need, ok: v.smallest !== null && v.smallest <= g.need };
  });
}

async function cmdHeard(a) {
  const run = await loadRun(a.workspace);
  const { cfg, ws } = run;
  const r = openRound(cfg, 'open');
  const file = a.reply ? path.resolve(a.reply) : path.join(r.box, 'scratch', 'reply.json');
  const got = readReply(file);
  countAgent(cfg, got.text);
  if (got.error) { await run.save(); fail(`${got.error}. Send the same brief to a fresh agent, then run heard again (or pass --reply <file> with its message saved).`); }
  const d = got.data;
  const letters = r.graphics.map((g) => g.letter);
  const shown = [...new Set(r.graphics.flatMap((g) => g.sizes))];
  const problems = [];
  if (!d || typeof d !== 'object' || Array.isArray(d) || !Array.isArray(d.graphics)) problems.push('"graphics" must be a list, one entry per graphic');
  else {
    for (const l of letters) if (d.graphics.filter((x) => x && x.id === l).length !== 1) problems.push(`graphic ${l} must appear exactly once`);
    for (const x of d.graphics) {
      if (!x || !letters.includes(x.id)) { problems.push(`"${x && x.id}" isn't one of the graphics (${letters.join(', ')})`); continue; }
      if (typeof x.what !== 'string' || !x.what.trim()) problems.push(`graphic ${x.id}: "what" must say what it is`);
      if (typeof x.name !== 'string' || !x.name.trim()) problems.push(`graphic ${x.id}: "name" must be a word or two`);
      if (x.suggests !== undefined && typeof x.suggests !== 'string') problems.push(`graphic ${x.id}: "suggests" must be text`);
      if (x.unreadable !== undefined && typeof x.unreadable !== 'string') problems.push(`graphic ${x.id}: "unreadable" must be text`);
      if (x.smallest !== null && !shown.includes(x.smallest)) problems.push(`graphic ${x.id}: "smallest" must be one of ${shown.join(', ')}, or null`);
    }
  }
  if (problems.length) { await run.save(); fail(`the viewer's reply doesn't fit the brief: ${problems.join('; ')}. Send the same brief to a fresh agent and run heard again.`); }
  const viewer = { graphics: letters.map((l) => { const x = d.graphics.find((y) => y.id === l); return { id: l, what: x.what.trim(), suggests: (x.suggests || '').trim(), name: x.name.trim(), smallest: x.smallest, unreadable: (x.unreadable || '').trim() }; }), notes: typeof d.notes === 'string' ? d.notes : '' };
  r.viewer = viewer;
  r.heardAt = now();
  r.sizes = sizeResults(r);
  const rdir = path.join(ws, 'audience', `round-${r.n}`);
  await saveJson(path.join(rdir, 'viewer.json'), viewer);
  const files = resolveFiles(cfg);
  r.changedDuring = fingerprint(cfg, files) !== r.fingerprint || gitStatus(cfg, files) !== r.git;
  console.log(`Round ${r.n}, what the viewer saw:`);
  for (const g of viewer.graphics) {
    console.log(`  ${g.id}: ${snippet(g.what, 140)}${g.suggests ? ` · suggests: ${snippet(g.suggests, 80)}` : ''} · they'd call it "${g.name}" · recognisable from ${g.smallest === null ? 'no size shown' : `${g.smallest} px`}`);
    if (g.unreadable) console.log(`     couldn't make out: ${snippet(g.unreadable, 200)}`);
  }
  for (const s of r.sizes) if (!s.ok) console.log(`  SIZE  ${s.letter} (${s.rel}) needs to read at ${s.need} px; the viewer ${s.smallest === null ? "couldn't recognise it at any size" : `could only recognise it from ${s.smallest} px`}`);
  if (r.changedDuring) console.log("WARN  the graphics changed while the viewer had them. If you didn't change them, restore them before going on.");
  if (!r.reads.length) {
    r.status = 'done';
    r.landed = 0;
    r.total = 0;
    r.doneAt = now();
    await rmBox(r.box);
    await run.save();
    console.log('\nNo reads were set, so there is nothing to match: read the answer above against what the graphics are for.');
    return r.sizes.every((s) => s.ok) ? 0 : 1;
  }
  const quote = (t) => String(t).replace(/\s*\n\s*/g, ' ');
  const mbox = await newBox();
  const brief = fill(template('Matcher brief'), {
    'count line': r.graphics.length === 1 ? 'one graphic' : `${r.graphics.length} graphics`,
    reads: r.reads.map((x) => `${x.id}. ${x.letter ? `(about graphic ${x.letter}) ` : ''}${x.text}`).join('\n'),
    answers: viewer.graphics.map((g) => `**Graphic ${g.id}**\n\n> **What it is:** ${quote(g.what)}\n>\n> **What it suggests:** ${quote(g.suggests) || '(nothing)'}\n>\n> **What they'd call it:** ${quote(g.name)}\n>\n> **What they couldn't make out:** ${quote(g.unreadable) || '(nothing)'}`).join('\n\n'),
    'scratch folder': slash(path.join(mbox, 'scratch')),
  });
  await fs.writeFile(path.join(rdir, 'match-brief.md'), brief, 'utf8');
  r.matchBox = mbox;
  r.status = 'heard';
  await rmBox(r.box);
  await run.save();
  console.log(`\nMatcher brief: ${path.join(rdir, 'match-brief.md')}`);
  console.log('Send it, exactly as written, to one more fresh agent (general-purpose, never fork). It writes reply.json in');
  console.log(`  ${path.join(mbox, 'scratch')}`);
  console.log(`then run: node svg.mjs landed -w "${ws}"`);
  return 0;
}

async function cmdLanded(a) {
  const run = await loadRun(a.workspace);
  const { cfg, ws } = run;
  const r = openRound(cfg, 'heard');
  const file = a.reply ? path.resolve(a.reply) : path.join(r.matchBox, 'scratch', 'reply.json');
  const got = readReply(file);
  countAgent(cfg, got.text);
  if (got.error) { await run.save(); fail(`${got.error}. Send the same matcher brief to a fresh agent, then run landed again.`); }
  const d = got.data;
  const lst = d && Array.isArray(d.reads) ? d.reads : null;
  const letters = r.graphics.map((g) => g.letter);
  const problems = [];
  if (!lst) problems.push('"reads" must be a list');
  else {
    for (const x of r.reads) if (lst.filter((y) => y && y.id === x.id).length !== 1) problems.push(`read ${x.id} must appear exactly once`);
    for (const y of lst) {
      if (!y || !['landed', 'partly', 'missed'].includes(y.verdict)) problems.push(`read ${y && y.id}: verdict must be landed, partly or missed`);
      else if (y.graphic != null && !letters.includes(y.graphic)) problems.push(`read ${y.id}: graphic must be one of ${letters.join(', ')} or null`);
    }
  }
  if (problems.length) { await run.save(); fail(`the matcher's reply doesn't fit the brief: ${problems.join('; ')}. Send the same brief to a fresh agent and run landed again.`); }
  const words = Object.fromEntries(r.viewer.graphics.map((g) => [g.id, wordsOf(g)]));
  const all = Object.values(words).join(' \n ');
  const verdicts = r.reads.map((read) => {
    const x = lst.find((y) => y.id === read.id);
    let verdict = x.verdict, note = '';
    const q = typeof x.quote === 'string' ? x.quote : '';
    const about = read.letter || (letters.includes(x.graphic) ? x.graphic : null);
    if (verdict !== 'missed') {
      if (!norm(q)) { note = `said ${verdict}, with no quote`; verdict = 'missed'; }
      else if (read.letter && x.graphic && x.graphic !== read.letter) { note = `said ${verdict}, quoting graphic ${x.graphic}, but this read is about graphic ${read.letter}`; verdict = 'missed'; }
      else if (!(about ? words[about] : all).includes(norm(q))) { note = `said ${verdict}, but its quote isn't in what the viewer wrote${about ? ` about graphic ${about}` : ''}`; verdict = 'missed'; }
    }
    return { id: read.id, read: read.text, about: read.letter, verdict, said: x.verdict, graphic: about, quote: q || null, why: typeof x.why === 'string' ? x.why : '', note };
  });
  r.verdicts = verdicts;
  r.extra = Array.isArray(d.extra) ? d.extra.filter((e) => typeof e === 'string').slice(0, 5) : [];
  r.landed = verdicts.filter((v) => v.verdict === 'landed').length;
  r.total = verdicts.length;
  r.status = 'done';
  r.doneAt = now();
  await saveJson(path.join(ws, 'audience', `round-${r.n}`, 'verdicts.json'), { verdicts, extra: r.extra, sizes: r.sizes });
  await rmBox(r.matchBox);
  await run.save();
  console.log(`Round ${r.n}: ${r.landed} of ${plural(r.total, 'read')} landed with a blind viewer.`);
  for (const v of verdicts) {
    console.log(`  ${v.verdict.toUpperCase().padEnd(6)} ${v.id}. ${v.about ? `(${v.about}) ` : ''}${v.read}`);
    if (v.quote && v.verdict !== 'missed') console.log(`         they wrote: “${v.quote}”`);
    if (v.note) console.log(`         (${v.note})`);
  }
  for (const s of r.sizes) if (!s.ok) console.log(`  SIZE   ${s.letter} (${s.rel}) needs to read at ${s.need} px; the viewer ${s.smallest === null ? "couldn't recognise it at any size" : `recognised it only from ${s.smallest} px`}`);
  for (const e of r.extra) console.log(`  Also took away: ${e}`);
  const ok = r.landed === r.total && r.sizes.every((s) => s.ok);
  if (ok) { console.log('Every read landed, at every size.'); return 0; }
  console.log('Fix the graphic where a read missed or a size failed (the graphic, never the reads), run check again, then audience for a new round with a fresh viewer.');
  return 1;
}

// ---------------------------------------------------------------- land (commit the graphics on a local branch)

// Attribution, not mentions: a graphic about an AI tool can name it, but nothing may say who made it.
const ATTRIBUTION = /co-authored-by|generated (with|by)|\b(written|made|created|built|drafted|designed|drawn) (with|by|using) (claude|chatgpt|gpt|copilot|gemini|an? ai\b|ai\b)|\bai[- ](generated|assisted|written|made)\b|\u{1F916}/iu;
const ATTRIBUTION_DIFF = /co-authored-by:|generated (with|by) (claude|chatgpt|gpt|copilot|gemini|an? ai\b|ai\b)|\b(made|created|built|designed|drawn) (with|by|using) (claude|chatgpt|an? ai\b)|<meta[^>]+name=["']generator["'][^>]*(claude|gpt|\bai\b)|<dc:creator>[^<]*(claude|chatgpt|anthropic|openai)|generator:[^\n>]*(claude|chatgpt|anthropic|openai)|\u{1F916}/iu;

async function cmdLand(a) {
  const run = await loadRun(a.workspace);
  const { cfg } = run;
  const message = (a.message || []).join('\n\n').trim();
  if (!message) fail('land needs -m "<commit message>": what changed and why, in plain words');
  if (ATTRIBUTION.test(message)) fail("the commit message mentions an AI or a co-author. Commits must read as the user's own work: say what changed and why, nothing else.");
  const top = gitTop(cfg.project);
  if (!top) { console.log(`${cfg.project} isn't in a git repository, so there is nothing to commit. The graphics are in place as they are.`); return 0; }
  const files = resolveFiles(cfg).filter((f) => f.exists).map((f) => f.canon);
  const paths = [...files, ...cfg.outputs.filter((p) => existsSync(p)).map(canonical)];
  const outside = paths.filter((p) => !inside(p, top));
  const key = (s) => (process.platform === 'win32' ? s.toLowerCase() : s);
  const ours = [...new Set(paths.filter((p) => inside(p, top)).map((p) => slash(path.relative(top, p))))];
  if (!ours.length) fail(`none of the run's files are inside ${top}, so there is nothing to commit`);
  const before = git(top, ['diff', '--cached', '--name-only']).out.split('\n').filter(Boolean).filter((f) => !ours.map(key).includes(key(f)));
  if (before.length) fail(`other files are already staged (${before.slice(0, 5).join(', ')}): land commits only the run's graphics. Unstage them first; nothing was changed.`);
  const current = git(top, ['symbolic-ref', '-q', '--short', 'HEAD']).out.trim() || null;
  let target = a.branch || cfg.branch;
  if (!target) {
    target = `killer-svg/${today()}`;
    for (let i = 2; git(top, ['rev-parse', '--verify', '-q', `refs/heads/${target}`]).ok; i++) target = `killer-svg/${today()}-${i}`;
  }
  // A branch named with --branch, here or at init, is where the user (or killer-goal) wants it, even the starting one.
  if (!a.branch && !(cfg.branchNamed && target === cfg.branch) && target === cfg.git.startBranch) fail(`refusing to commit on ${target}, the branch you started on. Pass --branch ${target} if that's really where this goes.`);
  if (current !== target) {
    const exists = git(top, ['rev-parse', '--verify', '-q', `refs/heads/${target}`]).ok;
    const sw = git(top, exists ? ['switch', target] : ['switch', '-c', target]);
    if (!sw.ok) fail(`couldn't switch to ${target}: ${sw.err.trim()}`);
  }
  const add = git(top, ['add', '-A', '--', ...ours]);
  if (!add.ok) fail(`git add failed: ${add.err.trim()}`);
  const staged = git(top, ['diff', '--cached', '--name-only']).out.split('\n').filter(Boolean);
  if (!staged.length) { cfg.branch = target; await run.save(); console.log(`Nothing new to commit (on ${target}).`); return 0; }
  const diff = git(top, ['diff', '--cached', '--text', '-U0', '--', ...ours], { maxBuffer: 256 * 1024 * 1024 }).out;
  const hit = diff.split('\n').find((l) => l.startsWith('+') && ATTRIBUTION_DIFF.test(l));
  if (hit) { git(top, ['reset', '-q', '--', ...ours]); fail(`a graphic says who or what generated it ("${snippet(hit.slice(1), 70)}"). Remove that; the files must read as the user's own work.`); }
  const c = git(top, ['commit', '-q', '-m', message]);
  if (!c.ok) fail(`git commit failed: ${(c.err || c.out).trim()}`);
  const sha = git(top, ['rev-parse', '--short', 'HEAD']).out.trim();
  const subject = message.split('\n')[0];
  cfg.branch = target;
  cfg.commits.push(`${sha} ${subject}`);
  await run.save();
  console.log(`Committed ${plural(staged.length, 'file')} on ${target}: ${sha} ${subject}`);
  for (const f of staged) console.log(`  ${f}`);
  if (outside.length) console.log(`Not committed, outside the repository: ${outside.join(', ')}`);
  console.log('Nothing was pushed.');
  return 0;
}

// ---------------------------------------------------------------- decide, wait

async function cmdDecide(a) {
  const run = await loadRun(a.workspace);
  run.cfg.decisions.push(a.text.trim());
  await run.save();
  console.log(`Recorded a decision to check (${run.cfg.decisions.length} so far).`);
}
async function cmdWait(a) {
  const run = await loadRun(a.workspace);
  if (!a.command) fail('wait needs --command "<the exact command the user would run>"');
  run.cfg.waiting.push({ text: a.text.trim(), command: a.command.trim() });
  await run.save();
  console.log(`Recorded: waiting for the user's OK (${run.cfg.waiting.length} so far).`);
}

// ---------------------------------------------------------------- report

async function cmdReport(a) {
  const run = await loadRun(a.workspace);
  const { cfg, ws } = run;
  const files = resolveFiles(cfg);
  const fp = fingerprint(cfg, files);
  const check = loadJson(path.join(ws, 'check.json'), null);
  const current = !!(check && check.fingerprint === fp);
  const rounds = cfg.rounds.filter((r) => r.status === 'done');
  const last = rounds[rounds.length - 1] || null;
  const lastOk = !!(last && last.landed === last.total && (last.sizes || []).every((s) => s.ok));
  const checkOk = !!(check && check.ok && current);
  const blindOk = !cfg.blind || !!(last && lastOk && last.fingerprint === fp);
  let status = files.length && checkOk && blindOk ? 'done' : 'partial';
  if (!check && cfg.blocked) status = 'blocked';
  const checked = check ? check.summary.enginesChecked : [];
  const notChecked = check ? check.summary.enginesNotChecked : [];
  let headline;
  if (status === 'blocked') headline = `blocked: ${cfg.blocked}`;
  else if (!files.length) headline = 'no graphics in the run yet';
  else if (!check) headline = 'no check has run yet';
  else if (!check.ok) {
    const bits = check.files.filter((f) => f.fails.length).slice(0, 3).map((f) => `${[...new Set(f.fails.map((x) => x.rule || x.check))].join(' and ')} on ${f.file}`);
    headline = `${plural(files.length, 'file')}; check fails on ${plural(check.summary.failingFiles.length || 1, 'file')} (${bits.join('; ') || check.summary.FAIL[0]})${current ? '' : ' (on an earlier version: run check again)'}`;
  } else {
    headline = `${plural(files.length, 'file')}, every check passes in ${list(checked.map((e) => TITLE[e]))}${notChecked.length ? ` (${list(notChecked.map((e) => TITLE[e]))} not checked)` : ''}${current ? '' : ' (on an earlier version: run check again)'}`;
    if (cfg.blind) {
      if (!last) headline += "; the blind viewer hasn't seen them yet";
      else headline += cfg.reads.length ? `; a blind viewer got ${last.landed} of ${plural(last.total, 'read')} (round ${last.n})${(last.sizes || []).every((s) => s.ok) ? '' : ', but not at every size'}` : `; a blind viewer looked (round ${last.n})${(last.sizes || []).every((s) => s.ok) ? '' : ', and couldn\'t recognise it at every size'}`;
    }
  }
  const md = [];
  md.push(`# SVG report: ${cfg.kind}`, '', `**Result:** ${headline}.`, '', `**Brief:** ${cfg.brief}`, '');
  if (cfg.reads.length) md.push('**Must read as:**', ...cfg.reads.map((r, i) => `${i + 1}. ${r}`), '');
  md.push(`**Target:** ${cfg.target} · backgrounds ${cfg.on.map((b) => `${b.name} (${b.css})`).join(', ')} · ${cfg.sizes ? `sizes ${cfg.sizes.join(', ')} px` : 'sizes by kind'}`, '');
  md.push('## Files', '', '| File | Sizes | Result |', '|---|---|---|');
  for (const it of files) {
    const f = check?.files.find((x) => x.file === it.rel);
    md.push(`| \`${it.rel}\` | ${f?.sizes ? f.sizes.join(', ') : '-'} | ${!it.exists ? 'not written' : !f ? 'not checked' : f.fails.length ? `FAIL: ${[...new Set(f.fails.map((x) => x.rule || x.check))].join(', ')}` : f.warns.length ? `pass, ${plural(f.warns.length, 'warning')}` : 'pass'} |`);
  }
  md.push('');
  md.push('## Check', '');
  if (!check) md.push("Check hasn't run.", '');
  else {
    md.push(`${plural(cfg.checks.length, 'run')}; the last on ${check.generatedAt}, ${current ? 'on the files as they are now' : '**on an earlier version of the files: run check again**'}. ${check.summary.FAIL.length} failing, ${plural(check.summary.WARN.length, 'warning')}.`, '');
    md.push(`Engines: ${ENGINES.map((e) => `${TITLE[e]} ${check.engines[e]?.status === 'checked' ? `${check.engines[e].version}, checked` : `not checked (${check.engines[e]?.reason || 'unknown'})`}`).join('; ')}.`, '');
    const lines = [...check.summary.FAIL.map((l) => `- FAIL ${l}`), ...check.summary.WARN.map((l) => `- WARN ${l}`)];
    if (lines.length) md.push(...lines, '');
    for (const f of check.files) if (f.sheet) md.push(`Contact sheet for \`${f.file}\`: \`${f.sheet}\``);
    if (cfg.sheet) md.push(`Overview sheet: \`${cfg.sheet.path}\`${cfg.sheet.fingerprint === fp ? '' : ' (from an earlier version)'}`);
    md.push('');
  }
  md.push('## Blind viewer', '');
  if (!cfg.blind) md.push('Not run: the user chose no blind check.', '');
  else if (!rounds.length) md.push('No round finished.', '');
  else {
    for (const r of rounds) {
      md.push(`**Round ${r.n}**${r.total ? `: ${r.landed} of ${r.total} landed` : ''}${r.changedDuring ? ' (the graphics changed while the viewer had them)' : ''}`, '');
      for (const g of r.viewer.graphics) {
        const meta = r.graphics.find((x) => x.letter === g.id);
        md.push(`- ${g.id} (\`${meta.rel}\`): "${g.what}"; they'd call it "${g.name}"${g.suggests ? `; suggests ${g.suggests}` : ''}; recognisable from ${g.smallest === null ? 'no size shown' : `${g.smallest} px`} (needs ${meta.need} px)${g.unreadable ? `; couldn't make out: ${g.unreadable}` : ''}`);
      }
      if (r.verdicts) md.push(...r.verdicts.map((v) => `- Read ${v.id}: ${v.verdict}${v.quote && v.verdict !== 'missed' ? `, from “${v.quote}”` : ''}${v.note ? ` (${v.note})` : ''}${v.why && v.verdict !== 'landed' ? `. ${v.why}` : ''}`));
      md.push('');
    }
  }
  if (cfg.minified.length || cfg.outputs.length) {
    md.push('## Outputs', '');
    for (const m of cfg.minified) md.push(`- Minified \`${m.out}\`: ${kb(m.before)} to ${kb(m.after)}, renders checked the same`);
    for (const o of cfg.outputs.filter((p) => /\.png$/i.test(p))) md.push(`- Exported \`${o}\``);
    md.push('');
  }
  if (cfg.decisions.length) md.push('## Decisions to check', '', ...cfg.decisions.map((d) => `- ${d}`), '');
  if (cfg.waiting.length) md.push('## Waiting for your OK', '', ...cfg.waiting.map((w) => `- ${w.text}: \`${w.command}\``), '');
  md.push('## Run details', '', `Run folder: \`${ws}\``, `Agents sent: ${cfg.replies.length} (each reply a viewer or matcher wrote counts once)`);
  if (cfg.branch || cfg.commits.length) md.push(`Branch: \`${cfg.branch}\`${cfg.commits.length ? `; commits: ${cfg.commits.map((c) => `\`${c}\``).join(', ')}` : ''}. Nothing was pushed.`);
  md.push('');
  await fs.writeFile(path.join(ws, 'report.md'), md.join('\n'), 'utf8');
  const summary = {
    skill: 'killer-svg', status, headline, report: path.join(ws, 'report.md'),
    branch: cfg.branch || null, commits: cfg.commits, decisions: cfg.decisions,
    waiting: cfg.waiting.map((w) => `${w.text}: ${w.command}`),
    numbers: {
      files: files.length, check_runs: cfg.checks.length, checks_failing: check ? check.summary.FAIL.length : null, checks_warning: check ? check.summary.WARN.length : null,
      check_current: current, engines_checked: checked.length, engines_not_checked: notChecked.length, rounds: rounds.length,
      reads_landed: last ? last.landed : null, reads_total: cfg.reads.length, sizes_recognised: last ? (last.sizes || []).every((s) => s.ok) : null,
      agents: cfg.replies.length, minified: cfg.minified.length, exported: cfg.outputs.filter((p) => /\.png$/i.test(p)).length,
    },
  };
  await saveJson(path.join(ws, 'summary.json'), summary);
  console.log(`report: ${path.join(ws, 'report.md')}`);
  console.log(`summary: ${path.join(ws, 'summary.json')}`);
  console.log(`killer-svg: ${status} · ${headline} · ${path.join(ws, 'report.md')}`);
  return 0;
}

// ---------------------------------------------------------------- main

const COMMANDS = { init: cmdInit, add: cmdAdd, lint: cmdLint, render: cmdRender, engines: cmdEngines, contrast: cmdContrast, github: cmdGithub,
  motion: cmdMotion, minify: cmdMinify, export: cmdExport, check: cmdCheck, sheet: cmdSheet, audience: cmdAudience, heard: cmdHeard,
  landed: cmdLanded, land: cmdLand, decide: cmdDecide, wait: cmdWait, report: cmdReport };

async function main(argv) {
  for (const s of [process.stdout, process.stderr]) if (s.setDefaultEncoding) s.setDefaultEncoding('utf8');
  const [cmd, ...rest] = argv;
  if (!cmd || cmd === '-h' || cmd === '--help' || !COMMANDS[cmd]) {
    const head = readFileSync(fileURLToPath(import.meta.url), 'utf8').split('*/')[0].replace(/^#!.*\n\/\*\*\n?/, '').replace(/^ \* ?/gm, '');
    console.log(head.trim());
    return cmd && !COMMANDS[cmd] && cmd !== '-h' && cmd !== '--help' ? 2 : 0;
  }
  const a = parseArgs(cmd, rest);
  const code = await COMMANDS[cmd](a);
  return typeof code === 'number' ? code : 0;
}

const isMain = (() => {
  try {
    const me = realpathSync(fileURLToPath(import.meta.url)), them = realpathSync(path.resolve(process.argv[1] || ''));
    return process.platform === 'win32' ? me.toLowerCase() === them.toLowerCase() : me === them;
  } catch { return false; }
})();
if (isMain) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; }, (e) => {
    if (e instanceof Fail) { console.error(`error: ${e.message}`); process.exitCode = e.code; }
    else { console.error(e && e.stack ? e.stack : String(e)); process.exitCode = 2; }
  });
}

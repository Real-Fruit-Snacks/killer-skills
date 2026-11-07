#!/usr/bin/env node
/**
 * deck.mjs - build, render, check and export a killer-slides deck, and keep the books for a run.
 *
 *   node deck.mjs <command> [options]          node deck.mjs <command> --help shows a command's options
 *
 * Commands
 *   new <folder> --title "..."            write a deck skeleton: deck.html, the engine and a stand-in theme
 *   init <deck> [--talk 10 | --read] ...  start a run in ~/.claude/killer-slides/<deck>/<date>-<slug>/
 *   shoot -w <run> [--sizes ...] [--steps] screenshot every slide at projector, laptop and 4:3 sizes
 *   check -w <run>                        measure every slide in the real render: overflow, small text,
 *                                         contrast, density, notes, assets, motion, layout, timing.
 *                                         Writes check.json; exits 1 if any slide fails
 *   sheet -w <run>                        a contact sheet: every slide as a numbered thumbnail
 *   pdf -w <run> [--notes]                export the PDF, one slide per page, and check its pages
 *   audience -w <run> [--room "..."]      the slide images and the brief for a blind audience agent
 *   heard -w <run>                        read the audience's reply; write the matcher's brief
 *   landed -w <run>                       read the matcher's reply: which key messages landed
 *   land -w <run> -m "..." [--branch b]   commit the deck folder on a local branch (never pushes)
 *   decide -w <run> "<text>"              record a call the user should check
 *   wait -w <run> "<text>" --command "c"  record an outward action left for the user's OK
 *   report -w <run>                       write report.md and summary.json
 *
 * Exit codes: 0 fine, 1 a check failed, 2 a usage or setup error.
 *
 * Environment: KILLER_SLIDES_HOME moves the runs (default ~/.claude/killer-slides);
 * KILLER_SLIDES_SANDBOXES moves the audience folders (default: the temp folder);
 * KILLER_SKILLS_CACHE moves the packs (default ~/.cache); KILLER_SLIDES_NO_INSTALL=1 never
 * installs Playwright or Chromium and fails instead.
 *
 * Needs Node 20+. Playwright comes from the current project, then the killer-skills runtime in
 * ~/.cache/killer-skills, then killer-design's cache, then this skill's own ~/.cache/killer-slides,
 * where the first run installs it if nothing else has it. No other dependencies.
 */
import { createRequire } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { execSync, execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs/promises';
import { existsSync, statSync, readFileSync, readdirSync, realpathSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash, randomBytes } from 'node:crypto';
import zlib from 'node:zlib';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SKILL = path.resolve(HERE, '..');
const SKELETON = path.join(SKILL, 'assets', 'skeleton');
const BRIEFS = path.join(SKILL, 'references', 'briefs.md');
const W = 1920, H = 1080;
const SIZES = '1920x1080,1280x720,1024x768';
const AUDIENCE_SIZE = { width: 1280, height: 720 };
const WORDS_PER_MINUTE = 140;

// Thresholds. Talk decks are seen from the back of a room; read-alone decks carry more words.
const LIMITS = {
  smallWarn: 24, smallFail: 20,
  contrastBody: 4.5, contrastLarge: 3, largePx: 40, largeBoldPx: 32,
  talk: { wordsWarn: 40, wordsFail: 70 },
  read: { wordsWarn: 75, wordsFail: 120 },
  bulletsWarn: 6,
};

class Fail extends Error {
  constructor(msg, code = 2) { super(msg); this.code = code; }
}
const fail = (msg, code = 2) => { throw new Fail(msg, code); };

// ---------------------------------------------------------------- small helpers

const pad2 = (n) => String(n).padStart(2, '0');
const today = () => { const d = new Date(); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`; };
const now = () => { const d = new Date(); return `${today()}T${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`; };
const slugify = (s, dflt = 'deck') => (String(s || '').toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40).replace(/-+$/, '')) || dflt;
const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;
const snippet = (t, n = 60) => { const s = String(t || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const slash = (p) => String(p).split(path.sep).join('/');
const cacheRoot = () => process.env.KILLER_SKILLS_CACHE || path.join(os.homedir(), '.cache');
const runsHome = () => path.resolve(process.env.KILLER_SLIDES_HOME || path.join(os.homedir(), '.claude', 'killer-slides'));
const sandboxesHome = () => path.join(path.resolve(process.env.KILLER_SLIDES_SANDBOXES || os.tmpdir()), 'ksl');

function loadJson(file, dflt) {
  if (!existsSync(file)) return dflt;
  return JSON.parse(readFileSync(file, 'utf8').replace(/^﻿/, ''));
}
async function saveJson(file, data) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.${randomBytes(2).toString('hex')}.tmp`;
  await fs.writeFile(tmp, JSON.stringify(data, null, 2) + '\n', 'utf8');
  await fs.rename(tmp, file);
}

// ---------------------------------------------------------------- PNG, colour and PDF (no dependencies)

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

/** The width and height in a PNG's header. */
export function pngSize(buf) {
  if (buf.length < 24 || buf.readUInt32BE(0) !== 0x89504e47) throw new Error('not a PNG');
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
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
/** A colour with alpha (0-1) painted over an opaque one. */
export function over(top, under) {
  const a = top.a ?? 1;
  return { r: top.r * a + under.r * (1 - a), g: top.g * a + under.g * (1 - a), b: top.b * a + under.b * (1 - a) };
}
export const hex = ({ r, g, b }) => '#' + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');

/**
 * Contrast of text against the pixels actually behind it. `png` is a decoded screenshot taken with
 * the text's fill turned off, so every pixel in the text's boxes is background: a colour, a
 * gradient, an image, an overlay. Returns the ratio at the 10th percentile (the worst tenth of the
 * background) and the background colour there.
 */
export function contrastBehind(png, rects, fg) {
  const { width, height, data } = png;
  const ox = png.x || 0, oy = png.y || 0;
  const seen = [];
  for (const [rx, ry, w, h] of rects) {
    const x = rx - ox, y = ry - oy;
    const x0 = Math.max(0, Math.floor(x)), y0 = Math.max(0, Math.floor(y));
    const x1 = Math.min(width, Math.ceil(x + w)), y1 = Math.min(height, Math.ceil(y + h));
    if (x1 <= x0 || y1 <= y0) continue;
    const sx = Math.max(1, Math.floor((x1 - x0) / 48)), sy = Math.max(1, Math.floor((y1 - y0) / 10));
    for (let yy = y0; yy < y1; yy += sy) {
      for (let xx = x0; xx < x1; xx += sx) {
        const i = (yy * width + xx) * 4;
        const bg = { r: data[i], g: data[i + 1], b: data[i + 2] };
        seen.push([contrast(over(fg, bg), bg), bg]);
      }
    }
  }
  if (!seen.length) return null;
  seen.sort((p, q) => p[0] - q[0]);
  const [ratio, bg] = seen[Math.floor(seen.length * 0.1)];
  return { ratio, bg, samples: seen.length };
}

/** The viewport region that holds every checkable text box, for the text-hidden screenshot. */
function textClip(items, vw, vh) {
  let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
  for (const it of items) {
    if (it.decorative || it.unknown) continue;
    for (const [x, y, w, h] of it.rects) { l = Math.min(l, x); t = Math.min(t, y); r = Math.max(r, x + w); b = Math.max(b, y + h); }
  }
  l = Math.max(0, Math.floor(l)); t = Math.max(0, Math.floor(t)); r = Math.min(vw, Math.ceil(r)); b = Math.min(vh, Math.ceil(b));
  return r - l >= 1 && b - t >= 1 ? { x: l, y: t, width: r - l, height: b - t } : null;
}

/** Page count and page sizes (points) of a PDF, from its page objects. */
export function pdfInfo(buf) {
  const text = buf.toString('latin1');
  const pages = text.match(/\/Type\s*\/Page(?![a-zA-Z])/g) || [];
  const boxes = [...text.matchAll(/\/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]/g)]
    .map((m) => ({ width: +m[3] - +m[1], height: +m[4] - +m[2] }));
  return { pages: pages.length, boxes };
}

// ---------------------------------------------------------------- Playwright (resolved like killer-design's shoot.mjs)

/**
 * Find Playwright and launch Chromium. Looks in the current project, the killer-skills runtime
 * (~/.cache/killer-skills, which brings its own Chromium), killer-design's cache and this skill's
 * cache, in that order. If none has it, installs it into ~/.cache/killer-slides, and downloads
 * Chromium if it's missing, unless `install` is false or KILLER_SLIDES_NO_INSTALL is set.
 * Kept in one function so it can be shared later.
 */
export async function launchChromium({ install = true, find = false } = {}) {
  const root = cacheRoot();
  const runtime = path.join(root, 'killer-skills');
  const own = path.join(root, 'killer-slides');
  const allowed = install && !process.env.KILLER_SLIDES_NO_INSTALL;
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
  if (find) return found;
  if (!found) {
    if (!allowed) throw new Fail("Playwright isn't installed: not in this project, ~/.cache/killer-skills, ~/.cache/killer-design or ~/.cache/killer-slides. Run the killer-skills installer, or run again without KILLER_SLIDES_NO_INSTALL to install it into ~/.cache/killer-slides.");
    console.error(`[deck] Playwright not found - installing it once into ${own} ...`);
    await fs.mkdir(own, { recursive: true });
    const pj = path.join(own, 'package.json');
    if (!existsSync(pj)) await fs.writeFile(pj, '{ "name": "killer-slides-tools", "private": true }\n');
    try {
      execSync('npm install --prefer-offline --no-audit --no-fund --loglevel=error playwright', { cwd: own, stdio: ['ignore', 'inherit', 'inherit'] });
    } catch (e) { throw new Fail(`installing Playwright into ${own} failed (it needs the network the first time): ${e.message}`); }
    const req = createRequire(path.join(own, '__resolve__.js'));
    found = { entry: req.resolve('playwright'), dir: path.dirname(req.resolve('playwright/package.json')), base: own };
  }
  const mod = await import(pathToFileURL(found.entry).href);
  const pw = mod.default ?? mod;
  try {
    return await pw.chromium.launch();
  } catch (e) {
    if (!/Executable doesn't exist|playwright install/i.test(String(e.message))) throw e;
    if (!allowed) throw new Fail(`Playwright is in ${found.base} but its Chromium isn't installed, and KILLER_SLIDES_NO_INSTALL is set.`);
    console.error('[deck] Downloading Chromium for Playwright (one-time) ...');
    execFileSync(process.execPath, [path.join(found.dir, 'cli.js'), 'install', 'chromium'], { stdio: ['ignore', 'inherit', 'inherit'] });
    return await pw.chromium.launch();
  }
}

// ---------------------------------------------------------------- arguments

const COMMON = { workspace: 's', help: 'b' };
const SPECS = {
  new: { pos: ['folder'], opts: { title: 's', lang: 's', minutes: 'n' }, usage: 'new <folder> --title "<title>" [--lang en] [--minutes 10]' },
  init: { pos: ['deck'], opts: { talk: 'n', read: 'b', notes: 'b', 'no-notes': 'b', message: 'l', room: 's', 'no-blind': 'b', 'no-pdf': 'b', slug: 's', branch: 's' },
    usage: 'init <deck folder or deck.html> [--talk <minutes> | --read] [--no-notes] [--message "<key message>" ...] [--room "<who is in the room>"] [--no-blind] [--no-pdf] [--slug <name>] [--branch <name>]' },
  shoot: { opts: { sizes: 's', steps: 'b' }, usage: `shoot -w <run> [--sizes ${SIZES}] [--steps]` },
  check: { opts: {}, usage: 'check -w <run>' },
  sheet: { opts: { out: 's' }, usage: 'sheet -w <run> [--out <file.png>]' },
  pdf: { opts: { notes: 'b', out: 's' }, usage: 'pdf -w <run> [--notes] [--out <file.pdf>]' },
  audience: { opts: { room: 's' }, usage: 'audience -w <run> [--room "<who is in the room>"]' },
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
      if (!t) fail(`unknown option --${key} for ${cmd}.\nusage: node deck.mjs ${spec.usage}`);
      if (t === 'b') { out[key] = true; continue; }
      if (val === null) {
        if (i + 1 >= argv.length) fail(`--${key} needs a value.\nusage: node deck.mjs ${spec.usage}`);
        val = argv[++i];
      }
      if (t === 'n') {
        const v = Number(val);
        if (!Number.isFinite(v) || v <= 0) fail(`--${key} needs a positive number, not "${val}"`);
        out[key] = v;
      } else if (t === 'l') (out[key] ||= []).push(val);
      else out[key] = val;
    } else out._.push(a);
  }
  if (out.help) { console.log(`usage: node deck.mjs ${spec.usage}`); process.exit(0); }
  const pos = spec.pos || [];
  if (out._.length > pos.length) fail(`unexpected argument "${out._[pos.length]}".\nusage: node deck.mjs ${spec.usage}`);
  pos.forEach((name, i) => { out[name] = out._[i]; });
  for (const name of pos) if (!out[name]) fail(`${cmd} needs <${name}>.\nusage: node deck.mjs ${spec.usage}`);
  if (cmd !== 'new' && cmd !== 'init' && !out.workspace) fail(`${cmd} needs -w <run folder> (from init).\nusage: node deck.mjs ${spec.usage}`);
  return out;
}

// ---------------------------------------------------------------- runs

function git(cwd, args, opts = {}) {
  const r = spawnSync('git', args, { cwd, encoding: 'utf8', ...opts });
  if (r.error) return { ok: false, out: '', err: String(r.error.message) };
  return { ok: r.status === 0, out: r.stdout || '', err: r.stderr || '' };
}
// One spelling of a path, so path.relative can compare two: Windows 8.3 short names (RUNNER~1 for
// runneradmin) and symlinks (macOS's /var is /private/var) resolved, as git spells its top folder. A path
// that doesn't exist yet keeps its missing tail.
function canonical(p) {
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
function gitTop(dir) {
  const r = git(dir, ['rev-parse', '--show-toplevel']);
  return r.ok ? canonical(r.out.trim()) : null;
}
function gitStatus(top, deckDir) {
  if (!top) return null;
  const r = git(top, ['status', '--porcelain=v1', '--untracked-files=all', '--', path.relative(top, canonical(deckDir)) || '.']);
  return r.ok ? r.out : null;
}

function resolveDeck(arg) {
  const abs = path.resolve(arg);
  if (existsSync(abs) && statSync(abs).isDirectory()) return { deck: path.join(abs, 'deck.html'), deckDir: abs };
  if (/\.html?$/i.test(abs)) return { deck: abs, deckDir: path.dirname(abs) };
  return { deck: path.join(abs, 'deck.html'), deckDir: abs };
}

/** A hash of the deck folder's files (not PDFs, hidden folders or node_modules), to tell whether a result is current. */
function fingerprint(deckDir) {
  const h = createHash('sha1');
  const walk = (dir, rel) => {
    let names;
    try { names = readdirSync(dir, { withFileTypes: true }); } catch { return; }
    names.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
    for (const e of names) {
      if (e.name.startsWith('.') || e.name === 'node_modules') continue;
      const p = path.join(dir, e.name), r = rel ? `${rel}/${e.name}` : e.name;
      if (e.isDirectory()) walk(p, r);
      else if (e.isFile() && !/\.pdf$/i.test(e.name)) { h.update(r + '\0'); h.update(readFileSync(p)); h.update('\0'); }
    }
  };
  walk(deckDir, '');
  return h.digest('hex').slice(0, 16);
}

async function loadRun(ws) {
  const dir = path.resolve(ws);
  const cfg = loadJson(path.join(dir, 'config.json'), null);
  if (!cfg || cfg.skill !== 'killer-slides') fail(`no killer-slides run in ${dir}; run init first`);
  return { ws: dir, cfg, save: () => saveJson(path.join(dir, 'config.json'), cfg) };
}
function needDeck(cfg) {
  if (!existsSync(cfg.deck)) fail(`no deck at ${cfg.deck}. Write one with: node deck.mjs new "${cfg.deckDir}" --title "<title>"`);
}

// ---------------------------------------------------------------- the browser side (runs inside the page)

/* Installs window.__ksl: the helpers every command uses to move through a deck and measure it.
   Everything in here runs in the browser, so it can't use anything from this file. */
function pageKit() {
  if (window.__ksl) return;
  const K = {};
  const cs = (e) => getComputedStyle(e);
  const frames = (n = 2) => new Promise((r) => { const step = () => (n-- <= 0 ? r() : requestAnimationFrame(step)); step(); });
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  K.slides = () => {
    const m = document.querySelector('.deck');
    const list = m ? [...m.children].filter((e) => e.classList.contains('slide')) : [];
    return list.length ? list : [...document.querySelectorAll('.slide')];
  };
  K.api = () => !!(window.deck && typeof window.deck.go === 'function' && typeof window.deck.count === 'number');
  K.count = () => (K.api() ? window.deck.count : K.slides().length);
  K.visible = (n) => {
    const s = K.slides()[n - 1];
    if (!s) return false;
    const st = cs(s), r = s.getBoundingClientRect();
    return st.visibility === 'visible' && st.display !== 'none' && r.width > 1 && r.right > 0 && r.left < innerWidth && r.bottom > 0 && r.top < innerHeight;
  };
  K.current = () => {
    if (K.api()) return window.deck.index;
    const n = K.slides().findIndex((_, i) => K.visible(i + 1));
    return n + 1;
  };
  K.settle = async (cap = 4000) => {
    await frames(2);
    const running = document.getAnimations().filter((a) => a.playState === 'running' && Number.isFinite(a.effect?.getComputedTiming?.().endTime));
    await Promise.race([Promise.all(running.map((a) => a.finished.catch(() => {}))), wait(cap)]);
    const imgs = [...document.images].filter((i) => !i.complete);
    await Promise.race([Promise.all(imgs.map((i) => new Promise((r) => { i.addEventListener('load', r, { once: true }); i.addEventListener('error', r, { once: true }); }))), wait(3000)]);
    await document.fonts.ready;
    await frames(2);
  };
  K.show = async (n, step, settle = true) => {
    if (K.api()) window.deck.go(n, step ?? 'last');
    else { location.hash = '#/' + n; await wait(150); }
    if (settle) await K.settle(); else await frames(2);
    return { index: K.api() ? window.deck.index : null, visible: K.visible(n) };
  };
  K.steps = (n) => (K.api() && typeof window.deck.steps === 'function' ? window.deck.steps(n) : (K.slides()[n - 1]?.querySelectorAll('[data-step]').length || 0));
  K.showAllBuilds = () => { for (const el of document.querySelectorAll('[data-step]')) el.classList.add('is-shown'); };

  const sel = (el, stop) => {
    const parts = [];
    for (let e = el, d = 0; e && e.nodeType === 1 && e !== stop && d < 4; e = e.parentElement, d++) {
      let s = e.tagName.toLowerCase();
      if (e.id) { parts.unshift(`${s}#${e.id}`); break; }
      const c = [...e.classList].filter((x) => !x.startsWith('is-')).slice(0, 2);
      if (c.length) s += '.' + c.join('.');
      parts.unshift(s);
    }
    return parts.join(' > ');
  };
  const cvs = document.createElement('canvas');
  cvs.width = cvs.height = 1;
  const ctx = cvs.getContext('2d', { willReadFrequently: true });
  const rgba = (c) => {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = '#000';
    ctx.fillStyle = c;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
  };
  const scaleOf = (e) => {
    const s = cs(e);
    let k = 1;
    if (s.transform && s.transform !== 'none') {
      try { const m = new DOMMatrixReadOnly(s.transform); k *= Math.sqrt(Math.abs(m.a * m.d - m.b * m.c)); } catch { /* keep 1 */ }
    }
    if (s.scale && s.scale !== 'none') {
      const v = s.scale.split(/\s+/).map(parseFloat).filter(Number.isFinite);
      if (v.length) k *= v.length > 1 ? Math.sqrt(Math.abs(v[0] * v[1])) : Math.abs(v[0]);
    }
    return k;
  };
  const totalScale = (el) => { let k = 1; for (let e = el; e && e.nodeType === 1; e = e.parentElement) k *= scaleOf(e); return k; };
  const opacityOf = (el) => { let o = 1; for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= parseFloat(cs(e).opacity); return Number.isFinite(o) ? o : 1; };
  const WORD = /[\p{L}\p{N}]+(?:['’.,\-][\p{L}\p{N}]+)*/gu;
  const GENERIC = new Set(['serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui', 'ui-serif', 'ui-sans-serif', 'ui-monospace', 'ui-rounded', 'math', 'emoji', 'fangsong', '-apple-system', 'blinkmacsystemfont']);

  /* Everything check needs about slide n as it stands now (every build in). Rects are viewport px. */
  K.measure = (n) => {
    const slide = K.slides()[n - 1];
    const sr = slide.getBoundingClientRect();
    const k = sr.width / (slide.offsetWidth || 1920);
    const tol = 2 * k;
    const out = { n, title: '', canvas: { x: sr.left, y: sr.top, w: sr.width, h: sr.height, scale: k },
      items: [], overflow: [], spill: [], collisions: [], words: 0, bullets: 0, invisible: 0, decorative: 0,
      images: { broken: [], noAlt: [] }, todos: [], families: [], layout: [], continuous: [] };
    const heading = slide.querySelector('h1, h2, h3');
    out.title = (slide.getAttribute('data-title') || (heading ? heading.textContent : '')).replace(/\s+/g, ' ').trim();
    const notes = slide.querySelector('.notes');
    const notesText = notes ? notes.textContent.replace(/\s+/g, ' ').trim() : '';
    out.notes = { present: notesText.length > 0, words: (notesText.match(WORD) || []).length };
    out.steps = K.steps(n);

    // Text, grouped by the element that holds it.
    const byEl = new Map();
    const walker = document.createTreeWalker(slide, NodeFilter.SHOW_TEXT);
    for (let t = walker.nextNode(); t; t = walker.nextNode()) {
      const el = t.parentElement;
      if (!el || !t.textContent.trim() || el.closest('script, style, noscript, template, .notes, .deck-ui')) continue;
      if (!byEl.has(el)) byEl.set(el, []);
      byEl.get(el).push(t);
    }
    const range = document.createRange();
    const families = new Set();
    const itemEls = [];
    // Elements under a running animation sit somewhere different in every frame; layout comparisons skip them.
    const moving = new Set(document.getAnimations().filter((x) => x.playState === 'running').map((x) => x.effect && x.effect.target).filter(Boolean));
    const animated = (el) => { for (let e = el; e && e !== slide.parentElement; e = e.parentElement) if (moving.has(e)) return true; return false; };
    for (const [el, nodes] of byEl) {
      const s = cs(el);
      if (s.visibility !== 'visible') continue;
      const isSvg = el instanceof SVGElement;
      const raw = [];
      for (const t of nodes) {
        range.selectNodeContents(t);
        for (const r of range.getClientRects()) if (r.width > 0.5 && r.height > 0.5) raw.push(r);
      }
      if (!raw.length && isSvg) { const r = el.getBoundingClientRect(); if (r.width > 0.5) raw.push(r); }
      if (!raw.length) continue;
      const op = opacityOf(el);
      if (op < 0.05) { out.invisible++; continue; }
      let scale = totalScale(el);
      if (isSvg) { const m = el.getScreenCTM && el.getScreenCTM(); if (m) scale = Math.sqrt(Math.abs(m.a * m.d - m.b * m.c)); }
      const fontPx = parseFloat(s.fontSize);
      const px = fontPx * scale;
      // The line box: Range rects cover the font's whole ascent and descent, which tight leading lets
      // overhang the line. Measuring the line keeps a 0.9 line-height headline from reading as clipped.
      const lh = s.lineHeight === 'normal' ? fontPx * 1.2 : parseFloat(s.lineHeight);
      const lineH = isSvg || !Number.isFinite(lh) ? Infinity : lh * scale;
      const rects = raw.map((r) => {
        const h = Math.min(r.height, lineH), y = r.top + (r.height - h) / 2;
        return [r.left, y, r.width, h];
      });
      const text = nodes.map((t) => t.textContent).join(' ').replace(/\s+/g, ' ').trim();
      const decorative = !!el.closest('[aria-hidden="true"]');
      const fill = isSvg ? s.fill : (s.webkitTextFillColor || s.color);
      const clipText = !isSvg && (/text/.test(s.backgroundClip || '') || /text/.test(s.webkitBackgroundClip || ''));
      const fg = /url\(/.test(fill) ? { r: 0, g: 0, b: 0, a: 0 } : rgba(fill);
      fg.a *= op * (isSvg ? parseFloat(s.fillOpacity) || 1 : 1);
      let unknown = null;
      if (clipText || fg.a === 0) unknown = 'gradient or image fill';
      else if (s.mixBlendMode && s.mixBlendMode !== 'normal') unknown = `mix-blend-mode: ${s.mixBlendMode}`;
      if (clipText) el.setAttribute('data-ksl-cliptext', '');
      const words = (text.match(WORD) || []).length;
      const item = { el: sel(el, slide), text: text.slice(0, 80), words, px: Math.round(px * 10) / 10, weight: parseInt(s.fontWeight, 10) || 400,
        fg: { r: fg.r, g: fg.g, b: fg.b, a: fg.a }, unknown, decorative, rects };
      out.items.push(item);
      itemEls.push(el);
      const u = rects.reduce((a, [x, y, w, h]) => [Math.min(a[0], x), Math.min(a[1], y), Math.max(a[2], x + w), Math.max(a[3], y + h)], [Infinity, Infinity, -Infinity, -Infinity]);
      out.layout.push([(u[0] - sr.left) / k, (u[1] - sr.top) / k, (u[2] - u[0]) / k, (u[3] - u[1]) / k, px / k].map((v) => Math.round(v * 10) / 10).concat(animated(el) ? 1 : 0));
      if (decorative) { out.decorative++; continue; }
      out.words += words;
      const fam = s.fontFamily.split(',')[0].replace(/["']/g, '').trim();
      if (fam) families.add(fam);

      // Off the canvas.
      let worst = null;
      for (const [x, y, w, h] of rects) {
        const over = Math.max(sr.left - x, x + w - sr.right, sr.top - y, y + h - sr.bottom);
        if (over > tol && (!worst || over > worst.by)) {
          const edge = x + w - sr.right === over ? 'right' : sr.left - x === over ? 'left' : y + h - sr.bottom === over ? 'bottom' : 'top';
          worst = { by: Math.round(over / k), edge };
        }
      }
      if (worst) out.overflow.push({ kind: 'off-canvas', el: item.el, text: item.text, detail: `runs ${worst.by}px past the ${worst.edge} edge of the slide` });
      // Clipped by a box that hides its overflow.
      for (let a = el; a && a !== slide; a = a.parentElement) {
        const st = cs(a);
        const cx = st.overflowX !== 'visible', cy = st.overflowY !== 'visible';
        if (!cx && !cy) continue;
        const ar = a.getBoundingClientRect();
        const ka = a.offsetWidth ? ar.width / a.offsetWidth : 1;
        const box = { l: ar.left + parseFloat(st.borderLeftWidth) * ka, r: ar.right - parseFloat(st.borderRightWidth) * ka,
          t: ar.top + parseFloat(st.borderTopWidth) * ka, b: ar.bottom - parseFloat(st.borderBottomWidth) * ka };
        let by = 0;
        for (const [x, y, w, h] of rects) {
          if (cx) by = Math.max(by, box.l - x, x + w - box.r);
          if (cy) by = Math.max(by, box.t - y, y + h - box.b);
        }
        if (by > tol) {
          out.overflow.push({ kind: 'clipped', el: item.el, text: item.text, detail: `is cut off by ${sel(a, slide)} (overflow: ${st.overflowX === st.overflowY ? st.overflowX : st.overflowX + ' ' + st.overflowY}), ${Math.round(by / k)}px hidden` });
          break;
        }
      }
      // Spilling out of a visible box (a card with a background or border) that doesn't clip.
      for (let a = el; a && a !== slide; a = a.parentElement) {
        const st = cs(a);
        if (st.display === 'inline' || st.display === 'contents' || st.overflowX !== 'visible' || st.overflowY !== 'visible') continue;
        const boxed = rgba(st.backgroundColor).a > 0.05 || (st.backgroundImage && st.backgroundImage !== 'none')
          || ['Top', 'Right', 'Bottom', 'Left'].some((side) => parseFloat(st[`border${side}Width`]) > 0 && rgba(st[`border${side}Color`]).a > 0.05);
        if (!boxed) continue;
        const ar = a.getBoundingClientRect();
        let by = 0;
        for (const [x, y, w, h] of rects) by = Math.max(by, ar.left - x, x + w - ar.right, ar.top - y, y + h - ar.bottom);
        if (by > 4 * k) { out.spill.push({ el: item.el, text: item.text, box: sel(a, slide), by: Math.round(by / k) }); break; }
      }
    }
    out.families = [...families];

    // Text on top of other text.
    const live = out.items.map((it, i) => ({ it, el: itemEls[i] })).filter((x) => !x.it.decorative);
    for (let i = 0; i < live.length && out.collisions.length < 5; i++) {
      for (let j = i + 1; j < live.length && out.collisions.length < 5; j++) {
        const A = live[i], B = live[j];
        if (A.el === B.el || A.el.contains(B.el) || B.el.contains(A.el)) continue;
        let hit = false;
        for (const [ax, ay, aw, ah] of A.it.rects) {
          for (const [bx, by, bw, bh] of B.it.rects) {
            const ox = Math.min(ax + aw, bx + bw) - Math.max(ax, bx), oy = Math.min(ay + ah, by + bh) - Math.max(ay, by);
            if (ox > 0 && oy > 0 && ox * oy > 0.25 * Math.min(aw * ah, bw * bh)) { hit = true; break; }
          }
          if (hit) break;
        }
        if (hit) out.collisions.push({ a: A.it.text.slice(0, 40), b: B.it.text.slice(0, 40) });
      }
    }

    // Pictures, diagrams and tables cut off by the slide's edge. Decorative ones may bleed.
    for (const el of slide.querySelectorAll('img, svg, video, canvas, iframe, object, embed, table')) {
      if (el.closest('.notes') || (el.tagName.toLowerCase() === 'svg' && el.parentElement && el.parentElement.closest('svg'))) continue;
      if (el.closest('[aria-hidden="true"], [data-bleed]') || /^(presentation|none)$/.test(el.getAttribute('role') || '') || (el.tagName === 'IMG' && el.getAttribute('alt') === '')) continue;
      const st = cs(el), r = el.getBoundingClientRect();
      if (st.visibility !== 'visible' || r.width < 1 || r.height < 1 || opacityOf(el) < 0.05) continue;
      const over = Math.max(sr.left - r.left, r.right - sr.right, sr.top - r.top, r.bottom - sr.bottom);
      if (over > tol) {
        const what = el.tagName.toLowerCase() + (el.getAttribute('alt') ? ` "${el.getAttribute('alt').slice(0, 40)}"` : '');
        out.overflow.push({ kind: 'cut-off', el: sel(el, slide), text: what, detail: `is cut off by the slide edge (${Math.round(over / k)}px outside); mark it data-bleed if that's deliberate` });
      }
    }

    for (const li of slide.querySelectorAll('li')) {
      if (li.closest('.notes')) continue;
      const r = li.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && cs(li).visibility === 'visible' && opacityOf(li) > 0.05) out.bullets++;
    }
    for (const img of slide.querySelectorAll('img')) {
      if (img.closest('.notes')) continue;
      // Images from the network are reported once, as requests that leave the machine.
      if (img.getAttribute('src') && img.complete && img.naturalWidth === 0 && /^(file|data|blob):/i.test(img.src)) out.images.broken.push({ src: img.getAttribute('src'), url: img.src });
      if (!img.hasAttribute('alt')) out.images.noAlt.push(img.getAttribute('src') || sel(img, slide));
    }
    for (const t of slide.querySelectorAll('.todo')) if (!t.closest('.notes')) out.todos.push(t.textContent.replace(/\s+/g, ' ').trim().slice(0, 60));
    for (const a of document.getAnimations()) {
      const t = a.effect && a.effect.target;
      const timing = a.effect && a.effect.getComputedTiming ? a.effect.getComputedTiming() : {};
      if (t && slide.contains(t) && a.playState === 'running' && timing.iterations === Infinity) {
        out.continuous.push(`${a.animationName || a.transitionProperty || 'animation'} on ${sel(t, slide)}`);
      }
    }
    out.continuous = [...new Set(out.continuous)].slice(0, 4);
    return out;
  };

  /* Animations running now inside slide n (or on the deck around it). */
  K.running = (n) => {
    const slide = K.slides()[n - 1];
    const found = [];
    for (const a of document.getAnimations()) {
      if (a.playState !== 'running') continue;
      const t = a.effect && a.effect.target;
      if (!t || !(slide.contains(t) || t.contains(slide))) continue;
      const timing = a.effect.getComputedTiming ? a.effect.getComputedTiming() : {};
      if (timing.iterations !== Infinity && !(timing.duration > 50)) continue;
      found.push(`${a.animationName || (a.transitionProperty ? 'transition of ' + a.transitionProperty : 'animation')} on ${sel(t, slide) || t.tagName.toLowerCase()}`);
    }
    return [...new Set(found)];
  };

  K.fonts = (families) => {
    const faces = [...document.fonts];
    const norm = (f) => f.replace(/["']/g, '').trim().toLowerCase();
    const failed = [...new Set(faces.filter((f) => f.status === 'error').map((f) => f.family.replace(/["']/g, '')))];
    const res = [];
    for (const fam of families) {
      const mine = faces.filter((f) => norm(f.family) === norm(fam));
      if (GENERIC.has(norm(fam))) res.push({ family: fam, status: 'generic' });
      else if (!mine.length) res.push({ family: fam, status: 'not in the deck' });
      else if (mine.some((f) => f.status === 'loaded')) res.push({ family: fam, status: 'loaded' });
      else res.push({ family: fam, status: 'not loaded' });
    }
    return { failed, families: res };
  };

  K.externalLinks = () => [...document.querySelectorAll('link[href], script[src], img[src], source[src], video[src], audio[src], iframe[src], object[data]')]
    .map((el) => el.getAttribute('href') || el.getAttribute('src') || el.getAttribute('data'))
    .filter((u) => /^(https?:)?\/\//i.test(u || ''));

  K.notesHtml = (n) => {
    const notes = K.slides()[n - 1]?.querySelector('.notes');
    if (!notes) return '';
    const c = notes.cloneNode(true);
    c.querySelectorAll('script, style, iframe, object, embed, link, img, video, audio').forEach((e) => e.remove());
    c.querySelectorAll('*').forEach((e) => { for (const a of [...e.attributes]) if (/^on/i.test(a.name) || a.name === 'style') e.removeAttribute(a.name); });
    return c.innerHTML;
  };
  window.__ksl = K;
}

const HIDE_TEXT = `
.slide *, .slide { -webkit-text-fill-color: transparent !important; -webkit-text-stroke-color: transparent !important;
  text-decoration-color: transparent !important; text-emphasis-color: transparent !important; caret-color: transparent !important; }
.slide *::before, .slide *::after, .slide::before, .slide::after, .slide *::marker { -webkit-text-fill-color: initial !important; }
.slide svg text, .slide svg tspan, .slide svg textPath { fill-opacity: 0 !important; stroke-opacity: 0 !important; }
[data-ksl-cliptext] { background: none !important; }`;

/** Open the deck from its file URL, blocking and recording every request that leaves this machine. */
async function openDeck(browser, cfg, { width = W, height = H, reduced = false, hash = '' } = {}) {
  const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1, reducedMotion: reduced ? 'reduce' : 'no-preference' });
  const log = { external: [], missing: [], blocked: [], console: [] };
  await context.route('**/*', (route) => {
    const u = route.request().url();
    if (/^(file|data|blob|about):/i.test(u)) return route.continue();
    log.external.push(u.slice(0, 200));
    return route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.on('console', (m) => { if (m.type() === 'error' && !/^Failed to load resource/i.test(m.text())) log.console.push(m.text().slice(0, 300)); });
  page.on('pageerror', (e) => log.console.push(String(e.message || e).slice(0, 300)));
  page.on('requestfailed', (r) => {
    const u = r.url();
    if (!/^file:/i.test(u)) return;
    let p = u;
    try { p = fileURLToPath(u); } catch { /* keep the URL */ }
    if (!existsSync(p)) log.missing.push(p);
    else log.blocked.push(`${p} (${r.failure()?.errorText || 'failed'})`);
  });
  page.on('websocket', (s) => log.external.push(s.url().slice(0, 200)));
  await page.goto(pathToFileURL(cfg.deck).href + hash, { waitUntil: 'load', timeout: 60000 });
  await page.evaluate(() => document.fonts.ready.then(() => true));
  await page.evaluate(pageKit);
  return { context, page, log };
}

async function withBrowser(cfg, fn, run) {
  let browser;
  try {
    browser = await launchChromium();
  } catch (e) {
    if (run) { run.cfg.blocked = `the browser couldn't start: ${e.message}`; await run.save(); }
    throw e instanceof Fail ? e : new Fail(`couldn't start Chromium: ${e.message}`);
  }
  try {
    if (run && run.cfg.blocked) { run.cfg.blocked = null; await run.save(); }
    return await fn(browser);
  } finally {
    await browser.close().catch(() => {});
  }
}

async function slideCount(page) {
  const n = await page.evaluate(() => window.__ksl.count());
  if (!n) fail('the deck has no slides: expected <section class="slide"> elements inside <main class="deck">');
  return n;
}

// ---------------------------------------------------------------- new

async function cmdNew(a) {
  const dir = path.resolve(a.folder);
  if (existsSync(path.join(dir, 'deck.html'))) fail(`${path.join(dir, 'deck.html')} already exists; new never overwrites a deck`);
  const title = (a.title || 'Untitled talk').trim();
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const html = readFileSync(path.join(SKELETON, 'deck.html'), 'utf8')
    .replace(/\{\{title\}\}/g, esc(title))
    .replace(/\{\{lang\}\}/g, esc(a.lang || 'en'))
    .replace(/\{\{minutes\}\}/g, a.minutes ? ` data-minutes="${a.minutes}"` : '');
  await fs.mkdir(path.join(dir, 'assets', 'images'), { recursive: true });
  for (const f of ['deck.js', 'deck.css', 'theme.css']) await fs.copyFile(path.join(SKELETON, 'assets', f), path.join(dir, 'assets', f));
  await fs.writeFile(path.join(dir, 'deck.html'), html, 'utf8');
  console.log(`wrote a deck skeleton in ${dir}:`);
  console.log('  deck.html          the slides and their speaker notes');
  console.log('  assets/theme.css   the look: neutral stand-ins to replace with this deck\'s own design');
  console.log('  assets/deck.js     the engine: keys, builds, presenter view, overview (leave as it is)');
  console.log('  assets/deck.css    the engine\'s canvas, scaling and print rules (leave as it is)');
  console.log('  assets/images/     pictures the slides use');
  console.log(`Fonts: node "${path.join(HERE, 'fonts.mjs')}" add "${dir}" "<Family>" ..., then link assets/fonts/fonts.css before theme.css.`);
}

// ---------------------------------------------------------------- init

async function cmdInit(a) {
  // The run usually starts before the deck exists (the outline comes first); new writes the folder later.
  const { deck, deckDir } = resolveDeck(a.deck);
  if (existsSync(deckDir) && !statSync(deckDir).isDirectory()) fail(`${deckDir} is a file, not a folder`);
  if (a.talk && a.read) fail('pick one: --talk <minutes> for a talk, or --read for a deck people read on their own');
  const messages = (a.message || []).map((m) => m.trim()).filter(Boolean);
  if (messages.length > 5) fail(`${messages.length} key messages is too many to test; keep the three to five that matter most`);
  roomLeaks((a.room || '').trim(), messages, 'init ... --room "<who they are>"');
  let near = deckDir;
  while (!existsSync(near) && path.dirname(near) !== near) near = path.dirname(near);
  const top = gitTop(near);
  const key = `${slugify(path.basename(deckDir), 'deck')}-${createHash('sha1').update(os.platform() === 'win32' ? deck.toLowerCase() : deck).digest('hex').slice(0, 8)}`;
  const name = slugify(a.slug || path.basename(deckDir));
  let ws = path.join(runsHome(), key, `${today()}-${name}`);
  for (let i = 2; existsSync(path.join(ws, 'config.json')); i++) ws = path.join(runsHome(), key, `${today()}-${name}-${i}`);
  const branch = top ? git(top, ['symbolic-ref', '-q', '--short', 'HEAD']).out.trim() || null : null;
  const cfg = {
    skill: 'killer-slides', version: 1, run_id: randomBytes(3).toString('hex'), created: now(),
    deck, deckDir,
    talk: a.read ? { read: true } : { minutes: a.talk || 10 },
    notes: !a['no-notes'], blind: !a['no-blind'], pdf: !a['no-pdf'],
    messages, room: a.room || '',
    git: { top, startBranch: branch, status: gitStatus(top, deckDir) },
    branch: a.branch || null, branchNamed: !!a.branch, commits: [], decisions: [], waiting: [],
    checks: [], shots: null, sheet: null, pdfs: [], rounds: [], blocked: null,
  };
  await saveJson(path.join(ws, 'config.json'), cfg);
  console.log(`Run folder: ${ws}`);
  console.log(`Deck: ${deck}${existsSync(deck) ? '' : ` (not written yet: node deck.mjs new "${deckDir}" --title "<title>")`}`);
  console.log(`Talk: ${cfg.talk.read ? 'a deck people read on their own' : `${cfg.talk.minutes} minutes`} · notes ${cfg.notes ? 'required on every slide' : 'not required'} · blind audience ${cfg.blind ? 'on' : 'off'} · PDF ${cfg.pdf ? 'yes' : 'no'}`);
  console.log(messages.length ? `Key messages:\n${messages.map((m, i) => `  ${i + 1}. ${m}`).join('\n')}` : 'Key messages: none set (the blind audience check needs them)');
  if (top) console.log(`Git: ${top} on ${branch || 'a detached HEAD'}; land commits on ${a.branch || `killer-slides/${today()}`}`);
}

// ---------------------------------------------------------------- shoot

async function cmdShoot(a) {
  const run = await loadRun(a.workspace);
  const { cfg, ws } = run;
  needDeck(cfg);
  const sizes = (a.sizes || SIZES).split(',').map((s) => s.trim()).filter(Boolean).map((s) => {
    const m = s.match(/^(\d{3,5})x(\d{3,5})$/);
    if (!m) fail(`--sizes takes WIDTHxHEIGHT pairs like 1920x1080, not "${s}"`);
    return { width: +m[1], height: +m[2], tag: s };
  });
  let written = [];
  await withBrowser(cfg, async (browser) => {
    written = await Promise.all(sizes.map(async (size) => {
      const dir = path.join(ws, 'shots', size.tag);
      await fs.mkdir(dir, { recursive: true });
      for (const f of await fs.readdir(dir)) if (/\.png$/.test(f)) await fs.rm(path.join(dir, f));
      const { context, page } = await openDeck(browser, cfg, size);
      const count = await slideCount(page);
      for (let n = 1; n <= count; n++) {
        if (a.steps) {
          const steps = await page.evaluate((i) => window.__ksl.steps(i), n);
          for (let s = 0; s < steps; s++) {
            await page.evaluate(([i, st]) => window.__ksl.show(i, st), [n, s]);
            await page.screenshot({ path: path.join(dir, `slide-${pad2(n)}-step-${s}.png`), animations: 'disabled' });
          }
        }
        await page.evaluate((i) => window.__ksl.show(i, 'last'), n);
        await page.screenshot({ path: path.join(dir, `slide-${pad2(n)}.png`), animations: 'disabled' });
      }
      await context.close();
      return { tag: size.tag, dir, count };
    }));
  }, run);
  cfg.shots = { at: now(), sizes: sizes.map((s) => s.tag), steps: !!a.steps, fingerprint: fingerprint(cfg.deckDir) };
  await run.save();
  for (const w of written) console.log(`${w.tag}: ${plural(w.count, 'slide')} -> ${w.dir}`);
  console.log(`Each slide shows its final state (every build in)${a.steps ? '; slide-NN-step-K.png shows it before build K+1' : ''}.`);
  console.log('Read them: 1920x1080 is the projector, 1280x720 a laptop or a small projector, 1024x768 a 4:3 projector letterboxing the deck.');
}

// ---------------------------------------------------------------- check

async function cmdCheck(a) {
  const run = await loadRun(a.workspace);
  const { cfg, ws } = run;
  needDeck(cfg);
  const shotDir = path.join(ws, 'shots', `${W}x${H}`);
  await fs.mkdir(shotDir, { recursive: true });
  for (const f of await fs.readdir(shotDir)) if (/^slide-\d+\.png$/.test(f)) await fs.rm(path.join(shotDir, f));
  const limits = cfg.talk.read ? LIMITS.read : LIMITS.talk;
  const result = { deck: cfg.deck, generatedAt: now(), fingerprint: fingerprint(cfg.deckDir), viewport: `${W}x${H}`,
    limits: { ...LIMITS, words: limits }, api: false, slides: [], deckIssues: { fails: [], warns: [], infos: [] } };

  await withBrowser(cfg, async (browser) => {
    const families = new Set();
    const links = [];
    let fontInfo, staticExternal;

    // Pass 1: the projector at 1920x1080, motion allowed, every build in. Then every #/n link.
    const passA = async (A, count) => {
      for (let n = 1; n <= count; n++) {
        const nav = await A.page.evaluate((i) => window.__ksl.show(i, 'last'), n);
        const m = await A.page.evaluate((i) => window.__ksl.measure(i), n);
        const shot = path.join(shotDir, `slide-${pad2(n)}.png`);
        await A.page.screenshot({ path: shot, animations: 'disabled' });
        // The same slide with the text's fill switched off: what's left in the text's boxes is background.
        // Only the region holding text is captured, to keep it quick.
        let bare = null;
        const clip = textClip(m.items, W, H);
        if (clip) {
          const hide = await A.page.addStyleTag({ content: HIDE_TEXT });
          bare = { ...decodePng(await A.page.screenshot({ clip, animations: 'disabled' })), x: clip.x, y: clip.y };
          await hide.evaluate((el) => el.remove());
        }
        await A.page.evaluate(() => document.querySelectorAll('[data-ksl-cliptext]').forEach((e) => e.removeAttribute('data-ksl-cliptext')));
        m.families.forEach((f) => families.add(f));
        result.slides[n - 1] = judge(m, nav, bare, shot, cfg, limits);
      }
      for (let n = 1; n <= count; n++) {
        const got = await A.page.evaluate(async (i) => { location.hash = '#/' + i; await new Promise((r) => setTimeout(r, 60)); return window.__ksl.current(); }, n);
        if (got !== n) links.push(`#/${n} showed slide ${got || 'none'}`);
      }
      fontInfo = await A.page.evaluate((f) => window.__ksl.fonts(f), [...families]);
      staticExternal = await A.page.evaluate(() => window.__ksl.externalLinks());
    };

    // Pass 2: a 4:3 projector with reduced motion on. Nothing may move, and every slide must lay
    // out exactly as it does at 1920x1080 (vw, vh or media queries inside a slide break that).
    const second = [];
    const passB = async (B, count) => {
      for (let n = 1; n <= count; n++) {
        const steps = await B.page.evaluate((i) => window.__ksl.steps(i), n);
        const moving = new Set();
        for (let s = 0; s <= steps; s++) {
          await B.page.evaluate(([i, st]) => window.__ksl.show(i, st, false), [n, s === steps ? 'last' : s]);
          for (const x of await B.page.evaluate((i) => window.__ksl.running(i), n)) moving.add(x);
        }
        await B.page.evaluate(() => window.__ksl.settle(1500));
        const one = await B.page.screenshot();
        await B.page.waitForTimeout(250);
        const two = await B.page.screenshot();
        if (!one.equals(two)) moving.add('the picture changes while nothing is pressed (a script, canvas, video or GIF)');
        second[n - 1] = { moving: [...moving], layout: (await B.page.evaluate((i) => window.__ksl.measure(i), n)).layout };
      }
    };

    // Both passes, and a fresh load straight onto the last slide, run side by side.
    const [A, B] = await Promise.all([openDeck(browser, cfg), openDeck(browser, cfg, { width: 1024, height: 768, reduced: true })]);
    const count = await slideCount(A.page);
    result.api = await A.page.evaluate(() => window.__ksl.api());
    result.title = (await A.page.title()).trim();
    const passL = async () => {
      const L = await openDeck(browser, cfg, { hash: `#/${count}` });
      const landed = await L.page.evaluate(async () => { await new Promise((r) => setTimeout(r, 100)); return window.__ksl.current(); });
      if (landed !== count) links.push(`opening deck.html#/${count} showed slide ${landed || 'none'}`);
      await L.context.close();
      return L;
    };
    const [, , L] = await Promise.all([passA(A, count), passB(B, count), passL()]);
    await Promise.all([A.context.close(), B.context.close()]);

    for (let n = 1; n <= count; n++) {
      const slide = result.slides[n - 1], b = second[n - 1];
      if (b.moving.length) addIssue(slide, 'fails', 'motion', `moves with reduced motion on: ${b.moving.slice(0, 3).join('; ')}. Put animations inside @media (prefers-reduced-motion: no-preference), or check matchMedia in the script`);
      const diff = layoutDiff(slide._layout, b.layout, slide._texts);
      if (diff) addIssue(slide, 'fails', 'layout', `lays out differently at 1024x768 than at 1920x1080: ${diff}. Something inside the slide uses vw, vh or a media query; size it in px on the 1920x1080 canvas`);
    }
    const shownBroken = new Set(result.slides.flatMap((s) => s.fails.filter((f) => f.check === 'assets').flatMap((f) => (f.items || []).map((b) => b.url))));
    const external = new Set();
    for (const u of [...staticExternal, ...A.log.external, ...B.log.external, ...L.log.external]) {
      const key = u.replace(/^\/\//, 'https://');
      if (external.has(key)) continue;
      external.add(key);
      addDeck(result, 'fails', 'external', `loads ${key} from the network (blocked): decks must be self-contained, so put the file in assets/`);
    }
    for (const log of [A.log, B.log, L.log]) {
      for (const p of log.missing) if (!shownBroken.has(pathToFileURL(p).href)) addDeck(result, 'fails', 'assets', `missing file: ${p}`);
      for (const p of log.blocked) addDeck(result, 'fails', 'assets', `the browser refused to load ${p}`);
      for (const c of log.console) {
        if (/blocked by CORS policy/i.test(c) && /file:/i.test(c)) addDeck(result, 'fails', 'console', `a file was loaded the way a web server would serve it, and browsers refuse that for a deck opened from disk: ${snippet(c, 160)}. Usually a <link rel="preload" ... crossorigin> (remove it: the @font-face rule loads the font by itself), fetch(), or <script type="module">`);
        else addDeck(result, 'fails', 'console', `console error: ${c}`);
      }
    }
    if (!result.api) addDeck(result, 'warns', 'navigation', 'no window.deck API (the deck wasn\'t built from the skeleton): moved between slides with #/n links, and builds were not checked');
    for (const l of links) addDeck(result, 'fails', 'navigation', `deep link ${l}`);
    for (const f of fontInfo.failed) addDeck(result, 'fails', 'fonts', `font "${f}" failed to load: check the url() in fonts.css`);
    for (const f of fontInfo.families) {
      if (f.status === 'not loaded') addDeck(result, 'fails', 'fonts', `font "${f.family}" is declared but didn't load, so the slides show a fallback`);
      if (f.status === 'not in the deck') addDeck(result, 'warns', 'fonts', `font "${f.family}" isn't in the deck: it shows only on machines that have it installed. Add it with fonts.mjs, or use a generic family`);
    }
    result.fonts = fontInfo.families;
  }, run);

  // Talk length against slides and notes.
  const count = result.slides.length;
  const notesWords = result.slides.reduce((s, x) => s + x.notesWords, 0);
  result.timing = { slides: count, notesWords };
  if (!cfg.talk.read) {
    const mins = cfg.talk.minutes;
    const content = count > 3 ? count - 2 : count;
    const spoken = Math.round((notesWords / WORDS_PER_MINUTE) * 10) / 10;
    result.timing = { ...result.timing, minutes: mins, contentSlides: content, notesMinutes: notesWords ? spoken : null };
    const say = (m) => (m < 1 ? 'under a minute' : `about ${m} minutes`);
    addDeck(result, 'infos', 'timing', `${plural(count, 'slide')} (${content} content) for a ${mins}-minute talk: at 1 to 2 minutes a content slide that's ${Math.ceil(mins / 2)} to ${mins}` + (notesWords ? `. The notes run ${say(spoken)} at ${WORDS_PER_MINUTE} words a minute` : ''));
    if (content > mins * 2) addDeck(result, 'warns', 'timing', `${content} content slides in ${mins} minutes is under 30 seconds each: merge or cut, unless the deck is built to move fast`);
    else if (content < mins / 3) addDeck(result, 'warns', 'timing', `${content} content slides for ${mins} minutes is over 3 minutes on each: the room will stare at one slide for a long time`);
    if (notesWords && spoken > mins * 1.15) addDeck(result, 'warns', 'timing', `the notes take about ${spoken} minutes to say; the talk is ${mins}. Cut the notes or the slides`);
    if (cfg.notes && notesWords && spoken < mins * 0.5) addDeck(result, 'infos', 'timing', `the notes cover ${say(spoken)} of ${mins}; the rest has to be improvised`);
  }

  for (const s of result.slides) { delete s._layout; delete s._texts; }
  const failing = result.slides.filter((s) => s.fails.length);
  const fails = failing.reduce((t, s) => t + s.fails.length, 0) + result.deckIssues.fails.length;
  const warns = result.slides.reduce((t, s) => t + s.warns.length, 0) + result.deckIssues.warns.length;
  result.summary = { slides: count, failingSlides: failing.map((s) => s.n), fails, warns };
  result.ok = fails === 0;
  await saveJson(path.join(ws, 'check.json'), result);
  cfg.checks.push({ at: result.generatedAt, fingerprint: result.fingerprint, slides: count, fails, warns, ok: result.ok, failing: failing.map((s) => s.n) });
  await run.save();

  console.log(`check: ${plural(count, 'slide')} at ${W}x${H} -> ${path.join(ws, 'check.json')}\n`);
  const line = (lvl, s, m) => console.log(`${lvl}  ${s ? `slide ${s.n}${s.title ? ` "${snippet(s.title, 40)}"` : ''}: ` : ''}${m.message}`);
  for (const lvl of ['fails', 'warns']) {
    for (const s of result.slides) for (const m of s[lvl]) line(lvl === 'fails' ? 'FAIL' : 'WARN', s, m);
    for (const m of result.deckIssues[lvl]) line(lvl === 'fails' ? 'FAIL' : 'WARN', null, m);
  }
  for (const s of result.slides) for (const m of s.infos) line('INFO', s, m);
  for (const m of result.deckIssues.infos) line('INFO', null, m);
  console.log(`\nScreenshots: ${shotDir}`);
  if (result.ok) console.log(`All ${plural(count, 'slide')} pass. That only means nothing measurable is wrong: now look at the screenshots.`);
  else console.log(`${count - failing.length} of ${plural(count, 'slide')} pass; ${plural(fails, 'failure')} in all. Fix the FAIL lines and run check again.`);
  return result.ok ? 0 : 1;
}

function addIssue(slide, lvl, check, message, items) { slide[lvl].push({ check, message, ...(items ? { items } : {}) }); }
function addDeck(result, lvl, check, message) {
  if (!result.deckIssues[lvl].some((x) => x.message === message)) result.deckIssues[lvl].push({ check, message });
}

/** Turn one slide's measurements into its FAIL, WARN and INFO lines. */
function judge(m, nav, bare, shot, cfg, limits) {
  const s = { n: m.n, title: m.title, words: m.words, bullets: m.bullets, steps: m.steps, notes: m.notes.present, notesWords: m.notes.words,
    screenshot: shot, fails: [], warns: [], infos: [], contrast: [], _layout: m.layout, _texts: m.items.map((i) => i.text) };
  if (!nav.visible || (nav.index && nav.index !== m.n)) addIssue(s, 'fails', 'navigation', `going to slide ${m.n} showed slide ${nav.index || 'none'}`);
  for (const o of m.overflow) addIssue(s, 'fails', 'overflow', `"${snippet(o.text, 50)}" ${o.detail}`, [o]);
  for (const o of m.spill.slice(0, 3)) addIssue(s, 'warns', 'overflow', `"${snippet(o.text, 50)}" spills ${o.by}px out of its box (${o.box})`, [o]);
  for (const c of m.collisions) addIssue(s, 'warns', 'collision', `"${snippet(c.a, 30)}" overlaps "${snippet(c.b, 30)}"`);

  const live = m.items.filter((i) => !i.decorative);
  const small = live.filter((i) => i.px < LIMITS.smallFail);
  const smallish = live.filter((i) => i.px >= LIMITS.smallFail && i.px < LIMITS.smallWarn);
  const list = (xs) => xs.slice(0, 3).map((i) => `${i.px}px "${snippet(i.text, 30)}"`).join(' | ');
  if (small.length) addIssue(s, 'fails', 'small-text', `text under ${LIMITS.smallFail}px can't be read from the back of the room: ${list(small)}`, small.map(({ el, text, px }) => ({ el, text, px })));
  if (smallish.length) addIssue(s, 'warns', 'small-text', `text under ${LIMITS.smallWarn}px (about 18pt projected): ${list(smallish)}`, smallish.map(({ el, text, px }) => ({ el, text, px })));

  let unknown = 0;
  for (const it of live) {
    if (it.unknown) { unknown++; continue; }
    const c = bare && contrastBehind(bare, it.rects, it.fg);
    if (!c) continue;
    const need = it.px >= LIMITS.largePx || (it.px >= LIMITS.largeBoldPx && it.weight >= 700) ? LIMITS.contrastLarge : LIMITS.contrastBody;
    const row = { el: it.el, text: it.text, px: it.px, ratio: Math.round(c.ratio * 100) / 100, need, fg: hex(over(it.fg, c.bg)), bg: hex(c.bg) };
    s.contrast.push(row);
    if (c.ratio < need) addIssue(s, 'fails', 'contrast', `${row.ratio}:1, needs ${need}:1: "${snippet(it.text, 40)}" ${row.fg} on ${row.bg}`, [row]);
  }
  if (unknown) addIssue(s, 'infos', 'contrast', `${plural(unknown, 'text element')} with a gradient or image fill or a blend mode: check their contrast by eye`);
  if (m.decorative) addIssue(s, 'infos', 'decorative', `${plural(m.decorative, 'text element')} marked aria-hidden (decorative) weren't checked for size or contrast`);
  if (m.invisible) addIssue(s, 'warns', 'invisible', `${plural(m.invisible, 'text element')} still invisible with every build in (opacity near 0): a reveal that never finishes?`);

  if (m.words > limits.wordsFail) addIssue(s, 'fails', 'density', `${m.words} words: more than ${limits.wordsFail}. Split it, or move the detail into the notes (aim for under ${limits.wordsWarn})`);
  else if (m.words > limits.wordsWarn) addIssue(s, 'warns', 'density', `${m.words} words: more than ${limits.wordsWarn}. Can the room take it in while you talk?`);
  if (m.bullets > LIMITS.bulletsWarn) addIssue(s, 'warns', 'density', `${m.bullets} bullets: more than ${LIMITS.bulletsWarn}. Show it as a diagram, or split it`);

  if (cfg.notes && !m.notes.present) addIssue(s, 'fails', 'notes', 'no speaker notes: add <aside class="notes"> with what you say here');
  for (const b of m.images.broken) addIssue(s, 'fails', 'assets', `image doesn't load: ${b.src}`, [b]);
  if (m.images.noAlt.length) addIssue(s, 'warns', 'assets', `image without alt text (use alt="" if it's decorative): ${m.images.noAlt.slice(0, 3).join(', ')}`);
  if (m.todos.length) addIssue(s, 'warns', 'stand-in', `stand-ins left for the speaker to fill: ${m.todos.slice(0, 4).map((t) => `"${t}"`).join(', ')}`);
  if (m.continuous.length) addIssue(s, 'infos', 'motion', `keeps moving (continuous animation): ${m.continuous.join('; ')}`);
  return s;
}

function layoutDiff(a, b, texts) {
  if (!a || !b) return null;
  if (a.length !== b.length) return `${a.length} text blocks at 1920x1080 but ${b.length} at 1024x768`;
  for (let i = 0; i < a.length; i++) {
    const [x1, y1, w1, h1, p1, m1] = a[i], [x2, y2, w2, h2, p2, m2] = b[i];
    if (m1 || m2) continue;
    const moved = Math.max(Math.abs(x1 - x2), Math.abs(y1 - y2), Math.abs(w1 - w2), Math.abs(h1 - h2));
    if (moved > 3 || Math.abs(p1 - p2) > 0.6) {
      const what = `"${snippet(texts[i], 30)}"`;
      return Math.abs(p1 - p2) > 0.6 ? `${what} is ${p1}px at 1920x1080 but ${p2}px at 1024x768` : `${what} moves ${Math.round(moved)}px`;
    }
  }
  return null;
}

// ---------------------------------------------------------------- sheet

const SHEET = { thumbW: 480, thumbH: 270, gap: 24, pad: 40, caption: 44, header: 64 };
/** Columns and pixel size of the contact sheet for `count` slides. */
export function sheetLayout(count) {
  const cols = count <= 4 ? Math.max(1, count) : count <= 16 ? 4 : 5;
  const rows = Math.ceil(count / cols);
  const { thumbW, thumbH, gap, pad, caption, header } = SHEET;
  return { cols, rows, width: pad * 2 + cols * thumbW + (cols - 1) * gap, height: pad * 2 + header + rows * (thumbH + caption) + (rows - 1) * gap };
}

async function cmdSheet(a) {
  const run = await loadRun(a.workspace);
  const { cfg, ws } = run;
  needDeck(cfg);
  const fp = fingerprint(cfg.deckDir);
  const check = loadJson(path.join(ws, 'check.json'), null);
  const marks = check && check.fingerprint === fp ? check : null;
  const out = path.resolve(a.out || path.join(ws, 'sheet.png'));
  let size;
  await withBrowser(cfg, async (browser) => {
    const { context, page } = await openDeck(browser, cfg);
    const count = await slideCount(page);
    const thumbs = [];
    for (let n = 1; n <= count; n++) {
      await page.evaluate((i) => window.__ksl.show(i, 'last'), n);
      const buf = await page.screenshot({ type: 'jpeg', quality: 82, animations: 'disabled' });
      const title = await page.evaluate((i) => { const s = window.__ksl.slides()[i - 1]; const h = s.querySelector('h1, h2, h3'); return (s.getAttribute('data-title') || (h ? h.textContent : '')).replace(/\s+/g, ' ').trim(); }, n);
      thumbs.push({ n, title, src: `data:image/jpeg;base64,${buf.toString('base64')}` });
    }
    const docTitle = await page.title();
    await context.close();
    size = sheetLayout(count);
    const S = SHEET;
    const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;');
    const flag = (n) => {
      const s = marks && marks.slides[n - 1];
      return s && s.fails.length ? [...new Set(s.fails.map((f) => f.check))].join(', ') : '';
    };
    const html = `<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;background:#1c1c1c;color:#e6e6e6;font:20px/1.3 system-ui,sans-serif;width:${size.width}px;height:${size.height}px;overflow:hidden}
.wrap{padding:${S.pad}px}
h1{margin:0;height:${S.header}px;font-size:26px;font-weight:600;display:flex;gap:24px;align-items:baseline}
h1 span{color:#9c9c9c;font-weight:400;font-size:20px}
.grid{display:grid;grid-template-columns:repeat(${size.cols},${S.thumbW}px);gap:${S.gap}px}
figure{margin:0;width:${S.thumbW}px}
img{display:block;width:${S.thumbW}px;height:${S.thumbH}px;outline:1px solid #444}
.bad img{outline:4px solid #ff6b5a}
figcaption{height:${S.caption}px;display:flex;gap:10px;align-items:center;white-space:nowrap;overflow:hidden}
b{font-variant-numeric:tabular-nums}i{font-style:normal;color:#ff8f80}
</style><div class="wrap"><h1>${esc(docTitle)} <span>${plural(count, 'slide')}${marks ? ` · check: ${marks.ok ? 'passes' : `${marks.summary.failingSlides.length} failing`}` : ''}</span></h1><div class="grid">`
      + thumbs.map((t) => `<figure class="${flag(t.n) ? 'bad' : ''}"><img src="${t.src}" alt=""><figcaption><b>${t.n}</b><span>${esc(snippet(t.title, 34))}</span>${flag(t.n) ? `<i>${esc(flag(t.n))}</i>` : ''}</figcaption></figure>`).join('')
      + '</div></div>';
    const sheet = await browser.newPage({ viewport: { width: size.width, height: size.height }, deviceScaleFactor: 1 });
    await sheet.setContent(html, { waitUntil: 'load' });
    await fs.mkdir(path.dirname(out), { recursive: true });
    await sheet.screenshot({ path: out });
    await sheet.close();
  }, run);
  cfg.sheet = { at: now(), path: out, width: size.width, height: size.height, fingerprint: fp };
  await run.save();
  console.log(`sheet: ${plural(size.cols, 'column')}, ${size.width}x${size.height} px -> ${out}`);
  if (!marks) console.log(check ? 'No failure marks: the deck changed since the last check.' : 'No failure marks: check hasn\'t run yet.');
  else if (marks.ok) console.log('Every slide passed the last check.');
  else console.log(`Slides that failed the last check are outlined in red: ${marks.summary.failingSlides.join(', ')}.`);
}

// ---------------------------------------------------------------- pdf

async function cmdPdf(a) {
  const run = await loadRun(a.workspace);
  const { cfg } = run;
  needDeck(cfg);
  const stem = path.basename(cfg.deck).replace(/\.html?$/i, '');
  const out = path.resolve(a.out || path.join(cfg.deckDir, `${stem}${a.notes ? '-notes' : ''}.pdf`));
  const want = a.notes ? { width: 1440, height: 1620 } : { width: 1440, height: 810 };
  let count = 0;
  await withBrowser(cfg, async (browser) => {
    const { context, page } = await openDeck(browser, cfg);
    count = await slideCount(page);
    await fs.mkdir(path.dirname(out), { recursive: true });
    if (!a.notes) {
      await page.evaluate(() => window.__ksl.showAllBuilds());
      await page.pdf({ path: out, preferCSSPageSize: true, printBackground: true, width: `${W}px`, height: `${H}px` });
    } else {
      // Notes pages: each slide's picture on top, what the speaker says underneath.
      const pages = [];
      for (let n = 1; n <= count; n++) {
        await page.evaluate((i) => window.__ksl.show(i, 'last'), n);
        const buf = await page.screenshot({ type: 'jpeg', quality: 90, animations: 'disabled' });
        const notes = await page.evaluate((i) => window.__ksl.notesHtml(i), n);
        pages.push({ n, src: `data:image/jpeg;base64,${buf.toString('base64')}`, notes });
      }
      const doc = await page.title();
      const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;');
      const html = `<!doctype html><meta charset="utf-8"><title>${esc(doc)}: notes</title><style>
@page{size:${W}px ${H * 2}px;margin:0}html,body{margin:0}
.p{width:${W}px;height:${H * 2}px;box-sizing:border-box;break-after:page;overflow:hidden;background:#fff;color:#1a1a1a}
.p:last-child{break-after:auto}.p img{display:block;width:${W}px;height:${H}px}
.n{box-sizing:border-box;height:${H}px;padding:56px 120px;font:34px/1.5 Georgia,"Times New Roman",serif}
.n h2{margin:0 0 24px;font:600 26px/1.2 system-ui,sans-serif;color:#666}.n p{margin:0 0 .7em;max-width:none}
</style>` + pages.map((p) => `<section class="p"><img src="${p.src}" alt=""><div class="n"><h2>${p.n} / ${count}</h2>${p.notes || '<p><em>No notes.</em></p>'}</div></section>`).join('');
      const np = await context.newPage();
      await np.setContent(html, { waitUntil: 'load' });
      await np.evaluate(() => {
        for (const n of document.querySelectorAll('.n')) {
          let size = 34;
          while (n.scrollHeight > n.clientHeight && size > 20) { size -= 2; n.style.fontSize = size + 'px'; }
        }
      });
      await np.pdf({ path: out, preferCSSPageSize: true, printBackground: true });
      await np.close();
    }
    await context.close();
  }, run);
  const info = pdfInfo(readFileSync(out));
  const wrongSize = info.boxes.filter((b) => Math.abs(b.width - want.width) > 1 || Math.abs(b.height - want.height) > 1);
  const ok = info.pages === count && !wrongSize.length;
  cfg.pdfs.push({ at: now(), path: out, notes: !!a.notes, pages: info.pages, slides: count, ok, fingerprint: fingerprint(cfg.deckDir) });
  await run.save();
  console.log(`pdf: ${out}`);
  console.log(`${plural(info.pages, 'page')} for ${plural(count, 'slide')}, ${info.boxes[0] ? `${info.boxes[0].width}x${info.boxes[0].height} pt` : 'no page size found'} (want ${want.width}x${want.height} pt${a.notes ? ': the slide on top, the notes under it' : ', which is 1920x1080 px'}).`);
  if (ok) { console.log('Every slide is on its own page.'); return 0; }
  if (info.pages !== count) console.log(`FAIL  the PDF has ${info.pages} pages but the deck has ${count} slides: a slide is taller than 1080px or the print CSS was changed (deck.css's @page and @media print rules).`);
  if (wrongSize.length) console.log(`FAIL  ${plural(wrongSize.length, 'page')} aren't ${want.width}x${want.height} pt: is @page { size: 1920px 1080px } still in deck.css?`);
  return 1;
}

// ---------------------------------------------------------------- the blind audience

const STOP = new Set('about after again also because been before being between both could does doing during each from have having here into just like many more most much must only other over same should some such than that their them then there these they this those through very what when where which while with would your yours will want people talk team teams room working work make makes made using used every'.split(' '));
const contentWords = (t) => new Set((String(t).toLowerCase().replace(/[’']s\b/g, '').match(/[\p{L}\p{N}]{4,}/gu) || []).filter((w) => !STOP.has(w)));
// "timetable" and "timetables", "night" and "nightly": the same word for this purpose.
const sameWord = (a, b) => a === b || ((a.startsWith(b) || b.startsWith(a)) && Math.abs(a.length - b.length) <= 3);

/** Refuse a room description that shares words with the key messages: it tells the audience what to listen for. */
function roomLeaks(room, messages, fix) {
  if (!room || !messages.length) return;
  const msg = messages.flatMap((m) => [...contentWords(m)]);
  const shared = [...contentWords(room)].filter((w) => msg.some((m) => sameWord(w, m)));
  if (shared.length) fail(`the room description shares words with the key messages (${shared.join(', ')}), which tells the audience what to listen for. Describe the people, not the talk: ${fix}`);
}

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
function fill(tpl, values) {
  const out = tpl.replace(/<([a-z][a-z ]*[a-z])>/g, (m, k) => (k in values ? values[k] : m));
  const left = out.match(/<(slides folder|scratch folder|count|last|kind|seat|room line|messages|argument|remember|unclear)>/);
  if (left) fail(`internal: the brief still has ${left[0]}`);
  return out;
}
const newBox = async () => {
  const box = path.join(sandboxesHome(), randomBytes(5).toString('hex'));
  await fs.mkdir(path.join(box, 'scratch'), { recursive: true });
  return box;
};
const rmBox = async (box) => { if (box && box.startsWith(sandboxesHome())) await fs.rm(box, { recursive: true, force: true }).catch(() => {}); };

async function cmdAudience(a) {
  const run = await loadRun(a.workspace);
  const { cfg, ws } = run;
  needDeck(cfg);
  if (a.room !== undefined) cfg.room = a.room.trim();
  roomLeaks(cfg.room, cfg.messages, 'audience -w <run> --room "<who they are>"');
  const sb = sandboxesHome();
  for (const p of [ws, cfg.deckDir]) {
    const rel = path.relative(canonical(p), canonical(sb));
    if (!rel.startsWith('..') && !path.isAbsolute(rel)) fail(`the audience folders (${sb}) would sit inside ${p}, where the audience could find the deck and its notes. Point KILLER_SLIDES_SANDBOXES somewhere else.`);
  }
  for (const r of cfg.rounds) if (r.status !== 'done') { r.status = 'abandoned'; await rmBox(r.box); await rmBox(r.matchBox); }
  const round = cfg.rounds.length + 1;
  const box = await newBox();
  const slidesDir = path.join(box, 'slides');
  await fs.mkdir(slidesDir, { recursive: true });
  let count = 0;
  await withBrowser(cfg, async (browser) => {
    const { context, page } = await openDeck(browser, cfg, AUDIENCE_SIZE);
    count = await slideCount(page);
    for (let n = 1; n <= count; n++) {
      await page.evaluate((i) => window.__ksl.show(i, 'last'), n);
      await page.screenshot({ path: path.join(slidesDir, `${pad2(n)}.png`), animations: 'disabled' });
    }
    await context.close();
  }, run);
  const talk = !cfg.talk.read;
  const brief = fill(template('Audience brief'), {
    kind: talk ? 'talk' : 'deck that people read on their own, with no speaker',
    'room line': cfg.room ? `You're one of the people it was made for: ${cfg.room}.` : 'Nobody has told you anything about it beforehand.',
    count: String(count), 'slides folder': slash(slidesDir), last: pad2(count),
    seat: talk ? "The speaker's voice isn't in these images, so all that reaches you is what's on the slides." : 'There is no speaker: the slides are all there is.',
    'scratch folder': slash(path.join(box, 'scratch')),
  });
  for (const leak of [ws, cfg.deckDir, cfg.deck]) {
    if (brief.toLowerCase().includes(slash(leak).toLowerCase())) fail(`internal: the audience brief mentions ${leak}`);
  }
  const rdir = path.join(ws, 'audience', `round-${round}`);
  await fs.mkdir(rdir, { recursive: true });
  await fs.writeFile(path.join(rdir, 'brief.md'), brief, 'utf8');
  cfg.rounds.push({ n: round, at: now(), status: 'open', box, matchBox: null, slides: count, fingerprint: fingerprint(cfg.deckDir), git: gitStatus(cfg.git.top, cfg.deckDir) });
  await run.save();
  console.log(`Audience round ${round}: ${plural(count, 'slide image')} at ${AUDIENCE_SIZE.width}x${AUDIENCE_SIZE.height} in ${slidesDir}`);
  console.log(`Brief: ${path.join(rdir, 'brief.md')}`);
  console.log('Send it, exactly as written, to one fresh agent (general-purpose, never fork). It writes reply.json in');
  console.log(`  ${path.join(box, 'scratch')}`);
  console.log(`then run: node deck.mjs heard -w "${ws}"`);
}

function openRound(cfg, want) {
  const r = cfg.rounds[cfg.rounds.length - 1];
  if (!r || r.status === 'done' || r.status === 'abandoned') fail('no audience round is open: run audience first');
  if (want && r.status !== want) fail(want === 'open' ? `round ${r.n} already has the audience's reply; run landed next, or audience again for a new round` : `round ${r.n} has no audience reply yet: run heard first`);
  return r;
}

function readReply(file) {
  if (!existsSync(file)) return { error: `no reply at ${file}` };
  const text = readFileSync(file, 'utf8').replace(/^﻿/, '');
  try { return { data: JSON.parse(text) }; } catch { /* a saved message: take its last JSON block */ }
  const blocks = [...text.matchAll(/```(?:json)?[ \t]*\n([\s\S]*?)```/g)].map((m) => m[1]).reverse();
  for (const b of blocks) { try { return { data: JSON.parse(b) }; } catch { /* try the next */ } }
  return { error: `${file} has no JSON that parses` };
}

async function cmdHeard(a) {
  const run = await loadRun(a.workspace);
  const { cfg, ws } = run;
  const r = openRound(cfg, 'open');
  const file = a.reply ? path.resolve(a.reply) : path.join(r.box, 'scratch', 'reply.json');
  const got = readReply(file);
  if (got.error) fail(`${got.error}. Send the same brief to a fresh agent, then run heard again (or pass --reply <file> with its message saved).`);
  const d = got.data;
  const problems = [];
  if (!d || typeof d !== 'object' || Array.isArray(d)) problems.push('the reply is not a JSON object');
  else {
    if (typeof d.argument !== 'string' || !d.argument.trim()) problems.push('"argument" must be a sentence or two');
    if (!Array.isArray(d.remember) || !d.remember.length || d.remember.some((x) => typeof x !== 'string' || !x.trim())) problems.push('"remember" must be a list of what they would remember');
    if (d.unclear !== undefined && (!Array.isArray(d.unclear) || d.unclear.some((u) => !u || typeof u !== 'object' || !Number.isInteger(u.slide) || u.slide < 1 || u.slide > r.slides || typeof u.problem !== 'string'))) problems.push(`"unclear" must be a list of {"slide": 1-${r.slides}, "problem": "..."}`);
  }
  if (problems.length) fail(`the audience's reply doesn't fit the brief: ${problems.join('; ')}. Send the same brief to a fresh agent and run heard again.`);
  const answer = { argument: d.argument.trim(), remember: d.remember.map((x) => x.trim()).slice(0, 5), unclear: (d.unclear || []).map((u) => ({ slide: u.slide, problem: u.problem.trim() })),
    best: d.best && typeof d.best === 'object' ? d.best : null, notes: typeof d.notes === 'string' ? d.notes : '' };
  r.audience = answer;
  r.heardAt = now();
  const rdir = path.join(ws, 'audience', `round-${r.n}`);
  await saveJson(path.join(rdir, 'audience.json'), answer);
  const fpNow = fingerprint(cfg.deckDir), gitNow = gitStatus(cfg.git.top, cfg.deckDir);
  r.changedDuring = fpNow !== r.fingerprint || gitNow !== r.git;
  console.log(`Round ${r.n}, what the audience took away:`);
  console.log(`  Argues: ${answer.argument}`);
  answer.remember.forEach((x, i) => console.log(`  ${i + 1}. ${x}`));
  for (const u of answer.unclear) console.log(`  Couldn't read or follow slide ${u.slide}: ${u.problem}`);
  if (r.changedDuring) console.log('WARN  the deck folder changed while the audience agent had it. If you didn\'t change it, restore it before going on.');
  if (!cfg.messages.length) {
    r.status = 'done';
    r.landed = null;
    r.total = 0;
    await rmBox(r.box);
    await run.save();
    console.log('\nNo key messages were set, so there is nothing to match: read the answer above against what the talk is for.');
    return 0;
  }
  const quote = (t) => String(t).replace(/\s*\n\s*/g, ' ');
  const mbox = await newBox();
  const brief = fill(template('Matcher brief'), {
    messages: cfg.messages.map((m, i) => `${i + 1}. ${m}`).join('\n'),
    argument: quote(answer.argument),
    remember: answer.remember.map((x, i) => `> ${i + 1}. ${quote(x)}`).join('\n'),
    unclear: answer.unclear.length ? answer.unclear.map((u) => `> - Slide ${u.slide}: ${quote(u.problem)}`).join('\n') : '> Nothing.',
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
  console.log(`then run: node deck.mjs landed -w "${ws}"`);
  return 0;
}

const norm = (t) => String(t || '').toLowerCase().replace(/[‘’`´]/g, "'").replace(/[“”]/g, '"').replace(/[–—‐-]/g, '-').replace(/\s+/g, ' ').replace(/^[\s"'.,;:!?-]+|[\s"'.,;:!?-]+$/g, '').trim();

async function cmdLanded(a) {
  const run = await loadRun(a.workspace);
  const { cfg, ws } = run;
  const r = openRound(cfg, 'heard');
  const file = a.reply ? path.resolve(a.reply) : path.join(r.matchBox, 'scratch', 'reply.json');
  const got = readReply(file);
  if (got.error) fail(`${got.error}. Send the same matcher brief to a fresh agent, then run landed again.`);
  const d = got.data;
  const list = d && Array.isArray(d.messages) ? d.messages : null;
  const problems = [];
  if (!list) problems.push('"messages" must be a list');
  else {
    const ids = list.map((x) => x && x.id);
    for (let i = 1; i <= cfg.messages.length; i++) if (ids.filter((x) => x === i).length !== 1) problems.push(`message ${i} must appear exactly once`);
    for (const x of list) if (!x || !['landed', 'partly', 'missed'].includes(x.verdict)) problems.push(`message ${x && x.id}: verdict must be landed, partly or missed`);
  }
  if (problems.length) fail(`the matcher's reply doesn't fit the brief: ${problems.join('; ')}. Send the same brief to a fresh agent and run landed again.`);
  const said = norm([r.audience.argument, ...r.audience.remember, ...r.audience.unclear.map((u) => u.problem)].join(' \n '));
  const verdicts = cfg.messages.map((message, i) => {
    const x = list.find((y) => y.id === i + 1);
    let verdict = x.verdict, note = '';
    const q = typeof x.quote === 'string' ? x.quote : '';
    if (verdict !== 'missed' && (!norm(q) || !said.includes(norm(q)))) {
      note = norm(q) ? `said ${verdict}, but its quote isn't in the audience's answer` : `said ${verdict}, with no quote`;
      verdict = 'missed';
    }
    return { id: i + 1, message, verdict, said: x.verdict, quote: q || null, why: typeof x.why === 'string' ? x.why : '', note };
  });
  r.verdicts = verdicts;
  r.extra = Array.isArray(d.extra) ? d.extra.filter((e) => typeof e === 'string').slice(0, 5) : [];
  r.landed = verdicts.filter((v) => v.verdict === 'landed').length;
  r.total = verdicts.length;
  r.status = 'done';
  r.doneAt = now();
  await saveJson(path.join(ws, 'audience', `round-${r.n}`, 'verdicts.json'), { verdicts, extra: r.extra });
  await rmBox(r.matchBox);
  await run.save();
  console.log(`Round ${r.n}: ${r.landed} of ${plural(r.total, 'key message')} landed with a blind audience.`);
  for (const v of verdicts) {
    console.log(`  ${v.verdict.toUpperCase().padEnd(6)} ${v.id}. ${v.message}`);
    if (v.quote && v.verdict !== 'missed') console.log(`         they wrote: “${v.quote}”`);
    if (v.note) console.log(`         (${v.note})`);
  }
  for (const e of r.extra) console.log(`  Also took away: ${e}`);
  for (const u of r.audience.unclear) console.log(`  Couldn't read or follow slide ${u.slide}: ${u.problem}`);
  if (r.landed === r.total) { console.log('Every key message landed.'); return 0; }
  console.log('Fix the deck where a message missed (the slides, never the brief), then run audience again for a new round with a fresh agent.');
  return 1;
}

// ---------------------------------------------------------------- land (commit the deck on a local branch)

// Attribution, not mentions: a talk about an AI tool can name it, but nothing may say who made the deck.
const ATTRIBUTION = /co-authored-by|generated (with|by)|\b(written|made|created|built|drafted|designed) (with|by|using) (claude|chatgpt|gpt|copilot|gemini|an? ai\b|ai\b)|\bai[- ](generated|assisted|written)\b|\u{1F916}/iu;
const ATTRIBUTION_DIFF = /co-authored-by:|generated (with|by) (claude|chatgpt|gpt|copilot|gemini|an? ai\b|ai\b)|\b(made|created|built|designed) (with|by|using) (claude|chatgpt|an? ai\b)|<meta[^>]+name=["']generator["'][^>]*(claude|gpt|\bai\b)|\u{1F916}/iu;

async function cmdLand(a) {
  const run = await loadRun(a.workspace);
  const { cfg } = run;
  const message = (a.message || []).join('\n\n').trim();
  if (!message) fail('land needs -m "<commit message>": what changed and why, in plain words');
  if (ATTRIBUTION.test(message)) fail('the commit message mentions an AI or a co-author. Commits must read as the user\'s own work: say what changed and why, nothing else.');
  const top = gitTop(cfg.deckDir);
  if (!top) { console.log(`${cfg.deckDir} isn't in a git repository, so there is nothing to commit. The deck is in place as it is.`); return 0; }
  const deckDir = canonical(cfg.deckDir);
  const inDeck = (f) => { const r = path.relative(deckDir, path.join(top, f)); return !r.startsWith('..') && !path.isAbsolute(r); };
  const before = git(top, ['diff', '--cached', '--name-only']).out.split('\n').filter(Boolean).filter((f) => !inDeck(f));
  if (before.length) fail(`other files are already staged (${before.slice(0, 5).join(', ')}): land commits only the deck folder. Unstage them first; nothing was changed.`);
  const current = git(top, ['symbolic-ref', '-q', '--short', 'HEAD']).out.trim() || null;
  let target = a.branch || cfg.branch;
  if (!target) {
    target = `killer-slides/${today()}`;
    for (let i = 2; git(top, ['rev-parse', '--verify', '-q', `refs/heads/${target}`]).ok; i++) target = `killer-slides/${today()}-${i}`;
  }
  // A branch named with --branch, here or at init, is where the user (or killer-goal) wants it, even the starting one.
  if (!a.branch && !(cfg.branchNamed && target === cfg.branch) && target === cfg.git.startBranch) fail(`refusing to commit on ${target}, the branch you started on. Pass --branch ${target} if that's really where this goes.`);
  if (current !== target) {
    const exists = git(top, ['rev-parse', '--verify', '-q', `refs/heads/${target}`]).ok;
    const sw = git(top, exists ? ['switch', target] : ['switch', '-c', target]);
    if (!sw.ok) fail(`couldn't switch to ${target}: ${sw.err.trim()}`);
  }
  const rel = path.relative(top, deckDir) || '.';
  const add = git(top, ['add', '-A', '--', rel]);
  if (!add.ok) fail(`git add failed: ${add.err.trim()}`);
  const staged = git(top, ['diff', '--cached', '--name-only']).out.split('\n').filter(Boolean);
  if (!staged.length) { cfg.branch = target; await run.save(); console.log(`Nothing new to commit in ${rel} (on ${target}).`); return 0; }
  const diff = git(top, ['diff', '--cached', '--text', '-U0', '--', rel], { maxBuffer: 256 * 1024 * 1024 }).out;
  const hit = diff.split('\n').find((l) => l.startsWith('+') && ATTRIBUTION_DIFF.test(l));
  if (hit) { git(top, ['reset', '-q', '--', rel]); fail(`the deck says who or what generated it ("${snippet(hit.slice(1), 70)}"). Remove that line; the deck must read as the user's own work.`); }
  const c = git(top, ['commit', '-q', '-m', message]);
  if (!c.ok) fail(`git commit failed: ${(c.err || c.out).trim()}`);
  const sha = git(top, ['rev-parse', '--short', 'HEAD']).out.trim();
  const subject = message.split('\n')[0];
  cfg.branch = target;
  cfg.commits.push(`${sha} ${subject}`);
  await run.save();
  console.log(`Committed ${plural(staged.length, 'file')} from ${rel} on ${target}: ${sha} ${subject}`);
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
  const fp = existsSync(cfg.deckDir) ? fingerprint(cfg.deckDir) : null;
  const check = loadJson(path.join(ws, 'check.json'), null);
  const last = cfg.checks[cfg.checks.length - 1] || null;
  const current = !!(check && check.fingerprint === fp);
  const pdf = [...cfg.pdfs].reverse().find((p) => !p.notes) || null;
  const notesPdf = [...cfg.pdfs].reverse().find((p) => p.notes) || null;
  const rounds = cfg.rounds.filter((r) => r.status === 'done');
  const lastRound = rounds[rounds.length - 1] || null;
  const slides = check ? check.summary.slides : 0;

  const checkOk = !!(check && check.ok && current);
  const pdfOk = !cfg.pdf || !!(pdf && pdf.ok && pdf.fingerprint === fp);
  const audienceOk = !cfg.blind || !cfg.messages.length ? true : !!(lastRound && lastRound.total && lastRound.landed === lastRound.total && lastRound.fingerprint === fp);
  let status = checkOk && pdfOk && audienceOk ? 'done' : 'partial';
  if (!check && cfg.blocked) status = 'blocked';

  const failing = check ? check.slides.filter((s) => s.fails.length) : [];
  const failText = failing.slice(0, 3).map((s) => `${[...new Set(s.fails.map((f) => f.check))].join(' and ')} on ${s.n}`).join('; ');
  let headline;
  if (status === 'blocked') headline = `blocked: ${cfg.blocked}`;
  else if (!check) headline = 'no check has run yet';
  else if (!check.ok) headline = `${plural(slides, 'slide')}; check fails on ${plural(failing.length, 'slide')} (${failText})` + (check.deckIssues.fails.length ? ` and ${plural(check.deckIssues.fails.length, 'deck-wide failure')}` : '');
  else {
    headline = `${plural(slides, 'slide')}, every check passes${current ? '' : ' (on an earlier version: run check again)'}`;
    if (cfg.blind && cfg.messages.length) {
      headline += lastRound ? `; a blind audience got ${lastRound.landed} of ${lastRound.total} key messages (round ${lastRound.n})` : '; the blind audience hasn\'t seen it yet';
    }
  }

  const md = [];
  md.push(`# Slides report: ${check?.title || path.basename(cfg.deckDir)}`, '');
  md.push(`**Result:** ${headline}.`, '');
  md.push(`**Deck:** \`${cfg.deck}\`${slides ? ` (${plural(slides, 'slide')})` : ''}. Open it in a browser to present.`);
  md.push(`**PDF:** ${pdf ? `\`${pdf.path}\`, ${plural(pdf.pages, 'page')}${pdf.ok ? ', one slide each' : ', page count or size wrong'}${pdf.fingerprint === fp ? '' : ' (from an earlier version of the deck)'}` : cfg.pdf ? 'not exported yet' : 'not wanted'}${notesPdf ? `. Notes pages: \`${notesPdf.path}\`` : ''}`);
  md.push(`**Contact sheet:** ${cfg.sheet ? `\`${cfg.sheet.path}\`${cfg.sheet.fingerprint === fp ? '' : ' (from an earlier version)'}` : 'not made'}`);
  md.push(`**Talk:** ${cfg.talk.read ? 'a deck people read on their own' : `${cfg.talk.minutes} minutes`}${check?.timing?.notesMinutes ? `; the notes run about ${check.timing.notesMinutes} minutes` : ''}`, '');

  md.push('## Check', '');
  if (!check) md.push('Check hasn\'t run.', '');
  else {
    md.push(`${plural(cfg.checks.length, 'run')}; the last on ${check.generatedAt}, ${current ? 'on the deck as it is now' : '**on an earlier version of the deck: run check again**'}. ${check.summary.fails} failing, ${plural(check.summary.warns, 'warning')}.`, '');
    md.push('| Slide | Title | Words | Builds | Notes | Result |', '|---|---|---|---|---|---|');
    for (const s of check.slides) md.push(`| ${s.n} | ${snippet(s.title, 40).replace(/\|/g, '/')} | ${s.words} | ${s.steps} | ${s.notes ? 'yes' : 'no'} | ${s.fails.length ? 'FAIL: ' + [...new Set(s.fails.map((f) => f.check))].join(', ') : s.warns.length ? 'warn: ' + [...new Set(s.warns.map((f) => f.check))].join(', ') : 'pass'} |`);
    md.push('');
    const lines = [...check.slides.flatMap((s) => [...s.fails.map((f) => `- FAIL slide ${s.n}: ${f.message}`), ...s.warns.map((f) => `- WARN slide ${s.n}: ${f.message}`)]),
      ...check.deckIssues.fails.map((f) => `- FAIL: ${f.message}`), ...check.deckIssues.warns.map((f) => `- WARN: ${f.message}`), ...check.deckIssues.infos.map((f) => `- ${f.message}`)];
    if (lines.length) md.push(...lines, '');
  }

  md.push('## Blind audience', '');
  if (!cfg.blind) md.push('Not run: the user chose no blind check.', '');
  else if (!rounds.length) md.push('No round finished.', '');
  else {
    if (cfg.messages.length) md.push('Key messages:', ...cfg.messages.map((m, i) => `${i + 1}. ${m}`), '');
    for (const r of rounds) {
      md.push(`**Round ${r.n}**${r.total ? `: ${r.landed} of ${r.total} landed` : ''}${r.changedDuring ? ' (the deck changed while the audience had it)' : ''}`, '');
      md.push(`- They said it argues: ${r.audience.argument}`);
      md.push("- They'd remember:", ...r.audience.remember.map((x) => `  - ${x}`));
      if (r.audience.unclear.length) md.push("- Couldn't read or follow:", ...r.audience.unclear.map((u) => `  - Slide ${u.slide}: ${u.problem}`));
      if (r.verdicts) md.push('- The key messages:', ...r.verdicts.map((v) => `  - ${v.id}: ${v.verdict}${v.quote && v.verdict !== 'missed' ? `, from “${v.quote}”` : ''}${v.note ? ` (${v.note})` : ''}${v.why && v.verdict !== 'landed' ? `. ${v.why}` : ''}`));
      md.push('');
    }
  }

  md.push('## How to present', '', 'Open `deck.html` in Chrome, Edge, Firefox or Safari. Nothing loads from the network.', '',
    '| Key | Does |', '|---|---|',
    '| Right, down, space, Page Down, or a click on the right half | Next build or slide |',
    '| Left, up, Shift+space, Page Up, or a click on the left half | Back |',
    '| Home, End | First and last slide |', '| f | Full screen |',
    '| p | Presenter view in a second window: this slide, the next, the notes and a timer. Both windows follow each other |',
    '| o | Overview of every slide; click one to go there |', '| b | Black screen, and back |',
    '| `#/5` at the end of the address | Opens on slide 5 |', '');
  if (cfg.decisions.length) md.push('## Decisions to check', '', ...cfg.decisions.map((d) => `- ${d}`), '');
  if (cfg.waiting.length) md.push('## Waiting for your OK', '', ...cfg.waiting.map((w) => `- ${w.text}: \`${w.command}\``), '');
  md.push('## Run details', '', `Run folder: \`${ws}\``);
  if (cfg.branch || cfg.commits.length) md.push(`Branch: \`${cfg.branch}\`${cfg.commits.length ? `; commits: ${cfg.commits.map((c) => `\`${c}\``).join(', ')}` : ''}. Nothing was pushed.`);
  md.push('');
  await fs.writeFile(path.join(ws, 'report.md'), md.join('\n'), 'utf8');

  const summary = {
    skill: 'killer-slides', status, headline, report: path.join(ws, 'report.md'),
    branch: cfg.branch || null, commits: cfg.commits, decisions: cfg.decisions,
    waiting: cfg.waiting.map((w) => `${w.text}: ${w.command}`),
    numbers: {
      slides, check_runs: cfg.checks.length, check_fails: check ? check.summary.fails : null, check_warnings: check ? check.summary.warns : null,
      check_current: current, pdf_pages: pdf ? pdf.pages : null, audience_rounds: rounds.length,
      messages_landed: lastRound ? lastRound.landed : null, messages_total: cfg.messages.length,
      talk_minutes: cfg.talk.read ? null : cfg.talk.minutes, notes_minutes: check?.timing?.notesMinutes ?? null,
    },
  };
  await saveJson(path.join(ws, 'summary.json'), summary);
  console.log(`report: ${path.join(ws, 'report.md')}`);
  console.log(`summary: ${path.join(ws, 'summary.json')}`);
  console.log(`killer-slides: ${status} · ${headline} · ${path.join(ws, 'report.md')}`);
}

// ---------------------------------------------------------------- main

const COMMANDS = { new: cmdNew, init: cmdInit, shoot: cmdShoot, check: cmdCheck, sheet: cmdSheet, pdf: cmdPdf,
  audience: cmdAudience, heard: cmdHeard, landed: cmdLanded, land: cmdLand, decide: cmdDecide, wait: cmdWait, report: cmdReport };

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

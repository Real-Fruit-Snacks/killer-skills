#!/usr/bin/env node
/**
 * render.mjs - see a README the way GitHub shows it, so you can look before you ship.
 *
 *   node render.mjs [README.md | repo-dir]          render a local README with GitHub's own renderer
 *   node render.mjs --url https://github.com/...     screenshot a live GitHub page
 *
 * Options
 *   --out <dir>      where screenshots and report.json go
 *                    (default: <os tmp>/killer-repo-shots/<label>)
 *   --label <name>   filename prefix and folder name, e.g. "before" and "after" (default: the folder name)
 *   --repo <o/r>     repo used to resolve #123 and @user references (default: from gh or the origin remote)
 *   --only <list>    variants, from desktop-light,desktop-dark,phone-light,phone-dark (default: all four)
 *   --wait <ms>      extra settle time after load (default 600). Animated images are captured at
 *                    that moment, so raise it (e.g. --wait 4000) to see a later frame.
 *   --offline        skip GitHub's Markdown API and make the approximate render straight away
 *
 * Local renders send the README to GitHub's Markdown API (through `gh` when it's
 * logged in), style it with github-markdown-css (vendor/), and load images from your
 * working tree, so uncommitted changes show. Mermaid diagrams are drawn with Mermaid
 * from the killer-skills runtime when it's installed, otherwise from jsDelivr, because
 * github.com draws them with its own JavaScript.
 *
 * When GitHub can't be reached (offline, rate-limited, gh logged out with no network),
 * the README is rendered with the vendored marked instead, with GitHub's main rules
 * applied: alerts, task lists, linked images, heading anchors, and stripped styles and
 * scripts. That render is close but not exact (no footnotes, math, emoji shortcodes or
 * #123 / @user links), so every screenshot carries an "approximate render" label and the
 * report says so.
 *
 * Desktop approximates the README column on a repo's home page at 1280px; phone is
 * 390px wide. Each variant gets a first-screen shot and a full-page shot.
 */
import fs from 'node:fs/promises';
import { existsSync, statSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { loadPlaywright, launch, serve, run, repoRoot, repoSlug, findReadme, RUNTIME, VENDOR } from './lib.mjs';

const VARIANTS = {
  'desktop-light': { layout: 'desktop', width: 1280, height: 900, scheme: 'light' },
  'desktop-dark': { layout: 'desktop', width: 1280, height: 900, scheme: 'dark' },
  'phone-light': { layout: 'phone', width: 390, height: 844, scheme: 'light' },
  'phone-dark': { layout: 'phone', width: 390, height: 844, scheme: 'dark' },
};
const LAYOUT = { desktop: { col: '900px', pad: '32px' }, phone: { col: '100%', pad: '16px' } };

// ---------- args ----------
const argv = process.argv.slice(2);
if (argv.includes('-h') || argv.includes('--help')) {
  console.log('usage: node render.mjs [README.md | dir] | --url <url>  [--out dir] [--label name] [--repo owner/name] [--only desktop-light,...] [--wait 600] [--offline]');
  process.exit(0);
}
const opt = { wait: 600, only: Object.keys(VARIANTS) };
let target;
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--out') opt.out = argv[++i];
  else if (a === '--label') opt.label = argv[++i];
  else if (a === '--repo') opt.repo = argv[++i];
  else if (a === '--url') opt.url = argv[++i];
  else if (a === '--only') opt.only = argv[++i].split(',').map((s) => s.trim()).filter(Boolean);
  else if (a === '--wait') opt.wait = Number(argv[++i]);
  else if (a === '--offline') opt.offline = true;
  else if (!target) target = a;
  else { console.error(`unknown argument: ${a}`); process.exit(1); }
}
for (const v of opt.only) if (!VARIANTS[v]) { console.error(`unknown variant: ${v}`); process.exit(1); }
const live = Boolean(opt.url);

// ---------- local README -> HTML ----------
async function renderWithGitHub(file, slug) {
  const args = ['api', 'markdown', '-F', `text=@${file}`, '-f', 'mode=gfm'];
  if (slug) args.push('-f', `context=${slug}`);
  const viaGh = run('gh', args, { maxBuffer: 64 * 1024 * 1024 });
  if (viaGh) return viaGh;
  const text = await fs.readFile(file, 'utf8');
  let res;
  try {
    res = await fetch('https://api.github.com/markdown', {
      method: 'POST',
      headers: { accept: 'application/vnd.github+json', 'content-type': 'application/json', 'user-agent': 'killer-repo' },
      body: JSON.stringify({ text, mode: 'gfm', ...(slug ? { context: slug } : {}) }),
      signal: AbortSignal.timeout(20000),
    });
  } catch (e) {
    throw new Error(`couldn't reach GitHub's Markdown API (${e.cause?.code || e.name || e.message})`);
  }
  if (!res.ok) throw new Error(`GitHub's Markdown API answered ${res.status} (log in with \`gh auth login\` to avoid rate limits)`);
  return res.text();
}

// The offline stand-in: the vendored marked, with GitHub's rules applied in the page (see githubRules).
async function renderApproximate(file) {
  const { marked } = await import(pathToFileURL(path.join(VENDOR, 'marked.esm.js')).href);
  const html = marked.parse(await fs.readFile(file, 'utf8'), { gfm: true, async: false });
  return html.replace(/<pre><code class="language-mermaid">([\s\S]*?)<\/code><\/pre>/g, (_, src) => `<pre class="mermaid">${src}</pre>`);
}

// github.com fills these sections in with its own JavaScript; stand in for them.
function enrich(html) {
  return html
    .replace(/<section class="js-render-needs-enrichment[^"]*"[^>]*data-type="mermaid"[\s\S]*?data-plain="([^"]*)"[\s\S]*?<\/section>/g,
      (_, src) => `<pre class="mermaid">${src}</pre>`)
    .replace(/<section class="js-render-needs-enrichment[^"]*"[^>]*data-type="([a-z]+)"[\s\S]*?<\/section>/g,
      (_, type) => `<div class="kr-note">A ${type} view renders here on github.com.</div>`);
}

// What GitHub does to rendered Markdown that marked doesn't. Runs in the page, for the approximate render only.
function githubRules() {
  const root = document.querySelector('.markdown-body');
  root.querySelectorAll('script, style, iframe, form, object, embed, video, link, meta, base').forEach((el) => el.remove());
  for (const el of root.querySelectorAll('*')) {
    for (const a of [...el.attributes]) if (a.name === 'style' || a.name.startsWith('on')) el.removeAttribute(a.name);
  }
  const seen = new Map();
  for (const h of root.querySelectorAll('h1, h2, h3, h4, h5, h6')) {
    let slug = h.textContent.trim().toLowerCase().replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, '').replace(/ /g, '-');
    const n = seen.get(slug) || 0;
    seen.set(slug, n + 1);
    if (n) slug += `-${n}`;
    h.id = slug;
  }
  const titles = { NOTE: 'Note', TIP: 'Tip', IMPORTANT: 'Important', WARNING: 'Warning', CAUTION: 'Caution' };
  for (const q of root.querySelectorAll('blockquote')) {
    const p = q.firstElementChild;
    const m = p && p.tagName === 'P' && p.innerHTML.match(/^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(<br>\s*)?/i);
    if (!m) continue;
    const type = m[1].toUpperCase();
    p.innerHTML = p.innerHTML.slice(m[0].length);
    if (!p.innerHTML.trim()) p.remove();
    const div = document.createElement('div');
    div.className = `markdown-alert markdown-alert-${type.toLowerCase()}`;
    const t = document.createElement('p');
    t.className = 'markdown-alert-title';
    t.textContent = titles[type];
    div.append(t, ...q.childNodes);
    q.replaceWith(div);
  }
  for (const img of root.querySelectorAll('img')) {
    if (img.closest('a')) continue;
    const a = document.createElement('a');
    a.href = img.getAttribute('src') || '';
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    img.replaceWith(a);
    a.append(img);
  }
  for (const c of root.querySelectorAll('li > input[type="checkbox"]')) {
    c.disabled = true;
    c.classList.add('task-list-item-checkbox');
    c.parentElement.classList.add('task-list-item');
    c.closest('ul, ol')?.classList.add('contains-task-list');
  }
}

function page(body, baseHref, css, layout, { approximate = false, mermaidSrc = null } = {}) {
  const { col, pad } = LAYOUT[layout];
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<base href="${baseHref}">
<style>${css}</style>
<style>
  html, body { margin: 0; background: #ffffff; }
  @media (prefers-color-scheme: dark) { html, body { background: #0d1117; } }
  .markdown-body { box-sizing: border-box; width: ${col}; max-width: 100%; margin: 0 auto; padding: ${pad}; }
  .kr-note { border: 1px dashed #8c959f; border-radius: 6px; padding: 12px; color: #8c959f; }
  .kr-approx { position: fixed; right: 8px; bottom: 8px; z-index: 9; font: 600 12px/1.4 system-ui, sans-serif; padding: 4px 8px;
    border-radius: 4px; background: #fff8c5; color: #3b2300; border: 1px solid #d4a72c; }
  @media (prefers-color-scheme: dark) { .kr-approx { background: #3b2300; color: #f8e3a1; border-color: #9e6a03; } }
</style></head>
<body${approximate ? ' data-approximate="1"' : ''}><article class="markdown-body">${body}</article>
${approximate ? `<div class="kr-approx">approximate render: GitHub's renderer was not used</div>` : ''}
<script type="module">
  ${approximate ? `(${githubRules.toString()})();` : ''}
  const blocks = document.querySelectorAll('pre.mermaid');
  if (blocks.length) {
    try {
      const local = ${JSON.stringify(mermaidSrc)};
      let mermaid;
      if (local) {
        await new Promise((ok, no) => {
          const s = document.createElement('script');
          s.src = local;
          s.onload = ok;
          s.onerror = () => no(new Error('could not load ' + local));
          document.head.append(s);
        });
        mermaid = window.mermaid;
      } else ({ default: mermaid } = await import('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs'));
      mermaid.initialize({ startOnLoad: false, theme: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'default' });
      await mermaid.run({ nodes: blocks });
    } catch (e) { document.body.dataset.mermaidError = String((e && e.message) || e); }
  }
  document.body.dataset.ready = '1';
</script></body></html>`;
}

// ---------- in-page inspection ----------
function inspect({ vh, live }) {
  const root = live ? document.body : document.querySelector('.markdown-body');
  const name = (src) => { try { return decodeURIComponent(new URL(src).pathname.split('/').pop()) || src; } catch { return src; } };
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const onFirst = (el) => { const r = el.getBoundingClientRect(); return shown(el) && r.top < vh && r.bottom > 0; };
  const imgs = [...root.querySelectorAll('img')];
  const isBadge = (i) => i.getBoundingClientRect().height <= 32 && i.getBoundingClientRect().width <= 240;
  const visuals = [...imgs.filter((i) => !isBadge(i)), ...root.querySelectorAll('pre.mermaid svg, video, canvas')];
  const words = (() => {
    let n = 0;
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (w.nextNode()) {
      const el = w.currentNode.parentElement;
      if (el && onFirst(el) && !el.closest('pre, svg, script, style')) n += (w.currentNode.textContent.match(/\S+/g) || []).length;
    }
    return n;
  })();
  return {
    pageHeight: document.documentElement.scrollHeight,
    overflowX: document.documentElement.scrollWidth > innerWidth + 1 ? document.documentElement.scrollWidth : 0,
    broken: imgs.filter((i) => i.complete && i.naturalWidth === 0).map((i) => i.getAttribute('src')),
    mermaidError: document.body.dataset.mermaidError || null,
    badges: imgs.filter(isBadge).length,
    images: imgs.filter((i) => !isBadge(i) && shown(i)).map((i) => ({ src: i.currentSrc || i.src, name: name(i.currentSrc || i.src) })),
    first: {
      visuals: visuals.filter(onFirst).map((el) => {
        const r = el.getBoundingClientRect();
        const src = el.tagName === 'IMG' ? el.currentSrc || el.src : null;
        return { name: src ? name(src) : el.tagName.toLowerCase(), src, w: Math.round(r.width), h: Math.round(Math.min(r.bottom, vh) - Math.max(r.top, 0)) };
      }),
      badges: imgs.filter((i) => isBadge(i) && onFirst(i)).length,
      headings: [...root.querySelectorAll('h1, h2, h3')].filter(onFirst).map((h) => h.textContent.trim()).slice(0, 4),
      code: [...root.querySelectorAll('pre')].filter((p) => !p.classList.contains('mermaid') && onFirst(p)).length,
      words,
    },
  };
}

// ---------- main ----------
let routes = {};
let root;
let label = opt.label;
let readme;
let approximateWhy = null;
if (!live) {
  const t = path.resolve(target || '.');
  if (!existsSync(t)) { console.error(`not found: ${t}`); process.exit(1); }
  readme = statSync(t).isDirectory() ? findReadme(t) : t;
  if (!readme) { console.error(`no README found in ${t}`); process.exit(1); }
  root = repoRoot(path.dirname(readme));
  const slug = repoSlug(root, opt.repo);
  label ||= path.basename(root);
  let html;
  if (opt.offline) approximateWhy = 'asked for with --offline';
  else {
    try {
      html = enrich(await renderWithGitHub(readme, slug));
    } catch (e) {
      approximateWhy = e.message;
    }
  }
  if (!html) html = await renderApproximate(readme);
  const css = await fs.readFile(path.join(VENDOR, 'github-markdown.css'), 'utf8');
  const mermaidFile = path.join(RUNTIME, 'mermaid', 'mermaid.min.js');
  const mermaidSrc = existsSync(mermaidFile) ? '/__killer_repo__/mermaid.min.js' : null;
  if (mermaidSrc) routes[mermaidSrc] = { file: mermaidFile };
  const rel = path.relative(root, path.dirname(readme)).split(path.sep).filter(Boolean);
  const base = '/' + rel.map(encodeURIComponent).join('/') + (rel.length ? '/' : '');
  for (const layout of Object.keys(LAYOUT)) {
    routes[`/__killer_repo__/${layout}.html`] = page(html, base, css, layout, { approximate: Boolean(approximateWhy), mermaidSrc });
  }
} else {
  label ||= 'live';
}

const out = path.resolve(opt.out || path.join(os.tmpdir(), 'killer-repo-shots', label));
await fs.mkdir(out, { recursive: true });

let server, origin;
if (!live) ({ server, origin } = await serve(root, routes));
const playwright = await loadPlaywright();
const browser = await launch(playwright);

const report = { target: live ? opt.url : readme, out, renderer: live ? 'github.com' : approximateWhy ? 'approximate' : 'github', approximateWhy, mermaid: live ? null : existsSync(path.join(RUNTIME, 'mermaid', 'mermaid.min.js')) ? 'runtime' : 'jsdelivr', variants: {} };
const lines = [];
const warn = (s) => lines.push(`WARN ${s}`);
const info = (s) => lines.push(`INFO ${s}`);
let shots = 0;

try {
  for (const name of opt.only) {
    const v = VARIANTS[name];
    const ctx = await browser.newContext({ viewport: { width: v.width, height: v.height }, colorScheme: v.scheme });
    const pg = await ctx.newPage();
    const failed = [];
    const bytes = new Map();
    // On live GitHub pages, only what the reader sees matters, not GitHub's own telemetry and payloads.
    const matters = (req) => !live || ['document', 'image', 'stylesheet', 'font'].includes(req.resourceType());
    pg.on('response', async (r) => {
      if (r.status() >= 400 && !r.url().includes('/__killer_repo__/') && matters(r.request())) failed.push(`${r.status()} ${r.url()}`);
      if (r.request().resourceType() === 'image' && r.status() < 400) {
        try { bytes.set(r.url(), (await r.body()).length); } catch { /* body unavailable, e.g. a redirect */ }
      }
    });
    pg.on('requestfailed', (r) => { if (matters(r)) failed.push(`failed ${r.url()}`); });
    const url = live ? opt.url : `${origin}/__killer_repo__/${v.layout}.html`;
    await pg.goto(url, { waitUntil: 'load', timeout: 60000 });
    if (!live) await pg.waitForSelector('body[data-ready="1"]', { timeout: 30000 }).catch(() => warn(`${name}: the page never finished rendering`));
    await pg.waitForTimeout(opt.wait);
    await pg.screenshot({ path: path.join(out, `${label}-${name}-first.png`) });
    await pg.screenshot({ path: path.join(out, `${label}-${name}-full.png`), fullPage: true });
    shots += 2;
    const r = await pg.evaluate(inspect, { vh: v.height, live });
    r.failedRequests = failed;
    const firstSrcs = new Set(r.first.visuals.map((x) => x.src).filter(Boolean));
    r.firstKB = Math.round([...firstSrcs].reduce((n, s) => n + (bytes.get(s) || 0), 0) / 1024);
    r.imageKB = Math.round([...bytes.values()].reduce((a, b) => a + b, 0) / 1024);
    report.variants[name] = r;
    await ctx.close();
  }
} finally {
  await browser.close();
  server?.close();
}

// ---------- report ----------
const summary = [];
if (approximateWhy) warn(`approximate render, not GitHub's (${approximateWhy}): the vendored marked with GitHub's main rules applied. Footnotes, math, emoji shortcodes and #123 / @user links aren't reproduced, so check the real page on github.com before calling it done`);
for (const [name, r] of Object.entries(report.variants)) {
  const f = r.first;
  const vis = f.visuals.length ? f.visuals.map((x) => `${x.name} ${x.w}x${x.h}`).join(', ') : 'none';
  if (!live) {
    summary.push(`${name.padEnd(13)} first screen: visuals: ${vis} (${r.firstKB} KB); ${f.badges} badge(s); ${f.code} code block(s); ${f.words} words; headings: ${f.headings.map((h) => `"${h}"`).join(', ') || 'none'}. Page ${r.pageHeight}px tall, ${r.imageKB} KB of images.`);
    if (r.firstKB > 2048) warn(`${name}: the first screen loads ${(r.firstKB / 1024).toFixed(1)} MB of images, heavy on mobile data`);
  }
  for (const b of r.broken) warn(`${name}: broken image ${b}`);
  for (const q of r.failedRequests) warn(`${name}: ${q}`);
  if (r.mermaidError) warn(`${name}: a Mermaid diagram failed to draw: ${r.mermaidError}`);
  if (r.overflowX) warn(`${name}: the page scrolls sideways (${r.overflowX}px wide)`);
  if (!live && name.startsWith('desktop') && !f.visuals.length) info(`${name}: nothing visual on the first screen; the top of the README is text only`);
}
if (!live) {
  const any = Object.values(report.variants)[0];
  if (any && any.badges > 6) info(`${any.badges} badges in the README; past a handful they read as noise`);
  const light = report.variants['desktop-light'], dark = report.variants['desktop-dark'];
  if (light && dark) {
    const darkSrcs = new Set(dark.images.map((i) => i.src));
    const same = [...new Set(light.images.filter((i) => darkSrcs.has(i.src) && /\.(png|gif|webp|apng|avif)(\?|$)/i.test(i.src)).map((i) => i.name))];
    if (same.length) info(`same raster image in light and dark mode, so check it reads on both backgrounds: ${same.join(', ')}`);
  }
}
await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
console.log(`Saved ${shots} screenshots and report.json to ${out}`);
for (const l of [...summary, ...lines]) console.log(l);

#!/usr/bin/env node
/**
 * audit.mjs - what someone landing on a repo would trip over, checked rather than guessed.
 *
 *   node audit.mjs [repo-dir] [--repo owner/name] [--offline] [--files a.md,b.md] [--json report.json]
 *
 * Markdown (the README, plus the other .md files at the root and in .github/, or --files):
 *   - local links and images exist, with the exact case GitHub needs, and are committed (not ignored)
 *   - #anchors match a heading, using GitHub's slugs
 *   - remote URLs answer (skipped with --offline); links into this repo on github.com are checked locally
 *   - image weight, and how many badges there are
 * The repo as GitHub shows it, through `gh` (skipped with --offline): description, website,
 * topics, social preview, license, latest release, community files, and the latest run of each
 * workflow on the default branch. Plus how crowded the root folder is.
 *
 * Exits 1 when anything is FAIL.
 */
import fs from 'node:fs/promises';
import { existsSync, statSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { run, repoRoot, repoSlug, findReadme } from './lib.mjs';

const argv = process.argv.slice(2);
if (argv.includes('-h') || argv.includes('--help')) {
  console.log('usage: node audit.mjs [repo-dir] [--repo owner/name] [--offline] [--files a.md,b.md] [--json out.json]');
  process.exit(0);
}
const opt = { offline: false };
let target;
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--repo') opt.repo = argv[++i];
  else if (a === '--offline') opt.offline = true;
  else if (a === '--files') opt.files = argv[++i].split(',').map((s) => s.trim()).filter(Boolean);
  else if (a === '--json') opt.json = argv[++i];
  else if (!target) target = a;
  else { console.error(`unknown argument: ${a}`); process.exit(1); }
}

const root = repoRoot(path.resolve(target || '.'));
const slug = opt.offline ? opt.repo || null : repoSlug(root, opt.repo);
const readme = findReadme(root);
const res = { root, slug, fail: [], warn: [], info: [], stats: {} };
const FAIL = (s) => res.fail.push(s), WARN = (s) => res.warn.push(s), INFO = (s) => res.info.push(s);
const rel = (p) => path.relative(root, p).split(path.sep).join('/');

const mdIn = (d) => { try { return readdirSync(d).filter((n) => /\.(md|markdown)$/i.test(n)).map((n) => path.join(d, n)); } catch { return []; } };
const files = [...new Set((opt.files ? opt.files.map((f) => path.resolve(root, f)) : [readme, ...mdIn(root), ...mdIn(path.join(root, '.github'))]).filter(Boolean))];
if (!readme) FAIL('no README: GitHub shows an empty page under the file list');

// What GitHub will have after a push: tracked files plus untracked ones that aren't ignored.
const listed = run('git', ['-C', root, 'ls-files', '--cached', '--others', '--exclude-standard']);
const gitFiles = listed === null ? null : new Set(listed.split('\n').filter(Boolean));
const tracked = new Set((run('git', ['-C', root, 'ls-files']) || '').split('\n').filter(Boolean));
const gitDirs = new Set();
if (gitFiles) for (const f of gitFiles) { const parts = f.split('/'); for (let i = 1; i < parts.length; i++) gitDirs.add(parts.slice(0, i).join('/')); }

// ---------- markdown ----------
function slugger() {
  const seen = new Map();
  return (text) => {
    const base = text.toLowerCase().trim().replace(/[^\p{L}\p{M}\p{N}\p{Pc}\- ]/gu, '').replace(/ /g, '-');
    let s = base;
    while (seen.has(s)) { seen.set(base, seen.get(base) + 1); s = `${base}-${seen.get(base)}`; }
    seen.set(s, 0);
    return s;
  };
}
const headingText = (t) => t
  .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
  .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
  .replace(/<[^>]+>/g, '')
  .replace(/`+/g, '')
  .replace(/[*~]/g, '')
  .replace(/(^|[^\p{L}\p{N}])_+|_+(?=[^\p{L}\p{N}]|$)/gu, '$1')
  .replace(/&[a-z]+;|&#\d+;/gi, '');

const parsed = new Map();
function parse(file) {
  if (parsed.has(file)) return parsed.get(file);
  const raw = readFileSync(file, 'utf8').split(/\r?\n/);
  const clean = [];
  let fence = null;
  for (const line of raw) {
    const m = line.match(/^ {0,3}(`{3,}|~{3,})/);
    if (fence) { if (m && m[1][0] === fence[0] && m[1].length >= fence.length && !line.trim().slice(m[1].length).trim()) fence = null; clean.push(''); continue; }
    if (m) { fence = m[1]; clean.push(''); continue; }
    clean.push(line.replace(/(`+)(?:(?!\1)[\s\S])*?\1/g, ' '));
  }
  const slug = slugger();
  const anchors = new Set();
  const refs = [];
  clean.forEach((line, i) => {
    const atx = line.match(/^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$/);
    if (atx) anchors.add(slug(headingText(raw[i].replace(/^ {0,3}#{1,6}\s+/, '').replace(/\s+#+\s*$/, ''))));
    else if (/^ {0,3}(=+|-+)\s*$/.test(line) && i > 0 && clean[i - 1].trim() && !/^\s*([|<>]|[-*+]\s|\d+\.\s|#)/.test(clean[i - 1])) anchors.add(slug(headingText(raw[i - 1].trim())));
    for (const h of line.matchAll(/<h[1-6][^>]*>(.*?)<\/h[1-6]>/gi)) anchors.add(slug(headingText(h[1])));
    for (const h of line.matchAll(/\b(?:id|name)\s*=\s*["']([^"']+)["']/gi)) anchors.add(h[1].toLowerCase());

    let rest = line;
    const take = (re, kind, group = 1) => {
      rest = rest.replace(re, (...m) => { refs.push({ line: i + 1, url: m[group], kind }); return ' '; });
    };
    take(/!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+["'(][^)]*)?\)/g, 'image');
    take(/\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+["'(][^)]*)?\)/g, 'link');
    take(/^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?=\s|$).*$/g, 'ref');
    for (const tag of rest.matchAll(/<(img|source)\b[^>]*>/gi)) {
      for (const a of tag[0].matchAll(/\bsrc\s*=\s*["']([^"']+)["']/gi)) refs.push({ line: i + 1, url: a[1], kind: 'image' });
      for (const a of tag[0].matchAll(/\bsrcset\s*=\s*["']([^"']+)["']/gi))
        for (const part of a[1].split(',')) { const u = part.trim().split(/\s+/)[0]; if (u) refs.push({ line: i + 1, url: u, kind: 'image' }); }
    }
    rest = rest.replace(/<(img|source)\b[^>]*>/gi, ' ');
    take(/<a\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>/gi, 'link');
    take(/<(https?:\/\/[^>\s]+)>/g, 'link');
    for (const m of rest.matchAll(/(?<![\w("'=<\/\[])https?:\/\/[^\s<>()"'`\]]+/g)) refs.push({ line: i + 1, url: m[0].replace(/[.,;:!?*_]+$/, ''), kind: 'link' });
  });
  const out = { anchors, refs };
  parsed.set(file, out);
  return out;
}

const IMG_EXT = /\.(png|jpe?g|gif|svg|webp|apng|avif|bmp|ico)$/i;
const BADGE = /shields\.io|badgen\.net|badge\.svg|\/badge(\/|\?|$)|codecov\.io|coveralls\.io|badge\.fury|img\.shields/i;
const remote = new Map(); // url -> [{file, line, kind}]
let imageBytes = 0, images = 0, badges = 0, checked = 0;

function checkLocal(fromFile, line, url, kind, label) {
  const [pathPart, frag] = url.split('#');
  const p = decodeURIComponent(pathPart.split('?')[0]);
  const where = `${rel(fromFile)}:${line}`;
  if (!p) return checkAnchor(fromFile, line, frag);
  const abs = p.startsWith('/') ? path.join(root, p) : path.resolve(path.dirname(fromFile), p);
  const r = rel(abs);
  if (r.startsWith('..')) { WARN(`${where} ${label} points outside the repo: ${url}`); return; }
  const isDir = r === '' || gitDirs.has(r.replace(/\/$/, ''));
  if (gitFiles && !gitFiles.has(r) && !isDir) {
    if (existsSync(abs)) {
      const ignored = run('git', ['-C', root, 'check-ignore', '-q', r]) !== null;
      const caseOnly = !ignored && [...gitFiles].some((f) => f.toLowerCase() === r.toLowerCase());
      if (caseOnly) FAIL(`${where} ${label} has the wrong letter case for GitHub, which is case-sensitive: ${url}`);
      else if (ignored) FAIL(`${where} ${label} exists here but git ignores it, so it won't be on GitHub: ${url}`);
      else FAIL(`${where} ${label} not found on GitHub: ${url}`);
    } else FAIL(`${where} ${label} not found: ${url}`);
    return;
  }
  if (!gitFiles && !existsSync(abs)) { FAIL(`${where} ${label} not found: ${url}`); return; }
  if (gitFiles && !tracked.has(r) && !isDir) INFO(`${where} ${label} isn't committed yet: ${url}`);
  if (kind === 'image' && existsSync(abs) && statSync(abs).isFile()) {
    const size = statSync(abs).size;
    imageBytes += size; images++;
    if (size > 1_000_000) WARN(`${where} heavy image: ${r} is ${(size / 1_048_576).toFixed(1)} MB, slow on phones and mobile data`);
  }
  if (frag && /\.(md|markdown)$/i.test(abs) && existsSync(abs)) checkAnchor(abs, line, frag, fromFile);
}

function checkAnchor(file, line, frag, linkedFrom = file) {
  if (!frag) return;
  const a = decodeURIComponent(frag).toLowerCase();
  if (/^l\d+(-l\d+)?$/.test(a)) return; // line links into code files
  const { anchors } = parse(file);
  if (!anchors.has(a) && !anchors.has(a.replace(/^user-content-/, '')))
    FAIL(`${rel(linkedFrom)}:${line} link to #${frag} matches no heading in ${rel(file)}`);
}

for (const file of files) {
  const { refs } = parse(file);
  for (const { line, url, kind } of refs) {
    if (/^(mailto|tel|javascript|data):/i.test(url)) continue;
    checked++;
    const isImage = kind === 'image' || IMG_EXT.test(url.split(/[?#]/)[0]);
    if (file === readme && isImage && BADGE.test(url)) badges++;
    const label = isImage ? 'image' : 'link';
    if (/^https?:\/\//i.test(url)) {
      const own = slug && url.match(new RegExp(`^https://(?:github\\.com/${slug}/(?:blob|tree|raw)/[^/]+|raw\\.githubusercontent\\.com/${slug}/[^/]+)/([^?#]+)(#.*)?$`, 'i'));
      if (own) { checkLocal(path.join(root, 'x'), line, '/' + own[1] + (own[2] || ''), isImage ? 'image' : 'link', `${label} (this repo)`); continue; }
      if (!remote.has(url)) remote.set(url, []);
      remote.get(url).push({ file, line, isImage });
    } else if (url.startsWith('#')) checkAnchor(file, line, url.slice(1));
    else checkLocal(file, line, url, isImage ? 'image' : kind, label);
  }
}

// ---------- remote URLs ----------
const localHost = (u) => {
  try {
    const h = new URL(u).hostname.replace(/^\[|\]$/g, '');
    return h === 'localhost' || h.endsWith('.localhost') || h.endsWith('.local') || h === '0.0.0.0' || h === '::1' ||
      /^127\./.test(h) || /^10\./.test(h) || /^192\.168\./.test(h) || /^172\.(1[6-9]|2\d|3[01])\./.test(h) ||
      /(^|\.)example(\.(com|org|net))?$/.test(h);
  } catch { return false; }
};
let skippedLocal = 0;
for (const url of [...remote.keys()]) if (localHost(url)) { remote.delete(url); skippedLocal++; }
if (skippedLocal) INFO(`${skippedLocal} link(s) to localhost, private or example addresses not checked`);

async function get(url, method = 'GET', wantBody = false) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), 10000);
  try {
    const r = await fetch(url, { method, redirect: 'follow', signal: ac.signal, headers: { 'user-agent': 'Mozilla/5.0 (killer-repo link check)' } });
    const type = r.headers.get('content-type') || '';
    let body = null;
    if (wantBody && /html/i.test(type)) body = (await r.text()).slice(0, 512 * 1024);
    else if (method === 'GET') r.body?.cancel().catch(() => {});
    return { status: r.status, length: Number(r.headers.get('content-length')) || 0, type, body };
  } finally { clearTimeout(t); }
}
async function probe(url) {
  try {
    let r = await get(url, 'HEAD');
    if (r.status >= 400) r = await get(url, 'GET', r.status === 404);
    return r;
  } catch (e) {
    return { error: e.name === 'AbortError' ? 'timed out' : (e.cause?.code || e.message) };
  }
}
// Single-page apps on GitHub Pages serve a copy of their index page as 404.html, so a deep
// link answers 404 yet loads fine in a browser. Spot that by comparing with the app's root page.
const roots = new Map();
async function spaRoute(url, body) {
  if (!body) return false;
  const u = new URL(url);
  const seg = u.pathname.split('/').filter(Boolean)[0];
  const title = (s) => (s.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [])[1]?.trim();
  for (const base of [seg ? `${u.origin}/${seg}/` : null, `${u.origin}/`].filter(Boolean)) {
    if (base === url) continue;
    if (!roots.has(base)) roots.set(base, get(base, 'GET', true).catch(() => null));
    const r = await roots.get(base);
    if (!r || r.status >= 400 || !r.body) continue;
    if (r.body === body) return true;
    if (title(r.body) && title(r.body) === title(body) && /<script[^>]+type=["']module/i.test(body)) return true;
  }
  return false;
}
if (!opt.offline && remote.size) {
  const urls = [...remote.keys()];
  let next = 0;
  await Promise.all(Array.from({ length: 8 }, async () => {
    while (next < urls.length) {
      const url = urls[next++];
      const r = await probe(url);
      const first = remote.get(url)[0];
      const where = `${rel(first.file)}:${first.line}`;
      if (r.error) WARN(`${where} unreachable (${r.error}): ${url}`);
      else if (r.status === 404 && (await spaRoute(url, r.body))) INFO(`${where} single-page app route: the server answers 404 but serves the app, which loads it: ${url}`);
      else if (r.status === 404 || r.status === 410) FAIL(`${where} dead link (${r.status}): ${url}`);
      else if (r.status >= 500) WARN(`${where} server error (${r.status}): ${url}`);
      else if (r.status >= 400) INFO(`${where} couldn't verify (${r.status}; the site may block checkers): ${url}`);
      else if (first.isImage && r.length > 1_000_000) WARN(`${where} heavy remote image (${(r.length / 1_048_576).toFixed(1)} MB): ${url}`);
    }
  }));
}
res.stats = { files: files.map(rel), refs: checked, remote: remote.size, images, imageKB: Math.round(imageBytes / 1024), badges };
if (badges > 6) WARN(`${badges} badges in the README: past a handful they read as noise`);

// ---------- the repo on GitHub ----------
if (!opt.offline && slug) {
  const j = JSON.parse(run('gh', ['repo', 'view', slug, '--json', 'description,homepageUrl,repositoryTopics,visibility,isArchived,licenseInfo,latestRelease,defaultBranchRef,usesCustomOpenGraphImage']) || 'null');
  if (!j) WARN(`couldn't read ${slug} through gh (not logged in, or no access); skipped the GitHub checks`);
  else {
    res.github = j;
    if (!j.description) WARN('no description: the About panel is empty, and search results show nothing under the name');
    else INFO(`description (${j.description.length} chars): ${j.description}`);
    if (!j.homepageUrl) INFO('no website in the About panel');
    const topics = (j.repositoryTopics || []).map((t) => t.name);
    if (!topics.length) WARN('no topics: they are how people find a repo through GitHub search and topic pages');
    else INFO(`topics (${topics.length}): ${topics.join(', ')}`);
    if (!j.usesCustomOpenGraphImage) INFO("no custom social card: shared links show GitHub's generated one");
    if (!j.licenseInfo) WARN('no license GitHub recognizes: others legally cannot reuse the code');
    if (j.latestRelease) INFO(`latest release: ${j.latestRelease.tagName} (${j.latestRelease.publishedAt.slice(0, 10)})`);
    else INFO('no releases');
    if (j.isArchived) INFO('the repo is archived');
    if (j.visibility === 'PUBLIC') {
      const cp = JSON.parse(run('gh', ['api', `repos/${slug}/community/profile`]) || 'null');
      if (cp) {
        // GitHub's profile misses some forms of these (issue forms in .github/ISSUE_TEMPLATE count as
        // null, for one), so a file in the working tree counts too.
        const here = {
          readme: [readme && rel(readme)],
          license: ['LICENSE', 'LICENSE.md', 'LICENSE.txt', 'COPYING'],
          contributing: ['CONTRIBUTING.md', '.github/CONTRIBUTING.md', 'docs/CONTRIBUTING.md'],
          code_of_conduct: ['CODE_OF_CONDUCT.md', '.github/CODE_OF_CONDUCT.md', 'docs/CODE_OF_CONDUCT.md'],
          issue_template: ['.github/ISSUE_TEMPLATE', '.github/ISSUE_TEMPLATE.md', 'ISSUE_TEMPLATE.md', 'docs/ISSUE_TEMPLATE.md'],
          pull_request_template: ['.github/pull_request_template.md', '.github/PULL_REQUEST_TEMPLATE', 'pull_request_template.md', 'docs/pull_request_template.md'],
        };
        const existsCI = (p) => {
          let d = root;
          for (const part of p.split('/')) {
            let names;
            try { names = readdirSync(d); } catch { return false; }
            const hit = names.find((n) => n.toLowerCase() === part.toLowerCase());
            if (!hit) return false;
            d = path.join(d, hit);
          }
          return true;
        };
        const missing = Object.entries(cp.files || {})
          .filter(([k, v]) => !v && !k.endsWith('_file') && !(here[k] || []).some((p) => p && existsCI(p)))
          .map(([k]) => k.replace(/_/g, ' '));
        INFO(`GitHub community profile: ${cp.health_percentage}%${missing.length ? `; missing: ${missing.join(', ')}` : ''} (add only what fits the project)`);
      }
    }
    const branch = j.defaultBranchRef?.name;
    const runs = JSON.parse(run('gh', ['run', 'list', '-R', slug, '--limit', '60', '--json', 'workflowName,conclusion,status,headBranch,createdAt']) || '[]');
    const latest = new Map();
    for (const r of runs) if (r.headBranch === branch && !latest.has(r.workflowName)) latest.set(r.workflowName, r);
    for (const [name, r] of latest) {
      if (r.conclusion === 'failure') WARN(`latest "${name}" run on ${branch} failed (${r.createdAt.slice(0, 10)}): a red check or badge undercuts everything else`);
    }
    if (latest.size) INFO(`workflows with runs on ${branch}: ${[...latest].map(([n, r]) => `${n} (${r.conclusion || r.status})`).join(', ')}`);
  }
}
const wf = path.join(root, '.github', 'workflows');
if (existsSync(wf)) INFO(`workflow files: ${readdirSync(wf).filter((n) => /\.ya?ml$/i.test(n)).join(', ') || 'none'}`);
for (const f of ['SECURITY.md', '.github/SECURITY.md', 'docs/SECURITY.md']) if (existsSync(path.join(root, f))) { INFO(`security policy: ${f}`); break; }
if (gitFiles) {
  const top = new Set([...gitFiles].map((f) => f.split('/')[0]));
  const dirs = [...top].filter((t) => gitDirs.has(t)).length;
  INFO(`the root shows ${top.size} entries (${dirs} folder${dirs === 1 ? "" : "s"}, ${top.size - dirs} file${top.size - dirs === 1 ? "" : "s"}) above the README`);
}

// ---------- print ----------
const s = res.stats;
console.log(`Audit of ${root}${slug ? ` (${slug})` : ''}`);
console.log(`Checked ${s.refs} links and images in ${s.files.length} file(s) (${s.remote} remote${opt.offline ? ', not fetched' : ''}); ${s.images} local images, ${s.imageKB} KB; ${s.badges} badges.`);
for (const l of res.fail) console.log(`FAIL ${l}`);
for (const l of res.warn) console.log(`WARN ${l}`);
for (const l of res.info) console.log(`INFO ${l}`);
if (!res.fail.length && !res.warn.length) console.log('No problems found.');
if (opt.json) await fs.writeFile(path.resolve(opt.json), JSON.stringify(res, null, 2));
process.exit(res.fail.length ? 1 : 0);

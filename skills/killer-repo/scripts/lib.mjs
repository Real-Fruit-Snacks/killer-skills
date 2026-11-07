/**
 * lib.mjs - shared helpers for the killer-repo scripts: finding or installing
 * Playwright once, launching Chromium, a throwaway static server, and repo lookups.
 */
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execSync, execFileSync } from 'node:child_process';
import http from 'node:http';
import fs from 'node:fs/promises';
import { existsSync, readdirSync, realpathSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CACHES = process.env.KILLER_SKILLS_CACHE || path.join(os.homedir(), '.cache');
export const CACHE = path.join(CACHES, 'killer-repo');
// The runtime the killer-skills installer puts in ~/.cache/killer-skills on Windows:
// Playwright with its own Chromium beside it, and Mermaid, so nothing needs downloading.
export const RUNTIME = path.join(CACHES, 'killer-skills');
// Files that ship inside this skill (github-markdown-css, marked), so they need no install.
export const VENDOR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'vendor');
// Reuse a copy the project, the runtime or killer-design already has before downloading another.
const BASES = [process.cwd(), RUNTIME, CACHE, path.join(CACHES, 'killer-design')];

export function log(msg) {
  console.error(`[killer-repo] ${msg}`);
}

/** Directory of an npm package, installing it into the cache on first use. */
export async function packageDir(name) {
  for (const base of BASES) {
    const d = path.join(base, 'node_modules', name);
    if (existsSync(path.join(d, 'package.json'))) return d;
  }
  await fs.mkdir(CACHE, { recursive: true });
  const pj = path.join(CACHE, 'package.json');
  if (!existsSync(pj)) await fs.writeFile(pj, '{ "name": "killer-repo-cache", "private": true }\n');
  log(`installing ${name} once into ${CACHE} ...`);
  execSync(`npm install --prefer-offline --no-audit --no-fund --loglevel=error ${name}`, { cwd: CACHE, stdio: 'inherit' });
  const d = path.join(CACHE, 'node_modules', name);
  if (!existsSync(path.join(d, 'package.json'))) throw new Error(`could not install ${name}`);
  return d;
}

export async function loadPlaywright() {
  const dir = await packageDir('playwright');
  // Point the runtime's Playwright at its own Chromium before it loads.
  if (dir.startsWith(RUNTIME + path.sep) && existsSync(path.join(RUNTIME, 'ms-playwright'))) process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(RUNTIME, 'ms-playwright');
  const req = createRequire(path.join(dir, '..', '__resolve__.js'));
  const mod = await import(pathToFileURL(req.resolve('playwright')).href);
  return { pw: mod.default ?? mod, dir };
}

export async function launch({ pw, dir }) {
  try {
    return await pw.chromium.launch();
  } catch (e) {
    if (!/Executable doesn't exist|playwright install/i.test(String(e.message))) throw e;
    log('downloading Chromium for Playwright (one-time) ...');
    execFileSync(process.execPath, [path.join(dir, 'cli.js'), 'install', 'chromium'], { stdio: 'inherit' });
    return await pw.chromium.launch();
  }
}

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp', '.avif': 'image/avif', '.apng': 'image/apng',
  '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.otf': 'font/otf',
  '.mp4': 'video/mp4', '.webm': 'video/webm',
};

/** Serve `root` on 127.0.0.1. `routes` maps exact paths to generated HTML, or to { file } to serve a file from elsewhere. */
export function serve(root, routes = {}) {
  const top = path.resolve(root);
  const server = http.createServer(async (req, res) => {
    const url = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const route = routes[url];
    if (route?.file) {
      res.writeHead(200, { 'content-type': TYPES[path.extname(route.file).toLowerCase()] || 'application/octet-stream' });
      return res.end(await fs.readFile(route.file));
    }
    if (route !== undefined) {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      return res.end(route);
    }
    const file = path.resolve(path.join(top, url));
    if (file !== top && !file.startsWith(top + path.sep)) {
      res.writeHead(403);
      return res.end();
    }
    try {
      const data = await fs.readFile(file);
      res.writeHead(200, { 'content-type': TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream' });
      res.end(data);
    } catch {
      res.writeHead(404);
      res.end('not found');
    }
  });
  return new Promise((resolve) =>
    server.listen(0, '127.0.0.1', () => resolve({ server, origin: `http://127.0.0.1:${server.address().port}` })));
}

export function run(cmd, args, opts = {}) {
  try {
    return execFileSync(cmd, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], ...opts }).trim();
  } catch {
    return null;
  }
}

// The repo's top folder, spelled the way the file system does (8.3 short names and symlinks resolved), so
// a path under it can be made relative to it.
export function repoRoot(dir) {
  const top = run('git', ['-C', dir, 'rev-parse', '--show-toplevel']);
  return realpathSync.native(top || dir);
}

/** owner/name from --repo, gh, or the origin remote. */
export function repoSlug(dir, given) {
  if (given) return given;
  const viaGh = run('gh', ['repo', 'view', '--json', 'nameWithOwner', '-q', '.nameWithOwner'], { cwd: dir });
  if (viaGh) return viaGh;
  const url = run('git', ['-C', dir, 'remote', 'get-url', 'origin']) || '';
  const m = url.match(/github\.com[/:]([^/]+\/[^/.]+?)(?:\.git)?$/);
  return m ? m[1] : null;
}

/** The README GitHub would show for a folder: .github/, then the root, then docs/. */
export function findReadme(dir) {
  for (const sub of ['.github', '.', 'docs']) {
    const d = path.join(dir, sub);
    let names = [];
    try { names = readdirSync(d); } catch { continue; }
    const hit = names.find((n) => /^readme(\.(md|markdown|mdown|mkd|txt|rst))?$/i.test(n));
    if (hit) return path.join(d, hit);
  }
  return null;
}

export function parseSize(s, fallback) {
  const m = String(s || '').match(/^(\d+)x(\d+)$/i);
  return m ? { width: Number(m[1]), height: Number(m[2]) } : fallback;
}

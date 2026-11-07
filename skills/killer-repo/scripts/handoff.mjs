#!/usr/bin/env node
// The books for a killer skill that has no other bookkeeping script (killer-design, killer-repo):
// a run folder, the calls the user should check, the outward actions left for their OK, and
// summary.json at the end, in the shape every killer skill writes for killer-goal to read.
//
//   node handoff.mjs start --project <folder> [--target "<what>"] [--slug <name>]
//                                          make the run folder; prints it
//   node handoff.mjs decide -w <run> "<text>"                     a call the user should check
//   node handoff.mjs wait -w <run> "<text>" [--command "<cmd>"]   an outward action left for their OK
//   node handoff.mjs finish -w <run> --headline "<what was done>" [--status done|partial|blocked]
//                    [--checks <report.json or folder> ...] [--report <file>]
//
// finish reads the checks the skill's own scripts wrote (shoot.mjs's report.json, audit.mjs
// --json): a run with a failing check is partial, whatever --status says. It lists the commits
// made in the project since start, and writes summary.json beside the handoff (<run>/report.md,
// unless --report names another file). Its last line is the one to end with.
//
// Runs live in ~/.claude/<skill>/<project>/<date>-<slug>/, like every killer skill's;
// KILLER_<SKILL>_HOME moves them. This file is shared: edit tools/shared/handoff.mjs, then run
// tools/sync.py, which copies it into each skill that uses it.
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

function die(msg, code = 2) {
  console.error(`error: ${msg}`);
  process.exit(code);
}

// The skill this copy belongs to: the name in its SKILL.md, or its folder's name.
function skillName() {
  try {
    const m = fs.readFileSync(path.join(HERE, '..', 'SKILL.md'), 'utf8').match(/^name:\s*["']?([A-Za-z0-9-]+)/m);
    if (m) return m[1];
  } catch { /* no SKILL.md beside it */ }
  return path.basename(path.dirname(HERE));
}

function parse(argv) {
  const opt = { _: [], checks: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const val = () => (i + 1 < argv.length ? argv[++i] : die(`${a} needs a value`));
    if (a === '-w' || a === '--workspace') opt.w = val();
    else if (a === '--project') opt.project = val();
    else if (a === '--target') opt.target = val();
    else if (a === '--slug') opt.slug = val();
    else if (a === '--command') opt.command = val();
    else if (a === '--headline') opt.headline = val();
    else if (a === '--status') opt.status = val();
    else if (a === '--report') opt.report = val();
    else if (a === '--checks') { while (i + 1 < argv.length && !argv[i + 1].startsWith('--')) opt.checks.push(argv[++i]); }
    else if (a === '-h' || a === '--help') opt.help = true;
    else if (a.startsWith('-') && a.length > 1) die(`unknown option ${a}`);
    else opt._.push(a);
  }
  return opt;
}

function git(cwd, ...args) {
  try {
    return execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  } catch {
    return null;
  }
}

const sha1 = (s) => createHash('sha1').update(s).digest('hex');
const pad = (n) => String(n).padStart(2, '0');
const today = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const stamp = () => { const d = new Date(); return `${today()}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
const slugify = (s) => (s || '').toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40).replace(/-+$/, '');

// The same <project> folder name as the Python skills' kit.project_key, so killer-goal finds the run.
function projectKey(top, isGit) {
  let ident = isGit ? git(top, 'config', '--get', 'remote.origin.url') || '' : '';
  ident = ident || (process.platform === 'win32' ? top.toLowerCase() : top);
  const slug = path.basename(top).replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'project';
  return `${slug}-${sha1(ident).slice(0, 8)}`;
}

function runsHome(skill) {
  const env = process.env[`KILLER_${skill.replace(/^killer-/, '').replace(/[^A-Za-z0-9]/g, '_').toUpperCase()}_HOME`];
  return path.resolve(env ? env.replace(/^~(?=$|[\\/])/, os.homedir()) : path.join(os.homedir(), '.claude', skill));
}

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^﻿/, ''));
  } catch (e) {
    if (e.code === 'ENOENT') return fallback;
    die(`${file} isn't valid JSON: ${e.message}`);
  }
}

// Write the whole file, then swap it in, so a reader never sees half of it. Windows refuses the
// swap while another process has the file open, so retry for a while.
function writeJson(file, data) {
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2) + '\n');
  for (let i = 0; ; i++) {
    try {
      fs.renameSync(tmp, file);
      return;
    } catch (e) {
      if (i >= 150 || !['EPERM', 'EACCES', 'EBUSY'].includes(e.code)) throw e;
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 100);
    }
  }
}

function load(opt) {
  if (!opt.w) die('give the run folder with -w <run>');
  const ws = path.resolve(opt.w);
  const cfg = readJson(path.join(ws, 'config.json'), null);
  if (!cfg) die(`no config.json in ${ws}; run start first`);
  return { ws, cfg };
}

function start(opt, skill) {
  const dir = path.resolve(opt.project || '.');
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) die(`no folder ${dir}`);
  const shown = git(dir, 'rev-parse', '--show-toplevel');
  const isGit = Boolean(shown);
  const top = fs.realpathSync.native(path.resolve(shown || dir));
  const head = isGit ? git(top, 'rev-parse', '--verify', '-q', 'HEAD^{commit}') : null;
  const branch = isGit ? git(top, 'symbolic-ref', '-q', '--short', 'HEAD') : null;
  const proj = path.join(runsHome(skill), projectKey(top, isGit));
  const name = slugify(opt.slug || (skill === 'killer-repo' ? 'repo-page' : 'site')) || 'run';
  let ws = path.join(proj, `${today()}-${name}`);
  for (let n = 2; fs.existsSync(path.join(ws, 'config.json')); n++) ws = path.join(proj, `${today()}-${name}-${n}`);
  fs.mkdirSync(ws, { recursive: true });
  writeJson(path.join(ws, 'config.json'), {
    skill, project: top, git: isGit, head: head || null, branch: branch || null,
    target: opt.target || '', created: stamp(),
  });
  console.log(`Run folder: ${ws}`);
  console.log(`Project: ${top}` + (head ? ` (${branch || 'detached'} at ${head.slice(0, 10)})` : ''));
  console.log(`Write the handoff to ${path.join(ws, 'report.md')}, then: node ${path.join(HERE, 'handoff.mjs')} finish -w "${ws}" --headline "<what was done>"`);
}

function record(opt, file, entry, said) {
  const { ws } = load(opt);
  if (!opt._[1]) die('say what it is, in quotes');
  const items = readJson(path.join(ws, file), []);
  items.push({ text: opt._[1], ...entry, at: stamp() });
  writeJson(path.join(ws, file), items);
  console.log(said(items.length));
}

// FAIL and WARN lines from a script's report: shoot.mjs keeps them in summary.FAIL and
// summary.WARN, audit.mjs --json in fail and warn.
function checkCounts(item) {
  let file = path.resolve(item);
  if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'report.json');
  const r = readJson(file, null);
  if (!r) die(`no report at ${file}`);
  const list = (...xs) => xs.find(Array.isArray) || [];
  const fails = list(r.summary?.FAIL, r.fail);
  const warns = list(r.summary?.WARN, r.warn);
  const shots = fs.readdirSync(path.dirname(file)).filter((f) => /\.(png|jpe?g)$/i.test(f)).length;
  return { file, fails, warns, shots };
}

function finish(opt, skill) {
  const { ws, cfg } = load(opt);
  if (!opt.headline || !opt.headline.trim()) die('give --headline "<what was done>"');
  let status = opt.status || 'done';
  if (!['done', 'partial', 'blocked'].includes(status)) die('--status is done, partial or blocked');
  const report = path.resolve(opt.report || path.join(ws, 'report.md'));
  if (!fs.existsSync(report)) die(`no handoff at ${report}; write it first, then run finish`);
  let headline = opt.headline.trim();
  const checks = opt.checks.map(checkCounts);
  const failing = checks.reduce((n, c) => n + c.fails.length, 0);
  if (failing && status === 'done') {
    status = 'partial';
    headline += `; ${failing} check${failing === 1 ? '' : 's'} still failing`;
    console.log(`note: ${failing} failing check${failing === 1 ? '' : 's'} in the reports, so this is partial, not done`);
  }
  let commits = [];
  if (cfg.git && cfg.head) {
    const log = git(cfg.project, 'log', '--format=%h %s', '-n', '100', `${cfg.head}..HEAD`) || '';
    commits = log.split('\n').filter((l) => l.trim());
  }
  const branch = commits.length ? git(cfg.project, 'symbolic-ref', '-q', '--short', 'HEAD') || null : null;
  const decisions = readJson(path.join(ws, 'decisions.json'), []);
  const waiting = readJson(path.join(ws, 'waiting.json'), []);
  writeJson(path.join(ws, 'summary.json'), {
    skill, status, headline, report, branch, commits,
    decisions: decisions.map((d) => d.text),
    waiting: waiting.map((w) => w.text + (w.command ? `: ${w.command}` : '')),
    numbers: {
      checks_failing: failing,
      checks_warning: checks.reduce((n, c) => n + c.warns.length, 0),
      screenshots: checks.reduce((n, c) => n + c.shots, 0),
      decisions: decisions.length,
      waiting: waiting.length,
    },
  });
  for (const c of checks) for (const f of c.fails) console.log(`FAIL ${f}`);
  console.log(`Wrote ${path.join(ws, 'summary.json')}`);
  console.log(`${skill}: ${status} · ${headline} · ${report}`);
}

const opt = parse(process.argv.slice(2));
const cmd = opt._[0];
if (opt.help || !cmd) {
  console.log(fs.readFileSync(fileURLToPath(import.meta.url), 'utf8').split('\n').slice(1, 20)
    .map((l) => l.replace(/^\/\/ ?/, '')).join('\n'));
  process.exit(cmd || opt.help ? 0 : 2);
}
const skill = skillName();
if (cmd === 'start') start(opt, skill);
else if (cmd === 'decide') record(opt, 'decisions.json', {}, (n) => `Recorded decision ${n}: ${opt._[1]}`);
else if (cmd === 'wait') {
  record(opt, 'waiting.json', { command: opt.command || '' },
    () => `Recorded as waiting for the user's OK: ${opt._[1]}` + (opt.command ? ` (${opt.command})` : ''));
} else if (cmd === 'finish') finish(opt, skill);
else die(`unknown command ${cmd}: use start, decide, wait or finish`);

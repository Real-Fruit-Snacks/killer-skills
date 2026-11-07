"""Scenario tests for killer-upgrade's upgrade.py. Run: python -I tests/test_upgrade.py

A fake ecosystem stands in for a real package manager: a tiny one (pm.py) in the fixture project,
packages from a folder that plays the registry, deps.json as the manifest and deps.lock as the
lockfile. No network and no real package manager is needed. The real ecosystems' parsers are checked
against saved samples of their tools' output.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPGRADE = ROOT / "skills" / "killer-upgrade" / "scripts" / "upgrade.py"
PY = sys.executable
failures = []
T0 = time.monotonic()
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


tmp = Path(tempfile.mkdtemp())
ENV = dict(os.environ, KILLER_UPGRADE_HOME=str(tmp / "runs"), KILLER_UPGRADE_SANDBOXES=str(tmp / "boxes"),
           GH_TOKEN="test-token-value", PYTHONIOENCODING="utf-8")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    ENV.pop(k, None)


def up(*args, ok=(0,)):
    r = subprocess.run([PY, str(UPGRADE), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    out = r.stdout + r.stderr
    if r.returncode not in ok:
        raise AssertionError(f"exit {r.returncode} for upgrade.py {' '.join(map(str, args))}:\n{out}")
    return out, r.returncode


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def run_folder(out):
    return Path(re.search(r"Run folder: (.+)", out).group(1).strip())


def box_of(out):
    return Path(re.search(r"project copy: (.+?)(?: \(at .*\))?$", out, re.M).group(1).strip()).parent


def plan_of(ws):
    return json.loads((ws / "plan.json").read_text(encoding="utf-8"))


def step(ws, sid):
    return next(s for s in plan_of(ws)["steps"] if s["id"] == sid)


spec = importlib.util.spec_from_file_location("upgrade_under_test", UPGRADE)
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)
U = U.lib   # upgrade.py is the entry point; its commands and parsers live in upgrade_lib.py

# ---------------------------------------------------------------- the fake ecosystem
PM = r'''"""A tiny package manager for tests: deps.json, deps.lock, and packages from $FAKE_REGISTRY."""
import hashlib, json, os, shutil, sys
from pathlib import Path

REG = Path(os.environ["FAKE_REGISTRY"])
MAN, LOCK, MODS = Path("deps.json"), Path("deps.lock"), Path("vendor_modules")
SECTIONS = ("dependencies", "devDependencies")


def vkey(v):
    return tuple(int(x) for x in v.split("."))


def versions(n):
    d = REG / n
    return sorted((p.name for p in d.iterdir()), key=vkey) if d.is_dir() else []


def digest(n, v):
    return hashlib.sha256((REG / n / v / f"{n}.py").read_bytes()).hexdigest()[:16]


def wanted_of(cur, vs):
    a = cur.split(".")
    same = [v for v in vs if v.split(".")[0] == a[0] and (a[0] != "0" or v.split(".")[1] == a[1])]
    return same[-1] if same else cur


def lock(man):
    out = {n: {"version": v, "digest": digest(n, v)} for s in SECTIONS for n, v in man.get(s, {}).items()}
    LOCK.write_text(json.dumps(dict(sorted(out.items())), indent=2) + "\n")


man = json.loads(MAN.read_text())
cmd = sys.argv[1]
if cmd == "lock":
    lock(man)
elif cmd == "install":
    lk = json.loads(LOCK.read_text())
    want = {n: v for s in SECTIONS for n, v in man.get(s, {}).items()}
    if {n: e["version"] for n, e in lk.items()} != want:
        print("error: deps.lock doesn't match deps.json")
        sys.exit(1)
    shutil.rmtree(MODS, ignore_errors=True)
    MODS.mkdir()
    for n, e in lk.items():
        src = REG / n / e["version"] / f"{n}.py"
        if not src.is_file() or digest(n, e["version"]) != e["digest"]:
            print(f"error: {n}@{e['version']} is missing or changed in the registry")
            sys.exit(1)
        shutil.copy(src, MODS / f"{n}.py")
    print(f"installed {len(lk)} packages")
elif cmd == "add":
    for spec in sys.argv[2:]:
        n, v = spec.split("@")
        if v not in versions(n):
            print(f"error: {n}@{v} isn't in the registry")
            sys.exit(1)
        sec = next((s for s in SECTIONS if n in man.get(s, {})), "dependencies")
        man.setdefault(sec, {})[n] = v
    MAN.write_text(json.dumps(man, indent=2) + "\n")
    lock(man)
    print("added " + " ".join(sys.argv[2:]))
elif cmd == "outdated":
    out = []
    for s in SECTIONS:
        for n, cur in sorted(man.get(s, {}).items()):
            vs = versions(n)
            adv = REG / n / cur / "advisory.json"
            e = {"name": n, "current": cur, "wanted": wanted_of(cur, vs), "latest": vs[-1],
                 "dev": s == "devDependencies"}
            if adv.is_file():
                e["advisories"] = [json.loads(adv.read_text())]
            if vs[-1] != cur or adv.is_file():
                out.append(e)
    print("checking the registry...", file=sys.stderr)
    print(json.dumps(out, indent=2))
    sys.exit(1 if out else 0)
'''

REGISTRY = {
    ("alpha", "1.0.0"): "def add(a, b):\n    return a + b\n",
    ("alpha", "1.1.0"): "def add(a, b):\n    return b + a\n",
    ("beta", "2.0.0"): "def parse_date(s):\n    y, m, d = s.split('-')\n    return (int(y), int(m), int(d))\n",
    ("beta", "2.0.1"): "def parse_date(s):\n    y, m, d = s.split('-')\n    return (int(y), int(d), int(m))\n",
    ("gamma", "1.2.0"): "def tag():\n    return 'g'\n",
    ("gamma", "1.3.0"): "def tag():\n    return 'g'\n",
    ("delta", "0.4.0"): "def mark():\n    return 'd'\n",
    ("delta", "0.4.1"): "def mark():\n    return 'd'\n",
    ("omega", "1.4.0"): "def greet(n):\n    return f'Hello, {n}'\n",
    ("omega", "2.0.0"): "def hello(n):\n    return f'Hello, {n}'\n",
    ("zeta", "0.3.1"): "def label(x):\n    return f'<{x}>'\n",
    ("zeta", "0.4.0"): "def label(x):\n    return f'<{x}>'\n",
    ("left", "1.0.0"): "def encode(s):\n    return 'v1:' + s\n",
    ("left", "1.1.0"): "import right\n\ndef encode(s):\n    return ('v2:' if getattr(right, 'V2', False) else 'v1:') + s\n",
    ("right", "1.0.0"): "def decode(s):\n    assert s.startswith('v1:')\n    return s[3:]\n",
    ("right", "1.1.0"): "V2 = True\n\ndef decode(s):\n    return s[3:] if s.startswith('v1:') else s[4:]\n",
    ("lint", "2.0.0"): "OK = True\n",
    ("lint", "2.1.0"): "OK = True\n",
    ("tool", "1.0.0"): "OK = True\n",
    ("tool", "1.0.3"): "OK = True\n",
}

APP = '''import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor_modules"))
import alpha, beta, delta, gamma, left, omega, right, zeta  # noqa: E401,E402


def add(a, b):
    return alpha.add(a, b)


def parse(s):
    return beta.parse_date(s)


def roundtrip(s):
    return right.decode(left.encode(s))


def greet(name):
    return omega.greet(name)


def label(x):
    return zeta.label(x) + gamma.tag() + delta.mark()
'''

TESTS = '''import os
import sys
import tempfile
import unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402


class TestApp(unittest.TestCase):
    def test_add(self):
        self.assertEqual(app.add(2, 3), 5)

    def test_dates(self):
        self.assertEqual(app.parse("2024-01-02"), (2024, 1, 2))

    def test_roundtrip(self):
        self.assertEqual(app.roundtrip("hi"), "hi")

    def test_greet(self):
        self.assertEqual(app.greet("Ann"), "Hello, Ann")

    def test_label(self):
        self.assertEqual(app.label("x"), "<x>gd")

    def test_known_broken(self):
        self.assertEqual(app.add(1, 1), 3)

    def test_flaky(self):
        p = os.path.join(tempfile.gettempdir(), "flaky-count")
        n = int(open(p).read()) + 1 if os.path.exists(p) else 1
        with open(p, "w") as f:
            f.write(str(n))
        self.assertNotEqual(n, 2)


if __name__ == "__main__":
    unittest.main()
'''

BUILD = ("import os, py_compile, tempfile\n"
         "py_compile.compile('app.py', cfile=os.path.join(tempfile.gettempdir(), 'app.pyc'), doraise=True)\n"
         "print('built')\n")

DEPS = {"dependencies": {"alpha": "1.0.0", "beta": "2.0.0", "delta": "0.4.0", "gamma": "1.2.0", "omega": "1.4.0",
                         "zeta": "0.3.1"},
        "devDependencies": {"left": "1.0.0", "lint": "2.0.0", "right": "1.0.0", "tool": "1.0.0"}}

try:
    # ---------------------------------------------------------------- parsers, against saved tool output
    npm_out = ('npm warn config production Use `--omit=dev` instead.\n' + json.dumps({
        "lodash": {"current": "4.17.20", "wanted": "4.17.21", "latest": "4.17.21", "dependent": "app",
                   "location": "node_modules/lodash", "type": "dependencies"},
        "jest": {"current": "29.6.0", "wanted": "29.7.0", "latest": "30.0.2", "dependent": "app",
                 "location": "node_modules/jest", "type": "devDependencies"},
        "react": [{"current": "18.2.0", "wanted": "18.3.1", "latest": "19.1.0", "dependent": "web",
                   "location": "node_modules/react", "type": "dependencies"},
                  {"current": "18.2.0", "wanted": "18.3.1", "latest": "19.1.0", "dependent": "app",
                   "location": "node_modules/react", "type": "dependencies"}]}, indent=2))
    pk = U.parse_npm_outdated(npm_out, root_name="app")
    by = {(p["name"], p["workspace"]): p for p in pk}
    check("npm outdated: read past a warning, dev from type, one entry per workspace",
          len(pk) == 4 and by[("jest", None)]["dev"] and by[("lodash", None)]["wanted"] == "4.17.21"
          and ("react", "web") in by and ("react", None) in by, pk)
    pip_out = json.dumps([{"name": "requests", "version": "2.28.0", "latest_version": "2.32.3", "latest_filetype": "wheel"},
                          {"name": "Django", "version": "4.1.0", "latest_version": "5.2.1", "latest_filetype": "wheel"}])
    pp = U.parse_pip_outdated(pip_out)
    check("pip list --outdated: current and latest", [(p["name"], p["current"], p["latest"]) for p in pp]
          == [("requests", "2.28.0", "2.32.3"), ("Django", "4.1.0", "5.2.1")], pp)
    check("pip index versions", U.parse_pip_index("Django (5.2.1)\nAvailable versions: 5.2.1, 5.2.0, 4.2.20, 4.1.0\n")
          == ["5.2.1", "5.2.0", "4.2.20", "4.1.0"])
    check("newest in the same major", U.newest_in_major(["5.2.1", "5.2.0", "4.2.20", "4.1.0"], "4.1.0") == "4.2.20")
    cargo_out = (json.dumps({"crate_name": "app", "dependencies": [
        {"name": "serde", "project": "1.0.150", "compat": "1.0.219", "latest": "1.0.219", "kind": "Normal", "platform": None},
        {"name": "rand", "project": "0.7.3", "compat": "---", "latest": "0.9.1", "kind": "Normal", "platform": None},
        {"name": "tempfile", "project": "3.3.0", "compat": "3.20.0", "latest": "3.20.0", "kind": "Development",
         "platform": None}]}) + "\n" + json.dumps({"crate_name": "lib", "dependencies": []}) + "\n")
    cp = U.parse_cargo_outdated(cargo_out)
    check("cargo outdated: one JSON per crate, '---' is no compatible update, dev from kind",
          [(p["name"], p["wanted"], p["latest"], p["dev"]) for p in cp]
          == [("serde", "1.0.219", "1.0.219", False), ("rand", None, "0.9.1", False), ("tempfile", "3.20.0", "3.20.0", True)], cp)
    dry = ("    Updating crates.io index\n     Locking 2 packages to latest compatible versions\n"
           "    Updating serde v1.0.150 -> v1.0.219\n    Updating serde_derive v1.0.150 -> v1.0.219 (latest: v1.0.219)\n"
           "warning: not updating lockfile due to dry run\n")
    check("cargo update --dry-run", [(p["name"], p["current"], p["wanted"]) for p in U.parse_cargo_dry_run(dry)]
          == [("serde", "1.0.150", "1.0.219"), ("serde_derive", "1.0.150", "1.0.219")])
    go_out = ('{\n\t"Path": "example.com/app",\n\t"Main": true,\n\t"GoVersion": "1.21"\n}\n'
              '{\n\t"Path": "github.com/pkg/errors",\n\t"Version": "v0.8.1",\n\t"Update": {\n\t\t"Path": "github.com/pkg/errors",\n'
              '\t\t"Version": "v0.9.1"\n\t}\n}\n'
              '{\n\t"Path": "golang.org/x/text",\n\t"Version": "v0.3.7",\n\t"Update": {"Path": "golang.org/x/text", '
              '"Version": "v0.25.0"},\n\t"Indirect": true\n}\n'
              '{\n\t"Path": "github.com/stretchr/testify",\n\t"Version": "v1.8.0",\n\t"Update": {"Path": '
              '"github.com/stretchr/testify", "Version": "v1.10.0"}\n}\n'
              '{\n\t"Path": "github.com/google/uuid",\n\t"Version": "v1.6.0"\n}\n')
    gp = U.parse_go_list(go_out)
    check("go list -m -u -json: a stream of objects; the main module and up-to-date ones skipped, indirect marked",
          [(p["name"], p["current"], p["wanted"], p["indirect"]) for p in gp]
          == [("github.com/pkg/errors", "v0.8.1", "v0.9.1", False), ("golang.org/x/text", "v0.3.7", "v0.25.0", True),
              ("github.com/stretchr/testify", "v1.8.0", "v1.10.0", False)], gp)
    check("pnpm outdated", [(p["name"], p["latest"], p["dev"]) for p in U.parse_pnpm_outdated(json.dumps(
        {"chalk": {"current": "4.1.2", "latest": "5.4.1", "wanted": "4.1.2", "dependencyType": "dependencies"}}))]
        == [("chalk", "5.4.1", False)])
    yarn_out = ('{"type":"info","data":"Color legend"}\n{"type":"table","data":{"head":["Package","Current","Wanted",'
                '"Latest","Package Type","URL"],"body":[["left-pad","1.1.0","1.3.0","1.3.0","devDependencies","x"]]}}\n')
    check("yarn outdated --json", [(p["name"], p["current"], p["wanted"], p["dev"]) for p in U.parse_yarn_outdated(yarn_out)]
          == [("left-pad", "1.1.0", "1.3.0", True)])
    check("bundle outdated --parseable", [(p["name"], p["current"], p["latest"]) for p in U.parse_bundle_outdated(
        "rack (newest 3.1.12, installed 2.2.8, requested ~> 2.2)\nrake (newest 13.2.1, installed 13.0.6)\n")]
        == [("rack", "2.2.8", "3.1.12"), ("rake", "13.0.6", "13.2.1")])
    check("dotnet list package --outdated --format json", [(p["name"], p["current"], p["latest"]) for p in
          U.parse_dotnet_outdated(json.dumps({"version": 1, "projects": [{"path": "App.csproj", "frameworks": [{
              "framework": "net8.0", "topLevelPackages": [{"id": "Newtonsoft.Json", "requestedVersion": "12.0.1",
                                                            "resolvedVersion": "12.0.1", "latestVersion": "13.0.3"}]}]}]}))]
          == [("Newtonsoft.Json", "12.0.1", "13.0.3")])
    check("poetry show --outdated", [(p["name"], p["current"], p["latest"]) for p in U.parse_poetry_outdated(
        "requests 2.28.0 2.32.3 Python HTTP for Humans.\npytest   (!) 7.4.0  8.3.5  pytest: simple testing\n")]
        == [("requests", "2.28.0", "2.32.3"), ("pytest", "7.4.0", "8.3.5")])
    audit = json.dumps({"auditReportVersion": 2, "vulnerabilities": {
        "lodash": {"name": "lodash", "severity": "high", "isDirect": True, "via": [
            {"source": 1094499, "name": "lodash", "title": "Prototype Pollution in lodash",
             "url": "https://github.com/advisories/GHSA-p6mc-m468-83gw", "severity": "high", "range": "<4.17.21"}],
            "fixAvailable": {"name": "lodash", "version": "4.17.21", "isSemVerMajor": False}},
        "mkdirp": {"name": "mkdirp", "severity": "critical", "isDirect": False, "via": ["minimist"], "fixAvailable": True}}})
    na = U.parse_npm_audit(audit)
    check("npm audit --json: the advisory id from its URL, the fixed version when the fix is that package",
          [(x["package"], x["id"], x["fixed_in"]) for x in na] == [("lodash", "GHSA-p6mc-m468-83gw", "4.17.21")], na)
    pa = U.parse_pip_audit(json.dumps({"dependencies": [{"name": "jinja2", "version": "3.1.2", "vulns": [
        {"id": "GHSA-h5c8-rqwp-cp95", "fix_versions": ["3.1.3"], "aliases": ["CVE-2024-22195"]}]},
        {"name": "flask", "version": "3.0.0", "vulns": []}], "fixes": []}))
    check("pip-audit -f json", [(x["package"], x["id"], x["fixed_in"]) for x in pa] == [("jinja2", "GHSA-h5c8-rqwp-cp95", "3.1.3")])
    ca = U.parse_cargo_audit(json.dumps({"vulnerabilities": {"found": True, "list": [{"advisory": {
        "id": "RUSTSEC-2020-0071", "package": "time", "title": "segfault"}, "versions": {"patched": [">=0.2.23"]},
        "package": {"name": "time", "version": "0.1.45"}}]}}))
    check("cargo audit --json", [(x["package"], x["id"], x["fixed_in"]) for x in ca] == [("time", "RUSTSEC-2020-0071", "0.2.23")])
    check("runtime indexes: Node.js LTS, Python, Go, Rust",
          U.parse_node_index(json.dumps([{"version": "v25.1.0", "lts": False}, {"version": "v24.11.0", "lts": "Krypton"}]))
          == "24.11.0" and U.parse_python_eol(json.dumps([{"cycle": "3.14", "latest": "3.14.0"}, {"cycle": "3.13",
                                                                                                    "latest": "3.13.9"}]))
          == "3.14.0" and U.parse_go_dl(json.dumps([{"version": "go1.25.3", "stable": True}])) == "1.25.3"
          and U.parse_rust_channel('[pkg.rust]\nversion = "1.91.0 (f8297e351 2025-10-28)"\n') == "1.91.0")

    # ---------------------------------------------------------------- versions and edits
    cases = [("1.2.3", "1.2.4", "patch"), ("1.2.3", "1.3.0", "minor"), ("1.2.3", "2.0.0", "major"),
             ("0.3.1", "0.4.0", "major"), ("0.3.1", "0.3.2", "patch"), ("0.0.3", "0.0.4", "major"),
             ("v1.2.3", "v1.3.0", "minor"), ("1.0.0", "1.1.0-beta.1", "major"), ("2024.1", "2024.2", "minor"),
             ("v3", "v4", "major"), ("weird", "1.0", "major")]
    got = [(a, b, U.classify(a, b)) for a, b, _ in cases]
    check("classify: semver, 0.x minor and 0.0.x patch are majors, prereleases and odd versions are majors",
          got == cases, [c for c in got if c not in cases])
    check("versions order numerically", U.newer("1.10.0", "1.9.0") and not U.newer("1.0.0rc1", "1.0.0")
          and U.newer("1.0.0", "1.0.0-rc.1"))
    check("satisfies", U.satisfies("2.31.0", [">=2.28", "<3"]) and not U.satisfies("3.0.1", [">=2.28", "<3"])
          and U.satisfies("4.2.7", ["~=4.1"]) and not U.satisfies("5.0", ["~=4.1"]) and U.satisfies("1.4.2", ["==1.4.*"]))
    check("requirement lines: pins move, extras, markers and comments stay, unpinned ones float",
          U.req_line_edit("requests==2.28.0", "requests", "2.32.3") == "requests==2.32.3"
          and U.req_line_edit('uvicorn[standard]==0.23.0 ; python_version >= "3.8"  # server', "uvicorn", "0.30.1")
          == 'uvicorn[standard]==0.30.1 ; python_version >= "3.8"  # server'
          and U.req_line_edit("Django~=4.1.0", "django", "4.2.7") == "Django~=4.2.7"
          and U.req_line_edit("flask>=2,<3", "Flask", "3.0.1") == "flask>=3.0.1,<4"
          and U.req_line_edit("flask>=2,<3", "flask", "2.3.0") is None
          and U.req_line_edit("numpy", "numpy", "2.0.0") is None)
    check("pyproject dependency strings", U.pyproject_edit('name = "app"\ndependencies = ["httpx>=0.24,<0.25", "rich"]\n',
                                                           "httpx", "0.27.0") == ('"httpx>=0.24,<0.25"', '"httpx>=0.27.0,<1"'))
    ct = 'rand = "0.7"\nclap = { version = "3.2.25", features = ["derive"] }\nserde = "1"\n'
    check("Cargo.toml version edits keep the form", U.cargo_edits(ct, "rand", "0.9.1") == [('rand = "0.7"', 'rand = "0.9"')]
          and U.cargo_edits(ct, "clap", "4.5.4") == [('clap = { version = "3.2.25", features = ["derive"] }',
                                                      'clap = { version = "4.5.4", features = ["derive"] }')]
          and U.cargo_edits(ct, "serde", "2.0.1") == [('serde = "1"', 'serde = "2"')])
    check("Gemfile version edits", U.gemfile_edits("gem 'rack', '~> 2.2'\ngem \"rake\"\n", "rack", "3.1.12")
          == [("gem 'rack', '~> 2.2'", "gem 'rack', '~> 3.1'")])
    toml = ('[project]\nname = "app"  # the name\ndependencies = [\n  "httpx>=0.24",\n  "rich",\n]\n'
            '[project.optional-dependencies]\ntest = ["pytest>=7"]\n[tool.poetry.group.dev.dependencies]\n'
            'black = "^23.0"\n[dependencies]\nclap = { version = "4", features = ["derive"] }\n')
    mt = U.mini_toml(toml)
    check("mini TOML (for Python 3.10): tables, arrays across lines, inline tables",
          mt["project"]["dependencies"] == ["httpx>=0.24", "rich"] and mt["project"]["name"] == "app"
          and mt["project"]["optional-dependencies"]["test"] == ["pytest>=7"]
          and mt["tool"]["poetry"]["group"]["dev"]["dependencies"]["black"] == "^23.0"
          and mt["dependencies"]["clap"]["version"] == "4", mt)
    deps = U.py_deps(mt)
    check("pyproject dependencies and groups, with dev ones marked",
          not deps["httpx"]["dev"] and deps["pytest"]["dev"] and deps["black"]["dev"]
          and deps["pytest"]["group"] == "optional:test", deps)
    check("split_cmd: a JSON list, or words", U.split_cmd('["a b", "c"]') == ["a b", "c"]
          and U.split_cmd('npm install --save-dev') == ["npm", "install", "--save-dev"])
    merged = U.merge_actions([U.run_act(["npm", "install"], ["a@1"], merge=True), U.run_act(["npm", "install"], ["b@2"], merge=True),
                              U.run_act(["cargo", "update"], ["-p", "x"])])
    check("package-manager commands that take several packages run once", [U.act_cmd(m) for m in merged]
          == [["npm", "install", "a@1", "b@2"], ["cargo", "update", "-p", "x"]], merged)
    mk = lambda uid, name, to, kind, adv=(), fixes=(): {"id": uid, "eco": "npm:.", "name": name, "to": to, "kind": kind,
                                                       "group": "major" if kind == "major" else "deps", "note": None,
                                                       "advisories": list(adv), "fixes": list(fixes)}
    pool = {"U1": mk("U1", "lodash", "4.17.21", "patch", ["GHSA-1"], ["GHSA-1"]), "U2": mk("U2", "lodash", "5.0.0", "major", ["GHSA-1"], ["GHSA-1"]),
            "U3": mk("U3", "qs", "6.5.3", "minor", ["GHSA-2"]), "U4": mk("U4", "qs", "7.0.0", "major", ["GHSA-2"], ["GHSA-2"]),
            "U5": mk("U5", "chalk", "5.0.0", "major"), "U6": mk("U6", "debug", "4.3.5", "patch")}
    pick = lambda scope, majors="try", holds=None: U.select(pool, {"scope": scope, "majors": majors}, holds or {})[0]
    check("plan selection: security keeps the smallest upgrade that fixes each advisory; minor drops majors; "
          "listed majors and holds are left out; one package",
          pick("security") == ["U1", "U4"] and pick("minor") == ["U1", "U3", "U6"]
          and pick("all", "list") == ["U1", "U3", "U6"] and pick("package:qs") == ["U3", "U4"]
          and pick("all", holds={"lodash": {"reason": "x", "upgrades": ["U1", "U2"]}}) == ["U3", "U4", "U5", "U6"],
          [pick("security"), pick("minor"), pick("all", "list"), pick("package:qs")])
    check("linked packages: one scope, or one name extending another", U.link_names("@babel/core", "@babel/preset-env")
          and U.link_names("react", "react-dom") and not U.link_names("react", "preact") and not U.link_names("@a/x", "@b/x"))

    # ---------------------------------------------------------------- reading test output
    pytest_v = ("tests/test_x.py::test_a PASSED                                           [ 25%]\n"
                "tests/test_x.py::test_b FAILED                                           [ 50%]\n"
                "tests/test_x.py::test_c[1-2] SKIPPED (no db)                             [ 75%]\n"
                "tests/test_x.py::test_d XFAIL                                            [100%]\n"
                "=== short test summary info ===\nFAILED tests/test_x.py::test_b - AssertionError: assert 1 == 2\n"
                "ERROR tests/test_y.py - ModuleNotFoundError: No module named 'foo'\n")
    r = U.parse_tests(pytest_v)
    check("pytest -v, its summary, and a collection error", r["passed"] == ["tests/test_x.py::test_a", "tests/test_x.py::test_d"]
          and r["failed"] == ["tests/test_x.py::test_b", "tests/test_y.py"] and r["skipped"] == ["tests/test_x.py::test_c[1-2]"]
          and r["format"] == "pytest", r)
    r = U.parse_tests("\x1b[32mPASSED\x1b[0m tests/a.py::test_ok\nPASSED tests/a.py::test_two\n")
    check("pytest -rA, with colour codes", r["passed"] == ["tests/a.py::test_ok", "tests/a.py::test_two"], r)
    ut = ("test_a (test_x.T) ... ok\ntest_b (test_x.T)\nCheck b. ... FAIL\ntest_c (test_x.T.test_c) ... skipped 'no'\n"
          "test_d (test_x.T.test_d) ... printed something\nok\n"
          "======\nERROR: test_e (test_x.T.test_e)\n------\nTraceback...\n")
    r = U.parse_tests(ut.replace("\n", "\r\n"))
    check("unittest -v: Python 3.10 and 3.11 forms, a docstring line, output in between, CRLF",
          r["passed"] == ["test_x.T.test_a", "test_x.T.test_d"] and r["failed"] == ["test_x.T.test_b", "test_x.T.test_e"]
          and r["skipped"] == ["test_x.T.test_c"], r)
    go_v = ("=== RUN   TestAdd\n--- PASS: TestAdd (0.00s)\n=== RUN   TestSub\n    sub_test.go:9: got 1\n"
            "--- FAIL: TestSub (0.00s)\n--- SKIP: TestSkip (0.00s)\nFAIL\nFAIL\texample.com/app\t0.003s\n"
            "FAIL\texample.com/app/broken [build failed]\nok  \texample.com/app/other\t0.002s\n")
    r = U.parse_tests(go_v)
    check("go test -v, and a package that didn't build", r["passed"] == ["TestAdd"]
          and r["failed"] == ["TestSub", "example.com/app/broken [build failed]"] and r["skipped"] == ["TestSkip"], r)
    r = U.parse_tests("running 3 tests\ntest tests::adds ... ok\ntest tests::subs ... FAILED\ntest tests::slow ... ignored\n"
                      "test src/lib.rs - add (line 5) ... ok\n")
    check("cargo test, doc tests too", r["passed"] == ["src/lib.rs - add (line 5)", "tests::adds"]
          and r["failed"] == ["tests::subs"] and r["skipped"] == ["tests::slow"], r)
    tap = ("TAP version 13\n# Subtest: adds\nok 1 - adds\n  ---\n  duration_ms: 0.5\n  ...\nnot ok 2 - subs\n"
           "ok 3 - later # SKIP not ready\nok 4 - someday # TODO\n1..4\n")
    r = U.parse_tests(tap)
    check("TAP (node --test)", r["passed"] == ["adds"] and r["failed"] == ["subs"] and r["skipped"] == ["later"], r)
    js = ("PASS src/a.test.js\n  math\n    ✓ adds (3 ms)\n    ✕ subs (1 ms)\nFAIL src/b.test.js\n"
          " ✓ src/c.test.ts (2 tests) 4ms\n   × divides\n✔ node spec ok (0.52ms)\n")
    r = U.parse_tests(js)
    check("jest, vitest and node's spec reporter, durations dropped",
          r["passed"] == ["adds", "node spec ok", "src/a.test.js", "src/c.test.ts"]
          and r["failed"] == ["divides", "src/b.test.js", "subs"], r)
    r = U.parse_tests("  math\n    ✔ adds\n    1) subs\n\n  1 passing (5ms)\n  1 failing\n\n  1) math\n       subs:\n")
    check("mocha", "adds" in r["passed"] and "subs" in r["failed"], r)
    check("plain output gives no per-test results", U.parse_tests("all good\nok\n")["format"] is None)
    jx = tmp / "junit.xml"
    jx.write_text('<testsuites><testsuite><testcase classname="a.B" name="ok"/><testcase classname="a.B" name="bad">'
                  '<failure message="x"/></testcase><testcase name="skip"><skipped/></testcase></testsuite></testsuites>')
    r = U.parse_junit([jx])
    check("JUnit XML", r["passed"] == ["a.B.ok"] and r["failed"] == ["a.B.bad"] and r["skipped"] == ["skip"], r)
    unit = {"cmd": ["t"], "judge": "tests", "always_pass": ["a", "b", "c"], "ever_fail": ["x", "f"], "cmd_ok": False}
    regs = U.regressions(unit, {"passed": ["a"], "failed": ["x", "new"], "skipped": ["b"], "code": 1, "timed_out": False})
    check("regressions: a new failure, a test now skipped, one that didn't run; old failures don't count",
          regs == ["new", "b (now skipped)", "c (didn't run)"], regs)
    regs = U.regressions(dict(unit, cmd_ok=True), {"passed": [], "failed": [], "skipped": [], "code": 2, "timed_out": False})
    check("regressions: a runner that crashed before any test ran", len(regs) == 1 and "ran none of the 3" in regs[0], regs)
    regs = U.regressions({"cmd": ["t"], "judge": "exit", "always_pass": [], "ever_fail": [], "cmd_ok": True},
                         {"passed": [], "failed": [], "skipped": [], "code": None, "timed_out": True})
    check("regressions: judged by exit code, a timeout counts", len(regs) == 1 and "timed out" in regs[0], regs)

    # ---------------------------------------------------------------- detection, across ecosystems
    det = tmp / "many"
    files = {
        "package.json": json.dumps({"name": "root", "workspaces": ["packages/*"], "engines": {"node": ">=18"},
                                    "scripts": {"test": "jest", "build": "tsc"}, "dependencies": {"left-pad": "^1.1.0"}}),
        "package-lock.json": "{}",
        "packages/a/package.json": json.dumps({"name": "a", "dependencies": {"chalk": "4.1.2"}}),
        "examples/demo/package.json": json.dumps({"name": "demo"}),
        "requirements.txt": "requests==2.28.0\n", "requirements-dev.txt": "pytest==7.4.0\n",
        "py/pyproject.toml": '[tool.poetry]\nname = "x"\n[tool.poetry.dependencies]\npython = "^3.10"\n', "py/poetry.lock": "",
        "rust/Cargo.toml": '[package]\nname = "r"\n[dependencies]\nrand = "0.7"\n', "rust/Cargo.lock": "",
        "go/go.mod": "module example.com/x\n\ngo 1.21\n", "go/go.sum": "",
        "Gemfile": "source 'https://rubygems.org'\ngem 'rack', '~> 2.2'\n", "Gemfile.lock": "",
        "dotnet/App.csproj": '<Project><ItemGroup><PackageReference Include="Newtonsoft.Json" Version="12.0.1" />'
                             '</ItemGroup></Project>',
        "pom.xml": "<project/>", ".nvmrc": "18\n", ".python-version": "3.11\n",
        "Dockerfile": "FROM node:18-alpine AS build\nRUN npm ci\nFROM build\n",
        ".github/workflows/ci.yml": ("jobs:\n  t:\n    steps:\n      - uses: actions/checkout@v3\n"
                                     "      - uses: actions/setup-node@v4.1.0\n"
                                     "      - uses: some/thing@0123456789abcdef0123456789abcdef01234567\n"
                                     "      - name: Run the tests with pytest\n        run: npm test\n"
                                     "      - run: |\n          pytest -q\n"),
        ".npmrc": "//registry.example.com/:_authToken=${NPM_TOKEN}\n",
    }
    for rel, text in files.items():
        (det / rel).parent.mkdir(parents=True, exist_ok=True)
        (det / rel).write_text(text, encoding="utf-8")
    g(det, "init", "-q", "-b", "main")
    out, _ = up("init", "--repo", det, "--slug", "detect")
    wd = run_folder(out)
    found = json.loads((wd / "ecosystems.json").read_text(encoding="utf-8"))
    ecos = {e["id"]: e for e in found["ecosystems"]}
    check("init: npm at the top with its workspace member; a loose package.json left out",
          ecos.get("npm:.", {}).get("members") == ["packages/a"] and ecos["npm:."]["lockfile"] == "package-lock.json"
          and not any("examples" in k for k in ecos) and "examples/demo/package.json" in out, (sorted(ecos), out))
    check("init: pip (both requirement files), poetry, cargo, go, bundler and dotnet",
          set(ecos) >= {"pip:.", "poetry:py", "cargo:rust", "go:go", "bundler:.", "dotnet:dotnet/App.csproj"}
          and ecos["pip:."]["manifests"] == ["requirements-dev.txt", "requirements.txt"], sorted(ecos))
    check("init: Maven listed only", any("Maven" in x["what"] for x in found["listed"]), found["listed"])
    rts = {(r["label"], r["current"], r["kind"]) for r in found["runtimes"]}
    check("init: runtime pins and minimums", {("Node.js", "18", "pin"), ("Python", "3.11", "pin"),
                                              ("Node.js", ">=18", "minimum"), ("Go", "1.21", "minimum")} <= rts, rts)
    check("init: CI action pins (tag and commit), and the image that isn't a build stage",
          [(x["repo"], x["pin"]) for x in found["actions"]] == [("checkout", "tag"), ("setup-node", "tag"), ("thing", "sha")]
          and [i["image"] for i in found["images"]] == ["node:18-alpine"], (found["actions"], found["images"]))
    cmds = {(c["kind"], c["cmd"]) for c in found["commands"]}
    check("init: commands from package scripts and CI, an install for each ecosystem",
          {("tests", "npm test"), ("build", "npm run build"), ("ci", "pytest -q"), ("ci", "npm test")} <= cmds
          and any(c.startswith("npm ci") for k, c in cmds if k == "install"), cmds)
    check("init: a registry token is flagged; credentials can't be checked", any(".npmrc" in x for x in found["auth"])
          and "Private registry: .npmrc" in out, found["auth"])

    # ---------------------------------------------------------------- the fixture project and its registry
    reg = tmp / "registry"
    for (name, ver), code in REGISTRY.items():
        (reg / name / ver).mkdir(parents=True)
        (reg / name / ver / f"{name}.py").write_text(code)
    (reg / "beta" / "2.0.0" / "advisory.json").write_text(json.dumps({"id": "FAKE-2024-1", "severity": "high",
                                                                      "fixed_in": "2.0.1"}))
    # The files on disk have LF line endings while git converts to CRLF on checkout (core.autocrlf=true, as
    # Git for Windows sets it), so a fresh checkout differs from the files the patches were made from.
    proj = tmp / "proj"
    (proj / "tests").mkdir(parents=True)
    for rel, text in (("pm.py", PM), ("app.py", APP), ("build.py", BUILD), ("tests/test_app.py", TESTS),
                      ("deps.json", json.dumps(DEPS, indent=2) + "\n"), (".gitignore", "vendor_modules/\n__pycache__/\n")):
        (proj / rel).write_bytes(text.encode("utf-8"))
    subprocess.run([PY, "pm.py", "lock"], cwd=proj, check=True, env=dict(ENV, FAKE_REGISTRY=str(reg)))
    g(proj, "init", "-q", "-b", "main")
    g(proj, "config", "user.name", "Test Person")
    g(proj, "config", "user.email", "test@example.com")
    g(proj, "config", "core.autocrlf", "true")
    g(proj, "add", "-A")
    g(proj, "commit", "-q", "-m", "first")
    main_sha = g(proj, "rev-parse", "HEAD").strip()

    out, _ = up("init", "--repo", proj, "--target", "a fake app", "--no-network", "--env", f"FAKE_REGISTRY={reg}")
    ws = run_folder(out)
    check("init: a run under KILLER_UPGRADE_HOME; an unknown ecosystem points at custom",
          str(ws).startswith(str(tmp / "runs")) and "describe yours with" in out.lower(), out)
    out, _ = up("custom", "-w", ws, "--name", "fakepm", "--manifest", "deps.json", "--outdated", '["pm", "outdated"]',
                "--apply", '["pm", "add"]', ok=(2,))
    check("custom: --apply must say where the name and version go", "{name} and {version}" in out, out)
    out, _ = up("custom", "-w", ws, "--name", "fakepm", "--manifest", "deps.json", "--lockfile", "deps.lock",
                "--outdated", json.dumps([PY, "pm.py", "outdated"]), "--apply", json.dumps([PY, "pm.py", "add", "{name}@{version}"]))
    check("custom: the fake ecosystem is registered", "Added ecosystem fakepm" in out, out)
    out, _ = up("outdated", "-w", ws, ok=(2,))
    check("outdated before a baseline is refused", "no baseline yet" in out, out)
    out, _ = up("baseline", "-w", ws, ok=(2,))
    check("baseline without tests is refused", "give the test command" in out, out)
    out, _ = up("baseline", "-w", ws, "--tests", PY, "x.py", ok=(2,))
    check("baseline: a section without -- gets a plain hint", "put -- after --tests" in out, out)

    out, _ = up("baseline", "--help")
    check("baseline: three test runs by default", "default 3" in out, out)
    no_venv = json.dumps([PY, "-c", "raise SystemExit(1)"])   # stands in for a Python without the venv module
    out, _ = up("baseline", "-w", ws, "--venv", no_venv, "--tests", "--", PY, "-V", ok=(2,))
    check("baseline --venv: a Python that can't make a virtual environment stops with a plain message, before "
          "any sandbox", "has no venv module" in out and "Pass a full Python" in out
          and not json.loads((ws / "config.json").read_text(encoding="utf-8"))["sandboxes"], out)
    # Two runs here rather than three, to keep the test quick: the flaky test fails on the second.
    TEST_CMD = [PY, "-m", "unittest", "discover", "-v", "-s", "tests"]
    out, _ = up("baseline", "-w", ws, "--runs", "2", "--install", "--", PY, "pm.py", "install", "--build", "--", PY,
                "build.py", "--tests", "--", *TEST_CMD)
    base = json.loads((ws / "baseline.json").read_text(encoding="utf-8"))
    unit = base["tests"][0]
    check("baseline: the runs read as unittest; the known failure and the flaky test are told apart",
          unit["format"] == "unittest" and len(unit["runs"]) == 2
          and unit["failing"] == ["test_app.TestApp.test_known_broken"] and unit["flaky"] == ["test_app.TestApp.test_flaky"]
          and len(unit["always_pass"]) == 5 and "5 pass every run" in out and "1 flaky (ignored)" in out, (unit, out))
    check("baseline: the install and build pass, and a try is estimated",
          base["ok"] and base["build"][0]["ok"] and "One try takes about" in out, out)

    out, _ = up("plan", "-w", ws, ok=(2,))
    check("plan before outdated is refused", "run outdated first" in out, out)
    out, _ = up("outdated", "-w", ws)
    od = json.loads((ws / "outdated.json").read_text(encoding="utf-8"))
    kinds = {u["name"]: (u["from"], u["to"], u["kind"], u["group"]) for u in od["upgrades"]}
    check("outdated: read from the tool's JSON, past its exit 1 and stderr; patch, minor, major and 0.x major",
          kinds == {"alpha": ("1.0.0", "1.1.0", "minor", "deps"), "beta": ("2.0.0", "2.0.1", "patch", "deps"),
                    "delta": ("0.4.0", "0.4.1", "patch", "deps"), "gamma": ("1.2.0", "1.3.0", "minor", "deps"),
                    "omega": ("1.4.0", "2.0.0", "major", "major"), "zeta": ("0.3.1", "0.4.0", "major", "major"),
                    "left": ("1.0.0", "1.1.0", "minor", "dev"), "lint": ("2.0.0", "2.1.0", "minor", "dev"),
                    "right": ("1.0.0", "1.1.0", "minor", "dev"), "tool": ("1.0.0", "1.0.3", "patch", "dev")}, kinds)
    check("outdated: the advisory, and which upgrade fixes it",
          [(x["package"], x["id"]) for x in od["advisories"]] == [("beta", "FAKE-2024-1")]
          and next(u for u in od["upgrades"] if u["name"] == "beta")["fixes"] == ["FAKE-2024-1"]
          and "1 of them 0.x" in out, out)
    cfg = json.loads((ws / "config.json").read_text(encoding="utf-8"))
    check("outdated: the baseline's sandbox is gone afterwards", not cfg["sandboxes"], cfg["sandboxes"])

    out, _ = up("plan", "-w", ws)
    pl = plan_of(ws)
    names = lambda s: [pl["upgrades"][u]["name"] for u in s["upgrades"]]
    check("plan: patch and minor in one batch, dev in another, each major on its own",
          [(s["id"], s["group"], names(s)) for s in pl["steps"]]
          == [("S1", "deps", ["alpha", "beta", "delta", "gamma"]), ("S2", "dev", ["left", "lint", "right", "tool"]),
              ("S3", "major", ["omega"]), ("S4", "major", ["zeta"])], pl["steps"])
    check("plan: titles that read as commit subjects, and a time estimate",
          pl["steps"][0]["title"] == "Upgrade 4 patch and minor dependencies"
          and pl["steps"][2]["title"] == "Upgrade omega 1.4.0 → 2.0.0" and "bisecting it takes up to" in out, out)

    # ---------- try: fail against the baseline
    out, code = up("try", "-w", ws, "S1", ok=(1,))
    s1 = step(ws, "S1")
    check("try: a broken batch fails on the test that passed at baseline, not the known failure or the flaky one",
          s1["status"] == "fail" and s1["last"]["broken"] == ["test_app.TestApp.test_dates"]
          and "bisect -w" in out, (s1, out))
    out, _ = up("bisect", "-w", ws, "S3", ok=(2,))
    check("bisect: a step that hasn't failed is refused", "whose try failed" in out, out)
    out, _ = up("try", "-w", ws, "S9", ok=(2,))
    check("try: an unknown step is refused", "no step S9" in out, out)

    # ---------- bisect: a single culprit
    out, code = up("bisect", "-w", ws, "S1")
    pl = plan_of(ws)
    b1 = pl["bisects"]["S1"]
    check("bisect: finds the single culprit and says the others pass together",
          "beta 2.0.0 → 2.0.1 breaks test_app.TestApp.test_dates; the other 3 pass together." in out
          and "trial 4:" in out
          and b1["culprits"] == [[next(u for u, x in pl["upgrades"].items() if x["name"] == "beta")]], out)
    check("bisect: a sensible number of trials for one culprit in four (at most 2·log2(4) + 1)",
          b1["trials"] <= 5, b1)
    s5, s6 = step(ws, "S5"), step(ws, "S6")
    check("bisect: splits the batch: the rest accepted from its trial, the culprit its own failing step",
          step(ws, "S1")["status"] == "split" and s5["status"] == "accepted" and names(s5) == ["alpha", "delta", "gamma"]
          and s6["status"] == "fail" and names(s6) == ["beta"] and pl["accepted"] == ["S5"], (s5, s6))
    out, _ = up("try", "-w", ws, "S1", ok=(2,))
    check("try: a split step is refused", "is split" in out, out)

    # ---------- hold a package inside an accepted step: it goes back to pending
    out, _ = up("hold", "-w", ws, "delta", "--reason", "waiting on a fix upstream")
    pl = plan_of(ws)
    check("hold: a package in an accepted step takes the step out of the accepted ones",
          pl["accepted"] == [] and step(ws, "S5")["status"] == "pending" and names(step(ws, "S5")) == ["alpha", "gamma"]
          and not (ws / "steps" / "S5.patch").exists() and "needs trying again" in out, (pl["accepted"], out))
    out, _ = up("hold", "-w", ws, "nosuch", "--reason", "x", ok=(2,))
    check("hold: an unknown package is refused", "no upgrade or step called nosuch" in out, out)
    out, _ = up("try", "-w", ws, "S5")
    check("try: what's left passes and is accepted", step(ws, "S5")["status"] == "accepted"
          and plan_of(ws)["accepted"] == ["S5"] and "S5 passes" in out, out)

    # ---------- bisect: an interacting pair
    out, code = up("try", "-w", ws, "S2", ok=(1,))
    check("try: the dev batch fails on the round trip", step(ws, "S2")["last"]["broken"] == ["test_app.TestApp.test_roundtrip"], out)
    out, code = up("bisect", "-w", ws, "S2")
    pl = plan_of(ws)
    b2 = pl["bisects"]["S2"]
    check("bisect: finds the pair that only breaks together",
          "left 1.0.0 → 1.1.0 and right 1.0.0 → 1.1.0 break test_app.TestApp.test_roundtrip together" in out
          and "the other 2 pass together" in out and len(b2["culprits"]) == 1 and len(b2["culprits"][0]) == 2, out)
    check("bisect: a sensible number of trials for a pair in four (at most 2·log2(4) + 2)", b2["trials"] <= 6, b2)
    s7, s8 = step(ws, "S7"), step(ws, "S8")
    check("bisect: the pair becomes one step; the rest is accepted on top of S5",
          names(s7) == ["lint", "tool"] and s7["status"] == "accepted" and names(s8) == ["left", "right"]
          and pl["accepted"] == ["S5", "S7"], (s7, s8, pl["accepted"]))
    tested = json.loads((ws / "steps" / "S7.files.json").read_text(encoding="utf-8"))
    check("an accepted step keeps the files its try tested; bisect's other passing parts leave nothing behind",
          tested["files"] == ["deps.json", "deps.lock"] and (ws / "steps" / "S7.files" / "deps.lock").is_file()
          and not list((ws / "trials").glob("*.files")), (tested, list((ws / "trials").glob("*.files"))))
    out, _ = up("hold", "-w", ws, "S8", "--reason", "left and right disagree on the v2 format")
    check("hold: a whole step by its id", step(ws, "S8")["status"] == "held", out)

    # ---------- a major that needs a code change: migrators
    out, _ = up("try", "-w", ws, "S3", ok=(1,))
    check("try: the major fails on the renamed function", step(ws, "S3")["last"]["broken"] == ["test_app.TestApp.test_greet"]
          and "send a migrator" in out, out)
    out, _ = up("bisect", "-w", ws, "S3", ok=(2,))
    check("bisect: one upgrade can't be split", "nothing to split" in out, out)
    out, _ = up("sandbox", "-w", ws, "migrate-S3", "--step", "S3")
    mbox = box_of(out)
    fails_txt = (mbox / "scratch" / "failures.txt").read_text(encoding="utf-8")
    check("sandbox --step: the upgrade applied and installed, the failures and commands written for the agent",
          "def hello" in (mbox / "repo" / "vendor_modules" / "omega.py").read_text()
          and "test_app.TestApp.test_greet" in fails_txt and "test_known_broken" in fails_txt
          and "pm.py install" in fails_txt and "Output of the failing run" in fails_txt
          and str(ws) not in out.split("state:")[0], (out, fails_txt[:600]))
    # attempt 1 pins back: refused, and it counts (the sandbox is kept, so the next attempt reuses it)
    deps = json.loads((mbox / "repo" / "deps.json").read_text())
    deps["dependencies"]["omega"] = "1.4.0"
    (mbox / "repo" / "deps.json").write_text(json.dumps(deps, indent=2) + "\n")
    subprocess.run([PY, "pm.py", "lock"], cwd=mbox / "repo", check=True, env=dict(ENV, FAKE_REGISTRY=str(reg)))
    out, _ = up("migrate", "-w", ws, "S3", "--sandbox", "migrate-S3", "--keep", ok=(1,))
    check("migrate: pinning back is refused (the lockfile and the dependencies changed) and counts as an attempt",
          "refused" in out and "lockfile deps.lock" in out and "dependencies in deps.json" in out
          and step(ws, "S3")["attempts"] == 1 and step(ws, "S3")["status"] == "fail" and mbox.exists(), out)
    g(mbox / "repo", "checkout", "-q", "--", "deps.json", "deps.lock")
    app = (mbox / "repo" / "app.py").read_text().replace("omega.greet(name)", "omega.hello(name)")
    (mbox / "repo" / "app.py").write_text(app)
    tst = mbox / "repo" / "tests" / "test_app.py"
    tst.write_text(tst.read_text().replace("    def test_greet(self):", "    # omega 2 calls it hello\n    def test_greet(self):"))
    (mbox / "scratch" / "reply.json").write_text(json.dumps({"summary": "omega 2.0 renamed greet to hello; app.greet calls hello.",
                                                             "files": ["app.py"]}))
    out, _ = up("migrate", "-w", ws, "S3", "--sandbox", "migrate-S3")
    s3 = step(ws, "S3")
    check("migrate: the migrator's sandbox is deleted afterwards (without --keep)", not mbox.exists(), mbox)
    check("migrate: the second attempt passes a fresh try and is accepted with its code change",
          s3["status"] == "accepted" and s3["attempts"] == 2 and s3.get("migration_patch") == "S3.migration-2.patch"
          and "app.py" in (s3.get("patch_files") or []), (s3, out))
    check("migrate: the step's patch holds the upgrade and the migration together",
          {"deps.json", "deps.lock", "app.py", "tests/test_app.py"} <= set(s3["patch_files"]), s3["patch_files"])
    dec = json.loads((ws / "decisions.json").read_text(encoding="utf-8"))
    check("migrate: changed test files become a decision to check", any("tests/test_app.py" in d["text"] for d in dec), dec)
    # S6, the culprit from bisect: two failed attempts hold it
    out, _ = up("sandbox", "-w", ws, "migrate-S6", "--step", "S6")
    mbox = box_of(out)
    out, _ = up("migrate", "-w", ws, "S6", "--sandbox", "migrate-S6", "--keep", ok=(1,))
    check("migrate: a migrator that changed nothing is attempt 1, and there's no reply", "changed nothing" in out
          and "no reply.json" in out and step(ws, "S6")["attempts"] == 1, out)
    (mbox / "repo" / "app.py").write_text((mbox / "repo" / "app.py").read_text() + "\n# tried\n")
    (mbox / "scratch" / "reply.json").write_text("not json at all")
    out, _ = up("migrate", "-w", ws, "S6", "--sandbox", "migrate-S6", ok=(1,))
    s6 = step(ws, "S6")
    check("migrate: two failed attempts hold the step, with the reason; a malformed reply doesn't stop the check",
          s6["status"] == "held" and "two migration attempts failed" in s6["reason"] and "test_dates" in s6["reason"]
          and "no JSON object that parses" in out and "is held" in out, (s6, out))

    # ---------- a 0.x major that passes, and a changelog reader
    out, _ = up("try", "-w", ws, "S4")
    check("try: the 0.x major passes and is accepted", step(ws, "S4")["status"] == "accepted", out)
    out, _ = up("sandbox", "-w", ws, "read-S4", "--step", "S4")
    rbox = box_of(out)
    check("sandbox for an accepted step: its state includes the step", "0.4.0" in (rbox / "repo" / "deps.json").read_text()
          and "failures:     none" in out, out)
    (rbox / "scratch" / "check_label.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nimport app\ngot = app.label('')\nprint('EXPECTED: an empty label is empty')\n"
        "print(f'OBSERVED: {got!r}')\nsys.exit(1 if got else 0)\n")
    (rbox / "scratch" / "reply.json").write_text(json.dumps({"read": ["https://example.invalid/zeta/changelog"], "changes": [
        {"change": "label() wraps an empty string too", "affects": "app.label", "risk": "low",
         "command": [PY, "../scratch/check_label.py"]},
        {"change": "zeta 0.4 drops Python 3.8", "affects": "not used here", "risk": "low", "command": None},
        {"title": "no change text"}]}))
    out, _ = up("notes", "-w", ws, "S4", "--sandbox", "read-S4")
    notes = json.loads((ws / "notes.json").read_text(encoding="utf-8"))["S4"]["changes"]
    check("notes: a reproduction is rerun in a fresh copy; advice without one stays unchecked; empty entries dropped",
          [c["checked"] for c in notes] == ["shows", None] and "[reproduced]" in out and "[unchecked]" in out, (notes, out))
    bad = tmp / "bad.json"
    bad.write_text("{nope")
    out, _ = up("notes", "-w", ws, "S4", bad, ok=(2,))
    check("notes: a reply with no JSON is refused", "no JSON that parses" in out, out)

    # ---------- steps added by hand: an install failure, and a runtime the sandbox can't run
    out, _ = up("add", "-w", ws, "--title", "Upgrade kappa to 9.9.9", "--edit", "deps.json", "no such text", "x", ok=(2,))
    check("add: an edit whose old text isn't there is refused", "doesn't contain" in out, out)
    out, _ = up("add", "-w", ws, "--title", "Upgrade kappa to 9.9.9", "--name", "kappa", "--", PY, "pm.py", "add", "kappa@9.9.9")
    sid = re.search(r"Added (S\d+)", out).group(1)
    out, _ = up("try", "-w", ws, sid, ok=(1,))
    check("try: a command that can't apply the upgrade is install-fail, with the tool's message",
          step(ws, sid)["status"] == "install-fail" and "isn't in the registry" in out, out)
    up("hold", "-w", ws, sid, "--reason", "kappa 9.9.9 doesn't exist")
    out, _ = up("add", "-w", ws, "--title", "Run on Python 99", "--group", "runtime", "--name", "Python", "--from", "3.0",
                "--to", "99.0", "--edit", "build.py", "print('built')", "print('built on 99')",
                "--probe", json.dumps([PY, "--version"]))
    rid = re.search(r"Added (S\d+)", out).group(1)
    out, _ = up("try", "-w", ws, rid, ok=(1,))
    check("try: a runtime step whose toolchain doesn't follow the pin can't be checked",
          step(ws, rid)["status"] == "install-fail" and "doesn't follow" in out, out)
    up("hold", "-w", ws, rid, "--reason", "Python 99 isn't installed here")

    # ---------- plan.json edited badly, and status
    good = (ws / "plan.json").read_text(encoding="utf-8")
    broken = json.loads(good)
    broken["steps"][0]["status"] = "bogus"
    (ws / "plan.json").write_text(json.dumps(broken), encoding="utf-8")
    out, _ = up("status", "-w", ws, ok=(2,))
    check("a hand-edited plan.json with a bad status is refused with what's wrong", "plan.json: S1 has status 'bogus'" in out, out)
    (ws / "plan.json").write_text(good, encoding="utf-8")
    out, _ = up("status", "-w", ws)
    check("status: the accepted steps in landing order, the project unchanged, and the next action",
          "unchanged since init" in out and out.index("S5") < out.index("S7") < out.index("S3 ") < out.index("S4 ")
          and "Next:" in out and "land -w" in out, out)

    # ---------- accepted patches compose in order
    copy = tmp / "compose"
    shutil.copytree(proj, copy, ignore=shutil.ignore_patterns(".git", "vendor_modules"))
    oks = [U.kit.apply_patch(copy, U.kit.read_patch(ws / "steps" / f"{s}.patch"))[0] for s in plan_of(ws)["accepted"]]
    got = json.loads((copy / "deps.json").read_text())
    check("accepted patches apply one after another, in the order they were accepted",
          all(oks) and plan_of(ws)["accepted"] == ["S5", "S7", "S3", "S4"]
          and got["dependencies"] == {"alpha": "1.1.0", "beta": "2.0.0", "delta": "0.4.0", "gamma": "1.3.0",
                                      "omega": "2.0.0", "zeta": "0.4.0"}
          and got["devDependencies"] == {"left": "1.0.0", "lint": "2.1.0", "right": "1.0.0", "tool": "1.0.3"}
          and "omega.hello" in (copy / "app.py").read_text(), (oks, got))

    # ---------- land on a new branch; the user's branch and working tree stay as they were
    status_before = g(proj, "status", "--porcelain")
    out, _ = up("land", "-w", ws)
    land = json.loads((ws / "land.json").read_text(encoding="utf-8"))
    br = land["branch"]
    log = g(proj, "log", "--format=%s", f"{main_sha}..{br}").splitlines()
    check("land: a new killer-upgrade/<date> branch, one commit per accepted step, in order",
          re.fullmatch(r"killer-upgrade/\d{4}-\d{2}-\d{2}", br) and log[::-1]
          == ["Upgrade 2 minor dependencies", "Upgrade 2 patch and minor dev dependencies",
              "Upgrade omega 1.4.0 → 2.0.0", "Upgrade zeta 0.3.1 → 0.4.0"], (br, log))
    check("land: the original branch and the working tree are untouched",
          g(proj, "rev-parse", "main").strip() == main_sha and g(proj, "branch", "--show-current").strip() == "main"
          and g(proj, "status", "--porcelain") == status_before and not g(proj, "worktree", "list").strip().count("\n"),
          g(proj, "worktree", "list"))
    each = [set(g(proj, "show", "--name-only", "--format=", c["sha"]).split()) for c in land["commits"]]
    check("land: each lockfile is committed with its manifest", all({"deps.json", "deps.lock"} <= f for f in each)
          and "app.py" in each[2], each)
    bodies = g(proj, "log", "--format=%B", f"{main_sha}..{br}")
    check("land: plain messages, with what changed and no attribution",
          "- alpha 1.0.0 → 1.1.0" in bodies and "Code changed for the new version: app.py, tests/test_app.py." in bodies
          and not re.search(r"co-authored|claude|anthropic|generated with|agent", bodies, re.I), bodies)
    check("land: the branch passes its final check in a fresh sandbox", land["final"]["verdict"] == "pass"
          and land["final"]["kept"] == 5 and "passes" in out, (land["final"], out))
    out, _ = up("land", "-w", ws, ok=(2,))
    check("land: a second land needs --again", "--again" in out, out)
    (proj / "deps.json").write_bytes((proj / "deps.json").read_bytes() + b"\n")
    branches = g(proj, "branch", "--list")
    out, _ = up("land", "-w", ws, "--again", ok=(2,))
    check("land: refuses when a file the upgrades change has uncommitted edits, before making anything",
          "deps.json changed since the run began" in out and g(proj, "branch", "--list") == branches, out)
    g(proj, "checkout", "-q", "--", "deps.json")
    # killer-goal names its branch, which is the current one: commit there
    g(proj, "checkout", "-q", "-b", "goal")
    out, _ = up("land", "-w", ws, "--again", "--branch", "goal")
    check("land --branch <current>: commits on it in the working tree; main still untouched",
          len(g(proj, "log", "--format=%s", f"{main_sha}..goal").splitlines()) == 4
          and g(proj, "rev-parse", "main").strip() == main_sha and g(proj, "status", "--porcelain") == ""
          and json.loads((proj / "deps.json").read_text())["dependencies"]["omega"] == "2.0.0", out)
    g(proj, "checkout", "-q", "main")

    # ---------- decide, wait, report
    up("decide", "-w", ws, "Held delta 0.4.1 until upstream fixes it")
    up("wait", "-w", ws, "Push the upgrade branch", "--command", "git push -u origin goal")
    out, _ = up("report", "-w", ws)
    rep = (ws / "report.md").read_text(encoding="utf-8")
    summ = json.loads((ws / "summary.json").read_text(encoding="utf-8"))
    check("report: the sections, in order", all(h in rep for h in (
        "## Upgraded", "## Held", "## Failed, and what bisect found", "## Majors", "## Behaviour changes to check",
        "## Security advisories", "## Final check", "## Not checked", "## Decisions to check", "## Waiting for your OK"))
        and rep.index("## Upgraded") < rep.index("## Held") < rep.index("## Final check"), rep)
    check("report: what was held and why, the bisect result, the migration and the advisory left",
          "waiting on a fix upstream" in rep and "two migration attempts failed" in rep
          and "beta 2.0.0 → 2.0.1 breaks" in rep and "passed after a code change (attempt 2)" in rep
          and "renamed greet to hello" in rep and "Test files changed: tests/test_app.py" in rep
          and "Left (1): beta FAKE-2024-1 (fixed in 2.0.1)" in rep and "reproduced in a fresh copy" in rep, rep)
    check("summary.json: the shape killer-goal reads, numbers from the script",
          set(summ) == {"skill", "status", "headline", "report", "branch", "commits", "decisions", "waiting", "numbers"}
          and summ["skill"] == "killer-upgrade" and summ["status"] == "done" and summ["branch"] == "goal"
          and len(summ["commits"]) == 4 and summ["numbers"]["upgrades_found"] == 10
          and summ["numbers"]["upgrades_landed"] == 6 and summ["numbers"]["upgrades_held"] == 6
          and summ["numbers"]["advisories_left"] == 1 and summ["numbers"]["baseline_tests_passing"] == 5
          and summ["numbers"]["bisect_trials"] == b1["trials"] + b2["trials"]
          and any("git push -u origin goal" in w for w in summ["waiting"])
          and any("delta" in d for d in summ["decisions"]), summ)
    check("report: ends with the one-line summary", out.strip().splitlines()[-1].startswith("killer-upgrade: done · 6 of 12 upgrades"),
          out)
    out, _ = up("clean", "-w", ws)
    cfg = json.loads((ws / "config.json").read_text(encoding="utf-8"))
    check("clean: no sandboxes left, and the run's cache is gone",
          not cfg["sandboxes"] and not (tmp / "boxes" / "kup" / cfg["run_id"]).exists(), list((tmp / "boxes").rglob("*"))[:5])
    up("clean", "-w", wd)

    # ---------- a baseline that can't install is blocked
    out, _ = up("init", "--repo", proj, "--slug", "blocked", "--no-network", "--env", f"FAKE_REGISTRY={tmp / 'nowhere'}")
    wb = run_folder(out)
    out, code = up("baseline", "-w", wb, "--install", "--", PY, "pm.py", "install", "--tests", "--", *TEST_CMD, ok=(1,))
    check("baseline: an install that fails blocks the run, with the output", "Blocked: the install failed" in out, out)
    out, _ = up("report", "-w", wb)
    check("report: a blocked run says so", json.loads((wb / "summary.json").read_text())["status"] == "blocked", out)
    up("clean", "-w", wb)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"took {time.monotonic() - T0:.0f} s")
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)

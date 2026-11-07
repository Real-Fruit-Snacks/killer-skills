"""Scenario tests for killer-release's release.py. Run: python -I tests/test_release.py"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True   # importing release.py below mustn't leave __pycache__ in the skill's folder
ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "skills" / "killer-release" / "scripts" / "release.py"
PY = sys.executable
TODAY = date.today().isoformat()
DAYS = lambda: {TODAY, date.today().isoformat()}   # a run that crosses midnight
failures = []
started = time.monotonic()


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


tmp = Path(tempfile.mkdtemp())
FAKEBIN = tmp / "fakebin"
ENV = dict(os.environ, KILLER_RELEASE_HOME=str(tmp / "runs"), KILLER_RELEASE_SANDBOXES=str(tmp / "boxes"),
           GH_TOKEN="test-token-value", PYTHONIOENCODING="utf-8")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "PYTHONPATH"):
    ENV.pop(k, None)
ENV["PATH"] = str(FAKEBIN) + os.pathsep + ENV.get("PATH", "")


TIMES = []


def rel(*args, ok=(0,), env=None):
    t0 = time.monotonic()
    r = subprocess.run([PY, "-B", str(RELEASE), *map(str, args)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env or ENV)
    out = r.stdout + r.stderr
    TIMES.append((time.monotonic() - t0, " ".join(str(x) for x in args[:2] + args[3:5])))
    if r.returncode not in ok:
        raise AssertionError(f"exit {r.returncode} for release.py {' '.join(map(str, args))}:\n{out}")
    return out, r.returncode


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def commit(repo, msg):
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "-m", msg)


def new_repo(path):
    path.mkdir(parents=True)
    g(path, "init", "-q", "-b", "main")
    for k, v in (("user.name", "Test Person"), ("user.email", "test@example.com"), ("commit.gpgsign", "false"),
                 ("tag.gpgsign", "false"), ("core.autocrlf", "false")):
        g(path, "config", k, v)


def run_folder(out):
    return Path(re.search(r"Run folder: (.+)", out).group(1).strip())


def box_of(out):
    return Path(re.search(r"scratch:\s+(.+?)\s{3}", out).group(1).strip()).parent


def refs(repo):
    return g(repo, "for-each-ref", "--format=%(refname) %(objectname)")


def fake_tool(name):
    """A stand-in for gh, npm or twine that leaves a marker if anything runs it."""
    marker = FAKEBIN / f"ran-{name}.txt"
    if os.name == "nt":
        write(FAKEBIN / f"{name}.cmd", f"@echo ran > \"{marker}\"\r\n")
    else:
        write(FAKEBIN / name, f"#!/bin/sh\necho ran > '{marker}'\n")
        (FAKEBIN / name).chmod(0o755)
    return marker


PYPROJECT = """[project]
name = "greet-tool"
version = "0.1.0"
description = "Prints a greeting."
requires-python = ">=3.10"
dependencies = []

[project.scripts]
greet = "greet_tool.cli:main"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"
"""
EXTRA_DEP = '\n[project.optional-dependencies]\ncolor = ["colorama>=0.4"]\n'
CLI = '''import argparse
import sys

from greet_tool import __version__


def main(argv=None):
    p = argparse.ArgumentParser(prog="greet", description="Print a greeting.")
    p.add_argument("--version", action="version", version=f"greet {__version__}")
    p.add_argument("name", nargs="?", default="world")
    a = p.parse_args(argv)
    text = f"Hello, {a.name}"
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
TEST_CLI = ("import os, sys\n"
            "sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))\n"
            "from greet_tool.cli import main\n"
            "assert main(['Ann']) == 0\nprint('ok')\n")
README = """# greet-tool

Prints a greeting.

```sh
pip install greet-tool==0.1.0
greet Ann
```

To pin the version in CI, set `GREET_VERSION=v0.1.0`.
"""
CHANGELOG = """# Changelog

## [Unreleased]

## [0.1.0] - 2026-01-01

- First release: `greet NAME` prints a greeting.

[Unreleased]: https://github.com/example/greet-tool/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/example/greet-tool/releases/tag/v0.1.0
"""
ENTRIES = """## [Unreleased]

### Added

- `--shout` prints the greeting in capitals.
- Export greetings to CSV with `--csv`.

### Fixed

- The greeting now ends with a full stop.
"""
BUILD = r'''"""Build the wheel and a zip by hand, with fixed timestamps: no build backend, no network."""
import base64, hashlib, re, shutil, sys, zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(), re.M).group(1)
dist = root / "dist"
dist.mkdir(exist_ok=True)
src = sorted((root / "src" / "greet_tool").glob("*.py"))


def write(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            zi = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            zi.compress_type, zi.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
            z.writestr(zi, data)


info = f"greet_tool-{version}.dist-info"
files = {f"greet_tool/{p.name}": p.read_bytes().replace(b"\r\n", b"\n") for p in src}
files[f"{info}/METADATA"] = f"Metadata-Version: 2.1\nName: greet-tool\nVersion: {version}\n".encode()
files[f"{info}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: build.py\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
files[f"{info}/entry_points.txt"] = b"[console_scripts]\ngreet = greet_tool.cli:main\n"
digest = lambda d: "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(d).digest()).rstrip(b"=").decode()
files[f"{info}/RECORD"] = ("".join(f"{k},{digest(v)},{len(v)}\n" for k, v in files.items()) + f"{info}/RECORD,,\n").encode()
write(dist / f"greet_tool-{version}-py3-none-any.whl", files)
top = f"greet-tool-{version}"
app = {f"{top}/greet_tool/{p.name}": p.read_bytes().replace(b"\r\n", b"\n") for p in src}
app[f"{top}/greet.py"] = (b"import os, sys\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
                          b"from greet_tool.cli import main\nsys.exit(main())\n")
write(dist / f"{top}.zip", app)
shutil.copy2(root / "install.py", dist / "install.py")
print("built", version)
'''
INSTALL_PY = '''"""Install greet from the zip beside this script into ~/greet-app."""
import os, sys, zipfile
from pathlib import Path

here = Path(__file__).resolve().parent
dest = Path(os.path.expanduser("~")) / "greet-app"
zips = sorted(here.glob("greet-tool-*.zip"))
if not zips:
    sys.exit("no greet-tool zip next to install.py")
zipfile.ZipFile(zips[0]).extractall(dest)
print("installed greet into", dest)
'''

try:
    mod_spec = importlib.util.spec_from_file_location("release_under_test", RELEASE)
    R = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(R)
    V = R.Ver.parse

    # ---------- versions, bumps and attribution, as functions
    nv = lambda cur, kind, pre=None, style="semver": str(R.next_version(V(cur), kind, pre, style))
    check("bump: patch, minor and major from 1.2.3",
          (nv("1.2.3", "patch"), nv("1.2.3", "minor"), nv("1.2.3", "major")) == ("1.2.4", "1.3.0", "2.0.0"))
    check("bump: 0.x moves the same way (the 0.x meaning is a note, not a different number)",
          (nv("0.1.0", "patch"), nv("0.1.0", "minor"), nv("0.1.0", "major")) == ("0.1.1", "0.2.0", "1.0.0"))
    check("bump: pre-releases start at .1 and count up; a plain bump finishes one",
          nv("1.2.3", "minor", "rc") == "1.3.0-rc.1" and nv("1.3.0-rc.1", "minor", "rc") == "1.3.0-rc.2"
          and nv("1.3.0-rc.1", "patch") == "1.3.0" and nv("2.0.0-beta.2", "major") == "2.0.0"
          and nv("1.3.0-rc.1", "minor", "beta.4") == "1.3.0-beta.4")
    check("bump: PEP 440 style for Python projects",
          nv("1.2.3", "minor", "rc", "pep440") == "1.3.0rc1" and nv("1.3.0rc1", "patch", "rc", "pep440") == "1.3.0rc2"
          and nv("1.2.3", "2.0.0") == "2.0.0")
    check("versions: ordering and equality across styles",
          V("1.3.0.dev1") < V("1.3.0a1") < V("1.3.0b1") < V("1.3.0rc1") < V("1.3.0") < V("1.3.1")
          and V("1.3.0-rc.1") == V("1.3.0rc1") and V("v2.0.0") == V("2.0.0") and V("banana") is None)
    vs = lambda out, v: R.version_seen(out, V(v))
    check("version check: the exact version, not one that merely contains it",
          vs("greet 0.2.0\n", "0.2.0") and vs("v0.2.0.", "0.2.0") and not vs("greet 10.2.0", "0.2.0")
          and not vs("0.2.0rc1", "0.2.0") and not vs("0.2.0-rc.1", "0.2.0") and not vs("0.2.01", "0.2.0")
          and vs("tool 0.2.0rc1", "0.2.0-rc.1"))
    hits = lambda text, ok=False: R.attribution_hits(text, "t", ok)
    check("attribution: trailers, robot lines and credits are caught; ordinary words aren't",
          hits("Co-Authored-By: Claude <noreply@anthropic.com>") and hits("\U0001F916 Generated with [Claude Code](https://claude.com/claude-code)")
          and hits("Drafted with the help of an AI assistant") and not hits("API docs are generated with pdoc now.")
          and not hits("Faster start-up on Windows."))
    check("attribution: the names alone count, unless the project is about them; trailers always count",
          hits("Runs in Claude Code") and not hits("Runs in Claude Code", True)
          and hits("Co-Authored-By: Someone <x@example.com>", True))
    try:
        import tomllib
    except ImportError:
        tomllib = None
    sample = PYPROJECT + EXTRA_DEP + ('\n[tool.x]\nlit = \'C:\\path\'\nmulti = [\n  "a", # one\n  { k = 1, v = true },\n]\n'
                                      'dotted.key = "d"\n[[bin]]\nname = "one"\n[[bin]]\nname = "two"\n')
    if tomllib:
        check("TOML: the 3.10 fallback reads what tomllib reads", R._Toml(sample).parse() == tomllib.loads(sample),
              R._Toml(sample).parse())
    else:
        print("SKIP TOML: no tomllib here to compare the fallback with")
    edited, notes = R.changelog_edit(CHANGELOG.replace("## [Unreleased]\n", ENTRIES), V("0.2.0"), TODAY, "v0.2.0")
    check("changelog: Unreleased becomes the dated section, a fresh Unreleased goes above, the compare links move",
          f"## [Unreleased]\n\n## [0.2.0] - {TODAY}\n\n### Added" in edited
          and "[Unreleased]: https://github.com/example/greet-tool/compare/v0.2.0...HEAD" in edited
          and "[0.2.0]: https://github.com/example/greet-tool/compare/v0.1.0...v0.2.0" in edited and not notes, edited)
    edited, _ = R.changelog_edit("# Changes\n\n## 1.3.0 (unreleased)\n\n- x\n\n## 1.2.0 (2026-11-05)\n\n- y\n", V("1.3.0"), TODAY, "v1.3.0")
    check("changelog: an '(unreleased)' heading gets the date in its own style", f"## 1.3.0 ({TODAY})" in edited, edited)

    # ---------- a Python project with a release behind it and work since
    bare = tmp / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    proj = tmp / "greet"
    new_repo(proj)
    g(proj, "remote", "add", "origin", "https://github.com/example/greet-tool.git")
    g(proj, "remote", "set-url", "--push", "origin", str(bare))
    write(proj / ".gitignore", "dist/\n__pycache__/\n")
    write(proj / "pyproject.toml", PYPROJECT)
    write(proj / "src" / "greet_tool" / "__init__.py", '__version__ = "0.1.0"\n')
    write(proj / "src" / "greet_tool" / "cli.py", CLI)
    write(proj / "tests" / "test_cli.py", TEST_CLI)
    write(proj / "README.md", README)
    write(proj / "CHANGELOG.md", CHANGELOG)
    write(proj / "tools" / "build.py", BUILD)
    write(proj / "install.py", INSTALL_PY)
    commit(proj, "First release")
    g(proj, "tag", "-a", "v0.1.0", "-m", "Release 0.1.0")
    g(proj, "push", "-q", "origin", "main", "refs/tags/v0.1.0")
    cli = CLI.replace('    a = p.parse_args(argv)', '    p.add_argument("--shout", action="store_true", help="print it in capitals")\n'
                      '    a = p.parse_args(argv)').replace("    print(text)\n", "    print(text.upper() if a.shout else text)\n")
    write(proj / "src" / "greet_tool" / "cli.py", cli)
    commit(proj, "Add a --shout option that prints the greeting in capitals")
    cli = cli.replace('f"Hello, {a.name}"', 'f"Hello, {a.name}."')
    write(proj / "src" / "greet_tool" / "cli.py", cli)
    commit(proj, "Fix the greeting's punctuation: end with a full stop")
    write(proj / ".github" / "workflows" / "ci.yml", "name: ci\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
          "    steps:\n      - run: python tests/test_cli.py\n")
    commit(proj, "Run the tests on every push")
    write(proj / "src" / "greet_tool" / "cli.py", cli.replace("    return 0\n", '    print("Goodbye for now.")\n    return 0\n', 1))
    write(proj / "pyproject.toml", PYPROJECT + EXTRA_DEP)
    commit(proj, "Add a farewell line after the greeting")
    write(proj / "CHANGELOG.md", CHANGELOG.replace("## [Unreleased]\n", ENTRIES))
    commit(proj, "Changelog: list what the next release has")
    main_sha = g(proj, "rev-parse", "main").strip()

    # ---------- init
    out, _ = rel("init", "--repo", proj, "--target", "a greeting tool", "--test", "python tests/test_cli.py")
    ws = run_folder(out)
    shape = json.loads((ws / "shape.json").read_text(encoding="utf-8"))
    sites = {(s["file"], s["group"]) for s in shape["sites"]}
    check("init: the run folder sits under KILLER_RELEASE_HOME, keyed by project",
          ws.parent.parent == tmp / "runs" and ws.parent.name.startswith("greet-"), ws)
    check("init: finds every version site (manifest, __version__, README pin, changelog) and the example apart",
          {("pyproject.toml", "manifest"), ("src/greet_tool/__init__.py", "mirror"), ("README.md", "pin"),
           ("CHANGELOG.md", "changelog"), ("README.md", "example")} <= sites and len(shape["sites"]) == 5, shape["sites"])
    check("init: the last release, the commits since, the entry point and the source of the version",
          "Last release: v0.1.0" in out and "5 commits since" in out and "greet (greet_tool.cli:main)" in out
          and "Version: 0.1.0, from pyproject.toml" in out, out)
    check("init: with no release workflow, publishing is by hand; pip fits as a smoke recipe",
          "Publish: by hand" in out and "--install pip --name greet-tool" in out
          and "Remote: origin (GitHub example/greet-tool)" in out, out)

    # ---------- versions
    out, code = rel("versions", "-w", ws)
    check("versions: all agree before any bump", code == 0 and "PASS: every version site agrees" in out, out)
    write(proj / "pyproject.toml", (PYPROJECT + EXTRA_DEP).replace('version = "0.1.0"', 'version = "0.2.0"'))
    write(proj / "src" / "greet_tool" / "__init__.py", '__version__ = "0.2.0"\n')
    out, code = rel("versions", "-w", ws, ok=(1,))
    check("versions: a partial bump by hand flags the README pin and the changelog",
          "README.md:6 pins 0.1.0, pyproject.toml says 0.2.0" in out and "CHANGELOG.md" in out.split("FAIL")[1]
          and "__init__.py" not in out.split("FAIL")[1], out)
    g(proj, "checkout", "-q", "--", ".")

    # ---------- changes
    out, _ = rel("changes", "-w", ws)
    changes = json.loads((ws / "changes.json").read_text(encoding="utf-8"))
    check("changes: lists the commits since the tag", len(changes["commits"]) == 5
          and "Add a --shout option" in out and "Run the tests on every push" in out, out)
    check("changes: --help at the tag and at HEAD, each in a sandbox, shows the new option",
          "greet --help: added --shout" in out, out)
    check("changes: the dependency diff and the suggested bump, with the 0.x note",
          "dependency added: colorama (extra color) >=0.4" in out and "Suggested release: minor" in out
          and "on 0.x" in out, out)
    check("changes: the real project's state is untouched", g(proj, "status", "--porcelain").strip() == "",
          g(proj, "status", "--porcelain"))

    # ---------- bump
    out, _ = rel("bump", "-w", ws, "minor")
    check("bump: the preview shows every edit as a diff and the 0.x note, and changes nothing",
          '-version = "0.1.0"' in out and '+version = "0.2.0"' in out and "+pip install greet-tool==0.2.0" in out
          and any(f"+## [0.2.0] - {d}" in out for d in DAYS()) and "Not changed: README.md:10" in out and "on 0.x" in out
          and g(proj, "status", "--porcelain").strip() == "" and g(proj, "branch", "--show-current").strip() == "main", out)
    out, _ = rel("bump", "-w", ws, "banana", ok=(2,))
    check("bump: a kind that isn't one is refused", "patch, minor, major or a version" in out, out)
    out, _ = rel("bump", "-w", ws, "0.0.9", ok=(2,))
    check("bump: a version that isn't newer than the last release is refused", "isn't newer" in out, out)
    out, _ = rel("bump", "-w", ws, "minor", "--apply")
    branch = g(proj, "branch", "--show-current").strip()
    check("bump --apply: a new local branch with one commit, and the user's branch untouched",
          branch == "killer-release/v0.2.0" and g(proj, "rev-parse", "main").strip() == main_sha
          and g(proj, "log", "-1", "--format=%s").strip() == "Release 0.2.0: bump the version"
          and g(proj, "rev-list", "--count", "main..HEAD").strip() == "1", out)
    check("bump --apply: every site moved, the example didn't, the changelog section is dated",
          'version = "0.2.0"' in (proj / "pyproject.toml").read_text() and "pip install greet-tool==0.2.0" in (proj / "README.md").read_text()
          and "GREET_VERSION=v0.1.0" in (proj / "README.md").read_text()
          and any(f"## [0.2.0] - {d}" in (proj / "CHANGELOG.md").read_text() for d in DAYS()), (proj / "README.md").read_text())
    check("bump --apply: the commit has no trailer of any kind", "Co-Authored" not in g(proj, "log", "-1", "--format=%B"))
    out, code = rel("versions", "-w", ws)
    check("versions: all agree on the release branch", code == 0, out)
    out, code = rel("guard", "-w", ws)
    check("guard: nothing outward since the run started", code == 0 and "PASS" in out, out)

    # ---------- changelog-check
    out, _ = rel("changelog-check", "-w", ws)
    cl = json.loads((ws / "changelog.json").read_text(encoding="utf-8"))
    by = {e["text"][:10]: e["id"] for e in cl["entries"]}
    check("changelog-check: maps entries to the commits and options behind them",
          len(cl["entries"]) == 3 and "(mentions --shout)" in out and "shares the words" in out, out)
    check("changelog-check: flags the entry with nothing behind it, and the changes with no entry",
          cl["entries_without_change"] == [by["Export gre"]] and len(cl["commits_without_entry"]) == 1
          and "Add a farewell line after the greeting  -> NO ENTRY" in out
          and "dependency added: colorama (extra color) >=0.4  -> NO ENTRY" in out, out)
    check("changelog-check: CI-only and changelog-only commits count as internal",
          "Run the tests on every push  -> internal (only ci)" in out and "internal (only changelog)" in out, out)

    # ---------- the release reviewer
    out, _ = rel("sandbox", "-w", ws, "review")
    rbox = box_of(out)
    check("sandbox: the reviewer's copy is the release branch, with the history to read",
          (rbox / "repo" / "pyproject.toml").read_text().count('"0.2.0"') == 1 and "git log -p v0.1.0.." in out, out)
    write(rbox / "scratch" / "reply.json", json.dumps({
        "entries": [{"entry": "`--shout` prints the greeting in capitals.", "verdict": "supported", "evidence": "cli.py"},
                    {"entry": "Export greetings to CSV with `--csv`.", "verdict": "unsupported", "evidence": "no --csv anywhere"},
                    {"entry": "The greeting now ends with a full stop.", "verdict": "supported", "evidence": "cli.py"}],
        "missing": [{"change": "a farewell line is printed after the greeting", "evidence": "cli.py",
                     "suggested_entry": "greet prints a farewell line"}]}))
    out, _ = rel("collect", "-w", ws, "--sandbox", "review", "--as", "reviewer")
    check("collect: a reviewer's unsupported entry and missing change become findings; the sandbox goes",
          "1 unsupported" in out and "1 missing change" in out and "Findings F1-F2" in out and not rbox.exists(), out)
    write(tmp / "maybe.json", json.dumps({"entries": [{"entry": "x", "verdict": "maybe"}]}))
    out, _ = rel("collect", "-w", ws, tmp / "maybe.json", "--as", "reviewer", ok=(2,))
    check("collect: an unknown verdict is refused", "supported, unsupported, overstated or unclear" in out, out)
    (tmp / "nojson.txt").write_text("I looked and it seemed fine.")
    out, _ = rel("collect", "-w", ws, tmp / "nojson.txt", "--as", "reviewer", ok=(2,))
    check("collect: a reply with no JSON is refused", "no JSON that parses" in out, out)
    out, _ = rel("collect", "-w", ws, "--sandbox", "nobody", "--as", "installer", ok=(2,))
    check("collect: a sandbox that isn't there is refused", "no sandbox called nobody" in out, out)

    # ---------- AI attribution: refused at commit, caught by the gate
    out, _ = rel("commit", "-w", ws, "-m", "Release 0.2.0: notes\n\nCo-Authored-By: Claude <noreply@anthropic.com>", ok=(2,))
    check("commit: a message with an AI trailer is refused", "credits an AI" in out and "Co-Authored-By" in out, out)
    write(proj / "CHANGELOG.md", (proj / "CHANGELOG.md").read_text().replace(
        "- The greeting now ends with a full stop.\n",
        "- The greeting now ends with a full stop.\n- Thanks to Claude for drafting these notes.\n"))
    out, _ = rel("commit", "-w", ws, "-m", "Release 0.2.0: changelog: thank a helper", "CHANGELOG.md")
    g(proj, "commit", "-q", "--allow-empty", "-m", "Tidy up\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    out, code = rel("check", "-w", ws, "--no-tests", "run in the final check", ok=(1,))
    att = out.split("attribution:")[1].split("\nPASS")[0].split("\nFAIL ")[0]
    check("check: AI attribution in the changelog and in a release commit fails the gate, with the lines",
          "FAIL  attribution" in out and "Thanks to Claude for drafting" in att and "Co-Authored-By: Claude" in att, out)
    check("check: open reviewer findings fail the gate, and nothing built or smoke-tested fails it too",
          "FAIL  review" in out and "F1 unsupported" in out and "FAIL  build" in out and "FAIL  smoke" in out
          and "SKIP  tests: not run: run in the final check" in out, out)
    g(proj, "reset", "-q", "--hard", "HEAD~2")
    out, code = rel("guard", "-w", ws, ok=(1,))
    check("guard: a rewritten release branch is reported", "was rewritten" in out, out)
    rel("guard", "-w", ws, "--accept")
    text = (proj / "CHANGELOG.md").read_text()
    text = text.replace("- Export greetings to CSV with `--csv`.\n", "- A farewell line after the greeting.\n")
    write(proj / "CHANGELOG.md", text)
    rel("commit", "-w", ws, "-m", "Release 0.2.0: changelog: drop the CSV entry, add the farewell line")
    rel("update", "-w", ws, "F1", "--status", "fixed", "--note", "entry removed")
    out, _ = rel("update", "-w", ws, "F2", "--status", "kept", "--note", "covered by the farewell entry")
    check("update: kept findings become decisions", "F2: kept" in out
          and "covered by the farewell entry" in (ws / "decisions.json").read_text(encoding="utf-8"), out)

    # ---------- build the old version: the smoke test catches it
    out, _ = rel("build", "-w", ws, "--rev", "v0.1.0", "--", PY, "tools/build.py")
    check("build: a fresh sandbox at the given commit; names that miss the version are flagged",
          "greet_tool-0.1.0-py3-none-any.whl" in out and "don't mention 0.2.0" in out, out)
    out, code = rel("smoke", "-w", ws, "--install", "archive", "--", "python", "greet-tool-0.1.0/greet.py", "--version", ok=(1,))
    check("smoke: an artifact of the old version fails, saying what it printed",
          "FAIL" in out and "didn't print 0.2.0; it printed 0.1.0" in out, out)

    # ---------- build the release
    out, _ = rel("build", "-w", ws, "--twice", "--artifacts", "dist/*", "--", PY, "tools/build.py")
    build = json.loads((ws / "build.json").read_text(encoding="utf-8"))
    names = sorted(x["name"] for x in build["artifacts"])
    check("build: from the release branch, artifacts and their sums in the run folder",
          names == ["greet-tool-0.2.0.zip", "greet_tool-0.2.0-py3-none-any.whl", "install.py"]
          and build["sha"] == g(proj, "rev-parse", "HEAD").strip()
          and (ws / "SHA256SUMS.txt").read_text().count("  ") == 3 and (ws / "artifacts" / "install.py").exists(), out)
    check("build --twice: the same bytes from two fresh sandboxes", "reproducible" in out, out)
    check("build: the real project has no dist/ (it was built in a sandbox)", not (proj / "dist").exists())

    # ---------- smoke: the core check
    fake_greet = FAKEBIN / ("greet.cmd" if os.name == "nt" else "greet")
    write(fake_greet, "@echo greet 0.2.0\r\n" if os.name == "nt" else "#!/bin/sh\necho greet 0.2.0\n")
    if os.name != "nt":
        fake_greet.chmod(0o755)
    out, code = rel("smoke", "-w", ws, "--install", "archive", "--", "greet", "--version", ok=(1,))
    check("smoke: a command found outside the sandbox fails, even though it printed the right version",
          "outside the sandbox" in out and str(FAKEBIN) in out, out)
    leak = dict(ENV, PYTHONPATH=str(proj / "src"))
    probe = ("import os, sys\nsys.path.insert(0, os.getcwd())\nimport greet_tool\nhere = os.path.abspath(greet_tool.__file__)\n"
             "print('FROM', here)\nsys.exit(1 if os.path.exists('pyproject.toml') or os.path.exists('src') or "
             "here.startswith(os.path.abspath(sys.argv[1])) else 0)\n")
    out, code = rel("smoke", "-w", ws, "--install", "archive", "--cwd", "greet-tool-0.2.0",
                    "--then", f"python -c \"{probe}\" \"{proj.as_posix()}\"",
                    "--then", "python greet.py Ann --shout", "--expect", "HELLO, ANN.",
                    "--", "python", "greet.py", "--version", env=leak)
    check("smoke archive: unpacked in an empty sandbox, network off, it printed the new version and ran",
          code == 0 and "PASS: the release installed from scratch and printed 0.2.0" in out and "network off" in out, out)
    check("smoke archive: no project source in the sandbox, and PYTHONPATH pointing at it didn't leak in",
          "FROM" in out and str(proj) not in out.split("FROM", 1)[1].splitlines()[0], out)
    has_venv = importlib.util.find_spec("venv") is not None and importlib.util.find_spec("ensurepip") is not None
    has_pip = importlib.util.find_spec("pip") is not None
    if has_venv or has_pip:
        out, code = rel("smoke", "-w", ws, "--install", "pip",
                        "--then", "python -c \"import greet_tool, sys; print('AT', greet_tool.__file__)\"",
                        "--then", "greet Ann --shout", "--expect", "HELLO, ANN.", "--", "greet", "--version", env=leak)
        sandboxes = str(tmp / "boxes")
        at = re.search(r"AT (.+)", out)
        check("smoke pip: the wheel installed offline into a fresh environment, and its greet printed 0.2.0",
              code == 0 and "PASS: the release installed from scratch and printed 0.2.0" in out
              and "(installed by the release)" in out, out)
        check("smoke pip: the package imported from the sandbox, not from the project or PYTHONPATH",
              at and Path(at.group(1).strip()).resolve().is_relative_to(Path(sandboxes).resolve()), out)
        if not has_venv:
            check("smoke pip: without venv it says it used pip --target", "pip --target" in out, out)
    else:
        out, code = rel("smoke", "-w", ws, "--install", "pip", "--", "greet", "--version", ok=(2,))
        check("smoke pip: a Python with no venv and no pip stops with a plain error", "has no venv module and no pip" in out, out)
        print("SKIP smoke pip install: this Python has neither venv nor pip (the archive and script recipes cover the core)")
    out, code = rel("smoke", "-w", ws, "--install", "script", "--script", "install.py",
                    "--then", "python {home}/greet-app/greet-tool-0.2.0/greet.py Bo",
                    "--", "python", "{home}/greet-app/greet-tool-0.2.0/greet.py", "--version")
    check("smoke script: the installer from the artifacts writes into the sandbox's home, and the result runs",
          code == 0 and "PASS" in out and "installed greet into" in out
          and not (Path.home() / "greet-app").exists(), out)
    out, _ = rel("smoke", "-w", ws, "--install", "npm", "--", "x", ok=(2,))
    check("smoke: a recipe with no artifact to suit it is refused", "none of the artifacts suits the npm recipe" in out, out)

    # ---------- notes
    out, _ = rel("notes", "-w", ws)
    notes = (ws / "notes.md").read_text(encoding="utf-8")
    check("notes: drafted from the changelog section, with the install line moved to the new version",
          notes.startswith("# greet-tool 0.2.0") and "`--shout` prints the greeting" in notes
          and "pip install greet-tool==0.2.0" in notes and "compare/v0.1.0...v0.2.0" in notes and "CSV" not in notes, notes)
    out, _ = rel("notes", "-w", ws, ok=(2,))
    check("notes: an existing notes.md isn't overwritten without --force", "--force" in out, out)

    # ---------- the gate
    out, code = rel("check", "-w", ws)
    check("check: every gate item passes once the release is right",
          code == 0 and "PASS: " in out and "FAIL" not in out and "PASS  smoke" in out and "PASS  attribution" in out
          and "PASS  tests: python tests/test_cli.py passed in a fresh sandbox" in out
          and "not checked with the network off" in out, out)

    # ---------- publish-plan runs nothing
    markers = [fake_tool(t) for t in ("gh", "npm", "twine", "cargo")]
    before_local, before_remote = refs(proj), refs(bare)
    head_before = g(proj, "rev-parse", "HEAD").strip()
    out, _ = rel("publish-plan", "-w", ws)
    plan = (ws / "publish.md").read_text(encoding="utf-8")
    check("publish-plan: the exact commands, in order, tailored to this project",
          plan.index("git merge --ff-only killer-release/v0.2.0") < plan.index("git tag -a v0.2.0")
          < plan.index("git push origin refs/tags/v0.2.0") < plan.index("twine upload") < plan.index("gh release create v0.2.0")
          and any(str(p) in plan for p in (ws / "artifacts", (ws / "artifacts").resolve())) and "--notes-file" in plan and "Gate:** PASS" in plan, plan)
    check("publish-plan: nothing ran: no tag, no push, no tool started, HEAD where it was",
          refs(proj) == before_local and refs(bare) == before_remote and not any(m.exists() for m in markers)
          and g(proj, "rev-parse", "HEAD").strip() == head_before and "v0.2.0" not in g(proj, "tag")
          and "Nothing was run" in out, out)
    waiting = json.loads((ws / "waiting.json").read_text(encoding="utf-8"))
    check("publish-plan: each step is recorded as waiting, with its command",
          len(waiting) == 5 and all(w["command"] for w in waiting), waiting)

    # ---------- publish: one step at a time, only with the OK
    out, _ = rel("publish", "-w", ws, "--step", "1", ok=(2,))
    check("publish: without --yes nothing runs", "Nothing was run" in out and refs(bare) == before_remote, out)
    out, _ = rel("publish", "-w", ws, "--step", "2", "--yes", ok=(2,))
    check("publish: steps run in order", "isn't done yet" in out and "v0.2.0" not in g(proj, "tag"), out)
    out, _ = rel("publish", "-w", ws, "--step", "1", "--yes")
    check("publish step 1: the release branch lands on main and main is pushed",
          g(bare, "rev-parse", "main").strip() == head_before and g(proj, "branch", "--show-current").strip() == "main", out)
    rel("publish", "-w", ws, "--step", "2", "--yes")
    rel("publish", "-w", ws, "--step", "3", "--yes")
    check("publish steps 2 and 3: the tag is made on the checked commit and pushed",
          g(bare, "rev-parse", "v0.2.0^{commit}").strip() == head_before and not any(m.exists() for m in markers))
    out, code = rel("guard", "-w", ws)
    check("guard: publish's own changes don't count as surprises", code == 0, out)
    for f in FAKEBIN.iterdir():   # the stand-ins have done their job; later steps need the real npm
        if f.stem in ("gh", "npm", "twine", "cargo") or f.name.startswith("ran-"):
            f.unlink()

    # ---------- report
    rel("decide", "-w", ws, "Kept the 0.x minor bump though the farewell line is a behaviour change")
    rel("wait", "-w", ws, "Announce the release", "--command", "gh discussion create")
    out, _ = rel("report", "-w", ws)
    summary = json.loads((ws / "summary.json").read_text(encoding="utf-8"))
    report = (ws / "report.md").read_text(encoding="utf-8")
    check("report: summary.json in the house shape, done, with the numbers from the script",
          summary["skill"] == "killer-release" and summary["status"] == "done" and "0.2.0 is ready" in summary["headline"]
          and summary["branch"] == "killer-release/v0.2.0" and len(summary["commits"]) == 2
          and summary["numbers"]["smoke_passed"] == summary["numbers"]["smoke_total"] >= 2
          and summary["numbers"]["publish_done"] == 3, summary)
    check("report: decisions and the steps still waiting (not the ones done)",
          any("0.x minor bump" in d for d in summary["decisions"]) and any("Kept F2" in d for d in summary["decisions"])
          and len(summary["waiting"]) == 3 and any("twine upload" in w for w in summary["waiting"])
          and any("gh discussion create" in w for w in summary["waiting"]), summary)
    check("report: the sections a reader needs", all(s in report for s in (
        "## Gate", "## Clean installs", "## Artifacts", "## Changes since v0.1.0", "## Review findings",
        "## Decisions to check", "## Waiting for your OK", "## Not checked", "## Published")), report)
    check("report: the last line is the one killer-goal reads",
          out.strip().splitlines()[-1].startswith("killer-release: done · greet-tool 0.2.0 is ready"), out)

    # ---------- a Node.js project whose CI publishes on a tag, and a first release
    node = tmp / "hello-node"
    new_repo(node)
    g(node, "remote", "add", "origin", "https://github.com/example/hello-node.git")
    pkg = {"name": "hello-node", "version": "1.4.0", "bin": {"hello-node": "bin/hello.js"}}
    write(node / "package.json", json.dumps(pkg, indent=2) + "\n")
    write(node / "package-lock.json", json.dumps({"name": "hello-node", "version": "1.4.0", "lockfileVersion": 3,
                                                  "requires": True, "packages": {"": {"name": "hello-node", "version": "1.4.0",
                                                                                     "bin": {"hello-node": "bin/hello.js"}}}},
                                                 indent=2) + "\n")
    write(node / "bin" / "hello.js", "console.log('hello');\n")
    write(node / "CHANGELOG.md", "# Changelog\n\n## Unreleased\n\n- First release.\n")
    write(node / ".github" / "workflows" / "release.yml",
          "name: release\non:\n  push:\n    tags:\n      - 'v*'\njobs:\n  release:\n    runs-on: ubuntu-latest\n"
          "    steps:\n      - run: npm pack\n      - run: npm publish\n      - run: gh release create \"$GITHUB_REF_NAME\" *.tgz\n")
    commit(node, "First version")
    out, _ = rel("init", "--repo", node, "--test", "node --version")
    wn = run_folder(out)
    check("init (node): a first release, CI publishing on a tag push, the lock file as a site",
          "Last release: none, so this is the first release" in out and "Publish: CI does it" in out
          and "publishes to npm" in out and "package-lock.json" in out and "(node, package.json)" in out, out)
    out, _ = rel("changes", "-w", wn, "--no-help")
    check("changes (node): all of history for a first release", "Changes in all of history (first release): 1 commit" in out, out)
    write(node / "bin" / "hello.js", "console.log('changed');\n")
    out, _ = rel("bump", "-w", wn, "patch", "--apply", ok=(2,))
    check("bump --apply: refuses uncommitted changes and makes no branch",
          "uncommitted changes" in out and "killer-release" not in g(node, "branch")
          and (node / "bin" / "hello.js").read_text() == "console.log('changed');\n", out)
    g(node, "checkout", "-q", "--", ".")
    g(node, "branch", "killer-release/v1.4.1")
    out, _ = rel("bump", "-w", wn, "patch", "--apply")
    check("bump --apply: a taken branch name gets -2", g(node, "branch", "--show-current").strip() == "killer-release/v1.4.1-2", out)
    check("bump --apply: an Unreleased section with no earlier one becomes '## 1.4.1 - <date>'",
          any(f"## 1.4.1 - {d}" in (node / "CHANGELOG.md").read_text() for d in DAYS()), (node / "CHANGELOG.md").read_text())
    lock = json.loads((node / "package-lock.json").read_text())
    if shutil.which("npm"):
        check("bump --apply: the lock file is rewritten by npm in a sandbox, and committed",
              lock["version"] == "1.4.1" and lock["packages"][""]["version"] == "1.4.1"
              and "package-lock.json" in g(node, "show", "--name-only", "--format=", "HEAD"), out)
    else:
        print("SKIP lock file update: no npm here")
        check("bump --apply: without npm, the lock file is listed for the user", "package-lock.json still records" in out, out)
    out, _ = rel("publish-plan", "-w", wn)
    plan = (wn / "publish.md").read_text(encoding="utf-8")
    check("publish-plan (node): CI publishes on the tag, so the plan is push the tag and CI does the rest",
          "push the tag, and CI does the rest" in plan and ".github/workflows/release.yml" in plan
          and "npm publish" not in plan and "gh release create" not in plan and "git push origin refs/tags/v1.4.1" in plan
          and "Gate:** not passed" in plan, plan)
    out, _ = rel("init", "--repo", node, "--landing", "check", "--slug", "check-only")
    wc = run_folder(out)
    out, _ = rel("bump", "-w", wc, "minor", "--apply", ok=(2,))
    check("bump --apply: a check-only run changes nothing", "check-only" in out, out)

    # ---------- setup errors
    out, _ = rel("init", "--repo", tmp / "no-such-folder", ok=(2,))
    check("init: a folder that isn't there is refused", "no folder" in out, out)
    plain = tmp / "plain"
    plain.mkdir()
    out, _ = rel("init", "--repo", plain, ok=(2,))
    check("init: a folder that isn't a git repository is refused", "needs a git repository" in out, out)
    out, _ = rel("versions", "-w", tmp / "not-a-run", ok=(2,))
    check("every command: a folder with no run is refused", "run init first" in out, out)
    out, _ = rel("smoke", "-w", wc, "--install", "archive", "--", "x", ok=(2,))
    check("smoke: no build yet is refused", "no successful build" in out, out)

    # ---------- clean
    for w in (ws, wn, wc):
        rel("clean", "-w", w)
    left = [p for p in (tmp / "boxes").rglob("*") if p.is_dir() and p.parent.parent == tmp / "boxes"] if (tmp / "boxes").exists() else []
    check("clean: no sandboxes left behind", not left, left)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if os.environ.get("RELEASE_TEST_TIMES"):
    for secs, what in sorted(TIMES, reverse=True)[:15]:
        print(f"  {secs:6.1f} s  {what}")
print(f"took {time.monotonic() - started:.0f} s")
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)

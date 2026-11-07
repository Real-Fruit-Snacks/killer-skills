"""Scenario tests for killer-docs' script (docs.py). Run: python -I tests/test_docs.py"""
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "skills" / "killer-docs" / "scripts" / "docs.py"
PY = sys.executable
WIN = os.name == "nt"
failures = []
T0 = time.monotonic()


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


tmp = Path(tempfile.mkdtemp())
ENV = dict(os.environ, KILLER_DOCS_HOME=str(tmp / "runs"), KILLER_DOCS_SANDBOXES=str(tmp / "boxes"),
           GH_TOKEN="test-token-value", PYTHONIOENCODING="utf-8")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    ENV.pop(k, None)


def docs(*args, ok=(0,)):
    r = subprocess.run([PY, str(DOCS), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    out = r.stdout + r.stderr
    if r.returncode not in ok:
        raise AssertionError(f"exit {r.returncode} for docs.py {' '.join(map(str, args))}:\n{out}")
    return out, r.returncode


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def run_folder(out):
    return Path(re.search(r"Run folder: (.+)", out).group(1).strip())


def box_path(out):
    return Path(re.search(r"project copy: (.+?)(?: \(at .*\))?$", out, re.M).group(1).strip()).parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


FENCE = "```"
GREET = '''import argparse
import datetime
import sys


def greeting(name):
    return f"Hello, {name}!"


def main(argv=None):
    p = argparse.ArgumentParser(prog="greet", description="Say hello.")
    p.add_argument("--name", default="world", help="who to greet")
    p.add_argument("--time", action="store_true", help="say what time it is")
    p.add_argument("--loud", action="store_true", help="shout it")
    a = p.parse_args(argv)
    msg = greeting(a.name)
    if a.loud:
        msg = msg.upper()
    if a.time:
        msg += f" It is {datetime.datetime.now():%Y-%m-%d %H:%M:%S}."
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
README = f"""# greet

Say hello from the command line.

## Install

Nothing to install. Set up a scratch file first:

{FENCE}sh
echo "setting up" > state.txt
{FENCE}

Check it's there:

{FENCE}console
$ cat state.txt
setting up
{FENCE}

## Use

{FENCE}console
$ python greet.py --name Ada
Hello, Ada!
$ python greet.py --name Ada --time
Hello, Ada! It is 2020-01-01 09:00:00.
{FENCE}

Pass `--shout` to shout.

{FENCE}console
$ python greet.py --name Bob
Hi, Bob!
{FENCE}

<!-- docs: skip -->
{FENCE}sh
rm -rf everything
{FENCE}

To use the web service, set your key:

{FENCE}sh
export GREET_KEY=<your-api-key>
{FENCE}

See the [guide](docs/guide.md), the [Python API](docs/guide.md#python-api), the [missing page](docs/nope.md),
[install](#install) and [setup](#instal).
"""
GUIDE = f"""# Guide

## Python API

{FENCE}python
from greet import greeting
print(greeting("Ada"))
{FENCE}

Prints:

{FENCE}text
Hello, Ada!
{FENCE}

Then, in the same session:

{FENCE}python
print(greeting("Grace"))
{FENCE}

{FENCE}output
Hello, Grace!
{FENCE}

Or at the prompt:

{FENCE}pycon
>>> from greet import greeting
>>> greeting("Ada")
'Hello, Ada!'
{FENCE}

## Colours

{FENCE}sh
python greet.py --colour red
{FENCE}

## Windows

{FENCE}powershell
Set-Content -Path ps.txt -Value "from powershell"
Get-Content ps.txt
{FENCE}

## Moving around

{FENCE}sh
cd docs
export STAGE=two
{FENCE}

{FENCE}console
$ echo "$STAGE in $(basename "$PWD")"
two in docs
{FENCE}
"""
PORT = free_port()
SERVER = f"""# Serving

<!-- docs: background=1 -->
{FENCE}sh
python -m http.server {PORT} --bind 127.0.0.1
{FENCE}

{FENCE}sh
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:{PORT}/').status)"
{FENCE}

<!-- docs: timeout=2 -->
{FENCE}sh
python -c "import time; time.sleep(60)"
{FENCE}
"""
SHOWS = ("import subprocess, sys\nr = subprocess.run([sys.executable, 'greet.py', '--shout'], capture_output=True, text=True)\n"
         "print('EXPECTED: greet --shout shouts, as the README says')\n"
         "print('OBSERVED: exit', r.returncode, r.stderr.strip().splitlines()[-1] if r.stderr.strip() else '')\n"
         "sys.exit(1 if r.returncode else 0)\n")
BEHAVES = ("import subprocess, sys\nr = subprocess.run([sys.executable, 'greet.py', '--name', 'Ada'], capture_output=True, text=True)\n"
           "print('EXPECTED: Hello, Ada!')\nprint('OBSERVED:', r.stdout.strip())\nsys.exit(0 if r.stdout.strip() == 'Hello, Ada!' else 1)\n")

try:
    # ---------- normalisation, straight from the module
    spec = importlib.util.spec_from_file_location("killer_docs_script", DOCS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    c = mod.compare
    check("compare: a timestamp and a duration that differ are ignored",
          c("Done at 2020-01-01 09:00:00 (took 0.5s)", "Done at 2026-11-06 22:33:01 (took 1.25s)")["ok"])
    check("compare: a real difference isn't", not c("Hello, Ada!", "Hi, Ada!")["ok"]
          and c("Hello, Ada!", "Hi, Ada!")["diff"])
    check("compare: version numbers and git ids are ignored, but plain numbers aren't",
          c("greet 1.2.3, commit abc1234", "greet 1.4.0, commit 9f8e7d6")["ok"] and not c("mean: 3.0", "mean: 6.0")["ok"])
    check("compare: line endings, trailing spaces, runs of spaces and path separators",
          c("out\\file.txt  \r\nnext   line\r\n", "out/file.txt\nnext line\n")["ok"])
    r = c("first\n...\nlast", "first\nmiddle\nlast")
    check("compare: ... in the shown output stands for lines left out", r["ok"], r)
    r = c("Hello, Ada!", "warning: old config\nHello, Ada!")
    check("compare: shown lines found in order count as a partial match, and say so",
          r["ok"] and r["match"] == "partial" and "1 more line" in r["note"], r)
    sh = lambda code: {"kind": "shell", "dialect": "sh", "commands": None, "code": code, "ann": {}}
    check("safety: commands that change the system are refused",
          all(mod.system_reason(sh(x)) for x in ("sudo make install", "brew install jq", "docker run -it app",
                                                 "rm -rf /", "setx PATH foo", "cp tool /usr/local/bin/", "git push origin main"))
          and not any(mod.system_reason(sh(x)) for x in ("rm -rf build", "echo hi > /dev/null", "npm start", "ls /usr/bin")),
          [(x, mod.system_reason(sh(x))) for x in ("sudo make install", "rm -rf /", "rm -rf build", "npm start")])
    top = Path(tempfile.mkdtemp())
    net = lambda code: mod.network_reason(sh(code), top)
    check("network: installs from a registry need it; local installs and this machine don't",
          all(net(x) for x in ("pip install requests", "python -m pip install -r requirements.txt", "npm install lodash",
                               "curl -fsSL https://get.example.io/install.sh | sh", "git clone https://github.com/a/b"))
          and not any(net(x) for x in ("pip install .", "pip install -e .[dev]", "curl http://localhost:8000/health",
                                       "npm install ./local-pkg", "python greet.py")),
          [(x, net(x)) for x in ("pip install requests", "pip install -e .[dev]", "curl http://localhost:8000/health")])
    shutil.rmtree(top, ignore_errors=True)
    check("not checked, not failed: a missing tool, and pip in a sandbox that can't install",
          mod.missing_tool("x.sh: line 3: make: command not found") == "make"
          and mod.missing_tool("pip isn't available in this sandbox: its Python has no private environment") == "pip")

    # ---------- a project with docs
    proj = tmp / "greet"
    (proj / "docs").mkdir(parents=True)
    g(proj, "init", "-q", "-b", "main")
    g(proj, "config", "user.name", "Test Person")
    g(proj, "config", "user.email", "test@example.com")
    g(proj, "config", "commit.gpgsign", "false")
    (proj / "greet.py").write_text(GREET, encoding="utf-8")
    (proj / "pyproject.toml").write_text('[project]\nname = "greet"\nversion = "1.0.0"\n\n[project.scripts]\n'
                                         'greet = "greet:main"\n', encoding="utf-8")
    (proj / "README.md").write_text(README, encoding="utf-8")
    (proj / "docs" / "guide.md").write_text(GUIDE, encoding="utf-8")
    (proj / "docs" / "server.md").write_text(SERVER, encoding="utf-8")
    (proj / "tests").mkdir()
    (proj / "tests" / "fixture.md").write_text(f"{FENCE}sh\nexit 1\n{FENCE}\n", encoding="utf-8")
    g(proj, "add", "-A")
    g(proj, "commit", "-q", "-m", "first")

    out, code = docs("init", "--repo", proj, "--scope", "everything", ok=(2,))
    check("init: an unknown scope fails and names the choices", "all, start, cli" in out, out)
    out, _ = docs("init", "--repo", proj, "--target", "a greeter")
    ws = run_folder(out)
    cfg = jload(ws / "config.json")
    check("init: the run folder sits under KILLER_DOCS_HOME", ws.parent.parent == tmp / "runs", ws)
    check("init: lists the docs, README first, and leaves test fixtures out",
          [d[0] for d in cfg["docs_init"]] == ["README.md", "docs/guide.md", "docs/server.md"], cfg["docs_init"])
    check("init: finds the command from pyproject.toml", [e["name"] for e in cfg["entry_points"]] == ["greet"]
          and "Commands the docs may describe: greet (pyproject.toml scripts)" in out, out)
    check("init: picks a Python for the sandboxes", cfg["python"]["path"] and "Python for the sandboxes" in out, out)
    sh_ok = bool(cfg["tools"].get("bash") or cfg["tools"].get("sh"))
    if not sh_ok:
        print("SKIP the sh checks below: no sh on this machine")

    # ---------- extract
    out, _ = docs("extract", "-w", ws)
    data = jload(ws / "blocks.json")
    bl = {b["id"]: b for b in data["blocks"]}
    at = lambda doc, line: next(b for b in data["blocks"] if b["doc"] == doc and b["line"] == line)
    rl = README.splitlines()
    gl = GUIDE.splitlines()
    rline = lambda s: next(i for i, x in enumerate(rl, 1) if x.startswith(FENCE + s) or x == s)
    b_setup = at("README.md", rl.index(FENCE + "sh") + 1)
    b_cat = at("README.md", rl.index(FENCE + "console") + 1)
    use = [b for b in data["blocks"] if b["doc"] == "README.md" and b["heading"][-1:] == ["Use"] and b["kind"] == "shell"]
    b_skip = next(b for b in data["blocks"] if b["code"] == "rm -rf everything")
    b_ph = next(b for b in data["blocks"] if "GREET_KEY" in b["code"])
    b_py1 = next(b for b in data["blocks"] if b["doc"] == "docs/guide.md" and b["kind"] == "code" and "Ada" in b["code"])
    b_py2 = next(b for b in data["blocks"] if b["kind"] == "code" and "Grace" in b["code"])
    b_out = next(b for b in data["blocks"] if b["kind"] == "output" and b["code"] == "Hello, Ada!")
    b_pycon = next(b for b in data["blocks"] if b["kind"] == "doctest")
    b_colour = next(b for b in data["blocks"] if "--colour" in b["code"])
    b_ps = next(b for b in data["blocks"] if b["lang"] == "powershell")
    b_cd = next(b for b in data["blocks"] if b["code"].startswith("cd docs"))
    b_echo = next(b for b in data["blocks"] if "STAGE in" in b["code"])
    check("extract: sh blocks are shell blocks for sh", b_setup["kind"] == "shell" and b_setup["dialect"] == "sh"
          and not b_setup["commands"], b_setup)
    check("extract: a console block splits $ commands from the output each shows",
          b_cat["commands"] == [{"cmd": "cat state.txt", "at": 0, "out": "setting up"}], b_cat["commands"])
    check("extract: two commands, each with its own output",
          [c["cmd"] for c in use[0]["commands"]] == ["python greet.py --name Ada", "python greet.py --name Ada --time"]
          and use[0]["commands"][1]["out"] == "Hello, Ada! It is 2020-01-01 09:00:00.", use[0]["commands"])
    check("extract: the heading path is recorded", use[0]["heading"] == ["greet", "Use"], use[0]["heading"])
    check("extract: <!-- docs: skip --> is honoured", b_skip["skip"]["why"] == "marked", b_skip["skip"])
    check("extract: a placeholder isn't run", b_ph["skip"]["why"] == "placeholder" and "<your-api-key>" in b_ph["skip"]["reason"],
          b_ph["skip"])
    check("extract: a Python block is code, and the text block after 'Prints:' is its output",
          b_py1["runner"] == "python" and b_py1["shown"] == "Hello, Ada!" and b_out["output_of"] == b_py1["line"], b_py1)
    check("extract: an output-tagged block right after code is its output", b_py2["shown"] == "Hello, Grace!", b_py2)
    check("extract: >>> prompts make a doctest", b_pycon["kind"] == "doctest", b_pycon)
    check("extract: a PowerShell block under a Windows heading runs only on Windows",
          (b_ps["skip"] is None) if WIN else (b_ps["skip"]["why"] == "platform"), b_ps["skip"])
    check("extract: the table says what runs and why the rest don't",
          "won't run: marked `docs: skip` in the doc" in out and "won't run: has a placeholder" in out, out)

    # ---------- the lead's skip (the side-effect check)
    out, _ = docs("skip", "-w", ws, b_setup["id"], "--reason", "writes a file the real project shouldn't get")
    b2 = next(b for b in jload(ws / "blocks.json")["blocks"] if b["id"] == b_setup["id"])
    check("skip: the lead can leave a block out, with the reason", b2["skip"] == {"why": "lead", "reason": "writes a file the real project shouldn't get"}, b2["skip"])
    out, code = docs("skip", "-w", ws, b_setup["id"], ok=(2,))
    check("skip: a reason is required", "--reason" in out, out)
    docs("skip", "-w", ws, b_setup["id"], "--undo")
    check("skip --undo runs it again", next(b for b in jload(ws / "blocks.json")["blocks"] if b["id"] == b_setup["id"])["skip"] is None)

    # ---------- run-blocks
    docs("guard", "-w", ws)
    out, code = docs("run-blocks", "-w", ws, ok=(1,))
    p1 = jload(ws / "passes" / "blocks-1.json")
    res = {r["id"]: r for r in p1["results"]}
    rs = lambda b: res[b["id"]]["status"]
    if sh_ok:
        check("run-blocks: a working sh block passes", rs(b_setup) == "passed", res[b_setup["id"]])
        check("run-blocks: state carries between blocks: the file made by one block is there for the next",
              rs(b_cat) == "passed" and res[b_cat["id"]]["commands"][0]["match"] == "exact", res[b_cat["id"]])
        check("run-blocks: a timestamp in the shown output doesn't make it differ",
              rs(use[0]) == "passed" and [u["match"] for u in res[use[0]["id"]]["commands"]] == ["exact", "exact"],
              res[use[0]["id"]])
        check("run-blocks: output that really differs is reported, with a diff",
              rs(use[1]) == "differs" and any("Hi, Bob!" in x for x in res[use[1]["id"]]["commands"][0]["diff"]),
              res[use[1]["id"]])
        check("run-blocks: a failing command fails, with its exit code", rs(b_colour) == "failed"
              and res[b_colour["id"]]["exit"] == 2, res[b_colour["id"]])
        check("run-blocks: the working folder and exported variables carry on, as in one terminal",
              rs(b_echo) == "passed" and res[b_echo["id"]]["commands"][0]["match"] == "exact"
              and any("started in docs/" in n for n in res[b_echo["id"]]["notes"]), res[b_echo["id"]])
    check("run-blocks: a Python block runs, and its output matches", rs(b_py1) == "passed"
          and res[b_py1["id"]]["match"] == "exact", res[b_py1["id"]])
    check("run-blocks: a Python block that continues an earlier one runs after it, and says so",
          rs(b_py2) == "passed" and any("as in one session" in n for n in res[b_py2["id"]]["notes"]), res[b_py2["id"]])
    check("run-blocks: the doctest passes", rs(b_pycon) == "passed", res[b_pycon["id"]])
    check("run-blocks: skipped blocks are listed with their reason, never counted as passing",
          rs(b_skip) == "skipped" and res[b_ph["id"]]["skip"]["why"] == "placeholder"
          and "Not checked:" in out and "with placeholders" in out, out)
    if WIN:
        check("run-blocks: the PowerShell block runs on Windows", rs(b_ps) == "passed", res[b_ps["id"]])
    else:
        check("run-blocks: the PowerShell block is listed as for another system", res[b_ps["id"]]["skip"]["why"] == "platform")
    srv = [r for r in p1["results"] if r["doc"] == "docs/server.md"]
    if sh_ok:
        check("run-blocks: a background server serves the next block, and is stopped afterwards",
              srv[0]["status"] == "passed" and srv[0].get("background") and srv[1]["status"] == "passed"
              and "200" in srv[1]["output"], srv[:2])
        check("run-blocks: a block that never finishes times out, with the hint", srv[2]["status"] == "timed-out"
              and srv[2]["secs"] < 30 and any("interactive or a server" in n for n in srv[2]["notes"]), srv[2])
        def listening():
            with socket.socket() as s:
                s.settimeout(1)
                return s.connect_ex(("127.0.0.1", PORT)) == 0
        deadline = time.monotonic() + 5   # a stopped server can take a moment to let go of its port
        while listening() and time.monotonic() < deadline:
            time.sleep(0.2)
        check("run-blocks: nothing is left listening", not listening())
    check("run-blocks: the real project is untouched", not (proj / "state.txt").exists()
          and g(proj, "status", "--porcelain").strip() == "", g(proj, "status", "--porcelain"))
    out2, _ = docs("guard", "-w", ws)
    check("guard: the project's git status is unchanged", "unchanged" in out2, out2)
    check("run-blocks: its sandboxes are deleted", not jload(ws / "config.json")["sandboxes"], jload(ws / "config.json")["sandboxes"])
    fs = jload(ws / "findings.json")["findings"]
    if sh_ok:
        f_bob = next(f for f in fs if f.get("block", {}).get("key") == use[1]["key"])
        f_col = next(f for f in fs if f.get("block", {}).get("key") == b_colour["key"])
        check("run-blocks: a differing block is a proven finding, stale, with its reproduction",
              f_bob["status"] == "proven" and f_bob["kind"] == "stale" and f_bob["repro"]["type"] == "blocks", f_bob)
        check("run-blocks: a failing block is a proven finding", f_col["status"] == "proven" and f_col["kind"] == "wrong", f_col)
        out, code = docs("repro", "-w", ws, f_col["id"], ok=(1,))
        check("repro: reruns the doc's blocks up to that one in a fresh copy", "failed (exit 2)" in out, out)

    # ---------- help-drift
    out, code = docs("help-drift", "-w", ws, ok=(1,))
    h = jload(ws / "passes" / "help-1.json")
    names = lambda lst: sorted(m["name"] for m in lst)
    check("help-drift: reads the command's --help", h["entries"][0]["ok"] and ["--loud"] in h["entries"][0]["groups"], h["entries"])
    check("help-drift: options the docs mention that --help lacks (high)", names(h["missing"]) == ["--colour", "--shout"], h["missing"])
    check("help-drift: options --help has that no doc mentions (low)", names(h["undocumented"]) == ["--loud"], h["undocumented"])
    fs = jload(ws / "findings.json")["findings"]
    drift = [f for f in fs if f["kind"] == "drift"]
    check("help-drift: findings with severity by direction",
          sorted((f["severity"], f["title"].split()[4] if "mention" in f["title"] else "") for f in drift if f["severity"] == "high")
          and {f["severity"] for f in drift} == {"high", "low"} and len(drift) == 3, [(f["severity"], f["title"]) for f in drift])

    # ---------- links
    out, code = docs("links", "-w", ws, ok=(1,))
    lk = jload(ws / "passes" / "links-1.json")
    check("links: a missing file and a missing anchor are broken; good links and anchors aren't",
          sorted((b["url"], b["kind"]) for b in lk["broken"]) == [("#instal", "anchor"), ("docs/nope.md", "link")], lk["broken"])
    check("links: web links aren't fetched with the network off", "not checked (network off)" in out, out)

    # ---------- journeys and a newcomer's walk
    out, code = docs("journey", "-w", ws, "add", "install", "--goal", "Install greet and greet someone",
                     "--docs", "README.md", "docs/nope.md", ok=(2,))
    check("journey: a doc that doesn't exist is refused", "no such file" in out, out)
    docs("journey", "-w", ws, "add", "install", "--goal", "Install greet and greet someone",
         "--docs", "README.md", "docs/guide.md", "--check", "python greet.py --name Ada")
    out, _ = docs("sandbox", "-w", ws, "walk-install-1")
    box = box_path(out)
    check("sandbox: a private Python for the agent", "private virtual environment" in out or "pip` can't install" in out, out)
    (box / "scratch" / "shout.py").write_text(SHOWS, encoding="utf-8")
    (box / "scratch" / "fine.py").write_text(BEHAVES, encoding="utf-8")
    (box / "repo" / "greet.py").write_text(GREET.replace("Hello", "Hullo"), encoding="utf-8")
    rl2 = README.splitlines()
    use_line = rl2.index("## Use") + 3
    shout_line = next(i for i, x in enumerate(rl2, 1) if "--shout" in x)
    (box / "scratch" / "reply.json").write_text(json.dumps({
        "journey": "install", "outcome": "reached-with-help",
        "stuck_at": None,
        "steps": [{"doc": "README.md", "line": use_line, "action": "ran python greet.py --name Ada", "result": "Hello, Ada!", "ok": True},
                  {"doc": str(box / "repo" / "README.md"), "line": shout_line, "action": "ran greet --shout",
                   "result": "error: unrecognized arguments", "ok": False, "guess": "that --loud is what it meant",
                   "workaround": "used --loud"}],
        "guesses": ["that --loud is what --shout meant"],
        "findings": [
            {"title": "--shout doesn't exist", "kind": "wrong", "severity": "high", "doc": "README.md", "line": shout_line,
             "told": "Pass --shout to shout", "happened": "error: unrecognized arguments: --shout",
             "command": [PY, "../scratch/shout.py"]},
            {"title": "the API example looked wrong to me", "kind": "wrong", "severity": "low", "doc": "docs/guide.md",
             "line": gl.index("## Python API") + 3, "told": "print(greeting('Ada'))", "happened": "it seemed off",
             "command": [PY, "../scratch/fine.py"]},
            {"title": "which Python?", "kind": "missing", "severity": "medium", "doc": "README.md", "line": use_line,
             "told": "python greet.py", "happened": "the README never says which Python version it needs", "command": None},
        ],
        "read": ["README.md", "docs/guide.md", "greet.py"]}), encoding="utf-8")
    out, _ = docs("collect", "-w", ws, "--sandbox", "walk-install-1", "--journey", "install")
    walks = jload(ws / "walks.json")
    fs = {f["title"]: f for f in jload(ws / "findings.json")["findings"]}
    check("collect: a reproduction that shows in a fresh copy makes the finding proven",
          fs["--shout doesn't exist"]["status"] == "proven" and fs["--shout doesn't exist"]["verified"] == "shows", fs["--shout doesn't exist"])
    check("collect: one that doesn't show leaves it reported, with a note",
          fs["the API example looked wrong to me"]["status"] == "candidate"
          and "didn't show the problem" in fs["the API example looked wrong to me"]["notes"][0], fs["the API example looked wrong to me"])
    check("collect: a finding with no reproduction is kept as reported", fs["which Python?"]["status"] == "candidate"
          and fs["which Python?"]["heading"] == "greet > Use", fs["which Python?"])
    check("collect: sandbox paths in the reply become project paths",
          json.loads((ws / "walks" / "install-1.json").read_text(encoding="utf-8"))["steps"][1]["doc"] == "README.md")
    w1 = walks[0]
    check("collect: the walk is recorded with its outcome, guesses and the goal check run in its sandbox",
          w1["outcome"] == "reached-with-help" and w1["check"]["ok"] and w1["guesses"] and w1["walk"] == 1, w1)
    check("collect: it notices project files the newcomer changed, and files it read outside its list",
          w1["changed_files"] == ["greet.py"] and w1["read_outside"] == ["greet.py"]
          and "changed project files" in out and "outside its list" in out, (w1, out))
    check("collect: the sandbox is deleted, its scratch files kept without the reply",
          not box.exists() and (ws / "scratch" / "walk-install-1" / "shout.py").exists()
          and not (ws / "scratch" / "walk-install-1" / "reply.json").exists())

    # ---------- replies that are missing or malformed
    out, _ = docs("sandbox", "-w", ws, "walk-bad")
    bad = box_path(out)
    out, code = docs("collect", "-w", ws, "--sandbox", "walk-bad", "--journey", "install", ok=(2,))
    check("collect: no reply.json is an error, and the sandbox is kept", "no reply at" in out and bad.exists(), out)
    (bad / "scratch" / "reply.json").write_text("I got stuck, sorry {", encoding="utf-8")
    out, code = docs("collect", "-w", ws, "--sandbox", "walk-bad", "--journey", "install", ok=(2,))
    check("collect: a reply with no JSON is an error", "no JSON that parses" in out and bad.exists(), out)
    (bad / "scratch" / "reply.json").write_text(json.dumps({"outcome": "great", "steps": []}), encoding="utf-8")
    out, code = docs("collect", "-w", ws, "--sandbox", "walk-bad", "--journey", "install", ok=(2,))
    check("collect: an unknown outcome is refused", "reached, reached-with-help or stuck" in out, out)
    out, code = docs("collect", "-w", ws, "--sandbox", "walk-bad", ok=(2,))
    check("collect: a newcomer's reply needs its journey", "--journey" in out, out)
    docs("clean", "-w", ws)

    # ---------- fixing: a branch, commits, recheck
    out, _ = docs("branch", "-w", ws)
    branch = g(proj, "branch", "--show-current").strip()
    check("branch: a new local killer-docs/<date> branch", re.fullmatch(r"killer-docs/\d{4}-\d{2}-\d{2}", branch), branch)
    fixed_readme = README.replace("Hi, Bob!", "Hello, Bob!").replace("docs/nope.md", "docs/guide.md").replace("#instal)", "#install)")
    (proj / "README.md").write_text(fixed_readme, encoding="utf-8")
    (proj / "docs" / "guide.md").write_text(GUIDE.replace("--colour red", "--name Ada"), encoding="utf-8")
    out, code = docs("commit", "-w", ws, "-m", "Fix the README\n\nCo-Authored-By: Claude <noreply@anthropic.com>", "--",
                     "README.md", ok=(2,))
    check("commit: a message with an attribution trailer is refused", "attribution" in out, out)
    out, code = docs("commit", "-w", ws, "-m", "Fix greet", "--", "greet.py", ok=(2,))
    check("commit: code isn't a doc fix", "isn't a doc" in out, out)
    ids = [f["id"] for f in jload(ws / "findings.json")["findings"] if f.get("block")]
    out, _ = docs("commit", "-w", ws, "-m", "Fix the README's example output and links, and the guide's colour example",
                  "--findings", *ids, "--", "README.md", "docs/guide.md")
    log = g(proj, "log", "--format=%s%n%b", "-1")
    check("commit: commits only those docs, on the branch, with the message as given",
          "Fix the README's example output" in log and "Co-Authored" not in log
          and g(proj, "show", "--name-only", "--format=", "HEAD").split() == ["README.md", "docs/guide.md"], log)
    out, code = docs("recheck", "-w", ws, ok=(1,))
    fs = {f["id"]: f for f in jload(ws / "findings.json")["findings"]}
    if sh_ok:
        check("recheck: the fixed blocks pass now, block by block against the first run",
              "differs -> passed" in out and "failed -> passed (the block changed)" in out, out)
        check("recheck: their findings are fixed, by a rerun", fs[f_bob["id"]]["status"] == "fixed"
              and fs[f_col["id"]]["fixed_by"]["how"] == "rerun", (fs[f_bob["id"]], fs[f_col["id"]]))
    lfs = [f for f in fs.values() if f["kind"] == "broken-link"]
    check("recheck: the fixed links are fixed", lfs and all(f["status"] == "fixed" for f in lfs), lfs)
    dr = {f["title"]: f["status"] for f in fs.values() if f["kind"] == "drift"}
    check("recheck: rerunning help-drift doesn't add the same findings twice",
          len([f for f in fs.values() if f["kind"] == "drift"]) == 3, dr)
    check("recheck: help drift fixed where the docs changed, still open where they didn't",
          [s for t, s in dr.items() if "--colour" in t] == ["fixed"] and [s for t, s in dr.items() if "--shout" in t] == ["proven"]
          and [s for t, s in dr.items() if "--loud" in t] == ["proven"], dr)
    shout = next(f for f in fs.values() if f["title"] == "--shout doesn't exist")
    check("recheck: a newcomer's reproduction that still shows stays open", shout["status"] == "proven" and shout["recheck"] == "shows", shout)
    check("recheck: the real project is still as committed", g(proj, "status", "--porcelain").strip() == "",
          g(proj, "status", "--porcelain"))

    # ---------- the second walk: a fresh newcomer
    out, _ = docs("sandbox", "-w", ws, "walk-install-1")
    (box_path(out) / "scratch" / "reply.json").write_text(json.dumps({"journey": "install", "outcome": "reached", "steps": [],
                                                                       "findings": []}), encoding="utf-8")
    out, code = docs("collect", "-w", ws, "--sandbox", "walk-install-1", "--journey", "install", "--walk", "2", ok=(2,))
    check("collect: a second walk can't come from the first walk's sandbox", "fresh newcomer" in out, out)
    docs("clean", "-w", ws)
    out, _ = docs("sandbox", "-w", ws, "walk-install-2")
    fl = fixed_readme.splitlines()
    (box_path(out) / "scratch" / "reply.json").write_text(json.dumps({
        "journey": "install", "outcome": "reached",
        "steps": [{"doc": "README.md", "line": fl.index("## Use") + 3, "action": "ran python greet.py --name Ada",
                   "result": "Hello, Ada!", "ok": True}],
        "findings": [], "read": ["README.md"]}), encoding="utf-8")
    out, _ = docs("collect", "-w", ws, "--sandbox", "walk-install-2", "--journey", "install")
    fs = {f["title"]: f for f in jload(ws / "findings.json")["findings"]}
    check("collect: walk 2 fixes a first-walk finding whose step the new newcomer passed cleanly",
          fs["which Python?"]["status"] == "fixed" and fs["which Python?"]["fixed_by"]["how"] == "walk", fs["which Python?"])
    check("collect: a finding in a part walk 2 never reached stays open, with a note",
          fs["the API example looked wrong to me"]["status"] == "candidate"
          and any("didn't reach this part" in n for n in fs["the API example looked wrong to me"]["notes"]),
          fs["the API example looked wrong to me"])
    check("collect: a proven finding isn't fixed by a walk", fs["--shout doesn't exist"]["status"] == "proven")

    # ---------- findings by hand, decisions, report
    docs("add", "-w", ws, "--title", "the install section is long", "--kind", "unclear", "--severity", "low",
         "--doc", "README.md", "--line", "5", "--source", "user")
    out, _ = docs("update", "-w", ws, "D1", "--status", "dismissed", ok=(0, 2))
    check("update: dismissing needs a reason", "--note" in out, out)
    lone = next(f for f in jload(ws / "findings.json")["findings"] if f["title"] == "the install section is long")
    docs("update", "-w", ws, lone["id"], "--status", "dismissed", "--note", "it's three lines")
    docs("update", "-w", ws, shout["id"], "--kind", "code", "--note", "the docs describe --shout; the code should have it")
    docs("decide", "-w", ws, "Left --loud undocumented: it may be on its way out")
    docs("wait", "-w", ws, "Push the fix branch", "--command", f"git push -u origin {branch}")
    out, _ = docs("list", "-w", ws)
    check("list: findings by status and severity", out.index("proven") < out.index("candidate") < out.index("fixed") < out.index("dismissed"), out)
    out, _ = docs("report", "-w", ws)
    report = (ws / "report.md").read_text(encoding="utf-8")
    summary = jload(ws / "summary.json")
    check("report: journeys, blocks, findings, help, links and what wasn't checked, in order",
          report.index("## Journeys") < report.index("## Blocks") < report.index("## Findings")
          < report.index("## Help text") < report.index("## Links") < report.index("## What wasn't checked"), report)
    check("report: each journey's walks, first and latest, with the goal check",
          re.search(r"\| install \| .* \| reached-with-help, goal check passed.* \| reached, goal check passed", report), report)
    check("report: skipped blocks are listed with their reasons",
          "marked `docs: skip` in the doc" in report.split("## What wasn't checked")[1]
          and "has a placeholder" in report.split("## What wasn't checked")[1], report)
    check("report: fixed findings say how the fix was checked",
          "checked: a rerun passes now" in report and "checked: walk 2's newcomer passed this step" in report, report)
    check("report: a finding for killer-bugs gets its own section", "For killer-bugs" in report and "--shout doesn't exist" in report.split("For killer-bugs")[1])
    check("report: decisions, the waiting push and the commit",
          "## Decisions to check" in report and "git push -u origin" in report and f"## Commits on `{branch}`" in report, report)
    own = report.replace(PY, "").replace(PY.replace("\\", "/"), "")   # the test's own interpreter may live under ~/.claude
    check("report: no AI attribution in what it writes", not re.search(r"(?i)claude|anthropic|generated with|co-authored", own),
          re.findall(r"(?i).{30}(?:claude|anthropic).{30}", own))
    check("summary.json: the fields killer-goal reads", summary["skill"] == "killer-docs"
          and summary["status"] in ("done", "partial", "blocked") and summary["branch"] == branch
          and len(summary["commits"]) == 1 and summary["decisions"] and summary["waiting"]
          and Path(summary["report"]).resolve() == (ws / "report.md").resolve(), summary)
    nums = summary["numbers"]
    check("summary.json: the numbers come from the run", nums["journeys"] == 1 and nums["journeys_reached_latest_walk"] == 1
          and nums["journeys_reached_first_walk"] == 0 and nums["help_undocumented"] == 1
          and nums["links_broken"] == 0 and nums["findings_for_killer_bugs"] == 1, nums)
    check("report: ends with the one-line handoff", re.search(r"killer-docs: (done|partial|blocked) · .+ · .+report\.md", out), out)
    if sh_ok:
        check("summary.json: blocks failing or differing at first (3) and after the fixes (1: the timeout)",
              nums["blocks_bad_first"] == 3 and nums["blocks_bad_latest"] == 1, nums)
        check("summary.json: partial, because a proven finding is still open after fixing",
              summary["status"] == "partial", summary)

    # ---------- other scopes, and a folder that isn't a git repo
    out, _ = docs("init", "--repo", proj, "--scope", "start", "--slug", "start")
    check("init --scope start: only the README and getting-started docs",
          [d[0] for d in jload(run_folder(out) / "config.json")["docs_init"]] == ["README.md"], out)
    out, _ = docs("init", "--repo", proj, "--scope", "cli", "--slug", "cli")
    wc = run_folder(out)
    out, _ = docs("run-blocks", "-w", wc)
    check("init --scope cli: run-blocks has nothing to do", "CLI help only" in out, out)
    plain = tmp / "plain"
    plain.mkdir()
    (plain / "README.md").write_text(f"# plain\n\n{FENCE}python\nprint('hi')\n{FENCE}\n\n{FENCE}\nhi\n{FENCE}\n", encoding="utf-8")
    out, _ = docs("init", "--repo", plain)
    wp = run_folder(out)
    out, code = docs("run-blocks", "-w", wp)
    check("plain folder: blocks run in a copy, untagged output after code counts", "1 passed" in out
          and jload(wp / "passes" / "blocks-1.json")["results"][0]["match"] == "exact", out)
    out, code = docs("branch", "-w", wp, ok=(2,))
    check("plain folder: fixing on a branch needs git", "git repository" in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"({round(time.monotonic() - T0)} s)")
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)

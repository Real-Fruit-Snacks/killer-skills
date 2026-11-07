"""Tests for the shared kit (tools/shared/kit.py and run.py) and tools/sync.py. Run: python -I tests/test_kit.py"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


tmp = Path(tempfile.mkdtemp())
os.environ.update(KILLER_KITTEST_HOME=str(tmp / "runs"), KILLER_KITTEST_SANDBOXES=str(tmp / "boxes"),
                  GH_TOKEN="test-token-value", PYTHONIOENCODING="utf-8", PYTHONPATH=str(tmp / "host-path"))
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)

spec = importlib.util.spec_from_file_location("killer_kit", ROOT / "tools" / "shared" / "kit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)

PRINT_ENV = ("import os, sys\n"
             "print('\\n'.join(os.environ.get(k, '-') for k in ('HOME', 'TEMP', 'GH_TOKEN', 'HTTPS_PROXY', 'KILLER_SCRATCH', 'PYTHONPATH')))\n")

try:
    # ---------- a project with awkward files, in every working-tree state
    proj = tmp / "proj"
    proj.mkdir()
    g(proj, "init", "-q", "-b", "main")
    g(proj, "config", "user.name", "Test Person")
    g(proj, "config", "user.email", "test@example.com")
    g(proj, "config", "core.autocrlf", "true")
    (proj / ".gitattributes").write_text("*.txt text\n")
    (proj / ".gitignore").write_text("ignored/\n")
    (proj / "crlf.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    (proj / "latin.dat").write_bytes("caf\xe9\n".encode("latin-1"))
    (proj / "blob.bin").write_bytes(bytes(range(256)))
    (proj / "gone.py").write_text("x = 1\n")
    (proj / "app.py").write_text("print('v1')\n")
    g(proj, "add", "-A")
    g(proj, "commit", "-q", "-m", "first")
    (proj / "app.py").write_text("print('v2, uncommitted')\n")
    (proj / "new.py").write_text("print('untracked')\n")
    (proj / "ignored").mkdir()
    (proj / "ignored" / "x.txt").write_text("ignored\n")
    (proj / "printenv.py").write_text(PRINT_ENV)

    ws, cfg, files = kit.init_run("kittest", str(proj), target="a test project", extra={"mine": 1})
    check("init: the run lives under KILLER_<SKILL>_HOME", str(ws).startswith(str(tmp / "runs")), ws)
    check("init: scope has text files, not ignored or binary ones",
          "app.py" in files and "new.py" in files and "ignored/x.txt" not in files and "blob.bin" not in files, files)
    check("init: the skill's own fields are kept", kit.load_run(ws)[1]["mine"] == 1)

    _, _, changes = kit.init_run("kittest", str(proj), scope="changes", quiet=True)
    check("scope changes: only uncommitted work", set(changes) == {"app.py", "new.py", "printenv.py"}, changes)

    # ---------- a sandbox
    box = kit.make_sandbox(cfg, ws, "agent1")
    repo = box / "repo"
    check("sandbox: carries uncommitted and untracked work",
          (repo / "app.py").read_text() == "print('v2, uncommitted')\n" and (repo / "new.py").exists())
    check("sandbox: has no remote", g(repo, "remote").strip() == "")
    check("sandbox: base.txt is the HEAD commit", kit.box_base(box) == g(repo, "rev-parse", "HEAD").strip())
    check("sandbox: no diff before anything changes", kit.box_diff(box) == "")
    res = kit.run_in(box, [PY, "printenv.py"])
    vals = res["runs"][0]["out"].splitlines()
    check("run.py: own home and temp, no token, dead proxy, scratch folder, no host PYTHONPATH",
          Path(vals[0]) == box / "home" and Path(vals[1]) == box / "tmp" and vals[2] == "-"
          and vals[3] == kit.DEAD_PROXY and Path(vals[4]) == box / "scratch" and vals[5] == "-", vals)
    res = kit.run_in(box, ["python", "-c", "import sys; print(sys.version_info >= (3, 10))"])
    check("run.py: `python` runs a real Python, never the Microsoft Store stand-in",
          not kit.failed(res["runs"][0]) and "True" in res["runs"][0]["out"], kit.summary(res))

    # ---------- per-sandbox settings, stand-ins ahead on PATH, input for interactive commands
    standin = tmp / "standin"
    standin.mkdir()
    if os.name == "nt":
        (standin / "gh.cmd").write_text("@echo stand-in gh refuses %*\r\n@exit /b 3\r\n")
    else:
        (standin / "gh").write_text("#!/bin/sh\necho stand-in gh refuses \"$@\"\nexit 3\n")
        (standin / "gh").chmod(0o755)
    box2 = kit.make_sandbox(cfg, ws, "agent2", env={"TRAP": "{sandbox}/trap"}, path_first=[str(standin)])
    out = kit.run_in(box2, [PY, "-c", "import os; print('TRAP=' + os.environ['TRAP'])"])["runs"][0]["out"]
    check("env: {sandbox} in a setting is each sandbox's own folder",
          f"TRAP={box2}/trap".replace("\\", "/") in out.replace("\\", "/"), out)
    r = kit.run_in(box2, ["gh", "auth", "status"])["runs"][0]
    check("path_first: a stand-in comes before the real tool on PATH",
          r["code"] == 3 and "stand-in gh refuses auth status" in r["out"], r)
    answers = tmp / "answers.txt"
    answers.write_text("yes\n")
    ask = [PY, "-c", "import sys; print('GOT=' + sys.stdin.read().strip())"]
    r = kit.run_in(box2, ask, stdin=answers)["runs"][0]
    check("stdin: a file is fed to the command as its input", "GOT=yes" in r["out"], r)
    r = kit.run_in(box2, ask)["runs"][0]
    check("stdin: without one, the command gets no input (and doesn't hang)", "GOT=" in r["out"] and r["code"] == 0, r)
    r = subprocess.run([PY, str(box2 / "run.py"), "--stdin", str(answers), "--", *ask], capture_output=True, text=True)
    check("run.py --stdin on the command line", "GOT=yes" in r.stdout, r.stdout + r.stderr)
    r = subprocess.run([PY, str(box2 / "run.py"), "--stdin", str(tmp / "missing.txt"), "--", *ask],
                       capture_output=True, text=True)
    check("run.py --stdin: a missing file is an error, not a silent empty input",
          r.returncode == 2 and "no file" in r.stderr, r.stdout + r.stderr)
    kit.drop_sandbox(ws, cfg, "agent2")

    # ---------- an agent changes things
    (repo / "crlf.txt").write_bytes(b"one\r\nTWO\r\nthree\r\n")
    (repo / "latin.dat").write_bytes("caf\xe9 au lait\n".encode("latin-1"))
    (repo / "blob.bin").write_bytes(bytes(reversed(range(256))))
    (repo / "gone.py").unlink()
    (repo / "added.py").write_text("print('added')\n")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "junk.pyc").write_bytes(b"\0junk")
    patch = kit.box_diff(box)
    touched = kit.changed_files(patch)
    check("box_diff: every change, no __pycache__",
          set(touched) == {"crlf.txt", "latin.dat", "blob.bin", "gone.py", "added.py"}, touched)
    check("box_diff: CRLF kept", "TWO\r" in patch, patch[:400])
    kit.write_patch(ws / "p.patch", patch)
    check("write_patch/read_patch: round trip", kit.read_patch(ws / "p.patch") == patch)

    # ---------- the patch applies to a fresh copy, and to the real project
    verify = ("import pathlib, sys\n"
              "ok = pathlib.Path('crlf.txt').read_bytes() == b'one\\r\\nTWO\\r\\nthree\\r\\n' "
              "and pathlib.Path('latin.dat').read_bytes() == 'caf\\xe9 au lait\\n'.encode('latin-1') "
              "and pathlib.Path('blob.bin').read_bytes() == bytes(reversed(range(256))) "
              "and not pathlib.Path('gone.py').exists() and pathlib.Path('added.py').exists()\n"
              "print('OBSERVED:', ok)\nsys.exit(0 if ok else 1)\n")
    (box / "scratch" / "verify.py").write_text(verify)
    fresh = kit.run_fresh(cfg, ws, [PY, "../scratch/verify.py"], patch=patch, scratch_from=box / "scratch")
    check("run_fresh: the patch applies to a fresh copy, byte for byte",
          fresh["runs"] and not kit.failed(fresh["runs"][0]), kit.summary(fresh))
    check("run_fresh: its sandbox is gone afterwards", list(kit.load_run(ws)[1]["sandboxes"]) == ["agent1"])
    bad = kit.run_fresh(cfg, ws, [PY, "-c", "print(1)"], patch=patch.replace("TWO", "TW0", 1).replace("two", "tw0", 1))
    check("run_fresh: a patch that doesn't apply runs nothing", bad.get("patch_error") and not bad["runs"], bad)

    real = tmp / "real-copy"
    shutil.copytree(proj, real, ignore=shutil.ignore_patterns(".git"))
    shutil.copytree(proj / ".git", real / ".git")
    ok, msg = kit.apply_patch(real, patch)
    check("apply_patch: applies to the project's working tree", ok, msg)
    check("apply_patch: the files match the sandbox's exactly",
          all((real / f).read_bytes() == (repo / f).read_bytes() for f in ("crlf.txt", "latin.dat", "blob.bin", "added.py"))
          and not (real / "gone.py").exists())
    ok, msg = kit.apply_patch(real, patch, check_only=True)
    check("apply_patch --check: a patch already applied doesn't apply twice", not ok, msg)

    # ---------- an older revision checks out with the project's own line-ending settings
    lf = tmp / "lf"
    lf.mkdir()
    g(lf, "init", "-q", "-b", "main")
    g(lf, "config", "core.autocrlf", "false")
    (lf / "a.txt").write_bytes(b"one\ntwo\n")
    g(lf, "-c", "user.name=T", "-c", "user.email=t@example.com", "add", "-A")
    g(lf, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", "lf")
    lws, lcfg, _ = kit.init_run("kittest", str(lf), quiet=True)
    lbox = kit.make_sandbox(lcfg, lws, "rev", rev="HEAD")
    check("sandbox --rev: a project with core.autocrlf=false gets LF files, whatever git's global setting",
          (lbox / "repo" / "a.txt").read_bytes() == b"one\ntwo\n", (lbox / "repo" / "a.txt").read_bytes())
    kit.clean_sandboxes(lws, lcfg)

    # ---------- an older revision, and a folder that isn't a git repo
    old = kit.make_sandbox(cfg, ws, "old", rev="HEAD")
    check("sandbox --rev: the commit, without uncommitted work",
          (old / "repo" / "app.py").read_text() == "print('v1')\n" and not (old / "repo" / "new.py").exists())
    plain = tmp / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("a = 1\n")
    pws, pcfg, _ = kit.init_run("kittest", str(plain), quiet=True)
    pbox = kit.make_sandbox(pcfg, pws, "p")
    (pbox / "repo" / "a.py").write_text("a = 2\n")
    check("plain folder: diffs work without git in the project", kit.changed_files(kit.box_diff(pbox)) == ["a.py"])
    check("plain folder: the project itself is left alone", not (plain / ".git").exists())

    # ---------- base=False: the project's own git state, and a scratch folder with another name
    g(proj, "add", "app.py")   # one change staged, the rest left as it is
    nb = kit.make_sandbox(cfg, ws, "nobase", base=False, scratch="repro")
    nrepo = nb / "repo"
    check("base=False: HEAD and branch are the project's, and there's no base commit",
          g(nrepo, "rev-parse", "HEAD") == g(proj, "rev-parse", "HEAD")
          and g(nrepo, "branch", "--show-current").strip() == "main" and not (nb / "base.txt").exists())
    check("base=False: git status matches the project's: staged stays staged, the rest uncommitted",
          g(nrepo, "status", "--porcelain") == g(proj, "status", "--porcelain")
          and g(nrepo, "diff", "--cached", "--name-only").split() == ["app.py"],
          (g(nrepo, "status", "--porcelain"), g(proj, "status", "--porcelain")))
    env_set = json.loads((nb / "env.json").read_text(encoding="utf-8"))["set"]
    check("scratch=: the folder, KILLER_SCRATCH and the sandbox text use the name",
          (nb / "repro").is_dir() and not (nb / "scratch").exists() and Path(env_set["KILLER_SCRATCH"]) == nb / "repro"
          and str(nb / "repro") in kit.sandbox_text(nb, False, scratch="repro"), env_set.get("KILLER_SCRATCH"))
    try:
        kit.box_base(nb)
        check("base=False: asking for its base fails plainly", False)
    except SystemExit:
        check("base=False: asking for its base fails plainly", True)
    g(proj, "reset", "-q", "app.py")
    pnb = kit.make_sandbox(pcfg, pws, "pnb", base=False)
    check("base=False on a plain folder: the copy isn't made a git repo either",
          (pnb / "repo" / "a.py").is_file() and not (pnb / "repo" / ".git").exists())

    # ---------- names, cleaning
    try:
        kit.make_sandbox(cfg, ws, "bad name")
        check("sandbox: a bad name is refused", False)
    except SystemExit:
        check("sandbox: a bad name is refused", True)
    try:
        kit.make_sandbox(cfg, ws, "s2", scratch="repo")
        check("sandbox: a scratch folder can't be called repo", False)
    except SystemExit:
        check("sandbox: a scratch folder can't be called repo", True)
    n = kit.clean_sandboxes(ws, cfg)
    check("clean: removes every sandbox", n == 3 and not box.exists() and not old.exists() and not nb.exists(), n)
    kit.clean_sandboxes(pws, pcfg)

    # ---------- many processes reading and replacing one file (Windows refuses a replace while it's open)
    worker = tmp / "worker.py"
    worker.write_text(
        "import importlib.util, sys\nfrom pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('k', {str(ROOT / 'tools' / 'shared' / 'kit.py')!r})\n"
        "kit = importlib.util.module_from_spec(spec); spec.loader.exec_module(kit)\n"
        "bad = 0\n"
        "for i in range(150):\n"
        "    try:\n"
        "        kit.load_json(Path(sys.argv[1]), {}); kit.save_json(Path(sys.argv[1]), {'i': i})\n"
        "    except Exception as e:\n"
        "        bad += 1; print(repr(e), file=sys.stderr)\n"
        "print(bad)\n")
    shared = tmp / "shared.json"
    kit.save_json(shared, {})
    procs = [subprocess.Popen([PY, "-I", str(worker), str(shared)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for _ in range(6)]
    outs = [p.communicate() for p in procs]
    bad = sum(int(o.strip() or 1) for o, _ in outs)
    check("save_json/load_json: six processes at once, no errors", bad == 0, [e[-300:] for _, e in outs if e])

    # ---------- sync.py
    r = subprocess.run([PY, "-I", str(ROOT / "tools" / "sync.py"), "--check"], capture_output=True, text=True)
    check("sync.py --check: every shared copy matches its source", r.returncode == 0, r.stdout + r.stderr)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)

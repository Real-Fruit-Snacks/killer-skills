"""Tests for tools/package.py. Run: python -I tests/test_package.py"""
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "tools" / "package.py"
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


def pack(repo, out):
    script = repo / "tools" / "package.py"
    return subprocess.run([sys.executable, "-I", str(script), "--out", str(out)], capture_output=True, text=True)


def zip_files(path):
    with zipfile.ZipFile(path) as z:
        return {i.filename: z.read(i) for i in z.infolist() if not i.is_dir()}


def tgz_files(path):
    with tarfile.open(path, "r:gz") as t:
        return {m.name: t.extractfile(m).read() for m in t.getmembers() if m.isfile()}


def index_files(repo, prefix):
    """What git's index holds under skills/<prefix>, with the stored bytes."""
    listing = subprocess.run(["git", "-C", str(repo), "ls-files", "-s", "--", f"skills/{prefix}"],
                             capture_output=True, text=True, check=True).stdout.splitlines()
    out = {}
    for line in listing:
        meta, path = line.split("\t", 1)
        sha = meta.split()[1]
        data = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", sha], capture_output=True, check=True).stdout
        out[path[len("skills/"):]] = data
    return out


tmp = Path(tempfile.mkdtemp())
try:
    # ---------- the real skills
    r = pack(ROOT, tmp / "a")
    check("packs the repo's skills", r.returncode == 0, r.stdout + r.stderr)
    dist = tmp / "a"
    skills = sorted(p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").exists())
    expected = sorted([f"{s}.zip" for s in skills] + [f"{s}.tar.gz" for s in skills] + ["killer-skills.zip", "killer-skills.tar.gz"])
    sums = dict(reversed(line.split("  ", 1)) for line in (dist / "SHA256SUMS.txt").read_text().splitlines())
    check("SHA256SUMS lists every archive", sorted(sums) == expected, sorted(sums))
    check("every sum matches its file",
          all(hashlib.sha256((dist / f).read_bytes()).hexdigest() == h for f, h in sums.items()))

    every = {}
    for s in skills:
        want = index_files(ROOT, s)
        every.update(want)
        z, t = zip_files(dist / f"{s}.zip"), tgz_files(dist / f"{s}.tar.gz")
        check(f"{s}.zip holds exactly its files from git", z == want, sorted(set(z) ^ set(want)))
        check(f"{s}.tar.gz holds the same files", t == want, sorted(set(t) ^ set(want)))
        check(f"{s} has {s}/SKILL.md at the root", f"{s}/SKILL.md" in z)
    check("the bundle holds every skill", zip_files(dist / "killer-skills.zip") == every and tgz_files(dist / "killer-skills.tar.gz") == every)
    check("text files keep git's LF endings", all(b"\r\n" not in v for k, v in every.items() if k.endswith((".md", ".mjs", ".py"))))

    r2 = pack(ROOT, tmp / "b")
    check("packing twice gives the same bytes",
          (tmp / "a" / "SHA256SUMS.txt").read_bytes() == (tmp / "b" / "SHA256SUMS.txt").read_bytes(), r2.stdout)

    # ---------- a scratch repo with problems
    repo = tmp / "repo"
    (repo / "tools").mkdir(parents=True)
    shutil.copy(PACKAGE, repo / "tools" / "package.py")

    def skill(folder, text):
        d = repo / "skills" / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(text, encoding="utf-8")

    def git(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)

    git("init", "-q")
    skill("good-one", "---\nname: good-one\ndescription: Does one thing\n---\n# Good\n")
    git("add", "-A")
    (repo / "skills" / "good-one" / "notes.txt").write_text("not in git")
    r = pack(repo, tmp / "c")
    check("a clean scratch repo packs", r.returncode == 0, r.stdout + r.stderr)
    check("untracked files are left out and named", "left out skills/good-one/notes.txt" in r.stdout, r.stdout)
    check("left-out file isn't in the archive", "good-one/notes.txt" not in zip_files(tmp / "c" / "good-one.zip"))

    skill("wrong-name", "---\nname: other-name\ndescription: x\n---\n")
    skill("no-desc", "---\nname: no-desc\n---\n")
    skill("Caps", "---\nname: Caps\ndescription: x\n---\n")
    skill("no-front", "# No frontmatter\n")
    skill("long-desc", "---\nname: long-desc\ndescription: " + "x" * 1025 + "\n---\n")
    skill("killer-skills", "---\nname: killer-skills\ndescription: x\n---\n")
    skill("killer-skills-fonts", "---\nname: killer-skills-fonts\ndescription: x\n---\n")
    git("add", "-A")
    r = pack(repo, tmp / "d")
    out = r.stdout + r.stderr
    check("problems stop the pack", r.returncode != 0, out)
    for frag in ["skills/wrong-name: frontmatter name is 'other-name'", "skills/no-desc: frontmatter has no description",
                 "skills/Caps: folder name must be", "skills/no-front: SKILL.md must start with frontmatter",
                 "skills/long-desc: description is 1025 characters", "skills/killer-skills: killer-skills is the name of the bundle",
                 "skills/killer-skills-fonts: names starting killer-skills- are the packs'"]:
        check(f"reports: {frag}", frag in out, out)
    check("nothing is written when packing fails", not (tmp / "d").exists())

    # ---------- tools/packs.py writes the same bytes for the same contents
    sys.path.insert(0, str(ROOT / "tools"))
    import packs
    src = tmp / "pack-src"
    (src / "sub" / ".links").mkdir(parents=True)
    (src / "a.txt").write_text("alpha\n" * 200)
    (src / "sub" / "b.woff2").write_bytes(b"wOF2" + bytes(range(256)) * 8)
    (src / "sub" / "tool.exe").write_bytes(b"MZ" + b"\0" * 64)
    (src / "sub" / ".links" / "hash").write_text("C:/a/temp/path")
    one, two = tmp / "one.zip", tmp / "two.zip"
    packs.zipdir(src, one, ["sub/.links/*"])
    for f in src.rglob("*"):
        if f.is_file():
            os.utime(f, (1234567890, 1234567890))   # new mtimes, same contents
    packs.zipdir(src, two, ["sub/.links/*"])
    check("packs.zipdir: the same contents give the same bytes", one.read_bytes() == two.read_bytes())
    with zipfile.ZipFile(one) as z:
        infos = {i.filename: i for i in z.infolist()}
    check("packs.zipdir: files at the root, sorted, excludes left out", list(infos) == ["a.txt", "sub/b.woff2", "sub/tool.exe"], list(infos))
    check("packs.zipdir: woff2 stored, text deflated",
          infos["sub/b.woff2"].compress_type == zipfile.ZIP_STORED and infos["a.txt"].compress_type == zipfile.ZIP_DEFLATED)
    check("packs.zipdir: one fixed date", {i.date_time for i in infos.values()} == {packs.STAMP})
    t1, t2 = tmp / "one.tar.gz", tmp / "two.tar.gz"
    packs.tgzdir(src, t1, ["sub/.links/*"])
    os.utime(src / "a.txt", (1, 1))
    packs.tgzdir(src, t2, ["sub/.links/*"])
    check("packs.tgzdir: the same contents give the same bytes", t1.read_bytes() == t2.read_bytes())
    with tarfile.open(t1) as tf:
        names = tf.getnames()
    check("packs.tgzdir: folders first, excludes left out", names == ["sub", "a.txt", "sub/b.woff2", "sub/tool.exe"], names)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)

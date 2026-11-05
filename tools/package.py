#!/usr/bin/env python3
"""Pack every skill in skills/ for a release.

usage: python3 tools/package.py [--out dist]

For each skills/<name>/SKILL.md, writes to --out:
    <name>.zip             the skill on its own: unzip into ~/.claude/skills
    <name>.tar.gz          the same files, for install.sh
    killer-skills.zip      every skill, side by side
    killer-skills.tar.gz
    SHA256SUMS.txt         sha256sum format, over all of the above

Every archive has the skill folders at its root, so it unpacks straight into
a skills folder. Contents come from git's index (committed or staged files,
with the line endings git stores), and every timestamp is the last commit's
(or SOURCE_DATE_EPOCH), so one commit always packs to the same bytes on any OS. Untracked files are
left out; the tool says which.

Before packing, each SKILL.md is checked: frontmatter first, a name that
matches its folder (lowercase letters, digits and hyphens, 64 at most) and a
description of 1 to 1024 characters.
"""
import argparse
import gzip
import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = "killer-skills"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def git(*args, data=None):
    r = subprocess.run(["git", "-C", str(ROOT), *args], input=data, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def frontmatter(text):
    """The YAML frontmatter's top-level keys, read just well enough for name and description."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    keys, key = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            return keys
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            key = m.group(1)
            keys[key] = m.group(2).strip()
        elif key and line.startswith((" ", "\t")):
            keys[key] += " " + line.strip()
    return None  # never closed


def unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def check_skill(name, skill_md):
    problems = []
    if not NAME_RE.match(name) or len(name) > 64:
        problems.append("folder name must be lowercase letters, digits and hyphens, 64 characters at most")
    if name == BUNDLE:
        problems.append(f"{BUNDLE} is the name of the bundle of every skill; pick another")
    fm = frontmatter(skill_md)
    if fm is None:
        return problems + ["SKILL.md must start with frontmatter between two '---' lines"]
    if unquote(fm.get("name", "")) != name:
        problems.append(f"frontmatter name is {fm.get('name')!r}, but the folder is {name!r}")
    desc = unquote(fm.get("description", ""))
    if not desc:
        problems.append("frontmatter has no description")
    elif len(desc) > 1024:
        problems.append(f"description is {len(desc)} characters; the limit is 1024")
    return problems


def tracked():
    """{path: (mode, blob sha)} for every file in the index under skills/."""
    out = {}
    for entry in git("ls-files", "-s", "-z", "--", "skills").split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        mode, sha, stage = meta.split()
        if stage != b"0":
            sys.exit(f"unresolved merge conflict in {path.decode()}")
        out[path.decode()] = (int(mode, 8), sha.decode())
    return out


def read_blobs(shas):
    """Blob contents by sha, through one git cat-file --batch call."""
    raw = git("cat-file", "--batch", data=("\n".join(shas) + "\n").encode())
    blobs, i = {}, 0
    for sha in shas:
        nl = raw.index(b"\n", i)
        _, kind, size = raw[i:nl].split()
        size = int(size)
        if kind != b"blob":
            sys.exit(f"{sha} is a {kind.decode()}, not a file")
        blobs[sha] = raw[nl + 1:nl + 1 + size]
        i = nl + 1 + size + 1
    return blobs


def write_zip(path, files, epoch):
    stamp = time.gmtime(max(epoch, 315532800))[:6]  # zip dates start in 1980
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for arc, mode, data in files:
            info = zipfile.ZipInfo(arc, stamp)
            info.external_attr = (0o100755 if mode & 0o111 else 0o100644) << 16
            info.create_system = 3  # unix, so the mode above is honoured
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)


def write_tgz(path, files, epoch):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        dirs = sorted({str(Path(arc).parent.as_posix()) for arc, _, _ in files} - {"."})
        expanded = set()
        for d in dirs:  # parents first, so tar tools that need them see them
            parts = d.split("/")
            for n in range(1, len(parts) + 1):
                expanded.add("/".join(parts[:n]))
        for d in sorted(expanded):
            info = tarfile.TarInfo(d)
            info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, epoch
            t.addfile(info)
        for arc, mode, data in files:
            info = tarfile.TarInfo(arc)
            info.size, info.mtime = len(data), epoch
            info.mode = 0o755 if mode & 0o111 else 0o644
            t.addfile(info, io.BytesIO(data))
    with open(path, "wb") as f, gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=epoch, compresslevel=9) as g:
        g.write(buf.getvalue())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="dist", help="output folder (default: dist)")
    a = ap.parse_args()
    out = Path(a.out)
    if not out.is_absolute():
        out = Path.cwd() / out

    index = tracked()
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if not epoch:
        r = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%ct"], capture_output=True, text=True)
        epoch = r.stdout.strip() if r.returncode == 0 else ""
    epoch = int(epoch or 0)
    folders = sorted(p.name for p in (ROOT / "skills").iterdir() if p.is_dir())
    names = [n for n in folders if f"skills/{n}/SKILL.md" in index]
    for n in folders:
        if n not in names:
            print(f"skipped skills/{n}: no SKILL.md in git")
    if not names:
        sys.exit("no skills to pack: add skills/<name>/SKILL.md to git first")

    untracked = git("ls-files", "--others", "--exclude-standard", "--", "skills").decode().split()
    for p in untracked:
        print(f"left out {p}: not in git")

    blobs = read_blobs(sorted({sha for _, sha in index.values()}))
    failed = False
    per_skill = {}
    for name in names:
        problems = check_skill(name, blobs[index[f"skills/{name}/SKILL.md"][1]].decode("utf-8"))
        for p in problems:
            print(f"skills/{name}: {p}")
        failed = failed or bool(problems)
        prefix = f"skills/{name}/"
        per_skill[name] = [(path[len("skills/"):], mode, blobs[sha])
                           for path, (mode, sha) in sorted(index.items()) if path.startswith(prefix)]
    if failed:
        sys.exit("fix the problems above, then pack again")

    out.mkdir(parents=True, exist_ok=True)
    made = []
    for name in names:
        write_zip(out / f"{name}.zip", per_skill[name], epoch)
        write_tgz(out / f"{name}.tar.gz", per_skill[name], epoch)
        made += [f"{name}.zip", f"{name}.tar.gz"]
        print(f"packed {name}: {len(per_skill[name])} files")
    everything = [f for name in names for f in per_skill[name]]
    write_zip(out / f"{BUNDLE}.zip", everything, epoch)
    write_tgz(out / f"{BUNDLE}.tar.gz", everything, epoch)
    made += [f"{BUNDLE}.zip", f"{BUNDLE}.tar.gz"]

    sums = "".join(f"{hashlib.sha256((out / f).read_bytes()).hexdigest()}  {f}\n" for f in sorted(made))
    (out / "SHA256SUMS.txt").write_bytes(sums.encode())
    print(f"{len(made) + 1} files in {out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build the packs that ship beside the skills in a release, and keep their pins current.

usage:
    python3 tools/packs.py fonts  [--out dist]    killer-skills-fonts.zip and .tar.gz
    python3 tools/packs.py node   [--out dist]    killer-skills-node-windows-x64.zip
    python3 tools/packs.py python [--out dist]    killer-skills-python-windows-x64.zip
    python3 tools/packs.py zipdir SRC OUT.zip [--exclude GLOB ...]
    python3 tools/packs.py pin [fonts|node|python]

The contents are pinned. tools/runtime.json has the Node.js and Python versions with the
SHA-256 of each download; tools/fonts.json lists every font family with the npm package
(from Fontsource) it comes from, its version and its integrity hash. A download that
doesn't match its pin stops the build. `pin` looks up the newest versions (Node.js
within the pinned major line, Python within the pinned minor) and rewrites the pins.

Every pack is written the same way (entries sorted, one fixed timestamp, fixed modes),
so the same pins always give the same bytes and the same line in SHA256SUMS.txt, and an
installer that already has a pack doesn't download it again for a new release.

The font pack holds, for each family, its latin and latin-ext .woff2 files (one file
with every axis for a variable family, one per weight and style for a static one) and
its licence, plus fonts.json, the index killer-design's fonts.mjs reads.
"""
import argparse
import base64
import fnmatch
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "tools" / "runtime.json"
FONTS = ROOT / "tools" / "fonts.json"
STAMP = (2000, 1, 1, 0, 0, 0)          # every entry's date, so a pack's bytes depend on its contents only
EPOCH = 946684800                      # the same moment, for tar
STORED = {".woff2", ".woff", ".zip", ".gz", ".png", ".jpg", ".webp"}   # already compressed
SUBSETS = ("latin", "latin-ext")
UA = {"User-Agent": "killer-skills-packs"}


def say(*a):
    print(*a, flush=True)


def fetch(url, tries=5, missing_ok=False):
    """The body at url, retrying 5xx and network errors. A 404 is None with missing_ok, else fatal."""
    for n in range(1, tries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 and missing_ok:
                return None
            if e.code < 500 or n == tries:
                sys.exit(f"could not download {url}: HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if n == tries:
                sys.exit(f"could not download {url}: {e}")
        time.sleep(2 * n)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


# ---- writing packs ------------------------------------------------------------------

def entries(src, exclude=()):
    src = Path(src)
    out = []
    for p in sorted(src.rglob("*"), key=lambda p: p.relative_to(src).as_posix()):
        rel = p.relative_to(src).as_posix()
        if p.is_file() and not any(fnmatch.fnmatchcase(rel, g) for g in exclude):
            out.append((rel, p))
    return out


def mode_of(rel):
    return 0o755 if rel.endswith((".exe", ".dll", ".sh")) else 0o644


def zipdir(src, out, exclude=()):
    """Zip a folder's files (not the folder itself) so the same contents always give the same bytes."""
    out = Path(out)
    part = out.with_name(out.name + ".part")
    with zipfile.ZipFile(part, "w") as z:
        for rel, p in entries(src, exclude):
            info = zipfile.ZipInfo(rel, STAMP)
            info.create_system = 3   # unix, so the mode below is what's recorded
            info.external_attr = (0o100000 | mode_of(rel)) << 16
            info.compress_type = zipfile.ZIP_STORED if p.suffix.lower() in STORED else zipfile.ZIP_DEFLATED
            info.file_size = p.stat().st_size
            with p.open("rb") as f, z.open(info, "w", force_zip64=info.file_size > 0x7FFFFFFF) as w:
                shutil.copyfileobj(f, w, 1 << 20)
    os.replace(part, out)
    return out


def tgzdir(src, out, exclude=()):
    """The same, as a .tar.gz for install.sh."""
    out = Path(out)
    files = entries(src, exclude)
    dirs = sorted({"/".join(rel.split("/")[:n]) for rel, _ in files for n in range(1, rel.count("/") + 1)})
    part = out.with_name(out.name + ".part")
    with open(part, "wb") as f, gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=EPOCH, compresslevel=9) as g:
        with tarfile.open(fileobj=g, mode="w", format=tarfile.PAX_FORMAT) as t:
            for d in dirs:
                info = tarfile.TarInfo(d)
                info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, EPOCH
                t.addfile(info)
            for rel, p in files:
                info = tarfile.TarInfo(rel)
                info.size, info.mode, info.mtime = p.stat().st_size, mode_of(rel), EPOCH
                with p.open("rb") as fh:
                    t.addfile(info, fh)
    os.replace(part, out)
    return out


def report(path):
    say(f"wrote {path} ({path.stat().st_size / 1048576:.1f} MB, sha256 {sha256(path.read_bytes())[:16]}...)")


# ---- Node.js ------------------------------------------------------------------------

def node_url(v, name=None):
    return f"https://nodejs.org/dist/v{v}/" + (name or f"node-v{v}-win-x64.zip")


def build_node(out):
    pin = load(PINS)["node"]
    v = pin["version"]
    say(f"== Node.js {v}")
    data = fetch(node_url(v))
    if sha256(data) != pin["sha256"]:
        sys.exit(f"node-v{v}-win-x64.zip doesn't match the sha256 pinned in tools/runtime.json")
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "node"
        stage.mkdir()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in ("node.exe", "LICENSE"):
                (stage / name).write_bytes(z.read(f"node-v{v}-win-x64/{name}"))
        save(stage / "node.json", {"platform": "windows-x64", "version": v})
        report(zipdir(stage, Path(out) / "killer-skills-node-windows-x64.zip"))


# ---- Python -------------------------------------------------------------------------

def python_url(v):
    return f"https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"


def build_python(out):
    pin = load(PINS)["python"]
    v = pin["version"]
    say(f"== Python {v} (the embeddable package)")
    data = fetch(python_url(v))
    if sha256(data) != pin["sha256"]:
        sys.exit(f"python-{v}-embed-amd64.zip doesn't match the sha256 pinned in tools/runtime.json")
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "python"
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(stage)
        if not (stage / "python.exe").exists():
            sys.exit(f"python-{v}-embed-amd64.zip has no python.exe")
        save(stage / "python.json", {"platform": "windows-x64", "version": v})
        report(zipdir(stage, Path(out) / "killer-skills-python-windows-x64.zip"))


# ---- fonts --------------------------------------------------------------------------

def num(x):
    x = float(x)
    return int(x) if x.is_integer() else x


def slug(family):
    return re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")


def tarball_url(package, version):
    return f"https://registry.npmjs.org/{package}/-/{package.split('/')[-1]}-{version}.tgz"


def check_integrity(data, integrity, what):
    algo, _, want = integrity.partition("-")
    if algo not in ("sha512", "sha384", "sha256") or base64.b64encode(hashlib.new(algo, data).digest()).decode() != want:
        sys.exit(f"{what} doesn't match the integrity hash pinned in tools/fonts.json")


def pick_files(names, fid, variable):
    """The .woff2 files to keep: per subset and style, every axis in one file (variable) or every weight (static)."""
    subsets = sorted(SUBSETS, key=len, reverse=True)   # latin-ext before latin, or latin would claim its files
    pat = re.compile(rf"^files/{re.escape(fid)}-({'|'.join(map(re.escape, subsets))})-(.+)-(normal|italic)\.woff2$")
    found = {}
    for n in names:
        m = pat.match(n)
        if m:
            found.setdefault((m.group(1), m.group(3)), {})[m.group(2)] = n
    keep = []
    for (subset, style), by in sorted(found.items()):
        if variable:
            axis = next((a for a in ("full", "standard", "wght") if a in by), None) or sorted(by)[0]
            keep.append((by[axis], subset, style, None))
        else:
            for w in sorted(by, key=lambda w: int(w) if w.isdigit() else 0):
                if w.isdigit():
                    keep.append((by[w], subset, style, int(w)))
    return keep


def build_fonts(out):
    pins = load(FONTS)["families"]
    say(f"== fonts: {len(pins)} families")
    index = []
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "fonts"
        stage.mkdir()
        for pin in pins:
            family, package, version = pin["family"], pin["package"], pin["version"]
            data = fetch(tarball_url(package, version))
            check_integrity(data, pin["integrity"], f"{package}@{version}")
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
                files = {m.name.removeprefix("package/"): m for m in t.getmembers() if m.isfile()}

                def read(name):
                    return t.extractfile(files[name]).read()
                meta = json.loads(read("metadata.json"))
                unicode = json.loads(read("unicode.json")) if "unicode.json" in files else {}
                fid = meta["id"]
                variable = meta.get("variable") or None
                chosen = pick_files(files, fid, bool(variable))
                if not chosen:
                    sys.exit(f"{package}@{version} has no latin .woff2 files")
                d = stage / fid
                d.mkdir()
                (d / "LICENSE").write_bytes(read("LICENSE"))
                entry_files = []
                for name, subset, style, weight in chosen:
                    blob = read(name)
                    if blob[:4] != b"wOF2":
                        sys.exit(f"{package}: {name} isn't a woff2 file")
                    leaf = name.split("/")[-1]
                    (d / leaf).write_bytes(blob)
                    rng = unicode.get(subset) or unicode.get(f"[{subset}]")
                    f = {"file": leaf, "subset": subset, "style": style}
                    if weight is not None:
                        f["weight"] = weight
                    if rng:
                        f["unicodeRange"] = rng
                    entry_files.append(f)
            entry = {"family": family, "id": fid, "category": meta.get("category"),
                     "license": (meta.get("license") or {}).get("type"), "source": f"{package}@{version}"}
            if variable:
                entry["axes"] = {a: [num(v["min"]), num(v["max"])] for a, v in sorted(variable.items())}
            else:
                entry["weights"] = sorted({f["weight"] for f in entry_files})
            entry["styles"] = sorted({f["style"] for f in entry_files}, key=["normal", "italic"].index)
            entry["files"] = entry_files
            index.append(entry)
            size = sum((d / f["file"]).stat().st_size for f in entry_files)
            say(f"  {family}: {len(entry_files)} files, {size // 1024} KB")
        names = [e["family"] for e in index]
        if len(set(names)) != len(names) or len({e["id"] for e in index}) != len(index):
            sys.exit("tools/fonts.json lists a family twice")
        save(stage / "fonts.json", {"count": len(index), "subsets": list(SUBSETS),
                                    "source": "Fontsource (npm), built from Google Fonts", "families": index})
        Path(out).mkdir(parents=True, exist_ok=True)
        report(zipdir(stage, Path(out) / "killer-skills-fonts.zip"))
        report(tgzdir(stage, Path(out) / "killer-skills-fonts.tar.gz"))


# ---- pins ---------------------------------------------------------------------------

def pin_node(pins):
    major = pins.get("node", {}).get("version", "24").split(".")[0]
    rel = json.loads(fetch("https://nodejs.org/dist/index.json"))
    v = next(r["version"][1:] for r in rel if r["version"].startswith(f"v{major}.") and "win-x64-zip" in r["files"])
    sums = fetch(node_url(v, "SHASUMS256.txt")).decode()
    h = next(line.split()[0] for line in sums.splitlines() if line.endswith(f"  node-v{v}-win-x64.zip"))
    if sha256(fetch(node_url(v))) != h:
        sys.exit(f"node-v{v}-win-x64.zip doesn't match nodejs.org's SHASUMS256.txt")
    pins["node"] = {"version": v, "sha256": h}
    say(f"node: {v}")


def pin_python(pins):
    minor = ".".join(pins.get("python", {}).get("version", "3.14").split(".")[:2])
    listing = fetch("https://www.python.org/ftp/python/").decode()
    found = {m for m in re.findall(r'href="(\d+\.\d+\.\d+)/"', listing) if m.startswith(minor + ".")}
    for v in sorted(found, key=lambda v: tuple(map(int, v.split("."))), reverse=True):
        data = fetch(python_url(v), missing_ok=True)
        if data is None:
            continue   # a release folder without the embeddable package yet
        pins["python"] = {"version": v, "sha256": sha256(data)}
        say(f"python: {v}")
        return
    sys.exit(f"no Python {minor}.x on python.org has an embeddable package")


def pin_fonts():
    doc = load(FONTS)
    out = []
    for f in doc["families"]:
        fid = slug(f["family"])
        for package in (f"@fontsource-variable/{fid}", f"@fontsource/{fid}"):
            body = fetch(f"https://registry.npmjs.org/{package}/latest", missing_ok=True)
            if body is None:
                continue
            meta = json.loads(body)
            out.append({"family": f["family"], "package": package, "version": meta["version"],
                        "integrity": meta["dist"]["integrity"]})
            say(f"{f['family']}: {package}@{meta['version']}")
            break
        else:
            sys.exit(f"{f['family']}: no Fontsource package called {fid}")
    doc["families"] = out
    save(FONTS, doc)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fonts", "node", "python"):
        s = sub.add_parser(name)
        s.add_argument("--out", default="dist")
    z = sub.add_parser("zipdir")
    z.add_argument("src")
    z.add_argument("out")
    z.add_argument("--exclude", action="append", default=[], help="a glob over paths inside SRC, e.g. 'ms-playwright/.links/*'")
    p = sub.add_parser("pin")
    p.add_argument("what", nargs="*", choices=["fonts", "node", "python"], default=[])
    a = ap.parse_args()

    if a.cmd in ("fonts", "node", "python"):
        Path(a.out).mkdir(parents=True, exist_ok=True)
        {"fonts": build_fonts, "node": build_node, "python": build_python}[a.cmd](a.out)
    elif a.cmd == "zipdir":
        report(zipdir(a.src, a.out, a.exclude))
    elif a.cmd == "pin":
        what = a.what or ["fonts", "node", "python"]
        if "node" in what or "python" in what:
            pins = load(PINS)
            if "node" in what:
                pin_node(pins)
            if "python" in what:
                pin_python(pins)
            save(PINS, pins)
        if "fonts" in what:
            pin_fonts()


if __name__ == "__main__":
    main()

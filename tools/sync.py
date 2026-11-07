#!/usr/bin/env python3
"""Copy the shared scripts into the skills that use them.

usage: python3 tools/sync.py           copy every shared file into its skills
       python3 tools/sync.py --check   exit 1 if any copy differs from its source

Each skill is its own download, so a script two skills share ships in both. The source
of each one is listed below; edit the source, never a copy, then run this. CI runs
--check, so a copy that drifts fails the build.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PYTHON_KIT = ["killer-bugs", "killer-tests", "killer-docs", "killer-perf", "killer-release", "killer-upgrade", "killer-goal"]

SHARED = {
    "tools/shared/kit.py": PYTHON_KIT,
    "tools/shared/run.py": PYTHON_KIT,
    "skills/killer-design/scripts/fonts.mjs": ["killer-slides"],
}


def pairs():
    for src, skills in SHARED.items():
        for skill in skills:
            folder = ROOT / "skills" / skill
            if folder.is_dir():
                yield ROOT / src, folder / "scripts" / Path(src).name


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report copies that differ, change nothing")
    a = ap.parse_args()
    drift = 0
    for src, dst in pairs():
        want = src.read_bytes()
        have = dst.read_bytes() if dst.is_file() else None
        if have == want:
            continue
        rel = dst.relative_to(ROOT).as_posix()
        if a.check:
            print(f"{rel} differs from {src.relative_to(ROOT).as_posix()}" if have is not None else f"{rel} is missing")
            drift += 1
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(want)
            print(f"copied {src.relative_to(ROOT).as_posix()} -> {rel}")
    if a.check:
        print("shared copies match their sources" if not drift else f"{drift} copies differ: run python3 tools/sync.py")
    sys.exit(1 if drift else 0)


if __name__ == "__main__":
    main()

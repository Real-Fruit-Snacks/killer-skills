# Install recipes

`release.py smoke` installs the artifacts from the last `build` the way a user would, in a sandbox that holds nothing of the project: an empty working folder with its own home, temp and app-data folders, no tokens or keys, a dead proxy for the network, and the user's own Python and npm settings hidden (`PYTHONPATH`, the user site, pip and npm config, virtualenv and conda settings, global install folders). Then it runs what a new user runs first.

```
py <skill-dir>/scripts/release.py smoke -w <run> --install <recipe> [options] -- <first command>
```

What every recipe shares:

- **The first command must print the new version.** Make it `<tool> --version` or the nearest thing. The script looks for the exact version (`0.2.0` isn't `10.2.0` or `0.2.0rc1`) in its output. `--no-version-check "<why>"` turns that off, for a release that has no version to print; say why, it goes in the report.
- **Then one real command:** `--then "<command>"` (repeatable), each must exit 0. `--expect "<text>"` (repeatable) must appear in the install's or the commands' output.
- **The command has to be the installed one.** If `mytool` on the PATH turns out to be a copy outside the sandbox (an old global install, say), the smoke fails: it would be testing the wrong thing. The same goes for `python -m <module>` importing from outside. Interpreters (`python`, `node`, `sh`, ...) may come from the machine.
- **A bare `python`** that finds nothing, or Windows' Microsoft Store stub, is swapped for the Python running `release.py`, with a note.
- **Placeholders:** `{home}` (the sandbox's home), `{work}` (its working folder) and `{sandbox}` work in commands, `--env` values and `--bin-dir`.
- **Network off** unless `--net`. If dependencies can't be found offline, the failure says so: give `--wheelhouse <folder>` (pip) or name what's missing, or run with `--net` and record why with `decide`. The report lists every install that went online.
- **Labels:** each smoke is recorded under `--label` (default `<recipe> on <platform>`); a new run with the same label replaces the old result. The gate wants every recorded smoke to pass against the current build.
- `--cwd <folder>` runs the commands from a folder inside the working folder, `--keep` keeps the sandbox to look inside, `--timeout` and `--install-timeout` cap the steps.

## pip

```
smoke -w <run> --install pip [--name <package>] [--wheelhouse <folder>] [--python <exe>] -- mytool --version
```

A new virtual environment, and `pip install --no-index --find-links <artifacts>` of the wheel (or of `--name`, letting pip pick among several platform wheels; an sdist only when there's no wheel). With pip 22.3 or later it makes the environment with `--without-pip` and installs with this Python's pip (`pip --python <env>`): the same install, without the 20 seconds `ensurepip` takes. Older pip: a normal venv and its own pip. A Python with no `venv` (Windows' embeddable package) but with pip: `pip install --target` with `PYTHONPATH`, said in the result. No venv and no pip: it stops and asks for a full Python with `--python`.

Dependencies: a pure package with none installs offline as is. Otherwise build a wheelhouse once, with the network, from the release's own requirements (`pip download -d <folder> <the dependencies>`), and pass `--wheelhouse <folder>`.

## npm

```
smoke -w <run> --install npm -- mytool --version
```

A throwaway `package.json` in the working folder, then `npm install --offline <the .tgz>` (from `npm pack`). The package's bins land in `node_modules/.bin`, first on the PATH. Dependencies need npm's cache or `--net`; the sandbox's npm cache starts empty.

## archive

```
smoke -w <run> --install archive [--artifact <name>] [--cwd <top folder>] -- <the unpacked tool> --version
```

Unzips or untars each chosen archive (`.zip`, `.tar.gz`, `.tgz`, `.tar.xz`, `.tar.bz2`) into the working folder, refusing entries that would land outside it. The working folder, its `bin/`, each top-level folder and its `bin/` go first on the PATH. Good for anything shipped as a zip or tarball, and for Python apps with a launcher script (`python greet-tool-1.2.0/greet.py --version`).

## script

```
smoke -w <run> --install script --script install.sh [--script-args "..."] [--env NAME=VALUE ...] -- <what a user runs next>
```

Copies every artifact into the working folder, the way a user downloads a release into one folder, and runs the installer from there: `sh` for `.sh`, Windows PowerShell for `.ps1` (`pwsh` elsewhere), the running Python for `.py`, `cmd` for `.cmd`/`.bat`. The smoke command runs after it.

Do the side-effect check on the installer first. It runs with the sandbox's home, but nothing redirects the Windows registry, the user's PATH, services or scheduled tasks, and PowerShell 5.1 ignores the dead proxy. Give it its own settings pointing into the sandbox (`--env INSTALL_DIR={home}/tools`), or don't smoke it here and say so in the report.

This repo's own installers are a worked example. `install.sh` and `install.ps1` install from a folder that holds the release's `SHA256SUMS.txt`, and print what they installed, not a version:

```
build -w <run> --artifacts "dist/*" install.sh install.ps1 -- python -I tools/package.py --out dist
smoke -w <run> --install script --script install.sh --env KILLER_SKILLS_RUNTIME=skip \
  --no-version-check "the installer prints no version; the skills carry none" \
  --expect "installed killer-bugs" -- python -c "import os, sys; sys.exit(0 if os.path.isfile(os.path.expanduser('~/.claude/skills/killer-bugs/SKILL.md')) else 1)"
smoke -w <run> --install script --script install.ps1 --label "install.ps1 on windows" \
  --env "KILLER_SKILLS_DIR={home}/.claude/skills" "KILLER_SKILLS_CACHE={home}/.cache" KILLER_SKILLS_RUNTIME=skip \
  --no-version-check "the installer prints no version" --expect "installed killer-bugs" -- python -c "print('ok')"
```

## binary

```
smoke -w <run> --install binary --artifact mytool -- mytool --version
```

Copies the file into `bin/` in the working folder, marks it executable, and puts `bin/` first on the PATH. For `target/release/<name>`, Go binaries and other single files.

## Versions computed from tags

With setuptools-scm, hatch-vcs and the like, the version only exists once the tag does. Build with `build --tag-in-sandbox`: the sandbox's copy gets the release tag before the build, so the artifacts carry the new version, and the real project gets no tag until `publish`.

## Through CI

If the user chose "Also through the project's CI", the push that starts it is outward: record it with `wait --command "git push -u origin killer-release/v<version>"` and ask before running it. Its result is the CI's word, not a smoke the script ran; report it as such.

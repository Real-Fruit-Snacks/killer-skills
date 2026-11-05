#!/bin/sh
# Install killer-skills into ~/.claude/skills, from the latest release.
#
#   curl -fsSL https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.sh | sh
#
# Name skills to install only those:
#
#   curl -fsSL https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.sh | sh -s -- killer-repo
#
# Each archive is checked against the release's SHA256SUMS.txt before anything
# is replaced. A skill that's already installed is replaced whole, so files a
# newer version dropped don't linger.
#
#   KILLER_SKILLS_VERSION=v1.0.0   a release tag instead of the latest
#   KILLER_SKILLS_DIR=path         install somewhere other than ~/.claude/skills
#
# The runtime that saves killer-design and killer-repo their first-use downloads
# (Playwright, Chromium, Mermaid) is built for Windows only, and install.ps1 installs it.
set -eu

repo=Real-Fruit-Snacks/killer-skills
bundle=killer-skills
version=${KILLER_SKILLS_VERSION:-latest}
if [ "$version" = latest ]; then
  base=https://github.com/$repo/releases/latest/download
else
  base=https://github.com/$repo/releases/download/$version
fi
base=${KILLER_SKILLS_URL:-$base}   # for tests: a folder of release files served over http
dest=${KILLER_SKILLS_DIR:-$HOME/.claude/skills}

fail() { printf 'failed: %s\n' "$*" >&2; exit 1; }

if command -v curl >/dev/null 2>&1; then
  # --retry covers GitHub's occasional 5xx on release downloads (about 30 s of backoff)
  fetch() { curl -fsSL --retry 5 -o "$2" "$1" || fail "could not download $1"; }
elif command -v wget >/dev/null 2>&1; then
  fetch() { wget -q -O "$2" "$1" || fail "could not download $1"; }
else
  fail "needs curl or wget"
fi
if command -v sha256sum >/dev/null 2>&1; then
  sha256() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
  sha256() { shasum -a 256 "$1" | cut -d' ' -f1; }
else
  fail "needs sha256sum or shasum to check the downloads"
fi
command -v tar >/dev/null 2>&1 || fail "needs tar"

tmp=$(mktemp -d 2>/dev/null || mktemp -d -t killer-skills)
trap 'rm -rf "$tmp"' EXIT INT TERM

fetch "$base/SHA256SUMS.txt" "$tmp/SHA256SUMS.txt"
available=$(sed -n 's/^[0-9a-f]\{64\}  \(.*\)\.tar\.gz$/\1/p' "$tmp/SHA256SUMS.txt" | grep -v "^$bundle\$" || true)
[ -n "$available" ] || fail "no skills listed in $base/SHA256SUMS.txt"
[ $# -gt 0 ] || set -- $available

for name in "$@"; do
  case $name in
    *[!a-z0-9-]* | '' | -*) fail "'$name' isn't a skill name. Available: $(echo $available)" ;;
  esac
  printf '%s\n' "$available" | grep -qx "$name" || fail "no skill called $name in this release. Available: $(echo $available)"
done

mkdir -p "$dest"
count=0
for name in "$@"; do
  archive=$tmp/$name.tar.gz
  fetch "$base/$name.tar.gz" "$archive"
  want=$(sed -n "s/^\([0-9a-f]\{64\}\)  $name\.tar\.gz\$/\1/p" "$tmp/SHA256SUMS.txt")
  [ "$(sha256 "$archive")" = "$want" ] || fail "$name.tar.gz doesn't match SHA256SUMS.txt; nothing was changed for $name"
  mkdir "$tmp/x-$name"
  tar -xzf "$archive" -C "$tmp/x-$name"
  [ -f "$tmp/x-$name/$name/SKILL.md" ] || fail "$name.tar.gz has no $name/SKILL.md"
  if [ -e "$dest/$name" ]; then verb=updated; rm -rf "$dest/$name"; else verb=installed; fi
  mv "$tmp/x-$name/$name" "$dest/$name"
  printf '%s %s\n' "$verb" "$name"
  count=$((count + 1))
done

case $(uname -s 2>/dev/null) in
  MINGW* | MSYS* | CYGWIN*)
    case " $* " in
      *" killer-design "* | *" killer-repo "*)
        if [ "${KILLER_SKILLS_RUNTIME:-}" != skip ] && grep -q '  killer-skills-runtime-windows-x64\.zip$' "$tmp/SHA256SUMS.txt"; then
          printf 'the Windows runtime (Playwright, Chromium, Mermaid) installs with install.ps1; without it, killer-design and killer-repo download Playwright on first use\n'
        fi ;;
    esac ;;
esac

case $dest in
  "$HOME"/*) shown="~${dest#"$HOME"}" ;;
  *) shown=$dest ;;
esac
if [ "$count" -eq 1 ]; then noun=skill; them=it; else noun=skills; them=them; fi
printf '%s %s in %s\n' "$count" "$noun" "$shown"
printf 'open a new Claude Code session to use %s\n' "$them"

#!/bin/sh
# Install killer-skills into ~/.claude/skills, from the latest release.
#
#   curl -fsSL https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.sh | sh
#
# Name skills to install only those:
#
#   curl -fsSL https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.sh | sh -s -- killer-repo
#
# Offline: download the release's files into one folder (this script, SHA256SUMS.txt and
# the .tar.gz files) and run it from there. It installs from that folder and downloads
# nothing:
#
#   sh install.sh [skill ...]
#
# Each archive is checked against the release's SHA256SUMS.txt before anything is
# replaced. A skill that's already installed is replaced whole, so files a newer version
# dropped don't linger.
#
# With killer-design, killer-repo or killer-slides it also installs the font pack (the web fonts
# killer-design suggests, to self-host) into ~/.cache/killer-skills-fonts, unless the same
# one is already there. The Windows packs (Playwright with Chromium and Mermaid, Node.js,
# Python) install with install.ps1.
#
#   KILLER_SKILLS_VERSION=v1.0.0   a release tag instead of the latest
#   KILLER_SKILLS_DIR=path         install somewhere other than ~/.claude/skills
#   KILLER_SKILLS_RUNTIME=skip     the skills only, no font pack
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
caches=${KILLER_SKILLS_CACHE:-$HOME/.cache}

fail() { printf 'failed: %s\n' "$*" >&2; exit 1; }
show() {
  case $1 in
    "$HOME"/*) printf '~%s' "${1#"$HOME"}" ;;
    *) printf '%s' "$1" ;;
  esac
}

# Run as a file (not piped in) from a folder of this release's files, whose SHA256SUMS.txt
# lists the bundle, it installs from that folder.
here=
case $0 in
  *install.sh)
    d=$(dirname -- "$0")
    if [ -f "$d/SHA256SUMS.txt" ] && grep -q "^[0-9a-f]\{64\}  $bundle\.tar\.gz\$" "$d/SHA256SUMS.txt"; then
      here=$(cd -- "$d" && pwd)
    fi ;;
esac

if [ -n "$here" ]; then
  fetch() { [ -f "$here/$1" ] || fail "$1 isn't in $(show "$here")"; cp "$here/$1" "$2"; }
elif command -v curl >/dev/null 2>&1; then
  # --retry covers GitHub's occasional 5xx on release downloads (about 30 s of backoff)
  fetch() { curl -fsSL --retry 5 -o "$2" "$base/$1" || fail "could not download $base/$1"; }
elif command -v wget >/dev/null 2>&1; then
  fetch() { wget -q -O "$2" "$base/$1" || fail "could not download $base/$1"; }
else
  fail "needs curl or wget"
fi
if command -v sha256sum >/dev/null 2>&1; then
  sha256() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
  sha256() { shasum -a 256 "$1" | cut -d' ' -f1; }
else
  fail "needs sha256sum or shasum to check the archives"
fi
command -v tar >/dev/null 2>&1 || fail "needs tar"

tmp=$(mktemp -d 2>/dev/null || mktemp -d -t killer-skills)
stage=
trap 'rm -rf "$tmp"; [ -z "$stage" ] || rm -rf "$stage"' EXIT INT TERM

# The hash SHA256SUMS.txt lists for a file.
want() { awk -v f="$1" '$2 == f && length($1) == 64 { print $1 }' "$tmp/SHA256SUMS.txt"; }

# Swap a finished folder in. The old one is moved aside first, so a folder that's in use
# stops the install with nothing changed.
swap() {
  if [ -e "$2" ]; then
    old=$(dirname -- "$2")/.killer-skills-old-$$
    rm -rf "$old"
    mv "$2" "$old" || fail "$(show "$2") is in use, probably by a Claude Code session running one of its scripts. Close it and run the installer again; nothing was changed there."
    mv "$1" "$2"
    rm -rf "$old" || true
    verb=updated
  else
    mv "$1" "$2"
    verb=installed
  fi
}

[ -z "$here" ] || printf 'installing from %s\n' "$(show "$here")"
fetch SHA256SUMS.txt "$tmp/SHA256SUMS.txt"
available=$(sed -n 's/^[0-9a-f]\{64\}  \(.*\)\.tar\.gz$/\1/p' "$tmp/SHA256SUMS.txt" | grep -v -e "^$bundle\$" -e "^$bundle-" || true)
[ -n "$available" ] || fail "no skills listed in SHA256SUMS.txt"
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
  fetch "$name.tar.gz" "$archive"
  [ "$(sha256 "$archive")" = "$(want "$name.tar.gz")" ] || fail "$name.tar.gz doesn't match SHA256SUMS.txt; nothing was changed for $name"
  mkdir "$tmp/x-$name"
  tar -xzf "$archive" -C "$tmp/x-$name"
  [ -f "$tmp/x-$name/$name/SKILL.md" ] || fail "$name.tar.gz has no $name/SKILL.md"
  swap "$tmp/x-$name/$name" "$dest/$name"
  printf '%s %s\n' "$verb" "$name"
  count=$((count + 1))
done

# A pack into its folder in the cache, unless the same one is already there.
#   install_pack <asset> <folder> <what> <manifest>
install_pack() {
  target=$caches/$2
  hash=$(want "$1")
  if [ -f "$target/installed-sha256.txt" ] && [ "$(tr -d '\r\n' < "$target/installed-sha256.txt")" = "$hash" ]; then
    printf '%s already in %s\n' "$3" "$(show "$target")"
    return 0
  fi
  if [ -n "$here" ] && [ ! -f "$here/$1" ]; then
    printf "skipped %s: %s isn't in %s\n" "$3" "$1" "$(show "$here")"
    return 0
  fi
  [ -n "$here" ] || printf 'downloading %s ...\n' "$3"
  fetch "$1" "$tmp/$1"
  [ "$(sha256 "$tmp/$1")" = "$hash" ] || fail "$1 doesn't match SHA256SUMS.txt; $3 wasn't changed"
  mkdir -p "$caches"
  stage=$caches/.$2-$$   # beside the target, so the swap is a rename on the same filesystem
  rm -rf "$stage"
  mkdir "$stage"
  tar -xzf "$tmp/$1" -C "$stage"
  [ -f "$stage/$4" ] || fail "$1 has no $4"
  printf '%s\n' "$hash" > "$stage/installed-sha256.txt"
  swap "$stage" "$target"
  stage=
  n=$(sed -n 's/^  "count": \([0-9][0-9]*\),*$/\1/p' "$target/$4")   # the font pack's family count
  printf '%s %s%s, in %s\n' "$verb" "$3" "${n:+: $n families}" "$(show "$target")"
}

browser=no
council=no
for name in "$@"; do
  case $name in
    killer-design | killer-repo | killer-slides) browser=yes ;;
    killer-council | killer-bugs | killer-tests | killer-docs | killer-perf | killer-release | killer-upgrade | killer-goal) council=yes ;;
  esac
done
if [ "${KILLER_SKILLS_RUNTIME:-}" != skip ]; then
  if [ "$browser" = yes ] && [ -n "$(want killer-skills-fonts.tar.gz)" ]; then
    install_pack killer-skills-fonts.tar.gz killer-skills-fonts "the font pack" fonts.json
  fi
  case $(uname -s 2>/dev/null) in
    MINGW* | MSYS* | CYGWIN*)
      if { [ "$browser" = yes ] || [ "$council" = yes ]; } && [ -n "$(want killer-skills-runtime-windows-x64.zip)" ]; then
        printf 'the Windows packs (Playwright with Chromium and Mermaid, and Node.js or Python if they are missing) install with install.ps1; without them, killer-design, killer-repo and killer-slides download Playwright on first use\n'
      fi ;;
  esac
fi

if [ "$count" -eq 1 ]; then noun=skill; them=it; else noun=skills; them=them; fi
printf '%s %s in %s\n' "$count" "$noun" "$(show "$dest")"
printf 'open a new Claude Code session to use %s\n' "$them"

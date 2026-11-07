# What the script reads and checks

## Version sites (`init`, `versions`, `bump`)

A version site is one place the project's own version is written down, with its file, line and text. Each has a group:

| Group | Where | Bumped | Must agree |
|---|---|---|---|
| manifest | `pyproject.toml` `[project]` or `[tool.poetry]`, `package.json`, `Cargo.toml` `[package]` or `[workspace.package]`, `setup.cfg` `[metadata]`, `setup.py`, `VERSION` / `version.txt`, `.csproj` `<Version>`, `.gemspec`, `.nuspec`, `Chart.yaml`; the file a dynamic version reads (`attr:` / `file:` / hatch `path`) | yes | yes |
| mirror | `__version__ = "..."` in the package, `VERSION =` in `version.rb`, a Dockerfile `LABEL version` | yes | yes |
| pin | the README, `INSTALL` and `docs/` naming this package at a version: `pip install name==1.2.3`, `name@1.2.3`, `cargo install name --version`, `gem install name -v`, this repo's `/releases/download/v1.2.3/`, `uses: owner/repo@v1.2.3` | yes | yes |
| lock | `package-lock.json`, `npm-shrinkwrap.json`, `Cargo.lock`, `uv.lock`: the project's own entry | by its own tool, in a sandbox | yes |
| changelog | the newest version heading | dated by `bump` | yes, or ahead of it while undated |
| example | `SOMETHING_VERSION=v1.0.0`-style examples in docs and top-level scripts | only with `--include-examples` | no |

The version the others must agree with is the release's (after `bump`), otherwise the manifest's, otherwise the last tag's. Folders like `tests/`, `examples/`, `vendor/`, `node_modules/`, `dist/` and dot-folders are skipped. In a monorepo, `init --package <folder>` limits the manifests to that package and reads tags like `name@1.2.3` or `name-v1.2.3`.

A version computed from tags (setuptools-scm, hatch-vcs, pdm's scm source, poetry-dynamic-versioning, semantic-release's `0.0.0-development`) has no manifest sites: the tag is the bump.

## How bump counts

`patch`, `minor` and `major` count from the last release (the newest version tag in this branch's history), the way `npm version` does: from `1.3.0-rc.1`, `patch` and `minor` give `1.3.0`. `--pre rc` makes a pre-release and counts up (`rc.1`, `rc.2`); Python projects get PEP 440 spelling (`1.3.0rc1`). On 0.x, a minor bump is the breaking one: `^0.1.0` ranges won't pick up `0.2.0` by themselves. `bump` refuses a version that isn't newer than the last release, or older than what the manifest already says.

The changelog: a section for the version that says "unreleased" gets today's date in its own style; otherwise an `Unreleased` section becomes the version's section, with a fresh empty `Unreleased` above it in Keep a Changelog files and the compare links moved. It never writes entries.

## How changelog-check matches

Each entry (a top-level bullet in the section, nested bullets included) is matched to a commit when they share a PR number, when the entry names an option, file or identifier the commit's message or diff contains, or when they share two words that aren't filler (or one rare long one). Entries are matched to surface changes when they name them. The changelog file's own lines never count as evidence. Commits that only touch tests, CI or the changelog are internal and need no entry. Words can match by accident and miss a paraphrase: the map is for the lead and the reviewer, not a verdict.

## The gate (`check`)

| Item | Passes when |
|---|---|
| versions | every site at the release commit says the release's version |
| changelog | it has a section for the version, with entries, not marked unreleased, dated (or undated like every earlier section) |
| tag | the tag doesn't exist here; on the remote too when the network is allowed (otherwise `publish` checks right before pushing it) |
| tree | the checkout is the release branch with nothing uncommitted |
| tests | the test command passes in a fresh sandbox of the release commit (`--no-tests "<why>"` skips it, said in the report) |
| build | the last build was of the release commit, and the artifacts' bytes still match their hashes |
| smoke | every recorded clean install passed, against this build's bytes |
| attribution | no AI credit in the release commits (messages, authors and added lines), `notes.md` or the changelog section |
| outward | no tag made, nothing pushed or fetched, and the user's branch unmoved since the run began, except by `publish` |
| review | no open unsupported, overstated, missing or stuck finding (skipped when no reviewer ran) |

## AI attribution

Always caught: `Co-Authored-By:` trailers, Anthropic's noreply address, the robot emoji, "generated with/by" or "written/drafted/made with" followed by an AI (Claude, ChatGPT, GPT, Copilot, Gemini, an AI, an LLM), and Claude Code links. "Claude" or "Anthropic" on its own is caught too, unless the project's README already names them, which makes them the product's name (a project of Claude Code skills, say), not a credit. "Docs generated with pdoc" is not attribution and isn't flagged. `release.py commit` refuses a message with any of these before it reaches the history.

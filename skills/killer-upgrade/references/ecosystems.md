# Ecosystems

What `upgrade.py` recognises, the commands it runs for each one, and how to describe one it doesn't know.

## The package managers

| Manager | Found by | Install for the baseline | What's out of date | Patch and minor | Major |
|---|---|---|---|---|---|
| npm | `package.json` + `package-lock.json`, workspaces from `"workspaces"` | `npm ci --ignore-scripts` | `npm outdated --json --long` (`--workspaces --include-workspace-root` in a monorepo); `npm audit --json` | `npm install --package-lock-only <name>@<to>`, with `-w <workspace>`, `--save-dev`, and the range style kept (`--save-exact`, `--save-prefix=~`) | the same, to the new major |
| pnpm | `pnpm-lock.yaml`, workspaces from `pnpm-workspace.yaml` | `pnpm install --frozen-lockfile --ignore-scripts` | `pnpm outdated --format json` (`-r`); `pnpm audit --json` | `pnpm add <name>@<to>`, with `--filter <workspace>`, `-D` | the same |
| yarn 1 | `yarn.lock` | `yarn install --frozen-lockfile` | `yarn outdated --json` | `yarn add <name>@<to>`, or `yarn workspace <ws> add`, with `--dev` | the same |
| yarn 2+ | `yarn.lock` with `.yarnrc.yml` | `yarn install --immutable` | nothing: yarn 2+ has no outdated command, so add steps by hand | `yarn up <name>@^<to>` | the same |
| pip | `requirements*.txt`, `constraints*.txt`, `requirements/*.txt`, or `[project] dependencies` in `pyproject.toml` | `baseline --venv`, then `python -m pip install -r requirements.txt` | `python -m pip list --outdated --format=json` in the sandbox's venv, direct dependencies only; PyPI for the newest version in the current major; `pip-audit` when it's installed | the pinned line (`==`, `~=`), or the range that stops the new version, edited by the script | the same edit |
| uv | `uv.lock` | `uv sync --frozen` | `uv pip list --outdated --format json` | `uv lock --upgrade-package <name>==<to>` | `uv add --no-sync '<name>>=<to>'`, with `--dev`, `--group` or `--optional` where it's declared |
| poetry | `poetry.lock`, or `[tool.poetry]` | `poetry install --no-interaction`, with `init --env POETRY_VIRTUALENVS_IN_PROJECT=true` | `poetry show --outdated --top-level` | `poetry add --lock <name>@^<to>`, with `--group` | the same |
| Cargo | `Cargo.toml` + `Cargo.lock`, with workspace members | `cargo fetch` | `cargo outdated --format json --root-deps-only` when cargo-outdated is installed, else `cargo update --dry-run` (which finds no majors); `cargo audit --json` when installed | `cargo update -p <name>@<from> --precise <to>` | the version in `Cargo.toml` edited by the script, then `cargo update -p <name>` |
| Go | `go.mod` + `go.sum` | `go mod download` | `go list -m -u -json all`, direct modules only | `go get <module>@<to>` | not found by `go list`: a new major changes the module path (`/v2`). Add it by hand |
| Bundler | `Gemfile` + `Gemfile.lock` | `bundle install` (gems go inside the sandbox) | `bundle outdated --parseable --only-explicit` | `bundle update --conservative <gem>` | the `Gemfile` constraint edited by the script, then the same |
| .NET | `*.csproj`, `*.fsproj`, `*.vbproj` with `PackageReference` | `dotnet restore` | `dotnet list <project> package --outdated --format json`, and again with `--highest-minor` | `dotnet add <project> package <id> --version <to>` | the same |
| Maven, Gradle, pipenv, pdm, bun | `pom.xml`, `build.gradle(.kts)`, `Pipfile`, `pdm.lock`, `bun.lock` | | listed only | add steps by hand, or describe the tool with `custom` | |

Some things that hold for all of them:

- **Upgrades change the manifest and lockfile; the try installs from the lockfile.** npm's `--package-lock-only` and poetry's `--lock` don't touch the installed packages; the others install them, which is harmless, because `node_modules` and virtual environments stay out of the patch. The install that follows is the clean one CI would run (`npm ci`), which is what shows the lockfile is consistent.
- **What a step's patch keeps:** the files its upgrade commands and edits changed, plus any manifest or lockfile the install rewrote. Anything else the install wrote (generated code, say) is left out, and `try` says so.
- **Install scripts are code from the internet.** npm's `preinstall`, `install` and `postinstall`, Python's `setup.py`, Cargo's `build.rs` all run during an install. init suggests `--ignore-scripts` for npm and pnpm when the project's own `package.json` has no install hooks. If the baseline fails with it (a native module that must compile, say), drop it and say so in the announcement.
- **Sandboxes share a package cache within the run** (npm, pnpm, yarn, pip, uv, poetry), so only the first install downloads everything. `clean` deletes it. Cargo, Go and NuGet use their usual caches in your home folder, which the kit passes through.
- **Global installs stay in the sandbox:** `npm_config_prefix` and `BUNDLE_PATH` point inside it.

## Workspaces and monorepos

- **npm, pnpm, yarn:** one ecosystem at the workspace root. Members come from its workspace globs. Each upgrade runs with the workspace flag for the member that declares it, and outdated runs once from the root over every workspace.
- **Cargo:** one ecosystem per workspace root. A major edits the member's `Cargo.toml` that declares the crate.
- **Go:** one ecosystem per `go.mod`.
- Separate roots with their own lockfiles (two apps in one repository) are separate ecosystems. Their upgrades share the batches.
- A `package.json` with no lockfile that isn't a workspace member (a fixture, an example) is left out, and init says which.

## Packages that move together

Packages released together move to the same version in the same step: one npm scope (`@babel/core` and `@babel/preset-env`), or one name extending another (`react` and `react-dom`). Bisect treats them as one item, so it never tries `react` 19 without `react-dom` 19.

## No lockfile

Upgrades still work, but each sandbox resolves the dependencies afresh, so what was tested isn't exactly what lands. init says so. Offer to add a lockfile only when the project's docs suggest one (a CONTRIBUTING that says to commit it, a CI step that expects it). Adding one is a change of its own: make it by hand, and say so in the report.

## Private registries

Sandboxes drop credentials (tokens, keys and passwords in environment variables), and they have their own home folder, so `~/.npmrc`, `~/.pypirc` and `pip.conf` in your real home aren't there. A package from a private registry fails to install, as install-fail. init lists the project files that point at one (`.npmrc`, `.yarnrc.yml`, `pip.conf`, `poetry.toml`, `nuget.config`, `.cargo/config.toml`). Never copy credentials into a sandbox. Hold those packages with the reason "from a private registry: can't be checked here", so the report says so.

## Runtimes

- **Pins** (`.nvmrc`, `.node-version`, `.python-version`, `.ruby-version`, `rust-toolchain(.toml)`, `.tool-versions`, Go's `toolchain` line) are what the project develops and tests on. With `init --also runtimes`, outdated looks up the newest release (Node.js's latest LTS from nodejs.org, Python from endoflife.date, Go from go.dev, Rust's stable channel) and makes one step for each. It keeps the pin's precision: a pin of `18` gets a bare major back, `3.11` a major and minor, `20.11.1` a full version. Ruby has no lookup: add a step by hand.
- **Minimums** (`engines.node`, `requires-python`, Go's `go` line) are what your users need. Raising one drops support for them, so they're listed and never changed unless the user asks (add a step by hand).
- **A runtime step only counts if the tests ran on the new version.** Before the install, try runs a probe (`node --version`, `python --version`, `go version`, `rustc --version`) in the sandbox. If its output doesn't show the new version, the step is install-fail: the toolchain on this machine doesn't follow the pin (no nvm, fnm, volta or mise shim; no rustup; `GOTOOLCHAIN=local`). Hold it with that reason. rustup follows `rust-toolchain` files, and Go 1.21 and later fetches the toolchain it's asked for, so those can often be checked. For a step added by hand, give the probe with `add --probe '<command>' --to <version>`.

## CI actions and base images

- **`uses: owner/repo@vN` pins:** with `init --also actions` and `gh` logged in, outdated asks `gh api repos/<owner>/<repo>/releases/latest` and keeps the pin's precision (`v3` becomes `v4`, `v3.5.2` becomes `v4.1.1`). Pins to a commit or a branch are left alone. The sandbox doesn't run CI, so an action step passes trivially: the commit message and the report say so, and the first CI run is the real check.
- **Dockerfile `FROM` tags** are listed; no registry is asked. To move one, add a step, and if Docker is available, put a `docker build` in the baseline's build commands so the step is really checked:
  ```
  upgrade.py add -w <run> --title "Upgrade the node image 18 → 22" --group image --edit Dockerfile "FROM node:18-alpine" "FROM node:22-alpine"
  ```

## An ecosystem upgrade.py doesn't know: custom

```
upgrade.py custom -w <run> --name <name> [--dir <folder>] --manifest <file> [--lockfile <file>] --outdated '<command>' --apply '<command>'
```

- **`--outdated`** runs in the sandbox after the baseline's install, from `--dir`. It prints JSON on stdout: a list, or `{"packages": [...]}`, of `{"name", "current", "latest", "wanted", "dev", "advisories"}`. Only the first three are required. `wanted` is the newest version the manifest's range allows. `advisories` is a list of ids, or of `{"id", "severity", "fixed_in"}`. Its exit code doesn't matter, and stderr is ignored.
- **`--apply`** applies one upgrade, with `{name}`, `{version}` and `{from}` filled in. It changes the manifest and lockfile and nothing else.
- Write a command as a JSON list (`'["python", "tools/deps.py", "add", "{name}=={version}"]'`) when an argument has spaces or backslashes; otherwise as plain words.
- Its upgrades are classified, planned, tried and bisected like any other. On the landed branch, `--outdated` runs again to see which advisories are left.

## Reading test output

Per-test results come from what the test command prints: pytest (`-v`, or `-rA` for the summary), `unittest -v`, `go test -v`, `cargo test`, TAP (`node --test`, tap), jest, vitest, mocha and node's spec reporter. With per-test results, a test that already failed at the baseline doesn't count against an upgrade, and a new failure is named.

A runner that prints none of these is judged by its exit code alone. That's fine while it passes at the baseline, and useless when it already fails there: baseline warns, and the report lists it as unchecked. Make it print per-test results, or give `baseline --junit '<glob>'` to read the JUnit XML files it writes (jest-junit, `pytest --junitxml`, gotestsum, `dotnet test --logger junit`).

A test that passed at the baseline and is now skipped, or no longer runs, counts as broken.

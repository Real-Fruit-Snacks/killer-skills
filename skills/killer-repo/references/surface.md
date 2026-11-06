# The rest of the repo page

Everything public here changes what everyone sees as soon as it's applied, so show the user and get an OK first.

## About panel

```
gh repo edit <owner>/<repo> --description "<one line>" --homepage "<url>"
gh repo edit <owner>/<repo> --add-topic <a>,<b> --remove-topic <c>
```

- **Description:** up to 350 characters. Search results and link previews cut it short, so the hook goes first. Usually it's the README's one-liner.
- **Topics:** up to 20, each lowercase letters, numbers and hyphens, at most 50 characters. Use what people search for: the problem domain, the platform, the language, and well-known tools it works with. Check GitHub's topic pages for the established spelling: one popular topic beats three variants nobody follows.
- **Website:** the live app, the docs site or the GitHub Pages site. Leave it empty rather than pointing back at the repo.

## Social card

- 1280×640 is the recommended size, 640×320 the minimum. PNG, JPG or GIF, under 1 MB.
- There's no API for it. Upload it at Settings → General → Social preview → Edit, or through a browser the user is logged into, if they ask you to.
- It's often seen at around 400px wide in feeds and chats, so it needs one big idea: the name, one line and one visual. Keep important content away from the edges, because some platforms crop.
- It's a single image with no dark variant, so pick a palette that sits well in both light and dark feeds.
- Build it in HTML and render it with `node <skill-dir>/scripts/card.mjs card.html --out docs/assets/social.png`. Commit the HTML next to the PNG so the card can be remade.
- Self-host the card's fonts so it renders offline and the same every time: with killer-design installed, `node <killer-design-dir>/scripts/fonts.mjs add docs "Family"` copies a face from the killer-skills font pack next to the HTML and writes `docs/assets/fonts/fonts.css`.

## Releases

- Notes should tell users what changed *for them*, not list commits. Put upgrade steps and breaking changes first.
- Attach built assets with checksums when people download binaries.
- `gh release edit <tag> --notes-file notes.md` rewrites published notes. That's public, so ask first.
- `.github/release.yml` sorts GitHub's auto-generated notes into categories by label:

  ```yaml
  changelog:
    categories:
      - title: New
        labels: [feature]
      - title: Fixed
        labels: [bug]
      - title: Other changes
        labels: ["*"]
  ```

## Community files

Add only what fits the project. An empty-looking contributing guide on a personal tool is noise.

| File | What it does | When it fits |
|---|---|---|
| `LICENSE` | Lets others legally use the code | Always, but choosing one is the user's legal decision, so ask; never pick for them |
| `SECURITY.md` | How to report a vulnerability privately | Anything security-relevant, or anything with real users |
| `CONTRIBUTING.md` | How to contribute, and whether contributions are wanted at all | When outside contributions are welcome, or to say plainly that they aren't |
| `.github/ISSUE_TEMPLATE/*.yml` | Issue forms with fields and dropdowns | Tools people report bugs against: ask for version, platform and steps |
| `.github/ISSUE_TEMPLATE/config.yml` | Turns off blank issues; adds contact links | Alongside the forms, to route questions somewhere else |
| `.github/pull_request_template.md` | A checklist for pull requests | When others send pull requests |
| `CODE_OF_CONDUCT.md` | Behavior expectations | Projects with a community around them |
| `.github/FUNDING.yml` | The Sponsor button | When the maintainer takes sponsorship |
| `CITATION.cff` | "Cite this repository" | Research software |
| `.github/dependabot.yml` | Update pull requests for dependencies and Actions | Anything with dependencies or workflows |

A minimal bug report form:

```yaml
name: Bug report
description: Something doesn't work the way the README says
labels: [bug]
body:
  - type: input
    id: version
    attributes: { label: Version, placeholder: v1.2.0 }
    validations: { required: true }
  - type: dropdown
    id: platform
    attributes: { label: Platform, options: [Windows, macOS, Linux] }
    validations: { required: true }
  - type: textarea
    id: what
    attributes: { label: What happened, description: What you ran, what you expected, what you got }
    validations: { required: true }
```

## Workflows

- **CI** that tests what the README claims, on the platforms it names, using a matrix. Its badge is `https://github.com/<owner>/<repo>/actions/workflows/<file>.yml/badge.svg`. Add the badge only once the workflow is green.
- **Releases:** on a tag push, build, compute checksums and run `gh release create` with the assets, so a release can't be forgotten halfway.
- **Regenerating visuals:** rerun the committed demo script when the relevant code changes, then commit the result or open a pull request with it.
- **Link check** on a schedule (lychee is the usual action), because links rot even when the code doesn't.
- **Every new workflow must pass on the branch.** Push, watch the run, fix what fails. A red check on a repo page undoes the rest of the work.

## The root folder

The file list sits above the README, so it's the first thing on the page. Fewer, clearer entries read as care. Move clutter into folders **only when nothing depends on the paths.** Search the repo, its scripts and installers, its docs, and other repos for references first. A raw-URL install line (`…/main/install.ps1`) breaks for everyone the moment the file moves.

## Repo settings worth a look

These are public, so ask first:

- Turn off tabs that would be empty (Wiki, Projects) with `gh repo edit --enable-wiki=false --enable-projects=false`.
- Turn on Discussions if there's a community asking questions in issues.
- Pin the issue people keep looking for, such as a known-issues list or a roadmap.

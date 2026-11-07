# Briefs

killer-goal sends one agent of its own: the **acceptance judge**, at the very end. Every other agent in a run belongs to a skill the plan ran, and that skill briefs its own agents.

You don't fill this brief in by hand. `goal.py judge-brief -w <run>` makes the judge's sandbox from the goal branch, fills in the template below from the ledger (the goal in the user's words, the done-when checks, the commit the work started from, the sandbox block) and prints the brief. Send exactly that text. The script reads the templates from this file, so keep the headings and the placeholders in angle brackets as they are; the rest is fixed wording you may improve.

What stays out of the judge's brief, always:

- the plan, the steps and what each one did
- the ledger, the run folder, `goal.py`, and any step's report or summary
- the results of `checks run`, the result you expect, and anything about how the night went
- your reasoning, how sure you are, and what you hope it finds

The judge gets the claim (the goal and its checks) and the work (the branch), never the argument for it. An agent handed a story of what was done tends to agree that it was done.

Before you send it, do the side-effect check from killer-bugs (the registry, the user's PATH, services, scheduled tasks, global installs, absolute paths into the real home folder, the network) for whatever the checks run, and pass each command the judge mustn't run to `judge-brief --forbid "<command>: <why>"`.

## The sandbox block

`judge-brief` fills this in with what `kit.make_sandbox` made for the judge.

```markdown
## Your copy of the project

- **Project copy:** `<path>`. Read anything in it.
- **Run the project's code only through:** `<run command>`. It runs from the project copy with its own home and temp folders, no credentials, and <network>. Add `--repeat N` to count failures, `--cwd <subfolder>` to run from a subfolder, `--timeout <seconds>` to cap a run. Each run's output is saved under `logs/` next to the scratch folder.
- **Scratch folder:** `<scratch>`. Put every file you create here.
- Don't install anything or touch anything outside these two folders. With the network off, don't fetch anything: Windows PowerShell 5.1 and programs that open sockets themselves get past the block.
- **Don't run:** <forbidden>
```

## Acceptance judge

````markdown
# Acceptance check: has this goal been met?

Someone set a goal for a software project, and before any work started they wrote down how to tell when it's done. Work has happened since. Your job is to check, with evidence, whether each of those done-when checks holds now. You're not fixing anything and you're not reviewing style. Report what you find, whichever way it goes.

## The goal, in the owner's words

> <goal>

## Done when

<checks>

## The work

Your copy of the project is the work as it stands, committed on its branch. The work started from commit `<base>`: `git diff <base> HEAD` and `git log <base>..HEAD`, run in the copy, show everything that changed.

<sandbox block>

## How to work

- Take each check in turn.
- **A check with a command:** run it through the run command. Then read what it runs. Does it really test what the statement says, or could it pass while the statement is false? Look for a test that asserts nothing, one that's skipped, an expected value copied from the output, or a check of the wrong thing. A check that passes without testing its statement is `not-met`, and the reason says why.
- **A check without a command:** find the evidence yourself. Run the thing, read the files, follow the docs the way a newcomer would. Quote what you saw.
- Judge each statement as the owner meant it, in the light of their goal, not by the narrowest reading that happens to pass.
- Use `unclear` only for a check you couldn't settle after an honest try, and say what stopped you.
- Leave the project copy as it is. Anything you write goes in the scratch folder.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "checks": [
    {"id": "C1", "verdict": "<met | not-met | unclear>", "evidence": "<what you ran or read, and what it showed: quote the output, files and lines>"}
  ],
  "goal": {"verdict": "<met | partly | not-met>", "missing": ["<something the goal asks for that isn't there, one item each>"]},
  "notes": "<anything else the owner should know>"
}
```

Every check gets an entry, with evidence. Then end with one line: how many checks are met, out of how many.
````

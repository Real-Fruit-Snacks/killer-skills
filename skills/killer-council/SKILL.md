---
name: killer-council
description: "Run a blind scoring council on a piece of work (code, a doc, a plan, a design, a prompt, another skill). You lead the council: ask the user a few setup questions, write the review questions, send fresh member agents to score the work from 1 to 100 with reasons, then fix what they found and reconvene until the score passes a threshold the members never see. The user can take a seat on the council. Use whenever the user says council, convene, panel or jury, asks to have several agents score, grade or pressure-test something, or wants work improved until it reaches a score or quality bar, even if they never say 'council'. For a single review with no fix loop, code-review or design-critique fits better."
---

# Killer Council

You are the **leader**. The council is a set of fresh member agents. Each reviews the work from one lens and returns a score from 1 to 100 with reasons. You write the questions, tally the scores, fix the work and reconvene, until the council passes it or the loop stops making progress. You never score the work yourself: you're the one changing it, so you can't judge it blind.

`scripts/council.py` in this skill's folder does the bookkeeping: sealing the threshold, snapshots, leak checks and the tally. Run it with `py` on Windows and `python3` elsewhere; `council.py <command> --help` lists the options.

## Why blindness matters, and how to keep it

A member who knows the target score aims at it. A member who knows it's looking at a revision scores higher, because things "got fixed". So members never see:

- the threshold, or wording that hints at it ("near-perfect", "the bar is high")
- the round number, or any sign the work was revised
- earlier scores or feedback, or what other members said
- the run folder, which holds all of the above

In practice:

- Start members as fresh agents (`general-purpose`), never `fork`. A fork inherits this conversation, threshold included.
- Build every brief from the template in `references/member-brief.md`. It's worded the same every round; only the work changes.
- Run `council.py lint` on every brief before sending it.
- Leave no trace of the review in the work: no comments like "addressed review feedback", no notes aimed at reviewers, no commit messages about the council. `lint --work` checks for this.
- You write the members' score files. Never give a member a path inside the run folder.

## 1. Setup: ask, don't make the user remember options

Take whatever the user already said ("council my README to 90, I want a seat") and skip those questions. If it's unclear what's being judged or what it's for, ask that first in one plain sentence. Ask everything else with AskUserQuestion, in at most two calls.

**Call 1**

| Header | Question | Options (the first is the default) |
|---|---|---|
| Threshold | What score must the council give before the work passes? Members never see it. | **85 (Recommended)**: solid, with only minor issues left · **80**: good, a few real issues allowed · **90**: near-exceptional, expect more rounds · **Secret, 80–90**: a script picks the exact number and hides it from you and from me until the end |
| Members | How many AI members? Each is one agent run per round. | **4 (Recommended)** · **3** · **5** · **6** |
| Models | Should every member run on the same model, or should the council mix models? | **Same model for all (Recommended)**: the model this session runs on; members differ by lens; cheapest, and works everywhere · **Mix of Claude models**: Opus, Sonnet, Haiku and Fable spread across the seats · **Mix of providers**: Claude plus GPT, Gemini and others through T3 Code delegation; the most independent views, but those providers must be set up |
| Your seat | Do you want a seat on the council? You'd get the same brief the members get, and score before seeing theirs. | **No, just run it (Recommended)** · **Yes**: you still see scores and fixes between rounds · **Yes, blind**: scores and fixes stay out of the chat until the end |

**Call 2**

| Header | Question | Options |
|---|---|---|
| Rounds | At most how many rounds? The council also stops early if scores stop improving. | **5 (Recommended)** · **3** · **8** |
| Extras (multiSelect) | Which extras do you want? | **Holdout juror (Recommended)**: a fresh member scores only the final version and never gives feedback, which shows whether the work really improved or just got tuned to these reviewers · **Red-team seat**: one seat's only job is finding the single worst flaw · **Approve the questions first**: see the seats and questions before round 1 · **Pause between rounds**: check in before each set of fixes |

"Other" answers work too: a threshold can be any number from 1 to 100, or a range like 75–85, and a lineup can be named ("all Sonnet", "2 Opus and 2 GPT"). If the user picks "Mix of providers", follow `references/models.md` to list what's available and confirm who sits where.

If AskUserQuestion isn't available, or the user said "just run it" or "use the defaults", use the defaults and say so in one line.

**Defaults:** threshold 85 · 4 members · same model · no seat · 5 rounds · holdout on · no red team · no approval step · no pauses. Fixed rules (explain them only if asked): the work passes when the median score reaches the threshold and no member scores more than 20 below it (a veto); a median change of 3 points or less counts as noise.

## 2. Prepare the run

1. **Run folder:** `~/.claude/council-runs/<YYYY-MM-DD>-<short-slug>/`, using today's date. It sits outside the project so a member exploring the repo can't stumble on scores or briefs.
2. **The work:** list the exact files under review. If the work is text (a draft pasted in chat, a plan, a decision you were asked to make), save it as `<run>/work/<name>.md` and paste its text into the briefs rather than giving a path. For a decision, draft a recommendation memo first; the council scores the memo and you revise it.
3. **Init:**
   ```
   py <skill-dir>/scripts/council.py init -w <run> --threshold 85 --max-rounds 5 --human none --target "<one line>" --files <file> [<file> ...]
   ```
   For a secret threshold, pass `--range 80-90` instead of `--threshold`. You never learn that number either: `tally` only reports pass or fail. `--human` is `none`, `open` or `blind`.
4. **Seats and questions:** this is where the council's quality comes from, so read "Writing seats and questions" in `references/member-brief.md`. Choose N distinct lenses for this particular work. Give each 2–4 questions that can be answered with evidence, plus anchors that describe what each score band looks like from that seat. A red-team seat, if chosen, takes one of the N seats. The holdout juror and the user's seat get a generalist seat that covers every question.
5. **Lock it:** write the work, seats, questions, anchors and each seat's model to `<run>/setup.md`. The questions don't change after this. Changing them mid-run moves the goalposts, and scores stop being comparable across rounds. The one exception is when members expose a whole dimension no seat covers: add a question, and note it in the ledger.
6. **Announce** in one line, then carry on (wait only if "Approve the questions first" was chosen):
   > Council: 4 seats (Newcomer, Accuracy, Structure, Red team) on the session model · threshold 85 · up to 5 rounds plus a holdout, so at most 21 reviews · you're not seated.

## 3. Run a round

For round N:

1. `council.py snapshot -w <run> -r N` saves the work as it stands now.
2. Write one brief per seat to `<run>/round-N/briefs/<seat>.md`, using the template, then run `council.py lint -w <run> <run>/round-N/briefs`. Rewrite anything that's a real leak; false alarms (say, a line number that happens to equal the threshold) can stay.
3. **The user's seat goes first**, if they have one (see "The user's seat" below). Members start only after the user's score is in, so the user can't anchor on theirs.
4. **Send all AI members at once:** one message with one Agent call per seat, using `subagent_type: "general-purpose"`, `run_in_background: false`, `description: "<Seat> review"` (no round number), the brief text exactly as written as the prompt, and the seat's model (see `references/models.md`).
5. **Save each reply** as `<run>/round-N/scores/<seat>.json`: the JSON block from the end of the member's reply, plus `"seat"`, `"model"` (the model it ran on; when seats inherit the session's model, use that model's ID) and `"source"` (`agent`, `delegate:<provider>` or `human`). If a reply has no usable JSON, send the same brief once more to a fresh agent. If that fails too, leave the seat out of this round and note it.
6. **Check for side effects:** `council.py verify -w <run> -r N`. Members are told not to change anything, but they run code. If a tracked file changed, `restore -r N` and note which seat did it. Remove clutter they left next to the work (like `__pycache__`) if it's clearly theirs.
7. `council.py tally -w <run> -r N` prints the scores, the median, any vetoes and a decision, and rebuilds `<run>/ledger.md`.

## 4. Act on the decision

- **PASS:** the work is done. Go to section 6.
- **CONTINUE:** go to section 5. If the tally flags a **regression** (the median fell more than the noise band below the best round), the last fixes made things worse. Run `council.py restore -w <run> -r <best>` first, then work from that version using both rounds' feedback, and try a different fix.
- **STOP_PLATEAU or STOP_CAP:** restore the best round if it isn't the current one, then go to section 6. Stopping a stuck loop is deliberate: more rounds of the same fixes burn tokens and tend to bloat the work.

## 5. Triage and fix

Write `<run>/round-N/triage.md`, sorting every issue the members raised into one of three groups:

- **Fix:** what you'll change, and why.
- **Reject:** why the point is wrong, out of scope, or against what the user asked for. Rejections go in the final report so the user can overrule you.
- **Conflict:** members pulling in opposite directions ("shorter" vs. "more detail"). Make the call, write down why, and stick with it.

Then make the fixes, following these principles:

- **Fix causes, not scores.** Do the real work. Text aimed at reviewers ("this is fully tested"), claims the work doesn't back up, or padding meant to look thorough would raise scores while making the work worse. That is exactly what the holdout juror is there to catch.
- **Worst first.** Issues from a seat that vetoed, high-severity issues and issues several seats raised come first. Not every suggestion earns a change; applying all of them is how work bloats.
- **Stay in scope.** Edit only tracked files. Before you touch or create any other file, run `council.py track -w <run> <file>` so a restore can undo it.
- **Open questions** from members: if the answer is a fact you know (the audience, how to run it), add it to the Context section of the next round's briefs and note that in the triage. If only the user knows, ask them. If you can't ask (the run is unattended), make the call and list it under "Decisions to check" in the report. Context is for facts, never for hints about quality.
- **Your own finds:** if you spot a real problem no member raised, you can fix it too. Log it in the triage as yours.

Before the next round, run `council.py lint -w <run> --work <tracked files>` to catch any trace of the review left in the work. Then go back to section 3 with N+1.

If "Pause between rounds" is on, show the tally and a summary of the triage, and ask before applying fixes. In blind mode, ask without showing scores.

## 6. Holdout and report

**Holdout juror** (if on): one fresh agent in the generalist seat scores the final version. Write its brief from the same template to `<run>/holdout/brief.md` and lint it. Send it like a member (`description: "Generalist review"`), save its reply as `<run>/holdout/score.json`, run `council.py verify -w <run> -r <final round>`, then `council.py holdout -w <run> -r <final round>`. Don't act on the holdout's feedback in this run: once you fix toward it, it's just another member.

Two holdout results need saying plainly: an overfitting warning (the holdout scored well below the council), or a holdout score below the threshold even when it's consistent with the council. For either one, offer two options: accept the work as it is, or run more rounds on the open issues with a new holdout at the end.

**Report:** write `<run>/report.md` from this template.

```markdown
# Council report: <target>

**Result:** Passed in round 3 with a median of 87 (threshold 85).
  (or: Stopped after round 5 because scores stopped improving. Best: round 4, median 81.)
**Final version:** <paths> (restored from round 4)  ← only if a restore happened
**Holdout:** 84 vs. a council median of 87, consistent.  (or the overfitting warning)
**Reviews run:** 13 (4 seats × 3 rounds, plus the holdout)

## Scores by round
<the table from ledger.md>

## What changed
- After round 1: ...

## Feedback rejected
- <Seat>, round N: "<issue>". Rejected because ...

## Still open
Issues raised in the final round (including the holdout's), and the lowest seat's dissent, a sentence or two each.

## Decisions to check
Calls you made that only the user could really answer. Leave this section out if there were none.

## Run details
Seats, models and questions. Run folder: <path>
```

For a sealed threshold, run `council.py reveal -w <run>` and put the number in the report. Then give the user a short summary in chat: the result, the holdout, the three biggest changes, what's still open, and a link to `report.md`. A pass can still leave open issues; offer to fix them outside the council. If the host can render charts, the score trajectory makes a good small line chart. Don't commit anything unless the user asks.

## The user's seat

- The user gets the generalist seat unless they ask for a particular lens.
- Each round, write `briefs/you.md` from the same template and lint it. Post it in chat exactly as written, link the file, and add one line outside the brief: "Reply however you like: a score from 1 to 100 and your reasons is enough. Say 'pass' to skip this round." Then end your turn and wait.
- Put their reply into the same JSON shape, with `"source": "human"`. Keep their words, and don't add issues they didn't raise. If they gave no score, ask once.
- Their score counts like any member's, in the median and for vetoes. If they pass, the round goes ahead without them.
- **Blind mode:** until the end, all the chat shows between rounds is "Fixes applied. Here's your next brief." followed by the brief. No scores, no list of fixes, no hints about passing. Tell them once, at the start, that tool calls (edits, score files) are still visible in the UI if they expand them, so they should leave those collapsed. If pauses are on, ask "Continue?" with no numbers.
- The user knows the threshold (unless it's sealed) and remembers earlier versions, so their seat isn't blind the way the AI seats are. That's expected; explain it if they ask.

## When things go wrong

- **A member edits files:** `verify` catches it. Restore that round's snapshot, note which seat did it, and run that seat again.
- **A score contradicts its own findings** (95 alongside a high-severity issue): don't override it. Note it in the triage. If it keeps happening, that seat's anchors are too vague; tell the user.
- **A provider or model the user chose isn't available:** say so and ask. Never swap in another one quietly.
- **The work is huge** (a whole repo): ask which part matters, or give each seat a focused slice and say so in its brief. A council can't meaningfully score 200 files in one pass.

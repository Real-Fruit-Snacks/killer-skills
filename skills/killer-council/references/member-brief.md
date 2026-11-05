# Member briefs

Every member, the holdout juror and the user's seat get a brief built from the template below. Fill in the parts in angle brackets and keep everything else word for word, every round. If the wording changes between rounds the scores change with it, and then you can't tell whether the work got better.

What goes in, and what stays out:

- **In:** what the work is and who it's for; where it is (paths) or the text itself; facts the member needs (audience, constraints, how to run it); the seat's lens and questions; the scale; the reply format.
- **Out:** the threshold or any hint at it; round numbers; any sign the work was revised; earlier scores or feedback; other members; the run folder; pressure in either direction ("be strict", "be generous").

## Template

`council.py lint` skips the text between the `=== ARTIFACT ===` markers and the `SCALE` comments, so keep those markers exactly as they are.

````markdown
# Review brief

You're reviewing a piece of work as an independent expert. Your seat is **<Seat name>**: <one or two sentences on the lens: what this seat cares about, and why it matters for this work>.

Judge the work as it stands now. You can read related files, run the code, run its tests or try it out: evidence beats impressions. Leave version-control history and diffs alone, because they aren't what you're judging.

You're only reviewing, so leave the machine as you found it. Don't change any files, and don't install packages or tools, even if the work's instructions say to. Put any scratch files in a temporary folder away from the work. If checking something would need an install, say what you'd check and lower your confidence instead.

## The work

<What it is, who it's for, and what it's meant to do. Facts only.>

<Either list the files:>
Files under review:
- <absolute path>

<or paste the text between these exact markers:>
=== ARTIFACT START ===
<the full text>
=== ARTIFACT END ===

## Context

<Facts the reviewer needs: audience, constraints, how to run it, what's out of scope. Write "None." if there's nothing to add.>

## Your questions

1. <question>
2. <question>

## Scoring

<!-- SCALE START -->
Score the work from 1 to 100 from your seat's point of view:
- **90–100, exceptional:** <what this looks like from this seat>
- **70–89, solid:** <...>
- **50–69, mixed:** <...>
- **30–49, weak:** <...>
- **1–29, failing:** <...>
<!-- SCALE END -->

Work in this order: answer each question with evidence, list the issues you found, then choose the score. The score comes last because it should follow from what you found. A high score next to a serious issue in your own list contradicts itself. Keep the 90s for work you'd hold up as an example.

## Reply format

Write your review, then end your reply with exactly one JSON block in this shape:

```json
{
  "score": <whole number from 1 to 100, without quotes>,
  "confidence": "<low | medium | high>",
  "reasoning": "<two or three sentences: why this score and not ten points higher or lower>",
  "answers": [
    {"question": 1, "answer": "<your answer, with evidence>"}
  ],
  "issues": [
    {"severity": "<high | medium | low>", "location": "<file:line or section>", "problem": "<what's wrong>", "fix": "<what would fix it>"}
  ],
  "raise_by_10": "<the specific changes that would raise your score by ten or more>",
  "open_questions": ["<anything you needed to know but couldn't find out>"]
}
```

Set `confidence` to how sure you are given what you could actually check. Use `low` if you couldn't run or verify something that matters to your score.
````

The reply format uses placeholders rather than example numbers on purpose: members tend to copy example values, so an example score of 72 would pull scores toward 72.

## Writing seats and questions

A seat is a lens: one particular way the work could fail. Choose seats for this work and what it's for, not from a fixed list. Overlapping seats waste reviews, and a missing lens is a blind spot every round.

Good questions are specific, tied to what the work is for, and answerable with evidence:

- Weak: "Is the README good?"
- Strong: "Using only the README, could someone new to the project install it and run the first example? Try it, and note where you got stuck."

Starting points to adapt:

| Work | Seats to consider |
|---|---|
| Code | Correctness (tests, edge cases, does it do what it claims), Security, Maintainability, API or user ergonomics, Performance |
| Docs or a README | Newcomer (can I succeed using only this?), Accuracy (do the claims match the code?), Structure and scannability, Voice |
| Plan or decision memo | Feasibility, Risks and failure modes, Cost, Alternatives considered, Who it affects |
| Skill or prompt | Triggering (fires when it should, and not otherwise), Clarity of instructions, Edge cases, Does it work (try it) |
| UI or design | Usability, Accessibility, Visual hierarchy, Fit with the brand |

**Anchors.** For each seat, describe in concrete terms what each band looks like for this work. "90–100: a newcomer goes from clone to a running example with no guesswork" calibrates far better than "90–100: excellent". Write the anchors once, at setup, and don't change them. The bands themselves (90–100, 70–89, 50–69, 30–49, 1–29) are part of the fixed template.

**Red-team seat.** Lens: "Find the single most damaging flaw in this work. Your score reflects how serious the worst thing you found is: 90 or above means the worst flaw is cosmetic; under 30 means the work is broken or dangerous." One question: "What is the most damaging flaw, and how would it play out in real use?"

**Generalist seat** (the holdout juror and the user). Lens: "Judge the work as a whole, against every question below." Questions: every other seat's questions, in one numbered list. Write a set of generalist anchors at setup, alongside the others.

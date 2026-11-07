# Briefs

The blind audience check uses two fresh agents a round. `deck.mjs` writes both briefs from the templates below, filling in the parts in angle brackets, so the wording is the same every round and nothing leaks in by accident. Send each brief exactly as the script wrote it. Don't add context, and don't edit it to help the deck along: if a message missed, the deck changes, never the brief.

What stays out of both briefs, and why:

- **The deck file, its folder, the notes and the slide titles.** The audience sees only the slide images, in a folder of their own under the temp folder. Speaker notes would tell them what the talk means; the slides have to do that alone.
- **The key messages, for the audience.** An audience told what to listen for hears it. The room description (`--room`) is checked against the key messages, and `audience` refuses one that shares their words.
- **The slides, for the matcher.** It sees the key messages and the audience's answer, nothing else, so it judges what the audience wrote, not what the slides say.
- **The round number, earlier answers and anything you hope for.** A judge who knows the deck was revised looks for the fix.

Each agent writes its full answer to `reply.json` in its scratch folder and ends with one line. `deck.mjs heard` and `deck.mjs landed` read the files; the agents' last lines are only for you.

## Audience brief

`deck.mjs audience` fills in: the kind of deck, the room line, how many slides, the folder, the last file name, the seat line and the scratch folder.

````markdown
# Look at a deck and say what it argued

You're about to see the slides of a <kind>, one at a time, in order, as images. <room line>

## The slides

There are <count> images in `<slides folder>`: `01.png` is the first slide and `<last>.png` the last. Open each one with your Read tool, in order, and take it in the way you would from a seat in the room. <seat>

Look only at those images. Don't open any other file or folder, and don't search for anything about this deck: the point is what the slides alone get across.

## What to say

1. **What it argues**, in one or two sentences, the way you'd tell a colleague who missed it.
2. **The three things you'd remember** a day later, most memorable first.
3. **Anything you couldn't read or follow**: the slide number and what was wrong (too small, too faint, too much on it, a chart you couldn't read, a step you didn't get). An empty list is a fine answer.
4. **The slide that worked best**, if one did, and why in a few words.

Say what you actually took away, not what you think the speaker meant. If a slide was hard work, say so.

## Your answer

Write this JSON to `<scratch folder>/reply.json`:

```json
{
  "argument": "<one or two sentences>",
  "remember": ["<first>", "<second>", "<third>"],
  "unclear": [{"slide": 4, "problem": "<what was wrong>"}],
  "best": {"slide": 7, "why": "<a few words>"},
  "notes": "<anything else, or an empty string>"
}
```

Then end with one line: what it argues, in a sentence.
````

## Matcher brief

`deck.mjs heard` fills in: the key messages, the audience's answer and the scratch folder.

````markdown
# Did the message get through?

Someone made a deck to leave people with a few key messages. Afterwards, one person who saw it wrote down what they took away. Your job is to say, for each key message, whether that person got it.

## The key messages

<messages>

## What they wrote

> **What it argues:** <argument>
>
> **What they'd remember:**
<remember>
>
> **What they couldn't read or follow:**
<unclear>

## How to judge

- **landed:** their words carry the message's point, even in other words. Someone reading only their answer would know it.
- **partly:** they got part of it, or the topic without the point. They know it was about cost, but not that cost fell.
- **missed:** nothing in their answer carries it.

Judge only what they wrote. Don't fill gaps with what the deck probably said. For each one you mark landed or partly, quote the words of theirs that show it, copied exactly from above; a phrase is enough. An answer can carry more than one message.

## Your answer

Write this JSON to `<scratch folder>/reply.json`:

```json
{
  "messages": [
    {"id": 1, "verdict": "<landed | partly | missed>", "quote": "<their exact words, or null>", "why": "<one sentence>"}
  ],
  "extra": ["<anything they took away that isn't one of the key messages>"]
}
```

List every key message once, by its number. Then end with one line: how many landed.
````

## What the script checks in the replies

- **The audience's reply** must have an `argument`, a `remember` list, and `unclear` entries that name real slide numbers. A missing or malformed reply stops `heard` with what's wrong: send the same brief to a fresh agent and run `heard` again.
- **The matcher's reply** must list every key message exactly once with a verdict. A `landed` or `partly` whose quote isn't in the audience's answer word for word (ignoring case, spacing and curly quotes) counts as `missed`, and the output says so. The verdict needs the audience's own words behind it.
- **The deck folder** is fingerprinted when the round starts and again when the reply comes in. If it changed while an agent had the images, `heard` says so: if you didn't change it, restore it.

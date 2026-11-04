# Gecko brand

Working notes for anyone building Gecko's landing page, dashboard or bot copy. Visual identity (logo, palette, type) is still open; see "Visuals" at the end.

## The name

**Gecko catches bugs.** Geckos eat bugs, cling to any surface, and stay still until the moment they strike. That is the product: it sits quietly on your repo, and when someone drops a video of a bug, it catches it and brings back the fix.

- Product: **Gecko**
- Bot: **gecko[bot]** on GitHub
- Company line: *Gecko — send a video of a bug, get back a pull request that proves it and fixes it.*

Name collisions to know about: Mozilla's Gecko browser engine, CoinGecko, Gecko Robotics. Pick a distinctive domain and social handle (e.g. "trygecko", "geckohq", "gecko.fix") rather than plain "gecko".

## Taglines

Primary: **Send a video. Get a fix.**

Alternates, by context:
- Landing hero: *Your users record the bug. Gecko catches it.*
- Developer angle: *From screen recording to failing test to verified fix.*
- Trust angle: *Every fix comes with proof.*
- Playful (social, stickers): *Gecko eats bugs.*

## What Gecko promises

Use these three ideas, in this order, wherever we explain the product:

1. **It reproduces.** From a plain recording, with nothing installed, Gecko replays the bug in a real browser and writes a test that fails the same way.
2. **It proves.** A fix counts only if that test passes, the test is untouched, and the rest of the suite stays green.
3. **It's honest.** If Gecko can't reproduce a bug, it says so and shows why. It never opens a guess.

## Voice

Gecko sounds like a calm, sharp teammate who has already done the work.

| Do | Don't |
| --- | --- |
| Say what happened, with the number: "Reproduced in 1 attempt, fixed in 1 round, 139 s." | Hype: "Blazing-fast AI magic!" |
| Lead with the result, then the evidence. | Narrate the process before saying whether it worked. |
| Admit limits plainly: "Could not reproduce. The replay never showed two copies." | Hedge or apologise at length. |
| Use the user's words for the bug. | Rename the bug in jargon. |
| A little personality in small places: "Caught it." | Puns in every sentence. |

## Bot copy (GitHub)

| Moment | Copy |
| --- | --- |
| Starts | **On it.** Watching the recording, replaying it in a real browser, and writing a test that fails the same way. I'll update this comment. |
| Fixed | **Caught it.** {PR link} · a test that fails on `main` · a fix by Muse Code that passes it, with the suite still green. |
| Reproduced, no fix | **Reproduced, no verified fix yet.** The failing test is on `{branch}`; Muse Code's attempts didn't pass verification. |
| Can't reproduce | **Couldn't reproduce this one.** {reason}. A recording that starts from a fresh page load helps. |
| No video | I need a screen recording to catch this. Attach an MP4 or MOV and comment `/gecko`. |

## Product vocabulary

| Use | Avoid |
| --- | --- |
| run | job, task (in UI copy) |
| recording, video | clip, footage |
| reproduce, reproduced | repro'd |
| verified fix | AI fix, auto-fix |
| declined (could not reproduce) | failed (when Gecko correctly found no bug) |

## Mascot

The gecko is a friendly, clever hunter: big curious eyes, sticky toe pads, curled tail. It appears at moments of success ("caught it") and on empty states, not on every screen. Muse Image explorations are in `brand/explore/` (not final).

## Visuals

To be set from the founders' design direction. Until then the product uses neutral ink, cream and a single accent. When the palette is chosen, keep: one brand accent, a separate status set (good, warning, critical) that the brand accent never imitates, and chart colors from a validated categorical palette.

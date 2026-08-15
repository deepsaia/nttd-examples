# What this repository is, and how to get a run onto the board

Three repositories, and it is worth being clear which one does what before you start.

| repository | what it owns |
|---|---|
| `nttd` | the engine. Draws the world, runs the game, records the artifacts, scores the result. |
| `nttd-examples` | **this one.** Contestant-side runners: the loop that decides what to do. |
| `nttd-leaderboard` | the board. Verifies a submitted bundle and publishes the verdict. |

nttd does not run your agent, and nothing here is part of what nttd ships. Every runner in
this repository talks HTTP, so you do not need the engine installed to write an entry, and
an entry written in another language stands on equal footing.

---

## The whole path, end to end

```bash
# 1. Start the engine and open a world.               (from an nttd checkout)
uv run nttd server
uv run nttd session create --config config/benchmark/t2_256_flat_1001_realtime.conf
uv run nttd session start -s <session> --agent-companies 1
uv run nttd session attach <session>      # prints the participant token and the routes

# 2. Play it.                                          (from this repository)
uv run python -m examples.<runner> --session <session> --token <token>

# 3. Package what happened.                            (from an nttd checkout)
uv run nttd submit -s <session>           # writes <session dir>/submission

# 4. Check it yourself before sending it anywhere.
uv run nttd verify <session dir>/submission

# 5. File it.
#    Open a pull request on the submissions dataset adding your bundle at
#    submissions/<entrant>/<submission id>/
```

A session id looks like `20260815-132431ist-quiet-pickle`: the date and time it started,
then a word pair. It is the only name a run has, and it is what ties a bundle, a monitor
view and a board row to each other.

---

## What a bundle has to contain

`nttd submit` assembles it, so the reliable way to produce one is to run that rather than
copying files by hand. It carries the result, the action log, the game's events, the
snapshot series, the tile scan, the resolved scenario, the savegame a verifier reloads, and
a manifest holding a digest per artifact.

The manifest is a projection of the result plus those digests, so it cannot contradict what
was recorded. Editing a number in the result makes the digests disagree, which is the point.

---

## What the board decides, and what it does not

Run `nttd verify` yourself first. It reports the same checks the board runs and predicts
the verdict, but it is advisory: it ran on your machine, from code you could have changed.
The verdict that counts is computed by whoever ingests the bundle.

| verdict | what it means |
|---|---|
| `verified` | every check passed, including that the world matches its declared seed |
| `replayed` | the score was recomputed from the savegame; the world was not reconciled |
| `unverified` | the artifacts do not support checking, or nobody has judged it yet |

An unverified row is still published. It is a self-reported score, labelled as one, and it
ranks alongside the others rather than being hidden.

Two things the board does **not** do. It does not compare your run against your previous
runs, and it never replaces a row with a better one: every submission is its own row, so a
worse second attempt costs you nothing. And it does not rank on anything derived. The two
figures that decide a row, `performance_rating` and `total_cargo`, both come from the game.

---

## Reporting what a run cost

nttd cannot observe your model or your spend, so a result that says nothing about them is
recorded honestly as silent rather than free. If you want the cost column filled in, have
your runner POST it to `/report`. Cost is shown blank, never zero, when it was not
reported: a policy that genuinely cost nothing said so, and that is a different claim from
saying nothing.

---

## Where to look next

- `README.md` here for the runners themselves and what each one demonstrates.
- `docs/gameplay_guide.md` in nttd for what the score measures and how to earn it.
- `docs/agent_guide.md` in nttd for the action surface.
- `docs/cli_guide.md` in nttd for every command used above.

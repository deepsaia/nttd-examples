# What this repository is, and how to get a run onto the board

Three repositories, and it is worth being clear which one does what.

| repository | what it owns |
|---|---|
| `nttd` | the engine. Draws the world, runs the game, records the artifacts, scores the result. |
| `nttd-examples` | **this one.** Contestant-side runners: the loop that decides what to do. |
| `nttd-leaderboard` | the board. Verifies a submitted bundle and publishes the verdict. |

nttd does not run your agent, and nothing here is part of what nttd ships. Every runner talks
HTTP, so you do not need the engine installed to write an entry, and an entry written in
another language stands on equal footing.

---

## The whole path, end to end

```bash
# 1. Start the engine and open a world.               (from an nttd checkout)
uv run nttd server
uv run nttd benchmark --config config/benchmark/t1_256_flat_1001_stepped.conf

#    or drive the lifecycle yourself, which is the same thing in three commands:
uv run nttd session create --config config/benchmark/t2_256_flat_1001_realtime.conf
uv run nttd session start -s <session> --agent-companies 1
uv run nttd session attach <session>      # prints the participant token and the routes

# 2. Play it.                                          (from this repository)
uv run runex          # asks the approach, the agent server, the session and the token

#    or by hand, which is what runex ends up running:
uv run python -m examples.minimal_runner --session <session> --token <token>

# 3. Package what happened.                            (from an nttd checkout)
uv run nttd package -s <session>          # writes <session dir>/submission

# 4. Check it yourself before sending it anywhere.
uv run nttd verify logs/sessions/<session>/submission

# 5. File it as a pull request on the submissions dataset.
uv sync --extra publish
export HF_TOKEN=...                       # your own token, write scope
uv run nttd publish -s <session> --entrant <you> --id <name>

# 6. MERGE that pull request, on HuggingFace.
#    Verification reads the dataset's main branch, so until it is merged there is
#    nothing there to verify and the verdict comes back unverified.
```

A session id looks like `20260815-132431ist-quiet-pickle`: the date and time it started, then
a word pair. It is the only name a run has, and it ties a bundle, a monitor view and a board
row to each other.

The entrant is read from your HuggingFace token unless you name one. It is not a label: the
board refuses a pull request touching anything outside `submissions/<the account that opened
it>/`, so a name that is merely a label is a submission that bounces.

---

## What a bundle has to contain

`nttd package` assembles it, so the reliable way to produce one is to run that rather than
copying files by hand. It carries the result, the action log, the game's events, the snapshot
series, the tile scan, the resolved scenario, the savegame a verifier reloads, and a manifest
holding a digest per artifact.

The manifest is a projection of the result plus those digests, so it cannot contradict what
was recorded. Editing a number in the result makes the digests disagree, which is the point.

Reported spend is **not** in the bundle. It is your claim about yourself, nttd never observed
it, and no check can recheck it; the totals travel in `result.parquet` and the series stays in
the session directory for the monitor.

---

## What the board decides, and what it does not

Run `nttd verify` yourself first. It reports the same checks the board runs and predicts the
verdict, but it is advisory: it ran on your machine, from code you could have changed.

| verdict | what it means |
|---|---|
| `verified` | every check passed, including that the world matches its declared seed |
| `replayed` | the score was recomputed from the savegame; the world was not reconciled |
| `unverified` | the artifacts do not support checking, or nobody has judged it yet |

An unverified row is still published. It is a self-reported score, labelled as one, and it
ranks alongside the others rather than being hidden.

Two things the board does **not** do. It does not compare your run against your previous runs,
and it never replaces a row with a better one: every submission is its own row, so a worse
second attempt costs you nothing. And it does not rank on anything derived. The two figures
that decide a row, `company_value` and `total_cargo`, both come straight from the game.

---

## Reporting what a run cost

nttd cannot observe your model or your spend, so a result that says nothing about them is
recorded as silent rather than free. To fill the cost column, have your runner POST it to
`/report`. Cost shows blank, never zero, when it was not reported: a policy that genuinely
cost nothing said so, and that is a different claim from saying nothing.

Tokens and price are separate claims. A runner that knows its token counts and not their price
reports the tokens and omits the cost, and the board shows the run with a blank cost rather
than a free one.

---

## Where to look next

- `README.md` here for the runners and the three commands a run takes.
- `docs/gameplay_guide.md` in nttd for what the score measures and how to earn it.
- `docs/agent_guide.md` in nttd for the action surface.
- `docs/cli_guide.md` in nttd for every command used above.

# nttd-examples

Reference runners for [nttd](https://github.com/deepsaia/nttd), a benchmark for
long-horizon planning built on OpenTTD.

nttd does not run your agent. It owns the world and the record; you own the loop. Nothing
here imports the `nttd` package: every runner talks HTTP, so you do not need the engine
installed to write an entry, and an entry written in another language is on equal footing.

---

## Install

```bash
git clone git@github.com:deepsaia/nttd-examples.git
cd nttd-examples
uv sync                      # requests, httpx, websockets, and the runex launcher
uv sync --extra neuro-san    # + the neuro-san agent networks
cp .env.example .env         # then fill in ANTHROPIC_API_KEY
```

Everything in `.env.example` already holds the value this repository expects except the API
key, so that is the only line you write. It is read by `runex` and by the agent server it
starts.

---

## A whole run, in three commands

Three terminals, two repositories. The first two stand up a world; the third plays it.

```bash
# --- in an nttd checkout -----------------------------------------------------------------
uv run nttd server                                                             # terminal 1
uv run nttd benchmark --config config/benchmark/t1_256_flat_1001_stepped.conf   # terminal 2

# --- here ----------------------------------------------------------------------------------
uv run runex                                                                   # terminal 3
```

**1. `nttd server`** is the engine's HTTP API on `:8000`. It runs no game by itself. Leave it
up: one server serves any number of sessions.

**2. `nttd benchmark --config <conf>`** creates a session, draws its world, starts OpenTTD on
it, prints the **session id** and the **participant token**, then waits for the end condition
and writes the result. It does **not** play. The config decides the world and how long the run
lasts; `ls config/benchmark/` in the nttd checkout has all four tiers in both modes.

**3. `runex`** asks which approach, which session and which token, then starts the run. It
reads what it can from the servers already running, so the usual answer is Enter.

For an approach that needs an agent server, it finds one or starts one, on `localhost:8088`
unless you say otherwise, and shuts down what it started. A server you started yourself is
**used, not duplicated**, and left running. That is why it probes the port rather than only
checking whether something is on it: a busy port might be a server to use or something else
entirely, and those want opposite answers.

Run `ns run` yourself if you want NSFlow on <http://localhost:4173>, where every tool call and
its arguments are visible while a turn runs. `runex` will find it.

### Or drive the lifecycle yourself

`nttd benchmark` is these rolled together. Use them separately to change something in between,
run two sessions against one server, or open a world now and attach much later:

```bash
uv run nttd session create --config config/benchmark/t2_256_flat_1001_realtime.conf
uv run nttd session start -s <session> --agent-companies 1
uv run nttd session attach <session>   # prints the participant token
uv run nttd session stop -s <session>
```

`--agent-companies 1` is the part to notice: without it the session has no contestant company,
so no token is issued and nothing can play it. Nothing here waits for the end condition, so
you end the run yourself.

---

## What step 3 looks like

Questions in order, and the usual answer to each is Enter:

```
  1  How will it play?

       Approach                  What decides
  1    neuro-san                 A multi-agent network: a strategist that reads the
                                 position and calls workers which survey, build, buy
                                 and repair.
  2    scripted                  No model and no framework. A fixed policy playing the
                                 same stepped loop.
       evolution strategies      not written yet
       reinforcement learning    not written yet

  2  Agent server
     starting a neuro-san server on localhost:8088
     serving 1 network(s)

  •  Network: ns_air_agent (the only one this server serves)

  3  Which session?

       Session                            Scenario                    Mode      Days   State
  1    20260824-095217ist-perky-rocket    t1-256-flat-1001-stepped    stepped    366   running  scored

  4  Participant token
     nttd issued pt_ae9a99a44499418a8632856663bd7c65 for this session
```

One network is stated rather than offered, because a menu of one is a keystroke that teaches
nothing. Serve two or more and it asks, with each network's own description of what it plays:

```
  •  Which network?

       Network           What it plays
  1    ns_air_agent      Plays one nttd session as an air transport company.
  2    ns_rail_agent     Rail: platform axis, depot junction and rail type must agree.
  3    ns_water_agent    Water: which docks share a body of water at all.
```

The list comes from the server, not from a manifest on disk: the manifest is what the server
was told to load, and this is what it did load. A `--network` naming something it does not
serve is refused with the list of what it does.

An approach whose dependency is missing says so in the menu rather than failing thirty seconds
into a run. The agent server comes before the session so that nothing slow happens after a
live scored game has been chosen.

Also `python -m runex` from a checkout, and `nttd runex` with both installed together.
`--kind`, `--session`, `--token`, `--network` and `--yes` skip whichever questions you have
already answered, which is what a script wants.

Nothing depends on it. Every run it starts can be started by hand:

```bash
uv run python -m examples.minimal_runner --session <session> --token pt_...
uv run python -m examples.neuro_san_play --session <session> --token pt_... --network ns_air_agent
```

---

## Writing your own

The full contract is in nttd's [agent guide](https://github.com/deepsaia/nttd/blob/main/docs/agent_guide.md).
The short version:

- **Observe** with `GET /v1/participant/sessions/{id}/state/full`. It returns the complete
  entitled game state and is deliberately not filtered for you, because deciding what matters
  is part of the task.
- **Query** with `POST /v1/participant/sessions/{id}/state/gs/query?action=<name>` for what a
  snapshot does not carry: a buildable tile, the engine list, a cost estimate. Read-only
  commands only.
- **Act** with `POST .../actions/submit` or `.../actions/submit-batch`. **No ceiling on a
  batch**: lay a whole route in one if you want. How much to attempt per decision is part of
  what the benchmark measures, so nttd does not decide it for you.
- **Report** spend with `POST .../report`, per model. nttd runs no model, so it cannot observe
  what you spent. Repeated calls accumulate.

For RL and evolution strategies, use `POST .../step/reset` and `POST .../step`: the game is
paused between steps, so deliberation costs no game time. nttd ships a Gym wrapper at
`nttd.rl.env.NttdEnv`, an ordinary client over those same routes.

**Human parity.** An agent may take any action a human can take through the GUI, and nothing
more. Nine superhuman actions are operator-only, including `change_bank_balance`,
`set_max_loan` and `found_town`. Reaching for one in a scored session is refused and recorded:
it does not void the run, since nothing happened, but the result reports `clean_run = false`.

---

## Submitting it for scoring

Three more commands, all from the nttd checkout, because the engine owns the record.

```bash
uv run nttd package -s <session>                        # writes <session dir>/submission
uv run nttd verify logs/sessions/<session>/submission   # your own check first

uv sync --extra publish                                 # once
export HF_TOKEN=...                                     # your own token, write scope
uv run nttd publish -s <session> --entrant <you> --id <name>
```

**4. `nttd package`** collects the savegame, the action log, the snapshots and the result row
into one bundle with a digest over each artifact. Needs no token.

**5. `nttd verify`** runs the same checks the board runs and predicts a verdict. It is
**advisory**: it ran on your machine, from code you could have changed. `--regenerate` also
rebuilds the world from its declared seed and compares terrain, which is the strongest check
available before filing.

**6. `nttd publish`** opens the pull request. The token is your own, so nobody needs write
access to the board, and the entrant is read from it: the board refuses a diff outside
`submissions/<the account that opened it>/`, so a name that is merely a label is a submission
that bounces. `--dry-run` first shows exactly what would be filed and where.

The pull request must then be **merged** before verification will find it. The board ranks on
`company_value`, what the company is worth when the run ends, with `total_cargo` breaking a
tie. Both come straight from the game.

[docs/submitting.md](docs/submitting.md) has the whole path and says which of the three
repositories owns which part.

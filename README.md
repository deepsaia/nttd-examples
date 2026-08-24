# nttd-examples

Reference runners for [nttd](https://github.com/deepsaia/nttd), a benchmark for
long-horizon planning built on OpenTTD.

nttd does not run your agent. It owns the world and the record; you own the loop. This
repository holds worked examples of that loop, kept separately because they are
**contestant-side code**: nothing here is part of what nttd ships, and none of it is
needed to run a benchmark.

Nothing here imports the `nttd` package. Every runner talks HTTP, which is the point:
you do not need the engine installed to write an entry, and an entry written in another
language is on equal footing.

---

## Install

```bash
git clone git@github.com:deepsaia/nttd-examples.git
cd nttd-examples
uv sync                              # requests, httpx, websockets, and the runex launcher
uv sync --extra neuro-san            # + the neuro-san agent networks
cp .env.example .env                 # then fill in ANTHROPIC_API_KEY
```

`.env` is read by `ns run`, which starts the neuro-san server, and by `runex`. Everything
in `.env.example` already has the value this repository expects except the API key, so
the only line you have to write is that one.

---

## You also need a world to play

nttd stands one up. From an nttd checkout, in one command:

```bash
uv run nttd server                                                              # terminal 1
uv run nttd benchmark --config config/benchmark/t1_256_flat_1001_stepped.conf   # terminal 2
```

`benchmark` creates the session, draws the world, prints the session id and the
participant token, then waits for the end condition and writes the result.

Or in four, when you want to change something in between, run two sessions against one
server, or start a world now and attach to it later:

```bash
uv run nttd server                                                # terminal 1
uv run nttd session create --config config/benchmark/t2_256_flat_1001_realtime.conf
uv run nttd session start -s <session> --agent-companies 1
uv run nttd session attach <session>  # prints the participant token
```

Either is fine. `--agent-companies 1` is the part to notice: without it the session has no
contestant company, so no token is issued and nothing can play it.

---

## Run an experiment

```bash
uv run runex
```

Four questions in order, and the usual answer to all four is Enter:

```
  1  How will it play?

       Approach                  What decides
  1    neuro-san                 A multi-agent network: a strategist that reads the
                                 position and calls workers which survey, build, buy
                                 and repair. Needs a neuro-san server running.
  2    scripted                  No model and no framework. A fixed policy playing the
                                 same stepped loop.
       evolution strategies      not written yet
       reinforcement learning    not written yet

  2  Which session?

       Session                            Scenario                    Mode      Days   State
  1    20260824-095217ist-perky-rocket    t1-256-flat-1001-stepped    stepped    366   running  scored

  3  Participant token
     nttd issued pt_ae9a99a44499418a8632856663bd7c65 for this session
```

It reads the open sessions from the running nttd server and offers the token that server
issued, so nothing has to be carried between terminals. An approach whose dependency is
missing says so in the menu rather than failing thirty seconds into a run.

The same tool is `python -m runex` from a checkout, and `nttd runex` if you have nttd and
these examples installed in one environment. `--kind`, `--session`, `--token` and `--yes`
skip whichever questions you have already answered, which is what a script wants.

Nothing depends on it. Every run it starts can be started by hand:

```bash
uv run python -m examples.minimal_runner --session <session> --token pt_...
uv run python -m examples.neuro_san_play --session <session> --token pt_... --network ns_air_agent
```

---

## What is here

| Path | |
|---|---|
| `examples/minimal_runner.py` | A whole stepped run with no model and no framework. Start here. |
| `examples/neuro_san_play.py` | The same run played by the neuro-san agent networks. |
| `agents/neuro_san/` | Those networks: their registries live in `registries/`, their coded tools in `agents/neuro_san/coded_tools/`. |
| `agents/nttd_client.py` | A small framework-agnostic HTTP client. |
| `agents/strategy/` | Hand-written strategy notes, one per transport mode. |
| `runex/` | The interactive launcher. |

One example per idea rather than one per SDK. There were LangChain, OpenAI and LangGraph
runners too, all demonstrating the same loop through a different client, and four copies of
one idea drift in four directions.

---

## Start here: `examples/minimal_runner.py`

The whole contract in one file: observe, decide, submit, report. No LLM and no framework,
so it runs without an API key. Its `decide()` is deliberately trivial; that function is
your entry and everything around it is plumbing that does not change.

It is also the reference for the four submission outcomes, which is the part contestants
most often get wrong:

| Status | Means | Retry? |
|---|---|---|
| `success` | it happened | n/a |
| `failed` | the game refused a legal request: bad tile, no money, no path | yes, with different parameters |
| `rejected` | never legal for a participant: unknown, or operator-tier | no, and retrying forever is the classic bug |
| `blocked` | you hit the action ceiling | not this submission |

### The trap it exists to show

In real-time mode `state/full` is served from the last GameScript refresh, so it **lags
your own actions**. Measured on a live session: a `set_loan` took **7.1 seconds** to show
up in a fresh observation.

So the obvious loop is wrong. Observe, act, observe again, and the second observation
still shows your pre-action state, so you submit the same action a second time. The first
version of `minimal_runner.py` did this three times in a row against a live session, and
every submission honestly reported `success`.

`changed_entities` on the action result is the immediate, authoritative answer to what
your action did. The runner keeps those as an overlay on each fresh observation until the
server's view catches up. With that in place it submits once, which the session's
`actions.parquet` confirms: one row, not four.

---

## The neuro-san networks

One network per transport mode, because the four are different games: air decides on town
population against airport coverage, road on many short pairs because one saturates, water
on which docks share a body of water at all, and rail on platform axis, depot junction and
rail type. A single network with a mode switch would be four strategies averaged into none.

Air is the one that is written. `agents/neuro_san/DESIGN.md` is the design and the evidence
behind it; every rule in it cost a run to learn.

```bash
uv sync --extra neuro-san
uv run ns run                        # terminal 3, the neuro-san server and its web UI
uv run runex --kind neuro-san
```

`ns run` reads `.env` from the project root, which is why the API key belongs there rather
than in a shell. It also serves NSFlow on <http://localhost:4173>, where every tool call
and its arguments are visible while a turn runs: the quickest way to see why a network
chose what it chose.

---

## Writing your own

The full contract is in nttd's [agent guide](https://github.com/deepsaia/nttd/blob/main/docs/agent_guide.md).
The short version:

- **Observe** with `GET /v1/participant/sessions/{id}/state/full`. It returns the
  complete entitled game state and is deliberately not filtered for you, because
  deciding what matters is part of the task.
- **Query** with `POST /v1/participant/sessions/{id}/state/gs/query?action=<name>` for
  what a snapshot does not carry: a buildable tile, the engine list, a cost estimate.
  Only read-only commands are accepted.
- **Act** with `POST .../actions/submit` or `.../actions/submit-batch`. At most 15
  actions per submission, per company. A batch over the ceiling is refused whole, so a
  route planned as one batch never ends up half-built.
- **Report** spend with `POST .../report`, per model. nttd runs no model, so it cannot
  observe what you spent. Repeated calls accumulate.

For RL and evolution strategies, use `POST .../step/reset` and `POST .../step`: the game
is paused between steps, so deliberation costs no game time. nttd ships a Gym wrapper at
`nttd.rl.env.NttdEnv` which is an ordinary client over those same routes.

### Human parity

An agent may take any action a human can take through the GUI, and nothing more. Nine
superhuman actions are operator-only, including `change_bank_balance`, `set_max_loan`,
and `found_town`. Reaching for one in a scored session is refused and recorded: it does
not void the run, since nothing happened, but the result reports `clean_run = false`.

---

## Submitting

[docs/submitting.md](docs/submitting.md) has the whole path from opening a world to a
verdict on the board, and says which of the three repositories owns which part.

---

## Tests

```bash
uv run --extra dev --extra neuro-san pytest -q
uv run --extra dev ruff check agents/ examples/ runex/ tests/
```

The `neuro-san` extra is needed for the coded-tool tests, because those tools import it.
It pulls in about 96 packages, which is why it is not part of `dev`. Without it those
modules are skipped rather than erroring, so a plain `uv sync` still gives a green run.

---

## License

Apache-2.0, matching nttd.

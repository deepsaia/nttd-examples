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
uv sync                              # requests, httpx, websockets
uv sync --extra langchain            # + LangChain runners
uv sync --extra langgraph            # + LangGraph runner
uv sync --extra openai               # + OpenAI SDK runner
uv sync --extra neuro-san            # + the neuro-san multi-agent system
```

You also need an nttd server and a session to attach to. From an nttd checkout:

```bash
uv run nttd server                                                # terminal 1
uv run nttd session create --config config/benchmark/t2_256_flat_1001_realtime.conf
uv run nttd session start -s <session> --agent-companies 1
uv run nttd session attach <session>  # prints the participant token
```

See [docs/submitting.md](docs/submitting.md) for the whole path from opening a world to
getting a verdict on the board, and for which of the three repositories owns which part.

---

## Start here

```bash
python examples/minimal_runner.py --session ses_... --token pt_... --steps 3
```

`examples/minimal_runner.py` is the whole contract in one file: observe, decide, submit,
report. No LLM and no framework, so it runs without an API key. Its `decide()` is
deliberately trivial; that function is your entry and everything around it is plumbing
that does not change.

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

## What is here

| Path | Status |
|---|---|
| `examples/minimal_runner.py` | **Current.** Verified against a live session. |
| `examples/neuro_san_mas/` | Coded tools use `state/gs/query`, which is current. The surrounding loop predates participant tokens. |
| `examples/langchain_nttd_agent.py` | Predates participant tokens, see below |
| `examples/langgraph_nttd_agent.py` | Predates participant tokens |
| `examples/openai_nttd_agent.py` | Predates participant tokens |
| `examples/simple_bus_agent.py` | Predates participant tokens |
| `examples/agent_client.py` | Predates participant tokens |
| `examples/manual_bus_test.py` | A hand-driven route build, useful for reading |
| `agents/` | A small framework-agnostic client and a scripted policy |

### What "predates participant tokens" means

These runners were written when nttd ran the agent loop itself. They still register
successfully:

```
POST /sessions/{id}/agents/connect            ->  200
GET  /sessions/{id}/state/compact?company_id=0 -> 200
```

and then fail to act:

```
POST /sessions/{id}/actions/submit             ->  401
{"detail":"A valid participant token is required. Pass it as X-Participant-Token ..."}
```

A submission now has to carry the participant token that `nttd session attach` prints.
The token answers "which company is this action for" in a form you cannot get wrong: the
company is derived from the token server-side and overwrites whatever is in the request
body, so `company_id` in the payload is ignored.

Porting one is small. Add the header, drop the registration call, and read from
`state/full` rather than `state/compact`:

```python
H = {"X-Participant-Token": token}
requests.post(f"{P}/actions/submit", headers=H, json={...})
```

`minimal_runner.py` shows the finished shape.

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

## Tests

```bash
uv run --extra dev --extra neuro-san pytest -q     # 134 tests
uv run --extra dev ruff check examples/ agents/ tests/
```

The `neuro-san` extra is needed for the coded-tool tests, because those tools import it.
It pulls in about 96 packages, which is why it is not part of `dev`.

---

## License

Apache-2.0, matching nttd.

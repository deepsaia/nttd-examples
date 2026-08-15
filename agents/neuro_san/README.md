# The nttd agent networks

Three neuro-san networks over one set of coded tools. They replaced a LangGraph system that
did the same job, and the reason for the change is the division of labour rather than the
framework.

## Where the intelligence goes, and where it does not

**Python states the facts and enforces the rules.** Everything that cost a run to learn is a
coded tool: deterministic, always right, and free. A model is never asked to remember them,
because a prompt that asks a model to remember something varies run to run and nothing
catches it when it forgets.

**Models exercise judgement.** Which of several viable corridors to take, whether to expand
or consolidate, which problem matters most now. That is what a model is for, and it is what
the benchmark is measuring.

## The tools, and what each one is defending against

| tool | the failure it exists to prevent |
|---|---|
| `read_position` | a fleet of nine and a flat profit line, which is what a lost train, a plane parked in its hangar and a ship circling its own pool all look like from outside |
| `verify_reachable` | a build that returned `success` while the vehicle could not leave: 60 game days of a bus sitting at a depot, four trains parked for a year |
| `rank_sites` | an airport 16 tiles from the town it was meant to serve, earning nothing until it was moved |
| `buy_and_dispatch` | a large plane at a small airport, a maglev on plain rail, a full-load order parking a train at a slow producer, and a vehicle bought too late to pay for itself |

`nttd_gateway` is the only thing that talks to nttd: it builds the action envelope, keeps
the session id and token in `sly_data` and out of the chat stream, and surfaces refusals
verbatim, because nttd's errors carry the coordinate that fixes the bug.

## The networks

| network | what it plays |
|---|---|
| `nttd_air` | aircraft, which need nothing built between their endpoints |
| `nttd_ground` | road, rail and water, which all depend on a depot-to-line junction |
| `nttd_portfolio` | every mode, allocating to whichever reaches revenue soonest |

They share `nttd_aaosa.hocon`: the delegation instructions and the ground rules that hold in
every mode. Five copies of one rule become five different rules, so there is one copy.

## Running one

```bash
uv sync --extra neuro-san
uv run python -m examples.neuro_san_runner --session <session> --token <token> --network nttd_air
```

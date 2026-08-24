# The nttd agent networks

One neuro-san network per transport mode, over a shared foundation of coded tools. They
replaced a LangGraph system that did the same job, and the reason for the change is the
division of labour rather than the framework.

`DESIGN.md` is the design and the evidence behind it. This page is how to run one.

## Where the intelligence goes, and where it does not

**Python states the facts and enforces the rules.** Everything that cost a run to learn is a
coded tool: deterministic, always right, and free. A model is never asked to remember them,
because a prompt that asks a model to remember something varies run to run and nothing
catches it when it forgets.

**Models exercise judgement.** Which of several viable corridors to take, whether to expand
or consolidate, which problem matters most now. That is what a model is for, and it is what
the benchmark is measuring.

## Four networks, not one with a switch

| network | plays | state |
|---|---|---|
| `ns_air_agent` | aircraft, which need nothing built between their endpoints | written |
| `ns_water_agent` | ships, and which docks share a body of water at all | planned |
| `ns_road_agent` | many short pairs, because one road route saturates | planned |
| `ns_rail_agent` | platform axis, depot junction, rail type | planned |

The four modes are different games and a shared strategist would be four strategies averaged
into none. What they DO share is `registries/ns_common.hocon` and the tools under
`coded_tools/ns/`: the gateway, the plan, the reports, the clock. Five copies of one rule
become five different rules, so there is one copy.

## The shape of a network

`ns_air_agent` is a strategist with four workers and twenty coded tools.

```
AirCompany  (the strategist: the front man, and the only agent whose history survives a turn)
├── Scout        survey_airport_sites, rank_corridors
├── Builder      plan_build_corridor, confirm_airports
├── FleetGrowth  choose_aircraft, plan_buy_aircraft, plan_dispatch, plan_clone_aircraft
└── FleetCare    air_health_check, plan_repoint, plan_retire
```

and holds directly the tools that see and the three that cost time: `read_situation`,
`fleet_report`, `route_report`, `inspect`, `refusals`, then `commit_plan`, `advance_days`
and `set_loan_to`.

**Planning is free; only committing costs a day.** Every `plan_` tool stages actions into a
batch and returns immediately. `commit_plan` submits the batch and steps the world once. A
batch has no ceiling, so a whole corridor goes in one commit rather than one action at a
time, which is what stops a 366 day budget being eaten by paperwork.

**The strategist is the front man because neuro-san keeps only the front man's history.**
Every worker is recreated from scratch each turn. Strategy is the one job that cannot afford
amnesia, so it sits where the memory is; anything a worker must not forget lives in a tool
that enforces it rather than in a page of prose it may skip.

**Memory that survives a turn is `sly_data`, and it has to be declared.** neuro-san's
`SlyDataRedactor` is security-by-default, so with nothing listed nothing returns to the
client and every scrap of cross-turn state dies at the turn boundary. `ns_common.hocon`
lists the nine keys explicitly, and deliberately not the credentials or the step lock.

## What the tools defend against

| tool | the failure it exists to prevent |
|---|---|
| `read_situation` | the engine's own problems list, used verbatim, because deriving problems from `idle_reason` calls a plane loading at a gate broken |
| `fleet_report` | a fleet of nine and a flat profit line, which is what one lost aircraft among eight healthy ones looks like from outside |
| `confirm_airports` | a build that returned `success` while no aircraft could use it |
| `survey_airport_sites` | an airport outside the catchment of the town it was meant to serve, earning nothing |
| `plan_build_corridor` | the ends and the proof in one batch, because the ORDER is where runs are lost |
| `choose_aircraft` | invented engine ids. Without a tool that lists them, a model offered 30, 40, 21, 60 and 90 while the real ones were 238 to 246 |
| `plan_buy_aircraft` | a large aeroplane at a small airport, and a purchase too late to return its price |
| `plan_dispatch` | `start_vehicle` is a TOGGLE, so calling it on a moving aircraft stops it |
| `air_health_check` | the only place a verdict is passed on an aircraft, so two tools cannot disagree about what stuck means |
| `plan_retire` | selling a working aircraft. It requires a stuck verdict, a committed repoint, and time for the repoint to have failed |
| `refusals` | asking the game the same refused question twice. One earlier network submitted one refused purchase 35 times |

`ns/gateway.py` is the only thing that talks to nttd. It builds the action envelope, keeps
the session id and token in `sly_data` and out of the chat stream, holds the per-session step
lock so two concurrent tools cannot both step, and surfaces refusals verbatim, because nttd's
errors carry the coordinate that fixes the bug.

There is deliberately **no score tool**. The game reports its own `performance_rating` and
that appears in `read_situation`; a tool that recomputed the marking scheme would both
duplicate the engine and hand the player the answer sheet.

## Running one

```bash
uv sync --extra neuro-san
cp .env.example .env                 # fill in ANTHROPIC_API_KEY

uv run ns run                        # terminal A: the networks on :8080, nsflow on :4173
uv run runex --kind neuro-san        # terminal B: pick a session and play it
```

`runex` finds the open sessions on the nttd server, offers the token that server issued, and
asks which network if more than one is served. By hand it is:

```bash
uv run python -m examples.neuro_san_play \
    --session <session> --token <token> --network ns_air_agent
```

`neuro_san_play.py` holds the conversation and nothing else. It asks the network for another
turn while the session is open and stops when the game closes it. Turns carry `chat_context`
forward, so the network remembers what it decided without one turn having to hold a whole
run.

**The cadence is the network's decision, not a setting.** An earlier runner woke the agent
every 30 game days, a number lifted from how the game was played by hand. Judging when to act
and how long to wait is part of what the benchmark measures, so it belongs to the agent: ten
days to see whether a vehicle left its depot, ninety to see whether a route pays.

`ns run` also serves nsflow at <http://localhost:4173>, which draws the network and shows
every tool call and its arguments as they happen. That is the thing to watch while a run is
going, alongside `nttd monitor`.

`ns` is neuro-san-studio's launcher: it loads the project-root `.env` before starting, so the
manifest path, the tool path and the model key live in one file rather than being exported
per terminal.

## Two things about how the tools load

`.env.example` sets `AGENT_TOOL_PATH_ONLY=true`. Without it, a registry's
`class = "ns.read_situation.ReadSituation"` resolves as a fully qualified import from
anywhere on PYTHONPATH, so an agent network file could name any importable class in the
environment and neuro-san would load it. These networks only reference their own tools, so
restricting resolution costs nothing.

Under that restriction the tools load as flat siblings rather than as part of this package,
which is why each one imports the shared foundation both ways. It also means a module here
named after a standard library one shadows it for the whole process: a file called
`inspect.py` broke `leaf_common` with a circular import of `logging`, which reads as a
neuro-san fault and is not one. `tests/test_ns_air_agent.py` checks for that.

# Four agent networks, one foundation

Design notes for `ns_air_agent`, `ns_water_agent`, `ns_road_agent`, `ns_rail_agent`.

This file is the plan and the record of why. It is being written as the design is researched,
so sections arrive in order rather than all at once.

---

## 1. Four networks, not one with a mode switch

The four modes are not variations on a theme. They are different games, and the evidence is
eight hand-played runs:

| mode | what actually decides the run | growth means | the failure that kills it |
|---|---|---|---|
| air | town population times whether the site is inside its airport's coverage; plane size against field size | more aircraft on the busiest leg | an airport outside its coverage earns nothing; a large plane at a small field crashes with no warning |
| road | corridors mostly exist already, so it is cheapest and quickest to revenue | **more town pairs**, because one pair saturates at three or four buses | a stop connected to nothing, silently: the bus sat in its depot for sixty days |
| water | which docks share a body of water at all | more ships on a lane already proved | a depot in a pool cut off from its own dock, ships circling while the dock fills |
| rail | platform orientation taken from the approach axis; a depot joining the main line mid-corridor; rail type matching the locomotive | a second corridor, since one unsignalled line cannot take two trains | five distinct silent failures, and a three tile platform collects 12 passengers where an airport in the same town collects hundreds |

Rail may not want passengers at all. Its catchment is tiny while industry tonnage is large, so
freight pairing is plausibly its entire strategy, which no other mode shares.

So: **the plumbing is shared, the judgement is not.** One foundation carries the gateway, the
position report, failure memory, batching and parameter checking. Each network has its own
strategist, its own survey, its own ranking and its own verification, and they do not pretend
to be the same problem.

Road and rail are deliberately separate networks. Rail alone carries platform axis, depot
junction, rail type, bridge-versus-tunnel and consist assembly; putting road in the same
network would bury the simplest mode inside the hardest one.

---

## 2. Decisions already settled, with evidence

### The action surface is read from the running server, not copied

nttd serves the whole manifest at `GET /v1/public/actions`: 124 actions, each with its
description, tier, category, parameters, `one_of` constraints and declared return fields.
Measured live.

So nothing is copied into this repository. A coded tool reads the manifest from the server it
is playing, which buys two things a copy cannot:

- **It cannot drift.** The manifest is generated from the GameScript, so it always describes
  the engine actually in play.
- **Parameters can be checked before the game refuses them.** `build_bridge` wants
  `start_x`/`start_y` and not `from_x`/`from_y`; that is machine-readable in the manifest, so
  a tool can catch it rather than spending a game day learning it.

`config/actions/` stays in nttd: it is the generator's output that feeds that endpoint, not
something an agent reads directly.

### There is no per-step action ceiling

`StepRequest` in nttd states it: "Variable length, because a step is not one action. A policy
that wants to lay a whole route in one step may." No ceiling is enforced, and the `fairness.py`
referenced by an old comment does not exist.

So submitting one action per step, which the current tools do, is waste rather than compliance.
A step is a game day, and a 366 day run cannot spend a day on paperwork. The shape that follows
is: build the route in one step, then buy every vehicle with its orders and starts in a second
step, because a hangar coordinate only exists once the airport does.

### Nothing an agent cannot know may be an argument

A model asked for an identifier it has no way to obtain will invent one. Measured: a run
submitted `buy_vehicle` **35 times** with engine ids 30, 40, 21, 60, 90, 50, 100, 110, all
invented, all refused with `ERR_PRECONDITION_FAILED`, at a hangar coordinate that was also a
guess. Real aircraft ids in that era are 238 to 246.

The cause was not the prompt. `choose_plane` existed as a plain Python function rather than a
tool, so there was no way to obtain an engine id at all. Every identifier and coordinate is
therefore resolved inside a coded tool, and tools take intent rather than ids.

### A network must see its own refusals

The same 35 failures also show what happens without memory of them: nothing carried a refusal
from one turn to the next, so the network repeated it until the run ended. Refusals are kept in
`sly_data` and reported by the first tool every turn calls.

---

### Two defects that were ending every run

Both found by reading neuro-san's own source, and both verified here rather than taken on
trust.

**Cross-turn state was being discarded entirely.** neuro-san's `SlyDataRedactor` is
security-by-default: its own docstring says "our stance is security by default, so when
nothing is listed, it is equivalent to ... false". No registry declared
`allow.to_upstream.sly_data`, so nothing in `sly_data` ever returned to the client, and every
scrap of memory died at the turn boundary. The route just built, the ledger of refusals, the
cached survey: all gone by the next turn. That is why the network could not correct itself and
why it submitted the same refused purchase 35 times.

The remedy is one declaration, not a second state store. `StreamingInputProcessor.process_once`
merges `returned_sly_data` back into the state it passes forward, so with an explicit
allow-list `sly_data` round-trips turn to turn. This design therefore keeps ONE live store,
which is the neuro-san-native one:

    allow = { to_upstream = { sly_data = ["plan", "routes", "refusals", "sites", "decisions"] } }

Listed explicitly rather than `true`, because a live `asyncio.Lock` must never be in the set.

**A healthy fleet was being reported as broken.** `idle_reason` in the GameScript returns
`at_station` for an aircraft loading at a gate and `in_depot` for one in its hangar; both are
normal. The old `read_position` treated any non-empty `idle_reason` as a problem, so a fleet
working correctly read as a wall of faults, the strategist was told to repair before expanding,
and a repair tool would eventually have SOLD working aircraft.

The remedy is to stop deriving this at all. nttd already computes it at `GET /state/situation`,
whose docstring is explicit about why: "Arithmetic, not description. An agent that derives
these from a raw observation spends a model call on counting and can get it wrong, which is a
way for a good decision-maker to look bad at a benchmark meant to measure judgement." Its
`problems` list never reads `idle_reason` and allows for a vehicle still settling.

---

## 3. Shape of a turn

    look  ->  decide  ->  plan  ->  commit once  ->  advance
    (free)   (free)      (free)     (one game day)   (n game days)

Planning is free. Only `commit_plan` and `advance_days` move the clock, so a turn that builds
two airports and buys four aircraft with their orders spends two days rather than ten. `plan_*`
tools accumulate into `sly_data["plan"]`; `commit_plan` validates the whole batch, submits it
as one step, and reports what the game said about each action.

This is how the best hand-played run actually worked, and where its wasted opening days go.

## 4. Agent roster

Five agents, and **no AAOSA**. AAOSA asks every down-chain agent who is responsible, which is
for federated routing between agents of unknown capability. Here the capabilities are known
and fixed, so the front man calls exactly the agent it needs and the protocol's extra model
calls buy nothing.

| agent | model | job |
|---|---|---|
| `AirCompany` (front man) | strong | **the strategist.** Sees the whole picture and decides what to fix and where to expand |
| `Scout` | cheap | turns the map into a ranked, pre-costed table of candidate corridors |
| `Builder` | cheap | turns a chosen corridor into planned build actions, then confirms what landed |
| `FleetGrowth` | cheap | adds aircraft; owns airport-class to plane-class pairing and the hangar tile |
| `FleetCare` | cheap | keeps the fleet alive; never sells on its own initiative |

The strategist is the front man deliberately: neuro-san preserves only the front man's history
across turns, so any other agent starts each turn with amnesia. What it sees is INJECTED by
middleware on every model call rather than requested, which is what keeps its instructions to
about fifteen lines instead of a page.

## 5. Why the other three modes reuse this

Everything under `coded_tools/ns/` is mode-agnostic: the gateway, the envelope, the plan
accumulator, the situation and score reports, the fleet and route reports, `inspect`, the
refusal ledger, `commit_plan`, `advance_days`. Only the siting, the vehicle choice, the health
rules and the strategist's prose are per mode, which is exactly the split section 1 argues for.

---

## 6. Tasks

### 1. Foundations: gateway, session resources, journal, plan accumulator

**Why.** Everything else sits on these four. The current gateway discards the whole StepResult except action_results, keeps an asyncio.Lock inside sly_data (which blocks any to_upstream allow-list), and has a realtime branch that posts a schema the API rejects with 422. And there is no durable state at all, which is the single defect that makes the existing network unable to add an aircraft to a route it already built from turn 2 onward.

**What.** Create agents/neuro_san/coded_tools/ns/ with: constants.py (every sly_data key and journal section name as a module constant, so a typo cannot create a second store); envelope.py (the one builder for {"action": name, "params": {...}}, since StepRequest refuses top-level parameters); gateway.py rewritten to return the full StepResult (snapshot, days_advanced, terminated, end_reason, action_results), to delete the realtime branch and declare the network stepped-only, and to take its lock from session_resources; session_resources.py holding the per-process, per-session asyncio.Lock and RunJournal handle keyed by session_id; journal.py with a RunJournal class writing logs/ns_agent/<session_id>/journal.json atomically plus a readable journal.md, with sections sites, corridors, built, routes, refusals, decisions, disposals, prices, fleet_seen, waiting_history; plan.py with an ActionPlan accumulator doing the get-mutate-put pattern over sly_data['ns_plan'] with provenance per entry. Unit tests with a fake HTTP transport, no live server.

### 2. Observation tools: situation, score, fleet, routes, inspect, refusals

**Why.** The strategist requirement is unmet today: nothing reports per-vehicle profit, cargo waiting per route, the score breakdown, or a crash, and read_position derives problems from idle_reason, which is non-empty for at_station and in_depot so a healthy loading fleet reads as a wall of problems and fix_problems then sells the planes.

**What.** Under ns/situation/: read_situation.py (GET /state/situation and /state/full; use situation.problems verbatim, which never reads idle_reason and whose _SETTLING_DAYS is 400 so it cannot fire spuriously in a 366-day run); score_report.py (the nine components from get_expense_breakdown, get_companies and /state/full, with gap-to-next-point, the three T1-unreachable components marked, and an explicit statement when performance_rating is -1); fleet_report.py (get_vehicles(vehicle_type="aircraft") plus get_orders, diffed against journal.fleet_seen to report vehicles gone since the last look); route_report.py (routes plus per-end cargo waiting with a 30-day trend from journal.waiting_history and a saturated flag); inspect.py (one of station_id, vehicle_id, town_id, calling get_station_info, get_vehicle_info plus get_orders, or get_town_info plus get_town_rating); refusal_ledger.py (journal refusals grouped by action and param fingerprint with error_name, error_code, counts and a do-not-retry list). All are free queries and cost no game day.

### 3. The batch: commit_plan, advance_days, money and memory tools

**Why.** A step is a game day and a batch has no ceiling, yet the best hand-played run spent 15 days on an opening that needs 3. This task is where those 12 days come from, and where a refusal stops being repeatable.

**What.** ns/plan/commit_plan.py: validate-everything-then-report before mutating (intra-batch dependency such as buy_vehicle followed by add_order on its own output; connect_rail and connect_road must be alone in a step; affordability against balance plus any set_loan in the same batch minus the 40,000 reserve minus in-flight disposal proceeds; refuse anything twice-refused with its prior error_name), then post the whole batch to POST /step, read the entire StepResult, branch on presence of error_code rather than substring-matching prose, write refusals to the journal and record the per-build cash delta into journal.prices. ns/plan/plan_show.py and plan_clear.py. ns/clock/advance_days.py: N empty steps reading each returned snapshot for free, honouring terminated and end_reason instead of the current bare except Exception that convinces the network a live session ended, with stop_when conditions vehicle_lost, fleet_shrank, cash_below:N, cargo_waiting_above:N, quarter_boundary, day:N. ns/money/set_loan_to.py (exact amount not delta; warn on crossing 250,000 because SCORE_LOAN is max(0, 250000 - loan)) and price_check.py (engine prices from get_engines, airport prices learned from journal.prices, optional estimate_cost riding a wait step with company_id injected into the NESTED params and posted to /actions/submit since it is refused at /state/gs/query with 403). ns/memory/note_decision.py.

### 4. Air siting and corridors

**Why.** This owns the largest measured gap in the whole recorded set. blithe-harbor built a metropolitan airport 29 tiles from Tonwood, it attached to a 348-person village, and the run scored 118 against 173. The current rank_sites sorts airport types by area ascending, so it always picks commuter, which forces small planes everywhere, and it never excludes towns already built on.

**What.** ns_air_agent/air_rules.py: the airport-type to plane-type table (PT_BIG_PLANE only at LARGE 1, METROPOLITAN 3, INTERNATIONAL 4, INTERCON 7; heliports 2, 6, 8 take no aeroplane) and the catchment gate. survey_airport_sites.py: get_airport_types then find_airport_spots(town_id, airport_type, radius=7) per town by population descending; reject within_coverage false or distance greater than max_tiles_from_centre (default 6); keep the LARGEST type that fits close in; handle the empty result explicitly; write stable site_ids to the journal and exclude towns already built on. rank_corridors.py: pairwise over surveyed sites with distance FAVOURED not penalised, min population, get_cargo_income(PASS, distance), implied plane class from the worse of the two airport types, expected revenue per trip; never call /state/routes?agent_type=air, which filters out every town pair under 100 tiles. plan_build_corridor.py: price both airports first and plan neither if only one is affordable, then set_loan plus both build_airport in ONE batch with airport_type always explicit. confirm_airports.py: read the station NAME back and assert it contains the intended town name, then cache get_hangars into the journal. plan_upgrade_airport.py and plan_town_action.py.

### 5. Air fleet: choose, buy, clone, dispatch

**Why.** get_engines(vehicle_type="air") returns TRAIN engines with success true, which is the highest-probability LLM error in the whole air surface. The hangar tile is not derivable from the airport tile (+5 x for metropolitan, +4 x for commuter, +3 y for international) and four consecutive buy_vehicle calls at the airport coordinates failed ERR_UNKNOWN with no diagnostic. A second start_vehicle parks a fleet.

**What.** ns_air_agent/choose_aircraft.py: hard-code the literal "aircraft"; filter plane_type against the corridor's worse airport type via air_rules; score by capacity times income_per_unit at this distance times speed minus running cost over remaining days, not capacity/running_cost and not max capacity; re-query get_engines every call; return the top 3 with prices. plan_buy_aircraft.py: resolve the hangar tile from the cached get_hangars, never by arithmetic, and refuse any purchase taking cash below the 40,000 reserve (the best run's floor was 38,441; blithe-harbor bottomed at 7,707). plan_clone_aircraft.py: clone_vehicle(depot_tile=hangar, share_orders=true) plus start_vehicle, encoding that a clone arrives stopped and that a clone without an explicit depot is built at the original's current tile. plan_dispatch.py: the fixed triplet add_order(src, order_flags=0), add_order(dst), start_vehicle exactly once, never OF_FULL_LOAD, station_id never destination.

### 6. Air fleet care: health, repoint, service, retire

**Why.** fix_problems re-orders every broken vehicle onto the LAST route's stations regardless of which route it flies, appends orders without clearing so a lost vehicle ends with four zig-zagging orders, and batches send_to_depot with sell_vehicle in one step so the sale is refused every time and resubmitted forever. blithe-harbor's disposal took 32 game days and three ERR_VEHICLE_NOT_IN_DEPOT refusals, and buying during the wait bottomed cash at 7,707.

**What.** ns_air_agent/air_health_check.py: treat at_station and in_depot as normal, gate on elapsed days in that state (30 or more is stuck), check orders are exactly two goto_station matching the route, and refuse any verdict harsher than watching before day 75, because cargo_delivered_total was exactly 0 until day 73. plan_repoint.py: read get_orders, emit remove_order per existing index descending, then the two correct orders, then start_vehicle, resolving the route from journal.routes rather than from the last route in a list. plan_service.py: send_to_depot_service only, never send_to_depot, which parks the aircraft and needs a follow-up start. plan_retire.py: a journal-backed state machine marked, sent, in_depot, sold, polling get_vehicle_info.in_depot between phases, warning the round trip is 20 to 35 game days, and marking expected proceeds unavailable to the reserve guard while the vehicle is in flight.

### 7. Middleware: state injection and the turn guard

**Why.** The sub-agents are recreated from scratch every turn because prepare_chat_context preserves only the front man's history, so any instruction to "call read_position first" is a page of prose that a fresh agent may skip. Injecting the position removes that page and guarantees fresh truth. The turn guard catches the two turns that waste a day: one that plans without committing, and one that never moves the clock.

**What.** agents/neuro_san/middleware/air_state_middleware.py subclassing langchain.agents.middleware.AgentMiddleware (NOT neuro_san_studio.middleware, which the installed 0.3.19 wheel does not ship): abefore_model resolves state fresh because it changes between calls within a turn, awrap_model_call prepends a scoped '## Position' block (phase, money and reserve, the nine score components with gaps, the fleet table with per-vehicle profit, the route table with waiting and trend, situation.problems, the refusal ledger, the pending plan, decisions due for review), with per-agent scoping via a constructor arg and progress_reporter streaming surveyed sites and committed actions with a 5-second leading-edge throttle and an end-of-run flush. turn_guard_middleware.py: aafter_agent with @hook_config(can_jump_to=["model"]), returning {"messages": [HumanMessage(...)], "jump_to": "model"} when the plan is non-empty and uncommitted or when the turn neither committed nor advanced, capped at 2 retries and reset after a clean turn. Both take sly_data and progress_reporter via HOCON args.

### 8. Registries: ns_common.hocon and ns_air_agent.hocon

**Why.** The network must be renamed from nttd_air to ns_air_agent, must declare an explicit sly_data allow-list (nothing does today, which is why all cross-turn state dies), must drop AAOSA in favour of a named pipeline, and must set the step and execution limits that a 60-day turn needs.

**What.** registries/ns_common.hocon: llm_config (claude-sonnet network-wide), the shared instruction blocks via include and ${substitution} with adjacent-string concatenation, and the allow block declaring to_upstream sly_data {ns_turn_summary: true} and to_downstream {session_id, token} only, explicit rather than true because a live asyncio.Lock must never be in the set. registries/ns_air_agent.hocon: front man AirCompany on claude-opus with about 15 lines of instructions and tools Scout, Builder, FleetGrowth, FleetCare, read_situation, inspect, set_loan_to, commit_plan, advance_days, note_decision; the four sub-agents on the cheap model with 4 or 5 tools each and no aaosa_call wrapper, since a directed pipeline replaces the determine-follow-up-fulfill protocol; every behavioural rule pushed into function and parameter descriptions rather than prose; structure_formats json on the front man; max_message_history 40; error_formatter json with error_fragments ["Error:"] on the tool-bearing agents and [] on AirCompany, whose narration legitimately contains the word Error; max_steps 40000 and max_execution_seconds 6000. Update manifest.hocon and delete nttd_air, nttd_ground and nttd_portfolio with their seven coded tools.

### 9. Runner and repo hygiene

**Why.** fix_problems.py is untracked while all three HOCONs reference it, so a clean clone fails to load every network. Two tests fail against current code, TestToolResolution omits three tools, one lock test asserts a design that is changing, and the runner sends a byte-identical prompt every turn with no turn number or elapsed days even though it computes them.

**What.** examples/neuro_san_play.py: put the turn number, elapsed game days and days remaining into the TURN prompt, and keep chat_context round-tripping as it already does. Rewrite tests/test_neuro_san_networks.py: enumerate tools from the HOCON so no tool can be untracked and uncovered again; rewrite test_the_networks_know_that_acting_costs_a_day and test_buying_without_a_route_is_refused against the new surface; rewrite test_two_gateways_on_one_session_share_the_lock to assert sharing through SessionResources rather than sly_data; add a test asserting no registry references a class that is not tracked in git. Regenerate agents/neuro_san/README.md and DESIGN.md to describe the 26 tools. Run uv run ruff check.

### 10. Test suite for the invariants, then a scored run against the baseline

**Why.** Every invariant in this design exists because a specific run lost points to breaking it. A test that fails when the invariant is removed is what stops it drifting back, and CLAUDE.md requires tests for new functionality. The run is the only evidence the design works.

**What.** tests/ with a fake gateway (recorded fixtures from logs/sessions/20260815-071144ist-grand-tundra and 20260815-073254ist-blithe-harbor, no live server) covering: the airport-type to plane-type table rejects PT_BIG_PLANE at a commuter field; survey_airport_sites rejects a spot 29 tiles out and one with within_coverage false; confirm_airports flags a station named Fontborough Airport when Tonwood was intended; rank_corridors ranks a 289-tile pair above a 47-tile pair; commit_plan refuses buy_vehicle plus add_order on its own output in one batch, refuses connect_rail alongside anything, and refuses a twice-refused fingerprint; plan_retire never emits sell_vehicle before in_depot is observed; plan_repoint clears before it adds and resolves the route from the journal not the list tail; fleet_report reports a vehicle gone; score_report marks min_profit, min_income and cargo unreachable and states when the rating is -1; advance_days does not report session_ended on an HTTP error; choose_aircraft passes the literal "aircraft". Then run a full stepped T1 session on seed 1169784865 and compare rating, cargo_delivered_total, final_loan, action count and step count against the 173 baseline, using logs/sessions/<id>/actions.parquet and result.parquet.


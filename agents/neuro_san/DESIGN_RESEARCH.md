# ns_air_agent

A neuro-san agent network that plays one nttd session as an air transport company.

Target: beat the best hand-played air run on the same seed. That run
(`20260815-071144ist-grand-tundra`, seed 1169784865) scored **rating 173, 4,975 cargo, 41
actions over 23 of 366 steps**. This design keeps everything that run got right, fixes the
three failures its sibling runs made, and claims two things it left on the table: the
12 wasted opening days and the 50 forfeited `SCORE_LOAN` points.

---

## 0. What this replaces, and why a redesign rather than an extension

The current `nttd_air` / `nttd_ground` / `nttd_portfolio` networks share seven coded tools
and differ by three lines of prose. Two defects in them are run-ending on their own:

1. **No registry declares `allow.to_upstream.sly_data`.** neuro-san's `SlyDataRedactor` is
   security-by-default: with nothing allow-listed it returns `None`. So every scrap of
   cross-turn state dies at turn end. From turn 2 onward `buy_and_dispatch` answers "no route
   yet" forever, and `read_position.recent_failures` (the entire mechanism built to stop the
   measured 35 repeated refusals) is always empty.
2. **`idle_reason` is non-empty for any stationary vehicle**, including `at_station` and
   `in_depot`. A healthy air fleet loading at terminals is reported as a wall of problems, the
   front man is told to fix those before expanding, and `fix_problems` sells a plane it
   "fixed" three times.

Beyond those: `rank_sites` prefers the *smallest* airport that covers a town (the exact
inverse of the measured playbook), caches at most `limit` sites for the whole session and
never excludes towns already built on; `fix_problems` re-orders every broken vehicle onto the
*last* route's stations and batches `send_to_depot` + `sell_vehicle` into one step so the sale
always fails; `verify_reachable` cannot answer for air at all, yet ground rule 1 requires it;
and nothing reports per-vehicle profit, cargo waiting per route, the score breakdown, or a
crash.

Extending that surface would keep the shape that produced the defects. This is a rewrite with
a different spine: **durable state on disk, a batch accumulator, a strategist that sees
everything, and every invariant in Python.**

---

## 1. Design spine, in five sentences

1. **One turn is: look, decide, plan, commit once, advance.** Planning is free; only
   `commit_plan` and `advance_days` move the clock.
2. **A step is a game day and a batch has no ceiling**, so a turn that builds four airports
   spends one day, not four.
3. **Cross-turn memory lives on disk** in a per-session journal, not in `sly_data`, because
   `sly_data` provably does not survive a turn boundary here.
4. **The strategist sees the whole picture without asking**, because middleware injects it
   into the system prompt on every model call.
5. **Every identifier comes from the game**, carried as an opaque id through tools and the
   journal. The model never types a coordinate, an engine id or a tile.

---

## 2. Agent roster

Five agents. **No AAOSA.** AAOSA's protocol ("call ALL your down-chain agents to determine
who is responsible") is for federated routing between agents of unknown capability. Here the
capabilities are known and fixed, and the protocol would triple the LLM calls per turn for no
information. The front man calls exactly the agent it needs, in a named order. This follows
the user guide's own advice to "define an explicit pipeline (analyze, decompose, delegate,
synthesize)".

### 2.1 `AirCompany` (front man, strong model)

**This is the strategist.** It is the front man deliberately: neuro-san's
`prepare_chat_context` preserves **only the front man's message history** across turns, so the
front man is the one agent with continuity. Putting strategy anywhere else means the
strategist starts every turn with amnesia, which is what happens today.

It cannot be defined by a `class` (neuro-san forbids a coded-tool front man), but it may hold
coded tools in its `tools` list, and it does.

Sees, injected by `AirStateMiddleware` on every model call, never requested:
phase, money and headroom, the nine score components with gaps, the fleet table with
per-vehicle profit, the route table with cargo waiting and trend, `/state/situation.problems`,
the refusal ledger, the pending plan, and decisions due for review.

Tools: `Scout`, `Builder`, `FleetGrowth`, `FleetCare` (agents); `read_situation`, `inspect`,
`set_loan_to`, `commit_plan`, `advance_days`, `note_decision` (coded).

Instructions: about 15 lines. Everything else is in tool descriptions and injected state.

### 2.2 `Scout` (cheap model)

Turns the map into a ranked, pre-costed table of candidate corridors. Emits `corridor_id`s
and nothing the strategist has to copy.

Tools: `survey_airport_sites`, `rank_corridors`, `price_check`, `inspect`.

### 2.3 `Builder` (cheap model)

Turns a chosen `corridor_id` into planned build actions, and verifies after commit that the
airport landed in the town it was meant to serve.

Tools: `plan_build_corridor`, `confirm_airports`, `plan_upgrade_airport`, `plan_town_action`,
`price_check`.

### 2.4 `FleetGrowth` (cheap model)

Adds aircraft. Owns the airport-class to plane-class pairing and the hangar tile.

Tools: `choose_aircraft`, `plan_buy_aircraft`, `plan_clone_aircraft`, `plan_dispatch`.

### 2.5 `FleetCare` (cheap model)

Keeps the fleet alive. Never sells anything on its own initiative; disposal is a strategist
decision executed as a state machine.

Tools: `air_health_check`, `plan_repoint`, `plan_service`, `plan_retire`, `inspect`.

### 2.6 Model assignment

`llm_config` network-wide is `claude-sonnet`; `AirCompany` overrides to `claude-opus`. The
research is explicit that the strong model belongs only on the strategist and that
site-scoring and formatting agents can run cheap. `Scout` gets `temperature` default and
nothing generative; there is no candidate-diversity role here because candidate generation is
Python.

---

## 3. Tool catalogue

26 tools. One class per module, per the project coding guidelines. Every tool returns
`"Error: <what went wrong>. <what to do instead>"` as a string rather than raising:
`attempt_invoke` converts exceptions to `"Error: ..."` anyway, and a deliberate message is a
retry prompt written for the model rather than for the log.

Every tool that reads the world uses `POST /v1/participant/sessions/{sid}/state/gs/query?action=<name>`
with params as the whole body (free, no game day), or `GET .../state/full` and
`.../state/situation`. Only `commit_plan` and `advance_days` call `POST .../step`.

### 3.1 Shared, mode-agnostic (`ns/`)

| tool | args | returns | invariant or failure it owns |
|---|---|---|---|
| `read_situation` | none | phase, money, problems, counts, days_left, terminated | **Owns the at_station false positive.** Reads `GET /state/situation`, whose `problems` list never touches `idle_reason`. Its `_SETTLING_DAYS` is 400, longer than a 366-day run, so "vehicle is losing money" cannot fire spuriously during the 73-day ramp. Stashes the full snapshot in `sly_data`; returns about 25 lines. |
| `score_report` | none | the nine components, points earned, gap to next point, three marked unwinnable | **Owns the opaque rating.** Computes from `get_expense_breakdown`, `get_companies`, `/state/full`. States plainly when `performance_rating` is `-1` (not yet computed) instead of reporting a catastrophic score. Marks `SCORE_MIN_PROFIT` (needs 2-year-old vehicles), `SCORE_MIN_INCOME` (needs every quarter profitable) and `SCORE_CARGO` (8 cargo types) as out of reach in T1 so the strategist stops optimising them. |
| `fleet_report` | none | per aircraft: id, route, `profit_this_year`, `profit_last_year`, age, state, orders_ok; plus `gone_since_last_look` | **Owns invisible crashes and SCORE_VEHICLES blindness.** `get_vehicles(vehicle_type="aircraft")`, diffed against `fleet_seen` in the journal. A fleet dropping 9 to 7 now reads "aircraft 14 and 16 are gone since day 203", not a smaller number. `SCORE_VEHICLES` counts vehicles with positive *last year* profit and nothing previously surfaced it. |
| `route_report` | none | per route: id, corridor, stations, vehicles, profit, cargo waiting each end, 30-day trend, saturated bool | **Owns unknowable saturation.** Waiting-cargo history is kept in the journal so a trend exists. Replaces the unexplained 200-unit threshold with growing-versus-shrinking, which is the actual question ("more vehicles here" versus "a new corridor"). |
| `inspect` | one of `station_id`, `vehicle_id`, `town_id` | the full record for that entity | **Owns the missing drill-down.** `get_station_info` / `get_vehicle_info` + `get_orders` / `get_town_info` + `get_town_rating`. Free, so there is no cost argument against it. |
| `refusal_ledger` | none | refusals grouped by (action, param fingerprint) with count, `error_name`, `error_code`, first and last game date, and a do-not-retry list | **Owns the measured 35 identical refused purchases.** Also injected into every agent's prompt, so a refusal is in front of the network before it decides, not after it repeats. |
| `note_decision` | `what`, `why`, `expected`, `review_on_day` | confirmation | **Owns "the network cannot see its own past decisions".** The journal is the only thing that survives; the sub-agents have no chat continuity at all. |
| `plan_show` / `plan_clear` | none / `reason` | the pending batch | Lets the strategist abandon a half-built plan without committing it. |
| `commit_plan` | none | per-action verdict table, `terminated`, `end_reason`, day advanced | **Owns batching and the discarded step result.** See section 5. |
| `advance_days` | `days` (1..120), `stop_when` (list of named conditions) | days actually passed, why it stopped, what changed | **Owns blind, uninterruptible waiting.** Reads `terminated` and `end_reason` from each `StepResult` instead of treating any exception as the end of the run (the current `let_time_pass` has a bare `except Exception` that convinces the network a live session has finished). Reads the snapshot each step returns for free rather than issuing a separate GET. `stop_when` conditions: `vehicle_lost`, `fleet_shrank`, `cash_below:N`, `cargo_waiting_above:N`, `quarter_boundary`, `day:N`. |
| `set_loan_to` | `amount` | planned action + resulting balance/loan | **Owns the 50 forfeited SCORE_LOAN points.** `set_loan` sets an exact amount, not a delta. Appended to the plan so it rides in the same step as the builds it funds (the hand-played run spent a whole day on it alone). Warns when crossing 250,000 in either direction, because `SCORE_LOAN = max(0, 250000 - loan)`. |
| `price_check` | `what` (corridor_id / engine_id / airport_type) | price, source (`measured` / `catalogue` / `estimate`) | **Owns blind borrowing and the orphaned airport.** Engine prices come from `get_engines.price`. Airport prices are learned: `commit_plan` records the cash delta across each build into `journal.prices`, so after the first build the map's own numbers are known. Where nothing is known it can ride an `estimate_cost` action along with a wait step, which costs nothing extra because the step is a day either way. It injects `company_id` into the **nested** `params` (the documented trap: `apply_company_scope` only sets the top-level one) and posts to `/actions/submit`, because `estimate_cost` is a participant action and is refused at `/state/gs/query` with 403. |

### 3.2 Air-specific (`ns_air_agent/`)

| tool | args | returns | invariant or failure it owns |
|---|---|---|---|
| `survey_airport_sites` | `top_n_towns` (default 20), `max_tiles_from_centre` (default 6) | list of `{site_id, town, population, airport_type, x, y, distance, within_coverage}` | **Owns FAILURE 1 (blithe-harbor).** That run built a metropolitan airport 29 tiles from Tonwood (pop 2421); it landed inside the catchment of Fontborough (pop 348) and two 480-seat planes flew an almost empty 286-tile leg: rating 118 versus 173. This tool rejects any spot with `within_coverage=false` or `distance > max_tiles_from_centre`. All four airports in the best run were 3 to 6 tiles out. It calls `find_airport_spots(town_id, airport_type, radius=7)`, never radius 20: `find_airport_spots` sorts by cargo acceptance and a wide radius buries the close-in answer. It calls `get_airport_types` first and, per town, keeps the **largest** type that fits close in, which is the exact inverse of the current `rank_sites` (which sorts by `width*height` ascending and therefore always picks commuter, which forces small planes everywhere). Handles the empty result explicitly instead of returning `[]` in silence. Writes site records to the journal with stable `site_id`s and marks ones already built on. |
| `rank_corridors` | `limit` (default 8) | list of `{corridor_id, towns, populations, distance, airport_types, plane_class, income_per_unit, expected_revenue_per_trip, build_cost}` | **Owns "long legs pay" and the `/state/routes?agent_type=air` trap.** Measured: one big plane on a 205-tile leg earned 74,986 while small planes on 35-tile hops earned about 13,000 each. Ranking is `min(pop_a, pop_b)` weighted, distance **favoured not penalised**, times `get_cargo_income(PASS, distance)`. This deliberately does not reuse `builders._closest_pair`, whose "short beats big" scoring is correct for road and has the wrong sign for air. It never calls `/state/routes?agent_type=air`, which appends "air" only at distance >= 100 tiles and so returns an all-but-empty list on a 256x256 map, making an air agent report "nothing to build". |
| `plan_build_corridor` | `corridor_id` | planned actions + total cost + resulting balance | **Owns the orphaned airport and the wasted borrow day.** Prices both airports first; if only one is affordable it plans neither and says so (today `build_route` returns not-ready and walks away from a paid-for airport it neither records nor removes). Appends `set_loan` (if needed) plus both `build_airport` calls to **one** batch. Always passes `airport_type` explicitly, because the default 0 silently means AT_SMALL, which is not the footprint the site was surveyed for. |
| `confirm_airports` | `corridor_id` | station ids, names, hangar tiles, `serves_intended_town` bool per end | **Owns FAILURE 1's cheapest detector.** After commit it reads the station **name** back from `get_stations` and asserts it contains the intended town's name. The station in blithe-harbor was literally named "Fontborough Airport": the game was saying which catchment it landed in and nothing read it. Also calls `get_hangars` (no parameters) and caches `station_id -> hangar_tile` into the journal. |
| `plan_upgrade_airport` | `station_id`, `new_airport_type` | planned actions, cost, aircraft that must be re-dispatched | **Owns the missing upgrade path.** `remove_airport(tile)` then `build_airport(same tile, larger type)` in one batch, with the follow-up re-dispatch queued in the journal. Commuter to international quadruples the load on the same leg. Note the addressing asymmetry the tool absorbs: `remove_airport` takes a tile, `open_close_airport` takes a station id. |
| `plan_town_action` | `town_id`, `action` | planned action, cost, current rating | Unused yield levers: `TOWN_ACTION_ADVERTISE_LARGE` and `TOWN_ACTION_BUY_RIGHTS` raise an air station's passenger yield directly. Gated on `get_town_rating` and cash. Sends the integer enum despite the manifest declaring the field as a string. |
| `choose_aircraft` | `corridor_id` | top 3 `{engine_id, name, capacity, max_speed, price, running_cost, plane_type, expected_revenue_over_remaining_days}` | **Owns the highest-probability LLM error in the whole air surface.** `_VehicleTypeEnum` accepts exactly `train`/`road`/`ship`/`aircraft` and silently returns train engines for anything else **with `success: true`**, so an agent asking for "air" or "plane" engines confidently plans a fleet of steam locomotives. This tool hard-codes the literal `"aircraft"`. It also filters `plane_type` against the corridor's **worse** airport type using a fixed table: `PT_BIG_PLANE` (3) only if every station is in {LARGE 1, METROPOLITAN 3, INTERNATIONAL 4, INTERCON 7}, otherwise `PT_SMALL_PLANE` (1); heliports (2, 6, 8) take no aeroplane at all. A combined run lost three big planes (about 150,000) to bare `vehicle_crashed` events by flying them into commuter airports. Scoring is capacity times `income_per_unit` at this distance times speed, minus running cost over remaining days: not `capacity/running_cost` (which ignores price, speed and leg length) and not max capacity (which picks the big plane every time). Re-queries `get_engines` on every call so mid-run availability changes are picked up. |
| `plan_buy_aircraft` | `corridor_id`, `engine_id`, `count` | planned `buy_vehicle` actions, cash after, reserve check | **Owns the hangar trap and the cash floor.** The depot for an aircraft is the **hangar tile**, and the offset from the airport tile is not derivable: measured +5 in x for metropolitan and large, +4 in x for commuter, +3 in **y** for international. Four consecutive `buy_vehicle` calls at the airport's own coordinates failed `ERR_UNKNOWN` with no diagnostic. This tool never computes a hangar; it reads the cached `get_hangars` result. It refuses any purchase that would take cash below the 40,000 reserve (best run's floor was 38,441; blithe-harbor bottomed at 7,707 and nearly died). |
| `plan_clone_aircraft` | `route_id`, `count` | planned `clone_vehicle` + `start_vehicle` actions | **Owns a wasted day per growth wave.** `clone_vehicle(vehicle_id, depot_tile=hangar, share_orders=true)` buys **and** orders in one action, so adding aircraft to a live route costs one day and 2N actions instead of two days and 3N. A clone arrives **stopped** and must be started, and a clone without an explicit depot is built at the original's current tile, which for a flying aircraft is nowhere useful. Both are encoded. |
| `plan_dispatch` | `vehicle_ids` (from the previous commit) | the triplet per vehicle | **Owns the double-start and the full-load deadlock.** The fixed pattern from the best run: `add_order(station_src, order_flags=0)`, `add_order(station_dst)`, `start_vehicle`, exactly once. A second `start_vehicle` toggles the plane back to stopped and has parked a whole fleet. No air run ever used `OF_FULL_LOAD` (64) or `OF_FULL_LOAD_ANY` (96); 64 is a common self-inflicted deadlock. Non-stop flags are dropped for aircraft, so flags 0 is correct. Uses `station_id`, never `destination` (which is read as a tile index first). |
| `plan_repoint` | `vehicle_id`, `route_id` | planned remove-then-add-then-start | **Owns fix_problems' worst bug.** Today `_stations_for` walks `reversed(routes)` and re-orders **every** broken vehicle onto the newest route's stations regardless of which route it flies, and `add_order` **appends**, so a vehicle that went lost ends with four orders zig-zagging between two unrelated town pairs, and `read_position` reports it as healthy. This tool reads `get_orders`, emits `remove_order` for each existing index in descending order, then the two correct ones, then `start_vehicle`. Re-pointing salvaged a losing leg in solid-coral. |
| `plan_retire` | `vehicle_id` | the next step of the disposal state machine | **Owns FAILURE 2.** blithe-harbor issued `sell_vehicle` on a flying aircraft three times (`ERR_VEHICLE_NOT_IN_DEPOT`) and the sale finally completed 32 game days after the first attempt. `fix_problems` still batches `send_to_depot` and `sell_vehicle` into the same step, so the sale is refused every time and, because the repair counter is already past its threshold, the pair is resubmitted forever. This is a journal-backed machine: `marked -> sent (send_to_depot) -> in_depot (polled from get_vehicle_info) -> sold (sell_vehicle)`. It warns the caller the round trip is 20 to 35 game days on a long leg. **Owns FAILURE 3 too:** while any vehicle is in the pipeline, its expected proceeds are marked unavailable, so the reserve guard cannot be spent against money that has not arrived (blithe-harbor bought two more planes during the wait and bottomed at 7,707). |
| `plan_service` | `vehicle_id` | planned `send_to_depot_service` | **Owns the parked fleet.** `send_to_depot` stops the vehicle at the depot and needs a follow-up `start_vehicle`; `send_to_depot_service` services and resumes its orders. A network that forgets the follow-up loses the aircraft for the rest of the run. This tool never emits `send_to_depot` for maintenance. |
| `air_health_check` | none | per aircraft: `lost`, `idle_reason` with days in that state, `orders_ok`, `age`, verdict | **Owns the sell-a-healthy-plane failure and the no-judgement window.** `at_station` and `in_depot` are normal; a plane that has been `at_station` for 30 or more days is stuck. It refuses to return any verdict harsher than "watching" before day 75, because `cargo_delivered_total` stayed at exactly 0 until day 73 in the best run and the far end of a 289-tile trunk did not see its first aircraft until day 43. Nothing may be sold, re-ordered or judged on profit before then. |

---

## 4. State: three tiers, and why

### 4.1 Tier 1, per turn: `sly_data`

Keys are module constants in `ns/constants.py` so a typo cannot silently create a second
store (the pattern the agent_network_editor uses for exactly this reason).

| key | contents |
|---|---|
| `session_id`, `token` | supplied by the runner, never returned upstream |
| `ns_plan` | the pending action batch, list of `{action, params, why, source_tool}` |
| `ns_world` | this turn's `/state/full` and `/state/situation` |
| `ns_survey` | this turn's site and corridor records |
| `ns_engines`, `ns_hangars` | query caches |
| `ns_turn_summary` | the only key allow-listed `to_upstream` |

**The `asyncio.Lock` moves out of `sly_data`.** Today `NttdGateway._step_lock` stores a live
`asyncio.Lock` under `sly_data["step_lock"]`, which means that naively declaring
`allow.to_upstream.sly_data = true` to fix the state loss would fail on serialisation. The
lock moves into a per-process `SessionResources` registry keyed by session id, which is where
process-scoped, non-serialisable things belong.

The registry declares an explicit, minimal allow block:

```
"allow": {
    "to_upstream":   { "sly_data": { "ns_turn_summary": true } },
    "to_downstream": { "sly_data": { "session_id": true, "token": true } }
}
```

Explicit rather than `true`, because security-by-default is the correct posture for a bearer
token and because a live lock must never be in the set.

### 4.2 Tier 2, whole run: the journal on disk

`logs/ns_agent/<session_id>/journal.json`, plus a human-readable `journal.md`. Written through
one `RunJournal` class with atomic replace.

| section | contents | why it exists |
|---|---|---|
| `sites`, `corridors` | the survey, with `built` / `rejected` marks | a survey should happen once, not every turn; today it re-runs every turn and still caps at 6 |
| `built` | station_id, town, airport type, tile, hangar tile, actual cost | hangar tiles are not derivable; costs are learned per map |
| `routes` | route_id, corridor_id, stations, dispatched vehicles | so `plan_repoint` knows which route a vehicle belongs to |
| `refusals` | action, param fingerprint, `error_name`, `error_code`, count, first/last date | the 35-refusal fix, and the hard stop in section 6 |
| `decisions` | date, what, why, expected, review_on_day | the strategist's own history |
| `disposals` | vehicle_id, state | the two-phase sale |
| `prices` | learned airport cost per type, engine prices | so borrowing is arithmetic, not a guess |
| `fleet_seen` | vehicle_id, last seen day | crash detection by diff |
| `waiting_history` | station_id, [(day, waiting)] | saturation trend |

**Why disk and not `sly_data`:** `sly_data` provably does not survive a turn boundary in this
deployment, and even with an allow block it depends on the runner echoing it back. Disk also
gives the benchmark a post-run audit trail that survives a crashed runner.

**Why not `PersistentMemoryMiddleware`:** the neuro-san docs draw a hard line: persistent
memory "holds user-facing information ... It is not for internal LLM state such as LLM call
results, pass/fail outcomes, or execution traces." A refusal ledger and a disposal state
machine are exactly execution traces.

**Why not `chat_context` alone:** `prepare_chat_context` preserves only the front man's
history, so `Scout`, `Builder`, `FleetGrowth` and `FleetCare` are recreated from scratch every
turn. Whatever they saw survives only if the front man happened to narrate it. `max_message_history`
is set on the front man (40) so a 100-turn run does not blow the context window; nothing sets
it today.

### 4.3 Tier 3, across runs

Out of scope for v1. When it arrives it is `PersistentMemoryMiddleware` with a `markdown_file`
backend holding map-independent lessons ("airports beyond 6 tiles do not pay"), never in-run
state.

---

## 5. Batching: the plan, and the single commit

### 5.1 The rules

`StepRequest.actions` has **no ceiling** ("a batch of any size still advances exactly one
day"), and the T1 scenario is `interval_days = 1` for 366 steps. So the cost of a decision is
the number of *steps*, not the number of actions.

The best hand-played run spent 15 days on its opening: one `set_loan`, four `build_airport`
one per day, five `buy_vehicle` one per day, five dispatch triplets one per day. Under this
design the same opening is **three days**:

| step | batch | why it cannot merge with the next |
|---|---|---|
| 1 | `set_loan` + 4x `build_airport` | station ids and hangar tiles are unknown until built |
| 2 | 5x `buy_vehicle` | vehicle ids are unknown until bought |
| 3 | 5x (`add_order`, `add_order`, `start_vehicle`) = 15 actions | nothing follows |

Between steps, `get_stations`, `get_station_info` and `get_hangars` are free queries that cost
no day. That is 12 extra earning days at the front of the run against the 173-rated baseline,
and the ramp to first cargo was 73 days, so those days are at the most expensive point.

### 5.2 What `commit_plan` validates before submitting

Validate everything and report every error at once, before mutating anything (the
`CreateNetwork` rationale: "collect every offending name so the caller can fix them all in one
pass rather than one per turn").

1. **Intra-batch dependency.** An action whose parameters depend on another action's *result*
   in the same batch is refused, naming the pair. Concretely `buy_vehicle` then `add_order` on
   the vehicle it creates. This is the only genuine two-step sequence in air, and
   `clone_vehicle(share_orders=true)` removes it for planes 2..N.
2. **Tick-dependence.** `connect_rail` and `connect_road` are the only actions that cannot run
   against a paused world, so they must be alone in a step. Air never emits them; the rule is
   written here so road and rail inherit it.
3. **Affordability.** Sum of priced actions against balance plus any `set_loan` in the same
   batch, minus the 40,000 reserve, minus proceeds of any in-flight disposal.
4. **Refusal history.** Any action whose (action, param fingerprint) has been refused twice is
   refused in Python with the prior `error_name`. A hard stop, not a prompt.
5. **Air invariants.** Plane class against airport class; exactly one `start_vehicle` per
   vehicle; `add_order` flags 0 for air; `airport_type` explicitly present.

### 5.3 What `commit_plan` returns and records

It posts the whole batch to `POST /step` and reads the **entire** `StepResult`:
`action_results`, `snapshot`, `days_advanced`, `terminated`, `end_reason`. Today the gateway
throws away everything but `action_results`, which is why `let_time_pass` has to guess whether
the run is over.

It returns a compact verdict table and writes each failure to the journal. It branches on the
**presence of `error_code`**, never on substring-matching the prose: an OpenTTD refusal carries
`error_code` / `error_name` / `error_category`; an nttd precondition failure carries none, and
that absence is how "the game said no" is told from "the request never reached it".

It also records the cash delta per build into `journal.prices`, which is how airport costs get
learned without spending a day on `estimate_cost`.

### 5.4 The action envelope

One function builds `{"action": name, "params": {...}}`. `StepRequest` refuses an action with
parameters at the top level (it used to drop them in silence and then fail on
"the index 'x' does not exist"). No tool constructs the envelope by hand.

---

## 6. Failures: how they are surfaced, and how repetition stops

Four layers, deliberately redundant because the measured failure was 35 identical refusals:

1. **Tool level.** `"Error: <what> - <what to try instead>"` as a normal return value, with
   the game's own text kept verbatim where it carries a coordinate.
2. **Journal level.** Every refusal is fingerprinted and counted with its `error_name`.
3. **Prompt level.** `AirStateMiddleware` injects the refusal ledger into every agent's system
   prompt each model call, so the refusal is in front of the model *before* it decides.
4. **Hard stop.** `commit_plan` will not submit a twice-refused call at all.

Network-level HOCON: `"error_formatter": "json"` so failures are machine-parseable, and
`"error_fragments": ["Error:"]` on the tool-bearing agents but **not** on `AirCompany`, whose
narration legitimately contains the word "Error" and would otherwise be substring-matched and
replaced wholesale (the `instructions_writer` precedent).

Also set network-wide: `"max_steps": 40000`, `"max_execution_seconds": 6000`. The defaults
(10,000 and 300 seconds) will not survive a turn that advances 60 days.

---

## 7. Middleware

### 7.1 `AirStateMiddleware` (`awrap_model_call`, plus `abefore_model` to refresh)

Prepends a compact `## Position` block to the system message on every model call, resolved
fresh in `abefore_model` because the world changes between calls within a turn. This is the
single most reusable idea in the research: the planner is never told to "call read_state
first", the instructions shrink by a page, and the model always sees fresh truth.

Injected block, about 40 lines, scoped per agent (Scout gets sites and money; FleetCare gets
fleet and problems; AirCompany gets all of it):

```
## Position, day 182 of 366 (GROWTH)
Money  balance 150,155  loan 300,000  max 300,000  headroom 0  reserve floor 40,000
Score  173/1000: delivered 3303/40000 (33pts) vehicles 5 (4pts) stations 4/80 (5pts)
       max_income 142,808 (100pts) money (1pt) loan 0pts (loan > 250,000)
       unreachable in T1: min_profit, min_income, cargo
Fleet  v7  trunk  +74,986 this yr   |  v5  trunk  +71,204  |  v9  [2,1] +16,379 ...
Routes [0,1] 289 tiles 2 planes  waiting 210 PASS rising     SATURATED
       [3,1] 119 tiles 1 plane   waiting 34 PASS flat
Problems  cargo is piling up at Hondinghall Airport (210 PASS)
Refused   none in the last 30 days
Plan      empty
Review    day 182: "check trunk saturation" (set on day 110)
```

`progress_reporter` streams each surveyed site and each committed action to the client as
`AGENT_PROGRESS` structures with the 5-second leading-edge throttle and end-of-run flush, so
the nttd UI sees the detail while the LLM context sees only the summary.

### 7.2 `TurnGuardMiddleware` (`aafter_agent`, `@hook_config(can_jump_to=["model"])`)

Fires when the front man has no pending tool calls. Two checks:

- **Uncommitted plan.** If `ns_plan` is non-empty, jump back to `model` with
  `"You planned N actions and did not commit them. Call commit_plan, or plan_clear with a reason."`
  This is the "planned but never committed, day wasted" failure, caught even if the agent
  never notices.
- **Turn with no clock movement.** If the turn neither committed nor advanced, jump back with
  the same shape. A turn that moves nothing is a turn the runner will simply repeat.

Retry cap 2, reset after a clean turn, following the `AgentNetworkPersistenceMiddleware`
pattern. A model that cannot fix its own turn must not burn the game loop.

**Not used:** `LlmConfigToolSelectorMiddleware`. The front man advertises 10 tools, above the
7 the selector defaults to, but the tools are grouped by agent and each sub-agent sees 4 or 5.
If tool selection degrades in practice the selector is the lever, with the caveat from its own
HOCON that terse `function.description`s then cause tools to be skipped.

---

## 8. How the strategist decides

### 8.1 Phases, from `days_left`

| phase | days | rule |
|---|---|---|
| OPENING | 0 to 15 | Draw the loan, build 4 airports on the top-ranked corridors, buy one plane per airport plus one for the trunk, dispatch. Three steps, not fifteen. |
| RAMP | 15 to 75 | **No judgement.** Nothing may be sold, re-ordered or judged on profit. `cargo_delivered_total` was exactly 0 until day 73 and the far end of a 289-tile trunk saw its first aircraft on day 43. Health checks read station cargo waiting and vehicle position only. `advance_days` with `stop_when=["vehicle_lost","fleet_shrank","cash_below:35000"]`. |
| GROWTH | 75 to 250 | Review every 55 to 70 days. Buy iff `cash - price >= 40,000` **and** the target leg's source station has more than 70 units waiting. Put the new plane on the leg whose existing aircraft has the **highest** `profit_this_year`, not the emptiest leg (the 289-tile trunk took 5 of 9 planes and returned 71% of profit). Upgrade an airport before opening a fifth corridor if the trunk is saturated. |
| ENDGAME | 250 to 366 | No new aircraft unless it can clear its own price before 31 December: `SCORE_VEHICLES` counts only vehicles whose **last year** profit was positive, and the one plane bought on day 183 ended at -2,491. **Repay the loan.** `SCORE_LOAN = max(0, 250,000 - loan)`; the best run ended with 272,065 cash against a 300,000 loan and scored 0 of 50, worth roughly +43 points against an achieved 173. This is the largest single unclaimed component in the whole recorded set and nothing hand-played ever did it. |

### 8.2 The four numbers the best run actually used

Town population; airport-tile distance to the town centre; cargo waiting at each station;
`profit_this_year` per aircraft. All four are injected, all four come from the game, none is
computed by the model. The rest of the injected block exists to stop the model optimising
things it cannot win.

### 8.3 One trade the strategist can now see and previously could not

`SCORE_STATIONS` is 100 points at 80 station facilities. Four airports is 5 points. Twelve
airports would be 15. `SCORE_DELIVERED` is 400 points and is what siting quality moves. The
score report states both so the strategist declines the station chase explicitly rather than
never seeing it.

---

## 9. File layout in nttd-examples

`AGENT_TOOL_PATH=agents/neuro_san/coded_tools` with `AGENT_TOOL_PATH_ONLY=true`. Class
resolution searches `<tool_path>/<network_name>/` first and then walks up to `<tool_path>/`,
so air tools are referenced bare and shared tools by their package path.

```
registries/
  ns_common.hocon              # llm_config, ground rules, shared agent bodies, allow blocks
  ns_air_agent.hocon
  manifest.hocon               # { "ns_air_agent.hocon": true }

agents/neuro_san/
  coded_tools/
    ns/                                  # mode-agnostic, shared by all four modes
      constants.py                       # every sly_data and journal key, once
      gateway.py                         # NttdGateway (rewritten)
      session_resources.py               # per-process lock + journal handle, keyed by session
      journal.py                         # RunJournal
      plan.py                            # ActionPlan accumulator
      envelope.py                        # the one action-envelope builder
      scoring.py                         # the nine components
      situation/read_situation.py
      situation/score_report.py
      situation/fleet_report.py
      situation/route_report.py
      situation/inspect.py
      situation/refusal_ledger.py
      plan/plan_show.py
      plan/plan_clear.py
      plan/commit_plan.py
      clock/advance_days.py
      money/set_loan_to.py
      money/price_check.py
      memory/note_decision.py
    ns_air_agent/                        # air only
      air_rules.py                       # airport type <-> plane type table, catchment gate
      survey_airport_sites.py
      rank_corridors.py
      plan_build_corridor.py
      confirm_airports.py
      plan_upgrade_airport.py
      plan_town_action.py
      choose_aircraft.py
      plan_buy_aircraft.py
      plan_clone_aircraft.py
      plan_dispatch.py
      plan_repoint.py
      plan_retire.py
      plan_service.py
      air_health_check.py
  middleware/
    air_state_middleware.py
    turn_guard_middleware.py

examples/
  neuro_san_play.py            # turn prompt carries turn number, elapsed and remaining days
```

Middleware subclasses `langchain.agents.middleware.AgentMiddleware`. **It does not import
`neuro_san_studio.middleware`:** the published `neuro-san-studio` 0.3.19 wheel installed here
ships `coded_tools`, `commands`, `discovery`, `exporter`, `importer`, `interfaces`, `mcp`,
`plugins`, `runner`, `templates`, `toolbox` and `utils`, and no `middleware` package.
`AgentChecklistMiddleware` and friends exist only in the studio git checkout and are patterns
to copy, not dependencies to take.

Deleted: `nttd_air.hocon`, `nttd_ground.hocon`, `nttd_portfolio.hocon` and the seven current
coded tools. `nttd_ground` conflates road and rail, which the four-network plan forbids.

---

## 10. How road, rail and water reuse this

Everything under `ns/` is mode-agnostic and moves unchanged: gateway, journal, plan, commit,
advance, score, refusal ledger, inspect, situation, money, envelope. `ns_common.hocon` holds
`llm_config`, the allow blocks, the ground rules and the shared agent instruction bodies via
HOCON `include` and `${substitution}` (adjacent quoted strings auto-concatenate; substitution
does not work inside a quoted string, hence the concatenation style).

A new mode is one HOCON plus one `ns_<mode>_agent/` package holding survey, rank, build and
fleet tools. Three things are deliberately **not** shared:

1. **The corridor ranking function.** `builders._closest_pair` scores `(pop_a+pop_b)/span`,
   "short beats big". That is correct for road (a pair saturates past three or four buses,
   so growth is more pairs) and has the wrong sign for air. Each mode owns its own.
2. **Ground rule 1.** "Nothing buys a vehicle until `verify_reachable` says a vehicle can get
   there" is right for road and rail, where the join is the hard part, and unsatisfiable for
   air, where `trace_route` does not answer and `build_route._air` never called it. Air's proof
   is dispatch-and-observe: `lost` and `idle_reason` on `get_vehicle_info`. Water's is worse
   still (docks are frequently on unconnected water and `trace_route` does not answer for
   water either), so water needs its own connectivity proof.
3. **Tick-dependence.** `connect_rail` and `connect_road` cannot run against a paused world.
   `commit_plan` already enforces "alone in a step", so rail and road inherit the rule for
   free. Air and water never trip it, which is why an air-only network can run entirely
   against a paused world with unbounded deliberation.

Shared by construction: the batch discipline, the refusal ledger, the disposal state machine
(`send_to_depot` then poll then sell is the same in every mode), the score report, the crash
diff, the phases, and the reserve guard.

---

## 11. Repo hygiene that must land with this

Not optional: an air run cannot be evaluated on a network that may not load.

- `agents/neuro_san/coded_tools/fix_problems.py` is **untracked in git** while all three
  HOCONs reference it. A clean clone fails to load every network. The file goes away with the
  rewrite, but the tracking gap must not repeat: the resolution test must cover every tool.
- Two tests currently fail (`test_the_networks_know_that_acting_costs_a_day`,
  `test_buying_without_a_route_is_refused`). They assert design guards, not syntax, and are
  rewritten against the new surface.
- `TestToolResolution.TOOLS` omits `build_route`, `borrow` and `fix_problems`, so three tools
  were never covered by the both-ways import test. The new test enumerates the HOCON.
- `test_two_gateways_on_one_session_share_the_lock` asserts sharing through `sly_data`. It is
  rewritten to assert sharing through `SessionResources`.
- The realtime branch in `NttdGateway.act` posts `{"actions": [...]}` to `/actions/submit`,
  which takes a **single** `ActionEnvelope` and 422s. The T1 benchmark is stepped. **Declare
  the network stepped-only and delete the branch** rather than carry a path that cannot work.
- The README tool table documents six tools and omits two. It is regenerated.

---

## 12. What success looks like

Measured against `20260815-071144ist-grand-tundra` (seed 1169784865, rating 173, 4,975 cargo)
on the same seed:

| claim | mechanism | expected |
|---|---|---|
| opening in 3 steps not 15 | batching in `commit_plan` | 12 extra earning days at the most expensive point of the run |
| loan repaid before 31 December | ENDGAME phase + `set_loan_to` | up to +43 rating points, the largest unclaimed component |
| no airport off-catchment | `survey_airport_sites` gate + `confirm_airports` name check | avoids the 173 to 118 gap that FAILURE 1 caused |
| no big plane at a small field | `air_rules` table in `choose_aircraft` | avoids the measured loss of three aircraft, about 150,000 |
| no repeated refusal | refusal ledger, injected and hard-stopped | avoids the measured 35 refused purchases |
| growth on the earning leg | injected fleet table with per-vehicle profit | reproduces the 71%-of-profit trunk concentration deliberately rather than by luck |

Everything above is checkable from `logs/sessions/<id>/actions.parquet` and
`result.parquet` without instrumenting the network, because neuro-san already journals every
tool call as `{"tool_start": true, "tool_args": ...}` and `{"tool_end": true, "tool_error":
bool, "tool_output": ...}`.

"""A verdict per aircraft, and the only place in air fleet care that judges one.

Repair is most of a well played run, and every failure worth catching in eight hand played runs
was a SINGLE vehicle failing quietly while the fleet count looked healthy: four aircraft parked
in a hangar for sixty days beside a fleet that was still called five strong.

Three rules, each one paid for:

**at_station and in_depot are NORMAL.** An earlier version treated any non-empty `idle_reason`
as a problem. `idle_reason` reads "at_station" for an aircraft loading at a gate and "in_depot"
for one sitting in its own hangar, so a healthy fleet read as a wall of faults, and the repair
tool behind it would eventually have sold working aircraft. This tool never reads
`idle_reason`. Its primary source is the problems list the ENGINE computes on
/state/situation, which declines to call a vehicle broken for loading at a station.

**Where judgement is unavoidable, judge on ELAPSED TIME.** One observation cannot say how long
something has been true: an aircraft parked in a hangar looks exactly like an aircraft that
landed a second ago. So each look records where a vehicle is, and the day it arrived there,
into sly_data. The verdict is about the number of days it has not moved, not about the state
it is in.

**Judge the vehicle, never the calendar.** This used to excuse everything before day 75, a
number taken from one run where cargo delivered stayed at 0 until day 73. That is a fact about
one map's long legs, not about the game, and as a RUN day it also gave no grace at all to an
aircraft bought late. Nothing here depends on how far through the session it is, so the tool
behaves the same on a one year run and a ten year one.

**A staged repair is not a repair.** plan_repoint records that a repoint was staged, and only
this tool may turn that into a repoint that happened, because only this tool can see the aircraft
move. A marker written at staging time made a refused or uncommitted commit read as a completed
repair, which stopped a stuck vehicle being flagged at all.

The vehicle ids it returns come from the game, which is what makes them safe to hand back as
arguments to plan_repoint and plan_retire. Nothing here asks a model for an id.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns import session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns_air_agent import air_keys as air
    from agents.neuro_san.coded_tools.ns_air_agent.choose_aircraft import AIRCRAFT
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where ns and ns_air are siblings and the
    # package above them is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a class reference resolving from anywhere
    # on PYTHONPATH.
    from ns import session
    from ns.gateway import NttdGateway

    from ns_air_agent import air_keys as air
    from ns_air_agent.choose_aircraft import AIRCRAFT

# There is no ramp period here, and there deliberately is not one.
#
# A flat "judge nothing before day 75" used to guard this, taken from one run where cargo
# delivered stayed at 0 until day 73. That was a fact about one map's long legs rather than about
# the game, and being a RUN day rather than a vehicle day it also judged an aircraft bought on
# day 300 the moment it left the hangar.
#
# Replacing it with a per route estimate was worse: it needed a tiles-per-day-per-unit-of-speed
# conversion that nothing in the engine publishes and no run here measured. Every one of the three
# faults below is a real fault at any age, and a newly bought aircraft is already safe because the
# not-moving clause counts from when this tool first saw it, so the window was protecting nothing.

# Days in one place that make an aircraft stuck rather than busy. A CHOSEN threshold, not a
# measured one: a passenger aircraft that has gone a month without changing tile is not loading,
# whatever its leg. It is deliberately generous, because the cost of calling a working aircraft
# stuck is higher than the cost of noticing a broken one a fortnight late.
STUCK_DAYS = 30

# Situation problems about a vehicle are phrased "vehicle <name or id> ...".
_VEHICLE_PREFIX = "vehicle "


class AirHealthCheck(CodedTool):
    """Every aircraft, its verdict and the reason for it. Free: it costs no game day."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._look, args, sly_data)

    async def _look(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        world = await gateway.observe()
        situation = await gateway.situation()
        # Company scoped and aircraft only, unlike situation's vehicle list, and it carries
        # in_depot and the orders in one call so no vehicle needs a query of its own.
        fleet: list[dict[str, Any]] = await gateway.query(
            "get_vehicles", {"vehicle_type": AIRCRAFT}
        ) or []

        record = sly_data.setdefault(air.HEALTH, {})
        seen = record.setdefault("vehicles", {})
        day = _run_day(world.get("game") or {}, record)
        record["day"] = day

        problems = situation.get("problems") or []
        # An aircraft already on its way to a hangar to be sold is reported as such rather than
        # as one more fault to fix. Repointing one is how a disposal gets undone halfway.
        retiring = sly_data.get(air.RETIRING) or {}
        claimed: set[int] = set()
        reports: list[dict[str, Any]] = []

        for vehicle in fleet:
            named = _problems_naming(problems, vehicle)
            claimed.update(index for index, _ in named)
            report = _look_at(vehicle, [text for _, text in named], seen, day)
            report["retiring"] = str(vehicle.get("id")) in retiring
            reports.append(report)

        # An aircraft that is no longer in the fleet was sold or crashed, and its record has to
        # go with it: left behind, it stays on plan_repoint's target list and that tool stages
        # orders for a vehicle id the game no longer knows, which is refused every time.
        # Reporting the loss belongs to fleet_report, which diffs the whole fleet; this only
        # keeps its own timings honest.
        for vid in set(seen) - {str(vehicle.get("id")) for vehicle in fleet}:
            del seen[vid]

        # Anything the engine reported that names no aircraft of ours: an unfinished route, a
        # station nothing calls at, cargo piling up. Passed through verbatim rather than
        # dropped, because fleet care is not the only thing that reads this report.
        others = [
            f"{entry.get('problem')}: {entry.get('detail')}"
            for index, entry in enumerate(problems) if index not in claimed
        ]

        if not fleet:
            return {
                "day": day,
                "aircraft": [],
                "other_problems": others,
                "note": "no aircraft owned yet, so there is nothing to judge",
            }

        return {
            "day": day,
            "judging": True,
            "aircraft": reports,
            # The ids worth acting on, which is why anything already being sold is not in them.
            "stuck": [
                r["vehicle_id"] for r in reports if r["verdict"] == "stuck" and not r["retiring"]
            ],
            "watch": [
                r["vehicle_id"] for r in reports if r["verdict"] == "watch" and not r["retiring"]
            ],
            "being_retired": [r["vehicle_id"] for r in reports if r["retiring"]],
            "other_problems": others,
            "next": (
                "plan_repoint takes any vehicle_id listed here, and an aircraft parked in its "
                "hangar with the right two orders needs nothing more than the start_vehicle "
                f"plan_dispatch stages. Stuck means {STUCK_DAYS} days on the same tile, or "
                "orders that are not two station orders to two different stations, or a problem "
                "the engine itself raised. Watch means one of those is close but not met, and "
                "watch means watch rather than act."
            ),
        }


def _run_day(game: dict[str, Any], record: dict[str, Any]) -> int:
    """How many days of the run have gone, which is what every rule here is written in.

    The horizon is published on the snapshot, so the arithmetic is exact when the run is bounded
    by days. When it is not, the game date still moves one per day, and the first date seen is
    kept so the difference means something.
    """
    total = int(game.get("game_days_total") or 0)
    if total:
        return max(0, total - int(game.get("game_days_remaining") or 0))
    date = int(game.get("game_date") or 0)
    first = record.get("day_zero_date")
    if first is None:
        record["day_zero_date"] = first = date
    return max(0, date - int(first))


def _look_at(
    vehicle: dict[str, Any],
    named: list[str],
    seen: dict[str, Any],
    day: int,
) -> dict[str, Any]:
    """One aircraft: update how long it has been where it is, then judge it."""
    vid = str(vehicle.get("id"))
    entry = seen.setdefault(vid, {})
    where = _where(vehicle)
    if entry.get("where") != where:
        entry["where"] = where
        entry["since_day"] = day
        _promote_staged_repoint(entry, day)
    entry["name"] = vehicle.get("name") or f"aircraft {vid}"
    entry["seen_day"] = day

    still = day - int(entry.get("since_day", day))
    orders_ok = _orders_look_right(vehicle)

    # Per aircraft rather than per run, so a late purchase is described as new instead of as
    # something the run has had all year to fix. The game reports age in days directly.
    entry[air.IN_SERVICE_DAYS] = int(vehicle.get("age") or 0)

    verdict, why = _judge(still, orders_ok, named, entry)
    entry["verdict"] = verdict
    entry["why"] = why
    # The two faults repointing fixes. Kept on the record so plan_repoint has a target list it
    # did not have to derive a second time, and so a model never supplies one.
    entry["needs_repoint"] = verdict == "stuck" or not orders_ok

    return {
        "vehicle_id": int(vehicle.get("id")),
        "name": entry["name"],
        "verdict": verdict,
        "why": why,
        "days_where_it_is": still,
        "where": where,
        "orders": int(vehicle.get("order_count") or 0),
        "profit_this_year": vehicle.get("profit_this_year"),
        "age_days": vehicle.get("age"),
        "repoints": int(entry.get(air.REPOINTS, 0)),
        # Reported separately from the count, because a repoint that is staged and not yet
        # committed has repaired nothing and a reader has to be able to tell the two apart.
        "repoint_staged_on_day": entry.get(air.REPOINT_STAGED_DAY),
    }


def _promote_staged_repoint(entry: dict[str, Any], day: int) -> None:
    """Turn a staged repoint into a completed one, once the aircraft has actually moved.

    plan_repoint can only record an INTENT: it stages a batch and commit_plan submits it, so at
    staging time nothing has happened yet. Writing the accomplishment there meant a repoint that
    was refused, or simply never committed, still counted: this check then stopped flagging a
    vehicle that was still stuck in the same hangar, and plan_retire read the same marker as
    "repointing has been tried" and sold it.

    Movement is the evidence, and it is the one thing this function is called on: the caller has
    just seen the aircraft somewhere other than where it was.
    """
    if entry.get(air.REPOINT_STAGED_DAY) is None:
        return
    del entry[air.REPOINT_STAGED_DAY]
    entry[air.REPOINTED_DAY] = day
    entry[air.REPOINTS] = int(entry.get(air.REPOINTS, 0)) + 1


def _where(vehicle: dict[str, Any]) -> str:
    """The state whose duration is being timed.

    Position plus whether it is in a hangar, and nothing else. Being in a hangar is not a fault
    and neither is standing at a gate; what a repair has to know is how long either has lasted,
    and that needs a value that changes the moment the aircraft does something.
    """
    place = f"{vehicle.get('x')},{vehicle.get('y')}"
    return f"hangar at {place}" if vehicle.get("in_depot") else place


def _orders_look_right(vehicle: dict[str, Any]) -> bool:
    """Two station orders to two different stations, which is what an air route is.

    Not a count. A vehicle whose orders were appended rather than cleared ends with four orders
    zig-zagging between two unrelated town pairs and an order_count that looks busy, and that
    aircraft flies a route nobody planned.
    """
    orders = vehicle.get("orders") or []
    if len(orders) != 2 or not all(order.get("is_goto_station") for order in orders):
        return False
    return orders[0].get("destination") != orders[1].get("destination")


def _problems_naming(
    problems: list[dict[str, Any]], vehicle: dict[str, Any]
) -> list[tuple[int, str]]:
    """The engine's own problems about this aircraft, with where each sat in the list.

    Matched on the whole name followed by a space, so "Aircraft 1" does not claim the problem
    belonging to "Aircraft 11". The index comes back so the caller can pass on everything that
    was not claimed instead of losing it.
    """
    name = str(vehicle.get("name") or "")
    vid = str(vehicle.get("id"))
    found: list[tuple[int, str]] = []
    for index, entry in enumerate(problems):
        text = str(entry.get("problem") or "")
        if not text.startswith(_VEHICLE_PREFIX):
            continue
        rest = text[len(_VEHICLE_PREFIX):]
        if (name and rest.startswith(f"{name} ")) or rest.startswith(f"{vid} "):
            found.append((index, f"{text} ({entry.get('detail')})"))
    return found


def _judge(
    still: int,
    orders_ok: bool,
    named: list[str],
    entry: dict[str, Any],
) -> tuple[str, str]:
    """healthy, watch or stuck, and why in the words of whatever decided it."""
    faults: list[str] = []
    if not orders_ok:
        faults.append("its orders are not two station orders to two different stations")
    faults.extend(named)
    if still >= STUCK_DAYS:
        faults.append(f"it has not moved for {still} days")

    if not faults:
        return "healthy", "moving, with the two station orders a route needs"

    reason = "; ".join(faults)
    if entry.get(air.REPOINT_STAGED_DAY) is not None:
        reason = (
            f"{reason}; a repoint was staged on day {entry[air.REPOINT_STAGED_DAY]} and this "
            "aircraft has not moved since, so it was never committed or it did not take"
        )
    elif entry.get(air.REPOINTED_DAY) is not None:
        reason = f"{reason}; last repointed on day {entry[air.REPOINTED_DAY]}"

    if still >= STUCK_DAYS or not orders_ok:
        return "stuck", reason
    return "watch", reason

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

**Nothing is a fault before day 75.** cargo_delivered_total was exactly 0 until day 73 of the
best measured run, because aircraft take that long to complete paying trips on a long leg, and
the far end of a 289 tile trunk did not see its first aircraft until day 43. Before day 75 the
harshest verdict available is "watch", or a network condemns a fleet that is simply still
ramping.

The vehicle ids it returns come from the game, which is what makes them safe to hand back as
arguments to plan_repoint and plan_retire. Nothing here asks a model for an id.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns_air import air_keys as air
    from agents.neuro_san.coded_tools.ns_air.choose_aircraft import AIRCRAFT
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where ns and ns_air are siblings and the
    # package above them is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a class reference resolving from anywhere
    # on PYTHONPATH.
    from ns.gateway import NttdGateway

    from ns_air import air_keys as air
    from ns_air.choose_aircraft import AIRCRAFT

# Before this day nothing is called broken. See the module docstring: 0 cargo delivered until
# day 73 in the run that scored best.
RAMP_DAYS = 75

# Days in one place that make an aircraft stuck rather than busy. A passenger aircraft on a
# long leg is back at a gate well inside a month, so a month of not moving is not loading.
STUCK_DAYS = 30

# Situation problems about a vehicle are phrased "vehicle <name or id> ...".
_VEHICLE_PREFIX = "vehicle "


class AirHealthCheck(CodedTool):
    """Every aircraft, its verdict and the reason for it. Free: it costs no game day."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
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
            "judging": day >= RAMP_DAYS,
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
                f"plan_dispatch stages. Before day {RAMP_DAYS} nothing is called stuck, because "
                "cargo delivered was 0 until day 73 of a working run: watch means watch, not act."
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
    entry["name"] = vehicle.get("name") or f"aircraft {vid}"
    entry["seen_day"] = day

    still = day - int(entry.get("since_day", day))
    orders_ok = _orders_look_right(vehicle)
    verdict, why = _judge(day, still, orders_ok, named, entry)
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
        "repoints": int(entry.get("repoints", 0)),
    }


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
    day: int,
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
    if entry.get("repointed_day") is not None:
        reason = f"{reason}; last repointed on day {entry['repointed_day']}"

    if day < RAMP_DAYS:
        return "watch", (
            f"{reason}. Day {day}, so this is not a fault yet: cargo delivered was exactly 0 "
            f"until day 73 of the best measured run, and nothing is condemned before day "
            f"{RAMP_DAYS}"
        )
    if still >= STUCK_DAYS or not orders_ok:
        return "stuck", reason
    return "watch", reason

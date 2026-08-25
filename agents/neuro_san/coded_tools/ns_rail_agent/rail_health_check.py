"""A verdict per train, and the only place rail fleet care judges one.

Rail has a failure air does not: a train can be **lost**. The engine reports it directly, and
its own documentation for trace_route names it as the authority: a track walk says the rails
join, but only `lost` on get_vehicle_info says whether this train, on its approach axis, with
its length, can actually run the line. So that field is read here and believed over anything
inferred from geometry.

**Which is why this asks about each train twice.** `get_vehicles` lists the fleet cheaply but
carries no `lost` and no `cargo`: those two live only on `get_vehicle_info`, one train at a
time. Read off the list instead they are simply absent, and absent is not false. The lost
verdict then never fires at all, on the mode whose signature failure is a train that cannot
run its own line. Both queries are free, so the fleet is walked and each entry filled in.

Three rules carried over from air, each paid for there:

**at_station and in_depot are NORMAL.** `idle_reason` reads "at_station" for a train loading at
a platform. Treating any non-empty value as a fault made a healthy fleet read as a wall of
problems and would have sold working vehicles. This never reads it; the engine's own problems
list is the source.

**Judge on ELAPSED TIME.** One observation cannot say how long something has been true. A train
stopped in a platform looks exactly like one that arrived a second ago, so each look records
where a vehicle is and the day it got there.

**A staged repair is not a repair.** plan_repoint records an intent; only this tool can turn
that into a repoint that happened, because only this tool sees the train move.

The one rail-specific verdict is `carries_nothing`: a locomotive whose wagons never attached.
It is not stuck, it is not lost, it moves, it has orders, and it earns zero forever.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway, QueryRefused
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_keys as rail
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_rules as rules
except ImportError:
    from ns import session
    from ns.gateway import NttdGateway, QueryRefused

    from ns_rail_agent import rail_keys as rail
    from ns_rail_agent import rail_rules as rules

TRAIN = "train"

# Days on one tile that make a train stuck rather than busy. A CHOSEN threshold: a freight
# train on any leg is back at a platform inside a month, so a month without moving is not
# loading. Generous on purpose, because calling a working train stuck costs more than noticing
# a broken one a fortnight late.
STUCK_DAYS = 30

_VEHICLE_PREFIX = "vehicle "


class RailHealthCheck(CodedTool):
    """Every train, its verdict and the reason. Free: it costs no game day."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._look, args, sly_data)

    async def _look(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        world = await gateway.observe()
        situation = await gateway.situation()
        listed: list[dict[str, Any]] = await gateway.query(
            "get_vehicles", {"vehicle_type": TRAIN},
        ) or []
        fleet = [await _filled_in(gateway, entry) for entry in listed]

        record = sly_data.setdefault(rail.HEALTH, {})
        seen = record.setdefault("vehicles", {})
        day = _run_day(world.get("game") or {}, record)
        record["day"] = day

        problems = situation.get("problems") or []
        retiring = sly_data.get(rail.RETIRING) or {}
        claimed: set[int] = set()
        reports: list[dict[str, Any]] = []

        for vehicle in fleet:
            named = _problems_naming(problems, vehicle)
            claimed.update(index for index, _ in named)
            report = _look_at(vehicle, [text for _, text in named], seen, day)
            report["retiring"] = str(vehicle.get("id")) in retiring
            reports.append(report)

        for vid in set(seen) - {str(v.get("id")) for v in fleet}:
            del seen[vid]

        others = [
            f"{entry.get('problem')}: {entry.get('detail')}"
            for index, entry in enumerate(problems) if index not in claimed
        ]

        if not fleet:
            return {
                "day": day, "trains": [], "other_problems": others,
                "note": "no train owned yet, so there is nothing to judge",
            }

        return {
            "day": day,
            "trains": reports,
            "lost": [r["vehicle_id"] for r in reports if r["verdict"] == "lost"],
            "carries_nothing": [
                r["vehicle_id"] for r in reports if r["verdict"] == "carries_nothing"
            ],
            "stuck": [
                r["vehicle_id"] for r in reports
                if r["verdict"] == "stuck" and not r["retiring"]
            ],
            "being_retired": [r["vehicle_id"] for r in reports if r["retiring"]],
            "other_problems": others,
            "next": (
                "A train the game calls LOST has a route it cannot run, whatever the track "
                "looks like; plan_repoint gives it fresh orders. One that carries_nothing is a "
                "locomotive whose wagons never attached: no order fixes that, it has to be "
                "sold and rebuilt. Stuck means it has not moved in "
                f"{STUCK_DAYS} days. Anything else is watch, and watch means watch."
            ),
        }


async def _filled_in(gateway: NttdGateway, listed: dict[str, Any]) -> dict[str, Any]:
    """One train's list entry with the fields only get_vehicle_info carries.

    Merged the way round that keeps both: the list contributes `order_count` and
    `running_cost`, the detail contributes `lost`, `cargo` and `idle_reason`, and the detail
    wins where they overlap because it was read a moment later.

    A refused detail query leaves the list entry as it stands. That train is then judged
    without `lost` and without `cargo`, which the judgement handles by not treating an absent
    field as a fault: one unreadable train must not become one condemned train.
    """
    try:
        detail = await gateway.query(
            "get_vehicle_info", {"vehicle_id": int(listed.get("id") or 0)},
        )
    except QueryRefused:
        return listed
    if not isinstance(detail, dict) or not detail:
        return listed
    return {**listed, **detail}


def _run_day(game: dict[str, Any], record: dict[str, Any]) -> int:
    total = int(game.get("game_days_total") or 0)
    if total:
        return max(0, total - int(game.get("game_days_remaining") or 0))
    date = int(game.get("game_date") or 0)
    first = record.get("day_zero_date")
    if first is None:
        record["day_zero_date"] = first = date
    return max(0, date - int(first))


def _look_at(
    vehicle: dict[str, Any], named: list[str], seen: dict[str, Any], day: int
) -> dict[str, Any]:
    vid = str(vehicle.get("id"))
    entry = seen.setdefault(vid, {})
    where = _where(vehicle)
    if entry.get("where") != where:
        entry["where"] = where
        entry["since_day"] = day
        _promote_staged_repoint(entry, day)
    entry["name"] = vehicle.get("name") or f"train {vid}"
    entry["seen_day"] = day
    entry[rail.IN_SERVICE_DAYS] = int(vehicle.get("age") or 0)

    still = day - int(entry.get("since_day", day))
    verdict, why = _judge(vehicle, still, named, entry)
    entry["verdict"] = verdict
    entry["why"] = why
    entry["needs_repoint"] = verdict in ("lost", "stuck")

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
        "repoints": int(entry.get(rail.REPOINTS, 0)),
        "repoint_staged_on_day": entry.get(rail.REPOINT_STAGED_DAY),
    }


def _judge(
    vehicle: dict[str, Any], still: int, named: list[str], entry: dict[str, Any]
) -> tuple[str, str]:
    """lost, carries_nothing, stuck, watch or healthy, worst first.

    Order matters. A lost train that also has not moved is lost, not stuck: the fix differs,
    and reporting the milder fault sends a repair at the wrong problem.
    """
    if vehicle.get("lost"):
        return "lost", (
            "the game reports this train as lost, which means its route is one it cannot "
            "actually run. Track that traces as joined can still be unrunnable: the approach "
            "axis, a dead end needing a reverse, or a train longer than its platform"
        )

    if not rules.vehicle_carries_cargo(vehicle):
        return "carries_nothing", (
            "this is a locomotive with no wagon carrying anything. It runs its route, reports "
            "a position and a profit line, and earns zero. No order fixes it; it has to be "
            "sold and rebuilt with wagons coupled"
        )

    faults: list[str] = []
    if not _orders_look_right(vehicle):
        faults.append("its orders are not two station orders to two different stations")
    faults.extend(named)
    if still >= STUCK_DAYS:
        faults.append(f"it has not moved for {still} days")

    if not faults:
        return "healthy", "moving, loaded, with the two station orders a route needs"

    reason = "; ".join(faults)
    if entry.get(rail.REPOINT_STAGED_DAY) is not None:
        reason = (
            f"{reason}; a repoint was staged on day {entry[rail.REPOINT_STAGED_DAY]} and this "
            "train has not moved since, so it was never committed or it did not take"
        )
    elif entry.get(rail.REPOINTED_DAY) is not None:
        reason = f"{reason}; last repointed on day {entry[rail.REPOINTED_DAY]}"

    if still >= STUCK_DAYS or not _orders_look_right(vehicle):
        return "stuck", reason
    return "watch", reason


def _promote_staged_repoint(entry: dict[str, Any], day: int) -> None:
    """A staged repoint becomes a real one only once the train has actually moved."""
    if entry.get(rail.REPOINT_STAGED_DAY) is None:
        return
    del entry[rail.REPOINT_STAGED_DAY]
    entry[rail.REPOINTED_DAY] = day
    entry[rail.REPOINTS] = int(entry.get(rail.REPOINTS, 0)) + 1


def _where(vehicle: dict[str, Any]) -> str:
    place = f"{vehicle.get('x')},{vehicle.get('y')}"
    return f"depot at {place}" if vehicle.get("in_depot") else place


def _orders_look_right(vehicle: dict[str, Any]) -> bool:
    """Two station orders to two different stations, which is what a route is.

    Not a count. Orders appended rather than cleared leave a train zig-zagging between two
    unrelated pairs with an order_count that looks busy.
    """
    orders = vehicle.get("orders") or []
    if len(orders) != 2 or not all(order.get("is_goto_station") for order in orders):
        return False
    return orders[0].get("destination") != orders[1].get("destination")


def _problems_naming(
    problems: list[dict[str, Any]], vehicle: dict[str, Any]
) -> list[tuple[int, str]]:
    """The engine's own problems about this train, with where each sat in the list."""
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

"""Give a train fresh orders: clear what it has, add the two it needs, start it.

**Clear before adding.** `add_order` APPENDS. A train that went lost and is given two more
orders ends with four, zig-zagging between two unrelated pairs, and reports an order_count
that looks busier than a healthy train's. So every existing order is removed first, by index,
in DESCENDING order, because removing index 0 renumbers everything above it.

**A staged repoint is not a repoint.** This records only an intent. rail_health_check turns it
into a repoint that happened, and only once it has seen the train move, because that is the
only evidence the orders took. A marker written here would make a refused or uncommitted batch
read as a repair, which is how a stuck train stops being flagged and a retirement then sells it.

**Repointing does not fix a locomotive with no wagons.** That train's orders are fine. It has
to be sold and rebuilt, which is plan_retire and then plan_buy_train.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import envelope, session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway, QueryRefused
    from agents.neuro_san.coded_tools.ns.plan import Plan
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_keys as rail
    from agents.neuro_san.coded_tools.ns_rail_agent.plan_dispatch import NO_FLAGS
except ImportError:
    from ns import constants as key
    from ns import envelope, session
    from ns.gateway import NttdGateway, QueryRefused
    from ns.plan import Plan

    from ns_rail_agent import rail_keys as rail
    from ns_rail_agent.plan_dispatch import NO_FLAGS

# Days to let a committed repoint prove itself before anything harsher is considered. A train
# given fresh orders has to reach a station to show they took, and on a long line that is not
# immediate.
REPOINT_GRACE_DAYS = 45


class PlanRepoint(CodedTool):
    """Stages a clean set of orders for one train. Changes nothing until committed."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._stage, args, sly_data)

    async def _stage(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        vehicle_id = args.get("vehicle_id")
        if vehicle_id is None:
            return (
                "Error: no vehicle_id was given. rail_health_check lists the ids worth "
                "repointing under 'lost' and 'stuck'; use one of those."
            )

        record = sly_data.get(rail.HEALTH) or {}
        seen = record.get("vehicles") or {}
        entry = seen.get(str(vehicle_id), {})
        if entry.get("verdict") == "carries_nothing":
            return (
                f"Error: train {vehicle_id} is a locomotive with no wagons carrying anything. "
                "Its orders are not the problem and fresh ones will not help. Sell it with "
                "plan_retire and build another with plan_buy_train."
            )

        pair_id = str(args.get("pair_id") or "").strip()
        stations = _stations_for(sly_data, pair_id)
        if len(stations) != 2:
            return (
                f"Error: {pair_id or '(no pair_id)'} does not name a confirmed route with two "
                "stations. Give the pair_id of the corridor this train should work, as "
                "confirm_rail_route reports it."
            )

        try:
            current = await gateway.query("get_orders", {"vehicle_id": int(vehicle_id)}) or {}
        except QueryRefused as refused:
            return f"Error: the orders could not be read ({refused}). Nothing was staged."

        existing = current.get("orders") if isinstance(current, dict) else current
        count = len(existing or [])

        actions = [
            # Descending, because removing index 0 renumbers every order above it and a batch
            # is submitted in order.
            envelope.action("remove_order", vehicle_id=int(vehicle_id), order_index=index)
            for index in range(count - 1, -1, -1)
        ]
        actions += [
            envelope.action(
                "add_order", vehicle_id=int(vehicle_id),
                station_id=int(stations[0]), order_flags=NO_FLAGS,
            ),
            envelope.action(
                "add_order", vehicle_id=int(vehicle_id),
                station_id=int(stations[1]), order_flags=NO_FLAGS,
            ),
        ]

        problems = envelope.check(actions)
        if problems:
            return f"Error: the repoint cannot be staged as written: {'; '.join(problems)}"

        # An INTENT, not an accomplishment. Only rail_health_check may promote it, and only
        # once it has seen the train move.
        entry = seen.setdefault(str(vehicle_id), {})
        entry[rail.REPOINT_STAGED_DAY] = int(record.get("day") or 0)
        record.setdefault("vehicles", seen)
        sly_data[rail.HEALTH] = record

        plan = Plan(sly_data)
        waiting = plan.add(*actions)
        return {
            "staged": len(actions),
            "waiting_in_plan": waiting,
            "train": int(vehicle_id),
            "orders_removed": count,
            "now_runs_between": stations,
            "plan": plan.describe(),
            "next": (
                "commit_plan submits them in one day. The orders carry no flags, for the same "
                "reason a new route's do not. This counts as repaired only once the train has "
                f"moved; if it has not after {REPOINT_GRACE_DAYS} days it is a candidate for "
                "plan_retire."
            ),
        }


def _stations_for(sly_data: dict[str, Any], pair_id: str) -> list[int]:
    for route in sly_data.get(key.ROUTES) or []:
        if _same(str(route.get("pair_id") or ""), pair_id):
            return [int(s) for s in (route.get("stations") or []) if s is not None]
    return []


def _same(one: str, other: str) -> bool:
    return "".join(one.split()).lower() == "".join(other.split()).lower()

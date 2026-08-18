"""Copy an aircraft that is already flying a corridor, orders and all.

A clone takes the original's orders with it, so the two add_order calls and the whole question
of which station is which never arise. One action replaces three, and a fleet grown by cloning
cannot end up pointed at the wrong end of the wrong corridor, which is what a hand-built
dispatch does when it reads the last route in a list instead of the one the aircraft flies.

**The depot is passed explicitly.** clone_vehicle without one builds at the vehicle's current
tile, "which only works while it is parked in a depot" in the engine's own words, and the
aircraft worth cloning is by definition the one in the air. So the hangar goes in, read back
from the game exactly as a purchase reads it.

**A clone arrives STOPPED.** That was measured: cloned aircraft sat in their hangar reporting a
full order list while the corridor they were bought for kept piling up. Its start cannot be
staged beside it, because a clone's vehicle_id is only handed back by the step that builds it,
and naming an id that does not exist yet is the failure behind 35 refused purchases. So this
tool stages a start for every aircraft ALREADY parked with orders, which is exactly the clone
committed last time, and says plainly that the ones staged now need the same treatment after
the commit. plan_dispatch does the same sweep, so either tool finishes the job.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns import counting, session
    from agents.neuro_san.coded_tools.ns.envelope import action, check
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.observation import (
        matches_route,
        route_station_ids,
        station_by_tile,
        stations_called_at,
    )
    from agents.neuro_san.coded_tools.ns.plan import Plan
    from agents.neuro_san.coded_tools.ns_air.choose_aircraft import (
        AIRCRAFT,
        known_routes,
        name_of,
        route_for,
    )
    from agents.neuro_san.coded_tools.ns_air.plan_buy_aircraft import (
        MOST_AT_ONCE,
        hangar_for,
        refuse_if_late,
    )
    from agents.neuro_san.coded_tools.ns_air.plan_dispatch import parked_with_orders
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where ns and ns_air are siblings and the
    # package above them is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a class reference resolving from anywhere
    # on PYTHONPATH.
    from ns import counting, session
    from ns.envelope import action, check
    from ns.gateway import NttdGateway
    from ns.observation import (
        matches_route,
        route_station_ids,
        station_by_tile,
        stations_called_at,
    )
    from ns.plan import Plan

    from ns_air.choose_aircraft import AIRCRAFT, known_routes, name_of, route_for
    from ns_air.plan_buy_aircraft import MOST_AT_ONCE, hangar_for, refuse_if_late
    from ns_air.plan_dispatch import parked_with_orders

# Shared rather than copied: two aircraft with one order list change together afterwards, so a
# corridor repointed once is repointed for every aircraft on it.
SHARE_ORDERS = True


class PlanCloneAircraft(CodedTool):
    """More of what already works on a corridor, with its orders copied for free."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._stage_clone, args, sly_data)

    async def _stage_clone(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        route = route_for(sly_data, town=args.get("town"))
        if route is None:
            return (
                "Error: no corridor to clone on. Corridors on record: "
                f"{known_routes(sly_data) or 'none yet'}."
            )

        # A clone costs what an aircraft costs, so it is bound by the same horizon.
        late = refuse_if_late(await gateway.observe())
        if late:
            return late

        fleet = await gateway.query("get_vehicles", {"vehicle_type": AIRCRAFT}) or []
        flying = await _flying_this_route(gateway, route, fleet)
        if not flying:
            return (
                f"Error: no aircraft is flying {name_of(route)} yet, and cloning copies orders, "
                "so there has to be a set of orders to copy. Use plan_buy_aircraft and "
                "plan_dispatch for the first one; clone after it is running."
            )

        # The busiest earner is the one worth doubling. Measured: a 289 tile trunk took 5 of 9
        # aircraft and returned 71 per cent of the profit, so more of the best beats one each.
        original = max(flying, key=lambda plane: int(plane.get("profit_this_year") or 0))

        hangar = await hangar_for(gateway, route)
        if hangar is None:
            return (
                f"Error: no hangar is recorded for {name_of(route)} and the game reports none "
                "for its stations. Run confirm_airports first: without a depot the clone is "
                "built at the original's current tile, which fails while it is in the air."
            )

        wanted, note_on_count = counting.counted(args.get("count"), 1, most=MOST_AT_ONCE)
        stranded = parked_with_orders(fleet)
        batch = [action("start_vehicle", vehicle_id=int(plane["id"])) for plane in stranded]
        batch += [
            action(
                "clone_vehicle",
                vehicle_id=int(original["id"]),
                share_orders=SHARE_ORDERS,
                **hangar,
            )
            for _ in range(wanted)
        ]

        problems = check(batch)
        if problems:
            return f"Error: the clone is malformed and was not staged. {'; '.join(problems)}"

        plan = Plan(sly_data)
        plan.add(*batch)
        return {
            "staged": len(batch),
            "corridor": name_of(route),
            "cloning": original.get("name") or original["id"],
            "copies": wanted,
            "profit_this_year_of_original": original.get("profit_this_year"),
            "started_stranded": [plane.get("name") or plane["id"] for plane in stranded],
            "hangar": hangar,
            "already_refused": plan.already_refused(),
            "plan": plan.describe(),
            "next": (
                "commit_plan, and then plan_dispatch on the turn after it. A clone arrives "
                "STOPPED and its vehicle id only exists once the clone has been committed, so "
                "its start cannot be in this batch."
            ),
        } | counting.said(note_on_count)


async def _flying_this_route(
    gateway: NttdGateway,
    route: dict[str, Any],
    fleet: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The aircraft whose orders actually point at this corridor's airports.

    Matched on TILES, not on station ids. GSOrder.GetOrderDestination answers the destination
    tile even for an order that was added by station_id, so comparing an order's destination
    against a station id matches nothing on a real map and, worse, could match a station whose
    id happens to equal some tile index. The tiles come from the situation report, which
    computes them against the map width the engine actually has.

    Falls back to whatever the route record lists as its own vehicles, and to nothing at all
    otherwise. Refusing beats cloning an aircraft off another corridor: a clone shares orders,
    so a wrong original produces an aircraft flying the wrong pair of towns.
    """
    # The join belongs to ns/observation.py and is used here rather than repeated. Matching on
    # ANY shared stop is what this used to do and it is wrong on a hub: grand-tundra ran four
    # lines with three of them calling at station 1, so any-overlap put all nine aircraft on the
    # first recorded line. A route is the PAIR of stops it is made of, and matches_route requires
    # both. Cloning off the wrong corridor is worse than refusing, because a clone copies orders.
    world = await gateway.observe()
    by_tile = station_by_tile(world)
    wanted = route_station_ids(route)
    matched = [
        plane for plane in fleet
        if matches_route(stations_called_at(plane, by_tile), wanted)
    ]
    if matched:
        return matched

    recorded = {str(vehicle) for vehicle in route.get("vehicles") or []}
    return [plane for plane in fleet if str(plane.get("id")) in recorded]

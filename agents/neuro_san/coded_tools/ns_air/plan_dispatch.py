"""Give every aircraft that has no orders its two stops and a start, in ONE game day.

**Why orders and starts batch and buying cannot batch with them.** add_order and start_vehicle
both take a vehicle_id, and a vehicle_id does not exist until the purchase that created it has
been committed: buy_vehicle hands it back in the step result. A batch that buys and then orders
in the same step names ids that do not exist yet, so it is refused, or worse it is filled in
with invented ones, which is the failure that produced 35 refused purchases in a row. Aircraft
already bought are a different case entirely: their ids are real, so the whole fleet can be
ordered and started in one batch, and one game day dispatches nine aircraft as cheaply as one.

**order_flags 0.** Flag 64 is full load, and it parked a train at a slow source for months
waiting for a load that never arrived. Orders take what is there.

**One start, at the end of each triplet.** start_vehicle used to TOGGLE, so a vehicle started
by a dispatch and then started again explicitly ended up parked while both calls answered
success. Three runs scored zero cargo that way.

This tool does not step. It stages, and commit_plan spends the day.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns.envelope import action, check
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.plan import Plan
    from agents.neuro_san.coded_tools.ns_air.choose_aircraft import (
        AIRCRAFT,
        known_routes,
        name_of,
        route_for,
    )
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where ns and ns_air are siblings and the
    # package above them is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a class reference resolving from anywhere
    # on PYTHONPATH.
    from ns.envelope import action, check
    from ns.gateway import NttdGateway
    from ns.plan import Plan

    from ns_air.choose_aircraft import AIRCRAFT, known_routes, name_of, route_for

# Orders take what is waiting. 64 is OF_FULL_LOAD and it is how a vehicle stops being a
# vehicle.
TAKE_WHAT_IS_THERE = 0

# A route is two stations. More orders than that is a tour, and a tour on two airports is
# three visits to the same pair.
STOPS_PER_ROUTE = 2


class PlanDispatch(CodedTool):
    """Orders and a start for every aircraft that is not yet flying, staged as one day."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
        fleet = await gateway.query("get_vehicles", {"vehicle_type": AIRCRAFT}) or []
        waiting = [plane for plane in fleet if not int(plane.get("order_count") or 0)]
        parked = parked_with_orders(fleet)
        if not waiting and not parked:
            return {
                "staged": 0,
                "why": (
                    "every aircraft already has orders and is out of its hangar. Nothing to "
                    "dispatch. If one is not earning, that is a health question, not this."
                ),
            }

        stations: list[Any] = []
        route = None
        if waiting:
            route = route_for(sly_data, town=args.get("town"))
            if route is None:
                return (
                    f"Error: {len(waiting)} aircraft with no orders and no corridor to point "
                    f"them at. Corridors on record: {known_routes(sly_data) or 'none yet'}."
                )
            stations = list(route.get("stations") or [])[:STOPS_PER_ROUTE]
            if len(stations) < STOPS_PER_ROUTE:
                return (
                    f"Error: {name_of(route)} records {len(stations)} station and an aircraft "
                    "needs two to fly between. Build or confirm the second airport first."
                )

        batch: list[dict[str, Any]] = []
        for plane in waiting:
            vehicle = int(plane["id"])
            for station in stations:
                batch.append(action(
                    "add_order",
                    vehicle_id=vehicle,
                    station_id=int(station),
                    order_flags=TAKE_WHAT_IS_THERE,
                ))
            batch.append(action("start_vehicle", vehicle_id=vehicle))
        for plane in parked:
            # Orders already, so all it needs is the start it never got. This is what a clone
            # looks like the turn after it was committed, and what a purchase looks like if a
            # commit went in without its start.
            batch.append(action("start_vehicle", vehicle_id=int(plane["id"])))

        problems = check(batch)
        if problems:
            return f"Error: the dispatch is malformed and was not staged. {'; '.join(problems)}"

        plan = Plan(sly_data)
        plan.add(*batch)
        return {
            "staged": len(batch),
            "corridor": name_of(route) if route else None,
            "ordered_and_started": [plane.get("name") or plane["id"] for plane in waiting],
            "started_only": [plane.get("name") or plane["id"] for plane in parked],
            "half_ordered": [
                plane.get("name") or plane["id"] for plane in fleet
                if 0 < int(plane.get("order_count") or 0) < STOPS_PER_ROUTE
            ],
            "already_refused": plan.already_refused(),
            "plan": plan.describe(),
            "next": (
                "commit_plan. One game day dispatches the whole fleet, however many aircraft "
                "are in it."
            ),
        }


def parked_with_orders(fleet: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aircraft sitting in a hangar that already know where to go.

    in_depot is IsStoppedInDepot, which means PARKED rather than passing through for service,
    so this is precisely the set that will never leave without a start. It is the state a
    cloned aircraft arrives in, and the state a bought one stays in if its start was left out.

    Deliberately not derived from `running` or from idle_reason. `running` is state ==
    VS_RUNNING, which is false for an aeroplane loading at a gate, and idle_reason answers
    "at_station" for the same aircraft and "in_depot" for one in its hangar; both are normal.
    Reading either as a fault reported a healthy fleet as a wall of faults.
    """
    return [
        plane for plane in fleet
        if plane.get("in_depot") and int(plane.get("order_count") or 0) >= STOPS_PER_ROUTE
    ]

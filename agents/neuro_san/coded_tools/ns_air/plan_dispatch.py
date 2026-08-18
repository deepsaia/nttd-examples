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

**Each aircraft goes to ITS OWN corridor's airports.** An earlier version resolved one route and
gave that route's two stations to every orderless aircraft in the fleet, so on a company with two
corridors the second corridor's aircraft were dispatched onto the first one's airports and had to
be repointed afterwards. An orderless aircraft is parked in the hangar it was bought at, and while
in_depot its x,y IS the hangar tile, so the hangar says which corridor it belongs to. One that
cannot be attributed is reported and left alone: dispatching to the wrong corridor is worse than
not dispatching at all.

This tool does not step. It stages, and commit_plan spends the day.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns import session
    from agents.neuro_san.coded_tools.ns.envelope import action, check
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.observation import known_routes as recorded_routes
    from agents.neuro_san.coded_tools.ns.plan import Plan
    from agents.neuro_san.coded_tools.ns_air.choose_aircraft import (
        AIRCRAFT,
        known_routes,
        name_of,
        route_for,
    )
    from agents.neuro_san.coded_tools.ns_air.plan_buy_aircraft import recorded_hangars
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where ns and ns_air are siblings and the
    # package above them is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a class reference resolving from anywhere
    # on PYTHONPATH.
    from ns import session
    from ns.envelope import action, check
    from ns.gateway import NttdGateway
    from ns.observation import known_routes as recorded_routes
    from ns.plan import Plan

    from ns_air.choose_aircraft import AIRCRAFT, known_routes, name_of, route_for
    from ns_air.plan_buy_aircraft import recorded_hangars

# Orders take what is waiting. 64 is OF_FULL_LOAD and it is how a vehicle stops being a
# vehicle.
TAKE_WHAT_IS_THERE = 0

# A route is two stations. More orders than that is a tour, and a tour on two airports is
# three visits to the same pair.
STOPS_PER_ROUTE = 2


class PlanDispatch(CodedTool):
    """Orders and a start for every aircraft that is not yet flying, staged as one day."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._stage_dispatch, args, sly_data)

    async def _stage_dispatch(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
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

        town = str(args.get("town") or "").strip()
        routes = _air_routes(sly_data)
        if town:
            named = route_for(sly_data, town=town)
            if named is None:
                return (
                    f"Error: no recorded corridor serves {town}. Corridors on record: "
                    f"{known_routes(sly_data) or 'none yet'}."
                )
            routes = [named]
        if waiting and not routes:
            return (
                f"Error: {len(waiting)} aircraft with no orders and no corridor to point "
                f"them at. Corridors on record: {known_routes(sly_data) or 'none yet'}."
            )

        owner: dict[tuple[int, int], dict[str, Any]] = {}
        if waiting:
            # Free, and it covers a route whose record holds no hangar because its purchase read
            # one back from the game instead of from confirm_airports.
            owner = _routes_by_hangar(routes, await gateway.query("get_hangars") or [])
        groups, unattributed = _grouped_by_hangar(waiting, owner)

        batch: list[dict[str, Any]] = []
        dispatched: list[dict[str, Any]] = []
        left_alone: list[str] = _left_alone(unattributed, routes)
        for route, planes in groups:
            stations = list(route.get("stations") or [])[:STOPS_PER_ROUTE]
            if len(stations) < STOPS_PER_ROUTE:
                left_alone.append(
                    f"{name_of(route)} records {len(stations)} station and an aircraft needs two "
                    f"to fly between, so the {len(planes)} in its hangar were left alone. Build "
                    "or confirm the second airport first."
                )
                continue
            for plane in planes:
                vehicle = int(plane["id"])
                for station in stations:
                    batch.append(action(
                        "add_order",
                        vehicle_id=vehicle,
                        station_id=int(station),
                        order_flags=TAKE_WHAT_IS_THERE,
                    ))
                batch.append(action("start_vehicle", vehicle_id=vehicle))
            dispatched.append({
                "corridor": name_of(route),
                "stations": stations,
                "aircraft": [plane.get("name") or plane["id"] for plane in planes],
            })
        for plane in parked:
            # Orders already, so all it needs is the start it never got. This is what a clone
            # looks like the turn after it was committed, and what a purchase looks like if a
            # commit went in without its start.
            batch.append(action("start_vehicle", vehicle_id=int(plane["id"])))

        if not batch:
            return {
                "staged": 0,
                "left_alone": left_alone,
                "why": (
                    "nothing could be dispatched. Every waiting aircraft is listed above with the "
                    "reason; an aircraft sent to another corridor's airports has to be repointed "
                    "afterwards, which costs more than leaving it where it is."
                ),
            }

        problems = check(batch)
        if problems:
            return f"Error: the dispatch is malformed and was not staged. {'; '.join(problems)}"

        plan = Plan(sly_data)
        plan.add(*batch)
        return {
            "staged": len(batch),
            "dispatched": dispatched,
            "left_alone": left_alone,
            "started_only": [plane.get("name") or plane["id"] for plane in parked],
            "half_ordered": [
                plane.get("name") or plane["id"] for plane in fleet
                if 0 < int(plane.get("order_count") or 0) < STOPS_PER_ROUTE
            ],
            "already_refused": plan.already_refused(),
            "plan": plan.describe(),
            "next": (
                "commit_plan. One game day dispatches the whole fleet, however many aircraft "
                "are in it and however many corridors they belong to."
            ),
        }


def _air_routes(sly_data: dict[str, Any]) -> list[dict[str, Any]]:
    """The recorded corridors an aircraft could be dispatched onto."""
    return [
        route for route in recorded_routes(sly_data)
        if (route.get("mode") or "air") == "air"
    ]


def _routes_by_hangar(
    routes: list[dict[str, Any]], hangars: list[dict[str, Any]]
) -> dict[tuple[int, int], dict[str, Any]]:
    """Hangar coordinates to the one route that hangar belongs to.

    Coordinates rather than tiles, because an aircraft reports x and y and a tile index needs the
    map width to compare against them, which is one more thing to get wrong.

    A hangar two routes both claim is left out entirely. grand-tundra ran a hub, four lines with
    three of them calling at station 1, so the hangar at that airport belongs to three corridors
    and an aircraft parked in it says nothing about which one it was bought for.
    """
    claims: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for route in routes:
        for place in _hangars_of(route, hangars):
            claims.setdefault(place, []).append(route)
    return {place: found[0] for place, found in claims.items() if len(found) == 1}


def _hangars_of(route: dict[str, Any], hangars: list[dict[str, Any]]) -> set[tuple[int, int]]:
    """Every hangar this route has, from its own record and from the game's answer.

    Both sources, because either can be the only one. confirm_airports writes the hangars it read
    back, and a route recorded without them still has them on the map under its station ids.
    """
    places: set[tuple[int, int]] = set()
    for recorded in (route.get("hangar"), route.get("depot"), *recorded_hangars(route)):
        place = _place(recorded)
        if place is not None:
            places.add(place)

    stations = {str(station) for station in route.get("stations") or []}
    for entry in hangars:
        if str(entry.get("station_id")) in stations:
            place = _place(entry)
            if place is not None:
                places.add(place)
    return places


def _place(recorded: Any) -> tuple[int, int] | None:
    """One hangar record as coordinates, whichever spelling wrote it, or None when it has none."""
    if not isinstance(recorded, dict):
        return None
    for x_field, y_field in (("hangar_x", "hangar_y"), ("x", "y")):
        if recorded.get(x_field) is not None and recorded.get(y_field) is not None:
            return int(recorded[x_field]), int(recorded[y_field])
    return None


def _grouped_by_hangar(
    waiting: list[dict[str, Any]], owner: dict[tuple[int, int], dict[str, Any]]
) -> tuple[list[tuple[dict[str, Any], list[dict[str, Any]]]], list[dict[str, Any]]]:
    """The waiting aircraft split by the corridor whose hangar each is parked in, and the rest.

    Only an aircraft the game reports as in_depot is attributed. in_depot is IsStoppedInDepot, and
    it is what makes the position a hangar tile rather than wherever the aeroplane happens to be
    flying, so an orderless aircraft in the air is deliberately left in the second list.

    Routes are grouped by identity rather than by any id, because a route record has no key that
    every builder writes and two corridors between the same towns would collide on the towns.
    """
    groups: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    unattributed: list[dict[str, Any]] = []
    for plane in waiting:
        route = None
        if plane.get("in_depot"):
            route = owner.get((int(plane.get("x") or 0), int(plane.get("y") or 0)))
        if route is None:
            unattributed.append(plane)
            continue
        for known, planes in groups:
            if known is route:
                planes.append(plane)
                break
        else:
            groups.append((route, [plane]))
    return groups, unattributed


def _left_alone(
    unattributed: list[dict[str, Any]], routes: list[dict[str, Any]]
) -> list[str]:
    """One line per aircraft nothing can be said about, with what to do instead of guessing."""
    named = [name_of(route) for route in routes]
    return [
        f"{plane.get('name') or plane['id']} has no orders and is at "
        f"x{plane.get('x')} y{plane.get('y')}, which is not a hangar belonging to exactly one "
        f"recorded corridor, so it was left alone. Corridors considered: {named or 'none'}. Call "
        "this again naming the town it should serve, or plan_repoint if it is already flying."
        for plane in unattributed
    ]


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

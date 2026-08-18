"""Which aeroplane to buy for a corridor, chosen from what the game will actually sell.

Three failures live here, and the tools that buy import this module rather than repeat them.

**The vehicle type is the literal "aircraft".** get_engines takes train, road, ship or
aircraft, and answers anything else with TRAIN engines and success true. An agent that asks
for "air" or "plane" engines confidently plans a fleet of locomotives, is refused at a hangar,
and is told nothing about why. So the literal is hard coded here and is never an argument.

**A big plane at a small field crashes**, with no warning and no refusal from the action that
sent it there. Three aircraft were lost that way before plane_type was checked against the
airport. The WORSE of the two ends decides: a big plane only when both ends are AT_LARGE,
AT_METROPOLITAN, AT_INTERNATIONAL or AT_INTERCON, and only when the game answered for both ends.
all() over a one-end answer is True, so a partial answer used to read as a safe corridor.

**No model ever names an engine.** The shortlist exists so a strategist can reason about the
trade, and the id travels onward inside the tools. A run with no tool able to produce an engine
id submitted buy_vehicle 35 times with invented ones, 30, 40, 21, 60, 90, when the aircraft
actually on sale in that era were 238 to 246. Every one was refused ERR_PRECONDITION_FAILED.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where ns and ns_air are siblings and the
    # package above them is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a class reference resolving from anywhere
    # on PYTHONPATH.
    from ns import constants as key
    from ns import session
    from ns.gateway import NttdGateway

# The one value get_engines accepts for aeroplanes. Anything else returns trains.
AIRCRAFT = "aircraft"

# GSAirport plane classes. A helicopter is 0, a small plane 1, a big plane 3.
HELICOPTER = 0
SMALL_PLANE = 1
BIG_PLANE = 3

# The airport types that take a big plane: AT_LARGE 1, AT_METROPOLITAN 3, AT_INTERNATIONAL 4,
# AT_INTERCON 7. AT_SMALL 0 and AT_COMMUTER 5 do not, and 2, 6 and 8 are heliports, which take
# no aeroplane at all.
BIG_PLANE_AIRPORTS = frozenset({1, 3, 4, 7})

# How many to show. Three is enough to see the trade between capacity and upkeep and few
# enough that the recommendation is not buried.
SHORTLIST = 3


class ChooseAircraft(CodedTool):
    """The aeroplanes a corridor can land, best first, with one recommended."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._shortlist, args, sly_data)

    async def _shortlist(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        route = route_for(sly_data, corridor_id=args.get("corridor_id"))
        if route is None:
            return (
                "Error: no route by that name. Corridors on record: "
                f"{known_routes(sly_data) or 'none yet'}. Build one first, or call this with "
                "no argument to use the newest."
            )

        airports = await airports_of(gateway, route)
        big_ok = accepts_big_planes(route, airports)
        ranked = await rank_aircraft(gateway, route, airports)
        if not ranked:
            return (
                "Error: no aeroplane on sale fits this corridor. Either the year has none yet, "
                "or an end of it is a heliport, which takes no aeroplane. Check the airport "
                f"types that were built: {[a.get('airport_type') for a in airports] or 'unknown'}."
            )

        best = ranked[0]
        return {
            "corridor": name_of(route),
            "takes_big_planes": big_ok,
            "airport_types": [a.get("airport_type") for a in airports],
            "shortlist": [_readable(engine) for engine in ranked[:SHORTLIST]],
            "recommended": best.get("name"),
            "why": (
                f"{best.get('name')} carries {best.get('capacity')} for {best.get('running_cost')} "
                f"a day, the most per unit of upkeep of the {len(ranked)} aeroplanes this "
                "corridor can land. Running cost is charged every day whether it flies or not, "
                "so the biggest airframe is not the best buy."
                + ("" if big_ok else " Only small planes are listed: the smaller of the two "
                   "airports cannot take a big one, and a big one sent there crashes.")
            ),
            "next": (
                "plan_buy_aircraft with a count. It resolves this engine again itself, so you "
                "never have to pass an engine id."
            ),
        }


def route_for(
    sly_data: dict[str, Any],
    corridor_id: Any = None,
    town: str | None = None,
) -> dict[str, Any] | None:
    """The route a fleet decision is about: named by corridor, named by a town, or the newest.

    None when a name was given and nothing matches, so the caller can say which names exist.
    Defaulting a miss to the newest route would silently buy for the wrong corridor, which is
    the same class of error as inventing an id.
    """
    routes = sly_data.get(key.ROUTES) or []
    if not routes:
        return None

    if corridor_id is not None and str(corridor_id).strip():
        wanted = str(corridor_id).strip().lower()
        for route in routes:
            if wanted in {str(route.get("corridor_id") or "").lower(),
                          str(route.get("route_id") or "").lower()}:
                return route
        return None

    if town is not None and str(town).strip():
        wanted = str(town).strip().lower()
        for route in routes:
            if any(wanted == str(name).strip().lower() for name in route.get("towns") or []):
                return route
        return None

    # The newest, because growth follows the corridor just built.
    return routes[-1]


def known_routes(sly_data: dict[str, Any]) -> list[str]:
    """The corridors on record, as a reader would name them, for a retry prompt."""
    return [name_of(route) for route in sly_data.get(key.ROUTES) or []]


def name_of(route: dict[str, Any]) -> str:
    """What to call a route in a message: its corridor id, or the towns it joins."""
    identity = route.get("corridor_id") or route.get("route_id")
    towns = " to ".join(str(name) for name in route.get("towns") or [] if name)
    if identity and towns:
        return f"{identity} ({towns})"
    return str(identity or towns or "the newest route")


async def airports_of(gateway: NttdGateway, route: dict[str, Any]) -> list[dict[str, Any]]:
    """This route's airports, as get_hangars reports them.

    One query answers both questions the fleet has: where aircraft are bought, and what class
    of aeroplane the ends will take. Both come from the game rather than from arithmetic on the
    airport tile, because the hangar offset is not derivable: measured +5 in x for metropolitan
    and large, +4 in x for commuter and +3 in y for international. Four buy_vehicle calls at an
    airport's own coordinates failed ERR_UNKNOWN with no diagnostic.
    """
    wanted = {str(station) for station in route.get("stations") or []}
    if not wanted:
        return []
    hangars = await gateway.query("get_hangars") or []
    return [entry for entry in hangars if str(entry.get("station_id")) in wanted]


def accepts_big_planes(route: dict[str, Any], airports: list[dict[str, Any]]) -> bool:
    """Whether EVERY end of this route can take a big plane, on evidence about every end.

    all() over a partial answer is True, and so is all() over an empty one, so a route whose
    second airport was missing from get_hangars was judged big-plane-safe on evidence about one
    end. That is why the count is checked first: an entry per stop, or the game's answer does not
    get to decide. Three aircraft were lost in one measured run to a big plane sent to a commuter
    field, which crashes with no warning and no refusal from the action that sent it.

    Where the evidence is short the route record answers, and where it says nothing the answer is
    SMALL. Assuming small is the safe direction: a small plane at a large field is merely less
    efficient, a big one at a small field is destroyed.
    """
    stops = len(route.get("stations") or [])
    answered = {str(entry.get("station_id")) for entry in airports}
    if stops and len(answered) >= stops:
        return all(
            int(entry.get("airport_type", -1)) in BIG_PLANE_AIRPORTS for entry in airports
        )
    return bool(route.get("takes_big_planes"))


async def rank_aircraft(
    gateway: NttdGateway,
    route: dict[str, Any],
    airports: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Aeroplanes this route can land, best first, as get_engines returned them.

    Re-queried on every call rather than cached: what is on sale changes with the year, and a
    fleet planned from last spring's list is planned from engines that may no longer be built.
    """
    engines = await gateway.query("get_engines", {"vehicle_type": AIRCRAFT}) or []
    if airports is None:
        airports = await airports_of(gateway, route)

    # Helicopters are excluded because a corridor is two airports: they are slower, carry less
    # and exist here only to serve heliports, which this package does not build.
    usable = [
        engine for engine in engines
        if not engine.get("is_wagon")
        and int(engine.get("plane_type", SMALL_PLANE)) != HELICOPTER
    ]
    if not accepts_big_planes(route, airports):
        usable = [
            engine for engine in usable
            if int(engine.get("plane_type", SMALL_PLANE)) == SMALL_PLANE
        ]
    return sorted(usable, key=_score, reverse=True)


def _score(engine: dict[str, Any]) -> float:
    """What it carries against what it costs to keep.

    Running cost is charged every day whether the aircraft flies or not, so ranking on capacity
    alone buys the largest airframe on the list and a fleet that cannot cover its own upkeep.
    """
    return float(engine.get("capacity") or 0) / max(float(engine.get("running_cost") or 1), 1.0)


def _readable(engine: dict[str, Any]) -> dict[str, Any]:
    """One engine, as the strategist should see it.

    plane_type is included because it is the field that decides whether this aircraft can land
    at the corridor's smaller end at all, and a reader who knows 1 is small and 3 is big can
    check the recommendation rather than take it on trust.
    """
    return {
        "name": engine.get("name"),
        "id": engine.get("id"),
        "capacity": engine.get("capacity"),
        "price": engine.get("price"),
        "running_cost": engine.get("running_cost"),
        "max_speed": engine.get("max_speed"),
        "plane_type": engine.get("plane_type"),
        "carried_per_running_cost": round(_score(engine), 3),
    }

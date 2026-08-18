"""Reading the world, and the two lookups every report needs to do it correctly.

The counterpart to envelope.py: that module is the one place an action is shaped, this is
the one place an observation is read. Four report tools would otherwise each carry their own
copy of the same four lines and the same two traps.

Three things live here, each measured:

**One fetch per turn.** /state/full is the whole world, and a turn that calls
read_situation, fleet_report and route_report would pull it three times. It is cached under
`constants.SNAPSHOT`, which is deliberately NOT in `constants.ALLOWED`: a whole world
snapshot has no business crossing a turn boundary in a chat payload, and a stale one would
be worse than none.

**An order's destination is a TILE, not a station id.** GSOrder.GetOrderDestination returns
`Station::xy`, so a vehicle's orders name tiles while a route record names station ids, and
comparing the two directly matches nothing. Verified against a recorded run: order
destination 47659 on a 256 wide map is 186 * 256 + 43, and station 0 "Hondinghall Airport"
sits at x=43, y=186. This is why "which route does this vehicle fly" needs a lookup at all.

**A route record is written by whichever builder built it.** Air records `stations`, the
engine's own route objects use `station_ids`. Reading only one spelling made a route with
vehicles on it report as having none, so both are accepted.
"""

from __future__ import annotations

from typing import Any

try:
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
except ImportError:
    from ns import constants as key
    from ns.gateway import NttdGateway

# The company the token owns. /state/situation defaults to company 0 and filters its
# stations and vehicles by it, so anything reading /state/full has to agree with that or the
# two reports describe different companies.
OUR_COMPANY = 0


async def world(
    gateway: NttdGateway, sly_data: dict[str, Any], *, refresh: bool = False
) -> dict[str, Any]:
    """The whole world, fetched once per turn and shared by every tool that asks.

    `refresh` is for the tool that opens a turn: the cache is only true until the clock
    moves, and a report drawn from yesterday's world is worse than one that costs a request.

    The other half of that contract is held by whatever advances a day. A step returns the
    world it produced, so commit_plan, advance_days and set_loan_to overwrite this cache with
    that snapshot rather than dropping it, which keeps a later tool in the same turn current
    without a second request.
    """
    cached = sly_data.get(key.SNAPSHOT)
    if not refresh and isinstance(cached, dict) and cached:
        return cached
    fresh = await gateway.observe()
    sly_data[key.SNAPSHOT] = fresh
    return fresh


def our_company(observation: dict[str, Any]) -> dict[str, Any]:
    """The company being played, by id rather than by position in the list.

    A session with a rival in it returns more than one company and nothing promises ours is
    first, so taking `companies[0]` reports the rival's money on a map with an AI in it.
    """
    companies = observation.get("companies") or []
    for company in companies:
        if company.get("id") == OUR_COMPANY:
            return company
    return companies[0] if companies else {}


def our_vehicles(observation: dict[str, Any]) -> list[dict[str, Any]]:
    """Only the vehicles this company owns. A rival's fleet is not this fleet."""
    return [
        vehicle for vehicle in (observation.get("vehicles") or [])
        if vehicle.get("company_id", OUR_COMPANY) == OUR_COMPANY
    ]


def our_stations(observation: dict[str, Any]) -> list[dict[str, Any]]:
    """Only the stations this company owns."""
    return [
        station for station in (observation.get("stations") or [])
        if station.get("company_id", OUR_COMPANY) == OUR_COMPANY
    ]


def station_by_tile(observation: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Tile index to station, which is what turns an order into a place.

    Empty when the map width is unknown, because a tile computed against the wrong width
    points at a real tile somewhere else and a confidently wrong route match is worse than
    an absent one.
    """
    width = int((observation.get("game") or {}).get("map_width") or 0)
    if not width:
        return {}
    return {
        int(station.get("y", 0)) * width + int(station.get("x", 0)): station
        for station in our_stations(observation)
    }


def stations_called_at(vehicle: dict[str, Any], by_tile: dict[int, dict[str, Any]]) -> set[int]:
    """The station ids a vehicle's orders actually send it to.

    Depot and waypoint orders are skipped: a hangar visit is not a stop the route is made
    of, and counting it would stop a serviced aircraft matching the route it flies.
    """
    called: set[int] = set()
    for order in vehicle.get("orders") or []:
        if not order.get("is_goto_station"):
            continue
        station = by_tile.get(int(order.get("destination") or 0))
        if station is not None:
            called.add(int(station.get("id", -1)))
    return called


def matches_route(called: set[int], station_ids: list[int]) -> int:
    """How strongly a vehicle's stops say it flies this route. 0 means it does not.

    Sharing ONE station is not a match, and this is the whole reason the function exists.
    grand-tundra ran a hub: four airports, four lines, and three of them calling at station 1.
    Matching on any overlap put all 9 of its aircraft on the first recorded line and made two
    routes report 13 vehicles between them when the company owned 9. A route is the PAIR of
    stops it is made of, so a real match calls at both.

    The count is returned rather than a boolean so a caller with several candidate routes can
    take the best one instead of the first one in the list.
    """
    wanted = set(station_ids)
    if not wanted or not called:
        return 0
    shared = len(called & wanted)
    needed = 2 if len(wanted) >= 2 else 1
    return shared if shared >= needed else 0


def known_routes(sly_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every route a builder recorded, ready or not.

    Deliberately unfiltered. An earlier version hid records whose `ready` was false, which is
    exactly the corridor that has just been built and not yet confirmed: the one the strategist
    most needs to see, and the one it would then never be told to confirm. `ready` gates
    SPENDING, not visibility, so the tools that buy check it and the reports show it.
    """
    return [record for record in (sly_data.get(key.ROUTES) or []) if isinstance(record, dict)]


def route_station_ids(record: dict[str, Any]) -> list[int]:
    """A route's station ids, whichever spelling the builder that wrote it used."""
    raw = record.get("stations")
    if raw is None:
        raw = record.get("station_ids") or []
    ids: list[int] = []
    for entry in raw:
        if isinstance(entry, dict):
            entry = entry.get("id", entry.get("station_id"))
        if isinstance(entry, int):
            ids.append(entry)
    return ids


def route_name(record: dict[str, Any], index: int) -> str:
    """What to call a route in a report.

    Towns, because that is the only handle a model can use in a later tool call without
    inventing an id. A route with no towns recorded falls back to its position.
    """
    towns = [str(town) for town in (record.get("towns") or []) if town]
    return " to ".join(towns) if towns else f"route {index}"


def money(amount: Any) -> str:
    """A figure a reader can compare at a glance. 272065 and 27206 look alike; 272,065 does not."""
    try:
        return f"{int(amount):,}"
    except (TypeError, ValueError):
        return "unknown"

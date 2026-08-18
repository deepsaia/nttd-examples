"""Each route that was built: what is waiting on it, what is serving it, what it earns.

The question this answers is the one that decides every expansion turn: add a vehicle to a
route that already works, or open a new corridor. Getting it backwards is expensive both
ways. A route with 300 passengers standing on the platform is turning away money that a
single extra vehicle would collect, and a route clearing everything it gathers pays nothing
extra for a fourth aircraft, which then burns running costs for the rest of the run.

The waiting threshold is 100, which is the engine's own: `_PILING_UP` in nttd's situation.py,
the number above which it raises "cargo is piling up" as a problem. Using the same figure
means this report and the problems list never disagree about the same station.

Vehicles are matched to a route through the station list, because a vehicle's orders name
TILES and a route record names station ids. Counting `vehicle_count` off the record instead
was how a route that had lost an aircraft still reported three.
"""

from __future__ import annotations

from typing import Any

import httpx
from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import observation as obs
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
except ImportError:
    from ns import observation as obs
    from ns.gateway import NttdGateway

# nttd's own threshold for "cargo is piling up", from situation.py. Below it, waiting cargo
# is just the gap between vehicle visits and means nothing.
WAITING_IS_PILING_UP = 100

# Below this a route is clearing what it collects and another vehicle on it earns almost
# nothing, so the money belongs in a new corridor.
WAITING_IS_CLEARED = 25


class RouteReport(CodedTool):
    """Per route: its ends, what is queued at them, what serves it and what it earns."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        try:
            gateway = NttdGateway(sly_data)
            world = await obs.world(gateway, sly_data)
        except ValueError as missing:
            return f"Error: {missing}. The runner supplies both; do not invent them."
        except httpx.HTTPError as unreachable:
            return (
                f"Error: nttd did not answer: {unreachable}. Do not retry more than once; "
                "report that the routes could not be read."
            )

        routes = obs.known_routes(sly_data)
        if not routes:
            return (
                "No routes recorded yet. Nothing has been built, or it was built by "
                "something that did not record it. Build one before buying anything."
            )

        stations = {int(s.get("id", -1)): s for s in obs.our_stations(world)}
        by_tile = obs.station_by_tile(world)
        crews = _crews(obs.our_vehicles(world), by_tile)

        lines: list[str] = []
        for index, record in enumerate(routes, 1):
            lines.extend(_route_lines(index, record, stations, crews))
        return "\n".join(lines)


def _crews(
    vehicles: list[dict[str, Any]], by_tile: dict[int, dict[str, Any]]
) -> list[tuple[set[int], dict[str, Any]]]:
    """Every vehicle paired with the station ids its orders actually send it to."""
    return [(obs.stations_called_at(vehicle, by_tile), vehicle) for vehicle in vehicles]


def _route_lines(
    index: int,
    record: dict[str, Any],
    stations: dict[int, dict[str, Any]],
    crews: list[tuple[set[int], dict[str, Any]]],
) -> list[str]:
    """One route, and the verdict that decides what to spend on it."""
    station_ids = obs.route_station_ids(record)
    serving = [
        vehicle for called, vehicle in crews if obs.matches_route(called, station_ids)
    ]
    profit = sum(
        int(v.get("profit_this_year") or 0) + int(v.get("profit_last_year") or 0)
        for v in serving
    )

    ends: list[str] = []
    waits: list[int] = []
    for station_id in station_ids:
        station = stations.get(station_id)
        if station is None:
            ends.append(f"station {station_id} is GONE from the observation")
            continue
        waiting = sum(
            int(cargo.get("waiting") or 0) for cargo in (station.get("cargo_waiting") or [])
        )
        waits.append(waiting)
        ends.append(f"{station.get('name') or station_id} (id {station_id}): {waiting} waiting")

    header = (
        f"{index}. {obs.route_name(record, index)} [{record.get('mode', 'unknown mode')}], "
        f"vehicles {len(serving)}, combined profit {obs.money(profit)}"
    )
    return [header, *(f"     {end}" for end in ends), f"     {_verdict(waits, len(serving))}"]


def _verdict(waits: list[int], serving: int) -> str:
    """What to do about this route, said as an instruction rather than a number."""
    if not serving:
        return (
            "VERDICT: nothing serves it. An unfinished route earns nothing while having "
            "already cost what it cost, so buy vehicles for this before building anything new."
        )
    if not waits:
        return "VERDICT: its stations could not be read, so there is nothing to judge."
    worst = max(waits)
    if worst >= WAITING_IS_PILING_UP:
        return (
            f"VERDICT: WANTS MORE VEHICLES. {worst} waiting at its busiest end against "
            f"{serving} serving it, so it collects faster than it clears and another vehicle "
            "here earns more than a new route would."
        )
    if sum(waits) <= WAITING_IS_CLEARED:
        return (
            "VERDICT: WANTS NONE. It clears what it collects, so another vehicle here would "
            "fly empty and pay running costs. Put the money into a new corridor."
        )
    return f"VERDICT: keeping up, {max(waits)} waiting at its busiest end. Leave it alone."

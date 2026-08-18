"""Every vehicle, worst earner first, and anything that has stopped existing.

Two decisions, both from watching runs fail:

**Worst first.** A fleet of thirty with one aircraft flying an empty leg reads, from a total,
as a fleet of thirty. Sorting the loss to the top makes the one row that needs a decision the
one row a model reads first. The tie-break is last year's profit, because at the end of a 366
day run the year has rolled and `profit_this_year` is 0 for the whole fleet: a recorded run
finished with nine aircraft all showing 0 this year and 89,110 last, and sorting on this year
alone put them in arbitrary order at exactly the moment ranking mattered.

**A vehicle that has vanished is reported.** A crash removes it from the observation and says
nothing else. The count drops by one, which is invisible against thirty, and the route it
flew quietly loses a third of its capacity. So the ids seen last time are kept and diffed.

`idle_reason` is deliberately not on this report. It answers "at_station" for an aircraft
loading at a gate and "in_depot" for one in its hangar, and both are normal; a report that
showed it turned a healthy fleet into a wall of faults. `lost`, which the game sets when a
vehicle genuinely cannot find its way, is shown. For everything else use the engine's
problems list from read_situation.
"""

from __future__ import annotations

from typing import Any

import httpx
from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import observation as obs
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
except ImportError:
    from ns import constants as key
    from ns import observation as obs
    from ns.gateway import NttdGateway


class FleetReport(CodedTool):
    """Per vehicle earnings and orders, plus whatever is no longer there."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        try:
            gateway = NttdGateway(sly_data)
            world = await obs.world(gateway, sly_data)
        except ValueError as missing:
            return f"Error: {missing}. The runner supplies both; do not invent them."
        except httpx.HTTPError as unreachable:
            return (
                f"Error: nttd did not answer: {unreachable}. Do not retry more than once; "
                "report that the fleet could not be read."
            )

        vehicles = obs.our_vehicles(world)
        by_tile = obs.station_by_tile(world)
        routes = obs.known_routes(sly_data)

        gone = _gone(vehicles, sly_data)
        if not vehicles:
            return "\n".join(["fleet: nothing bought yet.", *gone])

        # Worst first, and last year breaks the tie so a rolled-over year still ranks.
        ordered = sorted(
            vehicles,
            key=lambda v: (int(v.get("profit_this_year") or 0),
                           int(v.get("profit_last_year") or 0)),
        )
        lines = [
            f"fleet: {len(vehicles)} vehicles, worst earner first",
            "   id  type      at            this yr     last yr   age  speed  ord  route",
        ]
        lines.extend(_row(vehicle, by_tile, routes) for vehicle in ordered)
        lines.extend(gone)
        return "\n".join(lines)


def _row(
    vehicle: dict[str, Any], by_tile: dict[int, dict[str, Any]], routes: list[dict[str, Any]]
) -> str:
    """One vehicle, in the columns a decision is made from."""
    # x145 y110 rather than 145,110: the profit columns beside it are comma grouped, and
    # a position written the same way reads as one hundred and forty-five thousand.
    where = f"x{vehicle.get('x', 0)} y{vehicle.get('y', 0)}"
    flag = "  LOST, it cannot find its way" if vehicle.get("lost") else ""
    return (
        f"{int(vehicle.get('id', -1)):>5}  {str(vehicle.get('type', '?')):<9} "
        f"{where:<10} {obs.money(vehicle.get('profit_this_year')):>10}  "
        f"{obs.money(vehicle.get('profit_last_year')):>10}  "
        f"{int(vehicle.get('age') or 0):>4}  {int(vehicle.get('current_speed') or 0):>5}  "
        f"{int(vehicle.get('order_count') or 0):>3}  "
        f"{_serving(vehicle, by_tile, routes)}{flag}"
    )


def _serving(
    vehicle: dict[str, Any], by_tile: dict[int, dict[str, Any]], routes: list[dict[str, Any]]
) -> str:
    """Which recorded route this vehicle's orders actually match.

    Its orders name tiles and a route record names station ids, so the two are joined
    through the station list rather than compared directly. A vehicle whose orders match no
    recorded route is the interesting case: it is flying somewhere nothing planned.
    """
    called = obs.stations_called_at(vehicle, by_tile)
    if not called:
        return "NO STOPS, its orders send it nowhere a station of ours stands"
    scored = [
        (obs.matches_route(called, obs.route_station_ids(record)), index, record)
        for index, record in enumerate(routes, 1)
    ]
    best = max(scored, default=(0, 0, {}))
    if best[0]:
        return obs.route_name(best[2], best[1])
    names = sorted(
        str(station.get("name") or station.get("id"))
        for station in by_tile.values() if int(station.get("id", -1)) in called
    )
    return "no recorded route, it calls at " + ", ".join(names)


def _gone(vehicles: list[dict[str, Any]], sly_data: dict[str, Any]) -> list[str]:
    """Ids seen last time and not this time, which is what a crash looks like from here."""
    now = sorted(int(v.get("id", -1)) for v in vehicles)
    before = {int(vid) for vid in (sly_data.get(key.FLEET_SEEN) or [])}
    sly_data[key.FLEET_SEEN] = now
    missing = sorted(before - set(now))
    if not missing:
        return []
    return [
        f"GONE since the last fleet_report: vehicle ids {missing}. A vehicle leaves the "
        "observation when it crashes or is sold. Nothing else removes one, so if it was not "
        "sold deliberately the route it flew is short a vehicle and wants a replacement."
    ]

"""Build the infrastructure a route needs, and say whether it will actually carry anything.

This is the tool whose absence made the network look foolish: with only a way to rank sites
and a way to buy vehicles, buying was the only thing it could do, so it bought aircraft with
nowhere to land. A network can only do what its tools let it do.

One tool rather than four actions the model strings together, because the ORDER is where
runs are lost. Every mode is: put the two ends down, join them if the mode needs joining,
give the vehicles somewhere to live, and then prove a vehicle can get from one end to the
other. The proof is the part that is skipped when a human writes the sequence by hand, and
it is the part that separates a route from a shape on the map.

Air is the exception worth naming: aircraft need nothing built between their endpoints, so
two airports IS the route. That is why air outperforms every other mode here.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway
except ImportError:
    from nttd_gateway import NttdGateway


class BuildRoute(CodedTool):
    """Two ends and whatever joins them, verified before anything is bought."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
        mode = str(args.get("mode") or "air")
        first, second = args["from_site"], args["to_site"]

        if mode == "air":
            return await _air(gateway, first, second)
        if mode == "water":
            return await _water(gateway, first, second)
        return await _surface(gateway, mode, first, second)


async def _air(gateway: NttdGateway, first: dict, second: dict) -> dict:
    """Two airports. There is nothing to join, which is the whole advantage of the mode."""
    built = []
    for site in (first, second):
        reply = await gateway.act([gateway.envelope(
            "build_airport",
            x=int(site["x"]), y=int(site["y"]), airport_type=int(site["airport_type"]),
        )])
        result = reply[0] if reply else {}
        built.append({
            "town": site.get("town"),
            "status": result.get("status"),
            "error": result.get("error") or "",
        })
        if result.get("status") != "success":
            return {"built": built, "ready": False,
                    "why": f"{site.get('town')}: {result.get('error')}"}

    hangars = await gateway.query("get_hangars") or []
    stations = await gateway.query("get_stations") or []
    return {
        "built": built,
        "ready": True,
        "stations": [s["id"] for s in stations],
        "hangars": [{"x": h["hangar_x"], "y": h["hangar_y"]} for h in hangars],
        "next": "buy aircraft the airports can take, and order them between two stations",
    }


async def _water(gateway: NttdGateway, first: dict, second: dict) -> dict:
    """Two docks and a depot, and the depot is the part that goes wrong.

    A depot found near a dock is frequently in a pool cut off from it: ships built there
    circle their own puddle while the dock they were meant to serve fills up. Nothing here
    can settle that from the outside, so the answer says so, and the ship is the proof.
    """
    for site in (first, second):
        reply = await gateway.act([gateway.envelope(
            "build_dock", x=int(site["x"]), y=int(site["y"]),
        )])
        if (reply[0] if reply else {}).get("status") != "success":
            return {"ready": False, "why": f"dock at {site.get('town')} refused"}

    spots = await gateway.query("find_water_depot_spots", {
        "x": int(first["x"]), "y": int(first["y"]), "radius": 12, "max_results": 4,
    }) or []
    spots = spots.get("spots", spots) if isinstance(spots, dict) else spots
    if not spots:
        return {"ready": False, "why": "no water depot spot near the first dock"}

    depot = spots[0]
    await gateway.act([gateway.envelope(
        "build_water_depot",
        x=int(depot["x"]), y=int(depot["y"]), direction=int(depot.get("depot_direction", 0)),
    )])
    stations = await gateway.query("get_stations") or []
    return {
        "ready": True,
        "stations": [s["id"] for s in stations],
        "depot": {"x": depot["x"], "y": depot["y"]},
        "warning": (
            "a water depot can sit in a pool cut off from its own dock. Buy ONE ship, let a "
            "little time pass, and check it is moving before buying more."
        ),
    }


async def _surface(gateway: NttdGateway, mode: str, first: dict, second: dict) -> dict:
    """Road or rail: two stops, the corridor between them, a depot, and the proof.

    The proof is not optional. A stop and the road to it have both reported success while
    the stop was connected to nothing, and the bus then sat in its depot for sixty game days
    burning running costs. Rail fails the same way through a depot that joins the station's
    own stub instead of the line.
    """
    action = "build_road_stop" if mode == "road" else "build_rail_station"
    connect = "connect_road" if mode == "road" else "connect_rail"

    for site in (first, second):
        await gateway.act([gateway.envelope(
            action, x=int(site["x"]), y=int(site["y"]),
        )])

    joined = await gateway.act([gateway.envelope(
        connect,
        from_x=int(first["x"]), from_y=int(first["y"]),
        to_x=int(second["x"]), to_y=int(second["y"]),
    )])
    verdict = joined[0] if joined else {}
    if verdict.get("status") != "success":
        # The refusal names the tile, which is the only part worth having.
        return {"ready": False, "why": verdict.get("error") or "the corridor did not join"}

    traced = await gateway.query("trace_route", {
        "from_x": int(first["x"]), "from_y": int(first["y"]),
        "to_x": int(second["x"]), "to_y": int(second["y"]),
        "transport_type": mode,
    }) or {}
    reachable = int(traced.get("tiles_reachable") or 0)
    if reachable <= 1:
        return {"ready": False, "why": "the stop is connected to nothing; do not buy a vehicle"}

    stations = await gateway.query("get_stations") or []
    return {
        "ready": True,
        "stations": [s["id"] for s in stations],
        "tiles_reachable": reachable,
        "next": "build a depot against the MIDDLE of the corridor, then buy a vehicle",
    }

"""Build everything a route needs, record it, and say whether it will carry anything.

This tool exists because a network can only do what its tools let it do. Without it, buying
was the only action available and the network bought aircraft with nowhere to land.

One tool rather than a sequence the model assembles, because the ORDER is where runs are
lost, and because every id and coordinate it produces has to come from the game. A model
asked for a hangar coordinate will invent one: measured, thirty-five refused purchases at a
guessed hangar with guessed engine ids.

What it records matters as much as what it builds. The stations, the depot, whether the
field takes large aircraft and which rail was laid all go into sly_data, so buying afterwards
needs no argument from the model at all.

Per mode, the thing that goes wrong:

- **air**: two airports IS the route. Aircraft need nothing between their endpoints, which is
  why the mode outperforms the others.
- **water**: a depot found near a dock is frequently in a pool cut off from it. Ships built
  there circle their own puddle while the dock they serve fills up.
- **road and rail**: a stop and the road to it both report success while the stop is
  connected to nothing, and rail depots join the station's own stub instead of the line. So
  the corridor is traced before anything is bought, and the depot goes against the MIDDLE of
  the line rather than beside a platform.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway
except ImportError:
    from nttd_gateway import NttdGateway

# A junction that reaches only its own stub answers with a handful of tiles; a working one
# reaches most of the corridor. Measured: 5 of 71 tiles was the failure, on three towns.
REACHES_ENOUGH = 0.5


class BuildRoute(CodedTool):
    """Two ends, whatever joins them, and a depot. Verified before anything is bought."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
        mode = str(args.get("mode") or "air")
        first, second = args["from_site"], args["to_site"]

        if mode == "air":
            route = await _air(gateway, first, second)
        elif mode == "water":
            route = await _water(gateway, first, second)
        else:
            route = await _surface(gateway, mode, first, second)

        if route.get("ready"):
            route["mode"] = mode
            # Remembered as the CURRENT route, and appended to the ones already running, so
            # a second route does not erase the first and vehicles can be added to either.
            sly_data["route"] = route
            sly_data.setdefault("routes", []).append(route)
        return route


async def _stations(gateway: NttdGateway) -> list[dict[str, Any]]:
    return await gateway.query("get_stations") or []


async def _air(gateway: NttdGateway, first: dict, second: dict) -> dict:
    """Two airports, built in ONE step. A step is a game day; two builds is two days."""
    before = {s["id"] for s in await _stations(gateway)}
    results = await gateway.act([
        gateway.envelope(
            "build_airport",
            x=int(site["x"]), y=int(site["y"]), airport_type=int(site["airport_type"]),
        )
        for site in (first, second)
    ])
    refused = [r.get("error") for r in results if r.get("status") != "success"]
    fresh = [s for s in await _stations(gateway) if s["id"] not in before]
    if len(fresh) < 2:
        return {"ready": False, "why": refused[0] if refused else "the airports were refused"}

    hangars = await gateway.query("get_hangars") or []
    # The hangar belonging to one of the airports just built, not any hangar on the map.
    ours = [h for h in hangars if h.get("station_id") in {s["id"] for s in fresh}]
    if not ours:
        return {"ready": False, "why": "the airports were built but report no hangar"}

    return {
        "ready": True,
        "stations": [s["id"] for s in fresh],
        "depot": {"x": ours[0]["hangar_x"], "y": ours[0]["hangar_y"]},
        # Both ends must take the aircraft, so the smaller field decides.
        "takes_big_planes": bool(first.get("takes_big_planes"))
        and bool(second.get("takes_big_planes")),
        "towns": [first.get("town"), second.get("town")],
    }


async def _water(gateway: NttdGateway, first: dict, second: dict) -> dict:
    """Two docks and a depot, and the depot is the part that goes wrong."""
    before = {s["id"] for s in await _stations(gateway)}
    results = await gateway.act([
        gateway.envelope("build_dock", x=int(site["x"]), y=int(site["y"]))
        for site in (first, second)
    ])
    refused = [r.get("error") for r in results if r.get("status") != "success"]
    fresh = [s for s in await _stations(gateway) if s["id"] not in before]
    if len(fresh) < 2:
        return {"ready": False, "why": refused[0] if refused else "the docks were refused"}

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
    return {
        "ready": True,
        "stations": [s["id"] for s in fresh],
        "depot": {"x": depot["x"], "y": depot["y"]},
        "towns": [first.get("town"), second.get("town")],
        "warning": "a water depot can sit in a pool cut off from its own dock. Buy ONE ship, "
                   "let a little time pass, and check it moves before buying more.",
    }


async def _surface(gateway: NttdGateway, mode: str, first: dict, second: dict) -> dict:
    """Road or rail: two ends, the corridor, a depot on the LINE, and the proof."""
    build = "build_road_stop" if mode == "road" else "build_rail_station"
    connect = "connect_road" if mode == "road" else "connect_rail"

    before = {s["id"] for s in await _stations(gateway)}
    await gateway.act([
        gateway.envelope(build, x=int(site["x"]), y=int(site["y"]))
        for site in (first, second)
    ])
    fresh = [s for s in await _stations(gateway) if s["id"] not in before]
    if len(fresh) < 2:
        return {"ready": False, "why": f"the {mode} stops were refused"}

    ends = [(s["x"], s["y"]) for s in fresh]
    joined = await gateway.act([gateway.envelope(
        connect,
        from_x=ends[0][0], from_y=ends[0][1], to_x=ends[1][0], to_y=ends[1][1],
    )])
    verdict = joined[0] if joined else {}
    if verdict.get("status") != "success":
        # The refusal names the tile it failed on, which is the only part worth having.
        return {"ready": False, "why": verdict.get("error") or "the corridor did not join"}

    traced = await gateway.query("trace_route", {
        "from_x": ends[0][0], "from_y": ends[0][1],
        "to_x": ends[1][0], "to_y": ends[1][1], "transport_type": mode,
    }) or {}
    reachable = int(traced.get("tiles_reachable") or 0)
    if reachable <= 1:
        return {"ready": False,
                "why": "the stop is connected to nothing; a vehicle could not leave it"}

    depot = await _depot_on_the_line(gateway, mode, ends, reachable)
    if depot is None:
        return {"ready": False,
                "why": "no depot could be placed that reaches the line. A depot beside a "
                       "platform joins that station's own stub, and the vehicle never leaves."}

    return {
        "ready": True,
        "stations": [s["id"] for s in fresh],
        "depot": depot,
        "rail_type": 0 if mode == "rail" else None,
        "tiles_reachable": reachable,
        "towns": [first.get("town"), second.get("town")],
    }


async def _depot_on_the_line(
    gateway: NttdGateway, mode: str, ends: list[tuple[int, int]], line: int
) -> dict | None:
    """A depot against the MIDDLE of the corridor, proved to reach the far end.

    Searched from along the line rather than beside a platform. Measured at three towns: a
    depot next to a platform reached 5 to 8 tiles of a 71 tile line and four trains sat in
    them for a whole game year, while one placed mid-corridor reached 60.
    """
    finder = "find_depot_spots" if mode == "road" else "find_rail_depot_spot"
    builder = "build_road_depot" if mode == "road" else "build_rail_depot"
    middle = ((ends[0][0] + ends[1][0]) // 2, (ends[0][1] + ends[1][1]) // 2)

    for anchor in (middle, ends[0]):
        spots = await gateway.query(
            finder, {"x": anchor[0], "y": anchor[1], "radius": 14, "max_results": 4},
        ) or []
        spots = spots.get("spots", spots) if isinstance(spots, dict) else spots
        for spot in spots[:3]:
            await gateway.act([gateway.envelope(
                builder, x=int(spot["x"]), y=int(spot["y"]),
                direction=int(spot.get("depot_direction", 0)),
            )])
            if mode == "rail":
                await gateway.act([gateway.envelope(
                    "connect_depot", x=int(spot["x"]), y=int(spot["y"]),
                )])
            reach = await gateway.query("trace_route", {
                "from_x": int(spot["x"]), "from_y": int(spot["y"]),
                "to_x": ends[1][0], "to_y": ends[1][1], "transport_type": mode,
            }) or {}
            if int(reach.get("tiles_reachable") or 0) >= max(10, int(line * REACHES_ENOUGH)):
                return {"x": spot["x"], "y": spot["y"]}
    return None

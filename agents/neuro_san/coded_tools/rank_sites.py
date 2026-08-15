"""Where to build, ranked, with the traps already excluded.

Siting is where most of the difference between a good run and an empty one is decided, and
almost all of it is arithmetic rather than judgement. What is left for the model is which of
several viable corridors to take, not whether a site is viable at all.

Per mode, the rule that had to be learned by losing a run to it:

- **air**: a site must be INSIDE its own airport's catchment. A commuter field covers 4
  tiles, and one sited 16 to 28 tiles from the town centre earned nothing until it was
  moved, at which point a quarter's income went from 25 to 131,740. Both endpoints must
  also be real towns: a long leg into a 348 person village returns planes empty and costs
  the same to fly as a leg into a city.
- **water**: two docks are only a route if they share a body of water. Docks sited per town
  are frequently on unconnected lakes, and the planner is unreliable in both directions, so
  candidates are ranked but never promised.
- **road**: towns arrive with roads, so most of a corridor already exists. The scarce thing
  is the join at each end, not the road.
- **rail**: orientation before siting. A platform is chosen so its `valid_directions`
  contains the axis the corridor actually approaches on; siting the nearest spot and hoping
  is what leaves a platform meeting track side on.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway

# Aircraft that can use a field at all. Type 0 does not exist in this era.
PLANE_AIRPORTS = (1, 3, 4, 5, 7)

# A town too small to fill anything. Measured: a 348 person endpoint returned big planes
# almost empty on a leg that cost the same as one into a city.
TOO_SMALL_TO_SERVE = 400


class RankSites(CodedTool):
    """Viable places to build for one mode, best first."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
        mode = str(args.get("mode") or "air")
        limit = int(args.get("limit") or 6)

        towns = sorted(
            await gateway.query("get_towns") or [],
            key=lambda t: -(t.get("population") or 0),
        )
        towns = [t for t in towns if (t.get("population") or 0) >= TOO_SMALL_TO_SERVE]

        if mode == "air":
            return await _air_sites(gateway, towns, limit)
        if mode == "water":
            return await _water_sites(gateway, towns, limit)
        return [
            {"town_id": t["id"], "name": t["name"], "population": t["population"],
             "x": t["x"], "y": t["y"]}
            for t in towns[:limit]
        ]


async def _air_sites(gateway: NttdGateway, towns: list[dict], limit: int) -> list[dict]:
    """The smallest airport that actually covers each town, largest towns first.

    Type matters twice. It decides whether the town is inside the catchment at all, and it
    decides which aircraft may land: large planes crash at small fields, so a network of
    commuter airports has to be flown with small planes and a network of large ones can
    carry four times the load on the same leg.
    """
    kinds = [k for k in (await gateway.query("get_airport_types") or [])
             if k.get("id") in PLANE_AIRPORTS]
    kinds.sort(key=lambda k: (k.get("width", 9) * k.get("height", 9)))

    found: list[dict] = []
    for town in towns:
        for kind in kinds:
            spot = await _covered_spot(gateway, town, kind)
            if spot:
                found.append(spot)
                break
        if len(found) >= limit:
            break
    return found


async def _covered_spot(gateway: NttdGateway, town: dict, kind: dict) -> dict | None:
    """A spot for this airport type that the town actually falls inside."""
    for radius in (3, 5, 8):
        spots = await gateway.query(
            "find_airport_spots",
            {"town_id": town["id"], "airport_type": kind["id"],
             "radius": radius, "max_results": 1},
        ) or []
        if spots and spots[0].get("within_coverage"):
            return {
                "town": town["name"],
                "population": town["population"],
                "airport_type": kind["id"],
                "x": spots[0]["x"],
                "y": spots[0]["y"],
                "takes_big_planes": kind["id"] != 5,
            }
    return None


async def _water_sites(gateway: NttdGateway, towns: list[dict], limit: int) -> list[dict]:
    """Dock spots by town, flagged as unproven.

    Deliberately not promised. Whether two docks share water cannot be settled from here:
    the planner has called a pair unconnected that a ship then served, and called pairs
    connected whose ships never arrived. The vehicle is the authority, so the sequence that
    works is build two docks, run one ship, and watch whether it moves.
    """
    found: list[dict] = []
    for town in towns:
        spots = await gateway.query(
            "find_dock_spots", {"town_id": town["id"], "radius": 12, "max_results": 1},
        ) or []
        if spots:
            found.append({
                "town": town["name"],
                "population": town["population"],
                "x": spots[0]["x"],
                "y": spots[0]["y"],
                "shares_water_with": "unknown until a ship is run",
            })
        if len(found) >= limit:
            break
    return found

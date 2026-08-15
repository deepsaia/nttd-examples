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

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where these modules are siblings and the
    # package above them is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a tool resolving from anywhere on
    # PYTHONPATH, which is what keeps a `class` reference in a HOCON from reaching
    # arbitrary code.
    from nttd_gateway import NttdGateway

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

        # Surveyed once per session, then remembered. The map does not move: a town that
        # an airport covers still covers it next turn, and a dock spot stays where it is.
        # Without this the whole survey ran again on every turn, which for air meant one
        # query per airport type per town, every time the network asked where to build.
        remembered = (sly_data.setdefault("sites", {})).get(mode)
        if remembered:
            return remembered[:limit]

        towns = sorted(
            await gateway.query("get_towns") or [],
            key=lambda t: -(t.get("population") or 0),
        )
        towns = [t for t in towns if (t.get("population") or 0) >= TOO_SMALL_TO_SERVE]

        if mode == "air":
            found = await _air_sites(gateway, towns, limit)
        elif mode == "water":
            found = await _water_sites(gateway, towns, limit)
        else:
            found = _town_sites(towns, limit)
        sly_data["sites"][mode] = found
        return found


def _town_sites(towns: list[dict], limit: int) -> list[dict]:
    """Road and rail start from the towns themselves; the corridor is chosen later."""
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
    """A spot for this airport type that the town actually falls inside.

    ONE query per type, asking widely and choosing from what comes back. This used to walk
    radii of 3, 5 and 8 taking the single nearest each time, which is three calls to answer
    a question one call answers better: a radius of 8 already contains the smaller ones, and
    asking for several candidates finds a covered site that taking only the nearest misses.
    """
    spots = await gateway.query(
        "find_airport_spots",
        {"town_id": town["id"], "airport_type": kind["id"], "radius": 8, "max_results": 6},
    ) or []
    for spot in spots:
        if spot.get("within_coverage"):
            return {
                "town": town["name"],
                "population": town["population"],
                "airport_type": kind["id"],
                "x": spot["x"],
                "y": spot["y"],
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

"""Where an airport can stand and still see the town it was built for.

This is the tool that owns the largest measured gap in the recorded set. One run put a
metropolitan airport 29 tiles from Tonwood, a town of 2,421. The airport landed inside the
catchment of Fontborough, population 348, two 480 seat planes flew an almost empty 286 tile
leg, and the run scored 118 against its sibling's 173.

Three gates, each one a lost run:

**Inside the coverage, always.** `find_airport_spots` returns `within_coverage`, and a spot
where it is false earns nothing at all. Every airport in the best run was 3 to 6 tiles from
its town centre.

**The biggest type that covers, not the smallest.** Smallest-that-covers always resolves to a
commuter field, which is SMALL: it caps the whole network at small aircraft and about a
quarter of the capacity on the same leg.

**No village endpoints.** A 348 person town returns planes empty and costs the same to fly to
as a city.

Surveyed ONCE per session and cached, because towns do not move and coverage does not change.
The previous version re-surveyed on every turn, which is one query per airport type per town,
every time the network asked where to build.

Nothing here prices an airport. `estimate_cost` is a participant action rather than a
read-only query, so pricing costs a step; affordability is the strategist's business with the
loan, and this tool stays free.
"""

from __future__ import annotations

from typing import Any

import httpx
from neuro_san.interfaces.coded_tool import CodedTool

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns_air import air_rules
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where `ns` and `ns_air` are packages beside
    # the module being loaded and the packages above them are not on the path.
    from ns import constants as key
    from ns.gateway import NttdGateway

    from ns_air import air_rules

# A town too small to fill anything. Measured: a 348 person endpoint sent 480 seat planes back
# almost empty on a leg that cost exactly as much to fly as one into a city.
TOO_SMALL_TO_SERVE = 400

# How many towns to survey. The ranking only ever uses the largest, and each town costs one
# query per airport type until one lands, so surveying the whole map buys nothing.
TOWNS_SURVEYED = 20

# Spots to ask for per query. find_airport_spots sorts by cargo acceptance first and distance
# second, so the covered spot is not always the first one back and asking for one can miss it.
SPOTS_PER_QUERY = 8

DEFAULT_LIMIT = 8


class SurveyAirportSites(CodedTool):
    """Every town worth an airport, with the biggest field that still covers it."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
        limit = int(args.get("limit") or DEFAULT_LIMIT)

        remembered = sly_data.get(key.SITES) or []
        if remembered:
            return {
                "sites": remembered[:limit],
                "surveyed": len(remembered),
                "from_cache": True,
            }

        try:
            sites = await _survey(gateway)
        except httpx.HTTPError as exception:
            return (
                f"Error: nttd did not answer the survey ({exception}). Nothing was cached, so "
                "calling this again is a real retry rather than a repeat."
            )

        if not sites:
            return (
                f"Error: no town of {TOO_SMALL_TO_SERVE} people or more has a buildable airport "
                "spot inside its own coverage. Report that this map cannot be flown rather than "
                "asking again: towns do not move and the answer will not change."
            )

        # Cached only when it found something. A transport failure also answers empty, and
        # caching that would freeze one bad reply into the whole session.
        sly_data[key.SITES] = sites
        return {
            "sites": sites[:limit],
            "surveyed": len(sites),
            "from_cache": False,
            "note": "Every site listed is inside its own airport's coverage. Name a site by its "
                    "site_id. The coordinates are the builder's business, not yours.",
        }


async def _survey(gateway: NttdGateway) -> list[dict[str, Any]]:
    """The whole map, once: the largest towns and the biggest field each of them fits."""
    towns = [
        town for town in (await gateway.query("get_towns") or [])
        if int(town.get("population") or 0) >= TOO_SMALL_TO_SERVE
    ]
    towns.sort(key=lambda town: -int(town.get("population") or 0))

    offered = await gateway.query("get_airport_types") or []
    # get_airport_types lists only what this year offers, so an intercontinental airport is
    # simply absent in 1960 rather than refused after a day is spent on it.
    reach_by_type = {
        int(kind["id"]): int(kind.get("coverage") or 0)
        for kind in offered if kind.get("id") is not None
    }
    types = air_rules.largest_first(reach_by_type)

    sites: list[dict[str, Any]] = []
    for town in towns[:TOWNS_SURVEYED]:
        site = await _best_site(gateway, town, types, reach_by_type)
        if site is not None:
            sites.append(site)
    return sites


async def _best_site(
    gateway: NttdGateway,
    town: dict[str, Any],
    types: list[int],
    reach_by_type: dict[int, int],
) -> dict[str, Any] | None:
    """The biggest field that still puts this town inside its own catchment."""
    for airport_type in types:
        reach = reach_by_type.get(airport_type) or air_rules.coverage(airport_type)
        if reach <= 0:
            continue
        # The search box IS the catchment. within_coverage is abs(dx) + abs(dy) <= coverage, so
        # no tile outside this box can ever qualify, and a wider radius only buries the close
        # answer under spots that score better on cargo acceptance. Searching wide is how an
        # airport ended up 29 tiles from the town it was built for.
        spots = await gateway.query("find_airport_spots", {
            "town_id": int(town["id"]),
            "airport_type": airport_type,
            "radius": reach,
            "max_results": SPOTS_PER_QUERY,
        }) or []
        covered = [spot for spot in spots if spot.get("within_coverage")]
        if not covered:
            continue
        # Nearest the centre wins, because coverage is a radius around the AIRPORT: a spot at
        # the edge of it leaves half the town's houses outside the catchment.
        spot = min(covered, key=lambda spot: int(spot.get("distance") or 0))
        return {
            # Derived from the town id, which never changes, so a site keeps its name even if
            # the survey is ever recomputed.
            "site_id": f"site-{int(town['id'])}",
            "town": str(town["name"]),
            "town_id": int(town["id"]),
            "population": int(town.get("population") or 0),
            "airport_type": airport_type,
            "airport": air_rules.name(airport_type),
            "takes_big_planes": air_rules.takes_big_planes(airport_type),
            "x": int(spot["x"]),
            "y": int(spot["y"]),
            "tiles_from_town_centre": int(spot.get("distance") or 0),
            "coverage": reach,
        }
    return None

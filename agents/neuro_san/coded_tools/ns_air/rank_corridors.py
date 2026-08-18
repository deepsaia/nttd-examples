"""Which pair of surveyed sites to fly, best first.

Air is the one mode where distance PAYS. The evidence is stark: one big plane on a 205 tile
leg earned 74,986 in a year while small planes on 35 tile hops earned about 13,000 each. So
distance is favoured here rather than penalised, which is the opposite sign to the road
ranking, where a short corridor is cheap to join and saturates quickly.

Two numbers decide a corridor and both are already known from the survey:

**The smaller population.** A leg is only as full as its emptier end. A long leg into a 348
person village returns planes empty and costs the same to fly as a leg into a city, which is
how one run scored 118 against its sibling's 173.

**The distance between the airports.** Not between the town centres: the game pays on the
distance the aircraft actually flies, and the airports are what it flies between.

Whether BOTH ends take a big plane is reported and sorts first, because the smaller field
decides what may fly and a commuter end caps the leg at about a quarter of the capacity.

This tool makes no query at all. Everything it needs is in the cached survey, so ranking is
free and instant and can be redone as often as the strategist likes.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns_air import air_rules
except ImportError:
    from ns import constants as key

    from ns_air import air_rules

DEFAULT_LIMIT = 8


class RankCorridors(CodedTool):
    """Town pairs worth an air route, the longest and busiest first."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        limit = int(args.get("limit") or DEFAULT_LIMIT)

        sites = sly_data.get(key.SITES) or []
        if not sites:
            return (
                "Error: nothing has been surveyed yet, so there is no pair to rank. Call "
                "survey_airport_sites first; it is free and it runs once for the whole session."
            )

        routes = sly_data.get(key.ROUTES) or []
        corridors = corridors_from_sites(sites, routes)
        if not corridors:
            return (
                f"Error: all {len(sites)} surveyed sites are already paired by routes that exist. "
                "Add aircraft to a route that is filling up instead of opening another one."
            )

        return {
            "corridors": corridors[:limit],
            "candidates": len(corridors),
            "note": "Name a corridor by its corridor_id when you build it. Both ends taking big "
                    "planes is worth more than a slightly longer leg that does not.",
        }


def corridors_from_sites(
    sites: list[dict[str, Any]], routes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Every pair of surveyed sites still worth flying, best first.

    A module function rather than a method, because plan_build_corridor resolves a corridor_id
    by running this again. One producer of corridor ids means an id the strategist reads back
    always resolves to the pair it was shown.
    """
    flown = {
        frozenset(str(town) for town in (route.get("towns") or []))
        for route in routes if len(route.get("towns") or []) == 2
    }

    corridors: list[dict[str, Any]] = []
    for index, first in enumerate(sites):
        for second in sites[index + 1:]:
            pair = frozenset((str(first["town"]), str(second["town"])))
            # The same pair twice is waste. A town that already has an airport can still anchor
            # a DIFFERENT corridor, which is how a second leg gets added to a hub.
            if pair in flown:
                continue
            corridors.append(_corridor(first, second))

    # Big planes first, then the score. A corridor whose smaller field is commuter is capped at
    # a small aircraft however long and busy the leg is.
    corridors.sort(
        key=lambda corridor: (corridor["takes_big_planes"], corridor["score"]), reverse=True,
    )
    return corridors


def corridor_key(corridor_id: str) -> str:
    """One spelling of a corridor id, so a strategist saying it back cannot miss by a space."""
    return "".join(str(corridor_id).split()).lower()


def _corridor(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """One candidate pair, with the two numbers that decide it."""
    distance = abs(int(first["x"]) - int(second["x"])) + abs(int(first["y"]) - int(second["y"]))
    populations = [int(first.get("population") or 0), int(second.get("population") or 0)]
    types = [int(first["airport_type"]), int(second["airport_type"])]
    big = air_rules.both_take_big_planes(types[0], types[1])
    return {
        "corridor_id": f"{_slug(first['town'])}-{_slug(second['town'])}",
        "towns": [str(first["town"]), str(second["town"])],
        "sites": [str(first["site_id"]), str(second["site_id"])],
        "populations": populations,
        "distance": distance,
        "airport_types": types,
        "airports": [air_rules.name(types[0]), air_rules.name(types[1])],
        "takes_big_planes": big,
        "plane_class": "big" if big else "small",
        # The emptier end times the length of the leg. Both halves are measured: a leg is only
        # as full as its smaller town, and revenue per trip rises with the distance flown.
        "score": min(populations) * distance,
    }


def _slug(town: str) -> str:
    """A town name with nothing in it a strategist could mistype back."""
    return "".join(str(town).split()).lower()

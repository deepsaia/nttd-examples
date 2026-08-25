"""Producers paired with consumers that take what they make, with a station spot at each end.

**This is where rail differs most from air.** An air corridor joins two TOWNS and carries their
passengers, so siting is about population and catchment. A rail corridor carries freight from
an industry that makes it to an industry that wants it, so a pair only exists if the cargo
matches at both ends. A line between two well-sited stations that do not share a cargo hauls
nothing, and every report about it looks healthy.

**Industry ids, not town ids.** A station sited at the town nearest an industry does not serve
the industry: catchment is small and a town centre is rarely inside it. The finder takes an
`industry_id` precisely so the spot lands where the cargo is, and asking by town is the same
mistake in a form that returns plausible-looking spots.

The survey is cached because industries do not move. Re-running it costs queries and returns
the same answer; what changes is which pairs are already built, and rank_corridors reads that
from the routes rather than from here.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import counting, session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway, QueryRefused
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_keys as rail
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_rules as rules
except ImportError:
    from ns import counting, session
    from ns.gateway import NttdGateway, QueryRefused

    from ns_rail_agent import rail_keys as rail
    from ns_rail_agent import rail_rules as rules

# How many industries to examine. Every one costs a spot query at each end of every pair it
# joins, so a whole 256x256 map's worth is minutes of queries for a handful of usable pairs.
MOST_INDUSTRIES = 24

# How many pairs to look for spots at. Ranking happens after, so this is how many candidates
# the ranker gets to choose between rather than how many get built.
MOST_PAIRS = 8

# How many spots to ask for at each end. More than a handful because the axis constraint
# rejects most of them: of 14 offered at each end of one measured corridor, 8 and 7 faced the
# needed axis, so asking for four would usually still find one and sometimes would not.
MOST_SPOTS = 12


class SurveyRailPairs(CodedTool):
    """Producer and consumer pairs that share a cargo, each with a buildable spot. Free."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._survey, args, sly_data)

    async def _survey(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        rail_type = sly_data.get(rail.RAIL_TYPE)
        if rail_type is None:
            return (
                "Error: no rail type has been chosen, and a station spot is searched for one "
                "rail type at a time. Call choose_rail_type first; it costs no game day."
            )

        try:
            industries: list[dict[str, Any]] = await gateway.query("get_industries") or []
        except QueryRefused as refused:
            return f"Error: the industry list was refused ({refused}). Nothing was surveyed."

        if not industries:
            return "Error: the game reports no industries, so there is no freight to haul."

        wanted, note = counting.counted(
            args.get("most_industries"), MOST_INDUSTRIES, most=MOST_INDUSTRIES,
        )
        pairs = _pairs_by_cargo(industries[:wanted])
        if not pairs:
            return {
                "pairs": [],
                "industries_examined": min(wanted, len(industries)),
                "note": (
                    "No industry on this map makes something another one accepts, within the "
                    "ones examined. That is a fact about the map rather than a failure: "
                    "raise most_industries, or this is a map for passengers rather than "
                    "freight."
                ),
            }

        found: list[dict[str, Any]] = []
        for pair in pairs[:MOST_PAIRS]:
            sited = await self._sites_for(gateway, pair, int(rail_type))
            if sited:
                found.append(sited)

        sly_data[rail.PAIRS] = found
        return {
            "pairs": [_readable(entry) for entry in found],
            "industries_examined": min(wanted, len(industries)),
            "note": note or None,
            "next": (
                "rank_corridors orders these by what they would carry and how far. Nothing is "
                "built until plan_build_corridor stages one and commit_plan submits it."
            ),
        }

    async def _sites_for(
        self, gateway: NttdGateway, pair: dict[str, Any], rail_type: int
    ) -> dict[str, Any] | None:
        """A spot at each end that takes the axis the line will arrive on. Rule 1.

        The axis is worked out FIRST, from where the two industries are, and then a spot is
        asked for that takes it. That order is the whole point: choosing the nearest spot and
        accepting whatever orientation came with it is what built platforms the arriving track
        met side-on, three sessions running.

        Returns None when either end has no such spot. A pair with one good end is not half a
        route, it is no route, and offering it invites a build that cannot finish.
        """
        axis = rules.axis_for_approach(
            int(pair["producer"].get("x") or 0), int(pair["producer"].get("y") or 0),
            int(pair["consumer"].get("x") or 0), int(pair["consumer"].get("y") or 0),
        )

        ends = {}
        for role in ("producer", "consumer"):
            industry = pair[role]
            try:
                found = await gateway.query("find_station_spot", {
                    "industry_id": int(industry["id"]),
                    "rail_type": rail_type,
                    "platform_length": rules.PLATFORM_LENGTH,
                    "max_results": MOST_SPOTS,
                })
            except QueryRefused:
                return None

            spot = _facing_the_line(found, axis)
            if spot is None:
                return None
            ends[role] = spot

        return {
            "axis": axis,
            "pair_id": f"{pair['cargo']}-{pair['producer']['id']}-{pair['consumer']['id']}",
            "cargo": pair["cargo"],
            "cargo_id": pair.get("cargo_id"),
            "producer": {**ends["producer"], "industry_id": int(pair["producer"]["id"]),
                         "name": pair["producer"].get("name")},
            "consumer": {**ends["consumer"], "industry_id": int(pair["consumer"]["id"]),
                         "name": pair["consumer"].get("name")},
            "distance": _distance(ends["producer"], ends["consumer"]),
        }


def _pairs_by_cargo(industries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every producer joined to every consumer of the same cargo.

    Reads `produces_cargo` and `accepts_cargo`, which the industry list carries from day one.
    The `production` field does not: it is built from last month's output, so on day one it is
    empty for every industry on the map and a pairing built from it finds nothing at all.
    """
    pairs: list[dict[str, Any]] = []
    for producer in industries:
        makes = producer.get("produces_cargo") or []
        if not makes:
            continue
        for consumer in industries:
            if int(consumer.get("id", -1)) == int(producer.get("id", -2)):
                continue
            takes = consumer.get("accepts_cargo") or []
            shared = [cargo for cargo in makes if cargo in takes]
            for cargo in shared:
                pairs.append({
                    "producer": producer, "consumer": consumer,
                    "cargo": _label(cargo), "cargo_id": _cargo_id(cargo),
                })
    return pairs


def _facing_the_line(found: Any, axis: int) -> dict[str, Any] | None:
    """The nearest spot that takes the axis the corridor needs, or None. Rules 1 and 1b.

    The finder returns spots sorted by distance, so the first match is the nearest one that
    faces the right way. A spot is only a match when a train could ENTER on that axis, not
    merely when the platform fits: those are two different lists and building on the wrong one
    made a station that had to be demolished.
    """
    spots = (found or {}).get("spots") if isinstance(found, dict) else found
    for spot in spots or []:
        takes, _ = rules.spot_takes_axis(spot, axis)
        if not takes:
            continue
        return {
            "x": int(spot.get("x") or 0), "y": int(spot.get("y") or 0),
            "direction": axis,
            "distance_to_industry": spot.get("distance"),
            "cargo_acceptance": spot.get("cargo_acceptance"),
        }
    return None


def _label(cargo: Any) -> str:
    if isinstance(cargo, dict):
        return str(cargo.get("label") or cargo.get("name") or cargo.get("id") or "?")
    return str(cargo)


def _cargo_id(cargo: Any) -> int | None:
    if isinstance(cargo, dict):
        raw = cargo.get("id")
        return int(raw) if isinstance(raw, int) else None
    return cargo if isinstance(cargo, int) else None


def _distance(one: dict[str, Any], other: dict[str, Any]) -> int:
    """Manhattan tiles, which is how the game measures a haul for payment."""
    return abs(int(one["x"]) - int(other["x"])) + abs(int(one["y"]) - int(other["y"]))


def _readable(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "pair_id": entry["pair_id"],
        "cargo": entry["cargo"],
        "from": entry["producer"].get("name"),
        "to": entry["consumer"].get("name"),
        "distance": entry["distance"],
    }

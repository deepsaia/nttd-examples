"""Prove the airports that were built are the airports that were intended, and record them.

Run straight after the commit that builds a corridor. It answers two questions that nothing
else can answer, and both have a measured price.

**Did each airport land in the town it was built for?** The game names a station after the
catchment it joined. One run built an airport for Tonwood and the station came back named
"Fontborough Airport", a village of 348 people; the run scored 118 where its sibling scored
173. The game said what had happened and nothing read it, so this tool reads the name back
and says so loudly.

**Where is the hangar?** An aircraft is built at the HANGAR tile, and that tile is not
derivable from the airport tile: measured offsets were +5 in x for metropolitan and large, +4
in x for commuter and +3 in y for international. Four consecutive purchases at an airport's
own coordinates failed ERR_UNKNOWN with no diagnostic. The hangar comes from `get_hangars` or
it does not come at all.

What it writes into `routes` is everything the fleet tools need, so buying afterwards needs no
argument from a model: the two station ids, the hangar as the depot, whether both ends take a
big plane, and the towns.
"""

from __future__ import annotations

from typing import Any

import httpx
from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway, QueryRefused
    from agents.neuro_san.coded_tools.ns_air import air_rules
    from agents.neuro_san.coded_tools.ns_air.plan_build_corridor import CORRIDOR_INTENT
except ImportError:
    from ns import constants as key
    from ns import session
    from ns.gateway import NttdGateway, QueryRefused

    from ns_air import air_rules
    from ns_air.plan_build_corridor import CORRIDOR_INTENT


class ConfirmAirports(CodedTool):
    """Reads back what was built, checks it against what was intended, and records the route."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._confirm, args, sly_data)

    async def _confirm(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        waiting = [
            decision for decision in (sly_data.get(key.DECISIONS) or [])
            if decision.get("kind") == CORRIDOR_INTENT and not decision.get("confirmed")
        ]
        if not waiting:
            return (
                "Error: no corridor is waiting to be confirmed. This runs after commit_plan has "
                "committed a corridor staged by plan_build_corridor, and each corridor is "
                "confirmed once."
            )
        intent = waiting[-1]

        try:
            stations = await gateway.query("get_stations") or []
            hangars = await gateway.query("get_hangars") or []
        except (httpx.HTTPError, QueryRefused) as exception:
            # Kept rather than left to the shared guard: what a caller has to be told here is that
            # the route was NOT recorded, so buying aircraft for it now buys them for a corridor
            # nothing has checked.
            return (
                f"Error: nttd did not answer ({exception}), so what was built is still unknown. "
                "Nothing was recorded. Call this again before buying any aircraft."
            )

        already = {int(station_id) for station_id in intent.get("stations_before") or []}
        fresh = [
            station for station in stations
            if station.get("has_airport") and int(station["id"]) not in already
        ]
        if len(fresh) < 2:
            return (
                f"Error: {len(fresh)} of the 2 airports for {intent['corridor_id']} exist. The "
                "other was refused, and the refusal says why. No route was recorded, because an "
                "air route with one end carries nothing. Read the refusal and site the missing "
                "end somewhere else."
            )

        hangar_by_station = {int(hangar["station_id"]): hangar for hangar in hangars}
        ends: list[dict[str, Any]] = []
        warnings: list[str] = []
        unclaimed = list(fresh)
        for site in intent.get("sites") or []:
            end = _end(site, unclaimed, hangar_by_station)
            unclaimed = [station for station in unclaimed if int(station["id"]) != end["station_id"]]
            warnings.extend(_warnings(site, end))
            ends.append(end)

        route = _route(intent, ends)
        sly_data.setdefault(key.ROUTES, []).append(route)
        intent["confirmed"] = True

        return {
            "route": route,
            "warnings": warnings,
            "verdict": "as intended" if not warnings else "NOT as intended, read the warnings",
        }


def _end(
    site: dict[str, Any],
    unclaimed: list[dict[str, Any]],
    hangar_by_station: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """The station that answers for one intended site, and its hangar.

    Matched on the NAME first, because the name is the game telling you which town's catchment
    the airport joined. Only when no new station carries the town's name does it fall back to
    the nearest one, which is the case that has to be flagged rather than quietly accepted.
    """
    town = str(site.get("town") or "")
    named = [
        station for station in unclaimed
        if town.lower() in str(station.get("name") or "").lower()
    ]
    if named:
        station = named[0]
        serves_intended_town = True
    else:
        station = min(
            unclaimed,
            key=lambda candidate: abs(int(candidate["x"]) - int(site["x"]))
            + abs(int(candidate["y"]) - int(site["y"])),
        )
        serves_intended_town = False

    hangar = hangar_by_station.get(int(station["id"])) or {}
    airport_type = int(hangar.get("airport_type", site.get("airport_type", 0)))
    return {
        "station_id": int(station["id"]),
        "station_name": str(station.get("name") or ""),
        "intended_town": town,
        "serves_intended_town": serves_intended_town,
        "airport_type": airport_type,
        "airport": air_rules.name(airport_type),
        "takes_big_planes": air_rules.takes_big_planes(airport_type),
        "hangar": (
            {"x": int(hangar["hangar_x"]), "y": int(hangar["hangar_y"]),
             "tile": int(hangar["hangar_tile"])}
            if hangar else None
        ),
    }


def _warnings(site: dict[str, Any], end: dict[str, Any]) -> list[str]:
    """Everything about this end that a fleet tool must not be allowed to discover later."""
    said: list[str] = []
    if not end["serves_intended_town"]:
        said.append(
            f"{end['station_name']} was built for {end['intended_town']} and is not named for it. "
            "The game names a station after the catchment it joined, so this airport is serving "
            "another town. That exact failure cost a run 55 rating points, 118 against 173, "
            "because the planes flew a long leg into a 348 person village and came back empty. "
            "Do not buy aircraft for this end until it is re-sited."
        )
    if end["hangar"] is None:
        said.append(
            f"{end['station_name']} reports no hangar, so no aircraft can be built there. Buy at "
            "the other end of the corridor instead."
        )
    intended_type = int(site.get("airport_type") or 0)
    if end["airport_type"] != intended_type:
        said.append(
            f"{end['station_name']} is a {end['airport']} airport where a "
            f"{air_rules.name(intended_type)} one was intended."
        )
    if not end["takes_big_planes"]:
        said.append(
            f"{end['station_name']} is a {end['airport']} field, which is SMALL. A big plane "
            "crashes there with no warning and no refusal, so this corridor is capped at small "
            "aircraft."
        )
    return said


def _route(intent: dict[str, Any], ends: list[dict[str, Any]]) -> dict[str, Any]:
    """The route record, in the shape the fleet tools read it.

    `depot` is the hangar, because that is where an aircraft is built. It is taken from the end
    that has one rather than assumed to be the first: an aircraft built at either hangar can
    fly the whole corridor.
    """
    hangars = [end["hangar"] for end in ends if end["hangar"] is not None]
    types = [end["airport_type"] for end in ends]
    return {
        "mode": "air",
        "corridor_id": intent.get("corridor_id"),
        "stations": [end["station_id"] for end in ends],
        "station_names": [end["station_name"] for end in ends],
        "towns": [end["intended_town"] for end in ends],
        "depot": hangars[0] if hangars else None,
        "hangars": [
            {"station_id": end["station_id"], **end["hangar"]}
            for end in ends if end["hangar"] is not None
        ],
        "airport_types": types,
        # The smaller field decides what may fly, so one commuter end makes the whole leg small.
        "takes_big_planes": air_rules.both_take_big_planes(*types) if len(types) == 2 else False,
        "serves_intended_towns": [end["serves_intended_town"] for end in ends],
        "distance": intent.get("distance"),
        "ready": all(end["serves_intended_town"] for end in ends) and bool(hangars),
    }

"""Which surveyed pair to build next, and why that one.

Rail revenue rises with distance, so distance leads, inside a band: below about twenty tiles a
lorry would have done the job, and beyond roughly a hundred and twenty a single train spends
its life in transit and the line earns once a lap.

Ties break on how much the consuming end ACCEPTS of the cargo, which the survey already
carries from the spot finder. Production is deliberately not used, however tempting: the
industry list builds it from last month's output, so on day one it is zero for every industry
on the map and a ranking that leaned on it would rank nothing at all on the turn it matters.

A pair already served is not offered again. Building a second line between the same two
industries splits the same cargo across two routes and pays for two locomotives to carry what
one was carrying.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_keys as rail
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_rules as rules
except ImportError:
    from ns import constants as key
    from ns import session
    from ns.gateway import NttdGateway

    from ns_rail_agent import rail_keys as rail
    from ns_rail_agent import rail_rules as rules


class RankCorridors(CodedTool):
    """The surveyed pairs worth building, best first. Costs no game day."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._rank, args, sly_data)

    async def _rank(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        pairs = sly_data.get(rail.PAIRS) or []
        if not pairs:
            return (
                "Error: nothing has been surveyed. Call choose_rail_type, then "
                "survey_rail_pairs, then rank here."
            )

        routes = sly_data.get(key.ROUTES) or []
        served = {_pair_key(str(route.get("pair_id") or "")) for route in routes}

        offered = [pair for pair in pairs if _pair_key(pair["pair_id"]) not in served]
        if not offered:
            return {
                "corridors": [],
                "note": (
                    "Every surveyed pair is already served. Survey again with a larger "
                    "most_industries, or grow the fleet on the lines that exist rather than "
                    "building a second line between two industries already joined."
                ),
            }

        ranked = sorted(offered, key=lambda pair: (_worth(pair), _acceptance(pair)), reverse=True)
        return {
            "corridors": [
                {
                    "pair_id": pair["pair_id"],
                    "cargo": pair["cargo"],
                    "from": pair["producer"].get("name"),
                    "to": pair["consumer"].get("name"),
                    "distance": pair["distance"],
                    "accepted_at_the_far_end": _acceptance(pair),
                    "why": _why(pair),
                }
                for pair in ranked
            ],
            "next": (
                "plan_build_corridor takes a pair_id exactly as it appears here. It stages the "
                "whole corridor in the one order that works and builds nothing until "
                "commit_plan submits it."
            ),
        }


def _worth(pair: dict[str, Any]) -> float:
    """How much this corridor is worth building, as a number to sort by.

    Distance, because freight pays by how far it is carried, but only up to the point where a
    single train spends its life in transit and the line earns once a lap. Beyond that the
    extra tiles cost track and buy nothing until there are trains enough to fill them.
    """
    distance = float(pair.get("distance") or 0)
    if distance < rules.SHORTEST_WORTH_RAILS:
        # Short enough that a lorry would have done. Ranked last rather than refused: on a
        # cramped map a short line that exists beats a long one that does not.
        return distance * 0.25
    if distance > rules.LONGEST_WORTH_ONE_TRAIN:
        return float(rules.LONGEST_WORTH_ONE_TRAIN)
    return distance


def _acceptance(pair: dict[str, Any]) -> int:
    """How much of this cargo the consumer's spot accepts, from the finder's own reading.

    Zero when the finder said nothing about it, which is honest: a spot with no acceptance
    figure has not been shown to take the cargo, and ranking it as if it had is how a line gets
    built to an industry that will not unload it.
    """
    spot = pair.get("consumer") or {}
    wanted = str(pair.get("cargo") or "")
    for entry in spot.get("cargo_acceptance") or []:
        if str(entry.get("cargo_label") or "") == wanted:
            return int(entry.get("acceptance") or 0)
    return 0


def _why(pair: dict[str, Any]) -> str:
    distance = int(pair.get("distance") or 0)
    if distance < rules.SHORTEST_WORTH_RAILS:
        return (
            f"{distance} tiles, shorter than the {rules.SHORTEST_WORTH_RAILS} where rail "
            "starts to beat a lorry, so it is ranked below the longer hauls"
        )
    if distance > rules.LONGEST_WORTH_ONE_TRAIN:
        return (
            f"{distance} tiles, long enough that one train earns once a lap; worth building "
            "when there is money for a second train on it"
        )
    return f"{distance} tiles of {pair.get('cargo')}, which is the range rail is paid for"


def _pair_key(pair_id: str) -> str:
    """Compared loosely, because a model retyping an id changes its case or spacing."""
    return "".join(pair_id.split()).lower()

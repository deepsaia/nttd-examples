"""Put a depot beside a line that already exists, and join it to that line.

Two rules meet here, and both are ordering rules rather than judgement:

**The depot comes after the track.** `find_rail_depot_spot` searches for a tile adjacent to
existing rail. Asked before the line is laid it correctly returns nothing, which reads as a
failure and is not one: it is the wrong question asked too early. So this tool refuses to run
until confirm_rail_route says the line joins, rather than passing an empty search back as a
problem to solve.

**Building a depot does not connect it.** The neighbouring track needs a curve piece facing the
depot's entrance. `connect_rail` cannot supply it, because it lays rail on both endpoints and
so fails against the depot itself; `connect_depot` exists for exactly this. A depot that was
built and not joined looks like a depot in every report, and a train built in it cannot leave.

Both actions are staged together, in that order, because a batch is submitted in order and one
commit costs one game day where two cost two.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import envelope, session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway, QueryRefused
    from agents.neuro_san.coded_tools.ns.plan import Plan
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_keys as rail
except ImportError:
    from ns import envelope, session
    from ns.gateway import NttdGateway, QueryRefused
    from ns.plan import Plan

    from ns_rail_agent import rail_keys as rail

# How far from the producer's platform to look. Near the loading end so a train returns to
# service beside the station it works from rather than crossing the whole line to be repaired.
SEARCH_RADIUS = 10


class PlanAddDepot(CodedTool):
    """Stages a depot beside an existing line, and the piece that joins it on."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._stage, args, sly_data)

    async def _stage(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        pair_id = str(args.get("pair_id") or "").strip()
        if not pair_id:
            return (
                "Error: no pair_id was given. Use the one confirm_rail_route reports for a "
                "corridor whose stage is no_depot."
            )

        intents = sly_data.get(rail.INTENT) or {}
        intent = next(
            (value for key_id, value in intents.items() if _same(key_id, pair_id)), None,
        )
        if intent is None:
            return f"Error: {pair_id} was never staged. Staged: {', '.join(intents) or 'none'}."

        stage = intent.get("stage")
        if stage in ("staged", "stations_missing", "line_not_joined"):
            return (
                f"Error: {pair_id} is at stage '{stage}', and a depot is searched for beside "
                "EXISTING rail. Asked now the search correctly returns nothing. Commit the "
                "corridor, then confirm_rail_route, and come back when the line joins."
            )
        existing = intent.get("depot") or {}
        if existing.get("connected"):
            return (
                f"{pair_id} already has a depot joined to its line at "
                f"({existing.get('x')},{existing.get('y')}). Nothing staged."
            )

        if existing.get("x") is not None:
            # A depot is already there and the confirm says it is not joined, so the missing
            # piece is the curve and not the building. Searching for a fresh spot here would
            # buy a SECOND depot beside the first and leave both unjoined, which is the same
            # money spent twice on the half of the job that already worked.
            return await self._join_only(sly_data, existing)

        rail_type = int(intent.get("rail_type") or sly_data.get(rail.RAIL_TYPE) or 0)
        producer = intent["producer"]
        try:
            found = await gateway.query("find_rail_depot_spot", {
                "x": int(producer["x"]), "y": int(producer["y"]),
                "rail_type": rail_type, "radius": SEARCH_RADIUS, "max_results": 4,
            })
        except QueryRefused as refused:
            return f"Error: the depot search was refused ({refused}). Nothing was staged."

        spot = _first(found)
        if spot is None:
            return (
                f"Error: no depot fits beside the line near the {producer.get('name')} end "
                f"within {SEARCH_RADIUS} tiles. The line exists, so this is about ground "
                "rather than order: inspect the tiles along it and pick somewhere with a "
                "clear neighbour, or build the depot nearer the consumer end."
            )

        actions = [
            envelope.action(
                "build_rail_depot",
                x=int(spot["x"]), y=int(spot["y"]),
                direction=int(spot.get("depot_direction") or 0),
                rail_type=rail_type,
            ),
            # The second half of the rule. Building it does not join it.
            envelope.action("connect_depot", x=int(spot["x"]), y=int(spot["y"])),
        ]
        problems = envelope.check(actions)
        if problems:
            return f"Error: the depot cannot be staged as written: {'; '.join(problems)}"

        intent["depot"] = {
            "x": int(spot["x"]), "y": int(spot["y"]),
            "direction": int(spot.get("depot_direction") or 0),
            "adjacent_track": [spot.get("adjacent_track_x"), spot.get("adjacent_track_y")],
            "connected": False,
        }

        plan = Plan(sly_data)
        waiting = plan.add(*actions)
        return {
            "staged": len(actions),
            "waiting_in_plan": waiting,
            "depot": {"x": int(spot["x"]), "y": int(spot["y"]),
                      "beside_track_at": intent["depot"]["adjacent_track"]},
            "plan": plan.describe(),
            "next": (
                "commit_plan submits both and spends one game day. Then confirm_rail_route "
                "again: a depot that built but did not join reads as a depot everywhere else, "
                "and a train built in it cannot leave."
            ),
        }


    async def _join_only(
        self, sly_data: dict[str, Any], depot: dict[str, Any]
    ) -> dict[str, Any] | str:
        """Stage the curve piece for a depot that was built and never joined. Rule 5."""
        action = envelope.action("connect_depot", x=int(depot["x"]), y=int(depot["y"]))
        problems = envelope.check([action])
        if problems:
            return f"Error: the depot cannot be joined as written: {'; '.join(problems)}"

        plan = Plan(sly_data)
        waiting = plan.add(action)
        return {
            "staged": 1,
            "waiting_in_plan": waiting,
            "depot": {"x": int(depot["x"]), "y": int(depot["y"])},
            "plan": plan.describe(),
            "next": (
                "The depot is already built; only the piece joining it to the line is "
                "missing. commit_plan submits that, then confirm_rail_route traces from the "
                "depot to the platform and says whether it took."
            ),
        }


def _first(found: Any) -> dict[str, Any] | None:
    spots = found.get("spots") if isinstance(found, dict) else found
    for spot in spots or []:
        if spot.get("x") is not None and spot.get("y") is not None:
            return spot
    return None


def _same(one: str, other: str) -> bool:
    return "".join(one.split()).lower() == "".join(other.split()).lower()

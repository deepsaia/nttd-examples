"""Stage a whole rail corridor, in the one order that works.

An air corridor is two airports and nothing between them: one batch, and it either built or it
did not. A rail corridor is four things that depend on each other, and the dependency is what
makes rail hard:

    1. the station at the producer      built on the axis the finder reported, 1 x 3
    2. the station at the consumer      likewise
    3. the line between them            connect_rail, hinted at both platforms
    4. the depot beside the line        found only AFTER track exists, then connected

Staged as ONE batch because a batch has no ceiling, so six actions committed separately cost
six days of a run that has 366. The batch is ordered, and the order is not interchangeable: the
line cannot be hinted at platforms that do not exist yet, and `find_rail_depot_spot` searches
for a tile adjacent to existing rail, so asked before the line is laid it correctly returns
nothing.

**One batch here is still two game days**, and that is deliberate. `commit_plan` puts
`connect_rail` in a step of its own, because it lays a whole corridor and can fail on a single
tile, and its refusal names that tile: batched with the stations, the coordinate in the report
would be ambiguous about which action it belonged to. So the stations go in one day and the
line in the next, and the report about the line means something.

**The depot is therefore not staged here.** Its spot cannot be known until the track is built,
and a plan that guessed one would be staging an action against a tile nothing has checked.
This tool stages the three that can be known, and says so; `plan_add_depot` finishes the
corridor once the line exists.

**Nothing is submitted.** A `plan_` tool stages and returns what it staged. Only commit_plan
moves the clock, which is what lets the strategist, the builder and the fleet contribute to
one day.

**The intent is written down first.** Rail fails in the middle rather than at the ends, and
after the commit the only way to tell which of the staged actions did not take is to compare
against what was meant. That comparison is confirm_rail_route.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import envelope, session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.plan import Plan
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_keys as rail
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_rules as rules
except ImportError:
    from ns import envelope, session
    from ns.gateway import NttdGateway
    from ns.plan import Plan

    from ns_rail_agent import rail_keys as rail
    from ns_rail_agent import rail_rules as rules


class PlanBuildCorridor(CodedTool):
    """Stages both stations and the line between them. Builds nothing until committed."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._stage, args, sly_data)

    async def _stage(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        pair_id = str(args.get("pair_id") or "").strip()
        if not pair_id:
            return (
                "Error: no pair_id was given. Call rank_corridors and pass back one of the "
                "pair_id values exactly as it came out."
            )

        rail_type = sly_data.get(rail.RAIL_TYPE)
        if rail_type is None:
            return (
                "Error: no rail type has been chosen. A station, its track and its depot must "
                "all be the same type or a train cannot enter its own route. Call "
                "choose_rail_type first."
            )

        pairs = sly_data.get(rail.PAIRS) or []
        wanted = "".join(pair_id.split()).lower()
        pair = next(
            (entry for entry in pairs
             if "".join(str(entry["pair_id"]).split()).lower() == wanted),
            None,
        )
        if pair is None:
            return (
                f"Error: {pair_id} is not a surveyed pair. Call rank_corridors and use a "
                "pair_id exactly as it comes back."
            )

        producer, consumer = pair["producer"], pair["consumer"]
        actions = [
            # Rule 1 and rule 2 together: the axis the finder reported, and the footprint it
            # dry-ran. Both passed explicitly, because both default to something else.
            envelope.action(
                "build_rail_station",
                x=int(producer["x"]), y=int(producer["y"]),
                direction=int(producer["direction"]), rail_type=int(rail_type),
                **rules.station_footprint(),
            ),
            envelope.action(
                "build_rail_station",
                x=int(consumer["x"]), y=int(consumer["y"]),
                direction=int(consumer["direction"]), rail_type=int(rail_type),
                **rules.station_footprint(),
            ),
            # Rule 3: hinted at both platforms, so the line joins them rather than merely
            # reaching them. Staged after both stations in the same batch, which is fine
            # because a batch is submitted in order.
            envelope.action(
                "connect_rail",
                from_x=int(producer["x"]), from_y=int(producer["y"]),
                to_x=int(consumer["x"]), to_y=int(consumer["y"]),
                from_hint_x=int(producer["x"]), from_hint_y=int(producer["y"]),
                to_hint_x=int(consumer["x"]), to_hint_y=int(consumer["y"]),
                rail_type=int(rail_type),
            ),
        ]

        problems = envelope.check(actions)
        if problems:
            return f"Error: the corridor cannot be staged as written: {'; '.join(problems)}"

        # Written BEFORE the commit. Afterwards there is no way to tell which of three actions
        # was the one that did not take without knowing what was meant.
        intents = sly_data.setdefault(rail.INTENT, {})
        intents[pair["pair_id"]] = {
            "pair_id": pair["pair_id"],
            "cargo": pair["cargo"],
            "cargo_id": pair.get("cargo_id"),
            "rail_type": int(rail_type),
            "producer": {"x": int(producer["x"]), "y": int(producer["y"]),
                         "direction": int(producer["direction"]),
                         "industry_id": producer.get("industry_id"),
                         "name": producer.get("name")},
            "consumer": {"x": int(consumer["x"]), "y": int(consumer["y"]),
                         "direction": int(consumer["direction"]),
                         "industry_id": consumer.get("industry_id"),
                         "name": consumer.get("name")},
            "stage": "staged",
        }

        plan = Plan(sly_data)
        waiting = plan.add(*actions)
        return {
            "staged": len(actions),
            "waiting_in_plan": waiting,
            "corridor": {
                "pair_id": pair["pair_id"], "cargo": pair["cargo"],
                "from": producer.get("name"), "to": consumer.get("name"),
                "distance": pair.get("distance"),
            },
            "plan": plan.describe(),
            "next": (
                "commit_plan submits these three as two game days: the stations together, "
                "then the line alone, because a partial connect_rail names the tile it failed "
                "on and that coordinate has to belong to one action. Then confirm_rail_route, "
                "because connect_rail keeps a partial build and reports it: a reply is not a "
                "line that joins. The depot comes after that, from plan_add_depot, because the "
                "search for one looks for a tile beside existing rail and there is none until "
                "the line is laid."
            ),
        }

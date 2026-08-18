"""Stage both airports of one corridor, and remember what they were meant to serve.

Two airports IS an air route. Nothing has to be built between them, which is why the mode
outperforms the others and why a corridor is one batch rather than a project.

**Both in one step.** A step is a game day and a batch has no ceiling, so two `build_airport`
actions staged together cost ONE day. Committing them separately costs two, and a 366 day run
that spends a day per action loses the year to paperwork.

**Nothing is submitted here.** A `plan_` tool stages and returns what it staged. Only
commit_plan moves the clock, which is what lets the strategist, the builder and the fleet all
contribute to the same day.

**The intent is recorded.** What the corridor was meant to serve, and which stations already
existed, are written down before the commit, because after it the only way to tell a correct
airport from one that landed in the wrong town's catchment is to compare against what was
intended. That comparison is confirm_airports, and the failure it catches cost a run 55
rating points.
"""

from __future__ import annotations

from typing import Any

import httpx
from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import envelope, session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway, QueryRefused
    from agents.neuro_san.coded_tools.ns.plan import Plan
    from agents.neuro_san.coded_tools.ns_air.rank_corridors import corridor_key, corridors_from_sites
except ImportError:
    from ns import constants as key
    from ns import envelope, session
    from ns.gateway import NttdGateway, QueryRefused
    from ns.plan import Plan

    from ns_air.rank_corridors import corridor_key, corridors_from_sites

# The kind of decision this tool writes and confirm_airports reads back. Named once, because a
# typo here does not raise, it just means confirm never finds the intent it is looking for.
CORRIDOR_INTENT = "air_corridor"


class PlanBuildCorridor(CodedTool):
    """Stages the two airports of a named corridor. Builds nothing until the plan is committed."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._stage_corridor, args, sly_data)

    async def _stage_corridor(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        corridor_id = str(args.get("corridor_id") or "").strip()
        if not corridor_id:
            return (
                "Error: no corridor_id was given. Call rank_corridors and pass back one of the "
                "corridor_id values exactly as it came out."
            )

        sites = sly_data.get(key.SITES) or []
        if not sites:
            return (
                "Error: nothing has been surveyed, so no corridor exists to build. Call "
                "survey_airport_sites, then rank_corridors, then name a corridor here."
            )

        routes = sly_data.get(key.ROUTES) or []
        wanted = corridor_key(corridor_id)
        corridor = next(
            (candidate for candidate in corridors_from_sites(sites, routes)
             if corridor_key(candidate["corridor_id"]) == wanted),
            None,
        )
        if corridor is None:
            return (
                f"Error: {corridor_id} is not a corridor on offer. Call rank_corridors and use a "
                "corridor_id exactly as it comes back. A pair that is already flown is not "
                "offered a second time."
            )

        by_id = {str(site["site_id"]): site for site in sites}
        ends = [by_id[site_id] for site_id in corridor["sites"] if site_id in by_id]
        if len(ends) != 2:
            return (
                f"Error: the survey no longer holds both ends of {corridor_id}. Call "
                "survey_airport_sites again and re-rank before building."
            )

        actions = [
            envelope.action(
                "build_airport",
                x=int(site["x"]), y=int(site["y"]), airport_type=int(site["airport_type"]),
            )
            for site in ends
        ]
        # Checked against the engine's own manifest before a game day is spent finding out.
        problems = envelope.check(actions)
        if problems:
            return f"Error: the corridor cannot be staged as written: {'; '.join(problems)}"

        plan = Plan(sly_data)
        repeats = _repeats_this_tool_staged(plan, actions)
        if repeats:
            return (
                f"Error: {'; '.join(repeats)}. Nothing was staged. Pick a different corridor "
                "rather than sending the same refused build again."
            )

        # A free query, and the only anchor confirm_airports has for telling which stations are
        # new. Reading it after the commit cannot separate these airports from any other.
        try:
            existing = [int(station["id"]) for station in await gateway.query("get_stations") or []]
        except (httpx.HTTPError, QueryRefused) as exception:
            # Caught here rather than by the shared guard because the actions are already on the
            # plan by this point, and a guard that only wrote an Error string would leave two
            # airports staged for a corridor whose anchor was never recorded.
            del plan.actions[-len(actions):]
            return (
                f"Error: nttd did not answer get_stations ({exception}), so the corridor was not "
                "staged. Nothing has changed; try again."
            )

        sly_data.setdefault(key.DECISIONS, []).append({
            "kind": CORRIDOR_INTENT,
            "corridor_id": corridor["corridor_id"],
            "towns": corridor["towns"],
            "airport_types": corridor["airport_types"],
            "distance": corridor["distance"],
            "sites": [
                {
                    "site_id": str(site["site_id"]),
                    "town": str(site["town"]),
                    "x": int(site["x"]),
                    "y": int(site["y"]),
                    "airport_type": int(site["airport_type"]),
                }
                for site in ends
            ],
            "stations_before": existing,
            "confirmed": False,
        })

        return {
            "staged": corridor["corridor_id"],
            "towns": corridor["towns"],
            "airports": corridor["airports"],
            "distance": corridor["distance"],
            "takes_big_planes": corridor["takes_big_planes"],
            "plan_now": plan.describe(),
            "game_days_when_committed": 1,
            "next": "Nothing is built yet. commit_plan submits the whole plan as one step, which "
                    "is one game day for both airports together. Call confirm_airports straight "
                    "after it: that is what proves each airport landed in the town it was meant "
                    "to serve.",
        }


def _repeats_this_tool_staged(plan: Plan, actions: list[dict[str, Any]]) -> list[str]:
    """Anything this call would re-send that the game has already refused.

    The plan may hold entries from other tools, and one of those may itself be a repeat that
    is not this tool's business, so only the lines that appear once these actions are staged
    are reported. If there are any, the actions come straight back off the plan.

    The check exists because one measured run submitted the same refused action 35 times.
    """
    before = set(plan.already_refused())
    plan.add(*actions)
    repeats = [line for line in plan.already_refused() if line not in before]
    if repeats:
        del plan.actions[-len(actions):]
    return repeats

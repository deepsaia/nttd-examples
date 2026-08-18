"""Let the world run, for as long as the network decides.

Time is a decision the agent owns, not a constant in a runner, because building takes a moment
and earning takes months. A company cannot tell whether the route it just built works until its
vehicles have had time to move, and it cannot tell whether a corridor is saturated until cargo
has had time to pile up.

The measured shape of that wait, from the best air run:

* about **10 days** to see a vehicle leave its depot and get under way
* about **30 days** to see a route start earning at all; `cargo_delivered_total` stayed at
  exactly 0 until day 73, and the far end of a 289-tile trunk did not see its first aircraft
  until day 43
* about **90 days** to see whether a route pays, which is the only horizon on which a decision
  to sell or re-point is worth making

So the useful loop is waves: commit to something, let a stretch of days pass, then look again.
Nothing may be judged a failure inside the first stretch.

What changed over the stretch comes back with it, read from the snapshot each step already
returns rather than from a second GET afterwards. A step that reports `terminated` ends the
wait and says so: the run is over and nothing further can be built or bought.
"""

from __future__ import annotations

from typing import Any

import httpx
from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import counting
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.observation import our_company, our_vehicles, world
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where `ns` is a package beside the flat tools
    # and the repository above it is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a tool resolving from anywhere on PYTHONPATH.
    from ns import constants as key
    from ns import counting
    from ns.gateway import NttdGateway
    from ns.observation import our_company, our_vehicles, world

# A whole T1 year is 366 days, so no single call can run the entire session out by accident and
# leave the network with nothing left to decide.
MOST_DAYS_AT_ONCE = 120

# Long enough to see a vehicle leave its depot, which is the shortest wait worth taking.
DEFAULT_DAYS = 30


class AdvanceDays(CodedTool):
    """Spend game days doing nothing, and report what moved."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        try:
            gateway = NttdGateway(sly_data)
        except ValueError as problem:
            return f"Error: {problem}. The runner supplies these; nothing here can invent them."

        wanted, note_on_days = counting.counted(
            args.get("days"), DEFAULT_DAYS, most=MOST_DAYS_AT_ONCE
        )

        try:
            # The turn's cached world, which every tool that steps keeps current, so the
            # baseline is what the company looked like when the wait began and not a second
            # copy of the whole map.
            before = await world(gateway, sly_data)
        except httpx.HTTPError as problem:
            return f"Error: could not read the world before waiting ({problem}). No days passed."

        latest = before
        days = 0
        steps = 0
        ended = False
        end_reason = ""
        # Loops on DAYS, not on steps. A step advances the scenario's heartbeat interval,
        # which is one day for the benchmark tiers ("One game day per step", set explicitly in
        # t1_256_flat_1001_stepped.conf) but defaults to THIRTY in scenario_config. Iterating
        # `wanted` times would therefore ask for 30 days and advance 900 on any scenario that
        # takes the default. The step cap is the same number, which is exactly right at one day
        # per step and a backstop if a step ever reports no progress at all.
        while days < wanted and steps < wanted:
            try:
                # An empty action list is a legal step. It lets a day go by without doing
                # anything, which is what waiting is made of.
                result = await gateway.step([])
            except httpx.HTTPError as problem:
                return {
                    "days_passed": days,
                    "session_ended": False,
                    "changed": _difference(before, latest),
                    "why_it_stopped": f"the step was refused by the server: {problem}",
                }
            steps += 1
            days += int(result.get("days_advanced") or 0)
            snapshot = result.get("snapshot")
            if snapshot:
                latest = snapshot
                # Each step observed the world after it moved, so the turn's cache is stale
                # the moment a day passes. Keeping it current here is what stops a later tool
                # in this turn reporting the fleet as it was 90 days ago.
                sly_data[key.SNAPSHOT] = snapshot
            if result.get("terminated"):
                ended = True
                end_reason = result.get("end_reason") or "the run reached its day budget"
                break

        report: dict[str, Any] = {
            "days_passed": days,
            "steps_taken": steps,
            "days_asked_for": wanted,
            "session_ended": ended,
            "changed": _difference(before, latest),
        }
        # Said rather than applied in silence, because a network that asked for half a year and got
        # four months cannot otherwise tell the two apart.
        report.update(counting.said(note_on_days))
        if ended:
            report["end_reason"] = end_reason
            report["note"] = "the run is over; nothing further can be built, bought or waited on"
        return report


def _difference(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What moved while time passed, which is the only reason to have waited.

    Company and fleet come from the shared reader rather than from `companies[0]` and every
    vehicle on the map, because a scenario with a rival in it returns that rival too and
    nothing promises ours is first in the list.
    """
    was = our_company(before)
    now = our_company(after)
    vehicles = our_vehicles(after)
    moving = sum(1 for vehicle in vehicles if vehicle.get("current_speed"))

    changed: dict[str, Any] = {
        "cargo_delivered": _delta(was, now, "cargo_delivered_total"),
        "money": _delta(was, now, "money"),
        "money_now": int(now.get("money") or 0),
        # Out of the whole fleet, because 2 of 9 moving is a problem and 2 of 2 is a route
        # still filling up, and the fraction is the only form that says which.
        "vehicles_moving": f"{moving} of {len(vehicles)}",
    }

    rating = int(now.get("performance_rating") or -1)
    # OpenTTD needs a full quarter of history before it computes a rating at all, and -1 read as
    # a score makes a healthy young company look catastrophic.
    changed["rating"] = "not computed yet" if rating < 0 else rating

    remaining = (after.get("game") or {}).get("game_days_remaining")
    if remaining is not None:
        # A vehicle bought with sixty days to go is cash converted into a depreciating asset,
        # so the horizon belongs beside the money.
        changed["days_left"] = int(remaining)
    return changed


def _delta(was: dict[str, Any], now: dict[str, Any], field: str) -> int:
    return int(now.get(field) or 0) - int(was.get(field) or 0)

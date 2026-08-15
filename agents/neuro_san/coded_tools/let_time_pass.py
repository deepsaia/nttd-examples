"""Let the world run, for as long as the network decides.

Time is a decision, and it belongs to the agent rather than to a constant in a runner.
Building takes a moment and earning takes months: a company cannot tell whether the route
it just built works until vehicles have had time to move, and it cannot tell whether a
network is saturated until cargo has had time to pile up.

A step advances one game day and a T1 run is 366 of them, so the useful shape is waves:
commit to something, let a stretch of days pass, then look again. How long a stretch is a
judgement about what was just done. Ten days is enough to see whether a vehicle left its
depot; ninety is what it takes to see whether a route pays.

Returns what changed over the stretch, so the answer is worth having on its own rather than
needing a separate read afterwards, and says plainly when the session has ended.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway

# A whole T1 year, so no single call can run the entire run out by accident and leave the
# network with nothing left to decide.
MOST_DAYS_AT_ONCE = 120


class LetTimePass(CodedTool):
    """Advance the game, and report what moved."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
        wanted = max(1, min(int(args.get("days") or 30), MOST_DAYS_AT_ONCE))

        before = await gateway.observe()
        played = 0
        for _ in range(wanted):
            try:
                # An empty action list is a legal step: it lets a day go by without doing
                # anything, which is what waiting is.
                await gateway.act([])
            except Exception:
                # The session closes itself when it reaches its tier's day budget, and a
                # step refused after that is the run finishing rather than a failure.
                return {
                    "days_passed": played,
                    "session_ended": True,
                    "note": "the run is over; nothing further can be built or bought",
                }
            played += 1

        after = await gateway.observe()
        return {
            "days_passed": played,
            "session_ended": False,
            "changed": _difference(before, after),
        }


def _difference(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What moved while time passed, which is the only reason to have waited."""
    was = (before.get("companies") or [{}])[0]
    now = (after.get("companies") or [{}])[0]

    def delta(field: str) -> int:
        return int(now.get(field) or 0) - int(was.get(field) or 0)

    moving = sum(1 for v in (after.get("vehicles") or []) if v.get("current_speed"))
    return {
        "cargo_delivered": delta("cargo_delivered_total"),
        "money": delta("money"),
        "rating_now": now.get("performance_rating"),
        "vehicles_moving": f"{moving} of {len(after.get('vehicles') or [])}",
    }

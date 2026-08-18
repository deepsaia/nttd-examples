"""Write down what was decided and why, so a later turn can tell whether it worked.

The sub-agents have no chat continuity at all: only the front man's history survives a turn, and
everything else is rebuilt from tools. Without a written record every turn re-derives its
strategy from the same observation and arrives somewhere slightly different, which is how a run
ends up with four half-built corridors instead of two finished ones.

A decision without a review date is a wish. `review_in_days` turns "buy two more aircraft for
the Tonwood trunk" into something a later turn can check: the record comes back flagged once its
date has passed, along with the reason, so the turn that looks at it knows what it was supposed
to have achieved. About 90 days is the honest interval for anything about whether a route pays;
10 days is right for whether a vehicle actually left its depot.

The game date is recorded rather than the wall clock. `game_date` is days since year 0, so
comparing dates is subtraction and there is no calendar to get wrong.

The list is shared with the build intents a `plan_` tool writes before its commit, so the trim
here is kind-aware: an unconfirmed intent is never evicted, however many decisions follow it.
"""

from __future__ import annotations

from typing import Any

import httpx
from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import counting
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.observation import world
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where `ns` is a package beside the flat tools
    # and the repository above it is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a tool resolving from anywhere on PYTHONPATH.
    from ns import constants as key
    from ns import counting
    from ns.gateway import NttdGateway
    from ns.observation import world

# How many decisions to carry. Enough to see the shape of a strategy, few enough that the one
# that matters now is not buried under an opening move from day 12.
DECISIONS_KEPT = 20

# How many to hand back. A wall of history is a way of saying nothing.
DECISIONS_RETURNED = 6


class NoteDecision(CodedTool):
    """Record one decision, and report which earlier ones are now due for review."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        decision = str(args.get("decision") or "").strip()
        because = str(args.get("because") or "").strip()
        if not decision or not because:
            return (
                "Error: a decision needs both what was decided and why. The reason is the part "
                "a later turn uses to judge whether it worked; without it the record cannot be "
                "acted on."
            )

        try:
            gateway = NttdGateway(sly_data)
        except ValueError as problem:
            return f"Error: {problem}. The runner supplies these; nothing here can invent them."

        try:
            today = await _game_date(gateway, sly_data)
        except httpx.HTTPError as problem:
            return f"Error: could not read the game date ({problem}). Nothing was recorded."

        review_in = counting.whole(args.get("review_in_days"))
        record: dict[str, Any] = {
            "decision": decision,
            "because": because,
            "on_day": today,
        }
        if review_in is not None and review_in > 0:
            record["review_on_day"] = today + review_in

        decisions: list[dict[str, Any]] = sly_data.setdefault(key.DECISIONS, [])
        decisions.append(record)
        decisions[:] = _kept(decisions)

        due = [
            entry for entry in decisions
            if entry is not record and counting.whole(entry.get("review_on_day")) is not None
            and int(entry["review_on_day"]) <= today
        ]
        report: dict[str, Any] = {
            "recorded": record,
            "game_day": today,
            "recent_decisions": decisions[-DECISIONS_RETURNED:],
        }
        if due:
            report["due_for_review"] = due
            report["what_to_do"] = (
                "these were meant to be checked by now. Look at what each one was for and say "
                "whether it worked before deciding anything new."
            )
        return report


async def _game_date(gateway: NttdGateway, sly_data: dict[str, Any]) -> int:
    """Today, in days since year 0.

    Taken from the turn's shared world rather than a read of its own. The whole map is a large
    thing to fetch for one integer, and the date does not move inside a turn: only a step
    advances it, and the tools that step keep the cache current.
    """
    return int(((await world(gateway, sly_data)).get("game") or {}).get("game_date") or 0)


def _kept(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The trim, which counts only plain decisions and never evicts an unconfirmed intent.

    plan_build_corridor writes its pending corridor into this same list and confirm_airports
    reads it back to prove each airport landed in the town it was built for. A trim that counted
    every record deleted that intent on the twenty-first decision of a long run, and the check it
    feeds is the one that catches a misplaced airport, the failure that cost a run 55 rating
    points, 118 against 173. An intent stops being protected the moment it is confirmed.
    """
    plain = [entry for entry in decisions if not _is_pending_intent(entry)]
    evicted = {id(entry) for entry in plain[:-DECISIONS_KEPT]}
    return [entry for entry in decisions if id(entry) not in evicted]


def _is_pending_intent(entry: dict[str, Any]) -> bool:
    """Whether this record is a staged intent still waiting for the tool that confirms it."""
    return bool(entry.get("kind")) and not entry.get("confirmed")

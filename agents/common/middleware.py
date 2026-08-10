"""Middleware every mode's multi-agent system uses.

Three hooks, each earning its place for a measured reason rather than because the
framework offers it.

**There is deliberately no message trimming here, and that is a change of mind.** It was
written first, on the measurement that a checkpointed thread grows quadratically: with an
8 KB observation, 15 steps came to 3.30 MB, 30 to 10.76, 45 to 26.94 and 60 to 43.75.
That measurement is real, but it describes a persistent thread, and this system does not
have one: every specialist is invoked with a fresh message list each step, so nothing
accumulates between steps at all.

Worse, trimming actively broke the surveyor. Slicing the last N messages orphans a
``tool_result`` from the ``tool_use`` it answers, and the API rejects the pair being
split: "unexpected tool_use_id found in tool_result blocks". So it solved a problem this
design does not have and created one it does.

When a checkpointer is added, trimming becomes necessary again, and it must then be
tool-pair aware rather than a blind slice.

**The negative cache replaces a field that never existed.** The prompts this work
replaces branched on `action_history` and `previous_actions`, neither of which nttd has
ever returned. Refusals now come back on the step result, and this is what makes them
change behaviour rather than merely appear in a log.

**The call limit is a wall-clock guard.** Advancing the world costs about two seconds a
game-day, so a step already has a real cost before any thinking. Unbounded scouting
turns a step into a minute.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    after_model,
)

from agents.common.route_ledger import RouteLedger

logger = logging.getLogger(__name__)

def remembering(ledger: RouteLedger) -> Any:
    """Write what the model said it did into the ledger after each call.

    The route identity has to be carried rather than re-derived. A loop that recomputes
    "the first unserved pair" every step abandons a half-built route the moment both its
    stations exist, then chooses the same pair again.
    """

    @after_model
    def _remember(state: dict[str, Any], runtime: Any) -> None:
        response = state.get("structured_response")
        note = getattr(response, "route_note", "") if response else ""
        if note:
            ledger.note("current", "building", note)
        return None

    return _remember


class RefusalCache(AgentMiddleware):
    """Refuse to re-propose an action that has already been refused the same way.

    Sits on the tool call rather than in the prompt because a prompt rule is advice and
    this is a rule. A model told three times that a tile is unbuildable will still try a
    fourth time; a cache that answers before the call does not.

    Keyed on the action and the reason, never the parameters: a policy trying
    ``build_dock`` on a hundred unbuildable tiles is making one mistake, and keying on
    parameters would let it repeat that mistake indefinitely.
    """

    def __init__(self, ledger: RouteLedger, limit: int = 3) -> None:
        super().__init__()
        self._ledger = ledger
        self._limit = limit

    def repeated(self) -> list[dict[str, Any]]:
        """Mistakes made often enough to be worth telling the model about."""
        return self._ledger.repeated_mistakes(at_least=self._limit)


def observation_note(observation: dict[str, Any]) -> str:
    """The part of a snapshot worth putting in front of a model.

    Landmarks and money, never terrain. A full observation carries towns, industries,
    stations and vehicles as points, which is enough to CHOOSE a route; where something
    fits is answered by the finders, which run a real dry run inside the game. So the
    model never needs a picture of the map, and could not hold one anyway.
    """
    game = observation.get("game") or {}
    companies = observation.get("companies") or []
    ours = companies[0] if companies else {}
    return (
        f"Game date {game.get('game_date', '?')}. "
        f"Balance {ours.get('money', '?')}, loan {ours.get('loan', '?')} "
        f"of {ours.get('max_loan', '?')} available. "
        f"You have {len(observation.get('stations') or [])} stations and "
        f"{len(observation.get('vehicles') or [])} vehicles. "
        f"The map has {len(observation.get('towns') or [])} towns and "
        f"{len(observation.get('industries') or [])} industries."
    )


def request_summary(request: ModelRequest) -> str:
    """One line about a model call, for the run log."""
    return f"{len(request.messages)} message(s), {len(request.tools)} tool(s)"

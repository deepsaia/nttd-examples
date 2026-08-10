"""What the company has built, and what it has already been refused.

This is the memory that has to survive hundreds of steps, and it is deliberately small:
what routes exist, what stage each is at, and which mistakes not to repeat. Not terrain,
and not observations. Raw observations never enter it, because the point of a ledger is
that it can be re-read cheaply every step.

It exists because of a specific failure mode. A loop that re-derives "the first unserved
pair" from the world every step abandons a half-built route the moment both its stations
exist, then picks the same pair again, then abandons it again. The route identity has to
be carried rather than recomputed, and this is where it is carried.

Backed by LangGraph's ``BaseStore``, so it works with the in-memory store in a test and
a SQLite one in a run without changing here.
"""

from __future__ import annotations

from typing import Any

from langgraph.store.base import BaseStore

from agents.common.schema import Refusal

# One namespace per run per mode, so a rail specialist does not read a road route as its
# own, and two runs never share a ledger.
_ROUTES = "routes"
_REFUSALS = "refusals"


class RouteLedger:
    """The company's build history and its list of things not to try again."""

    def __init__(self, store: BaseStore, run_id: str, mode: str) -> None:
        self._store = store
        self._run = run_id
        self._mode = mode

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def start_route(self, route_id: str, description: str) -> None:
        """Record that a route is being worked on, before it exists in the world.

        Written at the moment of intent rather than on completion. A route recorded only
        when finished is a route that cannot be resumed, which is the whole failure this
        guards against.
        """
        self._store.put(
            (self._run, self._mode, _ROUTES),
            route_id,
            {"description": description, "stage": "planned", "notes": []},
        )

    def note(self, route_id: str, stage: str, note: str) -> None:
        """Advance a route and say what happened."""
        item = self._store.get((self._run, self._mode, _ROUTES), route_id)
        value: dict[str, Any] = dict(item.value) if item else {
            "description": "", "stage": "planned", "notes": [],
        }
        value["stage"] = stage
        if note:
            value["notes"] = [*value.get("notes", []), note][-10:]
        self._store.put((self._run, self._mode, _ROUTES), route_id, value)

    def unfinished(self) -> list[dict[str, Any]]:
        """Routes started and not yet earning, newest last.

        What the orchestrator asks before choosing what to do: an unfinished route is
        almost always more valuable than a new one, because a half-built route earns
        nothing at all while costing what it already cost.
        """
        return [
            {"route_id": item.key, **item.value}
            for item in self._store.search((self._run, self._mode, _ROUTES))
            if item.value.get("stage") != "earning"
        ]

    def summary(self) -> str:
        """The ledger as prompt text, short enough to include every step."""
        routes = list(self._store.search((self._run, self._mode, _ROUTES)))
        if not routes:
            return "No routes started yet."
        lines = [
            f"- {item.key} [{item.value.get('stage', '?')}] "
            f"{item.value.get('description', '')}"
            for item in routes
        ]
        return "Routes so far:\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # Refusals
    # ------------------------------------------------------------------

    def remember_refusal(self, refusal: Refusal) -> None:
        """Record a refusal so the same mistake is not made twice."""
        item = self._store.get((self._run, self._mode, _REFUSALS), refusal.key())
        count = (item.value.get("count", 0) if item else 0) + 1
        self._store.put(
            (self._run, self._mode, _REFUSALS),
            refusal.key(),
            {"action": refusal.action, "error": refusal.error, "count": count},
        )

    def times_refused(self, refusal: Refusal) -> int:
        """How often this exact mistake has already been made."""
        item = self._store.get((self._run, self._mode, _REFUSALS), refusal.key())
        return item.value.get("count", 0) if item else 0

    def repeated_mistakes(self, at_least: int = 2) -> list[dict[str, Any]]:
        """Refusals that have happened more than once.

        Surfaced to the model rather than merely counted. One refusal is information;
        the same refusal three times is a policy stuck in a loop, and it should be told
        so in those words.
        """
        return [
            item.value
            for item in self._store.search((self._run, self._mode, _REFUSALS))
            if item.value.get("count", 0) >= at_least
        ]

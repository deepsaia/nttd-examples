"""The nttd sessions a runner could attach to, read from the server rather than guessed at.

A contestant knows their session by what it is, "the T1 stepped one I started ten minutes ago",
and not by its id. The id is a date and two words. So this asks the server what exists and
describes each one by the things that distinguish it: the scenario, whether it is scored, how
long the run is and whether it has started.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

# Statuses a runner can still usefully attach to. A session becomes attachable the moment it is
# started, and stops being so once it has ended: the result is already written by then and
# nothing a runner does can change it.
OPEN_STATUSES = frozenset({"active", "pending"})

# The prefix `nttd benchmark` puts on a scenario id. Stripped for display only, because
# "benchmark-t1-256-flat-1001-stepped" says "benchmark" twice on a screen that already said it.
_SCENARIO_PREFIX = "benchmark-"


@dataclass(frozen=True)
class Session:
    """One session, reduced to what a person needs in order to recognise it."""

    session_id: str
    scenario: str
    status: str
    running: bool
    runtime_mode: str
    game_days: int
    scored: bool
    agent_companies: int
    created_at: str

    @property
    def attachable(self) -> bool:
        """Whether a runner can still play it.

        `running` is the session manager's live view and `status` is what the database last
        recorded. They disagree while a session is starting or has just died, and either one
        being positive is enough to be worth offering: the worst case is a clear refusal from
        the server a moment later, which beats hiding a session the contestant just created.
        """
        return self.running or self.status in OPEN_STATUSES

    @property
    def has_contestant(self) -> bool:
        """Whether anything can play it at all.

        A session started without --agent-companies has no participant token, so there is no
        company for a runner to be. It is a legitimate thing to create, and a dead end here.
        """
        return self.agent_companies > 0


def fetch(api_url: str, timeout: float = 10.0) -> list[Session]:
    """Every session the server knows, newest first."""
    reply = httpx.get(f"{api_url}/v1/operator/admin/sessions", timeout=timeout)
    reply.raise_for_status()
    return [_read(record) for record in reply.json().get("sessions") or []]


def token_for(api_url: str, session_id: str, timeout: float = 10.0) -> str:
    """The participant token the server issued for this session, or "" if it has none.

    Offered as the default at the token prompt so the common case is one keystroke. It is not
    used silently: the prompt still shows what it is about to use, because a contestant running
    several sessions needs to see which company they are about to play as.
    """
    try:
        reply = httpx.get(
            f"{api_url}/v1/operator/admin/sessions/{session_id}/participants", timeout=timeout
        )
        reply.raise_for_status()
    except httpx.HTTPError:
        return ""
    participants = reply.json().get("participants") or []
    return str(participants[0].get("token", "")) if participants else ""


def _read(record: dict[str, Any]) -> Session:
    """One server record, flattened.

    The interesting fields live in `settings` under underscore-prefixed keys that nttd writes
    when it creates a session from a benchmark config, so they are absent on a hand-made one.
    Every read here tolerates that.
    """
    settings: dict[str, Any] = record.get("settings") or {}
    scenario = str(settings.get("_scenario_id") or record.get("name") or record["session_id"])
    return Session(
        session_id=str(record["session_id"]),
        scenario=scenario.removeprefix(_SCENARIO_PREFIX),
        status=str(record.get("status") or "unknown"),
        running=bool(record.get("running")),
        runtime_mode=str(settings.get("_runtime_mode") or "unknown"),
        game_days=int(settings.get("_ec_max_heartbeats") or 0),
        scored=str(settings.get("_scored") or "0") == "1",
        agent_companies=int(settings.get("_agent_companies") or 0),
        created_at=str(record.get("created_at") or ""),
    )

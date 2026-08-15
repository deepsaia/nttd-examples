"""The one place this system talks to nttd.

Every coded tool goes through here, for three reasons that each cost a run to learn.

**The envelope is not optional.** An action is `{"action": name, "params": {...}}`, and
params at the top level are refused. One function builds it so no tool can get it wrong.

**Refusals are surfaced verbatim.** nttd's errors carry the coordinate that fixes the bug,
"1 of 71 have no through connection, first at (93,185)". Summarising that loses the only
part worth having.

**Credentials live in sly_data, never in the chat stream.** The session id and participant
token identify the company and are not something a model should see, restate or invent.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

BASE_URL = os.environ.get("NTTD_API_URL", "http://127.0.0.1:8000")
TIMEOUT_SECONDS = float(os.environ.get("NTTD_TIMEOUT_SECONDS", "900"))


class NttdGateway:
    """A participant's view of one session."""

    def __init__(self, sly_data: dict[str, Any]) -> None:
        self._session = str(sly_data.get("session_id") or "")
        self._token = str(sly_data.get("token") or "")
        if not self._session or not self._token:
            raise ValueError(
                "session_id and token must be in sly_data: they address the company and "
                "are deliberately kept out of the chat stream"
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    @property
    def _root(self) -> str:
        return f"{BASE_URL}/v1/participant/sessions/{self._session}"

    async def query(self, action: str, params: dict[str, Any] | None = None) -> Any:
        """A read-only observation. Free: it needs no game ticks and works while paused."""
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            reply = await client.get(
                f"{self._root}/state/gs/query",
                params={"action": action, **(params or {})},
                headers=self._headers,
            )
            reply.raise_for_status()
            return reply.json()

    async def observe(self) -> dict[str, Any]:
        """The whole game state, which is what nttd returns rather than a projection."""
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            reply = await client.get(f"{self._root}/state/full", headers=self._headers)
            reply.raise_for_status()
            return reply.json()

    async def act(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Submit a batch and advance one step. The only call that changes anything.

        Returns one result per action, each carrying the game's own verdict. A `success`
        here means the command was accepted, NOT that a route works: that is what the
        verify tools are for, and conflating the two is the single most expensive mistake
        available in this benchmark.
        """
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            reply = await client.post(
                f"{self._root}/step", json={"actions": actions}, headers=self._headers
            )
            reply.raise_for_status()
            return reply.json()

    @staticmethod
    def envelope(action: str, **params: Any) -> dict[str, Any]:
        """One action, in the shape the step endpoint requires."""
        return {"action": action, "params": params}

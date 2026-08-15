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

import asyncio
import os
from typing import Any

import httpx

BASE_URL = os.environ.get("NTTD_API_URL", "http://127.0.0.1:8000")
TIMEOUT_SECONDS = float(os.environ.get("NTTD_TIMEOUT_SECONDS", "900"))

# How many times to wait out a step that is already in flight. A step takes well under a
# second, so this is generous; it is a backstop behind the lock, not the mechanism.
STEP_RETRIES = 4


class NttdGateway:
    """A participant's view of one session."""

    def __init__(self, sly_data: dict[str, Any]) -> None:
        self._sly = sly_data
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
        """A read-only observation. Free: it needs no game ticks and works while paused.

        POST, with the action as a query parameter and its arguments as the body. A GET
        answers 405 here, which is worth stating because the shape reads like a GET: it is
        read-only, it changes nothing, and it costs no game time. The body is why it is a
        POST, since an action's arguments are structured rather than flat.

        The payload comes back wrapped as {"result": ...}; callers want the contents.
        """
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            reply = await client.post(
                f"{self._root}/state/gs/query",
                params={"action": action},
                json=params or {},
                headers=self._headers,
            )
            reply.raise_for_status()
            return reply.json().get("result")

    async def observe(self) -> dict[str, Any]:
        """The whole game state, which is what nttd returns rather than a projection."""
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            reply = await client.get(f"{self._root}/state/full", headers=self._headers)
            reply.raise_for_status()
            return reply.json()

    def _step_lock(self) -> asyncio.Lock:
        """One lock per session, shared by every tool in this conversation.

        A session takes ONE step at a time: the gate refuses a second with 409 while one is
        in flight, because two steps overlapping would advance the world twice for one
        decision. neuro-san runs tool calls concurrently, so two aircraft bought in the same
        turn, or a purchase overlapping a stretch of time passing, collide.

        Kept in sly_data because that is what every coded tool in one invocation shares, so
        they queue behind each other instead of racing.
        """
        lock = self._sly.get("step_lock")
        if lock is None:
            lock = self._sly["step_lock"] = asyncio.Lock()
        return lock

    async def act(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Submit a batch and advance one day. The only call that changes anything.

        Acting and time passing are the same call in stepped play: nttd executes the batch
        and then advances the world by one step, which is one game day. So a turn that
        builds something has already moved the clock by a day, and an EMPTY batch is how a
        day is spent without doing anything, which is what waiting is made of.

        Returns one result per action, each carrying the game's own verdict. A `success`
        here means the command was accepted, NOT that a route works: that is what the
        verify tools are for, and conflating the two is the single most expensive mistake
        available in this benchmark.
        """
        async with self._step_lock():
            await self._register()
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                route = "actions/submit" if self._sly.get("realtime") else "step"
                # The lock covers one event loop. Waiting briefly and trying again covers
                # the rest: a step already in flight finishes in well under a second, and
                # the alternative is losing a decision the network has already made.
                for attempt in range(STEP_RETRIES):
                    reply = await client.post(
                        f"{self._root}/{route}",
                        json={"actions": actions},
                        headers=self._headers,
                    )
                    if reply.status_code != 409 or "flight" not in reply.text.lower():
                        break
                    await asyncio.sleep(0.4 * (attempt + 1))
                reply.raise_for_status()
            # One entry per action, under action_results. The step also returns the fresh
            # observation, which is how a result is seen: nttd executes the batch, advances
            # a day, and answers with what the world looks like afterwards.
            return reply.json().get("action_results") or []

    async def _register(self) -> None:
        """Declare this company a stepper, once, before the first step.

        A stepped session refuses /step with 409 until the company has registered, because
        the gate has to know who it is waiting for before it will hold the world still. The
        opening observation comes back from the same call.

        Idempotent on the server, but tracked here anyway: it is remembered in sly_data, so
        one registration covers a whole run rather than a call per turn.

        A realtime scenario does not step at all and says so rather than registering. Its
        actions go to actions/submit and the clock runs regardless, which is noted in
        sly_data so the next call takes the right route the first time.
        """
        if self._sly.get("registered") or self._sly.get("realtime"):
            return
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            reply = await client.post(
                f"{self._root}/step/reset", json={}, headers=self._headers
            )
            if reply.status_code == 409 and "stepped" in reply.text.lower():
                self._sly["realtime"] = True
                return
            reply.raise_for_status()
        self._sly["registered"] = True

    @staticmethod
    def envelope(action: str, **params: Any) -> dict[str, Any]:
        """One action, in the shape the step endpoint requires."""
        return {"action": action, "params": params}

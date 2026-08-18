"""The one place these tools talk to nttd.

Shared by all four networks, because none of this is about a transport mode.

Three things it owns, each of which cost a run to learn:

**Credentials never enter the chat stream.** The session id and participant token arrive in
`sly_data`, address the company, and are not something a model should see, restate or invent.

**Refusals are kept, verbatim.** nttd's errors carry the coordinate that fixes the bug, "1 of
71 have no through connection, first at (93,185)". They are also recorded, because a network
that cannot see its own failures repeats them: one measured run submitted the same purchase 35
times with the same error.

**One step at a time, and the lock is not in sly_data.** A session takes one step at a time and
the gate refuses a second with 409. neuro-san runs tool calls concurrently, so they must queue.
The lock lives in a module dictionary rather than in `sly_data`, because everything in
`sly_data` has to be serialisable to cross back to the client and a live asyncio.Lock is not.
Putting it there would have forced the whole allow-list to stay closed, which is what made
cross-turn memory die in the first place.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

try:
    # As part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns import constants as key
except ImportError:
    # As flat siblings, which is how neuro-san loads coded tools when
    # AGENT_TOOL_PATH_ONLY is true. Both spellings are needed and the foundation was
    # the one place that had only one.
    import constants as key

BASE_URL = os.environ.get("NTTD_API_URL", "http://127.0.0.1:8000")
TIMEOUT_SECONDS = float(os.environ.get("NTTD_TIMEOUT_SECONDS", "900"))

# A step takes well under a second, so waiting one out is generous. This is a backstop behind
# the lock for anything the lock cannot cover, such as a second event loop.
STEP_RETRIES = 4

# Per session, in this process. Not in sly_data: see the module docstring.
_LOCKS: dict[str, asyncio.Lock] = {}

# How many refusals to carry. Enough to see a pattern, few enough that the one that matters
# now is not buried under an old one.
REFUSALS_KEPT = 12


class QueryRefused(RuntimeError):
    """The GameScript answered a query with success false and a reason.

    Its own class because it is not a transport failure and must not be caught alongside one:
    an unreachable server is a different problem from a query the engine declined, and the
    engine's reason is the useful part.
    """


class NttdGateway:
    """A participant's view of one session."""

    def __init__(self, sly_data: dict[str, Any]) -> None:
        self._sly = sly_data
        self._session = str(sly_data.get(key.SESSION_ID) or "")
        self._token = str(sly_data.get(key.TOKEN) or "")
        if not self._session or not self._token:
            raise ValueError(
                f"{key.SESSION_ID} and {key.TOKEN} must be in sly_data: they address the "
                "company and are deliberately kept out of the chat stream"
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    @property
    def _root(self) -> str:
        return f"{BASE_URL}/v1/participant/sessions/{self._session}"

    def _lock(self) -> asyncio.Lock:
        lock = _LOCKS.get(self._session)
        if lock is None:
            lock = _LOCKS[self._session] = asyncio.Lock()
        return lock

    async def query(self, action: str, params: dict[str, Any] | None = None) -> Any:
        """A read-only observation. Free: it costs no game day and works while paused.

        POST, with the action as a query parameter and its arguments as the body. A GET answers
        405 here, which is worth stating because the call reads like a GET. The payload arrives
        wrapped as {"result": ...} and callers want the contents.
        """
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            reply = await client.post(
                f"{self._root}/state/gs/query",
                params={"action": action},
                json=params or {},
                headers=self._headers,
            )
            reply.raise_for_status()
            answer = reply.json()

        # A GameScript query can answer {success: false, error: "..."} with HTTP 200, and
        # reading only "result" turns that into None. A caller then sees an empty list and
        # reports "no sites found" when the truth was a refused query with a reason attached.
        if isinstance(answer, dict) and answer.get("success") is False:
            raise QueryRefused(str(answer.get("error") or f"{action} was refused"))
        return answer.get("result") if isinstance(answer, dict) else answer

    async def observe(self) -> dict[str, Any]:
        """The whole game state, which is what nttd returns rather than a projection."""
        return await self._get("state/full")

    async def situation(self) -> dict[str, Any]:
        """What the company has, earns and is getting wrong, computed by the engine.

        Used in preference to deriving the same thing here. nttd's own reason: "an agent that
        derives these from a raw observation spends a model call on counting and can get it
        wrong, which is a way for a good decision-maker to look bad at a benchmark meant to
        measure judgement." Its problems list also declines to call a vehicle broken for
        loading at a station, which a hand-rolled version did, reporting a healthy fleet as a
        wall of faults.
        """
        return await self._get("state/situation")

    async def _get(self, path: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            reply = await client.get(f"{self._root}/{path}", headers=self._headers)
            reply.raise_for_status()
            return reply.json()

    async def step(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        """Submit a batch, advance one game day, and return everything the game answered.

        Acting and time passing are the same call: nttd executes the batch and then advances
        one step, which is one day. An EMPTY batch is therefore how a day is spent doing
        nothing, which is what waiting is made of.

        The whole StepResult is returned, not just the per-action verdicts, because the fresh
        observation and the end-of-run flag come back in the same reply and fetching them again
        is a second round trip for something already in hand.
        """
        async with self._lock():
            await self._register()
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                for attempt in range(STEP_RETRIES):
                    reply = await client.post(
                        f"{self._root}/step", json={"actions": batch}, headers=self._headers,
                    )
                    if reply.status_code == 404:
                        # The session closed itself at its day budget. That is the run
                        # finishing, not an error to retry.
                        self._sly[key.ENDED] = True
                        return {"terminated": True, "action_results": []}
                    if reply.status_code != 409 or "flight" not in reply.text.lower():
                        break
                    await asyncio.sleep(0.4 * (attempt + 1))
                reply.raise_for_status()
                result = reply.json()

        self._remember(batch, result.get("action_results") or [])
        if result.get("terminated"):
            self._sly[key.ENDED] = True
        return result

    def _remember(self, batch: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
        """Keep what was refused, so the same mistake is not made a second time."""
        refusals = self._sly.setdefault(key.REFUSALS, [])
        for entry, result in zip(batch, results, strict=False):
            if (result or {}).get("status") == "success":
                continue
            refusals.append({
                "action": entry.get("action"),
                "params": entry.get("params"),
                "error": (result or {}).get("error") or "refused",
                "error_name": (result or {}).get("error_name") or "",
            })
        del refusals[:-REFUSALS_KEPT]

    async def _register(self) -> None:
        """Declare this company a stepper, once, before the first step.

        A stepped session refuses /step with 409 until the company has registered, because the
        gate has to know who it is waiting for before it will hold the world still. Measured:
        without this every first action failed with a 409 that reads like a bug.

        Realtime is not handled by pretending: a scenario that does not step says so, and these
        networks are built for stepped play where deliberation is free.
        """
        if self._sly.get(key.REGISTERED):
            return
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            reply = await client.post(f"{self._root}/step/reset", json={}, headers=self._headers)
            reply.raise_for_status()
        self._sly[key.REGISTERED] = True

"""Shared HTTP + WebSocket transport for nttd runners.

Every call goes to the **participant tier**, ``/v1/participant/sessions/{id}/...``, and
carries the participant token issued when the session started. That token is also what
says which company you are, so no method takes a ``company_id``: the server resolves it
and overrides anything a request body claims, which is what stops one entrant acting for
another.

This talks HTTP and nothing else. The ``nttd`` package is not imported here or anywhere
in this repository, so an entry written in another language is on equal footing.

**Two modes, and a stepped run is the one to reach for.** In real time the world runs
while you think, so a slow policy is punished for being slow. In stepped mode the world
is paused between steps, so deliberation costs zero game-days. RL, ES and multi-agent
entries all want stepped.

A note on what this file used to be. It targeted ``/sessions/{id}/agents/connect`` and
unprefixed session routes, neither of which exist: nttd moved to tiered prefixes and
dropped agent registration in favour of the token. Every test still passed, because they
mock the tool layer. ``tests/test_route_contract.py`` is the answer to that.
"""

import asyncio
import json as _json
import logging
import uuid
from collections import deque
from typing import Any

import requests
import websockets

logger = logging.getLogger(__name__)

_WS_RECONNECT_BASE = 2.0
_WS_RECONNECT_CAP = 30.0

# The tier prefix. Participant is the only tier a contestant may reach: operator routes
# author scenarios and are refused outright in a scored run.
PARTICIPANT_PREFIX = "/v1/participant"


class NttdClient:
    """HTTP + WebSocket transport for one company in one nttd session."""

    def __init__(
        self,
        base_url: str,
        session_id: str,
        token: str,
        agent_id: str = "runner",
        snapshot_history_len: int = 5,
    ) -> None:
        """
        Args:
            base_url: Where nttd is listening, for example ``http://127.0.0.1:8000``.
            session_id: The session to play.
            token: The participant token, from ``nttd session attach`` or
                ``logs/sessions/<id>/participants.json``. It identifies the company, so
                there is no company argument anywhere in this class.
            agent_id: A name for the WebSocket channel and your own logs. nttd does not
                register it or check it.
            snapshot_history_len: How many heartbeat snapshots to keep for trends.
        """
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.token = token
        self.agent_id = agent_id
        self._history: deque[dict[str, Any]] = deque(maxlen=snapshot_history_len)
        self._snapshot_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self._ws_task: asyncio.Task[None] | None = None

    @property
    def _session_url(self) -> str:
        return f"{self.base_url}{PARTICIPANT_PREFIX}/sessions/{self.session_id}"

    @property
    def _headers(self) -> dict[str, str]:
        """The token, on every request. nttd accepts it either as this header or as
        ``Authorization: Bearer``; the explicit one wins."""
        return {"X-Participant-Token": self.token}

    # ------------------------------------------------------------------
    # Stepped mode
    # ------------------------------------------------------------------

    def reset(self) -> dict[str, Any]:
        """Enter stepped mode and return the opening observation.

        Pauses the world and registers you as the stepper. Idempotent: calling it again
        re-pauses and re-observes without restarting the run, so it is safe on reconnect.
        A ``step`` without this first is refused with a 409, deliberately, because
        serving it would advance a world you did not know you had started.

        It does **not** begin a new episode. A session is a run, and rewinding one in
        place would leave the action log describing two runs as though they were one, so
        a fresh episode means a fresh session.
        """
        resp = requests.post(
            f"{self._session_url}/step/reset", headers=self._headers, timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def step(self, actions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Flush a batch of actions, advance the world once, and observe.

        Blocks until the world has moved and been re-observed, so you never have to
        guess when your actions took effect. There is no decision deadline: take as long
        as you like between calls.

        Args:
            actions: ``[{"action": name, "params": {...}}]``. An empty list is a
                legitimate step, because waiting is a move.

        Returns:
            ``snapshot``, ``step``, ``days_advanced``, ``terminated`` and ``end_reason``.
            Stop when ``terminated`` is true; further steps will not advance a finished
            run.
        """
        resp = requests.post(
            f"{self._session_url}/step",
            headers=self._headers,
            json={"actions": actions or []},
            timeout=600,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def get_full_state(self) -> dict[str, Any]:
        """The whole game state. This is what a step returns, and what nttd records."""
        resp = requests.get(
            f"{self._session_url}/state/full", headers=self._headers, timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_compact_snapshot(self) -> dict[str, Any]:
        """A smaller view, for a real-time loop that polls often."""
        resp = requests.get(
            f"{self._session_url}/state/compact", headers=self._headers, timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def get_state(self, slice_name: str) -> Any:
        """One slice of the cached world state: towns, industries, vehicles, stations.

        Named slices rather than a method each, because nttd serves them at
        ``state/<name>`` and a wrapper per slice would be a list to keep in step with
        the server for no gain.
        """
        resp = requests.get(
            f"{self._session_url}/state/{slice_name}",
            headers=self._headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def gs_query(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Ask the GameScript a read-only question, such as ``find_road_depot_spot``."""
        resp = requests.post(
            f"{self._session_url}/state/gs/query",
            headers=self._headers,
            params={"action": action},
            json=params or {},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def action_manifest(self, category: str | None = None) -> dict[str, Any]:
        """The full action manifest: descriptions, parameters, enums.

        Read rather than hardcoded. The manifest is generated from the GameScript, so it
        is the only description of the action surface that cannot drift from it, which is
        what makes it worth building a prompt from.

        Served on the **public** tier because it describes nttd rather than a session,
        so it needs no token and can be read before a run starts.
        """
        resp = requests.get(
            f"{self.base_url}/v1/public/actions",
            params={"category": category} if category else None,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def available_actions(self) -> dict[str, Any]:
        """Which action names this session will accept, grouped by category.

        Names only. For what each one takes, use ``action_manifest``.
        """
        resp = requests.get(
            f"{self._session_url}/actions/available", headers=self._headers, timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Real-time actions
    # ------------------------------------------------------------------

    def submit_action(
        self, action_type: str, params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submit one action outside stepped mode.

        ``company_id`` is sent because the envelope requires it, and is then overridden
        from the token, so it cannot be used to act for anybody else.
        """
        resp = requests.post(
            f"{self._session_url}/actions/submit",
            headers=self._headers,
            json=self._envelope(action_type, params),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def submit_actions_batch(
        self, actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Submit several actions in order. Results come back in the same order."""
        resp = requests.post(
            f"{self._session_url}/actions/submit-batch",
            headers=self._headers,
            json=[
                self._envelope(a["action_type"], a.get("params")) for a in actions
            ],
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _envelope(
        self, action_type: str, params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "action_id": f"act_{uuid.uuid4().hex[:8]}",
            "company_id": 0,
            "action_type": action_type,
            "parameters": params or {},
            "mode": "atomic",
        }

    # ------------------------------------------------------------------
    # Reporting what nttd cannot see
    # ------------------------------------------------------------------

    def report(self, **fields: Any) -> dict[str, Any]:
        """Declare the model, token spend and cost nttd has no way to observe.

        It runs no model, so these land in the result marked as reported rather than
        measured. Saying nothing is honest and leaves the cost column blank; reporting
        zero is a claim that the run was free.
        """
        resp = requests.post(
            f"{self._session_url}/report",
            headers=self._headers,
            json=fields,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # WebSocket, for the real-time heartbeat loop
    # ------------------------------------------------------------------

    async def start_ws(self) -> None:
        """Start the WebSocket listener (reconnects with backoff)."""
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def stop(self) -> None:
        """Cancel the WebSocket listener. There is nothing to unregister."""
        if self._ws_task is not None:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

    async def _ws_loop(self) -> None:
        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        backoff = _WS_RECONNECT_BASE
        while True:
            try:
                async with websockets.connect(
                    f"{ws_url}/ws/{self.session_id}/{self.agent_id}",
                    ping_interval=None,
                ) as ws:
                    backoff = _WS_RECONNECT_BASE
                    logger.info("WebSocket connected for %s", self.agent_id)
                    async for raw in ws:
                        self._receive(_json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("WebSocket dropped (%s), retrying in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _WS_RECONNECT_CAP)

    def _receive(self, msg: dict[str, Any]) -> None:
        """Keep the newest snapshot, dropping any the runner did not collect.

        A queue of one, replaced rather than appended to: a runner that fell behind
        should act on what is true now, not work through a backlog of stale worlds.
        """
        if msg.get("type") not in {"heartbeat", "snapshot"}:
            return
        self._history.append(msg)
        if self._snapshot_queue.full():
            self._snapshot_queue.get_nowait()
        self._snapshot_queue.put_nowait(msg)

    async def wait_for_snapshot(self) -> dict[str, Any]:
        """Block until the next snapshot arrives over the WebSocket."""
        return await self._snapshot_queue.get()

    def get_snapshot_history(self, n: int = 5) -> list[dict[str, Any]]:
        """The last N snapshots, newest first."""
        return list(reversed(list(self._history)[-n:]))

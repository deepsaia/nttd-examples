"""The smallest runner that plays a real nttd session.

No LLM and no framework, so it runs without an API key and is the thing to read first
if you are writing an entry. It is also the reference for the four submission outcomes,
which is the part contestants most often get wrong: `failed` means the game refused a
legal request, `rejected` means the request was never legal, and retrying the second one
forever is the classic bug.

Usage:
    python examples/minimal_runner.py --session ses_... --token pt_... --steps 5
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

import requests

logger = logging.getLogger("minimal_runner")

TIMEOUT_OBSERVE = 60
TIMEOUT_ACT = 180

# The server refuses a submission over this, whole, rather than part-executing it.
MAX_ACTIONS_PER_SUBMISSION = 15


class MinimalRunner:
    """Observe, decide, submit. The loop nttd does not run for you."""

    def __init__(self, base_url: str, session_id: str, token: str) -> None:
        self._participant = f"{base_url}/v1/participant/sessions/{session_id}"
        self._headers = {"X-Participant-Token": token}

    def observe(self) -> dict[str, Any]:
        """Fetch the full entitled game state.

        Deliberately unfiltered: deciding what matters is part of the task, so the
        filtering belongs in your code.
        """
        response = requests.get(
            f"{self._participant}/state/full", headers=self._headers, timeout=TIMEOUT_OBSERVE,
        )
        response.raise_for_status()
        return response.json()

    def query(self, action: str, **params: Any) -> Any:
        """Run a read-only GameScript query, for things a snapshot does not carry."""
        response = requests.post(
            f"{self._participant}/state/gs/query",
            params={"action": action},
            headers=self._headers,
            json=params,
            timeout=TIMEOUT_ACT,
        )
        response.raise_for_status()
        return response.json().get("result")

    def submit(self, action_type: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Submit one action and return its result envelope."""
        response = requests.post(
            f"{self._participant}/actions/submit",
            headers=self._headers,
            timeout=TIMEOUT_ACT,
            json={
                "action_id": f"{action_type}-{id(parameters)}",
                "action_type": action_type,
                # Ignored by the server: the company is derived from the token, so this
                # cannot be spoofed and does not need to be right.
                "company_id": 0,
                "parameters": parameters,
            },
        )
        response.raise_for_status()
        return response.json()

    def report(self, models: list[dict[str, Any]]) -> None:
        """Tell nttd what your run cost, since it runs no model and cannot observe it.

        Recorded as contestant-reported and flagged unverified. Repeated calls
        accumulate, so report each cycle as your provider returns usage.
        """
        requests.post(
            f"{self._participant}/report",
            headers=self._headers,
            timeout=TIMEOUT_OBSERVE,
            json={
                "nttd_framework": "minimal_runner",
                "participant_type": "scripted",
                "models": models,
            },
        )


TARGET_LOAN = 200_000


def decide(state: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """A deliberately trivial policy: borrow up to a target, then stop.

    Replace this. Everything above it is plumbing that does not change; this is the part
    that is your entry.

    Note that a company starts with a loan already drawn, not at zero, which is the kind
    of thing to check against a real observation rather than assume.
    """
    mine = next((c for c in state.get("companies", []) if c.get("id") == 0), None)
    if mine is None:
        return []

    loan = mine.get("loan", 0)
    if loan < min(TARGET_LOAN, mine.get("max_loan", TARGET_LOAN)):
        return [("set_loan", {"amount": TARGET_LOAN})]
    return []


def apply_changes(state: dict[str, Any], changed: dict[str, Any]) -> None:
    """Fold an action's changed_entities into the local view of your own company.

    This matters more than it looks. In real-time mode `state/full` is served from the
    last GameScript refresh, so it lags: measured at **7.1 seconds** for a `set_loan` to
    appear in a fresh observation. A loop that observes, acts, then observes again will
    read its own pre-action state and submit the same action a second time. The first
    version of this file did exactly that, three times in a row, and every submission
    honestly reported success.

    `changed_entities` on the action result is the immediate, authoritative answer to
    what your action did. Trust it over your next observation.
    """
    mine = next((c for c in state.get("companies", []) if c.get("id") == 0), None)
    if mine is None:
        return
    if "loan" in changed:
        mine["loan"] = changed["loan"]
    if "balance" in changed:
        mine["money"] = changed["balance"]


def play(runner: MinimalRunner, steps: int) -> None:
    """Run the loop, reporting each outcome so the difference is visible."""
    # What we know our own actions changed, kept as an overlay on each fresh observation
    # until the server's view catches up. Without this the loop re-reads its own stale
    # pre-action state and submits again.
    applied: dict[str, Any] = {}

    for step in range(steps):
        state = runner.observe()
        apply_changes(state, applied)

        actions = decide(state)
        if not actions:
            logger.info("step %d: nothing to do", step)
            continue

        if len(actions) > MAX_ACTIONS_PER_SUBMISSION:
            logger.warning(
                "step %d: %d actions exceeds the ceiling of %d, trimming",
                step, len(actions), MAX_ACTIONS_PER_SUBMISSION,
            )
            actions = actions[:MAX_ACTIONS_PER_SUBMISSION]

        for action_type, parameters in actions:
            result = runner.submit(action_type, parameters)
            status = result.get("status")
            if status == "success":
                logger.info("step %d: %s succeeded", step, action_type)
                applied.update(result.get("changed_entities") or {})
                apply_changes(state, applied)
            elif status == "failed":
                # The game refused a legal request. Worth retrying with different
                # parameters: a different tile, or after earning more money.
                logger.info("step %d: %s failed: %s", step, action_type, result.get("error"))
            elif status == "rejected":
                # Never legal for a participant. Retrying is pointless.
                logger.error("step %d: %s rejected: %s", step, action_type, result.get("error"))
            elif status == "blocked":
                logger.warning("step %d: %s hit the action ceiling", step, action_type)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, help="Session id from nttd session attach")
    parser.add_argument("--token", required=True, help="Participant token")
    parser.add_argument("--url", default="http://localhost:8000", help="nttd server URL")
    parser.add_argument("--steps", type=int, default=5, help="How many observe/act cycles")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    runner = MinimalRunner(args.url, args.session, args.token)
    play(runner, args.steps)
    runner.report([{"model": "none", "role": "scripted", "prompt_tokens": 0,
                    "completion_tokens": 0, "total_cost_usd": 0.0}])


if __name__ == "__main__":
    main()

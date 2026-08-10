#!/usr/bin/env python3
"""The smallest complete nttd entry: a stepped run, start to submission.

No model, no framework. It draws a loan and then waits, which is a poor strategy and a
complete one: it plays a whole run and produces a bundle a board will accept. Read it for
the shape, then replace ``decide`` with something that plays.

    uv run python examples/minimal_runner.py --session ses_... --token pt_...

Get the session and token from nttd:

    uv run nttd session create --config config/benchmark/t2_256_flat_1001_stepped.conf
    uv run nttd session start -s ses_... --agent-companies 1
    uv run nttd session attach ses_...

**Why stepped.** The world is paused between steps, so thinking costs zero game-days. A
slow policy is not punished for being slow, which is the only way an LLM and a trained
policy can be compared on what they decide rather than how fast they decide it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from agents.nttd_client import NttdClient

logger = logging.getLogger("minimal")


def decide(observation: dict[str, Any], step: int) -> list[dict[str, Any]]:
    """What to do this step. Replace this.

    Returns a list of ``{"action": name, "params": {...}}``. An empty list is a real
    move: waiting is a decision, and nttd records it as one.

    ``observation`` is the full game state, the same thing nttd writes into
    ``snapshots.parquet``, so nothing a human could see is hidden from you.
    """
    if step == 0:
        # Borrowing first is not strategy. It is the smallest action that demonstrably
        # changes the world, so a reader can watch the loop work.
        return [{"action": "set_loan", "params": {"amount": 100_000}}]
    return []


def play(client: NttdClient, max_steps: int) -> dict[str, Any]:
    """Step until the scenario ends the run, or the safety limit trips.

    The scenario owns the end conditions. A runner that stopped on its own count would
    end somewhere the scenario did not choose, and two runs would then cover different
    amounts of world, so ``max_steps`` is a safety net rather than a plan.
    """
    result = client.reset()
    logger.info("Stepped mode. Opening game date: %s", _game_date(result))

    for step in range(max_steps):
        actions = decide(result["snapshot"], step)
        result = client.step(actions)

        if result.get("terminated"):
            logger.info(
                "Run ended after %s steps: %s",
                result.get("step"), result.get("end_reason") or "no reason given",
            )
            return result

    logger.warning(
        "Stopped at the %d step safety limit with the run still going. The scenario "
        "decides when a run ends, so raise the limit rather than reading this as the end",
        max_steps,
    )
    return result


def _game_date(result: dict[str, Any]) -> Any:
    return (result.get("snapshot") or {}).get("game", {}).get("game_date")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="A minimal stepped nttd runner")
    parser.add_argument("--session", required=True, help="Session id, ses_...")
    parser.add_argument("--token", required=True, help="Participant token, pt_...")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--max-steps", type=int, default=500)
    args = parser.parse_args()

    client = NttdClient(base_url=args.url, session_id=args.session, token=args.token)

    # Declared, never measured. nttd runs no model, so it cannot see what you used or
    # what it cost. Saying nothing is honest and leaves the cost column blank on the
    # board; reporting zero claims the run was free, which for this runner is true.
    client.report(model="none", total_cost_usd=0.0, spend_is_reported=True)

    result = play(client, max_steps=args.max_steps)
    logger.info("Final step %s, game date %s", result.get("step"), _game_date(result))
    logger.info("Now package it: nttd submit --session %s", args.session)
    return 0


if __name__ == "__main__":
    sys.exit(main())

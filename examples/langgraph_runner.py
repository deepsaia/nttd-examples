#!/usr/bin/env python3
"""A LangGraph multi-agent system playing one transport mode in nttd.

    uv sync --extra langgraph
    export ANTHROPIC_API_KEY=...
    uv run python examples/langgraph_runner.py --session ses_... --token pt_... --mode rail

Each mode is its own multi-agent system: a coded orchestrator over four agents, on a base
of Python tools that compute rather than describe.

nttd states the facts. `state/situation` gives money, what is built, what earns, per-route
health and a list of problems; `state/routes` gives which pairs are worth serving; the
finders dry-run the real build to say where something fits. None of that needs a model.

The models exercise judgement: an observer says what matters, a consultant chooses the
objective, a builder turns it into actions, a validator checks the batch makes sense.
Observer, consultant and validator are identical across the four modes, because judging
whether a corridor pays is the same problem whether cargo moves by rail or by ship.

**One company, one batch, one step.** However many agents deliberate, nttd sees a single
stepper. That is what makes the orchestrator an arbitrator rather than a fan-out, and it
is why adding agents cannot buy more actions per step, only better ones.

**Deliberation is free.** The world is paused between steps, so three model calls cost
zero game-days. A slow policy is not punished for being slow, which is the only way this
can be compared with a trained policy on what it decides rather than how fast.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from typing import Any

from agents import action_brief, strategy_loader
from agents.common.route_ledger import RouteLedger
from agents.mas.mode_system import ModeSystem
from agents.nttd_client import NttdClient
from agents.tools import NttdTools

logger = logging.getLogger("mas")

MODEL = "claude-sonnet-5"

# The slice of nttd's surface each mode plays. Narrowed deliberately: the full catalogue
# is around 130 actions, and a road runner handed the rail, marine and aviation
# references as well pays for context it will never call.
CATEGORIES = {
    "road": ("road", "vehicle", "orders", "company", "query"),
    "rail": ("rail", "vehicle", "orders", "company", "query"),
    "water": ("marine", "vehicle", "orders", "company", "query"),
    "air": ("aviation", "vehicle", "orders", "company", "query"),
}


def build_system(client: NttdClient, mode: str, run_id: str) -> ModeSystem:
    """Assemble one mode's multi-agent system.

    The action reference and the strategy are fetched once per run, not per step:
    neither changes mid-session, and paying for them every step would be the largest
    cost in the loop.
    """
    from langgraph.store.memory import InMemoryStore

    return ModeSystem(
        mode=mode,
        tools=NttdTools(client, mode=mode),
        ledger=RouteLedger(InMemoryStore(), run_id=run_id, mode=mode),
        model=MODEL,
        action_reference=action_brief.build(client, categories=CATEGORIES[mode]),
        strategy=strategy_loader.load(mode),
    ).build()


def play(client: NttdClient, system: ModeSystem, max_steps: int) -> dict[str, Any]:
    """Step until the scenario ends the run, or the safety limit trips."""
    result = client.reset()

    for step in range(max_steps):
        batch = system.decide()
        logger.info("Step %d: %s", step, batch.reasoning)

        result = client.step(
            [{"action": a.action, "params": a.params} for a in batch.actions],
        )

        # What the actions actually did, from the step that flushed them. Recorded so
        # the same refusal is not proposed again: a refused action usually changes
        # nothing, so the observation alone cannot tell the system it failed.
        for refusal in system.learn(result.get("action_results") or []):
            logger.warning("  refused %s: %s", refusal.action, refusal.error)

        if result.get("terminated"):
            logger.info("Run ended: %s", result.get("end_reason") or "no reason given")
            return result

    logger.warning("Stopped at the %d step safety limit", max_steps)
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="A LangGraph MAS playing nttd")
    parser.add_argument("--session", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument(
        "--mode", default="road", choices=strategy_loader.MODES,
        help="Which transport mode to play. Road is the easiest to get earning.",
    )
    args = parser.parse_args()

    client = NttdClient(base_url=args.url, session_id=args.session, token=args.token)
    system = build_system(client, args.mode, run_id=uuid.uuid4().hex[:8])

    # Declared because nttd cannot see it. Spend is left unreported rather than guessed:
    # blank on the board is a different and more honest claim than zero.
    client.report(model=MODEL, participant_type="mas", agent_id=f"langgraph-{args.mode}")

    result = play(client, system, max_steps=args.max_steps)
    logger.info("Finished at step %s. Now: nttd submit --session %s",
                result.get("step"), args.session)
    return 0


if __name__ == "__main__":
    sys.exit(main())

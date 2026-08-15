"""Play one nttd session with a neuro-san agent network.

    uv run python -m examples.neuro_san_runner --session <id> --token <token> --steps 40

The loop is deliberately thin, and everything it does is a decision nttd forces rather than
one this file makes. neuro-san holds the agents; the coded tools hold the traps; this holds
the turn.

The session id and token go into `sly_data`, which neuro-san keeps out of the chat stream.
They address the company and are not something a model should see, restate or invent.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

logger = logging.getLogger("nttd.neuro_san")

DEFAULT_NETWORK = "nttd_air"


async def play(session: str, token: str, network: str, steps: int) -> int:
    """Run the network once per step until the budget or the session runs out."""
    from neuro_san.session.direct_agent_session import DirectAgentSession  # noqa: PLC0415

    sly_data = {"session_id": session, "token": token}
    agent = DirectAgentSession(agent_name=network)

    for step in range(1, steps + 1):
        reply = await asyncio.to_thread(
            agent.streaming_chat,
            {
                "user_message": {
                    "type": "AGENT_FRAMEWORK",
                    "text": (
                        "Take the next step in this session. Read the position first, fix "
                        "what is broken before building anything new, and say what you did."
                    ),
                },
                "sly_data": sly_data,
            },
        )
        said = _last_text(reply)
        logger.info("step %d: %s", step, said)
        print(f"  step {step}: {said}")

        # The session ends on its own when the tier's day budget runs out. A step that
        # cannot be taken is the end of the run, not an error to retry.
        if sly_data.get("session_ended"):
            print("  the session has ended")
            break
    return 0


def _last_text(reply: object) -> str:
    """The final thing the network said, out of a stream of messages."""
    text = ""
    for message in reply or []:
        answer = (message or {}).get("response", {}).get("text")
        if answer:
            text = answer
    return text.strip() or "(no answer)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Play nttd with a neuro-san agent network")
    parser.add_argument("--session", required=True, help="Session id from `nttd session attach`")
    parser.add_argument("--token", default=os.environ.get("NTTD_TOKEN", ""), help="Participant token")
    parser.add_argument("--network", default=DEFAULT_NETWORK, help="Which agent network to run")
    parser.add_argument("--steps", type=int, default=40, help="How many steps to take")
    args = parser.parse_args()

    if not args.token:
        parser.error("a participant token is required: --token or NTTD_TOKEN")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return asyncio.run(play(args.session, args.token, args.network, args.steps))


if __name__ == "__main__":
    raise SystemExit(main())

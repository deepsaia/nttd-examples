"""Play one nttd session with a neuro-san agent network, until the world ends.

    uv run python -m examples.neuro_san_play --session <id> --token <token> --network nttd_air

Named for the system it drives. Everything in it is neuro-san specific: it holds a
conversation with an agent network over neuro-san's own client, and a different approach,
scripted or reinforcement learning or anything else, plays the same session through the same
HTTP surface with none of this file.

**What this loop decides: nothing about the game.** It asks the network for another turn
while the session is still open, and stops when the session closes. That is the whole of it.

An earlier version woke the agent every 30 game days, a number lifted from how the game was
played by hand. That is the wrong place for it: judging when to act, and how long to let the
world run before looking again, is part of what the benchmark measures. The network has a
`let_time_pass` tool and makes that call itself. This file only notices that the run is not
over yet.

**Why a loop at all**, rather than one long conversation: a benchmark run is a game year, and
a single turn that tried to play all of it would grow its own context until the model lost
the early part of the run. Successive turns carry `chat_context` forward, so the network
remembers what it decided while each turn stays a manageable size.

Stepped and realtime differ only in who moves the clock. In stepped play the network advances
it with `let_time_pass`; in realtime the clock runs regardless and that tool simply lets time
be observed. The loop is the same either way: keep asking until the session ends.
"""

from __future__ import annotations

import argparse
import logging
import os

import httpx

logger = logging.getLogger("nttd.play")

API_URL = os.environ.get("NTTD_API_URL", "http://127.0.0.1:8000")

TURN = (
    "Take the next decision in this session. Read the position first, fix anything that is "
    "broken before building something new, and let time pass when you need the world to run "
    "before you can judge what you did. Say briefly what you did and why."
)


def _status(session: str) -> dict:
    """What the game says about itself: the run's own view, not this loop's."""
    try:
        reply = httpx.get(f"{API_URL}/v1/public/sessions/{session}/status", timeout=30)
        if reply.status_code == 404:
            return {"ended": True}
        reply.raise_for_status()
        return reply.json()
    except httpx.HTTPError as failure:
        logger.warning("Could not read session status: %r", failure)
        return {"ended": True}


def play(session: str, token: str, network: str, host: str, port: int, turns: int) -> int:
    from neuro_san.client.streaming_input_processor import (  # noqa: PLC0415
        StreamingInputProcessor,
    )
    from neuro_san.session.http_service_agent_session import (  # noqa: PLC0415
        HttpServiceAgentSession,
    )

    agent = HttpServiceAgentSession(
        host=host, port=str(port), agent_name=network, streaming_timeout_in_seconds=1800
    )
    processor = StreamingInputProcessor(session=agent)

    # sly_data addresses the company and is deliberately kept out of the chat stream: it is
    # not something a model should see, restate or invent.
    state: dict = {
        "sly_data": {"session_id": session, "token": token},
        "chat_context": {},
        "last_chat_response": None,
        "user_input": TURN,
    }

    start = _status(session).get("game_date")
    for turn in range(1, turns + 1):
        state["user_input"] = TURN
        state = processor.process_once(state)

        said = (state.get("last_chat_response") or "").strip()
        now = _status(session)
        if now.get("ended") or now.get("status") in ("ended", "archived"):
            print(f"  turn {turn}: {said}")
            print("  the session has ended; the result is written")
            return 0

        today = now.get("game_date")
        played = (today - start) if isinstance(today, int) and isinstance(start, int) else "?"
        print(f"  turn {turn} (day {played}): {said}")

    print(f"  stopped after {turns} turns with the session still open")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Play an nttd session with a neuro-san network")
    parser.add_argument("--session", required=True, help="Session id")
    parser.add_argument("--token", default=os.environ.get("NTTD_TOKEN", ""), help="Participant token")
    parser.add_argument("--network", default="nttd_air", help="Which agent network to run")
    parser.add_argument("--host", default="localhost", help="Where neuro-san is serving")
    parser.add_argument("--port", type=int, default=8080, help="neuro-san HTTP port")
    # A backstop, not a schedule. The run ends when the world does; this only stops a loop
    # that would otherwise spin forever against a network that has stopped making progress.
    parser.add_argument(
        "--max-turns", type=int, default=200,
        help="Give up after this many turns even if the session is still open",
    )
    args = parser.parse_args()

    if not args.token:
        parser.error("a participant token is required: --token or NTTD_TOKEN")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"Playing {args.session} with {args.network}")
    return play(args.session, args.token, args.network, args.host, args.port, args.max_turns)


if __name__ == "__main__":
    raise SystemExit(main())

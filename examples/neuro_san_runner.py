"""Play one nttd session with a neuro-san agent network.

    uv run python -m examples.neuro_san_runner --session <id> --token <token>

Talks to a running neuro-san server over HTTP, so the agents are served by neuro-san and
this file only takes the turn. Start the server first, from this repository:

    cp .env.example .env      # fill in ANTHROPIC_API_KEY
    uv run ns run --server-only

`ns` comes from neuro-san-studio and loads the project-root .env before starting, which is
why the manifest path, the tool path and the key are not exported by hand here. Drop
--server-only to get the nsflow UI at http://localhost:4173 as well, which is worth having
the first time: it draws the network and shows each agent's reasoning as it runs.

**Decisions are sparse; time is not.** A step advances one game day, and a T1 run is 366 of
them, so a loop that asks the network to decide something every day would spend 366 model
calls to play one year and mostly be told nothing has changed. Hand play that scored well
went in waves: act, let a month or two pass, reassess when there is something new to see.
That is what --decide-every is, and the default comes from what worked by hand.

The run ends when the world does. The session stops itself at its tier's day budget, and a
step that is refused because the session has ended is the end of the run rather than an
error, so nothing here counts turns towards a finish line of its own.

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


async def play(
    session: str,
    token: str,
    network: str,
    decide_every: int,
    max_decisions: int,
    host: str,
    port: int,
) -> int:
    """Run the network once per step until the budget or the session runs out."""
    from neuro_san.session.http_service_agent_session import (  # noqa: PLC0415
        HttpServiceAgentSession,
    )

    sly_data = {"session_id": session, "token": token}
    agent = HttpServiceAgentSession(
        host=host, port=str(port), agent_name=network, streaming_timeout_in_seconds=900
    )

    decisions = 0
    while True:
        if max_decisions and decisions >= max_decisions:
            print(f"  stopping after {decisions} decisions, as asked")
            break

        replies = await asyncio.to_thread(
            lambda: list(
                agent.streaming_chat({
                    "user_message": {
                        "type": "AGENT_FRAMEWORK",
                        "text": (
                            "Take the next decision in this session. Read the position "
                            "first, fix what is broken before building anything new, and "
                            "say what you did."
                        ),
                    },
                    "sly_data": sly_data,
                })
            )
        )
        decisions += 1
        said = _last_text(replies)
        logger.info("decision %d: %s", decisions, said)
        print(f"  decision {decisions}: {said}")

        # Then let the world run. The network has just committed to something, and it
        # cannot tell whether it worked until vehicles have had time to move.
        played = await _let_time_pass(session, token, decide_every)
        if played < decide_every:
            print(f"  the session has ended after {played} further days")
            break
    return 0


async def _let_time_pass(session: str, token: str, days: int) -> int:
    """Advance the world, and report how many days actually passed.

    An empty action list is a legal step: it lets a day go by without doing anything, which
    is what waiting is. Fewer days than asked for means the session reached its budget and
    closed, which is the run finishing rather than a failure.
    """
    import httpx  # noqa: PLC0415

    root = f"{os.environ.get('NTTD_API_URL', 'http://127.0.0.1:8000')}/v1/participant/sessions/{session}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=900) as client:
        for day in range(days):
            reply = await client.post(f"{root}/step", json={"actions": []}, headers=headers)
            if reply.status_code == 404:
                return day
            reply.raise_for_status()
    return days


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
    parser.add_argument(
        "--decide-every", type=int, default=30,
        help="Game days between decisions. Hand play that scored well reassessed monthly.",
    )
    parser.add_argument(
        "--max-decisions", type=int, default=0,
        help="Stop after this many decisions. 0 plays until the session ends; use 1 or 2 "
             "to check a turn completes before committing to a whole run.",
    )
    parser.add_argument("--host", default="localhost", help="Where neuro-san is serving")
    parser.add_argument("--port", type=int, default=8080, help="neuro-san HTTP port")
    args = parser.parse_args()

    if not args.token:
        parser.error("a participant token is required: --token or NTTD_TOKEN")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return asyncio.run(
        play(
            args.session, args.token, args.network,
            args.decide_every, args.max_decisions, args.host, args.port,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

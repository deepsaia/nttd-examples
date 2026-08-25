"""Play one nttd session with a neuro-san agent network, until the world ends.

    uv run python -m examples.neuro_san_play --session <id> --token <token> --network ns_air_agent

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

# What the board shows in its system type column. Declared HERE rather than passed in by
# whatever launched this, so a run started by hand says the same thing as one started by the
# launcher: the runner is the only thing that knows what it is. It matches the key runex
# offers this runner under, which tests/test_runex.py asserts.
SYSTEM_TYPE = "neuro-san"

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


# The flat keys neuro-san puts beside the per-model breakdown in its token accounting: the
# whole network's totals for a request. Skipped when walking it, because the per-model entries
# below them already carry the same numbers split up, and counting both doubles everything.
_AGGREGATE_KEYS = frozenset({
    "total_tokens", "prompt_tokens", "completion_tokens", "successful_requests",
    "empty_responses", "total_cost", "time_taken_in_seconds", "caveats",
})


def _spend_from(accounting: dict) -> list[dict]:
    """neuro-san's token accounting for one turn, as nttd's per-model spend.

    The shape, read off a real payload rather than assumed:

        {total_tokens, prompt_tokens, completion_tokens, successful_requests,
         empty_responses, total_cost, time_taken_in_seconds, caveats,
         models: {provider: {model: {the same per-model figures}}}}

    The per-model breakdown is NESTED under `models`. An earlier version walked the top level
    looking for it, found the `models` dict itself, and read a provider name as a model with
    zero tokens. It passed a test because the test used a fixture invented from the same wrong
    assumption; the fixture below is lifted from a logged payload.

    nttd wants one entry per model, and its free-form `role` is where the provider goes: a
    front man on opus and workers on sonnet is exactly the split it keeps spend per model to
    show.

    **The cost is omitted when neuro-san reports zero.** It prices models from its own table
    and falls back to zero with only a log warning when a model is not in it, so a zero is far
    more likely to mean "no price for this model" than "this was free". nttd tells those apart:
    an absent cost leaves the board's cost column blank, while a zero claims the run cost
    nothing. Passing through a fallback zero would publish that claim on a run that spent real
    money.
    """
    spend: list[dict] = []
    breakdown = (accounting or {}).get("models")
    if not isinstance(breakdown, dict):
        return spend
    for provider, models in breakdown.items():
        if not isinstance(models, dict):
            continue
        for model, stats in models.items():
            if not isinstance(stats, dict):
                continue
            cost = float(stats.get("total_cost") or 0.0)
            entry = {
                "model": str(model),
                "role": str(provider),
                "prompt_tokens": int(stats.get("prompt_tokens") or 0),
                "completion_tokens": int(stats.get("completion_tokens") or 0),
            }
            if cost > 0:
                entry["total_cost_usd"] = cost
            spend.append(entry)
    return spend


def _report_spend(session: str, token: str, accounting: dict) -> None:
    """Send one turn's usage. nttd ADDS what it is told, so this reports per turn.

    Per turn is also the honest unit. Each turn is its own request carrying `chat_context`
    forward, so the history is re-sent and re-billed every time, and the totals only add up if
    every turn is counted. neuro-san resets its accounting per request, so what arrives here
    is this turn alone rather than a running total.

    Estimates, and declared as such by nttd, which marks the whole group reported rather than
    observed. neuro-san's own caveat: "Token counts are approximate and estimated using
    tiktoken."
    """
    spend = _spend_from(accounting)
    if not spend:
        return
    try:
        reply = httpx.post(
            f"{API_URL}/v1/participant/sessions/{session}/report",
            headers={"X-Participant-Token": token},
            json={"models": spend},
            timeout=30,
        )
        reply.raise_for_status()
    except httpx.HTTPError as failure:
        logger.warning("Could not report this turn's spend: %r", failure)


def _declare(session: str, token: str, network: str) -> None:
    """Tell nttd what is playing, since it cannot see it.

    nttd runs no model and watches only actions, so it cannot tell a multi-agent network from
    a scripted policy by looking. What it is told lands in result.parquet and becomes the
    board's system type column, so a row can say what produced it.

    Not fatal. A session that will not take the declaration is still a session worth playing,
    and losing a label is a smaller loss than refusing to start.
    """
    try:
        reply = httpx.post(
            f"{API_URL}/v1/participant/sessions/{session}/report",
            headers={"X-Participant-Token": token},
            json={
                "nttd_framework": SYSTEM_TYPE,
                "participant_type": "multi-agent",
                "agent_id": network,
            },
            timeout=30,
        )
        reply.raise_for_status()
    except httpx.HTTPError as failure:
        logger.warning("Could not declare the system type: %r", failure)


def play(session: str, token: str, network: str, host: str, port: int, turns: int) -> int:
    from neuro_san.client.streaming_input_processor import (  # noqa: PLC0415
        StreamingInputProcessor,
    )
    from neuro_san.session.http_service_agent_session import (  # noqa: PLC0415
        HttpServiceAgentSession,
    )

    _declare(session, token, network)

    agent = HttpServiceAgentSession(
        host=host, port=str(port), agent_name=network, streaming_timeout_in_seconds=1800
    )
    processor = StreamingInputProcessor(session=agent)

    # sly_data addresses the company and is deliberately kept out of the chat stream: it is
    # not something a model should see, restate or invent.
    state: dict = {
        # MAXIMAL, because the token accounting arrives as an AgentMessage and the DEFAULT
        # filter is MINIMAL, which is a compound of ChatContextMessageFilter alone. So the
        # server dropped every accounting message before it left, `token_accounting` came
        # back empty, and the runner reported nothing while looking like it had: no error, no
        # warning, just a bundle that said no spend was reported.
        "chat_filter": {"chat_filter_type": "MAXIMAL"},
        "sly_data": {"session_id": session, "token": token},
        "chat_context": {},
        "last_chat_response": None,
        "user_input": TURN,
    }

    start = _status(session).get("game_date")
    for turn in range(1, turns + 1):
        # Stamped here because only the client knows where a turn begins. A coded tool sees
        # one continuous stream of calls; it cannot tell the last call of one request from
        # the first call of the next. advance_days reads this to bound the days a single turn
        # may spend, which is what stops a turn growing until it exceeds the server's
        # execution cap and is cancelled with everything in it lost.
        state["sly_data"]["turn_stamp"] = turn
        state["user_input"] = TURN
        state = processor.process_once(state)
        _report_spend(session, token, state.get("token_accounting") or {})

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
    parser.add_argument("--network", default="ns_air_agent", help="Which agent network to run")
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

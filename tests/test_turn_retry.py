"""What happens to a turn whose stream dies under it.

A live run ended on this. neuro-san streams a turn back over one long HTTP response, the
provider went quiet for longer than the client was willing to wait, and the whole process came
down with a traceback about connectivity while the session it was playing carried on with hours
left on the clock. Nothing was wrong with the world, the token, the server or the network. The
run was simply thrown away.

Two things went wrong and both are covered here. The client waited less time than the server
was allowed to take, which test_ns_networks.py now asserts against every served network. And a
failed stream was fatal, which is what these tests are about.
"""

from __future__ import annotations

from typing import Any

import pytest

from examples import neuro_san_play
from examples.neuro_san_play import TURN_ATTEMPTS, _take_turn


class Flaky:
    """A processor that fails a given number of times before working."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0
        self.seen: list[dict[str, Any]] = []

    def process_once(self, state: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        self.seen.append(dict(state))
        if self.calls <= self.failures:
            # The shape neuro-san really raises: its connectivity help text, wrapped in a
            # ValueError, with the underlying requests error already swallowed by its own
            # except clause.
            raise ValueError("Some basic suggestions to help debug connectivity issues: ...")
        return {**state, "last_chat_response": "done"}


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backoff is real seconds in a run and must not be real seconds in a test."""
    monkeypatch.setattr(neuro_san_play.time, "sleep", lambda _seconds: None)


def test_a_turn_that_works_first_time_is_not_retried() -> None:
    """The ordinary case, asserted so a retry loop cannot quietly double every turn's cost."""
    processor = Flaky(failures=0)
    answer = _take_turn(processor, {"user_input": "go"}, 1)
    assert answer is not None
    assert processor.calls == 1


def test_a_dropped_stream_is_taken_again() -> None:
    """What is lost is the turn's sly_data; what survives is the world and the conversation.

    The world is on nttd's side and the chat_context is still held here, so the next attempt
    reads the position again and carries on, which is what the network does at the start of
    every turn anyway.
    """
    processor = Flaky(failures=TURN_ATTEMPTS - 1)
    answer = _take_turn(processor, {"user_input": "go"}, 1)
    assert answer is not None and answer["last_chat_response"] == "done"
    assert processor.calls == TURN_ATTEMPTS


def test_the_same_turn_is_re_sent_and_not_a_different_one() -> None:
    """A retry that changed the request would be this loop playing the game."""
    processor = Flaky(failures=1)
    _take_turn(processor, {"user_input": neuro_san_play.TURN, "sly_data": {"turn_stamp": 4}}, 4)
    assert processor.seen[0] == processor.seen[1]


def test_it_gives_up_rather_than_retrying_for_ever() -> None:
    """A fourth attempt against something genuinely broken only delays a report the run needs."""
    processor = Flaky(failures=99)
    assert _take_turn(processor, {"user_input": "go"}, 1) is None
    assert processor.calls == TURN_ATTEMPTS


def test_only_the_stream_is_retried_and_not_an_unhelpful_answer() -> None:
    """A turn that completed and said something useless is a turn the network is entitled to.

    Re-running it would be the loop overruling the thing it exists to measure.
    """
    processor = Flaky(failures=0)
    answer = _take_turn(processor, {"user_input": "go"}, 1)
    assert answer is not None
    assert processor.calls == 1, "a completed turn must never be taken twice"

"""How many game days one turn may spend.

A turn is one request. The agent decides what to do in it and how far to let the world run,
and on a measured run one turn advanced 55 days across two calls and was then cancelled with
"exceeded max_execution_seconds=300.0s". A cancelled turn loses everything in it, including
its token accounting, so the run reported no spend at all.

So a turn has a day budget. The agent still chooses, anywhere from one day up to what is left.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[1] / "agents" / "neuro_san" / "coded_tools"
sys.path.insert(0, str(_TOOLS))

from ns import constants as key  # noqa: E402
from ns.advance_days import DAYS_PER_TURN, MOST_DAYS_AT_ONCE, _spent_this_turn  # noqa: E402


def test_a_fresh_turn_starts_with_the_whole_budget() -> None:
    assert _spent_this_turn({key.TURN_STAMP: 1}) == 0


def test_days_accumulate_within_one_turn() -> None:
    """Two calls in one turn share a budget. That is the whole point: a per-call cap would
    have let the measured turn spend 55 days across two calls without noticing."""
    sly: dict = {key.TURN_STAMP: 1}
    _spent_this_turn(sly)
    sly[key.DAYS_THIS_TURN] = 10
    assert _spent_this_turn(sly) == 10


def test_the_budget_resets_when_the_turn_changes() -> None:
    """The runner stamps each turn, because only the client knows where one begins."""
    sly: dict = {key.TURN_STAMP: 1}
    _spent_this_turn(sly)
    sly[key.DAYS_THIS_TURN] = DAYS_PER_TURN

    sly[key.TURN_STAMP] = 2
    assert _spent_this_turn(sly) == 0, "a new turn starts fresh"
    assert sly[key.DAYS_THIS_TURN] == 0


def test_a_runner_that_stamps_nothing_is_still_bounded() -> None:
    """Without a stamp there is no turn to bound, so the per-call ceiling is the only unit
    left. It is the same number, so an unstamped runner is bounded per call rather than
    unbounded."""
    assert _spent_this_turn({}) == 0
    assert MOST_DAYS_AT_ONCE == DAYS_PER_TURN


def test_the_budget_is_small_enough_to_keep_turns_cheap() -> None:
    """Turns are the unit of COST: each re-sends the conversation and is billed for it.

    A measured 366 day run took 13 turns at about $1.73 each. One day per turn would be 366
    turns. Fourteen keeps a T1 run near 26.
    """
    assert 7 <= DAYS_PER_TURN <= 30, "outside this a T1 run is either too dear or too coarse"
    assert 366 / DAYS_PER_TURN < 40, "a T1 run should not need forty turns"


@pytest.mark.parametrize("stamp", [1, "turn-1", 0])
def test_any_stamp_the_runner_chooses_works(stamp: object) -> None:
    """It is compared, never parsed, so its shape is the runner's business."""
    sly: dict = {key.TURN_STAMP: stamp}
    assert _spent_this_turn(sly) == 0
    sly[key.DAYS_THIS_TURN] = 3
    assert _spent_this_turn(sly) == 3


def test_the_keys_survive_the_turn_boundary() -> None:
    """sly_data is redacted by default, so a key not declared upstream dies at the boundary
    and the budget would reset every turn without anyone noticing."""
    for name in (key.DAYS_THIS_TURN, key.TURN_STAMP, key.TURN_STAMP_SEEN):
        assert name in key.ALLOWED, f"{name} is kept between turns but never returns upstream"

"""What `runex` must keep true, as opposed to how it looks.

The look is the point of the tool and none of it is asserted here. What is asserted is the
handful of things that would make it quietly wrong: offering a session nothing can play,
hiding one the contestant just created, or putting a credential somewhere it can be read.
"""

from __future__ import annotations

import os
from typing import Any

from runex import sessions
from runex.kinds import KINDS, by_key
from runex.launcher import Launcher


def _record(**overrides: Any) -> dict[str, Any]:
    """A server session record, shaped like the ones GET /admin/sessions returns."""
    settings = {
        "_scenario_id": "benchmark-t1-256-flat-1001-stepped",
        "_runtime_mode": "stepped",
        "_ec_max_heartbeats": "366",
        "_scored": "1",
        "_agent_companies": "1",
    }
    settings.update(overrides.pop("settings", {}))
    record = {
        "session_id": "20260824-095217ist-perky-rocket",
        "status": "active",
        "running": True,
        "created_at": "2026-08-24T04:22:17+00:00",
        "settings": settings,
    }
    record.update(overrides)
    return record


# --- reading what the server said --------------------------------------------------------


def test_a_session_is_described_by_what_distinguishes_it() -> None:
    """The id is a date and two words, so the scenario is what a person recognises."""
    found = sessions._read(_record())
    assert found.scenario == "t1-256-flat-1001-stepped"
    assert found.runtime_mode == "stepped"
    assert found.game_days == 366
    assert found.scored is True


def test_a_hand_made_session_survives_having_none_of_the_benchmark_settings() -> None:
    """Those keys are written by `nttd benchmark`, so a session made another way lacks them."""
    found = sessions._read({"session_id": "abc", "status": "active", "settings": {}})
    assert found.scenario == "abc"
    assert found.game_days == 0
    assert found.scored is False


# --- which sessions are offered ------------------------------------------------------------


def test_an_ended_session_is_not_offered() -> None:
    """Its result is already written, and nothing a runner does now can change it."""
    assert not sessions._read(_record(status="ended", running=False)).attachable
    assert not sessions._read(_record(status="archived", running=False)).attachable


def test_a_session_the_database_has_not_caught_up_with_is_still_offered() -> None:
    """`running` and `status` disagree while a session is starting.

    Hiding it then means a contestant who has just created a session is told there is nothing
    to play. A stale offer costs one clear refusal from the server; a hidden session costs the
    contestant their assumption that the tool can see their work.
    """
    assert sessions._read(_record(status="pending", running=False)).attachable
    assert sessions._read(_record(status="unknown", running=True)).attachable


def test_a_session_with_no_contestant_company_is_a_dead_end() -> None:
    """Started without --agent-companies, it has no token, so no runner can be anybody."""
    found = sessions._read(_record(settings={"_agent_companies": "0"}))
    assert found.attachable
    assert not found.has_contestant


# --- the credential --------------------------------------------------------------------------


def test_the_token_never_reaches_the_command_line() -> None:
    """An argv is readable by every process on the machine.

    The launcher passes it as NTTD_TOKEN instead, which is why both runners default that
    option from the environment.
    """
    command = Launcher("http://127.0.0.1:8000").command(by_key("scripted"), "some-session")
    assert "pt_" not in " ".join(command)
    assert not [part for part in command if part == "--token"]


def test_both_written_runners_accept_the_token_from_the_environment() -> None:
    """Otherwise keeping it off the command line would simply stop them working."""
    for kind in KINDS:
        if not kind.written:
            continue
        path = kind.module.replace(".", os.sep) + ".py"
        source = open(path).read()
        assert 'os.environ.get("NTTD_TOKEN"' in source, f"{kind.module} cannot read NTTD_TOKEN"


# --- the menu ----------------------------------------------------------------------------------


def test_unwritten_approaches_are_listed_rather_than_hidden() -> None:
    """A menu showing only what exists reads as though nothing else were intended."""
    keys = {kind.key for kind in KINDS}
    assert {"neuro-san", "scripted", "es", "rl"} <= keys
    assert not by_key("es").written
    assert not by_key("es").ready


def test_an_unwritten_approach_says_so_rather_than_failing_later() -> None:
    assert by_key("es").unavailable_because == "not written yet"


def test_a_written_approach_reports_the_extra_it_needs() -> None:
    """Reported at the menu, because the alternative is a traceback thirty seconds in."""
    neuro = by_key("neuro-san")
    assert neuro.written
    assert neuro.requires == "neuro_san"
    assert "uv sync" in neuro.install_hint

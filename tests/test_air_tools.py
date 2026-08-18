"""The air tools' expensive lessons, held in place.

Every test here stands for a failure that was measured in a run rather than imagined: an
aircraft dispatched onto another corridor's airports, a repair recorded before it was submitted,
a build intent evicted by twenty ordinary decisions, a big plane sent to a commuter field on
evidence about one end of two.

Nothing here talks to nttd. A fake session answers the queries, which is what makes these
deterministic: the tools are the place the lessons live, so the tools are what is tested.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("neuro_san")

from agents.neuro_san.coded_tools.ns import commit_plan, counting, note_decision, session  # noqa: E402
from agents.neuro_san.coded_tools.ns.gateway import QueryRefused  # noqa: E402
from agents.neuro_san.coded_tools.ns.note_decision import NoteDecision  # noqa: E402
from agents.neuro_san.coded_tools.ns_air import air_keys as air  # noqa: E402
from agents.neuro_san.coded_tools.ns_air.air_health_check import AirHealthCheck  # noqa: E402
from agents.neuro_san.coded_tools.ns_air.choose_aircraft import accepts_big_planes  # noqa: E402
from agents.neuro_san.coded_tools.ns_air.plan_dispatch import PlanDispatch  # noqa: E402
from agents.neuro_san.coded_tools.ns_air.plan_repoint import PlanRepoint  # noqa: E402
from agents.neuro_san.coded_tools.ns_air.plan_retire import PlanRetire, _sweep  # noqa: E402

CREDENTIALS = {"session_id": "s-1", "token": "t-1"}


class FakeSession:
    """What nttd would have answered, without nttd."""

    def __init__(self, **answers: Any) -> None:
        self.answers = answers

    async def query(self, action: str, params: dict[str, Any] | None = None) -> Any:
        answer = self.answers.get(action)
        if callable(answer):
            return answer(params or {})
        return answer if answer is not None else []

    async def observe(self) -> dict[str, Any]:
        return self.answers.get("observe") or {}

    async def situation(self) -> dict[str, Any]:
        return self.answers.get("situation") or {}


def _answering(monkeypatch: pytest.MonkeyPatch, fake: FakeSession) -> None:
    """Every tool opens its session through ns.session, so one patch covers all of them."""
    monkeypatch.setattr(session, "NttdGateway", lambda sly_data: fake)


def _route(corridor: str, stations: list[int], hangar: tuple[int, int]) -> dict[str, Any]:
    return {
        "mode": "air",
        "corridor_id": corridor,
        "towns": [f"{corridor}-from", f"{corridor}-to"],
        "stations": stations,
        "depot": {"x": hangar[0], "y": hangar[1]},
    }


def _plane(vid: int, place: tuple[int, int], *, orders: int = 0, parked: bool = True) -> dict:
    return {
        "id": vid, "name": f"aircraft {vid}", "order_count": orders,
        "in_depot": parked, "x": place[0], "y": place[1], "orders": [],
    }


# --- the session guard ---------------------------------------------------------------------

async def test_a_missing_token_is_a_sentence_and_not_a_crash() -> None:
    """A raised ValueError reaches a model as a framework error with no reason attached."""
    answer = await PlanDispatch().async_invoke({}, {})
    assert isinstance(answer, str) and "must be in sly_data" in answer


async def test_a_refused_query_comes_back_in_the_engines_own_words() -> None:
    """The reason carries the coordinate that fixes the bug, so it is passed through verbatim."""

    async def refuses(gateway: Any, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        raise QueryRefused("1 of 71 have no through connection, first at (93,185)")

    answer = await session.guarded(refuses, {}, dict(CREDENTIALS))
    assert "first at (93,185)" in answer


async def test_the_body_may_still_raise_a_value_error_of_its_own() -> None:
    """Only construction answers for a ValueError: a mistyped count is not a missing token."""

    async def miscounts(gateway: Any, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return int("two")

    with pytest.raises(ValueError):
        await session.guarded(miscounts, {}, dict(CREDENTIALS))


# --- dispatch, per corridor ----------------------------------------------------------------

async def test_each_waiting_aircraft_is_dispatched_to_its_own_corridor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One resolved route for the whole fleet put the second corridor's aircraft on the first."""
    sly = dict(CREDENTIALS) | {
        "routes": [_route("a-b", [0, 1], (10, 10)), _route("c-d", [2, 3], (50, 50))],
    }
    fleet = [_plane(1, (10, 10)), _plane(2, (50, 50))]
    _answering(monkeypatch, FakeSession(get_vehicles=fleet, get_hangars=[]))

    report = await PlanDispatch().async_invoke({}, sly)

    stations = {entry["corridor"]: entry["stations"] for entry in report["dispatched"]}
    aircraft = {entry["corridor"]: entry["aircraft"] for entry in report["dispatched"]}
    assert stations["a-b (a-b-from to a-b-to)"] == [0, 1]
    assert stations["c-d (c-d-from to c-d-to)"] == [2, 3]
    assert aircraft["a-b (a-b-from to a-b-to)"] == ["aircraft 1"]
    assert aircraft["c-d (c-d-from to c-d-to)"] == ["aircraft 2"]


async def test_an_aircraft_in_no_recorded_hangar_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatching to the wrong corridor costs a repoint, so nothing is guessed."""
    sly = dict(CREDENTIALS) | {"routes": [_route("a-b", [0, 1], (10, 10))]}
    stray = _plane(9, (99, 99))
    _answering(monkeypatch, FakeSession(get_vehicles=[stray], get_hangars=[]))

    report = await PlanDispatch().async_invoke({}, sly)

    assert report["staged"] == 0
    assert len(report["left_alone"]) == 1
    assert not sly.get("plan")


async def test_a_hangar_two_corridors_share_names_neither(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """grand-tundra ran a hub: three of four lines called at one station, so its hangar says
    nothing about which line an aircraft was bought for."""
    sly = dict(CREDENTIALS) | {
        "routes": [_route("a-b", [0, 1], (10, 10)), _route("b-c", [1, 2], (10, 10))],
    }
    _answering(monkeypatch, FakeSession(get_vehicles=[_plane(1, (10, 10))], get_hangars=[]))

    report = await PlanDispatch().async_invoke({}, sly)

    assert report["staged"] == 0 and len(report["left_alone"]) == 1


async def test_the_games_hangars_cover_a_route_recorded_without_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A purchase can resolve a hangar from get_hangars, leaving the route record with none."""
    sly = dict(CREDENTIALS) | {
        "routes": [{"mode": "air", "towns": ["E", "F"], "stations": [7, 8]}],
    }
    hangars = [{"station_id": 7, "hangar_x": 10, "hangar_y": 10, "hangar_tile": 2570}]
    _answering(monkeypatch, FakeSession(get_vehicles=[_plane(1, (10, 10))], get_hangars=hangars))

    report = await PlanDispatch().async_invoke({}, sly)

    assert report["dispatched"][0]["stations"] == [7, 8]


# --- a staged repair is not a repair -------------------------------------------------------

def _health(vid: int, place: tuple[int, int], day: int) -> dict[str, Any]:
    return {
        "day": day,
        "vehicles": {
            str(vid): {
                "where": f"hangar at {place[0]},{place[1]}",
                "since_day": day - 60,
                "name": f"aircraft {vid}",
                "verdict": "stuck",
                "needs_repoint": True,
            },
        },
    }


async def test_a_repoint_is_recorded_as_staged_and_not_as_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """commit_plan has not run yet, and a refused commit must not read as a repaired vehicle."""
    sly = dict(CREDENTIALS) | {
        "routes": [_route("a-b", [0, 1], (10, 10))],
        air.HEALTH: _health(1, (10, 10), 100),
    }
    fake = FakeSession(
        observe={"game": {"map_width": 256}, "stations": [{"id": 0, "x": 1, "y": 1},
                                                         {"id": 1, "x": 2, "y": 2}]},
        get_orders={"orders": []},
    )
    _answering(monkeypatch, fake)

    await PlanRepoint().async_invoke({"vehicle_id": 1}, sly)

    entry = sly[air.HEALTH]["vehicles"]["1"]
    assert entry[air.REPOINT_STAGED_DAY] == 100
    assert air.REPOINTED_DAY not in entry
    assert entry.get(air.REPOINTS, 0) == 0


async def test_the_health_check_promotes_a_staged_repoint_once_the_aircraft_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Movement is the only evidence a repoint took, and it is what the counter counts."""
    record = _health(1, (10, 10), 100)
    record["vehicles"]["1"][air.REPOINT_STAGED_DAY] = 100
    sly = dict(CREDENTIALS) | {air.HEALTH: record}
    still_there = FakeSession(
        observe={"game": {"game_days_total": 366, "game_days_remaining": 200}},
        situation={"problems": []},
        get_vehicles=[_plane(1, (10, 10))],
    )
    _answering(monkeypatch, still_there)

    await AirHealthCheck().async_invoke({}, sly)
    entry = sly[air.HEALTH]["vehicles"]["1"]
    assert entry[air.REPOINT_STAGED_DAY] == 100, "it has not moved, so nothing was repaired"

    moved = FakeSession(
        observe={"game": {"game_days_total": 366, "game_days_remaining": 190}},
        situation={"problems": []},
        get_vehicles=[_plane(1, (44, 44), parked=False)],
    )
    _answering(monkeypatch, moved)
    await AirHealthCheck().async_invoke({}, sly)

    entry = sly[air.HEALTH]["vehicles"]["1"]
    assert air.REPOINT_STAGED_DAY not in entry
    assert entry[air.REPOINTED_DAY] == 176
    assert entry[air.REPOINTS] == 1


async def test_a_vehicle_with_a_staged_repoint_is_not_yet_hopeless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selling one whose repair was never committed sells an aircraft nothing was tried on."""
    record = _health(1, (10, 10), 200)
    record["vehicles"]["1"][air.REPOINT_STAGED_DAY] = 100
    sly = dict(CREDENTIALS) | {air.HEALTH: record}
    _answering(monkeypatch, FakeSession(get_vehicles=[_plane(1, (10, 10))]))

    report = await PlanRetire().async_invoke({}, sly)

    assert report["staged"] == []
    assert "no aircraft is hopeless" in report["note"]


def test_a_sale_becomes_an_attempt_only_once_a_commit_carried_it() -> None:
    """Three plans nobody committed used to spend the whole allowance of three attempts."""
    entry: dict[str, Any] = {"stage": "sent", "sent_day": 10, "name": "aircraft 1"}
    retiring = {"1": entry}
    owned = {"1": {"id": 1, "in_depot": True}}

    batch, _, _, _ = _sweep(retiring, owned, 20, set())
    assert len(batch) == 1 and entry[air.SELL_STAGED_DAY] == 20
    assert air.SELL_ATTEMPTS not in entry

    batch, _, waiting, _ = _sweep(retiring, owned, 21, {"1"})
    assert batch == [] and air.SELL_ATTEMPTS not in entry
    assert "not committed" in waiting[0]["state"]

    batch, _, _, _ = _sweep(retiring, owned, 22, set())
    assert entry[air.SELL_ATTEMPTS] == 1 and len(batch) == 1


# --- the pending build intent --------------------------------------------------------------

async def test_twenty_decisions_do_not_evict_an_unconfirmed_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """confirm_airports reads that intent back, and its check cost a run 55 rating points."""
    intent = {"kind": "air_corridor", "corridor_id": "a-b", "confirmed": False}
    sly = dict(CREDENTIALS) | {"decisions": [intent]}
    # note_decision opens its own session rather than through the shared guard, because it needs
    # the game date before it will record anything at all.
    monkeypatch.setattr(
        note_decision, "NttdGateway",
        lambda sly_data: FakeSession(observe={"game": {"game_date": 738156}}),
    )

    for number in range(30):
        await NoteDecision().async_invoke(
            {"decision": f"decision {number}", "because": "measured"}, sly
        )

    assert sly["decisions"][0] is intent
    intent["confirmed"] = True
    await NoteDecision().async_invoke({"decision": "one more", "because": "measured"}, sly)
    assert intent not in sly["decisions"], "a confirmed intent is an ordinary decision again"


# --- big planes ----------------------------------------------------------------------------

def test_one_answered_end_of_two_is_not_evidence_of_a_big_plane_corridor() -> None:
    """all() over a partial answer is True, and three aircraft were lost to that."""
    route = {"stations": [0, 1], "takes_big_planes": False}
    assert accepts_big_planes(route, [{"station_id": 0, "airport_type": 3}]) is False
    assert accepts_big_planes(route, []) is False


def test_both_ends_answered_large_takes_a_big_plane() -> None:
    route = {"stations": [0, 1]}
    both = [{"station_id": 0, "airport_type": 3}, {"station_id": 1, "airport_type": 4}]
    assert accepts_big_planes(route, both) is True
    commuter = [{"station_id": 0, "airport_type": 3}, {"station_id": 1, "airport_type": 5}]
    assert accepts_big_planes(route, commuter) is False


# --- borrowing before spending -------------------------------------------------------------

def test_a_staged_loan_is_credited_only_when_it_runs_before_the_spending() -> None:
    """A step executes its actions in order, so a set_loan behind a build has not paid for it."""
    build = {"action": "build_airport", "params": {"x": 1, "y": 2}}
    borrow = {"action": "set_loan", "params": {"amount": 200_000}}
    assert commit_plan._borrowing([build, borrow], 100_000) == 0
    assert commit_plan._borrowing([borrow, build], 100_000) == 100_000


def test_a_late_borrowing_is_reordered_and_a_repayment_is_not() -> None:
    """Hoisting a repayment would take money out of the bank before the batch spends it."""
    build = {"action": "build_airport", "params": {"x": 1, "y": 2}}
    borrow = {"action": "set_loan", "params": {"amount": 200_000}}
    repay = {"action": "set_loan", "params": {"amount": 50_000}}
    assert commit_plan._loan_first([build, borrow], 100_000) == [borrow, build]
    assert commit_plan._loan_first([build, repay], 100_000) == [build, repay]


# --- figures a model supplied --------------------------------------------------------------

def test_a_word_where_a_count_belongs_falls_back_and_says_so() -> None:
    """int("two") raises, which turns a recoverable misunderstanding into a tool error."""
    value, said = counting.counted("two", 1, most=4)
    assert value == 1 and "not a whole number" in said


def test_an_absent_argument_takes_the_default_in_silence() -> None:
    assert counting.counted(None, 30, most=120) == (30, "")


def test_a_clamp_is_said_out_loud() -> None:
    """max(1, min(asked, most)) turned 500 days into 120 and said nothing about it."""
    value, said = counting.counted(500, 30, most=120)
    assert value == 120 and "120" in said
    value, said = counting.counted(0, 1, most=4)
    assert value == 1 and said

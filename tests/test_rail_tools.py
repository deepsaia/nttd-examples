"""What the rail tools do, checked against replies shaped like the game's own.

Nothing here talks to nttd. What makes these deterministic tests rather than a restatement of
my own assumptions is `rail_replies.shaped`: a fixture may only use field names the running
game publishes, and test_rail_action_surface.py is what confirms that list against it. So a
tool that reads a field by the wrong name gets None here, exactly as it would in a real run,
and the assertion below it fails.

That mattered. Four bugs of precisely that shape were live in this package at once, every one
of them silent, and each is now a test in this file with the failure it produced written next
to it.
"""

from __future__ import annotations

from typing import Any

import pytest
import rail_replies
from rail_replies import shaped, spot, train, train_detail

pytest.importorskip("neuro_san")

from agents.neuro_san.coded_tools.ns import constants as key  # noqa: E402
from agents.neuro_san.coded_tools.ns import session  # noqa: E402
from agents.neuro_san.coded_tools.ns_rail_agent import rail_keys as rail  # noqa: E402
from agents.neuro_san.coded_tools.ns_rail_agent import rail_rules as rules  # noqa: E402
from agents.neuro_san.coded_tools.ns_rail_agent.choose_rail_type import ChooseRailType  # noqa: E402
from agents.neuro_san.coded_tools.ns_rail_agent.choose_train import ChooseTrain  # noqa: E402
from agents.neuro_san.coded_tools.ns_rail_agent.confirm_rail_route import ConfirmRailRoute  # noqa: E402
from agents.neuro_san.coded_tools.ns_rail_agent.plan_dispatch import PlanDispatch  # noqa: E402
from agents.neuro_san.coded_tools.ns_rail_agent.plan_repoint import PlanRepoint  # noqa: E402
from agents.neuro_san.coded_tools.ns_rail_agent.plan_retire import PlanRetire  # noqa: E402
from agents.neuro_san.coded_tools.ns_rail_agent.rail_health_check import RailHealthCheck  # noqa: E402
from agents.neuro_san.coded_tools.ns_rail_agent.survey_rail_pairs import SurveyRailPairs  # noqa: E402

CREDENTIALS = {"session_id": "s-1", "token": "t-1"}


class FakeSession:
    """What nttd would have answered, without nttd."""

    def __init__(self, **answers: Any) -> None:
        self.answers = answers
        self.asked: list[tuple[str, dict[str, Any]]] = []

    async def query(self, action: str, params: dict[str, Any] | None = None) -> Any:
        self.asked.append((action, params or {}))
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


def _staged(plan: list[dict[str, Any]], action: str) -> list[dict[str, Any]]:
    return [entry["params"] for entry in plan if entry["action"] == action]


# --- rule 1: the axis comes from the approach, and both lists have to agree -------------------


def test_the_axis_is_taken_from_the_corridor_and_not_from_a_spot() -> None:
    """Siting by distance and accepting the orientation that came with it failed three ways.

    A corridor mostly along x arrives along x, so the platform has to run along x for the track
    to meet its END. Meeting its side is a station a train cannot enter, and one measured
    attempt traced from it and reached exactly 3 tiles: the platform, isolated.
    """
    assert rules.axis_for_approach(10, 10, 90, 14) == rules.AXIS_ALONG_X
    assert rules.axis_for_approach(10, 10, 14, 90) == rules.AXIS_ALONG_Y


def test_a_spot_a_train_cannot_enter_is_refused_however_well_it_fits() -> None:
    """valid_directions and reachable_directions answer different questions.

    The engine's own measurement: a spot offered with BOTH orientations valid had neither entry
    usable along x, a town building and a road in the way, and one usable along y. Building on
    the first valid one made a station no train could ever enter and the only repair was to
    demolish it.
    """
    fits_both_enters_y = spot(5, 5, fits=[0, 1], enterable=[1])

    takes_x, why = rules.spot_takes_axis(fits_both_enters_y, rules.AXIS_ALONG_X)
    assert not takes_x
    assert "enter" in why

    takes_y, _ = rules.spot_takes_axis(fits_both_enters_y, rules.AXIS_ALONG_Y)
    assert takes_y


def test_a_spot_no_train_can_enter_at_all_is_refused() -> None:
    """An empty reachable_directions means the footprint fits and the station is useless."""
    takes, why = rules.spot_takes_axis(spot(5, 5, fits=[0, 1], enterable=[]), 0)
    assert not takes
    assert "unusable" in why


async def test_the_survey_sites_on_the_axis_the_line_will_arrive_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nearest spot is passed over when it faces the wrong way.

    This is the whole of rule 1 in one call: two industries far apart along x, a near spot that
    only takes y, and a further one that takes x. The further one is the right answer.
    """
    industries = [
        shaped("get_industries", id=1, name="Mine", x=10, y=10,
               produces_cargo=[0], accepts_cargo=[]),
        shaped("get_industries", id=2, name="Mill", x=90, y=12,
               produces_cargo=[], accepts_cargo=[0]),
    ]
    spots = {
        1: [spot(11, 10, fits=[1], enterable=[1], distance=1),
            spot(13, 10, fits=[0], enterable=[0], distance=3)],
        2: [spot(89, 12, fits=[1], enterable=[1], distance=1),
            spot(87, 12, fits=[0], enterable=[0], distance=3)],
    }
    fake = FakeSession(
        get_industries=industries,
        find_station_spot=lambda params: {"spots": spots[params["industry_id"]]},
    )
    _answering(monkeypatch, fake)

    sly: dict[str, Any] = {**CREDENTIALS, rail.RAIL_TYPE: 0}
    answer = await SurveyRailPairs().async_invoke({}, sly)

    assert isinstance(answer, dict), answer
    pairs = sly[rail.PAIRS]
    assert len(pairs) == 1
    assert pairs[0]["axis"] == rules.AXIS_ALONG_X
    assert (pairs[0]["producer"]["x"], pairs[0]["producer"]["y"]) == (13, 10)
    assert pairs[0]["producer"]["direction"] == rules.AXIS_ALONG_X


# --- rule 3: a reply is not a line that joins ------------------------------------------------


def test_every_segment_built_is_not_the_same_as_a_line_that_joins() -> None:
    """The engine's exact words for the bad case, and `failed` is empty in it.

    "every segment built, but the line is not continuous: 1 of 5 have no through connection,
    first at (175,231)". A check reading only `failed` calls that a finished corridor, and one
    gap means no route at all.
    """
    whole, _ = rules.line_is_whole(shaped("connect_rail", failed=[], gaps=[], status="ok"))
    assert whole

    joined, why = rules.line_is_whole(
        shaped("connect_rail", failed=[], gaps=[{"x": 175, "y": 231}], status="partial")
    )
    assert not joined
    assert "not continuous" in why


# --- rule 6: a locomotive alone carries nothing, and the two forms are not interchangeable ----


def test_a_working_train_is_not_condemned_by_the_build_reply_check() -> None:
    """The bug this pair of functions exists to make impossible.

    `wagons_attached` and `capacity_by_cargo` are build_train reply fields. Read off a VEHICLE
    they are both absent, so the check answered "carries nothing" for every train in the fleet,
    which is a verdict plan_retire sells trains for.
    """
    healthy = train_detail(1, capacity=40)
    assert rules.vehicle_carries_cargo(healthy)
    assert not rules.build_carries_cargo(healthy), (
        "the build-reply form must not accept a vehicle, or the two would be interchangeable "
        "and the distinction would be lost again"
    )


def test_a_locomotive_with_no_wagons_is_caught_on_a_live_vehicle() -> None:
    """The GameScript appends a cargo entry only where capacity is above zero.

    So an EMPTY list is the fact itself: this consist holds nothing. It runs its route, reports
    a position and a profit line, and earns zero.
    """
    assert not rules.vehicle_carries_cargo(train_detail(1, capacity=0))


def test_an_unreadable_reply_does_not_condemn_a_train() -> None:
    """Absent is not empty. A query that failed must not read as a broken train."""
    assert rules.vehicle_carries_cargo({"id": 1})


async def test_a_train_that_carries_nothing_is_not_dispatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checked at the last moment it is still cheap to fix."""
    _answering(monkeypatch, FakeSession(get_vehicle_info=train_detail(7, capacity=0)))
    sly: dict[str, Any] = {
        **CREDENTIALS,
        rail.INTENT: {"coal-1-2": {
            "pair_id": "coal-1-2",
            "producer": {"station_id": 1}, "consumer": {"station_id": 2},
        }},
    }
    answer = await PlanDispatch().async_invoke({"vehicle_id": 7, "pair_id": "coal-1-2"}, sly)
    assert isinstance(answer, str) and "no wagons" in answer
    assert not sly.get(key.PLAN)


# --- the health check reads the game's own verdict --------------------------------------------


async def test_lost_is_read_from_the_detail_and_not_from_the_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`lost` is on get_vehicle_info and not on get_vehicles.

    Read off the list it is simply absent, which is indistinguishable from a healthy train, and
    the mode's signature failure could never be detected at all.
    """
    fake = FakeSession(
        get_vehicles=[train(3)],
        get_vehicle_info=train_detail(3, lost=True),
        observe={"game": {"game_days_total": 366, "game_days_remaining": 300}},
        situation={"problems": []},
    )
    _answering(monkeypatch, fake)

    answer = await RailHealthCheck().async_invoke({}, {**CREDENTIALS})
    assert isinstance(answer, dict), answer
    assert answer["lost"] == [3]
    assert "get_vehicle_info" in {action for action, _ in fake.asked}


async def test_a_healthy_train_is_called_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The counterpart, and the one that catches a check that condemns everything."""
    fake = FakeSession(
        get_vehicles=[train(3)],
        get_vehicle_info=train_detail(3, capacity=40, destinations=(1, 2)),
        observe={"game": {"game_days_total": 366, "game_days_remaining": 300}},
        situation={"problems": []},
    )
    _answering(monkeypatch, fake)

    answer = await RailHealthCheck().async_invoke({}, {**CREDENTIALS})
    assert answer["trains"][0]["verdict"] == "healthy", answer["trains"][0]
    assert answer["lost"] == []
    assert answer["carries_nothing"] == []


# --- the engine list is read by the names it really uses --------------------------------------


async def test_the_engine_identifier_is_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine list calls it `id`. `engine_id` is what a VEHICLE carries.

    Reading that name here returns None from every entry, which becomes engine 0 and a purchase
    refused for a reason that has nothing to do with the real mistake.
    """
    fake = FakeSession(get_engines=[
        shaped("get_engines", id=42, name="Dash", price=9000, power=1200,
               capacity=0, is_wagon=False, rail_type=0),
        shaped("get_engines", id=77, name="Coal Wagon", price=800, power=0,
               capacity=30, cargo_type=0, is_wagon=True, rail_type=0),
    ])
    _answering(monkeypatch, fake)

    sly: dict[str, Any] = {
        **CREDENTIALS,
        rail.INTENT: {"coal-1-2": {"pair_id": "coal-1-2", "rail_type": 0,
                                   "cargo": "COAL", "cargo_id": 0}},
    }
    answer = await ChooseTrain().async_invoke({"pair_id": "coal-1-2"}, sly)
    assert isinstance(answer, dict), answer
    assert answer["engine_id"] == 42
    assert answer["wagon_id"] == 77


async def test_a_wagon_is_told_from_a_locomotive_by_the_published_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A powered wagon would fool an inference from power alone, and the list says outright."""
    fake = FakeSession(get_engines=[
        shaped("get_engines", id=42, name="Loco", price=9000, power=1200,
               capacity=0, is_wagon=False, rail_type=0),
        shaped("get_engines", id=51, name="Powered Van", price=1000, power=300,
               capacity=25, cargo_type=0, is_wagon=True, rail_type=0),
    ])
    _answering(monkeypatch, fake)
    sly: dict[str, Any] = {
        **CREDENTIALS,
        rail.INTENT: {"c": {"pair_id": "c", "rail_type": 0, "cargo": "COAL", "cargo_id": 0}},
    }
    answer = await ChooseTrain().async_invoke({"pair_id": "c"}, sly)
    assert answer["engine_id"] == 42
    assert answer["wagon_id"] == 51


async def test_an_engine_for_another_rail_type_is_not_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Of 40 engines on one measured map, 28 were monorail or maglev.

    The fastest of those cannot enter track built with the default type, and the game sells it
    anyway.
    """
    fake = FakeSession(get_engines=[
        shaped("get_engines", id=99, name="Maglev", price=40000, power=9000,
               capacity=0, is_wagon=False, rail_type=2),
        shaped("get_engines", id=42, name="Dash", price=9000, power=1200,
               capacity=0, is_wagon=False, rail_type=0),
        shaped("get_engines", id=77, name="Wagon", price=800, power=0,
               capacity=30, cargo_type=0, is_wagon=True, rail_type=0),
    ])
    _answering(monkeypatch, fake)
    sly: dict[str, Any] = {
        **CREDENTIALS,
        rail.INTENT: {"c": {"pair_id": "c", "rail_type": 0, "cargo": "COAL", "cargo_id": 0}},
    }
    answer = await ChooseTrain().async_invoke({"pair_id": "c"}, sly)
    assert answer["engine_id"] == 42, "the maglev has more power and cannot run on this line"


async def test_a_rail_type_with_no_rolling_stock_is_never_chosen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cheapest is not enough: a type with no locomotive is a line no train can ever use."""
    fake = FakeSession(
        get_rail_types=[
            shaped("get_rail_types", id=0, name="Cheap", available=True, build_cost_per_tile=10),
            shaped("get_rail_types", id=1, name="Dear", available=True, build_cost_per_tile=90),
        ],
        get_engines=[
            shaped("get_engines", id=42, name="Loco", price=9000, power=1200,
                   capacity=0, is_wagon=False, rail_type=1),
            shaped("get_engines", id=77, name="Wagon", price=800, power=0,
                   capacity=30, is_wagon=True, rail_type=1),
        ],
    )
    _answering(monkeypatch, fake)

    sly: dict[str, Any] = {**CREDENTIALS}
    answer = await ChooseRailType().async_invoke({}, sly)
    assert isinstance(answer, dict), answer
    assert answer["rail_type"] == 1, "the cheap type has nothing that runs on it"
    assert sly[rail.RAIL_TYPE] == 1
    assert "Cheap" in answer["skipped_no_rolling_stock"]


# --- confirming a corridor --------------------------------------------------------------------


async def test_a_bus_stop_near_the_platform_is_not_the_station(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The station list holds every station the company owns, of every kind.

    One that matches on distance alone records a route whose ends are not the ones that were
    built, and every check after that reads the record rather than the map.
    """
    fake = FakeSession(get_stations=[
        shaped("get_stations", id=5, name="Bus stop", x=10, y=10, has_rail=False),
    ])
    _answering(monkeypatch, fake)
    sly: dict[str, Any] = {
        **CREDENTIALS,
        rail.INTENT: {"c": {
            "pair_id": "c", "cargo": "COAL",
            "producer": {"x": 10, "y": 10}, "consumer": {"x": 90, "y": 10},
        }},
    }
    answer = await ConfirmRailRoute().async_invoke({}, sly)
    assert answer["corridors"][0]["stage"] == "stations_missing"


async def test_a_depot_is_only_connected_once_the_game_says_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building a depot does not join it, and nothing but a trace can say whether it took.

    Left to the flag plan_add_depot writes, it stayed False forever and no corridor ever reached
    ready_for_a_train, which is a build loop that can never finish.
    """
    traces = iter([
        shaped("trace_route", line_exists=True, exhausted=False, tiles_reachable=80),
        shaped("trace_route", line_exists=True, exhausted=False, tiles_reachable=4),
    ])
    fake = FakeSession(
        get_stations=[
            shaped("get_stations", id=5, name="Mine", x=10, y=10, has_rail=True),
            shaped("get_stations", id=6, name="Mill", x=90, y=10, has_rail=True),
        ],
        trace_route=lambda _params: next(traces),
    )
    _answering(monkeypatch, fake)
    sly: dict[str, Any] = {
        **CREDENTIALS,
        rail.INTENT: {"c": {
            "pair_id": "c", "cargo": "COAL",
            "producer": {"x": 10, "y": 10}, "consumer": {"x": 90, "y": 10},
            "depot": {"x": 12, "y": 11, "connected": False},
        }},
    }
    answer = await ConfirmRailRoute().async_invoke({}, sly)
    assert answer["ready_for_a_train"] == ["c"], answer
    assert sly[rail.INTENT]["c"]["depot"]["connected"] is True
    assert sly[key.ROUTES][0]["stations"] == [5, 6]


async def test_a_depot_the_trace_cannot_reach_leaves_the_corridor_unfinished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A depot built and never joined reads as a depot in every other report."""
    traces = iter([
        shaped("trace_route", line_exists=True, exhausted=False, tiles_reachable=80),
        shaped("trace_route", line_exists=False, exhausted=False, tiles_reachable=1),
    ])
    fake = FakeSession(
        get_stations=[
            shaped("get_stations", id=5, name="Mine", x=10, y=10, has_rail=True),
            shaped("get_stations", id=6, name="Mill", x=90, y=10, has_rail=True),
        ],
        trace_route=lambda _params: next(traces),
    )
    _answering(monkeypatch, fake)
    sly: dict[str, Any] = {
        **CREDENTIALS,
        rail.INTENT: {"c": {
            "pair_id": "c", "cargo": "COAL",
            "producer": {"x": 10, "y": 10}, "consumer": {"x": 90, "y": 10},
            "depot": {"x": 12, "y": 11, "connected": True},
        }},
    }
    answer = await ConfirmRailRoute().async_invoke({}, sly)
    assert answer["corridors"][0]["stage"] == "depot_not_connected"
    assert sly[rail.INTENT]["c"]["depot"]["connected"] is False
    assert not sly.get(key.ROUTES)


# --- repair -----------------------------------------------------------------------------------


async def test_a_repoint_clears_before_it_adds_and_removes_from_the_top(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """add_order APPENDS, and removing index 0 renumbers everything above it.

    A train given two more orders ends with four, zig-zagging between two unrelated pairs, and
    reports an order_count that looks busier than a healthy train's.
    """
    fake = FakeSession(get_orders=shaped("get_orders", orders=[{}, {}, {}]))
    _answering(monkeypatch, fake)
    sly: dict[str, Any] = {
        **CREDENTIALS,
        key.ROUTES: [{"pair_id": "c", "stations": [5, 6]}],
        rail.HEALTH: {"day": 100, "vehicles": {"7": {"verdict": "stuck"}}},
    }
    answer = await PlanRepoint().async_invoke({"vehicle_id": 7, "pair_id": "c"}, sly)

    assert isinstance(answer, dict), answer
    plan = sly[key.PLAN]
    assert [entry["action"] for entry in plan] == [
        "remove_order", "remove_order", "remove_order", "add_order", "add_order",
    ]
    assert [params["order_index"] for params in _staged(plan, "remove_order")] == [2, 1, 0]
    assert [params["station_id"] for params in _staged(plan, "add_order")] == [5, 6]


async def test_a_repoint_records_an_intent_and_not_a_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the health check may promote it, and only once it has seen the train move.

    A marker written here would make a refused or uncommitted batch read as a repair, which is
    how a stuck train stops being flagged and a retirement then sells it.
    """
    _answering(monkeypatch, FakeSession(get_orders=shaped("get_orders", orders=[])))
    sly: dict[str, Any] = {
        **CREDENTIALS,
        key.ROUTES: [{"pair_id": "c", "stations": [5, 6]}],
        rail.HEALTH: {"day": 100, "vehicles": {"7": {"verdict": "stuck"}}},
    }
    await PlanRepoint().async_invoke({"vehicle_id": 7, "pair_id": "c"}, sly)

    entry = sly[rail.HEALTH]["vehicles"]["7"]
    assert entry[rail.REPOINT_STAGED_DAY] == 100
    assert rail.REPOINTED_DAY not in entry


async def test_a_locomotive_with_no_wagons_is_not_repointed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its orders are fine. No order couples a wagon, so a repoint is a wasted commit."""
    _answering(monkeypatch, FakeSession())
    sly: dict[str, Any] = {
        **CREDENTIALS,
        key.ROUTES: [{"pair_id": "c", "stations": [5, 6]}],
        rail.HEALTH: {"day": 100, "vehicles": {"7": {"verdict": "carries_nothing"}}},
    }
    answer = await PlanRepoint().async_invoke({"vehicle_id": 7, "pair_id": "c"}, sly)
    assert isinstance(answer, str) and "plan_retire" in answer
    assert not sly.get(key.PLAN)


async def test_nothing_is_sent_to_a_depot_that_does_not_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rail's own failure, and the reason this refusal is right.

    An aircraft always has a hangar, because the hangar is part of the airport. A train sent to
    a depot its network does not have keeps its old orders and never arrives, so the sale never
    becomes possible and the disposal sits in the record looking like it is in progress.
    """
    _answering(monkeypatch, FakeSession(get_vehicles=[train(7)]))
    sly: dict[str, Any] = {
        **CREDENTIALS,
        key.ROUTES: [{"pair_id": "c", "stations": [5, 6],
                      "depot": {"x": 1, "y": 1, "connected": False}}],
        rail.HEALTH: {"day": 100, "vehicles": {"7": {"verdict": "carries_nothing"}}},
    }
    answer = await PlanRetire().async_invoke({"vehicle_id": 7}, sly)
    assert isinstance(answer, str) and "plan_add_depot" in answer
    assert not sly.get(key.PLAN)


async def test_a_sale_is_two_calls_because_the_train_has_to_arrive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sell_vehicle needs the train stopped in a depot, and send_to_depot only asks it to go.

    Both in the same step always fails, because when the sale executes the train is still on
    the line.
    """
    fake = FakeSession(get_vehicles=[train(7, in_depot=False)])
    _answering(monkeypatch, fake)
    sly: dict[str, Any] = {
        **CREDENTIALS,
        key.ROUTES: [{"pair_id": "c", "stations": [5, 6],
                      "depot": {"x": 1, "y": 1, "connected": True}}],
        rail.HEALTH: {"day": 100, "vehicles": {"7": {"verdict": "carries_nothing",
                                                     "name": "train 7"}}},
    }

    first = await PlanRetire().async_invoke({"vehicle_id": 7}, sly)
    assert isinstance(first, dict), first
    assert [entry["action"] for entry in sly[key.PLAN]] == ["send_to_depot"]

    # The commit happened; the train has since arrived.
    sly[key.PLAN] = []
    fake.answers["get_vehicles"] = [train(7, in_depot=True)]
    second = await PlanRetire().async_invoke({}, sly)
    assert isinstance(second, dict), second
    assert [entry["action"] for entry in sly[key.PLAN]] == ["sell_vehicle"]


def test_the_fixtures_cannot_invent_a_field() -> None:
    """The property this whole file rests on, asserted rather than assumed."""
    with pytest.raises(AssertionError, match="does not publish"):
        shaped("get_vehicles", lost=True)
    assert "lost" in rail_replies.FIELDS["get_vehicle_info"]

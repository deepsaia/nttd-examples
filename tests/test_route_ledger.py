"""The memory that stops a run forgetting what it was building.

The failure this exists to prevent, seen in the loop it replaces: a policy that
re-derives "the first unserved pair" from the world every step abandons a half-built
route as soon as both its stations exist, picks the same pair again, and abandons it
again. Nothing in the observation says "you were partway through this", so the identity
has to be carried.

Exercised against a real BaseStore rather than a mock, so these check behaviour rather
than agreeing with a fake.
"""

from __future__ import annotations

import pytest
from langgraph.store.memory import InMemoryStore

from agents.common.route_ledger import RouteLedger
from agents.common.schema import Refusal


@pytest.fixture
def ledger() -> RouteLedger:
    return RouteLedger(InMemoryStore(), run_id="run-1", mode="rail")


class TestCarryingARouteAcrossSteps:
    def test_a_started_route_is_unfinished(self, ledger: RouteLedger) -> None:
        ledger.start_route("coal-a", "Coal mine 3 to power station 7")
        assert [r["route_id"] for r in ledger.unfinished()] == ["coal-a"]

    def test_it_is_recorded_at_intent_not_at_completion(self, ledger: RouteLedger) -> None:
        """A route recorded only when finished cannot be resumed, which is the whole
        point of recording it."""
        ledger.start_route("coal-a", "Coal mine 3 to power station 7")
        assert "coal-a" in ledger.summary()

    def test_an_earning_route_drops_out_of_unfinished(self, ledger: RouteLedger) -> None:
        ledger.start_route("coal-a", "Coal")
        ledger.note("coal-a", "earning", "first delivery")
        assert ledger.unfinished() == []

    def test_a_part_built_route_stays_unfinished(self, ledger: RouteLedger) -> None:
        """Stations built is not a working route. This is the exact state the old loop
        treated as done."""
        ledger.start_route("coal-a", "Coal")
        ledger.note("coal-a", "stations_built", "both ends placed")
        assert [r["route_id"] for r in ledger.unfinished()] == ["coal-a"]

    def test_notes_are_bounded(self, ledger: RouteLedger) -> None:
        """The ledger is re-read every step, so it cannot grow without limit."""
        ledger.start_route("coal-a", "Coal")
        for i in range(30):
            ledger.note("coal-a", "building", f"step {i}")
        assert len(ledger.unfinished()[0]["notes"]) <= 10

    def test_noting_an_unknown_route_does_not_raise(self, ledger: RouteLedger) -> None:
        """A specialist may report progress on something the orchestrator never
        registered. Losing the note is better than losing the step."""
        ledger.note("ghost", "building", "something happened")
        assert any(r["route_id"] == "ghost" for r in ledger.unfinished())


class TestNotRepeatingMistakes:
    def test_a_refusal_is_counted(self, ledger: RouteLedger) -> None:
        refusal = Refusal(action="build_dock", error="Invalid tile ID: 1")
        ledger.remember_refusal(refusal)
        ledger.remember_refusal(refusal)
        assert ledger.times_refused(refusal) == 2

    def test_the_same_mistake_on_a_different_tile_still_counts(
        self, ledger: RouteLedger,
    ) -> None:
        """Keyed on the action and the reason, not the parameters. A policy trying
        build_dock on a hundred unbuildable tiles is making one mistake, and keying on
        parameters would let it repeat that forever."""
        ledger.remember_refusal(Refusal(action="build_dock", error="not coastal", error_name="ERR_SITE"))
        ledger.remember_refusal(Refusal(action="build_dock", error="not coastal", error_name="ERR_SITE"))
        assert ledger.repeated_mistakes()

    def test_a_different_reason_is_a_different_mistake(self, ledger: RouteLedger) -> None:
        ledger.remember_refusal(Refusal(action="build_dock", error="not coastal", error_name="ERR_SITE"))
        ledger.remember_refusal(Refusal(action="build_dock", error="no money", error_name="ERR_CASH"))
        assert ledger.repeated_mistakes() == []

    def test_one_refusal_is_not_a_repeated_mistake(self, ledger: RouteLedger) -> None:
        """One refusal is information. The same refusal three times is a stuck policy,
        and only the second deserves telling the model about."""
        ledger.remember_refusal(Refusal(action="build_dock", error="x"))
        assert ledger.repeated_mistakes() == []


class TestIsolation:
    def test_two_modes_do_not_share_routes(self) -> None:
        """The combined system runs four of these over one store. A rail specialist
        reading a road route as its own would be worse than no memory at all."""
        store = InMemoryStore()
        rail = RouteLedger(store, run_id="run-1", mode="rail")
        road = RouteLedger(store, run_id="run-1", mode="road")
        rail.start_route("coal-a", "Coal")
        assert road.unfinished() == []

    def test_two_runs_do_not_share_routes(self) -> None:
        store = InMemoryStore()
        first = RouteLedger(store, run_id="run-1", mode="rail")
        second = RouteLedger(store, run_id="run-2", mode="rail")
        first.start_route("coal-a", "Coal")
        assert second.unfinished() == []

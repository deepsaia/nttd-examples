"""The action reference in a prompt is generated, not written.

``examples/agent_instructions.py`` was 47,000 characters restating, in prose, what nttd
already publishes as data. It had drifted: it still told models to call ``build_rail``,
which nttd deleted because it was ``build_path`` with a shorter list. A model following
it would have spent a step discovering that.

Generating the reference makes that impossible rather than unlikely, which is the same
move nttd made internally when it started generating its manifest from the GameScript
instead of hand-writing a validator.

Strategy stays hand-written, and short. A manifest cannot know which cargo pays.
"""

from __future__ import annotations

from typing import Any

import pytest

from agents import action_brief

MANIFEST = {
    "manifest_version": 1,
    "actions": {
        "set_loan": {
            "category": "company",
            "description": "Set the loan to an exact amount.",
            "parameters": {
                "amount": {"required": True, "description": "An amount of money."},
            },
        },
        "build_road_stop": {
            "category": "road",
            "description": "Build a stop for road vehicles.",
            "parameters": {
                "x": {"required": True, "description": "X coordinate."},
                "y": {"required": True, "description": "Y coordinate."},
                "stop_type": {
                    "required": False,
                    "default": {"expression": "GSRoad.ROADVEHTYPE_BUS"},
                    "description": "Which kind of stop.",
                    "enum": {"values": {"ROADVEHTYPE_BUS": 0, "ROADVEHTYPE_TRUCK": 1}},
                },
            },
        },
        "build_rail_station": {
            "category": "rail",
            "description": "Build a railway station.",
            "parameters": {"x": {"required": True, "description": "X coordinate."}},
        },
        "change_bank_balance": {
            "category": "deity",
            "description": "Operator only. Not playable.",
            "parameters": {},
        },
    },
}

PLAYABLE = {
    "company": ["set_loan"],
    "road": ["build_road_stop"],
    "rail": ["build_rail_station"],
}


class FakeClient:
    def __init__(self, manifest: dict[str, Any] = MANIFEST) -> None:
        self._manifest = manifest

    def action_manifest(self, category: str | None = None) -> dict[str, Any]:
        return self._manifest

    def available_actions(self) -> dict[str, Any]:
        return PLAYABLE


@pytest.fixture
def brief() -> str:
    return action_brief.build(FakeClient())


class TestWhatItSays:
    def test_it_names_the_actions_and_their_required_parameters(self, brief: str) -> None:
        assert "**set_loan(amount)**" in brief
        assert "**build_road_stop(x, y)**" in brief

    def test_an_optional_parameter_shows_its_default(self, brief: str) -> None:
        """Told only that a parameter is optional, a model still has to decide whether
        to pass it. Told the default, it can leave it alone."""
        assert "optional, default GSRoad.ROADVEHTYPE_BUS" in brief

    def test_enum_values_are_spelled_out(self, brief: str) -> None:
        """`stop_type` is an integer, and which integer is the whole question."""
        assert "ROADVEHTYPE_BUS=0" in brief
        assert "ROADVEHTYPE_TRUCK=1" in brief

    def test_it_is_grouped_by_category(self, brief: str) -> None:
        assert "## company" in brief
        assert "## road" in brief


class TestWhatItRefusesToSay:
    def test_an_operator_action_is_left_out(self, brief: str) -> None:
        """The manifest describes the whole surface including operator actions. A prompt
        listing one a session refuses costs a step to find out."""
        assert "change_bank_balance" not in brief

    def test_it_cannot_name_an_action_that_does_not_exist(self) -> None:
        """The regression, stated directly. `build_rail` was in the hand-written file
        after nttd deleted it; nothing generated from the manifest can do that."""
        assert "build_rail(" not in action_brief.build(FakeClient())

    def test_a_category_filter_drops_the_rest(self) -> None:
        """The full surface is around 120 actions. A road runner handed the rail, marine
        and aviation references pays for context it will never call."""
        narrowed = action_brief.build(FakeClient(), categories=("road",))
        assert "build_road_stop" in narrowed
        assert "build_rail_station" not in narrowed
        assert "set_loan" not in narrowed


class TestItStaysSmall:
    def test_it_is_far_shorter_than_what_it_replaced(self) -> None:
        """Against a real manifest this is about 6,000 characters for five categories,
        where the file it replaced was 47,000 for everything. The fixture here is tiny,
        so this only guards the shape: a reference that grew past the strategy it sits
        beside would be the old problem returning."""
        assert len(action_brief.build(FakeClient())) < 2_000

    def test_it_says_where_it_came_from(self, brief: str) -> None:
        """So a contestant debugging a refused action knows the list is authoritative
        and current, rather than wondering whether the prompt is stale."""
        assert "generated from the running GameScript" in brief

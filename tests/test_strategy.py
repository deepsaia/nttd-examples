"""The hand-written half of a prompt, and the guard against it rotting again.

A 47,000 character instruction file was deleted after one stale action name was spotted
in it. Validating it properly afterwards showed that was the wrong trade: of the 47K,
about 5K duplicated the manifest and about 42K was strategy the manifest cannot express.
Action names in it were 48 of 50 correct.

So the reference half is generated now and the strategy half is written by hand, short,
and per mode. These tests exist because the previous file had no tests at all, which is
how a worked example could recommend `is_truck` while the game wanted `is_truck_stop`
and nothing anywhere noticed.

The strongest checks here need a running nttd and skip without one, same as the route
contract.
"""

from __future__ import annotations

import os

import pytest
import requests

from agents import strategy_loader

BASE_URL = os.environ.get("NTTD_BASE_URL", "http://127.0.0.1:8000")

# Named in the strategy files and deleted from nttd, or never actions at all. Each one
# was really in the file this replaced.
NEVER_RECOMMEND = (
    "build_rail",       # deleted; build_path took over
    "build_road_line",  # deleted; build_path with a longer list
    "pathfind",         # never an action, it is an operator route
    "is_truck",         # the parameter is is_truck_stop
    "age_days",         # the field is age; the old patience rules keyed on a ghost
)


@pytest.fixture(params=strategy_loader.MODES)
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


class TestEveryModeHasStrategy:
    def test_it_loads(self, mode: str) -> None:
        assert strategy_loader.load(mode).strip()

    def test_it_has_a_description(self, mode: str) -> None:
        """An orchestrator picking between specialists reads these rather than loading
        four whole files into context."""
        assert strategy_loader.describe(mode)

    def test_the_frontmatter_is_stripped_from_the_prompt(self, mode: str) -> None:
        body = strategy_loader.load(mode)
        assert not body.startswith("---")
        assert "description:" not in body.split("\n")[0]

    def test_the_shared_strategy_is_included(self, mode: str) -> None:
        """The mode files deliberately do not repeat the universal rules, so loading one
        without the common half would drop them silently."""
        assert "Complete ONE working route" in strategy_loader.load(mode)

    def test_it_stays_short(self, mode: str) -> None:
        """The file this replaced was 47,000 characters. Length was not incidental to
        its problems: nobody could find the strategy inside it, so nobody corrected it."""
        assert len(strategy_loader.load(mode)) < 8_000

    def test_an_unknown_mode_raises(self) -> None:
        """Rather than returning empty. An agent silently running with no strategy looks
        like a bad model rather than a missing file."""
        with pytest.raises(ValueError, match="No strategy"):
            strategy_loader.load("hyperloop")


class TestItDoesNotRepeatWhatIsGenerated:
    def test_no_mode_documents_parameters(self, mode: str) -> None:
        """Reference belongs to action_brief. A hand-copied parameter list is exactly
        what went stale last time, and it goes stale silently."""
        body = strategy_loader.load(mode)
        for shape in ("(x, y)", "(tile,", "parameters:", "required:"):
            assert shape not in body, f"{mode} strategy is restating the manifest: {shape}"

    @pytest.mark.parametrize("dead", NEVER_RECOMMEND)
    def test_it_names_nothing_that_was_deleted_or_never_existed(
        self, mode: str, dead: str,
    ) -> None:
        body = strategy_loader.load(mode)
        # Split on word boundaries so build_rail does not match build_rail_track, which
        # is a real action and the only way to lay a junction stub.
        words = set(
            body.replace("`", " ").replace(",", " ").replace(".", " ")
                .replace("(", " ").replace(")", " ").split()
        )
        assert dead not in words, f"{mode} strategy names {dead}"

    def test_rail_mentions_the_actions_that_replaced_the_deleted_ones(self) -> None:
        """The rail prompt never mentioned build_path, never mentioned signals, and
        forbade build_rail_track, in the mode whose whole difficulty is track geometry."""
        body = strategy_loader.load("rail")
        assert "build_path" in body
        assert "build_rail_track" in body
        assert "connect_rail" in body


@pytest.fixture(scope="module")
def manifest() -> dict:
    """The live action surface. Module scope so one fetch serves every check."""
    try:
        response = requests.get(f"{BASE_URL}/v1/public/actions", timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        pytest.skip(f"No nttd at {BASE_URL} ({exc})")
    return response.json()["actions"]


class TestTheAdviceMatchesTheGame:
    """Checked against a running nttd, because a fixture would just agree with itself."""

    def test_every_action_the_strategy_names_exists(
        self, mode: str, manifest: dict,
    ) -> None:
        body = strategy_loader.load(mode)
        words = {
            w for w in body.replace("`", " ").replace(",", " ").replace(".", " ")
                          .replace("(", " ").replace(")", " ").split()
            if "_" in w and w.islower()
        }
        verbs = ("build", "connect", "remove", "buy", "find", "get", "set", "add")
        named = {w for w in words if w.split("_")[0] in verbs}
        unknown = sorted(named - set(manifest))
        assert not unknown, f"{mode} strategy names actions nttd does not have: {unknown}"

    def test_the_truck_stop_parameter_is_the_real_one(self, manifest: dict) -> None:
        """The specific drift that made every truck route silently a bus route."""
        params = manifest["build_road_stop"]["parameters"]
        assert "is_truck_stop" in params
        assert "is_truck" not in params
        assert "is_truck_stop" in strategy_loader.load("road")

"""Every route this client calls must exist on a real nttd.

**Why this file exists.** The client in this repository spent weeks calling
``/sessions/{id}/agents/connect`` and unprefixed session routes. Neither had existed for
some time: nttd moved everything under tier prefixes and dropped agent registration in
favour of the participant token. Every test passed throughout, because they mock the tool
layer, so the runners were validated against a fake that agreed with whatever the client
did.

Mocks cannot catch this by construction. The only thing that can is the server, so this
asks one: it reads ``/openapi.json`` and checks that every path the client uses is really
there. nttd solved the same problem internally by generating its action manifest from the
GameScript rather than hand-writing it; this is the contestant-side equivalent.

Skipped when no server is reachable, so it never blocks anybody. Run it against one:

    uv run pytest tests/test_route_contract.py -v          # NTTD_BASE_URL or localhost

That means a green suite is **not** evidence the client works. Only a green run of this
file is, which is stated here rather than left for somebody to discover the way I did.
"""

from __future__ import annotations

import os

import pytest
import requests

from agents import action_brief
from agents.nttd_client import PARTICIPANT_PREFIX, NttdClient

BASE_URL = os.environ.get("NTTD_BASE_URL", "http://127.0.0.1:8000")

# Every path the client builds, with the placeholders OpenAPI uses. Written out rather
# than scraped from the source, so adding a call to the client without adding it here is
# a visible omission rather than a silent one.
USED_PATHS = (
    "/step",
    "/step/reset",
    "/state/full",
    "/state/compact",
    "/state/gs/query",
    "/state/towns",
    "/state/industries",
    "/state/vehicles",
    "/state/stations",
    "/actions/available",
    "/actions/submit",
    "/actions/submit-batch",
    "/report",
)


def _openapi() -> dict:
    try:
        response = requests.get(f"{BASE_URL}/openapi.json", timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        pytest.skip(f"No nttd at {BASE_URL} ({exc}). Start one with: nttd server")
    return response.json()


@pytest.fixture(scope="module")
def spec() -> dict:
    """The whole OpenAPI document, so tests can read deprecation as well as presence."""
    return _openapi()


@pytest.fixture(scope="module")
def served(spec: dict) -> set[str]:
    """Every path nttd actually serves."""
    return set(spec["paths"])


class TestTheClientCallsRoutesThatExist:
    @pytest.mark.parametrize("suffix", USED_PATHS)
    def test_the_route_is_served(self, served: set[str], suffix: str) -> None:
        expected = f"{PARTICIPANT_PREFIX}/sessions/{{session_id}}{suffix}"
        assert expected in served, (
            f"The client calls {expected}, which nttd does not serve. Either nttd moved "
            f"it or this client is out of date; check GET {BASE_URL}/docs."
        )

    def test_the_client_builds_the_url_this_file_checks(self) -> None:
        """Otherwise the paths above could be right while the client builds something
        else entirely, which is exactly the failure being guarded against."""
        client = NttdClient(
            base_url=BASE_URL, session_id="ses_x", token="pt_x",
        )
        assert client._session_url == f"{BASE_URL}{PARTICIPANT_PREFIX}/sessions/ses_x"


class TestTheClientAvoidsTheDeprecatedSurface:
    """nttd still serves every route untiered, marked deprecated, for old callers.

    That shim is why the drift here was quiet rather than loud. The old client called
    `/sessions/{id}/actions/submit`, which still resolves, so runners half worked: some
    calls landed on the deprecated surface and `agents/connect` simply 404ed. Had the
    shim not existed, the first request would have failed and somebody would have fixed
    it that day.

    Same router object included twice, so the handlers and the token checks are
    identical; this is not a way around anything. It is a second name for the same
    surface, and an example should teach the one that is not deprecated.
    """

    @pytest.mark.parametrize("suffix", USED_PATHS)
    def test_the_route_the_client_uses_is_not_deprecated(self, spec: dict, suffix: str) -> None:
        path = f"{PARTICIPANT_PREFIX}/sessions/{{session_id}}{suffix}"
        operations = spec["paths"][path]
        deprecated = [
            verb for verb, op in operations.items()
            if isinstance(op, dict) and op.get("deprecated")
        ]
        assert not deprecated, f"{path} is deprecated for {deprecated}"

    def test_the_untiered_twin_is_deprecated(self, spec: dict) -> None:
        """Stated so that if nttd ever drops the shim, this test says so plainly rather
        than a runner failing somewhere less obvious."""
        untiered = "/sessions/{session_id}/actions/submit"
        if untiered not in spec["paths"]:
            pytest.skip("nttd has dropped the untiered shim, which is the intended end state")
        assert spec["paths"][untiered]["post"]["deprecated"] is True

    def test_the_client_no_longer_registers_an_agent(self) -> None:
        """Registration went when the participant token arrived: the token says who you
        are, so a separate handshake was a second answer to one question. This one did
        not merely move, it was deleted, so the old client 404ed on every start."""
        assert not hasattr(NttdClient, "register")
        assert not hasattr(NttdClient, "unregister")

    def test_no_client_url_omits_the_tier(self) -> None:
        client = NttdClient(base_url=BASE_URL, session_id="ses_x", token="pt_x")
        assert PARTICIPANT_PREFIX in client._session_url


class TestTheTokenIsSentAndTheCompanyIsNot:
    def test_every_request_carries_the_token(self) -> None:
        client = NttdClient(base_url=BASE_URL, session_id="ses_x", token="pt_secret")
        assert client._headers == {"X-Participant-Token": "pt_secret"}

    def test_no_method_takes_a_company_id(self) -> None:
        """The token decides the company and the server overrides what a body claims, so
        a company argument would be a parameter that silently does nothing."""
        import inspect

        for name, method in inspect.getmembers(NttdClient, inspect.isfunction):
            if name.startswith("__"):
                continue
            assert "company_id" not in inspect.signature(method).parameters, (
                f"NttdClient.{name} takes company_id, which the server ignores"
            )


class TestTheActionsTheClientDocuments:
    """Names mentioned in the client's own prose have to be real actions."""

    def test_the_manifest_is_reachable_and_names_them(self, served: set[str]) -> None:
        catalogue = f"{PARTICIPANT_PREFIX}/sessions/{{session_id}}/actions/available"
        assert catalogue in served

    def test_no_docstring_names_an_action_that_was_deleted(self) -> None:
        """`build_road`, `build_rail` and `build_road_line` were removed from nttd: they
        were `build_path` with a shorter list. A docstring still recommending one would
        send a contestant at a 400."""
        import agents.nttd_client as module

        source = module.__doc__ or ""
        for method in vars(NttdClient).values():
            source += getattr(method, "__doc__", None) or ""
        # Split into words rather than matched with a pattern: `build_road` must not
        # also flag `build_road_stop`, and word splitting says that plainly.
        words = set(source.replace("`", " ").replace(",", " ").replace(".", " ").split())
        for gone in ("build_road", "build_rail", "build_road_line"):
            assert gone not in words, f"a docstring names {gone}, which nttd removed"


class TestTheGeneratedPromptOnlyNamesRealActions:
    """The one claim about `action_brief` worth checking, and it needs a server.

    The reference in a prompt used to be hand-written: 47,000 characters that had drifted
    far enough to still recommend `build_rail`, which nttd deleted. It is generated now,
    and the point of generating it is that it cannot name an action the server does not
    have. Only the server can confirm that; a fixture would just agree with itself.
    """

    def test_every_action_it_names_is_one_the_session_accepts(self) -> None:
        """Checked against `actions/available`, not the manifest `build` reads from.
        Comparing a rendering with its own source would only prove the rendering is
        faithful; this proves the brief is bounded by what the session will really take.
        """
        client = NttdClient(base_url=BASE_URL, session_id="ses_x", token="pt_x")
        brief = action_brief.build(client)

        playable = {
            name for names in client.available_actions().values()
            if isinstance(names, list) for name in names
        }
        named = {
            line.split("**")[1].split("(")[0]
            for line in brief.splitlines() if line.startswith("**")
        }
        assert named, "the brief named no actions at all"
        assert named <= playable, (
            f"the brief offers {sorted(named - playable)}, which this session refuses"
        )

    def test_it_leaves_out_actions_this_session_would_refuse(self) -> None:
        """The manifest describes operator actions too. Listing one costs a step to
        find out it is refused."""
        client = NttdClient(base_url=BASE_URL, session_id="ses_x", token="pt_x")
        brief = action_brief.build(client)
        assert "change_bank_balance" not in brief

    def test_a_category_filter_narrows_it(self) -> None:
        """The full surface is around 120 actions. A road runner handed the rail, marine
        and aviation references pays for context it will never call."""
        client = NttdClient(base_url=BASE_URL, session_id="ses_x", token="pt_x")
        roads = action_brief.build(client, categories=("road",))
        assert "build_road_stop" in roads
        assert "build_rail_station" not in roads


class TestTheRunnerActsOnRefusals:
    """The dead read that started this, now live.

    `_log_refusals` read result["action_results"], which StepResult did not carry, so
    every refusal passed unnoticed and the runner had no way to stop proposing the same
    rejected action. nttd now returns the outcome of each action on the step that
    flushed it.
    """

    def test_the_server_returns_action_results_on_a_step(self, spec: dict) -> None:
        fields = spec["components"]["schemas"]["StepResult"]["properties"]
        assert "action_results" in fields, (
            "nttd does not return per-action outcomes, so a runner cannot learn from a "
            "refusal: the observation alone cannot distinguish a refused action from "
            "one that was never sent."
        )

    def test_a_result_names_its_action(self, spec: dict) -> None:
        fields = spec["components"]["schemas"]["ActionResult"]["properties"]
        assert "action_type" in fields
        assert "error" in fields

    def test_the_runner_feeds_refusals_back_into_the_next_decision(self) -> None:
        """Logging a refusal is not acting on one. The planner has to see it, or it
        proposes the same action next step."""
        import inspect

        from examples import langgraph_runner

        assert "refusals" in inspect.getsource(langgraph_runner.play)
        note = langgraph_runner._refusal_note(
            [{"action_type": "build_dock", "error": "Invalid tile ID: 1"}],
        )
        assert "build_dock" in note
        assert "Invalid tile ID" in note
        assert "Do not repeat" in note

    def test_no_refusals_adds_nothing_to_the_prompt(self) -> None:
        from examples import langgraph_runner

        assert langgraph_runner._refusal_note([]) == ""

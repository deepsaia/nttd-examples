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
import re

import pytest
import requests

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
        for gone in (r"\bbuild_road\b", r"\bbuild_rail\b", r"\bbuild_road_line\b"):
            assert not re.search(gone, source), f"docstring names {gone}, which nttd removed"

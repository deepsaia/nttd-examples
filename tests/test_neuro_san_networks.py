"""The agent networks are well formed, and the traps stay in the coded tools.

A HOCON that neuro-san cannot load fails at run time, in a session that costs game days to
reach, so it is worth catching here. The rest of this file guards the design rather than the
syntax: what makes this system better than the LangGraph one it replaced is that the
expensive lessons live in deterministic Python, not in five sets of prose that drift.
"""

from __future__ import annotations

import pathlib

import pytest

pyhocon = pytest.importorskip("pyhocon")

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_REGISTRIES = _ROOT / "registries"
NETWORKS = ("nttd_air", "nttd_ground", "nttd_portfolio")


def _network(name: str) -> dict:
    """One network as neuro-san composes it, with the shared base included."""
    base = (_REGISTRIES / "nttd_aaosa.hocon").read_text()
    body = (_REGISTRIES / f"{name}.hocon").read_text()
    body = body.replace('include "registries/nttd_aaosa.hocon"', "")
    return pyhocon.ConfigFactory.parse_string(f"{base}\n{body}")


@pytest.mark.parametrize("name", NETWORKS)
def test_the_network_parses(name: str) -> None:
    assert _network(name)["tools"], name


@pytest.mark.parametrize("name", NETWORKS)
def test_the_front_man_takes_no_parameters(name: str) -> None:
    """That is what identifies it as the front man, and it may not be a coded tool."""
    front = _network(name)["tools"][0]
    assert "parameters" not in front["function"], name
    assert front.get("class", None) is None, f"{name}: a front man cannot be a CodedTool"


@pytest.mark.parametrize("name", NETWORKS)
def test_every_named_tool_exists(name: str) -> None:
    """A tool named in an agent's list but never defined fails only when it is reached."""
    tools = _network(name)["tools"]
    defined = {tool["name"] for tool in tools}
    for tool in tools:
        for wanted in tool.get("tools", []):
            assert wanted in defined, f"{name}: {tool['name']} calls undefined {wanted}"


@pytest.mark.parametrize("name", NETWORKS)
def test_every_coded_tool_resolves_to_a_class(name: str) -> None:
    import importlib

    # The tools subclass neuro_san's CodedTool, so this one needs the extra installed:
    #   uv sync --extra neuro-san
    pytest.importorskip("neuro_san")

    for tool in _network(name)["tools"]:
        target = tool.get("class", None)
        if not target:
            continue
        module_name, _, class_name = target.rpartition(".")
        module = importlib.import_module(f"agents.neuro_san.coded_tools.{module_name}")
        assert hasattr(module, class_name), target


@pytest.mark.parametrize("name", NETWORKS)
def test_the_ground_rules_are_shared_not_restated(name: str) -> None:
    """Five copies of one rule become five different rules."""
    body = (_REGISTRIES / f"{name}.hocon").read_text()
    assert "${nttd_ground_rules}" in body, name
    assert "400 of the 1,000 points" not in body, f"{name} restates a shared rule"


def test_the_expensive_lessons_live_in_python() -> None:
    """Each of these cost a run, and each is now a deterministic check rather than advice.

    A prompt asking a model to remember them is the design this replaces: it varies run to
    run, and nothing catches it when it forgets.
    """
    tools = _ROOT / "agents" / "neuro_san" / "coded_tools"
    assert "tiles_reachable" in (tools / "verify_reachable.py").read_text()
    assert "within_coverage" in (tools / "rank_sites.py").read_text()
    assert "DAYS_TO_PAY_BACK" in (tools / "buy_and_dispatch.py").read_text()
    assert "loan_costs_score" in (tools / "read_position.py").read_text()

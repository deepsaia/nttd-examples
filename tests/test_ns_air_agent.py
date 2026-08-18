"""The contract of the ns_air_agent network, as opposed to what its tools do.

Every assertion here failed at least once in a real run, and each one names the run that paid
for it. These are the rules that a later change is most likely to break quietly, because
breaking them produces a network that loads, answers, and plays badly.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
import sysconfig

import pytest

pyhocon = pytest.importorskip("pyhocon")

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_REGISTRIES = _ROOT / "registries"
_TOOLS = _ROOT / "agents" / "neuro_san" / "coded_tools"

NETWORK = "ns_air_agent"


def _network(name: str = NETWORK) -> dict:
    """The registry as neuro-san composes it, with the shared base included."""
    base = (_REGISTRIES / "ns_common.hocon").read_text()
    body = (_REGISTRIES / f"{name}.hocon").read_text()
    body = body.replace('include "registries/ns_common.hocon"', "")
    return pyhocon.ConfigFactory.parse_string(f"{base}\n{body}")


def _entries() -> list[dict]:
    return list(_network()["tools"])


def _agents() -> list[dict]:
    return [entry for entry in _entries() if entry.get("class", None) is None]


def _coded() -> list[dict]:
    return [entry for entry in _entries() if entry.get("class", None)]


# --- the registry loads at all ---------------------------------------------------------------


def test_the_network_is_registered() -> None:
    """A network absent from the manifest is not served, however good it is."""
    manifest = (_REGISTRIES / "manifest.hocon").read_text()
    assert f"{NETWORK}.hocon" in manifest, "the network is not served"
    assert (_REGISTRIES / f"{NETWORK}.hocon").exists()


def test_the_front_man_is_a_valid_front_man() -> None:
    """neuro-san identifies it by having no parameters, and forbids it being a coded tool."""
    front = _entries()[0]
    assert front["name"] == "AirCompany"
    assert "parameters" not in front["function"]
    assert front.get("class", None) is None


def test_every_referenced_tool_is_defined() -> None:
    """A tool named in an agent's list but never defined fails only when it is reached."""
    defined = {entry["name"] for entry in _entries()}
    for entry in _entries():
        for wanted in entry.get("tools", []):
            assert wanted in defined, f"{entry['name']} calls undefined {wanted}"


def test_every_coded_tool_resolves_to_a_class() -> None:
    """The class reference is a string, so nothing checks it until the tool is called."""
    sys.path.insert(0, str(_TOOLS))
    try:
        for entry in _coded():
            target = entry["class"]
            module_name, _, class_name = target.rpartition(".")
            module = importlib.import_module(module_name)
            assert hasattr(module, class_name), target
    finally:
        sys.path.remove(str(_TOOLS))


# --- the defect that was ending every run ----------------------------------------------------


def test_cross_turn_memory_is_allowed_upstream() -> None:
    """Without this declaration the network has no memory at all.

    neuro-san's SlyDataRedactor is security-by-default: "when nothing is listed, it is
    equivalent to ... false". With nothing declared, sly_data never returns to the client, so
    the route just built, the ledger of refusals and the cached survey all die at the turn
    boundary. That is why an earlier network could not correct itself and submitted one refused
    purchase 35 times.
    """
    allowed = _network()["allow"]["to_upstream"]["sly_data"]

    sys.path.insert(0, str(_TOOLS))
    try:
        from ns import constants  # noqa: PLC0415
    finally:
        sys.path.remove(str(_TOOLS))

    for carried in constants.ALLOWED:
        assert carried in allowed, f"{carried} is kept between turns but never returns upstream"


def test_credentials_and_live_objects_never_go_upstream() -> None:
    """A token in a chat payload is a leak, and a lock cannot be serialised at all."""
    allowed = set(_network()["allow"]["to_upstream"]["sly_data"])

    sys.path.insert(0, str(_TOOLS))
    try:
        from ns import constants  # noqa: PLC0415
    finally:
        sys.path.remove(str(_TOOLS))

    for secret in constants.CREDENTIALS:
        assert secret not in allowed, f"{secret} must not leave the tools"
    for local in constants.TURN_LOCAL:
        assert local not in allowed, f"{local} is turn local and must not cross"


# --- rules that keep the design honest -------------------------------------------------------


def test_only_three_tools_move_the_clock() -> None:
    """Planning is free and a batch has no ceiling, so a turn should cost one or two days.

    A plan_ tool that stepped would spend a day per staged action, which is how a 366 day
    budget gets eaten by paperwork: the best hand-played run spent 15 days on an opening that
    needs 3.
    """
    allowed_to_step = {"commit_plan.py", "advance_days.py", "set_loan_to.py", "gateway.py"}
    stepping = {
        path.name for path in _TOOLS.rglob("*.py")
        if ".step(" in path.read_text()
    }
    assert stepping <= allowed_to_step, f"these move the clock and should not: {stepping - allowed_to_step}"


def test_no_tool_diagnoses_a_vehicle_from_idle_reason() -> None:
    """idle_reason says at_station for an aircraft loading at a gate, which is normal.

    Treating any non-empty value as a fault made a healthy fleet read as a wall of problems,
    the strategist was told to repair before expanding, and the repair tool would eventually
    have sold working aircraft. The engine's own problems list is the source instead.
    """
    # Looks for the READ rather than the word, because explaining in prose why the field is not
    # used is exactly what these modules should do.
    reads = ('get("idle_reason")', "get('idle_reason')", '["idle_reason"]', "['idle_reason']")
    for path in _TOOLS.rglob("*.py"):
        text = path.read_text()
        for read in reads:
            assert read not in text, f"{path.name} reads idle_reason: {read}"


def test_no_tool_module_shadows_the_standard_library() -> None:
    """neuro-san puts AGENT_TOOL_PATH on sys.path, so a clash breaks the whole process.

    Measured: a module named inspect.py there shadowed the standard library's inspect and broke
    leaf_common with a circular import of logging, which reads as a neuro-san fault and is not
    one.
    """
    stdlib = sysconfig.get_paths()["stdlib"]
    for path in _TOOLS.rglob("*.py"):
        if path.stem == "__init__":
            continue
        try:
            spec = importlib.util.find_spec(path.stem)
        except (ImportError, ValueError):
            continue
        if spec and spec.origin and spec.origin.startswith(stdlib):
            pytest.fail(f"{path.name} shadows the standard library module {path.stem}")


def test_every_tool_module_loads_as_a_flat_sibling() -> None:
    """That is how neuro-san loads them when AGENT_TOOL_PATH_ONLY is true.

    Which it is, because otherwise a class reference in a registry resolves as a fully
    qualified import from anywhere on PYTHONPATH.
    """
    sys.path.insert(0, str(_TOOLS))
    try:
        for package in ("ns", "ns_air"):
            for path in sorted((_TOOLS / package).glob("*.py")):
                if path.stem != "__init__":
                    importlib.import_module(f"{package}.{path.stem}")
    finally:
        sys.path.remove(str(_TOOLS))


# --- the shape the strategy needs ------------------------------------------------------------


def test_the_strategist_can_see_everything_and_act_once() -> None:
    """It is the front man because neuro-san keeps only the front man's history across turns.

    Any other agent starts each turn with amnesia, and strategy is the one job that cannot
    afford that.
    """
    front = _entries()[0]
    held = set(front["tools"])
    for needed in ("read_situation", "score_report", "fleet_report", "route_report"):
        assert needed in held, f"the strategist cannot see {needed}"
    for needed in ("commit_plan", "advance_days", "note_decision"):
        assert needed in held, f"the strategist cannot {needed}"
    for worker in ("Scout", "Builder", "FleetGrowth", "FleetCare"):
        assert worker in held, f"the strategist cannot reach {worker}"


def test_the_strategist_runs_a_stronger_model_than_the_workers() -> None:
    """The trade-offs are at the top; scoring sites and formatting tables are not."""
    front = _entries()[0]
    assert front["llm_config"]["model_name"] == "claude-opus"
    assert _network()["llm_config"]["model_name"] == "claude-sonnet"


def test_instructions_are_short_because_the_rules_live_in_the_tools() -> None:
    """A page of prose is a page a fresh sub-agent may skip.

    Every worker is recreated each turn, so anything essential belongs in a tool that enforces
    it or in the tool's own description, not in an instruction block.
    """
    for entry in _agents():
        words = len(entry["instructions"].split())
        assert words < 400, f"{entry['name']} has {words} words of instructions"


def test_the_ground_rules_are_shared_not_restated() -> None:
    """Five copies of one rule become five different rules."""
    body = (_REGISTRIES / f"{NETWORK}.hocon").read_text()
    shared = (_REGISTRIES / "ns_common.hocon").read_text()
    assert "${ns_ground_rules}" in body, "the shared rules must be substituted, not copied"
    assert "${ns_worker_conduct}" in body
    # A distinctive line from the shared block must appear there and NOT be duplicated here.
    marker = "A build action returning success is NOT a working route"
    assert marker in shared
    assert marker not in body, "the ground rules are copied into the network instead of included"


def test_air_is_its_own_network_with_its_own_tools() -> None:
    """The four modes are different games, so the judgement is not shared.

    Air decides on population against airport coverage; road on many short pairs because one
    saturates; water on which docks share a body of water; rail on platform axis, depot
    junction and rail type. Only the plumbing under ns/ is common.
    """
    air_only = [entry["class"] for entry in _coded() if entry["class"].startswith("ns_air.")]
    shared = [entry["class"] for entry in _coded() if entry["class"].startswith("ns.")]
    assert len(air_only) >= 8, "air should carry its own siting, fleet and care tools"
    assert len(shared) >= 8, "the plumbing should be shared with the other three modes"

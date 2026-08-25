"""The contract every nttd agent network holds to, as opposed to what its tools do.

Every assertion here failed at least once in a real run, and each one names the run that paid
for it. These are the rules that a later change is most likely to break quietly, because
breaking them produces a network that loads, answers, and plays badly.

Parametrised over whatever the manifest serves rather than written against one network. The
rules are not about air or rail: they are about how neuro-san loads a network, what survives a
turn boundary, and where a rule is allowed to live. A second network that had to rediscover
them would rediscover about half.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
import sysconfig

import pytest

pyhocon = pytest.importorskip("pyhocon")

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_REGISTRIES = _ROOT / "agents" / "neuro_san" / "registries"
_TOOLS = _ROOT / "agents" / "neuro_san" / "coded_tools"

def _served() -> list[str]:
    """The networks the manifest actually serves, which is the list these rules apply to.

    Read from the manifest rather than listed here, so enabling a network is one edit and
    cannot silently escape the contract.
    """
    manifest = pyhocon.ConfigFactory.parse_string(
        (_REGISTRIES / "manifest.hocon").read_text()
    )
    # pyhocon keeps the quotes as part of a quoted key, so they are stripped here rather
    # than every caller receiving a name with punctuation in it.
    return sorted(
        name.strip('"').removesuffix(".hocon")
        for name, enabled in manifest.items() if enabled
    )


NETWORKS = _served()

# The foundation plus every network's own package. Flat-sibling importability is a property of
# a directory rather than of a registry, so it is checked per package.
PACKAGES = ["ns", *NETWORKS]


def _network(name: str) -> dict:
    """The registry as neuro-san composes it, with the shared base included."""
    base = (_REGISTRIES / "ns_common.hocon").read_text()
    body = (_REGISTRIES / f"{name}.hocon").read_text()
    body = body.replace('include "agents/neuro_san/registries/ns_common.hocon"', "")
    return pyhocon.ConfigFactory.parse_string(f"{base}\n{body}")


def _entries(name: str) -> list[dict]:
    return list(_network(name)["tools"])


def _agents(name: str) -> list[dict]:
    return [entry for entry in _entries(name) if entry.get("class", None) is None]


def _coded(name: str) -> list[dict]:
    return [entry for entry in _entries(name) if entry.get("class", None)]


@pytest.fixture(params=NETWORKS)
def network(request: pytest.FixtureRequest) -> str:
    """Each served network in turn, so a new one inherits every rule below."""
    return str(request.param)


def test_the_manifest_serves_something() -> None:
    """An empty manifest would make every parametrised test below vacuously pass."""
    assert NETWORKS, "the manifest serves no network, so nothing here is being checked"
    assert "ns_air_agent" in NETWORKS
    assert "ns_rail_agent" in NETWORKS


# --- the registry loads at all ---------------------------------------------------------------


def test_the_network_is_registered(network: str) -> None:
    """A network absent from the manifest is not served, however good it is."""
    assert (_REGISTRIES / f"{network}.hocon").exists()


def test_the_front_man_is_a_valid_front_man(network: str) -> None:
    """neuro-san identifies it by having no parameters, and forbids it being a coded tool.

    It is also the FIRST entry, because neuro-san takes the first as the front man, and it is
    the front man because neuro-san preserves only that agent's history across turns.
    """
    front = _entries(network)[0]
    assert front["name"].endswith("Company"), "the front man is the company's strategist"
    assert "parameters" not in front["function"]
    assert front.get("class", None) is None


def test_every_referenced_tool_is_defined(network: str) -> None:
    """A tool named in an agent's list but never defined fails only when it is reached."""
    defined = {entry["name"] for entry in _entries(network)}
    for entry in _entries(network):
        for wanted in entry.get("tools", []):
            assert wanted in defined, f"{entry['name']} calls undefined {wanted}"


def test_every_coded_tool_resolves_to_a_class(network: str) -> None:
    """The class reference is a string, so nothing checks it until the tool is called."""
    sys.path.insert(0, str(_TOOLS))
    try:
        for entry in _coded(network):
            target = entry["class"]
            module_name, _, class_name = target.rpartition(".")
            module = importlib.import_module(module_name)
            assert hasattr(module, class_name), target
    finally:
        sys.path.remove(str(_TOOLS))


# --- the defect that was ending every run ----------------------------------------------------


def test_cross_turn_memory_is_allowed_upstream(network: str) -> None:
    """Without this declaration the network has no memory at all.

    neuro-san's SlyDataRedactor is security-by-default: "when nothing is listed, it is
    equivalent to ... false". With nothing declared, sly_data never returns to the client, so
    the route just built, the ledger of refusals and the cached survey all die at the turn
    boundary. That is why an earlier network could not correct itself and submitted one refused
    purchase 35 times.
    """
    allowed = _network(network)["allow"]["to_upstream"]["sly_data"]

    sys.path.insert(0, str(_TOOLS))
    try:
        from ns import constants  # noqa: PLC0415
    finally:
        sys.path.remove(str(_TOOLS))

    for carried in constants.ALLOWED:
        assert carried in allowed, f"{carried} is kept between turns but never returns upstream"


def test_credentials_and_live_objects_never_go_upstream(network: str) -> None:
    """A token in a chat payload is a leak, and a lock cannot be serialised at all."""
    allowed = set(_network(network)["allow"]["to_upstream"]["sly_data"])

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


@pytest.mark.parametrize("package", PACKAGES)
def test_every_tool_module_loads_as_a_flat_sibling(package: str) -> None:
    """That is how neuro-san loads them when AGENT_TOOL_PATH_ONLY is true.

    Which it is, because otherwise a class reference in a registry resolves as a fully
    qualified import from anywhere on PYTHONPATH.
    """
    sys.path.insert(0, str(_TOOLS))
    try:
        for path in sorted((_TOOLS / package).glob("*.py")):
            if path.stem != "__init__":
                importlib.import_module(f"{package}.{path.stem}")
    finally:
        sys.path.remove(str(_TOOLS))


# --- the shape the strategy needs ------------------------------------------------------------


def test_the_strategist_can_see_everything_and_act_once(network: str) -> None:
    """It is the front man because neuro-san keeps only the front man's history across turns.

    Any other agent starts each turn with amnesia, and strategy is the one job that cannot
    afford that.
    """
    front = _entries(network)[0]
    held = set(front["tools"])
    for needed in ("read_situation", "fleet_report", "route_report"):
        assert needed in held, f"the strategist cannot see {needed}"
    for needed in ("commit_plan", "advance_days", "note_decision"):
        assert needed in held, f"the strategist cannot {needed}"
    for worker in ("Scout", "Builder", "FleetGrowth", "FleetCare"):
        assert worker in held, f"the strategist cannot reach {worker}"


def test_the_strategist_runs_a_stronger_model_than_the_workers(network: str) -> None:
    """The trade-offs are at the top; scoring sites and formatting tables are not."""
    front = _entries(network)[0]
    assert front["llm_config"]["model_name"] == "claude-opus"
    assert _network(network)["llm_config"]["model_name"] == "claude-sonnet"


# --- the client and the server have to agree about how long a turn may take -------------------


def test_the_client_waits_longer_than_the_server_is_allowed_to_take(network: str) -> None:
    """The defect that ended a live run, and the reason it was so hard to read.

    The client's stream timeout was 1800 seconds against a server allowed 6000. A turn the
    server was still entitled to be working on had its stream torn down underneath it, and what
    came back was neuro-san's connectivity help text: ten suggestions about ports, protocols and
    docker, not one of which was the problem. Nothing in either file mentioned the other, so
    the two numbers could drift apart without anything noticing.
    """
    from examples import neuro_san_play  # noqa: PLC0415

    allowed = int(_network(network)["max_execution_seconds"])
    assert neuro_san_play.STREAM_TIMEOUT_SECONDS > allowed, (
        f"the client gives up after {neuro_san_play.STREAM_TIMEOUT_SECONDS}s while "
        f"{network} may take {allowed}s, so a long turn will be killed by its own client"
    )


def test_a_retryable_provider_failure_does_not_end_the_turn(network: str) -> None:
    """A session is hours long, and one unlucky rate limit in the middle of it should not cost
    the whole run. neuro-san's default is 3 attempts; this asks for more, bounded by
    max_execution_seconds so it cannot become an unbounded retry loop.
    """
    assert int(_network(network)["max_attempts"]) >= 3


def test_the_deprecated_spelling_of_the_step_limit_is_not_used(network: str) -> None:
    """`max_iterations` is deprecated for `max_steps`, and it reads as the wrong thing anyway.

    It sounds like a count of iterations. What it bounds is the whole graph execution: every
    tool call and every model call, sequential nodes each taking a super-step.
    """
    for name in (f"{network}.hocon", "ns_common.hocon"):
        for line in (_REGISTRIES / name).read_text().splitlines():
            assert not line.strip().startswith("max_iterations"), (
                f"{name} uses the deprecated max_iterations; use max_steps"
            )


def test_the_server_side_request_timeout_is_left_alone(network: str) -> None:
    """It defaults to 0, meaning no timeout, and it caps a single client chat request.

    Setting it would abort exactly the long turns max_execution_seconds exists to allow. The
    timeout that needs raising when a run dies on a slow turn is the client's.
    """
    body = (_REGISTRIES / f"{network}.hocon").read_text()
    shared = (_REGISTRIES / "ns_common.hocon").read_text()
    for text in (body, shared):
        for line in text.splitlines():
            bare = line.strip()
            if bare.startswith("request_timeout_seconds"):
                pytest.fail(f"request_timeout_seconds is set: {bare}")


def test_the_execution_bounds_are_shared_not_copied(network: str) -> None:
    """Two networks with their own copies of one number is two numbers waiting to disagree."""
    body = (_REGISTRIES / f"{network}.hocon").read_text()
    for setting in ("max_execution_seconds", "max_message_history", "max_attempts",
                    "max_steps"):
        for line in body.splitlines():
            assert not line.strip().startswith(f"{setting} ="), (
                f"{network} sets {setting} itself; it belongs in ns_common.hocon"
            )
        assert _network(network)[setting] is not None


# A CHOSEN ceiling, not a measured one, and said so because the difference matters: the numbers
# in this codebase are either read from the game's source or derived from the session, and a
# style guard is neither. It exists to stop prose creeping back in once the rules have been moved
# into tools, and the figure includes the shared ground rules every agent carries.
INSTRUCTION_WORD_CEILING = 450


def test_instructions_are_short_because_the_rules_live_in_the_tools(network: str) -> None:
    """A page of prose is a page a fresh sub-agent may skip.

    Every worker is recreated each turn, so anything essential belongs in a tool that enforces it
    or in the tool's own description, where it is read at the moment of use.
    """
    for entry in _agents(network):
        words = len(entry["instructions"].split())
        assert words < INSTRUCTION_WORD_CEILING, (
            f"{entry['name']} has {words} words of instructions"
        )


def test_the_ground_rules_are_shared_not_restated(network: str) -> None:
    """Five copies of one rule become five different rules."""
    body = (_REGISTRIES / f"{network}.hocon").read_text()
    shared = (_REGISTRIES / "ns_common.hocon").read_text()
    assert "${ns_ground_rules}" in body, "the shared rules must be substituted, not copied"
    assert "${ns_worker_conduct}" in body
    # A distinctive line from the shared block must appear there and NOT be duplicated here.
    marker = "A build action returning success is NOT a working route"
    assert marker in shared
    assert marker not in body, "the ground rules are copied into the network instead of included"


def test_each_mode_is_its_own_network_with_its_own_tools(network: str) -> None:
    """The four modes are different games, so the judgement is not shared.

    Air decides on population against airport coverage; road on many short pairs because one
    saturates; water on which docks share a body of water; rail on platform axis, depot
    junction and rail type. Only the plumbing under ns/ is common.
    """
    coded = _coded(network)
    own = [e["class"] for e in coded if e["class"].startswith(f"{network}.")]
    shared = [e["class"] for e in coded if e["class"].startswith("ns.")]
    assert len(own) >= 8, f"{network} should carry its own siting, fleet and care tools"
    assert len(shared) >= 8, "the plumbing should be shared with the other three modes"


def test_the_network_does_not_compute_its_own_score(network: str) -> None:
    """Scoring is the benchmark's business, not the player's.

    A score_report tool once decomposed OpenTTD's 1000 point rating into its nine components so
    the strategist could optimise them. Two things were wrong with it. It reimplemented arithmetic
    the engine already performs and reports as performance_rating, and its own docstring conceded
    the estimate "can legitimately disagree" with that number: a tool that knowingly contradicts
    the authority will mislead. And a benchmark meant to measure how well a company is run should
    not hand the player a breakdown of the marking scheme, which measures something else.

    The game's own rating still appears in read_situation, because that is the engine reporting
    its own number rather than this network deriving one.
    """
    for entry in _entries(network):
        assert entry["name"] != "score_report"
        assert "score_report" not in entry.get("tools", [])

    tools = list(_TOOLS.rglob("*.py"))
    assert not [path for path in tools if path.stem == "score_report"]
    for path in tools:
        text = path.read_text()
        assert "SCORE_DELIVERED" not in text, f"{path.name} reimplements the rating weights"


def test_a_networks_own_tools_live_in_a_package_named_after_it(network: str) -> None:
    """So a network is one directory, not two with different names.

    The tools were under `coded_tools/ns_air` while the network was `ns_air_agent`, which
    reads as two things until you have opened both. `ns` is the exception and stays: it is
    the foundation the four modes share, and naming it after any one of them would be a lie.

    Written against the manifest rather than against air alone, so water, road and rail
    inherit the rule rather than each rediscovering it.
    """
    own = f"{network}."
    for entry in _coded(network):
        target = entry["class"]
        assert target.startswith(("ns.", own)), (
            f"{entry['name']} is {target}: a coded tool belongs either to the shared "
            f"foundation (ns.) or to this network ({own})"
        )


def test_the_package_the_registry_names_is_the_one_on_disk(network: str) -> None:
    """A class reference is a string, so nothing checks it until the tool is called."""
    assert (_TOOLS / network).is_dir(), f"no coded_tools/{network} for the registry to load"
    assert (_TOOLS / "ns").is_dir(), "the shared foundation should still be shared"

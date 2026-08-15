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


@pytest.mark.parametrize("name", NETWORKS)
def test_the_network_controls_its_own_clock(name: str) -> None:
    """When to act and how long to wait is judgement, so it belongs to the agent.

    An earlier version woke the agent every 30 game days from a runner, which is a number
    lifted from how the game was played by hand and imposed on every map. Deciding that a
    route needs 90 days to prove itself, or that a vehicle needs 10 to leave its depot, is
    part of what the benchmark measures.
    """
    network = _network(name)
    front = network["tools"][0]
    assert "let_time_pass" in front["tools"], f"{name}: the front man cannot advance time"

    defined = {tool["name"]: tool for tool in network["tools"]}
    assert defined["let_time_pass"].get("class", None) == "let_time_pass.LetTimePass"


def test_there_is_no_runner_reimplementing_the_client() -> None:
    """neuro-san's own client carries sly_data and streams a whole conversation.

    A file here that opened its own session and looped would be a second copy of that, and
    the copy is the one nobody maintains.
    """
    assert not (_ROOT / "examples" / "neuro_san_runner.py").exists()


class TestTheLoop:
    """A benchmark run is a loop, and the loop's only decision is whether the run is over.

    Two failures this is between. A single conversation asked to play a whole game year
    grows its own context until the model loses the start of the run. A loop that decides
    when the agent should act takes the judgement the benchmark is measuring and puts it in
    a constant: an earlier version woke the agent every 30 game days, a number lifted from
    hand play and imposed on every map.
    """

    def test_the_loop_ends_when_the_session_does(self) -> None:
        import inspect

        from examples import neuro_san_play

        source = inspect.getsource(neuro_san_play.play)
        assert "_status(session)" in source, "the game says when it is over, not this loop"
        assert "ended" in source

    def test_the_loop_does_not_schedule_the_agent(self) -> None:
        """No cadence, no step budget: only a backstop against a network making no progress."""
        import inspect

        from examples import neuro_san_play

        source = inspect.getsource(neuro_san_play)
        body = source.split('"""', 2)[2]
        assert "decide_every" not in body
        assert "max_turns" in body, "a runaway guard is fine; a schedule is not"

    def test_turns_carry_the_conversation_forward(self) -> None:
        """Otherwise every turn is amnesiac and re-derives what it already decided."""
        import inspect

        from examples import neuro_san_play

        assert "chat_context" in inspect.getsource(neuro_san_play.play)

    def test_the_networks_know_that_acting_costs_a_day(self) -> None:
        """Stepped play executes a batch and then advances one step, so building costs time."""
        for name in NETWORKS:
            body = (_REGISTRIES / f"{name}.hocon").read_text()
            assert "advances the world by one day" in body, name


class TestTheGateway:
    """The verbs have to match nttd's routes, and a mismatch only shows up at run time.

    Measured against a live session: `state/gs/query` answered 405 Method Not Allowed to a
    GET. It reads like a GET, being read-only and costing no game time, but an action's
    arguments are structured, so they travel as a body and the call is a POST.
    """

    def test_a_query_is_a_post_that_unwraps_the_result(self) -> None:
        import inspect

        from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway

        source = inspect.getsource(NttdGateway.query)
        assert "client.post(" in source, "a GET here answers 405"
        assert 'params={"action": action}' in source, "the action is a query parameter"
        assert 'json=params or {}' in source, "its arguments are the body"
        assert '.get("result")' in source, "the payload arrives wrapped"

    def test_the_full_state_is_a_get(self) -> None:
        import inspect

        from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway

        assert "client.get(" in inspect.getsource(NttdGateway.observe)

    def test_a_step_reads_its_own_results(self) -> None:
        import inspect

        from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway

        assert '"action_results"' in inspect.getsource(NttdGateway.act)

    def test_credentials_come_from_sly_data_and_are_required(self) -> None:
        """They address the company, and neuro-san keeps sly_data out of the chat stream."""
        import pytest as _pytest

        from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway

        gateway = NttdGateway({"session_id": "20260815-171604ist-peppy-finch", "token": "pt_x"})
        assert "20260815-171604ist-peppy-finch" in gateway._root
        assert gateway._headers["Authorization"] == "Bearer pt_x"

        with _pytest.raises(ValueError):
            NttdGateway({})


class TestToolResolution:
    """A coded tool has to load both ways, because neuro-san can be told to restrict how.

    With AGENT_TOOL_PATH_ONLY unset, a HOCON's `class = "read_position.ReadPosition"`
    resolves as a fully-qualified import from anywhere on PYTHONPATH: an agent network file
    could name any importable class in the environment and it would be loaded. Setting it
    restricts resolution to AGENT_TOOL_PATH, which is what these networks want, since they
    only ever reference their own tools.

    Under that restriction the tools are loaded as flat siblings rather than as part of this
    repository's package, so the import of the shared gateway has to work either way.
    """

    TOOLS = (
        "nttd_gateway", "read_position", "rank_sites",
        "buy_and_dispatch", "let_time_pass", "verify_reachable",
    )

    def test_each_tool_loads_as_a_flat_sibling(self) -> None:
        import importlib.util
        import sys

        pytest.importorskip("neuro_san")
        directory = _ROOT / "agents" / "neuro_san" / "coded_tools"
        sys.path.insert(0, str(directory))
        try:
            for name in self.TOOLS:
                spec = importlib.util.spec_from_file_location(name, directory / f"{name}.py")
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
        finally:
            sys.path.remove(str(directory))
            for name in self.TOOLS:
                sys.modules.pop(name, None)

    def test_each_tool_also_loads_as_part_of_this_package(self) -> None:
        import importlib

        pytest.importorskip("neuro_san")
        for name in self.TOOLS:
            importlib.import_module(f"agents.neuro_san.coded_tools.{name}")

    def test_the_restriction_is_on_by_default_for_anyone_copying_the_env(self) -> None:
        assert "AGENT_TOOL_PATH_ONLY=true" in (_ROOT / ".env.example").read_text()


class TestConcurrentSteps:
    """A session takes one step at a time, and neuro-san calls tools concurrently.

    The gate refuses a second step with 409 while one is in flight, because two overlapping
    steps would advance the world twice for one decision. A turn that buys three aircraft,
    or a purchase overlapping a stretch of time passing, hits that. Measured live: six
    concurrent steps, no failures, and the day advanced by exactly six.
    """

    def test_steps_queue_behind_one_shared_lock(self) -> None:
        import inspect

        from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway

        source = inspect.getsource(NttdGateway.act)
        assert "self._step_lock()" in source, "steps have to queue"

        lock = inspect.getsource(NttdGateway._step_lock)
        assert 'self._sly["step_lock"]' in lock, "shared through sly_data, not per instance"

    def test_a_step_in_flight_is_waited_out_rather_than_lost(self) -> None:
        """The lock covers one event loop; this covers the rest."""
        import inspect

        from agents.neuro_san.coded_tools import nttd_gateway

        source = inspect.getsource(nttd_gateway.NttdGateway.act)
        assert "STEP_RETRIES" in source
        assert '"flight" not in reply.text.lower()' in source, "only retry the in-flight 409"

    def test_two_gateways_on_one_session_share_the_lock(self) -> None:
        from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway

        sly = {"session_id": "20260815-183253ist-royal-coral", "token": "pt_x"}
        first, second = NttdGateway(sly), NttdGateway(sly)
        assert first._step_lock() is second._step_lock()

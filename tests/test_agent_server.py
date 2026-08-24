"""Finding a neuro-san server, or starting one.

runex used to refuse when nothing answered on the agent port: "No neuro-san server at
localhost:8080, start it in another terminal". True, and a poor answer from a launcher whose
whole job is to spare a contestant that terminal.
"""

from __future__ import annotations

import socket
from contextlib import closing
from pathlib import Path

import pytest

from runex import agent_server, networks
from runex.cli import _as_port, _split_address


def _a_free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("localhost", 0))
        return int(sock.getsockname()[1])


# --- a busy port is two situations, not one ------------------------------------------------


def test_a_port_holding_something_that_is_not_neuro_san_is_not_mistaken_for_one() -> None:
    """The distinction the whole design turns on.

    A neuro-san server already there should be USED; anything else there means ours needs
    somewhere else to go. Moving to the next free port without asking first gets the first
    case exactly backwards: it starts a second server, loads every network twice, and leaves
    the contestant watching NSFlow on whichever one they happened to open.
    """
    with closing(socket.socket()) as listener:
        listener.bind(("localhost", 0))
        listener.listen(1)
        port = int(listener.getsockname()[1])

        assert agent_server.port_is_taken("localhost", port) is True
        assert agent_server.looks_like_neuro_san("localhost", port) is None


def test_an_empty_port_is_neither_taken_nor_a_server() -> None:
    port = _a_free_port()
    assert agent_server.port_is_taken("localhost", port) is False
    assert agent_server.looks_like_neuro_san("localhost", port) is None


def test_the_next_free_port_skips_what_is_listening() -> None:
    with closing(socket.socket()) as listener:
        listener.bind(("localhost", 0))
        listener.listen(1)
        port = int(listener.getsockname()[1])
        assert agent_server.next_free_port("localhost", port) != port


def test_the_search_gives_up_rather_than_scanning_forever() -> None:
    """If twenty consecutive ports are taken, a twenty-first is not the answer needed."""
    assert agent_server.next_free_port("localhost", 70_000, limit=2) in (70_000, 70_001, None)


# --- only a server we started is ever stopped ----------------------------------------------


def test_a_server_we_only_found_is_never_stopped() -> None:
    """A contestant running `ns run` in another terminal, watching it, is not something a
    launcher may kill on its way out."""
    found = agent_server.AgentServer("localhost", 8088)
    assert found.ours is False
    found.stop()  # must be a no-op rather than an error


def test_a_server_we_started_is_ours_to_stop() -> None:
    class _Dead:
        def poll(self) -> int | None:
            return 0

    started = agent_server.AgentServer("localhost", 8088, _Dead())
    assert started.ours is True
    started.stop()  # already exited: still a no-op rather than an error


def test_stopping_escalates_when_it_will_not_close() -> None:
    """A run that has already written its result must not be held up by a slow server."""
    import signal  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    class _Stubborn:
        pid = 999_999

        def __init__(self) -> None:
            self.signals: list[int] = []

        def poll(self) -> int | None:
            return None

        def send_signal(self, sig: int) -> None:
            self.signals.append(sig)

        def wait(self, timeout: float | None = None) -> int:
            if signal.SIGKILL not in self.signals:
                raise subprocess.TimeoutExpired("ns", timeout or 0)
            return 0

    process = _Stubborn()
    agent_server.AgentServer("localhost", 1, process).stop()
    assert process.signals == [signal.SIGTERM, signal.SIGKILL], (
        "it must ask politely before insisting"
    )


# --- what a contestant may type -------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("localhost:8088", ("localhost", 8088)),
        ("otherbox:9100", ("otherbox", 9100)),
        ("9000", ("localhost", 9000)),
        ("otherbox", ("otherbox", 8088)),
        ("", ("localhost", 8088)),
        ("  localhost:9001  ", ("localhost", 9001)),
    ],
)
def test_an_address_is_read_however_it_was_typed(typed: str, expected: tuple) -> None:
    assert _split_address(typed, "localhost", 8088) == expected


def test_a_port_that_is_not_a_port_falls_back_rather_than_crashing() -> None:
    assert _as_port("abc", 8088) == 8088
    assert _as_port("0", 8088) == 8088
    assert _as_port("70000", 8088) == 8088
    assert _as_port("9000", 8088) == 9000


# --- the default -------------------------------------------------------------------------


def test_the_default_port_is_not_the_most_contended_one_on_the_machine() -> None:
    """8088 rather than neuro-san-studio's own 8080. The first thing this has to do is tell
    "a neuro-san server is here" from "something else is", and 8080 is where everything is."""
    assert agent_server.DEFAULT_PORT == 8088


def test_a_started_server_is_told_which_port_to_use() -> None:
    """`ns run` reads the port from .env otherwise, which would ignore the choice made here."""
    import inspect  # noqa: PLC0415

    source = inspect.getsource(agent_server.start)
    assert "--server-http-port" in source
    assert "NEURO_SAN_SERVER_HTTP_PORT" in source, "the env has to agree with the flag"
    assert "--server-only" in source, "NSFlow would bind a second port to find free"


def test_the_networks_come_from_the_server_that_was_found_or_started() -> None:
    """Not from the manifest on disk: that is what the server was told to load."""
    assert networks.fetch is agent_server.networks.fetch


# --- which approaches need a server ---------------------------------------------------------


def test_needing_a_server_is_a_property_of_the_approach() -> None:
    """Not a name checked in the flow.

    The launcher used to ask `if chosen.key == "neuro-san"`. Only neuro-san needs a server
    today, but a langgraph or RL entry may need one of its own, and a string comparison in
    the middle of the flow is the wrong place to find that out.
    """
    from runex.kinds import KINDS, by_key  # noqa: PLC0415

    assert by_key("neuro-san").agent_server is True
    assert by_key("scripted").agent_server is False, "a scripted policy runs in process"
    assert [k.key for k in KINDS if k.agent_server] == ["neuro-san"]


def test_the_flow_asks_the_kind_rather_than_its_name() -> None:
    """Otherwise a new kind needing a server would work everywhere except where it matters."""
    import inspect  # noqa: PLC0415

    from runex import cli  # noqa: PLC0415

    flow = inspect.getsource(cli.main)
    assert "chosen.agent_server" in flow
    assert 'key == "neuro-san"' not in flow


def test_choosing_a_network_does_not_lose_the_flag() -> None:
    """The kind is rebuilt once a network is picked, and a dropped flag would skip cleanup."""
    from runex.kinds import by_key  # noqa: PLC0415

    original = by_key("neuro-san")
    remade = type(original)(
        key=original.key, title="neuro-san / ns_air_agent", blurb=original.blurb,
        module=original.module, requires=original.requires,
        install_hint=original.install_hint, agent_server=original.agent_server,
        extra_args=("--network", "ns_air_agent"),
    )
    assert remade.agent_server is True, "a dropped flag would skip the shutdown"


# --- where the server gets started from ------------------------------------------------------


def test_the_project_root_is_found_from_any_subdirectory() -> None:
    """Not simply the working directory.

    A shell with `autocd` set turns a bare `runex` into a cd into the `runex/` package
    directory, because the command is not on PATH unless the venv is active. From there the
    working directory is one level below the checkout, the relative paths in .env resolve
    against the wrong place, and the server comes up serving nothing.

    Walking up costs nothing and fixes the general case too.
    """
    root = agent_server.project_root(Path.cwd())
    assert agent_server.looks_like_a_project(root)

    for inside in ("runex", "examples", "agents"):
        found = agent_server.project_root(root / inside)
        assert found == root, f"started from {inside}/ it looked in {found}"


def test_a_directory_that_is_not_a_checkout_is_recognised(tmp_path: Path) -> None:
    """So the launcher can say so rather than starting a server that serves nothing."""
    assert agent_server.looks_like_a_project(tmp_path) is False

    marker = tmp_path / agent_server._PROJECT_MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text("{}")
    assert agent_server.looks_like_a_project(tmp_path) is True
    assert agent_server.project_root(tmp_path) == tmp_path


def test_the_server_is_started_in_the_root_rather_than_the_working_directory() -> None:
    """The relative paths in .env are the server's configuration, so this is load-bearing."""
    import inspect  # noqa: PLC0415

    source = inspect.getsource(agent_server.start)
    assert "cwd=str(project_root())" in source


def test_stopping_signals_the_whole_process_group() -> None:
    """`ns run` is a launcher: it spawns the server as a child.

    Measured: terminating the parent alone left the child running and still holding the
    port, so the next run found a server it had not started, could not stop, and would not
    have stopped anyway. The group is signalled as one, which needs `start` to have put it
    in a session of its own.
    """
    import inspect  # noqa: PLC0415

    assert "start_new_session=True" in inspect.getsource(agent_server.start)
    assert "killpg" in inspect.getsource(agent_server.AgentServer._signal_group)


def test_a_group_that_has_already_gone_is_not_an_error() -> None:
    """A server that exited on its own must not make the shutdown path raise."""
    class _Gone:
        pid = 999_999

        def poll(self) -> int | None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def send_signal(self, sig: int) -> None:
            raise ProcessLookupError

    agent_server.AgentServer("localhost", 1, _Gone()).stop()


def test_the_server_log_is_appended_rather_than_wiped(tmp_path: Path) -> None:
    """It is read when a server has died and someone wants to know why.

    Measured: opening it with "wb" meant a later run destroyed the log of the run being
    diagnosed, which is the one moment the file exists for.
    """
    import inspect  # noqa: PLC0415

    source = inspect.getsource(agent_server.start)
    assert 'log.open("ab")' in source
    assert 'log.open("wb")' not in source

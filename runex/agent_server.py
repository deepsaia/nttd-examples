"""Finding an agent server, or starting one.

**Today that means neuro-san, and only neuro-san.** `start` runs `ns run` and nothing else,
and the ports and defaults below are neuro-san's. The rest of this module, probing a port,
telling a server from whatever else is on it, arbitrating a free one, and shutting down only
what it started, is about servers in general. When a langgraph or RL entry needs a server of
its own, what it takes to start one belongs on its `ExperimentKind`, which already carries
the flag that decides whether any of this runs at all.


runex used to refuse when nothing answered on the agent port: "No neuro-san server at
localhost:8080, start it in another terminal". That is a true statement and a poor tool. The
launcher exists to spare a contestant carrying things between terminals, and it was sending
them to one.

**A busy port is not one situation but two**, and they want opposite answers:

- a neuro-san server is already there, in which case use it. Starting a second one loads
  every network twice and leaves the contestant watching NSFlow on whichever one they
  happened to open.
- something else is there, in which case ours has to go somewhere else.

They are told apart by asking. A neuro-san server answers `GET /api/v1/list` with its
networks; anything else does not. So the port is probed before any decision, and only a port
holding something that is NOT a neuro-san server sends us looking for another one. Moving to
the next free port without probing would get the first case exactly backwards.

**Only a server this module started is ever stopped.** A contestant running `ns run` in
another terminal, watching it, is not something a launcher may kill on its way out.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

from runex import networks

logger = logging.getLogger(__name__)

# Where a neuro-san server is expected. 8088 rather than neuro-san-studio's own 8080 default,
# because 8080 is the most contended port on a developer's machine and the first thing this
# module has to do is tell "a neuro-san server is here" apart from "something else is".
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8088

# How far to look for a free port before giving up. Small on purpose: if twenty consecutive
# ports are taken, the answer a contestant needs is not a twenty-first port.
PORT_SEARCH_LIMIT = 20

# How long to wait for a started server to serve its networks. It loads every registry and
# builds each agent, so it is seconds rather than instant.
START_TIMEOUT_SECONDS = 90.0
POLL_SECONDS = 1.0


class AgentServer:
    """A neuro-san server: one we found, or one we started and will stop."""

    def __init__(self, host: str, port: int, process: subprocess.Popen | None = None) -> None:
        self.host = host
        self.port = port
        self._process = process

    @property
    def ours(self) -> bool:
        """Whether this module started it, and may therefore stop it."""
        return self._process is not None

    def stop(self) -> None:
        """Shut down a server we started. A server we merely found is left alone.

        The whole PROCESS GROUP, not the process. `ns run` is a launcher: it spawns the
        server as a child and exits nothing when signalled alone. Measured: terminating the
        parent left the child running, still holding the port, so the next run found a
        server it had not started, could not stop, and would not have stopped anyway.
        `start` puts them in their own session so the group can be signalled as one.
        """
        if self._process is None or self._process.poll() is not None:
            return

        self._signal_group(signal.SIGTERM)
        try:
            self._process.wait(timeout=15)
            return
        except subprocess.TimeoutExpired:
            # It loads a lot and can be slow to unwind. A run that has already written its
            # result should not be held up by a server that will not close.
            pass

        self._signal_group(signal.SIGKILL)
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("The agent server did not exit after SIGKILL")

    def _signal_group(self, sig: int) -> None:
        """Signal the server and everything it spawned, or just it if that is not possible."""
        if self._process is None:
            return
        try:
            os.killpg(os.getpgid(self._process.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            # Already gone, or a platform without process groups. Falling back to the one
            # process is still better than nothing.
            try:
                self._process.send_signal(sig)
            except (ProcessLookupError, ValueError):
                pass


def looks_like_neuro_san(host: str, port: int) -> list[networks.Network] | None:
    """The networks a neuro-san server is serving there, or None if that is not one.

    None covers both "nothing is listening" and "something is, and it is not neuro-san",
    because the caller does the same thing about each: find somewhere else to put ours.
    """
    if not port_is_taken(host, port):
        return None
    try:
        return networks.fetch(host, port, timeout=5.0)
    except Exception:  # noqa: BLE001 - anything that is not an answer means "not one of ours"
        return None


def port_is_taken(host: str, port: int) -> bool:
    """Whether anything at all accepts a connection there."""
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except OSError:
        return False


def next_free_port(host: str, port: int, limit: int = PORT_SEARCH_LIMIT) -> int | None:
    """The first port at or above this one that nothing is listening on."""
    for candidate in range(port, port + limit):
        if not port_is_taken(host, candidate):
            return candidate
    return None


# What marks the top of a checkout. The server reads AGENT_MANIFEST_FILE, AGENT_TOOL_PATH and
# the API key from a project-root .env, all of them RELATIVE paths, so the working directory
# it is started in is effectively its configuration.
_PROJECT_MARKER = Path("registries") / "manifest.hocon"


def looks_like_a_project(root: Path) -> bool:
    return (root / _PROJECT_MARKER).is_file()


def project_root(start: Path | None = None) -> Path:
    """The checkout to start the server in, found by walking up rather than assuming.

    Not simply the working directory. A shell with `autocd` set turns a bare `runex` into a
    cd into the `runex/` package directory, because the command is not on PATH unless the
    venv is active. From there the working directory is one level below the checkout, the
    relative paths in .env resolve against the wrong place, and the server comes up serving
    nothing.

    Walking up costs nothing and fixes the general case as well: `runex` started from
    `examples/`, or from any other subdirectory, finds the same root. Falls back to the
    directory this package sits in, which is the checkout when runex is run from source.
    """
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if looks_like_a_project(candidate):
            return candidate

    beside = Path(__file__).resolve().parents[1]
    if looks_like_a_project(beside):
        return beside
    return here


def start(host: str, port: int, log_path: Path | None = None) -> subprocess.Popen:
    """Start a neuro-san server on this port and return the process.

    `--server-only`: the launcher needs the agent server, and NSFlow binds a second port that
    would be one more thing to find free. A contestant who wants the UI runs `ns run` itself,
    which this module will then find and use rather than duplicate.

    Output goes to a file rather than a pipe. Nothing reads it until something has gone
    wrong, and a pipe nobody drains fills its buffer and blocks the process being watched.
    """
    log = (log_path or (project_root() / "logs" / "runex-neuro-san.log"))
    log.parent.mkdir(parents=True, exist_ok=True)
    # APPENDED, not truncated. This file is read when a server has died and someone wants to
    # know why, and a second runex opening it with "wb" destroys exactly that: measured, a
    # later run wiped the log of the run being diagnosed. Each start writes a banner so the
    # runs stay legible.
    handle = log.open("ab")
    handle.write(f"\n===== ns run --server-only on {host}:{port} =====\n".encode())
    handle.flush()

    env = dict(os.environ)
    env["NEURO_SAN_SERVER_HTTP_PORT"] = str(port)
    env["NEURO_SAN_SERVER_HOST"] = host

    return subprocess.Popen(
        [
            "ns", "run", "--server-only",
            "--server-host", host,
            "--server-http-port", str(port),
        ],
        cwd=str(project_root()),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        # Its own process group, so `stop` can signal the launcher AND the server it spawns.
        # Without this, terminating `ns run` leaves the server running and holding the port.
        start_new_session=True,
    )


def wait_until_serving(
    host: str, port: int, process: subprocess.Popen,
    timeout: float = START_TIMEOUT_SECONDS,
) -> list[networks.Network] | None:
    """Poll until it serves its networks, or it dies, or the wait runs out.

    Death is checked every poll rather than only at the end: a server that exits on a bad
    API key does so in a second, and waiting ninety of them to say so is ninety seconds of
    a contestant watching nothing.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return None
        served = looks_like_neuro_san(host, port)
        if served:
            return served
        time.sleep(POLL_SECONDS)
    return None

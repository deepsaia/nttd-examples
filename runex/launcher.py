"""Starting the chosen runner against the chosen session.

The token goes through the environment and never onto the command line. An argv is readable by
any process on the machine, and a participant token is the credential that says which company
is acting: printing one into `ps` output would be a poor thing for the reference tooling to
teach by example.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys

from runex.kinds import ExperimentKind


class Launcher:
    """Runs one experiment in a child process and waits for it."""

    def __init__(self, api_url: str) -> None:
        self._api_url = api_url

    def command(self, kind: ExperimentKind, session_id: str) -> list[str]:
        """The command that will be run, so it can be shown before it is."""
        return [
            sys.executable, "-m", kind.module,
            "--session", session_id,
            *kind.extra_args,
        ]

    def run(self, kind: ExperimentKind, session_id: str, token: str) -> int:
        """Start it, stream its output, and return its exit code."""
        env = dict(os.environ)
        env["NTTD_TOKEN"] = token
        env["NTTD_API_URL"] = self._api_url
        return subprocess.call(self.command(kind, session_id), env=env)


def reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """Whether something is listening there.

    A plain TCP connect rather than a health endpoint, because the endpoint differs between the
    servers this has to check and the question is the same for both: is anything there. A
    neuro-san run that starts against a dead server fails one turn later with a connection
    error, which reads as an agent fault and is not one.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

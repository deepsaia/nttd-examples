"""Starting the chosen runner against the chosen session.

The token goes through the environment and never onto the command line. An argv is readable by
any process on the machine, and a participant token is the credential that says which company
is acting: printing one into `ps` output would be a poor thing for the reference tooling to
teach by example.
"""

from __future__ import annotations

import os
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

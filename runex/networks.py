"""The agent networks a neuro-san server is actually serving.

Asked of the server rather than read from registries/manifest.hocon on disk. The manifest is
what the server was TOLD to load; this is what it did load. They differ whenever the manifest
has been edited since `ns run` started, which during development is most of the time.

There is deliberately no default here. An earlier version fell back to "ns_air_agent" when the
list call failed, which produced a line reading "Network: ns_air_agent" that was
indistinguishable from having genuinely found one network. On a server serving water and road
that would have handed the contestant air, and the mistake would only have surfaced turns later
inside the run.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Network:
    """One network, as the concierge describes it."""

    name: str
    description: str
    tags: tuple[str, ...]

    @property
    def summary(self) -> str:
        """The description, which is what tells a reader which network to pick.

        Networks name the mode they play and little else, so without this a menu of four is
        four near-identical strings.
        """
        return self.description or "no description"


def fetch(host: str, port: int, timeout: float = 10.0) -> list[Network]:
    """Every network the server serves, by name.

    Raises rather than returning a guess. The caller can report why it could not ask, which is
    a better thing to show than a network the server may not have.
    """
    reply = httpx.get(f"http://{host}:{port}/api/v1/list", timeout=timeout)
    reply.raise_for_status()
    agents = reply.json().get("agents") or []
    found = [_read(entry) for entry in agents]
    return sorted([network for network in found if network.name], key=lambda n: n.name)


def _read(entry: dict) -> Network:
    return Network(
        name=str(entry.get("agent_name") or ""),
        description=str(entry.get("description") or ""),
        tags=tuple(str(tag) for tag in entry.get("tags") or ()),
    )

"""The kinds of experiment this repository can run, and how each one is launched.

nttd is agent-agnostic, which is a claim that has to be visible somewhere. Here is where: every
kind below attaches to the same session through the same HTTP surface, differing only in what
decides. A menu that lists them side by side is the shortest honest statement of that.

Kinds that are not written yet are listed rather than hidden. A menu showing only what exists
reads as though nothing else were intended, and this one is meant to grow.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExperimentKind:
    """One way of playing a session, and what it takes to run it."""

    key: str
    title: str
    blurb: str
    # The module a run is launched as, with --session and --token. Empty means unwritten.
    module: str = ""
    # An import that must resolve before this can run, so a missing extra is reported at the
    # menu rather than as a traceback thirty seconds in.
    requires: str = ""
    # How to get that import, quoted back to the contestant verbatim.
    install_hint: str = ""
    extra_args: tuple[str, ...] = field(default_factory=tuple)

    @property
    def written(self) -> bool:
        return bool(self.module)

    @property
    def ready(self) -> bool:
        """Written, and its dependency actually importable in this environment."""
        if not self.written:
            return False
        if not self.requires:
            return True
        return importlib.util.find_spec(self.requires) is not None

    @property
    def unavailable_because(self) -> str:
        if not self.written:
            return "not written yet"
        if not self.ready:
            return f"needs {self.requires}: {self.install_hint}"
        return ""


KINDS: tuple[ExperimentKind, ...] = (
    ExperimentKind(
        key="neuro-san",
        title="neuro-san",
        blurb="A multi-agent network: a strategist that reads the position and calls workers "
              "which survey, build, buy and repair. Needs a neuro-san server running.",
        module="examples.neuro_san_play",
        requires="neuro_san",
        install_hint="uv sync --extra neuro-san",
    ),
    ExperimentKind(
        key="scripted",
        title="scripted",
        blurb="No model and no framework. A fixed policy playing the same stepped loop, which "
              "is the shortest thing that proves a session works end to end.",
        module="examples.minimal_runner",
    ),
    ExperimentKind(
        key="es",
        title="evolution strategies",
        blurb="A population searching over policy parameters. Stepped play exists for this: "
              "the world is paused between steps, so a slow evaluation costs no game days.",
    ),
    ExperimentKind(
        key="rl",
        title="reinforcement learning",
        blurb="A learned policy over the same stepped loop, one step of the world per step of "
              "the agent.",
    ),
)


def by_key(key: str) -> ExperimentKind | None:
    for kind in KINDS:
        if kind.key == key:
            return kind
    return None

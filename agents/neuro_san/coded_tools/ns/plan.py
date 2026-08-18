"""Actions staged but not yet submitted.

**Planning is free; committing costs a game day.** A step advances the world by one day and a
batch has no ceiling: nttd says so itself, "a policy that wants to lay a whole route in one step
may". So a turn that builds two airports and buys four aircraft with their orders should spend
one or two days, not ten. Submitting one action per step is how a 366 day budget gets eaten by
paperwork.

The accumulator is what lets several agents contribute to one step. Scout proposes nothing,
Builder adds the airports, FleetGrowth adds the purchases and the orders, and the strategist
commits once. Without it each agent would have to submit its own step and the turn would cost a
day per contributor.

Held in `sly_data` so it survives between turns: a plan staged and not committed is still a
plan. That only works because the registry declares an explicit allow-list; with neuro-san's
security-by-default nothing crosses the turn boundary at all.
"""

from __future__ import annotations

from typing import Any

try:
    # As part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns import constants as key
except ImportError:
    # As flat siblings, which is how neuro-san loads coded tools when
    # AGENT_TOOL_PATH_ONLY is true. Both spellings are needed and the foundation was
    # the one place that had only one.
    import constants as key

# Actions that must be alone in a step. connect_road and connect_rail lay a whole corridor and
# can partially fail on a single tile, and the refusal names that tile; batching them with
# anything else makes the report ambiguous about which action the coordinate belongs to.
ALONE_IN_A_STEP = ("connect_road", "connect_rail")


class Plan:
    """The batch being assembled for the next commit."""

    def __init__(self, sly_data: dict[str, Any]) -> None:
        self._sly = sly_data

    @property
    def actions(self) -> list[dict[str, Any]]:
        return self._sly.setdefault(key.PLAN, [])

    def add(self, *actions: dict[str, Any]) -> int:
        """Stage actions. Returns how many are now waiting."""
        self.actions.extend(actions)
        return len(self.actions)

    def clear(self) -> None:
        self._sly[key.PLAN] = []

    def describe(self) -> list[str]:
        """The plan as a reader would say it, for a tool to hand back to the strategist."""
        return [
            f"{index}. {entry.get('action')} {_readable(entry.get('params') or {})}"
            for index, entry in enumerate(self.actions, 1)
        ]

    def split(self) -> list[list[dict[str, Any]]]:
        """The plan as the steps it has to become.

        Usually one step. An action that must be alone gets its own, and anything after it
        follows in the next, which keeps a corridor's partial-failure report unambiguous.
        """
        steps: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for entry in self.actions:
            if entry.get("action") in ALONE_IN_A_STEP:
                if current:
                    steps.append(current)
                    current = []
                steps.append([entry])
                continue
            current.append(entry)
        if current:
            steps.append(current)
        return steps

    def already_refused(self) -> list[str]:
        """Anything staged that the game has already refused, in the same form.

        The check that stops a run repeating itself. Measured: 35 identical purchases, every one
        refused the same way, because nothing compared what was about to be sent against what
        had already failed.
        """
        refused = self._sly.get(key.REFUSALS) or []
        seen = {(r.get("action"), _fingerprint(r.get("params") or {})): r for r in refused}
        repeats: list[str] = []
        for entry in self.actions:
            match = seen.get((entry.get("action"), _fingerprint(entry.get("params") or {})))
            if match:
                repeats.append(
                    f"{entry.get('action')} {_readable(entry.get('params') or {})} was already "
                    f"refused: {match.get('error')}"
                )
        return repeats


def _fingerprint(params: dict[str, Any]) -> tuple:
    """What makes two attempts the same attempt, ignoring bookkeeping."""
    return tuple(sorted(
        (name, value) for name, value in params.items() if name != "company_id"
    ))


def _readable(params: dict[str, Any]) -> str:
    return ", ".join(f"{name}={value}" for name, value in sorted(params.items())
                     if name != "company_id")

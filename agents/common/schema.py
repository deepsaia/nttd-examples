"""What flows between the agents in a mode's multi-agent system.

Shared by all four modes and by the combined system, so the boundary is declared once.
Getting this wrong is the usual way a hierarchical LangGraph system becomes
unmaintainable: every level needs to agree on the observation and the memory while each
specialist keeps its own working notes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Action(BaseModel):
    """One action, in the shape nttd's step call takes."""

    action: str = Field(
        description="An action name from the reference you were given, e.g. set_loan",
    )
    params: dict[str, Any] = Field(
        default_factory=dict, description="That action's parameters",
    )


class ActionBatch(BaseModel):
    """What a specialist proposes for this step.

    Returned as structured output against a schema rather than parsed out of prose. The
    runner this replaced scraped JSON from markdown fences, and every model quirk became
    a new edge case in that parser.
    """

    reasoning: str = Field(
        description="One or two sentences on why these actions, for the run log",
    )
    actions: list[Action] = Field(
        default_factory=list,
        description="Empty is a real answer: waiting is a move",
    )
    route_note: str = Field(
        default="",
        description=(
            "What this step did towards the route being built, if anything. Carried "
            "into the next step so the run does not forget a half-built route."
        ),
    )


class Refusal(BaseModel):
    """One action nttd would not perform, and why.

    Kept as a type rather than a loose dict because it crosses three boundaries: the
    step result, the negative cache, and the next prompt.
    """

    action: str
    error: str
    error_name: str = ""

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> Refusal:
        return cls(
            action=result.get("action_type") or "action",
            error=result.get("error") or "no reason given",
            error_name=result.get("error_name") or "",
        )

    def key(self) -> str:
        """What makes two refusals the same for the negative cache.

        The action and the reason, not the parameters: a policy that keeps trying
        build_dock on different unbuildable tiles is making the same mistake, and
        keying on parameters would let it repeat that forever.
        """
        return f"{self.action}:{self.error_name or self.error[:60]}"

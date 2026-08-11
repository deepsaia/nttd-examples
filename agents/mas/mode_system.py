"""One transport mode's multi-agent system.

Not one agent. A small team with a coded orchestrator choosing which of them speaks this
step, over a shared memory. The same class builds all four modes, because the difference
between rail and water is the strategy file, the action categories and the specialist's
brief, not the machinery.

## The team

Three specialists, and every mode gets all three:

**surveyor** reads the world with tools and writes a short brief. It may spend tool calls
freely, because in stepped mode the world is paused while it thinks.

**consultant** decides what the company should be doing: which corridor is worth serving,
whether to expand or consolidate, whether to borrow. This is the commercial judgement
that is the same whether cargo moves by rail or by ship, which is why it is common rather
than per mode.

**builder** turns that into a batch of actions, and is the only one that differs by mode.

## Why an orchestrator rather than a fan-out

Every level resolves to one batch submitted once per step, because a session holds one
contestant company. So the orchestrator arbitrates; it does not gather. And it is coded
rather than a model, because advancing the world already costs about two seconds a
game-day and a model call to decide who should make a model call is a poor trade.

The rule it applies is the one that decides most runs: **finish the route you started**.
A half-built route earns nothing while having cost what it already cost, so an unfinished
route outranks a new opportunity every time.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.common.middleware import RefusalCache, observation_note, remembering
from agents.common.route_ledger import RouteLedger
from agents.common.schema import ActionBatch, Refusal
from agents.tools import NttdTools

logger = logging.getLogger(__name__)

# How many super-steps a specialist may take before the graph stops it. Each super-step
# is one model call plus whatever tools it asked for, so this is the real bound on both
# cost and wall clock.
#
# It replaces ToolCallLimitMiddleware, which was the wrong instrument and expensively so.
# Its `continue` behaviour blocks the tool that exceeded the cap and lets execution
# carry on, so the agent simply asked again, was blocked again, and looped: a measured
# run made 901 model calls across THREE steps, against a design intending three per
# step, and averaged 20 minutes a step. `end` would stop it but raises
# NotImplementedError when several tool calls are in flight, which Claude does routinely.
#
# A recursion limit is the framework's own bound, cannot be argued with by the model,
# and needs no middleware.
_SURVEY_TURNS = 8
_BUILD_TURNS = 10
# The consultant has no tools, so one turn to think and one to answer is enough.
_CONSULT_TURNS = 4

SURVEYOR_BRIEF = """You survey an OpenTTD transport company for the rest of the team.

Use your tools to establish what matters now: what the company owns, what it earns, which
towns and industries are unserved, and what it can afford. Write at most eight lines of
findings. No recommendations: the consultant decides.

{observation}
{ledger}"""

CONSULTANT_BRIEF = """You decide what this transport company should do next.

You do not choose tiles or name actions. You choose the OBJECTIVE for this step, in two
or three sentences: which corridor to serve, whether to finish what is started, whether
to buy vehicles for an existing route, or whether to wait.

The single most important rule: an unfinished route earns nothing while having already
cost money. Finish it before starting another.

The surveyor reports:
{brief}

{ledger}
{mistakes}"""

BUILDER_BRIEF = """You carry out one objective for a transport company, as actions.

The objective:
{objective}

You have TOOLS and you have ACTIONS, and they are different things.

TOOLS answer questions and cost nothing: the finders tell you where something will
actually fit, and they run a real dry run inside the game, so a tile one returns is a
tile the game has already agreed to. Use them before every build. Never guess a tile.

ACTIONS change the world and are what you return. They are listed at the end.

{strategy}

{mistakes}
{actions}"""


class ModeSystem:
    """The multi-agent system for one transport mode."""

    def __init__(
        self,
        mode: str,
        tools: NttdTools,
        ledger: RouteLedger,
        model: str,
        action_reference: str,
        strategy: str,
    ) -> None:
        self.mode = mode
        self._tools = tools
        self._ledger = ledger
        self._model = model
        self._reference = action_reference
        self._strategy = strategy
        self._cache = RefusalCache(ledger)
        self._surveyor: Any = None
        self._consultant: Any = None
        self._builder: Any = None

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------

    def build(self) -> ModeSystem:
        """Create the three specialists. Deferred so the class can be inspected and
        tested without a model or an API key."""
        from langchain.agents import create_agent
        from langchain.agents.structured_output import ToolStrategy
        from langchain_anthropic import ChatAnthropic

        def chat() -> Any:
            # No temperature: claude-sonnet-5 rejects it outright with "`temperature` is
            # deprecated for this model". The three specialists were differentiated by
            # it, and are now differentiated only by their brief, which is where the
            # real difference always was.
            #
            # The key is read from ANTHROPIC_API_KEY by the client itself and is never
            # handled here.
            return ChatAnthropic(model=self._model)

        self._surveyor = create_agent(
            model=chat(),
            tools=self._tools.as_langchain_tools(),
            system_prompt="You report what is, not what should be.",
        )
        self._consultant = create_agent(
            model=chat(),
            tools=[],
            system_prompt="You are a transport company strategist.",
        )
        self._builder = create_agent(
            model=chat(),
            # The builder gets the finders too, not just the surveyor. It is the one
            # choosing tiles, and a brief written in prose cannot carry a tile id
            # reliably. Without them a first shakedown run reasoned, correctly, that it
            # had no way to find a station spot, and then guessed one and was refused.
            tools=self._tools.as_langchain_tools(),
            system_prompt=f"You build {self.mode} infrastructure in OpenTTD.",
            middleware=[remembering(self._ledger)],
            # Structured output against a schema, with validation errors fed back so the
            # model corrects itself in the same turn rather than costing a step.
            response_format=ToolStrategy(ActionBatch, handle_errors=True),
        )
        return self

    # ------------------------------------------------------------------
    # One step
    # ------------------------------------------------------------------

    def decide(self, observation: dict[str, Any]) -> ActionBatch:
        """Survey, consult, then build. Returns the batch to submit."""
        note = observation_note(observation)
        mistakes = self._mistake_note()

        brief = self._say(self._surveyor, SURVEYOR_BRIEF.format(
            observation=note, ledger=self._ledger.summary(),
        ), _SURVEY_TURNS)
        objective = self._say(self._consultant, CONSULTANT_BRIEF.format(
            brief=brief, ledger=self._ledger.summary(), mistakes=mistakes,
        ), _CONSULT_TURNS)
        from langgraph.errors import GraphRecursionError

        try:
            result = self._builder.invoke(
                {"messages": [("user", BUILDER_BRIEF.format(
                    objective=objective, strategy=self._strategy,
                    mistakes=mistakes, actions=self._reference,
                ))]},
                config={"recursion_limit": _BUILD_TURNS},
            )
        except GraphRecursionError:
            # A builder that scouted until its budget ran out proposed nothing. Waiting
            # is a legitimate step, and it will see the same world again next time.
            logger.warning("%s builder hit its turn budget; waiting this step", self.mode)
            return ActionBatch(reasoning="turn budget exhausted while scouting", actions=[])

        batch = result.get("structured_response")
        if batch is None:
            # A model that returned no structured output has proposed nothing, which is
            # a legitimate step. Better than guessing at its prose.
            logger.warning("%s builder returned no batch; waiting this step", self.mode)
            return ActionBatch(reasoning="no batch produced", actions=[])
        return batch

    def learn(self, action_results: list[dict[str, Any]]) -> list[Refusal]:
        """Record what the last step's actions did. Returns the refusals."""
        refusals = [
            Refusal.from_result(r) for r in action_results
            if r.get("status") != "success"
        ]
        for refusal in refusals:
            self._ledger.remember_refusal(refusal)
        return refusals

    # ------------------------------------------------------------------

    def _say(self, agent: Any, prompt: str, turns: int) -> str:
        """Ask one specialist, bounded.

        Hitting the bound is a normal outcome, not a failure: a surveyor that used its
        whole budget looking around has still looked around. Raising would end the run
        over a specialist being thorough, so the exception is caught and the step goes
        on with whatever was learnt.
        """
        from langgraph.errors import GraphRecursionError

        try:
            result = agent.invoke(
                {"messages": [("user", prompt)]},
                config={"recursion_limit": turns},
            )
        except GraphRecursionError:
            logger.warning("%s specialist hit its %d turn budget", self.mode, turns)
            return "No findings: the turn budget ran out before an answer."
        return result["messages"][-1].content

    def _mistake_note(self) -> str:
        """Repeated refusals, in the words most likely to change behaviour.

        A count tells a model nothing. "build_dock has been refused three times for the
        same reason, stop trying it" tells it to do something else.
        """
        repeated = self._cache.repeated()
        if not repeated:
            return ""
        lines = "\n".join(
            f"- {m['action']} has been refused {m['count']} times: {m['error']}"
            for m in repeated
        )
        return (
            "You are repeating mistakes. Do NOT try these again; do something "
            f"different:\n{lines}\n"
        )

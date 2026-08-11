"""One transport mode's multi-agent system.

Four agents with one job each, over a base of Python tools that compute rather than
describe. The division is deliberate and it is the whole design:

**Python states the facts.** `situation` gives money, what is built, what earns, per-route
health and a list of problems. `route_candidates` gives which pairs are worth serving.
The finders give where something will actually fit, by dry-running the real build inside
the game. None of this needs a model, all of it is always right, and it costs nothing.

**Models exercise judgement.** Which problem matters most, whether to expand or
consolidate, which of three viable corridors to take, whether a batch makes sense given
the position. That is what a model is for and what the benchmark is measuring.

An earlier version had a surveyor agent whose job was to read numbers and summarise them.
That is the worst use of a model here: it cost a call per step, varied run to run, and
could get arithmetic wrong in ways nothing caught. Every figure it produced is now
computed.

## The agents

**observer** reads the computed report and says what matters now. It does no arithmetic.

**consultant** chooses the objective for this step. Commercial judgement, and identical
across all four modes, because deciding whether a corridor pays is the same problem
whether cargo moves by rail or by ship.

**builder** turns the objective into actions, confirming every site with a finder.

**validator** checks the batch against the position before it is submitted. Its job is
the semantic half only: nttd's own `/actions/interpret/validate` handles the mechanical
half for free, and the orchestrator calls that first.

## Why an orchestrator and not one big agent

Every level resolves to one batch submitted once per step, because a session holds one
contestant company. Mushing all four jobs into one prompt is the design this avoids: one
agent asked to observe, decide, build and check itself does all four poorly and gives no
way to tell which part failed.

The orchestrator is code, not a model. It sequences the four, runs the free mechanical
check, and decides whether to submit. A model call to decide who should make a model call
is a poor trade.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.common.middleware import remembering
from agents.common.route_ledger import RouteLedger
from agents.common.schema import ActionBatch, Refusal
from agents.tools import NttdTools

logger = logging.getLogger(__name__)

# Generous, and not budgets. Rationing turns was the wrong lever twice: a tool-call cap
# that let the loop continue produced 905 model calls across three steps, and tight
# recursion limits then made the builder spend every turn scouting and return nothing.
#
# What made a long loop expensive was the size of each call, not the number: the action
# reference and the strategy were re-sent every iteration. They are cached system prompts
# now, so an extra turn is cheap. These exist only so a stuck agent cannot spin forever.
_TURNS = 60

OBSERVER_SYSTEM = """You read a transport company's position and say what matters.

nttd has already computed the numbers: money, what is built, what earns, the health of
each route, and a list of problems. You do NOT recompute anything and you do not need to
check the arithmetic. Your job is judgement about significance.

Answer in at most six lines: what state the company is in, and which one or two things
most deserve attention this step. Say nothing about how to fix them; the strategist
decides that."""

OBSERVER_BRIEF = """The computed position:

{situation}

Routes worth serving that nobody serves yet:

{candidates}
"""

CONSULTANT_SYSTEM = """You are a transport company strategist.

You choose the OBJECTIVE for this step, in two or three sentences. You do not choose
tiles and you do not name actions.

The rule that decides most runs: an unfinished route earns nothing while having already
cost what it cost. Finish it before starting another. Cargo piling up at a station means
another vehicle on THAT route, not a new route.

{strategy}"""

CONSULTANT_BRIEF = """What the observer reports:

{observation}

{ledger}
{mistakes}"""

BUILDER_SYSTEM = """You build {mode} infrastructure in OpenTTD.

You have TOOLS and you have ACTIONS, and they are different.

TOOLS answer questions. The finders tell you where something will actually fit by
dry-running the real build inside the game, so a tile one returns is a tile the game has
already agreed to. Use them before building. Never guess a tile.

ACTIONS change the world and are what you return.

A step that returns no actions because you were still looking is a step wasted: you will
see the same world again with no more information. Look, then act.

{strategy}

{actions}"""

BUILDER_BRIEF = """Carry out this objective, as actions.

{objective}

{mistakes}"""

VALIDATOR_SYSTEM = """You check a proposed batch of actions against the company's position.

nttd has already checked the mechanical half: whether each action exists and takes the
parameters given. Do not repeat that. Your job is whether the batch makes SENSE:

- buying a vehicle for a route with no depot or no track
- borrowing when the balance already covers the spend
- building a second station in a town already served by this company
- starting a new route while an unfinished one is waiting
- spending more than the balance and loan headroom together

Answer with a short verdict. If the batch is sound, say so in one line. If not, say which
action is wrong and why, in one line each. You do not rewrite the batch."""

VALIDATOR_BRIEF = """The position:

{situation}

The proposed batch:

{batch}
"""


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
        self._agents: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------

    def build(self) -> ModeSystem:
        """Create the four agents. Deferred so the class can be inspected and tested
        without a model or an API key."""
        from langchain.agents import create_agent
        from langchain.agents.structured_output import ToolStrategy
        from langchain_anthropic import ChatAnthropic

        def cached(text: str) -> list[dict[str, Any]]:
            """A system prompt Anthropic can cache across calls.

            The action reference and the strategy are several thousand characters each
            and do not change for the life of a run. Carried in the user turn they were
            re-sent on every iteration of the agent loop, which is what made a long loop
            expensive rather than merely slow. Cached, they are charged once.
            """
            return [{
                "type": "text",
                "text": text,
                "cache_control": {"type": "ephemeral"},
            }]

        def chat() -> Any:
            # No temperature: claude-sonnet-5 rejects it. The agents are differentiated
            # by their brief, which is where the difference always was.
            #
            # The key is read from ANTHROPIC_API_KEY by the client and never handled here.
            return ChatAnthropic(model=self._model)

        # The observer and the validator are given no tools at all. Both are handed a
        # report that is already complete, and a tool would only tempt them to re-derive
        # what Python computed.
        self._agents["observer"] = create_agent(
            model=chat(), tools=[], system_prompt=cached(OBSERVER_SYSTEM),
        )
        self._agents["consultant"] = create_agent(
            model=chat(), tools=[],
            system_prompt=cached(CONSULTANT_SYSTEM.format(strategy=self._strategy)),
        )
        self._agents["builder"] = create_agent(
            model=chat(),
            tools=self._tools.for_building(),
            system_prompt=cached(BUILDER_SYSTEM.format(
                mode=self.mode, strategy=self._strategy, actions=self._reference,
            )),
            middleware=[remembering(self._ledger)],
            response_format=ToolStrategy(ActionBatch, handle_errors=True),
        )
        self._agents["validator"] = create_agent(
            model=chat(), tools=[], system_prompt=cached(VALIDATOR_SYSTEM),
        )
        return self

    # ------------------------------------------------------------------
    # One step
    # ------------------------------------------------------------------

    def decide(self) -> ActionBatch:
        """Observe, decide, build, check. Returns the batch to submit.

        The observation comes from nttd rather than from the snapshot passed to the
        runner: the computed report is the same facts, already correct, and asking for it
        costs one cheap HTTP call against a paused world.
        """
        situation = self._tools.situation()
        candidates = self._tools.route_candidates()
        mistakes = self._mistake_note()

        observation = self._say("observer", OBSERVER_BRIEF.format(
            situation=_brief(situation), candidates=_brief(candidates),
        ))
        objective = self._say("consultant", CONSULTANT_BRIEF.format(
            observation=observation, ledger=self._ledger.summary(), mistakes=mistakes,
        ))
        batch = self._build(objective, mistakes)
        if not batch.actions:
            return batch

        return self._checked(batch, situation)

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

    def _build(self, objective: str, mistakes: str) -> ActionBatch:
        from langgraph.errors import GraphRecursionError

        try:
            result = self._agents["builder"].invoke(
                {"messages": [("user", BUILDER_BRIEF.format(
                    objective=objective, mistakes=mistakes,
                ))]},
                config={"recursion_limit": _TURNS},
            )
        except GraphRecursionError:
            logger.warning("%s builder did not finish; waiting this step", self.mode)
            return ActionBatch(reasoning="builder did not settle on a batch", actions=[])

        batch = result.get("structured_response")
        if batch is None:
            logger.warning("%s builder returned no batch; waiting this step", self.mode)
            return ActionBatch(reasoning="no batch produced", actions=[])
        return batch

    def _checked(self, batch: ActionBatch, situation: dict[str, Any]) -> ActionBatch:
        """Both halves of validation, mechanical first because it is free.

        nttd's own check answers whether each action exists and takes these parameters,
        at no cost and with no argument. Only what survives it is worth a model's
        opinion, and the model is asked about sense rather than syntax.
        """
        proposed = [{"action": a.action, "params": a.params} for a in batch.actions]
        try:
            mechanical = self._tools.client.validate_actions(proposed)
        except Exception:
            logger.debug("Could not pre-validate the batch", exc_info=True)
            mechanical = {}

        errors = mechanical.get("errors") or mechanical.get("invalid") or {}
        if errors:
            logger.warning("%s batch failed nttd's own check: %s", self.mode, errors)
            batch.reasoning = f"{batch.reasoning} [refused by validation: {errors}]"
            return ActionBatch(reasoning=batch.reasoning, actions=[])

        verdict = self._say("validator", VALIDATOR_BRIEF.format(
            situation=_brief(situation), batch=_brief(proposed),
        ))
        logger.info("  validator: %s", verdict.strip()[:200])
        batch.reasoning = f"{batch.reasoning} [validator: {verdict.strip()[:160]}]"
        return batch

    def _say(self, agent: str, prompt: str) -> str:
        from langgraph.errors import GraphRecursionError

        try:
            result = self._agents[agent].invoke(
                {"messages": [("user", prompt)]},
                config={"recursion_limit": _TURNS},
            )
        except GraphRecursionError:
            logger.warning("%s %s did not finish", self.mode, agent)
            return f"No answer: the {agent} did not settle."

        # `.text` rather than `.content`. Anthropic returns content as a LIST of blocks
        # whenever there is more than one, so `.content` is a list as often as a string.
        # Taking it raw put a stringified list of dicts into the next agent's prompt, and
        # crashed outright the moment anything called a string method on it.
        return result["messages"][-1].text

    def _mistake_note(self) -> str:
        """Repeated refusals, in the words most likely to change behaviour.

        A count tells a model nothing. "build_dock has been refused three times for the
        same reason" tells it to do something else.
        """
        repeated = self._ledger.repeated_mistakes(at_least=2)
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


def _brief(value: Any) -> str:
    """Computed data as compact JSON for a prompt.

    Compact rather than indented: an indented dump of a route list is mostly whitespace,
    and whitespace is tokens.
    """
    import json

    return json.dumps(value, separators=(",", ":"), default=str)

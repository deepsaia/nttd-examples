#!/usr/bin/env python3
"""A LangGraph entry: several agents deciding what one company does.

    uv sync --extra langgraph
    export ANTHROPIC_API_KEY=...
    uv run python examples/langgraph_runner.py --session ses_... --token pt_...

This is the shape a multi-agent entry takes in nttd. A session holds **one contestant
company**; however many agents you run, they agree on a batch and one runner submits it.
nttd sees a single stepper and never learns how the decision was reached, which is
deliberate: how you organise your agents is the thing being measured, not something the
benchmark should constrain.

The graph here is two nodes, which is enough to show the pattern:

    survey  ->  plan  ->  (actions, submitted as one step)

**survey** reads the world with tools and writes a short brief. **plan** turns that brief
into actions. Splitting them costs an extra call and buys a real thing: the planner sees
a summary it can hold in mind rather than a full game state, and the surveyor can spend
tool calls freely because the world is paused while it does.

## Two decisions worth copying

**Actions come back as structured output, never parsed out of prose.** The model is given
a schema and returns data. The version of this file that came before scraped JSON out of
markdown fences with a regular expression, and every model quirk was a new edge case in
that parser.

**Tools read, they do not act.** Acting goes through the step call. A model that could
act through a tool would act between steps, and a step would then mean a different amount
of world depending on how many tools it happened to call.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field

from agents import action_brief
from agents.nttd_client import NttdClient
from agents.tools import NttdTools

logger = logging.getLogger("langgraph")

MODEL = "claude-sonnet-5"

# The slice of nttd's surface this runner plays. Narrowed deliberately: the full
# catalogue is around 120 actions, and a road-and-buses runner handed the rail, marine
# and aviation references as well pays for context it will never call.
CATEGORIES = ("road", "vehicle", "orders", "company", "query")


class Action(BaseModel):
    """One action, in the shape nttd's step call takes."""

    action: str = Field(description="An action name from nttd's manifest, e.g. set_loan")
    params: dict[str, Any] = Field(
        default_factory=dict, description="That action's parameters",
    )


class Plan(BaseModel):
    """What to do this step, and why.

    ``reasoning`` is for your logs, not for nttd. It is asked for because a model that
    has to justify a batch tends to propose a more coherent one, and because a run you
    cannot explain afterwards is hard to improve.
    """

    reasoning: str = Field(description="One or two sentences on why these actions")
    actions: list[Action] = Field(description="Empty is fine: waiting is a move")


class RunState(TypedDict):
    """What flows through the graph on one step."""

    observation: dict[str, Any]
    step: int
    brief: Annotated[str, "what the surveyor found"]
    plan: Plan


SURVEY_PROMPT = """You are surveying an OpenTTD transport company for a planner.

Use your tools to find out what matters right now: what the company owns, what it is
earning, which towns and industries are unserved, and what it can afford. Then write at
most eight lines of findings. No recommendations, no strategy: the planner decides.

Game date: {date}. Step {step}."""

# Strategy only. What each action is and what it takes comes from `action_brief`, which
# generates it from nttd's manifest, so this never restates something that can change.
# The file this replaced was 47,000 characters of hand-written reference and had already
# drifted: it still told models to call `build_rail`, which nttd deleted.
PLAN_PROMPT = """You run a transport company in OpenTTD. Decide what to do this step.

The surveyor reports:
{brief}

How to play well:
- Move cargo people want moved. A route earns on what it delivers, and payment falls the
  longer cargo sits, so a short busy route beats a long idle one.
- Ask where something fits before building it. Guessing a tile is the commonest way to
  waste a step.
- Borrow to build something that will earn, not to hold cash. Interest runs whether or
  not the money is working.
- Doing nothing is a legitimate answer. Return no actions if waiting is right.

{actions}"""


def build_graph(tools: NttdTools) -> Any:
    """Wire survey and plan into a graph.

    Built once and reused for every step: the graph is the policy, and the state is what
    changes.
    """
    from langchain_anthropic import ChatAnthropic
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import create_react_agent

    surveyor = create_react_agent(
        ChatAnthropic(model=MODEL, temperature=0.0), tools.as_langchain_tools(),
    )
    planner = ChatAnthropic(model=MODEL, temperature=0.3).with_structured_output(Plan)

    # Fetched once per run, not per step: the action surface cannot change mid-session,
    # and paying for it on every step would be the largest cost in the loop.
    actions = action_brief.build(tools.client, categories=CATEGORIES)

    def survey(state: RunState) -> dict[str, str]:
        game = state["observation"].get("game", {})
        prompt = SURVEY_PROMPT.format(
            date=game.get("game_date", "unknown"), step=state["step"],
        )
        reply = surveyor.invoke({"messages": [("user", prompt)]})
        return {"brief": reply["messages"][-1].content}

    def plan(state: RunState) -> dict[str, Plan]:
        prompt = PLAN_PROMPT.format(brief=state["brief"], actions=actions)
        return {"plan": planner.invoke(prompt)}

    graph = StateGraph(RunState)
    graph.add_node("survey", survey)
    graph.add_node("plan", plan)
    graph.add_edge(START, "survey")
    graph.add_edge("survey", "plan")
    graph.add_edge("plan", END)
    return graph.compile()


def play(client: NttdClient, graph: Any, max_steps: int) -> dict[str, Any]:
    """Step until the scenario ends the run, or the safety limit trips."""
    result = client.reset()

    for step in range(max_steps):
        decision: Plan = graph.invoke({
            "observation": result["snapshot"], "step": step,
        })["plan"]

        logger.info("Step %d: %s", step, decision.reasoning)
        result = client.step(
            [{"action": a.action, "params": a.params} for a in decision.actions],
        )

        # Refusals are recorded and worth watching: a policy whose actions are mostly
        # refused is failing in a way its score will not explain.
        _log_refusals(result)

        if result.get("terminated"):
            logger.info("Run ended: %s", result.get("end_reason") or "no reason given")
            return result

    logger.warning("Stopped at the %d step safety limit", max_steps)
    return result


def _log_refusals(result: dict[str, Any]) -> None:
    for outcome in result.get("action_results") or []:
        if outcome.get("status") != "success":
            logger.warning(
                "  refused %s: %s",
                outcome.get("action_type"), outcome.get("error") or "no reason given",
            )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="A LangGraph nttd runner")
    parser.add_argument("--session", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--max-steps", type=int, default=200)
    args = parser.parse_args()

    client = NttdClient(base_url=args.url, session_id=args.session, token=args.token)
    graph = build_graph(NttdTools(client))

    # The model is declared here because nttd cannot see it. Spend is left unreported
    # rather than guessed: an unreported cost shows as blank on the board, which is a
    # different and more honest claim than zero.
    client.report(model=MODEL, participant_type="mas")

    result = play(client, graph, max_steps=args.max_steps)
    logger.info("Finished at step %s. Now: nttd submit --session %s",
                result.get("step"), args.session)
    return 0


if __name__ == "__main__":
    sys.exit(main())

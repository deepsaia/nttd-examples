# Runners

Two worked entries, plus a neuro-san multi-agent system.

| | |
|---|---|
| `minimal_runner.py` | a whole stepped run with no model and no framework. Start here |
| `langgraph_runner.py` | a LangGraph graph: survey, then plan, submitted as one step |
| `neuro_san_mas/` | a neuro-san agent network over the same session |

One per idea rather than one per SDK. There were LangChain, OpenAI and scripted runners
as well, all demonstrating the same loop through a different client, and four copies of
one idea drift in four directions.

## The loop

```python
result = client.reset()                 # pause, register, observe
while not result["terminated"]:
    actions = decide(result["snapshot"])
    result = client.step(actions)       # flush, advance once, observe
```

That is the whole protocol. `decide` is yours.

**The world is paused between steps**, so deliberation costs zero game-days. A slow
policy is not punished for being slow, which is the only way an LLM and a trained policy
can be compared on what they decide rather than how fast.

## A prompt has two halves, and they are kept apart

**Reference** is generated. `agents/action_brief.py` builds it from nttd's manifest,
which nttd generates from the GameScript, so a prompt cannot describe an action that does
not exist. About 6,000 characters for the categories one mode needs.

**Strategy** is hand-written and short: `agents/strategy/*.md`, one per mode plus a
shared `common.md`. What order to build in, which cargo pays, how to recover from a
refusal, when to wait. A manifest cannot know any of it.

These were once one 47,000 character file. Mixing them meant the reference half went
stale, as a hand-copied reference always does, and the strategy half was buried where
nobody could find or edit it. `tests/test_strategy.py` checks the hand-written half
against a running nttd so it cannot rot the same way.

The files carry SKILL.md frontmatter, so handing them to a skills-aware framework later
needs no rewriting.

## Actions are data, never parsed out of prose

The LangGraph runner asks the model for structured output against a schema. The version
before it scraped JSON out of markdown fences, and every model quirk became a new edge
case in that parser.

## Tools read, they do not act

Acting goes through the step call. A model that could act through a tool would act
between steps, and a step would then mean a different amount of world depending on how
many tools it happened to call.

## Refusals come back, and are acted on

A step returns what each of its actions did, including why one was refused. The runner
feeds refusals into the next decision rather than only logging them: a refused action
usually changes nothing in the world, so without this the model proposes it again every
step.

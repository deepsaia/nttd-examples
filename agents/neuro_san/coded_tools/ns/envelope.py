"""The one place an nttd action is shaped, and the one place its parameters are checked.

Two failures live here, and both cost game days to learn the hard way.

**The envelope is not optional.** An action is `{"action": name, "params": {...}}`. nttd's
StepRequest refuses parameters at the top level, so a tool that builds the dict by hand has one
chance in two of being wrong.

**Parameter names are not guessable.** `build_bridge` wants `start_x` and `start_y`, not the
`from_x` and `from_y` that every other spatial action uses; discovering that took a refused
action and a read of the manifest. The manifest is machine-readable and served live by the
engine being played, so the check happens before the action is submitted rather than after the
game rejects it.

The manifest is fetched from the running server rather than copied into this repository. A copy
is a second source of truth that goes stale against the engine it claims to describe.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

BASE_URL = os.environ.get("NTTD_API_URL", "http://127.0.0.1:8000")

# Fetched once per process. The action surface is fixed for the life of a server, and every
# tool would otherwise pull a hundred kilobytes of manifest per call.
_MANIFEST: dict[str, Any] | None = None


def action(name: str, **params: Any) -> dict[str, Any]:
    """One action in the shape the step endpoint requires."""
    return {"action": name, "params": params}


def manifest() -> dict[str, Any]:
    """Every action the engine will accept, as the engine itself describes them.

    Empty when the server cannot be reached, because a missing manifest must not stop a run:
    checking is a courtesy that saves a game day, not a gate that ends the session.
    """
    global _MANIFEST  # noqa: PLW0603 - one cache per process, deliberately
    if _MANIFEST is not None:
        return _MANIFEST
    try:
        reply = httpx.get(f"{BASE_URL}/v1/public/actions", timeout=30)
        reply.raise_for_status()
        _MANIFEST = reply.json().get("actions") or {}
    except (httpx.HTTPError, ValueError):
        _MANIFEST = {}
    return _MANIFEST


def check(batch: list[dict[str, Any]]) -> list[str]:
    """What is wrong with this batch, before a game day is spent finding out.

    Reports rather than raises, and one line per problem, because a batch with two mistakes
    should not need two round trips to discover them.
    """
    known = manifest()
    if not known:
        return []

    problems: list[str] = []
    for index, entry in enumerate(batch):
        name = entry.get("action")
        spec = known.get(name)
        if spec is None:
            problems.append(f"[{index}] {name} is not an action this engine knows")
            continue

        given = set((entry.get("params") or {}).keys())
        declared = set((spec.get("parameters") or {}).keys())
        # company_id is applied from the token and accepted everywhere.
        unknown = given - declared - {"company_id"}
        if unknown:
            problems.append(
                f"[{index}] {name} does not take {sorted(unknown)}. It takes {sorted(declared)}"
            )

        missing = {
            key for key, rule in (spec.get("parameters") or {}).items()
            if rule.get("required") and key not in given
        }
        # one_of nests three deep: a list of GROUPS, each a list of ALTERNATIVES, each a list
        # of keys. build_airport declares [[["tile"], ["x", "y"]]], meaning give a tile or give
        # both coordinates. Every key inside a group is optional on its own, which is why the
        # required check above cannot see a missing depot at all.
        for group in spec.get("one_of") or []:
            alternatives = [set(alt) for alt in group]
            for alt in alternatives:
                missing -= alt
            if not any(alt <= given for alt in alternatives):
                readable = " or ".join(sorted("+".join(sorted(a)) for a in alternatives))
                problems.append(f"[{index}] {name} needs {readable}")
        if missing:
            problems.append(f"[{index}] {name} needs {sorted(missing)}")

    # Deduplicated in order. Two one_of groups naming the same alternatives report the same
    # line twice, and one problem said twice reads as two problems.
    seen: list[str] = []
    for line in problems:
        if line not in seen:
            seen.append(line)
    return seen

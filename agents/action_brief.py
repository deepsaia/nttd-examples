"""Turn nttd's action catalogue into the reference half of a prompt.

This replaces a 47,000 character hand-written instruction file. That file restated, in
prose, what nttd already publishes as data: every action, its parameters, which are
required, and what the enums mean. Restating a generated thing by hand only ever ends
one way, and it had: it still told models to call ``build_rail``, which nttd deleted
because it was ``build_path`` with a shorter list. A model following those instructions
would have spent a step on a 400.

So the reference is generated per run, from the same manifest the server validates
against. It cannot describe an action that does not exist, and a new action appears in
the prompt the moment nttd ships it.

**What stays hand-written is strategy**, which is the part a manifest cannot know: which
cargo pays, when to borrow, whether to serve a town or an industry. That belongs in your
runner, and it should be short. The pain of the old file was that the two were mixed, so
the strategy nobody could find sat inside the reference nobody needed to write.
"""

from __future__ import annotations

from typing import Any

from agents.nttd_client import NttdClient

# Enough for a model to choose and call an action. `gamescript_function` and `tier` are
# nttd's own bookkeeping and mean nothing to a contestant, so they are left out rather
# than spent as context.
_MAX_ENUM_VALUES = 12


def build(client: NttdClient, categories: tuple[str, ...] = ()) -> str:
    """The action reference for this session, as markdown.

    Args:
        client: A client for the running session. The catalogue comes from the server
            rather than a vendored copy, because a vendored copy is the thing that goes
            stale.
        categories: Restrict to these categories, for example ``("road", "vehicle",
            "orders")``. Empty means everything the participant tier allows.

            Worth using. The full surface is around 120 actions, and a road-and-buses
            runner given the rail, marine and aviation catalogues as well is paying for
            context it will never call.
    """
    catalogue = client.action_manifest()
    actions: dict[str, Any] = catalogue["actions"]

    if categories:
        actions = {
            name: entry for name, entry in actions.items()
            if entry.get("category") in categories
        }

    playable = set(_flatten(client.available_actions()))
    if playable:
        # Cross-checked against what this session will actually accept, not just what
        # nttd can describe. The manifest covers operator actions too, and a prompt
        # listing an action the session refuses spends a step to find out.
        actions = {name: e for name, e in actions.items() if name in playable}

    lines = [
        "# Actions you can take",
        "",
        f"Generated from nttd's manifest ({catalogue.get('manifest_version', 'unknown')}), "
        "which is itself generated from the running GameScript. If an action is not "
        "listed here, this session will refuse it.",
        "",
    ]
    for category in sorted({entry.get("category", "other") for entry in actions.values()}):
        lines.append(f"## {category}")
        lines.append("")
        for name, entry in sorted(actions.items()):
            if entry.get("category", "other") == category:
                lines.extend(_render(name, entry))
        lines.append("")
    return "\n".join(lines)


def _flatten(by_category: dict[str, Any]) -> list[str]:
    """The action names out of a category-to-names mapping."""
    return [
        name for names in by_category.values()
        if isinstance(names, list) for name in names
    ]


def _render(name: str, entry: dict[str, Any]) -> list[str]:
    """One action: what it does, and what it needs."""
    signature = ", ".join(
        param for param, spec in sorted(entry.get("parameters", {}).items())
        if spec.get("required")
    )
    lines = [f"**{name}({signature})** {entry.get('description', '').strip()}"]

    for param, spec in sorted(entry.get("parameters", {}).items()):
        lines.append(f"  - `{param}`{_optional(spec)}: {spec.get('description', '')}")
        lines.extend(_enum(spec))
    lines.append("")
    return lines


def _optional(spec: dict[str, Any]) -> str:
    """Say the default rather than only that something is optional.

    A model told a parameter is optional still has to decide whether to pass it. Told
    the default, it can leave it alone.
    """
    if spec.get("required"):
        return ""
    default = spec.get("default")
    if isinstance(default, dict):
        default = default.get("expression", "a game constant")
    return f" (optional, default {default})"


def _enum(spec: dict[str, Any]) -> list[str]:
    """The constants a parameter accepts.

    Without these a model has the parameter name and no way to choose a value:
    ``condition`` is an integer, and which integer is the whole question.
    """
    values = (spec.get("enum") or {}).get("values") or {}
    if not values:
        return []
    shown = sorted(values.items())[:_MAX_ENUM_VALUES]
    listed = ", ".join(f"{key}={value}" for key, value in shown)
    if len(values) > _MAX_ENUM_VALUES:
        listed += f", and {len(values) - _MAX_ENUM_VALUES} more"
    return [f"    one of: {listed}"]

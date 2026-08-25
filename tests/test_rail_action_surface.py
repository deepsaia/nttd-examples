"""Whether the rail tools talk to the game the game actually has.

Every bug this file exists to catch is SILENT. Not one of them raises, and not one shows up in a
lint pass or a type check, because Python is perfectly happy to read a field that is not there:
`{}.get("lost")` is None, `int(None or 0)` is 0, and a tool built on either goes on returning
plausible answers about a game it is not really reading.

Four were found in one afternoon on this package alone, and each had already been written,
reviewed and lint-checked:

    usable_axis          looked for `direction` on a spot. The finder calls it
                         `valid_directions`, so every spot was rejected and the network could
                         never site a station at all.
    rail_health_check    read `lost` off get_vehicles, which does not carry it. `lost` lives on
                         get_vehicle_info. The mode's signature failure could never be detected.
    carries_cargo        read `wagons_attached` off a VEHICLE. It is a build_train reply field,
                         so every train in the fleet answered "carries nothing", which is a
                         verdict this network sells trains for.
    choose_train         read `engine_id` off the engine list, which calls it `id`. Every
                         purchase would have been submitted for engine 0.

So the names are checked against the engine rather than against a fixture. A fixture written by
the same hand that wrote the bug agrees with the bug; that has happened here before, in the
spend-reporting tests, and it is why these need a running nttd and skip without one.
"""

from __future__ import annotations

import ast
import os
import pathlib
import sys
from typing import Any

import pytest
import rail_replies
import requests

BASE_URL = os.environ.get("NTTD_BASE_URL", "http://127.0.0.1:8000")

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / "agents" / "neuro_san" / "coded_tools"
_RAIL = _TOOLS / "ns_rail_agent"

# The functions that name an action or a query. `query` sends a read to the GameScript and
# `action` builds an envelope for a batch, and both take the name as their first argument.
_NAMING = ("query", "action")

# The vocabulary the rail fixtures are built from. Kept in one place and used by both halves of
# the loop: the fixtures may only use these names, and this file checks these names against the
# running game. See tests/rail_replies.py for why either half alone proves nothing.
READS = rail_replies.FIELDS


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    """The live action surface, including the read_only tier the queries live in."""
    try:
        response = requests.get(f"{BASE_URL}/v1/public/actions", timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        pytest.skip(f"No nttd at {BASE_URL} ({exc})")
    return response.json()["actions"]


def _calls() -> list[tuple[str, str, set[str]]]:
    """Every (module, action, parameter names) the rail package sends to the game.

    A `**` unpack is resolved rather than skipped. The first version of this walk ignored one,
    which made `plan_build_corridor` look as though it passed no station footprint at all: the
    call was correct and the checker was blind, and a checker that silently sees less than it
    claims is worse than no checker. Only a no-argument call into `rail_rules` is resolvable,
    and anything else raises rather than being quietly dropped.
    """
    sys.path.insert(0, str(_TOOLS))
    try:
        from ns_rail_agent import rail_rules  # noqa: PLC0415
    finally:
        sys.path.remove(str(_TOOLS))

    found: list[tuple[str, str, set[str]]] = []
    for path in sorted(_RAIL.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in _NAMING or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue

            params = {kw.arg for kw in node.keywords if kw.arg}
            for kw in node.keywords:
                if kw.arg is None:
                    params |= _unpacked(kw.value, rail_rules, path)
            for arg in node.args[1:]:
                if isinstance(arg, ast.Dict):
                    params |= {
                        k.value for k in arg.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    }
            found.append((path.name, first.value, params))
    return found


def _unpacked(node: ast.AST, rail_rules: Any, path: pathlib.Path) -> set[str]:
    """The keys a `**something()` contributes, by calling the helper for real."""
    if isinstance(node, ast.Dict):
        return {
            k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
    if isinstance(node, ast.IfExp):
        return _unpacked(node.body, rail_rules, path) | _unpacked(node.orelse, rail_rules, path)
    if isinstance(node, ast.Call) and not node.args and not node.keywords:
        func = node.func
        helper = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if helper and hasattr(rail_rules, helper):
            return set(getattr(rail_rules, helper)())
    raise AssertionError(
        f"{path.name} unpacks something this check cannot resolve, so its parameters would "
        "go unchecked. Pass them explicitly or move them into a no-argument rail_rules helper."
    )


def test_the_walk_actually_found_the_calls() -> None:
    """Every assertion below is over a list, so an empty one would pass in silence."""
    calls = _calls()
    assert len(calls) >= 20, f"only {len(calls)} calls found; the walk is not seeing the package"
    named = {action for _, action, _ in calls}
    for expected in ("build_rail_station", "connect_rail", "connect_depot", "build_train"):
        assert expected in named, f"{expected} is not being seen by the walk"


def test_every_action_the_rail_tools_name_exists(manifest: dict[str, Any]) -> None:
    """A tool once called `check_rail_connected`, which nttd has never had."""
    for module, action, _ in _calls():
        assert action in manifest, f"{module} calls {action}, which nttd does not have"


def test_every_parameter_the_rail_tools_pass_is_real(manifest: dict[str, Any]) -> None:
    """`is_truck` for `is_truck_stop` made every truck route silently a bus route."""
    for module, action, params in _calls():
        real = set(manifest[action].get("parameters") or {})
        unknown = sorted(params - real)
        assert not unknown, (
            f"{module} passes {unknown} to {action}, which takes {sorted(real)}"
        )


def test_every_reply_field_the_rail_tools_read_is_published(manifest: dict[str, Any]) -> None:
    """The four silent bugs in this file's docstring were all of this shape."""
    for action, fields in READS.items():
        assert action in manifest, f"READS names {action}, which nttd does not have"
        returns = manifest[action].get("returns") or {}
        published = set(returns.get("fields") or [])
        for inner in (returns.get("nested") or {}).values():
            published |= set(inner)
        unknown = sorted(fields - published)
        assert not unknown, (
            f"the rail tools read {unknown} from {action}, which publishes {sorted(published)}"
        )


def test_the_table_describes_code_that_exists() -> None:
    """A field left in READS after its reader is deleted checks nothing and reads as coverage."""
    source = "\n".join(path.read_text() for path in _RAIL.glob("*.py"))
    for action, fields in READS.items():
        assert action in source, f"READS names {action}, which no rail tool calls"
        for field in fields:
            assert f'"{field}"' in source, (
                f"READS claims {action}.{field} is read, and no rail tool reads it"
            )


def test_lost_is_never_read_from_the_vehicle_list(manifest: dict[str, Any]) -> None:
    """The specific miss, kept as its own check because it is the mode's whole failure mode.

    `lost` is the game's own answer to "can this train run its line", and trace_route's
    documentation names it as the authority over any track walk. It is published by
    get_vehicle_info and NOT by get_vehicles, and read off the list it is simply absent, which
    is indistinguishable from a healthy fleet.
    """
    assert "lost" in (manifest["get_vehicle_info"]["returns"]["fields"])
    assert "lost" not in (manifest["get_vehicles"]["returns"]["fields"])

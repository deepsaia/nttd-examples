"""One guard around every tool that talks to nttd, so ten tools do not carry ten copies of it.

A coded tool has exactly three ways to fail before it has done anything: the credentials are
missing, the server does not answer, and the GameScript answers the query with success false
and a reason. All three raise, and a raised exception out of `async_invoke` reaches the model
as a framework error with no reason attached, which is the one form a model cannot act on.

It is a function taking the work rather than a factory returning a gateway, because the awaits
have to be inside the guard too. A helper that only built the gateway would leave every tool to
repeat the same two except clauses around its own queries, which is the duplication this exists
to remove: ten tools, five lines each.

The three answers are deliberately different. Missing credentials are the runner's business and
nothing here can invent them. An unreachable server means nothing was changed, which is what
decides whether a retry is safe. A refused query carries the engine's own reason, "1 of 71 have
no through connection, first at (93,185)", and that reason is the only part worth having, so it
is passed through word for word.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway, QueryRefused
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where `ns` is a package beside the flat tools
    # and the repository above it is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a tool resolving from anywhere on PYTHONPATH.
    from ns.gateway import NttdGateway, QueryRefused

# What a tool hands over: its own body, with the open session in front of the arguments
# `async_invoke` was given.
Work = Callable[[NttdGateway, dict[str, Any], dict[str, Any]], Awaitable[Any]]


async def guarded(work: Work, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
    """Open the session, run the tool's body, and turn each of the three failures into a reply."""
    try:
        gateway = NttdGateway(sly_data)
    except ValueError as missing:
        return f"Error: {missing}. The runner supplies both; do not invent them."

    # Construction is guarded separately from the body, and only construction is allowed to
    # answer for a ValueError. int() on a model-supplied word raises ValueError too, and one
    # guard over both would report a mistyped count as a missing participant token.
    try:
        return await work(gateway, args, sly_data)
    except QueryRefused as refused:
        return (
            f"Error: nttd refused the query: {refused}. Nothing was changed, and that reason is "
            "the engine's own: act on it rather than asking again."
        )
    except httpx.HTTPError as unreachable:
        return (
            f"Error: nttd did not answer: {unreachable}. Nothing was changed and no game day was "
            "spent. The session may have ended; do not retry more than once."
        )

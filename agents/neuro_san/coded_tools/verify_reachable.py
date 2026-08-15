"""The gate that separates a built thing from a working one.

**A `success` from a build action is not a route.** This is the single most expensive
lesson in the benchmark and it holds in every mode:

- A road stop and the road to it both reported success while the stop was connected to
  nothing: the bus sat at the depot burning running costs for 60 game days.
- A rail depot reported `already_connected` while reaching 5 tiles of a 71 tile line. Four
  trains sat in their depots for a whole game year with correct orders.
- A water depot found beside a dock was in a pool cut off from it. Ships circled while that
  dock held 123 waiting passengers.

So nothing buys a vehicle until this returns `reachable`. The check is read-only, needs no
game ticks and works while paused, which makes it free next to the build it protects.

It reports rather than decides where it cannot be sure. `trace_route` gives false negatives
on rail, answering "did not trace" for a line a train then ran, so a rail corridor that
fails here is reported with what the BUILD said as well: `connect_rail` naming a tile and an
error is authoritative, an unexplained trace failure is not.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where these modules are siblings and the
    # package above them is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a tool resolving from anywhere on
    # PYTHONPATH, which is what keeps a `class` reference in a HOCON from reaching
    # arbitrary code.
    from nttd_gateway import NttdGateway

# A depot that reaches only its own stub answers with a handful of tiles. A working
# junction reaches most of the corridor, so the test is a share of the line rather than a
# constant: a 5 tile answer on a 71 tile line is the failure, on a 6 tile line it is fine.
REACHES_ENOUGH_OF_THE_LINE = 0.5


class VerifyReachable(CodedTool):
    """Whether a vehicle starting at one point can actually get to another."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
        transport = str(args.get("transport_type") or "road")
        start = (int(args["from_x"]), int(args["from_y"]))
        end = (int(args["to_x"]), int(args["to_y"]))

        traced = await gateway.query(
            "trace_route",
            {
                "from_x": start[0], "from_y": start[1],
                "to_x": end[0], "to_y": end[1],
                "transport_type": transport,
            },
        ) or {}

        reached = int(traced.get("tiles_reachable") or 0)
        verdict = {
            "reachable": bool(traced.get("line_exists")),
            "tiles_reachable": reached,
            "transport_type": transport,
            "from": list(start),
            "to": list(end),
        }

        # One tile means connected to nothing at all, whatever else the trace says. That is
        # the road failure, and it is unambiguous in every mode.
        if reached <= 1:
            verdict["reachable"] = False
            verdict["why"] = "connected to nothing: the vehicle cannot leave"
        elif not verdict["reachable"]:
            verdict["why"] = (
                "no through path found. On rail this is sometimes a false negative, so "
                "trust what the build reported: a connect that named a failing tile is "
                "authoritative, an unexplained trace failure is not."
            )
        return verdict

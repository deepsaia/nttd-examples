"""Build a locomotive WITH wagons, and refuse to build one without.

This is rule 6, the one that earns zero while looking complete. `buy_vehicle` gives an engine.
An engine on its own runs its route, reports as healthy, shows a profit line of nothing, and
carries no cargo, and there is no report anywhere that distinguishes it from a working train
except the cargo figure that stays at zero. `build_train` couples wagons in the same call and
says how many actually attached.

So this tool only ever stages `build_train`, always with a wagon count and a cargo, and
plan_dispatch checks the reply before starting anything.

**Only into a connected depot.** A train is built in a depot, and a depot that was built but
never joined to the line holds a train that cannot leave. confirm_rail_route knows which
corridors have one; this refuses the rest rather than spending the money to find out.

**Nothing is submitted.** Only commit_plan moves the clock.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import envelope, session, timing
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.plan import Plan
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_keys as rail
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_rules as rules
except ImportError:
    from ns import envelope, session, timing
    from ns.gateway import NttdGateway
    from ns.plan import Plan

    from ns_rail_agent import rail_keys as rail
    from ns_rail_agent import rail_rules as rules


class PlanBuyTrain(CodedTool):
    """Stages one locomotive with wagons coupled, into a corridor's connected depot."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._stage, args, sly_data)

    async def _stage(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        pair_id = str(args.get("pair_id") or "").strip()
        intents = sly_data.get(rail.INTENT) or {}
        intent = next(
            (value for key_id, value in intents.items() if _same(key_id, pair_id)), None,
        )
        if intent is None:
            return (
                f"Error: {pair_id or '(nothing)'} is not a staged corridor. Use a pair_id "
                "confirm_rail_route reports as ready_for_a_train."
            )

        depot = intent.get("depot") or {}
        if not depot.get("connected"):
            return (
                f"Error: {intent['pair_id']} has no depot joined to its line, so a train built "
                "there could not leave it. Run plan_add_depot, commit, then confirm_rail_route "
                "until it reports ready_for_a_train."
            )

        engine_id = args.get("engine_id")
        wagon_id = args.get("wagon_id")
        if engine_id is None or wagon_id is None:
            return (
                "Error: both engine_id and wagon_id are needed, and both come from "
                "choose_train. A locomotive without wagons runs the route and carries "
                "nothing, so this tool will not build one."
            )

        world = await gateway.observe()
        price = int(args.get("price") or 0)
        total, remaining = timing.horizon(world)
        too_late, why = timing.too_late_to_buy(price, world, total - remaining)
        if too_late:
            return f"Error: {why}"

        wagons = int(args.get("num_wagons") or rules.WAGONS)
        action = envelope.action(
            "build_train",
            depot_x=int(depot["x"]), depot_y=int(depot["y"]),
            engine_id=int(engine_id),
            wagon_id=int(wagon_id),
            num_wagons=wagons,
            **({"cargo_id": int(intent["cargo_id"])} if intent.get("cargo_id") is not None else {}),
        )
        problems = envelope.check([action])
        if problems:
            return f"Error: the train cannot be staged as written: {'; '.join(problems)}"

        plan = Plan(sly_data)
        waiting = plan.add(action)
        return {
            "staged": 1,
            "waiting_in_plan": waiting,
            "train": {
                "pair_id": intent["pair_id"], "cargo": intent.get("cargo"),
                "engine_id": int(engine_id), "wagon_id": int(wagon_id),
                "num_wagons": wagons,
                "depot": {"x": depot["x"], "y": depot["y"]},
            },
            "plan": plan.describe(),
            "next": (
                f"commit_plan submits it. {wagons} wagons is one per platform tile, because a "
                "train longer than its platform does not load fully. The reply says how many "
                "wagons actually attached, and plan_dispatch reads that before starting it: a "
                "locomotive whose wagons all failed is the one shape of broken train that "
                "every other report calls healthy."
            ),
        }


def _same(one: str, other: str) -> bool:
    return "".join(one.split()).lower() == "".join(other.split()).lower()

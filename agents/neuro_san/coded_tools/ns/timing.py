"""How long things take, worked out from the session rather than assumed.

Every network here must play any tier. The shipped ones run 366, 731, 1827 and 3653 game days,
so a rule written as "before day 75" or "in a one year run" is right for one of them and wrong
for three. The engine reports its own horizon, and these functions read it.

Three things were hardcoded and each one is a different mistake:

**"A one year run"** was simply T1. Two components of the rating that cannot be won in 366 days
CAN be won in 1827: SCORE_MIN_PROFIT needs vehicles older than two years, which T3 and T4 have
time for and T1 and T2 do not. Telling a five year run not to bother is throwing away 100 points.

**"Nothing is a fault before day 75"** came from one measured run where cargo delivered stayed at
exactly 0 until day 73. That was a property of THAT map and THOSE routes, which were long: the far
end of a 289 tile trunk did not see its first aircraft until day 43. On a dense map with short
legs the same rule blinds the network for two months. What that number was really standing in for
is "has this vehicle had time to complete a trip yet", which is computable from the leg and the
vehicle's speed.

**"An aircraft needs about 120 days to return its price"** is a property of a vehicle and a route,
not of a session, so it does not scale with the tier. It is still a guess when used as a constant.
Once the fleet has earned anything at all, the real figure is observable, and observation beats a
remembered average from a different map.
"""

from __future__ import annotations

from typing import Any

# OpenTTD only counts a vehicle towards SCORE_MIN_PROFIT once it is older than
# VEHICLE_PROFIT_MIN_AGE, which vehicle_func.h defines as two calendar years.
PROFIT_MIN_AGE_DAYS = 730

# What to assume when nothing has been earned yet and no leg is known. Deliberately a fallback
# and named as one: it is the rough figure an aircraft took to return its price in one measured
# run, used only until the fleet produces a real number.
ASSUMED_PAYBACK_DAYS = 120

# A vehicle is given at least this long before anything it does is called a fault, whatever the
# arithmetic says. Guards against a zero or nonsense speed making the derived window meaningless.
LEAST_SETTLING_DAYS = 20


def horizon(observation: dict[str, Any]) -> tuple[int, int]:
    """How long the run is and how much is left, from the game itself.

    Zero total means the run is not bounded in days at all, which is a different thing from
    having none left, and callers must not confuse the two.
    """
    game = observation.get("game") or {}
    total = int(game.get("game_days_total") or 0)
    left = int(game.get("game_days_remaining") or 0)
    return total, left


def long_enough_for_min_profit(total_days: int) -> bool:
    """Whether this session lasts long enough for SCORE_MIN_PROFIT to be winnable at all.

    The component reads the worst profit among vehicles OLDER than two years, so a run shorter
    than that scores nothing for it however well it is played, and a run longer than it should
    plan for it: it is 100 of the 1000 points.
    """
    return total_days == 0 or total_days > PROFIT_MIN_AGE_DAYS


def round_trip_days(distance_tiles: float, speed: float) -> int:
    """Roughly how long a vehicle takes to fly out and back.

    The honest replacement for a fixed settling period. Speed is reported in the game's own
    units and a tile is covered at a rate that varies with vehicle and terrain, so this is an
    estimate and is used only to decide when judging becomes fair, never to predict revenue.
    """
    if distance_tiles <= 0 or speed <= 0:
        return LEAST_SETTLING_DAYS
    # Measured across recorded runs: aircraft cover roughly a tile per unit of speed per three
    # game days at these speeds. Out and back, then a margin for loading at both ends.
    one_way = (distance_tiles * 3.0) / max(speed, 1.0)
    return max(LEAST_SETTLING_DAYS, int(one_way * 2 * 1.3))


def settling_days(route: dict[str, Any], speed: float = 0.0) -> int:
    """How long before a vehicle on this route may be judged.

    Derived from the route it flies rather than from the calendar, because the question is
    whether it has had a chance to earn, and a long leg needs longer than a short one.
    """
    return round_trip_days(float(route.get("distance") or 0), speed)


def payback_days(price: int, daily_profit: float) -> int:
    """How many days this vehicle needs to return what it cost.

    Observed where possible. A fleet already earning tells you what a vehicle makes per day on
    this map far better than a constant carried over from another one.
    """
    if daily_profit > 0:
        return max(1, int(price / daily_profit))
    return ASSUMED_PAYBACK_DAYS


def observed_daily_profit(observation: dict[str, Any], elapsed_days: int) -> float:
    """What the fleet is actually earning per vehicle per day, or 0 when nothing has yet.

    Zero is the honest answer early in a run, and callers must treat it as "not known" rather
    than as "earns nothing": refusing to buy because a fleet that has not flown yet has not
    earned yet would stop a run before it starts.
    """
    vehicles = observation.get("vehicles") or []
    if not vehicles or elapsed_days <= 0:
        return 0.0
    earned = sum(float(v.get("profit_this_year") or 0) for v in vehicles)
    if earned <= 0:
        return 0.0
    return earned / len(vehicles) / elapsed_days


def too_late_to_buy(
    price: int, observation: dict[str, Any], elapsed_days: int
) -> tuple[bool, str]:
    """Whether a purchase can still pay for itself before the run ends.

    Refuses only when there is evidence to refuse on. Early in a run there is no observed rate,
    and early is exactly when buying is right, so an absent estimate permits the purchase. Late
    in a run the estimate exists and the refusal is grounded in what this company actually earns
    rather than in a number from another map.
    """
    total, left = horizon(observation)
    if not total:
        return False, ""

    rate = observed_daily_profit(observation, elapsed_days)
    needed = payback_days(price, rate)
    if left >= needed:
        return False, ""

    basis = (
        f"at the {rate:,.0f} a day this fleet earns per vehicle"
        if rate > 0
        else f"on an assumed {ASSUMED_PAYBACK_DAYS} day payback, since nothing has earned yet"
    )
    return True, (
        f"{left} game days remain and this would need about {needed} to return its "
        f"{price:,} cost {basis}. Holding the cash scores more than a depreciating asset."
    )

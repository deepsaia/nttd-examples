"""How long things take, worked out from the session rather than assumed.

Every network here must play any tier. The shipped ones run 366, 731, 1827 and 3653 game days,
so a rule written as "before day 75" or "in a one year run" is right for one of them and wrong
for three. The engine reports its own horizon, and these functions read it.

Three things were hardcoded and each one is a different mistake:

**"A one year run"** was simply T1. A component of the rating that cannot be won in 366 days CAN
be won in 1827: SCORE_MIN_PROFIT needs vehicles older than two years, which the long tiers have
time for and the short ones do not. Telling a five year run not to bother throws away 100 points.

**"Nothing is a fault before day 75"** came from one measured run where cargo delivered stayed at
exactly 0 until day 73. That was a property of THAT map and THOSE routes, which were long. It is
gone rather than replaced: an attempt to derive the same window per route needed a tiles per day
conversion the engine does not publish and no run here measured, and a guess dressed as a
derivation is worse than a guess. Fleet care judges faults that are faults at any age instead.

**"An aircraft needs about 120 days to return its price"** is a property of a vehicle and a route,
not of a session, so it does not scale with the tier and was never measured on the map being
played. Once the fleet has earned anything the real figure is observable, and where it is not
observable this module now declines to answer rather than substituting a remembered average.
"""

from __future__ import annotations

from typing import Any

# OpenTTD only counts a vehicle towards SCORE_MIN_PROFIT once it is older than
# VEHICLE_PROFIT_MIN_AGE, which vehicle_func.h defines as two calendar years.
PROFIT_MIN_AGE_DAYS = 730

# A calendar year, which is the period profit_last_year covers. Leap years make this 365 or 366
# and the difference is far inside the error of any rate estimated from it.
DAYS_IN_YEAR = 365

# What payback_days returns when there is no evidence to answer with.
UNKNOWN = 0


def horizon(observation: dict[str, Any]) -> tuple[int, int]:
    """How long the run is and how much is left, from the game itself.

    Zero total means the run is not bounded in days at all, which is a different thing from
    having none left, and callers must not confuse the two.
    """
    game = observation.get("game") or {}
    total = int(game.get("game_days_total") or 0)
    left = int(game.get("game_days_remaining") or 0)
    return total, left


def elapsed(observation: dict[str, Any]) -> int:
    """Days of the run already played, which is what most rules here are written in."""
    total, left = horizon(observation)
    return max(0, total - left) if total else 0


def long_enough_for_min_profit(total_days: int) -> bool:
    """Whether this session lasts long enough for SCORE_MIN_PROFIT to be winnable at all.

    The component reads the worst profit among vehicles OLDER than two years, so a run shorter
    than that scores nothing for it however well it is played, and a run longer than it should
    plan for it: it is 100 of the 1000 points.
    """
    return total_days == 0 or total_days > PROFIT_MIN_AGE_DAYS


def payback_days(price: int, daily_profit: float) -> int:
    """How many days this vehicle needs to return what it cost, or UNKNOWN.

    There is no default. A constant here would be a number from another map presented as a
    prediction about this one, and every caller has to be able to tell a real estimate from an
    absent one in order to decide whether it has grounds to refuse anything.
    """
    if daily_profit <= 0:
        return UNKNOWN
    return max(1, int(price / daily_profit))


def observed_daily_profit(observation: dict[str, Any], elapsed_days: int) -> float:
    """What the fleet actually earns per vehicle per day, or 0 when nothing has yet.

    Zero is the honest answer early in a run, and callers must treat it as "not known" rather
    than as "earns nothing": refusing to buy because a fleet that has not flown yet has not
    earned yet would stop a run before it starts.

    profit_last_year is preferred wherever the fleet has one, because it covers a known period.
    profit_this_year resets on the game's new year, so dividing it by the days elapsed in the
    WHOLE run understates the rate by more and more as a long session goes on: at day 400 of a
    1827 day run it would divide five weeks of earnings by 400 days and conclude the fleet barely
    earns, which is how a long run talks itself out of ever buying another vehicle.
    """
    vehicles = observation.get("vehicles") or []
    if not vehicles:
        return 0.0

    last_year = sum(float(v.get("profit_last_year") or 0) for v in vehicles)
    if last_year > 0:
        return last_year / len(vehicles) / DAYS_IN_YEAR

    # Still inside the first game year, so the run's own elapsed days ARE the days this figure
    # accumulated over.
    if elapsed_days <= 0:
        return 0.0
    this_year = sum(float(v.get("profit_this_year") or 0) for v in vehicles)
    if this_year <= 0:
        return 0.0
    return this_year / len(vehicles) / min(elapsed_days, DAYS_IN_YEAR)


def too_late_to_buy(
    price: int, observation: dict[str, Any], elapsed_days: int
) -> tuple[bool, str]:
    """Whether a purchase can still pay for itself before the run ends.

    Refuses only when there is evidence to refuse on, which means an observed earning rate and a
    horizon to measure it against. With no rate there is nothing to predict payback from, and the
    purchase is allowed: early in a run there is never a rate, and early is exactly when buying is
    right. An unbounded run is never too late either, since there is no end to be short of.
    """
    total, left = horizon(observation)
    if not total:
        return False, ""

    rate = observed_daily_profit(observation, elapsed_days)
    needed = payback_days(price, rate)
    if needed == UNKNOWN or left >= needed:
        return False, ""

    return True, (
        f"{left} game days remain and this would need about {needed} to return its "
        f"{price:,} cost at the {rate:,.0f} a day this fleet earns per vehicle. Holding the cash "
        "scores more than a depreciating asset."
    )

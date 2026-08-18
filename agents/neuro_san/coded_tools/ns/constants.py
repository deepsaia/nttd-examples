"""Every key these tools share, named once.

A typo in a `sly_data` key does not raise. It silently creates a second store, so one tool
writes `route` and another reads `routes` and both look correct while the network behaves as
if it has no memory at all. Naming them here makes that a NameError instead.

The keys listed in ALLOWED are the ones a registry must declare under
`allow.to_upstream.sly_data`. neuro-san's redactor is security-by-default: with nothing listed
it returns None, and every scrap of cross-turn state dies at the turn boundary. That is not a
theory, it is what made a network repeat one refused purchase 35 times.

TURN_LOCAL keys are deliberately NOT allowed upstream. A live asyncio.Lock cannot cross a
process boundary and a whole world snapshot has no business in a chat payload.
"""

from __future__ import annotations

from typing import Final

# --- carried between turns ---------------------------------------------------------------

# Actions staged but not yet submitted. Planning is free; only committing costs a game day.
PLAN: Final = "plan"

# Routes that exist and carry, each with its stations, its depot and what it was built for.
ROUTES: Final = "routes"

# What the game refused, so the same call is not made twice. This is the ledger whose absence
# let one purchase be submitted 35 times with the same error.
REFUSALS: Final = "refusals"

# The surveyed map. Towns do not move and coverage does not change, so this is computed once.
SITES: Final = "sites"

# What the strategist decided and why, so a later turn can tell whether it worked.
DECISIONS: Final = "decisions"

# How many turns have been taken, which is not the same as how many game days have passed.
TURNS: Final = "turns"

ALLOWED: Final = (PLAN, ROUTES, REFUSALS, SITES, DECISIONS, TURNS)

# --- credentials, downstream only --------------------------------------------------------

SESSION_ID: Final = "session_id"
TOKEN: Final = "token"

CREDENTIALS: Final = (SESSION_ID, TOKEN)

# --- turn local, never serialised --------------------------------------------------------

# The most recent full observation, cached so several tools in one turn read it once.
SNAPSHOT: Final = "snapshot"

# True once the session has closed itself, so nothing tries to act on a finished run.
ENDED: Final = "ended"

TURN_LOCAL: Final = (SNAPSHOT, ENDED)

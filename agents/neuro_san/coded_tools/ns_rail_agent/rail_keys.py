"""The sly_data keys rail writes, named once.

Separate from `ns/constants.py` because nothing outside this package reads them, and the
cross-mode contract should not grow a key per mode. Named at all for the reason that file
gives: a typo in a sly_data key does not raise. It silently creates a second store, so a tool
writing `rail_health` and one reading `rail_heath` both look correct while every train reads
as newly seen every turn and nothing is ever judged stuck.

All of these MUST appear in the registry's `allow.to_upstream.sly_data` block. neuro-san's
redactor is security-by-default: unlisted, they return None and every scrap of cross-turn
state dies at the turn boundary.

Vehicle ids are stored as STRING keys. sly_data crosses that boundary as JSON, and JSON has no
integer keys, so an int key written this turn is a string key next turn and the lookup meant to
find it misses.
"""

from __future__ import annotations

from typing import Final

# Which rail technology this company builds with. Decided once and then used by every station,
# every track segment, every depot and every engine, because a line built in one type and a
# locomotive bought in another is a train that cannot enter its own route. Cached because
# get_rail_types does not change within a game year and the answer is needed by four tools.
RAIL_TYPE: Final = "rail_rail_type"

# Producer and consumer industries paired by a cargo they share, from survey_rail_pairs.
# Cached because industries do not move and the survey is the expensive part of scouting.
PAIRS: Final = "rail_pairs"

# What each corridor was MEANT to be, written before the build and read by confirm_rail_route
# after it. Rail fails in the middle rather than at the ends, so the intent is the only thing
# that says which of six staged actions was the one that did not take.
INTENT: Final = "rail_intent"

# How long each train has been where it is, and what was last decided about it.
HEALTH: Final = "rail_health"

# Trains sent to a depot and awaiting sale.
RETIRING: Final = "rail_retiring"

# Days a train has been in service, so a report can call a late purchase new rather than
# judging it by how far through the run it is.
IN_SERVICE_DAYS: Final = "in_service_days"

# How many times a train has been repointed, and when it last was.
#
# The staged and the completed spelling are kept a pair of names apart on purpose. A plan_ tool
# stages a batch and commit_plan submits it, so at staging time the only true thing to record is
# the INTENT. Writing the accomplishment instead left a record saying a train had been repaired
# when the batch was never committed: the health check then stopped flagging a train that was
# still stuck, and plan_retire counted the repair as tried and sold it.
REPOINTS: Final = "repoints"
REPOINT_STAGED_DAY: Final = "repoint_staged_day"
REPOINTED_DAY: Final = "repointed_day"

# A sale was staged on this run day. Not an attempt until a commit has carried it.
SELL_STAGED_DAY: Final = "sell_staged_day"

# How many sales the game has actually refused for a train stopped in its depot.
SELL_ATTEMPTS: Final = "sell_attempts"

ALLOWED: Final = (RAIL_TYPE, PAIRS, INTENT, HEALTH, RETIRING)

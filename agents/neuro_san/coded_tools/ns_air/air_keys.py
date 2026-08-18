"""The two sly_data keys air fleet care writes, named once.

Separate from `ns/constants.py` because these are not shared: nothing outside this package
reads them, and the cross-mode contract should not grow a key per mode. The reason for naming
them at all is the same one that file gives. A typo in a sly_data key does not raise, it
silently creates a second store, so a tool that writes `air_health` and one that reads
`air_heath` both look correct while every aircraft reads as newly seen on every turn and
nothing is ever judged stuck.

Both MUST appear in the registry's `allow.to_upstream.sly_data` block. neuro-san's redactor is
security-by-default: with nothing listed it returns None and all cross-turn state dies at the
turn boundary. These keys exist precisely to carry elapsed time across turns, so unlisted they
are worse than useless, they are a timer that resets to zero every turn and therefore never
reaches the 30 days that make a verdict.

Vehicle ids are stored as STRING keys. sly_data crosses the turn boundary as JSON and JSON has
no integer keys, so an int key written this turn is a string key next turn and the lookup that
was meant to find it misses.
"""

from __future__ import annotations

from typing import Final

# What the health check saw, and when. Holds the run day of the last look, the game date the
# run was first observed on, and per vehicle: where it was, the day it arrived there, its
# verdict, and how often it has been repointed.
HEALTH: Final = "air_health"

# Aircraft on their way to a hangar to be sold, and how far each has got. Selling is two
# stage, so the intent has to outlive the turn that formed it.
RETIRING: Final = "air_retiring"

ALLOWED: Final = (HEALTH, RETIRING)

# --- fields inside those records -------------------------------------------------------------
#
# Named here as well, and for a second reason on top of the typo one. A plan_ tool stages a
# batch and commit_plan submits it, so at staging time the only true thing to record is the
# INTENT. An earlier version wrote the accomplishment instead, and a repoint that was staged and
# never committed left a record saying the aircraft had been repaired: the health check then
# stopped flagging a vehicle that was still stuck, and plan_retire counted the repair as tried
# and sold it. Keeping the two spellings a pair of names apart is what makes that distinction
# impossible to lose.

# A repoint was staged on this run day. Not proof of anything: the batch may never be committed.
REPOINT_STAGED_DAY: Final = "repoint_staged_day"

# The run day a staged repoint was seen to take effect, which means the aircraft has moved.
REPOINTED_DAY: Final = "repointed_day"

# How many repoints have actually taken effect, counted only on promotion from staged.
REPOINTS: Final = "repoints"

# A sale was staged on this run day, and is not an attempt until a commit has carried it.
SELL_STAGED_DAY: Final = "sell_staged_day"

# How many sales the game has actually refused for an aircraft stopped in its hangar.
SELL_ATTEMPTS: Final = "sell_attempts"

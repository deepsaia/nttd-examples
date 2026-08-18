"""Reading a number a model supplied, in the one place every tool reads one.

`int(args.get("count") or 1)` raises ValueError on "two" and on "a few". A model that says
"two" has made a recoverable misunderstanding, and a tool that raises turns it into a framework
error the model is told nothing about, so the next call repeats it. Coerced here it becomes a
default plus one line of reply, which is the form a model can learn from.

The clamp lives here for the same reason. `max(1, min(asked, most))` silently changed 500 days
into 120 and said nothing, so a network that asked for half a year and got four months could not
tell the difference between the two.
"""

from __future__ import annotations

from typing import Any


def said(note: str) -> dict[str, str]:
    """The note as a fragment of a tool's reply, and nothing at all when there is no note.

    A key holding an empty string is a line of reply that says nothing, and every tool that takes
    a number would carry one. Merged in with `|` so a report stays a single expression.
    """
    return {"note_on_the_number": note} if note else {}


def whole(value: Any) -> int | None:
    """A whole number, or None when the value is not one. Never raises."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def counted(
    value: Any, default: int, *, least: int = 1, most: int | None = None
) -> tuple[int, str]:
    """The number to use, and the one line to say about it. An empty line means it was taken as
    given.

    Absent is not the same as unreadable: a tool called without the argument at all gets the
    default in silence, because there is nothing for the model to learn from that.
    """
    number = whole(value)
    if number is None:
        if value is None:
            return default, ""
        return default, (
            f"{value!r} is not a whole number, so {default} was used. Pass a figure rather than a "
            "word next time; a count is a number."
        )
    if number < least:
        return least, f"{number} was asked for and {least} is the fewest, so {least} was used."
    if most is not None and number > most:
        return most, (
            f"{number} was asked for and {most} is the most one call may take, so {most} was "
            "used. Call again for more once this has been seen to work."
        )
    return number, ""

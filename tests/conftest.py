"""Test configuration.

The neuro-san coded-tool tests need the `neuro-san` extra. An optional extra should not
break collection for everyone who has not installed it, so those modules are skipped
rather than erroring: `uv sync --extra langgraph` legitimately produces an environment
without neuro_san, and seven collection errors is a misleading way to say so.

    uv sync --extra neuro-san     # to run them
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

collect_ignore: list[str] = []

if importlib.util.find_spec("neuro_san") is None:
    collect_ignore = [
        path.name for path in Path(__file__).parent.glob("test_*.py")
        if "neuro_san" in path.read_text() or "coded_tools" in path.read_text()
    ]

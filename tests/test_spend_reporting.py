"""Turning neuro-san's token accounting into what nttd records.

nttd runs no model, so it cannot observe a token count or a dollar cost. It records what it
is told. neuro-san does know, and for a long time nobody passed it on: `neuro_san_play` never
called /report at all, so every run it played published a blank cost column and a bundle
warning that no spend was reported.
"""

from __future__ import annotations

from examples.neuro_san_play import _spend_from

# Lifted from a real payload in logs/server.log, not invented. The previous fixture was built
# from the same wrong assumption as the code it tested, so both agreed and both were wrong:
# the per-model breakdown is NESTED under "models", with the network's totals flat alongside.
ACCOUNTING = {
    "total_tokens": 394_558,
    "prompt_tokens": 391_083,
    "completion_tokens": 3_475,
    "successful_requests": 9,
    "empty_responses": 0,
    "total_cost": 2.026_369,
    "time_taken_in_seconds": 71.2,
    "caveats": ["Token counts are approximate and estimated using tiktoken."],
    "models": {
        "anthropic": {
            "claude-opus-5": {
                "total_tokens": 388_447, "prompt_tokens": 386_405,
                "completion_tokens": 2_042, "successful_requests": 7,
                "empty_responses": 0, "total_cost": 1.983_075,
                "time_taken_in_seconds": 62.97,
            },
            "claude-sonnet-5": {
                "total_tokens": 6_111, "prompt_tokens": 4_678,
                "completion_tokens": 1_433, "successful_requests": 2,
                "empty_responses": 0, "total_cost": 0.043_509,
                "time_taken_in_seconds": 8.23,
            },
        },
    },
}


def test_the_breakdown_is_read_from_where_it_actually_is() -> None:
    """The defect this file exists for.

    `models` is nested. Walking the top level for it finds the `models` dict itself and reads
    the provider name as a model with zero tokens, which nttd then records as a real entry.
    Worse, it looks like it worked: no error, and a bundle that says no spend was reported.
    """
    spend = _spend_from(ACCOUNTING)
    assert {entry["model"] for entry in spend} == {"claude-opus-5", "claude-sonnet-5"}
    assert "anthropic" not in {entry["model"] for entry in spend}
    assert all(entry["prompt_tokens"] > 0 for entry in spend), "zero tokens means it read a level too high"


def test_each_model_is_reported_separately() -> None:
    """A front man on opus and workers on sonnet is exactly the split nttd keeps spend per
    model to show. Collapsing it would throw away what makes a multi-agent entry interesting.
    """
    spend = {entry["model"]: entry for entry in _spend_from(ACCOUNTING)}
    assert set(spend) == {"claude-opus-5", "claude-sonnet-5"}
    assert spend["claude-opus-5"]["prompt_tokens"] == 386_405
    assert spend["claude-sonnet-5"]["completion_tokens"] == 1_433


def test_the_provider_becomes_the_role() -> None:
    """nttd's role is free-form on purpose, and the provider is what neuro-san groups by."""
    assert all(entry["role"] == "anthropic" for entry in _spend_from(ACCOUNTING))


def test_the_network_totals_are_not_counted_a_second_time() -> None:
    """They sit as flat keys beside `models` and carry the same numbers summed up."""
    spend = _spend_from(ACCOUNTING)
    assert len(spend) == 2, [entry["model"] for entry in spend]
    assert sum(entry["prompt_tokens"] for entry in spend) == ACCOUNTING["prompt_tokens"]
    assert sum(entry["completion_tokens"] for entry in spend) == ACCOUNTING["completion_tokens"]


def test_a_model_neuro_san_cannot_price_reports_tokens_and_no_cost() -> None:
    """The guard this exists for.

    neuro-san prices models from its own table and falls back to a cost of zero with only a
    log warning when a model is missing from it. So a zero is far more likely to mean "no
    price for this model" than "this was free". Passing it through would publish a free run
    over one that spent real money; nttd tells the two apart, so the key is omitted.
    """
    unpriced = {"models": {"anthropic": {"mystery": {
        "prompt_tokens": 7, "completion_tokens": 3, "total_cost": 0.0}}}}
    entry = _spend_from(unpriced)[0]
    assert entry["prompt_tokens"] == 7
    assert "total_cost_usd" not in entry, "a fallback zero must not be reported as a price"


def test_a_real_price_is_passed_through() -> None:
    priced = {entry["model"]: entry for entry in _spend_from(ACCOUNTING)}
    assert priced["claude-opus-5"]["total_cost_usd"] == 1.983_075


def test_nothing_to_report_is_nothing_sent() -> None:
    """A turn that used no model should not post an empty declaration."""
    assert _spend_from({}) == []
    assert _spend_from({"total_tokens": 0}) == [], "the flat totals alone are not a breakdown"
    assert _spend_from({"models": {}}) == []


def test_a_malformed_entry_does_not_bring_the_run_down() -> None:
    """The accounting is another system's output, so its shape is not guaranteed."""
    assert _spend_from({"models": "not a dict"}) == []
    assert _spend_from({"models": {"anthropic": "not a dict"}}) == []
    assert _spend_from({"models": {"anthropic": {"m": "not a dict"}}}) == []

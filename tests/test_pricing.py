"""Tests for the pinned list_sync pricing document and tier selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.metrics import PricingError, calculate_cost, load_pricing_file, price_for

PRICING_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "pricing.list_sync.2026-10-31.json"
)


def load_prices():
    return load_pricing_file(PRICING_PATH)


def test_pinned_pricing_document_loads_with_expected_metadata_and_tiers():
    document = json.loads(PRICING_PATH.read_text())
    assert document["pricing_basis"] == "list_sync"
    assert document["unit"] == "per_MTok"
    assert document["verified_date"] == "2026-10-31"

    prices = load_prices()
    assert set(prices) == {"claude-haiku-5-5", "claude-sonnet-5-5"}

    haiku = prices["claude-haiku-5-5"]
    assert len(haiku.tiers) == 2
    assert haiku.tiers[0].max_prompt_tokens == 100000
    assert haiku.tiers[1].max_prompt_tokens is None
    # Deliberately absent; must not be invented.
    assert haiku.tiers[1].cache_write_5m is None
    assert haiku.tiers[1].cache_write_1h is None

    sonnet = prices["claude-sonnet-5-5"]
    assert sonnet.input == 2.0
    assert sonnet.output == 10.0
    assert sonnet.cache_read == 0.1


def test_haiku_under_100k_uses_first_tier_and_cache_rates():
    haiku = load_prices()["claude-haiku-5-5"]
    usage = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 50,
        "thinking_tokens": 200,
    }

    # 1000*0.10 + 500*0.50 + 100*0.01 + 50*0.125 = 357.25 / 1e6
    assert calculate_cost(usage, haiku) == pytest.approx(0.00035725)


def test_haiku_over_100k_uses_second_tier():
    haiku = load_prices()["claude-haiku-5-5"]
    usage = {"input_tokens": 100001, "output_tokens": 1000}

    # 100001*0.50 + 1000*2.50 = 52500.5 / 1e6
    assert calculate_cost(usage, haiku) == pytest.approx(0.0525005)


def test_haiku_over_100k_cache_write_without_rate_raises_not_invents():
    haiku = load_prices()["claude-haiku-5-5"]
    usage = {
        "input_tokens": 100001,
        "output_tokens": 1000,
        "cache_creation_input_tokens": 10,
    }

    with pytest.raises(PricingError, match="cache_write_5m"):
        calculate_cost(usage, haiku)


def test_sonnet_flat_price_includes_cache_rates():
    sonnet = load_prices()["claude-sonnet-5-5"]
    usage = {
        "input_tokens": 2000,
        "output_tokens": 500,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 50,
    }

    # 2000*2 + 500*10 + 100*0.10 + 50*2.50 = 9135 / 1e6
    assert calculate_cost(usage, sonnet) == pytest.approx(0.009135)


def test_price_for_uses_the_loaded_table():
    prices = load_prices()
    assert price_for("claude-haiku-5-5", prices) is prices["claude-haiku-5-5"]
    with pytest.raises(PricingError, match="no pinned list price"):
        price_for("not-a-model", prices)


def test_tiered_to_dict_records_both_tiers():
    haiku = load_prices()["claude-haiku-5-5"]
    payload = haiku.to_dict()
    assert len(payload["tiers"]) == 2
    assert payload["tiers"][1]["max_prompt_tokens"] is None
    assert "cache_write_5m" not in payload["tiers"][1]

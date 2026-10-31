"""Metrics and list-price cost accounting.

Cost is computed from **logged API usage**, never from token estimates
(EVAL_DESIGN §8). The usage object keeps every field the SDK returns,
including cache and thinking fields, so the raw record in a trace is
lossless. Only the fields with a pinned list price contribute to cost.

Pricing is explicit configuration. This repository pins no Anthropic list
prices, so :data:`PRICING_PER_MTOK` is empty and a live run must supply a
pricing mapping. :class:`PricingError` is raised rather than silently
substituting an estimate.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


class PricingError(ValueError):
    """No pinned list price is available for a billed token class."""


@dataclass(frozen=True)
class TokenPrice:
    """List price in USD per million tokens for one model."""

    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TokenPrice":
        if "input" not in raw or "output" not in raw:
            raise PricingError("price entry needs 'input' and 'output' per-MTok values")
        for key in ("input", "output", "cache_read", "cache_write"):
            value = raw.get(key)
            if value is None:
                if key in ("input", "output"):
                    raise PricingError(
                        f"price entry needs a numeric '{key}' per-MTok value"
                    )
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PricingError(f"price '{key}' must be a number, not {value!r}")
            if not math.isfinite(float(value)):
                raise PricingError(f"price '{key}' must be finite")
        return cls(
            input=float(raw["input"]),
            output=float(raw["output"]),
            cache_read=(
                None if raw.get("cache_read") is None else float(raw["cache_read"])
            ),
            cache_write=(
                None if raw.get("cache_write") is None else float(raw["cache_write"])
            ),
        )


# No list prices are pinned in the repo. Do not invent one.
PRICING_PER_MTOK: dict[str, TokenPrice] = {}


def load_pricing_file(path: Path) -> dict[str, TokenPrice]:
    """Load a model-id -> price mapping from JSON.

    Expected shape::

        {"claude-model-id": {"input": 1.0, "output": 5.0,
                             "cache_read": 0.1, "cache_write": 1.25}}
    """

    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise PricingError("pricing file must contain a JSON object")
    pricing: dict[str, TokenPrice] = {}
    for model_id, entry in raw.items():
        if not isinstance(entry, dict):
            raise PricingError(f"pricing entry for {model_id!r} must be an object")
        pricing[str(model_id)] = TokenPrice.from_mapping(entry)
    return pricing


def price_for(
    model_id: str,
    pricing: Mapping[str, TokenPrice] | None = None,
) -> TokenPrice:
    table = PRICING_PER_MTOK if pricing is None else pricing
    try:
        return table[model_id]
    except KeyError as exc:
        raise PricingError(
            f"no pinned list price for model {model_id!r}; supply explicit "
            "pricing configuration before a live run"
        ) from exc


def normalize_usage(raw: Any) -> dict[str, Any]:
    """Return the SDK usage object as a plain, lossless dict.

    Numeric counters are kept as numbers; any non-numeric field the provider
    adds later is preserved unchanged rather than dropped.
    """

    if raw is None:
        return {}
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    elif hasattr(raw, "dict"):
        raw = raw.dict()
    if not isinstance(raw, dict):
        return {}
    usage: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, bool):
            usage[key] = value
        elif isinstance(value, int):
            usage[key] = value
        elif isinstance(value, float):
            usage[key] = int(value) if value.is_integer() else value
        else:
            usage[key] = value
    return usage


def _int_field(usage: Mapping[str, Any], key: str) -> int:
    value = usage.get(key, 0) or 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def calculate_cost(usage: Mapping[str, Any], price: TokenPrice) -> float:
    """Cost in USD from one response's logged usage and a pinned list price.

    ``thinking`` tokens are preserved in the raw usage but are not a separate
    billed class here: the provider counts them within ``output_tokens``.
    Cache token classes require their own pinned price; falling back to the
    input price would be an estimate and is forbidden.
    """

    total = _int_field(usage, "input_tokens") * price.input
    total += _int_field(usage, "output_tokens") * price.output

    cache_read = _int_field(usage, "cache_read_input_tokens")
    if cache_read:
        if price.cache_read is None:
            raise PricingError("cache_read tokens present but no cache_read price pinned")
        total += cache_read * price.cache_read

    cache_write = _int_field(usage, "cache_creation_input_tokens")
    if not cache_write:
        cache_write = _int_field(usage, "cache_write_input_tokens")
    if cache_write:
        if price.cache_write is None:
            raise PricingError(
                "cache_creation tokens present but no cache_write price pinned"
            )
        total += cache_write * price.cache_write

    return total / 1_000_000


def add_usage(total: dict[str, Any], usage: Mapping[str, Any]) -> dict[str, Any]:
    """Accumulate usage counters without discarding unknown fields."""

    for key, value in usage.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            total[key] = value
            continue
        current = total.get(key, 0)
        total[key] = current + value if isinstance(current, (int, float)) else value
    return total


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def mean_ci(values: Iterable[float], z: float = 1.96) -> tuple[float, float]:
    """Normal-approximation 95% interval for a small descriptive mean.

    Mini-set numbers are smoke diagnostics only, never headline estimates.
    """

    items = list(values)
    if not items:
        return (0.0, 0.0)
    mean = _mean(items)
    if len(items) == 1:
        return (mean, mean)
    variance = sum((value - mean) ** 2 for value in items) / (len(items) - 1)
    half = z * math.sqrt(variance / len(items))
    return (max(0.0, mean - half), min(1.0, mean + half))


def wilson_interval(successes: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a 0/1 or fractional proportion."""

    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = (
        z
        * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
        / denominator
    )
    return (max(0.0, centre - half), min(1.0, centre + half))


def percentile(values: Iterable[float], q: float) -> float:
    """Nearest-rank percentile (q in [0, 1])."""

    items = sorted(values)
    if not items:
        return 0.0
    index = min(len(items) - 1, max(0, math.ceil(q * len(items)) - 1))
    return items[index]


def latency_stats(values: Iterable[float]) -> dict[str, float]:
    items = list(values)
    return {"p50": percentile(items, 0.50), "p95": percentile(items, 0.95)}

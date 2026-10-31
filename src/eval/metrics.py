"""Metrics and list-price cost accounting.

Cost is computed from **logged API usage**, never from token estimates
(EVAL_DESIGN §8). The usage object keeps every field the SDK returns,
including cache and thinking fields, so the raw record in a trace is
lossless. Only the fields with a pinned list price contribute to cost.

Pricing is explicit configuration. The pinned list-price file lives in
``config/`` and a live run must pass it with ``--pricing-file``.
:class:`PricingError` is raised rather than silently substituting an estimate
or filling in an absent cache rate.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


class PricingError(ValueError):
    """No pinned list price is available for a billed token class."""


def _optional_number(raw: Mapping[str, Any], key: str) -> float | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PricingError(f"price '{key}' must be a number, not {value!r}")
    if not math.isfinite(float(value)):
        raise PricingError(f"price '{key}' must be finite")
    return float(value)


def _required_number(raw: Mapping[str, Any], key: str) -> float:
    value = _optional_number(raw, key)
    if value is None:
        raise PricingError(f"price entry needs a numeric '{key}' per-MTok value")
    return value


@dataclass(frozen=True)
class PriceTier:
    """One prompt-size pricing tier in USD per million tokens."""

    max_prompt_tokens: int | None
    input: float
    output: float
    cache_read: float | None = None
    cache_write_5m: float | None = None
    cache_write_1h: float | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PriceTier":
        max_prompt = raw.get("max_prompt_tokens")
        if max_prompt is not None:
            if (
                isinstance(max_prompt, bool)
                or not isinstance(max_prompt, int)
                or max_prompt <= 0
            ):
                raise PricingError(
                    "tier 'max_prompt_tokens' must be a positive integer or null"
                )
        return cls(
            max_prompt_tokens=max_prompt,
            input=_required_number(raw, "input"),
            output=_required_number(raw, "output"),
            cache_read=_optional_number(raw, "cache_read"),
            cache_write_5m=_optional_number(raw, "cache_write_5m"),
            cache_write_1h=_optional_number(raw, "cache_write_1h"),
        )


@dataclass(frozen=True)
class TokenPrice:
    """List price in USD per million tokens for one model.

    Flat prices keep the legacy ``input``/``output``/``cache_write`` fields.
    Tiered prices use ``tiers``; the last tier must have
    ``max_prompt_tokens=None``. An absent cache-write rate is left as ``None``
    and only raises if that token class is actually used.
    """

    input: float | None = None
    output: float | None = None
    cache_read: float | None = None
    cache_write: float | None = None
    cache_write_5m: float | None = None
    cache_write_1h: float | None = None
    tiers: tuple[PriceTier, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TokenPrice":
        if "tiers" in raw:
            raw_tiers = raw["tiers"]
            if not isinstance(raw_tiers, list) or not raw_tiers:
                raise PricingError("'tiers' must be a non-empty list")
            tiers = tuple(PriceTier.from_mapping(entry) for entry in raw_tiers)
            previous = 0
            for index, tier in enumerate(tiers):
                if tier.max_prompt_tokens is None:
                    if index != len(tiers) - 1:
                        raise PricingError("only the last tier may be open-ended")
                else:
                    if tier.max_prompt_tokens <= previous:
                        raise PricingError("tier limits must be strictly increasing")
                    previous = tier.max_prompt_tokens
            if tiers[-1].max_prompt_tokens is not None:
                raise PricingError("the last tier must have max_prompt_tokens null")
            return cls(tiers=tiers)

        cache_write = _optional_number(raw, "cache_write")
        cache_write_5m = _optional_number(raw, "cache_write_5m")
        if cache_write_5m is None:
            cache_write_5m = cache_write
        return cls(
            input=_required_number(raw, "input"),
            output=_required_number(raw, "output"),
            cache_read=_optional_number(raw, "cache_read"),
            cache_write=cache_write,
            cache_write_5m=cache_write_5m,
            cache_write_1h=_optional_number(raw, "cache_write_1h"),
        )

    def to_dict(self) -> dict[str, Any]:
        if self.tiers:
            tiers: list[dict[str, Any]] = []
            for tier in self.tiers:
                row = {
                    key: value
                    for key, value in asdict(tier).items()
                    if value is not None or key == "max_prompt_tokens"
                }
                tiers.append(row)
            return {"tiers": tiers}
        return {
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write_5m": (
                self.cache_write_5m
                if self.cache_write_5m is not None
                else self.cache_write
            ),
            "cache_write_1h": self.cache_write_1h,
        }

    def tier_for(self, prompt_tokens: int) -> PriceTier:
        if self.tiers:
            for tier in self.tiers:
                if (
                    tier.max_prompt_tokens is None
                    or prompt_tokens <= tier.max_prompt_tokens
                ):
                    return tier
            raise PricingError(
                f"no pricing tier covers {prompt_tokens} prompt tokens"
            )
        if self.input is None or self.output is None:
            raise PricingError("flat price is missing input/output")
        return PriceTier(
            max_prompt_tokens=None,
            input=self.input,
            output=self.output,
            cache_read=self.cache_read,
            cache_write_5m=self.cache_write_5m or self.cache_write,
            cache_write_1h=self.cache_write_1h,
        )


# Prices are supplied explicitly from config/; do not hard-code a fallback.
PRICING_PER_MTOK: dict[str, TokenPrice] = {}


def load_pricing_file(path: Path) -> dict[str, TokenPrice]:
    """Load a pinned pricing document.

    Accepts either a direct model-id mapping or the pinned document shape::

        {"pricing_basis": "list_sync", "unit": "per_MTok",
         "models": {"claude-model-id": {...}}}

    Tiered model entries use a ``tiers`` list. The last tier must be
    open-ended (``max_prompt_tokens: null``).
    """

    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise PricingError("pricing file must contain a JSON object")
    if raw.get("pricing_basis") not in (None, "list_sync"):
        raise PricingError("Phase 2 pricing_basis must be 'list_sync'")
    if raw.get("unit") not in (None, "per_MTok"):
        raise PricingError("Phase 2 pricing unit must be 'per_MTok'")
    entries = raw.get("models", raw)
    if not isinstance(entries, dict):
        raise PricingError("'models' must be an object")
    pricing: dict[str, TokenPrice] = {}
    for model_id, entry in entries.items():
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


def _cache_write_tokens(usage: Mapping[str, Any]) -> tuple[int, int]:
    """Return (5m, 1h) cache-write tokens without double counting.

    Newer SDKs may expose a split; older ones expose only the total. A total
    with no split is treated as the default 5-minute cache write.
    """

    total = _int_field(usage, "cache_creation_input_tokens")
    if not total:
        total = _int_field(usage, "cache_write_input_tokens")
    write_5m = _int_field(usage, "cache_write_5m_input_tokens")
    if not write_5m:
        write_5m = _int_field(usage, "cache_creation_5m_input_tokens")
    write_1h = _int_field(usage, "cache_write_1h_input_tokens")
    if not write_1h:
        write_1h = _int_field(usage, "cache_creation_1h_input_tokens")
    nested = usage.get("cache_creation")
    if isinstance(nested, dict):
        if not write_5m:
            write_5m = _int_field(nested, "ephemeral_5m_input_tokens")
        if not write_1h:
            write_1h = _int_field(nested, "ephemeral_1h_input_tokens")
    if write_5m + write_1h == 0 and total:
        write_5m = total
    return write_5m, write_1h


def calculate_cost(usage: Mapping[str, Any], price: TokenPrice) -> float:
    """Cost in USD from one response's logged usage and a pinned list price.

    ``thinking`` tokens are preserved in the raw usage but are not a separate
    billed class here: the provider counts them within ``output_tokens``.
    Tier selection uses total prompt tokens (input + cache read + cache
    write). Cache token classes require their own pinned price; falling back
    to input or inventing a missing rate is forbidden.
    """

    input_tokens = _int_field(usage, "input_tokens")
    output_tokens = _int_field(usage, "output_tokens")
    cache_read = _int_field(usage, "cache_read_input_tokens")
    write_5m, write_1h = _cache_write_tokens(usage)
    prompt_tokens = input_tokens + cache_read + write_5m + write_1h
    tier = price.tier_for(prompt_tokens)

    total = input_tokens * tier.input
    total += output_tokens * tier.output

    if cache_read:
        if tier.cache_read is None:
            raise PricingError(
                "cache_read tokens present but no cache_read price pinned "
                f"for this tier ({prompt_tokens} prompt tokens)"
            )
        total += cache_read * tier.cache_read

    if write_5m:
        if tier.cache_write_5m is None:
            raise PricingError(
                "5m cache-write tokens present but no cache_write_5m price "
                f"pinned for this tier ({prompt_tokens} prompt tokens)"
            )
        total += write_5m * tier.cache_write_5m

    if write_1h:
        if tier.cache_write_1h is None:
            raise PricingError(
                "1h cache-write tokens present but no cache_write_1h price "
                f"pinned for this tier ({prompt_tokens} prompt tokens)"
            )
        total += write_1h * tier.cache_write_1h

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

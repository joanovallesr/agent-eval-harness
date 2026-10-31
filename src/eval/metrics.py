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
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from scipy.stats import chi2, t as student_t


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


# ---------------------------------------------------------------------------
# Phase-3 paired analysis (EVAL_DESIGN §1 and §7).
#
# Primary estimator: equal-weighted mean over held-out tools of per-tool mean
# paired differences, with a t-interval on the per-tool means. Repeats are
# averaged within a case first. The case-level bootstrap is a sensitivity
# check only.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TInterval:
    mean: float
    low: float
    high: float
    n: int
    sd: float
    se: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def t_interval(values: Sequence[float], confidence: float = 0.95) -> TInterval:
    """Two-sided Student-t interval for the mean of ``values``."""

    items = [float(value) for value in values]
    n = len(items)
    if n == 0:
        raise ValueError("t_interval needs at least one value")
    mean = sum(items) / n
    if n == 1:
        return TInterval(mean=mean, low=mean, high=mean, n=1, sd=0.0, se=0.0)
    variance = sum((value - mean) ** 2 for value in items) / (n - 1)
    sd = math.sqrt(variance)
    se = sd / math.sqrt(n)
    alpha = 1.0 - confidence
    critical = float(student_t.ppf(1.0 - alpha / 2.0, n - 1))
    half = critical * se
    return TInterval(
        mean=mean,
        low=mean - half,
        high=mean + half,
        n=n,
        sd=sd,
        se=se,
    )


def sample_variance(values: Sequence[float]) -> float:
    items = [float(value) for value in values]
    if len(items) < 2:
        return 0.0
    mean = sum(items) / len(items)
    return sum((value - mean) ** 2 for value in items) / (len(items) - 1)


@dataclass(frozen=True)
class CasePair:
    task_id: str
    tool: str
    agent_exact: float
    baseline_exact: float
    agent_cost: float
    baseline_cost: float
    agent_input_tokens: float
    agent_output_tokens: float
    baseline_input_tokens: float
    baseline_output_tokens: float
    agent_thinking_tokens: float = 0.0
    baseline_thinking_tokens: float = 0.0

    @property
    def difference(self) -> float:
        return self.agent_exact - self.baseline_exact

    @property
    def discordant(self) -> bool:
        return self.agent_exact != self.baseline_exact

    @property
    def log_cost_ratio(self) -> float | None:
        if self.agent_cost <= 0 or self.baseline_cost <= 0:
            return None
        return math.log(self.agent_cost / self.baseline_cost)


def _usable_records(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        record
        for record in records
        if str(record.get("status", "completed")) == "completed"
    ]


def _case_means(
    records: Sequence[Mapping[str, Any]],
    key: str,
) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for record in _usable_records(records):
        grouped.setdefault(str(record["task_id"]), []).append(
            float(record.get(key, 0.0) or 0.0)
        )
    return {
        task_id: sum(values) / len(values)
        for task_id, values in grouped.items()
        if values
    }


def _case_repeat_counts(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in _usable_records(records):
        task_id = str(record["task_id"])
        counts[task_id] = counts.get(task_id, 0) + 1
    return counts


def _thinking_tokens(record: Mapping[str, Any]) -> float:
    raw = record.get("raw_usage") or {}
    if not isinstance(raw, Mapping):
        return 0.0
    details = raw.get("output_tokens_details")
    if isinstance(details, Mapping):
        value = details.get("thinking_tokens")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    value = raw.get("thinking_tokens")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def build_case_pairs(
    agent_records: Sequence[Mapping[str, Any]],
    baseline_records: Sequence[Mapping[str, Any]],
    *,
    tool_by_case: Mapping[str, str] | None = None,
) -> list[CasePair]:
    """Pair two arms case-by-case, averaging repeats within a case first."""

    agent_exact = _case_means(agent_records, "exact")
    baseline_exact = _case_means(baseline_records, "exact")
    agent_cost = _case_means(agent_records, "cost_usd")
    baseline_cost = _case_means(baseline_records, "cost_usd")
    agent_in = _case_means(agent_records, "input_tokens")
    agent_out = _case_means(agent_records, "output_tokens")
    baseline_in = _case_means(baseline_records, "input_tokens")
    baseline_out = _case_means(baseline_records, "output_tokens")

    agent_thinking: dict[str, list[float]] = {}
    baseline_thinking: dict[str, list[float]] = {}
    for record in _usable_records(agent_records):
        agent_thinking.setdefault(str(record["task_id"]), []).append(
            _thinking_tokens(record)
        )
    for record in _usable_records(baseline_records):
        baseline_thinking.setdefault(str(record["task_id"]), []).append(
            _thinking_tokens(record)
        )

    recorded_tools: dict[str, str] = {}
    for record in list(agent_records) + list(baseline_records):
        tool = record.get("tool_name")
        if isinstance(tool, str) and tool:
            recorded_tools[str(record["task_id"])] = tool
    tools = dict(tool_by_case or {})
    tools.update(recorded_tools)

    common = sorted(set(agent_exact) & set(baseline_exact))
    missing_agent = sorted(set(baseline_exact) - set(agent_exact))
    missing_baseline = sorted(set(agent_exact) - set(baseline_exact))
    if missing_agent or missing_baseline:
        raise ValueError(
            "paired design requires the same cases in both arms; "
            f"missing agent={len(missing_agent)} baseline={len(missing_baseline)}"
        )

    agent_repeats = _case_repeat_counts(agent_records)
    baseline_repeats = _case_repeat_counts(baseline_records)
    mismatched = [
        task_id
        for task_id in common
        if agent_repeats.get(task_id) != baseline_repeats.get(task_id)
    ]
    if mismatched:
        raise ValueError(
            "paired arms have unequal completed repeat counts for "
            f"{len(mismatched)} case(s); finish/resume the run before analysis"
        )

    pairs: list[CasePair] = []
    for task_id in common:
        tool = tools.get(task_id, "")
        if not tool:
            raise ValueError(f"no tool recorded for case {task_id!r}")
        a_thinking = agent_thinking.get(task_id, [0.0])
        b_thinking = baseline_thinking.get(task_id, [0.0])
        pairs.append(
            CasePair(
                task_id=task_id,
                tool=tool,
                agent_exact=agent_exact[task_id],
                baseline_exact=baseline_exact[task_id],
                agent_cost=agent_cost[task_id],
                baseline_cost=baseline_cost[task_id],
                agent_input_tokens=agent_in[task_id],
                agent_output_tokens=agent_out[task_id],
                baseline_input_tokens=baseline_in[task_id],
                baseline_output_tokens=baseline_out[task_id],
                agent_thinking_tokens=sum(a_thinking) / len(a_thinking),
                baseline_thinking_tokens=sum(b_thinking) / len(b_thinking),
            )
        )
    return pairs


@dataclass(frozen=True)
class ToolStat:
    tool: str
    n: int
    mean_agent: float
    mean_baseline: float
    mean_difference: float
    sigma2_within: float
    mean_agent_cost: float
    mean_baseline_cost: float
    cost_ratio: float | None
    log_cost_ratio: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def per_tool_stats(pairs: Sequence[CasePair]) -> dict[str, ToolStat]:
    grouped: dict[str, list[CasePair]] = {}
    for pair in pairs:
        grouped.setdefault(pair.tool, []).append(pair)
    stats: dict[str, ToolStat] = {}
    for tool in sorted(grouped):
        rows = grouped[tool]
        n = len(rows)
        mean_agent = sum(row.agent_exact for row in rows) / n
        mean_baseline = sum(row.baseline_exact for row in rows) / n
        differences = [row.difference for row in rows]
        mean_agent_cost = sum(row.agent_cost for row in rows) / n
        mean_baseline_cost = sum(row.baseline_cost for row in rows) / n
        ratio = (
            mean_agent_cost / mean_baseline_cost
            if mean_baseline_cost > 0
            else None
        )
        stats[tool] = ToolStat(
            tool=tool,
            n=n,
            mean_agent=mean_agent,
            mean_baseline=mean_baseline,
            mean_difference=sum(differences) / n,
            sigma2_within=sample_variance(differences),
            mean_agent_cost=mean_agent_cost,
            mean_baseline_cost=mean_baseline_cost,
            cost_ratio=ratio,
            log_cost_ratio=math.log(ratio) if ratio and ratio > 0 else None,
        )
    return stats


def discordant_rate(pairs: Sequence[CasePair]) -> float:
    if not pairs:
        return 0.0
    return sum(1 for pair in pairs if pair.discordant) / len(pairs)


def primary_interval(pairs: Sequence[CasePair]) -> TInterval:
    stats = per_tool_stats(pairs)
    return t_interval([stat.mean_difference for stat in stats.values()])


def cost_ratio_interval(pairs: Sequence[CasePair]) -> dict[str, Any]:
    stats = per_tool_stats(pairs)
    logs = [
        stat.log_cost_ratio
        for stat in stats.values()
        if stat.log_cost_ratio is not None
    ]
    if not logs:
        raise ValueError("no positive per-tool cost ratios")
    interval = t_interval(logs)
    return {
        "ratio": math.exp(interval.mean),
        "low": math.exp(interval.low),
        "high": math.exp(interval.high),
        "n_tools": interval.n,
        "mean_log_ratio": interval.mean,
        "sd_log_ratio": interval.sd,
    }


def aggregate_cost_ratio(pairs: Sequence[CasePair]) -> float:
    agent_total = sum(pair.agent_cost for pair in pairs)
    baseline_total = sum(pair.baseline_cost for pair in pairs)
    if baseline_total <= 0:
        raise ValueError("baseline cost is zero")
    return agent_total / baseline_total


def pessimistic_u(m: int) -> float:
    """EVAL_DESIGN §7: u = sqrt((m-1) / chi2_{0.20, m-1})."""

    if m <= 1:
        raise ValueError(f"u needs m >= 2, got {m}")
    return math.sqrt((m - 1) / float(chi2.ppf(0.20, m - 1)))


def m_from_tool_counts(
    counts: Mapping[str, int] | Sequence[int],
    threshold: int = 10,
) -> int:
    values = counts.values() if isinstance(counts, Mapping) else counts
    return sum(1 for count in values if int(count) >= threshold)


def power_multiplier(C: int) -> float:
    """(t(0.975,C-1) + t(0.80,C-1)) / sqrt(C) from §7."""

    if C < 2:
        raise ValueError("C must be >= 2")
    return float(
        student_t.ppf(0.975, C - 1) + student_t.ppf(0.80, C - 1)
    ) / math.sqrt(C)


def power_passes(
    s_pess: float,
    C: int,
    *,
    delta: float = 0.05,
    assumed_gap: float = 0.0,
) -> bool:
    """§7: (t_.975 + t_.80) * s_tool / sqrt(C) < delta + Delta."""

    return power_multiplier(C) * s_pess < delta + assumed_gap


def required_n_t(
    sigma2_within: float | Sequence[float],
    tau2: float,
    *,
    floor: int = 100,
) -> int:
    """One n_t for every tool: max over per-tool 4*sigma2_within/tau2.

    Passing a sequence uses the maximum per-tool requirement, as §7 requires;
    a scalar is the single-pooled-variance shorthand.
    """

    if tau2 <= 0:
        return floor
    if isinstance(sigma2_within, (int, float)):
        values = [float(sigma2_within)]
    else:
        values = [float(value) for value in sigma2_within]
    if not values:
        return floor
    return max(
        floor,
        max(int(math.ceil(4.0 * value / tau2)) for value in values),
    )


def s_pess_from_components(
    tau2: float,
    sigma2_within: float,
    n_t: int,
    u: float,
) -> float:
    """s_pess = sqrt((u*tau)^2 + sigma2_within/n_t)."""

    if n_t <= 0:
        raise ValueError("n_t must be positive")
    return math.sqrt((u * u * tau2) + sigma2_within / n_t)


def select_c(
    s_pess: float,
    *,
    delta: float = 0.05,
    assumed_gap: float = 0.0,
    budget_ok: Mapping[int, bool] | None = None,
) -> int | None:
    """Smallest C in {6,7,8} that passes power (and the budget, if supplied)."""

    for C in (6, 7, 8):
        if budget_ok is not None and not budget_ok.get(C, False):
            continue
        if power_passes(
            s_pess,
            C,
            delta=delta,
            assumed_gap=assumed_gap,
        ):
            return C
    return None


def _dev_stats(
    pairs: Sequence[CasePair],
    excluded_tools: Iterable[str] | None = None,
) -> list[ToolStat]:
    stats = per_tool_stats(pairs)
    excluded = set(excluded_tools or ())
    return [stat for tool, stat in stats.items() if tool not in excluded]


def variance_components(
    pairs: Sequence[CasePair],
    *,
    excluded_tools: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Separate within-tool noise from between-tool variance (§7)."""

    tool_stats = _dev_stats(pairs, excluded_tools)
    if len(tool_stats) < 2:
        raise ValueError("variance components need at least two tools")
    differences = [stat.mean_difference for stat in tool_stats]
    observed_var = sample_variance(differences)
    within_noise = sum(
        stat.sigma2_within / stat.n for stat in tool_stats
    ) / len(tool_stats)
    tau2 = max(0.0, observed_var - within_noise)
    degrees = sum(stat.n - 1 for stat in tool_stats)
    pooled_within = (
        sum((stat.n - 1) * stat.sigma2_within for stat in tool_stats) / degrees
        if degrees > 0
        else 0.0
    )
    m = m_from_tool_counts({stat.tool: stat.n for stat in tool_stats})
    u = pessimistic_u(m) if m >= 2 else None
    return {
        "observed_loto_var": observed_var,
        "observed_loto_sd": math.sqrt(observed_var),
        "mean_within_noise": within_noise,
        "tau2": tau2,
        "tau": math.sqrt(tau2),
        "sigma2_within": pooled_within,
        "sigma_within": math.sqrt(pooled_within),
        "m": m,
        "u": u,
        "n_tools": len(tool_stats),
    }


def loto_components(
    pairs: Sequence[CasePair],
    *,
    excluded_tools: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Leave-one-tool-out spread and pessimistic sizing inputs."""

    tool_stats = _dev_stats(pairs, excluded_tools)
    stats = {stat.tool: stat for stat in tool_stats}
    tools = list(stats)
    if len(tools) < 3:
        raise ValueError("leave-one-tool-out needs at least three tools")
    sd_by_left_out: dict[str, float] = {}
    tau2_by_left_out: dict[str, float] = {}
    for left_out in tools:
        remaining = [stats[tool] for tool in tools if tool != left_out]
        differences = [stat.mean_difference for stat in remaining]
        observed = sample_variance(differences)
        noise = sum(
            stat.sigma2_within / stat.n for stat in remaining
        ) / len(remaining)
        tau2 = max(0.0, observed - noise)
        sd_by_left_out[left_out] = math.sqrt(observed)
        tau2_by_left_out[left_out] = tau2
    spreads = list(sd_by_left_out.values())
    return {
        "per_left_out": {
            tool: {
                "loto_sd": sd_by_left_out[tool],
                "tau2": tau2_by_left_out[tool],
            }
            for tool in tools
        },
        "loto_sd_min": min(spreads),
        "loto_sd_median": sorted(spreads)[len(spreads) // 2],
        "loto_sd_max": max(spreads),
        "tau2_max": max(tau2_by_left_out.values()),
    }


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    resamples: int = 2000,
    seed: int = 42,
    confidence: float = 0.95,
) -> dict[str, float | int]:
    """Deterministic percentile bootstrap for a mean (sensitivity only)."""

    items = [float(value) for value in values]
    if not items:
        raise ValueError("bootstrap needs at least one value")
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(resamples):
        sample = [items[rng.randrange(len(items))] for _ in items]
        means.append(sum(sample) / len(sample))
    means.sort()
    alpha = (1.0 - confidence) / 2.0
    low = percentile(means, alpha)
    high = percentile(means, 1.0 - alpha)
    return {
        "mean": sum(items) / len(items),
        "low": low,
        "high": high,
        "resamples": resamples,
        "seed": seed,
    }


def bootstrap_primary_ci(
    pairs: Sequence[CasePair],
    *,
    resamples: int = 2000,
    seed: int = 42,
) -> dict[str, float | int]:
    return bootstrap_mean_ci(
        [pair.difference for pair in pairs],
        resamples=resamples,
        seed=seed,
    )


def bootstrap_cost_ratio_ci(
    pairs: Sequence[CasePair],
    *,
    resamples: int = 2000,
    seed: int = 42,
) -> dict[str, float | int]:
    """Case-level bootstrap of the pooled cost ratio (sensitivity only)."""

    if not pairs:
        raise ValueError("bootstrap needs at least one pair")
    rng = random.Random(seed)
    ratios: list[float] = []
    for _ in range(resamples):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        agent_total = sum(pair.agent_cost for pair in sample)
        baseline_total = sum(pair.baseline_cost for pair in sample)
        if baseline_total > 0:
            ratios.append(agent_total / baseline_total)
    ratios.sort()
    if not ratios:
        raise ValueError("no positive baseline cost in bootstrap samples")
    low = percentile(ratios, 0.025)
    high = percentile(ratios, 0.975)
    return {
        "ratio": aggregate_cost_ratio(pairs),
        "low": low,
        "high": high,
        "resamples": resamples,
        "seed": seed,
    }


def sizing_report(
    pairs: Sequence[CasePair],
    *,
    delta: float = 0.05,
    assumed_gap: float = 0.0,
    n_t_floor: int = 100,
    budget_ok: Mapping[int, bool] | None = None,
    excluded_tools: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Recompute m, u, s_pess, the power inequality, and the C proposal.

    §7 says to size to the pessimistic end of the leave-one-tool-out spread.
    The headline ``s_pess`` therefore uses the larger of the full-sample and
    LOTO-maximum between-tool variance. The full-sample figures are retained
    as a sensitivity comparison.
    """

    components = variance_components(pairs, excluded_tools=excluded_tools)
    u = components["u"]
    if u is None:
        raise ValueError("sizing needs at least two dev tools with >=10 cases")
    stats = _dev_stats(pairs, excluded_tools)
    per_tool_sigma2 = [stat.sigma2_within for stat in stats]
    sigma2_within = float(components["sigma2_within"])
    loto = loto_components(pairs, excluded_tools=excluded_tools)
    pessimistic_tau2 = max(float(components["tau2"]), float(loto["tau2_max"]))
    n_t = required_n_t(per_tool_sigma2, pessimistic_tau2, floor=n_t_floor)
    s_pess = s_pess_from_components(
        pessimistic_tau2,
        sigma2_within,
        n_t,
        u,
    )
    full_n_t = required_n_t(
        per_tool_sigma2,
        float(components["tau2"]),
        floor=n_t_floor,
    )
    full_s_pess = s_pess_from_components(
        float(components["tau2"]),
        sigma2_within,
        full_n_t,
        u,
    )
    power = {
        str(C): {
            "multiplier": power_multiplier(C),
            "passes": power_passes(
                s_pess,
                C,
                delta=delta,
                assumed_gap=assumed_gap,
            ),
            "max_s_pess": (delta + assumed_gap) / power_multiplier(C),
        }
        for C in (6, 7, 8)
    }
    return {
        "delta": delta,
        "assumed_gap": assumed_gap,
        "n_t_floor": n_t_floor,
        "components": components,
        "loto": loto,
        "pessimistic_tau2": pessimistic_tau2,
        "n_t": n_t,
        "s_pess": s_pess,
        "full_sample_n_t": full_n_t,
        "full_sample_s_pess": full_s_pess,
        "power": power,
        "proposed_C": select_c(
            s_pess,
            delta=delta,
            assumed_gap=assumed_gap,
            budget_ok=budget_ok,
        ),
        "proposed_C_full_sample": select_c(
            full_s_pess,
            delta=delta,
            assumed_gap=assumed_gap,
            budget_ok=budget_ok,
        ),
    }


def analyse_paired(
    agent_records: Sequence[Mapping[str, Any]],
    baseline_records: Sequence[Mapping[str, Any]],
    *,
    tool_by_case: Mapping[str, str] | None = None,
    label: str = "",
    resamples: int = 2000,
    seed: int = 42,
    delta: float = 0.05,
    assumed_gap: float = 0.0,
    budget_ok: Mapping[int, bool] | None = None,
    excluded_tools: Iterable[str] | None = None,
    include_sizing: bool = True,
) -> dict[str, Any]:
    """Full paired report for one comparison (primary or secondary)."""

    pairs = build_case_pairs(
        agent_records,
        baseline_records,
        tool_by_case=tool_by_case,
    )
    stats = per_tool_stats(pairs)
    interval = primary_interval(pairs)
    cost = cost_ratio_interval(pairs)
    metrics = {
        "label": label,
        "n_cases": len(pairs),
        "n_tools": len(stats),
        "discordant_rate": discordant_rate(pairs),
        "aggregate_cost_ratio": aggregate_cost_ratio(pairs),
        "cost_per_case": {
            "agent": sum(pair.agent_cost for pair in pairs) / len(pairs),
            "baseline": sum(pair.baseline_cost for pair in pairs) / len(pairs),
        },
        "tokens_per_case": {
            "agent_input": sum(pair.agent_input_tokens for pair in pairs) / len(pairs),
            "agent_output": sum(pair.agent_output_tokens for pair in pairs) / len(pairs),
            "baseline_input": sum(
                pair.baseline_input_tokens for pair in pairs
            ) / len(pairs),
            "baseline_output": sum(
                pair.baseline_output_tokens for pair in pairs
            ) / len(pairs),
            "agent_thinking": sum(
                pair.agent_thinking_tokens for pair in pairs
            ) / len(pairs),
            "baseline_thinking": sum(
                pair.baseline_thinking_tokens for pair in pairs
            ) / len(pairs),
        },
        "primary": interval.to_dict(),
        "primary_bootstrap": bootstrap_primary_ci(
            pairs, resamples=resamples, seed=seed
        ),
        "cost_ratio": cost,
        "cost_ratio_bootstrap": bootstrap_cost_ratio_ci(
            pairs, resamples=resamples, seed=seed
        ),
        "per_tool": {
            tool: stat.to_dict() for tool, stat in stats.items()
        },
    }
    if include_sizing:
        try:
            metrics["sizing"] = sizing_report(
                pairs,
                delta=delta,
                assumed_gap=assumed_gap,
                budget_ok=budget_ok,
                excluded_tools=excluded_tools,
            )
        except ValueError as exc:
            metrics["sizing"] = {"error": str(exc)}
    return metrics


def pilot_cost_gate(
    ratio: float,
    *,
    threshold: float = 0.10,
) -> dict[str, Any]:
    """Aggregate pilot cost-ratio gate from PROJECT_PLAN Phase 3."""

    return {
        "ratio": ratio,
        "threshold": threshold,
        "passes": ratio <= threshold,
        "definition": (
            "aggregate pilot list-price cost per case for haiku-agent divided "
            "by sonnet-direct; <= 0.10 clears the gate"
        ),
    }


def failure_mode_counts(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """Observed diagnostic events, not an exclusion list."""

    counts: dict[str, int] = {}
    for record in records:
        for kind in record.get("format_failure_kind") or []:
            key = str(kind)
            counts[key] = counts.get(key, 0) + 1
        reason = record.get("failure_reason")
        if isinstance(reason, str) and reason:
            key = f"failure_reason:{reason}"
            counts[key] = counts.get(key, 0) + 1
        status = str(record.get("status", "completed"))
        if status != "completed":
            key = f"status:{status}"
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))

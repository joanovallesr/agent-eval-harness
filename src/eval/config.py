"""Explicit Phase-2 configuration.

Nothing here is a pre-registration. The two model IDs are pinned as verified
for Phase 2 on 2026-10-31 and are recorded in every results file. The pinned
list-price file is ``config/pricing.list_sync.2026-10-31.json``; a live run
still requires ``--pricing-file`` explicitly.

Effort is recorded as provisional metadata. Phase 2 transmits **no** effort
parameter: all three arms record ``api_default``. Final effort selection is
Phase 3. ``request_params`` is the single place where a real, owner-pinned API
parameter can be added later.
"""

from __future__ import annotations

from typing import Mapping

from .metrics import PRICING_PER_MTOK, TokenPrice, price_for
from .metrics import PricingError

MATCH_SPEC_VERSION = "1.1"

# Starting points from docs/EVAL_DESIGN.md §2: "verify, then pin".
MODEL_IDS = {
    "haiku": "claude-haiku-5-5",
    "sonnet": "claude-sonnet-5-5",
}
MODEL_IDS_VERIFIED = True
MODEL_ID_STATUS = "verified 2026-10-31; list_sync pricing pinned in config/"

ARMS = ("haiku-agent", "haiku-direct", "sonnet-direct")
ARM_MODEL_KEYS = {
    "haiku-agent": "haiku",
    "haiku-direct": "haiku",
    "sonnet-direct": "sonnet",
}

# Provisional only. Final effort selection is Phase 3 (EVAL_DESIGN §2).
# Phase 2 transmits no effort parameter at all, so every arm records the same
# API-default marker. Do not claim a lower effort was used unless the harness
# actually sends it.
EFFORT_BY_ARM = {
    "haiku-agent": "api_default",
    "haiku-direct": "api_default",
    "sonnet-direct": "api_default",
}
EFFORT_PROVISIONAL = True
# Phase 2/3 default: no effort field is sent. A concrete effort is transmitted
# through the Anthropic Messages API's ``output_config.effort`` field (verified
# against anthropic SDK 2.3.0 type definitions:
# ``anthropic/types/output_config_param.py``). Valid values are low, medium,
# high, xhigh, and max. Never invent a top-level ``effort`` request field.
EFFORT_API_DEFAULT = "api_default"
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")
EFFORT_ORDER = VALID_EFFORTS
EFFORT_TOLERANCE = 0.02
EFFORT_FIELD = "output_config.effort"
# Default placeholder for a trace/agent built without an explicit effort.
# Agent instances overwrite it with the actual transmission state.
EFFORT_TRANSMITTED = False

# Frozen for Phase 2. Bump when the system or repair prompt changes.
PROMPT_VERSION = "phase2-mini-v1"

MAX_TOKENS = 1024
# Phase 2 has no pinned max_tokens value; this is provisional and recorded as
# such in run metadata. It must be pinned before a live run is analysed.
MAX_TOKENS_PROVISIONAL = True

# EVAL_DESIGN §3 and §4: "capped at 2 retries, then the last attempt stands."
# This is a retry cap, not a total-attempt cap: up to 3 attempts in total.
MAX_SCHEMA_REPAIR_RETRIES = 2
MAX_INFRASTRUCTURE_RETRIES = 2

# Nested retry policy (Phase 2):
# - infrastructure retries are per model request: initial + 2 retries = 3
#   HTTP requests for one model request;
# - schema-repair retries are per case: initial + 2 repair attempts = 3 model
#   requests for haiku-agent.
# Therefore the maximum HTTP requests per case/repeat are 3 * 3 = 9 for
# haiku-agent and 1 * 3 = 3 for a direct arm.
MAX_HTTP_REQUESTS_PER_CASE_AGENT = (
    1 + MAX_SCHEMA_REPAIR_RETRIES
) * (1 + MAX_INFRASTRUCTURE_RETRIES)
MAX_HTTP_REQUESTS_PER_CASE_DIRECT = 1 + MAX_INFRASTRUCTURE_RETRIES

MINI_SET_LABEL = "mini/smoke"
MINI_SET_DEV_VS_FINAL = "mini_smoke"
MINI_SET_PATH = "data/miniset.jsonl"


def model_id_for(arm: str) -> str:
    if arm not in ARM_MODEL_KEYS:
        raise KeyError(f"unknown arm {arm!r}")
    return MODEL_IDS[ARM_MODEL_KEYS[arm]]


def effort_for(arm: str) -> str:
    if arm not in EFFORT_BY_ARM:
        raise KeyError(f"unknown arm {arm!r}")
    return EFFORT_BY_ARM[arm]


def effort_request_params(effort: str) -> dict[str, dict[str, str]]:
    """Return the request fragment that transmits ``effort``.

    ``api_default`` means send nothing, so the provider's documented default is
    used. Any concrete effort is sent as ``output_config={"effort": ...}``.
    """

    if effort == EFFORT_API_DEFAULT:
        return {}
    if effort not in VALID_EFFORTS:
        raise ValueError(
            f"unknown effort {effort!r}; expected {EFFORT_API_DEFAULT!r} or one "
            f"of {', '.join(VALID_EFFORTS)}"
        )
    return {"output_config": {"effort": effort}}


def effort_is_transmitted(effort: str) -> bool:
    return effort != EFFORT_API_DEFAULT


def select_sonnet_effort(
    scores: Mapping[str, float],
    *,
    default_effort: str = EFFORT_API_DEFAULT,
) -> str:
    """EVAL_DESIGN §2: never weaken Sonnet.

    Pick the API default when it ties or beats every measured slice;
    otherwise pick the strongest measured slice. ``scores`` must include the
    default, because "whichever scores higher" is undefined otherwise.
    """

    if default_effort not in scores:
        raise ValueError(
            f"Sonnet effort selection needs a score for {default_effort!r}"
        )
    best = max(float(value) for value in scores.values())
    if float(scores[default_effort]) >= best:
        return default_effort
    for effort in EFFORT_ORDER:
        if effort in scores and float(scores[effort]) == best:
            return effort
    raise ValueError("no measured effort achieved the best score")


def select_shared_haiku_effort(
    arm_scores: Mapping[str, Mapping[str, float]],
    *,
    tolerance: float = EFFORT_TOLERANCE,
) -> str:
    """EVAL_DESIGN §2: lowest effort within tolerance of Haiku's best slice.

    Both Haiku arms must use the same effort. For each candidate effort we
    take the worst score across the Haiku arms (conservative), then choose the
    lowest effort whose worst score is within ``tolerance`` of the best worst
    score. The tolerance is pre-registered; it is not tuned after the fact.
    """

    if not arm_scores:
        raise ValueError("Haiku effort selection needs at least one arm")
    efforts = set.intersection(
        *(set(scores) for scores in arm_scores.values())
    )
    if not efforts:
        raise ValueError("Haiku arms have no common measured effort values")
    combined = {
        effort: min(float(scores[effort]) for scores in arm_scores.values())
        for effort in efforts
    }
    best = max(combined.values())
    eligible = [
        effort
        for effort in EFFORT_ORDER
        if effort in combined and combined[effort] >= best - tolerance
    ]
    if not eligible:
        raise ValueError("no Haiku effort is within tolerance")
    return eligible[0]


def pricing_for(
    model_id: str,
    pricing: dict[str, TokenPrice] | None = None,
) -> TokenPrice:
    """Resolve a price, preferring explicit configuration over the empty default."""

    return price_for(model_id, pricing if pricing is not None else PRICING_PER_MTOK)


__all__ = [
    "ARMS",
    "EFFORT_API_DEFAULT",
    "EFFORT_BY_ARM",
    "EFFORT_FIELD",
    "EFFORT_ORDER",
    "EFFORT_PROVISIONAL",
    "EFFORT_TOLERANCE",
    "EFFORT_TRANSMITTED",
    "VALID_EFFORTS",
    "MAX_INFRASTRUCTURE_RETRIES",
    "MAX_HTTP_REQUESTS_PER_CASE_AGENT",
    "MAX_HTTP_REQUESTS_PER_CASE_DIRECT",
    "MAX_SCHEMA_REPAIR_RETRIES",
    "MAX_TOKENS",
    "MAX_TOKENS_PROVISIONAL",
    "MATCH_SPEC_VERSION",
    "MINI_SET_DEV_VS_FINAL",
    "MINI_SET_LABEL",
    "MINI_SET_PATH",
    "MODEL_IDS",
    "MODEL_IDS_VERIFIED",
    "MODEL_ID_STATUS",
    "PROMPT_VERSION",
    "PricingError",
    "TokenPrice",
    "effort_for",
    "effort_is_transmitted",
    "effort_request_params",
    "model_id_for",
    "select_shared_haiku_effort",
    "select_sonnet_effort",
    "pricing_for",
]

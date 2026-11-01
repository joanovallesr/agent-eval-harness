"""LLM-as-judge for the Phase-5 multi-step traces.

The judge is GPT-6 Luna, a different model family from the trace models. It
labels one anonymized trace against the written rubric. It never sees the arm,
the model id, cost, tokens, or the human labels.

The judge prompt is versioned. Calibration uses one revision round at most,
driven only by tune-half disagreements, then freezes.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from eval.metrics import TokenPrice, calculate_cost

JUDGE_MODEL_ID = "gpt-6-luna"
JUDGE_PROMPT_VERSION = "phase5-judge-v1"
JUDGE_MAX_COMPLETION_TOKENS = 1000
JUDGE_MAX_PARSE_RETRIES = 2

Verdict = str
VALID_VERDICTS = ("correct", "partial", "wrong")


class JudgeError(RuntimeError):
    """The judge could not produce a usable verdict."""


@dataclass
class JudgeResult:
    trace_id: str
    label: str
    rationale: str
    prompt_version: str = JUDGE_PROMPT_VERSION
    model_id: str = JUDGE_MODEL_ID
    raw_response: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    parse_retries: int = 0
    request_count: int = 0
    status: str = "completed"
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SYSTEM_PROMPTS = {
    "phase5-judge-v1": """\
You are an independent evaluator labeling one agent trace against a written
rubric. You do not know which model or setting produced the trace. Use only the
task, the scripted mock results, the success criteria, and the trace provided.

Return exactly one JSON object and no other text:
{"label": "correct" | "partial" | "wrong", "rationale": "one or two sentences"}

Apply the rubric's decision order. Do not reward a plan that was not executed.
Do not penalize harmless extra calls. A correct action with a wrong reported
value is partial. If the trace stops before every required outcome is complete,
label it wrong unless the required outcomes were already complete.""",
}


def rubric_text(path: Path | str) -> str:
    return Path(path).read_text(encoding="utf-8")


def build_judge_messages(
    packet_trace: Mapping[str, Any],
    rubric: str,
    *,
    prompt_version: str = JUDGE_PROMPT_VERSION,
) -> list[dict[str, str]]:
    """Build the blind judge messages for one packet trace."""

    system = SYSTEM_PROMPTS.get(prompt_version)
    if system is None:
        raise KeyError(f"unknown judge prompt version {prompt_version!r}")
    task = {
        "query": packet_trace.get("query"),
        "offered_tools": packet_trace.get("offered_tools"),
        "success_criteria": packet_trace.get("success_criteria"),
        "scripted_mock_results": packet_trace.get("mock_results"),
    }
    trace = packet_trace.get("trace")
    user = (
        "# Rubric\n\n"
        f"{rubric}\n\n"
        "# Task\n\n"
        f"{json.dumps(task, indent=2, sort_keys=True)}\n\n"
        "# Trace\n\n"
        f"{json.dumps(trace, indent=2, sort_keys=True)}\n\n"
        "Return only the JSON object."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_verdict(text: str) -> tuple[str, str]:
    """Parse one strict JSON verdict, allowing a surrounding code fence."""

    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"judge response is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise JudgeError("judge response is not a JSON object")
    label = str(payload.get("label", "")).strip().lower()
    rationale = str(payload.get("rationale", "")).strip()
    if label not in VALID_VERDICTS:
        raise JudgeError(f"invalid judge label {label!r}")
    if not rationale:
        raise JudgeError("judge response has no rationale")
    return label, rationale


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, Mapping):
        return dict(usage)
    if hasattr(usage, "__dict__"):
        return dict(vars(usage))
    return {}


def _create_completion(
    client: Any,
    messages: list[dict[str, str]],
    *,
    model: str,
) -> Any:
    """Call the chat API, dropping unsupported optional parameters.

    GPT-6 Luna rejects ``temperature`` in some deployments. The judge should
    not fail for that reason; the fallback keeps the response format and the
    output cap.
    """

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": JUDGE_MAX_COMPLETION_TOKENS,
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    for _ in range(3):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            message = str(exc)
            removed = False
            for parameter in ("temperature", "response_format"):
                if parameter in message and (
                    "Unsupported parameter" in message
                    or "Unsupported value" in message
                    or "does not support" in message
                ):
                    kwargs.pop(parameter, None)
                    removed = True
                    break
            if not removed:
                raise
    raise JudgeError("judge API kept rejecting optional parameters")


def judge_trace(
    packet_trace: Mapping[str, Any],
    rubric: str,
    *,
    client: Any,
    price: TokenPrice,
    model: str = JUDGE_MODEL_ID,
    prompt_version: str = JUDGE_PROMPT_VERSION,
    max_parse_retries: int = JUDGE_MAX_PARSE_RETRIES,
    sleep: Any = None,
) -> JudgeResult:
    """Label one blind trace and record usage and cost."""

    sleep = sleep or time.sleep
    trace_id = str(packet_trace.get("trace_id", ""))
    messages = build_judge_messages(
        packet_trace,
        rubric,
        prompt_version=prompt_version,
    )
    result = JudgeResult(
        trace_id=trace_id,
        label="",
        rationale="",
        prompt_version=prompt_version,
        model_id=model,
    )
    parse_attempt = 0
    while True:
        started = time.perf_counter()
        try:
            response = _create_completion(client, messages, model=model)
        except Exception as exc:
            result.status = "error"
            result.error = f"{type(exc).__name__}: {exc}"
            raise JudgeError(result.error) from exc
        result.latency_s += time.perf_counter() - started
        result.request_count += 1
        usage = _usage_dict(getattr(response, "usage", None))
        input_tokens = int(usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(usage.get("completion_tokens", 0) or 0)
        result.input_tokens += input_tokens
        result.output_tokens += output_tokens
        result.cost_usd += calculate_cost(
            {"input_tokens": input_tokens, "output_tokens": output_tokens},
            price,
        )
        content = ""
        try:
            content = response.choices[0].message.content or ""
        except (AttributeError, IndexError) as exc:
            result.status = "error"
            result.error = f"unexpected judge response shape: {exc}"
            raise JudgeError(result.error) from exc
        result.raw_response = content
        try:
            label, rationale = parse_verdict(content)
        except JudgeError as exc:
            if parse_attempt >= max_parse_retries:
                result.status = "parse_failure"
                result.error = str(exc)
                return result
            parse_attempt += 1
            result.parse_retries = parse_attempt
            messages = messages + [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        "That response was not a valid verdict. Return only a "
                        'JSON object with "label" set to correct, partial, or '
                        'wrong and a one- or two-sentence "rationale".'
                    ),
                },
            ]
            sleep(0)
            continue
        result.label = label
        result.rationale = rationale
        result.status = "completed"
        return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    temporary.replace(path)


def _packet_traces(packet_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        str(row["trace_id"]): row
        for row in _read_jsonl(packet_dir / "traces.jsonl")
        if isinstance(row, dict) and row.get("trace_id")
    }


def _mapping_split(mapping_path: Path) -> dict[str, str]:
    payload = json.loads(mapping_path.read_text())
    rows = payload.get("mapping", payload)
    return {
        str(row["trace_id"]): str(row.get("split", ""))
        for row in rows
        if isinstance(row, dict) and row.get("trace_id")
    }


def run_judge(
    packet_dir: Path | str,
    mapping_path: Path | str,
    rubric_path: Path | str,
    out_path: Path | str,
    *,
    split: str,
    client: Any,
    price: TokenPrice,
    model: str = JUDGE_MODEL_ID,
    prompt_version: str = JUDGE_PROMPT_VERSION,
    cost_ceiling: float | None = None,
) -> dict[str, Any]:
    """Judge every packet trace in one split, resuming completed rows."""

    packet_dir = Path(packet_dir)
    out_path = Path(out_path)
    rubric = rubric_text(rubric_path)
    traces = _packet_traces(packet_dir)
    split_by_trace = _mapping_split(Path(mapping_path))
    selected = [
        trace_id
        for trace_id, trace_split in split_by_trace.items()
        if trace_split == split and trace_id in traces
    ]
    selected.sort()
    existing = _read_jsonl(out_path)
    done = {
        str(row["trace_id"])
        for row in existing
        if row.get("status") == "completed" and row.get("label")
    }
    total_cost = sum(float(row.get("cost_usd", 0.0) or 0.0) for row in existing)
    max_call_cost = max(
        (float(row.get("cost_usd", 0.0) or 0.0) for row in existing),
        default=0.0,
    )
    rows = list(existing)
    for trace_id in selected:
        if trace_id in done:
            continue
        if cost_ceiling is not None:
            if total_cost >= cost_ceiling or (
                max_call_cost > 0 and total_cost + max_call_cost >= cost_ceiling
            ):
                raise JudgeError(
                    f"judge cost ceiling reached before {trace_id}: "
                    f"${total_cost:.6f} >= ${cost_ceiling:.6f}"
                )
        result = judge_trace(
            traces[trace_id],
            rubric,
            client=client,
            price=price,
            model=model,
            prompt_version=prompt_version,
        )
        row = result.to_dict()
        row["split"] = split
        rows.append(row)
        _write_jsonl(out_path, rows)
        total_cost += result.cost_usd
        max_call_cost = max(max_call_cost, result.cost_usd)
        if result.status != "completed":
            raise JudgeError(
                f"judge did not produce a verdict for {trace_id}: "
                f"{result.error}"
            )
    completed = [
        row for row in rows if row.get("status") == "completed" and row.get("label")
    ]
    return {
        "split": split,
        "n_selected": len(selected),
        "n_completed": len(completed),
        "total_cost_usd": total_cost,
        "prompt_version": prompt_version,
        "model_id": model,
        "out_path": str(out_path),
    }


def _api_key_status() -> str:
    import os

    return "PRESENT" if os.environ.get("OPENAI_API_KEY") else "MISSING"


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5 judge runner")
    parser.add_argument("--packet-dir", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--rubric", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--split", choices=("tune", "report"), required=True)
    parser.add_argument("--model", default=JUDGE_MODEL_ID)
    parser.add_argument("--prompt-version", default=JUDGE_PROMPT_VERSION)
    parser.add_argument("--pricing-file", required=True)
    parser.add_argument("--cost-ceiling", type=float, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from eval.metrics import price_for

    if _api_key_status() != "PRESENT":
        parser.error("OPENAI_API_KEY is MISSING; refusing a judge run")
    if args.cost_ceiling <= 0:
        parser.error("--cost-ceiling must be positive")
    pricing = json.loads(Path(args.pricing_file).read_text())["models"]
    price = price_for(args.model, {args.model: TokenPrice.from_mapping(pricing[args.model])})
    plan = {
        "packet_dir": args.packet_dir,
        "split": args.split,
        "model": args.model,
        "prompt_version": args.prompt_version,
        "cost_ceiling": args.cost_ceiling,
        "openai_api_key": _api_key_status(),
        "out": args.out,
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return
    from openai import OpenAI

    try:
        result = run_judge(
            args.packet_dir,
            args.mapping,
            args.rubric,
            args.out,
            split=args.split,
            client=OpenAI(),
            price=price,
            model=args.model,
            prompt_version=args.prompt_version,
            cost_ceiling=args.cost_ceiling,
        )
    except JudgeError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        raise SystemExit(2) from exc
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

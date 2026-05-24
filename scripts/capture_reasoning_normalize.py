#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Capture before/after evidence for reasoning/content normalization.

Modes:
    --fixtures: replay committed golden provider payloads.
    --live: call the configured OpenAI-compatible provider repeatedly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from mcp_server_phytomni.api.openai_mapping import to_chat_completion
from mcp_server_phytomni.common.prompts import get_prompt
from mcp_server_phytomni.common.reasoning_content import (
    normalize_chat_completion_dict,
)
from mcp_server_phytomni.config.defaults import ChatConfig
from mcp_server_phytomni.config.settings import get_sensitive_config
from mcp_server_phytomni.mcp.result_formatting import (
    build_tool_result_envelope,
)

FIXTURE_DIR = Path("tests/fixtures/reasoning_content")
OUTPUT_DIR = Path("e2e/output")
PREVIEW_CHARS = 200
LIVE_REQUIRED_FLAGS = ("PHYTOMNI_RUN_INTEGRATION", "PHYTOMNI_ALLOW_NETWORK")


def main() -> int:
    """Run the requested capture mode and return a process exit code."""
    args = _parse_args()
    output_path = args.output or _default_output_path()
    if args.fixtures:
        records = _fixture_records()
    else:
        records = asyncio.run(_live_records(args.runs, args.prompt))
    _write_jsonl(output_path, records)
    repaired = sum(1 for record in records if record["repaired"])
    print(
        f"wrote {len(records)} records to {output_path} "
        f"(repair_count={repaired})"
    )
    return _validate_records(records)


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Capture reasoning/content normalization evidence."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--fixtures",
        action="store_true",
        help="Replay committed golden provider responses.",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Call the configured provider; requires network flags.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=30,
        help="Number of live provider calls to issue.",
    )
    parser.add_argument(
        "--prompt",
        default="Explain why leaves are green in one sentence.",
        help="Live-mode user prompt. It is not written to JSONL.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="JSONL output path. Defaults under e2e/output/.",
    )
    return parser.parse_args()


def _fixture_records() -> list[dict[str, Any]]:
    """Return JSONL records from committed fixture payloads."""
    records: list[dict[str, Any]] = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        fixture = _read_json(path)
        provider_raw = fixture["provider_raw"]
        record = _capture_record(
            mode="fixtures",
            run_id=str(fixture["fixture_id"]),
            provider_raw=provider_raw,
            expect_repair=bool(fixture["expect_repair"]),
        )
        records.append(record)
    if not records:
        raise SystemExit(f"no fixtures found in {FIXTURE_DIR}")
    return records


async def _live_records(runs: int, prompt: str) -> list[dict[str, Any]]:
    """Return JSONL records from repeated live provider calls."""
    _require_live_flags()
    if runs < 1:
        raise SystemExit("--runs must be >= 1")
    records: list[dict[str, Any]] = []
    for index in range(runs):
        provider_raw = await _call_provider(prompt)
        records.append(
            _capture_record(
                mode="live",
                run_id=str(index + 1),
                provider_raw=provider_raw,
                expect_repair=None,
            )
        )
    return records


async def _call_provider(prompt: str) -> dict[str, Any]:
    """Issue one direct non-cached provider call."""
    config = ChatConfig()
    sensitive = get_sensitive_config()
    client = AsyncOpenAI(
        api_key=sensitive.API_KEY.get_secret_value(),
        base_url=sensitive.BASE_URL,
    )
    params: dict[str, Any] = {
        "messages": [
            {
                "role": "system",
                "content": get_prompt(config.PROMPT_FILE, config.PROMPT_PATH),
            },
            {"role": "user", "content": prompt},
        ],
        "model": sensitive.MODEL_ID,
        "frequency_penalty": config.FREQUENCY_PENALTY,
        "n": config.N,
        "presence_penalty": config.PRESENCE_PENALTY,
        "response_format": dict(config.RESPONSE_FORMAT),
        "stream": False,
        "temperature": config.TEMPERATURE,
        "top_p": config.TOP_P,
        "user": config.USER,
        "timeout": config.TIMEOUT,
    }
    if config.MAX_TOKENS is not None:
        params["max_tokens"] = config.MAX_TOKENS
    if "reasoner" in sensitive.MODEL_ID and config.REASONING_EFFORT:
        params["reasoning_effort"] = config.REASONING_EFFORT
    completion = await client.chat.completions.create(**params)
    return completion.model_dump()


def _capture_record(
    *,
    mode: str,
    run_id: str,
    provider_raw: dict[str, Any],
    expect_repair: bool | None,
) -> dict[str, Any]:
    """Build one compact before/after evidence record."""
    normalized = normalize_chat_completion_dict(provider_raw)
    repaired = normalized is not provider_raw
    envelope = build_tool_result_envelope("ChatAgent", provider_raw)
    formatted = asdict(envelope.formatted)
    model = str(provider_raw.get("model") or "phyto-chat")
    api_response = to_chat_completion(formatted, envelope.raw, model)
    record = {
        "mode": mode,
        "run_id": run_id,
        "model": model,
        "expect_repair": expect_repair,
        "repaired": repaired,
        "provider_before": _payload_summary(provider_raw),
        "normalized_after": _payload_summary(normalized),
        "mcp_response": {
            "formatted_answer": _preview(formatted.get("answer")),
            "raw": _payload_summary(envelope.raw),
        },
        "api_response": _payload_summary(api_response),
    }
    record["repair_assertion"] = _repair_assertion(provider_raw, envelope.raw)
    return record


def _repair_assertion(before: Mapping[str, Any], after: Any) -> dict[str, Any]:
    """Return proof fields for one repair comparison."""
    before_message = _first_message(before)
    after_message = _first_message(after)
    tail = _expected_tail(before_message)
    after_content = str(after_message.get("content") or "")
    after_reasoning = str(after_message.get("reasoning_content") or "")
    return {
        "expected_tail": _preview(tail),
        "after_content_matches_tail": bool(tail and after_content == tail),
        "after_reasoning_excludes_tail": bool(
            tail and tail not in after_reasoning
        ),
    }


def _payload_summary(payload: Any) -> dict[str, Any]:
    """Return a secret-safe summary of one ChatCompletion-like payload."""
    message = _first_message(payload)
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    content_text = str(content or "")
    reasoning_text = str(reasoning or "")
    return {
        "content_len": len(content_text),
        "content_preview": _preview(content_text),
        "content_starts_think": content_text.lstrip().startswith("<think>"),
        "reasoning_len": len(reasoning_text),
        "reasoning_has_tail": _expected_tail(message) is not None,
        "reasoning_preview": _preview(reasoning_text),
        "usage": _safe_usage(payload),
    }


def _first_message(payload: Any) -> Mapping[str, Any]:
    """Return the first assistant message mapping, if present."""
    if not isinstance(payload, Mapping):
        return {}
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return {}
    first = choices[0]
    if not isinstance(first, Mapping):
        return {}
    message = first.get("message")
    return message if isinstance(message, Mapping) else {}


def _expected_tail(message: Mapping[str, Any]) -> str | None:
    """Return the answer tail if a known misplaced shape is present."""
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str):
        tail = _tail_after_think(reasoning)
        if tail:
            content = str(message.get("content") or "").strip()
            if not content or content == tail:
                return tail
    content_value = message.get("content")
    if isinstance(content_value, str):
        stripped = content_value.lstrip()
        if stripped.startswith("<think>"):
            return _tail_after_think(stripped)
    return None


def _tail_after_think(text: str) -> str | None:
    """Return non-empty text after the first closed ``</think>`` tag."""
    close = text.find("</think>")
    if close == -1:
        return None
    tail = text[close + len("</think>") :].strip()
    return tail or None


def _safe_usage(payload: Any) -> dict[str, Any]:
    """Return token usage values only, never auth or request data."""
    if not isinstance(payload, Mapping):
        return {}
    usage = payload.get("usage")
    return dict(usage) if isinstance(usage, Mapping) else {}


def _validate_records(records: list[dict[str, Any]]) -> int:
    """Return 1 when fixture expectations or repair invariants fail."""
    failures: list[str] = []
    for record in records:
        expect = record["expect_repair"]
        if expect is not None and bool(expect) != bool(record["repaired"]):
            failures.append(f"{record['run_id']}: repair expectation failed")
        if record["repaired"]:
            assertion = record["repair_assertion"]
            if not assertion["after_content_matches_tail"]:
                failures.append(f"{record['run_id']}: content mismatch")
            if not assertion["after_reasoning_excludes_tail"]:
                failures.append(f"{record['run_id']}: tail still in reasoning")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    return 0


def _require_live_flags() -> None:
    """Abort live mode unless explicit network flags are enabled."""
    enabled = [
        name for name in LIVE_REQUIRED_FLAGS if os.environ.get(name) == "1"
    ]
    if len(enabled) != len(LIVE_REQUIRED_FLAGS):
        required = ", ".join(LIVE_REQUIRED_FLAGS)
        raise SystemExit(f"--live requires {required}=1")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Write records to JSONL with deterministic UTF-8 encoding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    """Read one UTF-8 JSON object."""
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _default_output_path() -> Path:
    """Return the default timestamped evidence path."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return OUTPUT_DIR / f"reasoning_normalize_{stamp}.jsonl"


def _preview(value: Any) -> str:
    """Return a bounded single-line preview for JSONL evidence."""
    text = "" if value is None else str(value)
    return text.replace("\n", "\\n")[:PREVIEW_CHARS]


if __name__ == "__main__":
    raise SystemExit(main())

# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for reasoning normalization evidence capture summaries."""

import importlib.util
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "capture_reasoning_normalize.py"
)
SPEC = importlib.util.spec_from_file_location(
    "capture_reasoning_normalize", SCRIPT
)
assert SPEC is not None
assert SPEC.loader is not None
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)
payload_summary = getattr(capture, "_payload_summary")
capture_record = getattr(capture, "_capture_record")


def test_payload_summary_reports_reasoning_tail_and_duplicate_content():
    """Summaries expose bounded reasoning-tail evidence."""
    payload = {
        "choices": [
            {
                "message": {
                    "content": "Leaves reflect green light.",
                    "reasoning_content": (
                        "Identify chlorophyll absorption. "
                        "Leaves reflect green light."
                    ),
                }
            }
        ]
    }

    summary = payload_summary(payload)

    assert summary["content_in_reasoning"] is True
    assert summary["reasoning_tail_preview"].endswith(
        "Leaves reflect green light."
    )


def test_capture_record_reports_tagged_tail_preview():
    """Records expose the exact bounded tail that triggered repair."""
    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "fixture-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": (
                        "<think>Identify chlorophyll.</think>"
                        "Leaves reflect green light."
                    ),
                },
                "finish_reason": "stop",
            }
        ],
    }

    record = capture_record(
        mode="fixtures",
        run_id="tagged-tail",
        provider_raw=payload,
        expect_repair=True,
    )

    assert record["repaired"] is True
    assert (
        record["provider_before"]["tagged_tail_preview"]
        == "Leaves reflect green light."
    )
    assert (
        record["normalized_after"]["content_preview"]
        == "Leaves reflect green light."
    )


def test_capture_record_reports_all_corrected_after_surfaces():
    """Repair assertions prove normalized, MCP, and API fields agree."""
    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "fixture-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": (
                        "<think>Identify chlorophyll.</think>"
                        "Leaves reflect green light."
                    ),
                },
                "finish_reason": "stop",
            }
        ],
    }

    record = capture_record(
        mode="fixtures",
        run_id="tagged-tail",
        provider_raw=payload,
        expect_repair=True,
    )
    assertion = record["repair_assertion"]

    assert assertion["before_tagged_tail_present"] is True
    assert assertion["before_content_blank_or_duplicate"] is True
    assert assertion["normalized_corrected"] is True
    assert assertion["mcp_raw_corrected"] is True
    assert assertion["api_corrected"] is True
    assert assertion["same_sample_real_anomaly_fixed"] is True


def test_capture_record_repairs_orphan_close_reasoning_tail():
    """Orphan close-only reasoning is repaired by the normalizer."""
    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "fixture-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": (
                        "Identify chlorophyll.</think>"
                        "Leaves reflect green light."
                    ),
                },
                "finish_reason": "stop",
            }
        ],
    }

    record = capture_record(
        mode="fixtures",
        run_id="close-only",
        provider_raw=payload,
        expect_repair=True,
    )

    assert record["repaired"] is True
    assert record["provider_before"]["reasoning_has_tail"] is True
    assert (
        record["provider_before"]["tagged_tail_preview"]
        == "Leaves reflect green light."
    )
    assert record["repair_assertion"]["before_tagged_tail_present"] is True
    assert record["repair_assertion"]["same_sample_real_anomaly_fixed"] is True


def test_capture_record_does_not_repair_multiple_orphan_close_tags():
    """Multiple close tags without open tags remain no-op."""
    multi_close = (
        "Identify</think>chlorophyll.</think>Leaves reflect green light."
    )
    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "fixture-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": multi_close,
                },
                "finish_reason": "stop",
            }
        ],
    }

    record = capture_record(
        mode="fixtures",
        run_id="multi-close",
        provider_raw=payload,
        expect_repair=False,
    )

    assert record["repaired"] is False
    assert record["provider_before"]["reasoning_has_tail"] is False
    assert record["repair_assertion"]["before_tagged_tail_present"] is False
    assert (
        record["repair_assertion"]["same_sample_real_anomaly_fixed"] is False
    )

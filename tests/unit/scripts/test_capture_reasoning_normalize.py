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

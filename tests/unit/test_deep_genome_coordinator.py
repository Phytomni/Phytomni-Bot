# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contract tests for DeepGenome submissions and coordinator polling."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from mcp_server_phytomni.agents.deep_genome.coordinator import (
    RemoteSubmission,
    SubmissionProtocolError,
    WorkItemOutcome,
    normalize_submission,
    poll_work_item,
)

pytestmark = pytest.mark.unit


def _submission() -> RemoteSubmission:
    """Return a normalized submission with a deduplicated poll id."""
    return RemoteSubmission(
        submitted_task_id="caller-1",
        poll_task_id="source-1",
        output_dir="obs://bucket/out",
    )


async def _run_poll_fixture(
    statuses: Sequence[str],
    *,
    summary: str | None = "# usable result",
    clock: Sequence[float] | None = None,
) -> tuple[
    WorkItemOutcome, list[tuple[str, str | None, str | None]], list[float]
]:
    """Run the coordinator against deterministic status/clock fakes."""
    status_values = iter(statuses)
    clock_values = iter(clock if clock is not None else [0.0])
    transitions: list[tuple[str, str | None, str | None]] = []
    sleeps: list[float] = []

    def monotonic() -> float:
        """Return the next deterministic monotonic timestamp."""
        try:
            return next(clock_values)
        except StopIteration:
            return 0.0

    async def status_reader(task_id: str, timeout: float) -> dict[str, str]:
        """Return one remote status while asserting the effective id."""
        assert task_id == "source-1"
        assert timeout == 4.0
        return {"status": next(status_values)}

    async def result_resolver(submission: RemoteSubmission) -> str | None:
        """Return the fixture's local Markdown summary."""
        assert submission == _submission()
        return summary

    async def transition_sink(
        status: str,
        result: str | None,
        failure_reason: str | None,
    ) -> WorkItemOutcome:
        """Capture local transitions and return the settled outcome."""
        transitions.append((status, result, failure_reason))
        return WorkItemOutcome(status, result, failure_reason)

    async def sleep(delay: float) -> None:
        """Capture polling delays without waiting in real time."""
        sleeps.append(delay)

    outcome = await poll_work_item(
        _submission(),
        status_reader=status_reader,
        result_resolver=result_resolver,
        transition_sink=transition_sink,
        request_timeout=4.0,
        poll_interval=2.0,
        deadline_seconds=10.0,
        monotonic=monotonic,
        sleep=sleep,
    )
    return outcome, transitions, sleeps


def test_normalize_submission_preserves_dedup_source_identity() -> None:
    """Keep caller and effective remote polling ids separate."""
    normalized = normalize_submission(
        {
            "task_id": "caller-2",
            "source_task_id": "source-1",
            "output_dir": "obs://bucket/out",
        }
    )
    assert normalized.submitted_task_id == "caller-2"
    assert normalized.poll_task_id == "source-1"
    assert normalized.output_dir == "obs://bucket/out"


@pytest.mark.parametrize("field", ["task_id", "output_dir"])
def test_normalize_submission_rejects_missing_required_field(
    field: str,
) -> None:
    """Reject incomplete platform acknowledgements without echoing payloads."""
    payload = {"task_id": "task-1", "output_dir": "obs://bucket/out"}
    payload.pop(field)
    with pytest.raises(
        SubmissionProtocolError,
        match="invalid analysis submission",
    ):
        normalize_submission(payload)


@pytest.mark.parametrize(
    ("remote", "local"),
    [
        ("PENDING", "pending"),
        ("RUNNING", "running"),
        ("FAILED", "failed"),
        ("CANCELLED", "cancelled"),
    ],
)
async def test_poll_work_item_maps_remote_states(
    remote: str,
    local: str,
) -> None:
    """Map remote states and keep non-terminal transitions before polling."""
    statuses = (
        [remote]
        if local in {"failed", "cancelled"}
        else [
            remote,
            "FAILED",
        ]
    )
    outcome, transitions, sleeps = await _run_poll_fixture(statuses)

    if local in {"failed", "cancelled"}:
        assert outcome.status == local
        assert transitions[0][0] == local
        assert transitions[0][2] is not None
        assert sleeps == []
    else:
        assert outcome.status == "failed"
        assert sleeps == [2.0]
        assert transitions[-1][0] == "failed"


async def test_poll_work_item_uses_monotonic_deadline() -> None:
    """Stop at the monotonic deadline instead of a fixed poll count."""
    outcome, transitions, sleeps = await _run_poll_fixture(
        ["PENDING", "PENDING"],
        clock=[0.0, 9.0, 10.0],
    )

    assert outcome == WorkItemOutcome(
        "timed_out", None, "analysis task timed out"
    )
    assert [transition[0] for transition in transitions] == [
        "pending",
        "timed_out",
    ]
    assert sleeps == [2.0]


async def test_remote_success_requires_nonblank_summary() -> None:
    """Do not classify an empty remote result as locally usable."""
    outcome, transitions, sleeps = await _run_poll_fixture(
        ["SUCCEEDED"],
        summary="   \n\t",
    )

    assert outcome == WorkItemOutcome(
        "failed", None, "analysis result unusable"
    )
    assert transitions == [
        ("failed", None, "analysis result unusable"),
    ]
    assert sleeps == []


async def test_remote_success_strips_summary_and_does_not_sleep() -> None:
    """Return trimmed Markdown and stop immediately at terminal success."""
    outcome, transitions, sleeps = await _run_poll_fixture(
        ["SUCCEEDED"],
        summary="  # usable result  ",
    )

    assert outcome == WorkItemOutcome("succeeded", "# usable result", None)
    assert transitions == [("succeeded", "# usable result", None)]
    assert sleeps == []


async def test_unknown_remote_state_is_sanitized() -> None:
    """Unknown upstream state becomes a fixed local failure reason."""
    outcome, transitions, sleeps = await _run_poll_fixture(
        ["UNKNOWN secret response body"],
    )

    assert outcome.status == "failed"
    assert outcome.failure_reason == (
        "analysis task returned an unknown status"
    )
    assert "secret response body" not in (outcome.failure_reason or "")
    assert transitions == [
        ("failed", None, "analysis task returned an unknown status"),
    ]
    assert sleeps == []


async def test_result_resolver_error_is_sanitized() -> None:
    """Resolver/download errors settle one item without leaking details."""

    async def resolver(_submission: RemoteSubmission) -> str:
        """Raise an upstream-shaped error for the sanitizer assertion."""
        raise ValueError("https://upstream.invalid/token=secret")

    transitions: list[tuple[str, str | None, str | None]] = []

    async def sink(
        status: str,
        summary: str | None,
        reason: str | None,
    ) -> WorkItemOutcome:
        """Record the fixed local failure reason."""
        transitions.append((status, summary, reason))
        return WorkItemOutcome(status, summary, reason)

    async def status_reader(_task_id: str, _timeout: float) -> dict[str, str]:
        """Return a remote success acknowledgement."""
        return {"status": "SUCCEEDED"}

    async def no_sleep(_delay: float) -> None:
        """No terminal poll should sleep."""

    outcome = await poll_work_item(
        _submission(),
        status_reader=status_reader,
        result_resolver=resolver,
        transition_sink=sink,
        request_timeout=4.0,
        poll_interval=2.0,
        deadline_seconds=10.0,
        monotonic=lambda: 0.0,
        sleep=no_sleep,
    )

    assert outcome == WorkItemOutcome(
        "failed", None, "analysis result resolution failed"
    )
    assert transitions == [
        ("failed", None, "analysis result resolution failed"),
    ]


async def test_status_reader_error_is_sanitized() -> None:
    """Status lookup errors settle one optional item with fixed text."""

    async def status_reader(_task_id: str, _timeout: float) -> dict[str, str]:
        """Raise an upstream-shaped error for the sanitizer assertion."""
        raise RuntimeError("upstream body with credential=secret")

    async def resolver(_submission: RemoteSubmission) -> str:
        """Never resolve a result after status lookup failure."""
        raise AssertionError("resolver must not run")

    async def sink(
        status: str,
        summary: str | None,
        reason: str | None,
    ) -> WorkItemOutcome:
        """Return the outcome emitted by the coordinator."""
        return WorkItemOutcome(status, summary, reason)

    outcome = await poll_work_item(
        _submission(),
        status_reader=status_reader,
        result_resolver=resolver,
        transition_sink=sink,
        request_timeout=4.0,
        poll_interval=2.0,
        deadline_seconds=10.0,
        monotonic=lambda: 0.0,
        sleep=asyncio.sleep,
    )

    assert outcome.status == "failed"
    assert outcome.failure_reason == "analysis status lookup failed"
    assert "credential" not in (outcome.failure_reason or "")


async def test_cancelled_error_propagates_from_status_reader() -> None:
    """Parent cancellation is not converted to an optional branch failure."""

    async def status_reader(_task_id: str, _timeout: float) -> dict[str, str]:
        """Propagate cancellation from the remote status request."""
        raise asyncio.CancelledError

    async def resolver(_submission: RemoteSubmission) -> str:
        """Never resolve a cancelled item."""
        raise AssertionError("resolver must not run")

    async def sink(
        _status: str,
        _summary: str | None,
        _reason: str | None,
    ) -> WorkItemOutcome:
        """Never settle cancellation as an ordinary outcome."""
        raise AssertionError("transition sink must not receive cancellation")

    with pytest.raises(asyncio.CancelledError):
        await poll_work_item(
            _submission(),
            status_reader=status_reader,
            result_resolver=resolver,
            transition_sink=sink,
            request_timeout=4.0,
            poll_interval=2.0,
            deadline_seconds=10.0,
            monotonic=lambda: 0.0,
            sleep=asyncio.sleep,
        )

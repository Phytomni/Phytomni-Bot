# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit coverage for bounded managed-dataset description completion."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from mcp_server_phytomni.agents.shared import dataset_description
from mcp_server_phytomni.agents.shared.dataset_description import (
    complete_dataset_descriptions,
)
from mcp_server_phytomni.runtime.attachment_assets import ResolvedAsset

pytestmark = pytest.mark.unit


def _dataset(index: int = 1) -> ResolvedAsset:
    """Build one metadata-only dataset fixture with private sentinels."""
    return ResolvedAsset(
        asset_id=f"asset-id-private-{index}",
        reference=f"obs://internal-reference-private-{index}",
        filename=f"dataset-{index}.csv",
        content_type="text/csv",
        size_bytes=index * 101,
        purpose="dataset",
    )


def _response(items: list[Any]) -> dict[str, Any]:
    """Return one OpenAI-style structured completion payload."""
    return {
        "choices": [{"message": {"content": json.dumps({"items": items})}}]
    }


def _install_provider(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, Any] | None,
) -> dict[str, Any]:
    """Patch the provider and capture its rendered prompt and kwargs."""
    captured: dict[str, Any] = {}

    async def _provider(*, user_query: str, **kwargs: Any) -> dict[str, Any]:
        captured["calls"] = captured.get("calls", 0) + 1
        captured["user_query"] = user_query
        captured["kwargs"] = kwargs
        return response or {}

    monkeypatch.setattr(dataset_description, "phyto_chat", _provider)
    return captured


@pytest.mark.asyncio
async def test_supplied_description_is_copied_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nonblank user value wins and remains byte-for-byte unchanged."""
    supplied = "  User supplied role\n"

    async def _must_not_call(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("provider must not run for supplied input")

    monkeypatch.setattr(dataset_description, "phyto_chat", _must_not_call)

    result = await complete_dataset_descriptions(
        query="query-private",
        datasets=(_dataset(1), _dataset(2)),
        supplied_description=supplied,
    )

    assert result.descriptions == (supplied, supplied)
    assert result.source == "user"


@pytest.mark.asyncio
async def test_whitespace_supplied_description_enters_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only supplied text does not masquerade as a description."""
    _install_provider(
        monkeypatch,
        _response([{"label": "dataset_1", "description": "Generated"}]),
    )

    result = await complete_dataset_descriptions(
        query="query-private",
        datasets=(_dataset(),),
        supplied_description=" \t\n ",
    )

    assert result.descriptions == ("Generated",)
    assert result.source == "generated"


@pytest.mark.asyncio
async def test_one_dataset_uses_one_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One managed dataset completes through exactly one shared call."""
    captured = _install_provider(
        monkeypatch,
        _response([{"label": "dataset_1", "description": "Counts"}]),
    )

    result = await complete_dataset_descriptions(
        query="query-private",
        datasets=(_dataset(),),
        supplied_description=None,
    )

    assert result.descriptions == ("Counts",)
    assert result.source == "generated"
    assert captured["calls"] == 1


@pytest.mark.asyncio
async def test_ten_datasets_use_one_ordered_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fixed ten-item bound stays a single ordered completion call."""
    datasets = tuple(_dataset(index) for index in range(1, 11))
    captured = _install_provider(
        monkeypatch,
        _response(
            [
                {"label": f"dataset_{index}", "description": f"Role {index}"}
                for index in range(1, 11)
            ]
        ),
    )

    result = await complete_dataset_descriptions(
        query="query-private",
        datasets=datasets,
        supplied_description=None,
    )

    assert result.descriptions == tuple(
        f"Role {index}" for index in range(1, 11)
    )
    assert result.source == "generated"
    assert captured["calls"] == 1


@pytest.mark.asyncio
async def test_provider_request_is_single_shot_and_schema_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider options lock the completion's one-call structured contract."""
    captured = _install_provider(
        monkeypatch,
        _response([{"label": "dataset_1", "description": "Counts"}]),
    )

    await complete_dataset_descriptions(
        query="query-private",
        datasets=(_dataset(),),
        supplied_description=None,
    )

    kwargs = captured["kwargs"]
    assert kwargs["stream"] is False
    assert kwargs["n"] == 1
    assert kwargs["max_retries"] == 0
    assert kwargs["timeout"] == 15.0
    schema = kwargs["response_format"]["json_schema"]
    assert schema["name"] == "DatasetDescriptionCompletion"
    assert schema["schema"]["required"] == ["items"]
    assert schema["schema"]["properties"]["items"]["maxItems"] == 10
    item = schema["schema"]["properties"]["items"]["items"]
    assert item["required"] == ["label", "description"]


@pytest.mark.asyncio
async def test_prompt_carries_only_bounded_dataset_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt has query plus approved display metadata, never internals."""
    captured = _install_provider(
        monkeypatch,
        _response([{"label": "dataset_1", "description": "Counts"}]),
    )
    query = "canonical query sentinel"
    dataset = _dataset()

    await complete_dataset_descriptions(
        query=query,
        datasets=(dataset,),
        supplied_description=None,
    )

    prompt = captured["user_query"]
    for expected in (
        query,
        "dataset_1",
        dataset.filename,
        "101",
        "csv",
        "text/csv",
    ):
        assert expected in prompt
    for private in (
        dataset.asset_id,
        "owner-private-sentinel",
        dataset.reference,
        "object-key-private-sentinel",
        "capability-private-sentinel",
        "document-content-private-sentinel",
        "file-bytes-private-sentinel",
    ):
        assert private not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("items", "expected"),
    [
        (
            [{"label": "dataset_1", "description": "First"}],
            ("First", ""),
        ),
        (
            [
                {"label": "dataset_1", "description": "First"},
                {"label": "dataset_99", "description": "Unknown"},
            ],
            ("First", ""),
        ),
        (
            [
                {"label": "dataset_1", "description": "First"},
                {"label": "dataset_1", "description": "Duplicate"},
                {"label": "dataset_2", "description": "Second"},
            ],
            ("", "Second"),
        ),
        (
            [
                {"label": "dataset_1", "description": 4},
                {"label": "dataset_2", "description": "Second"},
            ],
            ("", "Second"),
        ),
        (
            [
                {"label": "dataset_1", "description": "  "},
                {"label": "dataset_2", "description": "Second"},
            ],
            ("", "Second"),
        ),
        (
            [
                {"label": "dataset_1", "description": "bad\x00value"},
                {"label": "dataset_2", "description": "Second"},
            ],
            ("", "Second"),
        ),
        (
            [
                {"label": "dataset_1", "description": "x" * 4001},
                {"label": "dataset_2", "description": "Second"},
            ],
            ("", "Second"),
        ),
        (
            [
                {"label": "dataset_1", "description": "x" * 4000},
                {"label": "dataset_2", "description": "Second"},
            ],
            ("x" * 4000, "Second"),
        ),
        ({"not": "a list"}, ("", "")),
        (
            ["not an object", {"label": "dataset_2", "description": "Second"}],
            ("", "Second"),
        ),
    ],
)
async def test_structured_output_is_normalized_per_label(
    monkeypatch: pytest.MonkeyPatch,
    items: Any,
    expected: tuple[str, str],
) -> None:
    """Malformed labels fail locally while valid ordered descriptions stay."""
    captured_response = {
        "choices": [{"message": {"content": json.dumps({"items": items})}}]
    }

    async def _provider(**_kwargs: Any) -> dict[str, Any]:
        return captured_response

    monkeypatch.setattr(dataset_description, "phyto_chat", _provider)
    result = await complete_dataset_descriptions(
        query="query-private",
        datasets=(_dataset(1), _dataset(2)),
        supplied_description=None,
    )

    assert result.descriptions == expected
    assert result.source == ("generated" if any(expected) else "empty")


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ("", "not-json-output-private"))
async def test_empty_or_unparseable_message_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    """Missing structured output remains nonterminal and empty."""
    _install_provider(
        monkeypatch,
        {"choices": [{"message": {"content": content}}]},
    )

    result = await complete_dataset_descriptions(
        query="query-private",
        datasets=(_dataset(),),
        supplied_description=None,
    )

    assert result.descriptions == ("",)
    assert result.source == "empty"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_name, code",
    [
        ("failing", "provider_failure"),
        ("sleeping", "timeout"),
        ("malformed", "malformed_output"),
    ],
)
async def test_nonterminal_failures_are_redacted_and_empty(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    provider_name: str,
    code: str,
) -> None:
    """Provider and parser failures are logged only as stable safe metadata."""
    provider = {
        "failing": _failing_provider,
        "sleeping": _sleeping_provider,
        "malformed": _malformed_provider,
    }[provider_name]
    monkeypatch.setattr(dataset_description, "phyto_chat", provider)
    if code == "timeout":
        monkeypatch.setattr(
            dataset_description, "_RESOLVER_TIMEOUT_SECONDS", 0.01
        )
    logger = logging.getLogger(dataset_description.__name__)
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(
            logging.WARNING, logger=dataset_description.__name__
        ):
            result = await complete_dataset_descriptions(
                query="query-private",
                datasets=(_dataset(),),
                supplied_description=None,
            )
    finally:
        logger.removeHandler(caplog.handler)

    assert result.descriptions == ("",)
    assert result.source == "empty"
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert code in messages
    for private in (
        "query-private",
        "dataset-1.csv",
        "internal-reference-private-1",
        "provider-body-private",
        "raw-model-output-private",
    ):
        assert private not in messages


@pytest.mark.asyncio
async def test_cancellation_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task cancellation remains visible to the shared resolver caller."""
    monkeypatch.setattr(dataset_description, "phyto_chat", _cancelled_provider)

    with pytest.raises(asyncio.CancelledError):
        await complete_dataset_descriptions(
            query="query-private",
            datasets=(_dataset(),),
            supplied_description=None,
        )


async def _failing_provider(**_kwargs: Any) -> dict[str, Any]:
    """Raise one ordinary provider failure without exposing it to callers."""
    raise RuntimeError("provider-body-private")


async def _sleeping_provider(**_kwargs: Any) -> dict[str, Any]:
    """Exceed the monkeypatched resolver timeout."""
    await asyncio.sleep(1)
    return _response([])


async def _malformed_provider(**_kwargs: Any) -> dict[str, Any]:
    """Return raw output that parser logs must not disclose."""
    return {"choices": [{"message": {"content": "raw-model-output-private"}}]}


async def _cancelled_provider(**_kwargs: Any) -> dict[str, Any]:
    """Simulate task cancellation from the provider transport."""
    raise asyncio.CancelledError

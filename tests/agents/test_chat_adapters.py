# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the shared consumer-agent-to-chat subgraph IO mappers.

Pins the 17-key bag the data / knowledge / analyst chat sites pass
to ``phyto_chat`` / ``CHAT_APP`` so the structural-mount wiring
projects the same arguments without drift. Covers the kwargs builder
(with and without the analyst-only ``response_format`` override), the
``ChatInput`` wrapper (with and without the data / knowledge
``obs_file_list``), and the ``ChatOutput.response`` unwrap.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.chat.state import ChatInput
from mcp_server_phytomni.graphs.chat_adapters import (
    build_chat_input,
    build_chat_kwargs_for,
    extract_chat_response,
)
from tests.support.config_fakes import fake_chat_config, fake_sensitive_config

pytestmark = pytest.mark.agent


def test_fake_chat_config_exposes_the_complete_chat_surface() -> None:
    """Shared config fixture exposes the canonical chat kwargs surface."""
    config = fake_chat_config(TEMPERATURE=0.7)

    assert config.TEMPERATURE == 0.7
    assert config.RETRIABLE_CODES == [429, 500, 502, 503, 504]
    assert config.TIMEOUT == 120.0


def test_fake_sensitive_config_exposes_secret_and_model_fields() -> None:
    """Shared sensitive fixture mirrors the fields consumed by adapters."""
    config = fake_sensitive_config()

    assert config.API_KEY.get_secret_value() == "sk-test"
    assert config.BASE_URL == "https://llm.example/v1"
    assert config.MODEL_ID == "phyto-llm-v1"


def test_build_chat_kwargs_for_packs_all_17_fields() -> None:
    """The returned dict has all 17 keys with the expected values.

    Pins the shared 17-key bag the data / knowledge / analyst chat
    sites pass. If the dispatch site ever adds or drops a kwarg, this
    test fails first so the adapter and the call sites stay aligned.
    """
    kwargs = build_chat_kwargs_for(
        config=fake_chat_config(),
        sensitive_config=fake_sensitive_config(),
    )

    assert kwargs == {
        "prompt_file": "prompt.yaml",
        "prompt_path": "/tmp/prompts",
        "model": "phyto-llm-v1",
        "frequency_penalty": 0.0,
        "n": 1,
        "presence_penalty": 0.0,
        "reasoning_effort": "medium",
        "response_format": {"type": "text"},
        "stream": False,
        "temperature": 0.2,
        "top_p": 0.9,
        "user": "consumer-user",
        "timeout": 120.0,
        "retriable_codes": [429, 500, 502, 503, 504],
        "max_retries": 3,
        "locale": "en-US",
        "locale_instruction": (
            "Write all natural-language prose in English. Preserve "
            "identifiers, "
            "sequences, numbers, citations, tool names, and structured keys "
            "exactly."
        ),
    }
    assert len(kwargs) == 17


def test_build_chat_kwargs_for_response_format_override() -> None:
    """The optional ``response_format`` kwarg shadows the config default.

    Pins the analyst-only override path: the five analyst chat sites
    pass per-site ``response_format`` dicts (``json_schema`` /
    ``json_object``) that must shadow ``config.RESPONSE_FORMAT`` in
    the returned bag; other keys stay at the config defaults.
    """
    schema_format = {"type": "json_schema", "json_schema": {"name": "x"}}
    kwargs = build_chat_kwargs_for(
        config=fake_chat_config(),
        sensitive_config=fake_sensitive_config(),
        response_format=schema_format,
    )

    assert kwargs["response_format"] == schema_format
    assert kwargs["model"] == "phyto-llm-v1"
    assert kwargs["temperature"] == 0.2


def test_build_chat_kwargs_for_default_inherits_config() -> None:
    """``response_format=None`` (the default) inherits ``RESPONSE_FORMAT``.

    Pins the data / knowledge path: those call sites never pass the
    override, so the bag must surface ``config.RESPONSE_FORMAT`` and
    never crash on a missing override key.
    """
    kwargs = build_chat_kwargs_for(
        config=fake_chat_config(),
        sensitive_config=fake_sensitive_config(),
        response_format=None,
    )

    assert kwargs["response_format"] == {"type": "text"}


def test_build_chat_input_minimum() -> None:
    """Without OBS files, ``ChatInput`` carries chat keys and locale.

    Pins that omitting ``obs_file_list`` keeps the key absent so the
    chat subgraph's ``prepare_context`` node skips the upload branch
    instead of materialising an empty list that would still trigger
    OBS lookups. This is the analyst path.
    """
    bag = {"model": "phyto-llm-v1"}
    result = build_chat_input(
        user_query="What is photosynthesis?",
        chat_kwargs=bag,
    )

    assert result == {
        "user_query": "What is photosynthesis?",
        "chat_kwargs": bag,
        "locale": "en-US",
    }
    assert "obs_file_list" not in result


def test_build_chat_input_with_obs_files() -> None:
    """A non-empty OBS list flows through as a copied list of strings.

    Pins the upload-context wiring: the chat subgraph reads
    ``obs_file_list`` to materialise upload context, so the adapter
    must forward the caller's list verbatim. The copy keeps a later
    consumer-node mutation from leaking into the subgraph's input.
    This is the data / knowledge path.
    """
    bag = {"model": "phyto-llm-v1"}
    obs_files = ["/obs/phytomni/doc1.pdf", "/obs/phytomni/doc2.pdf"]
    result = build_chat_input(
        user_query="Summarise these papers",
        chat_kwargs=bag,
        obs_file_list=obs_files,
    )

    assert result.get("user_query") == "Summarise these papers"
    assert result.get("chat_kwargs") == bag
    assert result.get("obs_file_list") == obs_files
    assert result.get("obs_file_list") is not obs_files


def test_build_chat_input_drops_empty_obs_list() -> None:
    """Empty list and ``None`` both omit the ``obs_file_list`` key.

    Pins the no-files branch: both the explicit empty list and the
    default ``None`` collapse to a ``ChatInput`` without the key, so
    the chat subgraph's ``prepare_context`` reads "no uploads"
    consistently and never iterates an empty list down to OBS.
    """
    bag = {"model": "phyto-llm-v1"}

    none_result = build_chat_input(
        user_query="Q1",
        chat_kwargs=bag,
        obs_file_list=None,
    )
    empty_result = build_chat_input(
        user_query="Q2",
        chat_kwargs=bag,
        obs_file_list=[],
    )

    assert "obs_file_list" not in none_result
    assert "obs_file_list" not in empty_result


def test_build_chat_input_satisfies_chat_input_schema() -> None:
    """The returned dict's key set is a subset of ``ChatInput`` fields.

    Pins that the wrapper never leaks an extra key into the chat
    subgraph's input schema; if it did, the parent graph wiring
    would fail validation when ``input_schema=ChatInput`` is enforced.
    """
    result = build_chat_input(
        user_query="Q",
        chat_kwargs={"model": "phyto-llm-v1"},
        obs_file_list=["/obs/phytomni/doc.pdf"],
    )
    chat_input_keys = getattr(ChatInput, "__required_keys__") | getattr(
        ChatInput, "__optional_keys__"
    )
    assert set(result.keys()) <= chat_input_keys


def test_extract_chat_response_extracts_response_dict() -> None:
    """A populated ``response`` field is returned verbatim.

    Pins the unwrap so consumer nodes receive the same raw upstream
    chat-completion dict they get today from ``phyto_chat(...)``
    before they patch downstream state onto ``choices[0].message``.
    """
    upstream = {
        "choices": [{"message": {"content": "Hello"}}],
        "usage": {"total_tokens": 42},
    }
    result = extract_chat_response({"response": upstream})

    assert result is upstream


def test_extract_chat_response_handles_none_gracefully() -> None:
    """``response=None`` collapses to ``{}`` instead of propagating None.

    Pins the safe-default: consumer nodes' downstream code indexes
    ``phyto_response.get("choices", ...)`` and the fallback branches
    expect a dict, so returning ``None`` here would crash the
    patcher. The empty dict keeps the patcher's "no choices yet"
    branches intact.
    """
    assert extract_chat_response({"response": None}) == {}

# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the KnowledgeAgent-to-chat subgraph IO mappers.

Pins the kwarg bag that ``knowledge/agent.py`` currently passes to
``phyto_chat`` at both the generate and follow-up call sites so the
upcoming wiring through ``adapter_node`` projects the same arguments
without behaviour drift. The two sites pass an identical 17-key
LLM/retry/provider bag and differ only by ``user_query``; the
mappers below cover that bag, the ``ChatInput`` wrapper, and the
``ChatOutput.response`` unwrap.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_server_phytomni.agents.chat.state import ChatInput
from mcp_server_phytomni.graphs.knowledge_to_chat_adapters import (
    build_knowledge_chat_input,
    build_knowledge_chat_kwargs,
    extract_chat_response,
)

pytestmark = pytest.mark.agent


def _fake_knowledge_config() -> SimpleNamespace:
    """Build a SimpleNamespace stand-in for ``KnowledgeAgentConfig``.

    Only the fields the kwargs builder reads are populated; using
    SimpleNamespace keeps the test independent from Pydantic
    validation rules on the real config.
    """
    return SimpleNamespace(
        PROMPT_FILE="prompt.yaml",
        PROMPT_PATH="/tmp/prompts",
        FREQUENCY_PENALTY=0.0,
        N=1,
        PRESENCE_PENALTY=0.0,
        REASONING_EFFORT="medium",
        RESPONSE_FORMAT={"type": "text"},
        STREAM=False,
        TEMPERATURE=0.2,
        TOP_P=0.9,
        USER="kg-user",
        TIMEOUT=120.0,
        RETRIABLE_CODES=[429, 500, 502, 503, 504],
        MAX_RETRIES=3,
    )


def _fake_sensitive_config() -> SimpleNamespace:
    """Build a SimpleNamespace stand-in for ``SensitiveConfig``.

    ``API_KEY`` exposes ``get_secret_value`` to mirror the real
    Pydantic ``SecretStr`` surface used inside the knowledge agent.
    """
    return SimpleNamespace(
        API_KEY=SimpleNamespace(get_secret_value=lambda: "sk-test"),
        BASE_URL="https://llm.example/v1",
        MODEL_ID="phyto-llm-v1",
    )


def test_build_knowledge_chat_kwargs_packs_all_17_fields() -> None:
    """The returned dict has all 17 keys with the expected values.

    Pins the 17-key bag both ``generate_node`` and ``follow_up_node``
    pass to ``phyto_chat``. If the dispatch site ever adds or drops a
    kwarg, this test fails first so the adapter and the call sites
    stay aligned.
    """
    kwargs = build_knowledge_chat_kwargs(
        knowledge_config=_fake_knowledge_config(),
        sensitive_config=_fake_sensitive_config(),
    )

    assert kwargs == {
        "prompt_file": "prompt.yaml",
        "prompt_path": "/tmp/prompts",
        "api_key": "sk-test",
        "base_url": "https://llm.example/v1",
        "model": "phyto-llm-v1",
        "frequency_penalty": 0.0,
        "n": 1,
        "presence_penalty": 0.0,
        "reasoning_effort": "medium",
        "response_format": {"type": "text"},
        "stream": False,
        "temperature": 0.2,
        "top_p": 0.9,
        "user": "kg-user",
        "timeout": 120.0,
        "retriable_codes": [429, 500, 502, 503, 504],
        "max_retries": 3,
    }
    assert len(kwargs) == 17


def test_build_knowledge_chat_input_minimum() -> None:
    """Without an OBS file list, ``ChatInput`` carries only the two keys.

    Pins that omitting ``obs_file_list`` keeps the key absent so the
    chat subgraph's ``prepare_context`` node skips the upload branch
    instead of materialising an empty list that would still trigger
    OBS lookups.
    """
    bag = {"model": "phyto-llm-v1", "api_key": "sk-test"}
    result = build_knowledge_chat_input(
        user_query="What is photosynthesis?",
        chat_kwargs=bag,
    )

    assert result == {
        "user_query": "What is photosynthesis?",
        "chat_kwargs": bag,
    }
    assert "obs_file_list" not in result


def test_build_knowledge_chat_input_with_obs_files() -> None:
    """A non-empty OBS list flows through as a copied list of strings.

    Pins the upload-context wiring: the chat subgraph reads
    ``obs_file_list`` to materialise upload context, so the adapter
    must forward the caller's list verbatim. The copy keeps a later
    knowledge-node mutation from leaking into the subgraph's input.
    """
    bag = {"model": "phyto-llm-v1"}
    obs_files = ["/obs/phytomni/doc1.pdf", "/obs/phytomni/doc2.pdf"]
    result = build_knowledge_chat_input(
        user_query="Summarise these papers",
        chat_kwargs=bag,
        obs_file_list=obs_files,
    )

    assert result.get("user_query") == "Summarise these papers"
    assert result.get("chat_kwargs") == bag
    assert result.get("obs_file_list") == obs_files
    assert result.get("obs_file_list") is not obs_files


def test_build_knowledge_chat_input_drops_empty_obs_list() -> None:
    """Empty list and ``None`` both omit the ``obs_file_list`` key.

    Pins the no-files branch: both the explicit empty list and the
    default ``None`` collapse to a ``ChatInput`` without the key, so
    the chat subgraph's ``prepare_context`` reads "no uploads"
    consistently and never iterates an empty list down to OBS.
    """
    bag = {"model": "phyto-llm-v1"}

    none_result = build_knowledge_chat_input(
        user_query="Q1",
        chat_kwargs=bag,
        obs_file_list=None,
    )
    empty_result = build_knowledge_chat_input(
        user_query="Q2",
        chat_kwargs=bag,
        obs_file_list=[],
    )

    assert "obs_file_list" not in none_result
    assert "obs_file_list" not in empty_result


def test_build_knowledge_chat_input_satisfies_chat_input_schema() -> None:
    """The returned dict's key set is a subset of ``ChatInput`` fields.

    Pins that the wrapper never leaks an extra key into the chat
    subgraph's input schema; if it did, the parent graph wiring
    would fail validation when ``input_schema=ChatInput`` is enforced.
    """
    result = build_knowledge_chat_input(
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

    Pins the unwrap so the knowledge node receives the same raw
    upstream chat-completion dict it gets today from
    ``phyto_chat(...)`` before it patches ``doc_list_payload`` onto
    ``choices[0].message``.
    """
    upstream = {
        "choices": [{"message": {"content": "Hello"}}],
        "usage": {"total_tokens": 42},
    }
    result = extract_chat_response({"response": upstream})

    assert result is upstream


def test_extract_chat_response_handles_none_gracefully() -> None:
    """``response=None`` collapses to ``{}`` instead of propagating None.

    Pins the safe-default: the knowledge node's downstream code
    indexes ``phyto_response.get("choices", ...)`` and the fallback
    branches expect a dict, so returning ``None`` here would crash
    the patcher. The empty dict keeps the patcher's
    "no choices yet" branches intact.
    """
    assert extract_chat_response({"response": None}) == {}

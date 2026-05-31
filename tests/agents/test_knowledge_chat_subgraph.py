# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Dual-path tests for ``KnowledgeAgent`` chat invocations.

Pins the ``USE_CHAT_SUBGRAPH`` flag branch on the knowledge
``generate_node`` and ``follow_up_node``: flag-off keeps the legacy
direct ``phyto_chat(...)`` call, flag-on routes through
``_cached_chat_app().ainvoke(ChatInput)`` via the
``knowledge_to_chat_adapters`` projection helpers. Both branches emit
the same downstream response shapes.
"""

from __future__ import annotations

from typing import cast

import pytest

from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.agents.knowledge.state import KnowledgeState
from mcp_server_phytomni.config.defaults import KnowledgeConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

from ._subgraph_branch_fakes import install_chat_branch_mocks

pytestmark = pytest.mark.agent

_KNOWLEDGE_MODULE = "mcp_server_phytomni.agents.knowledge.agent"

_CHAT_COMPLETION_RESPONSE = {
    "choices": [{"message": {"content": "synthesised answer"}}],
    "usage": {"total_tokens": 42},
}


def _build_agent(use_subgraph: bool) -> KnowledgeAgent:
    """Construct a ``KnowledgeAgent`` with ``USE_CHAT_SUBGRAPH`` set.

    Uses ``model_copy`` to flip the flag on the inherited
    ``ServerConfig`` field without tripping pylint ``C0103`` on a
    direct UPPERCASE attribute assignment, mirroring the
    ``build_branch_agent`` shape used by the analyst-subgraph tests.
    """
    config = KnowledgeConfig().model_copy(
        update={"USE_CHAT_SUBGRAPH": use_subgraph}
    )
    return KnowledgeAgent(
        knowledge_config=config,
        sensitive_config=SensitiveConfig.load(),
    )


def _minimal_generate_state() -> KnowledgeState:
    """Return the minimal state ``generate_node`` reads.

    ``generate_node`` reads ``user_query`` / ``retrieve_context`` /
    ``upload_context`` (defaults to ``""`` when absent) and the
    ``retrieved_docs`` list it embeds into ``doc_list_payload``. No
    other keys are read at this node, so the dict stays minimal.
    """
    return cast(
        KnowledgeState,
        {
            "user_query": "What is photosynthesis?",
            "retrieve_context": "doc1 ... doc2 ...",
            "upload_context": "",
            "retrieved_docs": [{"title": "Plant Biology.pdf"}],
        },
    )


@pytest.mark.parametrize("use_subgraph", [False, True])
async def test_generate_node_respects_chat_subgraph_flag(
    use_subgraph: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off awaits ``phyto_chat``; flag-on awaits the chat subgraph.

    Both branches are exercised against the same minimal state with
    the same canned chat-completion response so the downstream
    ``doc_list_payload`` patcher writes the same ``main_response`` /
    ``final_response`` shape regardless of which branch ran. The
    chat-subgraph branch returns its response under the ``response``
    key (the ``ChatOutput`` contract) which ``extract_chat_response``
    unwraps; the legacy branch returns the raw chat-completion dict
    directly.
    """
    legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        module_path=_KNOWLEDGE_MODULE,
        legacy_response=_CHAT_COMPLETION_RESPONSE,
        subgraph_response={"response": _CHAT_COMPLETION_RESPONSE},
    )

    agent = _build_agent(use_subgraph=use_subgraph)
    result = await agent.generate_node(_minimal_generate_state())

    if use_subgraph:
        subgraph_app_mock.ainvoke.assert_awaited_once()
        legacy_mock.assert_not_awaited()
    else:
        legacy_mock.assert_awaited_once()
        subgraph_app_mock.ainvoke.assert_not_awaited()

    assert result["main_response"] is result["final_response"]
    message = result["main_response"]["choices"][0]["message"]
    assert message["content"] == "synthesised answer"
    assert message["doc_list"] == [{"title": "Plant Biology.pdf"}]
    assert message["total"] == 10000


async def test_generate_node_subgraph_branch_projects_chat_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on branch forwards a ``ChatInput`` carrying the stitched query.

    Pins the projection contract: the adapter must hand the subgraph
    a ``ChatInput`` whose ``user_query`` is the prompt-stitched query
    (not the raw user question) and whose ``chat_kwargs`` is the
    17-key provider bag. Empty ``obs_file_list`` collapses to absence
    so the chat subgraph's ``prepare_context`` reads "no uploads"
    instead of iterating an empty list down to OBS.
    """
    _, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        module_path=_KNOWLEDGE_MODULE,
        subgraph_response={"response": _CHAT_COMPLETION_RESPONSE},
    )

    agent = _build_agent(use_subgraph=True)
    await agent.generate_node(_minimal_generate_state())

    call_args = subgraph_app_mock.ainvoke.await_args
    assert call_args is not None
    chat_input = call_args.args[0]
    # The prompt-stitched query must carry the retrieve context, not
    # the raw user question; this guards against a future refactor
    # that bypasses ``get_prompt(...)``.
    assert "doc1" in chat_input["user_query"]
    assert "What is photosynthesis?" in chat_input["user_query"]
    assert isinstance(chat_input["chat_kwargs"], dict)
    assert len(chat_input["chat_kwargs"]) == 17
    assert "obs_file_list" not in chat_input


_FOLLOW_UP_CHAT_RESPONSE = {
    "choices": [
        {"message": {"content": '["next question one", "next question two"]'}}
    ],
}


def _minimal_follow_up_state() -> KnowledgeState:
    """Return the minimal state ``follow_up_node`` reads.

    ``follow_up_node`` reads ``user_query`` and ``main_response``; the
    latter must expose ``choices[0].message.content`` so
    ``message_content`` can extract the prior assistant turn and so
    the downstream ``update({"follow_up_questions": ...})`` patch
    finds a writable message dict.
    """
    return cast(
        KnowledgeState,
        {
            "user_query": "What is photosynthesis?",
            "main_response": {
                "choices": [{"message": {"content": "primary answer body"}}]
            },
        },
    )


@pytest.mark.parametrize("use_subgraph", [False, True])
async def test_follow_up_node_respects_chat_subgraph_flag(
    use_subgraph: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-off awaits ``phyto_chat``; flag-on awaits the chat subgraph.

    Both branches are exercised against the same minimal state with
    the same canned follow-up response so the parsed
    ``follow_up_questions`` list is identical regardless of which
    branch ran. The chat-subgraph branch returns its response under
    the ``response`` key (the ``ChatOutput`` contract) which
    ``extract_chat_response`` unwraps; the legacy branch returns the
    raw chat-completion dict directly.
    """
    legacy_mock, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        module_path=_KNOWLEDGE_MODULE,
        legacy_response=_FOLLOW_UP_CHAT_RESPONSE,
        subgraph_response={"response": _FOLLOW_UP_CHAT_RESPONSE},
    )

    agent = _build_agent(use_subgraph=use_subgraph)
    result = await agent.follow_up_node(_minimal_follow_up_state())

    if use_subgraph:
        subgraph_app_mock.ainvoke.assert_awaited_once()
        legacy_mock.assert_not_awaited()
    else:
        legacy_mock.assert_awaited_once()
        subgraph_app_mock.ainvoke.assert_not_awaited()

    assert result["follow_up_questions"] == [
        "next question one",
        "next question two",
    ]
    message = result["final_response"]["choices"][0]["message"]
    assert message["follow_up_questions"] == [
        "next question one",
        "next question two",
    ]


async def test_follow_up_node_subgraph_branch_projects_chat_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-on branch forwards a ``ChatInput`` carrying the stitched query.

    Pins the projection contract for follow-up: the adapter must hand
    the subgraph a ``ChatInput`` whose ``user_query`` is the
    prompt-stitched query built from the ``system/follow_up_questions``
    template (carrying both the user question and the prior assistant
    answer), and whose ``chat_kwargs`` is the 17-key provider bag.
    """
    _, subgraph_app_mock = install_chat_branch_mocks(
        monkeypatch,
        module_path=_KNOWLEDGE_MODULE,
        subgraph_response={"response": _FOLLOW_UP_CHAT_RESPONSE},
    )

    agent = _build_agent(use_subgraph=True)
    await agent.follow_up_node(_minimal_follow_up_state())

    call_args = subgraph_app_mock.ainvoke.await_args
    assert call_args is not None
    chat_input = call_args.args[0]
    assert "What is photosynthesis?" in chat_input["user_query"]
    assert "primary answer body" in chat_input["user_query"]
    assert isinstance(chat_input["chat_kwargs"], dict)
    assert len(chat_input["chat_kwargs"]) == 17
    assert "obs_file_list" not in chat_input

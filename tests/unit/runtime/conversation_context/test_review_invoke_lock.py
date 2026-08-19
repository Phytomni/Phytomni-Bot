# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review new-review/scope-change must not hold the mutation flock."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from mcp_server_phytomni.agents.review.conversation import (
    ReviewConversationAdapter,
    ReviewConversationOperation,
    _candidate_thread_id,
)
from mcp_server_phytomni.runtime.conversation_context import (
    adapters as adapters_mod,
)
from mcp_server_phytomni.runtime.conversation_context.adapters import (
    ContextAgentInvocation,
    ConversationContextExecutor,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.conversation_context.projection import (
    agent_thread_id,
)
from mcp_server_phytomni.runtime.conversation_context.service import (
    AgentOutcome,
)
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)

pytestmark = pytest.mark.unit

_CONVERSATION_KEY = UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7")
_DIALOGUE_ID = UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad8")


def _envelope() -> ConversationEnvelopeV1:
    """Build one Expert Review envelope for the executor invoke seam."""
    payload: dict[str, object] = {
        "schema_version": 1,
        "conversation_key": str(_CONVERSATION_KEY),
        "dialogue_id": str(_DIALOGUE_ID),
    }
    payload["turn_id"] = "1"
    payload["request_id"] = "request-1"
    payload["operation"] = "append"
    payload["mode"] = "expert"
    payload["current_message"] = {
        "content": "review rice drought genes",
        "locale": "en-US",
    }
    payload["requested_agent_id"] = "ReviewAgent"
    payload["allowed_agent_ids"] = ["ReviewAgent"]
    payload["ledger_cursor"] = 1
    payload["ledger_version"] = "a" * 64
    payload["base_business_context_version"] = 0
    return ConversationEnvelopeV1.model_validate(
        {
            **payload,
            "history_delta": [
                {
                    "turn_id": "1",
                    "role": "user",
                    "content": "review rice drought genes",
                }
            ],
            "artifact_refs": [],
        }
    )


class _ReviewAdapter(ReviewConversationAdapter):
    """NEW_REVIEW adapter that satisfies the executor isinstance check."""

    def __init__(self, stable: str, candidate: str) -> None:
        super().__init__()
        self._stable_thread_id = stable
        self._candidate_thread_id = candidate
        self.failed = False

    @property
    def operation(self) -> ReviewConversationOperation:
        """Pin the turn as a new review so the mutation lock is required."""
        return ReviewConversationOperation.NEW_REVIEW

    def mark_failed(self) -> None:
        """Record a failed reservation without touching durable state."""
        self.failed = True


@pytest.mark.asyncio
async def test_new_review_releases_mutation_lock_before_invoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long Review run must not keep the session-db flock."""
    store = ConversationContextStore(str(tmp_path / "server_tasks.db"))
    executor = ConversationContextExecutor(
        store_factory=lambda: store,
        select_agent=cast(Any, lambda *_args, **_kwargs: None),
    )
    stable = agent_thread_id(_CONVERSATION_KEY, "ReviewAgent")
    candidate = _candidate_thread_id(stable, "1")
    adapter = _ReviewAdapter(stable, candidate)
    dispatch = ContextAgentInvocation(
        arguments={
            "user_query": "review rice drought genes",
            "locale": "en-US",
        },
        conversation_messages=(),
        agent_thread_id=stable,
        private_agent_state={"review_adapter": adapter},
    )
    monkeypatch.setattr(
        adapters_mod, "review_agent_invocation", lambda *_a, **_k: dispatch
    )
    events: list[str] = []
    original_acquire = adapters_mod.acquire_review_mutation_lock

    async def tracking_acquire(store_arg: Any, **kwargs: Any) -> Any:
        lock = await original_acquire(store_arg, **kwargs)
        original_release = lock.release

        def release() -> None:
            events.append("released")
            original_release()

        lock.release = release
        return lock

    monkeypatch.setattr(
        adapters_mod, "acquire_review_mutation_lock", tracking_acquire
    )

    async def invoke(
        _selected: str,
        _envelope: ConversationEnvelopeV1,
        _dispatch: ContextAgentInvocation,
    ) -> AgentOutcome:
        events.append("invoke")
        return AgentOutcome(result={"answer": "ok"}, status="succeeded")

    bindings = object.__getattribute__(executor, "_bindings")
    bindings.sync_invoker.set(invoke)
    invoke_bound = object.__getattribute__(executor, "_invoke")
    outcome = await invoke_bound(
        "ReviewAgent",
        _envelope(),
        cast(Any, None),
    )

    assert outcome.status == "succeeded"
    assert events == ["released", "invoke"]
    assert adapter.failed is False

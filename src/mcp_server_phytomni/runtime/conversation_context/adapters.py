# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""HTTP transport adapters for bounded conversation-context turns."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ...agents.brief_gene.conversation import BriefGeneConversationAdapter
from ...agents.data.conversation import DataConversationAdapter
from ...agents.expert import ToolSelection, ToolSelectionError
from ...agents.knowledge.conversation import (
    KnowledgeClarificationError,
    KnowledgeConversationAdapter,
)
from ...agents.review.conversation import (
    ReviewClarificationError,
    ReviewConversationAdapter,
    ReviewConversationOperation,
)
from ...config.defaults import ApiConfig
from .models import BusinessContext, ContextProjection, ConversationEnvelopeV1
from .projection import agent_thread_id
from .service import (
    AgentOutcome,
    AgentSelection,
    ConversationContextService,
    PreparedTurn,
    PrepareStatus,
    _bounded_review_stage_metadata,
    review_settlement_metadata_from_turn,
)
from .store import (
    ConversationContextStore,
    ReviewMutationLockTimeoutError,
    ReviewSettlementClaim,
    StoredTurn,
)


@dataclass(frozen=True, slots=True)
class ContextAgentInvocation:
    """Canonical public tool arguments plus private native-role history."""

    arguments: dict[str, Any]
    conversation_messages: tuple[dict[str, str], ...]
    agent_thread_id: str
    private_agent_state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _ReviewSettlementRequest:
    """Inputs shared by the private Review acknowledgment helpers."""

    key: tuple[str, str]
    accepted: bool
    staged_turn: StoredTurn | None
    expected_ledger_version: str | None
    mutation_lock_held: bool


@dataclass(frozen=True, slots=True)
class _ReviewSettlementClaim:
    """Durable claim state carried across private Review settlement steps."""

    staged_turn: StoredTurn | None
    claim_token: str | None = None
    claim_fence: int | None = None
    terminal_result: bool | None = None


SyncInvoker = Callable[
    [str, ConversationEnvelopeV1, ContextAgentInvocation],
    Awaitable[AgentOutcome],
]
AsyncInvoker = Callable[
    [str, ConversationEnvelopeV1, dict[str, Any]],
    Awaitable[dict[str, Any]],
]
RouterSelector = Callable[..., Awaitable[ToolSelection | None]]
StoreFactory = Callable[[], ConversationContextStore]
ReviewSettlementLoader = Callable[
    [Mapping[str, Any], StoredTurn], Awaitable[ReviewConversationAdapter]
]


async def _acquire_review_mutation_lock(
    store: ConversationContextStore, *, wait_seconds: float = 30.0
) -> Any:
    """Poll a nonblocking durable lock without blocking the event loop."""
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while True:
        try:
            return store.acquire_review_mutation_lock(timeout=0)
        except ReviewMutationLockTimeoutError:
            if asyncio.get_running_loop().time() >= deadline:
                raise
            await asyncio.sleep(0.01)


def _native_history_from_turns(
    turns: Sequence[Mapping[str, str] | object],
) -> tuple[dict[str, str], ...]:
    """Preserve the bounded chronological native-role history exactly."""
    messages: list[dict[str, str]] = []
    for turn in turns:
        role = getattr(turn, "role", None)
        content = getattr(turn, "content", None)
        if role is None and isinstance(turn, Mapping):
            role = turn.get("role")
            content = turn.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            messages.append({"role": role, "content": content})
    return tuple(messages)


def _native_history_from_projection(
    projection: ContextProjection,
) -> tuple[dict[str, str], ...]:
    """Return bounded native turns without adding fields to MCP schemas."""
    return _native_history_from_turns(projection.relevant_recent_turns)


def canonical_agent_invocation(
    projection: ContextProjection,
    *,
    selected_arguments: Mapping[str, Any] | None = None,
) -> ContextAgentInvocation:
    """Project V1 context to current tool inputs and private role history."""
    arguments = dict(selected_arguments or {})
    arguments["user_query"] = projection.current_query
    arguments["locale"] = projection.locale
    return ContextAgentInvocation(
        arguments=arguments,
        conversation_messages=_native_history_from_projection(projection),
        agent_thread_id=projection.agent_thread_id,
    )


def knowledge_agent_invocation(
    projection: ContextProjection,
    *,
    selected_arguments: Mapping[str, Any] | None = None,
) -> ContextAgentInvocation:
    """Project Knowledge V1 context into private retrieval dispatch state."""
    arguments = dict(selected_arguments or {})
    arguments["user_query"] = projection.current_query
    arguments["locale"] = projection.locale
    adapter = KnowledgeConversationAdapter()
    try:
        prepared = adapter.prepare(projection)
    except KnowledgeClarificationError as exc:
        return ContextAgentInvocation(
            arguments=arguments,
            conversation_messages=(),
            agent_thread_id=projection.agent_thread_id,
            private_agent_state={"clarification_message": str(exc)},
        )
    return ContextAgentInvocation(
        arguments=arguments,
        conversation_messages=(),
        agent_thread_id=prepared["thread_id"],
        private_agent_state={
            "retrieval_query": prepared["retrieval_query"],
            "answer_context": prepared["answer_context"],
            "knowledge_adapter": adapter,
        },
    )


def data_agent_invocation(
    projection: ContextProjection,
    *,
    selected_arguments: Mapping[str, Any] | None = None,
) -> ContextAgentInvocation:
    """Project Data V1 context into a standalone follow-up query."""
    arguments = dict(selected_arguments or {})
    arguments["locale"] = projection.locale
    adapter = DataConversationAdapter()
    prepared = adapter.prepare(projection)
    arguments["user_query"] = prepared["user_query"]
    return ContextAgentInvocation(
        arguments=arguments,
        conversation_messages=_native_history_from_projection(projection),
        agent_thread_id=prepared["thread_id"],
        private_agent_state={
            "data_adapter": adapter,
            "dialog_id": prepared["dialog_id"],
            "rewrite_query": prepared["rewrite_query"],
        },
    )


def brief_gene_agent_invocation(
    projection: ContextProjection,
    *,
    selected_arguments: Mapping[str, Any] | None = None,
) -> ContextAgentInvocation:
    """Project Brief Gene context into private operation state."""
    arguments = dict(selected_arguments or {})
    arguments["user_query"] = projection.current_query
    arguments["locale"] = projection.locale
    adapter = BriefGeneConversationAdapter()
    prepared = adapter.prepare(projection)
    private_state: dict[str, Any] = {
        "brief_gene_adapter": adapter,
        "brief_gene_projection": projection,
        "brief_gene_operation": prepared["operation"].value,
    }
    if prepared["operation"].value == "clarify":
        private_state["clarification_message"] = adapter.clarification_message
    return ContextAgentInvocation(
        arguments=arguments,
        conversation_messages=(),
        agent_thread_id=prepared["thread_id"],
        private_agent_state=private_state,
    )


def review_agent_invocation(
    projection: ContextProjection,
    *,
    selected_arguments: Mapping[str, Any] | None = None,
    turn_id: str | None = None,
) -> ContextAgentInvocation:
    """Project Review context into private bounded conversation state."""
    arguments = dict(selected_arguments or {})
    # Review checkpoints are private LangGraph state.  The native V1 seam
    # reloads the stable derived thread in ``prepare_from_agent``; accepting a
    # request-local checkpoint here would let stale router arguments influence
    # operation selection before that authoritative read.
    arguments.pop("review_checkpoint", None)
    arguments.pop("review_operation", None)
    arguments["user_query"] = projection.current_query
    arguments["locale"] = projection.locale
    adapter = ReviewConversationAdapter()
    try:
        prepared = adapter.prepare(
            projection,
            allow_unresolved_section=True,
            turn_id=turn_id,
        )
    except ReviewClarificationError as exc:
        return ContextAgentInvocation(
            arguments=arguments,
            conversation_messages=(),
            agent_thread_id=projection.agent_thread_id,
            private_agent_state={"clarification_message": str(exc)},
        )
    return ContextAgentInvocation(
        arguments=arguments,
        conversation_messages=_native_history_from_projection(projection),
        agent_thread_id=prepared["thread_id"],
        private_agent_state={
            "review_adapter": adapter,
            "review_operation": prepared["operation"].value,
            "review_projection": projection,
            "review_turn_id": turn_id,
            "review_stable_thread_id": adapter.stable_thread_id,
            "review_candidate_thread_id": adapter.candidate_thread_id,
        },
    )


def native_history_from_context(
    context: BusinessContext,
) -> tuple[dict[str, str], ...]:
    """Build bounded router history from Bot-owned semantic context."""
    return _native_history_from_turns(context.recent_turns)


class ConversationContextExecutor:
    """Run one shared service with request-local HTTP transport callbacks."""

    def __init__(
        self,
        *,
        store_factory: StoreFactory,
        select_agent: RouterSelector,
        api_config_factory: Callable[[], ApiConfig] = ApiConfig,
        review_settlement_loader: ReviewSettlementLoader | None = None,
    ) -> None:
        self._store_factory = store_factory
        self._select_agent = select_agent
        self._api_config_factory = api_config_factory
        self._review_settlement_loader = review_settlement_loader
        self._service: ConversationContextService | None = None
        self._sync_invoker: ContextVar[SyncInvoker | None] = ContextVar(
            "conversation_context_sync_invoker", default=None
        )
        self._async_invoker: ContextVar[AsyncInvoker | None] = ContextVar(
            "conversation_context_async_invoker", default=None
        )
        self._selected_arguments: ContextVar[dict[str, Any] | None] = (
            ContextVar("conversation_context_selected_arguments", default=None)
        )
        self._review_adapter: ContextVar[ReviewConversationAdapter | None] = (
            ContextVar("conversation_context_review_adapter", default=None)
        )
        self._pending_review_settlements: dict[
            tuple[str, str], ReviewConversationAdapter
        ] = {}
        self._pending_review_lock = asyncio.Lock()
        self._review_settlement_ack_lock = asyncio.Lock()
        self._max_pending_review_settlements = 256

    def _service_for_request(self) -> ConversationContextService:
        if self._service is None:
            self._service = ConversationContextService(
                self._store_factory(),
                router=self._route,
                invoke=self._invoke,
                delegate_async=self._delegate_async,
                api_config=self._api_config_factory(),
            )
        return self._service

    @staticmethod
    def _review_settlement_key(
        envelope: ConversationEnvelopeV1,
    ) -> tuple[str, str]:
        """Use the stable Go envelope identity for deferred Review state."""
        return str(envelope.conversation_key), envelope.turn_id

    @staticmethod
    def _expected_review_stable_thread_id(
        key: tuple[str, str],
    ) -> str | None:
        """Derive the only stable Review namespace for one conversation."""
        try:
            conversation_key = UUID(key[0])
        except (ValueError, AttributeError):
            return None
        return agent_thread_id(conversation_key, "ReviewAgent")

    async def defer_review_settlement(
        self,
        envelope: ConversationEnvelopeV1,
        adapter: ReviewConversationAdapter,
    ) -> None:
        """Retain one successful Review candidate until Go acknowledges it."""
        if not adapter.settlement_ready:
            return
        async with self._pending_review_lock:
            if len(self._pending_review_settlements) >= (
                self._max_pending_review_settlements
            ):
                oldest = next(iter(self._pending_review_settlements))
                self._pending_review_settlements.pop(oldest, None)
            self._pending_review_settlements[
                self._review_settlement_key(envelope)
            ] = adapter

    async def _load_review_settlement_adapter(
        self,
        key: tuple[str, str],
        staged_turn: StoredTurn | None,
    ) -> ReviewConversationAdapter | None:
        """Load a pending adapter from memory or durable staged metadata."""
        async with self._pending_review_lock:
            adapter = self._pending_review_settlements.pop(key, None)
        if adapter is not None:
            return adapter
        if staged_turn is None or self._review_settlement_loader is None:
            return None
        metadata = review_settlement_metadata_from_turn(staged_turn)
        if metadata is None:
            return None
        return await self._review_settlement_loader(metadata, staged_turn)

    async def _acknowledge_review_settlement_key(
        self,
        key: tuple[str, str],
        *,
        accepted: bool,
        staged_turn: StoredTurn | None = None,
        expected_ledger_version: str | None = None,
        mutation_lock_held: bool = False,
    ) -> bool:
        """Serialize the complete Review promotion for one ack boundary."""
        async with self._review_settlement_ack_lock:
            service = self._service_for_request()
            lock: Any | None = None
            if not mutation_lock_held:
                lock = await _acquire_review_mutation_lock(service.store)
            try:
                return await self._acknowledge_review_settlement_key_locked(
                    key,
                    accepted=accepted,
                    staged_turn=staged_turn,
                    expected_ledger_version=expected_ledger_version,
                    mutation_lock_held=True,
                )
            finally:
                if lock is not None:
                    lock.release()

    def _claim_review_settlement_state(
        self,
        service: ConversationContextService,
        request: _ReviewSettlementRequest,
    ) -> _ReviewSettlementClaim:
        """Load and claim durable Review metadata before adapter I/O."""
        key = request.key
        current_turn = service.store.load_turn(*key)
        staged_turn = current_turn or request.staged_turn
        metadata = review_settlement_metadata_from_turn(staged_turn)
        if metadata is None or current_turn is None:
            terminal_result = (
                False if current_turn is None and metadata is not None else None
            )
            return _ReviewSettlementClaim(
                staged_turn=staged_turn, terminal_result=terminal_result
            )
        bounded = _bounded_review_stage_metadata(
            metadata, allow_terminal=True
        )
        expected_stable = self._expected_review_stable_thread_id(key)
        if (
            bounded is None
            or bounded["turn_id"] != key[1]
            or expected_stable is None
            or bounded["stable_thread_id"] != expected_stable
        ):
            service.store.mark_review_settlement_failed(
                *key, mutation_lock_held=request.mutation_lock_held
            )
            return _ReviewSettlementClaim(
                staged_turn=staged_turn, terminal_result=False
            )
        settlement_state = bounded["settlement_state"]
        terminal_result = {
            "promoted": request.accepted,
            "rejected": False,
            "failed": False,
        }.get(settlement_state)
        if terminal_result is not None:
            return _ReviewSettlementClaim(
                staged_turn=staged_turn, terminal_result=terminal_result
            )
        claim = service.store.claim_review_settlement(
            *key,
            expected_ledger_version=request.expected_ledger_version,
            expected_base_context_version=(
                staged_turn.base_context_version
                if staged_turn is not None
                else None
            ),
        )
        return self._review_settlement_claim_result(
            service, request, staged_turn, claim
        )

    @staticmethod
    def _review_settlement_claim_result(
        service: ConversationContextService,
        request: _ReviewSettlementRequest,
        staged_turn: StoredTurn | None,
        claim: ReviewSettlementClaim,
    ) -> _ReviewSettlementClaim:
        """Validate a durable claim and reload its authoritative staged turn."""
        key = request.key
        if claim.status == "invalid":
            service.store.mark_review_settlement_failed(
                *key, mutation_lock_held=request.mutation_lock_held
            )
            return _ReviewSettlementClaim(
                staged_turn=staged_turn, terminal_result=False
            )
        if claim.status != "claimed" or claim.claim_token is None:
            return _ReviewSettlementClaim(
                staged_turn=staged_turn, terminal_result=False
            )
        if claim.fence_token is None:
            service.store.mark_review_settlement_failed(
                *key, mutation_lock_held=request.mutation_lock_held
            )
            return _ReviewSettlementClaim(
                staged_turn=staged_turn, terminal_result=False
            )
        return _ReviewSettlementClaim(
            staged_turn=service.store.load_turn(*key),
            claim_token=claim.claim_token,
            claim_fence=claim.fence_token,
        )

    @staticmethod
    def _finalize_review_claim_failed(
        service: ConversationContextService,
        key: tuple[str, str],
        claim: _ReviewSettlementClaim,
    ) -> None:
        """Persist a failed state when a claimed adapter cannot continue."""
        if claim.claim_token is not None:
            service.store.finalize_review_settlement(
                *key,
                claim_token=claim.claim_token,
                fence_token=claim.claim_fence,
                state="failed",
            )

    @staticmethod
    def _attach_review_settlement_fence(
        service: ConversationContextService,
        key: tuple[str, str],
        claim: _ReviewSettlementClaim,
        adapter: ReviewConversationAdapter,
    ) -> None:
        """Give the adapter a live fence check before private writes."""
        if claim.claim_token is None:
            return
        set_fence = getattr(adapter, "set_settlement_fence", None)
        if callable(set_fence):
            set_fence(
                lambda: service.store.is_review_settlement_claim_active(
                    *key,
                    claim_token=claim.claim_token,
                    fence_token=claim.claim_fence,
                )
            )

    async def _reject_review_settlement(
        self,
        service: ConversationContextService,
        request: _ReviewSettlementRequest,
        claim: _ReviewSettlementClaim,
        adapter: ReviewConversationAdapter,
    ) -> bool:
        """Discard a rejected Review candidate and finalize its marker."""
        adapter.mark_failed()
        cleanup_error: BaseException | None = None
        try:
            await adapter.discard_pending_candidate()
        except (
            asyncio.CancelledError,
            RuntimeError,
            ValueError,
            TypeError,
            OSError,
        ) as exc:
            cleanup_error = exc
        if claim.claim_token is None:
            marker_saved = True
        else:
            marker_saved = service.store.finalize_review_settlement(
                *request.key,
                claim_token=claim.claim_token,
                fence_token=claim.claim_fence,
                state="rejected",
            )
        if cleanup_error is not None:
            if claim.claim_token is not None:
                service.store.mark_review_settlement_failed(
                    *request.key,
                    mutation_lock_held=request.mutation_lock_held,
                )
            raise cleanup_error
        return marker_saved

    async def _accept_review_settlement(
        self,
        service: ConversationContextService,
        request: _ReviewSettlementRequest,
        claim: _ReviewSettlementClaim,
        adapter: ReviewConversationAdapter,
    ) -> bool:
        """Reserve, settle, and promote one accepted Review candidate."""
        if claim.claim_token is not None:
            assert claim.claim_fence is not None
            reservation = service.store.reserve_review_settlement(
                *request.key,
                claim_token=claim.claim_token,
                fence_token=claim.claim_fence,
                expected_ledger_version=request.expected_ledger_version,
                expected_base_context_version=(
                    claim.staged_turn.base_context_version
                    if claim.staged_turn is not None
                    else None
                ),
            )
            if reservation.status == "promoted":
                return True
            if reservation.status != "promoting":
                self._finalize_review_claim_failed(service, request.key, claim)
                return False
        try:
            if (
                claim.claim_token is not None
                and not service.store.is_review_settlement_claim_active(
                    *request.key, claim_token=claim.claim_token
                )
            ):
                raise RuntimeError("Review settlement claim was fenced")
            await adapter.settle_async(True)
        except BaseException:
            adapter.mark_failed()
            try:
                await adapter.discard_pending_candidate()
            finally:
                self._finalize_review_claim_failed(service, request.key, claim)
            raise
        if (
            claim.claim_token is not None
            and not service.store.finalize_review_settlement(
                *request.key,
                claim_token=claim.claim_token,
                fence_token=claim.claim_fence,
                state="promoted",
                report_revision=adapter.report_revision,
            )
        ):
            raise RuntimeError(
                "Review settlement marker could not be persisted"
            )
        return True

    async def _settle_review_adapter(
        self,
        service: ConversationContextService,
        request: _ReviewSettlementRequest,
        claim: _ReviewSettlementClaim,
        adapter: ReviewConversationAdapter,
    ) -> bool:
        """Run the accepted/rejected adapter path under the durable fence."""
        self._attach_review_settlement_fence(
            service, request.key, claim, adapter
        )
        if not request.accepted:
            return await self._reject_review_settlement(
                service, request, claim, adapter
            )
        return await self._accept_review_settlement(
            service, request, claim, adapter
        )

    async def _acknowledge_review_settlement_key_locked(
        self,
        key: tuple[str, str],
        *,
        accepted: bool,
        staged_turn: StoredTurn | None = None,
        expected_ledger_version: str | None = None,
        mutation_lock_held: bool = False,
    ) -> bool:
        """Apply one durable Review acknowledgment by conversation identity."""
        service = self._service_for_request()
        request = _ReviewSettlementRequest(
            key=key,
            accepted=accepted,
            staged_turn=staged_turn,
            expected_ledger_version=expected_ledger_version,
            mutation_lock_held=mutation_lock_held,
        )
        claim = self._claim_review_settlement_state(service, request)
        if claim.terminal_result is not None:
            return claim.terminal_result
        try:
            adapter = await self._load_review_settlement_adapter(
                key, claim.staged_turn
            )
        except BaseException:
            self._finalize_review_claim_failed(service, key, claim)
            raise
        if adapter is None:
            self._finalize_review_claim_failed(service, key, claim)
            return False
        return await self._settle_review_adapter(
            service, request, claim, adapter
        )

    async def acknowledge_review_settlement(
        self,
        envelope: ConversationEnvelopeV1,
        *,
        accepted: bool,
        mutation_lock_held: bool = False,
    ) -> bool:
        """Commit private Review state only after an explicit durable ack.

        The caller must invoke this seam only after the Go ledger has accepted
        the staged turn. A missing or rejected acknowledgement discards the
        candidate and leaves the active Review checkpoint untouched.
        """
        return await self._acknowledge_review_settlement_key(
            self._review_settlement_key(envelope),
            accepted=accepted,
            mutation_lock_held=mutation_lock_held,
        )

    async def acknowledge_review_settlement_for_turn(
        self,
        conversation_key: str,
        turn_id: str,
        *,
        accepted: bool,
        staged_turn: StoredTurn | None = None,
        expected_ledger_version: str | None = None,
        mutation_lock_held: bool = False,
    ) -> bool:
        """Acknowledge Review from the HTTP settlement route after restart."""
        return await self._acknowledge_review_settlement_key(
            (conversation_key, turn_id),
            accepted=accepted,
            staged_turn=staged_turn,
            expected_ledger_version=expected_ledger_version,
            mutation_lock_held=mutation_lock_held,
        )

    async def execute(
        self,
        *,
        envelope: ConversationEnvelopeV1,
        invoke: SyncInvoker,
        delegate_async: AsyncInvoker,
    ) -> PreparedTurn:
        """Bind one transport and execute the durable context lifecycle."""
        sync_token = self._sync_invoker.set(invoke)
        async_token = self._async_invoker.set(delegate_async)
        arguments_token = self._selected_arguments.set({})
        review_token = self._review_adapter.set(None)
        try:
            prepared = await self._service_for_request().execute_turn(envelope)
            adapter = self._review_adapter.get()
            if adapter is not None:
                ready_to_stage = (
                    prepared.status is PrepareStatus.RETURN_STAGED
                    and prepared.stage is not None
                    and not prepared.stage.context_degraded
                    and adapter.settlement_ready
                )
                if ready_to_stage:
                    await self.defer_review_settlement(envelope, adapter)
                else:
                    adapter.mark_failed()
                    await adapter.discard_pending_candidate()
            return prepared
        except BaseException:
            adapter = self._review_adapter.get()
            if adapter is not None:
                adapter.mark_failed()
                await adapter.discard_pending_candidate()
            raise
        finally:
            self._review_adapter.reset(review_token)
            self._selected_arguments.reset(arguments_token)
            self._async_invoker.reset(async_token)
            self._sync_invoker.reset(sync_token)

    async def _route(
        self,
        user_query: str,
        allowed_agent_ids: Sequence[str],
        context: BusinessContext,
    ) -> AgentSelection:
        selection = await self._select_agent(
            user_query,
            native_history_from_context(context),
            allowed_tools=allowed_agent_ids,
            forced_tool=None,
        )
        if selection is None:
            raise ToolSelectionError("strict routing returned no selection")
        self._selected_arguments.set(dict(selection.arguments))
        return AgentSelection(selection.tool_name, "ROUTER_SELECTED")

    async def _invoke(
        self,
        selected_agent_id: str,
        envelope: ConversationEnvelopeV1,
        projection: ContextProjection,
    ) -> AgentOutcome:
        invoke = self._sync_invoker.get()
        if invoke is None:
            raise RuntimeError("context sync invoker is unavailable")
        selected_arguments = self._selected_arguments.get() or {}
        if selected_agent_id == "KnowledgeAgent":
            dispatch = knowledge_agent_invocation(
                projection,
                selected_arguments=selected_arguments,
            )
        elif selected_agent_id == "DataAgent":
            dispatch = data_agent_invocation(
                projection,
                selected_arguments=selected_arguments,
            )
        elif selected_agent_id == "BriefGeneAgent":
            dispatch = brief_gene_agent_invocation(
                projection,
                selected_arguments=selected_arguments,
            )
        elif selected_agent_id == "ReviewAgent":
            dispatch = review_agent_invocation(
                projection,
                selected_arguments=selected_arguments,
                turn_id=envelope.turn_id,
            )
        else:
            dispatch = canonical_agent_invocation(
                projection,
                selected_arguments=selected_arguments,
            )
        if selected_agent_id == "ReviewAgent":
            adapter = dispatch.private_agent_state.get("review_adapter")
            self._review_adapter.set(
                adapter
                if isinstance(adapter, ReviewConversationAdapter)
                else None
            )
            review_operation = (
                adapter.operation
                if isinstance(adapter, ReviewConversationAdapter)
                else None
            )
            if (
                isinstance(adapter, ReviewConversationAdapter)
                and review_operation is not None
                and review_operation
                in {
                    ReviewConversationOperation.NEW_REVIEW,
                    ReviewConversationOperation.SCOPE_CHANGE,
                }
            ):
                store = self._service_for_request().store
                mutation_lock = await _acquire_review_mutation_lock(store)
                try:
                    registered = (
                        adapter.stable_thread_id is not None
                        and adapter.candidate_thread_id is not None
                        and store.register_review_candidate(
                            str(envelope.conversation_key),
                            envelope.turn_id,
                            review_operation.value,
                            adapter.stable_thread_id,
                            adapter.candidate_thread_id,
                            mutation_lock_held=True,
                        )
                    )
                    if not registered:
                        adapter.mark_failed()
                        return AgentOutcome(
                            result={"status": "failed"}, status="failed"
                        )
                    return await invoke(
                        selected_agent_id,
                        envelope,
                        dispatch,
                    )
                finally:
                    mutation_lock.release()
        return await invoke(
            selected_agent_id,
            envelope,
            dispatch,
        )

    async def _delegate_async(
        self,
        selected_agent_id: str,
        envelope: ConversationEnvelopeV1,
    ) -> dict[str, Any]:
        invoke = self._async_invoker.get()
        if invoke is None:
            raise RuntimeError("context async invoker is unavailable")
        arguments = dict(self._selected_arguments.get() or {})
        arguments.setdefault("user_query", envelope.current_message.content)
        arguments["locale"] = envelope.current_message.locale
        return await invoke(selected_agent_id, envelope, arguments)


__all__ = [
    "ContextAgentInvocation",
    "ConversationContextExecutor",
    "brief_gene_agent_invocation",
    "canonical_agent_invocation",
    "data_agent_invocation",
    "knowledge_agent_invocation",
    "native_history_from_context",
    "review_agent_invocation",
]

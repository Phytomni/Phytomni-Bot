# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""LangGraph-based deep research and literature review generation.

Hosts DeepResearchAgent (graph construction, node methods, arun
entry point) plus the review_agent_function compatibility wrapper.
Public IO schemas live in state.py; planning / report / summary
mixins live in their own modules; pipeline helpers live in
pipeline.py-style siblings.
"""

import logging
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send, interrupt

from ...common.prompts import get_prompt
from ...common.responses import message_content
from ...config.defaults import ReviewConfig
from ...config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    OBS_TRANSFER_CONFIG_FIELD_MAP,
    RETRIEVAL_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...config.settings import SensitiveConfig, get_sensitive_config
from ...graphs.chat_adapters import (
    build_chat_input,
    build_chat_kwargs_for,
    extract_chat_response,
)
from ...mcp.progress_events import emit_progress
from ...runtime.agent_registry import (
    agent_fingerprint_values,
    get_cached_agent,
)
from ...runtime.langgraph_runner import (
    ainvoke_graph,
    ensure_checkpointer,
    make_async_router,
)
from ...runtime.memory import MemoryGraphContext
from ..chat.service import phyto_chat
from ..knowledge.agent import KnowledgeAgent
from ..knowledge.state import KnowledgeInput, KnowledgeOutput, KnowledgeState
from ..shared.a2ui.loop import A2UI_MAX_ROUNDS, next_a2ui_round
from ..shared.analysis import _compute_traceback_digest
from ..shared.chat_subgraph import (
    CHAT_APP,
    make_chat_after_router,
    make_chat_node_wrapper,
)
from ..shared.intermediate_state import merge_intermediate_state
from ..shared.knowledge_subgraph import build_knowledge_app
from ..shared.parallel_dispatch import FailureRecord
from .planning import ReviewPlanningMixin
from .report import ReviewReportMixin
from .state import (
    DeepResearchInput,
    DeepResearchOutput,
    DeepResearchState,
)
from .summary import ReviewSummaryMixin

logger = logging.getLogger(__name__)

# Workers MUST NOT raise per the TW-1 sentinel-coexistence contract:
# downstream ``draft_reduce_node`` iterates the indexed-results list
# one entry per dimension and would short-circuit on a propagated
# exception. The worker therefore catches Exception broadly and writes
# BOTH a legacy empty-string sentinel AND a ``FailureRecord`` to the
# shared failures channel. Mirrors ``_RETRIEVE_WORKER_CAUGHT`` in
# ``planning.py`` which carries the same design intent through static
# analysis (pylint ``W0718``).
_DRAFT_WORKER_CAUGHT: tuple[type[Exception], ...] = (Exception,)

# Mirror of ``_DRAFT_WORKER_CAUGHT`` for the review_results fan-out
# worker.  The legacy ``review_node`` substituted the empty-JSON
# sentinel ``"{}"`` for failed critiques (see ``report.py``), and
# ``revise_node`` downstream parses each critique via
# ``_extract_json_object`` which returns ``{}`` on an empty payload, so
# the sentinel must remain ``"{}"`` (NOT ``""``) to preserve behaviour.
_REVIEW_RESULTS_WORKER_CAUGHT: tuple[type[Exception], ...] = (Exception,)

# Mirror of ``_REVIEW_RESULTS_WORKER_CAUGHT`` for the revised fan-out
# worker. Legacy ``revise_node`` substituted ``state["draft_contents"][idx]``
# for failed workers (see ``report.py`` lines 159-165). The worker
# writes an empty-string sentinel into ``revised_indexed_results`` and
# ``revised_reduce_node`` substitutes the original draft so dimension
# ordering and length stay aligned with ``research_dimensions``.
_REVISED_WORKER_CAUGHT: tuple[type[Exception], ...] = (Exception,)

REVIEW_CONFIG = ReviewConfig()

REVIEW_CONFIG_FIELD_MAP = {
    **CHAT_COMPLETION_CONFIG_FIELD_MAP,
    **RETRIEVAL_CONFIG_FIELD_MAP,
    **OBS_TRANSFER_CONFIG_FIELD_MAP,
    **RETRY_CONFIG_FIELD_MAP,
}
REVIEW_SENSITIVE_FIELD_MAP = {
    "base_url": "BASE_URL",
    "model": "MODEL_ID",
}
REVIEW_SECRET_FIELD_MAP = {
    "api_key": "API_KEY",
    "access_key_id": "ACCESS_KEY_ID",
    "secret_access_key": "SECRET_ACCESS_KEY",
}


class DeepResearchAgent(
    ReviewPlanningMixin, ReviewReportMixin, ReviewSummaryMixin
):
    """LangGraph-based deep research agent from the lihu branch logic.

    Attributes:
        checkpointer: LangGraph checkpointer used by the compiled graph.
        review_config: Public config for retrieval, upload, and chat defaults.
        sensitive_config: Sensitive config with model and OBS credentials.
        ka: KnowledgeAgent used for literature retrieval.
        app: Compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver | None = None,
        review_config: ReviewConfig = REVIEW_CONFIG,
        sensitive_config: SensitiveConfig | None = None,
        knowledge_agent: KnowledgeAgent | None = None,
    ):
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.review_config = review_config
        self.sensitive_config = sensitive_config or get_sensitive_config()
        self.ka = knowledge_agent or KnowledgeAgent(
            knowledge_config=review_config,
            sensitive_config=self.sensitive_config,
        )
        self._knowledge_app: (
            CompiledStateGraph[
                KnowledgeState,
                MemoryGraphContext,
                KnowledgeInput,
                KnowledgeOutput,
            ]
            | None
        )
        self._knowledge_app = build_knowledge_app(
            knowledge_config=self.review_config,
            sensitive_config=self.sensitive_config,
        )
        self.app = self._build_graph()

    def _build_graph(self):
        """Build and compile the LangGraph StateGraph workflow.

        Replaces the three single-shot chat sites
        (``plan_query`` / ``summary`` / ``follow_up``) with prep + post
        pairs surrounding a single shared ``chat`` node registered via
        :func:`~agents.shared.chat_subgraph.make_chat_node_wrapper`,
        and replaces both per-dimension chat fan-out sites (draft and
        review_results) with Send-dispatch triads (``draft_dispatch``
        → N × ``draft_worker_node`` → ``draft_reduce_node``;
        ``review_results_dispatch`` → N × ``review_results_worker_node``
        → ``review_results_reduce_node``) whose workers await the
        module-level :data:`CHAT_APP` so xray expands the chat
        subgraph under each worker. The retrieve site is likewise
        replaced by an analogous Send-dispatch triad
        (``retrieve_dispatch`` → N × ``retrieve_worker_node`` →
        ``retrieve_reduce_node``). The
        revised fan-out site is likewise replaced by a Send triad
        (``revised_dispatch`` → N × ``revised_worker_node`` →
        ``revised_reduce_node``) whose workers call
        ``self._feedback_rag``; the only legacy ``asyncio.gather`` body
        that remains lives inside ``_feedback_rag`` for the per-query
        supplementary retrieval fan-in.

        Returns:
            Compiled LangGraph application bound to
            ``self.checkpointer``.
        """
        workflow = StateGraph(
            state_schema=DeepResearchState,
            input_schema=DeepResearchInput,
            output_schema=DeepResearchOutput,
        )

        self._wire_chat_subgraph(workflow)

        return workflow.compile(checkpointer=self.checkpointer)

    def _wire_chat_subgraph(self, workflow: StateGraph) -> None:
        """Register the prep + post + shared chat form on ``workflow``.

        Replaces the three single-shot chat sites (``plan_query`` /
        ``summary`` / ``follow_up``) with prep + post pairs surrounding
        a single shared ``chat`` node. The chat node is registered via
        :func:`~agents.shared.chat_subgraph.make_chat_node_wrapper` so
        LangGraph's ``xray`` rendering can inline the compiled chat
        subgraph in the review render.
        :func:`~agents.shared.chat_subgraph.make_chat_after_router`
        reads the ``pending_post`` sentinel each prep node stages to
        branch back to the correct post node after the chat call.

        Both per-dimension chat fan-out sites are replaced by
        Send-dispatch triads: the draft site
        (``draft_dispatch`` → ``draft_worker_node`` × N →
        ``draft_reduce_node``) and the review_results site
        (``review_results_dispatch`` →
        ``review_results_worker_node`` × N →
        ``review_results_reduce_node``).  Each per-worker
        ``CHAT_APP.ainvoke`` lets xray expand the shared chat
        subgraph under every worker key. The revised fan-out site is
        likewise replaced by a Send triad (``revised_dispatch`` →
        ``revised_worker_node`` × N → ``revised_reduce_node``) whose
        workers call ``self._feedback_rag`` so the per-dimension
        critique-driven supplementary retrieval and revision run
        concurrently; the only legacy ``asyncio.gather`` body that
        remains lives inside ``_feedback_rag`` itself for the
        per-query supplementary retrieval fan-in.
        The retrieve site is likewise replaced by a Send-dispatch triad
        (``retrieve_dispatch`` → ``retrieve_worker_node`` × N →
        ``retrieve_reduce_node``) so each research dimension fans out
        to a dedicated KnowledgeAgent subgraph invocation before
        ``draft_dispatch``.

        Args:
            workflow: Uncompiled ``StateGraph`` to register nodes and
                edges on.
        """
        # === 3 prep + 3 post + 1 shared chat mount ===
        workflow.add_node("plan_query_prep_node", self.plan_query_prep_node)
        workflow.add_node("plan_query_post_node", self.plan_query_post_node)
        workflow.add_node("summary_prep_node", self.summary_prep_node)
        workflow.add_node("summary_post_node", self.summary_post_node)
        workflow.add_node("follow_up_prep_node", self.follow_up_prep_node)
        workflow.add_node("follow_up_post_node", self.follow_up_post_node)
        workflow.add_node("approval_node", self.approval_node)
        workflow.add_node(
            "chat",
            make_chat_node_wrapper(
                build_input_fn=lambda state: state["chat_payload"],
                extract_output_fn=lambda chat_output: (
                    chat_output.get("response") or {}
                ),
                response_key="chat_response",
            ),
        )

        # === Retrieve site: Send fan-out ===
        knowledge_app = self._knowledge_app
        if knowledge_app is None:
            raise RuntimeError(
                "unreachable: _knowledge_app must be built in __init__"
            )
        workflow.add_node(
            "retrieve_dispatch", self.retrieve_prepare_tasks_node
        )
        workflow.add_node(
            "retrieve_worker_node",
            self.make_retrieve_worker_node(knowledge_app),
        )
        workflow.add_node("retrieve_reduce_node", self.retrieve_reduce_node)
        workflow.add_conditional_edges(
            "retrieve_dispatch",
            make_async_router(self.route_retrieve_tasks),
            ["retrieve_worker_node"],
        )
        workflow.add_edge("retrieve_worker_node", "retrieve_reduce_node")
        retrieve_in, retrieve_out = (
            "retrieve_dispatch",
            "retrieve_reduce_node",
        )

        # === Draft site: Send fan-out (flag-on) ===
        workflow.add_node("draft_dispatch", self.draft_prepare_tasks_node)
        workflow.add_node("draft_worker_node", self.draft_worker_node)
        workflow.add_node("draft_reduce_node", self.draft_reduce_node)
        workflow.add_conditional_edges(
            "draft_dispatch",
            make_async_router(self.route_draft_tasks),
            ["draft_worker_node"],
        )
        workflow.add_edge("draft_worker_node", "draft_reduce_node")

        # === Review-results site: Send fan-out (flag-on) ===
        workflow.add_node(
            "review_results_dispatch",
            self.review_results_prepare_tasks_node,
        )
        workflow.add_node(
            "review_results_worker_node",
            self.review_results_worker_node,
        )
        workflow.add_node(
            "review_results_reduce_node",
            self.review_results_reduce_node,
        )
        workflow.add_conditional_edges(
            "review_results_dispatch",
            make_async_router(self.route_review_results_tasks),
            ["review_results_worker_node"],
        )
        workflow.add_edge(
            "review_results_worker_node", "review_results_reduce_node"
        )

        # === Revised site: Send fan-out (flag-on) ===
        workflow.add_node("revised_dispatch", self.revised_prepare_tasks_node)
        workflow.add_node("revised_worker_node", self.revised_worker_node)
        workflow.add_node("revised_reduce_node", self.revised_reduce_node)
        workflow.add_conditional_edges(
            "revised_dispatch",
            make_async_router(self.route_revised_tasks),
            ["revised_worker_node"],
        )
        workflow.add_edge("revised_worker_node", "revised_reduce_node")

        # === Wire prep → chat (3 sites) ===
        workflow.add_edge("plan_query_prep_node", "chat")
        workflow.add_edge("summary_prep_node", "chat")
        workflow.add_edge("follow_up_prep_node", "chat")

        # === Shared chat → post (after-router) ===
        workflow.add_conditional_edges(
            "chat",
            make_async_router(make_chat_after_router()),
            {
                "plan_query_post_node": "plan_query_post_node",
                "summary_post_node": "summary_post_node",
                "follow_up_post_node": "follow_up_post_node",
            },
        )

        # === Linear pipeline edges ===
        workflow.add_edge(START, "plan_query_prep_node")
        workflow.add_edge("plan_query_post_node", retrieve_in)
        workflow.add_edge(retrieve_out, "draft_dispatch")
        workflow.add_edge("draft_reduce_node", "review_results_dispatch")
        workflow.add_edge("review_results_reduce_node", "revised_dispatch")
        workflow.add_edge("revised_reduce_node", "summary_prep_node")
        workflow.add_edge("summary_post_node", "approval_node")
        workflow.add_conditional_edges(
            "approval_node",
            make_async_router(self.route_after_approval),
            {
                "follow_up_prep_node": "follow_up_prep_node",
                "summary_prep_node": "summary_prep_node",
            },
        )
        workflow.add_edge("follow_up_post_node", END)

    async def _chat(
        self,
        prompt: str,
        response_format_override: dict[str, str | dict] | None = None,
    ) -> dict[str, Any] | None:
        """Call the configured LLM."""
        return await phyto_chat(
            user_query=prompt,
            prompt_file=self.review_config.PROMPT_FILE,
            prompt_path=self.review_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.review_config.FREQUENCY_PENALTY,
            n=self.review_config.N,
            presence_penalty=self.review_config.PRESENCE_PENALTY,
            reasoning_effort=self.review_config.REASONING_EFFORT,
            response_format=response_format_override
            or self.review_config.RESPONSE_FORMAT,
            stream=self.review_config.STREAM,
            temperature=self.review_config.TEMPERATURE,
            top_p=self.review_config.TOP_P,
            user=self.review_config.USER,
            timeout=self.review_config.TIMEOUT,
            retriable_codes=self.review_config.RETRIABLE_CODES,
            max_retries=self.review_config.MAX_RETRIES,
        )

    async def approval_node(self, state: DeepResearchState) -> dict[str, Any]:
        """Pause for human approval of the synthesized summary.

        Emits a progress tick, then calls ``interrupt()`` with the draft
        summary so a human can approve or reject. On resume, LangGraph
        replays this node from the top and ``interrupt()`` returns the
        decision payload the adapter supplied. Each resume increments
        ``a2ui_round`` (bounded by :data:`A2UI_MAX_ROUNDS`).

        Args:
            state: Current workflow state; reads ``summary_content`` and
                ``a2ui_round``.

        Returns:
            State delta recording the human decision under
            ``approval_decision``, clearing ``approval_pending``, and
            bumping ``a2ui_round``.
        """
        emit_progress("awaiting_approval", 0, detail="awaiting human review")
        decision = interrupt({"draft": state["summary_content"]})
        return {
            "approval_decision": decision,
            "approval_pending": False,
            "a2ui_round": next_a2ui_round(state.get("a2ui_round")),
        }

    def route_after_approval(self, state: DeepResearchState) -> str:
        """Route after approval: follow-up, or redraft when under N=2.

        Cancel, form/choice submit, and approve all continue to
        follow-up. Plain reject redrafts via ``summary_prep_node`` only
        while ``a2ui_round < A2UI_MAX_ROUNDS``; at the cap, reject also
        forces follow-up.

        Args:
            state: Current workflow state; reads ``approval_decision``
                and ``a2ui_round``.

        Returns:
            ``"follow_up_prep_node"`` or ``"summary_prep_node"``.
        """
        decision = state.get("approval_decision") or {}
        round_ = state.get("a2ui_round") or 0
        if decision.get("cancelled") is True:
            return "follow_up_prep_node"
        if (
            decision.get("fields") is not None
            or decision.get("selected") is not None
        ):
            return "follow_up_prep_node"
        if decision.get("approved"):
            return "follow_up_prep_node"
        if round_ >= A2UI_MAX_ROUNDS:
            return "follow_up_prep_node"
        return "summary_prep_node"

    async def draft_prepare_tasks_node(
        self, state: DeepResearchState
    ) -> dict[str, Any]:
        """Prepare for Send fan-out over the draft dimensions.

        Acts as a graph 'split' node — returns an empty delta. The
        actual per-dimension Send payloads are constructed by
        ``route_draft_tasks`` (see ``add_conditional_edges`` in
        ``_wire_chat_subgraph``). Mirrors
        ``retrieve_prepare_tasks_node`` in ``planning.py``.
        """
        del state
        return {}

    async def draft_worker_node(
        self, state: DeepResearchState
    ) -> dict[str, Any]:
        """Per-dimension draft worker invoked via ``Send``.

        Awaits the module-level :data:`CHAT_APP` so LangGraph's
        ``find_subgraph_pregel`` walker discovers the compiled chat
        subgraph through the closure's ``__globals__`` lookup and
        expands it under ``draft_worker_node:<child>`` in xray.

        On success writes a single ``(task_index, content)`` tuple
        into ``draft_indexed_results`` via ``operator.add``. On
        exception writes BOTH a legacy empty-string sentinel AND a
        ``FailureRecord`` into the shared failures channel (TW-1
        coexistence — ``draft_reduce_node`` downstream still iterates
        a string per dimension; per-task failure detail surfaces in
        ``raw.phytomni_state``).
        """
        task_index = state["task_index"]
        try:
            chat_output = await CHAT_APP.ainvoke(state["chat_payload"])
            content = message_content(extract_chat_response(chat_output))
            return {
                "draft_indexed_results": [(task_index, content)],
            }
        except _DRAFT_WORKER_CAUGHT as exc:
            logger.exception(
                "draft worker failed: task_index=%s subtopic=%s",
                task_index,
                state.get("subtopic"),
            )
            return {
                "draft_indexed_results": [(task_index, "")],
                "failures": [
                    FailureRecord(
                        task_label=f"draft:{task_index}",
                        message=str(exc),
                        kind="execute",
                        traceback_digest=_compute_traceback_digest(exc),
                    )
                ],
            }

    async def draft_reduce_node(
        self, state: DeepResearchState
    ) -> dict[str, Any]:
        """Project ``draft_indexed_results`` into ``draft_contents``.

        Sorts the accumulated ``(task_index, content)`` tuples by
        task_index so concurrent worker completion order does not
        affect downstream dimension ordering.
        """
        emit_progress(
            "drafting",
            len(state.get("draft_indexed_results", [])),
            total=len(state.get("dimension_params", [])),
            detail="drafting review dimensions",
        )
        indexed = sorted(state["draft_indexed_results"], key=lambda t: t[0])
        return {
            "draft_contents": [content for _, content in indexed],
        }

    def route_draft_tasks(self, state: DeepResearchState) -> list[Send]:
        """Build N Send payloads, one per research dimension.

        Each payload carries the per-worker ``task_index``, the
        upstream ``subtopic`` / ``knowledge`` strings (used by the
        worker's failure logging), and the fully built ``chat_payload``
        the per-worker ``CHAT_APP.ainvoke`` consumes.
        """
        chat_kwargs = build_chat_kwargs_for(
            self.review_config,
            self.sensitive_config,
            with_follow_up=False,
        )
        return [
            Send(
                "draft_worker_node",
                {
                    "task_index": i,
                    "subtopic": param["subtopic"],
                    "knowledge": param["knowledge"],
                    "chat_payload": build_chat_input(
                        user_query=get_prompt(
                            self.review_config.PROMPT_FILE,
                            "user/deep_research_dimension",
                            param,
                        ),
                        chat_kwargs=chat_kwargs,
                    ),
                },
            )
            for i, param in enumerate(state["dimension_params"])
        ]

    async def review_results_prepare_tasks_node(
        self, state: DeepResearchState
    ) -> dict[str, Any]:
        """Prepare for Send fan-out over the per-draft critiques.

        Acts as a graph 'split' node — returns an empty delta. The
        actual per-dimension Send payloads are constructed by
        ``route_review_results_tasks`` (see ``add_conditional_edges``
        in ``_wire_chat_subgraph``). Mirrors
        ``draft_prepare_tasks_node`` above.
        """
        del state
        return {}

    async def review_results_worker_node(
        self, state: DeepResearchState
    ) -> dict[str, Any]:
        """Per-dimension critique worker invoked via ``Send``.

        Awaits the module-level :data:`CHAT_APP` so LangGraph's
        ``find_subgraph_pregel`` walker discovers the compiled chat
        subgraph through the closure's ``__globals__`` lookup and
        expands it under ``review_results_worker_node:<child>`` in
        xray.

        On success writes a single ``(task_index, content)`` tuple
        into ``review_indexed_results`` via ``operator.add``. On
        exception writes BOTH the legacy empty-JSON sentinel ``"{}"``
        AND a ``FailureRecord`` into the shared failures channel
        (TW-1 coexistence — ``review_results_reduce_node`` downstream
        still iterates a string per dimension; per-task failure detail
        surfaces in ``raw.phytomni_state``).
        """
        task_index = state["task_index"]
        try:
            chat_output = await CHAT_APP.ainvoke(state["chat_payload"])
            content = message_content(extract_chat_response(chat_output))
            return {
                "review_indexed_results": [(task_index, content)],
            }
        except _REVIEW_RESULTS_WORKER_CAUGHT as exc:
            logger.exception(
                "review_results worker failed: task_index=%s subtopic=%s",
                task_index,
                state.get("subtopic"),
            )
            return {
                "review_indexed_results": [(task_index, "{}")],
                "failures": [
                    FailureRecord(
                        task_label=f"review_results:{task_index}",
                        message=str(exc),
                        kind="execute",
                        traceback_digest=_compute_traceback_digest(exc),
                    )
                ],
            }

    async def review_results_reduce_node(
        self, state: DeepResearchState
    ) -> dict[str, Any]:
        """Project ``review_indexed_results`` into ``review_contents``.

        Sorts the accumulated ``(task_index, content)`` tuples by
        task_index so concurrent worker completion order does not
        affect downstream dimension ordering.
        """
        indexed = sorted(state["review_indexed_results"], key=lambda t: t[0])
        return {
            "review_contents": [content for _, content in indexed],
        }

    def route_review_results_tasks(
        self, state: DeepResearchState
    ) -> list[Send]:
        """Build N Send payloads, one per draft critique.

        Each payload carries the per-worker ``task_index``, the
        upstream ``subtopic`` string (used by the worker's failure
        logging), and the fully built ``chat_payload`` the per-worker
        ``CHAT_APP.ainvoke`` consumes.  Each prompt receives the
        current subtopic, the sibling subtopics (for cross-section
        awareness), and the draft text under critique — matches the
        legacy ``review_node`` prompt builder in ``report.py``.
        """
        chat_kwargs = build_chat_kwargs_for(
            self.review_config,
            self.sensitive_config,
            with_follow_up=False,
        )
        dimensions = state["research_dimensions"]
        draft_contents = state["draft_contents"]
        return [
            Send(
                "review_results_worker_node",
                {
                    "task_index": i,
                    "subtopic": dimensions[i],
                    "chat_payload": build_chat_input(
                        user_query=get_prompt(
                            self.review_config.PROMPT_FILE,
                            "user/deep_research_review",
                            {
                                "current_subtopic": dimensions[i],
                                "other_subtopics": [
                                    subtopic
                                    for idx, subtopic in enumerate(dimensions)
                                    if idx != i
                                ],
                                "draft_text": draft_text,
                            },
                        ),
                        chat_kwargs=chat_kwargs,
                    ),
                },
            )
            for i, draft_text in enumerate(draft_contents)
        ]

    async def revised_prepare_tasks_node(
        self, state: DeepResearchState
    ) -> dict[str, Any]:
        """Prepare for Send fan-out over the per-dimension revision passes.

        Acts as a graph 'split' node — returns an empty delta. The
        actual per-dimension Send payloads are constructed by
        ``route_revised_tasks`` (see ``add_conditional_edges`` in
        ``_wire_chat_subgraph``). Mirrors
        ``review_results_prepare_tasks_node`` above.
        """
        del state
        return {}

    async def revised_worker_node(
        self, state: DeepResearchState
    ) -> dict[str, Any]:
        """Per-dimension revision worker invoked via ``Send``.

        Calls ``self._feedback_rag`` directly (its body in
        ``report.py`` issues the supplementary retrieval gather and
        the citation audit), mirroring how the legacy ``revise_node``
        walked each dimension.  On success writes a single
        ``(task_index, revised_content)`` tuple into
        ``revised_indexed_results`` plus any supplementary documents
        into ``add_doc_list`` via ``operator.add``.

        On exception writes BOTH a legacy empty-string sentinel AND a
        ``FailureRecord`` into the shared failures channel
        (TW-1 coexistence — ``revised_reduce_node`` downstream still
        iterates one entry per dimension and substitutes the original
        draft when the sentinel is empty; per-task failure detail
        surfaces in ``raw.phytomni_state``).
        """
        # The Send payload built by ``route_revised_tasks`` always
        # populates these four fields with their concrete types; the
        # state schema declares them Optional so they remain absent on
        # the non-Send paths. Narrow with explicit assertions so a
        # malformed Send crashes loud here rather than corrupting
        # downstream ``_feedback_rag`` inputs silently.
        task_index = state["task_index"]
        draft_content = state["draft_content"]
        review_content = state["review_content"]
        raw_doc_list = state["raw_doc_list"]
        assert task_index is not None, "Send payload missing task_index"
        assert draft_content is not None, "Send payload missing draft_content"
        assert (
            review_content is not None
        ), "Send payload missing review_content"
        assert raw_doc_list is not None, "Send payload missing raw_doc_list"
        try:
            result = await self._feedback_rag(
                subtopic_idx=task_index,
                draft_content=draft_content,
                review_content=review_content,
                raw_doc_list=raw_doc_list,
            )
            return {
                "revised_indexed_results": [
                    (task_index, result.get("revised_content", ""))
                ],
                "add_doc_list": list(result.get("add_doc_list", [])),
                "failures": result.get("failures", []),
            }
        except _REVISED_WORKER_CAUGHT as exc:
            logger.exception(
                "revised worker failed: task_index=%s subtopic=%s",
                task_index,
                state.get("subtopic"),
            )
            return {
                "revised_indexed_results": [(task_index, "")],
                "add_doc_list": [],
                "failures": [
                    FailureRecord(
                        task_label=f"revised:{task_index}",
                        message=str(exc),
                        kind="execute",
                        traceback_digest=_compute_traceback_digest(exc),
                    )
                ],
            }

    async def revised_reduce_node(
        self, state: DeepResearchState
    ) -> dict[str, Any]:
        """Project ``revised_indexed_results`` into the output channels.

        Sorts the accumulated ``(task_index, content)`` tuples by
        task_index so concurrent worker completion order does not
        affect downstream dimension ordering. Empty-string sentinels
        (written by the worker on exception) are replaced with the
        original draft for that dimension, matching the legacy
        ``revise_node`` substitution at ``report.py`` lines 159-165.

        Writes BOTH the flat ``revised_contents`` list and the legacy
        ``revised_reports`` list of ``{"subtopic", "revised_report"}``
        dicts so existing readers in ``summary.py`` keep working
        without migration.
        """
        emit_progress(
            "revising",
            len(state.get("revised_indexed_results", [])),
            total=len(state.get("research_dimensions", [])),
            detail="revising review dimensions",
        )
        indexed = sorted(state["revised_indexed_results"], key=lambda t: t[0])
        drafts = state["draft_contents"]
        dimensions = state["research_dimensions"]
        revised_contents = [
            (content if content else drafts[idx]) for idx, content in indexed
        ]
        revised_reports = [
            {
                "subtopic": dimensions[idx],
                "revised_report": revised_contents[idx],
            }
            for idx in range(len(revised_contents))
        ]
        return {
            "revised_contents": revised_contents,
            "revised_reports": revised_reports,
        }

    def route_revised_tasks(self, state: DeepResearchState) -> list[Send]:
        """Build N Send payloads, one per dimension under revision.

        Each payload carries the per-worker ``task_index``, the
        upstream ``subtopic`` (used by the worker's failure logging),
        the prior ``draft_content`` and ``review_content`` strings,
        and the shared ``raw_doc_list`` snapshot the worker forwards
        to ``self._feedback_rag``.
        """
        dimensions = state["research_dimensions"]
        drafts = state["draft_contents"]
        reviews = state["review_contents"]
        raw_doc_list = state["all_raw_doc_list"]
        return [
            Send(
                "revised_worker_node",
                {
                    "task_index": i,
                    "subtopic": dimensions[i],
                    "draft_content": drafts[i],
                    "review_content": reviews[i],
                    "raw_doc_list": raw_doc_list,
                },
            )
            for i in range(len(dimensions))
        ]

    def initial_state(
        self,
        user_query: str,
        obs_file_list: list[str] | None = None,
    ) -> DeepResearchState:
        """Build the graph's initial state dict from wrapper arguments.

        Factored out of :meth:`arun` so the streaming seam can seed the
        same ~40-field state the blocking path invokes the graph with,
        without re-inlining the literal. The returned shape is
        byte-identical to the dict ``arun`` previously built inline.
        Public (not underscore-private) so the module-level
        ``review_stream_target`` accessor can reuse it without a
        protected-member access.

        Args:
            user_query: Research question to expand into a review.
            obs_file_list: Optional OBS files to include as context.

        Returns:
            The initial ``DeepResearchState`` dict.
        """
        initial_state: DeepResearchState = {
            "original_user_query": user_query,
            "user_query": "",
            "obs_file_list": obs_file_list or [],
            "upload_context": "",
            "total_length": 0,
            "research_dimensions": [],
            "all_raw_doc_list": [],
            "dimension_params": [],
            "draft_contents": [],
            "review_contents": [],
            "revised_reports": [],
            "add_doc_list": [],
            "summary_content": "",
            "final_response": {},
            # Inherited from ParallelDispatchState
            "analysis_type": "",
            "task_index": None,
            "task_ids": {},
            "completed_count": 0,
            "error": None,
            "failures": [],
            "interop": [],
            "degraded_interop": False,
            # Single-shot chat-mount fields (Step 6.2 pattern)
            "chat_payload": None,
            "chat_response": None,
            "pending_post": None,
            "ordered_doc_list": None,
            # Send-payload transient fields
            "subtopic": None,
            "knowledge": None,
            "dimension": None,
            "review_draft": None,
            "original_draft": None,
            "review_feedback": None,
            "knowledge_payload": None,
            "draft_content": None,
            "review_content": None,
            "raw_doc_list": None,
            # Fan-out indexed_results accumulators
            "retrieve_indexed_results": [],
            "draft_indexed_results": [],
            "review_indexed_results": [],
            "revised_indexed_results": [],
            # Fan-out final ordered outputs
            "revised_contents": [],
            # Human-in-the-loop approval
            "approval_pending": False,
            "approval_decision": {},
            "a2ui_round": 0,
        }
        return initial_state

    async def arun(
        self,
        user_query: str,
        obs_file_list: list[str] | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute the DeepResearchAgent workflow.

        Args:
            user_query: Research question to expand into a literature review.
            obs_file_list: Optional OBS files to include as source context.
            thread_id: Optional LangGraph checkpoint thread id.

        Returns:
            Chat-completions-style final response payload with review text,
            ordered references, and follow-up questions.
        """
        initial_state = self.initial_state(user_query, obs_file_list)
        final_state = await ainvoke_graph(
            self.app, initial_state, thread_id=thread_id
        )
        return merge_intermediate_state(final_state)


async def review_agent_function(
    user_query: str,
    obs_file_list: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run the LangGraph deep research review workflow.

    Args:
        user_query: Research question to expand into a literature review.
        obs_file_list: Optional OBS files to include as source context.
        **kwargs: Optional chat, retrieval, upload, retry, credential, and
            cache-fingerprint overrides.

    Returns:
        Chat-completions-style final response payload from DeepResearchAgent.
    """
    review_config = copy_config_with_overrides(
        REVIEW_CONFIG,
        kwargs,
        REVIEW_CONFIG_FIELD_MAP,
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        kwargs,
        field_map=REVIEW_SENSITIVE_FIELD_MAP,
        secret_field_map=REVIEW_SECRET_FIELD_MAP,
    )
    agent = get_cached_agent(
        "DeepResearchAgent",
        lambda: DeepResearchAgent(
            review_config=review_config,
            sensitive_config=sensitive_config,
            knowledge_agent=KnowledgeAgent(
                knowledge_config=review_config,
                sensitive_config=sensitive_config,
            ),
        ),
        agent_fingerprint_values(
            review_config=review_config,
            sensitive_config=sensitive_config,
        ),
    )
    return await agent.arun(
        user_query=user_query,
        obs_file_list=obs_file_list or [],
    )


def review_stream_target(
    user_query: str,
    obs_file_list: list[str] | None = None,
) -> tuple[Any, DeepResearchState]:
    """Return the cached DeepResearchAgent app + seeded streaming state.

    Mirrors ``review_agent_function``'s acquisition EXACTLY (same
    no-override config copies and the same ``knowledge_agent`` kwarg)
    so the registry fingerprint — and thus the cached instance — is
    identical to a no-override ``review_agent_function`` call. That
    lets a streaming request share one compiled graph with the
    blocking path.

    Args:
        user_query: Research question to expand into a literature review.
        obs_file_list: Optional OBS files to include as source context.

    Returns:
        Tuple of the compiled graph app and its initial state dict.
    """
    review_config = copy_config_with_overrides(
        REVIEW_CONFIG,
        {},
        REVIEW_CONFIG_FIELD_MAP,
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        {},
        field_map=REVIEW_SENSITIVE_FIELD_MAP,
        secret_field_map=REVIEW_SECRET_FIELD_MAP,
    )
    agent = get_cached_agent(
        "DeepResearchAgent",
        lambda: DeepResearchAgent(
            review_config=review_config,
            sensitive_config=sensitive_config,
            knowledge_agent=KnowledgeAgent(
                knowledge_config=review_config,
                sensitive_config=sensitive_config,
            ),
        ),
        agent_fingerprint_values(
            review_config=review_config,
            sensitive_config=sensitive_config,
        ),
    )
    return agent.app, agent.initial_state(user_query, obs_file_list)

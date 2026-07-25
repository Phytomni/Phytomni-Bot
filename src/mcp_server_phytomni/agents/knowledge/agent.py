# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Knowledge agent with retrieval and RAG-based synthesis.

Classes: KnowledgeAgent. The ``KnowledgeAgentState`` symbol is now
defined in :mod:`.state` and re-exported here for back compatibility.
Functions: multi_retrieve, multi_retrieve_generate, rerank, retrieve,
    retrieve_generate.
"""

from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from ...common.docs import (
    format_retrieved_doc_context,
    format_upload_context,
)
from ...common.prompts import get_prompt
from ...common.responses import message_content, parse_follow_up_questions
from ...config.defaults import KnowledgeConfig
from ...config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    OBS_TRANSFER_CONFIG_FIELD_MAP,
    RETRIEVAL_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...config.settings import SensitiveConfig, get_sensitive_config
from ...graphs.chat_adapters import build_chat_input, build_chat_kwargs_for
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
from ...runtime.locale import SupportedLocale
from ...runtime.memory import MemoryGraphContext
from ...storage.downloads import download_list_convert
from ..shared.chat_subgraph import (
    make_chat_after_router,
    mount_chat_node,
)
from ..shared.intermediate_state import merge_intermediate_state
from ..shared.memory_context import memory_context_for_graph
from ..shared.options import resolve_agent_locale
from .retrieval import multi_retrieve, rerank, retrieve
from .state import (
    KnowledgeAgentState,
    KnowledgeInput,
    KnowledgeOutput,
    KnowledgeState,
)

KNOWLEDGE_CONFIG = KnowledgeConfig()
RETRIEVE_CACHE_TTL = 300

KNOWLEDGE_CONFIG_FIELD_MAP = {
    "repo_id": "REPO_ID",
    "page_size": "PAGE_SIZE",
    **RETRIEVAL_CONFIG_FIELD_MAP,
    **CHAT_COMPLETION_CONFIG_FIELD_MAP,
    **OBS_TRANSFER_CONFIG_FIELD_MAP,
    **RETRY_CONFIG_FIELD_MAP,
}
KNOWLEDGE_SENSITIVE_FIELD_MAP = {
    "base_url": "BASE_URL",
    "model": "MODEL_ID",
}
KNOWLEDGE_SECRET_FIELD_MAP = {
    "api_key": "API_KEY",
    "access_key_id": "ACCESS_KEY_ID",
    "secret_access_key": "SECRET_ACCESS_KEY",
}
__all__ = [
    "KnowledgeAgent",
    "knowledge_stream_target",
    "multi_retrieve",
    "multi_retrieve_generate",
    "rerank",
    "retrieve",
    "retrieve_generate",
]


class KnowledgeAgent:
    """A LangGraph-based agent for knowledge base retrieval and generation.

    This agent orchestrates a workflow that processes user queries,
    retrieves relevant documents from a knowledge base, and generates
    responses using an LLM. It supports optional file uploads via OBS
    and can generate follow-up questions based on the initial response.

    The workflow graph processes a query through these stages:
        1. process_files_node: Downloads and parses uploaded OBS files.
        2. retrieve_node: Retrieves and reranks documents.
        3. generate prep/post pair: Generates a response using
           retrieved context.
        4. follow_up prep/post pair: Generates suggested follow-up
           questions.

    The chat calls split into prep + post pairs surrounding a single
    shared ``chat`` node so LangGraph's xray rendering can inline the
    chat subgraph in the consumer's graph.

    Args:
        checkpointer: A LangGraph checkpointer for state persistence.
                      Defaults to a fresh MemorySaver instance.
        knowledge_config: Configuration for knowledge base retrieval.
                          Defaults to the global KNOWLEDGE_CONFIG instance.
        sensitive_config: Configuration for sensitive data (e.g., credentials).
                          Defaults to the cached ``get_sensitive_config()``
                          instance when ``None``.

    Attributes:
        knowledge_config: The knowledge configuration instance.
        sensitive_config: The sensitive configuration instance.
        checkpointer: The checkpointer for state persistence.
        app: The compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver | None = None,
        knowledge_config=KNOWLEDGE_CONFIG,
        sensitive_config: SensitiveConfig | None = None,
    ):
        """Initialize the KnowledgeAgent and build the graph."""
        self.knowledge_config = knowledge_config
        self.sensitive_config = sensitive_config or get_sensitive_config()
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.app = self._build_graph()

    def _build_graph(self):
        """Build and compile the LangGraph StateGraph workflow.

        Splits ``generate_node`` and ``follow_up_node`` into prep +
        post pairs surrounding a single shared chat node registered
        via ``add_node`` with a conditional router that reads
        ``pending_post`` to direct the chat output back to the
        correct post node.
        """
        workflow = StateGraph(
            state_schema=KnowledgeState,
            context_schema=MemoryGraphContext,
            input_schema=KnowledgeInput,
            output_schema=KnowledgeOutput,
        )
        workflow.add_node("process_files_node", self.process_files_node)
        workflow.add_node("retrieve_node", self.retrieve_node)
        workflow.add_node("generate_prep_node", self.generate_prep_node)
        workflow.add_node("generate_post_node", self.generate_post_node)
        workflow.add_node("follow_up_prep_node", self.follow_up_prep_node)
        workflow.add_node("follow_up_post_node", self.follow_up_post_node)
        mount_chat_node(workflow)
        workflow.add_conditional_edges(
            START,
            make_async_router(self.route_start),
            {
                "process_files_node": "process_files_node",
                "retrieve_node": "retrieve_node",
            },
        )
        workflow.add_edge("process_files_node", "retrieve_node")
        workflow.add_conditional_edges(
            "retrieve_node",
            make_async_router(self.route_after_retrieve),
            {
                "generate_node": "generate_prep_node",
                "__end__": END,
            },
        )
        workflow.add_edge("generate_prep_node", "chat")
        workflow.add_conditional_edges(
            "chat",
            make_async_router(make_chat_after_router()),
            {
                "generate_post_node": "generate_post_node",
                "follow_up_post_node": "follow_up_post_node",
            },
        )
        workflow.add_conditional_edges(
            "generate_post_node",
            make_async_router(self.route_after_generate),
            {
                "follow_up_node": "follow_up_prep_node",
                "__end__": END,
            },
        )
        workflow.add_edge("follow_up_prep_node", "chat")
        workflow.add_edge("follow_up_post_node", END)

        return workflow.compile(checkpointer=self.checkpointer)

    async def process_files_node(self, state: KnowledgeAgentState):
        """Process OBS files and convert them to Markdown context.

        This node downloads files from OBS storage, converts them to text,
        and adds them to the state as upload_context. The content is
        truncated if it exceeds the maximum token limit defined in the
        knowledge configuration.

        Args:
            state: The current workflow state containing obs_file_list.

        Returns:
            A dictionary containing the upload_context key with the
            parsed file contents.
        """
        obs_file_list = state.get("obs_file_list", [])
        upload_context = ""
        total_length = 0

        if obs_file_list:
            access_key_id, secret_access_key = (
                self.sensitive_config.obs_credentials()
            )
            upload_texts = await download_list_convert(
                obs_file_list=obs_file_list,
                server_dir=self.knowledge_config.TEMP_DIR,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                obs_server=self.knowledge_config.OBS_SERVER,
                bucket_name=self.knowledge_config.BUCKET_NAME,
                part_size=self.knowledge_config.PART_SIZE,
                task_num=self.knowledge_config.TASK_NUM,
                max_retries=self.knowledge_config.MAX_RETRIES,
                max_concurrency=self.knowledge_config.MAX_CONCURRENCY,
                max_workers=self.knowledge_config.MAX_WORKERS,
            )
            upload_context, _ = format_upload_context(
                upload_texts,
                max_tokens=self.knowledge_config.MAX_TOKENS,
                initial_length=total_length,
            )

        return {"upload_context": upload_context}

    async def retrieve_node(self, state: KnowledgeAgentState):
        """Retrieve and rerank documents from the knowledge base.

        This node queries multiple knowledge repositories, merges the results,
        and formats them into a context string suitable for the LLM. It uses
        the multi_retrieve function to perform parallel retrieval across
        multiple repositories with reranking based on relevance scores.

        Args:
            state: The current workflow state containing user_query,
                   repo_id_dict, and upload_context.

        Returns:
            A dictionary containing:
                - retrieved_docs: The raw list of retrieved documents.
                - retrieve_context: The formatted context string for the LLM.
        """
        emit_progress("retrieving", 0, detail="querying knowledge base")
        user_query = state["user_query"]
        repo_id_dict = (
            state.get("repo_id_dict") or self.knowledge_config.REPO_ID_DICT
        )
        upload_context = state.get("upload_context", "")

        retrieve_response = await multi_retrieve(
            user_query=user_query,
            retrieve_url=self.knowledge_config.RETRIEVE_URL,
            repo_id_dict=repo_id_dict,
            page_num=self.knowledge_config.PAGE_NUM,
            filter_string=self.knowledge_config.FILTER_STRING,
            scope=self.knowledge_config.SCOPE,
            extra_repo_ids=self.knowledge_config.EXTRA_REPO_IDS,
            rerank_url=self.knowledge_config.RERANK_URL,
            rerank_batch_size=self.knowledge_config.RERANK_BATCH_SIZE,
            score_threshold=self.knowledge_config.SCORE_THRESHOLD,
            top_n=self.knowledge_config.TOP_N,
            timeout=self.knowledge_config.TIMEOUT,
            retriable_codes=self.knowledge_config.RETRIABLE_CODES,
            max_retries=self.knowledge_config.MAX_RETRIES,
        )

        retrieve_context, _ = format_retrieved_doc_context(
            retrieve_response.get("doc_list", []),
            max_tokens=self.knowledge_config.MAX_TOKENS,
            initial_length=len(upload_context),
        )

        return {
            "retrieved_docs": retrieve_response.get("doc_list", []),
            "retrieve_context": retrieve_context,
        }

    async def generate_prep_node(
        self,
        state: KnowledgeAgentState,
        runtime: Runtime[MemoryGraphContext] | None = None,
    ) -> dict[str, Any]:
        """Build the chat payload for the primary generate call.

        Mirrors the prompt-building half of :meth:`generate_node` but
        only emits the ``chat_payload`` plus the ``pending_post``
        sentinel that the after-chat router reads to branch back to
        ``generate_post_node`` once the shared chat subgraph returns.

        Args:
            state: The current workflow state. Reads ``user_query``,
                ``retrieve_context``, and optional ``upload_context``.

        Returns:
            A state delta with the ``ChatInput`` dict under
            ``chat_payload`` and ``"generate_post_node"`` under
            ``pending_post``.
        """
        user_query = state["user_query"]
        retrieve_context = state["retrieve_context"]
        upload_context = state.get("upload_context", "")

        if upload_context:
            chat_query = get_prompt(
                self.knowledge_config.PROMPT_FILE,
                "user/retrieval_file",
                {
                    "retrieve_results": retrieve_context,
                    "upload_context": upload_context,
                    "user_query": user_query,
                },
            )
        else:
            chat_query = get_prompt(
                self.knowledge_config.PROMPT_FILE,
                "user/retrieval",
                {
                    "retrieve_results": retrieve_context,
                    "user_query": user_query,
                },
            )

        memory_context = memory_context_for_graph(
            runtime.context if runtime is not None else None
        )
        if memory_context:
            chat_query = f"{memory_context}\n\n{chat_query}"

        chat_kwargs = build_chat_kwargs_for(
            self.knowledge_config,
            self.sensitive_config,
            locale=state.get("locale"),
        )
        chat_payload = build_chat_input(
            user_query=chat_query, chat_kwargs=chat_kwargs
        )
        return {
            "chat_payload": chat_payload,
            "pending_post": "generate_post_node",
        }

    async def generate_post_node(
        self, state: KnowledgeAgentState
    ) -> dict[str, Any]:
        """Merge retrieved docs into the shared chat subgraph response.

        Mirrors the doc-list-merge half of :meth:`generate_node` but
        reads the chat response from ``state['chat_response']`` (set
        by the shared chat node) instead of awaiting a fresh
        ``phyto_chat`` call. Returns the same ``main_response`` /
        ``final_response`` shape the legacy single-node path emitted.

        Args:
            state: The current workflow state. Reads
                ``retrieved_docs`` and the upstream
                ``chat_response`` written by the shared chat node.

        Returns:
            A state delta with ``main_response`` and
            ``final_response`` carrying the merged doc list payload.
        """
        emit_progress("generating", 0, detail="composing answer")
        phyto_response = dict(state.get("chat_response") or {})
        doc_list_payload = {
            "doc_list": state["retrieved_docs"],
            "total": 10000,
        }

        if (
            phyto_response
            and "choices" in phyto_response
            and len(phyto_response["choices"]) > 0
        ):
            if (
                "message" in phyto_response["choices"][0]
                and phyto_response["choices"][0]["message"] is not None
            ):
                phyto_response["choices"][0]["message"].update(
                    doc_list_payload
                )
            else:
                phyto_response["choices"][0]["message"] = doc_list_payload
        else:
            if not phyto_response:
                phyto_response = {"choices": [{"message": doc_list_payload}]}
            elif "choices" not in phyto_response:
                phyto_response["choices"] = [{"message": doc_list_payload}]
            elif len(phyto_response["choices"]) == 0:
                phyto_response["choices"].append({"message": doc_list_payload})

        return {
            "main_response": phyto_response,
            "final_response": phyto_response,
        }

    async def follow_up_prep_node(
        self,
        state: KnowledgeAgentState,
        runtime: Runtime[MemoryGraphContext] | None = None,
    ) -> dict[str, Any]:
        """Build the chat payload for the follow-up questions call.

        Mirrors the prompt-building half of :meth:`follow_up_node`
        and emits the ``pending_post`` sentinel that routes the
        shared chat node's output to ``follow_up_post_node``.

        Args:
            state: The current workflow state. Reads ``user_query``
                and the primary ``main_response`` whose message
                content seeds the follow-up template.

        Returns:
            A state delta with the ``ChatInput`` dict under
            ``chat_payload`` and ``"follow_up_post_node"`` under
            ``pending_post``.
        """
        user_query = state["user_query"]
        phyto_response = state["main_response"]
        system_response_text = message_content(phyto_response)

        follow_up_query = get_prompt(
            self.knowledge_config.PROMPT_FILE,
            "system/follow_up_questions",
            {
                "user_query": user_query,
                "system_response": system_response_text,
            },
        )
        memory_context = memory_context_for_graph(
            runtime.context if runtime is not None else None
        )
        if memory_context:
            follow_up_query = f"{memory_context}\n\n{follow_up_query}"

        chat_kwargs = build_chat_kwargs_for(
            self.knowledge_config,
            self.sensitive_config,
            locale=state.get("locale"),
        )
        chat_payload = build_chat_input(
            user_query=follow_up_query, chat_kwargs=chat_kwargs
        )
        return {
            "chat_payload": chat_payload,
            "pending_post": "follow_up_post_node",
        }

    async def follow_up_post_node(
        self, state: KnowledgeAgentState
    ) -> dict[str, Any]:
        """Parse follow-up questions and merge them into the primary turn.

        Mirrors the parse + mutate half of :meth:`follow_up_node` and
        reads the shared chat subgraph's return from
        ``state['chat_response']`` instead of awaiting a fresh call.

        Args:
            state: The current workflow state. Reads ``main_response``
                (the merged primary turn) and the upstream
                ``chat_response`` written by the shared chat node.

        Returns:
            A state delta with the parsed ``follow_up_questions``
            list and the ``final_response`` that now carries the
            list embedded on the primary message.
        """
        follow_up_response = state.get("chat_response") or {}
        phyto_response = state["main_response"]

        follow_up_list = parse_follow_up_questions(
            message_content(follow_up_response)
        )

        phyto_response["choices"][0]["message"].update(
            {"follow_up_questions": follow_up_list}
        )

        return {
            "follow_up_questions": follow_up_list,
            "final_response": phyto_response,
        }

    def route_start(
        self, state: KnowledgeAgentState
    ) -> Literal["process_files_node", "retrieve_node"]:
        """Route from the START node based on whether files are uploaded.

        This method determines the first node to execute based on the
        presence of user-uploaded OBS files.

        Args:
            state: The current workflow state.

        Returns:
            "process_files_node" if files are uploaded, otherwise
            "retrieve_node".
        """
        obs_file_list = state.get("obs_file_list")
        if obs_file_list and len(obs_file_list) > 0:
            return "process_files_node"
        return "retrieve_node"

    def route_after_retrieve(
        self, state: KnowledgeAgentState
    ) -> Literal["generate_node", "__end__"]:
        """Route after the retrieve node based on generation flag.

        This method determines whether to proceed to the generate node
        or end the workflow based on the is_generate flag.

        Args:
            state: The current workflow state.

        Returns:
            "generate_node" if is_generate is True, otherwise "__end__".
        """
        if state["is_generate"]:
            return "generate_node"
        return "__end__"

    def route_after_generate(
        self, state: KnowledgeAgentState
    ) -> Literal["follow_up_node", "__end__"]:
        """Route after the generate node based on follow-up flag.

        This method determines whether to proceed to the follow-up node
        or end the workflow based on the is_follow_up flag.

        Args:
            state: The current workflow state.

        Returns:
            "follow_up_node" if is_follow_up is True, otherwise "__end__".
        """
        if state["is_follow_up"]:
            return "follow_up_node"
        return "__end__"

    def initial_state(
        self,
        user_query: str,
        *,
        obs_file_list: list[str] | None = None,
        repo_id_dict: dict[str, int] | None = None,
        is_generate: bool = True,
        is_follow_up: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build the graph's initial state dict from wrapper arguments.

        Factored out of :meth:`arun` so the streaming seam can seed the
        same 11-field state the blocking path invokes the graph with,
        without re-inlining the literal. The returned shape is
        byte-identical to the dict ``arun`` previously built inline.
        Public (not underscore-private) so the module-level
        ``knowledge_stream_target`` accessor can reuse it without a
        protected-member access.

        Args:
            user_query: The user's natural language query.
            obs_file_list: Optional OBS file paths to upload; ``None``
                becomes an empty list.
            repo_id_dict: Optional repo-id to page-size mapping.
            is_generate: Whether to generate a response after retrieval.
            is_follow_up: Whether to generate follow-up questions.

        Returns:
            The initial LangGraph state dict.
        """
        return {
            "user_query": user_query,
            "locale": resolve_agent_locale(kwargs.get("locale")),
            "obs_file_list": obs_file_list or [],
            "repo_id_dict": repo_id_dict,
            "upload_context": "",
            "retrieved_docs": [],
            "retrieve_context": "",
            "main_response": {},
            "is_generate": is_generate,
            "is_follow_up": is_follow_up,
            "follow_up_questions": [],
            "final_response": {},
        }

    async def arun(
        self,
        user_query: str,
        **kwargs: Any,
    ):
        """Execute the KnowledgeAgent workflow.

        This is the main entry point for invoking the agent. It initializes
        the state with the user's query and optional parameters, then runs
        the LangGraph workflow.

        Args:
            user_query: The user's natural language query.
            obs_file_list: Optional list of OBS file paths to upload.
            repo_id_dict: Optional dictionary mapping repo names to IDs.
            is_generate: Whether to generate a response. Defaults to True.
            is_follow_up: Whether to generate follow-up questions. Defaults
                to True.
            thread_id: Optional thread ID for state persistence. If not
                provided, a new UUID will be generated.

        Returns:
            The final response dictionary containing the LLM response
            and optionally the doc_list and follow_up_questions.
        """
        obs_file_list = kwargs.get("obs_file_list")
        repo_id_dict = kwargs.get("repo_id_dict")
        is_generate = kwargs.get("is_generate", True)
        is_follow_up = kwargs.get("is_follow_up", True)
        initial_state = self.initial_state(
            user_query,
            obs_file_list=obs_file_list,
            repo_id_dict=repo_id_dict,
            is_generate=is_generate,
            is_follow_up=is_follow_up,
            locale=kwargs.get("locale"),
        )

        final_state = await ainvoke_graph(
            self.app,
            initial_state,
            thread_id=kwargs.get("thread_id"),
        )

        if not is_generate:
            return final_state["retrieved_docs"]
        return merge_intermediate_state(final_state)


def _knowledge_config_with_overrides(**kwargs: Any):
    """Build a KnowledgeConfig copy from compatibility wrapper arguments."""
    return copy_config_with_overrides(
        KNOWLEDGE_CONFIG,
        kwargs,
        KNOWLEDGE_CONFIG_FIELD_MAP,
    )


def _knowledge_sensitive_config_with_overrides(**kwargs: Any):
    """Build a SensitiveConfig copy from compatibility wrapper arguments."""
    return copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        kwargs,
        field_map=KNOWLEDGE_SENSITIVE_FIELD_MAP,
        secret_field_map=KNOWLEDGE_SECRET_FIELD_MAP,
    )


async def multi_retrieve_generate(
    user_query: str,
    obs_file_list: list[str] | None = None,
    repo_id_dict: dict[str, int] | None = None,
    is_generate: bool = True,
    is_follow_up: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compatibility wrapper around the LangGraph-based KnowledgeAgent.

    Args:
        user_query: User question or instruction for retrieval generation.
        obs_file_list: Optional OBS paths for uploaded context files.
        repo_id_dict: Optional mapping of repository IDs to page sizes.
        is_generate: Whether to generate an answer after retrieval.
        is_follow_up: Whether to generate follow-up questions.
        **kwargs: Keyword-compatible config and sensitive overrides.

    Returns:
        KnowledgeAgent final response or retrieved documents.
    """
    effective_locale = resolve_agent_locale(kwargs.get("locale"))
    knowledge_config = _knowledge_config_with_overrides(**kwargs)
    sensitive_config = _knowledge_sensitive_config_with_overrides(**kwargs)
    agent = get_cached_agent(
        "KnowledgeAgent",
        lambda: KnowledgeAgent(
            knowledge_config=knowledge_config,
            sensitive_config=sensitive_config,
        ),
        agent_fingerprint_values(
            knowledge_config=knowledge_config,
            sensitive_config=sensitive_config,
        ),
    )
    return await agent.arun(
        user_query=user_query,
        obs_file_list=obs_file_list or [],
        repo_id_dict=repo_id_dict,
        is_generate=is_generate,
        is_follow_up=is_follow_up,
        locale=effective_locale,
    )


def knowledge_stream_target(
    user_query: str,
    obs_file_list: list[str] | None = None,
    locale: SupportedLocale | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Return the cached KnowledgeAgent app + seeded streaming state.

    Acquires the SAME cached agent ``multi_retrieve_generate`` uses with
    DEFAULT config (no overrides), so a streaming request shares one
    compiled graph instance with a no-override blocking call. Streaming
    always wants the generated answer plus follow-ups, so ``repo_id_dict``
    stays ``None`` and ``is_generate`` / ``is_follow_up`` default ``True``
    — the exact shape a no-override ``multi_retrieve_generate`` seeds.

    Args:
        user_query: The user's natural language query.
        obs_file_list: Optional OBS file paths to combine with retrieval.

    Returns:
        Tuple of the compiled graph app and its initial state dict.
    """
    config = _knowledge_config_with_overrides()
    sensitive = _knowledge_sensitive_config_with_overrides()
    agent = get_cached_agent(
        "KnowledgeAgent",
        lambda: KnowledgeAgent(
            knowledge_config=config,
            sensitive_config=sensitive,
        ),
        agent_fingerprint_values(
            knowledge_config=config,
            sensitive_config=sensitive,
        ),
    )
    return agent.app, agent.initial_state(
        user_query,
        obs_file_list=obs_file_list,
        locale=locale,
    )


async def retrieve_generate(
    user_query: str,
    repo_id: str = KNOWLEDGE_CONFIG.REPO_ID,
    page_size: int = KNOWLEDGE_CONFIG.PAGE_SIZE,
    obs_file_list: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compatibility wrapper for single-repository retrieve and generate.

    Args:
        user_query: User question or instruction for retrieval generation.
        repo_id: Repository ID to search.
        page_size: Number of documents requested from the repository.
        obs_file_list: Optional OBS paths for uploaded context files.
        **kwargs: Keyword-compatible config and sensitive overrides.

    Returns:
        KnowledgeAgent final response for the selected repository.
    """
    return await multi_retrieve_generate(
        user_query=user_query,
        obs_file_list=obs_file_list or [],
        repo_id_dict={repo_id: page_size},
        **kwargs,
    )


def response_to_string(phyto_response: dict) -> str:
    """Convert a RAG response to a formatted string with references.

    This function extracts the generated content and document list from a
    retrieval-augmented generation response and formats it as a readable
    string with numbered references.

    Args:
        phyto_response: A dictionary containing the response from a RAG
            operation. Expected to have the structure returned by
            `multi_retrieve_generate`, with 'choices' containing message
            content and doc_list.

    Returns:
        A formatted string containing the generated content followed by
        a numbered reference list of the source documents.

    Examples:
        >>> response = {
        ...     'choices': [{
        ...         'message': {
        ...             'content': 'Photosynthesis is...',
        ...             'doc_list': [
        ...                 {'title': 'Plant Biology.pdf'},
        ...                 {'title': 'Botany Research'}
        ...             ]
        ...         }
        ...     }]
        ... }
        >>> result = response_to_string(response)
        >>> print(result)
        Photosynthesis is...

        ## Reference:
        [1] Plant Biology
        [2] Botany Research
    """
    content = ""
    doc_list = []

    if (
        phyto_response
        and "choices" in phyto_response
        and len(phyto_response["choices"]) > 0
    ):
        choice = phyto_response["choices"][0]
        if "message" in choice and choice["message"] is not None:
            message = choice["message"]
            content = message.get("content", "")
            doc_list = message.get("doc_list", [])

    doc_string = ""
    for doc_id, doc in enumerate(doc_list):
        title = doc.get("title", "") if doc is not None else ""
        if title:
            if title[-3:] in ("pdf", "PDF"):
                doc_string += f"[{doc_id+1}] " + title[:-4] + "\n\n"
            else:
                doc_string += f"[{doc_id+1}] " + title + "\n\n"
    return content + "\n\n## Reference:\n\n" + doc_string

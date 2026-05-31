# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""NL2SQL agent for natural language database queries.

Classes: DataAgent. ``DataAgentState`` is defined in :mod:`.state`
and re-exported here for back compatibility.
Functions: rewrite_nl2sql, retrieve_and_generate.
"""

import logging
from typing import Any, Dict, Literal, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...common.docs import format_retrieved_doc_fragment
from ...common.prompts import get_prompt
from ...config.defaults import DataConfig
from ...config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    NL2SQL_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...config.settings import SensitiveConfig, get_sensitive_config
from ...graphs.data_to_chat_adapters import (
    build_data_chat_input,
    build_data_chat_kwargs,
    extract_chat_response,
)
from ...runtime.agent_registry import (
    agent_fingerprint_values,
    get_cached_agent,
)
from ...runtime.langgraph_runner import ainvoke_graph, ensure_checkpointer
from ..chat.service import _cached_chat_app, phyto_chat
from ..knowledge.retrieval import retrieve
from ..shared.intermediate_state import merge_intermediate_state
from .nl2sql import (
    Nl2SqlRequest,
    _default_dialog_id,
    execute_nl2sql_request,
)
from .state import DataAgentState, DataInput, DataOutput, DataState

logger = logging.getLogger(__name__)

DATA_CONFIG = DataConfig()

DATA_CONFIG_FIELD_MAP = {
    "retrieve_url": "RETRIEVE_URL",
    "data_repo_id": "DATA_REPO_ID",
    "page_num": "PAGE_NUM",
    "page_size": "DATA_PAGE_SIZE",
    "filter_string": "FILTER_STRING",
    "scope": "SCOPE",
    "rerank_url": "RERANK_URL",
    "rerank_batch_size": "RERANK_BATCH_SIZE",
    "score_threshold": "SCORE_THRESHOLD",
    **CHAT_COMPLETION_CONFIG_FIELD_MAP,
    **RETRY_CONFIG_FIELD_MAP,
    **NL2SQL_CONFIG_FIELD_MAP,
}
DATA_SENSITIVE_FIELD_MAP = {
    "base_url": "BASE_URL",
    "model": "MODEL_ID",
}
DATA_SECRET_FIELD_MAP = {"api_key": "API_KEY"}


async def rewrite_nl2sql(
    user_query: str,
    is_rewrite: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph-based DataAgent.

    Args:
        user_query: Natural-language database question.
        is_rewrite: Whether to retrieve scenarios and rewrite before NL2SQL.
        **kwargs: Keyword-compatible config and sensitive overrides.

    Returns:
        Final DataAgent response dictionary from the LangGraph workflow.
    """
    active_dialog_id = kwargs.get("dialog_id") or _default_dialog_id()
    arguments = {**kwargs, "dialog_id": active_dialog_id}
    data_config = copy_config_with_overrides(
        DATA_CONFIG,
        arguments,
        DATA_CONFIG_FIELD_MAP,
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        arguments,
        field_map=DATA_SENSITIVE_FIELD_MAP,
        secret_field_map=DATA_SECRET_FIELD_MAP,
    )
    agent = get_cached_agent(
        "DataAgent",
        lambda: DataAgent(
            data_config=data_config,
            sensitive_config=sensitive_config,
        ),
        agent_fingerprint_values(
            data_config=data_config,
            sensitive_config=sensitive_config,
        ),
    )
    return await agent.arun(
        user_query=user_query,
        is_rewrite=is_rewrite,
        thread_id=active_dialog_id,
    )


class DataAgent:
    """A LangGraph-based agent for database querying via natural language.

    This agent orchestrates a workflow that converts natural language queries
    into SQL queries against a database. It retrieves relevant database
    scenarios, rewrites the query using an LLM for better SQL generation,
    and executes the resulting query.

    The workflow graph consists of three main nodes:
        1. retrieve_node: Retrieves relevant database scenarios for context.
        2. rewrite_node: Rewrites the query using an LLM for SQL generation.
        3. search_node: Executes NL2SQL conversion and queries the database.

    Args:
        checkpointer: A LangGraph checkpointer for state persistence.
                      Defaults to a fresh MemorySaver instance.
        data_config: Configuration for data retrieval and NL2SQL.
                     Defaults to the global DATA_CONFIG instance.
        sensitive_config: Configuration for sensitive data (e.g., API keys).
                          Defaults to the cached ``get_sensitive_config()``
                          instance when ``None``.

    Attributes:
        data_config: The data configuration instance.
        sensitive_config: The sensitive configuration instance.
        checkpointer: The checkpointer for state persistence.
        app: The compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        data_config=DATA_CONFIG,
        sensitive_config: Optional[SensitiveConfig] = None,
    ):
        """Initialize the DataAgent with configuration and build the graph."""
        self.data_config = data_config
        self.sensitive_config = sensitive_config or get_sensitive_config()
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.app = self._build_graph()

    def _build_graph(self):
        """Build and compile the LangGraph StateGraph workflow.

        This method constructs the workflow graph by adding nodes and
        defining the sequential edges between them. The resulting graph
        orchestrates the retrieve -> rewrite -> search pipeline.

        Returns:
            A compiled StateGraph with checkpointer support.
        """
        workflow = StateGraph(
            state_schema=DataState,
            input_schema=DataInput,
            output_schema=DataOutput,
        )
        workflow.add_node("retrieve_node", self.retrieve_node)
        workflow.add_node("rewrite_node", self.rewrite_node)
        workflow.add_node("search_node", self.search_node)

        workflow.add_conditional_edges(
            START, self.route_start, ["retrieve_node", "search_node"]
        )
        workflow.add_edge("retrieve_node", "rewrite_node")
        workflow.add_edge("rewrite_node", "search_node")
        workflow.add_edge("search_node", END)

        return workflow.compile(checkpointer=self.checkpointer)

    def route_start(
        self, state: DataAgentState
    ) -> Literal["retrieve_node", "search_node"]:
        """Choose whether the workflow should rewrite before SQL search.

        Args:
            state: Current DataAgent workflow state.

        Returns:
            Name of the first graph node to execute.
        """
        if state["is_rewrite"]:
            return "retrieve_node"
        return "search_node"

    async def retrieve_node(self, state: DataAgentState):
        """Retrieve relevant database scenarios and construct a query prompt.

        This node searches the knowledge base for relevant database scenarios
        that can help the LLM better understand the query context. It formats
        the retrieved scenarios into a prompt template for the rewrite node.

        Args:
            state: The current workflow state containing user_query.

        Returns:
            A dictionary containing the retrieve_prompt key with the
            constructed prompt for the next node.
        """
        user_query = state["user_query"]
        retrieve_response = await retrieve(
            user_query=user_query,
            retrieve_url=self.data_config.RETRIEVE_URL,
            repo_id=self.data_config.DATA_REPO_ID,
            page_num=self.data_config.PAGE_NUM,
            page_size=self.data_config.DATA_PAGE_SIZE,
            filter_string=self.data_config.FILTER_STRING,
            scope=self.data_config.SCOPE,
            extra_repo_ids=None,
            rerank_url=self.data_config.RERANK_URL,
            rerank_batch_size=self.data_config.RERANK_BATCH_SIZE,
            score_threshold=self.data_config.SCORE_THRESHOLD,
            timeout=self.data_config.TIMEOUT,
            retriable_codes=self.data_config.RETRIABLE_CODES,
            max_retries=self.data_config.MAX_RETRIES,
        )

        retrieve_results = []
        total_length = 0
        for i, doc in enumerate(retrieve_response.get("doc_list", [])):
            fragment = format_retrieved_doc_fragment(doc, i, label="scenario")
            if total_length + len(fragment) <= DATA_CONFIG.MAX_TOKENS:
                retrieve_results.append(fragment)
                total_length += len(fragment)
            else:
                break

        retrieve_context = "\n\n".join(retrieve_results)
        retrieve_prompt = get_prompt(
            DATA_CONFIG.PROMPT_FILE,
            "user/database",
            {"scenario_prompts": retrieve_context, "user_query": user_query},
        )

        return {"retrieve_prompt": retrieve_prompt}

    async def rewrite_node(self, state: DataAgentState):
        """Rewrite the query using an LLM for better SQL generation.

        This node sends the retrieved scenarios and original query to an LLM,
        which rewrites the query in a format optimized for natural language
        to SQL conversion. This improves the accuracy of the resulting SQL.

        Args:
            state: The current workflow state containing retrieve_prompt.

        Returns:
            A dictionary containing the rewrite_query key with the
            LLM-rewritten query.

        Raises:
            McpError: If the phyto_chat service fails to respond.
        """
        if self.data_config.USE_CHAT_SUBGRAPH:
            chat_kwargs = build_data_chat_kwargs(
                self.data_config, self.sensitive_config
            )
            chat_input = build_data_chat_input(
                user_query=state["retrieve_prompt"],
                chat_kwargs=chat_kwargs,
            )
            chat_output = await _cached_chat_app().ainvoke(chat_input)
            phyto_response = extract_chat_response(chat_output)
        else:
            phyto_response = await phyto_chat(
                user_query=state["retrieve_prompt"],
                prompt_file=self.data_config.PROMPT_FILE,
                prompt_path=self.data_config.PROMPT_PATH,
                api_key=self.sensitive_config.API_KEY.get_secret_value(),
                base_url=self.sensitive_config.BASE_URL,
                model=self.sensitive_config.MODEL_ID,
                frequency_penalty=self.data_config.FREQUENCY_PENALTY,
                n=self.data_config.N,
                presence_penalty=self.data_config.PRESENCE_PENALTY,
                reasoning_effort=self.data_config.REASONING_EFFORT,
                response_format=self.data_config.RESPONSE_FORMAT,
                stream=self.data_config.STREAM,
                temperature=self.data_config.TEMPERATURE,
                top_p=self.data_config.TOP_P,
                user=self.data_config.USER,
                timeout=self.data_config.TIMEOUT,
                retriable_codes=self.data_config.RETRIABLE_CODES,
                max_retries=self.data_config.MAX_RETRIES,
            )

        if (
            not phyto_response
            or "choices" not in phyto_response
            or not phyto_response["choices"]
        ):
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="Failed to get response from phyto_chat service",
                )
            )

        rewrite_query = phyto_response["choices"][0]["message"]["content"]
        return {"rewrite_query": rewrite_query}

    async def search_node(self, state: DataAgentState):
        """Execute the NL2SQL query and return database results.

        This node converts the rewritten natural language query to SQL
        using the nl2sql service and executes it against the database.
        The response may include insights depending on configuration.

        Args:
            state: The current workflow state containing rewrite_query.

        Returns:
            A dictionary containing the final_response key with the
            database query results.
        """
        if state["is_rewrite"]:
            query = state["rewrite_query"]
        else:
            query = state["user_query"]
        request = Nl2SqlRequest.from_kwargs(
            query,
            {
                "database_url": self.data_config.DATABASE_URL,
                "workspace_id": self.data_config.WORKSPACE_ID,
                "subject_id": self.data_config.SUBJECT_ID,
                "dialog_id": (
                    self.data_config.DIALOG_ID or _default_dialog_id()
                ),
                "need_insight": self.data_config.NEED_INSIGHT,
                "simplify_response": self.data_config.SIMPLIFY_RESPONSE,
                "timeout": self.data_config.TIMEOUT,
                "retriable_codes": self.data_config.RETRIABLE_CODES,
                "max_retries": self.data_config.MAX_RETRIES,
            },
        )
        result = await execute_nl2sql_request(request)
        if result is None:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="No response received from SQL database",
                )
            )
        logger.debug("nl2sql response: %s", result)
        return {"final_response": result}

    async def arun(
        self,
        user_query: str,
        is_rewrite: bool = True,
        thread_id: Optional[str] = None,
    ):
        """Execute the DataAgent workflow.

        This is the main entry point for invoking the agent. It initializes
        the state with the user's query, then runs the LangGraph workflow
        to retrieve scenarios, rewrite the query, and execute the database
        query.

        Args:
            user_query: The user's natural language query.
            thread_id: Optional thread ID for state persistence. If not
                       provided, a new UUID will be generated.

        Returns:
            The final response dictionary containing database query results.
        """
        initial_state = {
            "user_query": user_query,
            "is_rewrite": is_rewrite,
            "retrieve_prompt": None,
            "rewrite_query": None,
            "final_response": None,
        }

        final_state = await ainvoke_graph(
            self.app, initial_state, thread_id=thread_id
        )

        return merge_intermediate_state(final_state)

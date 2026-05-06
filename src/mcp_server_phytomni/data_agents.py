# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides functions for interacting with a database using
natural language queries.

It includes functions to convert natural language to SQL, execute the query,
and to first rewrite the natural language query using a language model for
better performance.
"""

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, TypedDict
from uuid import uuid1

from httpx import (
    AsyncClient,
    ConnectError,
    HTTPStatusError,
    Timeout,
    TimeoutException,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from .agent_registry import agent_fingerprint_values, get_cached_agent
from .chat_agents import phyto_chat
from .config.defaults import DataConfig
from .config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from .config.settings import SensitiveConfig
from .knowledge_agents import retrieve
from .langgraph_runner import ainvoke_graph, ensure_checkpointer
from .utils import (
    get_prompt,
    get_token,
    retry_http_status_or_raise,
    retry_network_or_raise,
)

DATA_CONFIG = DataConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()

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
    "database_url": "DATABASE_URL",
    "workspace_id": "WORKSPACE_ID",
    "subject_id": "SUBJECT_ID",
    "dialog_id": "DIALOG_ID",
    "need_insight": "NEED_INSIGHT",
    "simplify_response": "SIMPLIFY_RESPONSE",
}
DATA_SENSITIVE_FIELD_MAP = {
    "base_url": "BASE_URL",
    "model": "MODEL_ID",
}
DATA_SECRET_FIELD_MAP = {"api_key": "API_KEY"}


@dataclass(frozen=True)
class Nl2SqlRequest:
    """Resolved request settings for one NL2SQL call."""

    message_content: str
    database_url: str
    workspace_id: str
    subject_id: str
    dialog_id: str
    need_insight: bool
    simplify_response: bool
    timeout: float
    retriable_codes: tuple[int, ...]
    max_retries: int

    @classmethod
    def from_kwargs(cls, message_content: str, values: Dict[str, Any]):
        """Build request settings from keyword-compatible overrides."""
        retriable_codes = values.get("retriable_codes")
        if retriable_codes is None:
            retriable_codes = DATA_CONFIG.RETRIABLE_CODES
        return cls(
            message_content=message_content,
            database_url=values.get("database_url", DATA_CONFIG.DATABASE_URL),
            workspace_id=values.get("workspace_id", DATA_CONFIG.WORKSPACE_ID),
            subject_id=values.get("subject_id", DATA_CONFIG.SUBJECT_ID),
            dialog_id=values.get("dialog_id") or str(uuid1()),
            need_insight=values.get("need_insight", DATA_CONFIG.NEED_INSIGHT),
            simplify_response=values.get(
                "simplify_response",
                DATA_CONFIG.SIMPLIFY_RESPONSE,
            ),
            timeout=values.get("timeout", DATA_CONFIG.TIMEOUT),
            retriable_codes=tuple(retriable_codes),
            max_retries=values.get("max_retries", DATA_CONFIG.MAX_RETRIES),
        )

    def payload(self) -> Dict[str, Any]:
        """Return the database API JSON payload."""
        return {
            "subject_id": self.subject_id,
            "dialog_id": self.dialog_id,
            "message_content": self.message_content,
            "need_insight": self.need_insight,
            "simplify_response": self.simplify_response,
        }


async def nl2sql(
    message_content: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Convert a natural language query to SQL and execute it."""
    request = Nl2SqlRequest.from_kwargs(message_content, kwargs)
    client_timeout = Timeout(request.timeout, connect=request.timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(request.max_retries + 1):
            try:
                response = await client.post(
                    request.database_url,
                    headers={
                        "X-Auth-Token": await get_token(),
                        "X-Workspace-Id": request.workspace_id,
                        "Content-Type": "application/json",
                    },
                    json=request.payload(),
                    timeout=request.timeout,
                )
                response.raise_for_status()
                return response.json()

            except HTTPStatusError as exc:
                if await retry_http_status_or_raise(
                    exc,
                    attempt=attempt,
                    max_retries=request.max_retries,
                    retriable_codes=request.retriable_codes,
                    message="Failed to query SQL database",
                ):
                    continue

            except (ConnectError, TimeoutException) as exc:
                if await retry_network_or_raise(
                    exc,
                    attempt=attempt,
                    max_retries=request.max_retries,
                ):
                    continue

    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to query SQL database after all retries",
        )
    )


async def rewrite_nl2sql(
    user_query: str,
    is_rewrite: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph-based DataAgent."""
    active_dialog_id = kwargs.get("dialog_id") or str(uuid1())
    arguments = {**kwargs, "dialog_id": active_dialog_id}
    data_config = copy_config_with_overrides(
        DATA_CONFIG,
        arguments,
        DATA_CONFIG_FIELD_MAP,
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        SENSITIVE_CONFIG,
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


class DataAgentState(TypedDict):
    """State schema for the DataAgent LangGraph workflow.

    This TypedDict defines the shared state that flows through each node
    in the DataAgent graph. Each node reads from and writes to this state
    as the graph processes a natural language query for database retrieval.

    Attributes:
        user_query: The user's natural language query.
        is_rewrite: Is rewrite query or not.
        retrieve_prompt: Prompt containing retrieved scenarios.
        rewrite_query: The rewritten query optimized for SQL generation.
        final_response: The final response from the database query execution.
    """

    user_query: str
    is_rewrite: bool
    retrieve_prompt: str
    rewrite_query: str
    final_response: dict


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
                          Defaults to the global SENSITIVE_CONFIG instance.

    Attributes:
        DATA_CONFIG: The data configuration instance.
        SENSITIVE_CONFIG: The sensitive configuration instance.
        checkpointer: The checkpointer for state persistence.
        app: The compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        data_config=DATA_CONFIG,
        sensitive_config=SENSITIVE_CONFIG,
    ):
        """Initialize the DataAgent with configuration and build the graph."""
        self.data_config = data_config
        self.sensitive_config = sensitive_config
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
        workflow = StateGraph(DataAgentState)
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
        """Choose whether the workflow should rewrite before SQL search."""
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
            header = f"[scenario {i+1} begin] {doc['title']}"
            content_field = (
                doc.get("big_content")
                if "big_content" in doc
                else doc.get("content", "")
            )
            body = (
                f"{doc['subtitle']}\n{content_field}"
                if doc.get("subtitle")
                else doc.get("content", "")
            )
            fragment = f"{header}\n{body} [scenario {i+1} end]"
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
        dialog_id = self.data_config.DIALOG_ID
        client_timeout = Timeout(
            self.data_config.TIMEOUT, connect=self.data_config.TIMEOUT
        )
        if state["is_rewrite"]:
            query = state["rewrite_query"]
        else:
            query = state["user_query"]
        response: Any = None
        async with AsyncClient(timeout=client_timeout, verify=False) as client:
            for attempt in range(self.data_config.MAX_RETRIES + 1):
                try:
                    response = await client.post(
                        self.data_config.DATABASE_URL,
                        headers={
                            "X-Auth-Token": await get_token(),
                            "X-Workspace-Id": self.data_config.WORKSPACE_ID,
                            "Content-Type": "application/json",
                        },
                        json={
                            "subject_id": self.data_config.SUBJECT_ID,
                            "dialog_id": (
                                dialog_id if dialog_id else str(uuid1())
                            ),
                            "message_content": query,
                            "need_insight": self.data_config.NEED_INSIGHT,
                            "simplify_response": (
                                self.data_config.SIMPLIFY_RESPONSE
                            ),
                        },
                        timeout=self.data_config.TIMEOUT,
                    )
                    response.raise_for_status()

                except HTTPStatusError as exc:
                    if await retry_http_status_or_raise(
                        exc,
                        attempt=attempt,
                        max_retries=self.data_config.MAX_RETRIES,
                        retriable_codes=self.data_config.RETRIABLE_CODES,
                        message="Failed to query SQL database",
                    ):
                        continue

                except (ConnectError, TimeoutException) as exc:
                    if await retry_network_or_raise(
                        exc,
                        attempt=attempt,
                        max_retries=self.data_config.MAX_RETRIES,
                    ):
                        continue
        if response is None:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="No response received from SQL database",
                )
            )
        print(response.json())
        return {"final_response": response.json()}

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

        return final_state["final_response"]

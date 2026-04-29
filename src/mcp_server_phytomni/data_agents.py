# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides functions for interacting with a database using
natural language queries.

It includes functions to convert natural language to SQL, execute the query,
and to first rewrite the natural language query using a language model for
better performance.
"""
import asyncio
from random import uniform
from typing import Any, Dict, List, Optional, Union, TypedDict, Literal
from uuid import uuid1

from httpx import AsyncClient, ConnectError, HTTPStatusError
from httpx import Timeout, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR

from .chat_agents import phyto_chat
from .config.defaults import DataConfig
from .config.settings import SensitiveConfig
from .knowledge_agents import retrieve
from .utils import get_prompt, get_token

from pydantic import Field
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import AsyncCallbackManagerForRetrieverRun, CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import MemorySaver

dc = DataConfig()
sc = SensitiveConfig().load()


class DataAgentState(TypedDict):
    """State schema for the DataAgent LangGraph workflow.

    This TypedDict defines the shared state that flows through each node
    in the DataAgent graph. Each node reads from and writes to this state
    as the graph processes a natural language query for database retrieval.

    Attributes:
        user_query: The user's natural language query.
        is_rewrite: Is rewrite query or not.
        retrieve_promopt: The constructed prompt containing retrieved scenarios.
        rewrite_query: The rewritten query optimized for SQL generation.
        final_reponse: The final response from the database query execution.
    """
    user_query: str
    is_rewrite: bool
    retrieve_promopt: str
    rewrite_query: str
    final_reponse: dict


class DataAgent:
    """A LangGraph-based agent for database querying via natural language.

    This agent orchestrates a workflow that converts natural language queries
    into SQL queries against a database. It retrieves relevant database
    scenarios, rewrites the query using an LLM for better SQL generation,
    and executes the resulting query.

    The workflow graph consists of three main nodes:
        1. retrieve_node: Retrieves relevant database scenarios for context.
        2. rewrite_node: Rewrites the query using an LLM for SQL generation.
        3. search_node: Executes the NL2SQL conversion and queries the database.

    Args:
        checkpointer: A LangGraph checkpointer for state persistence.
                      Defaults to MemorySaver().
        data_config: Configuration for data retrieval and NL2SQL.
                     Defaults to the global dc instance.
        sensitive_config: Configuration for sensitive data (e.g., API keys).
                          Defaults to the global sc instance.

    Attributes:
        dc: The data configuration instance.
        sc: The sensitive configuration instance.
        checkpointer: The checkpointer for state persistence.
        app: The compiled LangGraph application.
    """

    def __init__(self, checkpointer=MemorySaver(), data_config=dc, sensitive_config=sc):
        """Initialize the DataAgent with configuration and build the graph."""
        self.dc = data_config
        self.sc = sensitive_config
        self.checkpointer = checkpointer
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
            START, 
            self.route_start,
            ["retrieve_node", "search_node"]
        )
        workflow.add_edge("retrieve_node", "rewrite_node")
        workflow.add_edge("rewrite_node", "search_node")
        workflow.add_edge("search_node", END)

        return workflow.compile(checkpointer=self.checkpointer)
    
    def route_start(self, state: DataAgentState) -> Literal["retrieve_node", "search_node"]:
        if state['is_rewrite']:
            return 'retrieve_node'
        return 'search_node'

    async def retrieve_node(self, state: DataAgentState):
        """Retrieve relevant database scenarios and construct a query prompt.

        This node searches the knowledge base for relevant database scenarios
        that can help the LLM better understand the query context. It formats
        the retrieved scenarios into a prompt template for the rewrite node.

        Args:
            state: The current workflow state containing user_query.

        Returns:
            A dictionary containing the retrieve_promopt key with the
            constructed prompt for the next node.
        """
        user_query = state["user_query"]
        retrieve_response = await retrieve(
            user_query=user_query,
            retrieve_url=self.dc.RETRIEVE_URL,
            repo_id=self.dc.DATA_REPO_ID,
            page_num=self.dc.PAGE_NUM,
            page_size=self.dc.DATA_PAGE_SIZE,
            filter_string=self.dc.FILTER_STRING,
            scope=self.dc.SCOPE,
            extra_repo_ids=None,
            rerank_url=self.dc.RERANK_URL,
            rerank_batch_size=self.dc.RERANK_BATCH_SIZE,
            score_threshold=self.dc.SCORE_THRESHOLD,
            timeout=self.dc.TIMEOUT,
            retriable_codes=self.dc.RETRIABLE_CODES,
            max_retries=self.dc.MAX_RETRIES,
        )

        retrieve_results = []
        total_length = 0
        for i, doc in enumerate(retrieve_response.get('doc_list', [])):
            header = f"[scenario {i+1} begin] {doc['title']}"
            content_field = (doc.get('big_content') if 'big_content' in doc
                             else doc.get('content', ''))
            body = (f"{doc['subtitle']}\n{content_field}"
                    if doc.get('subtitle') else doc.get('content', ''))
            fragment = f'{header}\n{body} [scenario {i+1} end]'
            if total_length + len(fragment) <= dc.MAX_TOKENS:
                retrieve_results.append(fragment)
                total_length += len(fragment)
            else:
                break

        retrieve_context = '\n\n'.join(retrieve_results)
        retrieve_prompt = get_prompt(dc.PROMPT_FILE, 'user/database',
                                     {'scenario_prompts': retrieve_context,
                                      'user_query': user_query})

        return {"retrieve_promopt": retrieve_prompt}
    
    async def rewrite_node(self, state: DataAgentState):
        """Rewrite the query using an LLM for better SQL generation.

        This node sends the retrieved scenarios and original query to an LLM,
        which rewrites the query in a format optimized for natural language
        to SQL conversion. This improves the accuracy of the resulting SQL.

        Args:
            state: The current workflow state containing retrieve_promopt.

        Returns:
            A dictionary containing the rewrite_query key with the
            LLM-rewritten query.

        Raises:
            McpError: If the phyto_chat service fails to respond.
        """
        phyto_response = await phyto_chat(
            user_query=state["retrieve_promopt"],
            prompt_file=self.dc.PROMPT_FILE,
            prompt_path=self.dc.PROMPT_PATH,
            api_key=self.sc.API_KEY.get_secret_value(),
            base_url=self.sc.BASE_URL,
            model=self.sc.MODEL_ID,
            frequency_penalty=self.dc.FREQUENCY_PENALTY,
            n=self.dc.N,
            presence_penalty=self.dc.PRESENCE_PENALTY,
            reasoning_effort=self.dc.REASONING_EFFORT,
            response_format=self.dc.RESPONSE_FORMAT,
            stream=self.dc.STREAM,
            temperature=self.dc.TEMPERATURE,
            top_p=self.dc.TOP_P,
            user=self.dc.USER,
            timeout=self.dc.TIMEOUT,
            retriable_codes=self.dc.RETRIABLE_CODES,
            max_retries=self.dc.MAX_RETRIES,
        )

        if (not phyto_response or 'choices' not in phyto_response or
                not phyto_response['choices']):
            raise McpError(ErrorData(
                code=INTERNAL_ERROR,
                message='Failed to get response from phyto_chat service'
            ))

        rewrite_query = phyto_response['choices'][0]['message']['content']
        return {"rewrite_query": rewrite_query}

    async def search_node(self, state: DataAgentState):
        """Execute the NL2SQL query and return database results.

        This node converts the rewritten natural language query to SQL
        using the nl2sql service and executes it against the database.
        The response may include insights depending on configuration.

        Args:
            state: The current workflow state containing rewrite_query.

        Returns:
            A dictionary containing the final_reponse key with the
            database query results.
        """
        dialog_id = self.dc.DIALOG_ID
        client_timeout = Timeout(self.dc.TIMEOUT, connect=self.dc.TIMEOUT)
        if state['is_rewrite']:
            query = state['rewrite_query']
        else:
            query = state['user_query']
        async with AsyncClient(timeout=client_timeout, verify=False) as client:
            for attempt in range(self.dc.MAX_RETRIES + 1):
                try:
                    response = await client.post(
                        self.dc.DATABASE_URL,
                        headers={'X-Auth-Token': await get_token(),
                                'X-Workspace-Id': self.dc.WORKSPACE_ID,
                                'Content-Type': 'application/json'},
                        json={
                            'subject_id': self.dc.SUBJECT_ID,
                            'dialog_id': dialog_id if dialog_id else str(uuid1()),
                            'message_content': query,
                            'need_insight': self.dc.NEED_INSIGHT,
                            'simplify_response': self.dc.SIMPLIFY_RESPONSE,
                        },
                        timeout=self.dc.TIMEOUT,
                    )
                    response.raise_for_status()

                except HTTPStatusError as e:
                    if (
                        hasattr(e, 'response') and
                        e.response is not None and
                        e.response.status_code in self.dc.RETRIABLE_CODES and
                        attempt < self.dc.MAX_RETRIES
                    ):
                        wait_time = (2 ** attempt) + uniform(0, 1)
                        await asyncio.sleep(wait_time)
                        continue
                    raise McpError(ErrorData(
                        code=INTERNAL_ERROR,
                        message=f'Failed to query SQL database: {str(e)}',
                    )) from e

                except (ConnectError, TimeoutException) as e:
                    if attempt < self.dc.MAX_RETRIES:
                        await asyncio.sleep(1.5 ** attempt)
                        continue
                    raise McpError(ErrorData(
                        code=INTERNAL_ERROR,
                        message=f'Network error: {str(e)}'
                    )) from e
        print(response.json())
        return {"final_reponse": response.json()}

    async def arun(self,
                   user_query: str,
                   is_rewrite: bool = True,
                   thread_id: Optional[str] = None):
        """Execute the DataAgent workflow.

        This is the main entry point for invoking the agent. It initializes
        the state with the user's query, then runs the LangGraph workflow
        to retrieve scenarios, rewrite the query, and execute the database query.

        Args:
            user_query: The user's natural language query.
            thread_id: Optional thread ID for state persistence. If not provided,
                       a new UUID will be generated.

        Returns:
            The final response dictionary containing the database query results.
        """
        if not thread_id:
            thread_id = str(uuid1())
        initial_state = {
            "user_query": user_query,
            "is_rewrite": is_rewrite,
            "retrieve_promopt": None,
            "rewrite_query": None, 
            "final_reponse": None
        }

        config = {"configurable": {"thread_id": thread_id}}
        final_state = await self.app.ainvoke(initial_state, config=config)
        
        return final_state["final_reponse"]


# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Knowledge agent with retrieval and RAG-based synthesis.

Classes: KnowledgeAgentState, KnowledgeAgent.
Functions: multi_retrieve, multi_retrieve_generate, rerank, retrieve,
    retrieve_generate.
"""

from typing import Any, Dict, List, Literal, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

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
from ...config.settings import SensitiveConfig
from ...runtime.agent_registry import (
    agent_fingerprint_values,
    get_cached_agent,
)
from ...runtime.langgraph_runner import ainvoke_graph, ensure_checkpointer
from ...storage.downloads import download_list_convert
from ..chat.service import phyto_chat
from .retrieval import multi_retrieve, rerank, retrieve

KNOWLEDGE_CONFIG = KnowledgeConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
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
    "multi_retrieve",
    "multi_retrieve_generate",
    "rerank",
    "retrieve",
    "retrieve_generate",
]


class KnowledgeAgentState(TypedDict):
    """State schema for the KnowledgeAgent LangGraph workflow.

    This TypedDict defines the shared state that flows through each node
    in the KnowledgeAgent graph. Each node reads from and writes to this
    state as the graph processes a user's query.

    Attributes:
        user_query: The user's natural language query.
        obs_file_list: A list of OBS file paths uploaded by the user.
        repo_id_dict: A dictionary mapping repository names to their IDs.
        upload_context: The parsed content from user-uploaded files.
        retrieved_docs: Documents retrieved from the knowledge base.
        retrieve_context: The formatted retrieval context for the LLM.
        main_response: The initial response from the LLM (contains choices).
        is_generate: Whether to generate a response after retrieval.
        is_follow_up: Whether to generate follow-up questions.
        follow_up_questions: A list of suggested follow-up questions.
        final_response: The final merged response returned to the user.
    """

    user_query: str
    obs_file_list: Optional[List[str]]
    repo_id_dict: Optional[Dict[str, int]]
    upload_context: str
    retrieved_docs: List[Dict[str, Any]]
    retrieve_context: str
    main_response: Dict[str, Any]
    is_generate: bool
    is_follow_up: bool
    follow_up_questions: List[dict]
    final_response: Dict[str, Any]


class KnowledgeAgent:
    """A LangGraph-based agent for knowledge base retrieval and generation.

    This agent orchestrates a workflow that processes user queries,
    retrieves relevant documents from a knowledge base, and generates
    responses using an LLM. It supports optional file uploads via OBS
    and can generate follow-up questions based on the initial response.

    The workflow graph consists of four main nodes:
        1. process_files_node: Downloads and parses user-uploaded OBS files.
        2. retrieve_node: Retrieves and reranks documents from knowledge bases.
        3. generate_node: Generates a response using the retrieved context.
        4. follow_up_node: Generates suggested follow-up questions.

    Args:
        checkpointer: A LangGraph checkpointer for state persistence.
                      Defaults to a fresh MemorySaver instance.
        knowledge_config: Configuration for knowledge base retrieval.
                          Defaults to the global KNOWLEDGE_CONFIG instance.
        sensitive_config: Configuration for sensitive data (e.g., credentials).
                          Defaults to the global SENSITIVE_CONFIG instance.

    Attributes:
        KNOWLEDGE_CONFIG: The knowledge configuration instance.
        SENSITIVE_CONFIG: The sensitive configuration instance.
        checkpointer: The checkpointer for state persistence.
        app: The compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        knowledge_config=KNOWLEDGE_CONFIG,
        sensitive_config=SENSITIVE_CONFIG,
    ):
        """Initialize the KnowledgeAgent and build the graph."""
        self.knowledge_config = knowledge_config
        self.sensitive_config = sensitive_config
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.app = self._build_graph()

    def _build_graph(self):
        """Build and compile the LangGraph StateGraph workflow.

        This method constructs the workflow graph by adding nodes,
        defining edges, and setting up conditional routing. The resulting
        graph orchestrates the retrieval and generation pipeline.

        Returns:
            A compiled StateGraph with checkpointer support.
        """
        workflow = StateGraph(KnowledgeAgentState)
        workflow.add_node("process_files_node", self.process_files_node)
        workflow.add_node("retrieve_node", self.retrieve_node)
        workflow.add_node("generate_node", self.generate_node)
        workflow.add_node("follow_up_node", self.follow_up_node)

        workflow.add_conditional_edges(START, self.route_start)
        workflow.add_edge("process_files_node", "retrieve_node")
        workflow.add_conditional_edges(
            "retrieve_node", self.route_after_retrieve
        )
        workflow.add_conditional_edges(
            "generate_node", self.route_after_generate
        )
        workflow.add_edge("follow_up_node", END)

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

    async def generate_node(self, state: KnowledgeAgentState):
        """Generate a response based on retrieved documents and user files.

        This node constructs a prompt using the retrieved context and any
        uploaded file content, then sends it to the LLM for response
        generation.
        The retrieved documents are attached to the response for reference.

        Args:
            state: The current workflow state containing user_query,
                   retrieve_context, upload_context, and retrieved_docs.

        Returns:
            A dictionary containing:
                - main_response: The LLM response with document references.
                - final_response: The same response (may be updated later).
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

        phyto_response = await phyto_chat(
            user_query=chat_query,
            prompt_file=self.knowledge_config.PROMPT_FILE,
            prompt_path=self.knowledge_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.knowledge_config.FREQUENCY_PENALTY,
            n=self.knowledge_config.N,
            presence_penalty=self.knowledge_config.PRESENCE_PENALTY,
            reasoning_effort=self.knowledge_config.REASONING_EFFORT,
            response_format=self.knowledge_config.RESPONSE_FORMAT,
            stream=self.knowledge_config.STREAM,
            temperature=self.knowledge_config.TEMPERATURE,
            top_p=self.knowledge_config.TOP_P,
            user=self.knowledge_config.USER,
            timeout=self.knowledge_config.TIMEOUT,
            retriable_codes=self.knowledge_config.RETRIABLE_CODES,
            max_retries=self.knowledge_config.MAX_RETRIES,
        )

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
            if phyto_response is None:
                phyto_response = {"choices": [{"message": doc_list_payload}]}
            elif "choices" not in phyto_response:
                phyto_response["choices"] = [{"message": doc_list_payload}]
            elif len(phyto_response["choices"]) == 0:
                phyto_response["choices"].append({"message": doc_list_payload})

        return {
            "main_response": phyto_response,
            "final_response": phyto_response,
        }

    async def follow_up_node(self, state: KnowledgeAgentState):
        """Generate suggested follow-up questions.

        This node analyzes the initial LLM response and generates relevant
        follow-up questions that the user might want to ask. Questions are
        parsed from the LLM output and attached to the final response.

        Args:
            state: The current workflow state containing user_query
                   and main_response.

        Returns:
            A dictionary containing:
                - follow_up_questions: A list of suggested questions.
                - final_response: Response with follow-up questions.
        """
        user_query = state["user_query"]
        phyto_response = state["main_response"]
        system_response_text = message_content(phyto_response)

        follow_up_response = await phyto_chat(
            user_query=get_prompt(
                self.knowledge_config.PROMPT_FILE,
                "system/follow_up_questions",
                {
                    "user_query": user_query,
                    "system_response": system_response_text,
                },
            ),
            prompt_file=self.knowledge_config.PROMPT_FILE,
            prompt_path=self.knowledge_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.knowledge_config.FREQUENCY_PENALTY,
            n=self.knowledge_config.N,
            presence_penalty=self.knowledge_config.PRESENCE_PENALTY,
            reasoning_effort=self.knowledge_config.REASONING_EFFORT,
            response_format=self.knowledge_config.RESPONSE_FORMAT,
            stream=self.knowledge_config.STREAM,
            temperature=self.knowledge_config.TEMPERATURE,
            top_p=self.knowledge_config.TOP_P,
            user=self.knowledge_config.USER,
            timeout=self.knowledge_config.TIMEOUT,
            retriable_codes=self.knowledge_config.RETRIABLE_CODES,
            max_retries=self.knowledge_config.MAX_RETRIES,
        )

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
        initial_state = {
            "user_query": user_query,
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

        final_state = await ainvoke_graph(
            self.app,
            initial_state,
            thread_id=kwargs.get("thread_id"),
        )

        if not is_generate:
            return final_state["retrieved_docs"]
        return final_state["final_response"]


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
        SENSITIVE_CONFIG,
        kwargs,
        field_map=KNOWLEDGE_SENSITIVE_FIELD_MAP,
        secret_field_map=KNOWLEDGE_SECRET_FIELD_MAP,
    )


async def multi_retrieve_generate(
    user_query: str,
    obs_file_list: Optional[List[str]] = None,
    repo_id_dict: Optional[Dict[str, int]] = None,
    is_generate: bool = True,
    is_follow_up: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
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
    )


async def retrieve_generate(
    user_query: str,
    repo_id: str = KNOWLEDGE_CONFIG.REPO_ID,
    page_size: int = KNOWLEDGE_CONFIG.PAGE_SIZE,
    obs_file_list: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
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

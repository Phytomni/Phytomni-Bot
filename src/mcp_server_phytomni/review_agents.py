# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""LangGraph-based deep research and literature review generation."""

import asyncio
import json
import re
from dataclasses import dataclass
from json import loads
from typing import Any, Dict, List, Optional, TypedDict, Union

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .agents.chat.service import phyto_chat
from .config.defaults import ReviewConfig
from .config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    OBS_TRANSFER_CONFIG_FIELD_MAP,
    RETRIEVAL_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from .config.settings import SensitiveConfig
from .knowledge_agents import KnowledgeAgent
from .runtime.agent_registry import agent_fingerprint_values, get_cached_agent
from .runtime.langgraph_runner import ainvoke_graph, ensure_checkpointer
from .storage.downloads import download_upload_context
from .utils import (
    get_prompt,
    message_content,
    parse_follow_up_questions,
)

REVIEW_CONFIG = ReviewConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
DEFAULT_ACCESS_KEY_ID, DEFAULT_SECRET_ACCESS_KEY = (
    SENSITIVE_CONFIG.obs_credentials()
)

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

CITATION_PATTERN = (
    r"\[(?:add )?document [^\]]+\]|\[[Ss]?\d+-\d{3}\]|\[[sS]?\d{3}\]"
)


@dataclass(frozen=True)
class SupplementaryResultContext:
    """Context used to format supplementary retrieval snippets."""

    subtopic_idx: int
    add_queries: List[Any]
    add_query_results: List[Any]
    add_doc_list: List[Dict[str, Any]]
    draft_content: str


@dataclass
class RetrievalAccumulator:
    """Mutable counters for bounded document retrieval."""

    raw_docs: List[Dict[str, Any]]
    current_length: int
    file_id: int = 0


@dataclass
class SupplementaryCounters:
    """Mutable counters for supplementary snippet formatting."""

    file_id: int = 0
    total_length: int = 0


@dataclass(frozen=True)
class SupplementaryFormatState:
    """Formatting limits and counters for supplementary snippets."""

    query_length: int
    counters: SupplementaryCounters


def _extract_json_object(text: str) -> Dict[str, Any]:
    """Extract a JSON object from model output."""
    start_index = text.find("{")
    end_index = text.rfind("}") + 1
    if start_index == -1 or end_index <= start_index:
        return {}
    try:
        parsed = loads(text[start_index:end_index])
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _doc_content(doc: Dict[str, Any]) -> str:
    """Return the best available document text field."""
    return str(doc.get("big_content") or doc.get("content") or "")


def _format_doc_fragment(doc: Dict[str, Any], doc_id: str) -> str:
    """Format a retrieved document as a prompt fragment."""
    title = doc.get("title", "")
    subtitle = doc.get("subtitle", "")
    content = _doc_content(doc)
    body = f"{subtitle}\n{content}" if subtitle else content
    return f"[{doc_id} begin] {title}\n{body} [{doc_id} end]"


def _normalize_citation_id(raw_id: str) -> str:
    """Normalize short citation aliases to internal document IDs."""
    if re.match(r"^[Ss]\d+-\d{3}$", raw_id):
        return f"add document {raw_id.upper()}"
    if re.match(r"^\d{3}$", raw_id):
        return f"document {raw_id}"
    if re.match(r"^[Ss]\d{3}$", raw_id):
        return f"document {raw_id[1:]}"
    return raw_id


def _renumber_citations(
    summary_text: str, doc_list: List[Dict[str, Any]]
) -> tuple[str, List[Dict[str, Any]]]:
    """Convert internal citation IDs to public [document:N] references."""
    final_doc_lookup = {
        str(doc.get("doc_id", "")): doc
        for doc in doc_list
        if doc.get("doc_id")
    }
    raw_tags = list(dict.fromkeys(re.findall(CITATION_PATTERN, summary_text)))
    tag_to_number: Dict[str, int] = {}
    ordered_doc_list: List[Dict[str, Any]] = []
    current_ref_number = 1

    for tag in raw_tags:
        norm_id = _normalize_citation_id(tag.strip("[]"))
        norm_tag = f"[{norm_id}]"
        if norm_tag in tag_to_number:
            continue
        tag_to_number[norm_tag] = current_ref_number
        if norm_id in final_doc_lookup:
            doc_copy = final_doc_lookup[norm_id].copy()
            doc_copy["doc_id"] = current_ref_number
            ordered_doc_list.append(doc_copy)
        else:
            ordered_doc_list.append(
                {
                    "doc_id": current_ref_number,
                    "title": "Unknown Document",
                    "content": "Content missing due to invalid reference.",
                }
            )
        current_ref_number += 1

    def replace_with_number(match: re.Match[str]) -> str:
        norm_id = _normalize_citation_id(match.group(0).strip("[]"))
        ref_number = tag_to_number.get(f"[{norm_id}]", "?")
        return f"[document:{ref_number}]"

    formatted_text = re.sub(
        CITATION_PATTERN, replace_with_number, summary_text
    )

    def sort_citation_block(match: re.Match[str]) -> str:
        nums = [
            int(num)
            for num in re.findall(r"\[document:(\d+)\]", match.group(0))
        ]
        return "".join(f"[document:{num}]" for num in sorted(set(nums)))

    formatted_text = re.sub(
        r"(?:\[document:\d+\][\s,]*){2,}",
        sort_citation_block,
        formatted_text,
    )
    return formatted_text, ordered_doc_list


class DeepResearchState(TypedDict):
    """State schema for the deep research LangGraph workflow."""

    original_user_query: str
    user_query: str
    obs_file_list: List[str]
    upload_context: str
    total_length: int
    research_dimensions: List[str]
    all_raw_doc_list: List[Dict[str, Any]]
    dimension_params: List[Dict[str, str]]
    draft_contents: List[str]
    review_contents: List[str]
    revised_reports: List[Dict[str, str]]
    add_doc_list: List[Dict[str, Any]]
    summary_content: str
    final_response: Dict[str, Any]


class DeepResearchAgent:
    """LangGraph-based deep research agent from the lihu branch logic."""

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        review_config: ReviewConfig = REVIEW_CONFIG,
        sensitive_config: SensitiveConfig = SENSITIVE_CONFIG,
        knowledge_agent: Optional[KnowledgeAgent] = None,
    ):
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.review_config = review_config
        self.sensitive_config = sensitive_config
        self.ka = knowledge_agent or KnowledgeAgent(
            knowledge_config=review_config,
            sensitive_config=sensitive_config,
        )
        self.app = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(DeepResearchState)
        workflow.add_node("plan_node", self.plan_node)
        workflow.add_node("retrieve_node", self.retrieve_node)
        workflow.add_node("draft_node", self.draft_node)
        workflow.add_node("review_node", self.review_node)
        workflow.add_node("revise_node", self.revise_node)
        workflow.add_node("summary_node", self.summary_node)
        workflow.add_node("post_process_node", self.post_process_node)

        workflow.add_edge(START, "plan_node")
        workflow.add_edge("plan_node", "retrieve_node")
        workflow.add_edge("retrieve_node", "draft_node")
        workflow.add_edge("draft_node", "review_node")
        workflow.add_edge("review_node", "revise_node")
        workflow.add_edge("revise_node", "summary_node")
        workflow.add_edge("summary_node", "post_process_node")
        workflow.add_edge("post_process_node", END)
        return workflow.compile(checkpointer=self.checkpointer)

    async def _chat(
        self,
        prompt: str,
        response_format_override: Optional[Dict[str, Union[str, Dict]]] = None,
    ) -> Optional[Dict[str, Any]]:
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

    async def plan_node(self, state: DeepResearchState):
        """Process uploaded files and decompose the topic into dimensions."""
        user_query = state["original_user_query"]
        total_length = 0
        upload_context = ""

        if state["obs_file_list"]:
            upload_context, total_length = await download_upload_context(
                state["obs_file_list"],
                self.review_config,
                self.sensitive_config,
            )
            user_query = get_prompt(
                self.review_config.PROMPT_FILE,
                "user/deep_research_query_file",
                {
                    "upload_context": upload_context,
                    "user_query": user_query,
                },
            )
        else:
            user_query = get_prompt(
                self.review_config.PROMPT_FILE,
                "user/deep_research_query",
                {"user_query": user_query},
            )

        query_response = await self._chat(
            user_query,
            {
                "type": "json_schema",
                "json_schema": {
                    "type": "object",
                    "properties": {
                        "Research_dimensions": {
                            "type": "array",
                            "items": {"type": "string"},
                        }
                    },
                    "required": ["Research_dimensions"],
                },
            },
        )
        dimensions_json = _extract_json_object(message_content(query_response))
        dimensions = dimensions_json.get("Research_dimensions", [])
        if not isinstance(dimensions, list) or not dimensions:
            raise ValueError("Invalid research dimensions from phyto_chat")

        return {
            "user_query": user_query,
            "upload_context": upload_context,
            "total_length": total_length,
            "research_dimensions": [
                str(dimension) for dimension in dimensions[:4]
            ],
        }

    async def retrieve_node(self, state: DeepResearchState):
        """Retrieve documents for each research dimension."""
        dimensions = state["research_dimensions"]
        results = await asyncio.gather(
            *[
                self.ka.arun(
                    user_query=dimension,
                    is_generate=False,
                    is_follow_up=False,
                )
                for dimension in dimensions
            ],
            return_exceptions=True,
        )

        accumulator = RetrievalAccumulator(
            raw_docs=[],
            current_length=state["total_length"],
        )
        dimension_params = []
        dimension_length = (
            self.review_config.MAX_TOKENS - state["total_length"]
        ) / max(1, len(dimensions))

        for index, result in enumerate(results):
            fragments = self._dimension_fragments(
                result,
                accumulator,
                state["total_length"] + dimension_length * (index + 1),
            )
            dimension_params.append(
                {
                    "subtopic": dimensions[index],
                    "knowledge": "\n\n".join(fragments),
                }
            )

        return {
            "all_raw_doc_list": accumulator.raw_docs,
            "dimension_params": dimension_params,
            "total_length": accumulator.current_length,
        }

    def _dimension_fragments(
        self,
        dimension_result: Any,
        accumulator: RetrievalAccumulator,
        length_limit: float,
    ) -> List[str]:
        """Format bounded fragments for one research dimension."""
        fragments: List[str] = []
        if isinstance(dimension_result, BaseException):
            return fragments

        for doc in dimension_result:
            current_doc_id = f"document {accumulator.file_id + 1:03d}"
            doc_copy = doc.copy()
            doc_copy["doc_id"] = current_doc_id
            fragment = _format_doc_fragment(doc_copy, current_doc_id)
            if accumulator.current_length + len(fragment) <= length_limit:
                fragments.append(fragment)
                accumulator.raw_docs.append(doc_copy)
                accumulator.current_length += len(fragment)
                accumulator.file_id += 1
            else:
                break
        return fragments

    async def draft_node(self, state: DeepResearchState):
        """Create one draft subsection per dimension."""
        draft_tasks = [
            self._chat(
                get_prompt(
                    self.review_config.PROMPT_FILE,
                    "user/deep_research_dimension",
                    param,
                )
            )
            for param in state["dimension_params"]
        ]
        draft_results = await asyncio.gather(
            *draft_tasks, return_exceptions=True
        )
        return {
            "draft_contents": [
                (
                    ""
                    if isinstance(result, BaseException)
                    else message_content(result)
                )
                for result in draft_results
            ]
        }

    async def review_node(self, state: DeepResearchState):
        """Critique each draft and request supplementary search queries."""
        dimensions = state["research_dimensions"]
        review_tasks = []
        for di, draft_text in enumerate(state["draft_contents"]):
            review_tasks.append(
                self._chat(
                    get_prompt(
                        self.review_config.PROMPT_FILE,
                        "user/deep_research_review",
                        {
                            "current_subtopic": dimensions[di],
                            "other_subtopics": [
                                subtopic
                                for idx, subtopic in enumerate(dimensions)
                                if idx != di
                            ],
                            "draft_text": draft_text,
                        },
                    )
                )
            )
        review_results = await asyncio.gather(
            *review_tasks, return_exceptions=True
        )
        return {
            "review_contents": [
                (
                    "{}"
                    if isinstance(result, BaseException)
                    else message_content(result)
                )
                for result in review_results
            ]
        }

    async def revise_node(self, state: DeepResearchState):
        """Revise each draft using critique-driven supplementary retrieval."""
        revised_tasks = [
            self._feedback_rag(
                subtopic_idx=idx,
                draft_content=state["draft_contents"][idx],
                review_content=state["review_contents"][idx],
                raw_doc_list=state["all_raw_doc_list"],
            )
            for idx in range(len(state["research_dimensions"]))
        ]
        revised_results = await asyncio.gather(
            *revised_tasks, return_exceptions=True
        )

        revised_reports = []
        add_doc_list: List[Dict[str, Any]] = []
        for idx, result in enumerate(revised_results):
            if isinstance(result, BaseException):
                revised_reports.append(
                    {
                        "subtopic": state["research_dimensions"][idx],
                        "revised_report": state["draft_contents"][idx],
                    }
                )
                continue
            revised_reports.append(
                {
                    "subtopic": state["research_dimensions"][idx],
                    "revised_report": result.get("revised_content", ""),
                }
            )
            add_doc_list.extend(result.get("add_doc_list", []))

        return {
            "revised_reports": revised_reports,
            "add_doc_list": add_doc_list,
        }

    async def _feedback_rag(
        self,
        subtopic_idx: int,
        draft_content: str,
        review_content: str,
        raw_doc_list: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Retrieve additional evidence, revise, and audit citations."""
        review_json = _extract_json_object(review_content)
        has_gaps = bool(review_json.get("has_critical_gaps", False))
        add_queries = review_json.get("search_queries", [])
        if not isinstance(add_queries, list):
            add_queries = []

        add_doc_list: List[Dict[str, Any]] = []
        content_to_check = draft_content

        if has_gaps and add_queries:
            add_query_results = await asyncio.gather(
                *[
                    self.ka.arun(
                        user_query=str(query),
                        is_generate=False,
                        is_follow_up=False,
                    )
                    for query in add_queries[:3]
                ],
                return_exceptions=True,
            )
            new_knowledge_str = self._format_supplementary_results(
                SupplementaryResultContext(
                    subtopic_idx=subtopic_idx,
                    add_queries=add_queries,
                    add_query_results=add_query_results,
                    add_doc_list=add_doc_list,
                    draft_content=draft_content,
                )
            )
            if new_knowledge_str.strip():
                feedback_response = await self._chat(
                    get_prompt(
                        self.review_config.PROMPT_FILE,
                        "user/deep_research_feedback",
                        {
                            "existing_draft": draft_content,
                            "new_snippets": new_knowledge_str,
                        },
                    )
                )
                feedback_content = message_content(feedback_response)
                if feedback_content:
                    content_to_check = feedback_content

        content_to_check = await self._audit_citations(
            content_to_check=content_to_check,
            raw_doc_list=raw_doc_list,
            add_doc_list=add_doc_list,
        )
        return {
            "revised_content": content_to_check,
            "add_doc_list": add_doc_list,
        }

    def _format_supplementary_results(
        self,
        context: SupplementaryResultContext,
    ) -> str:
        """Format supplementary retrieval snippets for revision."""
        query_count = max(1, len(context.add_query_results))
        add_query_length = max(
            1,
            int(
                (self.review_config.MAX_TOKENS - len(context.draft_content))
                / query_count
            ),
        )
        format_state = SupplementaryFormatState(
            query_length=add_query_length,
            counters=SupplementaryCounters(),
        )
        add_blocks = [
            block
            for add_num, add_result in enumerate(context.add_query_results)
            if (
                block := self._format_supplementary_query(
                    context,
                    add_result,
                    add_num,
                    format_state,
                )
            )
        ]
        return "\n\n---\n\n".join(add_blocks)

    def _format_supplementary_query(
        self,
        context: SupplementaryResultContext,
        add_result: Any,
        add_num: int,
        format_state: SupplementaryFormatState,
    ) -> str:
        """Format snippets for one supplementary query."""
        if isinstance(add_result, Exception) or not add_result:
            return ""

        counters = format_state.counters
        query = str(context.add_queries[add_num])
        fragments = []
        valid_doc_count = 0
        for doc in add_result:
            if valid_doc_count >= 3:
                break
            current_doc_id = (
                "add document "
                f"S{context.subtopic_idx + 1}-{counters.file_id + 1:03d}"
            )
            doc_copy = doc.copy()
            doc_copy["doc_id"] = current_doc_id
            fragment = _format_doc_fragment(doc_copy, current_doc_id)
            if len(fragment) > format_state.query_length:
                continue
            if counters.total_length + len(fragment) <= (
                format_state.query_length * (add_num + 1)
            ):
                context.add_doc_list.append(doc_copy)
                fragments.append(fragment)
                counters.total_length += len(fragment)
                counters.file_id += 1
                valid_doc_count += 1
            else:
                break
        if not fragments:
            return ""
        return (
            f"### Supplementary Direction {add_num + 1}: {query}\n\n"
            + "\n\n".join(fragments)
        )

    async def _audit_citations(
        self,
        content_to_check: str,
        raw_doc_list: List[Dict[str, Any]],
        add_doc_list: List[Dict[str, Any]],
    ) -> str:
        """Ask the model to remove unsupported citations."""
        all_doc_lookup = {
            str(doc["doc_id"]): _doc_content(doc)
            for doc in [*raw_doc_list, *add_doc_list]
            if doc.get("doc_id")
        }
        current_batch_docs: Dict[str, str] = {}
        current_batch_doc_len = 0

        async def run_citation_check(batch_docs: Dict[str, str]) -> None:
            nonlocal content_to_check
            if not batch_docs:
                return
            check_response = await self._chat(
                get_prompt(
                    self.review_config.PROMPT_FILE,
                    "user/deep_research_check",
                    {
                        "input_text": content_to_check,
                        "source_docs_json": json.dumps(
                            batch_docs, ensure_ascii=False
                        ),
                    },
                )
            )
            checked_text = message_content(check_response).strip()
            if checked_text:
                content_to_check = checked_text

        for tag in set(re.findall(CITATION_PATTERN, content_to_check)):
            raw_id = _normalize_citation_id(tag.strip("[]"))
            if raw_id not in all_doc_lookup:
                continue
            doc_content = all_doc_lookup[raw_id]
            if len(doc_content) > 3000:
                doc_content = f"{doc_content[:3000]}..."
            doc_json_str = json.dumps({tag: doc_content}, ensure_ascii=False)
            added_len = len(doc_json_str)
            if (
                len(content_to_check) + current_batch_doc_len + added_len
                > self.review_config.MAX_TOKENS
            ):
                await run_citation_check(current_batch_docs)
                current_batch_docs = {}
                current_batch_doc_len = 0
            current_batch_docs[tag] = doc_content
            current_batch_doc_len += added_len

        await run_citation_check(current_batch_docs)
        return content_to_check

    async def summary_node(self, state: DeepResearchState):
        """Synthesize the revised subsections into a final report."""
        summary_params: Dict[str, str] = {
            "user_query": state["original_user_query"]
        }
        for idx in range(4):
            report = (
                state["revised_reports"][idx]
                if idx < len(state["revised_reports"])
                else {}
            )
            title = (
                state["research_dimensions"][idx]
                if idx < len(state["research_dimensions"])
                else ""
            )
            summary_params[f"subsection_{idx + 1}_title"] = title
            summary_params[f"subsection_{idx + 1}_content"] = str(
                report.get("revised_report", "")
            )

        summary_response = await self._chat(
            get_prompt(
                self.review_config.PROMPT_FILE,
                "user/deep_research_summary",
                summary_params,
            )
        )
        content = message_content(summary_response).replace("`", "")
        return {"summary_content": content or "No summary generated"}

    async def post_process_node(self, state: DeepResearchState):
        """Renumber citations and attach references and follow-ups."""
        formatted_text, ordered_doc_list = _renumber_citations(
            state["summary_content"],
            [*state["all_raw_doc_list"], *state["add_doc_list"]],
        )
        follow_up_response = await self._chat(
            get_prompt(
                self.review_config.PROMPT_FILE,
                "system/follow_up_questions",
                {
                    "user_query": state["original_user_query"],
                    "system_response": formatted_text,
                },
            )
        )
        follow_up_list = parse_follow_up_questions(
            message_content(follow_up_response)
        )
        final_response = {
            "choices": [
                {
                    "message": {
                        "content": formatted_text,
                        "doc_list": ordered_doc_list,
                        "total": 10000,
                        "follow_up_questions": follow_up_list,
                    }
                }
            ]
        }
        return {"final_response": final_response}

    async def arun(
        self,
        user_query: str,
        obs_file_list: Optional[List[str]] = None,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute the DeepResearchAgent workflow."""
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
        }
        final_state = await ainvoke_graph(
            self.app, initial_state, thread_id=thread_id
        )
        return final_state["final_response"]


async def deep_research(
    user_query: str,
    obs_file_list: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph DeepResearchAgent."""
    review_config = copy_config_with_overrides(
        REVIEW_CONFIG,
        kwargs,
        REVIEW_CONFIG_FIELD_MAP,
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        SENSITIVE_CONFIG,
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

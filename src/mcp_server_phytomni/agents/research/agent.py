# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""LangGraph-based in silico research agents for computational workflows.

Classes: ResearchTaskContext, InSilicoResearchState, InSilicoResearchAgents.
Functions: in_silico_research, extract_goals_node, prepare_tasks,
    run_research_node.
"""

import logging
import operator
from dataclasses import dataclass, field
from json import loads
from time import perf_counter
from typing import Annotated, Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command, Send, interrupt

from ...common.prompts import get_prompt
from ...config.defaults import InSilicoResearchConfig
from ...config.overrides import (
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...config.settings import SensitiveConfig, get_sensitive_config
from ...graphs.analyst_dispatch_adapters import submit_analyst_via_subgraph
from ...graphs.chat_adapters import (
    build_chat_input,
    build_chat_kwargs_for,
    extract_chat_response,
)
from ...interop.planner import InteropMode
from ...runtime.agent_registry import (
    agent_fingerprint_values,
    get_cached_agent,
)
from ...runtime.langgraph_runner import (
    capture_workflow_boundary,
    ensure_checkpointer,
)
from ...storage.downloads import download_upload_context
from ...storage.path_policy import RunIdentity
from ..analyst.agent import (
    ANALYST_CONFIG_FIELD_MAP,
    ANALYST_SECRET_FIELD_MAP,
    ANALYST_SENSITIVE_FIELD_MAP,
    AnalystAgent,
)
from ..chat.service import _cached_chat_app
from ..shared.analysis import (
    AnalysisStateSpec,
    capture_analysis_result,
    run_analysis_graph,
)
from ..shared.analysis_storage import create_output_dir
from ..shared.interop import (
    InteropAttempt,
    build_a2a_resume_draft,
    has_interop_target_kind,
    initial_interop_state,
    interop_attempt_update,
    interop_evidence_update,
    interop_state_update,
    make_interop_record,
    merge_a2a_pending_fields,
    project_a2a_evidence,
    require_a2a_result,
    resolve_interop_dependencies,
    resolve_required_interop_dependencies,
    update_a2a_pending_from_result,
)
from ..shared.parallel_dispatch import (
    ParallelDispatchSpec,
    ParallelDispatchState,
    build_parallel_dispatch_graph,
)
from .interop import (
    RESEARCH_A2A_CAPABILITY,
    RESEARCH_INTEROP_FAILURES,
    RESEARCH_MCP_CAPABILITY,
    ResearchA2APending,
    ResearchEvidence,
    ResearchInteropDependencies,
    collect_research_a2a,
    collect_research_evidence,
    format_research_evidence,
)

logger = logging.getLogger(__name__)

IN_SILICO_CONFIG = InSilicoResearchConfig()

_RESEARCH_GOALS_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "type": "array",
        "description": (
            "A list of research objectives derived from the paper."
        ),
        "items": {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "context": {"type": "string"},
            },
            "required": ["goal", "context"],
        },
    },
}


@dataclass(frozen=True)
class ResearchTaskInterop:
    """Per-task opt-in controls for external Research evidence."""

    mode: InteropMode = "off"
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResearchTaskContext:
    """Resolved context for submitting one in-silico research task.

    Attributes:
        goal_description: Research objective submitted to AnalystAgent.
        context: Supporting plan or context for the task.
        data_list: Input datasets for the research task.
        output_dir: Output directory for generated results.
        task_name: Stable task label derived from the goal index.
        thread_id: LangGraph thread ID for the child AnalystAgent run.
    """

    goal_description: str
    context: str
    data_list: dict[str, str]
    output_dir: str
    task_name: str
    thread_id: str
    interop: ResearchTaskInterop = field(default_factory=ResearchTaskInterop)


class InSilicoResearchState(ParallelDispatchState):
    """State schema for the in silico research workflow.

    Inherits the shared parallel-dispatch bookkeeping fields
    (``analysis_type``, ``task_index``, ``task_ids``,
    ``completed_count``, ``error``, ``failures``) from
    ``ParallelDispatchState`` so
    multi-goal Send fan-out merges through reducers
    (``task_ids: operator.or_``, ``completed_count: operator.add``)
    instead of raising LangGraph's ``InvalidUpdateError`` on concurrent
    writes. Domain-specific fields below carry paper / goal / task
    bookkeeping that is unique to research.

    Attributes:
        paper_text: Scientific paper text to analyze for research goals.
        data_list: Dictionary of data sources for research.
        user_id: User identifier.
        obs_file_list: List of OBS file paths to include as context.
        output_dir: Output directory path for results.
        goals: List of extracted research objectives from the paper.
        research_tasks: List of research tasks to be executed.
    """

    paper_text: str
    data_list: dict[str, str]
    user_id: str
    obs_file_list: list[str]
    output_dir: str | None
    goals: list[dict[str, str]]  # List of extracted research objectives
    research_tasks: list[dict[str, str]]  # List of research tasks
    goal_description: str
    context: str
    task_name: str
    thread_id: str
    interop_mode: InteropMode
    interop_targets: list[str]
    evidence: Annotated[list[ResearchEvidence], operator.add]
    a2a_pending: Annotated[list[ResearchA2APending], operator.add]
    a2a_task_ids: Annotated[dict[str, str], operator.or_]


class InSilicoResearchAgents:
    """LangGraph-based agent for in silico research from scientific literature.

    This agent provides a workflow for extracting research goals from
    scientific papers and executing computational research workflows using
    LangGraph's parallel execution capabilities.

    Attributes:
        checkpointer: LangGraph checkpointer for state persistence.
        analyst_agent: AnalystAgent instance for task execution.
        in_silico_config: In silico research configuration.
        sensitive_config: Sensitive configuration settings.
        app: Compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver | None = None,
        analyst_agent: AnalystAgent | None = None,
        in_silico_config=IN_SILICO_CONFIG,
        sensitive_config: SensitiveConfig | None = None,
        interop_dependencies: ResearchInteropDependencies | None = None,
    ):
        """Initialize the InSilicoResearchAgents.

        Args:
            checkpointer: LangGraph MemorySaver for state persistence.
            analyst_agent: Optional AnalystAgent instance. Creates one if
                omitted.
            in_silico_config: In silico research configuration object.
            sensitive_config: Sensitive configuration for credentials.
        """
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.in_silico_config = in_silico_config
        self.sensitive_config = sensitive_config or get_sensitive_config()
        self.analyst_agent = analyst_agent or AnalystAgent(
            analyst_config=in_silico_config,
            sensitive_config=self.sensitive_config,
        )
        self._interop_dependencies = (
            interop_dependencies or ResearchInteropDependencies()
        )
        self.app = self._build_graph()

    def _build_graph(self):
        """Build the LangGraph workflow for in silico research tasks."""
        return build_parallel_dispatch_graph(
            ParallelDispatchSpec(
                state_class=InSilicoResearchState,
                prepare_node=self.prepare_tasks,
                work_node=self.run_research_node,
                route_fn=self.route_research_tasks,
                work_node_name="research_node",
                extract_node=self.extract_goals_node,
                extract_node_name="extract_goals_node",
            ),
            checkpointer=self.checkpointer,
            post_work_nodes=(
                ("research_a2a_resume_node", self.resume_research_a2a),
            ),
        )

    def route_research_tasks(self, state: InSilicoResearchState):
        """Dispatch research tasks in parallel using Send API.

        Args:
            state: Current in-silico research workflow state.

        Returns:
            LangGraph Send commands for each extracted research task.
        """
        tasks = state.get("research_tasks", [])
        return [
            Send(
                "research_node",
                {
                    "task_index": i,
                    "data_list": state.get("data_list", {}),
                    "output_dir": state.get("output_dir"),
                    "interop_mode": state.get("interop_mode", "off"),
                    "interop_targets": state.get("interop_targets", []),
                    **task,
                },
            )
            for i, task in enumerate(tasks)
        ]

    async def _extract_goals(
        self, user_query: str, obs_file_list: list[str]
    ) -> list[dict[str, str]]:
        """Extract research goals from scientific paper text.

        Args:
            user_query: The paper text or research query.
            obs_file_list: List of OBS file paths to include as context.

        Returns:
            List of research goal dictionaries with 'goal' and 'context' keys.
        """
        if obs_file_list:
            upload_context, _ = await download_upload_context(
                obs_file_list,
                self.in_silico_config,
                self.sensitive_config,
            )
            user_query = get_prompt(
                self.in_silico_config.PROMPT_FILE,
                "user/in_silico_research_goals_file",
                {"upload_context": upload_context, "paper_text": user_query},
            )
        else:
            user_query = get_prompt(
                self.in_silico_config.PROMPT_FILE,
                "user/in_silico_research_goals",
                {"paper_text": user_query},
            )

        chat_kwargs_bag = build_chat_kwargs_for(
            self.in_silico_config,
            self.sensitive_config,
            response_format=_RESEARCH_GOALS_RESPONSE_FORMAT,
        )
        chat_output = await _cached_chat_app().ainvoke(
            build_chat_input(
                user_query=user_query, chat_kwargs=chat_kwargs_bag
            )
        )
        phyto_response = extract_chat_response(chat_output)
        if not phyto_response:
            return []
        return loads(phyto_response["choices"][0]["message"]["content"])

    async def _submit_research_task(
        self,
        task: ResearchTaskContext,
        *,
        external_evidence: ResearchEvidence | None = None,
    ) -> dict:
        """Submit research task using AnalystAgent and wait for completion.

        Args:
            goal_description: Description of the research goal.
            context: Context information for the research.
            data_list: Dictionary of data sources for research.
            output_dir: Output directory path for results.
            task_name: Name identifier for the task.

        Returns:
            Dict containing task_id and output_dir.
        """
        logger.info(
            "Submitting research task via AnalystAgent: %s", task.task_name
        )

        evidence = external_evidence
        if evidence is None:
            evidence = await collect_research_evidence(
                {
                    "goal_description": task.goal_description,
                    "context": task.context,
                    "data_list": task.data_list,
                    "task_name": task.task_name,
                },
                mode=task.interop.mode,
                target_ids=task.interop.targets,
                dependencies=resolve_interop_dependencies(
                    self._interop_dependencies,
                    mode=task.interop.mode,
                    sensitive_config=self.sensitive_config,
                ),
            )
        if evidence is None and task.interop.mode == "required":
            raise RuntimeError(
                "required Research interop produced no external evidence"
            )
        prompt_context = task.context
        if evidence is not None:
            prompt_context = (
                f"{task.context}\n\n{format_research_evidence(evidence)}"
            )

        result = await submit_analyst_via_subgraph(
            self.analyst_agent,
            self.in_silico_config,
            self.sensitive_config,
            {
                "analysis_type": task.task_name,
                "target_id": task.task_name,
                "output_dir": task.output_dir,
                "prompt_parts": (
                    task.goal_description,
                    prompt_context,
                    task.data_list,
                ),
                "compute_resource": "medium",
            },
            is_polling=False,
        )

        if result.get("task_status") == "FAILED_AT_AGENT_LEVEL":
            raise RuntimeError(
                f"AnalystAgent failed: {result.get('error_detail')}"
            )

        task_id = result.get("task_id")
        logger.info("%s task completed (task_id: %s)", task.task_name, task_id)

        task_result: dict[str, Any] = {
            "task_id": task_id,
            "output_dir": result.get("output_dir"),
        }
        if evidence is not None:
            task_result["evidence"] = evidence
        return task_result

    async def _collect_research_external(
        self,
        task: ResearchTaskContext,
    ) -> ResearchEvidence | ResearchA2APending | None:
        """Resolve optional A2A evidence before the local analyst submit."""
        dependencies = resolve_interop_dependencies(
            self._interop_dependencies,
            mode=task.interop.mode,
            sensitive_config=self.sensitive_config,
        )
        if dependencies is not None:
            self._interop_dependencies = dependencies
        if not has_interop_target_kind(
            dependencies,
            task.interop.targets,
            "a2a",
        ):
            result = None
        else:
            result = await collect_research_a2a(
                {
                    "goal_description": task.goal_description,
                    "context": task.context,
                    "data_list": task.data_list,
                    "task_name": task.task_name,
                },
                mode=task.interop.mode,
                target_ids=task.interop.targets,
                dependencies=dependencies,
            )
        if result is not None:
            if result["status"] == "input_required":
                task_id = result.get("task_id")
                if not task_id:
                    raise RuntimeError(
                        "external A2A input-required response omitted task_id"
                    )
                pending = merge_a2a_pending_fields(
                    {
                        "task_name": task.task_name,
                        "goal_description": task.goal_description,
                        "context": task.context,
                        "data_list": dict(task.data_list),
                        "output_dir": task.output_dir,
                        "thread_id": task.thread_id,
                    },
                    {**result, "task_id": task_id},
                )
                return cast(ResearchA2APending, pending)
            return cast(ResearchEvidence, project_a2a_evidence(result))

        evidence = await collect_research_evidence(
            {
                "goal_description": task.goal_description,
                "context": task.context,
                "data_list": task.data_list,
                "task_name": task.task_name,
            },
            mode=task.interop.mode,
            target_ids=task.interop.targets,
            dependencies=dependencies,
        )
        if evidence is None and task.interop.mode == "required":
            raise RuntimeError(
                "required Research interop produced no external evidence"
            )
        return evidence

    async def extract_goals_node(self, state: InSilicoResearchState) -> dict:
        """Extract research goals from scientific paper text.

        This node is the entry point of the workflow, analyzing the paper
        content to identify and extract research objectives.

        Args:
            state: Current workflow state containing paper_text and
                obs_file_list.

        Returns:
            Dict with extracted goals list and error status.
        """
        paper_text = state["paper_text"]
        obs_file_list = state.get("obs_file_list", [])

        logger.info("Extracting research goals from paper")

        async def extract_goals() -> dict[str, Any]:
            """Extract goals and return the success state.

            Returns:
                State update containing extracted goals and no error.
            """
            goals = await self._extract_goals(paper_text, obs_file_list)
            logger.info("Extracted %d research goals", len(goals))
            return {"goals": goals, "error": None}

        def failure_state(exc: Exception) -> dict[str, Any]:
            """Store goal extraction failures in workflow state.

            Args:
                exc: Exception raised during goal extraction.

            Returns:
                Failure state update with an empty goals list.
            """
            logger.warning("Goal extraction failed: %s", exc)
            return {"goals": [], "error": str(exc)}

        return await capture_workflow_boundary(extract_goals, failure_state)

    async def prepare_tasks(self, state: InSilicoResearchState) -> dict:
        """Prepare the list of research tasks from extracted goals.

        Args:
            state: Current workflow state containing extracted goals.

        Returns:
            Dict with research_tasks, output_dir, task_ids, and
            completed_count.
        """
        goals = state.get("goals", [])
        run_identity = RunIdentity.create(
            user_id=state.get("user_id"),
            scope="in_silico_research_task",
        )
        access_key_id, secret_access_key = (
            self.sensitive_config.obs_credentials()
        )
        output_dir = state.get("output_dir") or create_output_dir(
            user_id=run_identity.user_id,
            task="in_silico_research_task",
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=self.in_silico_config.OBS_SERVER,
            bucket_name=self.in_silico_config.BUCKET_NAME,
            run_identity=run_identity,
        )

        tasks = [
            {
                "goal_description": goal["goal"],
                "context": goal["context"],
                "task_name": f"research_goal_{i}",
                "thread_id": run_identity.scoped_id("thread", i),
            }
            for i, goal in enumerate(goals)
        ]

        return {
            "research_tasks": tasks,
            "output_dir": output_dir,
            "task_ids": {},
            "completed_count": 0,
        }

    async def run_research_node(
        self,
        state: InSilicoResearchState,
    ) -> dict[str, Any] | Command:
        """Execute a single research task dispatched via Send API.

        This node is called dynamically for each research task.

        Args:
            state: Current workflow state containing task details.

        Returns:
            State updates with task ids, completed count, and optional error;
            an A2A input-required task returns a ``Command`` that persists
            the safe correlation ids before entering the resume node.
        """
        task_index = state.get("task_index")
        task_name = state["task_name"]
        output_dir = state.get("output_dir")
        if output_dir is None:
            raise ValueError("output_dir is required for research tasks")

        logger.info("[Research-%s] Executing: %s", task_index, task_name)

        task = ResearchTaskContext(
            goal_description=state["goal_description"],
            context=state["context"],
            data_list=state.get("data_list", {}),
            output_dir=output_dir,
            task_name=task_name,
            thread_id=state.get("thread_id", task_name),
            interop=ResearchTaskInterop(
                mode=state.get("interop_mode", "off"),
                targets=tuple(state.get("interop_targets", [])),
            ),
        )
        started = perf_counter()
        try:
            external = await self._collect_research_external(task)
        except RESEARCH_INTEROP_FAILURES as exc:

            async def failed_submit(error: Exception = exc) -> dict[str, Any]:
                """Re-enter the shared failure recorder for A2A errors."""
                raise error

            updates = await capture_analysis_result(
                state,
                analysis_type=task_name,
                submit_call=failed_submit,
                result_key=None,
                result_list_key="evidence",
            )
            if task.interop.mode != "off":
                updates.update(
                    interop_attempt_update(
                        InteropAttempt(
                            self._interop_dependencies,
                            task.interop.targets,
                            task.interop.mode,
                            RESEARCH_MCP_CAPABILITY,
                            RESEARCH_A2A_CAPABILITY,
                            "failed",
                            perf_counter() - started,
                        )
                    )
                )
            return updates

        if isinstance(external, dict) and "task_id" in external:
            pending = cast(ResearchA2APending, external)
            return Command(
                goto="research_a2a_resume_node",
                update={
                    "a2a_pending": [pending],
                    "a2a_task_ids": {task_name: pending["task_id"]},
                    **interop_state_update(
                        make_interop_record(
                            target_id=pending["target_id"],
                            kind="a2a",
                            capability=pending["capability"],
                            status="input_required",
                            latency_seconds=perf_counter() - started,
                        )
                    ),
                },
            )

        async def submit_call() -> dict[str, Any]:
            """Submit one research task and return its raw result.

            Returns:
                AnalystAgent payload with ``task_id`` and ``output_dir``.
            """
            if external is None:
                return await self._submit_research_task(task)
            return await self._submit_research_task(
                task,
                external_evidence=external,
            )

        updates = await capture_analysis_result(
            state,
            analysis_type=task_name,
            submit_call=submit_call,
            result_key=None,
            result_list_key="evidence",
        )
        updates["evidence"] = [
            item["evidence"]
            for item in updates.get("evidence", [])
            if isinstance(item, dict) and "evidence" in item
        ]
        if task.interop.mode != "off":
            if external is None:
                updates.update(
                    interop_attempt_update(
                        InteropAttempt(
                            self._interop_dependencies,
                            task.interop.targets,
                            task.interop.mode,
                            RESEARCH_MCP_CAPABILITY,
                            RESEARCH_A2A_CAPABILITY,
                            "degraded",
                            perf_counter() - started,
                            True,
                        )
                    )
                )
            else:
                updates.update(
                    interop_evidence_update(
                        external,
                        status="completed",
                        latency_seconds=perf_counter() - started,
                    )
                )
        return updates

    async def resume_research_a2a(
        self,
        state: InSilicoResearchState,
    ) -> dict[str, Any]:
        """Resume pending external A2A work after a graph interrupt."""
        pending_items = state.get("a2a_pending", [])
        if not pending_items:
            return {}
        pending = pending_items[0]
        started = perf_counter()
        task = ResearchTaskContext(
            goal_description=pending["goal_description"],
            context=pending["context"],
            data_list=dict(pending["data_list"]),
            output_dir=pending["output_dir"],
            task_name=pending["task_name"],
            thread_id=pending["thread_id"],
            interop=ResearchTaskInterop(
                mode="required",
                targets=(pending["target_id"],),
            ),
        )
        dependencies = resolve_required_interop_dependencies(
            self._interop_dependencies,
            self.sensitive_config,
        )
        while True:
            draft = build_a2a_resume_draft(
                pending,
                kind="external_a2a",
                label_key="task_name",
            )
            resume_payload = interrupt(draft)
            if not isinstance(resume_payload, dict):
                resume_payload = {"text": str(resume_payload)}
            result = await collect_research_a2a(
                {
                    "goal_description": task.goal_description,
                    "context": task.context,
                    "data_list": task.data_list,
                    "task_name": task.task_name,
                },
                mode="required",
                target_ids=task.interop.targets,
                dependencies=dependencies,
                resume=resume_payload,
                pending=pending,
            )
            result = require_a2a_result(result, "Research")
            if result["status"] != "input_required":
                break
            pending = cast(
                ResearchA2APending,
                update_a2a_pending_from_result(pending, result),
            )

        evidence = cast(ResearchEvidence, project_a2a_evidence(result))

        async def submit_call() -> dict[str, Any]:
            """Continue through the existing local Analyst submit seam."""
            return await self._submit_research_task(
                task,
                external_evidence=evidence,
            )

        updates = await capture_analysis_result(
            {**state, "task_ids": {}, "evidence": []},
            analysis_type=task.task_name,
            submit_call=submit_call,
            result_key=None,
            result_list_key="evidence",
        )
        updates["evidence"] = [
            item["evidence"]
            for item in updates.get("evidence", [])
            if isinstance(item, dict) and "evidence" in item
        ]
        updates.update(
            interop_evidence_update(
                evidence,
                status="completed",
                latency_seconds=perf_counter() - started,
            )
        )
        return updates

    async def arun(
        self,
        paper_text: str,
        data_list: dict[str, str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Conduct in silico research and return task_ids.

        Args:
            paper_text: Scientific paper text to analyze.
            data_list: Dictionary of data sources for research.
            user_id: Optional user identifier.
            obs_file_list: List of OBS files to include as context.
            output_dir: Optional output directory path.
            thread_id: Optional thread ID for checkpointer.

        Returns:
            Dict with task_ids mapping research goals to task IDs.
        """
        return await run_analysis_graph(
            self.app,
            {
                "paper_text": paper_text,
                "data_list": data_list,
                "obs_file_list": kwargs.get("obs_file_list") or [],
                **initial_interop_state(kwargs),
            },
            kwargs,
            ("task_ids", "goals", "error", "failures", "evidence"),
            AnalysisStateSpec(
                tasks_key="research_tasks",
                result_inits={"goals": [], "evidence": []},
            ),
        )


async def in_silico_research(
    user_query: str,
    data_list: dict[str, str],
    user_id: str | None = None,
    obs_file_list: list[str] | None = None,
    output_dir: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compatibility wrapper around the LangGraph in-silico research agent.

    Args:
        user_query: Paper text or research context to decompose.
        data_list: Input datasets available for submitted research tasks.
        user_id: Optional user identifier for output paths.
        obs_file_list: Optional OBS paths for uploaded paper/context files.
        output_dir: Optional explicit output directory.
        **kwargs: Keyword-compatible analysis and sensitive overrides.

    Returns:
        In-silico research goals, task IDs, and any workflow error.
    """
    in_silico_config = copy_config_with_overrides(
        IN_SILICO_CONFIG,
        kwargs,
        ANALYST_CONFIG_FIELD_MAP,
        fixed_updates={
            "USER_ID": user_id,
            "OUTPUT_DIR": output_dir,
        },
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        kwargs,
        field_map=ANALYST_SENSITIVE_FIELD_MAP,
        secret_field_map=ANALYST_SECRET_FIELD_MAP,
    )
    agent = get_cached_agent(
        "InSilicoResearchAgents",
        lambda: InSilicoResearchAgents(
            in_silico_config=in_silico_config,
            sensitive_config=sensitive_config,
        ),
        agent_fingerprint_values(
            in_silico_config=in_silico_config,
            sensitive_config=sensitive_config,
        ),
    )
    return await agent.arun(
        paper_text=user_query,
        data_list=data_list,
        user_id=user_id,
        obs_file_list=obs_file_list or [],
        output_dir=output_dir,
        interop_mode=kwargs.get("interop_mode", "off"),
        interop_targets=kwargs.get("interop_targets", []),
    )

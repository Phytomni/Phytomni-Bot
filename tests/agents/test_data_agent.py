# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline smoke tests for the DataAgent wrapper.

Covers graph routing, NL2SQL dialog id policy, DataAgent graph invocation, and
legacy rewrite_nl2sql wrapper thread-id compatibility.
"""

import importlib
from typing import Any, List, cast

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.agents.data import agent as data_agent_module
from mcp_server_phytomni.agents.data.agent import DataAgent, DataAgentState
from mcp_server_phytomni.agents.data.nl2sql import (
    Nl2SqlRequest,
    execute_nl2sql_request,
)
from mcp_server_phytomni.config.defaults import DataConfig
from mcp_server_phytomni.config.settings import SensitiveConfig

# ``agents.data.__init__`` re-exports the ``nl2sql`` *function*, which
# shadows the submodule of the same name on the package; resolve the
# real module from sys.modules so monkeypatch targets its globals.
nl2sql_module = importlib.import_module(
    "mcp_server_phytomni.agents.data.nl2sql"
)

pytestmark = pytest.mark.agent


@pytest.fixture(autouse=True)
def _clear_nl2sql_cache():
    """Reset the NL2SQL execute cache so tests cannot bleed state.

    The cached inner ``_execute_nl2sql_cached`` lives in the persistent
    .cache/phytomni SQLite store; without an explicit clear, a passing
    test caches its result against the natural-language question key
    and every later test that reuses that question short-circuits
    through the cache, bypassing the rotation-and-failure behavior
    those tests are pinning.
    """
    nl2sql_module.clear_nl2sql_cache()
    yield


class _FakePost:
    """Capture each attempt's ``dialog_id`` and script its outcome.

    Attributes:
        outcomes: One entry per expected attempt; an ``Exception``
            instance is raised, anything else is returned as the
            parsed JSON response.
        dialog_ids: ``dialog_id`` observed on each successive POST.
        backoff_attempts: Attempt index passed to each inter-rotation
            backoff (recorded by the patched no-op so the suite stays
            fast and the cadence is assertable).
    """

    def __init__(self, outcomes: List[Any]) -> None:
        """Store the scripted per-attempt outcomes."""
        self.outcomes = outcomes
        self.dialog_ids: List[str] = []
        self.backoff_attempts: List[int] = []

    async def __call__(self, client: Any, request: Any, retry: Any) -> Any:
        """Record the conversation id and return/raise the outcome.

        Args:
            client: Ignored fake HTTP client.
            request: ``JsonPostRequest`` whose body carries dialog_id.
            retry: Ignored retry policy.

        Returns:
            The scripted response for this attempt.

        Raises:
            Exception: When the scripted outcome is an exception.
        """
        del client, retry
        self.dialog_ids.append(request.json_body["dialog_id"])
        outcome = self.outcomes[len(self.dialog_ids) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def recorded_dialog_ids(self) -> List[str]:
        """Return the conversation ids seen across attempts in order.

        Returns:
            The ``dialog_id`` captured on each successive POST.
        """
        return self.dialog_ids

    def attempt_count(self) -> int:
        """Return how many conversations were attempted.

        Returns:
            Number of POSTs the rotation loop issued.
        """
        return len(self.dialog_ids)

    def recorded_backoffs(self) -> List[int]:
        """Return the attempt index handed to each backoff, in order.

        Returns:
            One entry per inter-rotation pause the loop performed.
        """
        return self.backoff_attempts


def _mcp_error() -> McpError:
    """Return an MCP error mirroring an exhausted single conversation."""
    return McpError(
        ErrorData(code=INTERNAL_ERROR, message="Failed to query SQL database")
    )


def _patch_transport(monkeypatch: pytest.MonkeyPatch, fake: _FakePost) -> None:
    """Stub the token fetch and shared POST helper for nl2sql tests.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        fake: Scripted POST stand-in capturing per-attempt dialog ids.
    """

    async def fake_token() -> str:
        """Return a dummy IAM token."""
        return "token-xyz"

    async def fake_backoff(attempt: int) -> None:
        """Record the backoff cadence without actually sleeping."""
        fake.backoff_attempts.append(attempt)

    monkeypatch.setattr(nl2sql_module, "get_token", fake_token)
    monkeypatch.setattr(nl2sql_module, "post_json_with_retries", fake)
    monkeypatch.setattr(nl2sql_module, "_rotation_backoff", fake_backoff)


class FakeCompiledGraph:
    """Minimal async graph stand-in used to inspect DataAgent invocation."""

    def __init__(self):
        """Verify init  ."""
        self.state = None
        self.config = None

    async def ainvoke(self, state, config=None):
        """Capture DataAgent graph invocation and return final response.

        Args:
            state: Initial DataAgent workflow state.
            config: Optional LangGraph runnable config.

        Returns:
            Final graph state containing the response payload.
        """
        self.state = state
        self.config = config
        return {
            "final_response": {
                "query": state["user_query"],
                "is_rewrite": state["is_rewrite"],
            }
        }

    def snapshot(self):
        """Return captured invocation details.

        Returns:
            Last state and config captured by ainvoke.
        """
        return {"state": self.state, "config": self.config}


def test_data_agent_routes_start_by_rewrite_flag():
    """Verify data agent routes start by rewrite flag."""
    agent = DataAgent(
        data_config=DataConfig(),
        sensitive_config=SensitiveConfig.load(),
    )

    assert (
        agent.route_start(cast(DataAgentState, {"is_rewrite": True}))
        == "retrieve_prep_node"
    )
    assert (
        agent.route_start(cast(DataAgentState, {"is_rewrite": False}))
        == "search_node"
    )


def test_nl2sql_request_keeps_explicit_dialog_id():
    """Verify caller-provided dialog IDs remain unchanged."""
    request = Nl2SqlRequest.from_kwargs(
        "plant height in rice",
        {"dialog_id": "dialog-1"},
    )

    assert request.payload()["dialog_id"] == "dialog-1"


def test_nl2sql_request_generates_policy_dialog_id_when_missing():
    """Verify missing dialog IDs use the shared generated ID style."""
    request = Nl2SqlRequest.from_kwargs("plant height in rice", {})

    assert "-dialog-" in request.payload()["dialog_id"]


async def test_data_agent_arun_invokes_compiled_graph_with_thread_id():
    """Verify data agent arun invokes compiled graph with thread id."""
    agent = DataAgent(
        data_config=DataConfig(),
        sensitive_config=SensitiveConfig.load(),
    )
    fake_graph = FakeCompiledGraph()
    object.__setattr__(agent, "app", fake_graph)

    result = await agent.arun(
        user_query="plant height in rice",
        is_rewrite=False,
        thread_id="pytest-thread",
    )

    assert result["query"] == "plant height in rice"
    assert result["is_rewrite"] is False
    assert result["phytomni_state"] == {}
    assert fake_graph.state == {
        "user_query": "plant height in rice",
        "is_rewrite": False,
        "retrieve_prompt": None,
        "rewrite_query": None,
        "final_response": None,
    }
    assert fake_graph.config == {
        "configurable": {"thread_id": "pytest-thread"}
    }


async def test_rewrite_nl2sql_uses_dialog_id_as_thread_id(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify wrapper keeps dialog ID and graph thread ID aligned.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the agent and
            cache.

    Returns:
        None after wrapper config and run assertions pass.
    """
    captured: dict[str, Any] = {}

    class FakeDataAgent:
        """Fake workflow that records constructor and run arguments.

        Attributes:
            Constructor and run inputs are stored in the outer captured dict.
        """

        def __init__(self, data_config, sensitive_config):
            """Capture resolved wrapper configuration."""
            captured["config"] = data_config
            captured["sensitive"] = sensitive_config

        async def arun(
            self,
            user_query: str,
            is_rewrite: bool = True,
            thread_id: str | None = None,
        ) -> dict[str, object]:
            """Capture the graph invocation.

            Args:
                user_query: Query forwarded by the wrapper.
                is_rewrite: Whether rewrite mode is enabled.
                thread_id: Thread id derived from dialog id.

            Returns:
                Minimal success payload.
            """
            captured["run"] = {
                "user_query": user_query,
                "is_rewrite": is_rewrite,
                "thread_id": thread_id,
            }
            return {"ok": True}

        def captured_config(self) -> Any:
            """Return captured config for lint-friendly fake shape.

            Returns:
                Captured DataConfig-like object.
            """
            return captured["config"]

    def no_cache(name, factory, fingerprint_values=None):
        """Return a fresh fake agent.

        Args:
            name: Ignored cache name.
            factory: Factory used to create the fake agent.
            fingerprint_values: Ignored cache fingerprint values.

        Returns:
            New fake agent instance from ``factory``.
        """
        del name, fingerprint_values
        return factory()

    monkeypatch.setattr(data_agent_module, "DataAgent", FakeDataAgent)
    monkeypatch.setattr(data_agent_module, "get_cached_agent", no_cache)

    result = await data_agent_module.rewrite_nl2sql(
        "plant height in rice",
        is_rewrite=False,
        dialog_id="dialog-1",
    )

    assert result == {"ok": True}
    assert captured["config"].DIALOG_ID == "dialog-1"
    assert captured["run"] == {
        "user_query": "plant height in rice",
        "is_rewrite": False,
        "thread_id": "dialog-1",
    }


async def test_execute_nl2sql_returns_first_success_without_rotation(
    monkeypatch: pytest.MonkeyPatch,
):
    """A first-attempt success returns immediately, one conversation.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    fake = _FakePost([{"answer": "ok"}])
    _patch_transport(monkeypatch, fake)
    request = Nl2SqlRequest.from_kwargs(
        "homologs of AT1G75370 in wheat",
        {"dialog_id": "dialog-explicit", "max_retries": 3},
    )

    result = await execute_nl2sql_request(request)

    assert result == {"answer": "ok"}
    # Exactly one conversation, and it honored the caller's dialog id.
    assert fake.recorded_dialog_ids() == ["dialog-explicit"]


async def test_execute_nl2sql_rotates_dialog_id_on_retry(
    monkeypatch: pytest.MonkeyPatch,
):
    """A failed conversation is retried under a brand-new dialog id.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    fake = _FakePost([_mcp_error(), {"answer": "recovered"}])
    _patch_transport(monkeypatch, fake)
    request = Nl2SqlRequest.from_kwargs(
        "homologs of AT1G75370 in wheat",
        {"dialog_id": "dialog-explicit", "max_retries": 3},
    )

    result = await execute_nl2sql_request(request)

    assert result == {"answer": "recovered"}
    ids = fake.recorded_dialog_ids()
    assert fake.attempt_count() == 2
    # Attempt 0 keeps the caller's conversation; attempt 1 is fresh,
    # so the poisoned server-side cache slot is bypassed.
    assert ids[0] == "dialog-explicit"
    assert ids[1] != "dialog-explicit"
    assert "-dialog-" in ids[1]


async def test_execute_nl2sql_reraises_after_exhausting_rotations(
    monkeypatch: pytest.MonkeyPatch,
):
    """All conversations failing re-raises McpError, never returns None.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    fake = _FakePost([_mcp_error(), _mcp_error(), _mcp_error()])
    _patch_transport(monkeypatch, fake)
    request = Nl2SqlRequest.from_kwargs(
        "homologs of AT1G75370 in wheat",
        {"dialog_id": "dialog-explicit", "max_retries": 2},
    )

    with pytest.raises(McpError):
        await execute_nl2sql_request(request)

    # max_retries=2 -> 1 original + 2 rotated = 3 conversations,
    # each under a distinct dialog id.
    assert fake.attempt_count() == 3
    assert len(set(fake.recorded_dialog_ids())) == 3


async def test_execute_nl2sql_first_attempt_generates_dialog_when_absent(
    monkeypatch: pytest.MonkeyPatch,
):
    """With no caller dialog id, attempt 0 still uses a generated id.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    fake = _FakePost([{"answer": "ok"}])
    _patch_transport(monkeypatch, fake)
    request = Nl2SqlRequest.from_kwargs(
        "homologs of AT1G75370 in wheat",
        {"max_retries": 3},
    )

    result = await execute_nl2sql_request(request)

    assert result == {"answer": "ok"}
    assert "-dialog-" in fake.recorded_dialog_ids()[0]


async def test_execute_nl2sql_backs_off_only_between_rotations(
    monkeypatch: pytest.MonkeyPatch,
):
    """Backoff runs between failed attempts, not before 0 or after last.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    # A first-attempt success must never pause.
    ok = _FakePost([{"answer": "ok"}])
    _patch_transport(monkeypatch, ok)
    await execute_nl2sql_request(
        Nl2SqlRequest.from_kwargs("q", {"max_retries": 3})
    )
    assert not ok.recorded_backoffs()

    # The success above just populated the deterministic-input cache;
    # clear it so the next phase exercises the failure-rotation path
    # against the same NL question rather than short-circuiting on
    # the cached "ok" answer.
    nl2sql_module.clear_nl2sql_cache()

    # All three conversations fail: pause after attempts 0 and 1, but
    # not after the final (attempt 2) which re-raises immediately.
    fail = _FakePost([_mcp_error(), _mcp_error(), _mcp_error()])
    _patch_transport(monkeypatch, fail)
    with pytest.raises(McpError):
        await execute_nl2sql_request(
            Nl2SqlRequest.from_kwargs("q", {"max_retries": 2})
        )
    assert fail.recorded_backoffs() == [0, 1]
    assert fail.attempt_count() == 3


async def test_execute_nl2sql_dedupes_identical_questions_across_dialogs(
    monkeypatch: pytest.MonkeyPatch,
):
    """Identical NL questions hit the BI gateway once regardless of dialog id.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    fake = _FakePost([{"answer": "wheat orthologs"}])
    _patch_transport(monkeypatch, fake)

    # Two requests carry different dialog_id values (one explicit, one
    # auto-generated) but the same NL question / workspace / subject /
    # database / insight flags — exactly the inputs the cache keys on.
    first_request = Nl2SqlRequest.from_kwargs(
        "homologs of AT1G75370 in wheat",
        {"dialog_id": "dialog-explicit", "max_retries": 0},
    )
    second_request = Nl2SqlRequest.from_kwargs(
        "homologs of AT1G75370 in wheat",
        {"max_retries": 0},
    )
    assert (
        first_request.payload_data["dialog_id"]
        != second_request.payload_data["dialog_id"]
    )

    first = await execute_nl2sql_request(first_request)
    second = await execute_nl2sql_request(second_request)

    assert first == second == {"answer": "wheat orthologs"}
    # Only the first request roundtripped to the gateway; the second
    # call landed on the cached answer despite its fresh dialog_id.
    assert fake.attempt_count() == 1

    # Flipping the NL question must miss the cache and re-run the
    # rotation logic with a fresh BI gateway call.
    fake.outcomes.append({"answer": "rice orthologs"})
    third_request = Nl2SqlRequest.from_kwargs(
        "homologs of AT1G75370 in rice",
        {"max_retries": 0},
    )

    third = await execute_nl2sql_request(third_request)

    assert third == {"answer": "rice orthologs"}
    assert fake.attempt_count() == 2

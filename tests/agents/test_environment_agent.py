# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline smoke tests for the environment region VCI analysis wrapper.

Covers the happy path that extracts province, city, and county codes and
submits a VCI task, plus the failure path when region code extraction
returns no <result> match.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server_phytomni.agents.environment import agent as environment_agent

pytestmark = pytest.mark.agent


def _region_chat_response(content: str) -> dict[str, Any]:
    """Return a minimal chat-completion payload with the given content.

    Args:
        content: Assistant message content to embed in the fake response.

    Returns:
        Chat completion dictionary with one choice carrying ``content``.
    """
    return {"choices": [{"message": {"content": content}}]}


async def test_region_vci_analysis_extracts_codes_and_submits_task(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify region_vci_analysis extracts codes and submits a VCI task.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace external
            chat, prompt, storage, and analyst submit dependencies.

    Returns:
        None after wrapper output and submit forwarding assertions pass.
    """
    captured: dict[str, Any] = {}

    async def fake_chat_ainvoke(chat_input: dict[str, Any]) -> dict[str, Any]:
        """Return a fake chat-subgraph state with a region code response.

        Args:
            chat_input: ``ChatInput`` mapping forwarded by the wrapper
                (captured for inspection).

        Returns:
            ``ChatOutput``-shaped state whose ``response`` carries a
            <result>...</result> chat completion payload.
        """
        captured["chat"] = chat_input
        return {
            "response": _region_chat_response(
                "<result>110000|110100|110101</result>"
            )
        }

    fake_chat_app = SimpleNamespace(ainvoke=fake_chat_ainvoke)

    def fake_load_text_file(path: str) -> str:
        """Capture the requested region-code file path and return JSON.

        Args:
            path: Region code reference file path.

        Returns:
            Static JSON string standing in for the region info bundle.
        """
        captured["region_path"] = path
        return "{}"

    def fake_get_prompt(
        prompt_file: str,
        prompt_path: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Return a deterministic stub prompt string for the VCI workflow.

        Args:
            prompt_file: Prompt YAML file path.
            prompt_path: Prompt key path within the YAML file.
            params: Optional prompt rendering parameters.

        Returns:
            Deterministic prompt string keyed on ``prompt_path``.
        """
        assert prompt_file
        assert params is None or isinstance(params, dict)
        return f"prompt:{prompt_path}"

    def fake_get_data_list(
        data: Any,
        analysis_type: str,
        task_name: str,
    ) -> list[str]:
        """Return a deterministic VCI data list.

        Args:
            data: Environment-data bundle forwarded by the wrapper.
            analysis_type: Static analysis discriminator.
            task_name: Sub-task name discriminator.

        Returns:
            Static OBS data list used by the VCI task.
        """
        assert data is not None
        assert analysis_type == "environment_analysis"
        assert task_name == "vci_analysis"
        return ["obs://data/vci-1", "obs://data/vci-2"]

    def fake_create_output_dir(
        user_id: str | None,
        task: str,
        **obs_kwargs: Any,
    ) -> str:
        """Return a deterministic VCI output directory path.

        Args:
            user_id: Run-identity user id forwarded by the wrapper.
            task: Output bucket task discriminator.
            **obs_kwargs: OBS credentials and run-identity forwarded by
                the wrapper; presence is asserted but values are unused.

        Returns:
            Static OBS output directory used by the VCI task.
        """
        assert user_id is not None
        assert task == "vci_analysis_task"
        assert "run_identity" in obs_kwargs
        return "obs://phytomni/test/vci-out"

    async def fake_submit(**kwargs: Any) -> dict[str, Any]:
        """Capture submit kwargs and return a fake task payload.

        Args:
            **kwargs: Analyst submit keyword arguments forwarded by the
                environment wrapper.

        Returns:
            Fake VCI task dictionary echoing the captured goal_description.
        """
        captured["submit"] = kwargs
        return {"task_id": "vci-task-123"}

    # Pin ``USE_ANALYST_SUBGRAPH=False`` so the analyst submission
    # routes through the wrapper-namespace ``submit`` this test mocks
    # rather than ``submit_analyst_via_subgraph``. Region-code
    # extraction always runs through the compiled chat subgraph, so
    # ``_cached_chat_app`` is stubbed below to drive that path.
    monkeypatch.setattr(
        environment_agent.ENVIRONMENT_CONFIG, "USE_ANALYST_SUBGRAPH", False
    )
    monkeypatch.setattr(
        environment_agent, "_cached_chat_app", lambda: fake_chat_app
    )
    monkeypatch.setattr(
        environment_agent, "load_text_file", fake_load_text_file
    )
    monkeypatch.setattr(environment_agent, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(environment_agent, "get_data_list", fake_get_data_list)
    monkeypatch.setattr(
        environment_agent, "create_output_dir", fake_create_output_dir
    )
    monkeypatch.setattr(environment_agent, "submit", fake_submit)

    result = await environment_agent.region_vci_analysis(
        query="Analyze Beijing vegetation index",
        user_id="test-user",
    )

    assert result == {"vci_analysis_task": {"task_id": "vci-task-123"}}
    assert captured["submit"]["goal_description"] == (
        "prompt:user/environment/vci_analysis"
    )
    assert captured["submit"]["data_list"] == [
        "obs://data/vci-1",
        "obs://data/vci-2",
    ]
    assert captured["submit"]["output_dir"] == "obs://phytomni/test/vci-out"
    assert captured["submit"]["meta"] == (
        "prompt:user/environment/vci_analysis_meta"
    )


async def test_region_vci_analysis_returns_none_task_when_codes_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify region_vci_analysis returns a None task when codes are absent.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the chat
            and prompt loaders.

    Returns:
        None after the early-return assertion passes.
    """

    async def fake_chat_ainvoke(chat_input: dict[str, Any]) -> dict[str, Any]:
        """Return a chat-subgraph state missing the expected <result> tag.

        Args:
            chat_input: ``ChatInput`` mapping forwarded by the wrapper.

        Returns:
            ``ChatOutput``-shaped state whose ``response`` content has no
            <result>...</result> match.
        """
        assert "user_query" in chat_input
        return {"response": _region_chat_response("no parseable result here")}

    fake_chat_app = SimpleNamespace(ainvoke=fake_chat_ainvoke)

    def fake_load_text_file(path: str) -> str:
        """Return an empty JSON object for any region-code file lookup.

        Args:
            path: Region code reference file path.

        Returns:
            Empty JSON object as a string.
        """
        assert path
        return "{}"

    def fake_get_prompt(
        prompt_file: str,
        prompt_path: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Return a deterministic prompt string.

        Args:
            prompt_file: Prompt YAML file path.
            prompt_path: Prompt key path within the YAML file.
            params: Optional prompt rendering parameters.

        Returns:
            Deterministic prompt string keyed on ``prompt_path``.
        """
        assert prompt_file
        assert params is None or isinstance(params, dict)
        return f"prompt:{prompt_path}"

    monkeypatch.setattr(
        environment_agent, "_cached_chat_app", lambda: fake_chat_app
    )
    monkeypatch.setattr(
        environment_agent, "load_text_file", fake_load_text_file
    )
    monkeypatch.setattr(environment_agent, "get_prompt", fake_get_prompt)

    result = await environment_agent.region_vci_analysis(
        query="Unparseable region request",
    )

    assert result == {"vci_analysis_task": None}

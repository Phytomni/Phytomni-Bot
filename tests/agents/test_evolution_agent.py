# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline smoke tests for the evolution analysis wrapper.

Covers the happy path that resolves target species to taxonomy ids and
submits an evolution task, and the failure path where the chat client
returns no response and the wrapper short-circuits.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.agents.evolution import agent as evolution_agent

pytestmark = pytest.mark.agent


async def test_evo_test_analysis_with_all_species_submits_task(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify evo_test_analysis submits a task when target species is All.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace external
            chat, prompt, storage, and analyst submit dependencies.

    Returns:
        None after wrapper output and submit forwarding assertions pass.
    """
    captured: dict[str, Any] = {}

    async def fake_phyto_chat(**kwargs: Any) -> dict[str, Any]:
        """Return a chat response whose target list is the All sentinel.

        Args:
            **kwargs: phyto_chat keyword arguments captured for inspection.

        Returns:
            Fake chat completion encoding target_spa_list=["All"].
        """
        captured["chat"] = kwargs
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"target_spa_list": ["All"]}',
                    }
                }
            ]
        }

    def fake_get_prompt(
        prompt_file: str,
        prompt_path: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Capture prompt lookups and return a deterministic stub.

        Args:
            prompt_file: Prompt YAML file path.
            prompt_path: Prompt key path within the YAML file.
            params: Optional prompt rendering parameters.

        Returns:
            Deterministic prompt string keyed on ``prompt_path``.
        """
        captured.setdefault("prompts", []).append(
            {
                "prompt_file": prompt_file,
                "prompt_path": prompt_path,
                "params": params,
            }
        )
        return f"prompt:{prompt_path}"

    def fake_get_data_list(*args: Any, **kwargs: Any) -> list[str]:
        """Return a deterministic data list and capture call inputs.

        Args:
            *args: Positional arguments forwarded by the wrapper.
            **kwargs: Keyword arguments forwarded by the wrapper.

        Returns:
            Static OBS data list used by the evolution task.
        """
        captured["data_list_call"] = {"args": args, "kwargs": kwargs}
        return ["obs://data/evo-1", "obs://data/evo-2"]

    def fake_create_output_dir(*args: Any, **kwargs: Any) -> str:
        """Return a deterministic output directory path.

        Args:
            *args: Positional arguments forwarded by the wrapper.
            **kwargs: Keyword arguments forwarded by the wrapper.

        Returns:
            Static OBS output directory used by the evolution task.
        """
        captured["output_dir_call"] = {"args": args, "kwargs": kwargs}
        return "obs://phytomni/test/evo-out"

    async def fake_submit(**kwargs: Any) -> dict[str, Any]:
        """Capture submit kwargs and return a fake task payload.

        Args:
            **kwargs: Analyst submit keyword arguments forwarded by the
                evolution wrapper.

        Returns:
            Fake evolution task dictionary.
        """
        captured["submit"] = kwargs
        return {"task_id": "evo-task-456"}

    monkeypatch.setattr(evolution_agent, "phyto_chat", fake_phyto_chat)
    monkeypatch.setattr(evolution_agent, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(evolution_agent, "get_data_list", fake_get_data_list)
    monkeypatch.setattr(
        evolution_agent, "create_output_dir", fake_create_output_dir
    )
    monkeypatch.setattr(evolution_agent, "submit", fake_submit)

    result = await evolution_agent.evo_test_analysis(
        query="Compare PHYB across species",
        species="osa",
        gene_id="AtPHYB",
        user_id="test-user",
    )

    assert result == {"evolution_agents_task": {"task_id": "evo-task-456"}}
    assert captured["submit"]["goal_description"] == (
        "prompt:user/evolution_agents_analysis"
    )
    assert captured["submit"]["data_list"] == [
        "obs://data/evo-1",
        "obs://data/evo-2",
    ]
    assert captured["submit"]["output_dir"] == "obs://phytomni/test/evo-out"
    assert captured["submit"]["meta"] == ("prompt:user/evolution_agents_meta")
    submit_prompt_params = [
        p["params"]
        for p in captured["prompts"]
        if p["prompt_path"] == "user/evolution_agents_analysis"
    ][0]
    assert submit_prompt_params == {
        "gene_id": "AtPHYB",
        "target_taxid": "All",
    }


async def test_evo_test_analysis_returns_none_task_when_chat_returns_none(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify evo_test_analysis short-circuits when phyto_chat returns None.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to replace the chat
            and prompt loaders.

    Returns:
        None after the early-return assertion passes.
    """

    async def fake_phyto_chat(**kwargs: Any) -> None:
        """Return None to trigger the _target_taxids early-return path.

        Args:
            **kwargs: phyto_chat keyword arguments captured for inspection.

        Returns:
            None, simulating a failed or skipped chat completion.
        """
        assert "user_query" in kwargs
        return None

    def fake_get_prompt(
        prompt_file: str,
        prompt_path: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Return a deterministic prompt string for any lookup.

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

    monkeypatch.setattr(evolution_agent, "phyto_chat", fake_phyto_chat)
    monkeypatch.setattr(evolution_agent, "get_prompt", fake_get_prompt)

    result = await evolution_agent.evo_test_analysis(
        query="ambiguous request",
        species="osa",
        gene_id="AtPHYB",
    )

    assert result == {"evolution_task": None}

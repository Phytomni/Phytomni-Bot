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

from contextlib import asynccontextmanager
from typing import Any

import httpx
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

    def fake_get_data_list(
        data: Any,
        analysis_type: str,
        species_code: str,
    ) -> list[str]:
        """Return a deterministic evolution data list.

        Args:
            data: Deepgenome-data bundle forwarded by the wrapper.
            analysis_type: Static analysis discriminator.
            species_code: Source species code forwarded by the wrapper.

        Returns:
            Static OBS data list used by the evolution task.
        """
        assert data is not None
        assert analysis_type == "evolution_analysis"
        assert species_code == "osa"
        return ["obs://data/evo-1", "obs://data/evo-2"]

    def fake_create_output_dir(**obs_kwargs: Any) -> str:
        """Return a deterministic evolution output directory path.

        Args:
            **obs_kwargs: OBS credentials, task name, user id, and
                run-identity forwarded by the wrapper.

        Returns:
            Static OBS output directory used by the evolution task.
        """
        assert obs_kwargs["task"] == "evolution_agents_task"
        assert "user_id" in obs_kwargs
        assert "run_identity" in obs_kwargs
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

    # Pin the legacy paths the rest of this test mocks: with both
    # default-True flags the wrapper would route through the chat
    # subgraph + ``submit_analyst_via_subgraph`` and skip the
    # ``phyto_chat`` / ``submit`` mocks installed below.
    monkeypatch.setattr(
        evolution_agent.DEEP_GENOME_CONFIG, "USE_CHAT_SUBGRAPH", False
    )
    monkeypatch.setattr(
        evolution_agent.DEEP_GENOME_CONFIG, "USE_ANALYST_SUBGRAPH", False
    )
    monkeypatch.setattr(evolution_agent, "phyto_chat", fake_phyto_chat)
    monkeypatch.setattr(evolution_agent, "get_prompt", fake_get_prompt)
    monkeypatch.setattr(evolution_agent, "get_data_list", fake_get_data_list)
    monkeypatch.setattr(
        evolution_agent, "create_output_dir", fake_create_output_dir
    )
    monkeypatch.setattr(evolution_agent, "submit", fake_submit)

    result = await evolution_agent.evo_test_analysis(
        query="Compare PHYB across species",
        species_code="osa",
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

    monkeypatch.setattr(
        evolution_agent.DEEP_GENOME_CONFIG, "USE_CHAT_SUBGRAPH", False
    )
    monkeypatch.setattr(evolution_agent, "phyto_chat", fake_phyto_chat)
    monkeypatch.setattr(evolution_agent, "get_prompt", fake_get_prompt)

    result = await evolution_agent.evo_test_analysis(
        query="ambiguous request",
        species_code="osa",
        gene_id="AtPHYB",
    )

    assert result == {"evolution_agents_task": None}


async def test_find_spa_taxids_uses_async_httpx_factory(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify the taxid lookup drives the shared async httpx factory.

    The previous implementation called ``requests.get`` synchronously,
    blocking the event loop and bypassing the central TLS resolver.
    This test mocks ``get_async_client`` so the captured kwargs and the
    JSON parsing both surface through the new async path.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap the auth
            token loader and the shared HTTP client factory.
    """
    captured: dict[str, Any] = {}

    async def fake_get_token() -> str:
        """Return a deterministic IAM token for the request headers."""
        return "fake-iam-token"

    monkeypatch.setattr(evolution_agent, "get_token", fake_get_token)

    @asynccontextmanager
    async def fake_factory(**factory_kwargs: Any):
        """Hand out a stub client that records the parameters passed in."""
        captured["factory_kwargs"] = factory_kwargs

        class _Client:
            """Stub async httpx client whose ``get`` returns a fixed body."""

            async def get(self, url: str, **call_kwargs: Any) -> Any:
                """Capture the request and return a two-record payload."""
                captured["url"] = url
                captured["call_kwargs"] = call_kwargs
                return httpx.Response(
                    200,
                    json={
                        "total": 2,
                        "records": [
                            {"answer": "9606. Homo sapiens"},
                            {"answer": "10090. Mus musculus"},
                        ],
                    },
                )

        yield _Client()

    monkeypatch.setattr(evolution_agent, "get_async_client", fake_factory)

    taxids = await evolution_agent.find_spa_taxids("Arabidopsis", timeout=12.0)

    assert taxids == ["9606", "10090"]
    assert captured["factory_kwargs"]["timeout"] == 12.0
    assert captured["factory_kwargs"]["trust_env"] is False
    assert (
        captured["call_kwargs"]["headers"]["X-Auth-Token"] == "fake-iam-token"
    )
    assert captured["call_kwargs"]["params"]["question"] == "Arabidopsis"


async def test_find_spa_taxids_returns_empty_on_non_200(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify the lookup short-circuits to an empty list on non-200.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap the auth
            token loader and the shared HTTP client factory.
    """

    async def fake_get_token() -> str:
        """Return a deterministic IAM token."""
        return "fake-iam-token"

    monkeypatch.setattr(evolution_agent, "get_token", fake_get_token)

    @asynccontextmanager
    async def fake_factory(**factory_kwargs: Any):
        """Yield a stub client that always returns a 502."""
        del factory_kwargs

        class _Client:
            """Stub async httpx client whose ``get`` always errors."""

            async def get(self, url: str, **call_kwargs: Any) -> Any:
                """Discard the request and return a 502 response."""
                del url, call_kwargs
                return httpx.Response(502, text="bad gateway")

        yield _Client()

    monkeypatch.setattr(evolution_agent, "get_async_client", fake_factory)

    taxids = await evolution_agent.find_spa_taxids("oryza", timeout=1.0)

    assert taxids == []

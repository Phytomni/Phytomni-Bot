# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Regression test for the exported network->deepgenome chain.

Locks AF-002: ``network_to_deep_genome_chain`` must translate its Latin
``species`` argument to a ``species_code`` and forward it under the
``species_code=`` kwarg to ``network_analysis`` (renamed by the
species-key rename), never the old ``species=`` kwarg with an
untranslated Latin name.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import traceback
from concurrent.futures import Executor, Future
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from mcp_server_phytomni.agents.analyst.agent import AnalystAgent
from mcp_server_phytomni.agents.network import agent as network_agent
from mcp_server_phytomni.agents.network import chain
from mcp_server_phytomni.agents.network.agent import (
    GeneNetworkAgents,
    GeneNetworkConfig,
    _network_submission_outcome,
    _project_network_submission_update,
)
from mcp_server_phytomni.agents.shared.remote_analysis import (
    RemoteAnalysisSubmissionError,
)
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.runtime.outbound import (
    ObsClientRuntime,
    OutboundPoolName,
)
from mcp_server_phytomni.runtime.outbound.registry import OutboundPoolRegistry
from mcp_server_phytomni.storage.obs_storage import ObsPathError
from tests.support.analysis_states import install_analysis_prompt_parts

pytestmark = pytest.mark.agent
_standard_to_thread = asyncio.to_thread


class _ImmediateExecutor(Executor):
    """Execute submitted test work immediately without owning loop state."""

    def submit(
        self,
        fn: Any,
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future[Any]:
        """Return a completed future for one submitted callable."""
        future: Future[Any] = Future()
        future.set_result(fn(*args, **kwargs))
        return future

    def shutdown(
        self,
        wait: bool = True,
        *,
        cancel_futures: bool = False,
    ) -> None:
        """Match the executor lifecycle contract without shared resources."""
        del wait, cancel_futures


def _asyncio_with_private_executor(
    executor: Executor,
) -> SimpleNamespace:
    """Return the chain's asyncio seam with isolated thread execution."""

    async def to_thread(
        function: Any,
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Run one blocking call without changing the loop's executor."""
        call = functools.partial(function, *args, **kwargs)
        return await asyncio.get_running_loop().run_in_executor(executor, call)

    return SimpleNamespace(gather=asyncio.gather, to_thread=to_thread)


def _assert_default_to_thread_usable(
    original_executor: Any,
    original_shutdown_state: bool,
) -> None:
    """Prove the ordinary to-thread owner was neither replaced nor closed."""
    loop = asyncio.get_running_loop()
    assert asyncio.to_thread is _standard_to_thread
    assert getattr(loop, "_default_executor", None) is original_executor
    assert (
        getattr(loop, "_executor_shutdown_called", False)
        is original_shutdown_state
    )


def test_chain_top20_missing_error_preserves_public_fields() -> None:
    """The exported error retains its original fields and default text."""
    error = chain.ChainTop20MissingError(
        output_dir="/obs/phytomni/results",
        filename="*top20_gene.csv",
        message="top-20 result is unavailable",
    )

    assert error.output_dir == "/obs/phytomni/results"
    assert error.filename == "*top20_gene.csv"
    assert str(error) == "[Errno None] None: '*top20_gene.csv'"
    assert error.args == (
        "top-20 result is unavailable "
        "(output_dir=/obs/phytomni/results, filename=*top20_gene.csv)",
    )


async def test_chain_forwards_species_code_to_network_analysis(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The chain forwards species_code='osa', never species='oryza sativa'."""
    net_mock = AsyncMock(
        return_value={
            "network_task": {"output_dir": "obs://run/net-out"},
            "output_dir": "obs://run/net-out",
            "task_id": "net-task",
        }
    )
    gene_mock = AsyncMock(
        return_value={"task_id": "g1", "output_dir": "obs://g1"}
    )
    monkeypatch.setattr(chain, "network_analysis", net_mock)
    monkeypatch.setattr(chain, "gene_function", gene_mock)
    monkeypatch.setattr(chain, "_scratch_dir_for", lambda _uid: tmp_path)
    monkeypatch.setattr(
        chain,
        "_download_top20_csv",
        AsyncMock(return_value=tmp_path / "x.csv"),
    )
    monkeypatch.setattr(
        chain, "_parse_top20_gene_ids", lambda _p: ["Os01g0100100"]
    )

    await chain.network_to_deep_genome_chain("oryza sativa", "TO:0000207")

    net_mock.assert_awaited_once()
    net_call = net_mock.await_args
    assert net_call is not None
    assert net_call.kwargs["species_code"] == "osa"
    assert "species" not in net_call.kwargs
    gene_call = gene_mock.await_args
    assert gene_call is not None
    assert gene_call.kwargs["species_code"] == "osa"


async def test_chain_local_top20_lookup_uses_no_obs_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A mounted top-20 file is resolved before OBS capacity is acquired."""
    bucket_name = GeneNetworkConfig().BUCKET_NAME
    output_dir = f"/obs/{bucket_name}/results"
    csv_path = tmp_path / "TO_0000207_top20_gene.csv"
    csv_path.write_text("gene_id\nOs01g0100100\n", encoding="utf-8")

    obs_run = AsyncMock(
        side_effect=AssertionError("local lookup acquired an OBS lease")
    )
    runtime = SimpleNamespace(run=obs_run)
    monkeypatch.setattr(
        chain,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=runtime),
    )
    monkeypatch.setattr(chain, "obsfs_path_for", lambda *_args: tmp_path)
    monkeypatch.setattr(chain, "_scratch_dir_for", lambda _uid: tmp_path)
    monkeypatch.setattr(
        chain,
        "network_analysis",
        AsyncMock(
            return_value={
                "network_task": {"output_dir": output_dir},
                "output_dir": output_dir,
                "task_id": "net-task",
            }
        ),
    )
    monkeypatch.setattr(
        chain,
        "download_obs_file",
        AsyncMock(return_value=str(csv_path)),
    )
    monkeypatch.setattr(
        chain,
        "gene_function",
        AsyncMock(return_value={"task_id": "gene-task"}),
    )

    loop = asyncio.get_running_loop()
    default_executor = getattr(loop, "_default_executor", None)
    executor_shutdown = getattr(loop, "_executor_shutdown_called", False)
    executor = _ImmediateExecutor()
    monkeypatch.setattr(
        chain,
        "asyncio",
        _asyncio_with_private_executor(executor),
    )
    try:
        async with asyncio.timeout(15):
            result = await chain.network_to_deep_genome_chain(
                "oryza sativa",
                "TO:0000207",
            )
    finally:
        executor.shutdown(wait=True)

    assert result["gene_ids"] == ["Os01g0100100"]
    assert obs_run.await_count == 0
    _assert_default_to_thread_usable(default_executor, executor_shutdown)


async def test_chain_rejects_invalid_sdk_prefix_before_obs_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An invalid network output prefix fails before OBS acquisition."""
    bucket_name = GeneNetworkConfig().BUCKET_NAME
    output_dir = "/obs/outside-bucket/private-results"
    list_objects = Mock()
    client = SimpleNamespace(listObjects=list_objects)
    pools = OutboundPoolRegistry(
        {
            name: (1 if name is OutboundPoolName.OBS else 0)
            for name in OutboundPoolName
        },
        wait_warn_seconds=1.0,
    )
    obs_runtime = ObsClientRuntime(pools, client)
    monkeypatch.setattr(
        chain,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=obs_runtime),
    )
    monkeypatch.setattr(chain, "_scratch_dir_for", lambda _uid: tmp_path)
    monkeypatch.setattr(
        chain,
        "network_analysis",
        AsyncMock(
            return_value={
                "network_task": {"output_dir": output_dir},
                "output_dir": output_dir,
                "task_id": "net-task",
            }
        ),
    )

    try:
        with pytest.raises(chain.ChainTop20MissingError) as captured:
            async with asyncio.timeout(15):
                await chain.network_to_deep_genome_chain(
                    "oryza sativa",
                    "TO:0000207",
                )
        snapshot = pools.snapshot(OutboundPoolName.OBS)
    finally:
        await obs_runtime.aclose()
        await pools.aclose()

    path_error = (
        f"OBS path points outside bucket '{bucket_name}': {output_dir}"
    )
    assert captured.value.filename == chain.TOP20_GLOB
    assert captured.value.args == (
        f"no file matching {chain.TOP20_GLOB!r} under network output_dir; "
        f"last error: {path_error} "
        f"(output_dir={output_dir}, filename={chain.TOP20_GLOB})",
    )
    assert isinstance(captured.value.__cause__, ObsPathError)
    assert str(captured.value.__cause__) == path_error
    list_objects.assert_not_called()
    assert snapshot.started == 0
    assert snapshot.in_use == 0
    assert snapshot.waiting == 0


async def test_chain_sdk_top20_lookup_releases_lease_between_pages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Each SDK fallback page is listed under its own OBS lease."""
    bucket_name = GeneNetworkConfig().BUCKET_NAME
    output_dir = f"/obs/{bucket_name}/results"
    csv_path = tmp_path / "TO_0000207_top20_gene.csv"
    csv_path.write_text("gene_id\nOs01g0100100\n", encoding="utf-8")
    list_objects = Mock(
        side_effect=[
            SimpleNamespace(
                status=200,
                body=SimpleNamespace(
                    contents=[SimpleNamespace(key="results/notes.txt")],
                    is_truncated=True,
                    next_marker="page-2",
                ),
            ),
            SimpleNamespace(
                status=200,
                body=SimpleNamespace(
                    contents=[
                        SimpleNamespace(
                            key="results/TO_0000207_top20_gene.csv"
                        )
                    ],
                    is_truncated=False,
                ),
            ),
        ]
    )
    client = SimpleNamespace(listObjects=list_objects)

    async def run(_profile: Any, operation: Any) -> Any:
        """Execute one lease-scoped SDK page against the fake client."""
        return operation(client)

    obs_run = AsyncMock(side_effect=run)
    runtime = SimpleNamespace(run=obs_run)
    monkeypatch.setattr(
        chain,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=runtime),
    )
    monkeypatch.setattr(
        chain,
        "obsfs_path_for",
        lambda *_args: tmp_path / "missing",
    )
    monkeypatch.setattr(chain, "_scratch_dir_for", lambda _uid: tmp_path)
    monkeypatch.setattr(
        chain,
        "network_analysis",
        AsyncMock(
            return_value={
                "network_task": {"output_dir": output_dir},
                "output_dir": output_dir,
                "task_id": "net-task",
            }
        ),
    )
    monkeypatch.setattr(
        chain,
        "download_obs_file",
        AsyncMock(return_value=str(csv_path)),
    )
    monkeypatch.setattr(
        chain,
        "gene_function",
        AsyncMock(return_value={"task_id": "gene-task"}),
    )

    loop = asyncio.get_running_loop()
    default_executor = getattr(loop, "_default_executor", None)
    executor_shutdown = getattr(loop, "_executor_shutdown_called", False)
    executor = _ImmediateExecutor()
    monkeypatch.setattr(
        chain,
        "asyncio",
        _asyncio_with_private_executor(executor),
    )
    try:
        async with asyncio.timeout(15):
            result = await chain.network_to_deep_genome_chain(
                "oryza sativa",
                "TO:0000207",
            )
    finally:
        executor.shutdown(wait=True)

    assert result["gene_ids"] == ["Os01g0100100"]
    assert [call.kwargs["marker"] for call in list_objects.call_args_list] == [
        None,
        "page-2",
    ]
    assert obs_run.await_count == 2
    _assert_default_to_thread_usable(default_executor, executor_shutdown)


@pytest.mark.parametrize(
    "provider_raises",
    [True, False],
    ids=["provider-error", "no-match"],
)
async def test_chain_sdk_missing_is_sanitized_and_releases_obs_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    provider_raises: bool,
) -> None:
    """SDK failure and no-match share one stable, resource-safe error."""
    caplog.set_level(logging.DEBUG)
    bucket_name = GeneNetworkConfig().BUCKET_NAME
    output_dir = f"/obs/{bucket_name}/results"
    provider_marker = "marker=private-page requestId=secret-request"
    close = Mock()
    if provider_raises:
        list_objects = Mock(
            side_effect=OSError(f"{provider_marker} errorCode=SecretDenied")
        )
    else:
        list_objects = Mock(
            return_value=SimpleNamespace(
                status=200,
                body=SimpleNamespace(contents=[], is_truncated=False),
            )
        )
    client = SimpleNamespace(
        listObjects=list_objects,
        close=close,
    )
    pools = OutboundPoolRegistry(
        {
            name: (1 if name is OutboundPoolName.OBS else 0)
            for name in OutboundPoolName
        },
        wait_warn_seconds=1.0,
    )
    obs_runtime = ObsClientRuntime(pools, client)
    monkeypatch.setattr(
        chain,
        "current_outbound_runtime",
        lambda: SimpleNamespace(obs=obs_runtime),
    )
    monkeypatch.setattr(
        chain,
        "obsfs_path_for",
        lambda *_args: tmp_path / "missing",
    )
    monkeypatch.setattr(chain, "_scratch_dir_for", lambda _uid: tmp_path)
    monkeypatch.setattr(
        chain,
        "network_analysis",
        AsyncMock(
            return_value={
                "network_task": {"output_dir": output_dir},
                "output_dir": output_dir,
                "task_id": "net-task",
            }
        ),
    )

    try:
        with pytest.raises(chain.ChainTop20MissingError) as captured:
            async with asyncio.timeout(15):
                await chain.network_to_deep_genome_chain(
                    "oryza sativa",
                    "TO:0000207",
                )

        expected_message = (
            "no file matching '*top20_gene.csv' under network output_dir; "
            "last error: no SDK match "
            f"(output_dir={output_dir}, filename=*top20_gene.csv)"
        )
        assert str(captured.value) == "[Errno None] None: '*top20_gene.csv'"
        assert captured.value.args == (expected_message,)
        assert captured.value.filename == chain.TOP20_GLOB
        assert isinstance(captured.value.__cause__, FileNotFoundError)
        assert str(captured.value.__cause__) == "no SDK match"
        assert provider_marker not in str(captured.value)
        assert provider_marker not in str(captured.value.__cause__)
        assert provider_marker not in "".join(
            traceback.format_exception(captured.value)
        )
        assert provider_marker not in caplog.text
        snapshot = pools.snapshot(OutboundPoolName.OBS)
        assert snapshot.in_use == 0
        assert snapshot.waiting == 0
        assert snapshot.started == 1
        assert snapshot.completed == int(not provider_raises)
        assert snapshot.failed == int(provider_raises)
    finally:
        await obs_runtime.aclose()
        await pools.aclose()

    close.assert_called_once_with()


async def test_network_rejects_blank_remote_task_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network treats a successful response without an ID as rejected."""
    analyst_stub = SimpleNamespace(identifier=lambda: "stub-analyst")
    agent = GeneNetworkAgents(
        gene_network_config=GeneNetworkConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
    )
    install_analysis_prompt_parts(monkeypatch, agent)
    monkeypatch.setattr(
        network_agent,
        "submit_analyst_via_subgraph",
        AsyncMock(return_value={"output_dir": "out"}),
    )

    with pytest.raises(
        RemoteAnalysisSubmissionError,
        match="omitted task_id",
    ):
        await getattr(agent, "_dispatch_and_wait_analysis")(
            "gene_network_analysis",
            "osa",
            "TO:0000207",
        )


async def test_network_arun_rejects_blank_id_in_final_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blank graph result cannot bypass the all-rejected guard."""
    analyst_stub = SimpleNamespace(identifier=lambda: "stub-analyst")
    agent = GeneNetworkAgents(
        gene_network_config=GeneNetworkConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
    )
    monkeypatch.setattr(
        network_agent,
        "run_analysis_graph",
        AsyncMock(
            return_value={
                "network_task": {"task_id": "   "},
                "phytomni_state": {},
            }
        ),
    )

    with pytest.raises(
        RemoteAnalysisSubmissionError,
        match="no remote task was accepted",
    ):
        await agent.arun("osa", "TO:0000207")


def test_network_outcome_skips_projector_doomed_task_ids() -> None:
    """Synthetic rejected-* network ids must not count as accepted."""
    updates = _project_network_submission_update(
        {
            "network_task": {
                "analysis_type": "gene_network_analysis",
                "_submission_rejected": {
                    "goal": "TO:0000207",
                    "code": "upstream_rejected",
                },
            }
        }
    )
    outcome = _network_submission_outcome(
        {
            **updates,
            "phytomni_state": {
                "submission_rejections": updates.get("submission_rejections")
            },
        }
    )
    assert outcome.kind == "rejected"
    assert outcome.task_ids == ()
    doomed = updates["network_task"]
    assert doomed["accepted"] is False
    assert str(doomed["task_id"]).startswith("rejected-")


async def test_network_arun_rejects_projector_doomed_task_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native MCP must not return unpollable rejected-* as accepted ids."""
    analyst_stub = SimpleNamespace(identifier=lambda: "stub-analyst")
    agent = GeneNetworkAgents(
        gene_network_config=GeneNetworkConfig(),
        sensitive_config=SensitiveConfig.load(),
        analyst_agent=cast(AnalystAgent, analyst_stub),
    )
    monkeypatch.setattr(
        network_agent,
        "run_analysis_graph",
        AsyncMock(
            return_value={
                "network_task": {
                    "task_id": "rejected-gene_network_analysis",
                    "accepted": False,
                    "status": "failed",
                    "analysis_type": "gene_network_analysis",
                    "error_code": "upstream_rejected",
                },
                "phytomni_state": {
                    "submission_rejections": [
                        {"goal": "TO:0000207", "code": "upstream_rejected"}
                    ]
                },
            }
        ),
    )

    with pytest.raises(
        RemoteAnalysisSubmissionError,
        match="no remote task was accepted",
    ):
        await agent.arun("osa", "TO:0000207")

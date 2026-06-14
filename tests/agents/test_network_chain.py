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

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mcp_server_phytomni.agents.network import chain

pytestmark = pytest.mark.agent


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

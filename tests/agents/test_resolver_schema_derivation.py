# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Resolver JSON schemas are derived from their Candidate models.

Pins that brief_gene and network no longer hand-maintain a second copy
of the candidate confidence constraint: the public resolver wrappers pass
an inner schema built from the pydantic model to the LLM, so the two
cannot drift.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.brief_gene import resolve_query as bg
from mcp_server_phytomni.agents.network import resolve_query as nw
from mcp_server_phytomni.config.defaults import (
    BriefGeneConfig,
    GeneNetworkConfig,
)
from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = [pytest.mark.unit, pytest.mark.agent]


def _candidate_confidence(schema: dict[str, Any]) -> dict[str, Any]:
    items = schema["json_schema"]["schema"]["properties"]["candidates"][
        "items"
    ]
    return items["properties"]["confidence"]


async def _capture_brief_gene_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Capture the response schema emitted by the public resolver wrapper."""
    captured: dict[str, Any] = {}

    async def fake_phyto_chat(**kwargs: Any) -> dict[str, Any]:
        captured["schema"] = kwargs["response_format"]
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"gene_id":"AT1G01010",'
                        '"species_code":"ath"}',
                    }
                }
            ]
        }

    monkeypatch.setattr(bg, "phyto_chat", fake_phyto_chat)
    await bg.resolve_brief_gene_user_query(
        "AT1G01010 in Arabidopsis",
        brief_config=BriefGeneConfig(),
        sensitive_config=SensitiveConfig.load(),
    )
    return cast(dict[str, Any], captured["schema"])


async def _capture_network_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Capture the response schema emitted by the public network resolver."""
    captured: dict[str, Any] = {}

    async def fake_phyto_chat(**kwargs: Any) -> dict[str, Any]:
        captured["schema"] = kwargs["response_format"]
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"to_id":"TO:0000207",'
                        '"species_code":"osa"}',
                    }
                }
            ]
        }

    monkeypatch.setattr(nw, "phyto_chat", fake_phyto_chat)
    await nw.resolve_network_user_query(
        "plant height in rice",
        network_config=GeneNetworkConfig(),
        sensitive_config=SensitiveConfig.load(),
    )
    return cast(dict[str, Any], captured["schema"])


async def test_brief_gene_schema_confidence_matches_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """brief_gene candidate confidence bounds come from the model."""
    conf = _candidate_confidence(await _capture_brief_gene_schema(monkeypatch))
    assert conf["minimum"] == 0.0
    assert conf["maximum"] == 1.0


async def test_network_schema_confidence_matches_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """network candidate confidence bounds come from the model."""
    conf = _candidate_confidence(await _capture_network_schema(monkeypatch))
    assert conf["minimum"] == 0.0
    assert conf["maximum"] == 1.0


async def test_brief_gene_schema_outer_name_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The outer json_schema wrapper name is unchanged (LLM contract)."""
    schema = await _capture_brief_gene_schema(monkeypatch)
    assert schema["json_schema"]["name"] == "BriefGeneResolution"


async def test_network_schema_outer_name_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The outer json_schema wrapper name is unchanged (LLM contract)."""
    schema = await _capture_network_schema(monkeypatch)
    assert schema["json_schema"]["name"] == "GeneNetworkResolution"


async def test_brief_gene_candidate_schema_is_model_derived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The candidate item schema equals the model's json schema subset."""
    model_schema = bg.BriefGeneIdCandidate.model_json_schema()
    schema = await _capture_brief_gene_schema(monkeypatch)
    items = schema["json_schema"]["schema"]["properties"]["candidates"][
        "items"
    ]
    assert (
        items["properties"]["confidence"]["maximum"]
        == model_schema["properties"]["confidence"]["maximum"]
    )
    assert (
        items["properties"]["confidence"]["minimum"]
        == model_schema["properties"]["confidence"]["minimum"]
    )


async def test_network_candidate_schema_is_model_derived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The candidate item schema equals the model's json schema subset."""
    model_schema = nw.GeneNetworkToIdCandidate.model_json_schema()
    schema = await _capture_network_schema(monkeypatch)
    items = schema["json_schema"]["schema"]["properties"]["candidates"][
        "items"
    ]
    assert (
        items["properties"]["confidence"]["maximum"]
        == model_schema["properties"]["confidence"]["maximum"]
    )
    assert (
        items["properties"]["confidence"]["minimum"]
        == model_schema["properties"]["confidence"]["minimum"]
    )

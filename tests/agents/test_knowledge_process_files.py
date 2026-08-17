# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Knowledge process_files_node upload-context branches."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.knowledge import agent as knowledge_mod
from mcp_server_phytomni.agents.knowledge.agent import KnowledgeAgent
from mcp_server_phytomni.agents.knowledge.state import KnowledgeState
from mcp_server_phytomni.config.models.agents import KnowledgeConfig

pytestmark = pytest.mark.unit


async def test_process_files_node_formats_obs_uploads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OBS uploads become upload_context; an empty list stays empty."""

    async def fake_download(**kwargs: Any) -> list[str]:
        assert kwargs["obs_file_list"] == ["obs://bucket/a.md"]
        return ["file body"]

    def fake_format(texts: list[str], **_kwargs: Any) -> tuple[str, int]:
        assert texts == ["file body"]
        return "formatted-upload", 12

    monkeypatch.setattr(knowledge_mod, "download_list_convert", fake_download)
    monkeypatch.setattr(knowledge_mod, "format_upload_context", fake_format)
    agent = object.__new__(KnowledgeAgent)
    agent.knowledge_config = cast(
        KnowledgeConfig,
        SimpleNamespace(
            TEMP_DIR="/tmp",
            BUCKET_NAME="bucket",
            PART_SIZE=1,
            TASK_NUM=1,
            MAX_RETRIES=1,
            MAX_CONCURRENCY=1,
            MAX_WORKERS=1,
            MAX_TOKENS=100,
        ),
    )
    filled = await agent.process_files_node(
        cast(KnowledgeState, {"obs_file_list": ["obs://bucket/a.md"]})
    )
    empty = await agent.process_files_node(cast(KnowledgeState, {}))
    assert filled == {"upload_context": "formatted-upload"}
    assert empty == {"upload_context": ""}

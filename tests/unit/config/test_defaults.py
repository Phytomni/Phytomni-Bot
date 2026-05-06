# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for non-secret default configuration models."""

from pathlib import Path

import pytest

from mcp_server_phytomni.config.defaults import (
    ChatConfig,
    DataConfig,
    KnowledgeConfig,
    ServerConfig,
)

pytestmark = pytest.mark.unit


def test_server_config_has_expected_core_defaults():
    """Verify server config has expected core defaults."""
    config = ServerConfig()

    assert config.MAX_TOKENS == 65536
    assert config.PROMPT_PATH == "system/ai4ps"
    assert config.PART_SIZE == 16777216
    assert Path(config.PROMPT_FILE).name == ".prompts.yaml"


def test_config_inheritance_keeps_agent_defaults_available():
    """Verify config inheritance keeps agent defaults available."""
    chat_config = ChatConfig()
    knowledge_config = KnowledgeConfig()
    data_config = DataConfig()

    assert chat_config.RESPONSE_FORMAT == {"type": "json_object"}
    assert knowledge_config.PAGE_NUM == 1
    assert knowledge_config.SCOPE == "both"
    assert data_config.DATA_PAGE_SIZE == 3
    assert data_config.SIMPLIFY_RESPONSE is True

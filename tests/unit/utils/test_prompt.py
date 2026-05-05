# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for YAML prompt loading and rendering helpers."""

import pytest

from mcp_server_phytomni.utils import (
    get_prompt,
    load_template,
    render_template,
)

pytestmark = pytest.mark.unit


def test_load_template_reads_nested_yaml_path(tmp_path):
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text(
        "system:\n  greeting: 'Hello {{ name }}'\n",
        encoding="utf-8",
    )

    assert load_template(str(template_file), "system/greeting") == (
        "Hello {{ name }}"
    )


def test_load_template_reads_single_top_level_value(tmp_path):
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text("only: plain text\n", encoding="utf-8")

    assert load_template(str(template_file)) == "plain text"


def test_load_template_raises_for_missing_nested_path(tmp_path):
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text("system:\n  greeting: hello\n", encoding="utf-8")

    with pytest.raises(KeyError, match="Path 'missing' not found"):
        load_template(str(template_file), "system/missing")


def test_render_template_stringifies_values_and_warns_on_missing_key():
    with pytest.warns(UserWarning, match="Missing parameter 'missing'"):
        rendered = render_template(
            "Hello {{ name }} {{ count }} {{ missing }}",
            {"name": "leaf", "count": 3},
        )

    assert rendered == "Hello leaf 3 "


def test_get_prompt_loads_and_renders_template(tmp_path):
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text(
        "user:\n  database: 'Query: {{ user_query }}'\n",
        encoding="utf-8",
    )

    prompt = get_prompt(
        str(template_file),
        "user/database",
        {"user_query": "plant height"},
    )

    assert prompt == "Query: plant height"

# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Coder/embed model YAML builder for the analyst compute task.

Functions: build_model_yaml.

Builds the model config YAML the remote analysis platform consumes. In
customer relay mode the coder/embed endpoints are rewritten to the relay
routes with the relay key so the platform never receives operator
coder/embed credentials; normal mode emits the operator endpoints.
"""

from __future__ import annotations

import textwrap
from typing import Any

from ...config.relay_mode import relay_mode_enabled

__all__ = ["build_model_yaml"]


def build_model_yaml(sensitive_config: Any, analyst_config: Any) -> str:
    """Build the coder/embed model YAML payload for the compute task.

    Args:
        sensitive_config: Secret config exposing the CODER_ / EMBED_
            endpoints + keys and (in relay mode) ``RELAY_API_KEY``.
        analyst_config: Config exposing ``RELAY_BASE_URL`` for relay mode.

    Returns:
        The model-config YAML string the compute task consumes. The
        coder ``api_base`` / embed ``inference_url`` and their keys point
        at the relay routes in relay mode; model identifiers stay
        operator-provided in both modes.
    """
    coder_url = sensitive_config.CODER_URL
    coder_key = sensitive_config.CODER_API_KEY.get_secret_value()
    embed_url = sensitive_config.EMBED_URL
    embed_key = sensitive_config.EMBED_API_KEY.get_secret_value()
    if relay_mode_enabled():
        relay_key = sensitive_config.RELAY_API_KEY.get_secret_value()
        relay_base = analyst_config.RELAY_BASE_URL
        coder_url = f"{relay_base}/v1/relay/coder"
        embed_url = f"{relay_base}/v1/relay/embed"
        coder_key = embed_key = relay_key
    model_config = textwrap.dedent(f"""\
        llm:
          model_name: {sensitive_config.CODER_MODEL}
          api_base: {coder_url}
          api_key: {coder_key}
          max_tokens: 32768
          url_header_user_agent: ""
          inference_endpoint: completions
          chat_api_endpoint: chat/completions
          server: openai
          proxy: ""
          proxy_verify: false
          header:
            Content-Type: application/json

        embed:
          model_id: {sensitive_config.EMBED_MODEL}
          api_token: {embed_key}
          inference_url: {embed_url}
          batch_size: 16
          url_header_user_agent: ""
          proxy: ""
          proxy_verify: false

        context_variables:
          running_env: local
          terminal_interactive: false
          black_list:
            - bioconductor-deseq2
            - r-deseq2
            - deseq2
            - r-deseq2
            - r
            - r-base
          conda_home: /opt/miniconda3
          conda_bioenv: bioenv
          conda_renv: bioenv
          do_execute: true
          debug: false
          max_round: 300
          max_times_per_round: 10
          proxy: ''
          proxy_verify: false

        mcp:
          biomcp:
            type: stdio
            command: uv
            args: ["run", "--with", "biomcp-python", "biomcp", "run"]
    """).strip()
    return model_config

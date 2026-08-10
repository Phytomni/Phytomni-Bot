# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure declarations for deployable environment configuration.

This module deliberately does not read ``os.environ`` or construct a settings
model. The tuples are shared by runtime validators, CI workflows, and the
value-safe checker so those surfaces cannot silently drift.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

SERVER_REQUIRED_ENDPOINT_FIELDS = (
    "TOKEN_URL",
    "RETRIEVE_URL",
    "RERANK_URL",
    "DATABASE_URL",
    "ANALYSIS_URL",
    "REPO_ID",
    "REPO_ID_DICT",
    "WORKSPACE_ID",
    "SUBJECT_ID",
    "OBS_SERVER",
)

DATA_REQUIRED_ENDPOINT_FIELDS = ("DATA_REPO_ID",)

ANALYST_REQUIRED_ENDPOINT_FIELDS = ("TOOL_REPO_ID", "APP_ID")

DEEP_GENOME_REQUIRED_ENDPOINT_FIELDS = (
    "SPA_FAQ_URL",
    "PROTOCOL_REPO_ID",
    "SPA_REPO_ID",
)

REQUIRED_DEPLOYMENT_FIELDS = (
    *SERVER_REQUIRED_ENDPOINT_FIELDS,
    "SPA_FAQ_URL",
    "DATA_REPO_ID",
    "TOOL_REPO_ID",
    "PROTOCOL_REPO_ID",
    "SPA_REPO_ID",
    "APP_ID",
)

REQUIRED_OUTBOUND_FIELDS = (
    "OUTBOUND_LLM_CONCURRENCY",
    "OUTBOUND_RETRIEVAL_CONCURRENCY",
    "OUTBOUND_RERANK_CONCURRENCY",
    "OUTBOUND_NL2SQL_CONCURRENCY",
    "OUTBOUND_ANALYSIS_CONTROL_CONCURRENCY",
    "OUTBOUND_ANALYSIS_STATUS_CONCURRENCY",
    "OUTBOUND_IAM_CONCURRENCY",
    "OUTBOUND_SPA_FAQ_CONCURRENCY",
    "OUTBOUND_BI_CONCURRENCY",
    "OUTBOUND_OBS_CONCURRENCY",
    "OUTBOUND_RELAY_CONTROL_CONCURRENCY",
    "OUTBOUND_INTEROP_CONCURRENCY",
    "OUTBOUND_POOL_WAIT_WARN_SECONDS",
)

REQUIRED_OPERATOR_SECRET_FIELDS = (
    "DOMAIN_NAME",
    "USER_NAME",
    "USER_PASSWORD",
    "ACCESS_KEY_ID",
    "SECRET_ACCESS_KEY",
    "BASE_URL",
    "MODEL_ID",
    "API_KEY",
    "GAUSS_DSN",
    "CODER_URL",
    "CODER_MODEL",
    "CODER_API_KEY",
    "EMBED_URL",
    "EMBED_MODEL",
    "EMBED_API_KEY",
)


def missing_environment(
    names: Sequence[str], environ: Mapping[str, str]
) -> tuple[str, ...]:
    """Return required names with missing or blank values.

    Args:
        names: Environment variable names to check.
        environ: Mapping supplying environment values, usually
            ``os.environ``. It is injected so this helper remains pure and
            straightforward to test.

    Returns:
        Missing names sorted lexicographically. Values are never returned or
        printed.
    """
    return tuple(
        sorted(name for name in names if not environ.get(name, "").strip())
    )


__all__ = [
    "ANALYST_REQUIRED_ENDPOINT_FIELDS",
    "DATA_REQUIRED_ENDPOINT_FIELDS",
    "DEEP_GENOME_REQUIRED_ENDPOINT_FIELDS",
    "REQUIRED_DEPLOYMENT_FIELDS",
    "REQUIRED_OUTBOUND_FIELDS",
    "REQUIRED_OPERATOR_SECRET_FIELDS",
    "SERVER_REQUIRED_ENDPOINT_FIELDS",
    "missing_environment",
]

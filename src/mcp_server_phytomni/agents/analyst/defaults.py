# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Analyst agent shared configuration constants and override field maps.

Lives in its own module so ``agent.py`` and ``submission.py`` can both
import the defaults without forming an import cycle. Without this split,
``submission.py`` would need to function-locally import the constants
from ``agent.py`` (which then re-exports submission symbols), producing
the canonical ``import-outside-toplevel`` cycle.
"""

from ...config.defaults import AnalystConfig
from ...config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    OBS_TRANSFER_CONFIG_FIELD_MAP,
    RETRIEVAL_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
)

ANALYST_CONFIG = AnalystConfig()

ANALYST_CONFIG_FIELD_MAP = {
    "analysis_url": "ANALYSIS_URL",
    "region": "ANALYSIS_REGION",
    "resource_dict": "RESOURCE",
    "app_id_dict": "APP_ID",
    "task_name": "TASK_NAME",
    "execute_code": "EXECUTE_CODE",
    "output_dir": "OUTPUT_DIR",
    **RETRIEVAL_CONFIG_FIELD_MAP,
    **CHAT_COMPLETION_CONFIG_FIELD_MAP,
    **OBS_TRANSFER_CONFIG_FIELD_MAP,
    **RETRY_CONFIG_FIELD_MAP,
    "max_poll": "MAX_POLL",
    "plan_min_score": "PLAN_MIN_SCORE",
}
ANALYST_SENSITIVE_FIELD_MAP = {
    "base_url": "BASE_URL",
    "model": "MODEL_ID",
    "model_url": "CODER_URL",
    "model_name": "CODER_MODEL",
}
ANALYST_SECRET_FIELD_MAP = {
    "api_key": "API_KEY",
    "coder_api_key": "CODER_API_KEY",
    "access_key_id": "ACCESS_KEY_ID",
    "secret_access_key": "SECRET_ACCESS_KEY",
}

__all__ = [
    "ANALYST_CONFIG",
    "ANALYST_CONFIG_FIELD_MAP",
    "ANALYST_SECRET_FIELD_MAP",
    "ANALYST_SENSITIVE_FIELD_MAP",
]

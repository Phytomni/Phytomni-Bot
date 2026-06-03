# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Customer relay-mode flag detection.

Functions: relay_mode_enabled.

A leaf module (depends only on ``os``) so both ``config/defaults.py``
and ``config/settings.py`` read the relay-mode switch without importing
each other. The import-time required-field validators consult
``os.environ`` directly because the config object is mid-construction.
"""

import os

__all__ = ["relay_mode_enabled"]

_RELAY_MODE_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "t", "y"})


def relay_mode_enabled() -> bool:
    """Return whether customer relay mode is active for this process.

    Reads the ``PHYTOMNI_RELAY_MODE`` / ``RELAY_MODE`` env flag straight
    from ``os.environ``. The unprefixed and ``PHYTOMNI_*`` forms share
    the dual-alias contract used by every other toggle; the prefixed
    form wins when both are set. Truthy parsing mirrors the
    ``ServerConfig.RELAY_MODE`` bool field.

    Returns:
        True when relay mode is enabled, False otherwise.
    """
    raw = os.environ.get("PHYTOMNI_RELAY_MODE")
    if raw is None:
        raw = os.environ.get("RELAY_MODE")
    if raw is None:
        return False
    return raw.strip().lower() in _RELAY_MODE_TRUE_VALUES

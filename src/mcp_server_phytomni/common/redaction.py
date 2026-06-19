# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Secret-scrubbing shared by failure messages and log output.

Lives in ``common`` (the lowest utility layer) so both
``agents/shared/parallel_dispatch.redact_failure_message`` and the
package log formatter in ``common/logging_config`` can scrub URLs and
credential fragments without an upward import. Keeping one
implementation here means a new secret pattern is added in exactly one
place and both consumers pick it up.
"""

from __future__ import annotations

import re
from typing import Final

# Any ``scheme://rest`` URL: backend HTTP errors embed the request URL --
# internal hostnames, ports, paths -- and a credential can ride in a
# query parameter, so the whole URL is replaced.
_URL_RE: Final = re.compile(r"\b[a-z][a-z0-9+.\-]*://\S+", re.IGNORECASE)
# A credential keyword joined to its value by ``=`` / ``:``
# (``token=...``, ``api_key: ...``).
_SECRET_RE: Final = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|token|secret|password|passwd|"
    r"authorization)\s*[=:]\s*\S+"
)
# A ``Bearer <token>`` authorization preamble.
_BEARER_RE: Final = re.compile(r"(?i)\bbearer\s+[\w.\-]+")


def redact_secrets(text: str) -> str:
    """Strip URLs and secret-like fragments from ``text``.

    Replaces any ``scheme://`` URL with ``<redacted-url>`` and any
    ``Bearer <token>`` / ``<keyword>=<value>`` credential fragment with
    ``<redacted-secret>``, keeping the surrounding text so the message
    stays diagnosable. Used for client-facing failure metadata
    (``redact_failure_message``) and, via the package log formatter, for
    every emitted log line so a ``logger.exception`` traceback never
    discloses an internal endpoint or secret.
    """
    redacted = _URL_RE.sub("<redacted-url>", text)
    # ``Bearer <token>`` first: the keyword pass below would otherwise
    # consume only ``Bearer`` after ``Authorization:`` and leave the token.
    redacted = _BEARER_RE.sub("<redacted-secret>", redacted)
    redacted = _SECRET_RE.sub("<redacted-secret>", redacted)
    return redacted

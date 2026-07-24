# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility client for the OBS SDK's deprecated TLS constructor.

The current OBS SDK still creates ``ssl.PROTOCOL_SSLv23`` in its base
constructor. Python 3.12 emits a deprecation warning for that protocol and the
repository treats warnings as errors. This subclass preserves the SDK's
credential, cipher, and certificate options while creating a modern TLS client
context before the inherited request machinery uses it.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path

from .obs_sdk import ObsClient as _SdkObsClient

__all__ = ["ObsClient"]


class ObsClient(_SdkObsClient):
    """OBS client using a non-deprecated TLS context."""

    def _init_ssl_context(self, custom_ciphers: str | None) -> None:
        """Initialize the SDK context without ``PROTOCOL_SSLv23``."""
        if self.ssl_verify:
            context = ssl.create_default_context()
            if isinstance(self.ssl_verify, str):
                cafile = os.fspath(self.ssl_verify)
                if Path(cafile).is_file():
                    context.load_verify_locations(cafile)
        else:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        if custom_ciphers:
            context.set_ciphers(str(custom_ciphers).strip())
        self.context = context

# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the OBS SDK TLS compatibility boundary."""

from __future__ import annotations

import ssl
import warnings

from mcp_server_phytomni.storage.obs_client import ObsClient


def test_obs_client_uses_modern_tls_context_without_warning() -> None:
    """Constructing the SDK client is warning-clean under error filters."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        client = ObsClient(
            access_key_id="test-access",
            secret_access_key="test-secret",
            server="https://obs.example.invalid",
        )

    assert client.context is not None
    assert client.context.protocol == ssl.PROTOCOL_TLS_CLIENT


def test_obs_client_loads_custom_ca_and_ciphers() -> None:
    """A file CA path and cipher string still use a modern TLS context."""
    import certifi

    client = ObsClient(
        access_key_id="test-access",
        secret_access_key="test-secret",
        server="https://obs.example.invalid",
        ssl_verify=certifi.where(),
        custom_ciphers="HIGH",
    )
    assert client.context is not None
    assert client.context.check_hostname is True
    assert client.context.verify_mode == ssl.CERT_REQUIRED


def test_obs_client_disables_verification_when_requested() -> None:
    """ssl_verify=False builds a non-validating TLS client context."""
    client = ObsClient(
        access_key_id="test-access",
        secret_access_key="test-secret",
        server="https://obs.example.invalid",
        ssl_verify=False,
    )
    assert client.context is not None
    assert client.context.check_hostname is False
    assert client.context.verify_mode == ssl.CERT_NONE

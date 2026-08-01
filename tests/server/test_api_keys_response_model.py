# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""The dormant api-keys / files response models are wired to routes.

Pins that the four admin routes declare a response_model (so OpenAPI
documents their shape and FastAPI validates the response) and that the
emitted JSON keys are unchanged from the prior hand-built dicts.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api.schemas import (
    ApiKeyCreateResponse,
    ApiKeyDeleteResponse,
    ApiKeyListResponse,
    UploadCreateResponse,
)

pytestmark = pytest.mark.server


def test_routes_declare_response_models() -> None:
    """The four admin routes carry their dormant response models."""
    app = api_app.create_app()
    want = {
        ("/v1/api-keys", "POST"): ApiKeyCreateResponse,
        ("/v1/api-keys", "GET"): ApiKeyListResponse,
        ("/v1/api-keys/{prefix}", "DELETE"): ApiKeyDeleteResponse,
        ("/v1/files", "POST"): UploadCreateResponse,
    }
    found: dict[tuple[str, str], object] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        raw_methods = getattr(route, "methods", None) or set()
        methods: set[str] = set(raw_methods)
        model = getattr(route, "response_model", None)
        for m in methods:
            key = (str(path), str(m))
            if key in want:
                found[key] = model
    assert found == want

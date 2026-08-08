# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the OBS object relay routes (server-side SDK termination).

The OBS routes do not proxy an HTTP upstream; the parent runs its own
``ObsClient`` through ``storage/obs_relay_ops`` (mocked here). The routes
re-validate the client path, confine list to the server-owned output
root, audit metadata (never the binary body), and gate on ``relay:obs``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi.routing import APIRoute
from tests.support.relay_fakes import (
    build_relay_app,
    make_relay_client_fixture,
    make_relay_key_fixture,
    make_relay_reset_fixture,
)

from mcp_server_phytomni.api.relay import forward as forward_module
from mcp_server_phytomni.api.relay.audit import RelayAuditStore
from mcp_server_phytomni.api.relay.routes import create_relay_router
from mcp_server_phytomni.storage import obs_relay_ops as ops_module
from mcp_server_phytomni.storage.obs_storage import ObsPathError

pytestmark = pytest.mark.server

_AUDIT_DB_ENV = "PHYTOMNI_RELAY_AUDIT_DB_PATH"


_relay_key_fixture = make_relay_key_fixture(user_id="customer")
_client_fixture = make_relay_client_fixture(build_relay_app)
_reset_inflight = make_relay_reset_fixture(forward_module)


@pytest.fixture(autouse=True)
def _redirect_relay_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redirect relay audit writes to a temp DB for every OBS route test."""
    monkeypatch.setenv(_AUDIT_DB_ENV, str(tmp_path / "relay_audit.sqlite"))


_OUTPUT_PREFIX = "agent_data/user_data/customer/runs/d/run_x/task/output/"
_GENE_MD = "gene-examples/md/AT1G01010_result.md"
_GENE_IMAGE = "gene-examples/img/AT1G01010/AT1G01010_network.png"


def test_relay_route_table_keeps_obs_surface_and_research_ops_narrow() -> None:
    """Research grants add no OBS list/body/write/delete/sign operation."""
    routes = {
        (route.path, tuple(sorted(route.methods or ())))
        for route in create_relay_router().routes
        if isinstance(route, APIRoute)
    }

    assert {
        ("/v1/relay/obs/object", ("GET",)),
        ("/v1/relay/obs/object", ("PUT",)),
        ("/v1/relay/obs/dir", ("PUT",)),
        ("/v1/relay/obs/list", ("GET",)),
    } <= routes
    research_routes = {
        path for path, _methods in routes if "/research-input/" in path
    }
    assert research_routes == {
        "/v1/relay/research-input/object-grants",
        "/v1/relay/research-input/object-grants/verify",
        "/v1/relay/research-input/object-grants/revoke",
    }


async def test_obs_put_object_writes_and_returns_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PUT writes the body at the validated key and returns the obs path."""
    fake = Mock(return_value="agent_data/uploads/customer/r/up/x.pdf")
    monkeypatch.setattr(ops_module, "put_object_bytes", fake)

    response = await client.put(
        "/v1/relay/obs/object"
        "?path=/obs/phytomni/agent_data/uploads/customer/r/up/x.pdf",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
        content=b"file-bytes",
    )

    assert response.status_code == 200
    assert response.json()["obs_path"].startswith("/obs/")
    assert fake.call_args.args[2] == b"file-bytes"
    assert fake.call_args.kwargs["obs_server"]


async def test_obs_get_object_streams_under_budget(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET streams the object content in chunks as an octet-stream."""
    monkeypatch.setattr(ops_module, "object_size", Mock(return_value=8))
    monkeypatch.setattr(
        ops_module,
        "iter_object_chunks",
        Mock(return_value=iter([b"ATOM", b" 1 N"])),
    )

    response = await client.get(
        "/v1/relay/obs/object"
        "?path=/obs/phytomni/agent_data/user_data/customer/runs/d/r.cif",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 200
    assert response.content == b"ATOM 1 N"
    assert response.headers["content-type"] == "application/octet-stream"


async def test_obs_get_object_rejects_over_budget(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An object larger than the response budget is a 413, never streamed."""
    monkeypatch.setenv("PHYTOMNI_RELAY_RESPONSE_MAX_BYTES", "16")
    monkeypatch.setattr(ops_module, "object_size", Mock(return_value=10**6))
    streamed = Mock(return_value=iter([b"x"]))
    monkeypatch.setattr(ops_module, "iter_object_chunks", streamed)

    response = await client.get(
        "/v1/relay/obs/object"
        "?path=/obs/phytomni/agent_data/user_data/customer/runs/d/r.cif",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 413
    assert not streamed.called


@pytest.mark.parametrize("path", [_GENE_MD, _GENE_IMAGE])
async def test_obs_get_allows_curated_gene_example_object(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """Authenticated relay keys can read canonical curated gene objects."""
    size = Mock(return_value=4)
    chunks = Mock(return_value=iter([b"GENE"]))
    monkeypatch.setattr(ops_module, "object_size", size)
    monkeypatch.setattr(ops_module, "iter_object_chunks", chunks)

    response = await client.get(
        f"/v1/relay/obs/object?path={path}",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 200
    assert response.content == b"GENE"
    assert size.call_args.args[1] == path


async def test_obs_list_allows_only_gene_markdown_root(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact curated Markdown root is listable."""
    listing = Mock(return_value=[_GENE_MD])
    monkeypatch.setattr(ops_module, "list_object_keys", listing)

    response = await client.get(
        "/v1/relay/obs/list?prefix=gene-examples/md/",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 200
    assert response.json() == {"keys": [_GENE_MD]}
    assert listing.call_args.args[1] == "gene-examples/md/"


@pytest.mark.parametrize(
    "path",
    [
        "gene-examples/",
        "gene-examples/md/",
        "gene-examples/md/at1g01010_result.md",
        "gene-examples/md/AT1G01010.md",
        "gene-examples/md/nested/AT1G01010_result.md",
        "gene-examples/img/",
        "gene-examples/img/AT1G01010/",
        "gene-examples/img/AT1G01010/Os01g01010_network.png",
        "gene-examples/img/AT1G01010/AT1G01010_network.jpg",
        "gene-examples/img/AT1G01010/nested/AT1G01010_network.png",
    ],
)
async def test_obs_get_rejects_noncanonical_gene_example_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """Noncanonical catalog paths are rejected before any OBS read."""
    size = Mock(return_value=4)
    monkeypatch.setattr(ops_module, "object_size", size)

    response = await client.get(
        f"/v1/relay/obs/object?path={path}",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 403
    assert not size.called


@pytest.mark.parametrize(
    "prefix",
    [
        "gene-examples/",
        "gene-examples/img/",
        "gene-examples/img/AT1G01010/",
        "gene-examples/md/AT1G01010",
    ],
)
async def test_obs_list_rejects_broad_gene_example_prefix(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
    prefix: str,
) -> None:
    """Catalog enumeration is limited to the exact Markdown root."""
    listing = Mock(return_value=[])
    monkeypatch.setattr(ops_module, "list_object_keys", listing)

    response = await client.get(
        f"/v1/relay/obs/list?prefix={prefix}",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 403
    assert not listing.called


@pytest.mark.parametrize(
    ("route", "path"),
    [
        ("/v1/relay/obs/object", _GENE_MD),
        ("/v1/relay/obs/object", _GENE_IMAGE),
        ("/v1/relay/obs/dir", _GENE_MD),
        ("/v1/relay/obs/dir", _GENE_IMAGE),
    ],
)
async def test_obs_mutation_rejects_gene_example_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    path: str,
) -> None:
    """Curated catalog objects cannot be mutated through either PUT route."""
    put_object = Mock(return_value=_GENE_MD)
    put_dir = Mock(return_value=_GENE_MD)
    monkeypatch.setattr(ops_module, "put_object_bytes", put_object)
    monkeypatch.setattr(ops_module, "put_dir_marker", put_dir)

    response = await client.put(
        f"{route}?path={path}",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
        content=b"forbidden",
    )

    assert response.status_code == 403
    assert not put_object.called
    assert not put_dir.called


@pytest.mark.parametrize(
    ("request_path", "expected_key"),
    [
        (
            "gene-examples/md/AT1G01010_result.md",
            "gene-examples/md/AT1G01010_result.md",
        ),
        (
            "gene-examples/md/GLYMA01G000100_result.md",
            "gene-examples/md/GLYMA01G000100_result.md",
        ),
        (
            "gene-examples/md/Os01g01010_result.md",
            "gene-examples/md/Os01g01010_result.md",
        ),
        (
            "gene-examples/md/TraesCS1A02G000100_result.md",
            "gene-examples/md/TraesCS1A02G000100_result.md",
        ),
        (
            "gene-examples/md/Zm00001eb000010_result.md",
            "gene-examples/md/Zm00001eb000010_result.md",
        ),
        (
            "/obs/phytomni/gene-examples/md/AT1G01010_result.md",
            "gene-examples/md/AT1G01010_result.md",
        ),
    ],
)
async def test_obs_gene_example_read_accepts_approved_species_prefix(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
    request_path: str,
    expected_key: str,
) -> None:
    """Approved species identifiers normalize to bucket-relative keys."""
    size = Mock(return_value=4)
    monkeypatch.setattr(ops_module, "object_size", size)
    monkeypatch.setattr(
        ops_module, "iter_object_chunks", Mock(return_value=iter([b"GENE"]))
    )

    response = await client.get(
        f"/v1/relay/obs/object?path={request_path}",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 200
    assert size.call_args.args[1] == expected_key


async def test_obs_get_object_rejects_cross_tenant_output_dir(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dedup-reused output_dir under another tenant is a 403, never read.

    The analyst fingerprint is content-keyed and intentionally tenant-
    agnostic, so a reuse hit can hand back a prior submitter's
    ``output_dir``. This pins the guarantee that makes that safe: the
    relay confines every object read to the caller key's own
    ``user_data/<user_id>/`` namespace, so a cross-tenant path is
    rejected before any byte is streamed.
    """
    size = Mock(return_value=8)
    chunks = Mock(return_value=iter([b"secret"]))
    monkeypatch.setattr(ops_module, "object_size", size)
    monkeypatch.setattr(ops_module, "iter_object_chunks", chunks)

    response = await client.get(
        "/v1/relay/obs/object"
        "?path=/obs/phytomni/agent_data/user_data/other_tenant/runs/d/r.cif",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "obs path outside tenant namespace"
    assert not size.called
    assert not chunks.called


async def test_obs_list_returns_keys_under_output_root(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list returns keys for a prefix under the server-owned output root."""
    fake = Mock(
        return_value=[f"{_OUTPUT_PREFIX}a.png", f"{_OUTPUT_PREFIX}b.md"]
    )
    monkeypatch.setattr(ops_module, "list_object_keys", fake)

    response = await client.get(
        f"/v1/relay/obs/list?prefix={_OUTPUT_PREFIX}",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 200
    assert response.json()["keys"] == [
        f"{_OUTPUT_PREFIX}a.png",
        f"{_OUTPUT_PREFIX}b.md",
    ]


async def test_obs_list_rejects_prefix_outside_output_root(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prefix not under the server output root is a 403 (no enumeration)."""
    fake = Mock(return_value=[])
    monkeypatch.setattr(ops_module, "list_object_keys", fake)

    response = await client.get(
        "/v1/relay/obs/list?prefix=agent_data/secrets/",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 403
    assert not fake.called


async def test_obs_dir_marker_creates_zero_byte_object(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PUT dir delegates to the dir-marker op and returns the path."""
    monkeypatch.setattr(
        ops_module, "put_dir_marker", Mock(return_value=_OUTPUT_PREFIX)
    )

    response = await client.put(
        f"/v1/relay/obs/dir?path={_OUTPUT_PREFIX}",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 200
    assert response.json()["obs_path"].startswith("/obs/")


async def test_obs_put_rejects_out_of_bucket_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An out-of-bucket path surfaces as a 400, not a 500."""
    monkeypatch.setattr(
        ops_module,
        "put_object_bytes",
        Mock(side_effect=ObsPathError("outside bucket")),
    )

    response = await client.put(
        "/v1/relay/obs/object?path=/obs/other-bucket/x",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
        content=b"x",
    )

    assert response.status_code == 400


async def test_obs_route_requires_obs_scope(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key without relay:obs is rejected with 403 before any OBS op."""
    fake = Mock(return_value="k")
    monkeypatch.setattr(ops_module, "put_object_bytes", fake)

    response = await client.put(
        "/v1/relay/obs/object?path=/obs/phytomni/agent_data/x",
        headers={"Authorization": f"Bearer {relay_key('llm')}"},
        content=b"x",
    )

    assert response.status_code == 403
    assert not fake.called


async def test_obs_get_rejects_foreign_tenant_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-bucket path under another tenant's namespace is a 403."""
    fake = Mock(return_value=8)
    monkeypatch.setattr(ops_module, "object_size", fake)

    response = await client.get(
        "/v1/relay/obs/object"
        "?path=agent_data/user_data/other-tenant/runs/x/r.cif",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 403
    assert not fake.called


async def test_obs_put_accepts_own_tenant_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path under the caller's own tenant namespace is accepted."""
    fake = Mock(return_value="agent_data/user_data/customer/runs/x/out.txt")
    monkeypatch.setattr(ops_module, "put_object_bytes", fake)

    response = await client.put(
        "/v1/relay/obs/object"
        "?path=agent_data/user_data/customer/runs/x/out.txt",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
        content=b"hi",
    )

    assert response.status_code == 200
    assert fake.called


async def test_obs_put_rejects_foreign_tenant_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writing under another tenant's namespace is a 403, no op call."""
    fake = Mock(return_value="k")
    monkeypatch.setattr(ops_module, "put_object_bytes", fake)

    response = await client.put(
        "/v1/relay/obs/object"
        "?path=agent_data/user_data/other-tenant/runs/x/out.txt",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
        content=b"hi",
    )

    assert response.status_code == 403
    assert not fake.called


async def test_obs_dir_rejects_foreign_tenant_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Making a dir marker under another tenant's namespace is a 403."""
    fake = Mock(return_value="k")
    monkeypatch.setattr(ops_module, "put_dir_marker", fake)

    response = await client.put(
        "/v1/relay/obs/dir?path=agent_data/user_data/other-tenant/runs/x/",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 403
    assert not fake.called


async def test_obs_list_rejects_foreign_tenant_prefix(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A list prefix under another tenant's output root is a 403."""
    fake = Mock(return_value=[])
    monkeypatch.setattr(ops_module, "list_object_keys", fake)

    response = await client.get(
        "/v1/relay/obs/list?prefix=agent_data/user_data/other-tenant/runs/",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 403
    assert not fake.called


_SHARED_FP = "a" * 64  # 64-hex content-addressed fingerprint
_SHARED_PREFIX = f"agent_data/shared/{_SHARED_FP}/output/"


async def test_obs_get_object_allows_shared_content_addressed_read(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET on agent_data/shared/<fp>/output/... is allowed (possession-of-fp).

    Any authenticated caller that knows a sha256 fingerprint already proved
    possession of the inputs that produced it, so the shared result root is
    open to any valid relay key, not just the submitting tenant.
    """
    monkeypatch.setattr(ops_module, "object_size", Mock(return_value=12))
    monkeypatch.setattr(
        ops_module,
        "iter_object_chunks",
        Mock(return_value=iter([b"ATOM RECORD"])),
    )

    response = await client.get(
        f"/v1/relay/obs/object" f"?path=/obs/phytomni/{_SHARED_PREFIX}r.cif",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 200
    assert response.content == b"ATOM RECORD"


async def test_obs_list_allows_shared_prefix(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """List on agent_data/shared/<fp>/output/ is allowed (possession-of-fp).

    A caller that holds the fingerprint may enumerate the shared result root
    to discover which output files are available for download.
    """
    fake = Mock(
        return_value=[
            f"{_SHARED_PREFIX}result.md",
            f"{_SHARED_PREFIX}plot.png",
        ]
    )
    monkeypatch.setattr(ops_module, "list_object_keys", fake)

    response = await client.get(
        f"/v1/relay/obs/list?prefix={_SHARED_PREFIX}",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 200
    assert response.json()["keys"] == [
        f"{_SHARED_PREFIX}result.md",
        f"{_SHARED_PREFIX}plot.png",
    ]


async def test_obs_upload_audit_records_metadata_not_binary(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audit row carries path/size metadata but never the raw bytes."""
    monkeypatch.setattr(
        ops_module, "put_object_bytes", Mock(return_value="agent_data/x")
    )
    secret = b"TOP-SECRET-PLASMID-SEQUENCE"

    response = await client.put(
        "/v1/relay/obs/object"
        "?path=/obs/phytomni/agent_data/user_data/customer/x",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
        content=secret,
    )

    assert response.status_code == 200
    rows = RelayAuditStore(os.environ[_AUDIT_DB_ENV]).query()
    obs_rows = [row for row in rows if row.service == "obs"]
    assert obs_rows
    blob = (obs_rows[0].request_body or "") + (obs_rows[0].response_body or "")
    assert secret.decode() not in blob
    assert str(len(secret)) in blob


async def test_obs_list_rejects_bare_shared_root(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bare shared root is a 403 — no fingerprint enumeration.

    Allowing ``agent_data/shared/`` without a fingerprint segment would let
    any caller list every tenant's results, defeating the possession-of-
    fingerprint model. The guard requires a full 64-hex fingerprint.
    """
    fake = Mock(return_value=[])
    monkeypatch.setattr(ops_module, "list_object_keys", fake)

    response = await client.get(
        "/v1/relay/obs/list?prefix=agent_data/shared/",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 403
    assert not fake.called


async def test_obs_get_object_rejects_non_fingerprint_shared_path(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared path without a full 64-hex fingerprint is a 403."""
    size = Mock(return_value=8)
    chunks = Mock(return_value=iter([b"x"]))
    monkeypatch.setattr(ops_module, "object_size", size)
    monkeypatch.setattr(ops_module, "iter_object_chunks", chunks)

    response = await client.get(
        "/v1/relay/obs/object"
        "?path=/obs/phytomni/agent_data/shared/not-a-fingerprint/r.cif",
        headers={"Authorization": f"Bearer {relay_key('obs')}"},
    )

    assert response.status_code == 403
    assert not size.called
    assert not chunks.called

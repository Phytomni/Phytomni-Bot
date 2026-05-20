# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e test for the external HTTP API chat surface.

Boots ``phytomni-api`` as a real uvicorn subprocess on an ephemeral
port, mints a per-user key in a throwaway SQLite store, then drives
``POST /v1/chat/completions`` for all four OpenAI models over real
HTTP. Review/BriefGene each ~10 min; runs only under a manual
``pytest e2e/`` with a configured ``.env``.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import IO, Any, NamedTuple

import httpx
import pytest
import pytest_asyncio

from mcp_server_phytomni.api.auth import ApiKeyStore

from .helpers.assertions import (
    ANNOTATION_CUES,
    GENE_ID,
    MIN_REVIEW_SECTIONS,
    PHOTOSYNTHESIS_KEYWORDS,
    WHEAT_DROUGHT_KEYWORDS,
    markdown_body,
    section_count,
)

pytestmark = pytest.mark.live

MODEL_IDS = {
    "phyto-chat",
    "phyto-knowledge",
    "phyto-review",
    "phyto-brief-gene",
}
_STARTUP_DEADLINE_DEFAULT = 120.0
_READ_TIMEOUT_DEFAULT = 1200.0
_LOG_TAIL = 500


class ApiServer(NamedTuple):
    """Connection details for the live API subprocess.

    Attributes:
        base_url: Root URL the uvicorn process is bound to.
        api_key: The one-time plaintext key minted for this session.
        user_id: The user the key authenticates.
    """

    base_url: str
    api_key: str
    user_id: str


def _free_port() -> int:
    """Reserve and release an ephemeral localhost port.

    Returns:
        A port free at call time; the startup health-gate tolerates the
        brief race between releasing and the child re-binding it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _startup_deadline_seconds() -> float:
    """Return the startup health-gate budget.

    Override with ``PHYTOMNI_E2E_API_STARTUP_SECONDS`` when cold-start
    secret decryption / license init is slow.

    Returns:
        Deadline in seconds.
    """
    raw = os.environ.get("PHYTOMNI_E2E_API_STARTUP_SECONDS")
    return float(raw) if raw else _STARTUP_DEADLINE_DEFAULT


def _read_timeout_seconds() -> float:
    """Return the per-request read timeout.

    Review/BriefGene block the synchronous endpoint ~10 min. Override
    with ``PHYTOMNI_E2E_API_READ_TIMEOUT_SECONDS``.

    Returns:
        Read timeout in seconds.
    """
    raw = os.environ.get("PHYTOMNI_E2E_API_READ_TIMEOUT_SECONDS")
    return float(raw) if raw else _READ_TIMEOUT_DEFAULT


def _drain(stream: IO[str], sink: "deque[str]") -> None:
    """Copy a subprocess stream line-by-line into a bounded buffer.

    Reading the pipe continuously prevents a full OS buffer from
    stalling a 10-min server; only the most recent lines are kept.

    Args:
        stream: The child's merged stdout/stderr text stream.
        sink: Bounded buffer retaining the most recent log lines.
    """
    for line in stream:
        sink.append(line.rstrip("\n"))


def _log_tail(logs: "deque[str]") -> str:
    """Return the captured subprocess log tail as one string."""
    return "\n".join(logs)


def _await_healthy(
    proc: "subprocess.Popen[str]", base_url: str, logs: "deque[str]"
) -> None:
    """Block until ``/healthz`` is ok or the deadline elapses.

    Args:
        proc: The running API subprocess (checked for early exit).
        base_url: Base URL the service is expected to bind.
        logs: Captured log tail surfaced on failure.

    Raises:
        RuntimeError: If the subprocess exits or never becomes healthy.
    """
    deadline = time.monotonic() + _startup_deadline_seconds()
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                "API subprocess exited during startup "
                f"(code {proc.returncode}); logs:\n{_log_tail(logs)}"
            )
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(f"{base_url}/healthz")
            if resp.status_code == 200 and resp.json().get("status") == "ok":
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(
        f"API did not become healthy in time; logs:\n{_log_tail(logs)}"
    )


@pytest.fixture(scope="session", name="api_server")
def api_server_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[ApiServer]:
    """Boot the HTTP API subprocess and yield its URL plus a key.

    Args:
        tmp_path_factory: Session tmp-dir factory for the throwaway
            SQLite stores.

    Yields:
        Connection details for the live API process.
    """
    store_dir = tmp_path_factory.mktemp("api")
    keys_db = str((store_dir / "api_keys.sqlite").resolve())
    runs_db = str((store_dir / "api_runs.sqlite").resolve())
    user_id = "phytomni-api-e2e"
    created = ApiKeyStore(keys_db).create(user_id=user_id, name="api-http-e2e")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.pop("PHYTOMNI_TESTING", None)
    env["API_HOST"] = "127.0.0.1"
    env["API_PORT"] = str(port)
    env["PHYTOMNI_API_KEYS_DB"] = keys_db
    env["PHYTOMNI_API_RUNS_DB"] = runs_db

    cmd = [sys.executable, "-m", "mcp_server_phytomni.api.server"]
    logs: "deque[str]" = deque(maxlen=_LOG_TAIL)
    with subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as proc:
        assert proc.stdout is not None
        drain = threading.Thread(
            target=_drain, args=(proc.stdout, logs), daemon=True
        )
        drain.start()
        try:
            _await_healthy(proc, base_url, logs)
            yield ApiServer(base_url, created.api_key, user_id)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
    drain.join(timeout=5)


@pytest_asyncio.fixture(name="api_client")
async def api_client_fixture(
    api_server: ApiServer,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an httpx client bound to the live API base URL.

    Args:
        api_server: The running API connection details.

    Yields:
        Async client with a long read timeout and no default auth
        header (so the auth-rejection test can omit it).
    """
    timeout = httpx.Timeout(
        connect=5.0,
        read=_read_timeout_seconds(),
        write=10.0,
        pool=5.0,
    )
    async with httpx.AsyncClient(
        base_url=api_server.base_url, timeout=timeout
    ) as client:
        yield client


def _auth(server: ApiServer) -> dict[str, str]:
    """Return the Bearer auth header for the issued key."""
    return {"Authorization": f"Bearer {server.api_key}"}


def _read_user_query(demo_data_dir: Path, name: str) -> str:
    """Return the ``user_query`` from a committed demo payload.

    Reads ``demo_data/payloads/<name>`` by path only, so this HTTP test
    does not pull in the OBS-publishing ``load_payload`` chain; the four
    chat payloads carry no OBS placeholders.

    Args:
        demo_data_dir: Resolved demo_data root.
        name: Payload filename, e.g. ``"chat_agent.json"``.

    Returns:
        The natural-language prompt string.
    """
    raw = (demo_data_dir / "payloads" / name).read_text(encoding="utf-8")
    return str(json.loads(raw)["user_query"])


def _completion_text(body: dict[str, Any]) -> str:
    """Return assistant content, unwrapping the citation envelope.

    Citation-bearing agents serialize the answer as a JSON envelope
    ``{"content": "<markdown>", "doc_list": [...]}``; unwrap it so
    keyword/section assertions see the markdown body.

    Args:
        body: Parsed chat-completion response.

    Returns:
        The assistant message content (envelope-unwrapped).
    """
    content = body["choices"][0]["message"]["content"]
    return markdown_body(content)


async def _chat(
    client: httpx.AsyncClient,
    server: ApiServer,
    *,
    model: str,
    query: str,
    **extra: Any,
) -> httpx.Response:
    """POST one authenticated chat-completion request.

    Args:
        client: The bound async HTTP client.
        server: Running API details (for the auth header).
        model: OpenAI-style model id.
        query: Single user-message content.
        **extra: Extra body keys (stream, obs_file_list, ...).

    Returns:
        The raw HTTP response.
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": query}],
        **extra,
    }
    return await client.post(
        "/v1/chat/completions", json=body, headers=_auth(server)
    )


async def test_healthz_ok(api_client: httpx.AsyncClient) -> None:
    """``/healthz`` returns ok and a correlation id without auth.

    Args:
        api_client: Bound async HTTP client.
    """
    resp = await api_client.get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert resp.headers.get("X-Request-Id")


async def test_models_lists_four(
    api_client: httpx.AsyncClient, api_server: ApiServer
) -> None:
    """``/v1/models`` lists exactly the four chat models.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details (auth header).
    """
    resp = await api_client.get("/v1/models", headers=_auth(api_server))

    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert {m["id"] for m in body["data"]} == MODEL_IDS


async def test_auth_rejected_without_key(
    api_client: httpx.AsyncClient,
) -> None:
    """A missing key yields the unified 401 envelope.

    Args:
        api_client: Bound async HTTP client (no auth header sent).
    """
    resp = await api_client.get("/v1/models")

    assert resp.status_code == 401
    error = resp.json()["error"]
    assert error["type"] == "unauthorized"
    assert error["code"] == 401
    assert resp.headers.get("WWW-Authenticate") == "Bearer"
    assert resp.headers.get("X-Request-Id")


async def test_unknown_model_404(
    api_client: httpx.AsyncClient, api_server: ApiServer
) -> None:
    """An unknown model id yields a 404 envelope.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
    """
    resp = await _chat(api_client, api_server, model="phyto-nope", query="hi")

    assert resp.status_code == 404
    assert resp.json()["error"]["type"] == "not_found"


async def test_stream_true_rejected(
    api_client: httpx.AsyncClient, api_server: ApiServer
) -> None:
    """``stream=true`` is refused with a 400 envelope.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
    """
    resp = await _chat(
        api_client,
        api_server,
        model="phyto-chat",
        query="hi",
        stream=True,
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "bad_request"


async def test_brief_gene_rejects_obs_list(
    api_client: httpx.AsyncClient, api_server: ApiServer
) -> None:
    """BriefGene rejects ``obs_file_list`` before invoking the agent.

    The obs check precedes ``invoke_tool_raw``, so this negative path is
    cheap (no ~10-min agent call).

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
    """
    resp = await _chat(
        api_client,
        api_server,
        model="phyto-brief-gene",
        query=GENE_ID,
        obs_file_list=["/obs/phytomni/nope.pdf"],
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "bad_request"


async def test_chat_completion(
    api_client: httpx.AsyncClient,
    api_server: ApiServer,
    demo_data_dir: Path,
) -> None:
    """``phyto-chat`` answers the C3 query in OpenAI shape.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
        demo_data_dir: Resolved demo_data root.
    """
    query = _read_user_query(demo_data_dir, "chat_agent.json")

    resp = await _chat(api_client, api_server, model="phyto-chat", query=query)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "phyto-chat"
    assert body["choices"][0]["message"]["role"] == "assistant"
    lowered = _completion_text(body).lower()
    matched = [k for k in PHOTOSYNTHESIS_KEYWORDS if k in lowered]
    assert matched, (
        f"phyto-chat missed every keyword {PHOTOSYNTHESIS_KEYWORDS}; "
        f"got: {lowered!r}"
    )


async def test_knowledge_completion(
    api_client: httpx.AsyncClient,
    api_server: ApiServer,
    demo_data_dir: Path,
) -> None:
    """``phyto-knowledge`` answers the wheat-drought query.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
        demo_data_dir: Resolved demo_data root.
    """
    query = _read_user_query(demo_data_dir, "knowledge_agent.json")

    resp = await _chat(
        api_client, api_server, model="phyto-knowledge", query=query
    )

    assert resp.status_code == 200, resp.text
    text = _completion_text(resp.json())
    assert text.strip(), "phyto-knowledge returned empty content"
    lowered = text.lower()
    matched = [k for k in WHEAT_DROUGHT_KEYWORDS if k in lowered]
    assert matched, (
        f"phyto-knowledge missed every keyword "
        f"{WHEAT_DROUGHT_KEYWORDS}; got: {lowered!r}"
    )


async def test_review_completion(
    api_client: httpx.AsyncClient,
    api_server: ApiServer,
    demo_data_dir: Path,
) -> None:
    """``phyto-review`` produces a multi-section drought review.

    ~10 min: the synchronous endpoint blocks until ReviewAgent finishes.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
        demo_data_dir: Resolved demo_data root.
    """
    query = _read_user_query(demo_data_dir, "review_agent.json")

    resp = await _chat(
        api_client, api_server, model="phyto-review", query=query
    )

    assert resp.status_code == 200, resp.text
    sections = section_count(_completion_text(resp.json()))
    assert sections >= MIN_REVIEW_SECTIONS, (
        f"phyto-review had only {sections} markdown sections "
        f"(expected >= {MIN_REVIEW_SECTIONS})"
    )


async def test_brief_gene_completion(
    api_client: httpx.AsyncClient,
    api_server: ApiServer,
) -> None:
    """``phyto-brief-gene`` returns a gene-card for Os01g0177400.

    ~10 min: the synchronous endpoint blocks until BriefGene finishes.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
    """
    resp = await _chat(
        api_client, api_server, model="phyto-brief-gene", query=GENE_ID
    )

    assert resp.status_code == 200, resp.text
    lowered = _completion_text(resp.json()).lower()
    assert (
        GENE_ID.lower() in lowered
    ), f"phyto-brief-gene did not mention {GENE_ID}; got: {lowered!r}"
    matched = [c for c in ANNOTATION_CUES if c in lowered]
    assert (
        matched
    ), f"phyto-brief-gene lacked every annotation cue {ANNOTATION_CUES}"

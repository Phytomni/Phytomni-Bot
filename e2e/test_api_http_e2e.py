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
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from .helpers.api_server import (
    ApiServer,
    boot_phytomni_api,
    make_async_client,
    service_auth_header,
)
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


@pytest.fixture(scope="session", name="api_server")
def api_server_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[ApiServer]:
    """Boot the HTTP API subprocess once per session.

    Args:
        tmp_path_factory: Session tmp-dir factory for the throwaway
            SQLite stores.

    Yields:
        Connection details for the live API process.
    """
    with boot_phytomni_api(tmp_path_factory) as server:
        yield server


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
    async with make_async_client(api_server) as client:
        yield client


def _auth(server: ApiServer) -> dict[str, str]:
    """Return the Bearer auth header for the issued key."""
    return {"Authorization": f"Bearer {server.api_key}"}


def _service_auth(server: ApiServer) -> dict[str, str]:
    """Return the ``X-Service-Token`` header for admin-scope calls."""
    return service_auth_header(server)


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
    """Return assistant content as plain markdown.

    Cited agents now emit plain markdown with inline ``[N]`` markers in
    ``message.content``; the ``markdown_body`` helper also tolerates the
    legacy ``{"content","doc_list"}`` JSON envelope for archived logs
    and pre-unwrap servers.

    Args:
        body: Parsed chat-completion response.

    Returns:
        The assistant message content as markdown.
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


@pytest.mark.parametrize(
    "non_streaming_model",
    ["phyto-knowledge", "phyto-review", "phyto-brief-gene"],
)
async def test_stream_true_rejected_for_non_chat_models(
    api_client: httpx.AsyncClient,
    api_server: ApiServer,
    non_streaming_model: str,
) -> None:
    """``stream=true`` is refused with a 400 envelope on non-chat models.

    Phase 5 wired SSE streaming behind ``_STREAM_CAPABLE_TOOLS =
    {"ChatAgent"}``; the three non-chat OpenAI-mapped models still
    return a 400 so this parametrized matrix pins the per-model policy
    instead of asserting a blanket "all models reject" that no longer
    matches HEAD.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
        non_streaming_model: One of the non-chat model ids.
    """
    resp = await _chat(
        api_client,
        api_server,
        model=non_streaming_model,
        query="hi",
        stream=True,
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["type"] == "bad_request"


async def test_chat_stream_sse_returns_data_lines_and_done(
    api_client: httpx.AsyncClient,
    api_server: ApiServer,
    demo_data_dir: Path,
) -> None:
    """``phyto-chat`` with ``stream=true`` produces an SSE event stream.

    The shape contract: ``text/event-stream`` content type, one or more
    ``data: {...}`` JSON chunks framed by ``\\n\\n``, and a terminal
    ``data: [DONE]`` sentinel. Each non-terminal chunk parses as a JSON
    object so chat-ai can render incremental deltas.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
        demo_data_dir: Resolved demo_data root.
    """
    query = _read_user_query(demo_data_dir, "chat_agent.json")

    payload = {
        "model": "phyto-chat",
        "messages": [{"role": "user", "content": query}],
        "stream": True,
    }
    data_chunks: list[str] = []
    saw_done = False
    content_type = ""
    async with api_client.stream(
        "POST",
        "/v1/chat/completions",
        json=payload,
        headers=_auth(api_server),
    ) as resp:
        assert (
            resp.status_code == 200
        ), f"SSE handshake failed; got {resp.status_code}"
        content_type = resp.headers.get("Content-Type", "")
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload_str = line.removeprefix("data: ")
            if payload_str == "[DONE]":
                saw_done = True
                break
            data_chunks.append(payload_str)

    assert (
        "text/event-stream" in content_type
    ), f"SSE content-type missing; got {content_type!r}"
    assert data_chunks, "SSE stream produced no data frames"
    parsed_first = json.loads(data_chunks[0])
    assert isinstance(
        parsed_first, dict
    ), f"SSE frame should parse as dict; got {parsed_first!r}"
    assert saw_done, "SSE stream did not terminate with data: [DONE]"


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


async def test_brief_gene_resolve_gene_id_smoke(
    api_client: httpx.AsyncClient,
    api_server: ApiServer,
) -> None:
    """resolve_gene_id rewrites a free-form Chinese query before BriefGene.

    The free-form Chinese question wraps the rice locus Os01g0177400 in
    a research-style sentence that BriefGene's plain path would
    normally fall through to ``user/brief_gene_function_nogeneid``;
    flipping ``resolve_gene_id=true`` should invoke the LLM resolver,
    rewrite ``user_query`` to the canonical locus id, surface the
    rewrite in ``metadata``, and produce a real gene card.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
    """
    free_form_query = f"请介绍水稻 {GENE_ID} 基因的功能"

    resp = await _chat(
        api_client,
        api_server,
        model="phyto-brief-gene",
        query=free_form_query,
        resolve_gene_id=True,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    formatted = body.get("formatted") or {}
    metadata = formatted.get("metadata") or {}
    assert (
        metadata.get("resolve_gene_id") is True
    ), f"resolver metadata missing; got body={body!r}"
    resolved_id = metadata.get("resolved_gene_id") or ""
    assert (
        resolved_id
    ), f"resolved_gene_id should be non-empty; got: {metadata!r}"
    assert metadata.get("original_query") == free_form_query
    lowered = _completion_text(body).lower()
    assert resolved_id.lower() in lowered or GENE_ID.lower() in lowered, (
        "BriefGene answer should mention the resolved or expected locus; "
        f"resolved={resolved_id!r} expected={GENE_ID!r} got={lowered!r}"
    )
    matched = [cue for cue in ANNOTATION_CUES if cue in lowered]
    assert matched, (
        "BriefGene answer lacked every annotation cue "
        f"{ANNOTATION_CUES}; resolver likely degraded to the "
        f"nogeneid fallback; got: {lowered!r}"
    )


async def test_api_keys_service_token_lifecycle(
    api_client: httpx.AsyncClient,
    api_server: ApiServer,
) -> None:
    """Service token can POST a user key, GET the list, and DELETE it.

    Mirrors the Web ops 90-day rotation workflow under the candidate-A
    consumer model: ops mints a fresh ``ptm_<web>`` user key, the
    listing reflects the new prefix, then revoking it drops the prefix
    from the list. The single-shot ``api_key`` value only appears in
    the POST response (per the irreversible-hash contract) so the
    assertions verify shape, not the literal value across calls.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
    """
    user_id = "web-cutover-e2e"
    create_resp = await api_client.post(
        "/v1/api-keys",
        json={"user_id": user_id, "name": "e2e-rotation"},
        headers=_service_auth(api_server),
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    prefix = created["prefix"]
    assert created["api_key"].startswith(
        "ptm_"
    ), f"minted key should carry ptm_ prefix; got: {created['api_key']!r}"
    assert created["user_id"] == user_id

    list_resp = await api_client.get(
        f"/v1/api-keys?user_id={user_id}",
        headers=_service_auth(api_server),
    )
    assert list_resp.status_code == 200, list_resp.text
    listed_prefixes = {row["prefix"] for row in list_resp.json()["data"]}
    assert prefix in listed_prefixes, (
        f"freshly-minted prefix {prefix!r} missing from listing; "
        f"got: {listed_prefixes!r}"
    )

    revoke_resp = await api_client.delete(
        f"/v1/api-keys/{prefix}",
        headers=_service_auth(api_server),
    )
    assert revoke_resp.status_code == 200, revoke_resp.text
    assert revoke_resp.json()["deleted"] is True

    post_revoke = await api_client.get(
        f"/v1/api-keys?user_id={user_id}",
        headers=_service_auth(api_server),
    )
    assert post_revoke.status_code == 200, post_revoke.text
    remaining = {row["prefix"] for row in post_revoke.json()["data"]}
    assert prefix not in remaining, (
        f"prefix {prefix!r} should be gone after DELETE; "
        f"got: {remaining!r}"
    )


async def test_runs_history_self_query_by_dialogue_id(
    api_client: httpx.AsyncClient,
    api_server: ApiServer,
    demo_data_dir: Path,
) -> None:
    """A chat with ``dialogue_id`` shows up in owner-scope history.

    Under the candidate-A model Web Go owns the real-user filter, so
    the owner-scope ``GET /v1/runs?dialogue_id=`` is the production
    read path for chat-ai history; this asserts the persistence chain
    (chat-completion writes ``dialogue_id`` into the runs row) and the
    listing filter both hold end-to-end.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
        demo_data_dir: Resolved demo_data root.
    """
    dialogue_id = "e2e-dlg-self-query"
    query = _read_user_query(demo_data_dir, "chat_agent.json")
    chat_resp = await _chat(
        api_client,
        api_server,
        model="phyto-chat",
        query=query,
        dialogue_id=dialogue_id,
    )
    assert chat_resp.status_code == 200, chat_resp.text

    runs_resp = await api_client.get(
        f"/v1/runs?dialogue_id={dialogue_id}",
        headers=_auth(api_server),
    )
    assert runs_resp.status_code == 200, runs_resp.text
    rows = runs_resp.json()["data"]
    # The route runs the dialogue_id predicate server-side before
    # ``limit``, so every returned row must match the requested
    # dialogue id; a client-side post-filter would mask AF-004
    # regressions.
    assert rows, (
        f"runs listing should include at least one row with "
        f"dialogue_id={dialogue_id!r}; got an empty payload"
    )
    for row in rows:
        assert row.get("dialogue_id") == dialogue_id, (
            "every row in a dialogue_id filtered listing must match; "
            f"got dialogue_id={row.get('dialogue_id')!r}"
        )
    row = rows[0]
    assert (
        row.get("query") == query
    ), f"row.query should mirror the chat prompt; got: {row.get('query')!r}"
    assert row.get("model") == "phyto-chat"


async def test_runs_history_delegated_user_id_via_service_token(
    api_client: httpx.AsyncClient,
    api_server: ApiServer,
    demo_data_dir: Path,
) -> None:
    """Service token can look up another user's runs via ``?user_id=``.

    Candidate-A relegates this delegated read path to ops debugging and
    multi-Web-instance SaaS predecessor work (Web Go itself does
    ``WHERE real_user_id=?`` in its own database), but the route
    contract still needs to hold: a service-token request can query
    runs owned by the fixture's user even though that user is not the
    service principal.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
        demo_data_dir: Resolved demo_data root.
    """
    dialogue_id = "e2e-dlg-delegated"
    query = _read_user_query(demo_data_dir, "chat_agent.json")
    chat_resp = await _chat(
        api_client,
        api_server,
        model="phyto-chat",
        query=query,
        dialogue_id=dialogue_id,
    )
    assert chat_resp.status_code == 200, chat_resp.text

    delegated_resp = await api_client.get(
        f"/v1/runs?user_id={api_server.user_id}" f"&dialogue_id={dialogue_id}",
        headers=_service_auth(api_server),
    )
    assert delegated_resp.status_code == 200, delegated_resp.text
    rows = delegated_resp.json()["data"]
    matching = [row for row in rows if row.get("dialogue_id") == dialogue_id]
    assert matching, (
        f"delegated lookup should surface dialogue_id={dialogue_id!r} "
        f"for user_id={api_server.user_id!r}; got {len(rows)} rows"
    )
    assert matching[0].get("user_id") == api_server.user_id


async def test_files_upload_returns_obs_path(
    api_client: httpx.AsyncClient,
    api_server: ApiServer,
    tmp_path: Path,
) -> None:
    """POST ``/v1/files`` returns a 201 envelope with an OBS path.

    Validates the Bot-owned ingestion contract: a small text upload
    succeeds, the response carries the OBS path under
    ``agent_data/uploads/``, and ``purpose`` round-trips one of the
    OpenAI-compatible Literal values.

    Args:
        api_client: Bound async HTTP client.
        api_server: Running API details.
        tmp_path: Per-test tmpdir for the upload payload.
    """
    upload_file = tmp_path / "hello.txt"
    upload_file.write_text("hello e2e cutover\n", encoding="utf-8")

    with upload_file.open("rb") as fh:
        files = {"file": ("hello.txt", fh, "text/plain")}
        data = {"purpose": "agent_context"}
        resp = await api_client.post(
            "/v1/files",
            files=files,
            data=data,
            headers=_auth(api_server),
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["object"] == "file"
    assert body["filename"] == "hello.txt"
    assert body["purpose"] == "agent_context"
    obs_path = body["obs_path"]
    assert obs_path.startswith(
        "/obs/"
    ), f"obs_path should start with /obs/; got: {obs_path!r}"
    assert (
        "/agent_data/uploads/" in obs_path
    ), f"obs_path should live under agent_data/uploads/; got: {obs_path!r}"

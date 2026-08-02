# Resumable upload contract fixtures

Copyable golden JSON for the Bot `obs-multipart-v2` biological-file upload
protocol. Web and Go consumers can vendor the files and verify the exact
fixture bytes through [`manifest.json`](manifest.json) without live OBS
credentials.

## Bot of record

The Bot owns these routes:

- `POST /v1/files` uses the service scope.
  `create_request.json` -> `create_response.json`
- `POST /v1/files/{asset_id}/capability` uses the service scope.
  `renew_request.json` -> `renew_response.json`
- `HEAD /v1/files/{asset_id}` uses the asset capability.
  `head_headers.json` and `head_response.json`
- `PUT /v1/files/{asset_id}/parts/{part_number}` uses the asset capability.
  `part_response.json`
- `POST /v1/files/{asset_id}/complete` uses the asset capability.
  `complete_request.json` -> `complete_response.json`
- `DELETE /v1/files/{asset_id}` uses the asset capability.
  `abort_response.json`

The public capability descriptor is pinned in `capability.json`. It is the
serialized output of `serialize_file_upload_capability()` and includes the
inclusive 10 GiB limit, 128 MiB part size, four-part concurrency limit, and
the seven-day session lifetime.

## Wire rules

- `create_request.json` and `renew_request.json` are control-plane bodies.
  `owner_subject` is the trusted owner assertion and is never returned as a
  provider coordinate.
- `create_response.json` and `renew_response.json` contain only the opaque
  capability and the Bot upload URL. The capability strings in these files are
  synthetic placeholders, not credentials.
- `HEAD` returns no JSON body. Its browser-visible state is represented by
  `head_headers.json`; `head_response.json` is the equivalent typed Bot model
  used to derive those headers.
- A part request carries an exact `Content-Length` and the
  `X-Phytomni-Part-SHA256` header. The response shape is pinned in
  `part_response.json`.
- Completion accepts an optional SHA-256 checksum and returns the safe
  `AssetDescriptor` in `complete_response.json`. Abort returns the same status
  model with `status: "aborted"`.
- Web/Go gateways must preserve the public field names and status/header
  semantics. They must not add provider bucket names, object keys, upload IDs,
  or capability material to the client contract.

## Ownership and acceptance boundary

The Bot registry binds an asset to one owner and validates the short-lived
asset capability for every data-plane operation. Owner-mismatch and missing-
asset behavior is covered by the Bot upload tests; this fixture packet does
not grant access to any real asset.

The files are synthetic and redacted. They prove JSON/header shape and digest
stability only. Real OBS credentials, four-part 10 GiB throughput and peak
RSS, interruption/restart recovery, staging, production, flag activation, and
Go legacy-relay removal remain separate owner evidence. The resumable-upload
feature flag stays disabled until those external checks are accepted.

## File index

- `manifest.json`: fixture set, protocol version, and SHA-256 digests.
- `capability.json`: sanitized capability descriptor and route inventory.
- `create_request.json` / `create_response.json`: create handshake.
- `renew_request.json` / `renew_response.json`: capability renewal.
- `head_response.json` / `head_headers.json`: typed status and HEAD projection.
- `part_response.json`: successful part response.
- `complete_request.json` / `complete_response.json`: completion handshake.
- `abort_response.json`: abort response.

# Web/Go stream and A2UI acceptance packet

Owner: Go gateway and Phytomni-Web\
Bot contract: `docs/reference/http-api.md` and
`docs/contracts/a2ui/`\
Current state: `External Pending`\
Evidence: `Not returned`

This packet covers `RC-WEB-006` and the stream portions of `RC-WEB-004` and
`RC-WEB-005`. The gateway owner authenticates and checks tenancy; after that,
the gateway must preserve the Bot contract exactly. No external code is
changed from this worktree.

## A2UI passthrough (`RC-WEB-006`)

For Chat and Review, run confirm, form, choice, cancel, stale-action, and
second-round cases using the goldens under `docs/contracts/a2ui/`.

Assert all of the following:

- the gateway forwards the uplink JSON bytes unchanged to
  `POST /v1/runs/{run_id}/a2ui-actions`;
- the response HTTP status and JSON body are unchanged on success and error;
- `surface_id`, `widget`, and path/body `run_id` are not rewritten;
- form and choice cancellation uses `{"cancelled": true}`;
- a second uplink after the first success returns Bot `409`;
- foreign runs return `404`, malformed payloads return `400`/`422`, and the
  disabled flag returns `403`;
- the two-round surface receives a fresh `surface_id` and does not loop past
  the documented N=2 bound.

Return byte-level request/response fixtures with gateway and Bot request IDs.

## AG-UI stream (`RC-WEB-006`)

Run `POST /v1/chat/completions` with `stream=true` for `phyto-chat`,
`phyto-knowledge`, and `phyto-brief-gene`. Run the flag-on Review pause and a
non-stream-capable model as negative controls.

Assert:

- pre-header setup/priming errors are ordinary JSON errors, not empty SSE;
- successful streams contain only the documented `RunStarted`,
  `StepStarted`, `TextMessage*`, `Custom`, and `RunFinished` vocabulary;
- `phyto.progress`, `phyto.references`, and `phyto.follow_up` frames remain
  valid JSON and appear before the terminal answer where applicable;
- an opened producer failure emits exactly one sanitized `RunError`, omits
  `RunFinished`, and still ends with one `data: [DONE]`;
- client cancellation follows the documented failed-settlement behavior;
- Review stream pauses emit the minimal A2UI frame and resume only through the
  non-stream route;
- unsupported models return the documented `400` without silently falling
  back to a different stream.

Return the raw SSE frame sequence after removing credentials, private URLs,
SQL, stack traces, and provider-sensitive text.

## Artifact and timeout checks

For Design and Network, verify that the paths in the terminal report are
resolved through the supported download surface and that an empty path list
renders a warning rather than an invented URL. For an upstream timeout, verify
the documented `504`, safe message, request ID, and known-run retention. Link
those results to `RC-WEB-004` and `RC-WEB-005` records.

## Review and rollback

Any byte mismatch, frame vocabulary drift, missing error frame, or leaked
credential leaves the item pending. Restore the gateway fallback/flag, attach
the failing fixture, and do not change the Bot matrix status until the owner
returns a corrected record.

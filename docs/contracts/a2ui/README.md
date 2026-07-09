# A2UI Chat confirm contract fixtures

Copyable golden JSON for the Chat A2UI confirm widget. Web and Go consumers
can vendor these files to lock request/response shapes without a live Bot.

## Bot of record

`POST /v1/runs/{run_id}/a2ui-actions`

## Web provisional

`POST /api/v1/conversations/:id/a2ui-actions` — request body must match the
uplink goldens in `chat_confirm/`.

## Go passthrough rules

- Auth and owner checks happen at the gateway.
- Forward uplink body bytes unchanged to Bot.
- Return Bot HTTP status and body unchanged to the client.

## Auth note

Same API-key authentication as other `/v1/runs/*` routes. Fixtures contain
no secrets.

## AG-UI Custom frame example

Downlink values arrive wrapped in an AG-UI `Custom` frame:

```json
{
  "type": "custom",
  "name": "phyto.a2ui",
  "value": {
    "catalog_version": "v1.0",
    "surface_id": "sfc-contract-1",
    "widget": "confirm",
    "props": {
      "title": "Continue?",
      "body": "Run the analysis as planned.",
      "confirm_label": "Confirm",
      "cancel_label": "Cancel"
    }
  }
}
```

See `chat_confirm/downlink.json` for the canonical `value` payload.

## File index

| File                                              | Purpose                           |
| ------------------------------------------------- | --------------------------------- |
| `chat_confirm/downlink.json`                      | Bot → client confirm widget value |
| `chat_confirm/uplink_accept.json`                 | Client → Bot accept action        |
| `chat_confirm/uplink_reject.json`                 | Client → Bot reject action        |
| `chat_confirm/success_accept.json`                | Terminal success after accept     |
| `chat_confirm/success_reject.json`                | Terminal success after reject     |
| `chat_confirm/errors/flag_off_403.json`           | A2UI feature flag disabled        |
| `chat_confirm/errors/widget_mismatch_400.json`    | Widget type mismatch              |
| `chat_confirm/errors/surface_mismatch_409.json`   | Surface ID mismatch               |
| `chat_confirm/errors/not_input_required_409.json` | Run not awaiting input            |
| `chat_confirm/errors/not_owner_404.json`          | Run not found / not owned         |
| `chat_confirm/errors/run_id_mismatch_400.json`    | Path vs body run_id mismatch      |

## Out of scope

Review confirm, form widgets, choice widgets, and other A2UI product paths
are not covered by this directory.

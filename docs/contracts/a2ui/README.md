# A2UI contract fixtures

Copyable golden JSON for Chat and Review A2UI widgets. Web and Go consumers
can vendor these files to lock request/response shapes without a live Bot.

## Bot of record

`POST /v1/runs/{run_id}/a2ui-actions`

## Current-SHA acceptance

The [Bot contract acceptance runbook](../../ops/bot-contract-acceptance-runbook.md)
defines the current-SHA focused packet, the nine A2UI fixture hashes, and the
boundary between Bot-local readiness and external acceptance. HTTP response
goldens for the same packet live under [`../http/`](../http/). These files are
synthetic and redacted; gateway forwarding, live streams, Web/Go behavior,
and staging acceptance remain owner evidence.

## Web provisional

`POST /api/v1/conversations/:id/a2ui-actions` — request body must match the
uplink goldens in each widget directory.

## Go passthrough rules

- Auth and owner checks happen at the gateway.
- Forward uplink body bytes unchanged to Bot.
- Return Bot HTTP status and body unchanged to the client.

## Auth note

Same API-key authentication as other `/v1/runs/*` routes. Fixtures contain
no secrets.

## Cancel semantics

Form and choice widgets use `payload: { "cancelled": true }` for cancel
uplink actions (not empty fields or empty selected). Confirm widgets continue
to use `{ "accepted": false }` for reject.

## Multi-turn (N=2)

Chat and Review A2UI pauses are bounded to **two rounds**
(`A2UI_MAX_ROUNDS = 2`). A second downlink in the same run uses a fresh
`surface_id`. The shape-lock golden for round 2 is
`multi_turn/round2_downlink.json` (`sfc-contract-2`).

The complete canonical `agent.run` pause bodies are available as
`review_confirm/input_required.json`, `review_form/input_required.json`,
`review_choice/input_required.json`, and
`multi_turn/round2_input_required.json`. They carry the resumable
`result.interrupt.draft.a2ui` surface, `run_id`, `thread_id`, and an empty
`task_ids` list. The direct native Review response may also expose the same
interrupt as a legacy top-level `interrupt` alias; the persisted `GET /v1/runs/{run_id}` projection uses `result.interrupt`.

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

See `chat_confirm/downlink.json` for the canonical confirm `value` payload.

## Implemented widget goldens

Review confirm, form, and choice goldens are implemented in this repository,
as are the corresponding Chat confirm, form, and choice goldens. These files
lock the Bot-side downlink, uplink, success, and cancel shapes for offline
consumers. Web/Go passthrough behavior and end-to-end gateway acceptance remain
external evidence; this repository does not claim that integration is complete.

The fixtures lock Bot-side shapes for offline consumers. Gateway passthrough,
live stream behavior, and end-to-end acceptance are deployment-owner checks;
these files do not claim that cross-repository integration is complete.

## File index

| File                                    | Purpose                             |
| --------------------------------------- | ----------------------------------- |
| `chat_confirm/downlink.json`            | Bot → client Chat confirm widget    |
| `chat_confirm/uplink_accept.json`       | Client → Bot accept action          |
| `chat_confirm/uplink_reject.json`       | Client → Bot reject action          |
| `chat_confirm/success_accept.json`      | Terminal success after accept       |
| `chat_confirm/success_reject.json`      | Terminal success after reject       |
| `chat_confirm/errors/*.json`            | Shared error matrix (see below)     |
| `review_confirm/downlink.json`          | Bot → client Review confirm widget  |
| `review_confirm/input_required.json`    | Full Review confirm pause body      |
| `review_confirm/uplink_accept.json`     | Client → Bot approve action         |
| `review_confirm/uplink_reject.json`     | Client → Bot reject action          |
| `review_confirm/success_accept.json`    | Terminal success after approve      |
| `review_confirm/success_reject.json`    | Terminal success after reject       |
| `review_confirm/errors/*.json`          | Same error matrix as chat_confirm   |
| `chat_form/downlink.json`               | Bot → client Chat form widget       |
| `chat_form/uplink_submit.json`          | Client → Bot form submit            |
| `chat_form/uplink_cancel.json`          | Client → Bot form cancel            |
| `chat_form/success_submit.json`         | Terminal success after submit       |
| `chat_form/success_cancel.json`         | Terminal success after cancel       |
| `chat_choice/downlink.json`             | Bot → client Chat choice widget     |
| `chat_choice/uplink_submit.json`        | Client → Bot choice submit          |
| `chat_choice/uplink_cancel.json`        | Client → Bot choice cancel          |
| `chat_choice/success_submit.json`       | Terminal success after submit       |
| `chat_choice/success_cancel.json`       | Terminal success after cancel       |
| `review_form/downlink.json`             | Bot → client Review form widget     |
| `review_form/input_required.json`       | Full Review form pause body         |
| `review_form/uplink_submit.json`        | Client → Bot Review form submit     |
| `review_form/uplink_cancel.json`        | Client → Bot Review form cancel     |
| `review_form/success_submit.json`       | Terminal success after submit       |
| `review_form/success_cancel.json`       | Terminal success after cancel       |
| `review_choice/downlink.json`           | Bot → client Review choice widget   |
| `review_choice/input_required.json`     | Full Review choice pause body       |
| `review_choice/uplink_submit.json`      | Client → Bot Review choice submit   |
| `review_choice/uplink_cancel.json`      | Client → Bot Review choice cancel   |
| `review_choice/success_submit.json`     | Terminal success after submit       |
| `review_choice/success_cancel.json`     | Terminal success after cancel       |
| `multi_turn/round2_downlink.json`       | Round-2 downlink (`sfc-contract-2`) |
| `multi_turn/round2_input_required.json` | Full round-2 pause body             |

### Error matrix (`chat_confirm/errors/` and `review_confirm/errors/`)

| File                          | HTTP status | Detail message                |
| ----------------------------- | ----------- | ----------------------------- |
| `flag_off_403.json`           | 403         | a2ui disabled                 |
| `widget_mismatch_400.json`    | 400         | widget mismatch               |
| `surface_mismatch_409.json`   | 409         | surface_id mismatch           |
| `not_input_required_409.json` | 409         | run is not awaiting input     |
| `not_owner_404.json`          | 404         | run not found: run-contract-1 |
| `run_id_mismatch_400.json`    | 400         | run_id mismatch               |

Form and choice directories do not duplicate error goldens; consumers should
reference the shared matrix under `chat_confirm/errors/` or
`review_confirm/errors/`.

## Out of scope

Go gateway passthrough implementation remains open for Web/Go consumers to
follow these goldens.

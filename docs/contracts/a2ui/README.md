# A2UI contract fixtures

Copyable golden JSON for Chat and Review A2UI widgets. Web and Go consumers
can vendor these files to lock request/response shapes without a live Bot.

## Bot of record

`POST /v1/runs/{run_id}/a2ui-actions`

## Current-SHA acceptance

The [Bot contract acceptance
runbook](../../ops/bot-contract-acceptance-runbook.md)
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
interrupt as a legacy top-level `interrupt` alias; the persisted
`GET /v1/runs/{run_id}` projection uses `result.interrupt`.

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

- **File:** `chat_confirm/downlink.json`
  **Purpose:** Bot → client Chat confirm widget

- **File:** `chat_confirm/uplink_accept.json`
  **Purpose:** Client → Bot accept action

- **File:** `chat_confirm/uplink_reject.json`
  **Purpose:** Client → Bot reject action

- **File:** `chat_confirm/success_accept.json`
  **Purpose:** Terminal success after accept

- **File:** `chat_confirm/success_reject.json`
  **Purpose:** Terminal success after reject

- **File:** `chat_confirm/errors/*.json`
  **Purpose:** Shared error matrix (see below)

- **File:** `review_confirm/downlink.json`
  **Purpose:** Bot → client Review confirm widget

- **File:** `review_confirm/input_required.json`
  **Purpose:** Full Review confirm pause body

- **File:** `review_confirm/uplink_accept.json`
  **Purpose:** Client → Bot approve action

- **File:** `review_confirm/uplink_reject.json`
  **Purpose:** Client → Bot reject action

- **File:** `review_confirm/success_accept.json`
  **Purpose:** Terminal success after approve

- **File:** `review_confirm/success_reject.json`
  **Purpose:** Terminal success after reject

- **File:** `review_confirm/errors/*.json`
  **Purpose:** Same error matrix as chat_confirm

- **File:** `chat_form/downlink.json`
  **Purpose:** Bot → client Chat form widget

- **File:** `chat_form/uplink_submit.json`
  **Purpose:** Client → Bot form submit

- **File:** `chat_form/uplink_cancel.json`
  **Purpose:** Client → Bot form cancel

- **File:** `chat_form/success_submit.json`
  **Purpose:** Terminal success after submit

- **File:** `chat_form/success_cancel.json`
  **Purpose:** Terminal success after cancel

- **File:** `chat_choice/downlink.json`
  **Purpose:** Bot → client Chat choice widget

- **File:** `chat_choice/uplink_submit.json`
  **Purpose:** Client → Bot choice submit

- **File:** `chat_choice/uplink_cancel.json`
  **Purpose:** Client → Bot choice cancel

- **File:** `chat_choice/success_submit.json`
  **Purpose:** Terminal success after submit

- **File:** `chat_choice/success_cancel.json`
  **Purpose:** Terminal success after cancel

- **File:** `review_form/downlink.json`
  **Purpose:** Bot → client Review form widget

- **File:** `review_form/input_required.json`
  **Purpose:** Full Review form pause body

- **File:** `review_form/uplink_submit.json`
  **Purpose:** Client → Bot Review form submit

- **File:** `review_form/uplink_cancel.json`
  **Purpose:** Client → Bot Review form cancel

- **File:** `review_form/success_submit.json`
  **Purpose:** Terminal success after submit

- **File:** `review_form/success_cancel.json`
  **Purpose:** Terminal success after cancel

- **File:** `review_choice/downlink.json`
  **Purpose:** Bot → client Review choice widget

- **File:** `review_choice/input_required.json`
  **Purpose:** Full Review choice pause body

- **File:** `review_choice/uplink_submit.json`
  **Purpose:** Client → Bot Review choice submit

- **File:** `review_choice/uplink_cancel.json`
  **Purpose:** Client → Bot Review choice cancel

- **File:** `review_choice/success_submit.json`
  **Purpose:** Terminal success after submit

- **File:** `review_choice/success_cancel.json`
  **Purpose:** Terminal success after cancel

- **File:** `multi_turn/round2_downlink.json`
  **Purpose:** Round-2 downlink (`sfc-contract-2`)

- **File:** `multi_turn/round2_input_required.json`
  **Purpose:** Full round-2 pause body

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

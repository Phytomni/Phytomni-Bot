# Conversation Context V1 Operations

Status: dark launch. The Bot flag is disabled by default. This runbook defines the protocol and rollback boundary; it does not claim staging or production activation.

## Flag and authority

Bot reads the feature flag `CONVERSATION_CONTEXT_V1_ENABLED`. The supported environment aliases are `CONVERSATION_CONTEXT_V1_ENABLED` and `PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED`; the default is `false`.

The Web Go gateway flag is `bot.multiturn_v1_enabled`. Go must stop sending V1 before the Bot flag is disabled. Bot owns business-context projection, bounded per-agent memory, route selection metadata, stable agent threads, and context delta staging/settlement. Bot does not authenticate end users, decide user permissions, or authorize artifact ownership. Those are Go responsibilities.

The current V1 scope is synchronous Chat, Knowledge, Data, Review, and Brief Gene. Asynchronous agents retain their existing `202` response and run/task lifecycle.

## Protocol surfaces

- `GET /v1/agents` advertises `conversation_context: [1]` only when the Bot capability is enabled. With the flag off, normal V0 capability behavior remains available and V1 is not advertised.
- `POST /v1/chat/completions` and `POST /v1/query/route` accept a validated V1 envelope when Go has enabled the contract. They return bounded route/stage metadata; display answers are not context metadata.
- `POST /v1/conversation-context/settle` acknowledges one staged Go turn by conversation key, turn ID, and ledger version. It returns only bounded mutation state and context version.
- `POST /v1/conversation-context/tombstone` tombstones one Bot-owned context and schedules checkpoint cleanup. It returns only bounded mutation state and context version.

The mutation endpoints are server-to-server surfaces. They are not browser authorization endpoints and must not be used to infer user ownership. If the Bot feature is disabled, the V1 mutation routes are unavailable and V0 routes remain the compatibility path.

## SQLite ownership and retention

The Bot context store creates these tables:

| Table | Purpose |
| --- | --- |
| `conversation_contexts` | Current bounded business-context projection, schema/ledger versions, active or tombstoned state, and checkpoint-cleanup state. |
| `conversation_turns` | Per-turn operation, base version, staged/committed/failed state, selected-agent metadata, bounded result/delta metadata, ledger version, and optional `expires_at`. |
| `conversation_review_checkpoint_cleanup` | Durable registration of Review candidate threads so tombstone and retention cleanup can retry checkpoint deletion safely. |

Staged turns are purged only when their code-governed `expires_at` is reached. Tombstoned contexts retain cleanup state until checkpoint deletion succeeds. The exact retention window is configuration/code governed; operators must inspect the deployed version and configuration rather than assume a fixed TTL. Do not delete the context tables as part of an ordinary flag rollback.

## Context and thread safety

The projection keeps bounded entities, artifact metadata, recent turns, task summaries, and per-agent memory. Raw answer/report/table output, credentials, signed links, storage paths, and user-identifying content do not belong in context metadata. Go's current V1 boundary omits assistant display prose when no typed Bot-owned metadata summary exists.

Each selected agent receives a stable private thread namespace derived from the conversation key and canonical agent ID, conceptually:

```text
conversation-context-v1:<conversation-key>:<agent-id>
```

This namespace prevents Chat, Knowledge, Data, Review, and Brief Gene checkpoints from colliding. A conversation key is an internal correlation key, not a permission boundary. Never treat it as proof that a caller owns a dialogue or artifact.

## Safe observability

Use only these counters and bounded labels:

```text
conversation_context_prepare_total{outcome}
conversation_context_rebuild_total{reason}
conversation_context_stage_total{outcome}
conversation_context_settle_total{outcome}
conversation_context_tombstone_total{outcome}
conversation_context_degraded_total{agent}
conversation_submission_stale_total
```

Logs and metric labels must not contain user names, dialogue IDs, turn text, summaries, artifact IDs or paths, allowlists, credentials, signed URLs, or raw answer/report/table output. Use canonical agent and bounded outcome/reason values only.

## Staging activation

Use synthetic accounts and synthetic data only.

1. Deploy Bot with `CONVERSATION_CONTEXT_V1_ENABLED=false`.
2. Verify `/v1/agents` has no V1 conversation-context advertisement.
3. Enable the Bot flag in authorized staging.
4. Verify `/v1/agents` advertises `conversation_context: [1]`.
5. Keep Go `bot.multiturn_v1_enabled=false` and run V0 smoke tests.
6. Enable Go V1 only after the Bot advertisement and compatibility checks pass.
7. Run the ten acceptance scenarios: Instant lock, Expert explicit and automatic routing, Knowledge-to-Data-to-Review continuity, Brief Gene follow-up/new identifier, permission revocation, restart rebuild, retry idempotency, cancellation, owner isolation, and disabled/legacy behavior including async lifecycle.
8. Observe stage, settle, rebuild, degraded, tombstone, and stale-submission outcomes through the approved counters.
9. Disable Go V1 immediately for any protocol, ownership, authorization, artifact, or lifecycle regression.

## Rollback

1. Disable Go `bot.multiturn_v1_enabled` first.
2. Verify Go has stopped sending V1 envelopes and settlement/tombstone requests.
3. Disable `CONVERSATION_CONTEXT_V1_ENABLED` in Bot.
4. Leave the SQLite context tables in place. Do not delete context data or checkpoints as part of a flag rollback; use the existing tombstone and retention cleanup paths for deliberate deletion.

The Bot must not be disabled while Go is still sending V1. A protocol or ownership regression is a release-blocking incident.

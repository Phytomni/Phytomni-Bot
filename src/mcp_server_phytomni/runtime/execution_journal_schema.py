# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical additive SQLite schema migration for execution journal V2."""

from __future__ import annotations

import sqlite3

EXECUTION_V2_RUN_COLUMNS = (
    ("execution_id", "TEXT"),
    ("execution_fingerprint_version", "INTEGER"),
    ("execution_fingerprint", "TEXT"),
    ("execution_command_hash", "TEXT"),
    ("execution_driver", "TEXT"),
    ("execution_deadline_at", "TEXT"),
    ("execution_terminal_outcome", "TEXT"),
    ("execution_supervisor_revision", "INTEGER NOT NULL DEFAULT 0"),
    ("execution_root_span_id", "TEXT"),
    ("execution_tombstoned_at", "TEXT"),
    ("execution_next_attempt_at", "TEXT"),
    ("execution_tracking_health", "TEXT NOT NULL DEFAULT 'healthy'"),
    ("execution_cancellation_state", "TEXT NOT NULL DEFAULT 'none'"),
    ("execution_context_stage_json", "TEXT"),
    ("execution_provider_join_lease_owner", "TEXT"),
    ("execution_provider_join_lease_expires_at", "TEXT"),
)

EXECUTION_V2_WORK_UNIT_COLUMNS = (
    ("provider_trace_cursor", "TEXT"),
    ("provider_trace_revision", "INTEGER NOT NULL DEFAULT 0"),
    ("provider_trace_adapter_version", "TEXT"),
    ("provider_trace_overlap_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("provider_trace_contact_at", "TEXT"),
    ("provider_trace_health", "TEXT NOT NULL DEFAULT 'healthy'"),
)

EXECUTION_V2_COMMAND_COLUMNS = (
    ("classification", "TEXT"),
    ("boundary_state", "TEXT"),
    ("first_error_code", "TEXT"),
    ("next_reconcile_at", "TEXT"),
    ("reconcile_attempt", "INTEGER NOT NULL DEFAULT 0"),
    ("reconcile_redispatch_count", "INTEGER NOT NULL DEFAULT 0"),
)

_CREATE_EXECUTION_EVENTS_V2 = """
CREATE TABLE IF NOT EXISTS execution_events_v2 (
    owner_ref TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    seq INTEGER NOT NULL CHECK (seq > 0),
    event_id TEXT NOT NULL,
    source_idempotency_key TEXT,
    schema_version INTEGER NOT NULL CHECK (schema_version = 2),
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    source TEXT NOT NULL,
    span_id TEXT NOT NULL,
    parent_span_id TEXT,
    work_unit_id TEXT,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    summary_json TEXT NOT NULL,
    public_payload_json TEXT NOT NULL,
    target_json TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_ref, execution_id, seq),
    UNIQUE (owner_ref, event_id),
    UNIQUE (owner_ref, execution_id, source_idempotency_key)
)
"""

_CREATE_EXECUTION_SPANS = """
CREATE TABLE IF NOT EXISTS execution_spans (
    owner_ref TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    parent_span_id TEXT,
    work_unit_id TEXT,
    kind TEXT NOT NULL,
    label_key TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0),
    join_policy TEXT,
    started_at TEXT,
    last_activity_at TEXT,
    ended_at TEXT,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    PRIMARY KEY (owner_ref, execution_id, span_id),
    CHECK (parent_span_id IS NULL OR parent_span_id != span_id)
)
"""

_CREATE_EXECUTION_WORK_UNITS = """
CREATE TABLE IF NOT EXISTS execution_work_units (
    owner_ref TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    work_unit_id TEXT NOT NULL,
    parent_span_id TEXT NOT NULL,
    operation_key TEXT NOT NULL,
    driver TEXT NOT NULL,
    status TEXT NOT NULL,
    join_policy TEXT,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0),
    max_attempts INTEGER NOT NULL DEFAULT 1 CHECK (max_attempts > 0),
    next_attempt_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    provider_kind TEXT,
    provider_task_id TEXT,
    provider_revision INTEGER NOT NULL DEFAULT 0
        CHECK (provider_revision >= 0),
    provider_trace_cursor TEXT,
    provider_trace_revision INTEGER NOT NULL DEFAULT 0
        CHECK (provider_trace_revision >= 0),
    provider_trace_adapter_version TEXT,
    provider_trace_overlap_json TEXT NOT NULL DEFAULT '[]',
    provider_trace_contact_at TEXT,
    provider_trace_health TEXT NOT NULL DEFAULT 'healthy',
    cancellation_state TEXT,
    deadline_at TEXT,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    PRIMARY KEY (owner_ref, execution_id, work_unit_id)
)
"""

_CREATE_EXECUTION_PROJECTION_V2 = """
CREATE TABLE IF NOT EXISTS execution_projection_v2 (
    owner_ref TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    latest_seq INTEGER NOT NULL DEFAULT 0 CHECK (latest_seq >= 0),
    first_available_seq INTEGER NOT NULL DEFAULT 1
        CHECK (first_available_seq >= 1),
    projection_json TEXT NOT NULL,
    projection_revision INTEGER NOT NULL DEFAULT 0
        CHECK (projection_revision >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_ref, execution_id)
)
"""

_CREATE_EXECUTION_EVENT_IDEMPOTENCY_V2 = """
CREATE TABLE IF NOT EXISTS execution_event_idempotency_v2 (
    owner_ref TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    source_idempotency_key TEXT NOT NULL,
    event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_ref, execution_id, source_idempotency_key)
)
"""

_CREATE_EXECUTION_OPERATIONS_V2 = """
CREATE TABLE IF NOT EXISTS execution_operations_v2 (
    owner_ref TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
    command_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    outcome_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_ref, execution_id, operation_id)
)
"""

_CREATE_EXECUTION_COMMANDS_V2 = """
CREATE TABLE IF NOT EXISTS execution_commands_v2 (
    owner_ref TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    command_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    next_attempt_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    classification TEXT,
    boundary_state TEXT,
    first_error_code TEXT,
    last_error_code TEXT,
    next_reconcile_at TEXT,
    reconcile_attempt INTEGER NOT NULL DEFAULT 0 CHECK (
        reconcile_attempt >= 0
    ),
    reconcile_redispatch_count INTEGER NOT NULL DEFAULT 0 CHECK (
        reconcile_redispatch_count >= 0
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    PRIMARY KEY (owner_ref, execution_id)
)
"""

_CREATE_EXECUTION_TARGET_BINDINGS_V2 = """
CREATE TABLE IF NOT EXISTS execution_target_bindings_v2 (
    owner_ref TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    role TEXT NOT NULL,
    name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    delivery_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_ref, execution_id, target_kind, target_id)
)
"""

_CREATE_EXECUTION_LOG_ARTIFACTS_V2 = """
CREATE TABLE IF NOT EXISTS execution_log_artifacts_v2 (
    owner_ref TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    content_blob BLOB NOT NULL,
    content_sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_ref, execution_id, target_id)
)
"""

_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_owner_execution_v2 "
    "ON runs(user_id, execution_id) WHERE execution_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_execution_events_v2_type_seq "
    "ON execution_events_v2(owner_ref, execution_id, event_type, seq)",
    "CREATE INDEX IF NOT EXISTS idx_execution_events_v2_occurred "
    "ON execution_events_v2(occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_execution_spans_parent "
    "ON execution_spans(owner_ref, execution_id, parent_span_id)",
    "CREATE INDEX IF NOT EXISTS idx_execution_work_units_due "
    "ON execution_work_units(status, next_attempt_at, lease_expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_execution_work_units_provider "
    "ON execution_work_units(provider_kind, provider_task_id)",
    "CREATE INDEX IF NOT EXISTS idx_execution_operations_v2_state "
    "ON execution_operations_v2(state, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_execution_commands_v2_due "
    "ON execution_commands_v2(state, next_attempt_at, lease_expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_execution_commands_v2_reconcile_due "
    "ON execution_commands_v2(state, next_reconcile_at, lease_expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_execution_target_bindings_v2_execution "
    "ON execution_target_bindings_v2(owner_ref, execution_id)",
    "CREATE INDEX IF NOT EXISTS idx_execution_log_artifacts_v2_execution "
    "ON execution_log_artifacts_v2(owner_ref, execution_id)",
)


def migrate_execution_journal_v2(connection: sqlite3.Connection) -> None:
    """Apply the idempotent V2 schema without rewriting legacy rows."""
    existing_run_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(runs)")
    }
    for column, column_type in EXECUTION_V2_RUN_COLUMNS:
        if column not in existing_run_columns:
            connection.execute(
                f"ALTER TABLE runs ADD COLUMN {column} {column_type}"
            )
    connection.execute(_CREATE_EXECUTION_EVENTS_V2)
    connection.execute(_CREATE_EXECUTION_SPANS)
    connection.execute(_CREATE_EXECUTION_WORK_UNITS)
    existing_work_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(execution_work_units)"
        )
    }
    for column, column_type in EXECUTION_V2_WORK_UNIT_COLUMNS:
        if column not in existing_work_columns:
            connection.execute(
                "ALTER TABLE execution_work_units ADD COLUMN "
                f"{column} {column_type}"
            )
    connection.execute(_CREATE_EXECUTION_PROJECTION_V2)
    connection.execute(_CREATE_EXECUTION_EVENT_IDEMPOTENCY_V2)
    connection.execute(_CREATE_EXECUTION_OPERATIONS_V2)
    connection.execute(_CREATE_EXECUTION_COMMANDS_V2)
    existing_command_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(execution_commands_v2)"
        )
    }
    for column, column_type in EXECUTION_V2_COMMAND_COLUMNS:
        if column not in existing_command_columns:
            connection.execute(
                "ALTER TABLE execution_commands_v2 ADD COLUMN "
                f"{column} {column_type}"
            )
    connection.execute(_CREATE_EXECUTION_TARGET_BINDINGS_V2)
    connection.execute(_CREATE_EXECUTION_LOG_ARTIFACTS_V2)
    for statement in _INDEX_DDL:
        connection.execute(statement)

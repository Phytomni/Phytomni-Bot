# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Explicit, user-scoped cross-session memory domain types."""

from .accessor import (
    MemoryAccessor,
    MemoryGraphContext,
    current_memory_accessor,
    get_default_memory_accessor,
    memory_accessor_context,
    memory_policy_from_config,
    resolve_memory_accessor,
)
from .models import (
    DEFAULT_MEMORY_MAX_CONTENT_BYTES,
    DEFAULT_MEMORY_MAX_ITEMS,
    DEFAULT_MEMORY_MAX_RETRIEVAL,
    DEFAULT_MEMORY_MAX_TAG_BYTES,
    DEFAULT_MEMORY_MAX_TAGS,
    DEFAULT_MEMORY_MAX_TOTAL_BYTES,
    DEFAULT_MEMORY_POLICY,
    MemoryAuditOperation,
    MemoryAuditRecord,
    MemoryCreate,
    MemoryId,
    MemoryItem,
    MemoryKind,
    MemoryPolicy,
    MemoryPolicyError,
    MemoryRecord,
    MemoryTag,
    MemoryUserId,
    MemoryWrite,
)
from .sqlite import (
    MemoryConflictError,
    MemoryNotFoundError,
    MemorySchemaError,
    MemoryStore,
    MemoryStoreError,
    memory_audit_context,
)

_PUBLIC_EXPORTS = {
    "DEFAULT_MEMORY_MAX_CONTENT_BYTES": DEFAULT_MEMORY_MAX_CONTENT_BYTES,
    "DEFAULT_MEMORY_MAX_ITEMS": DEFAULT_MEMORY_MAX_ITEMS,
    "DEFAULT_MEMORY_MAX_RETRIEVAL": DEFAULT_MEMORY_MAX_RETRIEVAL,
    "DEFAULT_MEMORY_MAX_TAG_BYTES": DEFAULT_MEMORY_MAX_TAG_BYTES,
    "DEFAULT_MEMORY_MAX_TAGS": DEFAULT_MEMORY_MAX_TAGS,
    "DEFAULT_MEMORY_MAX_TOTAL_BYTES": DEFAULT_MEMORY_MAX_TOTAL_BYTES,
    "DEFAULT_MEMORY_POLICY": DEFAULT_MEMORY_POLICY,
    "MemoryAccessor": MemoryAccessor,
    "MemoryGraphContext": MemoryGraphContext,
    "MemoryAuditOperation": MemoryAuditOperation,
    "MemoryAuditRecord": MemoryAuditRecord,
    "MemoryConflictError": MemoryConflictError,
    "MemoryCreate": MemoryCreate,
    "MemoryId": MemoryId,
    "MemoryItem": MemoryItem,
    "MemoryKind": MemoryKind,
    "MemoryNotFoundError": MemoryNotFoundError,
    "MemoryPolicy": MemoryPolicy,
    "MemoryPolicyError": MemoryPolicyError,
    "MemoryRecord": MemoryRecord,
    "MemorySchemaError": MemorySchemaError,
    "MemoryStore": MemoryStore,
    "MemoryStoreError": MemoryStoreError,
    "MemoryTag": MemoryTag,
    "MemoryUserId": MemoryUserId,
    "MemoryWrite": MemoryWrite,
    "current_memory_accessor": current_memory_accessor,
    "get_default_memory_accessor": get_default_memory_accessor,
    "memory_accessor_context": memory_accessor_context,
    "memory_audit_context": memory_audit_context,
    "memory_policy_from_config": memory_policy_from_config,
    "resolve_memory_accessor": resolve_memory_accessor,
}

__all__ = list(_PUBLIC_EXPORTS)

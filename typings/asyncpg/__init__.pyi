# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)

"""Minimal asyncpg declarations used by the GaussDB integration.

The upstream driver does not publish a ``py.typed`` marker.  These declarations
cover the small public surface used by Phytomni and deliberately keep driver
rows as ``object`` values until the SQL envelope normalizes them.
"""

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from . import exceptions

class Record(Mapping[str, object]):
    """Mapping-like row returned by ``Connection.fetch``."""

    def __getitem__(self, key: str) -> object: ...
    def __iter__(self): ...
    def __len__(self) -> int: ...

class Connection(Protocol):
    """Connection operations used by application and probe code."""

    def transaction(
        self, *, readonly: bool = ...
    ) -> AbstractAsyncContextManager[None]:
        """Return an async transaction context."""

    async def execute(
        self,
        query: str,
        *args: object,
        **kwargs: object,
    ) -> str:
        """Execute one SQL statement."""

    async def fetch(
        self,
        query: str,
        *args: object,
        **kwargs: object,
    ) -> list[Record]:
        """Fetch rows for one SQL statement."""

    async def fetchval(
        self,
        query: str,
        *args: object,
        **kwargs: object,
    ) -> object:
        """Fetch one scalar value."""

    async def close(self) -> None:
        """Close the connection."""

_PoolBorrower = AbstractAsyncContextManager[Connection]

class Pool(Protocol):
    """Pool operations used by the application and probe."""

    def acquire(self) -> _PoolBorrower:
        """Borrow one connection."""

    async def close(self) -> None:
        """Close the pool."""

async def connect(*_args: object, **_kwargs: object) -> Connection:
    """Open one driver connection."""

async def create_pool(*_args: object, **_kwargs: object) -> Pool:
    """Create a driver connection pool."""

PostgresError = exceptions.PostgresError
InterfaceError = exceptions.InterfaceError

# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)

"""Minimal openpyxl declarations used by deterministic demo generation."""

from collections.abc import Iterable
from datetime import datetime
from io import BytesIO

class DocumentProperties:
    """Workbook metadata fields written by the demo generator."""

    @property
    def creator(self) -> str | None:
        """Return the workbook creator."""

    @creator.setter
    def creator(self, _value: str | None) -> None:
        """Set the workbook creator."""

    @property
    def last_modified_by(self) -> str | None:
        """Return the last modifier."""

    @last_modified_by.setter
    def last_modified_by(self, _value: str | None) -> None:
        """Set the last modifier."""

    lastModifiedBy = last_modified_by

    @property
    def created(self) -> datetime | None:
        """Return the creation timestamp."""

    @created.setter
    def created(self, _value: datetime | None) -> None:
        """Set the creation timestamp."""

    @property
    def modified(self) -> datetime | None:
        """Return the modification timestamp."""

    @modified.setter
    def modified(self, _value: datetime | None) -> None:
        """Set the modification timestamp."""


class Worksheet:
    """Worksheet operations used by the demo generator."""

    @property
    def title(self) -> str:
        """Return the worksheet title."""

    @title.setter
    def title(self, _value: str) -> None:
        """Set the worksheet title."""

    def append(self, row: Iterable[object]) -> None:
        """Append one row to the worksheet."""


class Workbook:
    """Workbook operations used by the demo generator."""

    @property
    def active(self) -> Worksheet | None:
        """Return the active worksheet."""

    @property
    def properties(self) -> DocumentProperties:
        """Return workbook document properties."""

    def __init__(self) -> None:
        """Create an in-memory workbook."""

    def save(self, filename: str | BytesIO) -> None:
        """Write the workbook to a path or binary stream."""

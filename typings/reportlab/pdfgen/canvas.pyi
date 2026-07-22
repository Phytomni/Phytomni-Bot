# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.

"""Minimal reportlab canvas declarations used by demo generation."""


class TextObject:
    """Text operations used by the deterministic PDF writer."""

    def set_text_origin(self, x: float, y: float) -> None:
        """Set the text cursor origin."""

    setTextOrigin = set_text_origin

    def set_leading(self, leading: float) -> None:
        """Set the text line leading."""

    setLeading = set_leading

    def text_line(self, text: str) -> None:
        """Append one line of text."""

    textLine = text_line


class Canvas:
    """Canvas operations used by the deterministic PDF writer."""

    def __init__(
        self,
        filename: str,
        *,
        pagesize: tuple[float, float],
        invariant: int,
        pageCompression: int,
    ) -> None:
        """Create a canvas with deterministic output settings."""

    def set_title(self, title: str) -> None:
        """Set the document title."""

    setTitle = set_title

    def set_author(self, author: str) -> None:
        """Set the document author."""

    setAuthor = set_author

    def set_creator(self, creator: str) -> None:
        """Set the document creator."""

    setCreator = set_creator

    def set_subject(self, subject: str) -> None:
        """Set the document subject."""

    setSubject = set_subject

    def set_font(self, font_name: str, font_size: float) -> None:
        """Select the drawing font."""

    setFont = set_font

    def begin_text(self) -> TextObject:
        """Return a text object."""

    beginText = begin_text

    def draw_text(self, text: TextObject) -> None:
        """Draw a text object."""

    drawText = draw_text

    def show_page(self) -> None:
        """Finish the current page."""

    showPage = show_page

    def save(self) -> None:
        """Finalize the document."""

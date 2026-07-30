# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.

"""Minimal reportlab canvas declarations used by demo generation."""

class TextObject:
    """Text operations used by the deterministic PDF writer."""

    def set_text_origin(self, x: float, y: float) -> None:
        """Set the text cursor origin."""

    def set_leading(self, leading: float) -> None:
        """Set the text line leading."""

    def text_line(self, text: str) -> None:
        """Append one line of text."""

    def __getattr__(self, name: str): ...

class Canvas:
    """Canvas operations used by the deterministic PDF writer."""

    def __init__(
        self,
        filename: str,
        *,
        pagesize: tuple[float, float],
        invariant: int,
        **kwargs: int,
    ) -> None:
        """Create a canvas with deterministic output settings."""

    def set_title(self, title: str) -> None:
        """Set the document title."""

    def set_author(self, author: str) -> None:
        """Set the document author."""

    def set_creator(self, creator: str) -> None:
        """Set the document creator."""

    def set_subject(self, subject: str) -> None:
        """Set the document subject."""

    def set_font(self, font_name: str, font_size: float) -> None:
        """Select the drawing font."""

    def begin_text(self) -> TextObject:
        """Return a text object."""

    def draw_text(self, text: TextObject) -> None:
        """Draw a text object."""

    def show_page(self) -> None:
        """Finish the current page."""

    def save(self) -> None:
        """Finalize the document."""

    def __getattr__(self, name: str): ...

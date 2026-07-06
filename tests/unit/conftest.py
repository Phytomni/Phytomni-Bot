# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared fixtures for the resolver warn-but-accept unit tests.

Both resolver tests route a package logger's records into caplog
(defeating ``configure_logging``'s ``propagate=False``) and assert the
same unsupported-species WARNING, so the bodies live here once to keep
the two test modules below the duplicate-code guard.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

import pytest


@pytest.fixture
def attach_resolver_caplog(
    caplog: pytest.LogCaptureFixture,
) -> Iterator[Callable[[str], pytest.LogCaptureFixture]]:
    """Return a callable attaching caplog to a named package logger.

    ``common/logging_config.configure_logging`` sets the package
    logger's ``propagate=False`` so its warnings never reach pytest's
    root-attached caplog once configuration runs; attaching caplog's
    handler to the named logger sidesteps the gap. Handlers detach on
    teardown so the fixture stays per-test.
    """
    targets: list[logging.Logger] = []

    def _attach(logger_name: str) -> pytest.LogCaptureFixture:
        target = logging.getLogger(logger_name)
        target.addHandler(caplog.handler)
        target.setLevel(logging.WARNING)
        targets.append(target)
        return caplog

    yield _attach
    for target in targets:
        target.removeHandler(caplog.handler)


@pytest.fixture
def assert_unsupported_species_warning() -> (
    Callable[[list[logging.LogRecord], str], None]
):
    """Return an assertion for the unsupported-species WARNING shape."""

    def _assert(records: list[logging.LogRecord], expected_code: str) -> None:
        unsupported = [
            record
            for record in records
            if record.levelno == logging.WARNING
            and "not in the supported data map" in record.getMessage()
        ]
        assert unsupported, [r.getMessage() for r in records]
        assert expected_code in unsupported[0].getMessage()

    return _assert

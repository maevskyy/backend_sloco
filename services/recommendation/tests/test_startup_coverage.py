"""Startup coverage guard for the v4 engine (recommender-config-audit.md P0-2).

A mismatched artifact set silently drops unmatched places from candidates; the
guard logs the join coverage at engine init and WARNs below the threshold.
"""

import logging

import pytest

from recommendation_service.main import COVERAGE_WARN_RATIO, log_v4_embedding_coverage

LOGGER_NAME = "recommendation_service.main"


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_full_coverage_logs_info_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        log_v4_embedding_coverage(12578, 12578)
    assert "12578/12578" in caplog.text
    assert not _warnings(caplog)


def test_coverage_at_threshold_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        log_v4_embedding_coverage(95, 100)
    assert not _warnings(caplog)


def test_mismatched_artifact_set_warns(caplog: pytest.LogCaptureFixture) -> None:
    # The real P0-2 shape: old 2 508-place embedding set vs the 12 578-row catalog.
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        log_v4_embedding_coverage(2508, 12578)
    warnings = _warnings(caplog)
    assert warnings
    assert "artifact set" in warnings[0].getMessage()


def test_empty_catalog_warns_instead_of_dividing_by_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        log_v4_embedding_coverage(0, 0)
    assert _warnings(caplog)


def test_threshold_constant_is_sane() -> None:
    assert 0.5 < COVERAGE_WARN_RATIO <= 1.0

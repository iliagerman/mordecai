import logging

from app.logging_filters import SuppressHealthCheckAccessLog, install_uvicorn_access_log_filters


def test_suppress_healthcheck_access_log_by_args() -> None:
    filt = SuppressHealthCheckAccessLog()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %s',
        args=("127.0.0.1:12345", "GET", "/health", "1.1", 200),
        exc_info=None,
    )
    assert filt.filter(record) is False


def test_suppress_healthcheck_access_log_by_message_fallback() -> None:
    filt = SuppressHealthCheckAccessLog()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='127.0.0.1:12345 - "GET /health HTTP/1.1" 200 OK',
        args=(),
        exc_info=None,
    )
    assert filt.filter(record) is False


def test_non_health_access_log_not_suppressed() -> None:
    filt = SuppressHealthCheckAccessLog()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %s',
        args=("127.0.0.1:12345", "GET", "/api/tasks", "1.1", 200),
        exc_info=None,
    )
    assert filt.filter(record) is True


def test_install_does_not_duplicate_filter() -> None:
    logger = logging.getLogger("uvicorn.access")
    logger.filters.clear()

    install_uvicorn_access_log_filters()
    install_uvicorn_access_log_filters()

    matches = [f for f in logger.filters if isinstance(f, SuppressHealthCheckAccessLog)]
    assert len(matches) == 1

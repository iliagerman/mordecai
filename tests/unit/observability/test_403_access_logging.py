import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.task_router import create_task_router
from app.services.task_service import TaskService


class _DummyTaskService(TaskService):
    # We don't call into the service for whitelist rejections.
    pass


@pytest.fixture
def app_with_403_logging(mocker):
    # Build a minimal FastAPI app mirroring main.py's handler/middleware pieces.
    app = FastAPI()

    from fastapi import HTTPException, Request
    from fastapi.exception_handlers import http_exception_handler

    from app.observability.forbidden_access_log import log_forbidden_request

    @app.middleware("http")
    async def _capture_request_for_error_logs(request: Request, call_next):
        max_bytes = 32 * 1024
        try:
            content_type = request.headers.get("content-type", "")
            if "multipart/form-data" not in content_type:
                body = await request.body()
                if len(body) > max_bytes:
                    request.state._captured_body = body[:max_bytes]
                    request.state._captured_body_truncated = True
                else:
                    request.state._captured_body = body
                    request.state._captured_body_truncated = False

                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}

                request._receive = receive  # type: ignore[attr-defined]
            else:
                request.state._captured_body = b"<multipart omitted>"
                request.state._captured_body_truncated = False
        except Exception:
            request.state._captured_body = b"<capture failed>"
            request.state._captured_body_truncated = False

        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        if exc.status_code == 403:
            await log_forbidden_request(request, exc)
        return await http_exception_handler(request, exc)

    # Mocked service; won't be hit due to whitelist.
    task_service = mocker.Mock(spec=TaskService)
    app.include_router(create_task_router(task_service, allowed_users=["user-allowed"]))

    return app


def test_whitelist_403_emits_access_log(caplog, app_with_403_logging):
    caplog.set_level(logging.WARNING, logger="mordecai.access")

    client = TestClient(app_with_403_logging)
    resp = client.post(
        "/api/tasks",
        json={"userId": "user-denied", "title": "t", "description": "d"},
        headers={"Authorization": "Bearer super-secret-token"},
    )

    assert resp.status_code == 403

    # Find our one-line JSON payload.
    lines = [r.message for r in caplog.records if "ACCESS_FORBIDDEN" in r.message]
    assert lines, "expected ACCESS_FORBIDDEN log line"

    payload = json.loads(lines[-1].split("ACCESS_FORBIDDEN ", 1)[1])

    assert payload["status_code"] == 403
    assert payload["reason"]["code"] == "WHITELIST_DENY"

    # Ensure we don't leak bearer token in logs.
    headers = payload.get("headers") or {}
    # Sanitize() should redact authorization header.
    assert headers.get("authorization") == "[REDACTED]"

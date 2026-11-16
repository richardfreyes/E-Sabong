"""WebSocket handshakes carry the access token in the query string; logs must never keep it."""

import io
import logging

from app.core.logging import install_redaction, redact

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"  # noqa: S105 - fake, unsigned sample


def _capture(name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(name)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger, stream


def test_uvicorn_websocket_handshake_lines_are_redacted() -> None:
    install_redaction()
    logger, stream = _capture("uvicorn.error")
    # The exact calls uvicorn's websocket protocols make.
    logger.info('%s - "WebSocket %s" [accepted]', "203.0.113.5:0", f"/api/ws?token={TOKEN}")
    logger.info('%s - "WebSocket %s" 403', "203.0.113.5:0", f"/api/ws?lang=en&token={TOKEN}&v=2")
    out = stream.getvalue()
    assert TOKEN not in out
    assert '"WebSocket /api/ws?token=[redacted]" [accepted]' in out
    assert "/api/ws?lang=en&token=[redacted]&v=2" in out


def test_access_lines_and_plain_messages_are_redacted_without_touching_other_text() -> None:
    install_redaction()
    logger, stream = _capture("uvicorn.access")
    logger.warning('%s - "%s %s HTTP/%s" %d', "203.0.113.5:0", "GET", f"/api/x?access_token={TOKEN}&page=2", "1.1", 200)
    logging.getLogger("uvicorn.error").addHandler(logging.StreamHandler(stream))
    logging.getLogger("uvicorn.error").info(f"connection rejected for /api/ws?token={TOKEN}")
    out = stream.getvalue()
    assert TOKEN not in out
    assert "page=2" in out and "GET" in out and "200" in out


def test_redact_leaves_non_credential_values_alone() -> None:
    assert redact("/api/v1/declarator/events?compact=true") == "/api/v1/declarator/events?compact=true"
    assert redact("/api/ws?tokens=keep") == "/api/ws?tokens=keep"
    assert redact(42) == 42


def test_installing_twice_adds_one_filter() -> None:
    install_redaction()
    install_redaction()
    assert sum(type(f).__name__ == "RedactCredentials" for f in logging.getLogger("uvicorn.error").filters) == 1

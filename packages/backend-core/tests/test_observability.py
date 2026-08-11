"""Structured logging, redaction and correlation context (taskbook §63)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from backend_core.observability import (
    JsonFormatter,
    bind_context,
    get_context,
    get_request_id,
    redact,
    redact_text,
    set_context,
)


def render(record: logging.LogRecord) -> dict[str, Any]:
    """Format a record and parse it back as JSON."""
    parsed: dict[str, Any] = json.loads(JsonFormatter().format(record))
    return parsed


def make_record(msg: str, **extra: Any) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestRedaction:
    """§63 forbids logging API keys, passwords and full Authorization headers."""

    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "PASSWORD",
            "user_password",
            "secret",
            "client_secret",
            "api_key",
            "apiKey",
            "api-key",
            "access_key",
            "authorization",
            "Authorization",
            "token",
            "refresh_token",
            "jwt",
            "cookie",
            "session_id",
            "private_key",
            "credential",
            "signature",
        ],
    )
    def test_sensitive_keys_are_redacted(self, key: str) -> None:
        result = redact({key: "super-secret-value"})
        assert result[key] == "[REDACTED]"
        assert "super-secret-value" not in json.dumps(result)

    def test_redaction_reaches_nested_structures(self) -> None:
        payload = {
            "provider": {
                "name": "runway",
                "config": {"api_key": "sk-live-abcdef", "timeout": 30},
            },
            "attempts": [{"authorization": "Bearer abc.def.ghi"}],
        }
        rendered = json.dumps(redact(payload))
        assert "sk-live-abcdef" not in rendered
        assert "abc.def.ghi" not in rendered
        # Non-sensitive siblings survive — redaction must not gut the log.
        assert "runway" in rendered
        assert "30" in rendered

    def test_connection_string_password_is_stripped(self) -> None:
        text = "could not connect to postgresql+psycopg://aipvs:hunter2@db:5432/aipvs"
        cleaned = redact_text(text)
        assert "hunter2" not in cleaned
        assert "[REDACTED]" in cleaned
        # The rest stays useful for debugging.
        assert "db:5432" in cleaned
        assert "aipvs" in cleaned

    def test_long_base64_blob_is_dropped(self) -> None:
        """§63: never log raw image base64."""
        blob = "A" * 400
        cleaned = redact_text(f"image payload {blob} end")
        assert blob not in cleaned
        assert "[REDACTED_BLOB]" in cleaned

    def test_bytes_are_summarised_not_dumped(self) -> None:
        assert redact({"body": b"\x00\x01" * 100})["body"] == "<200 bytes>"

    def test_cyclic_structure_does_not_hang(self) -> None:
        """Logging must never be able to take down the process it describes."""
        cycle: dict[str, Any] = {"name": "root"}
        cycle["self"] = cycle
        assert "[REDACTED_DEPTH]" in json.dumps(redact(cycle))

    def test_message_body_is_redacted_too(self) -> None:
        record = make_record("connecting to redis://user:hunter2@cache:6379/0")
        assert "hunter2" not in json.dumps(render(record))


class TestJsonFormatter:
    def test_record_is_valid_single_line_json(self) -> None:
        output = JsonFormatter().format(make_record("hello"))
        assert "\n" not in output
        assert json.loads(output)["message"] == "hello"

    def test_core_fields_present(self) -> None:
        payload = render(make_record("hello"))
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test"
        assert payload["timestamp"].endswith("+00:00")

    def test_extra_fields_are_emitted(self) -> None:
        payload = render(make_record("done", http_status=200, duration_ms=12.5))
        assert payload["http_status"] == 200
        assert payload["duration_ms"] == 12.5

    def test_correlation_context_is_injected(self) -> None:
        with bind_context(request_id="req-1", workspace_id="ws-1", job_id="job-1"):
            payload = render(make_record("working"))
        assert payload["request_id"] == "req-1"
        assert payload["workspace_id"] == "ws-1"
        assert payload["job_id"] == "job-1"

    def test_unserialisable_values_do_not_raise(self) -> None:
        import uuid
        from datetime import UTC, datetime

        payload = render(make_record("ids", entity_id=uuid.uuid4(), at=datetime.now(tz=UTC)))
        assert isinstance(payload["entity_id"], str)
        assert isinstance(payload["at"], str)

    def test_exception_is_recorded_and_redacted(self) -> None:
        try:
            raise ValueError("failed on postgresql://u:hunter2@h/db")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="boom",
                args=(),
                exc_info=sys.exc_info(),
            )
        payload = render(record)
        assert "ValueError" in payload["exception"]
        assert "hunter2" not in payload["exception"]


class TestCorrelationContext:
    def test_context_starts_empty(self) -> None:
        assert get_request_id() is None

    def test_bind_restores_previous_value(self) -> None:
        with bind_context(request_id="outer"):
            assert get_request_id() == "outer"
            with bind_context(request_id="inner"):
                assert get_request_id() == "inner"
            assert get_request_id() == "outer"
        assert get_request_id() is None

    def test_context_is_restored_after_an_exception(self) -> None:
        with pytest.raises(RuntimeError), bind_context(job_id="job-1"):
            raise RuntimeError("boom")
        assert get_context() == {}

    def test_unknown_fields_are_ignored(self) -> None:
        """Adding context must never be able to break the work it describes."""
        set_context(not_a_real_field="x")
        assert "not_a_real_field" not in get_context()

    async def test_concurrent_tasks_do_not_share_context(self) -> None:
        """The reason this uses contextvars rather than a global."""
        seen: list[str | None] = []

        async def worker(name: str, delay: float) -> None:
            with bind_context(request_id=name):
                await asyncio.sleep(delay)
                seen.append(get_request_id())

        await asyncio.gather(worker("a", 0.02), worker("b", 0.01))
        assert sorted(x for x in seen if x) == ["a", "b"]

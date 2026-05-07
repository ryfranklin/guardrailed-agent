"""Contract tests for gagent_client.trace.

Pure-logic only. summarize_trace is exercised against fixture traces;
emit_invocation_log is exercised with a stub CloudWatch Logs client to
verify shape, daily-stream sharding, and best-effort failure semantics.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from gagent_client import (
    TraceSummary,
    emit_invocation_log,
    summarize_trace,
)


# ---- TraceSummary dataclass ----

class TestTraceSummary:
    def test_default_construction(self):
        s = TraceSummary()
        assert s.tools_called == []
        assert s.guardrail_blocks == 0
        assert s.guardrail_events == []

    def test_independent_default_lists(self):
        a = TraceSummary()
        b = TraceSummary()
        a.tools_called.append("/customers")
        assert b.tools_called == []


# ---- summarize_trace ----

class TestSummarizeTrace:
    def test_records_action_group_apipath(self):
        s = TraceSummary()
        summarize_trace(_action_group_event(api_path="/customers"), s)
        assert s.tools_called == ["/customers"]

    def test_records_apipath_when_actiongroupname_also_present(self):
        s = TraceSummary()
        summarize_trace(
            _action_group_event(api_path="/jobs", action_group_name="legacy"),
            s,
        )
        assert s.tools_called == ["/jobs"]

    def test_falls_back_to_actiongroupname(self):
        s = TraceSummary()
        summarize_trace(
            _action_group_event(api_path=None, action_group_name="query_legacy"),
            s,
        )
        assert s.tools_called == ["query_legacy"]

    def test_multiple_calls_accumulate(self):
        s = TraceSummary()
        summarize_trace(_action_group_event(api_path="/customers"), s)
        summarize_trace(_action_group_event(api_path="/jobs"), s)
        summarize_trace(_action_group_event(api_path="/customers"), s)
        assert s.tools_called == ["/customers", "/jobs", "/customers"]

    def test_intervened_guardrail_increments_block_count(self):
        s = TraceSummary()
        summarize_trace(_guardrail_event(action="INTERVENED"), s)
        assert s.guardrail_blocks == 1
        assert s.guardrail_events == [{"action": "INTERVENED"}]

    def test_blocked_guardrail_action_increments(self):
        s = TraceSummary()
        summarize_trace(_guardrail_event(action="BLOCKED"), s)
        assert s.guardrail_blocks == 1

    def test_intervene_lowercase_still_counts(self):
        s = TraceSummary()
        summarize_trace(_guardrail_event(action="intervened"), s)
        assert s.guardrail_blocks == 1

    def test_pass_action_does_not_block(self):
        s = TraceSummary()
        summarize_trace(_guardrail_event(action="PASS"), s)
        assert s.guardrail_blocks == 0
        assert s.guardrail_events == [{"action": "PASS"}]

    def test_unrelated_event_is_noop(self):
        s = TraceSummary()
        summarize_trace({"trace": {"modelInvocationOutput": {}}}, s)
        assert s.tools_called == []
        assert s.guardrail_blocks == 0
        assert s.guardrail_events == []

    def test_empty_event_does_not_raise(self):
        s = TraceSummary()
        summarize_trace({}, s)
        assert s.tools_called == []


def _action_group_event(
    *, api_path: str | None, action_group_name: str = "",
) -> dict:
    invocation: dict = {"actionGroupInvocationInput": {}}
    if api_path is not None:
        invocation["actionGroupInvocationInput"]["apiPath"] = api_path
    if action_group_name:
        invocation["actionGroupInvocationInput"]["actionGroupName"] = action_group_name
    return {
        "trace": {
            "orchestrationTrace": {
                "invocationInput": invocation,
            }
        }
    }


def _guardrail_event(*, action: str) -> dict:
    return {"trace": {"guardrailTrace": {"action": action}}}


# ---- emit_invocation_log ----

def _emit(logs_client, **overrides):
    kwargs = {
        "session_id": "sess-1",
        "persona": "owner",
        "role_arn": "arn:aws:iam::123:role/owner",
        "role_session_name": "gagent-owner-abc123",
        "surface": "lib",
        "trace_name": "case-x",
        "input_text": "show me X",
        "output_text": "here is X",
        "summary": TraceSummary(
            tools_called=["/customers", "/jobs"],
            guardrail_blocks=1,
            guardrail_events=[{"action": "INTERVENED"}],
        ),
        "duration_seconds": 2.5,
        "started_at": 1714750000.0,
        "metadata": {"service_region": None},
        "log_group": "/gagent/invocations",
        "region": "us-east-1",
        "logs_client": logs_client,
    }
    kwargs.update(overrides)
    return emit_invocation_log(**kwargs)


class TestEmitInvocationLog:
    def test_writes_payload_to_log_group(self):
        client = MagicMock()
        result = _emit(client)
        assert result is not None
        client.create_log_stream.assert_called_once()
        client.put_log_events.assert_called_once()

        put_kwargs = client.put_log_events.call_args.kwargs
        assert put_kwargs["logGroupName"] == "/gagent/invocations"
        assert put_kwargs["logStreamName"] == result
        events = put_kwargs["logEvents"]
        assert len(events) == 1
        payload = json.loads(events[0]["message"])
        assert payload["session_id"] == "sess-1"
        assert payload["persona"] == "owner"
        assert payload["surface"] == "lib"
        assert payload["trace_name"] == "case-x"
        assert payload["tools_called"] == ["/customers", "/jobs"]
        assert payload["guardrail_blocks"] == 1
        assert payload["duration_seconds"] == 2.5

    def test_stream_name_is_daily_sharded_by_persona(self):
        client = MagicMock()
        stream = _emit(client, persona="dispatcher", session_id="sess-zzz")
        assert stream is not None
        # Format: YYYY/MM/DD/<persona>/<session_id>
        parts = stream.split("/")
        assert parts[-2] == "dispatcher"
        assert parts[-1] == "sess-zzz"
        assert len(parts) == 5

    def test_resource_already_exists_is_swallowed(self):
        client = MagicMock()
        client.create_log_stream.side_effect = ClientError(
            {"Error": {"Code": "ResourceAlreadyExistsException", "Message": "exists"}},
            "CreateLogStream",
        )
        result = _emit(client)
        assert result is not None
        client.put_log_events.assert_called_once()

    def test_unexpected_client_error_returns_none(self):
        client = MagicMock()
        client.create_log_stream.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "nope"}},
            "CreateLogStream",
        )
        result = _emit(client)
        assert result is None

    def test_put_log_events_failure_returns_none(self):
        client = MagicMock()
        client.put_log_events.side_effect = RuntimeError("transient")
        result = _emit(client)
        assert result is None

    def test_truncates_oversized_io(self):
        client = MagicMock()
        big_input = "x" * 10_000
        big_output = "y" * 100_000
        result = _emit(client, input_text=big_input, output_text=big_output)
        assert result is not None
        payload = json.loads(client.put_log_events.call_args.kwargs["logEvents"][0]["message"])
        assert len(payload["input"]) <= 4_000
        assert len(payload["output"]) <= 16_000

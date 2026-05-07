"""Tests for the Phase 2.c tools (ADR-009 §Tools 7-9).

Each tool is exercised in:
  * a happy-path mocked invocation, and
  * one failure mode (missing dep, AWS error, malformed input).

The propose_query tests also assert the negative invariant: no
``boto3.client('bedrock-agent-runtime')`` call is made — i.e., the
action-group Lambda can never fire.
"""

from __future__ import annotations

import io
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from gagent_client import FlagPersonaResolver
from mcp_server import (
    ServerConfig,
    ServerState,
    health_impl,
    health_impl_async,
    propose_query_impl,
    recent_traces_impl,
)
from mcp_server import operability_tools


def _state(
    *,
    log_group: str = "/gagent/invocations",
    athena_workgroup: str | None = "gagent-demo",
) -> ServerState:
    cfg = ServerConfig(
        resolver=FlagPersonaResolver({
            "dispatcher": "arn:aws:iam::1:role/d",
            "technician_lead": "arn:aws:iam::1:role/tl",
            "owner": "arn:aws:iam::1:role/o",
        }),
        agent_id="A",
        agent_alias_id="B",
        region="us-east-1",
        glue_database="guardrailed_agent_demo",
        default_persona="owner",
        default_service_region=None,
        log_group=log_group,
        token_budget=10_000,
        foundation_model_id="us.anthropic.claude-sonnet-4-6",
        athena_workgroup_name=athena_workgroup,
    )
    return ServerState(config=cfg)


def _insights_row(
    *,
    persona: str,
    name: str,
    age_minutes: int = 1,
    tools_called: list[str] | None = None,
    guardrail_blocks: int = 0,
    duration_seconds: float = 1.5,
    session_id: str | None = None,
) -> dict[str, str]:
    """Build a row matching the shape returned by _query_logs_insights.

    Insights returns each value as a string; arrays/objects come back JSON-encoded.
    """
    ts = (
        datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    ).isoformat()
    return {
        "@timestamp": ts,
        "session_id": session_id or f"sid-{name}",
        "persona": persona,
        "role_arn": f"arn:aws:iam::1:role/{persona}",
        "role_session_name": f"gagent-{persona}-{name}",
        "surface": "mcp",
        "trace_name": name,
        "duration_seconds": str(duration_seconds),
        "tools_called": json.dumps(
            tools_called if tools_called is not None
            else (["/customers"] if persona == "owner" else [])
        ),
        "guardrail_blocks": str(guardrail_blocks),
        "input": f"prompt for {name}",
        "output": f"answer for {name}",
        "started_at": ts,
        "finished_at": ts,
        "metadata": json.dumps({"persona": persona}),
    }


def _patch_insights(monkeypatch, rows):
    """Replace _query_logs_insights with a stub returning the given rows.

    ``rows`` may be a list (returned verbatim) or a callable that receives
    the kwargs and returns a list.
    """
    if callable(rows):
        def fake(**kwargs):
            return rows(**kwargs)
    else:
        def fake(**kwargs):
            return list(rows)
    monkeypatch.setattr(operability_tools, "_query_logs_insights", fake)
    return fake


def _claude_text_response(text: str) -> dict:
    """Mimics a Bedrock Runtime InvokeModel response body for Claude."""
    payload = {
        "id": "msg_abc",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }
    return {
        "body": io.BytesIO(json.dumps(payload).encode("utf-8")),
        "contentType": "application/json",
    }


# ---------- propose_query ----------

class TestProposeQuery:
    def _proposal_text(self) -> str:
        return json.dumps({
            "drafted_sql": (
                "SELECT customer_id, service_tier FROM customer "
                "WHERE service_region = 'tempe-mesa' AND is_current = TRUE LIMIT 50"
            ),
            "chosen_tool": "/customers",
            "tool_arguments": {
                "filters": {"service_region": "tempe-mesa"},
                "limit": 50,
            },
            "rationale": (
                "The question asks for customers in a specific service "
                "region; /customers is the SCD2-aware tool for the "
                "customer table."
            ),
        })

    def test_requires_question(self):
        result = propose_query_impl(_state(), {})
        assert "error" in result

    def test_drafts_sql_via_invoke_model(self):
        bedrock = MagicMock()
        bedrock.invoke_model.return_value = _claude_text_response(
            self._proposal_text(),
        )
        with patch("mcp_server.operability_tools.boto3.client") as mock_client:
            mock_client.return_value = bedrock
            result = propose_query_impl(
                _state(),
                {"question": "How many customers in tempe-mesa?"},
            )

        assert "error" not in result
        assert result["executed"] is False
        assert result["chosen_tool"] == "/customers"
        assert "service_region" in (result["drafted_sql"] or "")
        assert result["tool_arguments"]["filters"]["service_region"] == "tempe-mesa"
        assert result["persona"] == "owner"
        assert result["model_id"] == "us.anthropic.claude-sonnet-4-6"

    def test_never_constructs_bedrock_agent_runtime_client(self):
        """The negative invariant: tool 7 must NOT touch the agent path."""
        bedrock = MagicMock()
        bedrock.invoke_model.return_value = _claude_text_response(
            self._proposal_text(),
        )
        clients_requested: list[str] = []

        def mock_client(service_name, *args, **kwargs):
            clients_requested.append(service_name)
            if service_name == "bedrock-runtime":
                return bedrock
            raise AssertionError(
                f"propose_query must not construct {service_name!r} client",
            )

        with patch("mcp_server.operability_tools.boto3.client", side_effect=mock_client):
            propose_query_impl(_state(), {"question": "anything"})

        assert clients_requested == ["bedrock-runtime"]
        assert "bedrock-agent-runtime" not in clients_requested

    def test_invoke_model_failure_returns_error(self):
        bedrock = MagicMock()
        bedrock.invoke_model.side_effect = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
            "InvokeModel",
        )
        with patch("mcp_server.operability_tools.boto3.client", return_value=bedrock):
            result = propose_query_impl(
                _state(), {"question": "anything"},
            )
        assert "error" in result
        assert "ThrottlingException" in result["error"]

    def test_handles_model_response_with_prose_around_json(self):
        """The model sometimes wraps the JSON in markdown fences or prose."""
        wrapped = (
            "Sure — here is the proposal:\n\n"
            "```json\n" + self._proposal_text() + "\n```"
        )
        bedrock = MagicMock()
        bedrock.invoke_model.return_value = _claude_text_response(wrapped)
        with patch("mcp_server.operability_tools.boto3.client", return_value=bedrock):
            result = propose_query_impl(_state(), {"question": "anything"})
        assert result["chosen_tool"] == "/customers"

    def test_infers_chosen_tool_from_sql_when_model_omits_it(self):
        """Falls back to extract_table_names when chosen_tool is missing."""
        text = json.dumps({
            "drafted_sql": "SELECT * FROM service_job WHERE status = 'completed'",
            "rationale": "looking at jobs",
        })
        bedrock = MagicMock()
        bedrock.invoke_model.return_value = _claude_text_response(text)
        with patch("mcp_server.operability_tools.boto3.client", return_value=bedrock):
            result = propose_query_impl(_state(), {"question": "any"})
        assert result["chosen_tool"] == "/jobs"


# ---------- recent_traces ----------

class TestRecentTraces:
    def test_no_log_group_returns_error(self):
        result = recent_traces_impl(_state(log_group=""), {})
        assert "log_group" in result["error"]

    def test_returns_recent_traces_unfiltered(self, monkeypatch):
        rows = [
            _insights_row(persona="owner", name="a", age_minutes=1),
            _insights_row(persona="dispatcher", name="b", age_minutes=2),
            _insights_row(persona="owner", name="c", age_minutes=3),
        ]
        _patch_insights(monkeypatch, rows)
        result = recent_traces_impl(_state(), {"limit": 10})
        assert "error" not in result
        assert result["trace_count"] == 3
        assert result["persona_filter"] is None
        assert result["log_group"] == "/gagent/invocations"

    def test_filters_by_persona(self, monkeypatch):
        # When the caller passes persona, the tool builds a filter clause
        # in the Insights query; the stub honors that by returning only
        # owner rows here.
        rows = [
            _insights_row(persona="owner", name="a", age_minutes=1),
            _insights_row(persona="owner", name="c", age_minutes=3),
        ]
        seen_query: dict[str, str] = {}

        def capture(**kwargs):
            seen_query["q"] = kwargs.get("query_string", "")
            return rows

        monkeypatch.setattr(operability_tools, "_query_logs_insights", capture)
        result = recent_traces_impl(
            _state(), {"persona": "owner", "limit": 10},
        )
        assert result["trace_count"] == 2
        for t in result["traces"]:
            assert t["persona"] == "owner"
        # The Insights query string must include the filter clause.
        assert 'persona = "owner"' in seen_query["q"]

    def test_invalid_persona_filter(self, monkeypatch):
        _patch_insights(monkeypatch, [])
        result = recent_traces_impl(_state(), {"persona": "hacker"})
        assert "invalid persona" in result["error"]

    def test_clamps_limit(self, monkeypatch):
        rows = [
            _insights_row(persona="owner", name=f"t{i}", age_minutes=i)
            for i in range(5)
        ]
        captured: dict[str, int] = {}

        def capture(**kwargs):
            captured["limit"] = kwargs.get("limit")
            return rows

        monkeypatch.setattr(operability_tools, "_query_logs_insights", capture)
        result = recent_traces_impl(_state(), {"limit": 5})
        assert result["trace_count"] == 5
        assert captured["limit"] == 5

    def test_logs_insights_failure_returns_error(self, monkeypatch):
        def boom(**kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(operability_tools, "_query_logs_insights", boom)
        result = recent_traces_impl(_state(), {})
        assert "Logs Insights query failed" in result["error"]
        assert "network down" in result["error"]

    def test_normalizes_legacy_persona_names(self, monkeypatch):
        rows = [
            _insights_row(persona="Analyst", name="legacy"),
            _insights_row(persona="owner", name="canonical"),
        ]
        _patch_insights(monkeypatch, rows)
        result = recent_traces_impl(_state(), {"limit": 10})
        legacy = next(t for t in result["traces"] if t["trace_name"] == "legacy")
        assert legacy["persona"] == "Analyst"
        assert legacy["normalized_persona"] == "dispatcher"
        assert legacy["persona_is_legacy"] is True
        assert result["legacy_persona_count"] == 1
        assert result["legacy_persona_map"]["Analyst"] == "dispatcher"

    def test_returns_summarized_row_shape(self, monkeypatch):
        rows = [
            _insights_row(
                persona="owner", name="a",
                tools_called=["/customers", "/jobs"],
                guardrail_blocks=2,
                duration_seconds=3.14,
                session_id="abc-123",
            ),
        ]
        _patch_insights(monkeypatch, rows)
        result = recent_traces_impl(_state(), {})
        t = result["traces"][0]
        assert t["session_id"] == "abc-123"
        assert t["tools_called"] == ["/customers", "/jobs"]
        assert t["guardrail_blocks"] == 2
        assert t["duration_seconds"] == pytest.approx(3.14)
        assert t["surface"] == "mcp"
        assert t["role_arn"] == "arn:aws:iam::1:role/owner"
        assert t["role_session_name"] == "gagent-owner-a"
        assert t["input_preview"].startswith("prompt for a")


# ---------- health ----------

class TestHealth:
    def _stub_aws(self) -> dict[str, MagicMock]:
        bedrock = MagicMock()
        bedrock.invoke_model.return_value = {
            "body": io.BytesIO(b'{"content":[{"type":"text","text":"."}]}'),
        }
        athena = MagicMock()
        athena.get_work_group.return_value = {
            "WorkGroup": {"Name": "gagent-demo", "State": "ENABLED"},
        }
        glue = MagicMock()
        glue.get_databases.return_value = {
            "DatabaseList": [{"Name": "guardrailed_agent_demo"}],
        }
        lf = MagicMock()
        lf.get_data_lake_settings.return_value = {
            "DataLakeSettings": {
                "DataLakeAdmins": [
                    {"DataLakePrincipalIdentifier": "arn:aws:iam::1:user/admin"},
                ],
            },
        }
        return {
            "bedrock-runtime": bedrock,
            "athena": athena,
            "glue": glue,
            "lakeformation": lf,
        }

    def _patch_clients(self, clients: dict[str, MagicMock]):
        def mock_client(service_name, *args, **kwargs):
            return clients[service_name]
        return mock_client

    def test_all_checks_pass(self, monkeypatch):
        # last_invocations is "skipped" because log_group is empty.
        clients = self._stub_aws()
        with patch("mcp_server.operability_tools.boto3.client", side_effect=self._patch_clients(clients)):
            result = health_impl(_state(log_group=""), {})
        assert result["overall"] in ("healthy", "degraded")
        names = {c["name"] for c in result["checks"]}
        assert names == {
            "bedrock_runtime", "athena_workgroup", "glue_catalog",
            "lake_formation", "last_invocations",
        }
        last_check = next(c for c in result["checks"] if c["name"] == "last_invocations")
        assert last_check["status"] == "skipped"
        # The other four should be ok.
        for c in result["checks"]:
            if c["name"] != "last_invocations":
                assert c["status"] == "ok", c

    def test_finishes_under_three_seconds(self):
        """Synthetic timing: with mocked clients each check returns
        ~immediately, so wall-clock should be well under 3s."""
        clients = self._stub_aws()
        with patch("mcp_server.operability_tools.boto3.client", side_effect=self._patch_clients(clients)):
            started = time.time()
            result = health_impl(_state(log_group=""), {})
            elapsed = time.time() - started
        assert elapsed < 3.0
        assert result["duration_seconds"] < 3.0

    def test_athena_failure_marks_check_failed(self):
        clients = self._stub_aws()
        clients["athena"].get_work_group.side_effect = ClientError(
            {"Error": {"Code": "InvalidRequestException", "Message": "no such workgroup"}},
            "GetWorkGroup",
        )
        with patch("mcp_server.operability_tools.boto3.client", side_effect=self._patch_clients(clients)):
            result = health_impl(_state(log_group=""), {})
        athena_check = next(c for c in result["checks"] if c["name"] == "athena_workgroup")
        assert athena_check["status"] == "fail"
        assert result["overall"] == "degraded"

    def test_athena_workgroup_unconfigured_skips(self):
        clients = self._stub_aws()
        with patch("mcp_server.operability_tools.boto3.client", side_effect=self._patch_clients(clients)):
            result = health_impl(
                _state(athena_workgroup=None, log_group=""), {},
            )
        athena_check = next(c for c in result["checks"] if c["name"] == "athena_workgroup")
        assert athena_check["status"] == "skipped"

    def test_last_invocations_from_log_group(self, monkeypatch):
        clients = self._stub_aws()
        ts = datetime.now(timezone.utc).isoformat()
        rows = [
            {"@timestamp": ts, "persona": "owner"},
            {"@timestamp": ts, "persona": "dispatcher"},
        ]
        monkeypatch.setattr(
            operability_tools, "_query_logs_insights",
            lambda **kwargs: list(rows),
        )

        with patch("mcp_server.operability_tools.boto3.client", side_effect=self._patch_clients(clients)):
            result = health_impl(_state(), {})

        last = next(c for c in result["checks"] if c["name"] == "last_invocations")
        assert last["status"] == "ok"
        assert "owner" in last["detail"]["last_success_by_persona"]
        assert "dispatcher" in last["detail"]["last_success_by_persona"]
        assert last["detail"]["log_group"] == "/gagent/invocations"

    def test_last_invocations_logs_insights_timeout_marks_degraded(self, monkeypatch):
        """Logs Insights timeouts must surface as ``degraded`` so the
        operator sees the budget breach explicitly."""
        clients = self._stub_aws()

        def slow(**kwargs):
            raise TimeoutError("Logs Insights query exceeded budget")

        monkeypatch.setattr(operability_tools, "_query_logs_insights", slow)

        with patch("mcp_server.operability_tools.boto3.client", side_effect=self._patch_clients(clients)):
            result = health_impl(_state(), {})

        last = next(c for c in result["checks"] if c["name"] == "last_invocations")
        # The sync helper catches the TimeoutError and reports "fail";
        # the async wait_for path reports "degraded". Either is acceptable
        # provided the overall verdict is "degraded".
        assert last["status"] in ("fail", "degraded")
        assert result["overall"] == "degraded"

    def test_health_impl_in_event_loop_raises_clear_error(self):
        """The MCP dispatcher runs in asyncio; calling sync health_impl
        from inside a running loop must fail loudly so callers know to
        use health_impl_async instead.

        Pre-fix: this surfaced as
        ``asyncio.run() cannot be called from a running event loop``
        with no actionable hint.
        """
        import asyncio

        async def call_from_loop():
            health_impl(_state(log_group=""), {})

        with pytest.raises(RuntimeError, match="health_impl_async"):
            asyncio.run(call_from_loop())

    def test_health_impl_async_works_in_event_loop(self):
        """The async variant is what the MCP dispatcher should use."""
        import asyncio

        clients = self._stub_aws()

        async def call_from_loop():
            with patch(
                "mcp_server.operability_tools.boto3.client",
                side_effect=self._patch_clients(clients),
            ):
                return await health_impl_async(_state(log_group=""), {})

        result = asyncio.run(call_from_loop())
        assert result["overall"] in ("healthy", "degraded")
        names = {c["name"] for c in result["checks"]}
        assert names == {
            "bedrock_runtime", "athena_workgroup", "glue_catalog",
            "lake_formation", "last_invocations",
        }

    def test_last_invocations_normalizes_legacy_persona_names(self, monkeypatch):
        """Pre-ADR-008 traces should surface under canonical persona names."""
        clients = self._stub_aws()
        ts = datetime.now(timezone.utc).isoformat()
        rows = [
            {"@timestamp": ts, "persona": "Analyst"},
            {"@timestamp": ts, "persona": "RegionalManager"},
        ]
        monkeypatch.setattr(
            operability_tools, "_query_logs_insights",
            lambda **kwargs: list(rows),
        )

        with patch("mcp_server.operability_tools.boto3.client", side_effect=self._patch_clients(clients)):
            result = health_impl(_state(), {})

        last = next(c for c in result["checks"] if c["name"] == "last_invocations")
        last_map = last["detail"]["last_success_by_persona"]
        assert "dispatcher" in last_map
        assert "technician_lead" in last_map
        assert "Analyst" not in last_map
        assert "RegionalManager" not in last_map
        assert last["detail"].get("legacy_persona_traces_normalized") is True

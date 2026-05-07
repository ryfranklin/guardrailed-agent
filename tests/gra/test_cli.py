"""Tests for the gra CLI (ADR-006).

The CLI is a thin shim: each test mocks ``gagent_client.invoke`` (for
ask) or the CloudWatch Logs Insights helper (for traces) and verifies
the dispatch contract — argument parsing, persona resolution, --json
output shape, trust-gate enforcement.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from gagent_client import FlagPersonaResolver, TraceSummary
from gagent_client.invoke import InvocationResponse
from gra.main import main as gra_main
from mcp_server.state import ServerConfig


def _stub_config(*, log_group: str = "/gagent/invocations") -> ServerConfig:
    return ServerConfig(
        resolver=FlagPersonaResolver({
            "dispatcher": "arn:aws:iam::1:role/d",
            "technician_lead": "arn:aws:iam::1:role/tl",
            "owner": "arn:aws:iam::1:role/o",
        }),
        agent_id="AGENT123",
        agent_alias_id="ALIAS456",
        region="us-east-1",
        glue_database="guardrailed_agent_demo",
        default_persona="owner",
        default_service_region=None,
        log_group=log_group,
        token_budget=10_000,
        foundation_model_id="us.anthropic.claude-sonnet-4-6",
        athena_workgroup_name="gagent-demo",
    )


@pytest.fixture
def trust_env(monkeypatch):
    monkeypatch.setenv("GAGENT_TRUSTED_OPERATOR", "1")


# ---- trust gate ----

class TestTrustGate:
    def test_refuses_to_run_without_trust_env(self, monkeypatch, capsys):
        monkeypatch.delenv("GAGENT_TRUSTED_OPERATOR", raising=False)
        rc = gra_main(["ask", "anything"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "GAGENT_TRUSTED_OPERATOR" in err

    def test_no_subcommand_prints_help(self, capsys, trust_env):
        rc = gra_main([])
        assert rc == 1
        out = capsys.readouterr().out
        assert "usage: gra" in out


# ---- ask ----

class TestAsk:
    def _fake_response(self) -> InvocationResponse:
        return InvocationResponse(
            text="hello, here is the answer.",
            trace_summary=TraceSummary(
                tools_called=["/customers"], guardrail_blocks=0,
            ),
            trace_events=[],
            duration_seconds=1.234,
            session_id="sid-abc",
            role_session_name="gagent-owner-abc123",
        )

    def test_invokes_gagent_client_with_resolved_persona(self, trust_env, capsys):
        cfg = _stub_config()
        with (
            patch("gra.main.load_config", return_value=cfg),
            patch("gra.main.agent_invoke", return_value=self._fake_response()) as mock_invoke,
        ):
            rc = gra_main(["ask", "--persona", "dispatcher", "test prompt"])

        assert rc == 0
        mock_invoke.assert_called_once()
        args, kwargs = mock_invoke.call_args
        assert args[0] == "test prompt"
        passed_persona = args[1]
        assert passed_persona.role == "dispatcher"
        assert kwargs["agent_id"] == "AGENT123"
        # Plain text path: response text on stdout, summary on stderr.
        captured = capsys.readouterr()
        assert "hello, here is the answer." in captured.out
        assert "persona=dispatcher" in captured.err

    def test_json_output_is_valid(self, trust_env, capsys):
        cfg = _stub_config()
        with (
            patch("gra.main.load_config", return_value=cfg),
            patch("gra.main.agent_invoke", return_value=self._fake_response()),
        ):
            rc = gra_main([
                "ask", "--persona", "dispatcher", "--json",
                "what's the most recent service job?",
            ])
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)  # Must parse cleanly.
        assert parsed["persona"] == "dispatcher"
        assert parsed["text"] == "hello, here is the answer."
        assert parsed["tools_called"] == ["/customers"]
        assert parsed["duration_seconds"] == 1.234

    def test_default_persona_used_when_flag_omitted(self, trust_env):
        cfg = _stub_config()
        # Default in stub is "owner".
        with (
            patch("gra.main.load_config", return_value=cfg),
            patch("gra.main.agent_invoke", return_value=self._fake_response()) as mock_invoke,
        ):
            gra_main(["ask", "anything"])
        passed_persona = mock_invoke.call_args.args[1]
        assert passed_persona.role == "owner"

    def test_technician_lead_requires_service_region(self, trust_env, capsys):
        cfg = _stub_config()
        with patch("gra.main.load_config", return_value=cfg):
            rc = gra_main(["ask", "--persona", "technician_lead", "anything"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "service-region" in err.lower() or "service_region" in err.lower()

    def test_technician_lead_with_service_region_flag(self, trust_env):
        cfg = _stub_config()
        with (
            patch("gra.main.load_config", return_value=cfg),
            patch("gra.main.agent_invoke", return_value=self._fake_response()) as mock_invoke,
        ):
            rc = gra_main([
                "ask", "--persona", "technician_lead",
                "--service-region", "tempe-mesa", "anything",
            ])
        assert rc == 0
        passed_persona = mock_invoke.call_args.args[1]
        assert passed_persona.role == "technician_lead"
        assert passed_persona.service_region == "tempe-mesa"

    def test_no_resolver_returns_not_configured(self, trust_env, capsys):
        cfg = _stub_config()
        cfg.resolver = None
        with patch("gra.main.load_config", return_value=cfg):
            rc = gra_main(["ask", "--json", "anything"])
        assert rc == 3
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert "error" in parsed
        assert "no persona role ARNs" in parsed["error"]

    def test_invoke_failure_returns_aws_error(self, trust_env, capsys):
        cfg = _stub_config()
        with (
            patch("gra.main.load_config", return_value=cfg),
            patch("gra.main.agent_invoke", side_effect=RuntimeError("boom")),
        ):
            rc = gra_main(["ask", "--persona", "owner", "--json", "anything"])
        assert rc == 2
        parsed = json.loads(capsys.readouterr().out)
        assert "InvokeAgent error" in parsed["error"]


# ---- personas ----

class TestPersonas:
    def test_lists_three_personas_human_readable(self, trust_env, capsys):
        cfg = _stub_config()
        with patch("gra.main.load_config", return_value=cfg):
            rc = gra_main(["personas"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "owner" in out
        assert "dispatcher" in out
        assert "technician_lead" in out
        # The active default ("owner") should be marked.
        assert "* owner" in out
        assert "Active default: owner" in out

    def test_json_shape(self, trust_env, capsys):
        cfg = _stub_config()
        with patch("gra.main.load_config", return_value=cfg):
            rc = gra_main(["personas", "--json"])
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["active"] == "owner"
        assert sorted(parsed["available"]) == [
            "dispatcher", "owner", "technician_lead",
        ]
        assert "owner" in parsed["role_arns"]

    def test_no_personas_configured(self, trust_env, capsys):
        cfg = _stub_config()
        cfg.resolver = None
        with patch("gra.main.load_config", return_value=cfg):
            rc = gra_main(["personas"])
        assert rc == 3
        captured = capsys.readouterr()
        assert "no personas configured" in captured.out


# ---- traces ----

class TestTraces:
    def _row(
        self,
        *,
        persona: str,
        name: str,
        age_minutes: int = 1,
        tools_called: list[str] | None = None,
    ) -> dict[str, str]:
        ts = (
            datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        ).isoformat()
        return {
            "@timestamp": ts,
            "session_id": f"sid-{name}",
            "persona": persona,
            "role_arn": f"arn:aws:iam::1:role/{persona}",
            "role_session_name": f"gagent-{persona}-{name}",
            "surface": "cli",
            "trace_name": name,
            "duration_seconds": "1.5",
            "tools_called": json.dumps(
                tools_called if tools_called is not None else ["/customers"],
            ),
            "guardrail_blocks": "0",
            "input": f"prompt for {name}",
            "output": f"answer {name}",
            "started_at": ts,
            "finished_at": ts,
            "metadata": json.dumps({"persona": persona}),
        }

    def test_no_log_group_returns_not_configured(self, trust_env, capsys):
        cfg = _stub_config(log_group="")
        with patch("gra.main.load_config", return_value=cfg):
            rc = gra_main(["traces"])
        assert rc == 3
        assert "log_group" in capsys.readouterr().err

    def test_human_readable_output_lists_traces(self, trust_env, capsys):
        cfg = _stub_config()
        rows = [
            self._row(persona="owner", name="a"),
            self._row(persona="dispatcher", name="b"),
        ]
        with (
            patch("gra.main.load_config", return_value=cfg),
            patch(
                "mcp_server.operability_tools._query_logs_insights",
                return_value=rows,
            ),
        ):
            rc = gra_main(["traces", "--limit", "5"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Showing 2 trace" in out
        assert "owner" in out
        assert "dispatcher" in out

    def test_json_shape(self, trust_env, capsys):
        cfg = _stub_config()
        rows = [self._row(persona="owner", name="a")]
        with (
            patch("gra.main.load_config", return_value=cfg),
            patch(
                "mcp_server.operability_tools._query_logs_insights",
                return_value=rows,
            ),
        ):
            rc = gra_main(["traces", "--limit", "5", "--json"])
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["limit"] == 5
        assert parsed["trace_count"] == 1
        assert parsed["traces"][0]["normalized_persona"] == "owner"
        assert parsed["log_group"] == "/gagent/invocations"

    def test_persona_filter_normalizes_legacy_names(self, trust_env, capsys):
        """When --persona dispatcher is passed, the Insights query
        applies a literal ``persona = "dispatcher"`` filter at the log
        layer. Traces written with the pre-ADR-008 ``Analyst`` value will
        not match that literal filter — but if the operator queries
        without --persona the legacy-persona normalization still kicks
        in client-side. Verify the unfiltered case here.
        """
        cfg = _stub_config()
        rows = [
            self._row(persona="Analyst", name="legacy-1"),
            self._row(persona="dispatcher", name="canonical-1"),
            self._row(persona="owner", name="other"),
        ]
        with (
            patch("gra.main.load_config", return_value=cfg),
            patch(
                "mcp_server.operability_tools._query_logs_insights",
                return_value=rows,
            ),
        ):
            rc = gra_main(["traces", "--json"])
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["trace_count"] == 3
        assert parsed["legacy_persona_count"] == 1
        # The legacy "Analyst" row normalizes to "dispatcher".
        legacy = next(t for t in parsed["traces"] if t["trace_name"] == "legacy-1")
        assert legacy["normalized_persona"] == "dispatcher"
        assert legacy["persona_is_legacy"] is True

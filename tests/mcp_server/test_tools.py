"""Tests for mcp_server.tools — pure-logic dispatch, persona resolution.

AWS-touching paths (gagent_client.invoke, boto3 glue) are mocked. The
tools' contract is: every code path returns a JSON-serializable dict;
errors are returned as {"error": ...}, never raised.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gagent_client import FlagPersonaResolver, Persona, TraceSummary
from gagent_client.invoke import InvocationResponse
from mcp_server import (
    ServerConfig,
    ServerState,
    ask_agent_impl,
    describe_schema_impl,
    list_tools_impl,
)


def _state(
    *,
    resolver: FlagPersonaResolver | None = None,
    agent_id: str | None = "AGENT",
    agent_alias_id: str | None = "ALIAS",
    glue_database: str | None = "guardrailed_agent_demo",
    default_persona: str = "owner",
    default_service_region: str | None = None,
) -> ServerState:
    if resolver is None:
        resolver = FlagPersonaResolver({
            "dispatcher": "arn:aws:iam::1:role/d",
            "technician_lead": "arn:aws:iam::1:role/tl",
            "owner": "arn:aws:iam::1:role/o",
        })
    cfg = ServerConfig(
        resolver=resolver,
        agent_id=agent_id,
        agent_alias_id=agent_alias_id,
        region="us-east-1",
        glue_database=glue_database,
        default_persona=default_persona,
        default_service_region=default_service_region,
        log_group="/gagent/invocations",
        token_budget=10_000,
    )
    return ServerState(config=cfg)


# ---- list_tools_impl ----

class TestListTools:
    def test_returns_three_tools(self):
        result = list_tools_impl(_state(), {})
        assert {t["name"] for t in result["tools"]} == {
            "ask_agent", "describe_schema", "list_tools",
        }

    def test_includes_default_persona(self):
        result = list_tools_impl(_state(default_persona="dispatcher"), {})
        assert result["default_persona"] == "dispatcher"

    def test_includes_available_personas(self):
        result = list_tools_impl(_state(), {})
        assert sorted(result["available_personas"]) == [
            "dispatcher", "owner", "technician_lead",
        ]

    def test_reports_agent_configured(self):
        configured = list_tools_impl(_state(), {})
        unconfigured = list_tools_impl(_state(agent_id=None), {})
        assert configured["agent_configured"] is True
        assert unconfigured["agent_configured"] is False

    def test_includes_log_group(self):
        result = list_tools_impl(_state(), {})
        assert result["log_group"] == "/gagent/invocations"


# ---- ask_agent_impl ----

class TestAskAgent:
    def test_requires_question(self):
        result = ask_agent_impl(_state(), {})
        assert "error" in result
        assert "question" in result["error"]

    def test_no_resolver_returns_error(self):
        state = _state(resolver=FlagPersonaResolver({}))
        # FlagPersonaResolver({}) is allowed but has no roles registered.
        # So the resolver IS not-None but resolution fails.
        # To test the resolver=None path explicitly, build state directly:
        cfg = state.config
        cfg = ServerConfig(
            resolver=None, agent_id="A", agent_alias_id="B",
            region="us-east-1", glue_database="x",
            default_persona="owner", default_service_region=None,
            log_group="/gagent/invocations", token_budget=1000,
        )
        state = ServerState(config=cfg)
        result = ask_agent_impl(state, {"question": "hi"})
        assert "error" in result
        assert "no persona role ARNs configured" in result["error"]

    def test_unknown_persona_returns_error(self):
        result = ask_agent_impl(_state(), {"question": "hi", "persona": "hacker"})
        assert "error" in result
        assert "persona must be one of" in result["error"]

    def test_missing_agent_id_returns_error(self):
        result = ask_agent_impl(
            _state(agent_id=None), {"question": "hi"},
        )
        assert "error" in result
        assert "agent_id" in result["error"]

    def test_invokes_gagent_client(self):
        state = _state()
        fake_response = InvocationResponse(
            text="hello world",
            trace_summary=TraceSummary(tools_called=["/customers"], guardrail_blocks=0),
            trace_events=[],
            duration_seconds=1.234,
            session_id="sid",
            role_session_name="gagent-owner-abc123",
        )
        with patch("mcp_server.tools.agent_invoke", return_value=fake_response) as mock_invoke:
            result = ask_agent_impl(
                state, {"question": "what tables are visible?"},
            )

        mock_invoke.assert_called_once()
        kwargs = mock_invoke.call_args.kwargs
        assert kwargs["agent_id"] == "AGENT"
        assert kwargs["agent_alias_id"] == "ALIAS"
        assert kwargs["region"] == "us-east-1"
        assert kwargs["surface"] == "mcp"
        assert kwargs["log_group"] == "/gagent/invocations"
        passed_persona = mock_invoke.call_args.args[1]
        assert passed_persona.role == "owner"

        assert result["text"] == "hello world"
        assert result["persona"] == "owner"
        assert result["tools_called"] == ["/customers"]
        assert result["guardrail_blocks"] == 0
        assert result["duration_seconds"] == 1.234
        # Token estimate updated
        assert result["session_tokens_estimate"] > 0

    def test_technician_lead_uses_default_service_region(self):
        state = _state(default_service_region="tempe-mesa")
        fake = InvocationResponse(
            text="ok", trace_summary=TraceSummary(),
            trace_events=[], duration_seconds=0.1, session_id="s",
            role_session_name="gagent-technician_lead-abc123",
        )
        with patch("mcp_server.tools.agent_invoke", return_value=fake) as mock_invoke:
            ask_agent_impl(state, {"question": "hi", "persona": "technician_lead"})
        passed = mock_invoke.call_args.args[1]
        assert passed.role == "technician_lead"
        assert passed.service_region == "tempe-mesa"

    def test_technician_lead_missing_service_region_returns_error(self):
        state = _state(default_service_region=None)
        result = ask_agent_impl(
            state, {"question": "hi", "persona": "technician_lead"},
        )
        assert "error" in result
        assert "service_region" in result["error"]

    def test_per_call_persona_override(self):
        fake = InvocationResponse(
            text="x", trace_summary=TraceSummary(),
            trace_events=[], duration_seconds=0.1, session_id="s",
            role_session_name="gagent-dispatcher-abc123",
        )
        with patch("mcp_server.tools.agent_invoke", return_value=fake) as mock_invoke:
            ask_agent_impl(
                _state(default_persona="owner"),
                {"question": "hi", "persona": "dispatcher"},
            )
        passed = mock_invoke.call_args.args[1]
        assert passed.role == "dispatcher"

    def test_shape_b_ignores_persona_override(self, caplog):
        """ADR-009 Phase 2.d: in Shape B the SsoPersonaResolver returns the
        SSO-bound persona regardless of the caller's --persona argument,
        and logs a WARN. This is the team-adoption safety net."""
        from gagent_client import SsoPersonaResolver
        from unittest.mock import MagicMock

        sts = MagicMock()
        sts.get_caller_identity.return_value = {
            "Arn": (
                "arn:aws:sts::1:assumed-role/"
                "AWSReservedSSO_DataReader_abc1234567890def/alice@example.com"
            ),
        }
        sso_resolver = SsoPersonaResolver(
            {
                "dispatcher": "arn:aws:iam::1:role/d",
                "technician_lead": "arn:aws:iam::1:role/tl",
                "owner": "arn:aws:iam::1:role/o",
            },
            mapping={
                "version": 1,
                "rules": [
                    {"match": {"permission_set": "DataReader"},
                     "persona": "dispatcher"},
                ],
            },
            sts_client=sts,
        )
        cfg = ServerConfig(
            resolver=sso_resolver,
            agent_id="A", agent_alias_id="B",
            region="us-east-1", glue_database="db",
            default_persona="dispatcher",  # bound to SSO
            default_service_region=None,
            log_group="/gagent/invocations",
            token_budget=10_000,
            shape="B",
        )
        state = ServerState(config=cfg)

        fake = InvocationResponse(
            text="x", trace_summary=TraceSummary(),
            trace_events=[], duration_seconds=0.1, session_id="s",
            role_session_name="gagent-dispatcher-abc123",
        )
        import logging
        with (
            patch("mcp_server.tools.agent_invoke", return_value=fake) as mock_invoke,
            caplog.at_level(logging.WARNING, logger="gagent_client.identity"),
        ):
            ask_agent_impl(
                state,
                # The caller TRIES to be 'owner' — Shape B must ignore.
                {"question": "any", "persona": "owner"},
            )
        passed = mock_invoke.call_args.args[1]
        assert passed.role == "dispatcher", (
            "Shape B must override --persona with the SSO-bound persona"
        )
        assert any(
            "ignoring role='owner' override" in m.message
            for m in caplog.records
        )


# ---- describe_schema_impl ----

class TestDescribeSchema:
    def test_no_glue_database_returns_error(self):
        result = describe_schema_impl(_state(glue_database=None), {})
        assert "error" in result
        assert "glue_database" in result["error"]

    def test_lists_visible_tables_when_no_table_arg(self):
        state = _state()
        glue = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"TableList": [{"Name": "customer"}, {"Name": "service_job"}]},
            {"TableList": [{"Name": "review"}]},
        ]
        glue.get_paginator.return_value = paginator

        with patch("mcp_server.tools.assume_persona", return_value=_fake_creds()), \
             patch("mcp_server.tools.boto3.client", return_value=glue):
            result = describe_schema_impl(state, {})

        assert result["database"] == "guardrailed_agent_demo"
        assert result["persona"] == "owner"
        assert result["tables"] == ["customer", "review", "service_job"]
        assert result["table_count"] == 3

    def test_describes_one_table(self):
        state = _state()
        glue = MagicMock()
        glue.get_table.return_value = {
            "Table": {
                "Name": "customer",
                "StorageDescriptor": {
                    "Columns": [
                        {"Name": "customer_id", "Type": "string"},
                        {"Name": "first_name", "Type": "string"},
                    ],
                },
                "PartitionKeys": [],
            }
        }

        with patch("mcp_server.tools.assume_persona", return_value=_fake_creds()), \
             patch("mcp_server.tools.boto3.client", return_value=glue):
            result = describe_schema_impl(state, {"table": "customer"})

        assert result["table"] == "customer"
        assert result["columns"] == [
            {"name": "customer_id", "type": "string"},
            {"name": "first_name", "type": "string"},
        ]
        assert result["column_count"] == 2

    def test_propagates_persona_choice_to_assume_role(self):
        state = _state()
        glue = MagicMock()
        glue.get_paginator.return_value = MagicMock(
            paginate=MagicMock(return_value=[{"TableList": []}]),
        )

        captured: dict = {}

        def fake_assume(persona: Persona, **kwargs):
            captured["persona"] = persona
            return _fake_creds()

        with patch("mcp_server.tools.assume_persona", side_effect=fake_assume), \
             patch("mcp_server.tools.boto3.client", return_value=glue):
            describe_schema_impl(state, {"persona": "dispatcher"})

        assert captured["persona"].role == "dispatcher"


def _fake_creds() -> dict[str, str]:
    return {
        "AccessKeyId": "AKIAFAKE",
        "SecretAccessKey": "secret",
        "SessionToken": "token",
    }

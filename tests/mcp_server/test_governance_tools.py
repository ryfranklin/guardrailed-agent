"""Mock-based tests for the governance tool impls.

AWS clients (Glue, Lake Formation, CloudTrail) and the CloudWatch Logs
Insights helper are mocked. The tests assert on the structured shape of
each tool's output and on the persona-resolution + error paths.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from gagent_client import FlagPersonaResolver
from mcp_server import (
    ServerConfig,
    ServerState,
    audit_trace_impl,
    eval_query_impl,
    explain_governance_impl,
)


def _state(*, log_group: str = "/gagent/invocations") -> ServerState:
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
    )
    return ServerState(config=cfg)


def _glue_get_table_response(
    columns: list[tuple[str, str]],
    *,
    size_bytes: int | None = None,
    record_count: int | None = None,
) -> dict:
    params: dict[str, str] = {}
    if size_bytes is not None:
        params["totalSize"] = str(size_bytes)
    if record_count is not None:
        params["recordCount"] = str(record_count)
    return {
        "Table": {
            "Name": "x",
            "StorageDescriptor": {
                "Columns": [{"Name": n, "Type": t} for n, t in columns],
            },
            "Parameters": params,
        }
    }


def _lf_tags_response(per_column: dict[str, dict[str, str]]) -> dict:
    return {
        "LFTagsOnColumns": [
            {
                "Name": col,
                "LFTags": [
                    {"TagKey": k, "TagValues": [v]} for k, v in tags.items()
                ],
            }
            for col, tags in per_column.items()
        ],
    }


def _list_permissions_response(grants: list[dict]) -> dict:
    return {"PrincipalResourcePermissions": grants}


def _lf_grants_dispatcher() -> list[dict]:
    return [{
        "Principal": {"DataLakePrincipalIdentifier": "arn:aws:iam::1:role/d"},
        "Resource": {
            "LFTagPolicy": {
                "ResourceType": "TABLE",
                "Expression": [
                    {"TagKey": "pii", "TagValues": ["false"]},
                    {"TagKey": "sensitivity", "TagValues": ["other"]},
                ],
            }
        },
        "Permissions": ["SELECT", "DESCRIBE"],
        "PermissionsWithGrantOption": [],
    }]


def _lf_grants_owner() -> list[dict]:
    return [{
        "Principal": {"DataLakePrincipalIdentifier": "arn:aws:iam::1:role/o"},
        "Resource": {
            "LFTagPolicy": {
                "ResourceType": "TABLE",
                "Expression": [
                    {"TagKey": "pii", "TagValues": ["true", "false"]},
                    {"TagKey": "sensitivity", "TagValues": ["high", "other"]},
                ],
            }
        },
        "Permissions": ["SELECT", "DESCRIBE"],
        "PermissionsWithGrantOption": [],
    }]


# ---- explain_governance_impl ----

class TestExplainGovernance:
    def test_requires_query(self):
        result = explain_governance_impl(_state(), {})
        assert "error" in result

    def test_unknown_table_in_query(self):
        result = explain_governance_impl(
            _state(), {"query": "SELECT * FROM evil_table"},
        )
        assert "error" in result
        assert "no recognized tables" in result["error"]

    def test_dispatcher_redacts_pii_and_high_sensitivity(self):
        glue = MagicMock()
        glue.get_table.return_value = _glue_get_table_response([
            ("customer_id", "string"),
            ("first_name", "string"),
            ("billing_notes", "string"),
        ])
        lf = MagicMock()
        lf.get_resource_lf_tags.return_value = _lf_tags_response({
            "customer_id": {"pii": "false", "sensitivity": "other"},
            "first_name": {"pii": "true", "sensitivity": "other"},
            "billing_notes": {"pii": "true", "sensitivity": "medium"},
        })
        lf.list_permissions.return_value = {"PrincipalResourcePermissions": _lf_grants_dispatcher()}

        with patch(
            "mcp_server.governance_tools._governance_clients",
            return_value=(glue, lf),
        ):
            result = explain_governance_impl(
                _state(), {
                    "query": "SELECT * FROM customer",
                    "persona": "dispatcher",
                },
            )

        assert result["persona"] == "dispatcher"
        assert result["tables_referenced"] == ["customer"]
        redacted_cols = {r["column"] for r in result["redacted_columns"]}
        visible_cols = {r["column"] for r in result["visible_columns"]}
        assert "first_name" in redacted_cols
        assert "billing_notes" in redacted_cols
        assert "customer_id" in visible_cols
        assert result["row_filters"] == []
        assert len(result["grant_evidence"]) >= 1

    def test_owner_sees_everything(self):
        glue = MagicMock()
        glue.get_table.return_value = _glue_get_table_response([
            ("customer_id", "string"),
            ("first_name", "string"),
            ("revenue_generated_usd", "decimal"),
        ])
        lf = MagicMock()
        lf.get_resource_lf_tags.return_value = _lf_tags_response({
            "customer_id": {"pii": "false", "sensitivity": "other"},
            "first_name": {"pii": "true", "sensitivity": "other"},
            "revenue_generated_usd": {"pii": "false", "sensitivity": "high"},
        })
        lf.list_permissions.return_value = {"PrincipalResourcePermissions": _lf_grants_owner()}

        with patch(
            "mcp_server.governance_tools._governance_clients",
            return_value=(glue, lf),
        ):
            result = explain_governance_impl(
                _state(),
                {"query": "SELECT * FROM customer", "persona": "owner"},
            )

        assert result["redacted_columns"] == []
        assert {r["column"] for r in result["visible_columns"]} == {
            "customer_id", "first_name", "revenue_generated_usd",
        }


# ---- eval_query_impl ----

class TestEvalQuery:
    def test_requires_query(self):
        result = eval_query_impl(_state(), {})
        assert "error" in result

    def test_estimates_cost_from_glue_size(self):
        glue = MagicMock()
        # 5 GB scan should project to ~$0.024 at $5/TB.
        glue.get_table.return_value = _glue_get_table_response(
            [("customer_id", "string")],
            size_bytes=5 * 1024 * 1024 * 1024,
            record_count=5_000,
        )
        lf = MagicMock()
        lf.list_permissions.return_value = {"PrincipalResourcePermissions": _lf_grants_dispatcher()}

        with patch(
            "mcp_server.governance_tools._governance_clients",
            return_value=(glue, lf),
        ):
            result = eval_query_impl(
                _state(), {"query": "SELECT * FROM customer"},
            )

        assert result["scanned_bytes_estimate"] == 5 * 1024 * 1024 * 1024
        assert result["cost_estimate_usd"] == pytest.approx(0.0244, abs=0.001)
        assert "GB" in result["scanned_bytes_human"]
        assert len(result["table_stats"]) == 1
        assert result["table_stats"][0]["row_count_estimate"] == 5_000

    def test_warns_when_size_unavailable(self):
        glue = MagicMock()
        glue.get_table.return_value = _glue_get_table_response(
            [("customer_id", "string")],
        )
        lf = MagicMock()
        lf.list_permissions.return_value = {"PrincipalResourcePermissions": _lf_grants_owner()}

        with patch(
            "mcp_server.governance_tools._governance_clients",
            return_value=(glue, lf),
        ):
            result = eval_query_impl(
                _state(), {"query": "SELECT * FROM customer"},
            )

        assert any("no size statistic" in w for w in result["warnings"])
        assert result["scanned_bytes_estimate"] == 0
        assert result["cost_estimate_usd"] == 0.0

    def test_custom_per_tb_rate(self):
        glue = MagicMock()
        glue.get_table.return_value = _glue_get_table_response(
            [("c", "string")],
            size_bytes=1024 ** 4,  # 1 TiB
        )
        lf = MagicMock()
        lf.list_permissions.return_value = {"PrincipalResourcePermissions": _lf_grants_owner()}

        with patch(
            "mcp_server.governance_tools._governance_clients",
            return_value=(glue, lf),
        ):
            result = eval_query_impl(
                _state(),
                {"query": "SELECT * FROM customer", "usd_per_tb": 10.0},
            )

        assert result["cost_estimate_usd"] == pytest.approx(10.0)


# ---- audit_trace_impl ----

class TestAuditTrace:
    def _trace_row(
        self,
        *,
        session_id: str,
        persona: str = "owner",
        role_session_name: str = "gagent-owner-abc123",
        ts: datetime | None = None,
    ) -> dict[str, str]:
        ts = ts or (datetime.now(timezone.utc) - timedelta(minutes=5))
        iso = ts.isoformat()
        return {
            "@timestamp": iso,
            "session_id": session_id,
            "persona": persona,
            "role_arn": f"arn:aws:iam::1:role/{persona}",
            "role_session_name": role_session_name,
            "surface": "mcp",
            "trace_name": "case-x",
            "duration_seconds": "1.5",
            "tools_called": json.dumps(["/customers"]),
            "guardrail_blocks": "0",
            "input": "show me X",
            "output": "here is X",
            "started_at": iso,
            "finished_at": iso,
            "metadata": json.dumps({"persona": persona}),
        }

    def test_requires_session_id(self):
        result = audit_trace_impl(_state(), {})
        assert "error" in result
        assert "session_id" in result["error"]

    def test_no_log_group_returns_error(self):
        result = audit_trace_impl(
            _state(log_group=""), {"session_id": "abc"},
        )
        assert "log_group" in result["error"]

    def test_returns_events_grouped_by_name(self, monkeypatch):
        ts = datetime.now(timezone.utc) - timedelta(minutes=5)
        rows = [
            self._trace_row(session_id="abc-123", ts=ts),
        ]
        captured: dict[str, str] = {}

        def fake_insights(**kwargs):
            captured["query"] = kwargs.get("query_string", "")
            return list(rows)

        monkeypatch.setattr(
            "mcp_server.governance_tools._query_logs_insights", fake_insights,
        )

        cloudtrail = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [{
            "Events": [
                {
                    "EventId": "e1",
                    "EventName": "AssumeRole",
                    "EventTime": ts,
                    "Username": "ms3dm-admin",
                    "EventSource": "sts.amazonaws.com",
                    "Resources": [],
                },
                {
                    "EventId": "e2",
                    "EventName": "GetDataAccess",
                    "EventTime": ts + timedelta(seconds=10),
                    "Username": "gagent-owner-abc123",
                    "EventSource": "lakeformation.amazonaws.com",
                    "Resources": [],
                },
            ],
        }]
        cloudtrail.get_paginator.return_value = paginator

        with patch("mcp_server.governance_tools.boto3.client", return_value=cloudtrail):
            result = audit_trace_impl(
                _state(), {"session_id": "abc-123"},
            )

        assert result["session_id"] == "abc-123"
        assert result["persona"] == "owner"
        assert result["role_session_name"] == "gagent-owner-abc123"
        assert result["role_arn"] == "arn:aws:iam::1:role/owner"
        assert result["log_group"] == "/gagent/invocations"
        assert result["cloudtrail_event_count"] == 2
        assert "AssumeRole" in result["events_by_name"]
        assert "GetDataAccess" in result["events_by_name"]
        # The Insights query must filter on session_id.
        assert 'session_id = "abc-123"' in captured["query"]

    def test_legacy_trace_id_alias_accepted(self, monkeypatch):
        """audit_trace must continue to accept the legacy trace_id arg."""
        rows = [self._trace_row(session_id="legacy-id")]
        monkeypatch.setattr(
            "mcp_server.governance_tools._query_logs_insights",
            lambda **kwargs: list(rows),
        )
        cloudtrail = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [{"Events": []}]
        cloudtrail.get_paginator.return_value = paginator

        with patch("mcp_server.governance_tools.boto3.client", return_value=cloudtrail):
            result = audit_trace_impl(_state(), {"trace_id": "legacy-id"})

        assert result["session_id"] == "legacy-id"

    def test_missing_session_returns_error(self, monkeypatch):
        monkeypatch.setattr(
            "mcp_server.governance_tools._query_logs_insights",
            lambda **kwargs: [],
        )
        result = audit_trace_impl(_state(), {"session_id": "missing"})
        assert "not found" in result["error"]

    def test_logs_insights_failure_returns_error(self, monkeypatch):
        def boom(**kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(
            "mcp_server.governance_tools._query_logs_insights", boom,
        )
        result = audit_trace_impl(_state(), {"session_id": "abc"})
        assert "Logs Insights query failed" in result["error"]

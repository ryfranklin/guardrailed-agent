"""Unit-style tests for the query_ambassadors handler.

These exercise pure logic only — request parsing, query construction,
response shaping. Integration coverage that hits Athena and Lake Formation
lives in the eval harness against the Demo account, per CLAUDE.md.
"""

from __future__ import annotations

import os

os.environ.setdefault("GLUE_DATABASE", "guardrailed_agent_test")
os.environ.setdefault("ATHENA_WORKGROUP", "gagent-test")
os.environ.setdefault("ENV", "test")

import pytest

from lambdas.query_ambassadors import handler  # noqa: E402


def _event(role: str, *, body: dict | None = None, region: str | None = None) -> dict:
    session_attrs = {"role": role}
    if region:
        session_attrs["region"] = region
    return {
        "messageVersion": "1.0",
        "actionGroup": "query_ambassadors",
        "apiPath": "/query",
        "httpMethod": "POST",
        "sessionId": "session-test",
        "sessionAttributes": session_attrs,
        "promptSessionAttributes": {},
        "requestBody": {
            "content": {
                "application/json": {
                    "properties": [
                        {"name": k, "type": "string", "value": str(v)}
                        for k, v in (body or {}).items()
                    ]
                }
            }
        },
    }


class TestRequestParsing:
    def test_defaults_to_ambassador_table(self):
        body = handler._parse_request_body(_event("analyst", body={"question_intent": "test"}))
        assert body["table"] == "ambassador"
        assert body["limit"] == 50

    def test_rejects_unknown_table(self):
        with pytest.raises(handler.BadRequest, match="Unknown table"):
            handler._parse_request_body(_event("analyst", body={"table": "evil_table"}))

    def test_clamps_limit(self):
        body = handler._parse_request_body(_event("analyst", body={"limit": "9999"}))
        assert body["limit"] == 200

    def test_clamps_negative_limit(self):
        body = handler._parse_request_body(_event("analyst", body={"limit": "-5"}))
        assert body["limit"] == 1


class TestQueryBuilding:
    def test_no_filters(self):
        persona = handler.PersonaContext(role="analyst", region=None, role_arn="arn:aws:iam::1:role/x")
        sql, params = handler._build_query(persona, {"table": "ambassador", "filters": {}, "limit": 50})
        assert sql == "SELECT * FROM ambassador LIMIT 50"
        assert params == []

    def test_filters_use_parameterized_placeholders(self):
        persona = handler.PersonaContext(role="admin", region=None, role_arn="arn:aws:iam::1:role/x")
        sql, params = handler._build_query(
            persona,
            {"table": "ambassador", "filters": {"status": "active"}, "limit": 10},
        )
        assert sql == "SELECT * FROM ambassador WHERE status = ? LIMIT 10"
        assert params == ["active"]

    def test_regional_manager_injects_region_predicate(self):
        persona = handler.PersonaContext(
            role="regional_manager", region="CA", role_arn="arn:aws:iam::1:role/x"
        )
        sql, params = handler._build_query(
            persona, {"table": "ambassador", "filters": {}, "limit": 50}
        )
        assert "region = ?" in sql
        assert params == ["CA"]

    def test_rejects_unknown_filter_column(self):
        persona = handler.PersonaContext(role="admin", region=None, role_arn="arn:aws:iam::1:role/x")
        with pytest.raises(handler.BadRequest, match="not in table"):
            handler._build_query(
                persona,
                {"table": "ambassador", "filters": {"not_a_real_column": "x"}, "limit": 50},
            )

    def test_rejects_sql_injection_in_filter_column(self):
        persona = handler.PersonaContext(role="admin", region=None, role_arn="arn:aws:iam::1:role/x")
        with pytest.raises(handler.BadRequest, match="Invalid filter column"):
            handler._build_query(
                persona,
                {"table": "ambassador", "filters": {"status; DROP TABLE x": "x"}, "limit": 50},
            )


class TestResponseShaping:
    def test_redacts_missing_pii_columns_for_analyst(self):
        persona = handler.PersonaContext(role="analyst", region=None, role_arn="arn:aws:iam::1:role/x")
        rows = [{"ambassador_id": "abc", "status": "active", "rank": "gold", "region": "CA"}]
        payload = handler._build_response_payload(
            persona, {"table": "ambassador", "question_intent": "test"}, rows
        )
        assert payload["row_count"] == 1
        row = payload["rows"][0]
        assert row["email"] == "REDACTED"
        assert row["ssn_last4"] == "REDACTED"
        assert row["status"] == "active"

    def test_admin_sees_unredacted_pii(self):
        persona = handler.PersonaContext(role="admin", region=None, role_arn="arn:aws:iam::1:role/x")
        rows = [
            {
                "ambassador_id": "abc",
                "email": "real@example.com",
                "first_name": "Alice",
                "ssn_last4": "1234",
            }
        ]
        payload = handler._build_response_payload(
            persona, {"table": "ambassador", "question_intent": "test"}, rows
        )
        row = payload["rows"][0]
        assert row["email"] == "real@example.com"
        assert row["ssn_last4"] == "1234"


class TestPersonaResolution:
    def test_rejects_unknown_role(self):
        with pytest.raises(handler.BadRequest, match="must be one of"):
            handler._resolve_persona(_event("hacker"))

    def test_regional_manager_requires_region(self):
        with pytest.raises(handler.BadRequest, match="requires a 'region'"):
            handler._resolve_persona(_event("regional_manager"))

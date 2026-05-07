"""Pure-logic tests for mcp_server.governance helpers.

Covers SQL parsing, tag-policy expression matching, column-visibility
resolution, cost projection, and human-bytes formatting. No AWS.
"""

from __future__ import annotations

import pytest

from mcp_server.governance import (
    ATHENA_USD_PER_TB,
    BYTES_PER_TB,
    DEFAULT_DATABASE_TAGS,
    GrantExpression,
    TagPolicyGrant,
    compute_column_visibility,
    extract_table_names,
    format_tag_expression,
    grant_to_dict,
    human_bytes,
    project_athena_cost,
)


KNOWN = {
    "customer", "service_job", "equipment", "review",
    "customer_signal_daily", "parts_inventory", "dispatch_event",
    "truck_roll", "warranty_claim", "equipment_telemetry_daily",
    "technician_utilization_daily", "technician",
}


# ---- SQL parser ----

class TestExtractTableNames:
    def test_simple_select(self):
        assert extract_table_names("SELECT * FROM customer", KNOWN) == ["customer"]

    def test_case_insensitive_keywords(self):
        assert extract_table_names("select * from CUSTOMER", KNOWN) == ["customer"]

    def test_three_way_join(self):
        sql = (
            "SELECT c.* FROM customer c "
            "JOIN equipment e ON e.customer_id = c.customer_id "
            "JOIN service_job j ON j.equipment_id = e.equipment_id"
        )
        assert extract_table_names(sql, KNOWN) == [
            "customer", "equipment", "service_job",
        ]

    def test_dedupes(self):
        sql = "SELECT * FROM customer JOIN customer c2 ON true"
        assert extract_table_names(sql, KNOWN) == ["customer"]

    def test_unknown_tables_filtered_out(self):
        sql = "SELECT * FROM evil_table JOIN customer c ON true"
        assert extract_table_names(sql, KNOWN) == ["customer"]

    def test_empty_query(self):
        assert extract_table_names("", KNOWN) == []

    def test_no_matches(self):
        assert extract_table_names("SELECT 1", KNOWN) == []


# ---- TagPolicyGrant matching ----

class TestGrantMatching:
    def test_dispatcher_grant_matches_clean_column(self):
        grant = TagPolicyGrant(
            permissions=["SELECT"],
            permissions_with_grant_option=[],
            resource_type="TABLE",
            expressions=[
                GrantExpression(key="pii", values=["false"]),
                GrantExpression(key="sensitivity", values=["other"]),
            ],
        )
        assert grant.matches_column_tags({"pii": "false", "sensitivity": "other"})

    def test_dispatcher_grant_rejects_pii(self):
        grant = TagPolicyGrant(
            permissions=["SELECT"], permissions_with_grant_option=[],
            resource_type="TABLE",
            expressions=[
                GrantExpression(key="pii", values=["false"]),
                GrantExpression(key="sensitivity", values=["other"]),
            ],
        )
        assert not grant.matches_column_tags({"pii": "true", "sensitivity": "other"})

    def test_dispatcher_grant_rejects_high_sensitivity(self):
        grant = TagPolicyGrant(
            permissions=["SELECT"], permissions_with_grant_option=[],
            resource_type="TABLE",
            expressions=[
                GrantExpression(key="pii", values=["false"]),
                GrantExpression(key="sensitivity", values=["other"]),
            ],
        )
        assert not grant.matches_column_tags({"pii": "false", "sensitivity": "high"})

    def test_owner_grant_matches_everything(self):
        grant = TagPolicyGrant(
            permissions=["SELECT"], permissions_with_grant_option=[],
            resource_type="TABLE",
            expressions=[
                GrantExpression(key="pii", values=["true", "false"]),
                GrantExpression(key="sensitivity", values=["high", "other"]),
            ],
        )
        assert grant.matches_column_tags({"pii": "true", "sensitivity": "high"})
        assert grant.matches_column_tags({"pii": "false", "sensitivity": "other"})

    def test_grant_rejects_when_column_missing_tag_key(self):
        grant = TagPolicyGrant(
            permissions=["SELECT"], permissions_with_grant_option=[],
            resource_type="TABLE",
            expressions=[
                GrantExpression(key="pii", values=["false"]),
            ],
        )
        # Missing tag key — fail closed
        assert not grant.matches_column_tags({"sensitivity": "other"})


# ---- compute_column_visibility ----

class TestComputeColumnVisibility:
    def _dispatcher_grants(self) -> list[TagPolicyGrant]:
        return [TagPolicyGrant(
            permissions=["SELECT", "DESCRIBE"],
            permissions_with_grant_option=[],
            resource_type="TABLE",
            expressions=[
                GrantExpression(key="pii", values=["false"]),
                GrantExpression(key="sensitivity", values=["other"]),
            ],
        )]

    def _owner_grants(self) -> list[TagPolicyGrant]:
        return [TagPolicyGrant(
            permissions=["SELECT", "DESCRIBE"],
            permissions_with_grant_option=[],
            resource_type="TABLE",
            expressions=[
                GrantExpression(key="pii", values=["true", "false"]),
                GrantExpression(key="sensitivity", values=["high", "other"]),
            ],
        )]

    def test_dispatcher_sees_clean_column(self):
        v = compute_column_visibility(
            "customer_id",
            {"pii": "false", "sensitivity": "other"},
            self._dispatcher_grants(),
        )
        assert v.visible is True
        assert "matched" in v.reason

    def test_dispatcher_redacted_for_pii(self):
        v = compute_column_visibility(
            "first_name",
            {"pii": "true", "sensitivity": "low"},
            self._dispatcher_grants(),
        )
        assert v.visible is False
        # Reason references the failing tag
        assert "pii=" in v.reason

    def test_dispatcher_redacted_for_high_sensitivity(self):
        v = compute_column_visibility(
            "unit_cost_usd",
            {"pii": "false", "sensitivity": "high"},
            self._dispatcher_grants(),
        )
        assert v.visible is False
        assert "sensitivity=high" in v.reason

    def test_owner_sees_high_sensitivity(self):
        v = compute_column_visibility(
            "unit_cost_usd",
            {"pii": "false", "sensitivity": "high"},
            self._owner_grants(),
        )
        assert v.visible is True

    def test_no_grants_redacts_everything(self):
        v = compute_column_visibility(
            "anything",
            {"pii": "false", "sensitivity": "other"},
            [],
        )
        assert v.visible is False
        assert "no LF_TAG_POLICY grants" in v.reason

    def test_database_grants_ignored_for_table_scope(self):
        # A DATABASE-scope grant must not satisfy a TABLE-scope probe.
        db_grant = TagPolicyGrant(
            permissions=["DESCRIBE"], permissions_with_grant_option=[],
            resource_type="DATABASE",
            expressions=[GrantExpression(key="pii", values=["true", "false"])],
        )
        v = compute_column_visibility(
            "first_name", {"pii": "true", "sensitivity": "other"}, [db_grant],
        )
        assert v.visible is False


# ---- format_tag_expression ----

class TestFormatTagExpression:
    def test_renders_keys_and_values(self):
        grant = TagPolicyGrant(
            permissions=[], permissions_with_grant_option=[],
            resource_type="TABLE",
            expressions=[
                GrantExpression(key="pii", values=["false"]),
                GrantExpression(key="sensitivity", values=["other"]),
            ],
        )
        rendered = format_tag_expression(grant)
        assert "pii=" in rendered
        assert "sensitivity=" in rendered
        assert " AND " in rendered

    def test_grant_to_dict_round_trip(self):
        grant = TagPolicyGrant(
            permissions=["SELECT"], permissions_with_grant_option=[],
            resource_type="TABLE",
            expressions=[GrantExpression(key="pii", values=["false"])],
        )
        d = grant_to_dict(grant)
        assert d["permissions"] == ["SELECT"]
        assert d["resource_type"] == "TABLE"
        assert d["tag_expression"] == [{"key": "pii", "values": ["false"]}]
        assert "pii" in d["tag_expression_str"]


# ---- cost projection ----

class TestProjectAthenaCost:
    def test_zero_bytes(self):
        assert project_athena_cost(0) == 0.0

    def test_negative_bytes(self):
        assert project_athena_cost(-100) == 0.0

    def test_one_tb_at_default_rate(self):
        assert project_athena_cost(BYTES_PER_TB) == pytest.approx(ATHENA_USD_PER_TB)

    def test_one_gb(self):
        gb = BYTES_PER_TB // 1024
        cost = project_athena_cost(gb)
        # ~$0.00488 per GB at $5/TB
        assert cost == pytest.approx(5.0 / 1024, rel=1e-3)

    def test_custom_per_tb_rate(self):
        cost = project_athena_cost(BYTES_PER_TB, usd_per_tb=10.0)
        assert cost == pytest.approx(10.0)


class TestHumanBytes:
    @pytest.mark.parametrize("n,expected_unit", [
        (0, "B"),
        (512, "B"),
        (1024, "KB"),
        (1024 * 1024, "MB"),
        (1024 * 1024 * 1024, "GB"),
        (BYTES_PER_TB, "TB"),
    ])
    def test_unit_picks(self, n, expected_unit):
        assert expected_unit in human_bytes(n)

    def test_none(self):
        assert human_bytes(None) == "unknown"


# ---- DEFAULT_DATABASE_TAGS ----

def test_default_database_tags():
    assert DEFAULT_DATABASE_TAGS == {"pii": "false", "sensitivity": "other"}

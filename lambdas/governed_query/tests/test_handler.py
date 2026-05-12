"""Unit-style tests for the query template handler (ADR-008 §4 step 6).

Pure-logic only — request parsing, dispatch, SQL building, parameter
guards. AWS-touching paths (assume_role, Athena execution) are not
exercised here. Live integration coverage lives in
tests/integration/test_templates_aws.py and the eval harness against
the Demo account.
"""

from __future__ import annotations

import os

os.environ.setdefault("GLUE_DATABASE", "guardrailed_agent_test")
os.environ.setdefault("ATHENA_WORKGROUP", "gagent-test")
os.environ.setdefault("ENV", "test")

import pytest

from lambdas.governed_query import handler  # noqa: E402


def _event(
    role: str,
    api_path: str,
    *,
    body: dict | None = None,
    service_region: str | None = None,
) -> dict:
    session_attrs = {"role": role}
    if service_region:
        session_attrs["service_region"] = service_region
    properties: list[dict] = []
    for k, v in (body or {}).items():
        if isinstance(v, dict):
            properties.append({"name": k, "type": "object", "value": v})
        elif isinstance(v, bool):
            properties.append({"name": k, "type": "boolean", "value": str(v).lower()})
        elif isinstance(v, int):
            properties.append({"name": k, "type": "integer", "value": str(v)})
        else:
            properties.append({"name": k, "type": "string", "value": str(v)})
    return {
        "messageVersion": "1.0",
        "actionGroup": "query_governed",
        "apiPath": api_path,
        "httpMethod": "POST",
        "sessionId": "session-test",
        "sessionAttributes": session_attrs,
        "promptSessionAttributes": {},
        "requestBody": {
            "content": {"application/json": {"properties": properties}},
        },
    }


def _persona(role: str = "owner", service_region: str | None = None) -> handler.PersonaContext:
    return handler.PersonaContext(
        role=role, service_region=service_region,
        role_arn=f"arn:aws:iam::1:role/gagent-{role}-test",
    )


# ---------- registry shape ----------

class TestTemplateRegistry:
    def test_six_paths_registered(self):
        assert set(handler.TEMPLATES.keys()) == {
            "/customers", "/jobs", "/signals",
            "/equipment_telemetry", "/technician_utilization", "/truck_rolls",
        }

    @pytest.mark.parametrize("api_path,expected_table", [
        ("/customers", "customer"),
        ("/jobs", "service_job"),
        ("/signals", "customer_signal_daily"),
        ("/equipment_telemetry", "equipment_telemetry_daily"),
        ("/technician_utilization", "technician_utilization_daily"),
        ("/truck_rolls", "truck_roll"),
    ])
    def test_template_table_mapping(self, api_path, expected_table):
        assert handler.TEMPLATES[api_path].table == expected_table

    def test_scd2_flag_only_on_customer(self):
        assert handler.TEMPLATES["/customers"].scd2 is True
        for path, t in handler.TEMPLATES.items():
            if path != "/customers":
                assert t.scd2 is False, f"{path} should not be SCD2"

    def test_soft_delete_flag_on_jobs_and_truck_rolls(self):
        soft_delete = {"/jobs", "/truck_rolls"}
        for path, t in handler.TEMPLATES.items():
            assert t.soft_delete is (path in soft_delete), (
                f"{path} soft_delete flag mismatch"
            )


# ---------- dispatch ----------

class TestDispatch:
    def test_unknown_api_path_rejected(self):
        with pytest.raises(handler.BadRequest, match="unknown apiPath"):
            handler._resolve_template({"apiPath": "/no_such_thing"})

    def test_persona_role_required(self):
        with pytest.raises(handler.BadRequest, match="must be one of"):
            handler._resolve_persona(_event("hacker", "/customers"))

    def test_technician_lead_requires_service_region(self):
        with pytest.raises(handler.BadRequest, match="service_region"):
            handler._resolve_persona(_event("technician_lead", "/customers"))

    def test_technician_lead_with_service_region_ok(self):
        persona = handler._resolve_persona(
            _event("technician_lead", "/customers", service_region="tempe-mesa"),
        )
        assert persona.role == "technician_lead"
        assert persona.service_region == "tempe-mesa"


# ---------- defaults: SCD2 (customers) ----------

class TestCustomersTemplate:
    template = handler.TEMPLATES["/customers"]

    def test_default_predicate_is_is_current_true(self):
        parsed = {"as_of_date": None, "include_deleted": False,
                  "eq_filters": {}, "range_filters": {}, "limit": 15}
        sql, params = handler.build_query(self.template, parsed)
        assert "is_current = TRUE" in sql
        assert params == []

    def test_as_of_date_swaps_to_point_in_time(self):
        parsed = {"as_of_date": "2026-01-01", "include_deleted": False,
                  "eq_filters": {}, "range_filters": {}, "limit": 15}
        sql, params = handler.build_query(self.template, parsed)
        assert "is_current" not in sql
        assert "effective_from <= timestamp '2026-01-01'" in sql
        assert "effective_to IS NULL OR effective_to > timestamp '2026-01-01'" in sql
        # The as_of_date is regex-safe and inlined; no positional params.
        assert params == []

    def test_eq_filter_appended(self):
        parsed = {"as_of_date": None, "include_deleted": False,
                  "eq_filters": {"service_region": "tempe-mesa"},
                  "range_filters": {}, "limit": 25}
        sql, params = handler.build_query(self.template, parsed)
        assert "service_region = ?" in sql
        assert params == ["tempe-mesa"]
        assert "LIMIT 25" in sql

    def test_invalid_filter_rejected(self):
        with pytest.raises(handler.BadRequest, match="not allowed"):
            handler._parse_request_body(
                _event("dispatcher", "/customers",
                       body={"filters": {"haxor_column": "x"}}),
                self.template, _persona("dispatcher"),
            )

    def test_invalid_as_of_date_rejected(self):
        with pytest.raises(handler.BadRequest, match="YYYY-MM-DD"):
            handler._parse_request_body(
                _event("dispatcher", "/customers", body={"as_of_date": "yesterday"}),
                self.template, _persona("dispatcher"),
            )

    def test_include_deleted_rejected_for_non_soft_delete(self):
        with pytest.raises(handler.BadRequest, match="not soft-delete"):
            handler._parse_request_body(
                _event("owner", "/customers", body={"include_deleted": True}),
                self.template, _persona("owner"),
            )


# ---------- defaults: soft-delete (jobs, truck_rolls) ----------

class TestSoftDeleteTemplates:
    @pytest.mark.parametrize("api_path", ["/jobs", "/truck_rolls"])
    def test_default_predicate_is_deleted_at_null(self, api_path):
        template = handler.TEMPLATES[api_path]
        parsed = {"as_of_date": None, "include_deleted": False,
                  "eq_filters": {}, "range_filters": {}, "limit": 15}
        sql, _ = handler.build_query(template, parsed)
        assert "deleted_at IS NULL" in sql

    @pytest.mark.parametrize("api_path", ["/jobs", "/truck_rolls"])
    def test_owner_can_include_deleted(self, api_path):
        template = handler.TEMPLATES[api_path]
        parsed = handler._parse_request_body(
            _event("owner", api_path, body={"include_deleted": True}),
            template, _persona("owner"),
        )
        assert parsed["include_deleted"] is True
        sql, _ = handler.build_query(template, parsed)
        assert "deleted_at IS NULL" not in sql

    @pytest.mark.parametrize("api_path", ["/jobs", "/truck_rolls"])
    @pytest.mark.parametrize("role", ["dispatcher", "technician_lead"])
    def test_non_owner_blocked_from_include_deleted(self, api_path, role):
        template = handler.TEMPLATES[api_path]
        with pytest.raises(handler.BadRequest, match="owner persona"):
            handler._parse_request_body(
                _event(role, api_path, body={"include_deleted": True},
                       service_region="tempe-mesa" if role == "technician_lead" else None),
                template,
                _persona(role,
                         service_region="tempe-mesa" if role == "technician_lead" else None),
            )

    def test_jobs_range_filter(self):
        template = handler.TEMPLATES["/jobs"]
        parsed = handler._parse_request_body(
            _event("owner", "/jobs",
                   body={"filters": {"scheduled_date_from": "2026-01-01",
                                     "scheduled_date_to": "2026-03-31"}}),
            template, _persona("owner"),
        )
        sql, params = handler.build_query(template, parsed)
        assert "scheduled_date >= ?" in sql
        assert "scheduled_date <= ?" in sql
        assert params == ["2026-01-01", "2026-03-31"]

    def test_jobs_eq_filter(self):
        template = handler.TEMPLATES["/jobs"]
        parsed = handler._parse_request_body(
            _event("dispatcher", "/jobs",
                   body={"filters": {"status": "completed"}}),
            template, _persona("dispatcher"),
        )
        sql, params = handler.build_query(template, parsed)
        assert "status = ?" in sql
        assert "completed" in params


# ---------- defaults: neither (signals, telemetry, utilization) ----------

class TestPlainFactTemplates:
    @pytest.mark.parametrize("api_path", [
        "/signals", "/equipment_telemetry", "/technician_utilization",
    ])
    def test_no_default_predicate(self, api_path):
        template = handler.TEMPLATES[api_path]
        parsed = {"as_of_date": None, "include_deleted": False,
                  "eq_filters": {}, "range_filters": {}, "limit": 15}
        sql, _ = handler.build_query(template, parsed)
        assert "is_current" not in sql
        assert "deleted_at" not in sql

    @pytest.mark.parametrize("api_path", [
        "/signals", "/equipment_telemetry", "/technician_utilization",
    ])
    def test_as_of_date_rejected(self, api_path):
        template = handler.TEMPLATES[api_path]
        with pytest.raises(handler.BadRequest, match="not SCD2"):
            handler._parse_request_body(
                _event("owner", api_path, body={"as_of_date": "2026-01-01"}),
                template, _persona("owner"),
            )

    def test_telemetry_min_predicted_failure(self):
        template = handler.TEMPLATES["/equipment_telemetry"]
        parsed = handler._parse_request_body(
            _event("owner", "/equipment_telemetry",
                   body={"filters": {"min_predicted_failure_30d": "0.5"}}),
            template, _persona("owner"),
        )
        sql, params = handler.build_query(template, parsed)
        assert "predicted_failure_30d >= ?" in sql
        assert params == ["0.5"]


# ---------- limit handling ----------

class TestLimit:
    def test_default_limit(self):
        template = handler.TEMPLATES["/customers"]
        parsed = handler._parse_request_body(
            _event("dispatcher", "/customers"),
            template, _persona("dispatcher"),
        )
        assert parsed["limit"] == 15

    def test_clamp_max_200(self):
        template = handler.TEMPLATES["/customers"]
        parsed = handler._parse_request_body(
            _event("dispatcher", "/customers", body={"limit": 9999}),
            template, _persona("dispatcher"),
        )
        assert parsed["limit"] == 200

    def test_clamp_min_1(self):
        template = handler.TEMPLATES["/customers"]
        parsed = handler._parse_request_body(
            _event("dispatcher", "/customers", body={"limit": -5}),
            template, _persona("dispatcher"),
        )
        assert parsed["limit"] == 1

    def test_non_integer_limit_rejected(self):
        template = handler.TEMPLATES["/customers"]
        with pytest.raises(handler.BadRequest, match="integer"):
            handler._parse_request_body(
                _event("dispatcher", "/customers", body={"limit": "abc"}),
                template, _persona("dispatcher"),
            )


# ---------- columns surfaced in SELECT ----------

class TestColumns:
    @pytest.mark.parametrize("api_path,expected_subset", [
        ("/customers", {"first_name", "service_region", "billing_notes"}),
        ("/jobs", {"job_id", "status", "deleted_at"}),
        ("/signals", {"signal_date", "engagement_score", "next_best_action"}),
        ("/equipment_telemetry", {"predicted_failure_30d", "fault_code_count"}),
        ("/technician_utilization", {"revenue_generated_usd", "billable_hours"}),
        ("/truck_rolls", {"miles_driven", "parts_pulled", "deleted_at"}),
    ])
    def test_select_includes_expected_columns(self, api_path, expected_subset):
        template = handler.TEMPLATES[api_path]
        cols = set(template.columns)
        assert expected_subset.issubset(cols), (
            f"{api_path} missing columns: {expected_subset - cols}"
        )

    def test_customers_does_not_leak_scd2_internals(self):
        cols = set(handler.TEMPLATES["/customers"].columns)
        assert "effective_from" not in cols
        assert "effective_to" not in cols
        assert "is_current" not in cols


# ---------- safety / injection ----------

class TestSafety:
    def test_eq_filter_value_does_not_inject(self):
        template = handler.TEMPLATES["/customers"]
        parsed = handler._parse_request_body(
            _event("owner", "/customers",
                   body={"filters": {"customer_id": "abc'; DROP TABLE customer; --"}}),
            template, _persona("owner"),
        )
        sql, params = handler.build_query(template, parsed)
        assert "DROP TABLE" not in sql
        assert params == ["abc'; DROP TABLE customer; --"]

    def test_unknown_filter_name_rejected(self):
        template = handler.TEMPLATES["/jobs"]
        with pytest.raises(handler.BadRequest, match="not allowed"):
            handler._parse_request_body(
                _event("owner", "/jobs",
                       body={"filters": {"; DROP TABLE service_job; --": "x"}}),
                template, _persona("owner"),
            )


# ---------- final assembly: SELECT shape ----------

class TestSqlAssembly:
    def test_customers_default_sql(self):
        template = handler.TEMPLATES["/customers"]
        parsed = handler._parse_request_body(
            _event("dispatcher", "/customers"),
            template, _persona("dispatcher"),
        )
        sql, _ = handler.build_query(template, parsed)
        assert sql.startswith("SELECT customer_id, customer_type")
        assert "FROM customer" in sql
        assert "WHERE is_current = TRUE" in sql
        assert sql.endswith("LIMIT 15")

    def test_truck_rolls_default_sql(self):
        template = handler.TEMPLATES["/truck_rolls"]
        parsed = handler._parse_request_body(
            _event("dispatcher", "/truck_rolls"),
            template, _persona("dispatcher"),
        )
        sql, _ = handler.build_query(template, parsed)
        assert "FROM truck_roll" in sql
        assert "WHERE deleted_at IS NULL" in sql


# ---------- column-deny retry (SELECT *) ----------

class TestColumnDenyRetry:
    """When the persona's tag policy denies one of the explicit columns,
    Lake Formation rejects the whole query. The Lambda retries once with
    SELECT * so LF transparently filters the result to the visible
    column subset — honoring the persona description's "PII redacted"
    promise.
    """

    def _fake_athena_chain(self):
        """Build a MagicMock that fails the first start+wait then
        succeeds on the second."""
        from unittest.mock import MagicMock
        from botocore.exceptions import ClientError as Cli

        athena = MagicMock()
        # Two distinct execution IDs; status differs by call number.
        athena.start_query_execution.side_effect = [
            {"QueryExecutionId": "deny-1"},
            {"QueryExecutionId": "ok-2"},
        ]
        def _get_exec(QueryExecutionId, **_):
            if QueryExecutionId == "deny-1":
                return {"QueryExecution": {"Status": {
                    "State": "FAILED",
                    "StateChangeReason": "Insufficient permissions: access denied on column billing_notes",
                }}}
            return {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}
        athena.get_query_execution.side_effect = _get_exec
        # Paginator for results returns 1 header row + 1 data row.
        paginator = MagicMock()
        paginator.paginate.return_value = [{
            "ResultSet": {
                "ResultSetMetadata": {"ColumnInfo": [
                    {"Name": "job_id"}, {"Name": "scheduled_date"},
                ]},
                "Rows": [
                    {"Data": [{"VarCharValue": "job_id"}, {"VarCharValue": "scheduled_date"}]},
                    {"Data": [{"VarCharValue": "j-1"}, {"VarCharValue": "2026-05-12"}]},
                ],
            }
        }]
        athena.get_paginator.return_value = paginator
        return athena, Cli  # noqa: RUF100

    def test_execute_query_select_star_vs_explicit(self):
        """build_query semantics: preview=True yields SELECT *, otherwise
        explicit column list."""
        template = handler.TEMPLATES["/jobs"]
        parsed = {
            "as_of_date": None, "include_deleted": False,
            "eq_filters": {}, "range_filters": {}, "limit": 15,
            "preview": False, "question_intent": "",
        }
        sql_explicit, _ = handler.build_query(template, parsed)
        sql_star, _ = handler.build_query(template, {**parsed, "preview": True})
        assert sql_explicit.startswith("SELECT job_id, customer_id")
        assert sql_star.startswith("SELECT * FROM service_job")
        # Both end with the same predicate + limit so LF column-filtering
        # is the only behavioral change.
        assert sql_explicit.endswith("WHERE deleted_at IS NULL LIMIT 15")
        assert sql_star.endswith("WHERE deleted_at IS NULL LIMIT 15")

    def test_run_query_retries_with_preview_on_column_deny(self):
        """End-to-end retry path: STS + Athena mocked, first call denies,
        second call succeeds. The persona ends up with the SELECT * result."""
        from unittest.mock import patch

        athena, _ = self._fake_athena_chain()
        with patch.object(handler.boto3, "client", return_value=athena), \
             patch.object(handler, "_assume_persona", return_value={
                 "AccessKeyId": "a", "SecretAccessKey": "b", "SessionToken": "c",
             }):
            template = handler.TEMPLATES["/jobs"]
            parsed = {
                "as_of_date": None, "include_deleted": False,
                "eq_filters": {}, "range_filters": {}, "limit": 15,
                "preview": False, "question_intent": "",
            }
            rows, cols = handler._run_query(
                _persona("dispatcher"), template, parsed,
            )
        # The retry's SELECT * succeeded; the column set reflects what
        # LF would return (here mocked to job_id + scheduled_date).
        assert cols == ["job_id", "scheduled_date"]
        assert rows == [{"job_id": "j-1", "scheduled_date": "2026-05-12"}]
        # Two start_query calls: first explicit-cols, second SELECT *.
        assert athena.start_query_execution.call_count == 2
        first_sql = athena.start_query_execution.call_args_list[0].kwargs["QueryString"]
        second_sql = athena.start_query_execution.call_args_list[1].kwargs["QueryString"]
        assert first_sql.startswith("SELECT job_id, customer_id")
        assert second_sql.startswith("SELECT * FROM service_job")

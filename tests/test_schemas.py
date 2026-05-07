"""Schema-shape contract tests for the HVAC home-services dataset (ADR-008).

Verify that all ten tables (six core + four supporting) plus the two daily
rollups exist, expected columns are present, SCD2 and soft-delete columns
appear where the ADR requires them, and every column has a PII / sensitivity
classification.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "data" / "synthesizer"))

import schemas  # noqa: E402


EXPECTED_TABLES = {
    "customer",
    "technician",
    "equipment",
    "service_job",
    "review",
    "customer_signal_daily",
    "parts_inventory",
    "dispatch_event",
    "truck_roll",
    "warranty_claim",
    "equipment_telemetry_daily",
    "technician_utilization_daily",
}

SCD2_TABLES = {"customer", "technician", "equipment", "parts_inventory"}
SCD2_REQUIRED = {"effective_from", "effective_to", "is_current"}

SOFT_DELETE_TABLES = {"service_job", "review", "truck_roll", "warranty_claim"}
SOFT_DELETE_REQUIRED = {"deleted_at", "deleted_by"}

EXPECTED_COLUMNS: dict[str, set[str]] = {
    "customer": {
        "customer_id", "customer_type", "service_tier", "service_region",
        "first_name", "last_name", "email", "phone",
        "street_address", "city", "postal_code", "billing_notes",
    },
    "technician": {
        "technician_id", "service_region", "certifications", "hire_date",
        "employment_status", "first_name", "last_name", "email", "phone",
        "home_address",
    },
    "equipment": {
        "equipment_id", "customer_id", "equipment_type", "manufacturer",
        "model_number", "serial_number", "install_date", "warranty_status",
        "warranty_expiry_date", "service_tier",
    },
    "service_job": {
        "job_id", "customer_id", "technician_id", "equipment_id", "job_type",
        "scheduled_date", "completed_date", "status", "total_billed_usd",
        "billing_notes",
    },
    "review": {
        "review_id", "job_id", "customer_id", "rating", "text", "is_public",
        "review_date",
    },
    "customer_signal_daily": {
        "signal_date", "customer_id", "engagement_score", "churn_risk",
        "next_best_action", "service_area_health",
    },
    "parts_inventory": {
        "sku", "part_name", "category", "supplier", "unit_cost_usd",
        "supplier_terms", "qty_on_hand", "warehouse_id",
    },
    "dispatch_event": {
        "event_id", "job_id", "technician_id", "event_ts", "event_type",
        "event_notes",
    },
    "truck_roll": {
        "truck_roll_id", "job_id", "technician_id", "equipment_id",
        "dispatch_ts", "return_ts", "miles_driven", "parts_pulled",
        "parts_returned", "outcome",
    },
    "warranty_claim": {
        "claim_id", "equipment_id", "customer_id", "claim_date", "status",
        "claim_reason", "payout_amount_usd", "supplier_reimbursement_usd",
        "resolved_date", "filed_by",
    },
    "equipment_telemetry_daily": {
        "equipment_id", "telemetry_date", "runtime_hours", "cycle_count",
        "fault_code_count", "efficiency_index", "predicted_failure_30d",
        "last_service_age_days",
    },
    "technician_utilization_daily": {
        "technician_id", "utilization_date", "jobs_completed",
        "billable_hours", "revenue_generated_usd",
        "customer_satisfaction_avg", "parts_consumed_cost_usd", "idle_hours",
    },
}

EXPECTED_PII: dict[str, set[str]] = {
    "customer": {
        "first_name", "last_name", "email", "phone",
        "street_address", "city", "postal_code", "billing_notes",
    },
    "technician": {
        "first_name", "last_name", "email", "phone", "home_address",
    },
    "service_job": {"billing_notes"},
    "review": {"customer_id", "text"},
}

EXPECTED_HIGH_SENSITIVITY: dict[str, set[str]] = {
    "parts_inventory": {"unit_cost_usd", "supplier_terms"},
    "warranty_claim": {"payout_amount_usd", "supplier_reimbursement_usd"},
    "technician_utilization_daily": {
        "revenue_generated_usd", "parts_consumed_cost_usd",
    },
}


def _columns(table: str) -> set[str]:
    return {field.name for field in schemas.SCHEMAS[table]}


def test_all_expected_tables_exist():
    assert set(schemas.SCHEMAS.keys()) == EXPECTED_TABLES


@pytest.mark.parametrize("table,expected", sorted(EXPECTED_COLUMNS.items()))
def test_table_has_expected_columns(table, expected):
    actual = _columns(table)
    missing = expected - actual
    assert not missing, f"{table} missing columns: {sorted(missing)}"


@pytest.mark.parametrize("table", sorted(SCD2_TABLES))
def test_scd2_columns_present(table):
    cols = _columns(table)
    missing = SCD2_REQUIRED - cols
    assert not missing, f"{table} missing SCD2 columns: {sorted(missing)}"


def test_no_scd2_on_non_scd2_tables():
    for table in EXPECTED_TABLES - SCD2_TABLES:
        cols = _columns(table)
        unexpected = SCD2_REQUIRED & cols
        assert not unexpected, (
            f"{table} should not have SCD2 columns: {sorted(unexpected)}"
        )


@pytest.mark.parametrize("table", sorted(SOFT_DELETE_TABLES))
def test_soft_delete_columns_present(table):
    cols = _columns(table)
    missing = SOFT_DELETE_REQUIRED - cols
    assert not missing, (
        f"{table} missing soft-delete columns: {sorted(missing)}"
    )


def test_no_soft_delete_on_non_soft_delete_tables():
    for table in EXPECTED_TABLES - SOFT_DELETE_TABLES:
        cols = _columns(table)
        unexpected = SOFT_DELETE_REQUIRED & cols
        assert not unexpected, (
            f"{table} should not have soft-delete columns: "
            f"{sorted(unexpected)}"
        )


def test_classifications_cover_every_column():
    for table, schema in schemas.SCHEMAS.items():
        cls = schemas.COLUMN_CLASSIFICATIONS.get(table)
        assert cls is not None, f"no classifications for {table}"
        for field in schema:
            assert field.name in cls, (
                f"{table}.{field.name} has no classification entry"
            )


def test_classification_entries_well_formed():
    for table, cls in schemas.COLUMN_CLASSIFICATIONS.items():
        for col, entry in cls.items():
            assert "pii" in entry, f"{table}.{col} missing pii flag"
            assert "sensitivity" in entry, (
                f"{table}.{col} missing sensitivity"
            )
            assert isinstance(entry["pii"], bool), (
                f"{table}.{col}.pii not bool"
            )
            assert entry["sensitivity"] in {"high", "medium", "low"}, (
                f"{table}.{col}.sensitivity invalid: {entry['sensitivity']}"
            )


def test_no_classification_for_phantom_columns():
    for table, cls in schemas.COLUMN_CLASSIFICATIONS.items():
        actual = _columns(table)
        phantom = set(cls.keys()) - actual
        assert not phantom, (
            f"{table} classifies non-existent columns: {sorted(phantom)}"
        )


@pytest.mark.parametrize("table,expected", sorted(EXPECTED_PII.items()))
def test_expected_pii_columns_flagged(table, expected):
    flagged = {
        col for col, c in schemas.COLUMN_CLASSIFICATIONS[table].items()
        if c["pii"]
    }
    missing = expected - flagged
    assert not missing, f"{table} not flagging PII on: {sorted(missing)}"


@pytest.mark.parametrize(
    "table,expected", sorted(EXPECTED_HIGH_SENSITIVITY.items())
)
def test_expected_high_sensitivity_columns(table, expected):
    high = {
        col for col, c in schemas.COLUMN_CLASSIFICATIONS[table].items()
        if c["sensitivity"] == "high"
    }
    missing = expected - high
    assert not missing, (
        f"{table} not flagging sensitivity=high on: {sorted(missing)}"
    )


def test_pii_columns_view_matches_classifications():
    for table, cls in schemas.COLUMN_CLASSIFICATIONS.items():
        derived = {col for col, c in cls.items() if c["pii"]}
        assert schemas.PII_COLUMNS[table] == derived, (
            f"PII_COLUMNS for {table} drifted from classifications"
        )


def test_partition_columns_reference_real_columns():
    for table, partitions in schemas.PARTITION_COLUMNS.items():
        cols = _columns(table)
        for partition_col in partitions:
            assert partition_col in cols, (
                f"{table} partition column {partition_col!r} not in schema"
            )

"""Pyarrow schemas and column classifications for the HVAC home-services dataset.

Per ADR-008. Ten tables: six core (customer, technician, equipment, service_job,
review, customer_signal_daily) plus four supporting (parts_inventory,
dispatch_event, truck_roll, warranty_claim) plus two daily rollups
(equipment_telemetry_daily, technician_utilization_daily).

SCD2 dimensions carry effective_from / effective_to / is_current. Fact tables
flagged for soft-delete carry deleted_at / deleted_by. Every column is
classified for PII and sensitivity to drive Lake Formation tagging.
"""

from __future__ import annotations

import pyarrow as pa

SCD2_FIELDS = [
    pa.field("effective_from", pa.timestamp("us"), nullable=False),
    pa.field("effective_to", pa.timestamp("us")),
    pa.field("is_current", pa.bool_(), nullable=False),
]

SOFT_DELETE_FIELDS = [
    pa.field("deleted_at", pa.timestamp("us")),
    pa.field("deleted_by", pa.string()),
]

SCD2_CLASSIFICATIONS = {
    "effective_from": {"pii": False, "sensitivity": "low"},
    "effective_to": {"pii": False, "sensitivity": "low"},
    "is_current": {"pii": False, "sensitivity": "low"},
}

SOFT_DELETE_CLASSIFICATIONS = {
    "deleted_at": {"pii": False, "sensitivity": "low"},
    "deleted_by": {"pii": False, "sensitivity": "low"},
}


CUSTOMER_SCHEMA = pa.schema(
    [
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("customer_type", pa.string(), nullable=False),
        pa.field("service_tier", pa.string(), nullable=False),
        pa.field("service_region", pa.string(), nullable=False),
        pa.field("first_name", pa.string()),
        pa.field("last_name", pa.string()),
        pa.field("email", pa.string()),
        pa.field("phone", pa.string()),
        pa.field("street_address", pa.string()),
        pa.field("city", pa.string()),
        pa.field("postal_code", pa.string()),
        pa.field("billing_notes", pa.string()),
        *SCD2_FIELDS,
    ]
)

CUSTOMER_CLASSIFICATIONS = {
    "customer_id": {"pii": False, "sensitivity": "low"},
    "customer_type": {"pii": False, "sensitivity": "low"},
    "service_tier": {"pii": False, "sensitivity": "low"},
    "service_region": {"pii": False, "sensitivity": "low"},
    "first_name": {"pii": True, "sensitivity": "low"},
    "last_name": {"pii": True, "sensitivity": "low"},
    "email": {"pii": True, "sensitivity": "low"},
    "phone": {"pii": True, "sensitivity": "low"},
    "street_address": {"pii": True, "sensitivity": "low"},
    "city": {"pii": True, "sensitivity": "low"},
    "postal_code": {"pii": True, "sensitivity": "low"},
    "billing_notes": {"pii": True, "sensitivity": "medium"},
    **SCD2_CLASSIFICATIONS,
}


TECHNICIAN_SCHEMA = pa.schema(
    [
        pa.field("technician_id", pa.string(), nullable=False),
        pa.field("service_region", pa.string(), nullable=False),
        pa.field("certifications", pa.list_(pa.string())),
        pa.field("hire_date", pa.date32(), nullable=False),
        pa.field("employment_status", pa.string(), nullable=False),
        pa.field("first_name", pa.string()),
        pa.field("last_name", pa.string()),
        pa.field("email", pa.string()),
        pa.field("phone", pa.string()),
        pa.field("home_address", pa.string()),
        *SCD2_FIELDS,
    ]
)

TECHNICIAN_CLASSIFICATIONS = {
    "technician_id": {"pii": False, "sensitivity": "low"},
    "service_region": {"pii": False, "sensitivity": "low"},
    "certifications": {"pii": False, "sensitivity": "low"},
    "hire_date": {"pii": False, "sensitivity": "low"},
    "employment_status": {"pii": False, "sensitivity": "low"},
    "first_name": {"pii": True, "sensitivity": "low"},
    "last_name": {"pii": True, "sensitivity": "low"},
    "email": {"pii": True, "sensitivity": "low"},
    "phone": {"pii": True, "sensitivity": "low"},
    "home_address": {"pii": True, "sensitivity": "low"},
    **SCD2_CLASSIFICATIONS,
}


EQUIPMENT_SCHEMA = pa.schema(
    [
        pa.field("equipment_id", pa.string(), nullable=False),
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("equipment_type", pa.string(), nullable=False),
        pa.field("manufacturer", pa.string(), nullable=False),
        pa.field("model_number", pa.string()),
        pa.field("serial_number", pa.string()),
        pa.field("install_date", pa.date32(), nullable=False),
        pa.field("warranty_status", pa.string(), nullable=False),
        pa.field("warranty_expiry_date", pa.date32()),
        pa.field("service_tier", pa.string(), nullable=False),
        *SCD2_FIELDS,
    ]
)

EQUIPMENT_CLASSIFICATIONS = {
    "equipment_id": {"pii": False, "sensitivity": "low"},
    "customer_id": {"pii": False, "sensitivity": "low"},
    "equipment_type": {"pii": False, "sensitivity": "low"},
    "manufacturer": {"pii": False, "sensitivity": "low"},
    "model_number": {"pii": False, "sensitivity": "low"},
    "serial_number": {"pii": False, "sensitivity": "low"},
    "install_date": {"pii": False, "sensitivity": "low"},
    "warranty_status": {"pii": False, "sensitivity": "low"},
    "warranty_expiry_date": {"pii": False, "sensitivity": "low"},
    "service_tier": {"pii": False, "sensitivity": "low"},
    **SCD2_CLASSIFICATIONS,
}


SERVICE_JOB_SCHEMA = pa.schema(
    [
        pa.field("job_id", pa.string(), nullable=False),
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("technician_id", pa.string()),
        pa.field("equipment_id", pa.string()),
        pa.field("job_type", pa.string(), nullable=False),
        pa.field("scheduled_date", pa.date32(), nullable=False),
        pa.field("completed_date", pa.date32()),
        pa.field("status", pa.string(), nullable=False),
        pa.field("total_billed_usd", pa.decimal128(10, 2)),
        pa.field("billing_notes", pa.string()),
        *SOFT_DELETE_FIELDS,
    ]
)

SERVICE_JOB_CLASSIFICATIONS = {
    "job_id": {"pii": False, "sensitivity": "low"},
    "customer_id": {"pii": False, "sensitivity": "low"},
    "technician_id": {"pii": False, "sensitivity": "low"},
    "equipment_id": {"pii": False, "sensitivity": "low"},
    "job_type": {"pii": False, "sensitivity": "low"},
    "scheduled_date": {"pii": False, "sensitivity": "low"},
    "completed_date": {"pii": False, "sensitivity": "low"},
    "status": {"pii": False, "sensitivity": "low"},
    "total_billed_usd": {"pii": False, "sensitivity": "low"},
    "billing_notes": {"pii": True, "sensitivity": "medium"},
    **SOFT_DELETE_CLASSIFICATIONS,
}


REVIEW_SCHEMA = pa.schema(
    [
        pa.field("review_id", pa.string(), nullable=False),
        pa.field("job_id", pa.string(), nullable=False),
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("rating", pa.int32(), nullable=False),
        pa.field("text", pa.string()),
        pa.field("is_public", pa.bool_(), nullable=False),
        pa.field("review_date", pa.date32(), nullable=False),
        *SOFT_DELETE_FIELDS,
    ]
)

REVIEW_CLASSIFICATIONS = {
    "review_id": {"pii": False, "sensitivity": "low"},
    "job_id": {"pii": False, "sensitivity": "low"},
    "customer_id": {"pii": True, "sensitivity": "low"},
    "rating": {"pii": False, "sensitivity": "low"},
    "text": {"pii": True, "sensitivity": "medium"},
    "is_public": {"pii": False, "sensitivity": "low"},
    "review_date": {"pii": False, "sensitivity": "low"},
    **SOFT_DELETE_CLASSIFICATIONS,
}


CUSTOMER_SIGNAL_DAILY_SCHEMA = pa.schema(
    [
        pa.field("signal_date", pa.date32(), nullable=False),
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("engagement_score", pa.int32(), nullable=False),
        pa.field("churn_risk", pa.int32(), nullable=False),
        pa.field("next_best_action", pa.string(), nullable=False),
        pa.field("service_area_health", pa.int32(), nullable=False),
    ]
)

CUSTOMER_SIGNAL_DAILY_CLASSIFICATIONS = {
    "signal_date": {"pii": False, "sensitivity": "low"},
    "customer_id": {"pii": False, "sensitivity": "low"},
    "engagement_score": {"pii": False, "sensitivity": "low"},
    "churn_risk": {"pii": False, "sensitivity": "low"},
    "next_best_action": {"pii": False, "sensitivity": "low"},
    "service_area_health": {"pii": False, "sensitivity": "low"},
}


PARTS_INVENTORY_SCHEMA = pa.schema(
    [
        pa.field("sku", pa.string(), nullable=False),
        pa.field("part_name", pa.string(), nullable=False),
        pa.field("category", pa.string(), nullable=False),
        pa.field("supplier", pa.string(), nullable=False),
        pa.field("unit_cost_usd", pa.decimal128(10, 2), nullable=False),
        pa.field("supplier_terms", pa.string()),
        pa.field("qty_on_hand", pa.int32(), nullable=False),
        pa.field("warehouse_id", pa.string(), nullable=False),
        *SCD2_FIELDS,
    ]
)

PARTS_INVENTORY_CLASSIFICATIONS = {
    "sku": {"pii": False, "sensitivity": "low"},
    "part_name": {"pii": False, "sensitivity": "low"},
    "category": {"pii": False, "sensitivity": "low"},
    "supplier": {"pii": False, "sensitivity": "low"},
    "unit_cost_usd": {"pii": False, "sensitivity": "high"},
    "supplier_terms": {"pii": False, "sensitivity": "high"},
    "qty_on_hand": {"pii": False, "sensitivity": "low"},
    "warehouse_id": {"pii": False, "sensitivity": "low"},
    **SCD2_CLASSIFICATIONS,
}


DISPATCH_EVENT_SCHEMA = pa.schema(
    [
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("job_id", pa.string(), nullable=False),
        pa.field("technician_id", pa.string()),
        pa.field("event_ts", pa.timestamp("us"), nullable=False),
        pa.field("event_type", pa.string(), nullable=False),
        pa.field("event_notes", pa.string()),
    ]
)

DISPATCH_EVENT_CLASSIFICATIONS = {
    "event_id": {"pii": False, "sensitivity": "low"},
    "job_id": {"pii": False, "sensitivity": "low"},
    "technician_id": {"pii": False, "sensitivity": "low"},
    "event_ts": {"pii": False, "sensitivity": "low"},
    "event_type": {"pii": False, "sensitivity": "low"},
    "event_notes": {"pii": False, "sensitivity": "low"},
}


TRUCK_ROLL_SCHEMA = pa.schema(
    [
        pa.field("truck_roll_id", pa.string(), nullable=False),
        pa.field("job_id", pa.string(), nullable=False),
        pa.field("technician_id", pa.string(), nullable=False),
        pa.field("equipment_id", pa.string()),
        pa.field("dispatch_ts", pa.timestamp("us"), nullable=False),
        pa.field("return_ts", pa.timestamp("us")),
        pa.field("miles_driven", pa.decimal128(8, 2)),
        pa.field("parts_pulled", pa.list_(pa.string())),
        pa.field("parts_returned", pa.list_(pa.string())),
        pa.field("outcome", pa.string()),
        *SOFT_DELETE_FIELDS,
    ]
)

TRUCK_ROLL_CLASSIFICATIONS = {
    "truck_roll_id": {"pii": False, "sensitivity": "low"},
    "job_id": {"pii": False, "sensitivity": "low"},
    "technician_id": {"pii": False, "sensitivity": "low"},
    "equipment_id": {"pii": False, "sensitivity": "low"},
    "dispatch_ts": {"pii": False, "sensitivity": "low"},
    "return_ts": {"pii": False, "sensitivity": "low"},
    "miles_driven": {"pii": False, "sensitivity": "low"},
    "parts_pulled": {"pii": False, "sensitivity": "low"},
    "parts_returned": {"pii": False, "sensitivity": "low"},
    "outcome": {"pii": False, "sensitivity": "low"},
    **SOFT_DELETE_CLASSIFICATIONS,
}


WARRANTY_CLAIM_SCHEMA = pa.schema(
    [
        pa.field("claim_id", pa.string(), nullable=False),
        pa.field("equipment_id", pa.string(), nullable=False),
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("claim_date", pa.date32(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("claim_reason", pa.string()),
        pa.field("payout_amount_usd", pa.decimal128(10, 2)),
        pa.field("supplier_reimbursement_usd", pa.decimal128(10, 2)),
        pa.field("resolved_date", pa.date32()),
        pa.field("filed_by", pa.string()),
        *SOFT_DELETE_FIELDS,
    ]
)

WARRANTY_CLAIM_CLASSIFICATIONS = {
    "claim_id": {"pii": False, "sensitivity": "low"},
    "equipment_id": {"pii": False, "sensitivity": "low"},
    "customer_id": {"pii": False, "sensitivity": "low"},
    "claim_date": {"pii": False, "sensitivity": "low"},
    "status": {"pii": False, "sensitivity": "low"},
    "claim_reason": {"pii": False, "sensitivity": "low"},
    "payout_amount_usd": {"pii": False, "sensitivity": "high"},
    "supplier_reimbursement_usd": {"pii": False, "sensitivity": "high"},
    "resolved_date": {"pii": False, "sensitivity": "low"},
    "filed_by": {"pii": False, "sensitivity": "low"},
    **SOFT_DELETE_CLASSIFICATIONS,
}


EQUIPMENT_TELEMETRY_DAILY_SCHEMA = pa.schema(
    [
        pa.field("equipment_id", pa.string(), nullable=False),
        pa.field("telemetry_date", pa.date32(), nullable=False),
        pa.field("runtime_hours", pa.decimal128(8, 2), nullable=False),
        pa.field("cycle_count", pa.int32(), nullable=False),
        pa.field("fault_code_count", pa.int32(), nullable=False),
        pa.field("efficiency_index", pa.int32(), nullable=False),
        pa.field("predicted_failure_30d", pa.decimal128(5, 4), nullable=False),
        pa.field("last_service_age_days", pa.int32(), nullable=False),
    ]
)

EQUIPMENT_TELEMETRY_DAILY_CLASSIFICATIONS = {
    "equipment_id": {"pii": False, "sensitivity": "low"},
    "telemetry_date": {"pii": False, "sensitivity": "low"},
    "runtime_hours": {"pii": False, "sensitivity": "low"},
    "cycle_count": {"pii": False, "sensitivity": "low"},
    "fault_code_count": {"pii": False, "sensitivity": "low"},
    "efficiency_index": {"pii": False, "sensitivity": "low"},
    "predicted_failure_30d": {"pii": False, "sensitivity": "low"},
    "last_service_age_days": {"pii": False, "sensitivity": "low"},
}


TECHNICIAN_UTILIZATION_DAILY_SCHEMA = pa.schema(
    [
        pa.field("technician_id", pa.string(), nullable=False),
        pa.field("utilization_date", pa.date32(), nullable=False),
        pa.field("jobs_completed", pa.int32(), nullable=False),
        pa.field("billable_hours", pa.decimal128(6, 2), nullable=False),
        pa.field("revenue_generated_usd", pa.decimal128(10, 2), nullable=False),
        pa.field("customer_satisfaction_avg", pa.decimal128(3, 2)),
        pa.field("parts_consumed_cost_usd", pa.decimal128(10, 2), nullable=False),
        pa.field("idle_hours", pa.decimal128(6, 2), nullable=False),
    ]
)

TECHNICIAN_UTILIZATION_DAILY_CLASSIFICATIONS = {
    "technician_id": {"pii": False, "sensitivity": "low"},
    "utilization_date": {"pii": False, "sensitivity": "low"},
    "jobs_completed": {"pii": False, "sensitivity": "low"},
    "billable_hours": {"pii": False, "sensitivity": "low"},
    "revenue_generated_usd": {"pii": False, "sensitivity": "high"},
    "customer_satisfaction_avg": {"pii": False, "sensitivity": "low"},
    "parts_consumed_cost_usd": {"pii": False, "sensitivity": "high"},
    "idle_hours": {"pii": False, "sensitivity": "low"},
}


SCHEMAS: dict[str, pa.Schema] = {
    "customer": CUSTOMER_SCHEMA,
    "technician": TECHNICIAN_SCHEMA,
    "equipment": EQUIPMENT_SCHEMA,
    "service_job": SERVICE_JOB_SCHEMA,
    "review": REVIEW_SCHEMA,
    "customer_signal_daily": CUSTOMER_SIGNAL_DAILY_SCHEMA,
    "parts_inventory": PARTS_INVENTORY_SCHEMA,
    "dispatch_event": DISPATCH_EVENT_SCHEMA,
    "truck_roll": TRUCK_ROLL_SCHEMA,
    "warranty_claim": WARRANTY_CLAIM_SCHEMA,
    "equipment_telemetry_daily": EQUIPMENT_TELEMETRY_DAILY_SCHEMA,
    "technician_utilization_daily": TECHNICIAN_UTILIZATION_DAILY_SCHEMA,
}

COLUMN_CLASSIFICATIONS: dict[str, dict[str, dict[str, bool | str]]] = {
    "customer": CUSTOMER_CLASSIFICATIONS,
    "technician": TECHNICIAN_CLASSIFICATIONS,
    "equipment": EQUIPMENT_CLASSIFICATIONS,
    "service_job": SERVICE_JOB_CLASSIFICATIONS,
    "review": REVIEW_CLASSIFICATIONS,
    "customer_signal_daily": CUSTOMER_SIGNAL_DAILY_CLASSIFICATIONS,
    "parts_inventory": PARTS_INVENTORY_CLASSIFICATIONS,
    "dispatch_event": DISPATCH_EVENT_CLASSIFICATIONS,
    "truck_roll": TRUCK_ROLL_CLASSIFICATIONS,
    "warranty_claim": WARRANTY_CLAIM_CLASSIFICATIONS,
    "equipment_telemetry_daily": EQUIPMENT_TELEMETRY_DAILY_CLASSIFICATIONS,
    "technician_utilization_daily": TECHNICIAN_UTILIZATION_DAILY_CLASSIFICATIONS,
}

PII_COLUMNS: dict[str, set[str]] = {
    table: {col for col, c in cls.items() if c["pii"]}
    for table, cls in COLUMN_CLASSIFICATIONS.items()
}

SENSITIVE_COLUMNS: dict[str, dict[str, set[str]]] = {
    table: {
        level: {col for col, c in cls.items() if c["sensitivity"] == level}
        for level in ("high", "medium", "low")
    }
    for table, cls in COLUMN_CLASSIFICATIONS.items()
}

PARTITION_COLUMNS: dict[str, list[str]] = {
    "customer_signal_daily": ["signal_date"],
    "dispatch_event": ["event_ts"],
    "equipment_telemetry_daily": ["telemetry_date"],
    "technician_utilization_daily": ["utilization_date"],
}


def athena_columns(table: str) -> list[tuple[str, str]]:
    """Return (column_name, athena_type) for the given table.

    Iceberg tables created via Athena DDL need explicit column types.
    Pyarrow types are mapped to Athena/Trino types here.
    """
    schema = SCHEMAS[table]
    return [(field.name, _arrow_to_athena(field.type)) for field in schema]


def _arrow_to_athena(arrow_type: pa.DataType) -> str:
    if pa.types.is_string(arrow_type):
        return "string"
    if pa.types.is_int32(arrow_type) or pa.types.is_int64(arrow_type):
        return "int"
    if pa.types.is_date(arrow_type):
        return "date"
    if pa.types.is_timestamp(arrow_type):
        return "timestamp"
    if pa.types.is_decimal(arrow_type):
        return f"decimal({arrow_type.precision},{arrow_type.scale})"
    if pa.types.is_list(arrow_type):
        return f"array<{_arrow_to_athena(arrow_type.value_type)}>"
    if pa.types.is_boolean(arrow_type):
        return "boolean"
    raise ValueError(f"Unsupported arrow type: {arrow_type}")

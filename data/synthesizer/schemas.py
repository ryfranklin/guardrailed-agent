"""Pyarrow schemas and column metadata for the four governed tables.

Used by both `generate.py` (to write Parquet) and the LF-Tag tagger (to
classify PII columns). Brief §8.
"""

from __future__ import annotations

import pyarrow as pa

AMBASSADOR_SCHEMA = pa.schema(
    [
        pa.field("ambassador_id", pa.string(), nullable=False),
        pa.field("enrollment_date", pa.date32(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("rank", pa.string(), nullable=False),
        pa.field("region", pa.string(), nullable=False),
        pa.field("first_name", pa.string()),
        pa.field("last_name", pa.string()),
        pa.field("email", pa.string()),
        pa.field("phone", pa.string()),
        pa.field("ssn_last4", pa.string()),
        pa.field("date_of_birth", pa.date32()),
        pa.field("street_address", pa.string()),
        pa.field("city", pa.string()),
        pa.field("postal_code", pa.string()),
    ]
)

AMBASSADOR_TEAM_SCHEMA = pa.schema(
    [
        pa.field("ambassador_id", pa.string(), nullable=False),
        pa.field("sponsor_id", pa.string()),
        pa.field("upline_path", pa.list_(pa.string())),
        pa.field("generation", pa.int32(), nullable=False),
        pa.field("joined_team_date", pa.date32(), nullable=False),
    ]
)

ORDER_FACT_SCHEMA = pa.schema(
    [
        pa.field("order_id", pa.string(), nullable=False),
        pa.field("ambassador_id", pa.string(), nullable=False),
        pa.field("order_date", pa.date32(), nullable=False),
        pa.field("order_total", pa.decimal128(10, 2), nullable=False),
        pa.field("product_category", pa.string(), nullable=False),
        pa.field("order_status", pa.string(), nullable=False),
        pa.field("payment_last4", pa.string()),
    ]
)

SIGNAL_FACT_SCHEMA = pa.schema(
    [
        pa.field("signal_date", pa.date32(), nullable=False),
        pa.field("ambassador_id", pa.string(), nullable=False),
        pa.field("momentum_score", pa.int32(), nullable=False),
        pa.field("churn_risk", pa.int32(), nullable=False),
        pa.field("next_best_action", pa.string(), nullable=False),
        pa.field("team_health_score", pa.int32(), nullable=False),
    ]
)

SCHEMAS: dict[str, pa.Schema] = {
    "ambassador": AMBASSADOR_SCHEMA,
    "ambassador_team": AMBASSADOR_TEAM_SCHEMA,
    "order_fact": ORDER_FACT_SCHEMA,
    "signal_fact": SIGNAL_FACT_SCHEMA,
}

PII_COLUMNS: dict[str, set[str]] = {
    "ambassador": {
        "first_name",
        "last_name",
        "email",
        "phone",
        "ssn_last4",
        "date_of_birth",
        "street_address",
        "city",
        "postal_code",
    },
    "ambassador_team": set(),
    "order_fact": {"payment_last4"},
    "signal_fact": set(),
}

PARTITION_COLUMNS: dict[str, list[str]] = {
    "signal_fact": ["signal_date"],
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
    if pa.types.is_decimal(arrow_type):
        return f"decimal({arrow_type.precision},{arrow_type.scale})"
    if pa.types.is_list(arrow_type):
        return f"array<{_arrow_to_athena(arrow_type.value_type)}>"
    if pa.types.is_boolean(arrow_type):
        return "boolean"
    raise ValueError(f"Unsupported arrow type: {arrow_type}")

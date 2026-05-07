#!/usr/bin/env python3
"""Register the ADR-008 HVAC tables as Iceberg in Glue via Athena CTAS.

Prerequisites:
  1. `python -m data.synthesizer.generate ... --upload` has staged ten core +
     supporting parquet files to s3://<bucket>/staging/<table>/
  2. `python -m data.synthesizer.telemetry ... --upload` (or manual upload)
     has staged equipment_telemetry_daily to the same staging prefix
  3. `python -m data.synthesizer.utilization ... --upload` (or manual upload)
     has staged technician_utilization_daily

This script then, for each of the twelve tables in
data/synthesizer/schemas.py::SCHEMAS:
  1. DROP TABLE IF EXISTS <table>      (idempotent re-registration)
  2. CREATE EXTERNAL TABLE stg_<table> over the staged parquet
  3. CREATE TABLE <table> WITH (table_type='ICEBERG', ...) AS SELECT * FROM stg_<table>
  4. DROP TABLE stg_<table>            (cleanup)

Partitioning per Iceberg spec:
  customer_signal_daily          identity on signal_date
  dispatch_event                 hour(event_ts)              (high-cardinality stream)
  equipment_telemetry_daily      identity on telemetry_date
  technician_utilization_daily   identity on utilization_date

Usage:
  python scripts/register-iceberg.py \\
      --bucket   $(terraform -chdir=terraform/envs/demo output -raw data_bucket_name) \\
      --database $(terraform -chdir=terraform/envs/demo output -raw glue_database_name) \\
      --workgroup $(terraform -chdir=terraform/envs/demo output -raw athena_workgroup_name) \\
      --region   us-east-1

  # subset:
  python scripts/register-iceberg.py ... --tables customer service_job

Idempotent. Re-runnable.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from data.synthesizer.schemas import SCHEMAS, athena_columns  # noqa: E402

logger = logging.getLogger("register-iceberg")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


# Per-table Iceberg partition spec strings. Empty list means unpartitioned.
PARTITIONING: dict[str, list[str]] = {
    # customer_signal_daily has only ~30 distinct signal_dates in the demo
    # window — identity partitioning fits well under Athena's 100-writer cap.
    "customer_signal_daily": ["signal_date"],
    # The other three time-series tables span ~365 days. Identity partitioning
    # by date or hour() partitioning by timestamp trips ICEBERG_TOO_MANY_OPEN_-
    # PARTITIONS during CTAS (>100 open writers). month() keeps it at ~12
    # partitions per year — pruning is still effective for the agent's
    # typical "last 7 / 30 / 90 days" queries.
    "dispatch_event": ["month(event_ts)"],
    "equipment_telemetry_daily": ["month(telemetry_date)"],
    "technician_utilization_daily": ["month(utilization_date)"],
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    targets = list(args.tables) if args.tables else list(SCHEMAS.keys())
    unknown = [t for t in targets if t not in SCHEMAS]
    if unknown:
        raise SystemExit(f"unknown table(s): {unknown}; valid: {sorted(SCHEMAS)}")

    import boto3
    athena = boto3.client("athena", region_name=args.region)

    for table in targets:
        logger.info("=== %s ===", table)
        stage_name = f"stg_{table}"
        stage_location = f"s3://{args.bucket}/staging/{table}/"
        iceberg_location = f"s3://{args.bucket}/{args.database}/{table}/"

        _run(athena, args, f"DROP TABLE IF EXISTS {table}")
        _run(athena, args, f"DROP TABLE IF EXISTS {stage_name}")
        _run(athena, args, _stage_external_ddl(stage_name, table, stage_location))
        _run(athena, args, _iceberg_ctas_ddl(table, stage_name, iceberg_location))
        _run(athena, args, f"DROP TABLE {stage_name}")
        logger.info("registered %s.%s -> %s", args.database, table, iceberg_location)

    logger.info("done; %d table(s) registered", len(targets))
    return 0


def _stage_external_ddl(stage_name: str, table: str, location: str) -> str:
    cols = ", ".join(f"`{name}` {typ}" for name, typ in athena_columns(table))
    return (
        f"CREATE EXTERNAL TABLE {stage_name} ({cols}) "
        f"STORED AS PARQUET LOCATION '{location}'"
    )


def _iceberg_ctas_ddl(table: str, stage_name: str, location: str) -> str:
    parts = PARTITIONING.get(table, [])
    partition_clause = ""
    if parts:
        spec = ", ".join(f"'{p}'" for p in parts)
        partition_clause = f", partitioning = ARRAY[{spec}]"
    return (
        f"CREATE TABLE {table} "
        f"WITH (table_type='ICEBERG', is_external=false, format='PARQUET', "
        f"location='{location}'{partition_clause}) "
        f"AS SELECT * FROM {stage_name}"
    )


def _run(athena, args: argparse.Namespace, sql: str) -> None:
    logger.info("athena: %s", sql[:140] + ("..." if len(sql) > 140 else ""))
    execution_id = athena.start_query_execution(
        QueryString=sql,
        WorkGroup=args.workgroup,
        QueryExecutionContext={"Database": args.database},
    )["QueryExecutionId"]
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        resp = athena.get_query_execution(QueryExecutionId=execution_id)
        status = resp["QueryExecution"]["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            return
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(
                f"athena {state}: {status.get('StateChangeReason')}\nSQL: {sql}"
            )
        time.sleep(2)
    raise TimeoutError(f"athena query timed out after {args.timeout}s: {sql}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Register HVAC tables as Iceberg via Athena CTAS (ADR-008).",
    )
    p.add_argument("--bucket", required=True, help="S3 bucket holding staged parquet.")
    p.add_argument("--database", required=True, help="Glue database name.")
    p.add_argument("--workgroup", required=True, help="Athena workgroup.")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--timeout", type=int, default=600,
                   help="Per-query timeout (seconds).")
    p.add_argument("--tables", nargs="+",
                   help=f"Subset of tables to register. Default: all {len(SCHEMAS)}.")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())

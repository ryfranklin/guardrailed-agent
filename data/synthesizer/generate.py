"""Synthetic ambassador-data generator.

Pipeline:
  1. Generate four tables with Faker (volumes per brief §8).
  2. Validate the team DAG (no cycles, depth bounded).
  3. Write Parquet files locally.
  4. Optionally upload to S3 and register as Iceberg via Athena CTAS.
  5. Optionally apply LF-Tag pii=true to PII columns.

Designed to be re-runnable: each invocation drops + recreates target tables.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import random
import sys
import time
import uuid
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from faker import Faker

from schemas import (
    PARTITION_COLUMNS,
    PII_COLUMNS,
    SCHEMAS,
    athena_columns,
)

logger = logging.getLogger("synth")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

US_STATES_WEIGHTED = [
    ("CA", 39), ("TX", 30), ("FL", 22), ("NY", 19), ("PA", 13), ("IL", 12),
    ("OH", 12), ("GA", 11), ("NC", 11), ("MI", 10), ("NJ", 9), ("VA", 9),
    ("WA", 8), ("AZ", 7), ("TN", 7), ("MA", 7), ("IN", 7), ("MO", 6),
    ("MD", 6), ("WI", 6), ("CO", 6), ("MN", 6), ("SC", 5), ("AL", 5),
    ("LA", 5), ("KY", 4), ("OR", 4), ("OK", 4), ("CT", 4), ("UT", 3),
    ("IA", 3), ("NV", 3), ("AR", 3), ("MS", 3), ("KS", 3), ("NM", 2),
    ("NE", 2), ("ID", 2), ("WV", 2), ("HI", 1), ("NH", 1), ("ME", 1),
    ("MT", 1), ("RI", 1), ("DE", 1), ("SD", 1), ("ND", 1), ("AK", 1),
    ("VT", 1), ("WY", 1),
]
STATUS_WEIGHTS = [("active", 70), ("inactive", 20), ("terminated", 10)]
RANK_WEIGHTS = [("bronze", 50), ("silver", 25), ("gold", 15), ("platinum", 7), ("diamond", 3)]
PRODUCT_CATEGORIES = ["wellness", "beauty", "home", "apparel", "outdoor"]
ORDER_STATUSES = ["completed", "completed", "completed", "completed", "refunded", "cancelled"]
NEXT_BEST_ACTIONS = ["outreach", "coaching", "promotion", "retention_offer"]


@dataclasses.dataclass
class Volumes:
    ambassadors: int = 10_000
    orders: int = 50_000
    signal_days: int = 30


@dataclasses.dataclass
class Config:
    output_dir: Path
    bucket: str | None
    database: str | None
    workgroup: str | None
    region: str
    seed: int
    volumes: Volumes
    upload: bool
    register: bool
    apply_lf_tags: bool


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = _config_from_args(args)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    random.seed(cfg.seed)
    fake = Faker("en_US")
    Faker.seed(cfg.seed)

    logger.info("generating tables seed=%d volumes=%s", cfg.seed, cfg.volumes)

    ambassadors = _gen_ambassadors(fake, cfg.volumes.ambassadors)
    teams = _gen_teams(ambassadors)
    orders = _gen_orders(fake, ambassadors, cfg.volumes.orders)
    signals = _gen_signals(ambassadors, cfg.volumes.signal_days)

    tables = {
        "ambassador": ambassadors,
        "ambassador_team": teams,
        "order_fact": orders,
        "signal_fact": signals,
    }
    for name, rows in tables.items():
        _write_parquet(name, rows, cfg.output_dir / f"{name}.parquet")
        logger.info("wrote %s rows=%d", name, len(rows))

    if cfg.upload:
        _ensure(cfg.bucket, "--bucket required when uploading")
        _upload_parquet(cfg.output_dir, cfg.bucket, list(tables))

    if cfg.register:
        _ensure(cfg.bucket and cfg.database and cfg.workgroup,
                "--bucket, --database, --workgroup required when registering")
        _register_iceberg_tables(cfg.bucket, cfg.database, cfg.workgroup, cfg.region, list(tables))

    if cfg.apply_lf_tags:
        _ensure(cfg.database, "--database required when applying LF tags")
        _apply_lf_tags(cfg.database, cfg.region)

    logger.info("done")
    return 0


def _ensure(condition: Any, message: str) -> None:
    if not condition:
        raise SystemExit(f"error: {message}")


def _gen_ambassadors(fake: Faker, n: int) -> list[dict[str, Any]]:
    today = date.today()
    out: list[dict[str, Any]] = []
    for _ in range(n):
        enrollment = today - timedelta(days=random.randint(30, 365 * 4))
        dob = today - timedelta(days=random.randint(365 * 22, 365 * 70))
        state = _weighted_choice(US_STATES_WEIGHTED)
        out.append(
            {
                "ambassador_id": str(uuid.uuid4()),
                "enrollment_date": enrollment,
                "status": _weighted_choice(STATUS_WEIGHTS),
                "rank": _weighted_choice(RANK_WEIGHTS),
                "region": state,
                "first_name": fake.first_name(),
                "last_name": fake.last_name(),
                "email": fake.email(),
                "phone": fake.numerify("###-###-####"),
                "ssn_last4": fake.numerify("####"),
                "date_of_birth": dob,
                "street_address": fake.street_address(),
                "city": fake.city(),
                "postal_code": fake.postcode(),
            }
        )
    return out


def _gen_teams(ambassadors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a sponsor DAG. ~5% are top-of-tree (no sponsor).

    Sponsors are drawn from earlier-enrolled ambassadors, capping max depth at 6.
    """
    sorted_amb = sorted(ambassadors, key=lambda a: a["enrollment_date"])
    by_id: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []

    for amb in sorted_amb:
        amb_id = amb["ambassador_id"]
        eligible = [
            a for a in by_id.values()
            if a["generation"] < 5 and a["enrollment_date"] <= amb["enrollment_date"]
        ]
        if not eligible or random.random() < 0.05:
            sponsor_id = None
            upline = []
            generation = 0
        else:
            sponsor = random.choice(eligible)
            sponsor_id = sponsor["ambassador_id"]
            upline = sponsor["upline_path"] + [sponsor_id]
            generation = sponsor["generation"] + 1

        joined = amb["enrollment_date"] + timedelta(days=random.randint(0, 14))
        record = {
            "ambassador_id": amb_id,
            "sponsor_id": sponsor_id,
            "upline_path": upline,
            "generation": generation,
            "joined_team_date": joined,
            "enrollment_date": amb["enrollment_date"],
        }
        by_id[amb_id] = record
        out.append({k: record[k] for k in
                    ("ambassador_id", "sponsor_id", "upline_path", "generation", "joined_team_date")})

    _validate_team_dag(out)
    return out


def _validate_team_dag(rows: list[dict[str, Any]]) -> None:
    by_id = {r["ambassador_id"]: r for r in rows}
    for r in rows:
        seen = set()
        cur = r["sponsor_id"]
        depth = 0
        while cur is not None:
            if cur in seen:
                raise ValueError(f"cycle detected at {r['ambassador_id']}")
            seen.add(cur)
            depth += 1
            if depth > 10:
                raise ValueError(f"depth exceeded at {r['ambassador_id']}")
            cur = by_id.get(cur, {}).get("sponsor_id")


def _gen_orders(fake: Faker, ambassadors: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    today = date.today()
    out: list[dict[str, Any]] = []
    active_amb = [a for a in ambassadors if a["status"] == "active"]
    pool = active_amb if active_amb else ambassadors
    for _ in range(n):
        amb = random.choice(pool)
        days_ago = int(random.expovariate(1 / 60))
        days_ago = min(days_ago, 365)
        order_date = today - timedelta(days=days_ago)
        out.append(
            {
                "order_id": str(uuid.uuid4()),
                "ambassador_id": amb["ambassador_id"],
                "order_date": order_date,
                "order_total": Decimal(f"{random.uniform(20, 800):.2f}"),
                "product_category": random.choice(PRODUCT_CATEGORIES),
                "order_status": random.choice(ORDER_STATUSES),
                "payment_last4": fake.numerify("####"),
            }
        )
    return out


def _gen_signals(ambassadors: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    today = date.today()
    out: list[dict[str, Any]] = []
    for d_offset in range(days):
        signal_date = today - timedelta(days=d_offset)
        for amb in ambassadors:
            if amb["status"] == "terminated" and random.random() < 0.7:
                continue
            out.append(
                {
                    "signal_date": signal_date,
                    "ambassador_id": amb["ambassador_id"],
                    "momentum_score": random.randint(0, 100),
                    "churn_risk": random.randint(0, 100),
                    "next_best_action": random.choice(NEXT_BEST_ACTIONS),
                    "team_health_score": random.randint(0, 100),
                }
            )
    return out


def _weighted_choice(choices: list[tuple[str, int]]) -> str:
    items, weights = zip(*choices, strict=True)
    return random.choices(items, weights=weights, k=1)[0]


def _write_parquet(table: str, rows: list[dict[str, Any]], path: Path) -> None:
    schema = SCHEMAS[table]
    columns: dict[str, list[Any]] = {field.name: [] for field in schema}
    for row in rows:
        for field in schema:
            columns[field.name].append(row.get(field.name))
    arrays = [pa.array(columns[field.name], type=field.type) for field in schema]
    arrow_table = pa.Table.from_arrays(arrays, schema=schema)
    pq.write_table(arrow_table, path, compression="snappy")


def _upload_parquet(local_dir: Path, bucket: str, tables: list[str]) -> None:
    import boto3

    s3 = boto3.client("s3")
    for table in tables:
        local = local_dir / f"{table}.parquet"
        key = f"staging/{table}/data.parquet"
        logger.info("uploading s3://%s/%s", bucket, key)
        s3.upload_file(str(local), bucket, key)


def _register_iceberg_tables(
    bucket: str, database: str, workgroup: str, region: str, tables: list[str]
) -> None:
    import boto3

    athena = boto3.client("athena", region_name=region)

    for table in tables:
        stage_name = f"_stage_{table}"
        stage_location = f"s3://{bucket}/staging/{table}/"
        iceberg_location = f"s3://{bucket}/{database}/{table}/"

        logger.info("registering iceberg table %s.%s", database, table)
        _run_athena_ddl(athena, database, workgroup, f"DROP TABLE IF EXISTS {table}")
        _run_athena_ddl(athena, database, workgroup, f"DROP TABLE IF EXISTS {stage_name}")
        _run_athena_ddl(
            athena, database, workgroup,
            _stage_external_table_ddl(stage_name, table, stage_location),
        )
        _run_athena_ddl(
            athena, database, workgroup,
            _iceberg_ctas_ddl(table, stage_name, iceberg_location),
        )
        _run_athena_ddl(athena, database, workgroup, f"DROP TABLE {stage_name}")


def _stage_external_table_ddl(stage_name: str, table: str, location: str) -> str:
    cols = ", ".join(f"`{name}` {typ}" for name, typ in athena_columns(table))
    return (
        f"CREATE EXTERNAL TABLE {stage_name} ({cols}) "
        f"STORED AS PARQUET LOCATION '{location}'"
    )


def _iceberg_ctas_ddl(table: str, stage_name: str, location: str) -> str:
    partition_clause = ""
    if table in PARTITION_COLUMNS:
        cols = ", ".join(f"'{c}'" for c in PARTITION_COLUMNS[table])
        partition_clause = f", partitioning = ARRAY[{cols}]"
    return (
        f"CREATE TABLE {table} "
        f"WITH (table_type='ICEBERG', format='PARQUET', "
        f"location='{location}'{partition_clause}) "
        f"AS SELECT * FROM {stage_name}"
    )


def _run_athena_ddl(athena, database: str, workgroup: str, sql: str) -> None:
    execution_id = athena.start_query_execution(
        QueryString=sql,
        WorkGroup=workgroup,
        QueryExecutionContext={"Database": database},
    )["QueryExecutionId"]
    deadline = time.time() + 300
    while time.time() < deadline:
        status = athena.get_query_execution(QueryExecutionId=execution_id)["QueryExecution"]["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            return
        if state in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"athena {state}: {status.get('StateChangeReason')}\nSQL: {sql}")
        time.sleep(2)
    raise TimeoutError(f"athena ddl timed out: {sql}")


def _apply_lf_tags(database: str, region: str) -> None:
    import boto3

    lf = boto3.client("lakeformation", region_name=region)
    for table, columns in PII_COLUMNS.items():
        for col in columns:
            logger.info("tagging %s.%s.%s pii=true", database, table, col)
            lf.add_lf_tags_to_resource(
                Resource={
                    "TableWithColumns": {
                        "DatabaseName": database,
                        "Name": table,
                        "ColumnNames": [col],
                    }
                },
                LFTags=[{"TagKey": "pii", "TagValues": ["true"]}],
            )

    counts = Counter()
    for table, columns in PII_COLUMNS.items():
        counts[table] = len(columns)
    logger.info("lf tag counts %s", dict(counts))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate the synthetic ambassador dataset.")
    p.add_argument("--output-dir", type=Path, default=Path("./output"))
    p.add_argument("--bucket", help="S3 bucket for upload + Iceberg target.")
    p.add_argument("--database", help="Glue database name.")
    p.add_argument("--workgroup", help="Athena workgroup.")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--ambassadors", type=int, default=10_000)
    p.add_argument("--orders", type=int, default=50_000)
    p.add_argument("--signal-days", type=int, default=30)
    p.add_argument("--upload", action="store_true")
    p.add_argument("--register", action="store_true")
    p.add_argument("--apply-lf-tags", action="store_true")
    p.add_argument("--all", action="store_true",
                   help="Shortcut: --upload --register --apply-lf-tags.")
    return p.parse_args(argv)


def _config_from_args(args: argparse.Namespace) -> Config:
    upload = args.upload or args.all
    register = args.register or args.all
    apply_lf = args.apply_lf_tags or args.all
    return Config(
        output_dir=args.output_dir,
        bucket=args.bucket,
        database=args.database,
        workgroup=args.workgroup,
        region=args.region,
        seed=args.seed,
        volumes=Volumes(
            ambassadors=args.ambassadors,
            orders=args.orders,
            signal_days=args.signal_days,
        ),
        upload=upload,
        register=register,
        apply_lf_tags=apply_lf,
    )


if __name__ == "__main__":
    sys.exit(main())

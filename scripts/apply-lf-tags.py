#!/usr/bin/env python3
"""Apply ADR-008 Lake Formation column-level tag attachments.

For every column in every table defined in data/synthesizer/schemas.py:
  pii=[true|false]            true if the column is PII per ADR-008
  sensitivity=[high|other]    high on the four named compensation/cost
                              columns; other otherwise

The four sensitivity=high columns (per ADR-008 §4 implementation outline):
  parts_inventory.unit_cost_usd
  parts_inventory.supplier_terms
  technician_utilization_daily.revenue_generated_usd
  warranty_claim.payout_amount_usd

Idempotency
-----------
Lake Formation's add_lf_tags_to_resource is documented as a no-op when the
tag value is already attached. Each call's response is inspected for
Failures; AlreadyExistsException-class failures are logged as `skipped` and
treated as success. Any other Failure is surfaced as a hard error.

Structured logging
------------------
Every action is one stdout line:
  applied {"database":"<db>","table":"<t>","column":"<c>","key":"<k>","value":"<v>"}
  skipped {"database":...,"table":...,"column":...,"key":...,"value":...,"reason":"..."}
  ok      {"database":...,"table":...,"column":...,"key":...,"value":...}
  missing {"database":...,"table":...,"column":...,"key":...,"expected":"..."}
  mismatch {"database":...,"table":...,"column":...,"key":...,"expected":"...","actual":"..."}
plus a final `summary {...}` line. Greppable for diff against expected.

Modes
-----
  apply       Apply tag attachments via Lake Formation. Default.
  --dry-run   Print what would be applied; no AWS calls.
  --verify    Read current LF state via get_resource_lf_tags and compare
              column-by-column to the expected map. Exit 1 on any
              mismatch/missing.

Usage
-----
  python scripts/apply-lf-tags.py --database <db> [--region <r>]
  python scripts/apply-lf-tags.py --database <db> --dry-run
  python scripts/apply-lf-tags.py --database <db> --verify

Verify the column-by-column tag state matches ADR-008 expectations
without re-applying:
  python scripts/apply-lf-tags.py --database <db> --verify
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from data.synthesizer.schemas import (  # noqa: E402
    COLUMN_CLASSIFICATIONS,
    SCHEMAS,
)

PII_TAG_KEY = "pii"
SENSITIVITY_TAG_KEY = "sensitivity"

HIGH_SENSITIVITY_COLUMNS: dict[str, set[str]] = {
    "parts_inventory": {"unit_cost_usd", "supplier_terms"},
    "technician_utilization_daily": {"revenue_generated_usd"},
    "warranty_claim": {"payout_amount_usd"},
}


def desired_tags(table: str, column: str) -> dict[str, str]:
    """Return the desired LF tag values for a column.

    pii is driven by the synthesizer's per-column classification.
    sensitivity is driven by the explicit HIGH_SENSITIVITY_COLUMNS map per
    ADR-008 §4 (two values: high or other).
    """
    cls = COLUMN_CLASSIFICATIONS[table][column]
    pii_value = "true" if cls["pii"] else "false"
    high_cols = HIGH_SENSITIVITY_COLUMNS.get(table, set())
    sensitivity_value = "high" if column in high_cols else "other"
    return {
        PII_TAG_KEY: pii_value,
        SENSITIVITY_TAG_KEY: sensitivity_value,
    }


def expected_state() -> dict[str, dict[str, dict[str, str]]]:
    """Return {table: {column: {key: value}}} for every table and column."""
    state: dict[str, dict[str, dict[str, str]]] = {}
    for table, schema in SCHEMAS.items():
        state[table] = {}
        for field in schema:
            state[table][field.name] = desired_tags(table, field.name)
    return state


def _emit(event: str, payload: dict[str, Any]) -> None:
    sys.stdout.write(f"{event} {json.dumps(payload, sort_keys=True)}\n")
    sys.stdout.flush()


def apply_all(database: str, region: str, dry_run: bool) -> int:
    counters = {"applied": 0, "skipped": 0, "table_missing": 0}
    if dry_run:
        for table, schema in SCHEMAS.items():
            for field in schema:
                tags = desired_tags(table, field.name)
                for key, value in tags.items():
                    _emit("applied", {
                        "database": database, "table": table,
                        "column": field.name, "key": key, "value": value,
                        "dry_run": True,
                    })
                    counters["applied"] += 1
        _emit("summary", {**counters, "mode": "dry-run"})
        return 0

    import boto3
    from botocore.exceptions import ClientError

    lf = boto3.client("lakeformation", region_name=region)

    for table, schema in SCHEMAS.items():
        try:
            for field in schema:
                tags = desired_tags(table, field.name)
                for key, value in tags.items():
                    response = lf.add_lf_tags_to_resource(
                        Resource={
                            "TableWithColumns": {
                                "DatabaseName": database,
                                "Name": table,
                                "ColumnNames": [field.name],
                            },
                        },
                        LFTags=[{"TagKey": key, "TagValues": [value]}],
                    )
                    failures = response.get("Failures") or []
                    if not failures:
                        _emit("applied", {
                            "database": database, "table": table,
                            "column": field.name, "key": key, "value": value,
                        })
                        counters["applied"] += 1
                        continue
                    for failure in failures:
                        err = failure.get("Error", {})
                        code = err.get("ErrorCode", "")
                        message = err.get("ErrorMessage", "")
                        if "AlreadyExists" in code or "already" in message.lower():
                            _emit("skipped", {
                                "database": database, "table": table,
                                "column": field.name, "key": key,
                                "value": value, "reason": code or message,
                            })
                            counters["skipped"] += 1
                        else:
                            _emit("error", {
                                "database": database, "table": table,
                                "column": field.name, "key": key,
                                "value": value, "code": code,
                                "message": message,
                            })
                            return 2
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "EntityNotFoundException":
                _emit("table_missing", {
                    "database": database, "table": table,
                    "reason": "table not registered in Glue yet",
                })
                counters["table_missing"] += 1
                continue
            raise

    _emit("summary", {**counters, "mode": "apply"})
    return 0


def verify(database: str, region: str) -> int:
    import boto3
    from botocore.exceptions import ClientError

    lf = boto3.client("lakeformation", region_name=region)

    actual: dict[str, dict[str, dict[str, str]]] = defaultdict(
        lambda: defaultdict(dict),
    )
    table_missing: list[str] = []
    for table in SCHEMAS:
        try:
            response = lf.get_resource_lf_tags(
                Resource={
                    "Table": {"DatabaseName": database, "Name": table},
                },
                ShowAssignedLFTags=True,
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "EntityNotFoundException":
                table_missing.append(table)
                _emit("table_missing", {
                    "database": database, "table": table,
                    "reason": "table not registered in Glue yet",
                })
                continue
            raise
        for tcol in response.get("LFTagsOnColumns", []) or []:
            col = tcol["Name"]
            for tag in tcol.get("LFTags", []) or []:
                key = tag["TagKey"]
                values = tag.get("TagValues") or []
                actual[table][col][key] = values[0] if values else ""

    expected = expected_state()
    counters = {"ok": 0, "missing": 0, "mismatch": 0,
                "table_missing": len(table_missing)}
    for table, columns in expected.items():
        if table in table_missing:
            continue
        for col, exp_tags in columns.items():
            for key, exp_val in exp_tags.items():
                act_val = actual.get(table, {}).get(col, {}).get(key)
                if act_val == exp_val:
                    _emit("ok", {
                        "database": database, "table": table,
                        "column": col, "key": key, "value": exp_val,
                    })
                    counters["ok"] += 1
                elif act_val is None:
                    _emit("missing", {
                        "database": database, "table": table,
                        "column": col, "key": key, "expected": exp_val,
                    })
                    counters["missing"] += 1
                else:
                    _emit("mismatch", {
                        "database": database, "table": table,
                        "column": col, "key": key,
                        "expected": exp_val, "actual": act_val,
                    })
                    counters["mismatch"] += 1

    _emit("summary", {**counters, "mode": "verify"})
    return 0 if counters["missing"] == 0 and counters["mismatch"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Apply / verify ADR-008 Lake Formation column tags.",
    )
    p.add_argument("--database", required=True,
                   help="Glue database name.")
    p.add_argument("--region", default="us-east-1")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Print actions without calling AWS.")
    mode.add_argument("--verify", action="store_true",
                      help="Compare actual LF tag state to expected.")
    args = p.parse_args(argv)

    if args.verify:
        return verify(args.database, args.region)
    return apply_all(args.database, args.region, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

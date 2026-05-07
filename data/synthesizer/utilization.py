"""Technician utilization daily rollup generator (ADR-008).

Aggregates the dispatch_event stream and supporting fact tables into one row
per (technician_id, utilization_date) for the configured window.

Per-day metrics:
  jobs_completed                count of dispatch_event rows where
                                event_type='complete' for that tech/day
  billable_hours                sum of (return_ts - dispatch_ts) on truck_rolls
                                whose dispatch_ts.date() == day
  revenue_generated_usd         sum of service_job.total_billed_usd for jobs
                                whose 'complete' dispatch event lands on day
  customer_satisfaction_avg     mean of review.rating for jobs completed by
                                this tech that day (NULL if no reviews)
  parts_consumed_cost_usd       sum over truck_rolls of (parts_pulled minus
                                parts_returned, multiset diff) valued at
                                parts_inventory.unit_cost_usd
  idle_hours                    max(0, 8 - billable_hours)

Emits one row per (tech, day) for the full window so dashboards can plot a
continuous time series with explicit zeros.

CLI:
  python -m data.synthesizer.utilization --input ./out --output ./out \\
      --days 365

Library:
  build_utilization(...)
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .schemas import SCHEMAS

logger = logging.getLogger("synth.utilization")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def build_utilization(
    technicians: list[dict[str, Any]],
    dispatch_events: list[dict[str, Any]],
    service_jobs: list[dict[str, Any]],
    truck_rolls: list[dict[str, Any]],
    parts_inventory: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    days: int,
    today: date,
) -> list[dict[str, Any]]:
    """Roll up event-stream + supporting facts to (tech, day) grain."""
    current_techs = [
        t for t in technicians if t.get("is_current", True)
    ]
    parts_cost: dict[str, Decimal] = {
        p["sku"]: p["unit_cost_usd"]
        for p in parts_inventory if p.get("is_current", True)
    }

    jobs_by_id = {j["job_id"]: j for j in service_jobs}

    completed_jobs_per_key: dict[tuple[str, date], list[str]] = defaultdict(list)
    for ev in dispatch_events:
        if ev["technician_id"] is None:
            continue
        if ev["event_type"] != "complete":
            continue
        key = (ev["technician_id"], _date_of(ev["event_ts"]))
        completed_jobs_per_key[key].append(ev["job_id"])

    rolls_per_key: dict[tuple[str, date], list[dict[str, Any]]] = defaultdict(list)
    for tr in truck_rolls:
        if tr.get("deleted_at") is not None:
            continue
        key = (tr["technician_id"], _date_of(tr["dispatch_ts"]))
        rolls_per_key[key].append(tr)

    reviews_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in reviews:
        if r.get("deleted_at") is not None:
            continue
        reviews_by_job[r["job_id"]].append(r)

    start_date = today - timedelta(days=days - 1)
    out: list[dict[str, Any]] = []
    for tech in current_techs:
        tech_id = tech["technician_id"]
        for d_offset in range(days):
            day = start_date + timedelta(days=d_offset)
            key = (tech_id, day)

            completed = completed_jobs_per_key.get(key, [])
            jobs_completed = len(completed)

            revenue = Decimal("0.00")
            for jid in completed:
                job = jobs_by_id.get(jid)
                if job is None:
                    continue
                billed = job.get("total_billed_usd")
                if billed is not None:
                    revenue += billed

            rolls = rolls_per_key.get(key, [])
            billable_seconds = 0.0
            for tr in rolls:
                if tr["return_ts"] is None:
                    continue
                billable_seconds += (
                    tr["return_ts"] - tr["dispatch_ts"]
                ).total_seconds()
            billable_hours = billable_seconds / 3600.0
            if billable_hours > 9999.99:
                billable_hours = 9999.99

            pulled_counter: Counter[str] = Counter()
            returned_counter: Counter[str] = Counter()
            for tr in rolls:
                pulled_counter.update(tr.get("parts_pulled") or [])
                returned_counter.update(tr.get("parts_returned") or [])
            consumed = pulled_counter - returned_counter
            parts_cost_total = Decimal("0.00")
            for sku, qty in consumed.items():
                cost = parts_cost.get(sku)
                if cost is not None:
                    parts_cost_total += cost * qty

            ratings: list[int] = []
            for jid in completed:
                for r in reviews_by_job.get(jid, []):
                    ratings.append(r["rating"])
            sat: Decimal | None
            if ratings:
                sat = Decimal(f"{sum(ratings) / len(ratings):.2f}")
            else:
                sat = None

            idle = max(0.0, 8.0 - billable_hours)

            out.append({
                "technician_id": tech_id,
                "utilization_date": day,
                "jobs_completed": jobs_completed,
                "billable_hours": Decimal(f"{billable_hours:.2f}"),
                "revenue_generated_usd": Decimal(f"{revenue:.2f}"),
                "customer_satisfaction_avg": sat,
                "parts_consumed_cost_usd": Decimal(f"{parts_cost_total:.2f}"),
                "idle_hours": Decimal(f"{idle:.2f}"),
            })

    return out


def _date_of(ts: Any) -> date:
    if isinstance(ts, datetime):
        return ts.date()
    if isinstance(ts, date):
        return ts
    raise TypeError(f"unexpected timestamp type: {type(ts)!r}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    inputs: dict[str, list[dict[str, Any]]] = {}
    for name in (
        "technician", "dispatch_event", "service_job",
        "truck_roll", "parts_inventory", "review",
    ):
        path = args.input / f"{name}.parquet"
        if not path.exists():
            raise SystemExit(
                f"error: {path} missing; run generate.py first",
            )
        logger.info("reading %s", path)
        inputs[name] = pq.read_table(path).to_pylist()

    rows = build_utilization(
        technicians=inputs["technician"],
        dispatch_events=inputs["dispatch_event"],
        service_jobs=inputs["service_job"],
        truck_rolls=inputs["truck_roll"],
        parts_inventory=inputs["parts_inventory"],
        reviews=inputs["review"],
        days=args.days,
        today=args.today,
    )

    out_path = args.output / "technician_utilization_daily.parquet"
    args.output.mkdir(parents=True, exist_ok=True)
    write_parquet("technician_utilization_daily", rows, out_path)
    logger.info("wrote %s rows=%d", out_path, len(rows))
    return 0


def write_parquet(
    table_name: str, rows: list[dict[str, Any]], path: Path,
) -> None:
    schema = SCHEMAS[table_name]
    columns: dict[str, list[Any]] = {field.name: [] for field in schema}
    for row in rows:
        for field in schema:
            columns[field.name].append(row.get(field.name))
    arrays = [
        pa.array(columns[field.name], type=field.type) for field in schema
    ]
    arrow_table = pa.Table.from_arrays(arrays, schema=schema)
    pq.write_table(arrow_table, path, compression="snappy")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate technician_utilization_daily rollups (ADR-008).",
    )
    p.add_argument("--input", type=Path, default=Path("./output"),
                   help="Directory containing parquet output from generate.py.")
    p.add_argument("--output", type=Path, default=Path("./output"))
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--today", type=date.fromisoformat,
                   default=date.today(),
                   help="Anchor date. Default: today.")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())

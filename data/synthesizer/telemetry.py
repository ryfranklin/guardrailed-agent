"""Equipment telemetry daily rollup generator (ADR-008).

predicted_failure_30d is a synthetic score, not a trained model. See ADR-008
open items.

The score is computed as a deterministic function of:
  base               = 0.05 (noise floor)
  + risk_warranty    = 0.30 if warranty_status != "active" else 0.00
  + risk_efficiency  = 0.20 * (1 - efficiency_index / 100)
  + risk_fault       = min(0.20, 0.05 * fault_code_count)
  + risk_age         = min(0.20, age_years / 15 * 0.20)
  + risk_service     = 0.10 if last_service_age_days > 365 else 0.00
  + small jitter
clamped to [0.0001, 0.9999] to fit decimal128(5, 4).

Seasonality: Phoenix-shaped. Cooling demand peaks Jun-Aug, heating demand
peaks Dec-Feb. Equipment type drives which curve dominates:
  hvac_central      -> cooling-only (high summer cycle_count)
  furnace           -> heating-only (high winter cycle_count)
  mini_split        -> dual-mode, weighted toward whichever season is hotter
  heat_pump         -> dual-mode, peaks both summer and winter
  water_heater      -> nearly flat year-round, slight winter bump

Heat pumps in winter generate a higher fault_code_count baseline than other
equipment (defrost cycle stress) per the ADR.

CLI:
  python -m data.synthesizer.telemetry --input ./out --output ./out \\
      --seed 42 --days 365

Library:
  build_telemetry(rng, equipment_rows, days, today)
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .schemas import SCHEMAS

logger = logging.getLogger("synth.telemetry")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


COOLING_DEMAND_BY_MONTH = {
    1: 0.10, 2: 0.10, 3: 0.20, 4: 0.40,
    5: 0.65, 6: 0.92, 7: 1.00, 8: 1.00,
    9: 0.85, 10: 0.55, 11: 0.25, 12: 0.10,
}

HEATING_DEMAND_BY_MONTH = {
    1: 0.85, 2: 0.70, 3: 0.30, 4: 0.10,
    5: 0.05, 6: 0.00, 7: 0.00, 8: 0.00,
    9: 0.05, 10: 0.15, 11: 0.45, 12: 0.80,
}


def _equipment_usage(equipment_type: str, month: int) -> float:
    """Fraction of full-load duty cycle for the given month, 0.0 to ~1.2."""
    cool = COOLING_DEMAND_BY_MONTH[month]
    heat = HEATING_DEMAND_BY_MONTH[month]
    if equipment_type == "hvac_central":
        return cool
    if equipment_type == "furnace":
        return heat
    if equipment_type == "mini_split":
        return max(cool, heat) * 0.85
    if equipment_type == "heat_pump":
        return cool + heat
    if equipment_type == "water_heater":
        return 0.55 + 0.15 * heat
    return 0.4


def _base_cycles_per_day(equipment_type: str) -> float:
    if equipment_type == "hvac_central":
        return 55.0
    if equipment_type == "furnace":
        return 28.0
    if equipment_type == "mini_split":
        return 38.0
    if equipment_type == "heat_pump":
        return 32.0
    if equipment_type == "water_heater":
        return 14.0
    return 20.0


def build_telemetry(
    rng: random.Random,
    equipment_rows: list[dict[str, Any]],
    days: int,
    today: date,
) -> list[dict[str, Any]]:
    """Generate one telemetry row per (equipment, day) for the window.

    Filters to is_current=True equipment so SCD2 history doesn't multiply rows.
    """
    current_equipment = [
        e for e in equipment_rows if e.get("is_current", True)
    ]
    start_date = today - timedelta(days=days - 1)

    out: list[dict[str, Any]] = []
    for eq in current_equipment:
        eq_type = eq["equipment_type"]
        install_date = eq["install_date"]
        warranty_status = eq["warranty_status"]
        last_service_anchor = rng.randint(0, 540)

        base_cycles = _base_cycles_per_day(eq_type)

        for d_offset in range(days):
            day = start_date + timedelta(days=d_offset)
            month = day.month
            usage = _equipment_usage(eq_type, month)

            cycle_count = max(0, int(
                base_cycles * usage * rng.uniform(0.6, 1.25)
            ))
            runtime_hours = max(0.0, min(
                24.0,
                cycle_count * rng.uniform(0.05, 0.18),
            ))

            age_days = max(0, (day - install_date).days)
            age_years = age_days / 365.25

            fault_lambda = 0.04 + 0.001 * age_years * 12 + 0.05 * usage
            if eq_type == "heat_pump" and month in (12, 1, 2):
                fault_lambda += 0.06
            fault_code_count = _poisson(rng, fault_lambda)

            efficiency_index = max(0, min(
                100,
                int(round(
                    100 - age_years * 2.8 - rng.uniform(0, 4)
                    - (3 if warranty_status == "void" else 0),
                )),
            ))

            last_service_age_days = (last_service_anchor + d_offset) % 720

            score = 0.05
            if warranty_status != "active":
                score += 0.30
            score += 0.20 * (1.0 - efficiency_index / 100.0)
            score += min(0.20, 0.05 * fault_code_count)
            score += min(0.20, age_years / 15.0 * 0.20)
            if last_service_age_days > 365:
                score += 0.10
            score += rng.uniform(-0.02, 0.02)
            score = max(0.0001, min(0.9999, score))

            out.append({
                "equipment_id": eq["equipment_id"],
                "telemetry_date": day,
                "runtime_hours": Decimal(f"{runtime_hours:.2f}"),
                "cycle_count": cycle_count,
                "fault_code_count": fault_code_count,
                "efficiency_index": efficiency_index,
                "predicted_failure_30d": Decimal(f"{score:.4f}"),
                "last_service_age_days": last_service_age_days,
            })

    return out


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    rng = random.Random(args.seed)

    equipment_path = args.input / "equipment.parquet"
    if not equipment_path.exists():
        raise SystemExit(f"error: {equipment_path} missing; run generate.py first")

    logger.info("reading %s", equipment_path)
    eq_rows = _parquet_to_dicts(equipment_path)

    today = args.today
    logger.info(
        "building equipment_telemetry_daily for %d days × %d units",
        args.days, sum(1 for e in eq_rows if e.get("is_current", True)),
    )
    rows = build_telemetry(rng, eq_rows, args.days, today)

    out_path = args.output / "equipment_telemetry_daily.parquet"
    args.output.mkdir(parents=True, exist_ok=True)
    write_parquet("equipment_telemetry_daily", rows, out_path)
    logger.info("wrote %s rows=%d", out_path, len(rows))
    return 0


def _parquet_to_dicts(path: Path) -> list[dict[str, Any]]:
    table = pq.read_table(path)
    return table.to_pylist()


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


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate equipment_telemetry_daily rollups (ADR-008).",
    )
    p.add_argument("--input", type=Path, default=Path("./output"),
                   help="Directory containing equipment.parquet from generate.py.")
    p.add_argument("--output", type=Path, default=Path("./output"))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--today", type=date.fromisoformat,
                   default=date.today(),
                   help="Anchor date. Default: today.")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())

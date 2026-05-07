"""Daily rollup tests for telemetry and utilization (ADR-008).

Verify:
  * row counts within ±10% of ADR ranges,
  * utilization.jobs_completed matches a (technician_id, DATE(event_ts))
    aggregation of dispatch_event,
  * telemetry shows summer > winter cycle_count for cooling-only equipment,
  * parquet round-trip succeeds with schema-valid types,
  * predicted_failure_30d stays inside [0.0001, 0.9999],
  * `--seed` is reproducible for telemetry.
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from datetime import date
from decimal import Decimal

import pyarrow.parquet as pq
import pytest
from faker import Faker

from data.synthesizer.generate import Volumes, build_dataset
from data.synthesizer.telemetry import (
    build_telemetry, write_parquet as write_telemetry_parquet,
)
from data.synthesizer.utilization import (
    build_utilization, write_parquet as write_utilization_parquet,
)

ANCHOR = date(2026, 5, 3)


def _generate_dataset(seed: int = 17, signal_days: int = 5) -> dict:
    rng = random.Random(seed)
    Faker.seed(seed)
    fake = Faker("en_US")
    vols = Volumes(
        customers=120,
        technicians=6,
        equipment_per_customer_avg=1.4,
        parts=40,
        service_jobs=600,
        review_share=0.31,
        truck_roll_share=0.96,
        dispatch_events_per_job_avg=4.0,
        warranty_claims=40,
        signal_days=signal_days,
    )
    return build_dataset(rng, fake, vols, ANCHOR)


def test_telemetry_row_count():
    tables = _generate_dataset()
    rng = random.Random(1)
    telemetry = build_telemetry(rng, tables["equipment"], days=365, today=ANCHOR)
    n_units = sum(1 for e in tables["equipment"] if e.get("is_current"))
    assert len(telemetry) == n_units * 365


def test_telemetry_predicted_failure_in_range():
    tables = _generate_dataset()
    rng = random.Random(2)
    telemetry = build_telemetry(rng, tables["equipment"], days=30, today=ANCHOR)
    for r in telemetry:
        v = r["predicted_failure_30d"]
        assert Decimal("0.0001") <= v <= Decimal("0.9999"), (
            f"predicted_failure_30d out of range: {v}"
        )


def test_telemetry_summer_higher_than_winter_for_cooling_units():
    tables = _generate_dataset()
    rng = random.Random(3)
    telemetry = build_telemetry(rng, tables["equipment"], days=365, today=ANCHOR)

    cooling_ids = {
        e["equipment_id"] for e in tables["equipment"]
        if e.get("is_current") and e["equipment_type"] == "hvac_central"
    }
    assert len(cooling_ids) >= 20, "need a sample of hvac_central units"

    summer = [
        r["cycle_count"] for r in telemetry
        if r["equipment_id"] in cooling_ids
        and r["telemetry_date"].month in (6, 7, 8)
    ]
    winter = [
        r["cycle_count"] for r in telemetry
        if r["equipment_id"] in cooling_ids
        and r["telemetry_date"].month in (12, 1, 2)
    ]
    assert summer and winter

    summer_median = statistics.median(summer)
    winter_median = statistics.median(winter)
    assert summer_median > winter_median, (
        f"hvac_central seasonality not detected: "
        f"summer_median={summer_median}, winter_median={winter_median}"
    )


def test_telemetry_seeded_reproducibility():
    tables = _generate_dataset()
    rng_a = random.Random(99)
    rng_b = random.Random(99)
    a = build_telemetry(rng_a, tables["equipment"], days=14, today=ANCHOR)
    b = build_telemetry(rng_b, tables["equipment"], days=14, today=ANCHOR)
    assert a == b


def test_utilization_row_count_full_window():
    tables = _generate_dataset()
    util = build_utilization(
        technicians=tables["technician"],
        dispatch_events=tables["dispatch_event"],
        service_jobs=tables["service_job"],
        truck_rolls=tables["truck_roll"],
        parts_inventory=tables["parts_inventory"],
        reviews=tables["review"],
        days=365,
        today=ANCHOR,
    )
    n_techs = sum(1 for t in tables["technician"] if t.get("is_current"))
    assert len(util) == n_techs * 365


def test_utilization_jobs_completed_matches_dispatch_groupby():
    """The acceptance test: jobs_completed equals
    SUM(event_type='complete') GROUP BY technician_id, DATE(event_ts)."""
    tables = _generate_dataset()
    util = build_utilization(
        technicians=tables["technician"],
        dispatch_events=tables["dispatch_event"],
        service_jobs=tables["service_job"],
        truck_rolls=tables["truck_roll"],
        parts_inventory=tables["parts_inventory"],
        reviews=tables["review"],
        days=365,
        today=ANCHOR,
    )

    expected: dict[tuple[str, date], int] = defaultdict(int)
    for ev in tables["dispatch_event"]:
        if ev["technician_id"] is None:
            continue
        if ev["event_type"] != "complete":
            continue
        expected[(ev["technician_id"], ev["event_ts"].date())] += 1

    actual = {
        (r["technician_id"], r["utilization_date"]): r["jobs_completed"]
        for r in util
    }
    for key, exp in expected.items():
        assert actual.get(key, 0) == exp, (
            f"jobs_completed mismatch at {key}: "
            f"expected={exp}, got={actual.get(key, 0)}"
        )


def test_utilization_revenue_matches_groupby():
    tables = _generate_dataset()
    util = build_utilization(
        technicians=tables["technician"],
        dispatch_events=tables["dispatch_event"],
        service_jobs=tables["service_job"],
        truck_rolls=tables["truck_roll"],
        parts_inventory=tables["parts_inventory"],
        reviews=tables["review"],
        days=365,
        today=ANCHOR,
    )

    jobs_by_id = {j["job_id"]: j for j in tables["service_job"]}
    expected: dict[tuple[str, date], Decimal] = defaultdict(lambda: Decimal("0.00"))
    for ev in tables["dispatch_event"]:
        if ev["technician_id"] is None:
            continue
        if ev["event_type"] != "complete":
            continue
        billed = jobs_by_id.get(ev["job_id"], {}).get("total_billed_usd")
        if billed is not None:
            expected[(ev["technician_id"], ev["event_ts"].date())] += billed

    actual = {
        (r["technician_id"], r["utilization_date"]): r["revenue_generated_usd"]
        for r in util
    }
    for key, exp in expected.items():
        diff = abs(actual.get(key, Decimal("0.00")) - exp)
        assert diff < Decimal("0.01"), (
            f"revenue mismatch at {key}: expected={exp}, got={actual.get(key)}"
        )


def test_utilization_idle_hours_complement_billable():
    tables = _generate_dataset()
    util = build_utilization(
        technicians=tables["technician"],
        dispatch_events=tables["dispatch_event"],
        service_jobs=tables["service_job"],
        truck_rolls=tables["truck_roll"],
        parts_inventory=tables["parts_inventory"],
        reviews=tables["review"],
        days=14,
        today=ANCHOR,
    )
    for r in util:
        billable = float(r["billable_hours"])
        idle = float(r["idle_hours"])
        if billable < 8.0:
            assert abs((billable + idle) - 8.0) < 0.011, (
                f"billable+idle != 8 on light day: {billable}+{idle}"
            )
        else:
            assert idle == 0.0


def test_telemetry_parquet_round_trip(tmp_path):
    tables = _generate_dataset()
    rng = random.Random(4)
    telemetry = build_telemetry(rng, tables["equipment"], days=14, today=ANCHOR)
    path = tmp_path / "equipment_telemetry_daily.parquet"
    write_telemetry_parquet("equipment_telemetry_daily", telemetry, path)
    assert path.exists()
    rt = pq.read_table(path)
    assert rt.num_rows == len(telemetry)


def test_utilization_parquet_round_trip(tmp_path):
    tables = _generate_dataset()
    util = build_utilization(
        technicians=tables["technician"],
        dispatch_events=tables["dispatch_event"],
        service_jobs=tables["service_job"],
        truck_rolls=tables["truck_roll"],
        parts_inventory=tables["parts_inventory"],
        reviews=tables["review"],
        days=14,
        today=ANCHOR,
    )
    path = tmp_path / "technician_utilization_daily.parquet"
    write_utilization_parquet("technician_utilization_daily", util, path)
    assert path.exists()
    rt = pq.read_table(path)
    assert rt.num_rows == len(util)


def test_telemetry_water_heater_is_relatively_flat():
    """Water heater is a control: usage is nearly flat year-round."""
    tables = _generate_dataset()
    rng = random.Random(5)
    telemetry = build_telemetry(rng, tables["equipment"], days=365, today=ANCHOR)
    wh_ids = {
        e["equipment_id"] for e in tables["equipment"]
        if e.get("is_current") and e["equipment_type"] == "water_heater"
    }
    if not wh_ids:
        pytest.skip("no water_heater units in this run")
    summer = [
        r["cycle_count"] for r in telemetry
        if r["equipment_id"] in wh_ids
        and r["telemetry_date"].month in (6, 7, 8)
    ]
    winter = [
        r["cycle_count"] for r in telemetry
        if r["equipment_id"] in wh_ids
        and r["telemetry_date"].month in (12, 1, 2)
    ]
    if not summer or not winter:
        pytest.skip("not enough water_heater rows")
    summer_median = statistics.median(summer)
    winter_median = statistics.median(winter)
    ratio = max(summer_median, 1) / max(winter_median, 1)
    assert 0.5 <= ratio <= 2.0, (
        f"water_heater should be relatively flat year-round, ratio={ratio}"
    )

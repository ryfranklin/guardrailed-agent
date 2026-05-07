"""Synthesizer contract tests for the HVAC home-services dataset (ADR-008).

Run the synthesizer with small volumes and verify:
  * every expected table is produced and non-empty,
  * FK consistency holds across the full DAG,
  * SCD2 invariants hold (one is_current=TRUE per natural key, no overlapping
    effective windows, non-current rows have effective_to set),
  * soft-delete invariants hold (deleted_at NULL XOR deleted_by NOT NULL is
    satisfied — i.e., both NULL or both NOT NULL),
  * `--seed` is reproducible across runs,
  * the DAG validator raises on injected violations.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import pytest
from faker import Faker

from data.synthesizer.generate import (
    Volumes,
    build_dataset,
    validate_dag,
)

ANCHOR = date(2026, 5, 3)


def _small_volumes() -> Volumes:
    return Volumes(
        customers=80,
        technicians=8,
        equipment_per_customer_avg=1.4,
        parts=40,
        service_jobs=300,
        review_share=0.25,
        truck_roll_share=0.85,
        dispatch_events_per_job_avg=4.0,
        warranty_claims=30,
        signal_days=5,
    )


@pytest.fixture(scope="module")
def small_dataset():
    rng = random.Random(7)
    Faker.seed(7)
    fake = Faker("en_US")
    tables = build_dataset(rng, fake, _small_volumes(), ANCHOR)
    return tables


EXPECTED_TABLES = {
    "customer", "technician", "equipment", "service_job", "review",
    "customer_signal_daily", "parts_inventory", "dispatch_event",
    "truck_roll", "warranty_claim",
}


def test_all_expected_tables_present(small_dataset):
    assert set(small_dataset.keys()) == EXPECTED_TABLES


def test_every_table_has_rows(small_dataset):
    for name, rows in small_dataset.items():
        assert len(rows) > 0, f"{name} produced zero rows"


def test_fk_customer_to_equipment(small_dataset):
    customer_ids = {r["customer_id"] for r in small_dataset["customer"]}
    for r in small_dataset["equipment"]:
        assert r["customer_id"] in customer_ids


def test_fk_service_job_chain(small_dataset):
    customer_ids = {r["customer_id"] for r in small_dataset["customer"]}
    tech_ids = {r["technician_id"] for r in small_dataset["technician"]}
    equipment_ids = {r["equipment_id"] for r in small_dataset["equipment"]}
    for r in small_dataset["service_job"]:
        assert r["customer_id"] in customer_ids
        assert r["technician_id"] is None or r["technician_id"] in tech_ids
        assert r["equipment_id"] is None or r["equipment_id"] in equipment_ids


def test_fk_dispatch_event_to_service_job(small_dataset):
    job_ids = {r["job_id"] for r in small_dataset["service_job"]}
    tech_ids = {r["technician_id"] for r in small_dataset["technician"]}
    for r in small_dataset["dispatch_event"]:
        assert r["job_id"] in job_ids
        assert r["technician_id"] is None or r["technician_id"] in tech_ids


def test_fk_truck_roll_chain(small_dataset):
    job_ids = {r["job_id"] for r in small_dataset["service_job"]}
    tech_ids = {r["technician_id"] for r in small_dataset["technician"]}
    equipment_ids = {r["equipment_id"] for r in small_dataset["equipment"]}
    skus = {r["sku"] for r in small_dataset["parts_inventory"]}
    for r in small_dataset["truck_roll"]:
        assert r["job_id"] in job_ids
        assert r["technician_id"] in tech_ids
        assert r["equipment_id"] is None or r["equipment_id"] in equipment_ids
        for sku in r["parts_pulled"] or []:
            assert sku in skus
        for sku in r["parts_returned"] or []:
            assert sku in skus


def test_fk_warranty_claim_chain(small_dataset):
    customer_ids = {r["customer_id"] for r in small_dataset["customer"]}
    equipment_ids = {r["equipment_id"] for r in small_dataset["equipment"]}
    for r in small_dataset["warranty_claim"]:
        assert r["equipment_id"] in equipment_ids
        assert r["customer_id"] in customer_ids


def test_fk_review_chain(small_dataset):
    job_ids = {r["job_id"] for r in small_dataset["service_job"]}
    customer_ids = {r["customer_id"] for r in small_dataset["customer"]}
    for r in small_dataset["review"]:
        assert r["job_id"] in job_ids
        assert r["customer_id"] in customer_ids


def test_fk_customer_signal_to_customer(small_dataset):
    customer_ids = {r["customer_id"] for r in small_dataset["customer"]}
    for r in small_dataset["customer_signal_daily"]:
        assert r["customer_id"] in customer_ids


SCD2_TABLES = [
    ("customer", "customer_id"),
    ("technician", "technician_id"),
    ("equipment", "equipment_id"),
    ("parts_inventory", "sku"),
]


@pytest.mark.parametrize("table,key", SCD2_TABLES)
def test_scd2_exactly_one_current_per_key(small_dataset, table, key):
    by_key = defaultdict(list)
    for r in small_dataset[table]:
        by_key[r[key]].append(r)
    for k, items in by_key.items():
        currents = [r for r in items if r["is_current"]]
        assert len(currents) == 1, (
            f"{table} {key}={k} has {len(currents)} is_current=TRUE rows"
        )
        assert currents[0]["effective_to"] is None, (
            f"{table} {key}={k} current row has non-NULL effective_to"
        )


@pytest.mark.parametrize("table,key", SCD2_TABLES)
def test_scd2_windows_non_overlapping(small_dataset, table, key):
    by_key = defaultdict(list)
    for r in small_dataset[table]:
        by_key[r[key]].append(r)
    for k, items in by_key.items():
        items_sorted = sorted(items, key=lambda r: r["effective_from"])
        for i in range(len(items_sorted) - 1):
            cur = items_sorted[i]
            nxt = items_sorted[i + 1]
            assert cur["effective_to"] is not None, (
                f"{table} {key}={k}: non-current row missing effective_to"
            )
            assert cur["effective_to"] <= nxt["effective_from"], (
                f"{table} {key}={k}: overlapping windows "
                f"({cur['effective_to']} > {nxt['effective_from']})"
            )


@pytest.mark.parametrize(
    "table",
    ["service_job", "review", "truck_roll", "warranty_claim"],
)
def test_soft_delete_xor_invariant(small_dataset, table):
    rows = small_dataset[table]
    for r in rows:
        a = r["deleted_at"]
        b = r["deleted_by"]
        assert (a is None) == (b is None), (
            f"{table}: deleted_at={a!r} deleted_by={b!r} violates XOR"
        )


def test_soft_delete_actually_deletes_some_jobs(small_dataset):
    deleted = [
        r for r in small_dataset["service_job"]
        if r["deleted_at"] is not None
    ]
    assert len(deleted) > 0, (
        "expected at least one soft-deleted service_job at this volume"
    )


def test_validator_passes_on_clean_dataset(small_dataset):
    validate_dag(small_dataset)


def test_validator_catches_fk_violation(small_dataset):
    bad = {k: list(v) for k, v in small_dataset.items()}
    bad["service_job"] = list(bad["service_job"])
    bad["service_job"].append({
        **bad["service_job"][0],
        "job_id": "synthetic-bad-job",
        "customer_id": "ghost-customer-id",
    })
    with pytest.raises(ValueError, match="customer_id missing"):
        validate_dag(bad)


def test_validator_catches_scd2_overlap():
    rng = random.Random(11)
    Faker.seed(11)
    fake = Faker("en_US")
    tables = build_dataset(rng, fake, _small_volumes(), ANCHOR)
    customers = list(tables["customer"])
    target_id = customers[0]["customer_id"]
    overlap_a = dict(customers[0])
    overlap_b = dict(customers[0])
    overlap_a.update({
        "effective_from": datetime(2025, 1, 1),
        "effective_to": datetime(2025, 6, 1),
        "is_current": False,
    })
    overlap_b.update({
        "effective_from": datetime(2025, 5, 1),
        "effective_to": datetime(2025, 12, 1),
        "is_current": False,
    })
    new_customers = [
        r for r in customers if r["customer_id"] != target_id
    ]
    new_customers.append(customers[0])
    new_customers.append(overlap_a)
    new_customers.append(overlap_b)
    tables_bad = dict(tables)
    tables_bad["customer"] = new_customers
    with pytest.raises(ValueError):
        validate_dag(tables_bad)


def test_validator_catches_soft_delete_violation(small_dataset):
    bad = {k: list(v) for k, v in small_dataset.items()}
    bad["service_job"] = list(bad["service_job"])
    bad["service_job"][0] = {
        **bad["service_job"][0],
        "deleted_at": datetime(2026, 1, 1),
        "deleted_by": None,
    }
    with pytest.raises(ValueError, match="soft-delete"):
        validate_dag(bad)


def test_seed_reproducibility():
    def run():
        rng = random.Random(99)
        Faker.seed(99)
        fake = Faker("en_US")
        return build_dataset(rng, fake, _small_volumes(), ANCHOR)

    a = run()
    b = run()
    for table in EXPECTED_TABLES:
        assert len(a[table]) == len(b[table]), (
            f"{table} row counts diverged across seeded runs"
        )
        rows_a = a[table]
        rows_b = b[table]
        for ra, rb in zip(rows_a, rows_b, strict=True):
            assert ra == rb, f"{table} row diverged across seeded runs"


def test_scd2_dimensions_have_some_history(small_dataset):
    for table, key in SCD2_TABLES:
        rows = small_dataset[table]
        natural_keys = {r[key] for r in rows}
        if len(rows) > len(natural_keys):
            return
    pytest.fail(
        "no SCD2 table produced any historical versions — "
        "synthesizer is not exercising the SCD2 path",
    )


def test_parquet_round_trip(tmp_path):
    """End-to-end: generate a tiny dataset and write all parquet files."""
    import pyarrow.parquet as pq

    from data.synthesizer.generate import _write_parquet

    rng = random.Random(3)
    Faker.seed(3)
    fake = Faker("en_US")
    vols = Volumes(
        customers=20, technicians=4, equipment_per_customer_avg=1.2,
        parts=10, service_jobs=40, review_share=0.3,
        truck_roll_share=0.8, dispatch_events_per_job_avg=3.0,
        warranty_claims=5, signal_days=2,
    )
    tables = build_dataset(rng, fake, vols, ANCHOR)
    validate_dag(tables)

    out_dir: Path = tmp_path
    for name, rows in tables.items():
        path = out_dir / f"{name}.parquet"
        _write_parquet(name, rows, path)
        assert path.exists()
        roundtripped = pq.read_table(path)
        assert roundtripped.num_rows == len(rows)

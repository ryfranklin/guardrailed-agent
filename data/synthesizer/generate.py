"""HVAC home-services synthetic dataset generator (ADR-008).

Pipeline:
  1. Generate base entities (customer, technician, parts_inventory, equipment).
  2. Generate transactional facts referencing those entities.
  3. Expand SCD2 dimensions with realistic change rates.
  4. Annotate soft-delete columns on configured fact tables.
  5. Validate the FK + SCD2 + soft-delete DAG.
  6. Write Parquet files. Optionally upload to S3.

CTAS into Iceberg + LF-Tag application live in a separate script (see
ADR-003); this module only produces Parquet. Reproducible from --seed.

Note on SCD2 row counts: ADR-008 names change rates (e.g., customer
phone/email ~2x/year) that, when applied to a 12-month window over 5,000
customers, would push totals well past the ADR's ~5,000-row table size.
We resolve by tuning expected-changes-in-window per dimension so totals
land within ADR ranges; the change-rate semantic survives but the
simulation window is short. See SCD2_PLANS below.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import math
import random
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa
import pyarrow.parquet as pq
from faker import Faker

from .schemas import SCHEMAS

logger = logging.getLogger("synth")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


SERVICE_REGIONS = ["north-phoenix", "tempe-mesa", "west-valley"]
PHX_CITIES = {
    "north-phoenix": ["Phoenix", "Glendale", "Peoria", "Sun City"],
    "tempe-mesa": ["Tempe", "Mesa", "Chandler", "Gilbert"],
    "west-valley": ["Goodyear", "Avondale", "Buckeye", "Surprise"],
}
PHX_ZIP_PREFIXES = ["850", "851", "852", "853"]

CUSTOMER_TYPES = [("residential", 78), ("commercial", 22)]
CUSTOMER_TIERS = [("standard", 60), ("premium", 30), ("commercial", 10)]
EQUIPMENT_TYPES = [
    ("hvac_central", 50),
    ("water_heater", 22),
    ("mini_split", 13),
    ("furnace", 10),
    ("heat_pump", 5),
]
EQUIPMENT_MANUFACTURERS = [
    "Trane", "Carrier", "Lennox", "Goodman", "Rheem",
    "AO Smith", "Mitsubishi", "Daikin", "York",
]
WARRANTY_STATUSES = [("active", 60), ("expired", 35), ("void", 5)]
EQUIPMENT_TIERS = [("standard", 70), ("premium", 25), ("ultra", 5)]
TECH_CERTIFICATIONS = [
    "EPA-608", "NATE", "Master HVAC", "Plumbing-L1",
    "Electrical-L1", "Refrigeration", "Manometer Certified",
]
TECH_EMPLOYMENT = [("active", 92), ("on_leave", 5), ("terminated", 3)]

JOB_TYPES = [("repair", 40), ("tune_up", 30), ("install", 15), ("emergency", 15)]
JOB_STATUSES = [
    ("completed", 78), ("scheduled", 8), ("in_progress", 4), ("cancelled", 10),
]

DISPATCH_FLOWS = {
    "completed": ["assigned", "en_route", "on_site", "complete"],
    "in_progress": ["assigned", "en_route", "on_site"],
    "scheduled": ["assigned"],
    "cancelled": ["assigned", "cancelled"],
}

NEXT_BEST_ACTIONS = [
    "recall_due", "upsell_maintenance_plan",
    "quote_replacement", "proactive_outreach",
]

PARTS_CATEGORIES = [
    "filter", "compressor", "blower_motor", "thermostat",
    "refrigerant", "capacitor", "contactor", "valve", "duct", "sensor",
]
PARTS_SUPPLIERS = [
    "AcmeHVAC Distributors", "AZ Wholesale Supply",
    "BlueRidge Parts Co", "Phoenix Components LLC",
]
SUPPLIER_TERMS = [
    "Net30 / 2% prepay", "Net60 / volume tier 3",
    "Net15 / no prepay", "Net30 / consignment",
]
WAREHOUSES = ["WH-PHX-01", "WH-PHX-02", "WH-MESA-01"]

TRUCK_ROLL_OUTCOMES = [
    ("resolved", 75), ("parts_needed", 15), ("escalated", 7), ("noshow", 3),
]
WARRANTY_CLAIM_STATUSES = [
    ("paid", 35), ("approved", 25), ("filed", 15),
    ("denied", 15), ("in_review", 10),
]
WARRANTY_CLAIM_REASONS = [
    "compressor_failure", "premature_corrosion", "control_board_fault",
    "refrigerant_leak", "blower_motor_failure", "thermostat_defect",
]

DELETED_BY_ACTORS = [
    "system:retention-policy",
    "ops-admin@example.com",
    "compliance@example.com",
    "dispatcher-lead@example.com",
]

REVIEW_TEMPLATES = [
    "Tech was on time and walked me through the diagnosis.",
    "Showed up late but the AC works again. Three stars for the wait.",
    "Excellent work. Will book again next year for tune-up.",
    "Job completed but the area was left messy.",
    "Quick repair, fair price. No upsells.",
    "Polite tech, clear pricing. The water heater install took 4 hours.",
    "Charged me for a part I'm not sure I needed. Asking my neighbor.",
    "Saved us during a 110-degree week. Thank you.",
]


@dataclasses.dataclass
class Volumes:
    customers: int = 4_800
    technicians: int = 28
    equipment_per_customer_avg: float = 1.45
    parts: int = 760
    service_jobs: int = 25_000
    review_share: float = 0.31
    truck_roll_share: float = 0.96
    dispatch_events_per_job_avg: float = 4.8
    warranty_claims: int = 1_800
    signal_days: int = 30


@dataclasses.dataclass
class SCD2Plan:
    """Per-table SCD2 expansion parameters.

    ``expected_changes`` is the average number of historical changes per
    natural key over the simulated history window. See module docstring
    for why these are tuned below the ADR's literal change rates.
    """

    window_days: int
    expected_changes: float


SCD2_PLANS: dict[str, SCD2Plan] = {
    "customer": SCD2Plan(window_days=180, expected_changes=0.05),
    "technician": SCD2Plan(window_days=540, expected_changes=0.18),
    "equipment": SCD2Plan(window_days=365, expected_changes=0.06),
    "parts_inventory": SCD2Plan(window_days=365, expected_changes=0.06),
}


@dataclasses.dataclass
class Config:
    output_dir: Path
    bucket: str | None
    region: str
    seed: int
    today: date
    volumes: Volumes
    upload: bool


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = _config_from_args(args)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(cfg.seed)
    Faker.seed(cfg.seed)
    fake = Faker("en_US")

    logger.info(
        "generating ADR-008 HVAC dataset seed=%d volumes=%s",
        cfg.seed, cfg.volumes,
    )

    tables = build_dataset(rng, fake, cfg.volumes, cfg.today)
    validate_dag(tables)

    for name, rows in tables.items():
        path = cfg.output_dir / f"{name}.parquet"
        _write_parquet(name, rows, path)
        logger.info("wrote %s rows=%d", name, len(rows))

    if cfg.upload:
        if not cfg.bucket:
            raise SystemExit("error: --bucket required when --upload is set")
        _upload_parquet(cfg.output_dir, cfg.bucket, list(tables))

    logger.info("done")
    return 0


def build_dataset(
    rng: random.Random,
    fake: Faker,
    volumes: Volumes,
    today: date,
) -> dict[str, list[dict[str, Any]]]:
    """Generate every table. Single entrypoint reused by tests."""
    customers = _gen_customers_base(rng, fake, volumes.customers)
    technicians = _gen_technicians_base(rng, fake, volumes.technicians, today)
    parts = _gen_parts_inventory_base(rng, volumes.parts)
    equipment = _gen_equipment_base(
        rng, customers, volumes.equipment_per_customer_avg, today,
    )
    service_jobs = _gen_service_jobs(
        rng, fake, customers, equipment, technicians,
        volumes.service_jobs, today,
    )
    dispatch_events = _gen_dispatch_events(
        rng, service_jobs, volumes.dispatch_events_per_job_avg,
    )
    truck_rolls = _gen_truck_rolls(
        rng, service_jobs, technicians, parts,
        volumes.truck_roll_share,
    )
    reviews = _gen_reviews(
        rng, service_jobs, volumes.review_share, today,
    )
    warranty_claims = _gen_warranty_claims(
        rng, equipment, customers, volumes.warranty_claims, today,
    )
    signals = _gen_customer_signals(
        rng, customers, volumes.signal_days, today,
    )

    customers_full = _expand_customer_scd2(rng, fake, customers, today)
    technicians_full = _expand_technician_scd2(rng, technicians, today)
    parts_full = _expand_parts_scd2(rng, parts, today)
    equipment_full = _expand_equipment_scd2(rng, equipment, today)

    _apply_soft_delete(rng, service_jobs, rate=0.03,
                       anchor_field="scheduled_date", today=today)
    _apply_soft_delete(rng, reviews, rate=0.005,
                       anchor_field="review_date", today=today)
    _apply_soft_delete(rng, truck_rolls, rate=0.005,
                       anchor_field="dispatch_ts", today=today)
    _apply_soft_delete(rng, warranty_claims, rate=0.005,
                       anchor_field="claim_date", today=today)

    return {
        "customer": customers_full,
        "technician": technicians_full,
        "equipment": equipment_full,
        "parts_inventory": parts_full,
        "service_job": service_jobs,
        "review": reviews,
        "truck_roll": truck_rolls,
        "warranty_claim": warranty_claims,
        "dispatch_event": dispatch_events,
        "customer_signal_daily": signals,
    }


def _gen_customers_base(
    rng: random.Random, fake: Faker, n: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _ in range(n):
        region = rng.choice(SERVICE_REGIONS)
        customer_type = _weighted(rng, CUSTOMER_TYPES)
        first = fake.first_name()
        last = fake.last_name()
        out.append({
            "customer_id": _gen_id(rng),
            "customer_type": customer_type,
            "service_tier": _weighted(
                rng,
                [("commercial", 100)] if customer_type == "commercial"
                else CUSTOMER_TIERS,
            ),
            "service_region": region,
            "first_name": first,
            "last_name": last,
            "email": _make_email(rng, first, last),
            "phone": fake.numerify("###-###-####"),
            "street_address": fake.street_address(),
            "city": rng.choice(PHX_CITIES[region]),
            "postal_code": _phx_zip(rng),
            "billing_notes": _maybe_billing_note(rng, fake),
        })
    return out


def _gen_technicians_base(
    rng: random.Random, fake: Faker, n: int, today: date,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _ in range(n):
        first = fake.first_name()
        last = fake.last_name()
        cert_count = rng.randint(2, 4)
        certs = rng.sample(TECH_CERTIFICATIONS, cert_count)
        hire_offset_days = rng.randint(60, 365 * 8)
        out.append({
            "technician_id": _gen_id(rng),
            "service_region": rng.choice(SERVICE_REGIONS),
            "certifications": certs,
            "hire_date": today - timedelta(days=hire_offset_days),
            "employment_status": _weighted(rng, TECH_EMPLOYMENT),
            "first_name": first,
            "last_name": last,
            "email": _make_email(rng, first, last, domain="services.example.com"),
            "phone": fake.numerify("###-###-####"),
            "home_address": f"{fake.street_address()}, {rng.choice(PHX_CITIES[rng.choice(SERVICE_REGIONS)])} AZ",
        })
    return out


def _gen_parts_inventory_base(
    rng: random.Random, n: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    used_skus: set[str] = set()
    while len(out) < n:
        cat = rng.choice(PARTS_CATEGORIES)
        sku = f"{cat[:3].upper()}-{rng.randint(10_000, 99_999)}"
        if sku in used_skus:
            continue
        used_skus.add(sku)
        unit_cost = round(rng.uniform(4.0, 1_800.0), 2)
        out.append({
            "sku": sku,
            "part_name": f"{cat.replace('_', ' ').title()} {rng.choice(['MK1', 'MK2', 'Pro', 'OEM', 'XL'])}",
            "category": cat,
            "supplier": rng.choice(PARTS_SUPPLIERS),
            "unit_cost_usd": Decimal(f"{unit_cost:.2f}"),
            "supplier_terms": rng.choice(SUPPLIER_TERMS),
            "qty_on_hand": rng.randint(0, 240),
            "warehouse_id": rng.choice(WAREHOUSES),
        })
    return out


def _gen_equipment_base(
    rng: random.Random,
    customers: list[dict[str, Any]],
    avg_per_customer: float,
    today: date,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in customers:
        n = _poisson(rng, avg_per_customer)
        for _ in range(n):
            install_offset = rng.randint(60, 365 * 12)
            install_date = today - timedelta(days=install_offset)
            warranty_years = rng.choice([1, 2, 5, 10])
            warranty_expiry = install_date + timedelta(days=365 * warranty_years)
            if warranty_expiry < today:
                warranty_status = "expired"
            elif rng.random() < 0.04:
                warranty_status = "void"
            else:
                warranty_status = "active"
            equipment_type = _weighted(rng, EQUIPMENT_TYPES)
            out.append({
                "equipment_id": _gen_id(rng),
                "customer_id": c["customer_id"],
                "equipment_type": equipment_type,
                "manufacturer": rng.choice(EQUIPMENT_MANUFACTURERS),
                "model_number": f"M{rng.randint(1000, 9999)}-{rng.choice(['A', 'B', 'C'])}",
                "serial_number": f"SN{rng.randint(10**9, 10**10 - 1)}",
                "install_date": install_date,
                "warranty_status": warranty_status,
                "warranty_expiry_date": warranty_expiry,
                "service_tier": _weighted(rng, EQUIPMENT_TIERS),
            })
    return out


def _gen_service_jobs(
    rng: random.Random,
    fake: Faker,
    customers: list[dict[str, Any]],
    equipment: list[dict[str, Any]],
    technicians: list[dict[str, Any]],
    n: int,
    today: date,
) -> list[dict[str, Any]]:
    eq_by_customer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in equipment:
        eq_by_customer[e["customer_id"]].append(e)

    techs_by_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in technicians:
        techs_by_region[t["service_region"]].append(t)

    out: list[dict[str, Any]] = []
    for _ in range(n):
        c = rng.choice(customers)
        candidates = eq_by_customer.get(c["customer_id"], [])
        eq = rng.choice(candidates) if candidates and rng.random() < 0.95 else None

        scheduled_offset = rng.randint(0, 365)
        scheduled = today - timedelta(days=scheduled_offset)
        status = _weighted(rng, JOB_STATUSES)
        completed: date | None
        if status == "completed":
            completed = scheduled + timedelta(days=rng.randint(0, 3))
            if completed > today:
                completed = today
        elif status in ("scheduled", "in_progress"):
            completed = None
        else:
            completed = None

        regional_techs = techs_by_region.get(c["service_region"], technicians)
        tech = rng.choice(regional_techs) if regional_techs else None
        tech_id = tech["technician_id"] if tech is not None else None
        if status == "scheduled" and rng.random() < 0.3:
            tech_id = None

        billed: Decimal | None
        if status == "completed":
            base = rng.uniform(85.0, 1_400.0)
            if rng.random() < 0.05:
                base += rng.uniform(2_000.0, 8_000.0)
            billed = Decimal(f"{base:.2f}")
        elif status == "cancelled":
            billed = Decimal("0.00") if rng.random() < 0.7 else None
        else:
            billed = None

        out.append({
            "job_id": _gen_id(rng),
            "customer_id": c["customer_id"],
            "technician_id": tech_id,
            "equipment_id": eq["equipment_id"] if eq is not None else None,
            "job_type": _weighted(rng, JOB_TYPES),
            "scheduled_date": scheduled,
            "completed_date": completed,
            "status": status,
            "total_billed_usd": billed,
            "billing_notes": _maybe_job_billing_note(rng, fake, status),
            "deleted_at": None,
            "deleted_by": None,
        })
    return out


def _gen_dispatch_events(
    rng: random.Random,
    service_jobs: list[dict[str, Any]],
    avg_per_job: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for job in service_jobs:
        flow = list(DISPATCH_FLOWS.get(job["status"], []))
        if not flow:
            continue
        if avg_per_job > len(flow):
            extras = _poisson(rng, avg_per_job - len(flow))
            for _ in range(extras):
                idx = rng.randint(0, len(flow) - 1)
                flow.insert(idx, flow[idx])
        elif avg_per_job < len(flow):
            target = max(1, _poisson(rng, avg_per_job))
            flow = flow[:target]

        scheduled = job["scheduled_date"]
        base_ts = datetime.combine(scheduled, time(hour=8))
        cur_ts = base_ts + timedelta(minutes=rng.randint(0, 240))
        for ev in flow:
            cur_ts = cur_ts + timedelta(minutes=rng.randint(3, 95))
            out.append({
                "event_id": _gen_id(rng),
                "job_id": job["job_id"],
                "technician_id": job["technician_id"],
                "event_ts": cur_ts,
                "event_type": ev,
                "event_notes": None if rng.random() < 0.85
                else f"auto-event {ev}",
            })
    return out


def _gen_truck_rolls(
    rng: random.Random,
    service_jobs: list[dict[str, Any]],
    technicians: list[dict[str, Any]],
    parts: list[dict[str, Any]],
    share: float,
) -> list[dict[str, Any]]:
    sku_pool = [p["sku"] for p in parts]
    techs_by_id = {t["technician_id"]: t for t in technicians}
    out: list[dict[str, Any]] = []
    for job in service_jobs:
        if job["status"] == "scheduled":
            continue
        if rng.random() > share:
            continue
        tech_id = job["technician_id"] or rng.choice(technicians)["technician_id"]
        if tech_id not in techs_by_id:
            tech_id = rng.choice(technicians)["technician_id"]
        scheduled = job["scheduled_date"]
        dispatch_ts = datetime.combine(scheduled, time(hour=8)) + timedelta(
            minutes=rng.randint(15, 360),
        )
        duration_min = rng.randint(45, 360)
        return_ts = dispatch_ts + timedelta(minutes=duration_min)
        miles = round(rng.uniform(2.0, 95.0), 2)
        n_parts = _poisson(rng, 0.9)
        parts_pulled = rng.sample(sku_pool, min(n_parts, len(sku_pool))) \
            if n_parts > 0 else []
        parts_returned: list[str] = [
            sku for sku in parts_pulled if rng.random() < 0.18
        ]
        out.append({
            "truck_roll_id": _gen_id(rng),
            "job_id": job["job_id"],
            "technician_id": tech_id,
            "equipment_id": job["equipment_id"],
            "dispatch_ts": dispatch_ts,
            "return_ts": return_ts,
            "miles_driven": Decimal(f"{miles:.2f}"),
            "parts_pulled": parts_pulled,
            "parts_returned": parts_returned,
            "outcome": _weighted(rng, TRUCK_ROLL_OUTCOMES),
            "deleted_at": None,
            "deleted_by": None,
        })
    return out


def _gen_reviews(
    rng: random.Random,
    service_jobs: list[dict[str, Any]],
    share: float,
    today: date,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    eligible = [j for j in service_jobs if j["status"] == "completed"]
    target = int(len(eligible) * share)
    for job in rng.sample(eligible, min(target, len(eligible))):
        review_offset = rng.randint(0, 14)
        review_date = (job["completed_date"] or job["scheduled_date"]) \
            + timedelta(days=review_offset)
        if review_date > today:
            review_date = today
        rating = _weighted(rng, [
            (5, 55), (4, 22), (3, 10), (2, 7), (1, 6),
        ])
        text = rng.choice(REVIEW_TEMPLATES)
        if rng.random() < 0.06:
            text = text + f" Reach me at {rng.choice(['shop@example.com', 'cell 555-0142'])}"
        out.append({
            "review_id": _gen_id(rng),
            "job_id": job["job_id"],
            "customer_id": job["customer_id"],
            "rating": rating,
            "text": text,
            "is_public": rng.random() < 0.7,
            "review_date": review_date,
            "deleted_at": None,
            "deleted_by": None,
        })
    return out


def _gen_warranty_claims(
    rng: random.Random,
    equipment: list[dict[str, Any]],
    customers: list[dict[str, Any]],
    n: int,
    today: date,
) -> list[dict[str, Any]]:
    cust_by_id = {c["customer_id"]: c for c in customers}
    out: list[dict[str, Any]] = []
    while len(out) < n:
        eq = rng.choice(equipment)
        c = cust_by_id.get(eq["customer_id"])
        if c is None:
            continue
        claim_offset = rng.randint(0, 365 * 2)
        claim_date = today - timedelta(days=claim_offset)
        status = _weighted(rng, WARRANTY_CLAIM_STATUSES)
        payout: Decimal | None
        supplier_reimb: Decimal | None
        resolved: date | None
        if status == "paid":
            payout = Decimal(f"{rng.uniform(180.0, 4_500.0):.2f}")
            supplier_reimb = Decimal(
                f"{float(payout) * rng.uniform(0.4, 0.95):.2f}",
            )
            resolved = claim_date + timedelta(days=rng.randint(7, 60))
        elif status == "approved":
            payout = Decimal(f"{rng.uniform(180.0, 4_500.0):.2f}")
            supplier_reimb = None
            resolved = claim_date + timedelta(days=rng.randint(2, 21))
        elif status == "denied":
            payout = Decimal("0.00")
            supplier_reimb = None
            resolved = claim_date + timedelta(days=rng.randint(2, 30))
        else:
            payout = None
            supplier_reimb = None
            resolved = None
        if resolved is not None and resolved > today:
            resolved = today
        out.append({
            "claim_id": _gen_id(rng),
            "equipment_id": eq["equipment_id"],
            "customer_id": eq["customer_id"],
            "claim_date": claim_date,
            "status": status,
            "claim_reason": rng.choice(WARRANTY_CLAIM_REASONS),
            "payout_amount_usd": payout,
            "supplier_reimbursement_usd": supplier_reimb,
            "resolved_date": resolved,
            "filed_by": rng.choice([
                "customer-portal", "dispatcher", "tech-app", "owner",
            ]),
            "deleted_at": None,
            "deleted_by": None,
        })
    return out


def _gen_customer_signals(
    rng: random.Random,
    customers: list[dict[str, Any]],
    days: int,
    today: date,
) -> list[dict[str, Any]]:
    region_health = {
        r: rng.randint(45, 95) for r in SERVICE_REGIONS
    }
    out: list[dict[str, Any]] = []
    for d_offset in range(days):
        signal_date = today - timedelta(days=d_offset)
        for c in customers:
            out.append({
                "signal_date": signal_date,
                "customer_id": c["customer_id"],
                "engagement_score": rng.randint(0, 100),
                "churn_risk": rng.randint(0, 100),
                "next_best_action": rng.choice(NEXT_BEST_ACTIONS),
                "service_area_health": region_health[c["service_region"]],
            })
    return out


def _expand_customer_scd2(
    rng: random.Random,
    fake: Faker,
    base_rows: list[dict[str, Any]],
    today: date,
) -> list[dict[str, Any]]:
    plan = SCD2_PLANS["customer"]

    def mutate(rng: random.Random, prev: dict[str, Any]) -> dict[str, Any]:
        if rng.random() < 0.5:
            return {"phone": fake.numerify("###-###-####")}
        return {"email": _make_email(rng, prev["first_name"], prev["last_name"])}

    return _expand_scd2(rng, base_rows, plan, mutate, today)


def _expand_technician_scd2(
    rng: random.Random,
    base_rows: list[dict[str, Any]],
    today: date,
) -> list[dict[str, Any]]:
    plan = SCD2_PLANS["technician"]

    def mutate(rng: random.Random, prev: dict[str, Any]) -> dict[str, Any]:
        choices = [r for r in SERVICE_REGIONS if r != prev["service_region"]]
        return {"service_region": rng.choice(choices)}

    return _expand_scd2(rng, base_rows, plan, mutate, today)


def _expand_parts_scd2(
    rng: random.Random,
    base_rows: list[dict[str, Any]],
    today: date,
) -> list[dict[str, Any]]:
    plan = SCD2_PLANS["parts_inventory"]

    def mutate(rng: random.Random, prev: dict[str, Any]) -> dict[str, Any]:
        new_cost = float(prev["unit_cost_usd"]) * rng.uniform(0.92, 1.18)
        return {
            "unit_cost_usd": Decimal(f"{new_cost:.2f}"),
            "qty_on_hand": rng.randint(0, 240),
        }

    return _expand_scd2(rng, base_rows, plan, mutate, today)


def _expand_equipment_scd2(
    rng: random.Random,
    base_rows: list[dict[str, Any]],
    today: date,
) -> list[dict[str, Any]]:
    """Equipment SCD2 follows warranty flips, not a Poisson process.

    For the configured share of equipment, generate one historical row whose
    service_tier was higher *before* warranty expired. The current row keeps
    the present warranty_status and a (possibly downgraded) service_tier.
    """
    plan = SCD2_PLANS["equipment"]
    flip_share = plan.expected_changes
    out: list[dict[str, Any]] = []
    for row in base_rows:
        install_ts = _to_dt(row["install_date"])
        anchor_ts = install_ts
        if rng.random() < flip_share and row["warranty_status"] != "active":
            flip_ts = _to_dt(row["warranty_expiry_date"]) \
                if row["warranty_expiry_date"] else _to_dt(today)
            if flip_ts <= install_ts:
                flip_ts = install_ts + timedelta(days=30)
            tier_chain = ["ultra", "premium", "standard"]
            cur_idx = tier_chain.index(row["service_tier"]) \
                if row["service_tier"] in tier_chain else 2
            prev_idx = max(0, cur_idx - 1)
            prev_tier = tier_chain[prev_idx]
            historical = dict(row)
            historical.update({
                "warranty_status": "active",
                "service_tier": prev_tier,
                "effective_from": anchor_ts,
                "effective_to": flip_ts,
                "is_current": False,
            })
            current = dict(row)
            current.update({
                "effective_from": flip_ts,
                "effective_to": None,
                "is_current": True,
            })
            out.append(historical)
            out.append(current)
        else:
            current = dict(row)
            current.update({
                "effective_from": anchor_ts,
                "effective_to": None,
                "is_current": True,
            })
            out.append(current)
    return out


def _expand_scd2(
    rng: random.Random,
    base_rows: list[dict[str, Any]],
    plan: SCD2Plan,
    mutate: Callable[[random.Random, dict[str, Any]], dict[str, Any]],
    today: date,
) -> list[dict[str, Any]]:
    """Generic SCD2 expander for time-driven changes.

    Produces a non-overlapping, contiguous chain of versions per natural-key
    row. The last version is is_current=TRUE with effective_to=None.
    """
    window_start = today - timedelta(days=plan.window_days)
    out: list[dict[str, Any]] = []
    for row in base_rows:
        n_changes = _poisson(rng, plan.expected_changes)
        anchor_date = _random_date_between(rng, window_start, today)
        anchor_ts = _to_dt(anchor_date)
        change_dates = sorted(
            _random_date_between(rng, anchor_date, today)
            for _ in range(n_changes)
        )
        change_dates = [d for d in change_dates if d > anchor_date]
        prev = dict(row)
        prev["effective_from"] = anchor_ts
        for ch in change_dates:
            ch_ts = _to_dt(ch)
            if ch_ts <= prev["effective_from"]:
                continue
            closed = dict(prev)
            closed["effective_to"] = ch_ts
            closed["is_current"] = False
            out.append(closed)
            new_row = dict(prev)
            new_row.update(mutate(rng, prev))
            new_row["effective_from"] = ch_ts
            prev = new_row
        prev["effective_to"] = None
        prev["is_current"] = True
        out.append(prev)
    return out


def _apply_soft_delete(
    rng: random.Random,
    rows: list[dict[str, Any]],
    rate: float,
    anchor_field: str,
    today: date,
) -> None:
    """Mutate `rows` in place: set deleted_at + deleted_by on a sampled fraction."""
    today_ts = _to_dt(today)
    for row in rows:
        if rng.random() >= rate:
            row.setdefault("deleted_at", None)
            row.setdefault("deleted_by", None)
            continue
        anchor = row[anchor_field]
        if isinstance(anchor, datetime):
            anchor_ts = anchor
            anchor_date = anchor.date()
        else:
            anchor_ts = _to_dt(anchor)
            anchor_date = anchor
        max_delta = max(1, (today - anchor_date).days)
        delete_ts = anchor_ts + timedelta(
            days=rng.randint(1, max_delta),
            seconds=rng.randint(0, 86_399),
        )
        if delete_ts > today_ts:
            delete_ts = today_ts
        row["deleted_at"] = delete_ts
        row["deleted_by"] = rng.choice(DELETED_BY_ACTORS)


def validate_dag(tables: dict[str, list[dict[str, Any]]]) -> None:
    """Raise ValueError on FK / SCD2 / soft-delete violations.

    FK chains: customer -> equipment -> service_job -> truck_roll -> warranty_claim,
    plus dispatch_event -> service_job, review -> service_job + customer,
    customer_signal_daily -> customer, truck_roll.parts_pulled -> parts_inventory.
    """
    customers = tables["customer"]
    technicians = tables["technician"]
    equipment = tables["equipment"]
    parts = tables["parts_inventory"]
    jobs = tables["service_job"]

    customer_ids = {r["customer_id"] for r in customers}
    tech_ids = {r["technician_id"] for r in technicians}
    equipment_ids = {r["equipment_id"] for r in equipment}
    sku_set = {r["sku"] for r in parts}
    job_ids = {r["job_id"] for r in jobs}

    for r in equipment:
        if r["customer_id"] not in customer_ids:
            raise ValueError(f"equipment {r['equipment_id']}: customer_id missing")

    for r in jobs:
        if r["customer_id"] not in customer_ids:
            raise ValueError(f"service_job {r['job_id']}: customer_id missing")
        if r["technician_id"] is not None and r["technician_id"] not in tech_ids:
            raise ValueError(f"service_job {r['job_id']}: technician_id missing")
        if r["equipment_id"] is not None and r["equipment_id"] not in equipment_ids:
            raise ValueError(f"service_job {r['job_id']}: equipment_id missing")

    for r in tables["dispatch_event"]:
        if r["job_id"] not in job_ids:
            raise ValueError(f"dispatch_event {r['event_id']}: job_id missing")
        if r["technician_id"] is not None and r["technician_id"] not in tech_ids:
            raise ValueError(
                f"dispatch_event {r['event_id']}: technician_id missing",
            )

    for r in tables["truck_roll"]:
        if r["job_id"] not in job_ids:
            raise ValueError(f"truck_roll {r['truck_roll_id']}: job_id missing")
        if r["technician_id"] not in tech_ids:
            raise ValueError(
                f"truck_roll {r['truck_roll_id']}: technician_id missing",
            )
        if r["equipment_id"] is not None and r["equipment_id"] not in equipment_ids:
            raise ValueError(
                f"truck_roll {r['truck_roll_id']}: equipment_id missing",
            )
        for sku in (r["parts_pulled"] or []):
            if sku not in sku_set:
                raise ValueError(
                    f"truck_roll {r['truck_roll_id']}: parts_pulled sku {sku} missing",
                )
        for sku in (r["parts_returned"] or []):
            if sku not in sku_set:
                raise ValueError(
                    f"truck_roll {r['truck_roll_id']}: parts_returned sku {sku} missing",
                )

    for r in tables["review"]:
        if r["job_id"] not in job_ids:
            raise ValueError(f"review {r['review_id']}: job_id missing")
        if r["customer_id"] not in customer_ids:
            raise ValueError(f"review {r['review_id']}: customer_id missing")

    for r in tables["warranty_claim"]:
        if r["equipment_id"] not in equipment_ids:
            raise ValueError(
                f"warranty_claim {r['claim_id']}: equipment_id missing",
            )
        if r["customer_id"] not in customer_ids:
            raise ValueError(
                f"warranty_claim {r['claim_id']}: customer_id missing",
            )

    for r in tables["customer_signal_daily"]:
        if r["customer_id"] not in customer_ids:
            raise ValueError("customer_signal_daily: customer_id missing")

    for table_name, key in [
        ("customer", "customer_id"),
        ("technician", "technician_id"),
        ("equipment", "equipment_id"),
        ("parts_inventory", "sku"),
    ]:
        _validate_scd2_invariants(tables[table_name], key, table_name)

    for table_name in ("service_job", "review", "truck_roll", "warranty_claim"):
        _validate_soft_delete_invariants(tables[table_name], table_name)


def _validate_scd2_invariants(
    rows: list[dict[str, Any]], key_field: str, table_name: str,
) -> None:
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_key[r[key_field]].append(r)
    for key, items in by_key.items():
        currents = [r for r in items if r["is_current"]]
        if len(currents) != 1:
            raise ValueError(
                f"{table_name} {key_field}={key}: "
                f"expected exactly one is_current=TRUE, got {len(currents)}",
            )
        if currents[0]["effective_to"] is not None:
            raise ValueError(
                f"{table_name} {key_field}={key}: "
                f"is_current row has non-NULL effective_to",
            )
        items_sorted = sorted(items, key=lambda r: r["effective_from"])
        for i, r in enumerate(items_sorted[:-1]):
            if r["effective_to"] is None:
                raise ValueError(
                    f"{table_name} {key_field}={key}: "
                    f"non-current row has NULL effective_to",
                )
            nxt = items_sorted[i + 1]
            if r["effective_to"] > nxt["effective_from"]:
                raise ValueError(
                    f"{table_name} {key_field}={key}: "
                    f"overlapping windows ({r['effective_to']} > "
                    f"{nxt['effective_from']})",
                )


def _validate_soft_delete_invariants(
    rows: list[dict[str, Any]], table_name: str,
) -> None:
    for r in rows:
        a = r.get("deleted_at")
        b = r.get("deleted_by")
        if (a is None) != (b is None):
            raise ValueError(
                f"{table_name}: soft-delete invariant broken "
                f"(deleted_at={a!r}, deleted_by={b!r})",
            )


def _write_parquet(table: str, rows: list[dict[str, Any]], path: Path) -> None:
    schema = SCHEMAS[table]
    columns: dict[str, list[Any]] = {field.name: [] for field in schema}
    for row in rows:
        for field in schema:
            columns[field.name].append(row.get(field.name))
    arrays = [
        pa.array(columns[field.name], type=field.type) for field in schema
    ]
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


def _gen_id(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128)))


def _weighted(rng: random.Random, weighted: list[tuple[Any, int]]) -> Any:
    items = [w[0] for w in weighted]
    weights = [w[1] for w in weighted]
    return rng.choices(items, weights=weights, k=1)[0]


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


def _random_date_between(
    rng: random.Random, start: date, end: date,
) -> date:
    delta = (end - start).days
    if delta <= 0:
        return start
    return start + timedelta(days=rng.randint(0, delta))


def _to_dt(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _phx_zip(rng: random.Random) -> str:
    return f"{rng.choice(PHX_ZIP_PREFIXES)}{rng.randint(10, 99)}"


def _make_email(
    rng: random.Random, first: str, last: str,
    domain: str = "example.com",
) -> str:
    handle = f"{first}.{last}".lower().replace(" ", "")
    if rng.random() < 0.3:
        handle = f"{handle}{rng.randint(10, 999)}"
    return f"{handle}@{domain}"


def _maybe_billing_note(rng: random.Random, fake: Faker) -> str | None:
    if rng.random() < 0.55:
        return None
    snippets = [
        "auto-pay enabled",
        "prefers paper invoice",
        f"primary contact: {fake.first_name()} {fake.last_name()}",
        "ACH on file",
        f"alt phone {fake.numerify('###-###-####')}",
    ]
    return rng.choice(snippets)


def _maybe_job_billing_note(
    rng: random.Random, fake: Faker, status: str,
) -> str | None:
    if status != "completed":
        return None
    if rng.random() < 0.7:
        return None
    snippets = [
        "warranty discount applied",
        "split bill with landlord",
        f"caller asked us to text {fake.numerify('###-###-####')}",
        "deposit on file from prior visit",
        "weekend rate",
    ]
    return rng.choice(snippets)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate the synthetic HVAC home-services dataset (ADR-008).",
    )
    p.add_argument("--output", "--output-dir", dest="output_dir",
                   type=Path, default=Path("./output"))
    p.add_argument("--bucket", help="S3 bucket for staging upload.")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--today", type=date.fromisoformat,
                   default=date.today(),
                   help="Anchor date for relative offsets. Default: today.")
    p.add_argument("--customers", type=int, default=Volumes.customers)
    p.add_argument("--technicians", type=int, default=Volumes.technicians)
    p.add_argument("--service-jobs", type=int, default=Volumes.service_jobs)
    p.add_argument("--parts", type=int, default=Volumes.parts)
    p.add_argument("--warranty-claims", type=int,
                   default=Volumes.warranty_claims)
    p.add_argument("--signal-days", type=int, default=Volumes.signal_days)
    p.add_argument("--upload", action="store_true")
    return p.parse_args(argv)


def _config_from_args(args: argparse.Namespace) -> Config:
    return Config(
        output_dir=args.output_dir,
        bucket=args.bucket,
        region=args.region,
        seed=args.seed,
        today=args.today,
        volumes=Volumes(
            customers=args.customers,
            technicians=args.technicians,
            service_jobs=args.service_jobs,
            parts=args.parts,
            warranty_claims=args.warranty_claims,
            signal_days=args.signal_days,
        ),
        upload=args.upload,
    )


if __name__ == "__main__":
    sys.exit(main())

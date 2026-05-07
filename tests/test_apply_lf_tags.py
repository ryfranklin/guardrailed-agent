"""Pure-logic tests for scripts/apply-lf-tags.py.

Covers: desired_tags() correctness, expected_state() exhaustiveness,
sensitivity=high lands on the four named columns and only on those columns,
pii classification matches the synthesizer's source of truth.

The script's AWS-calling paths (apply_all, verify) are not exercised here —
those need either a real Lake Formation environment or moto, which is not
yet a project dependency. Run them against the demo account instead and use
--verify to confirm column-by-column state.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "apply-lf-tags.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("apply_lf_tags", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["apply_lf_tags"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def lftags():
    return _load_script_module()


@pytest.fixture(scope="module")
def schemas_module():
    sys.path.insert(0, str(REPO_ROOT))
    from data.synthesizer import schemas as schemas_module
    return schemas_module


def test_high_sensitivity_columns_match_adr(lftags):
    expected = {
        "parts_inventory": {"unit_cost_usd", "supplier_terms"},
        "technician_utilization_daily": {"revenue_generated_usd"},
        "warranty_claim": {"payout_amount_usd"},
    }
    assert lftags.HIGH_SENSITIVITY_COLUMNS == expected


def test_desired_tags_assigns_high_to_named_columns(lftags):
    assert lftags.desired_tags("parts_inventory", "unit_cost_usd")["sensitivity"] == "high"
    assert lftags.desired_tags("parts_inventory", "supplier_terms")["sensitivity"] == "high"
    assert lftags.desired_tags(
        "technician_utilization_daily", "revenue_generated_usd",
    )["sensitivity"] == "high"
    assert lftags.desired_tags("warranty_claim", "payout_amount_usd")["sensitivity"] == "high"


def test_desired_tags_assigns_other_elsewhere(lftags, schemas_module):
    high = lftags.HIGH_SENSITIVITY_COLUMNS
    for table, schema in schemas_module.SCHEMAS.items():
        for field in schema:
            tags = lftags.desired_tags(table, field.name)
            if field.name in high.get(table, set()):
                assert tags["sensitivity"] == "high"
            else:
                assert tags["sensitivity"] == "other", (
                    f"{table}.{field.name} should be sensitivity=other, "
                    f"got {tags['sensitivity']}"
                )


def test_desired_tags_pii_matches_classifications(lftags, schemas_module):
    for table, cls in schemas_module.COLUMN_CLASSIFICATIONS.items():
        for col, entry in cls.items():
            expected = "true" if entry["pii"] else "false"
            assert lftags.desired_tags(table, col)["pii"] == expected, (
                f"{table}.{col} pii classification drifted"
            )


def test_expected_state_covers_every_column(lftags, schemas_module):
    state = lftags.expected_state()
    assert set(state.keys()) == set(schemas_module.SCHEMAS.keys())
    for table, schema in schemas_module.SCHEMAS.items():
        for field in schema:
            assert field.name in state[table], (
                f"expected_state missing {table}.{field.name}"
            )
            entry = state[table][field.name]
            assert set(entry.keys()) == {"pii", "sensitivity"}
            assert entry["pii"] in {"true", "false"}
            assert entry["sensitivity"] in {"high", "other"}


def test_expected_state_includes_all_twelve_tables(lftags):
    state = lftags.expected_state()
    assert set(state.keys()) == {
        "customer", "technician", "equipment", "service_job", "review",
        "customer_signal_daily", "parts_inventory", "dispatch_event",
        "truck_roll", "warranty_claim", "equipment_telemetry_daily",
        "technician_utilization_daily",
    }


def test_expected_state_count_is_predictable(lftags, schemas_module):
    """Total tag attachments = 2 keys × sum of columns across all tables."""
    state = lftags.expected_state()
    n_columns = sum(
        len(schema) for schema in schemas_module.SCHEMAS.values()
    )
    n_attachments = sum(
        len(tags)
        for cols in state.values()
        for tags in cols.values()
    )
    assert n_attachments == n_columns * 2


def test_dry_run_prints_one_line_per_attachment(lftags, capsys, schemas_module):
    rc = lftags.apply_all("test_db", "us-east-1", dry_run=True)
    assert rc == 0
    out = capsys.readouterr().out.splitlines()
    n_columns = sum(len(s) for s in schemas_module.SCHEMAS.values())
    applied_lines = [line for line in out if line.startswith("applied ")]
    assert len(applied_lines) == n_columns * 2
    summary_lines = [line for line in out if line.startswith("summary ")]
    assert len(summary_lines) == 1

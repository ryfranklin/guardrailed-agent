"""Acceptance test for ADR-009 Phase 2.b: explain_governance prediction
must match what Lake Formation actually does at query time.

For a sample query against `customer × equipment × service_job`, runs
explain_governance for each persona, then runs the same query via the
deployed Athena workgroup under that persona's STS credentials, and
diffs the columns LF actually returned against the prediction.

Skipped unless RUN_INTEGRATION=1 is set. The CI workflow (eval.yml)
exports RUN_INTEGRATION=1 on the live-eval job.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TF_DIR = REPO_ROOT / "terraform" / "envs" / "demo"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_INTEGRATION"),
    reason="set RUN_INTEGRATION=1 to run AWS-backed governance diff test",
)


SAMPLE_QUERY = (
    "SELECT * "
    "FROM customer c "
    "JOIN equipment e ON e.customer_id = c.customer_id "
    "JOIN service_job j ON j.equipment_id = e.equipment_id "
    "LIMIT 1"
)


def _terraform_outputs() -> dict[str, Any]:
    proc = subprocess.run(
        ["terraform", f"-chdir={TF_DIR}", "output", "-json"],
        capture_output=True, text=True, check=True,
    )
    raw = json.loads(proc.stdout)
    return {k: v["value"] for k, v in raw.items()}


def _persona_arns(cfg: dict[str, Any]) -> dict[str, str]:
    return {
        "dispatcher": cfg["dispatcher_role_arn"],
        "technician_lead": cfg["technician_lead_role_arn"],
        "owner": cfg["owner_role_arn"],
    }


def _build_state(cfg: dict[str, Any]):
    """Build a ServerState pointed at the deployed Demo env."""
    if not os.environ.get("RUN_INTEGRATION"):
        os.environ.setdefault("GAGENT_TRUSTED_OPERATOR", "1")
    os.environ.setdefault("GAGENT_GLUE_DATABASE", cfg["glue_database_name"])
    os.environ.setdefault("AWS_REGION", "us-east-1")
    os.environ.setdefault(
        "GAGENT_DISPATCHER_ROLE_ARN", cfg["dispatcher_role_arn"],
    )
    os.environ.setdefault(
        "GAGENT_TECHNICIAN_LEAD_ROLE_ARN", cfg["technician_lead_role_arn"],
    )
    os.environ.setdefault("GAGENT_OWNER_ROLE_ARN", cfg["owner_role_arn"])
    os.environ.setdefault("GAGENT_AGENT_ID", cfg.get("agent_id", ""))
    os.environ.setdefault(
        "GAGENT_AGENT_ALIAS_ID", cfg.get("agent_alias_id", ""),
    )

    from mcp_server import ServerState, load_config

    config = load_config(os.environ)
    return ServerState(config=config)


def _resolve_persona(state, role: str, *, service_region: str | None = None):
    assert state.config.resolver is not None
    return state.config.resolver.resolve(
        role,
        service_region=service_region if role == "technician_lead" else None,
    )


def _columns_visible_to_persona_via_athena(
    cfg: dict[str, Any], role: str, query: str,
    service_region: str | None = None,
) -> set[str]:
    """Run the query under the persona's creds via Athena; return result columns."""
    import boto3

    from gagent_client import assume_persona

    state = _build_state(cfg)
    persona = _resolve_persona(state, role, service_region=service_region)
    creds = assume_persona(persona)
    athena = boto3.client(
        "athena",
        region_name=cfg.get("region", "us-east-1") or "us-east-1",
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )

    exec_id = athena.start_query_execution(
        QueryString=query,
        WorkGroup=cfg["athena_workgroup_name"],
        QueryExecutionContext={"Database": cfg["glue_database_name"]},
    )["QueryExecutionId"]

    deadline = time.time() + 60
    while time.time() < deadline:
        status = athena.get_query_execution(QueryExecutionId=exec_id)["QueryExecution"]["Status"]
        state_str = status["State"]
        if state_str == "SUCCEEDED":
            break
        if state_str in ("FAILED", "CANCELLED"):
            reason = status.get("StateChangeReason", "")
            if "denied" in reason.lower() or "not authorized" in reason.lower() \
                    or "Insufficient permissions" in reason:
                return set()
            pytest.fail(f"Athena query {state_str} for {role}: {reason}")
        time.sleep(1)
    else:
        pytest.fail(f"Athena query timed out for {role}")

    results = athena.get_query_results(QueryExecutionId=exec_id, MaxResults=2)
    col_info = results["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
    return {c["Name"] for c in col_info}


def _redacted_predicted_for_table(
    state, query: str, role: str, table: str,
    service_region: str | None = None,
) -> set[str]:
    """Predicted-redacted column names for the given table from explain_governance."""
    from mcp_server import explain_governance_impl

    args: dict[str, Any] = {"query": query, "persona": role}
    if service_region:
        args["service_region"] = service_region
    result = explain_governance_impl(state, args)
    if "error" in result:
        pytest.fail(f"explain_governance error: {result}")
    return {
        r["column"] for r in result["redacted_columns"]
        if r.get("table") == table
    }


def _all_columns_for_table(state, table: str) -> set[str]:
    from mcp_server.governance import fetch_table_metadata
    from mcp_server.governance_tools import _governance_clients

    glue, _ = _governance_clients(state.config.region)
    columns, _, _ = fetch_table_metadata(
        glue, state.config.glue_database, table,
    )
    return set(columns)


# Athena qualifies cross-table SELECT * results with the source table as a
# prefix when names collide. Our diff is per-table, so we strip prefixes.
def _strip_table_prefix(cols: set[str]) -> set[str]:
    out: set[str] = set()
    for c in cols:
        if "." in c:
            _, rest = c.split(".", 1)
            out.add(rest)
        else:
            out.add(c)
    return out


@pytest.mark.parametrize("role,service_region", [
    ("dispatcher", None),
    ("technician_lead", "tempe-mesa"),
    ("owner", None),
])
def test_explain_governance_matches_athena(role, service_region):
    cfg = _terraform_outputs()
    state = _build_state(cfg)

    visible_via_athena_raw = _columns_visible_to_persona_via_athena(
        cfg, role, SAMPLE_QUERY, service_region=service_region,
    )
    if not visible_via_athena_raw:
        pytest.skip(f"persona {role} cannot SELECT this query at all")
    visible_via_athena = _strip_table_prefix(visible_via_athena_raw)

    for table in ("customer", "equipment", "service_job"):
        all_cols = _all_columns_for_table(state, table)
        predicted_redacted = _redacted_predicted_for_table(
            state, SAMPLE_QUERY, role, table, service_region=service_region,
        )
        actually_redacted = all_cols - visible_via_athena

        # Allow predicted ⊇ actually (we may over-predict redaction for
        # SCD2 internal fields the user wouldn't include in SELECT *), but
        # never under-predict — if Athena hid a column we said was visible,
        # the test fails.
        missed_redactions = actually_redacted - predicted_redacted
        assert not missed_redactions, (
            f"explain_governance under-predicted for {role} on {table}: "
            f"{sorted(missed_redactions)} were redacted by Athena but "
            f"NOT predicted as redacted. predicted={sorted(predicted_redacted)} "
            f"actual={sorted(actually_redacted)}"
        )


def test_explain_governance_renders_grant_evidence_for_each_persona():
    cfg = _terraform_outputs()
    state = _build_state(cfg)

    from mcp_server import explain_governance_impl

    for role in ("dispatcher", "technician_lead", "owner"):
        args: dict[str, Any] = {"query": SAMPLE_QUERY, "persona": role}
        if role == "technician_lead":
            args["service_region"] = "tempe-mesa"
        result = explain_governance_impl(state, args)
        assert "error" not in result, f"{role}: {result}"
        evidence = result["grant_evidence"]
        assert evidence, f"{role}: empty grant_evidence"
        # Must include both pii AND sensitivity expressions (ADR-008 dual-tag scheme).
        keys_seen: set[str] = set()
        for grant in evidence:
            for expr in grant["tag_expression"]:
                keys_seen.add(expr["key"])
        assert "pii" in keys_seen, f"{role}: no pii expression"
        assert "sensitivity" in keys_seen, f"{role}: no sensitivity expression"

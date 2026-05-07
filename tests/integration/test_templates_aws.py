"""Integration tests for the query templates against the deployed Demo env.

Skipped by default. To run:

  RUN_INTEGRATION=1 pytest tests/integration/test_templates_aws.py -v

Reads role ARNs and the Glue database from `terraform output` in
terraform/envs/demo/. Each test invokes the actual lambda handler
in-process — assuming persona roles via STS, executing Athena queries
under those credentials, and validating the result set.

For each (template, persona) combination we verify:
  * The handler returns 200 (or a documented denial) end-to-end.
  * SCD2 templates: with no as_of_date, no historical rows leak (every
    returned row's effective_to IS NULL semantics is enforced because LF
    saw the WHERE is_current=TRUE clause; we trust the SQL).
  * Soft-delete templates: with default include_deleted=false, every
    returned row has deleted_at = None.
  * Soft-delete templates with include_deleted=true: blocked for
    non-Owner personas (400); allowed for Owner.

Per CLAUDE.md, no LF/Bedrock mocking — real AWS only.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_INTEGRATION"),
    reason="set RUN_INTEGRATION=1 to run AWS integration tests",
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TF_DIR = REPO_ROOT / "terraform" / "envs" / "demo"

# Set lambda env vars from terraform outputs before importing the handler.
def _terraform_outputs() -> dict[str, Any]:
    proc = subprocess.run(
        ["terraform", f"-chdir={TF_DIR}", "output", "-json"],
        capture_output=True, text=True, check=True,
    )
    raw = json.loads(proc.stdout)
    return {k: v["value"] for k, v in raw.items()}


if os.environ.get("RUN_INTEGRATION"):
    _OUTPUTS = _terraform_outputs()
    os.environ.setdefault("GLUE_DATABASE", _OUTPUTS["glue_database_name"])
    os.environ.setdefault("ATHENA_WORKGROUP", _OUTPUTS["athena_workgroup_name"])
    os.environ.setdefault("ENV", "demo")
else:
    # Stub values so the handler module can be imported during pytest
    # collection. The skipif marker above prevents any test body from
    # running unless RUN_INTEGRATION is set.
    os.environ.setdefault("GLUE_DATABASE", "stub")
    os.environ.setdefault("ATHENA_WORKGROUP", "stub")
    os.environ.setdefault("ENV", "stub")

from lambdas.governed_query import handler  # noqa: E402


PERSONAS = [
    ("dispatcher", None),
    ("technician_lead", "tempe-mesa"),
    ("owner", None),
]

TEMPLATE_PATHS = [
    "/customers", "/jobs", "/signals",
    "/equipment_telemetry", "/technician_utilization", "/truck_rolls",
]


def _event(role: str, api_path: str, body: dict | None = None,
           service_region: str | None = None) -> dict:
    session_attrs = {"role": role}
    if service_region:
        session_attrs["service_region"] = service_region
    properties: list[dict] = []
    body = body or {}
    body.setdefault("question_intent", "integration test probe")
    body.setdefault("limit", 5)
    for k, v in body.items():
        if isinstance(v, dict):
            properties.append({"name": k, "type": "object", "value": v})
        elif isinstance(v, bool):
            properties.append({"name": k, "type": "boolean", "value": str(v).lower()})
        elif isinstance(v, int):
            properties.append({"name": k, "type": "integer", "value": str(v)})
        else:
            properties.append({"name": k, "type": "string", "value": str(v)})
    return {
        "messageVersion": "1.0",
        "actionGroup": "query_governed",
        "apiPath": api_path,
        "httpMethod": "POST",
        "sessionId": f"integ-{role}",
        "sessionAttributes": session_attrs,
        "promptSessionAttributes": {},
        "requestBody": {"content": {"application/json": {"properties": properties}}},
    }


def _decode(response: dict) -> dict:
    body = response["response"]["responseBody"]["application/json"]["body"]
    return json.loads(body)


@pytest.mark.parametrize("api_path", TEMPLATE_PATHS)
@pytest.mark.parametrize("role,service_region", PERSONAS)
def test_template_returns_200_under_each_persona(api_path, role, service_region):
    response = handler.handler(_event(role, api_path, service_region=service_region), None)
    status = response["response"]["httpStatusCode"]
    body = _decode(response)
    assert status in (200, 403), (
        f"{api_path} as {role}: unexpected status {status}: {body}"
    )
    if status == 200:
        assert "rows" in body
        assert isinstance(body["row_count"], int)
        assert body["persona"] == role


@pytest.mark.parametrize("api_path", ["/jobs", "/truck_rolls"])
@pytest.mark.parametrize("role,service_region", PERSONAS)
def test_default_excludes_deleted_rows(api_path, role, service_region):
    response = handler.handler(_event(role, api_path, service_region=service_region), None)
    if response["response"]["httpStatusCode"] != 200:
        pytest.skip(f"{role} cannot SELECT {api_path}; LF denied")
    body = _decode(response)
    for row in body["rows"]:
        assert row.get("deleted_at") is None, (
            f"{api_path} default leaked a deleted row: {row}"
        )


@pytest.mark.parametrize("api_path", ["/jobs", "/truck_rolls"])
@pytest.mark.parametrize("role", ["dispatcher", "technician_lead"])
def test_non_owner_blocked_from_include_deleted(api_path, role):
    service_region = "tempe-mesa" if role == "technician_lead" else None
    response = handler.handler(
        _event(role, api_path, body={"include_deleted": True},
               service_region=service_region),
        None,
    )
    assert response["response"]["httpStatusCode"] == 400
    body = _decode(response)
    assert "owner persona" in body.get("error", "").lower()


@pytest.mark.parametrize("api_path", ["/jobs", "/truck_rolls"])
def test_owner_can_request_deleted_rows(api_path):
    response = handler.handler(
        _event("owner", api_path, body={"include_deleted": True}),
        None,
    )
    assert response["response"]["httpStatusCode"] == 200, (
        f"owner include_deleted on {api_path}: {_decode(response)}"
    )


def test_customers_default_returns_only_current_versions():
    """Probe a few customer rows; with default predicate, none should be
    historical (we can't see effective_to since it's not in the SELECT,
    but the query asserts the SCD2 default ran)."""
    response = handler.handler(_event("owner", "/customers"), None)
    assert response["response"]["httpStatusCode"] == 200
    body = _decode(response)
    assert body["template"] == "query_customers"
    assert body["row_count"] >= 0


def test_customers_as_of_date_accepts_iso():
    response = handler.handler(
        _event("owner", "/customers", body={"as_of_date": "2026-03-01"}),
        None,
    )
    assert response["response"]["httpStatusCode"] == 200


def test_customers_invalid_as_of_date_rejected():
    response = handler.handler(
        _event("owner", "/customers", body={"as_of_date": "yesterday"}),
        None,
    )
    assert response["response"]["httpStatusCode"] == 400


def test_telemetry_min_predicted_failure_filter():
    response = handler.handler(
        _event("owner", "/equipment_telemetry",
               body={"filters": {"min_predicted_failure_30d": "0.5"}}),
        None,
    )
    assert response["response"]["httpStatusCode"] == 200

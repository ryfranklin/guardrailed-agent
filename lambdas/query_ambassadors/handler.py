"""Bedrock Agent action group: query_ambassadors.

Receives invocation events from a Bedrock Agent, derives the calling persona
from session attributes, assumes the matching IAM role *with session tags*,
runs an Athena query against the governed Glue/Iceberg dataset, and returns
shape-preserved JSON to the agent.

Lake Formation enforces row and column visibility via the persona role's
LF-Tag policy. This handler does not make access decisions — it just
preserves a stable response shape so the model sees explicit 'REDACTED'
markers for columns Lake Formation hid.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

GLUE_DATABASE = os.environ["GLUE_DATABASE"]
ATHENA_WORKGROUP = os.environ["ATHENA_WORKGROUP"]
ENV = os.environ.get("ENV", "demo")
ROLE_NAME_PREFIX = os.environ.get("ROLE_NAME_PREFIX", "gagent-")

ALLOWED_TABLES = {"ambassador", "ambassador_team", "order_fact", "signal_fact"}

PII_COLUMNS: dict[str, set[str]] = {
    "ambassador": {
        "first_name",
        "last_name",
        "email",
        "phone",
        "ssn_last4",
        "date_of_birth",
        "street_address",
        "city",
        "postal_code",
    },
    "ambassador_team": set(),
    "order_fact": {"payment_last4"},
    "signal_fact": set(),
}

ALL_COLUMNS: dict[str, list[str]] = {
    "ambassador": [
        "ambassador_id",
        "enrollment_date",
        "status",
        "rank",
        "region",
        "first_name",
        "last_name",
        "email",
        "phone",
        "ssn_last4",
        "date_of_birth",
        "street_address",
        "city",
        "postal_code",
    ],
    "ambassador_team": [
        "ambassador_id",
        "sponsor_id",
        "upline_path",
        "generation",
        "joined_team_date",
    ],
    "order_fact": [
        "order_id",
        "ambassador_id",
        "order_date",
        "order_total",
        "product_category",
        "order_status",
        "payment_last4",
    ],
    "signal_fact": [
        "signal_date",
        "ambassador_id",
        "momentum_score",
        "churn_risk",
        "next_best_action",
        "team_health_score",
    ],
}

PERSONAS = {"analyst", "regional_manager", "admin"}

IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

_sts = boto3.client("sts", config=Config(retries={"max_attempts": 3, "mode": "standard"}))


@dataclass
class PersonaContext:
    role: str
    region: str | None
    role_arn: str


class BadRequest(ValueError):
    """Raised for malformed or disallowed action-group inputs."""


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    logger.info("invocation: action_group=%s session=%s",
                event.get("actionGroup"), event.get("sessionId"))

    try:
        persona = _resolve_persona(event)
        body = _parse_request_body(event)
        rows = _run_query(persona, body)
        payload = _build_response_payload(persona, body, rows)
        return _agent_response(event, 200, payload)
    except BadRequest as exc:
        logger.warning("bad request: %s", exc)
        return _agent_response(event, 400, {"error": str(exc)})
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("AccessDeniedException", "AccessDenied"):
            logger.warning("lake formation denied: %s", exc)
            return _agent_response(event, 403, {"error": "Access denied for this persona."})
        logger.exception("aws client error")
        return _agent_response(event, 500, {"error": "Upstream AWS error."})
    except Exception:  # noqa: BLE001
        logger.exception("unhandled error")
        return _agent_response(event, 500, {"error": "Internal error."})


def _resolve_persona(event: dict[str, Any]) -> PersonaContext:
    session_attrs = event.get("sessionAttributes") or {}
    prompt_attrs = event.get("promptSessionAttributes") or {}
    merged = {**session_attrs, **prompt_attrs}

    role = (merged.get("role") or "").strip().lower()
    if role not in PERSONAS:
        raise BadRequest(
            f"Session attribute 'role' must be one of {sorted(PERSONAS)}; got {role!r}."
        )

    region = merged.get("region")
    if role == "regional_manager" and not region:
        raise BadRequest("regional_manager persona requires a 'region' session attribute.")

    role_arn = _persona_role_arn(role)
    return PersonaContext(role=role, region=region, role_arn=role_arn)


def _persona_role_arn(role: str) -> str:
    account_id = _sts.get_caller_identity()["Account"]
    role_name_part = "regional-manager" if role == "regional_manager" else role
    return f"arn:aws:iam::{account_id}:role/{ROLE_NAME_PREFIX}{role_name_part}-{ENV}"


def _parse_request_body(event: dict[str, Any]) -> dict[str, Any]:
    request_body = event.get("requestBody") or {}
    content = request_body.get("content") or {}
    json_body = content.get("application/json") or {}

    if "properties" in json_body:
        body = {p["name"]: _coerce_property(p) for p in json_body["properties"]}
    else:
        raw = json_body.get("body")
        body = json.loads(raw) if isinstance(raw, str) else (raw or {})

    table = (body.get("table") or "ambassador").strip().lower()
    if table not in ALLOWED_TABLES:
        raise BadRequest(f"Unknown table {table!r}. Allowed: {sorted(ALLOWED_TABLES)}.")

    limit = int(body.get("limit") or 50)
    limit = max(1, min(200, limit))

    filters = body.get("filters") or {}
    if not isinstance(filters, dict):
        raise BadRequest("filters must be an object of column->value pairs.")

    return {
        "table": table,
        "filters": filters,
        "limit": limit,
        "question_intent": body.get("question_intent") or "",
    }


def _coerce_property(prop: dict[str, Any]) -> Any:
    value = prop.get("value")
    type_ = (prop.get("type") or "string").lower()
    if value is None:
        return None
    if type_ in ("integer", "number"):
        try:
            return int(value) if type_ == "integer" else float(value)
        except (TypeError, ValueError):
            return value
    if type_ == "boolean":
        return str(value).lower() in ("true", "1", "yes")
    if type_ == "object":
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return {}
        return value or {}
    return str(value)


def _run_query(persona: PersonaContext, body: dict[str, Any]) -> list[dict[str, Any]]:
    creds = _assume_persona(persona)
    athena = boto3.client(
        "athena",
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )

    sql, params = _build_query(persona, body)
    logger.info("athena query persona=%s table=%s sql=%s", persona.role, body["table"], sql)

    start_kwargs: dict[str, Any] = {
        "QueryString": sql,
        "WorkGroup": ATHENA_WORKGROUP,
        "QueryExecutionContext": {"Database": GLUE_DATABASE},
    }
    if params:
        start_kwargs["ExecutionParameters"] = params

    execution_id = athena.start_query_execution(**start_kwargs)["QueryExecutionId"]

    _wait_for_query(athena, execution_id)
    return _fetch_results(athena, execution_id)


def _assume_persona(persona: PersonaContext) -> dict[str, str]:
    tags = [{"Key": "role", "Value": persona.role}]
    if persona.region:
        tags.append({"Key": "region", "Value": persona.region})

    response = _sts.assume_role(
        RoleArn=persona.role_arn,
        RoleSessionName=f"agent-{persona.role}-{uuid.uuid4().hex[:8]}",
        Tags=tags,
        TransitiveTagKeys=[t["Key"] for t in tags],
        DurationSeconds=900,
    )
    return response["Credentials"]


def _build_query(persona: PersonaContext, body: dict[str, Any]) -> tuple[str, list[str]]:
    table = body["table"]
    if not IDENT_RE.match(table):
        raise BadRequest("Invalid table identifier.")

    where_parts: list[str] = []
    params: list[str] = []

    if persona.role == "regional_manager" and persona.region and table == "ambassador":
        where_parts.append("region = ?")
        params.append(persona.region)

    for col, val in body["filters"].items():
        if not IDENT_RE.match(col):
            raise BadRequest(f"Invalid filter column {col!r}.")
        if col not in ALL_COLUMNS.get(table, []):
            raise BadRequest(f"Filter column {col!r} not in table {table}.")
        where_parts.append(f"{col} = ?")
        params.append(str(val))

    where = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
    sql = f"SELECT * FROM {table}{where} LIMIT {body['limit']}"
    return sql, params


def _wait_for_query(athena, execution_id: str, max_seconds: int = 50) -> None:
    deadline = time.time() + max_seconds
    delay = 0.5
    while time.time() < deadline:
        state_resp = athena.get_query_execution(QueryExecutionId=execution_id)
        status = state_resp["QueryExecution"]["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            return
        if state in ("FAILED", "CANCELLED"):
            reason = status.get("StateChangeReason", "unknown")
            if "denied" in reason.lower() or "not authorized" in reason.lower():
                raise ClientError(
                    {"Error": {"Code": "AccessDeniedException", "Message": reason}},
                    "GetQueryExecution",
                )
            raise RuntimeError(f"Athena query {state}: {reason}")
        time.sleep(delay)
        delay = min(delay * 1.5, 4.0)
    raise TimeoutError(f"Athena query {execution_id} did not complete in {max_seconds}s.")


def _fetch_results(athena, execution_id: str) -> list[dict[str, Any]]:
    paginator = athena.get_paginator("get_query_results")
    rows: list[dict[str, Any]] = []
    columns: list[str] = []
    first_page = True
    for page in paginator.paginate(QueryExecutionId=execution_id):
        result_set = page["ResultSet"]
        if first_page:
            columns = [c["Name"] for c in result_set["ResultSetMetadata"]["ColumnInfo"]]
            page_rows = result_set["Rows"][1:]
            first_page = False
        else:
            page_rows = result_set["Rows"]

        for row in page_rows:
            values = [cell.get("VarCharValue") for cell in row["Data"]]
            rows.append(dict(zip(columns, values, strict=True)))
    return rows


def _build_response_payload(
    persona: PersonaContext,
    body: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    table = body["table"]
    pii = PII_COLUMNS.get(table, set())
    expected = ALL_COLUMNS.get(table, [])

    redacted_rows: list[dict[str, Any]] = []
    for row in rows:
        normalized = {col: row.get(col) for col in expected}
        for col in pii:
            if col not in row or row.get(col) is None:
                normalized[col] = "REDACTED"
        redacted_rows.append(normalized)

    return {
        "rows": redacted_rows,
        "row_count": len(redacted_rows),
        "table": table,
        "persona": persona.role,
        "question_intent": body.get("question_intent") or "",
    }


def _agent_response(event: dict[str, Any], status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "messageVersion": event.get("messageVersion", "1.0"),
        "response": {
            "actionGroup": event.get("actionGroup"),
            "apiPath": event.get("apiPath"),
            "httpMethod": event.get("httpMethod"),
            "httpStatusCode": status,
            "responseBody": {
                "application/json": {
                    "body": json.dumps(body, default=str),
                }
            },
        },
        "sessionAttributes": event.get("sessionAttributes") or {},
        "promptSessionAttributes": event.get("promptSessionAttributes") or {},
    }

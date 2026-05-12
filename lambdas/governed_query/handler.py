"""Bedrock Agent action group: governed query templates (ADR-008 §4 step 6).

Six named SQL templates over the HVAC home-services schema, dispatched by
the action group's apiPath:

  /customers                  query_customers              SCD2
  /jobs                       query_jobs                   soft-delete
  /signals                    query_signals
  /equipment_telemetry        query_equipment_telemetry
  /technician_utilization     query_technician_utilization
  /truck_rolls                query_truck_rolls            soft-delete

Defaults baked in:
  * SCD2 dimensions filter to is_current = TRUE.
  * Soft-delete facts filter to deleted_at IS NULL.

Optional parameters:
  * as_of_date  (SCD2 templates only) — switches the SCD2 predicate to
    point-in-time (effective_from <= date < effective_to OR effective_to
    IS NULL). Format: YYYY-MM-DD.
  * include_deleted  (soft-delete templates only) — Owner persona only;
    other personas get 400. The Lambda checks the resolved persona.role
    before allowing the parameter through.

Lake Formation enforces row + column visibility on every query under the
assumed persona credentials. Lambda-side predicates are belt-and-braces;
LF is the authoritative gate (ADR-003).
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

PERSONAS = {"dispatcher", "technician_lead", "owner"}
OWNER_PERSONA = "owner"

IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_sts = boto3.client("sts", config=Config(retries={"max_attempts": 3, "mode": "standard"}))


@dataclass
class PersonaContext:
    role: str
    service_region: str | None
    role_arn: str


@dataclass(frozen=True)
class TemplateSpec:
    """Static specification for one action-group tool template."""

    name: str
    table: str
    scd2: bool
    soft_delete: bool
    columns: tuple[str, ...]
    eq_filters: dict[str, str]
    range_filters: dict[str, tuple[str, str]]


CUSTOMER_COLUMNS = (
    "customer_id", "customer_type", "service_tier", "service_region",
    "first_name", "last_name", "email", "phone",
    "street_address", "city", "postal_code", "billing_notes",
)

JOB_COLUMNS = (
    "job_id", "customer_id", "technician_id", "equipment_id",
    "job_type", "scheduled_date", "completed_date", "status",
    "total_billed_usd", "billing_notes", "deleted_at", "deleted_by",
)

SIGNAL_COLUMNS = (
    "signal_date", "customer_id", "engagement_score", "churn_risk",
    "next_best_action", "service_area_health",
)

TELEMETRY_COLUMNS = (
    "equipment_id", "telemetry_date", "runtime_hours", "cycle_count",
    "fault_code_count", "efficiency_index", "predicted_failure_30d",
    "last_service_age_days",
)

UTILIZATION_COLUMNS = (
    "technician_id", "utilization_date", "jobs_completed",
    "billable_hours", "revenue_generated_usd",
    "customer_satisfaction_avg", "parts_consumed_cost_usd", "idle_hours",
)

TRUCK_ROLL_COLUMNS = (
    "truck_roll_id", "job_id", "technician_id", "equipment_id",
    "dispatch_ts", "return_ts", "miles_driven",
    "parts_pulled", "parts_returned", "outcome",
    "deleted_at", "deleted_by",
)


TEMPLATES: dict[str, TemplateSpec] = {
    "/customers": TemplateSpec(
        name="query_customers",
        table="customer",
        scd2=True,
        soft_delete=False,
        columns=CUSTOMER_COLUMNS,
        eq_filters={
            "customer_id": "customer_id",
            "customer_type": "customer_type",
            "service_tier": "service_tier",
            "service_region": "service_region",
            "city": "city",
            "postal_code": "postal_code",
        },
        range_filters={},
    ),
    "/jobs": TemplateSpec(
        name="query_jobs",
        table="service_job",
        scd2=False,
        soft_delete=True,
        columns=JOB_COLUMNS,
        eq_filters={
            "job_id": "job_id",
            "customer_id": "customer_id",
            "technician_id": "technician_id",
            "equipment_id": "equipment_id",
            "status": "status",
            "job_type": "job_type",
        },
        range_filters={
            "scheduled_date_from": ("scheduled_date", ">="),
            "scheduled_date_to": ("scheduled_date", "<="),
            "completed_date_from": ("completed_date", ">="),
            "completed_date_to": ("completed_date", "<="),
        },
    ),
    "/signals": TemplateSpec(
        name="query_signals",
        table="customer_signal_daily",
        scd2=False,
        soft_delete=False,
        columns=SIGNAL_COLUMNS,
        eq_filters={
            "customer_id": "customer_id",
            "next_best_action": "next_best_action",
        },
        range_filters={
            "signal_date_from": ("signal_date", ">="),
            "signal_date_to": ("signal_date", "<="),
        },
    ),
    "/equipment_telemetry": TemplateSpec(
        name="query_equipment_telemetry",
        table="equipment_telemetry_daily",
        scd2=False,
        soft_delete=False,
        columns=TELEMETRY_COLUMNS,
        eq_filters={
            "equipment_id": "equipment_id",
        },
        range_filters={
            "telemetry_date_from": ("telemetry_date", ">="),
            "telemetry_date_to": ("telemetry_date", "<="),
            "min_predicted_failure_30d": ("predicted_failure_30d", ">="),
        },
    ),
    "/technician_utilization": TemplateSpec(
        name="query_technician_utilization",
        table="technician_utilization_daily",
        scd2=False,
        soft_delete=False,
        columns=UTILIZATION_COLUMNS,
        eq_filters={
            "technician_id": "technician_id",
        },
        range_filters={
            "utilization_date_from": ("utilization_date", ">="),
            "utilization_date_to": ("utilization_date", "<="),
        },
    ),
    "/truck_rolls": TemplateSpec(
        name="query_truck_rolls",
        table="truck_roll",
        scd2=False,
        soft_delete=True,
        columns=TRUCK_ROLL_COLUMNS,
        eq_filters={
            "truck_roll_id": "truck_roll_id",
            "job_id": "job_id",
            "technician_id": "technician_id",
            "equipment_id": "equipment_id",
            "outcome": "outcome",
        },
        range_filters={
            "dispatch_ts_from": ("dispatch_ts", ">="),
            "dispatch_ts_to": ("dispatch_ts", "<="),
        },
    ),
}


class BadRequest(ValueError):
    """Raised for malformed or disallowed action-group inputs."""


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    logger.info("invocation: action_group=%s api_path=%s session=%s",
                event.get("actionGroup"), event.get("apiPath"),
                event.get("sessionId"))

    try:
        persona = _resolve_persona(event)
        template = _resolve_template(event)
        body = _parse_request_body(event, template, persona)
        rows, columns = _run_query(persona, template, body)
        response_body: dict[str, Any] = {
            "rows": rows,
            "row_count": len(rows),
            "columns": columns,
            "template": template.name,
            "persona": persona.role,
            "question_intent": body.get("question_intent") or "",
        }
        if rows and not body.get("preview"):
            response_body["markdown_table"] = _render_markdown_table(rows, columns)
        return _agent_response(event, 200, response_body)
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

    service_region = merged.get("service_region")
    if role == "technician_lead" and not service_region:
        raise BadRequest(
            "technician_lead persona requires a 'service_region' session attribute."
        )

    role_arn = _persona_role_arn(role)
    return PersonaContext(role=role, service_region=service_region, role_arn=role_arn)


def _persona_role_arn(role: str) -> str:
    account_id = _sts.get_caller_identity()["Account"]
    role_name_part = "technician-lead" if role == "technician_lead" else role
    return f"arn:aws:iam::{account_id}:role/{ROLE_NAME_PREFIX}{role_name_part}-{ENV}"


def _resolve_template(event: dict[str, Any]) -> TemplateSpec:
    api_path = (event.get("apiPath") or "").strip()
    template = TEMPLATES.get(api_path)
    if template is None:
        raise BadRequest(
            f"unknown apiPath {api_path!r}; expected one of {sorted(TEMPLATES)}."
        )
    return template


def _parse_request_body(
    event: dict[str, Any],
    template: TemplateSpec,
    persona: PersonaContext,
) -> dict[str, Any]:
    raw = _extract_body(event)

    question_intent = (raw.get("question_intent") or "").strip()

    limit_raw = raw.get("limit")
    try:
        limit = int(limit_raw) if limit_raw is not None else 15
    except (TypeError, ValueError) as e:
        raise BadRequest("limit must be an integer.") from e
    limit = max(1, min(200, limit))

    as_of_date = raw.get("as_of_date")
    if as_of_date:
        as_of_date = str(as_of_date).strip()
        if not template.scd2:
            raise BadRequest(
                f"{template.name} does not support as_of_date "
                "(table is not SCD2)."
            )
        if not ISO_DATE_RE.match(as_of_date):
            raise BadRequest("as_of_date must be YYYY-MM-DD.")
    else:
        as_of_date = None

    include_deleted = bool(raw.get("include_deleted"))
    if include_deleted:
        if not template.soft_delete:
            raise BadRequest(
                f"{template.name} does not support include_deleted "
                "(table is not soft-delete)."
            )
        if persona.role != OWNER_PERSONA:
            raise BadRequest(
                "include_deleted requires the owner persona; "
                f"got {persona.role!r}."
            )

    # `preview` is the data-preview mode used by the gateway Lambda's
    # /preview path. It selects * instead of an explicit column list so
    # Lake Formation transparently filters down to the columns the persona
    # can see — turning column-level deny (the agent path retries) into a
    # missing-column UX (the preview path renders whatever LF returns).
    preview = bool(raw.get("preview"))

    filters_raw = raw.get("filters") or {}
    if not isinstance(filters_raw, dict):
        raise BadRequest("filters must be an object of column->value pairs.")

    eq_filters: dict[str, str] = {}
    range_filters: dict[str, str] = {}
    for fname, fval in filters_raw.items():
        if fname in template.eq_filters:
            eq_filters[fname] = str(fval)
        elif fname in template.range_filters:
            range_filters[fname] = str(fval)
        else:
            raise BadRequest(
                f"filter {fname!r} not allowed for {template.name}."
            )

    return {
        "question_intent": question_intent,
        "limit": limit,
        "as_of_date": as_of_date,
        "include_deleted": include_deleted,
        "eq_filters": eq_filters,
        "range_filters": range_filters,
        "preview": preview,
    }


def _extract_body(event: dict[str, Any]) -> dict[str, Any]:
    request_body = event.get("requestBody") or {}
    content = request_body.get("content") or {}
    json_body = content.get("application/json") or {}
    if "properties" in json_body:
        return {p["name"]: _coerce_property(p) for p in json_body["properties"]}
    raw = json_body.get("body")
    if isinstance(raw, str):
        return json.loads(raw)
    return raw or {}


def _coerce_property(prop: dict[str, Any]) -> Any:
    value = prop.get("value")
    type_ = (prop.get("type") or "string").lower()
    if value is None:
        return None
    if type_ == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if type_ == "number":
        try:
            return float(value)
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


def build_query(template: TemplateSpec, parsed: dict[str, Any]) -> tuple[str, list[str]]:
    """Compose the parameterized SQL for a template.

    Public for testing. Pure function: no AWS calls.
    """
    if not IDENT_RE.match(template.table):
        raise BadRequest("invalid table identifier.")

    where_parts: list[str] = []
    params: list[str] = []

    if template.scd2:
        if parsed["as_of_date"]:
            # Athena parameter binding substitutes ? as raw text, so a
            # bound `2026-03-01` parses as integer arithmetic (= 2022).
            # The as_of_date string is regex-validated upstream as
            # ^\d{4}-\d{2}-\d{2}$, so it's safe to inline as a SQL
            # `timestamp 'YYYY-MM-DD'` literal — no injection surface.
            as_of = parsed["as_of_date"]
            if not ISO_DATE_RE.match(as_of):
                raise BadRequest("as_of_date must be YYYY-MM-DD.")
            where_parts.append(
                f"effective_from <= timestamp '{as_of}' "
                f"AND (effective_to IS NULL OR effective_to > timestamp '{as_of}')"
            )
        else:
            where_parts.append("is_current = TRUE")

    if template.soft_delete and not parsed["include_deleted"]:
        where_parts.append("deleted_at IS NULL")

    for fname, fval in parsed["eq_filters"].items():
        col = template.eq_filters[fname]
        if not IDENT_RE.match(col):
            raise BadRequest(f"invalid filter column {col!r}.")
        where_parts.append(f"{col} = ?")
        params.append(fval)

    for fname, fval in parsed["range_filters"].items():
        col, op = template.range_filters[fname]
        if not IDENT_RE.match(col) or op not in (">=", "<=", ">", "<"):
            raise BadRequest(f"invalid range filter for {fname!r}.")
        where_parts.append(f"{col} {op} ?")
        params.append(fval)

    cols = "*" if parsed.get("preview") else ", ".join(template.columns)
    where = " WHERE " + " AND ".join(where_parts) if where_parts else ""
    sql = f"SELECT {cols} FROM {template.table}{where} LIMIT {parsed['limit']}"
    return sql, params


def _run_query(
    persona: PersonaContext,
    template: TemplateSpec,
    parsed: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    creds = _assume_persona(persona)
    athena = boto3.client(
        "athena",
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )

    sql, params = build_query(template, parsed)
    logger.info("athena query persona=%s template=%s sql=%s",
                persona.role, template.name, sql)

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
    if persona.service_region:
        tags.append({"Key": "service_region", "Value": persona.service_region})

    response = _sts.assume_role(
        RoleArn=persona.role_arn,
        RoleSessionName=f"agent-{persona.role}-{uuid.uuid4().hex[:8]}",
        Tags=tags,
        TransitiveTagKeys=[t["Key"] for t in tags],
        DurationSeconds=900,
    )
    return response["Credentials"]


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


def _fetch_results(
    athena, execution_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
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
    return rows, columns


def _render_markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    """Pre-render the result set as a GitHub-flavored markdown table.

    Why: the foundation model formatting 15+ rows into a table dominates
    end-to-end latency. Returning a ready-to-paste string lets the agent
    pass it through verbatim instead.
    """
    def _cell(value: Any) -> str:
        if value is None:
            return ""
        return str(value).replace("|", "\\|").replace("\n", " ")

    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join(["---"] * len(columns)) + " |"
    body_lines = [
        "| " + " | ".join(_cell(row.get(col)) for col in columns) + " |"
        for row in rows
    ]
    return "\n".join([header, separator, *body_lines])


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

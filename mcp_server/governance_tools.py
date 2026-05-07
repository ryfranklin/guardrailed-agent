"""Governance / cost / audit tools (ADR-009 Phase 2.b).

Three tools, all read-only against the catalog + policy + CloudWatch
Logs + CloudTrail:

  explain_governance(query, persona)
    Static probe — does NOT execute the query. Walks each table referenced
    in the SQL, fetches column LF tags + persona's tag-policy grants, and
    reports redacted_columns / row_filters / grant_evidence.

  eval_query(query, persona)
    Pre-flight cost + grant report. Estimates scanned bytes from Glue
    table parameters, projects USD cost from the workgroup's per-TB rate,
    and includes the persona's grant set.

  audit_trace(session_id)
    Given an invocation session_id, looks up the matching CloudWatch
    invocation log entry and the CloudTrail events that fired under the
    same role-session-name. Closes the "agent answered → here is the
    provenance" loop, AgentCore Observability native.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from .governance import (
    ATHENA_USD_PER_TB,
    BYTES_PER_TB,
    ColumnVisibility,
    compute_column_visibility,
    extract_table_names,
    fetch_column_lf_tags,
    fetch_persona_tag_grants,
    fetch_table_metadata,
    grant_to_dict,
    human_bytes,
    project_athena_cost,
)
from .operability_tools import _query_logs_insights, _try_float, _try_int, _try_json
from .state import ServerState
from .tools import _resolve_call_persona

logger = logging.getLogger("mcp_server.governance_tools")


GOVERNANCE_TOOL_DEFINITIONS = [
    {
        "name": "explain_governance",
        "description": (
            "Predict, without executing, what Lake Formation would do to a "
            "query under a given persona. Walks every referenced table, "
            "fetches column LF tags + the persona's tag-policy grants, and "
            "returns: redacted_columns (columns the persona cannot see and "
            "why), row_filters (Phase 2 LF row filter — empty in Phase 1), "
            "and grant_evidence (the actual tag expressions on the persona)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Athena SQL to probe. Not executed.",
                },
                "persona": {
                    "type": "string",
                    "enum": ["dispatcher", "technician_lead", "owner"],
                    "description": "Persona to probe. Defaults to server default.",
                },
                "service_region": {
                    "type": "string",
                    "description": "Required when persona=technician_lead.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "eval_query",
        "description": (
            "Pre-flight cost + grant report for a proposed Athena query. "
            "Returns scanned-bytes estimate (from Glue table parameters), "
            "projected USD cost at the workgroup's per-TB rate, and the "
            "persona's effective grant set against every referenced table. "
            "The query is NOT executed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "persona": {
                    "type": "string",
                    "enum": ["dispatcher", "technician_lead", "owner"],
                },
                "service_region": {"type": "string"},
                "usd_per_tb": {
                    "type": "number",
                    "description": (
                        "Override the Athena per-TB price (default 5.00 USD)."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "audit_trace",
        "description": (
            "Given an invocation session_id, fetches the CloudWatch log "
            "entry that gagent_client wrote for that turn and looks up "
            "matching CloudTrail events scoped by role-session-name. "
            "Returns the trace, events grouped by EventName, and the "
            "time window queried. Closes the loop between 'the agent "
            "answered' and 'here is the complete provenance trail'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": (
                        "Invocation session_id (also accepted as trace_id "
                        "for backwards-compat)."
                    ),
                },
                "trace_id": {
                    "type": "string",
                    "description": "Alias for session_id (deprecated).",
                },
                "lookback_hours": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 720,
                    "default": 168,
                    "description": (
                        "Hours of log history to scan when locating the "
                        "session entry (default 7 days)."
                    ),
                },
                "window_minutes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 60,
                    "default": 5,
                    "description": (
                        "Minutes of CloudTrail history to scan around the "
                        "trace start time."
                    ),
                },
            },
        },
    },
]


# ---- explain_governance ----

def explain_governance_impl(state: ServerState, args: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query")
    if not query or not isinstance(query, str):
        return {"error": "query is required and must be a string"}

    cfg = state.config
    if not cfg.glue_database:
        return {"error": "glue_database not configured"}

    resolved = _resolve_call_persona(state, args)
    if isinstance(resolved, dict):
        return resolved
    persona = resolved

    known_tables = _known_tables_from_args(args)
    tables = extract_table_names(query, known_tables)
    if not tables:
        return {
            "error": "no recognized tables found in query",
            "query": query,
            "known_tables_count": len(known_tables),
        }

    try:
        glue, lf = _governance_clients(cfg.region)
    except ClientError as exc:
        return _client_error_dict("AWS client init", exc)

    try:
        grants = fetch_persona_tag_grants(lf, persona.role_arn)
    except ClientError as exc:
        return _client_error_dict("ListPermissions", exc)

    redacted: list[dict[str, Any]] = []
    visible: list[dict[str, Any]] = []
    table_reports: list[dict[str, Any]] = []

    for table in tables:
        try:
            columns, _, _ = fetch_table_metadata(glue, cfg.glue_database, table)
            column_tags = fetch_column_lf_tags(lf, cfg.glue_database, table, columns)
        except ClientError as exc:
            return _client_error_dict(f"probe {table}", exc)

        per_column: list[ColumnVisibility] = []
        for col in columns:
            v = compute_column_visibility(col, column_tags.get(col, {}), grants)
            per_column.append(v)
            entry = {
                "table": table,
                "column": col,
                "tags": v.tags,
                "reason": v.reason,
            }
            if v.visible:
                visible.append(entry)
            else:
                redacted.append(entry)

        table_reports.append({
            "table": table,
            "column_count": len(columns),
            "redacted": [v.column for v in per_column if not v.visible],
            "visible": [v.column for v in per_column if v.visible],
        })

    return {
        "query": query,
        "persona": persona.role,
        "service_region": persona.service_region,
        "tables_referenced": tables,
        "redacted_columns": redacted,
        "visible_columns": visible,
        "row_filters": [],  # Phase 2 LF row-filter — placeholder, see ADR-003 open items
        "grant_evidence": [grant_to_dict(g) for g in grants if not g.resource_type or g.resource_type == "TABLE"],
        "table_reports": table_reports,
    }


# ---- eval_query ----

def eval_query_impl(state: ServerState, args: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query")
    if not query or not isinstance(query, str):
        return {"error": "query is required and must be a string"}

    cfg = state.config
    if not cfg.glue_database:
        return {"error": "glue_database not configured"}

    resolved = _resolve_call_persona(state, args)
    if isinstance(resolved, dict):
        return resolved
    persona = resolved

    usd_per_tb = float(args.get("usd_per_tb") or ATHENA_USD_PER_TB)
    known_tables = _known_tables_from_args(args)
    tables = extract_table_names(query, known_tables)
    if not tables:
        return {
            "error": "no recognized tables found in query",
            "query": query,
        }

    try:
        glue, lf = _governance_clients(cfg.region)
        s3 = boto3.client("s3", region_name=cfg.region)
    except ClientError as exc:
        return _client_error_dict("AWS client init", exc)

    try:
        grants = fetch_persona_tag_grants(lf, persona.role_arn)
    except ClientError as exc:
        return _client_error_dict("ListPermissions", exc)

    table_stats: list[dict[str, Any]] = []
    total_bytes = 0
    warnings: list[str] = []

    for table in tables:
        try:
            columns, size_bytes, row_count = fetch_table_metadata(
                glue, cfg.glue_database, table, s3=s3,
            )
        except ClientError as exc:
            return _client_error_dict(f"GetTable {table}", exc)
        table_stats.append({
            "table": table,
            "column_count": len(columns),
            "size_bytes_estimate": size_bytes,
            "size_human": human_bytes(size_bytes),
            "row_count_estimate": row_count,
        })
        if size_bytes is None:
            warnings.append(
                f"{table}: no size statistic in Glue parameters; cost estimate excludes it",
            )
        else:
            total_bytes += size_bytes

    cost_estimate = project_athena_cost(total_bytes, usd_per_tb=usd_per_tb)

    return {
        "query": query,
        "persona": persona.role,
        "service_region": persona.service_region,
        "tables_referenced": tables,
        "table_stats": table_stats,
        "scanned_bytes_estimate": total_bytes,
        "scanned_bytes_human": human_bytes(total_bytes),
        "cost_estimate_usd": round(cost_estimate, 6),
        "cost_per_tb_usd": usd_per_tb,
        "bytes_per_tb": BYTES_PER_TB,
        "grant_set": [grant_to_dict(g) for g in grants],
        "warnings": warnings,
        "notes": [
            "Estimate is a static upper bound from Glue table parameters. "
            "Actual scan is typically smaller because Iceberg + Athena prune "
            "files by filter / projection. Compare to "
            "QueryExecution.Statistics.DataScannedInBytes after running.",
        ],
    }


# ---- audit_trace ----

AUDIT_TRACE_FIELDS = (
    "@timestamp, session_id, persona, role_arn, role_session_name, "
    "surface, trace_name, duration_seconds, tools_called, "
    "guardrail_blocks, input, output, started_at, finished_at, metadata"
)


def audit_trace_impl(state: ServerState, args: dict[str, Any]) -> dict[str, Any]:
    session_id = args.get("session_id") or args.get("trace_id")
    if not session_id or not isinstance(session_id, str):
        return {"error": "session_id is required and must be a string"}

    cfg = state.config
    if not cfg.log_group:
        return {"error": "log_group not configured; cannot locate trace"}

    try:
        lookback_hours = int(args.get("lookback_hours") or 168)
    except (TypeError, ValueError):
        return {"error": "lookback_hours must be an integer"}
    lookback_hours = max(1, min(720, lookback_hours))

    try:
        window_minutes = int(args.get("window_minutes") or 5)
    except (TypeError, ValueError):
        return {"error": "window_minutes must be an integer"}
    window_minutes = max(1, min(60, window_minutes))

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=lookback_hours)
    query_string = (
        f"fields {AUDIT_TRACE_FIELDS} "
        f'| filter session_id = "{session_id}" '
        "| sort @timestamp desc "
        "| limit 1"
    )

    try:
        rows = _query_logs_insights(
            region=cfg.region,
            log_group=cfg.log_group,
            query_string=query_string,
            start=start,
            end=end,
            limit=1,
        )
    except (ClientError, RuntimeError, TimeoutError) as exc:
        logger.exception("Logs Insights audit_trace query failed")
        return {"error": f"Logs Insights query failed: {type(exc).__name__}: {exc}"}

    if not rows:
        return {
            "error": (
                f"session_id {session_id!r} not found in {cfg.log_group} "
                f"within last {lookback_hours}h"
            ),
        }
    row = rows[0]

    role_session_name = row.get("role_session_name")
    persona = row.get("persona")
    role_arn = row.get("role_arn")
    started_dt = _coerce_dt(row.get("started_at") or row.get("@timestamp"))
    finished_dt = _coerce_dt(row.get("finished_at")) or started_dt

    if started_dt is None:
        return {
            "error": "log entry has no usable timestamp; cannot scope CloudTrail lookup",
            "trace": _trace_summary(row),
        }

    window_start = started_dt - timedelta(minutes=1)
    window_end = (finished_dt or started_dt) + timedelta(minutes=window_minutes)

    try:
        cloudtrail = boto3.client("cloudtrail", region_name=cfg.region)
        events = _lookup_events(
            cloudtrail,
            start=window_start, end=window_end,
            role_session_name=role_session_name,
        )
    except ClientError as exc:
        return _client_error_dict("CloudTrail LookupEvents", exc)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        name = ev.get("EventName") or "Unknown"
        grouped.setdefault(name, []).append({
            "EventId": ev.get("EventId"),
            "EventName": name,
            "EventTime": _isoformat(ev.get("EventTime")),
            "Username": ev.get("Username"),
            "EventSource": ev.get("EventSource"),
            "Resources": ev.get("Resources") or [],
        })

    return {
        "session_id": session_id,
        "trace": _trace_summary(row),
        "persona": persona,
        "role_arn": role_arn,
        "role_session_name": role_session_name,
        "log_group": cfg.log_group,
        "window": {
            "start": _isoformat(window_start),
            "end": _isoformat(window_end),
            "minutes": window_minutes,
        },
        "cloudtrail_event_count": len(events),
        "events_by_name": grouped,
    }


# ---- helpers ----

def _governance_clients(region: str) -> tuple[Any, Any]:
    glue = boto3.client("glue", region_name=region)
    lf = boto3.client("lakeformation", region_name=region)
    return glue, lf


def _known_tables_from_args(args: dict[str, Any]) -> set[str]:
    """Return the set of allowed table names. Hardcoded to ADR-008 schema."""
    return {
        "customer", "technician", "equipment", "service_job", "review",
        "customer_signal_daily", "parts_inventory", "dispatch_event",
        "truck_roll", "warranty_claim", "equipment_telemetry_daily",
        "technician_utilization_daily",
    }


def _client_error_dict(label: str, exc: ClientError) -> dict[str, Any]:
    code = exc.response.get("Error", {}).get("Code", "Unknown")
    msg = exc.response.get("Error", {}).get("Message", str(exc))
    return {"error": f"{label}: {code}", "message": msg}


def _trace_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Compact view of an invocation log entry."""
    return {
        "session_id": row.get("session_id"),
        "trace_name": row.get("trace_name"),
        "timestamp": row.get("@timestamp"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "duration_seconds": _try_float(row.get("duration_seconds")),
        "tools_called": _try_json(row.get("tools_called")) or [],
        "guardrail_blocks": _try_int(row.get("guardrail_blocks")) or 0,
        "surface": row.get("surface"),
        "input": row.get("input"),
        "output_preview": _truncate(str(row.get("output") or ""), 200),
    }


def _lookup_events(
    cloudtrail: Any,
    *,
    start: datetime,
    end: datetime,
    role_session_name: str | None,
) -> list[dict[str, Any]]:
    """LookupEvents within [start, end], optionally filtered by Username."""
    out: list[dict[str, Any]] = []
    paginator = cloudtrail.get_paginator("lookup_events")
    kwargs: dict[str, Any] = {
        "StartTime": start,
        "EndTime": end,
        "MaxResults": 50,
    }
    if role_session_name:
        kwargs["LookupAttributes"] = [
            {"AttributeKey": "Username", "AttributeValue": role_session_name},
        ]
    for page in paginator.paginate(**kwargs):
        out.extend(page.get("Events") or [])
        if len(out) >= 250:
            break
    return out


def _coerce_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            # CloudWatch Logs Insights returns ISO 8601 strings.
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _isoformat(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"

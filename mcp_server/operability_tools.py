"""Phase 2.c — propose_query / recent_traces / health (ADR-009 §Tools 7-9).

These three together close the operability story: the operator (or
agent) can rehearse a query before running it, see what the agent has
been doing recently, and confirm the upstream surface is reachable.

  propose_query     drafts SQL via Bedrock Runtime InvokeModel — does
                    NOT touch the action-group Lambda, so no Athena
                    query can fire (verified in unit tests by mocking
                    boto3.client and asserting no bedrock-agent-runtime
                    construction).

  recent_traces     queries the gagent invocation log group via
                    CloudWatch Logs Insights, optionally filtered by
                    persona. Returns timing, tools_called, guardrail
                    interventions for each invocation (AgentCore
                    Observability native).

  health            concurrent reachability checks against Bedrock
                    Runtime, Athena, Glue, Lake Formation, plus the
                    last-successful-invocation timestamp per persona
                    from CloudWatch Logs. Targets <3s total wall-clock.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from .governance import extract_table_names
from .state import ServerState
from .tools import _resolve_call_persona

logger = logging.getLogger("mcp_server.operability_tools")


# Pre-ADR-008 persona names map cleanly to the current taxonomy. Historical
# traces from before the rename still appear in the catalog; we map them so
# audit views aren't fragmented by the rename.
LEGACY_PERSONA_MAP: dict[str, str] = {
    "Analyst": "dispatcher",
    "analyst": "dispatcher",
    "RegionalManager": "technician_lead",
    "regional_manager": "technician_lead",
    "Admin": "owner",
    "admin": "owner",
}

CURRENT_PERSONAS: tuple[str, ...] = ("dispatcher", "technician_lead", "owner")


def _normalize_persona_name(raw: str | None) -> tuple[str | None, bool]:
    """Return (canonical_name, is_legacy) for a persona string from trace metadata.

    Unknown or missing names pass through unchanged with is_legacy=False so
    callers can decide how to surface them.
    """
    if raw is None:
        return None, False
    if raw in CURRENT_PERSONAS:
        return raw, False
    mapped = LEGACY_PERSONA_MAP.get(raw)
    if mapped is not None:
        return mapped, True
    return raw, False


PHASE_2C_TOOL_DEFINITIONS = [
    {
        "name": "propose_query",
        "description": (
            "Draft the SQL the agent would run for a given question, "
            "WITHOUT executing it. Asks Claude directly via Bedrock "
            "Runtime InvokeModel — the action-group Lambda is never "
            "invoked. Use this for human-in-the-loop review before "
            "running a sensitive query (pair with explain_governance)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Natural-language question for the agent.",
                },
                "persona": {
                    "type": "string",
                    "enum": ["dispatcher", "technician_lead", "owner"],
                    "description": (
                        "Persona under which the proposed query would run. "
                        "Affects how the model thinks about redaction."
                    ),
                },
                "service_region": {
                    "type": "string",
                    "description": "Required when persona=technician_lead.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "recent_traces",
        "description": (
            "Return the N most recent agent invocations from CloudWatch "
            "Logs Insights, optionally filtered by persona. Each entry "
            "includes timing, tool calls, guardrail intervention count, "
            "and the role-session-name needed for CloudTrail correlation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "persona": {
                    "type": "string",
                    "enum": ["dispatcher", "technician_lead", "owner"],
                    "description": "Optional persona filter.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 10,
                },
                "hours": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 720,
                    "default": 168,
                    "description": "Lookback window in hours (default 7 days).",
                },
            },
        },
    },
    {
        "name": "health",
        "description": (
            "Reachability + last-success snapshot. Probes Bedrock "
            "Runtime (1-token InvokeModel), Athena workgroup, Glue "
            "catalog, Lake Formation settings, and the CloudWatch "
            "invocation log group for the last-successful invocation "
            "per persona. Designed to complete in under 3 seconds on a "
            "warm region."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ---- propose_query ----

PROPOSE_SYSTEM_PROMPT = (
    "You are an HVAC home-services analyst with read-only access to a "
    "governed Athena dataset (the ADR-008 schema: customer, technician, "
    "equipment, service_job, review, customer_signal_daily, "
    "parts_inventory, dispatch_event, truck_roll, warranty_claim, "
    "equipment_telemetry_daily, technician_utilization_daily).\n\n"
    "DO NOT execute any tools. You will be given one natural-language "
    "question. Respond with ONLY a JSON object of the form:\n"
    "{\n"
    '  "drafted_sql": "<the SQL you would run, or null if a tool call is '
    'simpler>",\n'
    '  "chosen_tool": "/customers" | "/jobs" | "/signals" | '
    '"/equipment_telemetry" | "/technician_utilization" | "/truck_rolls",\n'
    '  "tool_arguments": {<JSON arguments to the chosen tool>},\n'
    '  "rationale": "<2-3 sentences>"\n'
    "}\n\n"
    "Notes:\n"
    "- SCD2 dimensions (customer, technician, equipment, parts_inventory) "
    "default to is_current=TRUE; supply as_of_date for point-in-time.\n"
    "- Soft-delete facts (service_job, review, truck_roll, warranty_claim) "
    "default to deleted_at IS NULL; include_deleted is Owner-only.\n"
    "- Lake Formation hides PII columns from Dispatcher and "
    "sensitivity=high columns from Dispatcher and TechnicianLead.\n"
    "Persona for this request: {persona}."
)

# Default model when terraform output isn't available. Matches the agent
# module's default in terraform/modules/agent/variables.tf.
DEFAULT_PROPOSE_MODEL_ID = "us.anthropic.claude-sonnet-4-6"


def propose_query_impl(state: ServerState, args: dict[str, Any]) -> dict[str, Any]:
    question = args.get("question")
    if not question or not isinstance(question, str):
        return {"error": "question is required and must be a string"}

    resolved = _resolve_call_persona(state, args)
    if isinstance(resolved, dict):
        return resolved
    persona = resolved

    cfg = state.config
    model_id = (
        args.get("model_id")
        or _config_attr(cfg, "foundation_model_id")
        or DEFAULT_PROPOSE_MODEL_ID
    )

    try:
        bedrock = boto3.client("bedrock-runtime", region_name=cfg.region)
    except ClientError as exc:
        return _client_error_dict("bedrock-runtime client init", exc)

    system = PROPOSE_SYSTEM_PROMPT.replace("{persona}", persona.role)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "system": system,
        "messages": [{"role": "user", "content": question}],
    }

    try:
        response = bedrock.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body).encode("utf-8"),
        )
    except ClientError as exc:
        return _client_error_dict("InvokeModel", exc)

    raw = response["body"].read()
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"error": "could not parse Bedrock response", "raw": str(raw)[:500]}

    text = _extract_text(payload)
    parsed = _parse_proposal_json(text)
    inferred_tool = parsed.get("chosen_tool") or _infer_tool_from_sql(parsed.get("drafted_sql"))

    return {
        "question": question,
        "persona": persona.role,
        "service_region": persona.service_region,
        "model_id": model_id,
        "drafted_sql": parsed.get("drafted_sql"),
        "chosen_tool": inferred_tool,
        "tool_arguments": parsed.get("tool_arguments") or {},
        "rationale": parsed.get("rationale", ""),
        "executed": False,
        "raw_text": text if not parsed else None,
    }


def _extract_text(payload: dict[str, Any]) -> str:
    """Extract text from a Bedrock Runtime InvokeModel response (Claude-style)."""
    content = payload.get("content") or []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text", "")
    return ""


def _parse_proposal_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of the model output.

    Uses ``json.JSONDecoder.raw_decode`` so the parser correctly handles
    nested objects (the regex approach broke at two-deep nesting).
    """
    if not text:
        return {}
    text = text.strip()
    # Try direct parse first.
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except (TypeError, json.JSONDecodeError):
        pass
    # Scan for the first JSON object; raw_decode handles nesting natively.
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def _infer_tool_from_sql(sql: str | None) -> str | None:
    """Map a drafted SQL's primary table to one of the six action-group apiPaths."""
    if not sql:
        return None
    table_to_path = {
        "customer": "/customers",
        "service_job": "/jobs",
        "customer_signal_daily": "/signals",
        "equipment_telemetry_daily": "/equipment_telemetry",
        "technician_utilization_daily": "/technician_utilization",
        "truck_roll": "/truck_rolls",
    }
    tables = extract_table_names(sql, set(table_to_path))
    return table_to_path.get(tables[0]) if tables else None


# ---- recent_traces ----

# Fields pulled from each invocation log entry. Insights returns each
# value as a string; arrays/objects come back JSON-encoded.
RECENT_TRACES_FIELDS = (
    "@timestamp, session_id, persona, role_arn, role_session_name, "
    "surface, trace_name, duration_seconds, tools_called, "
    "guardrail_blocks, input, output, started_at, finished_at, metadata"
)

DEFAULT_RECENT_TRACES_HOURS = 168
INSIGHTS_QUERY_TIMEOUT_S = 5.0


def recent_traces_impl(state: ServerState, args: dict[str, Any]) -> dict[str, Any]:
    cfg = state.config
    if not cfg.log_group:
        return {"error": "log_group not configured"}

    persona_filter = args.get("persona")
    if persona_filter is not None and persona_filter not in (
        "dispatcher", "technician_lead", "owner",
    ):
        return {"error": f"invalid persona filter: {persona_filter!r}"}

    try:
        limit = int(args.get("limit") or 10)
    except (TypeError, ValueError):
        return {"error": "limit must be an integer"}
    limit = max(1, min(100, limit))

    try:
        hours = int(args.get("hours") or DEFAULT_RECENT_TRACES_HOURS)
    except (TypeError, ValueError):
        return {"error": "hours must be an integer"}
    hours = max(1, min(720, hours))

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    parts = [f"fields {RECENT_TRACES_FIELDS}"]
    if persona_filter:
        parts.append(f'filter persona = "{persona_filter}"')
    parts.append("sort @timestamp desc")
    parts.append(f"limit {limit}")
    query_string = " | ".join(parts)

    try:
        rows = _query_logs_insights(
            region=cfg.region,
            log_group=cfg.log_group,
            query_string=query_string,
            start=start,
            end=end,
            limit=limit,
        )
    except (ClientError, RuntimeError, TimeoutError) as exc:
        logger.exception("CloudWatch Logs Insights recent_traces query failed")
        return {"error": f"Logs Insights query failed: {type(exc).__name__}: {exc}"}

    summarized: list[dict[str, Any]] = []
    legacy_count = 0
    for row in rows:
        raw_persona = row.get("persona")
        canonical, is_legacy = _normalize_persona_name(raw_persona)
        if is_legacy:
            legacy_count += 1
        summarized.append({
            "session_id": row.get("session_id"),
            "trace_name": row.get("trace_name"),
            "timestamp": row.get("@timestamp"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
            "persona": raw_persona,
            "normalized_persona": canonical,
            "persona_is_legacy": is_legacy,
            "role_arn": row.get("role_arn"),
            "role_session_name": row.get("role_session_name"),
            "surface": row.get("surface"),
            "duration_seconds": _try_float(row.get("duration_seconds")),
            "tools_called": _try_json(row.get("tools_called")) or [],
            "guardrail_blocks": _try_int(row.get("guardrail_blocks")) or 0,
            "input_preview": _truncate(row.get("input") or "", 160),
        })

    return {
        "persona_filter": persona_filter,
        "limit": limit,
        "hours": hours,
        "log_group": cfg.log_group,
        "trace_count": len(summarized),
        "legacy_persona_count": legacy_count,
        "legacy_persona_map": LEGACY_PERSONA_MAP,
        "traces": summarized,
    }


def _query_logs_insights(
    *,
    region: str,
    log_group: str,
    query_string: str,
    start: datetime,
    end: datetime,
    limit: int = 100,
    poll_interval: float = 0.4,
    timeout_seconds: float = INSIGHTS_QUERY_TIMEOUT_S,
    logs_client: Any = None,
) -> list[dict[str, str]]:
    """Run a CloudWatch Logs Insights query and return one dict per row.

    Insights is async — start_query returns a queryId, get_query_results
    polls until status=Complete. Each result row is a list of
    {"field": ..., "value": ...} pairs we collapse into a flat dict.
    """
    client = logs_client or boto3.client("logs", region_name=region)
    resp = client.start_query(
        logGroupName=log_group,
        startTime=int(start.timestamp()),
        endTime=int(end.timestamp()),
        queryString=query_string,
        limit=limit,
    )
    qid = resp["queryId"]
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = client.get_query_results(queryId=qid)
        status = result.get("status")
        if status == "Complete":
            return [
                {f["field"]: f.get("value", "") for f in row}
                for row in result.get("results") or []
            ]
        if status in ("Failed", "Cancelled", "Timeout"):
            raise RuntimeError(f"Logs Insights query {status}: {qid}")
        time.sleep(poll_interval)
    try:
        client.stop_query(queryId=qid)
    except ClientError:
        logger.debug("stop_query best-effort failed for %s", qid, exc_info=True)
    raise TimeoutError(f"Logs Insights query exceeded {timeout_seconds}s budget")


def _try_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _try_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _try_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


# ---- health ----

@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "fail" | "skipped"
    duration_ms: int
    detail: Any = None


async def health_impl_async(
    state: ServerState, args: dict[str, Any],
) -> dict[str, Any]:
    """Async health probe — use this from the MCP dispatcher (already in a loop)."""
    return await _health_async(state)


def health_impl(state: ServerState, args: dict[str, Any]) -> dict[str, Any]:
    """Sync wrapper for non-async callers (tests, CLI scripts).

    Raises if called from inside a running event loop — use
    :func:`health_impl_async` directly in that case. The MCP server's
    stdio dispatcher is async; the CLI / unit tests are not.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_health_async(state))
    raise RuntimeError(
        "health_impl() called from inside a running event loop. "
        "Use health_impl_async() instead (mcp_server.server already does).",
    )


async def _health_async(state: ServerState) -> dict[str, Any]:
    cfg = state.config
    started = time.time()

    checks_coro = [
        _check_bedrock(cfg),
        _check_athena(cfg),
        _check_glue(cfg),
        _check_lake_formation(cfg),
        _check_last_invocations(cfg),
    ]
    results: list[CheckResult] = await asyncio.gather(*checks_coro)
    duration = time.time() - started

    overall = "healthy" if all(r.status == "ok" for r in results) else "degraded"
    return {
        "overall": overall,
        "duration_seconds": round(duration, 3),
        "region": cfg.region,
        "checks": [
            {
                "name": r.name,
                "status": r.status,
                "duration_ms": r.duration_ms,
                "detail": r.detail,
            }
            for r in results
        ],
    }


async def _check_bedrock(cfg: Any) -> CheckResult:
    return await asyncio.to_thread(_check_bedrock_sync, cfg)


def _check_bedrock_sync(cfg: Any) -> CheckResult:
    started = time.time()
    model_id = _config_attr(cfg, "foundation_model_id") or DEFAULT_PROPOSE_MODEL_ID
    try:
        bedrock = boto3.client("bedrock-runtime", region_name=cfg.region)
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
        bedrock.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body).encode("utf-8"),
        )
        return CheckResult(
            name="bedrock_runtime",
            status="ok",
            duration_ms=int((time.time() - started) * 1000),
            detail={"model_id": model_id},
        )
    except ClientError as exc:
        return CheckResult(
            name="bedrock_runtime",
            status="fail",
            duration_ms=int((time.time() - started) * 1000),
            detail=_client_error_dict("InvokeModel", exc),
        )


async def _check_athena(cfg: Any) -> CheckResult:
    return await asyncio.to_thread(_check_athena_sync, cfg)


def _check_athena_sync(cfg: Any) -> CheckResult:
    started = time.time()
    workgroup = _config_attr(cfg, "athena_workgroup_name")
    if not workgroup:
        return CheckResult(
            name="athena_workgroup",
            status="skipped",
            duration_ms=int((time.time() - started) * 1000),
            detail="GAGENT_ATHENA_WORKGROUP not configured",
        )
    try:
        athena = boto3.client("athena", region_name=cfg.region)
        response = athena.get_work_group(WorkGroup=workgroup)
        state_str = response.get("WorkGroup", {}).get("State", "UNKNOWN")
        return CheckResult(
            name="athena_workgroup",
            status="ok" if state_str == "ENABLED" else "fail",
            duration_ms=int((time.time() - started) * 1000),
            detail={"workgroup": workgroup, "state": state_str},
        )
    except ClientError as exc:
        return CheckResult(
            name="athena_workgroup",
            status="fail",
            duration_ms=int((time.time() - started) * 1000),
            detail=_client_error_dict("GetWorkGroup", exc),
        )


async def _check_glue(cfg: Any) -> CheckResult:
    return await asyncio.to_thread(_check_glue_sync, cfg)


def _check_glue_sync(cfg: Any) -> CheckResult:
    started = time.time()
    try:
        glue = boto3.client("glue", region_name=cfg.region)
        response = glue.get_databases(MaxResults=1)
        count = len(response.get("DatabaseList") or [])
        return CheckResult(
            name="glue_catalog",
            status="ok",
            duration_ms=int((time.time() - started) * 1000),
            detail={"databases_visible": count},
        )
    except ClientError as exc:
        return CheckResult(
            name="glue_catalog",
            status="fail",
            duration_ms=int((time.time() - started) * 1000),
            detail=_client_error_dict("GetDatabases", exc),
        )


async def _check_lake_formation(cfg: Any) -> CheckResult:
    return await asyncio.to_thread(_check_lake_formation_sync, cfg)


def _check_lake_formation_sync(cfg: Any) -> CheckResult:
    started = time.time()
    try:
        lf = boto3.client("lakeformation", region_name=cfg.region)
        response = lf.get_data_lake_settings()
        admins = (
            response.get("DataLakeSettings", {}).get("DataLakeAdmins", []) or []
        )
        return CheckResult(
            name="lake_formation",
            status="ok",
            duration_ms=int((time.time() - started) * 1000),
            detail={"admin_count": len(admins)},
        )
    except ClientError as exc:
        return CheckResult(
            name="lake_formation",
            status="fail",
            duration_ms=int((time.time() - started) * 1000),
            detail=_client_error_dict("GetDataLakeSettings", exc),
        )


HEALTH_INSIGHTS_TIMEOUT_S = 2.5
HEALTH_INSIGHTS_LOOKBACK_HOURS = 168


async def _check_last_invocations(cfg: Any) -> CheckResult:
    started = time.time()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_check_last_invocations_sync, cfg),
            timeout=HEALTH_INSIGHTS_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return CheckResult(
            name="last_invocations",
            status="degraded",
            duration_ms=int((time.time() - started) * 1000),
            detail=(
                f"Logs Insights query exceeded {HEALTH_INSIGHTS_TIMEOUT_S}s "
                "budget — call recent_traces directly for the full list."
            ),
        )


def _check_last_invocations_sync(cfg: Any) -> CheckResult:
    started = time.time()
    if not getattr(cfg, "log_group", None):
        return CheckResult(
            name="last_invocations",
            status="skipped",
            duration_ms=int((time.time() - started) * 1000),
            detail="log_group not configured",
        )
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=HEALTH_INSIGHTS_LOOKBACK_HOURS)
    # Pull a small batch ordered by recency, then collapse client-side.
    query_string = (
        "fields @timestamp, persona "
        "| sort @timestamp desc "
        "| limit 30"
    )
    try:
        rows = _query_logs_insights(
            region=cfg.region,
            log_group=cfg.log_group,
            query_string=query_string,
            start=start,
            end=end,
            limit=30,
            timeout_seconds=HEALTH_INSIGHTS_TIMEOUT_S - 0.3,
        )
    except (ClientError, RuntimeError, TimeoutError) as exc:
        return CheckResult(
            name="last_invocations",
            status="fail",
            duration_ms=int((time.time() - started) * 1000),
            detail=f"{type(exc).__name__}: {exc}",
        )

    last_by_persona: dict[str, str] = {}
    legacy_seen = False
    for row in rows:
        canonical, is_legacy = _normalize_persona_name(row.get("persona"))
        ts = row.get("@timestamp")
        if (
            canonical in CURRENT_PERSONAS
            and canonical not in last_by_persona
            and ts
        ):
            last_by_persona[canonical] = ts
            if is_legacy:
                legacy_seen = True
        if len(last_by_persona) == len(CURRENT_PERSONAS):
            break
    detail: dict[str, Any] = {
        "log_group": cfg.log_group,
        "last_success_by_persona": last_by_persona,
    }
    if legacy_seen:
        detail["legacy_persona_traces_normalized"] = True
    return CheckResult(
        name="last_invocations",
        status="ok",
        duration_ms=int((time.time() - started) * 1000),
        detail=detail,
    )


# ---- shared helpers ----

def _config_attr(cfg: Any, name: str) -> Any:
    """ServerConfig has a small attribute set; tools 7-9 also read foundation_model_id
    from the loaded terraform outputs even though it isn't a ServerConfig field today.
    Use a duck-typed getattr so the tools work either way."""
    return getattr(cfg, name, None)


def _client_error_dict(label: str, exc: ClientError) -> dict[str, Any]:
    code = exc.response.get("Error", {}).get("Code", "Unknown")
    msg = exc.response.get("Error", {}).get("Message", str(exc))
    return {"error": f"{label}: {code}", "message": msg}


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"

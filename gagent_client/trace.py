"""Bedrock-trace summarization + AgentCore Observability emission.

Two layers:

  In-process aggregation (TraceSummary + summarize_trace)
    Walk the orchestration trace events that come back from
    InvokeAgent(enableTrace=True) and aggregate tool calls + guardrail
    interventions into a small struct the agent path can hand back to
    callers. No I/O.

  AgentCore Observability emission (emit_invocation_log)
    Best-effort write of a single structured JSON entry to a CloudWatch
    Logs group (default ``/gagent/invocations``). The log line carries
    persona, surface, session_id, role_session_name, duration, tool
    calls, guardrail blocks. recent_traces and audit_trace query this
    log group via CloudWatch Logs Insights.

Why CloudWatch Logs: the project's "Bedrock-native" stance in ADR-001 /
ADR-004. AgentCore Observability surfaces this log group in the
CloudWatch console under "GenAI Observability" alongside the agent's
auto-emitted X-Ray traces. One AWS account boundary; one audit surface.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("gagent_client.trace")

DEFAULT_LOG_GROUP = "/gagent/invocations"


@dataclass
class TraceSummary:
    """Aggregated view of one InvokeAgent trace stream."""

    tools_called: list[str] = field(default_factory=list)
    guardrail_blocks: int = 0
    guardrail_events: list[dict[str, Any]] = field(default_factory=list)


def summarize_trace(
    trace: dict[str, Any], summary: TraceSummary,
) -> None:
    """Aggregate one Bedrock trace event into a running summary.

    Tool calls are tracked by apiPath (e.g., /customers) so per-template
    assertions work; falls back to actionGroupName for legacy traces.
    Guardrail interventions are counted and recorded.
    """
    orchestration = trace.get("trace", {}).get("orchestrationTrace", {})
    invocation = orchestration.get("invocationInput", {})
    if "actionGroupInvocationInput" in invocation:
        ag = invocation["actionGroupInvocationInput"]
        api_path = ag.get("apiPath") or ag.get("actionGroupName", "")
        summary.tools_called.append(api_path)

    gr = trace.get("trace", {}).get("guardrailTrace", {})
    if gr:
        action = gr.get("action") or ""
        if action.upper() in ("INTERVENED", "BLOCKED"):
            summary.guardrail_blocks += 1
        summary.guardrail_events.append({"action": action})


def resolve_log_group(env: Mapping[str, str] | None = None) -> str:
    """Resolve the CloudWatch log group name for invocation telemetry."""
    source = env if env is not None else os.environ
    return source.get("GAGENT_LOG_GROUP") or DEFAULT_LOG_GROUP


def emit_invocation_log(
    *,
    session_id: str,
    persona: str,
    role_arn: str,
    role_session_name: str,
    surface: str,
    trace_name: str,
    input_text: str,
    output_text: str,
    summary: TraceSummary,
    duration_seconds: float,
    started_at: float,
    metadata: dict[str, Any] | None = None,
    log_group: str | None = None,
    region: str | None = None,
    logs_client: Any = None,
) -> str | None:
    """Write one structured JSON line to the gagent CloudWatch log group.

    Returns the CloudWatch log stream name on success, ``None`` on
    best-effort failure (missing perms, log group absent, throttling).
    Trace emission is never a hard dependency of the agent path.
    """
    payload = {
        "session_id": session_id,
        "persona": persona,
        "role_arn": role_arn,
        "role_session_name": role_session_name,
        "surface": surface,
        "trace_name": trace_name,
        "input": _truncate(input_text, 4_000),
        "output": _truncate(output_text, 16_000),
        "tools_called": list(summary.tools_called),
        "guardrail_blocks": summary.guardrail_blocks,
        "duration_seconds": round(duration_seconds, 3),
        "started_at": _iso(started_at),
        "finished_at": _iso(time.time()),
        "metadata": metadata or {},
    }
    log_group = log_group or resolve_log_group()
    region = region or os.environ.get("AWS_REGION", "us-east-1")
    stream = _stream_name(persona, session_id)

    try:
        client = logs_client or boto3.client("logs", region_name=region)
        try:
            client.create_log_stream(
                logGroupName=log_group, logStreamName=stream,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "ResourceAlreadyExistsException":
                raise
        client.put_log_events(
            logGroupName=log_group,
            logStreamName=stream,
            logEvents=[{
                "timestamp": int(time.time() * 1000),
                "message": json.dumps(payload, default=str),
            }],
        )
        return stream
    except (ClientError, Exception):  # noqa: BLE001
        logger.exception(
            "CloudWatch Logs emit_invocation_log failed; continuing without trace",
        )
        return None


def _stream_name(persona: str, session_id: str) -> str:
    """Daily stream sharded by persona — keeps cardinality bounded.

    Format: ``YYYY/MM/DD/<persona>/<session_id>``. CloudWatch slashes
    render as folders in the console.
    """
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    return f"{today}/{persona}/{session_id}"


def _iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def _truncate(text: str, max_chars: int) -> str:
    if not text or len(text) <= max_chars:
        return text or ""
    return text[: max_chars - 1] + "…"


def trace_summary_to_dict(summary: TraceSummary) -> dict[str, Any]:
    """Convenience for callers serializing TraceSummary into JSON outputs."""
    return asdict(summary)

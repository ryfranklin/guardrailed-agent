"""invoke() — persona -> STS -> InvokeAgent -> CloudWatch Logs, end to end.

Per ADR-006 step 1, this is the shared pipeline that every Phase 2 surface
(MCP server, gra CLI, SMUS notebook, eval harness) consumes. The
extraction was the precondition for Phase 2 — every surface is a thin
shim on top of `invoke()`.

Observability: every invocation writes a structured JSON line to a
CloudWatch log group (default ``/gagent/invocations``) under the
operator's credentials. AgentCore Observability surfaces the log group
in the CloudWatch console alongside the Bedrock Agent's auto-emitted
X-Ray traces. recent_traces and audit_trace query the log group via
Logs Insights.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config

from .identity import Persona
from .trace import (
    TraceSummary,
    emit_invocation_log,
    resolve_log_group,
    summarize_trace,
)

logger = logging.getLogger("gagent_client.invoke")

DEFAULT_DURATION_SECONDS = 900
DEFAULT_READ_TIMEOUT = 300
DEFAULT_CONNECT_TIMEOUT = 10


@dataclass
class InvocationResponse:
    """Normalized response from one InvokeAgent turn."""

    text: str
    trace_summary: TraceSummary
    trace_events: list[dict[str, Any]]
    duration_seconds: float
    session_id: str
    role_session_name: str
    log_stream: str | None = None


def assume_persona(
    persona: Persona,
    *,
    duration_seconds: int = DEFAULT_DURATION_SECONDS,
    session_name: str | None = None,
) -> dict[str, str]:
    """STS AssumeRole with the persona's session tags.

    Tags are transitive so they propagate through the Lambda re-assumption
    chain (ADR-003). technician_lead carries an additional service_region
    tag whose presence is enforced by the role's trust policy.

    Caller can pass ``session_name`` to control the RoleSessionName — the
    same name lands in CloudWatch Logs so audit_trace can correlate the
    invocation back to CloudTrail events for the assumed role.
    """
    sts = boto3.client("sts")
    tags: list[dict[str, str]] = [{"Key": "role", "Value": persona.role}]
    if persona.service_region:
        tags.append({"Key": "service_region", "Value": persona.service_region})

    role_session_name = session_name or f"gagent-{persona.role}-{uuid.uuid4().hex[:6]}"
    response = sts.assume_role(
        RoleArn=persona.role_arn,
        RoleSessionName=role_session_name,
        Tags=tags,
        TransitiveTagKeys=[t["Key"] for t in tags],
        DurationSeconds=duration_seconds,
    )
    return response["Credentials"]


def invoke(
    question: str,
    persona: Persona,
    *,
    agent_id: str,
    agent_alias_id: str,
    region: str,
    session_id: str | None = None,
    enable_trace: bool = True,
    surface: str = "lib",
    trace_name: str = "gagent-invoke",
    trace_metadata: dict[str, Any] | None = None,
    log_group: str | None = None,
    emit_log: bool = True,
) -> InvocationResponse:
    """Run one InvokeAgent turn under the persona's credentials.

    Pipeline:
      1. AssumeRole with session tags (transitive).
      2. InvokeAgent with enableTrace.
      3. Stream chunks + summarize trace events into a TraceSummary.
      4. Best-effort: emit a structured invocation log line to CloudWatch.

    Returns a normalized InvocationResponse. AWS errors on the agent
    path propagate; CloudWatch Logs failures are swallowed (observability
    is never a hard dependency).

    The ``surface`` parameter identifies the calling surface in the log
    line — typical values: "mcp", "cli", "eval", "notebook", "lib".
    """
    role_session_name = f"gagent-{persona.role}-{uuid.uuid4().hex[:6]}"
    creds = assume_persona(persona, session_name=role_session_name)
    runtime = boto3.client(
        "bedrock-agent-runtime",
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        config=Config(
            read_timeout=DEFAULT_READ_TIMEOUT,
            connect_timeout=DEFAULT_CONNECT_TIMEOUT,
            retries={"max_attempts": 1},
        ),
    )

    session_id = session_id or role_session_name
    session_attrs: dict[str, str] = {"role": persona.role}
    if persona.service_region:
        session_attrs["service_region"] = persona.service_region

    started = time.time()
    response = runtime.invoke_agent(
        agentId=agent_id,
        agentAliasId=agent_alias_id,
        sessionId=session_id,
        inputText=question,
        enableTrace=enable_trace,
        sessionState={"sessionAttributes": session_attrs},
    )

    text_parts: list[str] = []
    trace_events: list[dict[str, Any]] = []
    summary = TraceSummary()
    for event in response["completion"]:
        if "chunk" in event:
            text_parts.append(event["chunk"]["bytes"].decode("utf-8"))
        elif "trace" in event and enable_trace:
            trace_events.append(event["trace"])
            summarize_trace(event["trace"], summary)

    duration = time.time() - started
    text = "".join(text_parts)

    log_stream: str | None = None
    if emit_log:
        log_stream = emit_invocation_log(
            session_id=session_id,
            persona=persona.role,
            role_arn=persona.role_arn,
            role_session_name=role_session_name,
            surface=surface,
            trace_name=trace_name,
            input_text=question,
            output_text=text,
            summary=summary,
            duration_seconds=duration,
            started_at=started,
            metadata={
                **(trace_metadata or {}),
                "service_region": persona.service_region,
            },
            log_group=log_group or resolve_log_group(),
            region=region,
        )

    return InvocationResponse(
        text=text,
        trace_summary=summary,
        trace_events=trace_events,
        duration_seconds=duration,
        session_id=session_id,
        role_session_name=role_session_name,
        log_stream=log_stream,
    )

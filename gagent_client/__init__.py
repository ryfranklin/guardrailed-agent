"""Shared client library for the guardrailed-agent (ADR-006, ADR-009).

The persona -> STS -> InvokeAgent -> CloudWatch Logs pipeline used by
the eval harness, MCP server (ADR-009), gra CLI, and SMUS notebook.
One library; many surfaces. Trace emission is AgentCore-Observability
native: each invocation writes a structured JSON line to a CloudWatch
log group (default /gagent/invocations) under the operator's credentials.

Public API:
  invoke(question, persona, ...)             one-call agent turn
  assume_persona(persona)                    STS AssumeRole only
  emit_invocation_log(...)                   structured trace emission
  Persona, InvocationResponse, TraceSummary  result types
  FlagPersonaResolver, SsoPersonaResolver    Shape A / Shape B resolvers
  CognitoPersonaResolver                     Cognito-authenticated callers (ADR-007)
"""

from .identity import (
    VALID_COGNITO_MODES,
    VALID_ROLES,
    CognitoPersonaResolver,
    FlagPersonaResolver,
    Persona,
    PersonaResolver,
    SsoPersonaResolver,
)
from .invoke import (
    DEFAULT_DURATION_SECONDS,
    InvocationResponse,
    assume_persona,
    invoke,
)
from .trace import (
    DEFAULT_LOG_GROUP,
    TraceSummary,
    emit_invocation_log,
    resolve_log_group,
    summarize_trace,
    trace_summary_to_dict,
)

__all__ = [
    "DEFAULT_DURATION_SECONDS",
    "DEFAULT_LOG_GROUP",
    "CognitoPersonaResolver",
    "FlagPersonaResolver",
    "InvocationResponse",
    "Persona",
    "PersonaResolver",
    "SsoPersonaResolver",
    "TraceSummary",
    "VALID_COGNITO_MODES",
    "VALID_ROLES",
    "assume_persona",
    "emit_invocation_log",
    "invoke",
    "resolve_log_group",
    "summarize_trace",
    "trace_summary_to_dict",
]

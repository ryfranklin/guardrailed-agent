"""MCP server for the guardrailed agent (ADR-009 Phase 2.a).

Stdio transport. Three tools: ask_agent, describe_schema, list_tools.
Wraps gagent_client; defers data-access enforcement to Lake Formation.

Trust gate: requires GAGENT_TRUSTED_OPERATOR=1 in env (Shape A only).

Usage:
  GAGENT_TRUSTED_OPERATOR=1 AWS_PROFILE=ms3dm-admin python -m mcp_server
"""

from .governance_tools import (
    GOVERNANCE_TOOL_DEFINITIONS,
    audit_trace_impl,
    eval_query_impl,
    explain_governance_impl,
)
from .operability_tools import (
    PHASE_2C_TOOL_DEFINITIONS,
    health_impl,
    health_impl_async,
    propose_query_impl,
    recent_traces_impl,
)
from .state import (
    SHAPE_A,
    SHAPE_B,
    ServerConfig,
    ServerStartupError,
    ServerState,
    TokenCounter,
    TrustGateError,
    determine_shape,
    enforce_trust_gate,
    load_config,
)
from .tools import (
    TOOL_DEFINITIONS,
    ask_agent_impl,
    describe_schema_impl,
    list_tools_impl,
)

__all__ = [
    "GOVERNANCE_TOOL_DEFINITIONS",
    "PHASE_2C_TOOL_DEFINITIONS",
    "SHAPE_A",
    "SHAPE_B",
    "ServerConfig",
    "ServerStartupError",
    "ServerState",
    "TOOL_DEFINITIONS",
    "TokenCounter",
    "TrustGateError",
    "ask_agent_impl",
    "audit_trace_impl",
    "describe_schema_impl",
    "determine_shape",
    "enforce_trust_gate",
    "eval_query_impl",
    "explain_governance_impl",
    "health_impl",
    "health_impl_async",
    "list_tools_impl",
    "load_config",
    "propose_query_impl",
    "recent_traces_impl",
]

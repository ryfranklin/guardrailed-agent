"""gra — the guardrailed-agent CLI (ADR-006 Phase 2).

A thin shim over `gagent_client` + `mcp_server.operability_tools.recent_traces_impl`.
Three subcommands: `ask`, `personas`, `traces`. All support `--json` for
pipeable JSON output.

Trust model: Shape A only — requires GAGENT_TRUSTED_OPERATOR=1 in env per
ADR-006 §Persona handling.
"""

from .main import main

__all__ = ["main"]

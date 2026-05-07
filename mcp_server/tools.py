"""Tool implementations for the MCP server (ADR-009 Phase 2.a).

Three tools, one transport (stdio):
  ask_agent          wraps gagent_client.invoke under the persona's STS creds.
  describe_schema    Glue GetTables / GetTable under the persona's creds —
                     Lake Formation filters columns at the catalog level so
                     the persona only sees what it's permitted to see.
  list_tools         self-describing tool inventory + active persona context.

Each impl returns a JSON-serializable dict. The MCP wiring in server.py
serializes that dict into a TextContent payload. Errors are returned as
structured {"error": "..."} dicts rather than raised, so MCP clients
get a consistent shape.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import boto3
from botocore.exceptions import ClientError

from gagent_client import Persona, assume_persona, invoke as agent_invoke

from .state import ServerState

logger = logging.getLogger("mcp_server.tools")


TOOL_DEFINITIONS = [
    {
        "name": "ask_agent",
        "description": (
            "Ask the guardrailed Bedrock Agent a natural-language question. "
            "Routes through the operator's persona role; Lake Formation "
            "enforces row + column visibility on every tool the agent calls. "
            "Returns the model's response plus a trace summary."
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
                        "Persona to assume for this call. Defaults to the "
                        "server's GAGENT_DEFAULT_PERSONA setting."
                    ),
                },
                "service_region": {
                    "type": "string",
                    "description": (
                        "Service region session tag. Required when persona="
                        "technician_lead. Ignored for other personas."
                    ),
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "describe_schema",
        "description": (
            "Describe the catalog schema visible to the active persona. "
            "Without arguments, returns the table list. With table=<name>, "
            "returns the column list. Lake Formation filters the result to "
            "what the persona is permitted to see — making the governance "
            "boundary tangible without exposing the policy itself."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": (
                        "Optional table name. Omit to list all visible tables."
                    ),
                },
                "persona": {
                    "type": "string",
                    "enum": ["dispatcher", "technician_lead", "owner"],
                    "description": "Persona override; defaults to server default.",
                },
                "service_region": {
                    "type": "string",
                    "description": "Required when persona=technician_lead.",
                },
            },
        },
    },
    {
        "name": "list_tools",
        "description": (
            "Return the registered tool list and the persona currently in "
            "effect. Self-describing: useful for IDE-side capability "
            "discovery and for debugging."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

VALID_PERSONAS = ("dispatcher", "technician_lead", "owner")


def ask_agent_impl(state: ServerState, args: dict[str, Any]) -> dict[str, Any]:
    question = args.get("question")
    if not question or not isinstance(question, str):
        return {"error": "question is required and must be a string"}

    resolved = _resolve_call_persona(state, args)
    if isinstance(resolved, dict):
        return resolved
    persona = resolved

    cfg = state.config
    if not cfg.agent_id or not cfg.agent_alias_id:
        return {
            "error": (
                "agent_id / agent_alias_id not configured; set "
                "GAGENT_AGENT_ID and GAGENT_AGENT_ALIAS_ID or run "
                "terraform apply in terraform/envs/demo"
            ),
        }

    try:
        response = agent_invoke(
            question,
            persona,
            agent_id=cfg.agent_id,
            agent_alias_id=cfg.agent_alias_id,
            region=cfg.region,
            session_id=f"mcp-{persona.role}-{uuid.uuid4().hex[:6]}",
            enable_trace=True,
            surface="mcp",
            trace_name=f"mcp-ask-{persona.role}",
            trace_metadata={"persona": persona.role},
            log_group=cfg.log_group,
        )
    except ClientError as exc:
        logger.exception("ask_agent: ClientError")
        return {
            "error": f"InvokeAgent error: {exc.response.get('Error', {}).get('Code', 'Unknown')}",
            "message": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("ask_agent: unhandled error")
        return {"error": f"{type(exc).__name__}: {exc}"}

    state.tokens.add(input_text=question, output_text=response.text)

    return {
        "text": response.text,
        "persona": persona.role,
        "service_region": persona.service_region,
        "tools_called": list(response.trace_summary.tools_called),
        "guardrail_blocks": response.trace_summary.guardrail_blocks,
        "duration_seconds": round(response.duration_seconds, 3),
        "session_tokens_estimate": state.tokens.used,
    }


def describe_schema_impl(state: ServerState, args: dict[str, Any]) -> dict[str, Any]:
    cfg = state.config
    if not cfg.glue_database:
        return {
            "error": (
                "glue_database not configured; set GAGENT_GLUE_DATABASE or "
                "ensure terraform output exposes glue_database_name"
            ),
        }

    resolved = _resolve_call_persona(state, args)
    if isinstance(resolved, dict):
        return resolved
    persona = resolved

    try:
        creds = assume_persona(persona)
    except ClientError as exc:
        logger.exception("describe_schema: AssumeRole failed")
        return {
            "error": f"AssumeRole error: {exc.response.get('Error', {}).get('Code', 'Unknown')}",
            "message": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"AssumeRole error: {type(exc).__name__}: {exc}"}

    glue = boto3.client(
        "glue",
        region_name=cfg.region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )

    table = args.get("table")
    try:
        if not table:
            return _list_visible_tables(glue, cfg.glue_database, persona)
        return _describe_one_table(glue, cfg.glue_database, table, persona)
    except ClientError as exc:
        logger.exception("describe_schema: Glue error")
        return {
            "error": f"Glue error: {exc.response.get('Error', {}).get('Code', 'Unknown')}",
            "message": str(exc),
            "persona": persona.role,
        }


def _list_visible_tables(
    glue: Any, database: str, persona: Persona,
) -> dict[str, Any]:
    tables: list[str] = []
    paginator = glue.get_paginator("get_tables")
    for page in paginator.paginate(DatabaseName=database):
        for t in page.get("TableList", []) or []:
            tables.append(t["Name"])
    return {
        "database": database,
        "persona": persona.role,
        "service_region": persona.service_region,
        "tables": sorted(tables),
        "table_count": len(tables),
    }


def _describe_one_table(
    glue: Any, database: str, table: str, persona: Persona,
) -> dict[str, Any]:
    response = glue.get_table(DatabaseName=database, Name=table)
    table_info = response["Table"]
    sd = table_info.get("StorageDescriptor", {}) or {}
    columns = [
        {"name": c["Name"], "type": c["Type"]}
        for c in (sd.get("Columns") or [])
    ]
    partition_keys = [
        {"name": c["Name"], "type": c["Type"]}
        for c in (table_info.get("PartitionKeys") or [])
    ]
    return {
        "database": database,
        "table": table,
        "persona": persona.role,
        "service_region": persona.service_region,
        "columns": columns,
        "column_count": len(columns),
        "partition_keys": partition_keys,
    }


def list_tools_impl(state: ServerState, args: dict[str, Any]) -> dict[str, Any]:
    cfg = state.config
    return {
        "tools": [
            {"name": t["name"], "description": t["description"]}
            for t in TOOL_DEFINITIONS
        ],
        "default_persona": cfg.default_persona,
        "default_service_region": cfg.default_service_region,
        "available_personas": (
            cfg.resolver.known_roles() if cfg.resolver else []
        ),
        "agent_configured": bool(cfg.agent_id and cfg.agent_alias_id),
        "glue_database": cfg.glue_database,
        "log_group": cfg.log_group,
        "session_tokens_estimate": state.tokens.used,
        "token_budget": state.tokens.budget,
    }


def _resolve_call_persona(
    state: ServerState, args: dict[str, Any],
) -> Persona | dict[str, Any]:
    """Pick (and validate) the persona for one tool call.

    Returns a resolved Persona on success, or an error dict on failure.
    Callers branch on isinstance(result, dict).
    """
    cfg = state.config
    if cfg.resolver is None:
        return {
            "error": (
                "no persona role ARNs configured; set "
                "GAGENT_{DISPATCHER,TECHNICIAN_LEAD,OWNER}_ROLE_ARN or run "
                "terraform apply in terraform/envs/demo"
            ),
        }

    requested_role = (args.get("persona") or cfg.default_persona).strip().lower()
    if requested_role not in VALID_PERSONAS:
        return {
            "error": (
                f"persona must be one of {list(VALID_PERSONAS)}; "
                f"got {requested_role!r}"
            ),
        }

    service_region = args.get("service_region")
    if requested_role == "technician_lead" and not service_region:
        service_region = cfg.default_service_region

    try:
        return cfg.resolver.resolve(
            requested_role,
            service_region=service_region if requested_role == "technician_lead" else None,
        )
    except (KeyError, ValueError) as exc:
        return {"error": f"persona resolution failed: {exc}"}

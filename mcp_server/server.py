"""MCP server scaffold using the official Anthropic mcp Python SDK.

Stdio transport per ADR-009 Shape A. Three tools registered. The server
refuses to start without GAGENT_TRUSTED_OPERATOR=1 in the environment.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .governance_tools import (
    GOVERNANCE_TOOL_DEFINITIONS,
    audit_trace_impl,
    eval_query_impl,
    explain_governance_impl,
)
from .operability_tools import (
    PHASE_2C_TOOL_DEFINITIONS,
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
    load_config,
)
from .tools import (
    TOOL_DEFINITIONS,
    ask_agent_impl,
    describe_schema_impl,
    list_tools_impl,
)

ALL_TOOL_DEFINITIONS = (
    TOOL_DEFINITIONS + GOVERNANCE_TOOL_DEFINITIONS + PHASE_2C_TOOL_DEFINITIONS
)

SERVER_NAME = "gagent-mcp"
SERVER_VERSION = "0.1.0"

logger = logging.getLogger("mcp_server.server")


def build_server(state: ServerState) -> Server:
    """Construct the MCP Server with the three Phase 2.a tools registered."""
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name=spec["name"],
                description=spec["description"],
                inputSchema=spec["inputSchema"],
            )
            for spec in ALL_TOOL_DEFINITIONS
        ]

    @server.call_tool()
    async def _call_tool(
        name: str, arguments: dict[str, Any] | None,
    ) -> list[TextContent]:
        args = arguments or {}
        if name == "ask_agent":
            result = ask_agent_impl(state, args)
        elif name == "describe_schema":
            result = describe_schema_impl(state, args)
        elif name == "list_tools":
            result = list_tools_impl(state, args)
        elif name == "explain_governance":
            result = explain_governance_impl(state, args)
        elif name == "eval_query":
            result = eval_query_impl(state, args)
        elif name == "audit_trace":
            result = audit_trace_impl(state, args)
        elif name == "propose_query":
            result = propose_query_impl(state, args)
        elif name == "recent_traces":
            result = recent_traces_impl(state, args)
        elif name == "health":
            # health does concurrent AWS reachability probes via asyncio.gather;
            # we're already inside an event loop here, so await directly.
            result = await health_impl_async(state, args)
        else:
            result = {"error": f"unknown tool {name!r}"}
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    return server


async def run_async(state: ServerState) -> int:
    server = build_server(state)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_NAME,
                server_version=SERVER_VERSION,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("GAGENT_LOG_LEVEL", "INFO"),
        # MCP uses stdout for JSON-RPC; logs MUST go to stderr.
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        config: ServerConfig = load_config(os.environ)
    except ServerStartupError as exc:
        logger.error("%s", exc)
        return 1

    state = ServerState(config=config)

    logger.info(
        "starting %s v%s shape=%s region=%s default_persona=%s personas=%s "
        "agent_configured=%s glue_database=%s log_group=%s token_budget=%d",
        SERVER_NAME, SERVER_VERSION, config.shape,
        config.region, config.default_persona,
        config.resolver.known_roles() if config.resolver else [],
        bool(config.agent_id and config.agent_alias_id),
        config.glue_database,
        config.log_group,
        config.token_budget,
    )
    if config.shape == SHAPE_B:
        logger.info(
            "Shape B: persona is bound to SSO identity; per-call "
            "--persona arguments will be ignored with a WARN.",
        )

    try:
        return asyncio.run(run_async(state))
    except KeyboardInterrupt:
        logger.info("interrupted; shutting down")
        return 0


if __name__ == "__main__":
    sys.exit(main())

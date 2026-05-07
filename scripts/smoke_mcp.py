#!/usr/bin/env python3
"""Smoke test: spawn the MCP server, call each of the three tools, print results.

Per ADR-009 Phase 2.a acceptance criterion. Run from the repo root with:

    GAGENT_TRUSTED_OPERATOR=1 AWS_PROFILE=ms3dm-admin \\
        python scripts/smoke_mcp.py

Spawns `python -m mcp_server` as a subprocess over stdio (same transport
Claude Code / Claude Desktop use), opens an MCP session, calls each tool,
and pretty-prints the result. Tools that need AWS (ask_agent,
describe_schema) will return structured error responses if AWS isn't
reachable — the smoke still prints them.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _print_section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _format_tool_result(result: object) -> str:
    """Extract the JSON payload from the MCP TextContent list."""
    content = getattr(result, "content", None) or []
    if not content:
        return json.dumps({"empty_result": True}, indent=2)
    blocks: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None:
            blocks.append(repr(block))
            continue
        try:
            blocks.append(json.dumps(json.loads(text), indent=2, default=str))
        except json.JSONDecodeError:
            blocks.append(text)
    return "\n".join(blocks)


async def main() -> int:
    if os.environ.get("GAGENT_TRUSTED_OPERATOR") != "1":
        print(
            "error: GAGENT_TRUSTED_OPERATOR=1 required (Shape A trust gate; ADR-006).",
            file=sys.stderr,
        )
        return 1

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server"],
        # Inherit AWS_PROFILE etc. so the subprocess sees the same creds.
        env={**os.environ},
        cwd=str(REPO_ROOT),
    )

    print(f"spawning: {server_params.command} {' '.join(server_params.args)}")
    print(f"cwd: {server_params.cwd}")

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init_result = await session.initialize()
            _print_section("initialize")
            print(f"server: {init_result.serverInfo.name} v{init_result.serverInfo.version}")
            print(f"protocol: {init_result.protocolVersion}")

            _print_section("protocol-level tools/list")
            listed = await session.list_tools()
            for t in listed.tools:
                print(f"  - {t.name}")

            _print_section("call: list_tools")
            result = await session.call_tool("list_tools", {})
            print(_format_tool_result(result))

            _print_section("call: describe_schema (no args)")
            result = await session.call_tool("describe_schema", {})
            print(_format_tool_result(result))

            _print_section("call: describe_schema(table='customer')")
            result = await session.call_tool(
                "describe_schema", {"table": "customer"},
            )
            print(_format_tool_result(result))

            _print_section("call: ask_agent")
            result = await session.call_tool(
                "ask_agent",
                {
                    "question": "What tables are available in the governed dataset?",
                },
            )
            print(_format_tool_result(result))

            # ADR-009 Phase 2.b governance tools below.

            _print_section("call: explain_governance(dispatcher, customer)")
            result = await session.call_tool(
                "explain_governance",
                {
                    "query": "SELECT * FROM customer LIMIT 5",
                    "persona": "dispatcher",
                },
            )
            print(_format_tool_result(result))

            _print_section("call: eval_query(owner, customer)")
            result = await session.call_tool(
                "eval_query",
                {
                    "query": "SELECT * FROM customer LIMIT 5",
                    "persona": "owner",
                },
            )
            print(_format_tool_result(result))

            # ADR-009 Phase 2.c operability tools below.

            _print_section("call: propose_query(dispatcher)")
            result = await session.call_tool(
                "propose_query",
                {
                    "question": "How many customers in the tempe-mesa region?",
                    "persona": "dispatcher",
                },
            )
            print(_format_tool_result(result))

            _print_section("call: recent_traces(limit=3)")
            result = await session.call_tool(
                "recent_traces", {"limit": 3},
            )
            print(_format_tool_result(result))

            _print_section("call: health()")
            result = await session.call_tool("health", {})
            print(_format_tool_result(result))

    print()
    print("smoke test complete.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

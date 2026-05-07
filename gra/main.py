"""gra CLI — ask / personas / traces subcommands (ADR-006).

Surface choice (per ADR-006): the CLI is one of three Phase 2 personal
interaction surfaces (CLI + MCP + SMUS notebook), all sitting on top of
the shared ``gagent_client`` library. The CLI is the pipeable / scriptable
surface — every subcommand has a ``--json`` flag for downstream tooling.

Trust model: Shape A only (GAGENT_TRUSTED_OPERATOR=1). The ``--persona``
flag trusts the caller and is unsafe outside solo use; the trust gate
makes that explicit at startup.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

from gagent_client import (
    InvocationResponse,
    Persona,
    invoke as agent_invoke,
)
from mcp_server.operability_tools import recent_traces_impl
from mcp_server.state import (
    ServerState,
    enforce_trust_gate,
    load_config,
)

logger = logging.getLogger("gra")

EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_AWS_ERROR = 2
EXIT_NOT_CONFIGURED = 3


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("GAGENT_LOG_LEVEL", "WARNING"),
        # JSON is the consumable output on stdout; logs go to stderr so
        # `gra ask --json | jq` keeps working.
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not args.command:
        parser.print_help()
        return EXIT_USER_ERROR

    try:
        enforce_trust_gate(os.environ)
    except SystemExit as exc:
        # Make sure the user sees the gate failure on stderr regardless of
        # how the root logger is configured by the caller (e.g., pytest).
        sys.stderr.write(
            "error: GAGENT_TRUSTED_OPERATOR=1 required (Shape A trust "
            "gate; ADR-006). Unset until you have a multi-user identity "
            "story (Shape B / Phase 2.d).\n",
        )
        return int(exc.code or 1)

    cfg = load_config(os.environ)

    if args.command == "ask":
        return _cmd_ask(args, cfg)
    if args.command == "personas":
        return _cmd_personas(args, cfg)
    if args.command == "traces":
        return _cmd_traces(args, cfg)
    parser.print_help()
    return EXIT_USER_ERROR


# ---- argparse ----

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gra",
        description=(
            "Guardrailed-agent CLI. Three subcommands wrap gagent_client. "
            "Shape A only — requires GAGENT_TRUSTED_OPERATOR=1 in env."
        ),
    )
    sub = p.add_subparsers(dest="command")

    ask = sub.add_parser(
        "ask",
        help="Ask the Bedrock agent a question under a persona.",
    )
    ask.add_argument("prompt", help="Natural-language question for the agent.")
    ask.add_argument("--persona", choices=["dispatcher", "technician_lead", "owner"],
                     help="Persona to assume. Defaults to GAGENT_DEFAULT_PERSONA.")
    ask.add_argument("--service-region", dest="service_region",
                     help="Required when persona=technician_lead.")
    ask.add_argument("--json", action="store_true",
                     help="Emit JSON to stdout instead of text + summary.")

    personas = sub.add_parser(
        "personas",
        help="List configured personas + the active default.",
    )
    personas.add_argument("--json", action="store_true",
                          help="Emit JSON to stdout.")

    traces = sub.add_parser(
        "traces",
        help="Show the N most recent invocation traces from the gagent CloudWatch log group.",
    )
    traces.add_argument("--persona", choices=["dispatcher", "technician_lead", "owner"],
                        help="Filter by persona (matches legacy + canonical names).")
    traces.add_argument("--limit", type=int, default=10,
                        help="Number of traces to show. Default 10.")
    traces.add_argument("--hours", type=int, default=168,
                        help="Lookback window in hours. Default 168 (7 days).")
    traces.add_argument("--json", action="store_true",
                        help="Emit JSON to stdout.")

    return p


# ---- ask ----

def _cmd_ask(args: argparse.Namespace, cfg: Any) -> int:
    if cfg.resolver is None:
        return _emit_error(
            args, EXIT_NOT_CONFIGURED,
            "no persona role ARNs configured; set GAGENT_*_ROLE_ARN env "
            "vars or run terraform apply in terraform/envs/demo",
        )
    if not (cfg.agent_id and cfg.agent_alias_id):
        return _emit_error(
            args, EXIT_NOT_CONFIGURED,
            "agent_id / agent_alias_id not configured; set GAGENT_AGENT_ID "
            "and GAGENT_AGENT_ALIAS_ID or run terraform apply",
        )

    role = args.persona or cfg.default_persona
    service_region: str | None = None
    if role == "technician_lead":
        service_region = args.service_region or cfg.default_service_region
        if not service_region:
            return _emit_error(
                args, EXIT_USER_ERROR,
                "technician_lead persona requires --service-region "
                "(or GAGENT_DEFAULT_SERVICE_REGION in env)",
            )

    try:
        persona: Persona = cfg.resolver.resolve(
            role, service_region=service_region,
        )
    except (KeyError, ValueError) as exc:
        return _emit_error(args, EXIT_USER_ERROR, f"persona resolve failed: {exc}")

    try:
        response: InvocationResponse = agent_invoke(
            args.prompt,
            persona,
            agent_id=cfg.agent_id,
            agent_alias_id=cfg.agent_alias_id,
            region=cfg.region,
            enable_trace=True,
            surface="cli",
            trace_name=f"gra-ask-{persona.role}",
            trace_metadata={"persona": persona.role},
            log_group=cfg.log_group,
        )
    except Exception as exc:  # noqa: BLE001
        return _emit_error(
            args, EXIT_AWS_ERROR,
            f"InvokeAgent error: {type(exc).__name__}: {exc}",
        )

    payload = {
        "persona": persona.role,
        "service_region": persona.service_region,
        "text": response.text,
        "tools_called": list(response.trace_summary.tools_called),
        "guardrail_blocks": response.trace_summary.guardrail_blocks,
        "duration_seconds": round(response.duration_seconds, 3),
        "session_id": response.session_id,
    }

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(response.text)
        sys.stderr.write(
            f"\n[persona={persona.role}"
            f" duration={response.duration_seconds:.2f}s"
            f" tools={response.trace_summary.tools_called}"
            f" guardrail_blocks={response.trace_summary.guardrail_blocks}]\n",
        )

    return EXIT_OK


# ---- personas ----

def _cmd_personas(args: argparse.Namespace, cfg: Any) -> int:
    available = cfg.resolver.known_roles() if cfg.resolver else []
    info: dict[str, Any] = {
        "active": cfg.default_persona,
        "default_service_region": cfg.default_service_region,
        "available": sorted(available),
        "role_arns": (
            dict(cfg.resolver._role_arns) if cfg.resolver else {}  # type: ignore[attr-defined]
        ),
    }

    if args.json:
        print(json.dumps(info, indent=2, default=str))
        return EXIT_OK

    if not available:
        print("(no personas configured)")
        print(
            "Set GAGENT_{DISPATCHER,TECHNICIAN_LEAD,OWNER}_ROLE_ARN or run "
            "terraform apply.",
            file=sys.stderr,
        )
        return EXIT_NOT_CONFIGURED

    print(f"Active default: {info['active']}")
    if info["default_service_region"]:
        print(f"Default service_region: {info['default_service_region']}")
    print()
    print("Available personas:")
    for role in info["available"]:
        marker = "* " if role == info["active"] else "  "
        arn = info["role_arns"].get(role, "(no ARN)")
        print(f"{marker}{role:<18}  {arn}")
    return EXIT_OK


# ---- traces ----

def _cmd_traces(args: argparse.Namespace, cfg: Any) -> int:
    if not cfg.log_group:
        return _emit_error(
            args, EXIT_NOT_CONFIGURED,
            "log_group not configured; set GAGENT_LOG_GROUP or rely on "
            "the default /gagent/invocations log group",
        )

    state = ServerState(config=cfg)
    result = recent_traces_impl(state, {
        "persona": args.persona,
        "limit": args.limit,
        "hours": args.hours,
    })

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return EXIT_OK

    if "error" in result:
        sys.stderr.write(f"error: {result['error']}\n")
        return EXIT_AWS_ERROR

    traces = result.get("traces", []) or []
    if not traces:
        print("(no traces)")
        return EXIT_OK

    print(
        f"Showing {result.get('trace_count', 0)} trace(s)"
        + (f" for persona={args.persona}" if args.persona else "")
        + (
            f" — {result.get('legacy_persona_count', 0)} use legacy persona names"
            if result.get("legacy_persona_count")
            else ""
        ),
    )
    print()
    for t in traces:
        ts = t.get("timestamp") or "?"
        persona = t.get("normalized_persona") or t.get("persona") or "?"
        legacy_marker = " (legacy)" if t.get("persona_is_legacy") else ""
        tools = ", ".join(t.get("tools_called") or []) or "-"
        guardrail = t.get("guardrail_blocks") or 0
        duration = t.get("duration_seconds")
        duration_str = f"{duration:.2f}s" if isinstance(duration, (int, float)) else "?"
        preview = (t.get("input_preview") or "").replace("\n", " ")
        print(
            f"  {ts}  {persona:<16}{legacy_marker:<10}  "
            f"{duration_str:>7}  tools=[{tools}]  guardrail={guardrail}",
        )
        if preview:
            print(f"                                                          {preview}")
    return EXIT_OK


# ---- helpers ----

def _emit_error(
    args: argparse.Namespace, code: int, message: str,
) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"error": message}))
    else:
        sys.stderr.write(f"error: {message}\n")
    return code


if __name__ == "__main__":
    sys.exit(main())

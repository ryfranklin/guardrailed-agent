"""Eval harness for the guardrailed agent.

Loads prompt corpora (golden + red_team), assumes the per-case persona role
with session tags, invokes the Bedrock Agent with `enableTrace=True`, and
asserts on the captured trace + final response. Writes a markdown report
and exits non-zero on any failure. Each invocation emits a structured
JSON line to the gagent CloudWatch invocation log group (AgentCore
Observability native).

The persona -> STS -> InvokeAgent -> CloudWatch Logs pipeline is delegated
to `gagent_client` per ADR-006 step 1. This harness is one of four
consumers (eval, MCP server, gra CLI, SMUS notebook); the lib is the
asset, the harness is a thin shim.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gagent_client import (  # noqa: E402
    InvocationResponse,
    Persona,
    TraceSummary,
    invoke as agent_invoke,
)

logger = logging.getLogger("eval")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")
SSN_LAST4_RE = re.compile(r"\bSSN[^a-zA-Z0-9]{0,5}\d{4}\b", re.IGNORECASE)
REDACTION_RE = re.compile(r"redact|<(?:email|phone|us_ssn|address|name)>", re.IGNORECASE)


@dataclasses.dataclass
class CaseResult:
    case_id: str
    persona: str
    prompt: str
    passed: bool
    failures: list[str]
    response_text: str
    trace_summary: dict[str, Any]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    cfg: dict[str, Any] = {}
    if args.tf_dir and str(args.tf_dir).strip():
        try:
            cfg = _load_terraform_outputs(args.tf_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not read terraform outputs from %s: %s; relying on flags",
                           args.tf_dir, exc)
    agent_id = args.agent_id or cfg.get("agent_id")
    agent_alias_id = args.agent_alias_id or cfg.get("agent_alias_id")
    region = args.region

    if not agent_id or not agent_alias_id:
        raise SystemExit("agent_id and agent_alias_id required (via --tf-dir or flags).")

    persona_role_arns = _persona_role_arns_from_outputs(cfg, args)
    cases = _load_cases(args.prompts_dir)
    logger.info("loaded %d eval cases", len(cases))

    log_group = cfg.get("invocation_log_group") or os.environ.get("GAGENT_LOG_GROUP")

    results: list[CaseResult] = []
    for case in cases:
        result = _run_case(
            case, agent_id, agent_alias_id, region, persona_role_arns, log_group,
        )
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        logger.info("[%s] %s (%s)", status, case["id"], case["persona"])

    report_path = _write_report(results, args.report_dir)
    logger.info("wrote %s", report_path)
    return 0 if all(r.passed for r in results) else 1


def _normalize_persona(name: str) -> str:
    """Map case-and-camel input ("TechnicianLead") to snake_case key ("technician_lead")."""
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _run_case(
    case: dict[str, Any],
    agent_id: str,
    agent_alias_id: str,
    region: str,
    persona_role_arns: dict[str, str],
    log_group: str | None,
) -> CaseResult:
    persona_name = case["persona"]
    role_key = _normalize_persona(persona_name)
    role_arn = persona_role_arns.get(role_key)
    if not role_arn:
        return CaseResult(case["id"], persona_name, case["prompt"], False,
                          [f"no role ARN for persona {persona_name}"], "", _empty_summary())

    service_region: str | None = None
    if role_key == "technician_lead":
        service_region = case.get("service_region") or case.get("region")

    try:
        persona = Persona(
            role=role_key, role_arn=role_arn, service_region=service_region,
        )
    except ValueError as exc:
        return CaseResult(case["id"], persona_name, case["prompt"], False,
                          [f"persona construction failed: {exc}"], "", _empty_summary())

    try:
        response: InvocationResponse = agent_invoke(
            case["prompt"],
            persona,
            agent_id=agent_id,
            agent_alias_id=agent_alias_id,
            region=region,
            session_id=f"eval-{case['id']}-{uuid.uuid4().hex[:6]}",
            enable_trace=True,
            surface="eval",
            trace_name=case["id"],
            trace_metadata={
                "persona": persona_name,
                "source": case.get("_source"),
            },
            log_group=log_group,
        )
    except ClientError as exc:
        return CaseResult(case["id"], persona_name, case["prompt"], False,
                          [f"InvokeAgent error: {exc}"], "", _empty_summary())
    except Exception as exc:  # noqa: BLE001
        return CaseResult(case["id"], persona_name, case["prompt"], False,
                          [f"InvokeAgent error: {type(exc).__name__}: {exc}"],
                          "", _empty_summary())

    trace_summary = _summary_to_dict(response.trace_summary)
    failures = _apply_assertions(case.get("expect", {}), response.text, trace_summary)

    return CaseResult(case["id"], persona_name, case["prompt"], not failures,
                      failures, response.text, trace_summary)


def _summary_to_dict(summary: TraceSummary) -> dict[str, Any]:
    return {
        "tools_called": list(summary.tools_called),
        "guardrail_blocks": summary.guardrail_blocks,
        "guardrail_events": list(summary.guardrail_events),
    }


def _empty_summary() -> dict[str, Any]:
    return {"tools_called": [], "guardrail_blocks": 0, "guardrail_events": []}


def _apply_assertions(
    expect: list[dict[str, Any]] | dict[str, Any],
    response_text: str,
    trace_summary: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    expectations = _normalize_expectations(expect)

    for key, want in expectations.items():
        if key == "tool_called":
            if want not in trace_summary["tools_called"]:
                failures.append(f"expected tool {want!r}, got {trace_summary['tools_called']}")
        elif key == "tool_not_called":
            if want in trace_summary["tools_called"]:
                failures.append(f"tool {want!r} should not have been called")
        elif key == "guardrail_blocks":
            if trace_summary["guardrail_blocks"] != int(want):
                failures.append(
                    f"expected {want} guardrail block(s), got {trace_summary['guardrail_blocks']}"
                )
        elif key == "response_contains_redaction":
            has_redaction = bool(REDACTION_RE.search(response_text))
            if bool(want) != has_redaction:
                failures.append(
                    f"response_contains_redaction expected {want}, got {has_redaction}"
                )
        elif key == "response_contains_pii":
            has_pii = bool(EMAIL_RE.search(response_text) or PHONE_RE.search(response_text) or SSN_LAST4_RE.search(response_text))
            if bool(want) != has_pii:
                failures.append(f"response_contains_pii expected {want}, got {has_pii}")
        elif key == "response_contains":
            if want not in response_text:
                failures.append(f"response missing substring {want!r}")
        elif key == "response_not_contains":
            if want in response_text:
                failures.append(f"response unexpectedly contains {want!r}")
        else:
            failures.append(f"unknown expectation key {key!r}")
    return failures


def _normalize_expectations(expect: Any) -> dict[str, Any]:
    if isinstance(expect, dict):
        return expect
    if isinstance(expect, list):
        out: dict[str, Any] = {}
        for item in expect:
            if isinstance(item, dict):
                out.update(item)
        return out
    return {}


def _persona_role_arns_from_outputs(cfg: dict[str, Any], args: argparse.Namespace) -> dict[str, str]:
    return {
        "dispatcher": args.dispatcher_role_arn or cfg.get("dispatcher_role_arn", ""),
        "technician_lead": args.technician_lead_role_arn or cfg.get("technician_lead_role_arn", ""),
        "owner": args.owner_role_arn or cfg.get("owner_role_arn", ""),
    }


def _load_terraform_outputs(tf_dir: Path) -> dict[str, Any]:
    import subprocess

    proc = subprocess.run(
        ["terraform", f"-chdir={tf_dir}", "output", "-json"],
        capture_output=True, text=True, check=True,
    )
    raw = json.loads(proc.stdout)
    return {k: v.get("value") for k, v in raw.items()}


def _load_cases(prompts_dir: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(prompts_dir.glob("*.yaml")):
        with path.open() as f:
            content = yaml.safe_load(f) or []
        for case in content:
            case["_source"] = path.name
            cases.append(case)
    return cases


def _write_report(results: list[CaseResult], report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = report_dir / f"eval-report-{ts}.md"

    passed = sum(1 for r in results if r.passed)
    total = len(results)

    lines = [
        f"# Eval report — {ts}",
        "",
        f"**{passed}/{total} cases passed.**",
        "",
        "| Case | Persona | Status | Failures |",
        "|---|---|---|---|",
    ]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        failures = "; ".join(r.failures) if r.failures else "—"
        lines.append(f"| `{r.case_id}` | {r.persona} | {status} | {failures} |")

    lines.append("")
    lines.append("## Failures detail")
    lines.append("")
    for r in results:
        if r.passed:
            continue
        lines.extend([
            f"### {r.case_id} — {r.persona}",
            "",
            f"**Prompt:** {r.prompt}",
            "",
            f"**Failures:** {'; '.join(r.failures)}",
            "",
            "**Response (first 1000 chars):**",
            "",
            "```",
            r.response_text[:1000],
            "```",
            "",
            f"**Trace summary:** `{json.dumps(r.trace_summary)}`",
            "",
        ])

    path.write_text("\n".join(lines))
    return path


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the eval corpora against the deployed agent.")
    p.add_argument("--prompts-dir", type=Path, default=Path(__file__).parent / "prompts")
    p.add_argument("--report-dir", type=Path, default=Path(__file__).parent / "reports")
    p.add_argument(
        "--tf-dir", type=Path,
        default=Path(__file__).parent.parent / "terraform" / "envs" / "demo",
        help="Terraform env dir to read outputs from. Pass empty/--tf-dir='' to disable.",
    )
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    p.add_argument("--agent-id")
    p.add_argument("--agent-alias-id")
    p.add_argument("--dispatcher-role-arn")
    p.add_argument("--technician-lead-role-arn")
    p.add_argument("--owner-role-arn")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())

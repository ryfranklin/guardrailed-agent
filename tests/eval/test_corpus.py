"""Eval corpus runner — pytest-flavored.

Runs the full ADR-008 golden + red-team corpus against the deployed Demo
agent. Skipped unless RUN_INTEGRATION=1 (the corpus needs a live Bedrock
Agent + Athena + Lake Formation in the demo account).

Each parameterized case:
  1. Loads from eval/prompts/*.yaml
  2. Assumes the persona role with session tags
  3. Calls Bedrock InvokeAgent with enableTrace=True
  4. Captures the orchestration trace + final response
  5. Applies the case's ``expect`` assertions
  6. Emits a structured invocation log line to the gagent CloudWatch
     log group (AgentCore Observability native)

On failure, the assertion message includes:
  case_id, persona, prompt, expected vs actual assertions, the trace
  summary (tools called, guardrail blocks), the response text head, and
  the case's ``lf_policy`` field — i.e., the Lake Formation policy that
  should have applied.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPTS_DIR = REPO_ROOT / "eval" / "prompts"
TF_DIR = REPO_ROOT / "terraform" / "envs" / "demo"

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_INTEGRATION"),
    reason="set RUN_INTEGRATION=1 to run the AWS-backed eval corpus",
)


def _load_runner_module():
    """Import eval/runner.py without requiring eval/ to be a package."""
    spec = importlib.util.spec_from_file_location(
        "_eval_runner_for_tests",
        REPO_ROOT / "eval" / "runner.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_eval_runner_for_tests"] = module
    spec.loader.exec_module(module)
    return module


def _load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(PROMPTS_DIR.glob("*.yaml")):
        with path.open() as f:
            data = yaml.safe_load(f) or []
        for c in data:
            c["_source"] = path.name
            cases.append(c)
    return cases


def _terraform_outputs() -> dict[str, Any]:
    proc = subprocess.run(
        ["terraform", f"-chdir={TF_DIR}", "output", "-json"],
        capture_output=True, text=True, check=True,
    )
    raw = json.loads(proc.stdout)
    return {k: v["value"] for k, v in raw.items()}


CASES = _load_cases() if os.environ.get("RUN_INTEGRATION") else []


@pytest.fixture(scope="session")
def harness():
    """Resolve agent IDs, persona role ARNs, and the invocation log group once."""
    runner = _load_runner_module()
    cfg = _terraform_outputs()
    region = os.environ.get("AWS_REGION", "us-east-1")

    persona_role_arns = {
        "dispatcher": cfg["dispatcher_role_arn"],
        "technician_lead": cfg["technician_lead_role_arn"],
        "owner": cfg["owner_role_arn"],
    }
    log_group = (
        cfg.get("invocation_log_group")
        or os.environ.get("GAGENT_LOG_GROUP")
    )

    yield {
        "runner": runner,
        "agent_id": cfg["agent_id"],
        "agent_alias_id": cfg["agent_alias_id"],
        "region": region,
        "persona_role_arns": persona_role_arns,
        "log_group": log_group,
    }


@pytest.mark.parametrize(
    "case",
    CASES if CASES else [pytest.param({}, marks=pytest.mark.skip(reason="corpus not loaded"))],
    ids=lambda c: c.get("id", "unloaded"),
)
def test_corpus_case(case, harness):
    runner = harness["runner"]
    result = runner._run_case(
        case,
        harness["agent_id"],
        harness["agent_alias_id"],
        harness["region"],
        harness["persona_role_arns"],
        harness["log_group"],
    )

    if result.passed:
        return

    expectations = runner._normalize_expectations(case.get("expect", {}))
    msg = "\n".join([
        "",
        f"  case_id:  {case['id']}",
        f"  persona:  {result.persona}",
        f"  source:   {case.get('_source', 'n/a')}",
        f"  prompt:   {case['prompt']}",
        f"  expected: {json.dumps(expectations, sort_keys=True)}",
        f"  failures: {'; '.join(result.failures)}",
        f"  tools:    {result.trace_summary.get('tools_called', [])}",
        f"  guard:    {result.trace_summary.get('guardrail_blocks', 0)} block(s)",
        f"  response: {result.response_text[:500]!r}",
        f"  lf_policy: {case.get('lf_policy', 'n/a')}",
    ])
    pytest.fail(msg, pytrace=False)

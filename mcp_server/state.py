"""Server state, configuration, trust gate, and token-budget tracking.

Per ADR-009 Phase 2.a:
  * The trust gate refuses to start the server unless GAGENT_TRUSTED_OPERATOR=1.
  * Persona resolution is Shape A (FlagPersonaResolver) only; the SSO
    resolver lands in Phase 2.d (Prompt 2.7).
  * A per-session token-budget warning logs at WARN when usage crosses
    the configured threshold.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from gagent_client import (
    DEFAULT_LOG_GROUP,
    FlagPersonaResolver,
    SsoPersonaResolver,
)
from gagent_client.identity import SsoIdentityError, SsoMappingError

logger = logging.getLogger("mcp_server.state")

DEFAULT_TOKEN_BUDGET = 25_000
DEFAULT_TF_DIR_RELATIVE = "terraform/envs/demo"
TRUST_ENV_VAR = "GAGENT_TRUSTED_OPERATOR"
TRUST_ERROR_MESSAGE = (
    f"{TRUST_ENV_VAR}=1 is required for Shape A (single-operator). "
    "Shape A trusts the caller and is unsafe outside solo use. "
    "For team adoption, leave the env var unset and configure Shape B "
    "(IAM Identity Center via persona_mapping.json) per "
    "docs/mcp/team-deployment.md."
)

SHAPE_A = "A"
SHAPE_B = "B"


class TrustGateError(SystemExit):
    """Raised when the trust gate is not satisfied (Shape A only)."""


class ServerStartupError(RuntimeError):
    """Raised when MCP startup cannot resolve a working persona resolver."""


def enforce_trust_gate(env: Mapping[str, str]) -> None:
    """Refuse to start unless GAGENT_TRUSTED_OPERATOR=1 is set.

    Used by callers that are Shape A only (the ``gra`` CLI per ADR-006).
    The MCP server uses ``determine_shape`` instead so Shape B is reachable.
    """
    if env.get(TRUST_ENV_VAR) != "1":
        logger.error(TRUST_ERROR_MESSAGE)
        raise TrustGateError(1)


def determine_shape(env: Mapping[str, str]) -> str:
    """Return SHAPE_A or SHAPE_B for the current environment.

    Shape A requires explicit opt-in via ``GAGENT_TRUSTED_OPERATOR=1``.
    Otherwise Shape B (SSO via IAM Identity Center) is the default; the
    SsoPersonaResolver fails at construction if no IIC identity is reachable.
    """
    if env.get(TRUST_ENV_VAR) == "1":
        return SHAPE_A
    return SHAPE_B


class TokenCounter:
    """Per-session token counter with one warning per breach.

    The token estimate is a simple chars/4 heuristic — Bedrock's actual
    usage numbers ride in the trace events, but the heuristic is good
    enough for an early-warning budget guardrail (ADR-001 §cost-runaway).

    Warnings fire at every multiple of the threshold so a long-running
    session can't silently burn through 10x its budget.
    """

    def __init__(self, budget: int):
        if budget <= 0:
            raise ValueError(f"budget must be positive, got {budget}")
        self.budget = budget
        self.used = 0
        self.warnings_emitted = 0

    def add(self, *, input_text: str, output_text: str) -> int:
        approx = (len(input_text) + len(output_text)) // 4
        self.used += approx
        crossed = self.used // self.budget
        if crossed > self.warnings_emitted:
            logger.warning(
                "session token budget exceeded: ~%d tokens used "
                "(threshold=%d, %dx breach)",
                self.used, self.budget, crossed,
            )
            self.warnings_emitted = crossed
        return approx


@dataclass
class ServerConfig:
    """Server-wide configuration resolved at startup."""

    resolver: Any  # FlagPersonaResolver or SsoPersonaResolver or None
    agent_id: str | None
    agent_alias_id: str | None
    region: str
    glue_database: str | None
    default_persona: str
    default_service_region: str | None
    log_group: str = DEFAULT_LOG_GROUP
    token_budget: int = DEFAULT_TOKEN_BUDGET
    # Phase 2.c additions used by health() and propose_query.
    foundation_model_id: str | None = None
    athena_workgroup_name: str | None = None
    # Phase 2.d: which deployment shape was resolved at startup.
    shape: str = SHAPE_A


@dataclass
class ServerState:
    """Runtime state — config plus mutable counters."""

    config: ServerConfig
    tokens: TokenCounter = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = TokenCounter(self.config.token_budget)


def load_config(env: Mapping[str, str] | None = None) -> ServerConfig:
    """Resolve config from terraform outputs + env overrides.

    Order:
      1. Terraform outputs (typically the deployed Demo env) — single
         source of truth for ARNs, agent IDs, glue database.
      2. Env vars override terraform outputs:
           GAGENT_AGENT_ID, GAGENT_AGENT_ALIAS_ID,
           GAGENT_DISPATCHER_ROLE_ARN, GAGENT_TECHNICIAN_LEAD_ROLE_ARN,
           GAGENT_OWNER_ROLE_ARN, GAGENT_GLUE_DATABASE,
           GAGENT_DEFAULT_PERSONA, GAGENT_DEFAULT_SERVICE_REGION,
           GAGENT_TOKEN_BUDGET, AWS_REGION.

    Missing pieces don't fail at load time — tools that need them
    return structured error responses on call.
    """
    env = env if env is not None else os.environ
    tf_outputs = _read_terraform_outputs(env)

    role_arns: dict[str, str] = {}
    for role in ("dispatcher", "technician_lead", "owner"):
        env_key = f"GAGENT_{role.upper()}_ROLE_ARN"
        arn = env.get(env_key) or tf_outputs.get(f"{role}_role_arn")
        if arn:
            role_arns[role] = arn

    shape = determine_shape(env)
    resolver = _build_resolver_for_shape(shape, env, role_arns)

    region = env.get("AWS_REGION", "us-east-1")

    # In Shape B the persona is bound to the SSO identity; honor that as
    # the server's default_persona so list_tools / health surface the right value.
    default_persona = env.get("GAGENT_DEFAULT_PERSONA", "owner")
    default_service_region = env.get("GAGENT_DEFAULT_SERVICE_REGION")
    if shape == SHAPE_B and isinstance(resolver, SsoPersonaResolver):
        default_persona = resolver.resolved_persona.role
        default_service_region = (
            resolver.resolved_persona.service_region
            or default_service_region
        )

    return ServerConfig(
        resolver=resolver,
        agent_id=env.get("GAGENT_AGENT_ID") or tf_outputs.get("agent_id"),
        agent_alias_id=env.get("GAGENT_AGENT_ALIAS_ID") or tf_outputs.get("agent_alias_id"),
        region=region,
        glue_database=env.get("GAGENT_GLUE_DATABASE") or tf_outputs.get("glue_database_name"),
        default_persona=default_persona,
        default_service_region=default_service_region,
        log_group=env.get("GAGENT_LOG_GROUP")
            or tf_outputs.get("invocation_log_group")
            or DEFAULT_LOG_GROUP,
        token_budget=int(env.get("GAGENT_TOKEN_BUDGET", str(DEFAULT_TOKEN_BUDGET))),
        foundation_model_id=env.get("GAGENT_FOUNDATION_MODEL_ID")
            or tf_outputs.get("foundation_model_id"),
        athena_workgroup_name=env.get("GAGENT_ATHENA_WORKGROUP")
            or tf_outputs.get("athena_workgroup_name"),
        shape=shape,
    )


def _build_resolver_for_shape(
    shape: str, env: Mapping[str, str], role_arns: dict[str, str],
) -> Any:
    """Pick the right resolver for the deployment shape.

    Shape A is forgiving (resolver=None when no ARNs configured — tools
    return structured errors). Shape B is strict — startup fails if SSO
    identity / mapping aren't reachable, so the team-adoption path
    surfaces misconfigurations immediately.
    """
    if shape == SHAPE_A:
        return FlagPersonaResolver(role_arns) if role_arns else None

    # Shape B
    if not role_arns:
        raise ServerStartupError(
            "Shape B requires persona role ARNs in env or terraform "
            "output. Set GAGENT_{DISPATCHER,TECHNICIAN_LEAD,OWNER}_ROLE_ARN "
            "(see docs/mcp/team-deployment.md).",
        )
    mapping_path = env.get("GAGENT_PERSONA_MAPPING_FILE")
    try:
        return SsoPersonaResolver(role_arns, mapping_path=mapping_path)
    except (SsoIdentityError, SsoMappingError) as exc:
        raise ServerStartupError(
            f"Shape B startup failed: {exc} "
            "If you intended Shape A (single-operator), set "
            f"{TRUST_ENV_VAR}=1.",
        ) from exc


def _read_terraform_outputs(env: Mapping[str, str]) -> dict[str, Any]:
    tf_dir = env.get("GAGENT_TF_DIR")
    if tf_dir:
        path = Path(tf_dir)
    else:
        # Default: discover terraform/envs/demo relative to repo root.
        path = Path(__file__).resolve().parent.parent / DEFAULT_TF_DIR_RELATIVE
    if not path.exists():
        logger.info("terraform dir not found at %s; relying on env vars", path)
        return {}
    try:
        proc = subprocess.run(
            ["terraform", f"-chdir={path}", "output", "-json"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        raw = json.loads(proc.stdout)
        return {k: v.get("value") for k, v in raw.items()}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            json.JSONDecodeError, FileNotFoundError) as exc:
        logger.warning("terraform output read failed: %s; relying on env vars", exc)
        return {}

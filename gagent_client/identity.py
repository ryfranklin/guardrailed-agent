"""Persona resolution for the guardrailed-agent client (ADR-006, ADR-009).

Two deployment shapes:

  Shape A — FlagPersonaResolver
    Caller passes the persona role name; resolver looks up the IAM ARN.
    Trust model: caller is trusted. Single-operator only. The
    GAGENT_TRUSTED_OPERATOR=1 trust gate per ADR-006 is enforced at the
    surface layer (CLI / MCP startup), not in this library.

  Shape B — SsoPersonaResolver
    Read the developer's IAM Identity Center identity (sts caller-identity
    against the SSO-assumed-role principal), map to a persona role via a
    static config file, return that Persona for every resolve() call.
    Per-call ``--persona`` overrides are no-ops with a WARN log — the
    persona is bound to the developer's SSO identity, not the caller's
    request. This is the team-adoption path per ADR-009.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

logger = logging.getLogger("gagent_client.identity")

VALID_ROLES: tuple[str, ...] = ("dispatcher", "technician_lead", "owner")

DEFAULT_MAPPING_FILE = Path(__file__).resolve().parent / "persona_mapping.json"


@dataclass(frozen=True)
class Persona:
    """Resolved persona — role, IAM role ARN, optional service_region.

    The role tag and (optionally) service_region tag are propagated
    transitively through STS AssumeRole so Lake Formation evaluates
    `aws:PrincipalTag/role` at access-check time per ADR-003.
    """

    role: str
    role_arn: str
    service_region: str | None = None

    def __post_init__(self) -> None:
        if self.role not in VALID_ROLES:
            raise ValueError(
                f"role must be one of {VALID_ROLES}; got {self.role!r}",
            )
        if self.role == "technician_lead" and not self.service_region:
            raise ValueError(
                "technician_lead persona requires a non-empty service_region",
            )
        if self.role != "technician_lead" and self.service_region:
            raise ValueError(
                f"service_region only applies to technician_lead; "
                f"got {self.role!r} with service_region={self.service_region!r}",
            )


@runtime_checkable
class PersonaResolver(Protocol):
    """Resolves a (role, service_region) request into a Persona."""

    def resolve(
        self, role: str, *, service_region: str | None = None,
    ) -> Persona:
        ...


class FlagPersonaResolver:
    """Shape A — caller passes role name; resolver supplies the role ARN.

    The role-arns mapping is typically built from terraform outputs at
    process start. Construction validates that all keys are valid
    persona names.
    """

    def __init__(self, role_arns: Mapping[str, str]):
        unknown = [r for r in role_arns if r not in VALID_ROLES]
        if unknown:
            raise ValueError(
                f"unknown role(s) in role_arns mapping: {unknown}; "
                f"valid: {list(VALID_ROLES)}",
            )
        self._role_arns: dict[str, str] = dict(role_arns)

    def resolve(
        self, role: str, *, service_region: str | None = None,
    ) -> Persona:
        if role not in self._role_arns:
            raise KeyError(
                f"no role ARN configured for {role!r}; "
                f"have {sorted(self._role_arns)}",
            )
        return Persona(
            role=role,
            role_arn=self._role_arns[role],
            service_region=service_region,
        )

    def known_roles(self) -> list[str]:
        return sorted(self._role_arns)


# ---- Shape B (SSO) ----

class SsoIdentityError(Exception):
    """Raised when the caller's identity is not an IIC-assumed-role principal."""


class SsoMappingError(Exception):
    """Raised when no rule in persona_mapping.json matches the caller."""


_SSO_ARN_RE = re.compile(
    r"^arn:[\w-]+:sts::(?P<account>\d+):assumed-role/"
    r"AWSReservedSSO_(?P<role_path>.+?)/(?P<user_id>.+)$",
)
_SSO_HASH_RE = re.compile(r"_[A-Za-z0-9]{8,}$")


def _parse_sso_arn(arn: str) -> dict[str, str] | None:
    """Extract account_id, permission_set, sso_user_id from an IIC-assumed-role ARN.

    Returns None for non-SSO ARNs (long-term IAM users, plain roles, etc.).
    The IAM Identity Center role naming convention is:
        AWSReservedSSO_<permission_set_name>_<random_hash>
    where <random_hash> is a 16-char alphanumeric. Permission set names may
    contain underscores, so we strip the trailing hash with a regex rather
    than splitting naively.
    """
    match = _SSO_ARN_RE.match(arn)
    if not match:
        return None
    role_path = match.group("role_path")
    permission_set = _SSO_HASH_RE.sub("", role_path)
    if permission_set == role_path:
        # No trailing hash detected — not a real IIC role.
        return None
    return {
        "account_id": match.group("account"),
        "permission_set": permission_set,
        "sso_user_id": match.group("user_id"),
    }


class SsoPersonaResolver:
    """Shape B — IAM Identity Center identity → persona role mapping.

    Construction probes the caller's STS identity. If the caller isn't
    SSO-assumed (e.g., long-term IAM user, EC2 instance profile), startup
    fails with SsoIdentityError. The mapping is a static JSON file
    (default ``gagent_client/persona_mapping.json``); rules are matched
    in order, first match wins, with optional ``default_persona`` fallback.

    Once constructed, every ``resolve()`` call returns the SSO-bound
    Persona, regardless of caller-supplied role / service_region. Overrides
    log a WARN (once per distinct value) so operators can see when their
    flag was ignored.
    """

    def __init__(
        self,
        role_arns: Mapping[str, str],
        *,
        mapping: dict[str, Any] | None = None,
        mapping_path: str | Path | None = None,
        sts_client: Any = None,
    ):
        unknown = [r for r in role_arns if r not in VALID_ROLES]
        if unknown:
            raise ValueError(
                f"unknown role(s) in role_arns mapping: {unknown}; "
                f"valid: {list(VALID_ROLES)}",
            )
        self._role_arns: dict[str, str] = dict(role_arns)

        if mapping is None:
            mapping = _load_mapping_file(
                Path(mapping_path) if mapping_path else DEFAULT_MAPPING_FILE,
            )
        self._mapping = mapping

        self._sso_identity = self._fetch_identity(sts_client)
        self._resolved_persona = self._lookup_persona(
            self._sso_identity, self._mapping,
        )
        # Track which (key, value) overrides we've already warned about so
        # noisy callers don't spam the log.
        self._logged_overrides: set[tuple[str, str]] = set()

    @staticmethod
    def _fetch_identity(sts_client: Any) -> dict[str, str]:
        import boto3

        sts = sts_client or boto3.client("sts")
        try:
            response = sts.get_caller_identity()
        except Exception as exc:  # noqa: BLE001
            raise SsoIdentityError(
                f"sts:GetCallerIdentity failed: {type(exc).__name__}: {exc}. "
                "Run `aws sso login` or use Shape A "
                "(GAGENT_TRUSTED_OPERATOR=1).",
            ) from exc
        arn = response.get("Arn", "")
        parsed = _parse_sso_arn(arn)
        if parsed is None:
            raise SsoIdentityError(
                f"Caller identity {arn!r} is not an IAM Identity Center "
                "assumed-role. Shape B requires SSO; run `aws sso login` "
                "or use Shape A (GAGENT_TRUSTED_OPERATOR=1).",
            )
        return parsed

    def _lookup_persona(
        self, identity: dict[str, str], mapping: dict[str, Any],
    ) -> Persona:
        rules = mapping.get("rules") or []
        for rule in rules:
            match = rule.get("match") or {}
            if not match:
                continue
            if all(identity.get(k) == v for k, v in match.items()):
                return self._build_persona(rule, mapping)

        default_role = mapping.get("default_persona")
        if default_role:
            return self._build_persona(
                {
                    "persona": default_role,
                    "service_region": mapping.get("default_service_region"),
                },
                mapping,
            )

        raise SsoMappingError(
            f"No persona mapping for SSO identity "
            f"(permission_set={identity.get('permission_set')!r}, "
            f"sso_user_id={identity.get('sso_user_id')!r}). "
            "Add a rule to persona_mapping.json or set default_persona. "
            "See docs/mcp/team-deployment.md.",
        )

    def _build_persona(
        self, rule: dict[str, Any], mapping: dict[str, Any],
    ) -> Persona:
        role = rule.get("persona")
        if role not in VALID_ROLES:
            raise SsoMappingError(
                f"rule references unknown persona {role!r}; "
                f"valid: {list(VALID_ROLES)}",
            )
        if role not in self._role_arns:
            raise SsoMappingError(
                f"persona {role!r} has no configured IAM role ARN. "
                "Set GAGENT_*_ROLE_ARN env vars or run terraform apply.",
            )
        service_region = (
            rule.get("service_region")
            or mapping.get("default_service_region")
        )
        if role == "technician_lead" and not service_region:
            raise SsoMappingError(
                "technician_lead persona rule must specify service_region "
                "(or default_service_region in the mapping).",
            )
        if role != "technician_lead":
            service_region = None
        return Persona(
            role=role, role_arn=self._role_arns[role],
            service_region=service_region,
        )

    def resolve(
        self, role: str | None = None, *, service_region: str | None = None,
    ) -> Persona:
        if role and role != self._resolved_persona.role:
            self._warn_override("role", role, self._resolved_persona.role)
        if (
            service_region
            and service_region != self._resolved_persona.service_region
        ):
            self._warn_override(
                "service_region", service_region,
                self._resolved_persona.service_region or "",
            )
        return self._resolved_persona

    def _warn_override(
        self, field: str, requested: str, resolved: str,
    ) -> None:
        key = (field, requested)
        if key in self._logged_overrides:
            return
        self._logged_overrides.add(key)
        logger.warning(
            "Shape B: ignoring %s=%r override; SSO-resolved value is %r",
            field, requested, resolved,
        )

    def known_roles(self) -> list[str]:
        return [self._resolved_persona.role]

    @property
    def sso_identity(self) -> dict[str, str]:
        return dict(self._sso_identity)

    @property
    def resolved_persona(self) -> Persona:
        return self._resolved_persona


def _load_mapping_file(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as exc:
        raise SsoMappingError(
            f"persona mapping file not found: {path}. Set "
            "GAGENT_PERSONA_MAPPING_FILE or copy "
            "gagent_client/persona_mapping.json into place.",
        ) from exc
    except json.JSONDecodeError as exc:
        raise SsoMappingError(
            f"persona mapping file at {path} is not valid JSON: {exc}",
        ) from exc

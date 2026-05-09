"""Contract tests for CognitoPersonaResolver (ADR-007, phase-3a-brief §8).

Pure-logic only. JWT validation is the API Gateway HTTP API authorizer's
job upstream — these tests pass already-decoded claims dicts.

Behavior table from §8 of docs/phase-3a-brief.md:

  | Mode          | claim   | requested_role  | service_region | Result                       |
  |---------------|---------|-----------------|----------------|------------------------------|
  | request-param | (any)   | dispatcher      | (any)          | Persona(dispatcher)          |
  | request-param | (any)   | technician_lead | tempe-mesa     | Persona(technician_lead, r)  |
  | request-param | (any)   | technician_lead | None           | ValueError                   |
  | request-param | (any)   | None            | (any)          | ValueError                   |
  | request-param | (any)   | invalid         | (any)          | ValueError                   |
  | claim-bound   | dispatcher | None         | (any)          | Persona(dispatcher)          |
  | claim-bound   | dispatcher | dispatcher   | (any)          | Persona(dispatcher)          |
  | claim-bound   | dispatcher | owner        | (any)          | PermissionError              |
  | claim-bound   | (absent)  | (any)        | (any)          | SsoMappingError              |
"""

from __future__ import annotations

import pytest

from gagent_client import (
    CognitoPersonaResolver,
    Persona,
    VALID_COGNITO_MODES,
)
from gagent_client.identity import (
    COGNITO_MODE_ENV_VAR,
    DEFAULT_SERVICE_REGION_ENV_VAR,
    SsoMappingError,
)


def _arns() -> dict[str, str]:
    return {
        "dispatcher": "arn:aws:iam::1:role/gagent-dispatcher-demo",
        "technician_lead": "arn:aws:iam::1:role/gagent-technician-lead-demo",
        "owner": "arn:aws:iam::1:role/gagent-owner-demo",
    }


def _claims_with_persona(persona: str | None) -> dict[str, object]:
    base: dict[str, object] = {"sub": "user-123", "email": "alice@example.com"}
    if persona is not None:
        base["custom:persona"] = persona
    return base


# ---- Construction & mode resolution ----


class TestConstruction:
    def test_explicit_mode_request_param(self):
        r = CognitoPersonaResolver(_arns(), mode="request-param", env={})
        assert r.mode == "request-param"

    def test_explicit_mode_claim_bound(self):
        r = CognitoPersonaResolver(_arns(), mode="claim-bound", env={})
        assert r.mode == "claim-bound"

    def test_default_mode_is_request_param_when_env_unset(self):
        r = CognitoPersonaResolver(_arns(), env={})
        assert r.mode == "request-param"

    def test_mode_resolution_from_env_var(self):
        r = CognitoPersonaResolver(
            _arns(),
            env={COGNITO_MODE_ENV_VAR: "claim-bound"},
        )
        assert r.mode == "claim-bound"

    def test_explicit_mode_overrides_env_var(self):
        r = CognitoPersonaResolver(
            _arns(),
            mode="request-param",
            env={COGNITO_MODE_ENV_VAR: "claim-bound"},
        )
        assert r.mode == "request-param"

    def test_invalid_mode_explicit_raises(self):
        with pytest.raises(ValueError, match="mode must be one of"):
            CognitoPersonaResolver(_arns(), mode="hybrid", env={})

    def test_invalid_mode_via_env_raises(self):
        with pytest.raises(ValueError, match="mode must be one of"):
            CognitoPersonaResolver(
                _arns(), env={COGNITO_MODE_ENV_VAR: "weird"},
            )

    def test_constructor_rejects_unknown_role_in_arns(self):
        with pytest.raises(ValueError, match="unknown role"):
            CognitoPersonaResolver(
                {"hacker": "arn:aws:iam::1:role/x"},
                mode="request-param",
                env={},
            )

    def test_known_roles_returns_sorted(self):
        r = CognitoPersonaResolver(_arns(), mode="request-param", env={})
        assert r.known_roles() == ["dispatcher", "owner", "technician_lead"]

    def test_valid_modes_constant(self):
        assert set(VALID_COGNITO_MODES) == {"request-param", "claim-bound"}


# ---- Shape A: request-param ----


class TestRequestParamMode:
    def _resolver(self, env: dict[str, str] | None = None) -> CognitoPersonaResolver:
        return CognitoPersonaResolver(
            _arns(), mode="request-param", env=env or {},
        )

    def test_dispatcher_with_any_claim(self):
        r = self._resolver()
        p = r.resolve(
            claims=_claims_with_persona("owner"),  # claim ignored in Shape A
            requested_role="dispatcher",
        )
        assert p.role == "dispatcher"
        assert p.role_arn == _arns()["dispatcher"]
        assert p.service_region is None

    def test_dispatcher_with_no_claim(self):
        r = self._resolver()
        p = r.resolve(claims={}, requested_role="dispatcher")
        assert p.role == "dispatcher"

    def test_dispatcher_drops_irrelevant_service_region(self):
        r = self._resolver()
        p = r.resolve(
            claims={},
            requested_role="dispatcher",
            requested_service_region="tempe-mesa",
        )
        assert p.role == "dispatcher"
        assert p.service_region is None

    def test_owner_with_any_claim(self):
        r = self._resolver()
        p = r.resolve(
            claims=_claims_with_persona("dispatcher"),
            requested_role="owner",
        )
        assert p.role == "owner"
        assert p.service_region is None

    def test_technician_lead_with_service_region(self):
        r = self._resolver()
        p = r.resolve(
            claims={},
            requested_role="technician_lead",
            requested_service_region="tempe-mesa",
        )
        assert p.role == "technician_lead"
        assert p.service_region == "tempe-mesa"
        assert p.role_arn == _arns()["technician_lead"]

    def test_technician_lead_without_service_region_raises(self):
        r = self._resolver()
        with pytest.raises(ValueError, match="service_region"):
            r.resolve(
                claims={},
                requested_role="technician_lead",
                requested_service_region=None,
            )

    def test_technician_lead_with_empty_service_region_raises(self):
        r = self._resolver()
        with pytest.raises(ValueError, match="service_region"):
            r.resolve(
                claims={},
                requested_role="technician_lead",
                requested_service_region="",
            )

    def test_no_requested_role_raises(self):
        r = self._resolver()
        with pytest.raises(ValueError, match="request-param mode requires"):
            r.resolve(claims=_claims_with_persona("owner"), requested_role=None)

    def test_empty_requested_role_raises(self):
        r = self._resolver()
        with pytest.raises(ValueError, match="request-param mode requires"):
            r.resolve(claims={}, requested_role="")

    def test_invalid_requested_role_raises(self):
        r = self._resolver()
        with pytest.raises(ValueError, match="invalid persona"):
            r.resolve(claims={}, requested_role="superuser")

    def test_default_service_region_env_var_does_not_apply_in_request_param(self):
        # Shape A is strictly explicit — the env var fallback only applies in
        # claim-bound mode.
        r = self._resolver(env={DEFAULT_SERVICE_REGION_ENV_VAR: "tempe-mesa"})
        with pytest.raises(ValueError, match="service_region"):
            r.resolve(
                claims={},
                requested_role="technician_lead",
                requested_service_region=None,
            )

    def test_returned_persona_is_correct_type(self):
        r = self._resolver()
        p = r.resolve(claims={}, requested_role="dispatcher")
        assert isinstance(p, Persona)

    def test_role_arns_map_is_isolated(self):
        arns = _arns()
        r = CognitoPersonaResolver(arns, mode="request-param", env={})
        arns["dispatcher"] = "arn:aws:iam::999:role/evil"
        p = r.resolve(claims={}, requested_role="dispatcher")
        assert p.role_arn == _arns()["dispatcher"]


# ---- Shape B: claim-bound ----


class TestClaimBoundMode:
    def _resolver(self, env: dict[str, str] | None = None) -> CognitoPersonaResolver:
        return CognitoPersonaResolver(
            _arns(), mode="claim-bound", env=env or {},
        )

    def test_claim_dispatcher_no_request_role(self):
        r = self._resolver()
        p = r.resolve(
            claims=_claims_with_persona("dispatcher"),
            requested_role=None,
        )
        assert p.role == "dispatcher"
        assert p.service_region is None

    def test_claim_owner_no_request_role(self):
        r = self._resolver()
        p = r.resolve(
            claims=_claims_with_persona("owner"),
            requested_role=None,
        )
        assert p.role == "owner"

    def test_claim_dispatcher_matching_request_role(self):
        r = self._resolver()
        p = r.resolve(
            claims=_claims_with_persona("dispatcher"),
            requested_role="dispatcher",
        )
        assert p.role == "dispatcher"

    def test_claim_dispatcher_mismatched_request_role_raises(self):
        r = self._resolver()
        with pytest.raises(PermissionError, match="does not match JWT claim"):
            r.resolve(
                claims=_claims_with_persona("dispatcher"),
                requested_role="owner",
            )

    def test_absent_claim_raises_sso_mapping_error(self):
        r = self._resolver()
        with pytest.raises(SsoMappingError, match="custom:persona"):
            r.resolve(claims=_claims_with_persona(None), requested_role=None)

    def test_empty_string_claim_raises(self):
        r = self._resolver()
        with pytest.raises(SsoMappingError, match="custom:persona"):
            r.resolve(claims=_claims_with_persona(""), requested_role=None)

    def test_invalid_claim_value_raises(self):
        r = self._resolver()
        with pytest.raises(SsoMappingError, match="not a valid persona"):
            r.resolve(
                claims=_claims_with_persona("superuser"),
                requested_role=None,
            )

    def test_technician_lead_claim_uses_request_service_region(self):
        r = self._resolver()
        p = r.resolve(
            claims=_claims_with_persona("technician_lead"),
            requested_role=None,
            requested_service_region="tempe-mesa",
        )
        assert p.role == "technician_lead"
        assert p.service_region == "tempe-mesa"

    def test_technician_lead_claim_falls_back_to_env_default_region(self):
        r = self._resolver(env={DEFAULT_SERVICE_REGION_ENV_VAR: "north-phoenix"})
        p = r.resolve(
            claims=_claims_with_persona("technician_lead"),
            requested_role=None,
        )
        assert p.role == "technician_lead"
        assert p.service_region == "north-phoenix"

    def test_technician_lead_claim_request_region_beats_env_default(self):
        r = self._resolver(env={DEFAULT_SERVICE_REGION_ENV_VAR: "north-phoenix"})
        p = r.resolve(
            claims=_claims_with_persona("technician_lead"),
            requested_role="technician_lead",
            requested_service_region="tempe-mesa",
        )
        assert p.service_region == "tempe-mesa"

    def test_technician_lead_claim_no_region_anywhere_raises(self):
        r = self._resolver()
        with pytest.raises(SsoMappingError, match="service_region"):
            r.resolve(
                claims=_claims_with_persona("technician_lead"),
                requested_role=None,
            )

    def test_claim_bound_ignores_irrelevant_service_region_for_dispatcher(self):
        r = self._resolver()
        p = r.resolve(
            claims=_claims_with_persona("dispatcher"),
            requested_role=None,
            requested_service_region="tempe-mesa",
        )
        assert p.service_region is None

    def test_missing_role_arn_for_claim_raises_keyerror(self):
        # If the user pool emits a persona claim that the deployment hasn't
        # provisioned an IAM role for, that's a configuration error.
        r = CognitoPersonaResolver(
            {"dispatcher": "arn:aws:iam::1:role/d"},
            mode="claim-bound",
            env={},
        )
        with pytest.raises(KeyError, match="owner"):
            r.resolve(
                claims=_claims_with_persona("owner"),
                requested_role=None,
            )

"""Contract tests for gagent_client.identity (ADR-006, ADR-009).

Pure-logic only. AWS-touching behavior (assume_persona, invoke) is
exercised by tests/integration/ and the eval corpus.
"""

from __future__ import annotations

import pytest

from gagent_client import (
    FlagPersonaResolver,
    Persona,
    PersonaResolver,
    SsoPersonaResolver,
    VALID_ROLES,
)


# ---- Persona dataclass ----

class TestPersonaDataclass:
    def test_constructs_dispatcher(self):
        p = Persona(role="dispatcher", role_arn="arn:aws:iam::1:role/d")
        assert p.role == "dispatcher"
        assert p.role_arn == "arn:aws:iam::1:role/d"
        assert p.service_region is None

    def test_constructs_owner(self):
        p = Persona(role="owner", role_arn="arn:aws:iam::1:role/o")
        assert p.role == "owner"
        assert p.service_region is None

    def test_constructs_technician_lead_with_service_region(self):
        p = Persona(
            role="technician_lead",
            role_arn="arn:aws:iam::1:role/tl",
            service_region="tempe-mesa",
        )
        assert p.role == "technician_lead"
        assert p.service_region == "tempe-mesa"

    def test_rejects_unknown_role(self):
        with pytest.raises(ValueError, match="role must be one of"):
            Persona(role="hacker", role_arn="arn:aws:iam::1:role/x")

    def test_technician_lead_requires_service_region(self):
        with pytest.raises(ValueError, match="service_region"):
            Persona(role="technician_lead", role_arn="arn:aws:iam::1:role/tl")

    def test_technician_lead_rejects_empty_service_region(self):
        with pytest.raises(ValueError, match="service_region"):
            Persona(role="technician_lead",
                    role_arn="arn:aws:iam::1:role/tl",
                    service_region="")

    def test_dispatcher_rejects_service_region(self):
        with pytest.raises(ValueError, match="service_region only applies"):
            Persona(role="dispatcher",
                    role_arn="arn:aws:iam::1:role/d",
                    service_region="tempe-mesa")

    def test_owner_rejects_service_region(self):
        with pytest.raises(ValueError, match="service_region only applies"):
            Persona(role="owner",
                    role_arn="arn:aws:iam::1:role/o",
                    service_region="tempe-mesa")

    def test_persona_is_frozen(self):
        p = Persona(role="owner", role_arn="arn:aws:iam::1:role/o")
        with pytest.raises(dataclasses_FrozenInstanceError()):
            p.role = "dispatcher"  # type: ignore[misc]

    def test_valid_roles_constant(self):
        assert set(VALID_ROLES) == {"dispatcher", "technician_lead", "owner"}


def dataclasses_FrozenInstanceError():
    import dataclasses
    return dataclasses.FrozenInstanceError


# ---- FlagPersonaResolver ----

class TestFlagPersonaResolver:
    def _arns(self) -> dict[str, str]:
        return {
            "dispatcher": "arn:aws:iam::1:role/gagent-dispatcher-test",
            "technician_lead": "arn:aws:iam::1:role/gagent-technician-lead-test",
            "owner": "arn:aws:iam::1:role/gagent-owner-test",
        }

    def test_satisfies_resolver_protocol(self):
        resolver = FlagPersonaResolver(self._arns())
        assert isinstance(resolver, PersonaResolver)

    def test_resolves_dispatcher(self):
        resolver = FlagPersonaResolver(self._arns())
        p = resolver.resolve("dispatcher")
        assert p.role == "dispatcher"
        assert p.role_arn == "arn:aws:iam::1:role/gagent-dispatcher-test"
        assert p.service_region is None

    def test_resolves_owner(self):
        resolver = FlagPersonaResolver(self._arns())
        p = resolver.resolve("owner")
        assert p.role == "owner"
        assert p.role_arn == "arn:aws:iam::1:role/gagent-owner-test"

    def test_resolves_technician_lead_with_service_region(self):
        resolver = FlagPersonaResolver(self._arns())
        p = resolver.resolve("technician_lead", service_region="tempe-mesa")
        assert p.role == "technician_lead"
        assert p.service_region == "tempe-mesa"

    def test_unknown_role_raises_keyerror(self):
        resolver = FlagPersonaResolver(self._arns())
        with pytest.raises(KeyError, match="hacker"):
            resolver.resolve("hacker")

    def test_constructor_rejects_unknown_role(self):
        with pytest.raises(ValueError, match="unknown role"):
            FlagPersonaResolver({"hacker": "arn:aws:iam::1:role/x"})

    def test_partial_arn_map_supported(self):
        resolver = FlagPersonaResolver({"owner": "arn:aws:iam::1:role/o"})
        assert resolver.resolve("owner").role == "owner"
        with pytest.raises(KeyError):
            resolver.resolve("dispatcher")

    def test_known_roles_returns_sorted(self):
        resolver = FlagPersonaResolver(self._arns())
        assert resolver.known_roles() == ["dispatcher", "owner", "technician_lead"]

    def test_resolver_propagates_persona_validation(self):
        resolver = FlagPersonaResolver(self._arns())
        with pytest.raises(ValueError, match="service_region"):
            resolver.resolve("technician_lead")  # missing service_region

    def test_resolver_isolates_role_arn_map(self):
        """Construction copies the arns map; mutating the source has no effect."""
        arns = self._arns()
        resolver = FlagPersonaResolver(arns)
        arns["dispatcher"] = "arn:aws:iam::999:role/evil"
        p = resolver.resolve("dispatcher")
        assert p.role_arn == "arn:aws:iam::1:role/gagent-dispatcher-test"


# ---- SsoPersonaResolver (Shape B) ----

import logging

from unittest.mock import MagicMock

from gagent_client.identity import (
    SsoIdentityError,
    SsoMappingError,
    _parse_sso_arn,
)


class TestParseSsoArn:
    def test_parses_standard_iic_arn(self):
        arn = (
            "arn:aws:sts::123456789012:assumed-role/"
            "AWSReservedSSO_DataAnalyst_abc1234567890def/alice@example.com"
        )
        parsed = _parse_sso_arn(arn)
        assert parsed == {
            "account_id": "123456789012",
            "permission_set": "DataAnalyst",
            "sso_user_id": "alice@example.com",
        }

    def test_handles_underscored_permission_set(self):
        arn = (
            "arn:aws:sts::1:assumed-role/"
            "AWSReservedSSO_Read_Write_Admin_xyz9876543210abc/bob@example.com"
        )
        parsed = _parse_sso_arn(arn)
        assert parsed is not None
        assert parsed["permission_set"] == "Read_Write_Admin"
        assert parsed["sso_user_id"] == "bob@example.com"

    def test_rejects_long_term_iam_user(self):
        assert _parse_sso_arn("arn:aws:iam::1:user/longterm") is None

    def test_rejects_non_sso_assumed_role(self):
        assert _parse_sso_arn(
            "arn:aws:sts::1:assumed-role/SomeRandomRole/session",
        ) is None


def _stub_sts(arn: str) -> MagicMock:
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Arn": arn, "Account": "1"}
    return sts


def _arns() -> dict[str, str]:
    return {
        "dispatcher": "arn:aws:iam::1:role/d",
        "technician_lead": "arn:aws:iam::1:role/tl",
        "owner": "arn:aws:iam::1:role/o",
    }


def _sso_arn(permission_set: str, user: str = "alice@example.com") -> str:
    return (
        f"arn:aws:sts::123456789012:assumed-role/"
        f"AWSReservedSSO_{permission_set}_abc123def4567890/{user}"
    )


class TestSsoPersonaResolverConstruction:
    def test_resolves_dispatcher_via_permission_set_match(self):
        resolver = SsoPersonaResolver(
            _arns(),
            mapping={
                "version": 1,
                "rules": [
                    {"match": {"permission_set": "DataReader"}, "persona": "dispatcher"},
                ],
            },
            sts_client=_stub_sts(_sso_arn("DataReader")),
        )
        assert resolver.resolved_persona.role == "dispatcher"
        assert resolver.resolved_persona.role_arn == "arn:aws:iam::1:role/d"
        assert resolver.resolved_persona.service_region is None
        assert resolver.sso_identity["permission_set"] == "DataReader"
        assert resolver.sso_identity["sso_user_id"] == "alice@example.com"

    def test_resolves_technician_lead_with_service_region(self):
        resolver = SsoPersonaResolver(
            _arns(),
            mapping={
                "version": 1,
                "rules": [{
                    "match": {"permission_set": "TechLead"},
                    "persona": "technician_lead",
                    "service_region": "tempe-mesa",
                }],
            },
            sts_client=_stub_sts(_sso_arn("TechLead")),
        )
        assert resolver.resolved_persona.role == "technician_lead"
        assert resolver.resolved_persona.service_region == "tempe-mesa"

    def test_user_specific_rule_beats_permission_set_rule(self):
        resolver = SsoPersonaResolver(
            _arns(),
            mapping={
                "version": 1,
                "rules": [
                    {"match": {"sso_user_id": "alice@example.com"}, "persona": "owner"},
                    {"match": {"permission_set": "DataReader"}, "persona": "dispatcher"},
                ],
            },
            sts_client=_stub_sts(_sso_arn("DataReader", "alice@example.com")),
        )
        assert resolver.resolved_persona.role == "owner"

    def test_default_persona_fallback_when_no_rules_match(self):
        resolver = SsoPersonaResolver(
            _arns(),
            mapping={
                "version": 1,
                "default_persona": "dispatcher",
                "rules": [
                    {"match": {"permission_set": "Unrelated"}, "persona": "owner"},
                ],
            },
            sts_client=_stub_sts(_sso_arn("Mystery")),
        )
        assert resolver.resolved_persona.role == "dispatcher"

    def test_no_match_no_default_raises(self):
        with pytest.raises(SsoMappingError, match="No persona mapping"):
            SsoPersonaResolver(
                _arns(),
                mapping={"version": 1, "rules": []},
                sts_client=_stub_sts(_sso_arn("Mystery")),
            )

    def test_long_term_iam_user_raises(self):
        sts = MagicMock()
        sts.get_caller_identity.return_value = {
            "Arn": "arn:aws:iam::1:user/longterm",
        }
        with pytest.raises(SsoIdentityError, match="not an IAM Identity Center"):
            SsoPersonaResolver(
                _arns(),
                mapping={"version": 1, "rules": []},
                sts_client=sts,
            )

    def test_sts_failure_raises_sso_identity_error(self):
        sts = MagicMock()
        sts.get_caller_identity.side_effect = RuntimeError("network down")
        with pytest.raises(SsoIdentityError, match="GetCallerIdentity failed"):
            SsoPersonaResolver(
                _arns(),
                mapping={"version": 1, "rules": []},
                sts_client=sts,
            )

    def test_rule_referencing_unknown_persona_raises(self):
        with pytest.raises(SsoMappingError, match="unknown persona"):
            SsoPersonaResolver(
                _arns(),
                mapping={
                    "version": 1,
                    "rules": [{"match": {"permission_set": "X"}, "persona": "hacker"}],
                },
                sts_client=_stub_sts(_sso_arn("X")),
            )

    def test_technician_lead_rule_without_service_region_raises(self):
        with pytest.raises(SsoMappingError, match="service_region"):
            SsoPersonaResolver(
                _arns(),
                mapping={
                    "version": 1,
                    "rules": [{
                        "match": {"permission_set": "X"},
                        "persona": "technician_lead",
                    }],
                },
                sts_client=_stub_sts(_sso_arn("X")),
            )


class TestSsoPersonaResolverResolve:
    """resolve() is the no-op-with-WARN path for Shape B overrides."""

    def _resolver(self) -> SsoPersonaResolver:
        return SsoPersonaResolver(
            _arns(),
            mapping={
                "version": 1,
                "rules": [{
                    "match": {"permission_set": "DataReader"},
                    "persona": "dispatcher",
                }],
            },
            sts_client=_stub_sts(_sso_arn("DataReader")),
        )

    def test_resolve_returns_sso_persona_when_no_args(self):
        r = self._resolver()
        p = r.resolve(role="dispatcher")
        assert p.role == "dispatcher"

    def test_resolve_ignores_role_override_and_warns(self, caplog):
        r = self._resolver()
        with caplog.at_level(logging.WARNING, logger="gagent_client.identity"):
            p = r.resolve(role="owner")
        assert p.role == "dispatcher"  # SSO persona, not the requested override
        assert any(
            "ignoring role='owner' override" in m.message
            for m in caplog.records
        )

    def test_resolve_ignores_service_region_override_and_warns(self, caplog):
        r = self._resolver()
        with caplog.at_level(logging.WARNING, logger="gagent_client.identity"):
            r.resolve(role="dispatcher", service_region="north-phoenix")
        assert any(
            "ignoring service_region" in m.message for m in caplog.records
        )

    def test_resolve_warns_only_once_per_distinct_override(self, caplog):
        r = self._resolver()
        with caplog.at_level(logging.WARNING, logger="gagent_client.identity"):
            r.resolve(role="owner")
            r.resolve(role="owner")
            r.resolve(role="owner")
        warns = [m for m in caplog.records if "ignoring role='owner'" in m.message]
        assert len(warns) == 1

    def test_known_roles_returns_only_resolved_role(self):
        r = self._resolver()
        assert r.known_roles() == ["dispatcher"]


class TestSsoMappingFile:
    def test_loads_default_mapping_file(self):
        # The shipped persona_mapping.json should be valid JSON with the
        # expected schema, even though no rule will match a test SSO arn.
        from gagent_client.identity import (
            DEFAULT_MAPPING_FILE, _load_mapping_file,
        )
        mapping = _load_mapping_file(DEFAULT_MAPPING_FILE)
        assert "rules" in mapping
        assert isinstance(mapping["rules"], list)
        for rule in mapping["rules"]:
            if "match" not in rule or rule["match"] is None:
                continue
            assert isinstance(rule["match"], dict)
            assert rule.get("persona") in (
                "dispatcher", "technician_lead", "owner",
            )

    def test_missing_mapping_file_raises(self, tmp_path):
        from gagent_client.identity import _load_mapping_file

        with pytest.raises(SsoMappingError, match="not found"):
            _load_mapping_file(tmp_path / "nope.json")

    def test_invalid_json_raises(self, tmp_path):
        from gagent_client.identity import _load_mapping_file

        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json")
        with pytest.raises(SsoMappingError, match="not valid JSON"):
            _load_mapping_file(bad)

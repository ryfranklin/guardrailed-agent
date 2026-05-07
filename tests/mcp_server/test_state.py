"""Tests for mcp_server.state — trust gate, token counter, config load.

Pure-logic only. No subprocess, no AWS.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from mcp_server import (
    SHAPE_A,
    SHAPE_B,
    ServerConfig,
    ServerStartupError,
    ServerState,
    TokenCounter,
    TrustGateError,
    determine_shape,
    enforce_trust_gate,
    load_config,
)


# ---- trust gate ----

class TestTrustGate:
    def test_raises_when_env_missing(self, caplog):
        with caplog.at_level(logging.ERROR):
            with pytest.raises(TrustGateError):
                enforce_trust_gate({})
        assert any("GAGENT_TRUSTED_OPERATOR" in r.message for r in caplog.records)

    def test_raises_when_env_zero(self):
        with pytest.raises(TrustGateError):
            enforce_trust_gate({"GAGENT_TRUSTED_OPERATOR": "0"})

    def test_raises_when_env_other_value(self):
        with pytest.raises(TrustGateError):
            enforce_trust_gate({"GAGENT_TRUSTED_OPERATOR": "true"})

    def test_passes_when_env_one(self):
        # No exception expected.
        enforce_trust_gate({"GAGENT_TRUSTED_OPERATOR": "1"})

    def test_exit_code_is_one(self):
        with pytest.raises(SystemExit) as exc_info:
            enforce_trust_gate({})
        assert exc_info.value.code == 1


# ---- token counter ----

class TestTokenCounter:
    def test_default_state(self):
        c = TokenCounter(budget=1000)
        assert c.budget == 1000
        assert c.used == 0
        assert c.warnings_emitted == 0

    def test_rejects_non_positive_budget(self):
        with pytest.raises(ValueError):
            TokenCounter(budget=0)
        with pytest.raises(ValueError):
            TokenCounter(budget=-1)

    def test_chars_over_4_heuristic(self):
        c = TokenCounter(budget=1000)
        added = c.add(input_text="abcd", output_text="efgh")  # 8 chars / 4 = 2
        assert added == 2
        assert c.used == 2

    def test_no_warning_below_threshold(self, caplog):
        c = TokenCounter(budget=100)
        with caplog.at_level(logging.WARNING):
            c.add(input_text="x" * 200, output_text="y" * 100)  # 75 tokens
        assert c.used == 75
        assert c.warnings_emitted == 0
        assert not any("budget exceeded" in r.message for r in caplog.records)

    def test_emits_warning_when_threshold_crossed(self, caplog):
        c = TokenCounter(budget=100)
        with caplog.at_level(logging.WARNING):
            c.add(input_text="x" * 240, output_text="y" * 240)  # 120 tokens
        assert c.used == 120
        assert c.warnings_emitted == 1
        assert sum(1 for r in caplog.records if "budget exceeded" in r.message) == 1

    def test_warning_fires_again_at_2x_breach(self, caplog):
        c = TokenCounter(budget=100)
        with caplog.at_level(logging.WARNING):
            c.add(input_text="x" * 240, output_text="y" * 240)  # 120 → 1x
            c.add(input_text="x" * 240, output_text="y" * 240)  # 240 → 2x
        assert c.warnings_emitted == 2

    def test_no_duplicate_warnings_within_same_band(self, caplog):
        c = TokenCounter(budget=100)
        with caplog.at_level(logging.WARNING):
            c.add(input_text="x" * 240, output_text="y" * 240)  # 120 → 1x
            c.add(input_text="x" * 4, output_text="y" * 4)      # 122 → still 1x
        assert c.warnings_emitted == 1


# ---- config loader ----

class TestLoadConfig:
    def test_uses_env_when_no_terraform(self, monkeypatch):
        monkeypatch.setenv("GAGENT_TF_DIR", "/nonexistent/path")
        cfg = load_config({
            "GAGENT_TRUSTED_OPERATOR": "1",  # Shape A
            "GAGENT_TF_DIR": "/nonexistent/path",
            "GAGENT_DISPATCHER_ROLE_ARN": "arn:aws:iam::1:role/d",
            "GAGENT_OWNER_ROLE_ARN": "arn:aws:iam::1:role/o",
            "GAGENT_AGENT_ID": "AGENT123",
            "GAGENT_AGENT_ALIAS_ID": "ALIAS456",
            "AWS_REGION": "us-west-2",
            "GAGENT_DEFAULT_PERSONA": "dispatcher",
            "GAGENT_TOKEN_BUDGET": "5000",
        })
        assert cfg.region == "us-west-2"
        assert cfg.agent_id == "AGENT123"
        assert cfg.agent_alias_id == "ALIAS456"
        assert cfg.default_persona == "dispatcher"
        assert cfg.token_budget == 5000
        assert cfg.resolver is not None
        assert set(cfg.resolver.known_roles()) == {"dispatcher", "owner"}

    def test_resolver_is_none_with_no_arns(self):
        cfg = load_config({
            "GAGENT_TRUSTED_OPERATOR": "1",  # Shape A is forgiving here
            "GAGENT_TF_DIR": "/nonexistent/path",
        })
        assert cfg.resolver is None
        assert cfg.shape == "A"

    def test_default_token_budget(self):
        cfg = load_config({
            "GAGENT_TRUSTED_OPERATOR": "1",
            "GAGENT_TF_DIR": "/nonexistent/path",
        })
        assert cfg.token_budget == 25_000

    def test_default_persona_is_owner(self):
        cfg = load_config({
            "GAGENT_TRUSTED_OPERATOR": "1",
            "GAGENT_TF_DIR": "/nonexistent/path",
        })
        assert cfg.default_persona == "owner"


# ---- server state ----

# ---- determine_shape ----

class TestDetermineShape:
    def test_shape_a_when_trusted_operator_set(self):
        assert determine_shape({"GAGENT_TRUSTED_OPERATOR": "1"}) == SHAPE_A

    def test_shape_b_by_default(self):
        assert determine_shape({}) == SHAPE_B

    def test_shape_b_when_value_is_zero(self):
        assert determine_shape({"GAGENT_TRUSTED_OPERATOR": "0"}) == SHAPE_B

    def test_shape_b_when_value_is_truthy_but_not_one(self):
        assert determine_shape({"GAGENT_TRUSTED_OPERATOR": "true"}) == SHAPE_B


# ---- load_config Shape B path ----

class TestLoadConfigShapeB:
    def _sso_arn(self) -> str:
        return (
            "arn:aws:sts::123:assumed-role/"
            "AWSReservedSSO_DataReader_abc1234567890def/alice@example.com"
        )

    def _shape_b_env(self, **extra) -> dict[str, str]:
        return {
            "GAGENT_TF_DIR": "/nonexistent/path",
            "GAGENT_DISPATCHER_ROLE_ARN": "arn:aws:iam::1:role/d",
            "GAGENT_TECHNICIAN_LEAD_ROLE_ARN": "arn:aws:iam::1:role/tl",
            "GAGENT_OWNER_ROLE_ARN": "arn:aws:iam::1:role/o",
            **extra,
        }

    def test_shape_b_with_sso_resolver_succeeds(self, tmp_path):
        # Custom mapping file with a permission_set match.
        mapping_file = tmp_path / "mapping.json"
        mapping_file.write_text(
            '{"version":1,"rules":[{"match":{"permission_set":"DataReader"},'
            '"persona":"dispatcher"}]}',
        )
        sts = MagicMock()
        sts.get_caller_identity.return_value = {"Arn": self._sso_arn()}

        with patch("boto3.client", return_value=sts):
            cfg = load_config(self._shape_b_env(
                GAGENT_PERSONA_MAPPING_FILE=str(mapping_file),
            ))

        assert cfg.shape == SHAPE_B
        assert cfg.resolver is not None
        assert cfg.default_persona == "dispatcher"  # Bound to SSO identity

    def test_shape_b_without_sso_identity_raises_startup_error(self):
        sts = MagicMock()
        sts.get_caller_identity.return_value = {
            "Arn": "arn:aws:iam::1:user/longterm",
        }
        with patch("boto3.client", return_value=sts):
            with pytest.raises(ServerStartupError, match="Shape B"):
                load_config(self._shape_b_env(
                    GAGENT_PERSONA_MAPPING_FILE="/dev/null",
                ))

    def test_shape_b_without_role_arns_raises_startup_error(self):
        with pytest.raises(ServerStartupError, match="role ARNs"):
            load_config({"GAGENT_TF_DIR": "/nonexistent/path"})

    def test_shape_b_default_persona_overridden_by_sso(self, tmp_path):
        mapping_file = tmp_path / "mapping.json"
        mapping_file.write_text(
            '{"version":1,"rules":[{"match":{"permission_set":"FieldOps"},'
            '"persona":"technician_lead","service_region":"west-valley"}]}',
        )
        sts = MagicMock()
        sts.get_caller_identity.return_value = {
            "Arn": (
                "arn:aws:sts::1:assumed-role/"
                "AWSReservedSSO_FieldOps_abc1234567890def/bob@example.com"
            ),
        }
        with patch("boto3.client", return_value=sts):
            cfg = load_config(self._shape_b_env(
                GAGENT_PERSONA_MAPPING_FILE=str(mapping_file),
                GAGENT_DEFAULT_PERSONA="dispatcher",  # Should be overridden
                GAGENT_DEFAULT_SERVICE_REGION="north-phoenix",  # Overridden too
            ))
        assert cfg.shape == SHAPE_B
        assert cfg.default_persona == "technician_lead"
        assert cfg.default_service_region == "west-valley"


class TestServerState:
    def _config(self) -> ServerConfig:
        return ServerConfig(
            resolver=None,
            agent_id=None,
            agent_alias_id=None,
            region="us-east-1",
            glue_database=None,
            default_persona="owner",
            default_service_region=None,
            log_group="/gagent/invocations",
            token_budget=1000,
        )

    def test_state_inits_token_counter_from_config(self):
        state = ServerState(config=self._config())
        assert state.tokens.budget == 1000
        assert state.tokens.used == 0


class TestLoadConfigLogGroup:
    def test_default_log_group(self):
        cfg = load_config({
            "GAGENT_TRUSTED_OPERATOR": "1",
            "GAGENT_TF_DIR": "/nonexistent/path",
        })
        assert cfg.log_group == "/gagent/invocations"

    def test_env_overrides_log_group(self):
        cfg = load_config({
            "GAGENT_TRUSTED_OPERATOR": "1",
            "GAGENT_TF_DIR": "/nonexistent/path",
            "GAGENT_LOG_GROUP": "/custom/log/group",
        })
        assert cfg.log_group == "/custom/log/group"

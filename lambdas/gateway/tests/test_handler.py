"""Unit-style tests for the gateway Lambda (phase-3a-brief §9).

Pure-logic only — body parsing, persona resolution, surface header,
error mapping. AWS-touching paths (gagent_client.invoke -> AssumeRole ->
InvokeAgent) are mocked.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

REQUIRED_ENV = {
    "GAGENT_AGENT_ID": "AGT-TEST",
    "GAGENT_AGENT_ALIAS_ID": "ALIAS-TEST",
    "AWS_REGION": "us-east-1",
    "GAGENT_DISPATCHER_ROLE_ARN": "arn:aws:iam::1:role/d",
    "GAGENT_TECHNICIAN_LEAD_ROLE_ARN": "arn:aws:iam::1:role/tl",
    "GAGENT_OWNER_ROLE_ARN": "arn:aws:iam::1:role/o",
    "GAGENT_LOG_GROUP": "/gagent/invocations",
    "GAGENT_GOVERNED_QUERY_LAMBDA_NAME": "gagent-governed-query-test",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GAGENT_GATEWAY_PERSONA_RESOLUTION", raising=False)
    monkeypatch.delenv("GAGENT_DEFAULT_SERVICE_REGION", raising=False)
    monkeypatch.delenv("GAGENT_GATEWAY_ALLOWED_ORIGINS", raising=False)


@pytest.fixture
def handler_module():
    # Import inside the fixture so env-var-dependent module state is
    # re-evaluated per-test if needed.
    from lambdas.gateway import handler as mod
    return mod


def _event(
    *,
    body: dict | str | None,
    claims: dict | None = None,
    headers: dict[str, str] | None = None,
    route_key: str = "POST /ask",
) -> dict[str, Any]:
    request_context: dict[str, Any] = {}
    if claims is not None:
        request_context["authorizer"] = {"jwt": {"claims": claims, "scopes": []}}
    if isinstance(body, dict) or body is None:
        body_str = json.dumps(body) if isinstance(body, dict) else body
    else:
        body_str = body
    return {
        "version": "2.0",
        "routeKey": route_key,
        "headers": headers or {},
        "requestContext": request_context,
        "body": body_str,
    }


def _claims(persona: str | None = None) -> dict[str, str]:
    base = {"sub": "user-123", "email": "alice@example.com"}
    if persona is not None:
        base["custom:persona"] = persona
    return base


def _fake_invocation_response(role: str):
    """Stand-in for gagent_client.InvocationResponse with the fields the handler reads."""
    from gagent_client import TraceSummary

    summary = TraceSummary(
        tools_called=["/customers"],
        guardrail_blocks=0,
    )

    class _Resp:
        text = f"hello from {role}"
        trace_summary = summary
        duration_seconds = 1.234
        session_id = f"gagent-{role}-abc123"

    return _Resp()


# ---- Happy path: each persona, surface=web (default) ----


class TestHappyPath:
    @pytest.mark.parametrize("persona", ["dispatcher", "owner"])
    def test_calls_invoke_and_shapes_response(
        self, handler_module, persona: str,
    ):
        event = _event(
            body={"question": f"hi from {persona}", "persona": persona},
            claims=_claims(),
        )
        with patch.object(
            handler_module, "invoke",
            return_value=_fake_invocation_response(persona),
        ) as mock_invoke:
            response = handler_module.handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["text"] == f"hello from {persona}"
        assert body["persona"] == persona
        assert body["service_region"] is None
        assert body["tools_called"] == ["/customers"]
        assert body["guardrail_blocks"] == 0
        assert body["session_id"] == f"gagent-{persona}-abc123"
        assert body["duration_seconds"] == 1.234

        mock_invoke.assert_called_once()
        kwargs = mock_invoke.call_args.kwargs
        assert kwargs["surface"] == "web"
        assert kwargs["agent_id"] == "AGT-TEST"
        assert kwargs["agent_alias_id"] == "ALIAS-TEST"
        assert kwargs["region"] == "us-east-1"
        assert kwargs["log_group"] == "/gagent/invocations"
        # The first positional arg is the question
        assert mock_invoke.call_args.args[0] == f"hi from {persona}"
        persona_arg = mock_invoke.call_args.args[1]
        assert persona_arg.role == persona

    def test_technician_lead_with_service_region(self, handler_module):
        event = _event(
            body={
                "question": "tempe jobs",
                "persona": "technician_lead",
                "service_region": "tempe-mesa",
            },
            claims=_claims(),
        )
        with patch.object(
            handler_module, "invoke",
            return_value=_fake_invocation_response("technician_lead"),
        ) as mock_invoke:
            response = handler_module.handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["persona"] == "technician_lead"
        persona_arg = mock_invoke.call_args.args[1]
        assert persona_arg.role == "technician_lead"
        assert persona_arg.service_region == "tempe-mesa"

    def test_default_response_headers(self, handler_module):
        event = _event(
            body={"question": "hi", "persona": "owner"},
            claims=_claims(),
        )
        with patch.object(
            handler_module, "invoke",
            return_value=_fake_invocation_response("owner"),
        ):
            response = handler_module.handler(event, None)

        assert response["headers"]["Content-Type"] == "application/json"
        assert response["headers"]["Vary"] == "Origin"
        assert "Access-Control-Allow-Origin" not in response["headers"]


# ---- Surface header allowlist ----


class TestSurfaceHeader:
    def test_surface_slack_via_header(self, handler_module):
        event = _event(
            body={"question": "hi", "persona": "owner"},
            claims=_claims(),
            headers={"X-Gagent-Surface": "slack"},
        )
        with patch.object(
            handler_module, "invoke",
            return_value=_fake_invocation_response("owner"),
        ) as mock_invoke:
            response = handler_module.handler(event, None)

        assert response["statusCode"] == 200
        assert mock_invoke.call_args.kwargs["surface"] == "slack"

    def test_surface_header_lowercase_match(self, handler_module):
        event = _event(
            body={"question": "hi", "persona": "owner"},
            claims=_claims(),
            headers={"x-gagent-surface": "WEB"},
        )
        with patch.object(
            handler_module, "invoke",
            return_value=_fake_invocation_response("owner"),
        ) as mock_invoke:
            response = handler_module.handler(event, None)

        assert response["statusCode"] == 200
        assert mock_invoke.call_args.kwargs["surface"] == "web"

    def test_surface_header_unknown_returns_400(self, handler_module):
        event = _event(
            body={"question": "hi", "persona": "owner"},
            claims=_claims(),
            headers={"X-Gagent-Surface": "telegram"},
        )
        with patch.object(handler_module, "invoke") as mock_invoke:
            response = handler_module.handler(event, None)

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["error"] == "invalid_surface"
        mock_invoke.assert_not_called()


# ---- Body validation ----


class TestBadRequests:
    def test_missing_body_returns_400(self, handler_module):
        event = _event(body=None, claims=_claims())
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["error"] == "invalid_body"

    def test_empty_body_returns_400(self, handler_module):
        event = _event(body="", claims=_claims())
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400

    def test_non_json_body_returns_400(self, handler_module):
        event = _event(body="not-json", claims=_claims())
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["error"] == "invalid_body"

    def test_array_body_returns_400(self, handler_module):
        event = _event(body="[1,2,3]", claims=_claims())
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400

    def test_missing_question_returns_400(self, handler_module):
        event = _event(body={"persona": "owner"}, claims=_claims())
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400

    def test_blank_question_returns_400(self, handler_module):
        event = _event(
            body={"question": "   ", "persona": "owner"},
            claims=_claims(),
        )
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400

    def test_persona_wrong_type_returns_400(self, handler_module):
        event = _event(
            body={"question": "hi", "persona": 5},
            claims=_claims(),
        )
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400

    def test_service_region_wrong_type_returns_400(self, handler_module):
        event = _event(
            body={
                "question": "hi", "persona": "technician_lead",
                "service_region": 123,
            },
            claims=_claims(),
        )
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400

    def test_request_param_missing_persona_returns_400(self, handler_module):
        event = _event(body={"question": "hi"}, claims=_claims())
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["error"] == "invalid_persona"

    def test_request_param_invalid_persona_returns_400(self, handler_module):
        event = _event(
            body={"question": "hi", "persona": "superuser"},
            claims=_claims(),
        )
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["error"] == "invalid_persona"

    def test_technician_lead_missing_service_region_returns_400(self, handler_module):
        event = _event(
            body={"question": "hi", "persona": "technician_lead"},
            claims=_claims(),
        )
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["error"] == "invalid_persona"


# ---- Auth (defensive — authorizer rejects upstream) ----


class TestAuth:
    def test_missing_jwt_claims_returns_401(self, handler_module):
        event = _event(
            body={"question": "hi", "persona": "owner"},
            claims=None,
        )
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 401
        body = json.loads(response["body"])
        assert body["error"] == "unauthorized"


# ---- Claim-bound mode (Shape B) ----


class TestClaimBoundMode:
    @pytest.fixture(autouse=True)
    def _claim_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAGENT_GATEWAY_PERSONA_RESOLUTION", "claim-bound")

    def test_persona_mismatch_returns_403(self, handler_module):
        event = _event(
            body={"question": "hi", "persona": "owner"},
            claims=_claims("dispatcher"),
        )
        with patch.object(handler_module, "invoke") as mock_invoke:
            response = handler_module.handler(event, None)

        assert response["statusCode"] == 403
        body = json.loads(response["body"])
        assert body["error"] == "persona_mismatch"
        mock_invoke.assert_not_called()

    def test_missing_claim_returns_400(self, handler_module):
        event = _event(
            body={"question": "hi"},
            claims=_claims(None),
        )
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["error"] == "invalid_persona"

    def test_claim_bound_happy_path(self, handler_module):
        event = _event(
            body={"question": "hi"},
            claims=_claims("owner"),
        )
        with patch.object(
            handler_module, "invoke",
            return_value=_fake_invocation_response("owner"),
        ) as mock_invoke:
            response = handler_module.handler(event, None)

        assert response["statusCode"] == 200
        persona_arg = mock_invoke.call_args.args[1]
        assert persona_arg.role == "owner"


# ---- Upstream errors ----


class TestUpstreamErrors:
    def _event(self) -> dict[str, Any]:
        return _event(
            body={"question": "hi", "persona": "owner"},
            claims=_claims(),
        )

    def test_throttle_returns_429(self, handler_module):
        err = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
            "InvokeAgent",
        )
        with patch.object(handler_module, "invoke", side_effect=err):
            response = handler_module.handler(self._event(), None)
        assert response["statusCode"] == 429
        body = json.loads(response["body"])
        assert body["error"] == "throttled"

    def test_too_many_requests_returns_429(self, handler_module):
        err = ClientError(
            {"Error": {"Code": "TooManyRequestsException", "Message": "x"}},
            "InvokeAgent",
        )
        with patch.object(handler_module, "invoke", side_effect=err):
            response = handler_module.handler(self._event(), None)
        assert response["statusCode"] == 429

    def test_botocore_read_timeout_returns_504(self, handler_module):
        err = ReadTimeoutError(endpoint_url="https://bedrock")
        with patch.object(handler_module, "invoke", side_effect=err):
            response = handler_module.handler(self._event(), None)
        assert response["statusCode"] == 504
        body = json.loads(response["body"])
        assert body["error"] == "upstream_timeout"

    def test_request_timeout_clienterror_returns_504(self, handler_module):
        err = ClientError(
            {"Error": {"Code": "RequestTimeout", "Message": "x"}},
            "InvokeAgent",
        )
        with patch.object(handler_module, "invoke", side_effect=err):
            response = handler_module.handler(self._event(), None)
        assert response["statusCode"] == 504

    def test_other_clienterror_returns_500(self, handler_module):
        err = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "x"}},
            "InvokeAgent",
        )
        with patch.object(handler_module, "invoke", side_effect=err):
            response = handler_module.handler(self._event(), None)
        assert response["statusCode"] == 500
        body = json.loads(response["body"])
        assert body["error"] == "upstream_error"

    def test_unexpected_exception_returns_500(self, handler_module):
        with patch.object(
            handler_module, "invoke", side_effect=RuntimeError("boom"),
        ):
            response = handler_module.handler(self._event(), None)
        assert response["statusCode"] == 500
        body = json.loads(response["body"])
        assert body["error"] == "internal_error"


# ---- CORS origin echo ----


class TestCors:
    def test_allowed_origin_echoed(self, handler_module):
        event = _event(
            body={"question": "hi", "persona": "owner"},
            claims=_claims(),
            headers={"Origin": "https://demo.ms3dm.tech"},
        )
        with patch.object(
            handler_module, "invoke",
            return_value=_fake_invocation_response("owner"),
        ):
            response = handler_module.handler(event, None)
        assert (
            response["headers"]["Access-Control-Allow-Origin"]
            == "https://demo.ms3dm.tech"
        )
        assert response["headers"]["Access-Control-Allow-Credentials"] == "true"

    def test_disallowed_origin_not_echoed(self, handler_module):
        event = _event(
            body={"question": "hi", "persona": "owner"},
            claims=_claims(),
            headers={"Origin": "https://evil.example"},
        )
        with patch.object(
            handler_module, "invoke",
            return_value=_fake_invocation_response("owner"),
        ):
            response = handler_module.handler(event, None)
        assert "Access-Control-Allow-Origin" not in response["headers"]

    def test_localhost_dev_origin_echoed_by_default(self, handler_module):
        event = _event(
            body={"question": "hi", "persona": "owner"},
            claims=_claims(),
            headers={"Origin": "http://localhost:5173"},
        )
        with patch.object(
            handler_module, "invoke",
            return_value=_fake_invocation_response("owner"),
        ):
            response = handler_module.handler(event, None)
        assert (
            response["headers"]["Access-Control-Allow-Origin"]
            == "http://localhost:5173"
        )

    def test_error_response_also_echoes_origin(self, handler_module):
        event = _event(
            body={"question": "hi"},  # missing persona triggers 400
            claims=_claims(),
            headers={"Origin": "https://demo.ms3dm.tech"},
        )
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400
        assert (
            response["headers"]["Access-Control-Allow-Origin"]
            == "https://demo.ms3dm.tech"
        )

    def test_custom_origin_via_env(
        self, handler_module, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv(
            "GAGENT_GATEWAY_ALLOWED_ORIGINS",
            "https://stage.ms3dm.tech, https://demo.ms3dm.tech",
        )
        event = _event(
            body={"question": "hi", "persona": "owner"},
            claims=_claims(),
            headers={"Origin": "https://stage.ms3dm.tech"},
        )
        with patch.object(
            handler_module, "invoke",
            return_value=_fake_invocation_response("owner"),
        ):
            response = handler_module.handler(event, None)
        assert (
            response["headers"]["Access-Control-Allow-Origin"]
            == "https://stage.ms3dm.tech"
        )


# ---- Smoke import ----


def test_import_handler_callable():
    from lambdas.gateway.handler import handler as h
    assert callable(h)


# ---- POST /preview ----


def _governed_query_response_payload(
    rows: list[dict[str, Any]],
    *,
    status: int = 200,
    template: str = "query_customers",
) -> dict[str, Any]:
    """Shape of the synchronous Lambda invoke response payload."""
    inner_body = json.dumps({
        "rows": rows,
        "row_count": len(rows),
        "template": template,
        "persona": "owner",
        "question_intent": "data preview",
    })
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": "governed_query",
            "apiPath": "/customers",
            "httpMethod": "POST",
            "httpStatusCode": status,
            "responseBody": {"application/json": {"body": inner_body}},
        },
        "sessionAttributes": {},
        "promptSessionAttributes": {},
    }


def _stub_lambda_client(payload: dict[str, Any]):
    """Build a fake boto3 lambda client that returns the given payload."""
    import io

    class _Resp:
        def __init__(self, data):
            self.data = data

        def read(self):
            return self.data

    fake_payload = io.BytesIO(json.dumps(payload).encode("utf-8"))

    class _FakeClient:
        last_invoke_args: dict[str, Any] | None = None

        def invoke(self, **kwargs):
            self.last_invoke_args = kwargs
            return {"StatusCode": 200, "Payload": _Resp(json.dumps(payload).encode("utf-8"))}

    _ = fake_payload  # silence unused
    return _FakeClient()


class TestPreviewHappyPath:
    def test_owner_preview_returns_rows(self, handler_module):
        rows = [
            {"customer_id": "c-1", "first_name": "Alice", "email": "a@example.com"},
            {"customer_id": "c-2", "first_name": "Bob", "email": "b@example.com"},
        ]
        fake = _stub_lambda_client(_governed_query_response_payload(rows))
        with patch.object(handler_module, "_get_lambda_client", return_value=fake):
            event = _event(
                route_key="POST /preview",
                body={"table": "customers", "persona": "owner", "limit": 5},
                claims=_claims(),
            )
            response = handler_module.handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["table"] == "customers"
        assert body["persona"] == "owner"
        assert body["row_count"] == 2
        assert body["rows"][0]["first_name"] == "Alice"

        # The synthesized invoke event should carry the persona role + the
        # right apiPath into governed_query.
        invoke_args = fake.last_invoke_args
        assert invoke_args is not None
        assert invoke_args["FunctionName"] == "gagent-governed-query-test"
        invoke_payload = json.loads(invoke_args["Payload"])
        assert invoke_payload["apiPath"] == "/customers"
        assert invoke_payload["sessionAttributes"]["role"] == "owner"

    def test_dispatcher_preview_passes_role_through(self, handler_module):
        rows = [{"customer_id": "c-1", "first_name": "REDACTED", "email": None}]
        fake = _stub_lambda_client(_governed_query_response_payload(rows))
        with patch.object(handler_module, "_get_lambda_client", return_value=fake):
            event = _event(
                route_key="POST /preview",
                body={"table": "customers", "persona": "dispatcher"},
                claims=_claims(),
            )
            response = handler_module.handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["persona"] == "dispatcher"
        assert body["rows"][0]["first_name"] == "REDACTED"

    def test_technician_lead_preview_threads_service_region(self, handler_module):
        rows: list[dict[str, Any]] = []
        fake = _stub_lambda_client(_governed_query_response_payload(rows))
        with patch.object(handler_module, "_get_lambda_client", return_value=fake):
            event = _event(
                route_key="POST /preview",
                body={
                    "table": "customers",
                    "persona": "technician_lead",
                    "service_region": "tempe-mesa",
                },
                claims=_claims(),
            )
            response = handler_module.handler(event, None)
        assert response["statusCode"] == 200
        invoke_payload = json.loads(fake.last_invoke_args["Payload"])
        assert invoke_payload["sessionAttributes"]["service_region"] == "tempe-mesa"


class TestPreviewBadRequests:
    def test_unknown_table_returns_400(self, handler_module):
        event = _event(
            route_key="POST /preview",
            body={"table": "secrets", "persona": "owner"},
            claims=_claims(),
        )
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["error"] == "invalid_table"

    def test_missing_persona_returns_400(self, handler_module):
        event = _event(
            route_key="POST /preview",
            body={"table": "customers"},
            claims=_claims(),
        )
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["error"] == "invalid_persona"

    def test_non_int_limit_returns_400(self, handler_module):
        event = _event(
            route_key="POST /preview",
            body={"table": "customers", "persona": "owner", "limit": "lots"},
            claims=_claims(),
        )
        response = handler_module.handler(event, None)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["error"] == "invalid_limit"

    def test_governed_query_returns_403_propagates(self, handler_module):
        payload = _governed_query_response_payload(rows=[], status=403)
        # The 403 is wrapped in the Bedrock action-group response; our handler
        # should surface it as a preview_failed BadRequest -> 400 with the
        # underlying error code in detail.
        fake = _stub_lambda_client(payload)
        with patch.object(handler_module, "_get_lambda_client", return_value=fake):
            event = _event(
                route_key="POST /preview",
                body={"table": "jobs", "persona": "dispatcher"},
                claims=_claims(),
            )
            response = handler_module.handler(event, None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["error"] == "preview_failed"

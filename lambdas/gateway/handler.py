"""API Gateway HTTP API gateway Lambda (ADR-007, ADR-010).

Front-door for the public web demo. Receives `POST /ask` requests already
authenticated by the API Gateway JWT authorizer (which validates the
Cognito ID token and decodes its claims into the event), resolves the
caller's persona, and invokes the Bedrock Agent through the shared
gagent_client pipeline.

Request envelope:
    { "question": str, "persona": str?, "service_region": str? }

Successful response envelope:
    { "text", "persona", "service_region", "tools_called",
      "guardrail_blocks", "duration_seconds", "session_id" }

Error response envelope:
    { "error": "<short_code>", "message": "<detail>" }

Persona resolution mode (Shape A vs Shape B) is fixed at deploy time via
the GAGENT_GATEWAY_PERSONA_RESOLUTION env var. The public demo runs
Shape A (request-param); future client deployments flip to Shape B.

Surface tagging defaults to "web". The X-Gagent-Surface header (allowlist:
{web, slack}) overrides for Phase 3.b's Slack adapter — it invokes this
Lambda directly via Lambda Invoke and tags surface="slack" so the same
CloudWatch log group can distinguish web vs Slack traffic.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import Any

from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    ReadTimeoutError,
)

from gagent_client import (
    CognitoPersonaResolver,
    invoke,
)
from gagent_client.identity import SsoMappingError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

ALLOWED_SURFACES: frozenset[str] = frozenset({"web", "slack"})
DEFAULT_SURFACE = "web"
SURFACE_HEADER = "x-gagent-surface"

DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "https://demo.ms3dm.tech",
    "http://localhost:5173",
)

THROTTLE_CODES: frozenset[str] = frozenset({
    "ThrottlingException",
    "Throttling",
    "TooManyRequestsException",
    "RequestLimitExceeded",
})

TIMEOUT_CODES: frozenset[str] = frozenset({
    "RequestTimeout",
    "RequestTimeoutException",
})


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required env var {name!r}")
    return value


def _build_role_arn_map() -> dict[str, str]:
    arns = {
        "dispatcher": os.environ.get("GAGENT_DISPATCHER_ROLE_ARN"),
        "technician_lead": os.environ.get("GAGENT_TECHNICIAN_LEAD_ROLE_ARN"),
        "owner": os.environ.get("GAGENT_OWNER_ROLE_ARN"),
    }
    return {k: v for k, v in arns.items() if v}


def _allowed_origins() -> list[str]:
    raw = os.environ.get("GAGENT_GATEWAY_ALLOWED_ORIGINS")
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _build_resolver() -> CognitoPersonaResolver:
    return CognitoPersonaResolver(_build_role_arn_map())


def _lower_headers(event: dict[str, Any]) -> dict[str, str]:
    headers = event.get("headers") or {}
    return {str(k).lower(): str(v) for k, v in headers.items() if v is not None}


def _origin_for_response(headers: dict[str, str]) -> str | None:
    origin = headers.get("origin")
    if not origin:
        return None
    return origin if origin in _allowed_origins() else None


def _claims(event: dict[str, Any]) -> dict[str, Any] | None:
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    jwt_block = authorizer.get("jwt") or {}
    claims = jwt_block.get("claims")
    if isinstance(claims, dict):
        return claims
    return None


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body")
    if raw is None or raw == "":
        raise _BadRequest("invalid_body", "request body is required.")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _BadRequest(
            "invalid_body", f"request body is not valid JSON: {exc.msg}",
        ) from exc
    if not isinstance(parsed, dict):
        raise _BadRequest("invalid_body", "request body must be a JSON object.")
    return parsed


def _validate_body(body: dict[str, Any]) -> tuple[str, str | None, str | None]:
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        raise _BadRequest("invalid_body", "'question' must be a non-empty string.")

    persona = body.get("persona")
    if persona is not None and not isinstance(persona, str):
        raise _BadRequest("invalid_body", "'persona' must be a string when provided.")

    service_region = body.get("service_region")
    if service_region is not None and not isinstance(service_region, str):
        raise _BadRequest(
            "invalid_body", "'service_region' must be a string when provided.",
        )

    return question, (persona or None), (service_region or None)


def _resolve_surface(headers: dict[str, str]) -> str:
    raw = headers.get(SURFACE_HEADER)
    if raw is None:
        return DEFAULT_SURFACE
    surface = raw.strip().lower()
    if surface not in ALLOWED_SURFACES:
        raise _BadRequest(
            "invalid_surface",
            f"X-Gagent-Surface must be one of {sorted(ALLOWED_SURFACES)}; "
            f"got {surface!r}.",
        )
    return surface


class _BadRequest(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    headers = _lower_headers(event)
    response_origin = _origin_for_response(headers)

    try:
        claims = _claims(event)
        if claims is None:
            return _error(401, "unauthorized", "missing JWT claims.", response_origin)

        body = _parse_body(event)
        question, requested_role, requested_service_region = _validate_body(body)
        surface = _resolve_surface(headers)

        resolver = _build_resolver()
        try:
            persona = resolver.resolve(
                claims=claims,
                requested_role=requested_role,
                requested_service_region=requested_service_region,
            )
        except PermissionError as exc:
            logger.info("persona claim mismatch: %s", exc)
            return _error(403, "persona_mismatch", str(exc), response_origin)
        except (ValueError, KeyError) as exc:
            logger.info("persona validation failed: %s", exc)
            return _error(400, "invalid_persona", str(exc), response_origin)
        except SsoMappingError as exc:
            logger.info("claim-bound persona resolution failed: %s", exc)
            return _error(400, "invalid_persona", str(exc), response_origin)

        try:
            response = invoke(
                question,
                persona,
                agent_id=_required_env("GAGENT_AGENT_ID"),
                agent_alias_id=_required_env("GAGENT_AGENT_ALIAS_ID"),
                region=_required_env("AWS_REGION"),
                surface=surface,
                trace_name=f"gateway-{surface}",
                log_group=os.environ.get("GAGENT_LOG_GROUP"),
            )
        except ClientError as exc:
            return _map_client_error(exc, response_origin)
        except (ReadTimeoutError, ConnectTimeoutError, socket.timeout) as exc:
            logger.warning("bedrock invoke timeout: %s", exc)
            return _error(
                504, "upstream_timeout",
                "Bedrock InvokeAgent timed out.", response_origin,
            )

        body = {
            "text": response.text,
            "persona": persona.role,
            "service_region": persona.service_region,
            "tools_called": list(response.trace_summary.tools_called),
            "guardrail_blocks": response.trace_summary.guardrail_blocks,
            "duration_seconds": round(response.duration_seconds, 3),
            "session_id": response.session_id,
        }
        return _ok(200, body, response_origin)

    except _BadRequest as exc:
        return _error(400, exc.code, exc.message, response_origin)
    except Exception as exc:  # noqa: BLE001
        logger.exception("unhandled gateway error: %s", exc)
        return _error(500, "internal_error", "Internal error.", response_origin)


def _map_client_error(exc: ClientError, response_origin: str | None) -> dict[str, Any]:
    code = exc.response.get("Error", {}).get("Code", "")
    if code in THROTTLE_CODES:
        logger.warning("bedrock throttle: %s", code)
        return _error(
            429, "throttled",
            "Bedrock throttled the request; retry shortly.", response_origin,
        )
    if code in TIMEOUT_CODES:
        logger.warning("bedrock timeout: %s", code)
        return _error(
            504, "upstream_timeout",
            "Bedrock InvokeAgent timed out.", response_origin,
        )
    logger.exception("bedrock client error code=%s", code)
    return _error(500, "upstream_error", "Upstream AWS error.", response_origin)


def _ok(
    status: int, body: dict[str, Any], response_origin: str | None,
) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": _headers(response_origin),
        "body": json.dumps(body, default=str),
    }


def _error(
    status: int, code: str, message: str, response_origin: str | None,
) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": _headers(response_origin),
        "body": json.dumps({"error": code, "message": message}),
    }


def _headers(response_origin: str | None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Vary": "Origin",
    }
    if response_origin:
        headers["Access-Control-Allow-Origin"] = response_origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return headers

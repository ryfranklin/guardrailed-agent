#!/usr/bin/env bash
# Phase 3.a public-web-demo smoke test.
#
# Hits POST /ask under the same prompt with each persona and prints the
# response envelope. Pairs with §6 acceptance criterion 12 from the
# Phase 3.a brief and §14 of the same.
#
# Pass criteria (visual inspection of output):
#   - dispatcher response: PII fields are NULL or contain "REDACTED".
#   - technician_lead:     real-looking PII (region-scoped row filter
#                          is Phase 2 backlog; v1 returns full PII).
#   - owner:               real-looking PII AND sensitivity-tagged
#                          columns (revenue, costs) are populated.
#   - all three:           appear in /gagent/invocations with surface=web.
#
# Prereqs:
#   - terraform apply succeeded for terraform/envs/demo/.
#   - SMOKE_TEST_JWT is exported with a valid Cognito ID token from a
#     signed-in user. To get one:
#       1. Sign in at https://demo.ms3dm.tech.
#       2. Open dev tools → Application → Storage → IndexedDB or
#          localStorage. Find the Amplify-prefixed idToken value.
#       3. export SMOKE_TEST_JWT='<paste>'
#     Or use Cognito's AdminInitiateAuth for a service test user once
#     one is provisioned.
#
# Usage:
#   ./scripts/smoke-web.sh
#   ./scripts/smoke-web.sh "What signals did you see for customer X?"
#   API_ENDPOINT=https://override.example ./scripts/smoke-web.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v jq >/dev/null 2>&1; then
  echo "error: 'jq' is required. brew install jq" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "error: 'curl' is required." >&2
  exit 1
fi

API_ENDPOINT="${API_ENDPOINT:-}"
if [ -z "$API_ENDPOINT" ]; then
  if ! command -v terraform >/dev/null 2>&1; then
    echo "error: API_ENDPOINT not set and 'terraform' not on PATH." >&2
    exit 1
  fi
  API_ENDPOINT="$(terraform -chdir="${REPO_ROOT}/terraform/envs/demo" \
    output -raw api_endpoint)"
fi

JWT="${SMOKE_TEST_JWT:?Set SMOKE_TEST_JWT to a valid Cognito ID token}"

DEFAULT_PROMPT="Show me customer 32869c51-5c92-4322-87d8-3eae02f35a14 contact info."
PROMPT="${1:-$DEFAULT_PROMPT}"

echo "endpoint: ${API_ENDPOINT}"
echo "prompt:   ${PROMPT}"
echo

for PERSONA in dispatcher technician_lead owner; do
  EXTRA=""
  if [ "$PERSONA" = "technician_lead" ]; then
    EXTRA=', "service_region": "tempe-mesa"'
  fi

  echo "=== persona=${PERSONA} ==="
  payload=$(printf '{"question": "%s", "persona": "%s"%s}' \
    "$PROMPT" "$PERSONA" "$EXTRA")

  curl --fail-with-body -sS -X POST "${API_ENDPOINT}/ask" \
    -H "Authorization: Bearer ${JWT}" \
    -H "Content-Type: application/json" \
    -H "X-Gagent-Surface: web" \
    -d "$payload" \
    | jq .
  echo
done

echo "All three calls completed. Verify in CloudWatch Logs Insights:"
echo "  fields @timestamp, persona, surface, tools_called, guardrail_blocks"
echo "  | filter surface = 'web'"
echo "  | sort @timestamp desc"
echo "  | limit 20"

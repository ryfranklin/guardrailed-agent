# governed_query Lambda

Action group backing the Bedrock Agent. Queries Athena against the Iceberg tables (HVAC home-services dataset, ADR-008) and returns shape-preserved JSON. Assumes a session-tagged role on each invocation; tags inherit from the calling principal — never hardcoded.

See `docs/repo-bootstrap-brief.md` §11 for the action group contract and §6 for the layout rule that keeps Bedrock-specific glue out of business logic.

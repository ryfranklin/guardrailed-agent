# query_ambassadors Lambda

Phase 1 action group backing the Bedrock Agent. Queries Athena against the Iceberg tables and returns shape-preserved JSON. Assumes a session-tagged role on each invocation; tags inherit from the calling principal — never hardcoded.

Stub. See `docs/repo-bootstrap-brief.md` §11 for the action group contract and §6 for the layout rule that keeps Bedrock-specific glue out of business logic.

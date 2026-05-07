"""Governance probe helpers (ADR-009 §Tool 4 — explain_governance, §Tool 5 — eval_query).

Pure logic + AWS read-only calls used by the three Phase 2.b tools. Probes
sit on top of:
  * Glue: GetTable / GetColumnStatisticsForTable
  * Lake Formation: GetResourceLFTags / ListPermissions
The tools never execute the user's query — they reason about it from the
catalog + policy alone (the diagram in ADR-009 §Governance probe).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("mcp_server.governance")


# Athena's per-TB scan price in us-east-1. Override per region as needed.
ATHENA_USD_PER_TB = 5.00
BYTES_PER_TB = 1099511627776  # 1024^4

# Tag keys that drive our LF policy (ADR-008).
PII_TAG_KEY = "pii"
SENSITIVITY_TAG_KEY = "sensitivity"

# Default tags inherited from the database when a column has no override
# (matches the database default attachments in terraform/modules/data-plane).
DEFAULT_DATABASE_TAGS: dict[str, str] = {
    PII_TAG_KEY: "false",
    SENSITIVITY_TAG_KEY: "other",
}


@dataclass
class GrantExpression:
    """One AND-ed clause in an LF tag-policy expression."""

    key: str
    values: list[str]

    def matches(self, tag_value: str) -> bool:
        return tag_value in self.values


@dataclass
class TagPolicyGrant:
    """One LF tag-policy grant on a principal: permissions + AND-ed expressions."""

    permissions: list[str]
    permissions_with_grant_option: list[str]
    resource_type: str
    expressions: list[GrantExpression]

    def matches_column_tags(self, column_tags: dict[str, str]) -> bool:
        """A grant applies iff every expression's key is satisfied by the column."""
        for expr in self.expressions:
            tag_value = column_tags.get(expr.key)
            if tag_value is None or not expr.matches(tag_value):
                return False
        return True


@dataclass
class ColumnVisibility:
    """Resolved per-column visibility for one persona × table × column."""

    column: str
    visible: bool
    tags: dict[str, str]
    matched_grant: TagPolicyGrant | None = None
    reason: str = ""


@dataclass
class TableProbe:
    """Aggregated probe result for one table."""

    table: str
    columns: list[str]
    column_tags: dict[str, dict[str, str]]
    visibility: list[ColumnVisibility] = field(default_factory=list)
    size_bytes: int | None = None
    row_count: int | None = None


# ---- SQL parsing ----

_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)


def extract_table_names(query: str, known_tables: set[str]) -> list[str]:
    """Extract table names from FROM / JOIN clauses, filtered to known tables.

    Deliberately small: no CTE expansion, no quoted identifiers. Aliases that
    match a known table name are ignored. Use the agent's six action-group
    templates plus known dimension/fact tables for `known_tables`.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in _TABLE_REF_RE.findall(query):
        candidate = raw.lower()
        if candidate in known_tables and candidate not in seen:
            out.append(candidate)
            seen.add(candidate)
    return out


# ---- Glue + LF probes ----

def fetch_table_metadata(
    glue: Any, database: str, table: str, s3: Any | None = None,
) -> tuple[list[str], int | None, int | None]:
    """Return (columns, size_bytes, row_count).

    Glue parameters first; S3 ListObjects fallback when ``s3`` is supplied.
    Iceberg tables registered via Athena CTAS often have empty Glue stats —
    the S3 fallback sums object sizes under the table's storage location to
    give eval_query something usable.
    """
    response = glue.get_table(DatabaseName=database, Name=table)
    table_info = response["Table"]
    sd = table_info.get("StorageDescriptor", {}) or {}
    columns = [c["Name"] for c in (sd.get("Columns") or [])]
    params = table_info.get("Parameters") or {}
    size_bytes = _parse_int(params.get("totalSize")) or _parse_int(params.get("total_size"))
    row_count = _parse_int(params.get("recordCount")) or _parse_int(params.get("numRows"))

    if size_bytes is None and s3 is not None:
        location = sd.get("Location") or ""
        size_bytes = _sum_s3_prefix_size(s3, location)

    return columns, size_bytes, row_count


def _sum_s3_prefix_size(s3: Any, s3_uri: str) -> int | None:
    """Sum the Size field of every object under the given s3:// URI prefix."""
    if not s3_uri.startswith("s3://"):
        return None
    rest = s3_uri[len("s3://"):]
    if "/" not in rest:
        return None
    bucket, key_prefix = rest.split("/", 1)
    if not key_prefix.endswith("/"):
        key_prefix += "/"

    total = 0
    seen_anything = False
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "Bucket": bucket, "Prefix": key_prefix, "MaxKeys": 1000,
        }
        if next_token:
            kwargs["ContinuationToken"] = next_token
        try:
            response = s3.list_objects_v2(**kwargs)
        except Exception:  # noqa: BLE001
            logger.exception("s3 list_objects_v2 failed for %s", s3_uri)
            return None
        for obj in response.get("Contents", []) or []:
            seen_anything = True
            total += int(obj.get("Size") or 0)
        if not response.get("IsTruncated"):
            break
        next_token = response.get("NextContinuationToken")
        if not next_token:
            break
    return total if seen_anything else None


def fetch_column_lf_tags(
    lf: Any, database: str, table: str, columns: list[str],
) -> dict[str, dict[str, str]]:
    """Return {column: {tag_key: tag_value}} for every column.

    Inherits database-default tags when a column has no explicit attachment.
    """
    response = lf.get_resource_lf_tags(
        Resource={
            "Table": {"DatabaseName": database, "Name": table},
        },
        ShowAssignedLFTags=True,
    )

    out: dict[str, dict[str, str]] = {
        col: dict(DEFAULT_DATABASE_TAGS) for col in columns
    }
    for col_block in response.get("LFTagsOnColumns", []) or []:
        col_name = col_block.get("Name")
        if not col_name:
            continue
        col_tags = out.setdefault(col_name, dict(DEFAULT_DATABASE_TAGS))
        for tag in col_block.get("LFTags", []) or []:
            key = tag.get("TagKey")
            values = tag.get("TagValues") or []
            if key and values:
                col_tags[key] = values[0]
    return out


def fetch_persona_tag_grants(
    lf: Any, role_arn: str, database: str | None = None,
) -> list[TagPolicyGrant]:
    """Fetch the tag-policy grants on the persona role principal.

    LF's ListPermissions API rejects ``Principal`` without an explicit
    ``Resource``, so we list all LF_TAG_POLICY grants in the catalog and
    filter client-side by principal ARN. The catalog typically has on the
    order of dozens of grants — small enough to scan.

    list_permissions is not a registered boto3 paginator, so we follow
    the NextToken manually.
    """
    grants: list[TagPolicyGrant] = []
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "ResourceType": "LF_TAG_POLICY",
            "MaxResults": 100,
        }
        if next_token:
            kwargs["NextToken"] = next_token
        response = lf.list_permissions(**kwargs)
        for entry in response.get("PrincipalResourcePermissions", []) or []:
            principal = (entry.get("Principal") or {}).get("DataLakePrincipalIdentifier")
            if principal != role_arn:
                continue
            tag_policy = (
                (entry.get("Resource") or {}).get("LFTagPolicy") or {}
            )
            if not tag_policy:
                continue
            expressions = [
                GrantExpression(key=e["TagKey"], values=list(e.get("TagValues") or []))
                for e in tag_policy.get("Expression", []) or []
                if e.get("TagKey")
            ]
            grants.append(TagPolicyGrant(
                permissions=list(entry.get("Permissions") or []),
                permissions_with_grant_option=list(
                    entry.get("PermissionsWithGrantOption") or [],
                ),
                resource_type=tag_policy.get("ResourceType", ""),
                expressions=expressions,
            ))
        next_token = response.get("NextToken")
        if not next_token:
            break
    return grants


# ---- visibility logic ----

def compute_column_visibility(
    column: str,
    column_tags: dict[str, str],
    grants: list[TagPolicyGrant],
) -> ColumnVisibility:
    """A column is visible iff at least one grant's expression matches its tags."""
    for grant in grants:
        if grant.resource_type and grant.resource_type != "TABLE":
            continue
        if grant.matches_column_tags(column_tags):
            return ColumnVisibility(
                column=column,
                visible=True,
                tags=dict(column_tags),
                matched_grant=grant,
                reason=f"matched {format_tag_expression(grant)}",
            )
    return ColumnVisibility(
        column=column,
        visible=False,
        tags=dict(column_tags),
        matched_grant=None,
        reason=_explain_redaction(column_tags, grants),
    )


def _explain_redaction(
    column_tags: dict[str, str], grants: list[TagPolicyGrant],
) -> str:
    """Human-readable reason a column was redacted."""
    if not grants:
        return "no LF_TAG_POLICY grants on this principal"
    bits: list[str] = []
    for grant in grants:
        if grant.resource_type and grant.resource_type != "TABLE":
            continue
        for expr in grant.expressions:
            tag_value = column_tags.get(expr.key, "<missing>")
            if tag_value not in expr.values:
                bits.append(
                    f"{expr.key}={tag_value} not in {expr.values}",
                )
                break
    if not bits:
        return "no matching grant"
    return "; ".join(bits[:2])  # cap noise


def format_tag_expression(grant: TagPolicyGrant) -> str:
    """Render an LF tag policy as 'pii=[false] AND sensitivity=[other]'."""
    if not grant.expressions:
        return "<no expressions>"
    return " AND ".join(
        f"{e.key}={list(e.values)}" for e in grant.expressions
    )


def grant_to_dict(grant: TagPolicyGrant) -> dict[str, Any]:
    return {
        "permissions": list(grant.permissions),
        "permissions_with_grant_option": list(grant.permissions_with_grant_option),
        "resource_type": grant.resource_type,
        "tag_expression": [
            {"key": e.key, "values": list(e.values)}
            for e in grant.expressions
        ],
        "tag_expression_str": format_tag_expression(grant),
    }


# ---- cost projection ----

def project_athena_cost(
    scanned_bytes: int, usd_per_tb: float = ATHENA_USD_PER_TB,
) -> float:
    """Project Athena cost in USD from scanned-bytes estimate."""
    if scanned_bytes <= 0:
        return 0.0
    return (scanned_bytes / BYTES_PER_TB) * usd_per_tb


def human_bytes(n: int | None) -> str:
    if n is None:
        return "unknown"
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for unit in units:
        if f < 1024 or unit == units[-1]:
            return f"{f:.2f} {unit}"
        f /= 1024
    return f"{f:.2f} TB"


def _parse_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

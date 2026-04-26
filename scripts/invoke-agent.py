"""Headless CLI for the Bedrock Agent.

Assumes a persona role with session tags, calls InvokeAgent, streams the
response chunks to stdout, and emits a Langfuse trace if credentials are
available. Reads agent_id and agent_alias_id from terraform outputs by
default.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import boto3


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = _terraform_outputs(args.tf_dir) if args.tf_dir else {}

    agent_id = args.agent_id or cfg.get("agent_id")
    agent_alias_id = args.agent_alias_id or cfg.get("agent_alias_id")
    if not agent_id or not agent_alias_id:
        sys.exit("agent_id and agent_alias_id required (via --tf-dir or flags).")

    tags = _parse_tags(args.tags)
    creds = _assume_role(args.assume_role, tags) if args.assume_role else None

    runtime = boto3.client(
        "bedrock-agent-runtime",
        region_name=args.region,
        **_creds_kwargs(creds),
    )

    session_id = args.session_id or f"cli-{uuid.uuid4().hex[:8]}"
    session_attrs = {k: v for k, v in tags.items()}

    started = time.time()
    response = runtime.invoke_agent(
        agentId=agent_id,
        agentAliasId=agent_alias_id,
        sessionId=session_id,
        inputText=args.prompt,
        enableTrace=args.trace,
        sessionState={"sessionAttributes": session_attrs},
    )

    text_parts: list[str] = []
    trace_events: list[dict[str, Any]] = []
    for event in response["completion"]:
        if "chunk" in event:
            chunk = event["chunk"]["bytes"].decode("utf-8")
            text_parts.append(chunk)
            sys.stdout.write(chunk)
            sys.stdout.flush()
        elif "trace" in event and args.trace:
            trace_events.append(event["trace"])

    sys.stdout.write("\n")
    duration = time.time() - started

    if args.trace_out:
        Path(args.trace_out).write_text(json.dumps(trace_events, default=str, indent=2))

    _emit_langfuse(cfg.get("langfuse_secret_arn"), args.region, args, text_parts, trace_events, duration)
    return 0


def _parse_tags(spec: str | None) -> dict[str, str]:
    if not spec:
        return {}
    out: dict[str, str] = {}
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            sys.exit(f"invalid --tags entry {pair!r}; expected key=value")
        k, v = pair.split("=", 1)
        out[k.strip()] = v.strip()
    if "role" not in out:
        sys.exit("--tags must include role=<analyst|regional_manager|admin>")
    return out


def _assume_role(role_arn: str, tags: dict[str, str]) -> dict[str, str]:
    sts = boto3.client("sts")
    tag_list = [{"Key": k, "Value": v} for k, v in tags.items()]
    response = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"cli-{tags.get('role', 'persona')}-{uuid.uuid4().hex[:6]}",
        Tags=tag_list,
        TransitiveTagKeys=[t["Key"] for t in tag_list],
        DurationSeconds=900,
    )
    return response["Credentials"]


def _creds_kwargs(creds: dict[str, str] | None) -> dict[str, str]:
    if not creds:
        return {}
    return {
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"],
    }


def _terraform_outputs(tf_dir: Path) -> dict[str, Any]:
    proc = subprocess.run(
        ["terraform", f"-chdir={tf_dir}", "output", "-json"],
        capture_output=True, text=True, check=True,
    )
    raw = json.loads(proc.stdout)
    return {k: v.get("value") for k, v in raw.items()}


def _emit_langfuse(
    secret_arn: str | None, region: str, args: argparse.Namespace,
    text_parts: list[str], trace_events: list[dict[str, Any]], duration: float,
) -> None:
    if not secret_arn:
        return
    try:
        secrets = boto3.client("secretsmanager", region_name=region)
        creds = json.loads(secrets.get_secret_value(SecretId=secret_arn)["SecretString"])
        from langfuse import Langfuse
        client = Langfuse(
            public_key=creds["public_key"],
            secret_key=creds["secret_key"],
            host=creds.get("host", "https://cloud.langfuse.com"),
        )
        trace = client.trace(
            name="invoke-agent-cli",
            input=args.prompt,
            output="".join(text_parts),
            metadata={
                "tags": _parse_tags(args.tags),
                "duration_seconds": duration,
                "trace_events_count": len(trace_events),
            },
        )
        for event in trace_events:
            try:
                trace.event(name="bedrock-trace", input=event)
            except Exception:  # noqa: BLE001
                pass
        client.flush()
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"langfuse emission skipped: {exc}\n")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Invoke the deployed Bedrock Agent under an assumed persona role.")
    p.add_argument("--prompt", required=True)
    p.add_argument("--assume-role", help="Role ARN to assume. Required unless ambient creds already match the persona.")
    p.add_argument("--tags", help="Session tags as key=value,key=value. Must include role=<persona>.")
    p.add_argument("--session-id", help="Session ID for multi-turn. Default: random.")
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    p.add_argument("--agent-id")
    p.add_argument("--agent-alias-id")
    p.add_argument(
        "--tf-dir", type=Path,
        default=Path(__file__).parent.parent / "terraform" / "envs" / "demo",
    )
    p.add_argument("--trace", action="store_true", default=True, help="Enable Bedrock-native trace.")
    p.add_argument("--no-trace", action="store_false", dest="trace")
    p.add_argument("--trace-out", help="Write raw trace events to this path as JSON.")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())

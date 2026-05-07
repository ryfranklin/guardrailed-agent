# Eval Harness

Runs `prompts/golden.yaml` (must pass) and `prompts/red_team.yaml` (must block / refuse) against the deployed Bedrock Agent. Each invocation emits a structured JSON line to the gagent CloudWatch invocation log group (AgentCore Observability native). Wired into CI via `.github/workflows/eval.yml`.

```bash
python runner.py
```

Stub. See `docs/repo-bootstrap-brief.md` §13 for prompt corpora structure and runner contract.

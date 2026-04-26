# Eval Harness

Runs `prompts/golden.yaml` (must pass) and `prompts/red_team.yaml` (must block / refuse) against the deployed Bedrock Agent. Wrapped in Langfuse traces. Wired into CI via `.github/workflows/eval.yml`.

```bash
python runner.py
```

Stub. See `docs/repo-bootstrap-brief.md` §13 for prompt corpora structure and runner contract.

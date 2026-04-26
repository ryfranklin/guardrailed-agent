#!/usr/bin/env bash
# Run the eval harness against the deployed agent. Same venv as smoke-test.sh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVAL_DIR="$REPO_ROOT/eval"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      ver=$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "0.0")
      major=${ver%.*}; minor=${ver#*.}
      if [[ "$major" -ge 3 && "$minor" -ge 12 ]]; then
        PYTHON_BIN=$(command -v "$candidate"); break
      fi
    fi
  done
fi
if [[ -z "$PYTHON_BIN" ]]; then
  echo "error: need python3.12 or newer. Set PYTHON_BIN to override." >&2
  exit 1
fi

if [[ ! -d "$EVAL_DIR/.venv" ]]; then
  "$PYTHON_BIN" -m venv "$EVAL_DIR/.venv"
  "$EVAL_DIR/.venv/bin/pip" install -q --upgrade pip
  "$EVAL_DIR/.venv/bin/pip" install -q -r "$EVAL_DIR/requirements.txt"
fi

"$EVAL_DIR/.venv/bin/python" "$EVAL_DIR/runner.py" "$@"

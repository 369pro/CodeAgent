#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT_DIR"

LANGFUSE_ENV="$ROOT_DIR/.codeagent/langfuse.env"
if [ -f "$LANGFUSE_ENV" ]; then
    set -a
    . "$LANGFUSE_ENV"
    set +a
fi

export LANGFUSE_BASE_URL="${LANGFUSE_BASE_URL:-http://localhost:3000}"

if command -v uv >/dev/null 2>&1; then
    exec uv run pcode "$@"
fi

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON="$ROOT_DIR/.venv/bin/python"
else
    PYTHON=python3
fi

export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m codeagent.pcode_cli "$@"

# pcode              # TUI
# pcode chat         # TUI
# pcode agent "..."  # 旧 ReAct agent
# ./run.sh           # TUI
# ./run.sh agent     # 旧 ReAct agent

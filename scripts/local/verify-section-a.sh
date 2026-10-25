#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd "$(dirname "$0")/../.." && pwd)
backend_dir="$repo_dir/apps/backend"
python="$backend_dir/.venv/bin/python"
cd "$backend_dir"

"$python" -m pytest tests
"$python" -m ruff check app tests
"$python" -m ruff format --check app tests
"$python" -m mypy app tests
sh -n "$repo_dir/scripts/local/probe-live.sh"

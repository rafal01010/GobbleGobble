#!/bin/sh
set -eu

backend_dir=$(CDPATH= cd "$(dirname "$0")/../../apps/backend" && pwd)
export PYTHONPATH="$backend_dir"
exec "$backend_dir/.venv/bin/python" -m app.probe_cli "$@"

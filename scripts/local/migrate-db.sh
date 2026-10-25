#!/bin/sh
set -eu

backend_dir=$(CDPATH= cd "$(dirname "$0")/../../apps/backend" && pwd)
cd "$backend_dir"
exec .venv/bin/python -m app.db.migrate "$@"

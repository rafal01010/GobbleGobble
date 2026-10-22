#!/bin/sh
set -eu

backend_dir=$(CDPATH= cd "$(dirname "$0")/../../apps/backend" && pwd)
cd "$backend_dir"
exec uv sync --locked "$@"

#!/bin/sh
set -eu

frontend_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
backend_dir="$frontend_dir/../backend"
out_dir="$frontend_dir/src/api"
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/gobblegobble-contract.XXXXXX")
tmp_output=
trap 'rm -rf "$tmp_dir"; if [ -n "$tmp_output" ]; then rm -f "$tmp_output"; fi' EXIT

(cd "$backend_dir" && .venv/bin/python -m app.schema_export) > "$tmp_dir/openapi.json"
"$frontend_dir/tools/contract/node_modules/.bin/openapi-typescript" "$tmp_dir/openapi.json" -o "$tmp_dir/contract.d.ts"
mkdir -p "$out_dir"
tmp_output=$(mktemp "$out_dir/.contract.d.ts.XXXXXX")
cp "$tmp_dir/contract.d.ts" "$tmp_output"
mv "$tmp_output" "$out_dir/contract.d.ts"

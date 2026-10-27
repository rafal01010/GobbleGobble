#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd "$(dirname "$0")/../.." && pwd)
python="$repo_dir/apps/backend/.venv/bin/python"

case "${1:---offline}" in
  --offline)
    if test "$#" -gt 1; then
      echo 'Offline verification accepts no additional arguments.' >&2
      exit 2
    fi
    "$repo_dir/scripts/local/verify-section-a.sh"
    "$python" -m pytest "$repo_dir/scripts/local/test_check_docs.py"
    "$python" -m ruff check "$repo_dir/scripts/local/verify-foundation-stack.py" "$repo_dir/scripts/local/backend-process.py"
    "$python" -m ruff format --check "$repo_dir/scripts/local/verify-foundation-stack.py" "$repo_dir/scripts/local/backend-process.py"
    sh -n "$0" "$repo_dir/scripts/local/init-backend.sh" "$repo_dir/scripts/local/migrate-db.sh"
    cd "$repo_dir/apps/frontend"
    npm run generate:api
    npm run check
    npm run build
    cd "$repo_dir"
    python3 scripts/local/check-docs.py
    ;;
  --stack)
    shift
    exec "$python" "$repo_dir/scripts/local/verify-foundation-stack.py" "$@"
    ;;
  --help)
    if test "$#" -ne 1; then
      echo 'Use --help without additional arguments.' >&2
      exit 2
    fi
    echo 'Usage: scripts/local/verify-foundation.sh [--offline | --stack OUTPUT_DIRECTORY]'
    echo 'Default: installed-dependency checks without servers or external calls.'
    echo '--stack: controlled loopback HTTP, worker and process checks in a fresh evidence directory.'
    ;;
  *)
    echo 'Use --help for foundation verification options.' >&2
    exit 2
    ;;
esac

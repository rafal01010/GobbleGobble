#!/bin/sh
set -eu
repo_dir=$(CDPATH= cd "$(dirname "$0")/../.." && pwd)
backend_dir="$repo_dir/apps/backend"
python="$backend_dir/.venv/bin/python"
cd "$backend_dir"
"$python" -m pytest tests/test_location_resolution.py tests/test_geoapify.py tests/test_geoapify_routes.py tests/test_meal_discovery.py tests/test_web_discovery.py tests/test_identity.py tests/test_journey.py tests/test_opening.py tests/test_candidates.py tests/test_google_branch_search.py tests/test_google_reviews.py tests/test_review_research.py tests/test_review_selection.py tests/test_evidence_storage.py tests/test_food_claims.py tests/test_review_interpretation.py tests/test_evidence_validity.py tests/test_evidence_projection.py tests/test_research_snapshots.py tests/test_research_tools.py tests/test_research_agent.py tests/test_research_assessment.py tests/test_research_budget.py tests/test_research_gate_controls.py tests/test_research_live_probe.py tests/test_sdk_probe.py tests/test_web_research.py tests/test_source_policy.py tests/test_calls.py tests/test_contract_examples.py -q
"$python" -m ruff check app/agents/research.py app/agents/research_assessment.py app/agents/research_budget.py tests/test_research_agent.py tests/test_research_assessment.py tests/test_research_assessment.py tests/test_research_budget.py "$repo_dir/scripts/local/verify-research-live.py"
"$python" -m mypy app/agents/research.py app/agents/research_assessment.py app/agents/research_budget.py --follow-imports=silent
python3 "$repo_dir/scripts/local/check-docs.py"

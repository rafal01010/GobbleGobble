import json
from pathlib import Path

import pytest
from agents.agent_output import AgentOutputSchema
from pydantic import ValidationError

from app.agents.owner_draft import OwnerDraft, validate_owner_draft
from app.agents.research import schema_restrictions
from app.schemas.recommendations import RecommendationCollection

EXAMPLE = Path("../../docs/examples/recommendation-contract/accepted_full_set.json")


def context():
    return RecommendationCollection.model_validate_json(EXAMPLE.read_text())


def draft(collection):
    source = collection.versions[0]
    return OwnerDraft.model_validate_json(
        json.dumps(
            {
                "primaries": [item.model_dump() for item in source.primaries],
                "runners_up": [item.model_dump() for item in source.runners_up],
                "shortfall_reason": source.shortfall_reason,
            }
        )
    )


def adapt(value, original):
    return validate_owner_draft(
        value,
        search_id=original.search_id,
        request=original.request,
        snapshot=original.snapshots[0],
        version_id="ver_owner",
    )


def test_registered_schema_is_supported_and_nested_output_validates():
    schema = AgentOutputSchema(OwnerDraft).json_schema()
    assert schema_restrictions(schema) == ()
    assert schema["properties"]["primaries"]["items"] == {
        "$ref": "#/$defs/RecommendationDraft"
    }


def test_owner_order_and_evidence_survive_authoritative_binding():
    original = context()
    result = adapt(draft(original), original)
    version = result.versions[0]
    assert result.search_id == "search_example"
    assert result.request == original.request
    assert version.version_id == "ver_owner"
    assert version.research_snapshot_id == "snap_initial"
    assert version.lineage.kind == "initial"
    assert [r.place_id for r in version.primaries] == [
        "branch_a",
        "branch_b",
        "branch_c",
    ]
    assert [r.place_id for r in version.runners_up] == [
        "branch_d",
        "branch_e",
        "branch_f",
        "branch_g",
    ]
    assert version.primaries[0].food_claim_ids == ("menu_a",)
    claim = next(
        c for c in result.snapshots[0].evidence.food_claims if c.claim_id == "menu_a"
    )
    assert claim.subjects[0].evidence_ids == ("ev_menu_a",)


def test_smaller_set_requires_reason_and_does_not_pad():
    original = context()
    value = draft(original).model_copy(
        update={
            "primaries": draft(original).primaries[:1],
            "runners_up": [],
            "shortfall_reason": "Only one candidate has enough evidence.",
        }
    )
    result = adapt(value, original)
    assert [r.place_id for r in result.versions[0].primaries] == ["branch_a"]
    assert result.versions[0].runners_up == ()
    with pytest.raises(ValidationError):
        adapt(value.model_copy(update={"shortfall_reason": None}), original)


@pytest.mark.parametrize(
    "change",
    [
        "wrong_branch",
        "unknown_claim",
        "duplicate",
        "too_many_primary",
        "too_many_runner",
        "unknown_place",
    ],
)
def test_invalid_selections_are_rejected_by_domain_contract(change):
    original = context()
    payload = draft(original).model_dump()
    if change == "wrong_branch":
        payload["primaries"][0]["food_claim_ids"] = ["menu_b"]
    elif change == "unknown_claim":
        payload["primaries"][0]["food_claim_ids"] = ["missing"]
    elif change == "duplicate":
        payload["runners_up"][0] = payload["primaries"][0]
    elif change == "too_many_primary":
        payload["primaries"].append(payload["runners_up"].pop())
    elif change == "too_many_runner":
        payload["runners_up"].append(
            {
                "place_id": "branch_h",
                "why_to_go": "Good food.",
                "food_claim_ids": ["menu_h"],
            }
        )
    else:
        payload["primaries"][0]["place_id"] = "unknown"
    with pytest.raises(ValidationError):
        adapt(OwnerDraft.model_validate(payload), original)


def test_unknown_model_fields_are_not_admitted():
    original = context()
    value = draft(original).model_dump() | {"version_id": "ver_model"}
    with pytest.raises(ValidationError):
        OwnerDraft.model_validate(value)

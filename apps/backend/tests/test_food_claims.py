import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.food_claims import FoodGap, InferredFood, SourcedFood, interpret_food
from app.schemas.evidence import EvidenceCollection


def collection():
    path = (
        Path(__file__).resolve().parents[3]
        / "docs/examples/evidence-contract/accepted_shared_source_scoped_menu_and_price.json"
    )
    data = json.loads(path.read_text())
    data["food_claims"] = []
    return EvidenceCollection.model_validate_json(json.dumps(data))


def test_menu_and_optional_price_copy_branch_scoped_evidence():
    result = interpret_food(
        collection(),
        sourced=(
            SourcedFood(
                claim_id="menu",
                place_id="branch_a",
                evidence_id="ev_menu_a",
                topic="menu",
            ),
            SourcedFood(
                claim_id="price",
                place_id="branch_a",
                evidence_id="ev_price_a",
                topic="price",
            ),
        ),
    )
    assert [
        (c.status, c.topic, c.statement, c.subjects[0].evidence_ids)
        for c in result.food_claims
    ] == [
        ("sourced", "menu", "Branch A lists roast chicken.", ("ev_menu_a",)),
        ("sourced", "price", "Branch A chicken costs PHP 250.", ("ev_price_a",)),
    ]


def test_missing_menu_and_price_can_coexist_with_qualified_inference():
    data = collection().model_dump()
    data["evidence"] = tuple(
        item.model_copy(update={"statement": "Branch A is known for grilled food."})
        if item.evidence_id == "ev_menu_a"
        else item
        for item in collection().evidence
    )
    result = interpret_food(
        EvidenceCollection.model_validate(data),
        inferred=(
            InferredFood(
                claim_id="suggestion",
                place_id="branch_a",
                evidence_id="ev_menu_a",
            ),
        ),
        gaps=(
            FoodGap(
                claim_id="menu_gap",
                place_id="branch_a",
                topic="menu",
                gap="No current menu was obtained.",
            ),
            FoodGap(
                claim_id="price_gap",
                place_id="branch_a",
                topic="price",
                gap="No current price was obtained.",
            ),
        ),
    )
    claim, menu_gap, price_gap = result.food_claims
    assert claim.status == "inferred"
    assert claim.topic == "food"
    assert (
        claim.statement
        == "Inferred suggestion: Consider food described by this source: Branch A is known for grilled food. Current availability and price are unconfirmed."
    )
    assert claim.basis == "The suggestion is based on the cited source statement."
    assert claim.subjects[0].evidence_ids == ("ev_menu_a",)
    assert (menu_gap.status, menu_gap.gap, price_gap.status, price_gap.gap) == (
        "unknown",
        "No current menu was obtained.",
        "unknown",
        "No current price was obtained.",
    )


def test_wrong_branch_and_unknown_evidence_are_rejected():
    with pytest.raises(ValidationError, match="another place"):
        interpret_food(
            collection(),
            sourced=(
                SourcedFood(
                    claim_id="wrong",
                    place_id="branch_b",
                    evidence_id="ev_menu_a",
                    topic="menu",
                ),
            ),
        )
    with pytest.raises(ValueError, match="unknown supporting evidence"):
        interpret_food(
            collection(),
            sourced=(
                SourcedFood(
                    claim_id="missing",
                    place_id="branch_a",
                    evidence_id="ev_missing",
                    topic="food",
                ),
            ),
        )
    with pytest.raises(ValidationError, match="another place"):
        interpret_food(
            collection(),
            inferred=(
                InferredFood(
                    claim_id="wrong",
                    place_id="branch_b",
                    evidence_id="ev_menu_a",
                ),
            ),
        )


def test_inference_rejects_free_text_and_unsupported_prices():
    with pytest.raises(ValidationError, match="Extra inputs"):
        InferredFood(
            claim_id="bad",
            place_id="branch_a",
            evidence_id="ev_menu_a",
            suggestion="Roast chicken is available today for PHP 999.",
            basis="The source mentions chicken.",
        )
    result = interpret_food(
        collection(),
        inferred=(
            InferredFood(claim_id="safe", place_id="branch_a", evidence_id="ev_menu_a"),
        ),
    )
    assert result.food_claims[0].statement == (
        "Inferred suggestion: Consider food described by this source: Branch A lists roast chicken. "
        "Current availability and price are unconfirmed."
    )
    assert "999" not in result.food_claims[0].statement

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.review_interpretation import InterpretedFinding, interpret_reviews
from app.schemas.evidence import EvidenceCollection

EXAMPLES = Path(__file__).resolve().parents[3] / "docs/examples/evidence-contract"


def collection(name="accepted_partial_with_usable_complaint", *, text=None):
    data = json.loads((EXAMPLES / f"{name}.json").read_text())
    assessment = data["review_assessments"][0]
    assessment["findings"] = []
    if text is not None:
        data["evidence"][0]["statement"] = text
        data["evidence"][0]["locator"]["exact_quote"] = None
    return EvidenceCollection.model_validate_json(json.dumps(data))


def finding(
    statement,
    *,
    topic="food",
    polarity="positive",
    kind="paraphrase",
    evidence_id="ev_review_a",
):
    return InterpretedFinding(
        evidence_id=evidence_id,
        topic=topic,
        polarity=polarity,
        kind=kind,
        statement=statement,
    )


def test_contrast_keeps_both_findings_and_sample_limits():
    original = collection(text="The chicken was excellent, but the service was slow.")
    result = interpret_reviews(
        original,
        "assessment_a",
        (
            finding("The reviewer enjoyed the chicken."),
            finding(
                "The reviewer found service slow.",
                topic="complaint",
                polarity="negative",
            ),
        ),
    )
    assessment = result.collection.review_assessments[0]
    assert [(f.topic, f.statement) for f in assessment.findings] == [
        ("food", "The reviewer enjoyed the chicken."),
        ("complaint", "The reviewer found service slow."),
    ]
    assert [item.polarity for item in result.findings] == ["positive", "negative"]
    assert [(f.polarity, f.wording) for f in assessment.findings] == [
        ("positive", "paraphrase"),
        ("negative", "paraphrase"),
    ]
    assert assessment.google.sample.sample_size == 1
    assert assessment.google.sample.selection_method == "Bounded source-selected sample"
    assert assessment.google.sample.partial is True
    assert assessment.google.sample.complete_history is False
    assert (
        result.collection.evidence[0].statement
        == "The chicken was excellent, but the service was slow."
    )


def test_negation_reported_quotes_personal_anecdotes_and_japanese_are_kept():
    text = "私は鶏肉が好きでしたが、友人は「味が薄い」と言いました。サービスは悪くなかった。"
    result = interpret_reviews(
        collection(text=text),
        "assessment_a",
        (
            finding("The reviewer liked the chicken."),
            finding(
                "The reviewer reports that a friend found it bland.",
                polarity="negative",
            ),
            finding(
                "The reviewer did not describe the service as bad.",
                topic="service",
                polarity="positive",
            ),
            finding("味が薄い", polarity="negative", kind="quotation"),
        ),
    )
    assert result.collection.evidence[0].statement == text
    assert [f.statement for f in result.collection.review_assessments[0].findings] == [
        "The reviewer liked the chicken.",
        "The reviewer reports that a friend found it bland.",
        "The reviewer did not describe the service as bad.",
        "味が薄い",
    ]
    assert result.collection.evidence[0].attribution_ids == ("author_example",)
    assert result.collection.review_assessments[0].findings[-1].wording == "quotation"
    data = result.collection.model_dump(mode="json")
    data["review_assessments"][0]["findings"][-1]["statement"] = "invented quotation"
    with pytest.raises(ValidationError, match="exact attributed evidence"):
        EvidenceCollection.model_validate_json(json.dumps(data))


def test_selected_negative_sample_does_not_establish_complete_history():
    result = interpret_reviews(
        collection(text="The soup was cold."),
        "assessment_a",
        (
            finding(
                "The reviewer found the soup cold.",
                topic="complaint",
                polarity="negative",
            ),
        ),
    )
    sample = result.collection.review_assessments[0].google.sample
    assert sample.reported_total == 24
    assert sample.assessed_count == 1
    assert sample.complete_history is False
    assert result.findings[0].polarity == "negative"


def test_no_reviews_is_valid_but_cannot_have_a_finding():
    original = collection("accepted_google_zero_review")
    assessment_id = original.review_assessments[0].assessment_id
    result = interpret_reviews(original, assessment_id, ())
    assert result.collection.review_assessments[0].google.status == "zero_review"
    assert result.findings == ()
    with pytest.raises(ValueError, match="available review sample"):
        interpret_reviews(original, assessment_id, (finding("Bad"),))


def test_quote_must_be_exact_and_attributed_and_support_must_be_scoped():
    original = collection(text="The chicken was excellent, but the service was slow.")
    with pytest.raises(ValueError, match="quotation does not match"):
        interpret_reviews(
            original, "assessment_a", (finding("chicken was awful", kind="quotation"),)
        )
    data = original.model_dump()
    data["evidence"] = (
        original.evidence[0].model_copy(update={"attribution_ids": ()}),
    )
    no_author = EvidenceCollection.model_validate(data)
    with pytest.raises(ValueError, match="author attribution"):
        interpret_reviews(
            no_author,
            "assessment_a",
            (finding("chicken was excellent", kind="quotation"),),
        )
    with pytest.raises(ValueError, match="unknown review evidence"):
        interpret_reviews(
            original, "assessment_a", (finding("Other", evidence_id="ev_missing"),)
        )
    data = json.loads(original.model_dump_json())
    other_source = dict(data["sources"][0])
    other_source["source_id"] = "src_google_b"
    other_source["subject_place_ids"] = ["branch_b"]
    other_source["origin"] = {
        "kind": "google",
        "branch": {"feature_id": "0xb:0x1"},
        "access_method": "anonymous",
    }
    other_evidence = dict(data["evidence"][0])
    other_evidence["evidence_id"] = "ev_review_b"
    other_evidence["source_id"] = "src_google_b"
    other_evidence["subject_place_ids"] = ["branch_b"]
    other_evidence["locator"] = {
        "kind": "google_review",
        "branch": {"feature_id": "0xb:0x1"},
        "review_id": "b",
        "source_url": "https://example.invalid/b",
        "link_basis": "review",
    }
    data["sources"].append(other_source)
    data["evidence"].append(other_evidence)
    other_branch = EvidenceCollection.model_validate_json(json.dumps(data))
    with pytest.raises(ValidationError, match="another place"):
        interpret_reviews(
            other_branch, "assessment_a", (finding("Other", evidence_id="ev_review_b"),)
        )

import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AT = "2026-10-25T04:00:00Z"


def source(source_id, places, google=None):
    declaration = {"decision": "unknown", "basis": "Synthetic policy declaration."}
    ephemeral = {"mode": "ephemeral", "basis": "Synthetic ephemeral example."}
    return {
        "source_id": source_id,
        "subject_place_ids": places,
        "publisher": "Google Maps" if google else "Example restaurant group",
        "source_url": "https://example.invalid/" + source_id,
        "attribution_ids": ["author_example"] if google else [],
        "origin": (
            {
                "kind": "google",
                "branch": {"feature_id": google},
                "access_method": "anonymous",
            }
            if google
            else {"kind": "independent", "access_method": "public_page"}
        ),
        "policy": {
            key: deepcopy(declaration)
            for key in ("access", "display", "model_input", "derived_output", "tracing")
        },
        "retention": {
            "raw_content": deepcopy(ephemeral),
            "derived_content": deepcopy(ephemeral),
            "identifiers": {"mode": "unknown", "basis": "Synthetic identifier policy."},
        },
    }


def evidence(evidence_id, source_id, places, statement, google=None):
    return {
        "evidence_id": evidence_id,
        "source_id": source_id,
        "subject_place_ids": places,
        "statement": statement,
        "locator": (
            {
                "kind": "google_review",
                "branch": {"feature_id": google},
                "review_id": "public_review_" + evidence_id,
                "source_url": "https://example.invalid/review/" + evidence_id,
                "link_basis": "review",
                "exact_quote": statement,
            }
            if google
            else {"kind": "document", "section": "Synthetic branch menu"}
        ),
        "attribution_ids": ["author_example"] if google else [],
        "freshness": {
            "retrieved_at": AT,
            "observed_at": None,
            "fresh_until": None,
            "basis": "Synthetic observation, freshness duration unknown.",
        },
    }


def support(place, *evidence_ids):
    return {"place_id": place, "evidence_ids": list(evidence_ids)}


def claim(claim_id, topic, statement, *subjects):
    return {
        "claim_id": claim_id,
        "status": "sourced",
        "topic": topic,
        "statement": statement,
        "subjects": list(subjects),
    }


def sample(evidence_ids, state="present"):
    return {
        "state": state,
        "evidence_ids": evidence_ids,
        "sample_size": len(evidence_ids),
        "assessed_evidence_ids": evidence_ids.copy(),
        "assessed_count": len(evidence_ids),
        "reported_total": 24,
        "reported_total_basis": "Synthetic supplied listing count, not refreshed.",
        "selection_method": "Bounded source-selected sample",
        "requested_order": "relevant",
        "observed_order": "unknown",
        "bounds": {"max_reviews": 2, "max_pages": 1},
        "complete_history": False,
    }


def collection():
    return {
        "place_ids": ["branch_a", "branch_b"],
        "sources": [],
        "attributions": [],
        "evidence": [],
        "food_claims": [],
        "review_assessments": [],
    }


def google_collection(outcome="available", state="present"):
    payload = collection()
    payload["sources"] = [source("src_google_a", ["branch_a"], "0xa:0x1")]
    payload["attributions"] = [
        {
            "attribution_id": "author_example",
            "kind": "author",
            "name": "Maria Example",
            "url": "https://example.invalid/public-profile",
        }
    ]
    google = {"status": outcome}
    if outcome in {"available", "zero_review", "restricted", "provider_unavailable"}:
        google["source_id"] = "src_google_a"
    findings = []
    if outcome == "available":
        review_ids = ["ev_review_a"] if state == "present" else []
        google["sample"] = sample(review_ids, state)
        if review_ids:
            payload["evidence"] = [
                evidence(
                    "ev_review_a",
                    "src_google_a",
                    ["branch_a"],
                    'Hindi masarap ang sabaw. I said, "not worth the price".',
                    "0xa:0x1",
                )
            ]
            findings = [
                {
                    "topic": "complaint",
                    "statement": "The sampled reviewer disliked the soup and its value.",
                    "subjects": [support("branch_a", "ev_review_a")],
                }
            ]
    elif outcome == "zero_review":
        google.update(
            {
                "reported_total": 0,
                "confirmation_basis": "Synthetic matched listing reports zero.",
            }
        )
    elif outcome == "restricted":
        google.update(
            {
                "requested_use": "display",
                "reason": "Synthetic source-specific display restriction.",
            }
        )
    else:
        google["reason"] = (
            "Synthetic ambiguous branch."
            if outcome == "no_match"
            else "Synthetic transport timeout."
        )
    payload["review_assessments"] = [
        {
            "assessment_id": "assessment_a",
            "place_id": "branch_a",
            "google": google,
            "findings": findings,
        }
    ]
    return payload


def write(name, payload):
    (ROOT / (name + ".json")).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main():
    shared = collection()
    shared["sources"] = [source("src_group_menu", ["branch_a", "branch_b"])]
    shared["evidence"] = [
        evidence(
            "ev_menu_a", "src_group_menu", ["branch_a"], "Branch A lists roast chicken."
        ),
        evidence(
            "ev_menu_b", "src_group_menu", ["branch_b"], "Branch B lists mushroom soup."
        ),
        evidence(
            "ev_price_a",
            "src_group_menu",
            ["branch_a"],
            "Branch A chicken costs PHP 250.",
        ),
        evidence(
            "ev_price_b", "src_group_menu", ["branch_b"], "Branch B soup costs PHP 180."
        ),
    ]
    shared["food_claims"] = [
        claim(
            "claim_menu_a",
            "menu",
            "Branch A lists roast chicken.",
            support("branch_a", "ev_menu_a"),
        ),
        claim(
            "claim_price_b",
            "price",
            "Branch B soup costs PHP 180.",
            support("branch_b", "ev_price_b"),
        ),
        {
            **claim(
                "claim_inference",
                "food",
                "Chicken may suit someone wanting a substantial meal.",
                support("branch_a", "ev_menu_a"),
            ),
            "status": "inferred",
            "basis": "A listed roast chicken dish supports this qualified suggestion, not portion certainty.",
        },
        {
            "claim_id": "claim_gap",
            "status": "unknown",
            "topic": "price",
            "subject_place_ids": ["branch_a"],
            "gap": "Current drink prices were not obtained.",
        },
    ]
    write("accepted_shared_source_scoped_menu_and_price", shared)
    stale = deepcopy(shared)
    stale["sources"][0]["retention"]["raw_content"] = {
        "mode": "until",
        "until": "2026-11-25T04:00:00Z",
        "basis": "Synthetic declaration permits retaining this stale observation.",
    }
    stale["evidence"][0]["freshness"].update(
        {
            "observed_at": "2026-09-25T04:00:00Z",
            "fresh_until": "2026-09-26T04:00:00Z",
            "basis": "Historical menu was already stale when retrieved.",
        }
    )
    write("accepted_stale_but_retainable", stale)
    multi = deepcopy(shared)
    multi["evidence"].append(
        evidence(
            "ev_both",
            "src_group_menu",
            ["branch_a", "branch_b"],
            "The group page explicitly lists rice at both branches.",
        )
    )
    multi["food_claims"].append(
        claim(
            "claim_both",
            "menu",
            "Both branches list rice.",
            support("branch_a", "ev_both"),
            support("branch_b", "ev_both"),
        )
    )
    write("accepted_explicit_multiplace", multi)

    for topic, claim_index, replacement in (
        ("menu", 0, "ev_menu_b"),
        ("price", 1, "ev_price_a"),
    ):
        bad = deepcopy(shared)
        bad["food_claims"][claim_index]["subjects"][0]["evidence_ids"] = [replacement]
        write("rejected_swapped_branch_" + topic, bad)
    bad = deepcopy(shared)
    bad["food_claims"][0]["subjects"][0]["evidence_ids"] = ["src_group_menu"]
    write("rejected_source_id_as_evidence_id", bad)
    bad = deepcopy(shared)
    bad["food_claims"][0]["subjects"][0]["evidence_ids"] = ["ev_nonexistent"]
    write("rejected_nonexistent_evidence", bad)
    bad = deepcopy(shared)
    bad["food_claims"] = [
        claim(
            "claim_union",
            "menu",
            "Both branches list chicken.",
            support("branch_a", "ev_menu_a"),
            support("branch_b", "ev_menu_a", "ev_menu_b"),
        )
    ]
    write("rejected_multisubject_union_of_support", bad)

    for state in ("present", "empty", "omitted"):
        write("accepted_google_" + state, google_collection(state=state))
    for outcome in ("zero_review", "no_match", "restricted", "provider_unavailable"):
        write("accepted_google_" + outcome, google_collection(outcome))
    bad = google_collection("zero_review")
    bad["review_assessments"][0]["google"]["reported_total"] = False
    write("rejected_zero_review_boolean_count", bad)

    partial = google_collection()
    partial["review_assessments"][0]["google"]["sample"].update(
        {
            "partial": True,
            "qualification": "A usable first page preceded a transport failure.",
            "omissions": ["Later pages were not retrieved."],
        }
    )
    write("accepted_partial_with_usable_complaint", partial)
    bad = deepcopy(partial)
    bad["sources"].append(source("src_google_b", ["branch_b"], "0xb:0x2"))
    bad["evidence"].append(
        evidence(
            "ev_review_b",
            "src_google_b",
            ["branch_b"],
            "My soup arrived cold.",
            "0xb:0x2",
        )
    )
    bad["review_assessments"][0]["findings"][0]["subjects"][0]["evidence_ids"] = [
        "ev_review_b"
    ]
    write("rejected_swapped_branch_complaint", bad)
    outside = google_collection()
    outside["evidence"].append(
        evidence(
            "ev_review_unassessed",
            "src_google_a",
            ["branch_a"],
            "Service was slow.",
            "0xa:0x1",
        )
    )
    outside["review_assessments"][0]["findings"][0]["subjects"][0]["evidence_ids"] = [
        "ev_review_unassessed"
    ]
    write("rejected_complaint_outside_declared_sample", outside)
    bad = google_collection()
    bad["evidence"][0]["locator"]["branch"]["feature_id"] = "0xb:0x2"
    write("rejected_google_locator_branch_mismatch", bad)
    bad = google_collection(state="empty")
    bad["review_assessments"][0]["google"]["sample"]["reported_total"] = 0
    write("rejected_empty_sample_as_confirmed_zero", bad)

    fallback = google_collection("provider_unavailable")
    fallback["sources"].append(source("src_independent_a", ["branch_a"]))
    fallback["evidence"].append(
        evidence(
            "ev_independent_a",
            "src_independent_a",
            ["branch_a"],
            "Independent menu lists chicken.",
        )
    )
    fallback["review_assessments"][0]["fallback"] = {
        "kind": "food_evidence",
        "source_id": "src_independent_a",
        "reason": "provider_unavailable",
        "evidence_ids": ["ev_independent_a"],
        "findings": [
            {
                "topic": "food",
                "statement": "The independent menu lists chicken.",
                "subjects": [support("branch_a", "ev_independent_a")],
            }
        ],
    }
    write("accepted_labeled_independent_food_fallback", fallback)
    review_fallback = deepcopy(fallback)
    review_fallback["review_assessments"][0]["fallback"] = {
        "kind": "review_sample",
        "source_id": "src_independent_a",
        "reason": "provider_unavailable",
        "sample": sample(["ev_independent_a"]),
        "findings": [],
    }
    review_fallback["evidence"][0]["statement"] = (
        "An independent reviewer reports that the soup was cold."
    )
    review_fallback["evidence"][0]["locator"]["section"] = "Independent public review"
    write("accepted_labeled_independent_review_fallback", review_fallback)
    bad = deepcopy(fallback)
    bad["review_assessments"][0]["fallback"]["reason"] = "no_match"
    write("rejected_fallback_reason_mismatch", bad)
    bad = deepcopy(fallback)
    bad["evidence"][0]["locator"] = google_collection()["evidence"][0]["locator"]
    write("rejected_independent_google_identity", bad)


if __name__ == "__main__":
    main()

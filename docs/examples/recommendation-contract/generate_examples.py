import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def place(suffix):
    return {
        "place_id": "branch_" + suffix,
        "kind": "branch",
        "name": "Example " + suffix,
        "branch_label": "Synthetic branch " + suffix,
        "location": {
            "coordinates": {"latitude": 14.6, "longitude": 121.0},
            "timezone": "Asia/Manila",
        },
        "journey": {"status": "unknown"},
        "opening": {"status": "unknown"},
    }


def claim(suffix):
    return {
        "claim_id": "menu_" + suffix,
        "status": "sourced",
        "topic": "menu",
        "statement": "The synthetic menu lists noodles for branch " + suffix + ".",
        "subjects": [
            {"place_id": "branch_" + suffix, "evidence_ids": ["ev_menu_" + suffix]}
        ],
    }


def recommendation(suffix):
    return {
        "place_id": "branch_" + suffix,
        "why_to_go": "An illustrative researched option, not a real recommendation.",
        "food_claim_ids": ["menu_" + suffix],
    }


def support(suffix, *claim_ids):
    return {"place_id": "branch_" + suffix, "food_claim_ids": list(claim_ids)}


def base():
    candidate_ids = ["branch_" + suffix for suffix in "abcdefgh"]
    declaration = {"decision": "unknown", "basis": "Synthetic policy declaration."}
    retention = {"mode": "ephemeral", "basis": "Synthetic ephemeral content."}
    source = {
        "source_id": "src_group",
        "subject_place_ids": candidate_ids,
        "publisher": "Example group",
        "source_url": "https://example.invalid/menu",
        "origin": {"kind": "independent", "access_method": "public_page"},
        "policy": {
            key: deepcopy(declaration)
            for key in ("access", "display", "model_input", "derived_output", "tracing")
        },
        "retention": {
            key: deepcopy(retention)
            for key in ("raw_content", "derived_content", "identifiers")
        },
    }
    evidence = [
        {
            "evidence_id": "ev_menu_" + suffix,
            "source_id": "src_group",
            "subject_place_ids": ["branch_" + suffix],
            "statement": "The synthetic menu lists noodles for branch " + suffix + ".",
            "locator": {"kind": "document", "section": "Branch " + suffix},
            "freshness": {
                "retrieved_at": "2026-10-25T04:00:00Z",
                "basis": "Synthetic observation with unknown freshness duration.",
            },
        }
        for suffix in "abcdefgh"
    ]
    multi_evidence = deepcopy(evidence[0])
    multi_evidence.update(
        evidence_id="ev_multi",
        subject_place_ids=["branch_a", "branch_b"],
        statement="Both synthetic branches list noodles.",
    )
    evidence.append(multi_evidence)
    claims = [claim(suffix) for suffix in "abcdefgh"]
    inferred = deepcopy(claim("h"))
    inferred.update(
        claim_id="inferred_h",
        status="inferred",
        topic="food",
        statement="Noodles are a suggested food option at synthetic branch h.",
        basis="The branch-specific menu observation is the stated inference basis.",
    )
    price = deepcopy(claim("a"))
    price.update(claim_id="price_a", topic="price", statement="Synthetic price detail.")
    claims.extend(
        [
            inferred,
            price,
            {
                "claim_id": "unknown_a",
                "status": "unknown",
                "topic": "food",
                "subject_place_ids": ["branch_a"],
                "gap": "Current food availability is unconfirmed.",
            },
            {
                "claim_id": "shared_noodles",
                "status": "sourced",
                "topic": "food",
                "statement": "Both synthetic branches list noodles.",
                "subjects": [
                    {"place_id": name, "evidence_ids": ["ev_multi"]}
                    for name in ("branch_a", "branch_b")
                ],
            },
        ]
    )
    return {
        "search_id": "search_example",
        "request": {
            "origin": {
                "kind": "chosen",
                "location": {
                    "coordinates": {"latitude": 14.6, "longitude": 121.0},
                    "timezone": "Asia/Manila",
                    "name": "Synthetic origin",
                },
            },
            "travel": {"mode": "walk", "minutes": 15},
            "eat_now_at": "2026-10-25T04:00:00Z",
        },
        "snapshots": [
            {
                "snapshot_id": "snap_initial",
                "discovery_leads": [place("lead")],
                "researched_candidates": [place(suffix) for suffix in "abcdefgh"],
                "evidence": {
                    "place_ids": [*candidate_ids, "branch_lead"],
                    "sources": [source],
                    "evidence": evidence,
                    "food_claims": claims,
                },
            }
        ],
        "versions": [
            {
                "version_id": "ver_initial",
                "research_snapshot_id": "snap_initial",
                "lineage": {"kind": "initial"},
                "primaries": [recommendation(suffix) for suffix in "abc"],
                "runners_up": [recommendation(suffix) for suffix in "defg"],
                "shortfall_reason": None,
                "food_choices": [
                    {
                        "choice_id": "noodles",
                        "version_id": "ver_initial",
                        "label": "Noodles",
                        "supports": [support("h", "menu_h")],
                    }
                ],
            }
        ],
    }


def chain():
    payload = base()
    parent = payload["versions"][0]
    child = deepcopy(parent)
    child.update(
        version_id="ver_refined",
        lineage={
            "kind": "food_choice",
            "parent_version_id": "ver_initial",
            "choice": {"version_id": "ver_initial", "choice_id": "noodles"},
        },
    )
    child["food_choices"][0]["version_id"] = "ver_refined"
    all_options = deepcopy(child)
    all_options.update(
        version_id="ver_all",
        lineage={"kind": "all_options", "parent_version_id": "ver_refined"},
    )
    all_options["food_choices"][0]["version_id"] = "ver_all"
    payload["versions"].extend([child, all_options])
    return payload


def generate():
    examples = {}

    def example(name, payload=None):
        value = base() if payload is None else deepcopy(payload)
        examples[name] = value
        return value

    example("accepted_full_set")
    value = example("accepted_two_runners")
    value["versions"][0]["runners_up"] = value["versions"][0]["runners_up"][:2]
    value = example("accepted_smaller_set")
    value["versions"][0].update(
        primaries=[recommendation("a")],
        runners_up=[],
        shortfall_reason="Only one supported option was found in this synthetic research.",
    )
    value = example("accepted_broader_pool_inference")
    value["versions"][0]["food_choices"][0]["supports"] = [support("h", "inferred_h")]
    value = example("accepted_shared_multiplace")
    value["versions"][0]["food_choices"][0]["supports"] = [
        support("a", "shared_noodles"),
        support("b", "shared_noodles"),
    ]
    value = example("accepted_unknown_gap_and_omitted_food")
    value["versions"][0]["primaries"][0]["food_claim_ids"] = ["unknown_a"]
    del value["versions"][0]["primaries"][1]["food_claim_ids"]
    value = example("accepted_new_snapshot_refinement", chain())
    new_snapshot = deepcopy(value["snapshots"][0])
    new_snapshot["snapshot_id"] = "snap_refined"
    value["snapshots"].append(new_snapshot)
    for version in value["versions"][1:]:
        version["research_snapshot_id"] = "snap_refined"
    value["versions"].reverse()

    value = example("rejected_four_primaries")
    value["versions"][0]["primaries"].append(recommendation("h"))
    value = example("rejected_five_runners")
    value["versions"][0]["runners_up"].append(recommendation("h"))
    value = example("rejected_duplicate_primary")
    value["versions"][0]["primaries"][1] = recommendation("a")
    value = example("rejected_duplicate_runner")
    value["versions"][0]["runners_up"][1] = recommendation("d")
    value = example("rejected_group_overlap")
    value["versions"][0]["runners_up"][0] = recommendation("a")
    value = example("rejected_unknown_candidate")
    value["versions"][0]["primaries"][0].update(
        place_id="branch_missing", food_claim_ids=[]
    )
    value = example("rejected_discovery_candidate")
    value["versions"][0]["primaries"][0].update(
        place_id="branch_lead", food_claim_ids=[]
    )
    value = example("rejected_missing_shortfall")
    value["versions"][0]["runners_up"] = []
    value = example("rejected_unknown_food_claim")
    value["versions"][0]["primaries"][0]["food_claim_ids"] = ["missing_claim"]
    value = example("rejected_wrong_branch_food_support")
    value["versions"][0]["food_choices"][0]["supports"] = [support("h", "menu_a")]
    value = example("rejected_unknown_only_food_support")
    value["versions"][0]["food_choices"][0]["supports"] = [support("a", "unknown_a")]
    value = example("rejected_price_only_food_support")
    value["versions"][0]["food_choices"][0]["supports"] = [support("a", "price_a")]
    value = example("rejected_choice_version_mismatch")
    value["versions"][0]["food_choices"][0]["version_id"] = "ver_other"
    value = example("rejected_stale_choice_repeated_local_id", chain())
    value["versions"][2]["lineage"] = {
        "kind": "food_choice",
        "parent_version_id": "ver_refined",
        "choice": {"version_id": "ver_initial", "choice_id": "noodles"},
    }
    value = example("rejected_missing_parent", chain())
    value["versions"][2]["lineage"]["parent_version_id"] = "ver_missing"
    value = example("rejected_missing_snapshot")
    value["versions"][0]["research_snapshot_id"] = "snap_missing"
    value = example("rejected_self_parent", chain())
    value["versions"][2]["lineage"]["parent_version_id"] = "ver_all"
    value = example("rejected_cyclic_lineage", chain())
    value["versions"][0]["lineage"] = {
        "kind": "all_options",
        "parent_version_id": "ver_all",
    }
    for name, payload in examples.items():
        (ROOT / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {len(examples)} synthetic recommendation payloads.")


if __name__ == "__main__":
    generate()

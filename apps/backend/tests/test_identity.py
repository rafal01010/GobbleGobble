import pytest
from app.identity import (
    IdentityAssertion,
    IdentityInputError,
    IdentityLead,
    IdentityReconciliation,
    IdentityRelation,
    OutletKind,
    reconcile_outlets,
)
from app.providers.geoapify import MealPlaceLead, Point, Provenance
from app.providers.web_research import URLCitation
from app.web_discovery import WebMealLead


def geographic(
    lead_id: str, provider_id: str, kind: OutletKind | None = "branch"
) -> IdentityLead:
    return IdentityLead(
        lead_id,
        MealPlaceLead(
            provider_id,
            Point(14.6, 121.0),
            "Same chain",
            "Shared mall address",
            ("catering.restaurant",),
            Provenance(),
        ),
        kind,
    )


def support(
    text: str = "The cited page identifies the two supplied records.",
) -> WebMealLead:
    return WebMealLead(
        text,
        0,
        0,
        0,
        len(text),
        (
            URLCitation(
                0,
                len(text),
                "Synthetic identity evidence",
                "https://example.com/outlets",
            ),
        ),
    )


def assertion(
    left: str, right: str, relation: IdentityRelation = "same_place"
) -> IdentityAssertion:
    return IdentityAssertion(
        left, right, relation, support(), "Explicit caller assessment"
    )


def members(result: IdentityReconciliation) -> list[list[str]]:
    return [[lead.lead_id for lead in outlet.leads] for outlet in result.outlets]


def test_exact_provider_duplicates_merge_and_keep_both_original_observations() -> None:
    first = geographic("first", "exact-id")
    second = geographic("second", "exact-id")
    result = reconcile_outlets((first, second))
    assert members(result) == [["first", "second"]]
    assert result.outlets[0].leads == (first, second)
    assert result.outlets[0].parent_identity_id is None


def test_similar_names_shared_coordinates_and_shared_citation_do_not_merge() -> None:
    web = support("Same chain at the same mall. Branch identity unknown.")
    result = reconcile_outlets(
        (
            geographic("north", "north"),
            geographic("south", "south"),
            IdentityLead("web", web),
        )
    )
    assert members(result) == [["north"], ["south"], ["web"]]
    assert result.outlets[2].kind is None


def test_explicit_scoped_cross_source_match_keeps_web_citation_and_provider_data() -> (
    None
):
    geo = geographic("geo", "geo-id")
    web = IdentityLead(
        "web", support("Official branch page names this exact outlet."), "branch"
    )
    match = assertion("geo", "web")
    result = reconcile_outlets((geo, web), (match,))
    assert members(result) == [["geo", "web"]]
    assert result.accepted_assertions == (match,)
    assert result.unresolved_assertions == ()
    assert result.outlets[0].leads[1].lead is web.lead
    assert result.outlets[0].leads[0].lead is geo.lead


def test_venue_and_two_stalls_keep_separate_ids_with_cited_containment() -> None:
    leads = (
        geographic("court", "court", "venue"),
        geographic("noodles", "noodles", "stall"),
        geographic("rice", "rice", "stall"),
    )
    links = (
        assertion("noodles", "court", "contained_by"),
        assertion("rice", "court", "contained_by"),
    )
    result = reconcile_outlets(leads, links)
    assert members(result) == [["court"], ["noodles"], ["rice"]]
    assert [item.parent_identity_id for item in result.outlets] == [
        None,
        "identity_1",
        "identity_1",
    ]
    assert result.accepted_assertions == links


@pytest.mark.parametrize("exact_provider", [False, True])
def test_kind_conflict_keeps_even_transitive_or_exact_id_matches_separate(
    exact_provider: bool,
) -> None:
    leads = (
        geographic("branch", "shared" if exact_provider else "branch", "branch"),
        geographic("unknown", "unknown", None),
        geographic("stall", "shared" if exact_provider else "stall", "stall"),
    )
    links = (assertion("branch", "unknown"), assertion("unknown", "stall"))
    result = reconcile_outlets(leads, links)
    assert members(result) == [["branch"], ["unknown"], ["stall"]]
    assert result.unresolved_assertions == links
    assert result.conflicts[0].lead_ids == ("branch", "unknown", "stall")


def test_transitive_parent_conflict_does_not_choose_first_matching_branch() -> None:
    leads = (
        geographic("a", "a", "stall"),
        geographic("bridge", "bridge", None),
        geographic("b", "b", "stall"),
        geographic("court-a", "court-a", "venue"),
        geographic("court-b", "court-b", "venue"),
    )
    same = (assertion("a", "bridge"), assertion("bridge", "b"))
    parents = (
        assertion("a", "court-a", "contained_by"),
        assertion("b", "court-b", "contained_by"),
    )
    result = reconcile_outlets(leads, same + parents)
    assert members(result) == [["a"], ["bridge"], ["b"], ["court-a"], ["court-b"]]
    assert [item.parent_identity_id for item in result.outlets] == [
        "identity_4",
        None,
        "identity_5",
        None,
        None,
    ]
    assert result.unresolved_assertions == same
    assert result.accepted_assertions == parents


def test_distinct_evidence_blocks_transitive_merge_regardless_of_link_order() -> None:
    leads = (geographic("a", "a"), geographic("b", "b"), geographic("c", "c"))
    links = (assertion("a", "b"), assertion("b", "c"), assertion("a", "c", "distinct"))
    for ordered in (links, tuple(reversed(links))):
        result = reconcile_outlets(leads, ordered)
        assert members(result) == [["a"], ["b"], ["c"]]
        assert [item.relation for item in result.accepted_assertions] == ["distinct"]


def test_unknown_parent_kind_remains_unresolved() -> None:
    leads = (geographic("stall", "stall", "stall"), geographic("venue", "venue", None))
    link = assertion("stall", "venue", "contained_by")
    result = reconcile_outlets(leads, (link,))
    assert result.unresolved_assertions == (link,)
    assert [item.parent_identity_id for item in result.outlets] == [None, None]
    assert result.conflicts[0].reason == "unknown_containment_kind"


def test_one_stall_with_conflicting_venue_claims_has_no_selected_parent() -> None:
    leads = (
        geographic("stall", "stall", "stall"),
        geographic("court-a", "court-a", "venue"),
        geographic("court-b", "court-b", "venue"),
    )
    links = (
        assertion("stall", "court-a", "contained_by"),
        assertion("stall", "court-b", "contained_by"),
    )
    result = reconcile_outlets(leads, links)
    assert members(result) == [["stall"], ["court-a"], ["court-b"]]
    assert result.outlets[0].parent_identity_id is None
    assert result.unresolved_assertions == links


def test_provider_ids_are_exact_and_uncited_assertions_are_rejected() -> None:
    leads = (geographic("a", "id"), geographic("b", " id "))
    assert members(reconcile_outlets(leads)) == [["a"], ["b"]]
    uncited = WebMealLead("Unsupported claim", 0, 0, 0, 17, ())
    link = IdentityAssertion("a", "b", "same_place", uncited, "No actual citation")
    with pytest.raises(IdentityInputError):
        reconcile_outlets(leads, (link,))


def test_identity_and_containment_of_same_records_conflict() -> None:
    leads = (geographic("a", "a", None), geographic("b", "b", None))
    links = (assertion("a", "b"), assertion("a", "b", "contained_by"))
    result = reconcile_outlets(leads, links)
    assert members(result) == [["a"], ["b"]]
    assert result.unresolved_assertions == links


def test_invalid_or_unscoped_assertion_fails_with_fixed_diagnostic() -> None:
    leads = (geographic("a", "a"), geographic("b", "b"))
    for links in ((assertion("a", "foreign"),), (assertion("a", "a"),)):
        with pytest.raises(
            IdentityInputError, match="^invalid identity reconciliation input$"
        ):
            reconcile_outlets(leads, links)
    with pytest.raises(IdentityInputError):
        reconcile_outlets((leads[0], leads[0]))


def test_empty_input_is_empty_and_repr_contains_no_evidence() -> None:
    assert reconcile_outlets(()).outlets == ()
    result = reconcile_outlets((geographic("private-lead", "private-id"),))
    assert repr(result) == "IdentityReconciliation()"

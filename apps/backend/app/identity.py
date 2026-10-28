from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.providers.geoapify import MealPlaceLead
from app.web_discovery import WebMealLead

type OutletKind = Literal["branch", "venue", "stall"]
type IdentityRelation = Literal["same_place", "distinct", "contained_by"]


class IdentityInputError(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid identity reconciliation input")


@dataclass(frozen=True)
class IdentityLead:
    lead_id: str = field(repr=False)
    lead: MealPlaceLead | WebMealLead = field(repr=False)
    kind: OutletKind | None = None


@dataclass(frozen=True)
class IdentityAssertion:
    left_lead_id: str = field(repr=False)
    right_lead_id: str = field(repr=False)
    relation: IdentityRelation
    support: WebMealLead = field(repr=False)
    explanation: str = field(repr=False)


@dataclass(frozen=True)
class ReconciledOutlet:
    identity_id: str = field(repr=False)
    leads: tuple[IdentityLead, ...] = field(repr=False)
    kind: OutletKind | None
    parent_identity_id: str | None = field(repr=False)


@dataclass(frozen=True)
class IdentityConflict:
    lead_ids: tuple[str, ...] = field(repr=False)
    reason: Literal["conflicting_identity", "unknown_containment_kind"]


@dataclass(frozen=True)
class IdentityReconciliation:
    outlets: tuple[ReconciledOutlet, ...] = field(repr=False)
    accepted_assertions: tuple[IdentityAssertion, ...] = field(repr=False)
    unresolved_assertions: tuple[IdentityAssertion, ...] = field(repr=False)
    conflicts: tuple[IdentityConflict, ...] = field(repr=False)


def _components(
    identifiers: tuple[str, ...],
    edges: list[tuple[str, str]],
    blocked: set[str],
) -> tuple[tuple[str, ...], ...]:
    neighbors: dict[str, set[str]] = {identifier: set() for identifier in identifiers}
    for left, right in edges:
        if left not in blocked and right not in blocked:
            neighbors[left].add(right)
            neighbors[right].add(left)
    groups: list[tuple[str, ...]] = []
    remaining = set(identifiers)
    for identifier in identifiers:
        if identifier not in remaining:
            continue
        reached = {identifier}
        pending = [identifier]
        while pending:
            for neighbor in neighbors[pending.pop()] - reached:
                reached.add(neighbor)
                pending.append(neighbor)
        remaining -= reached
        groups.append(tuple(item for item in identifiers if item in reached))
    return tuple(groups)


def reconcile_outlets(
    leads: tuple[IdentityLead, ...],
    assertions: tuple[IdentityAssertion, ...] = (),
) -> IdentityReconciliation:
    by_id = {lead.lead_id: lead for lead in leads}
    if len(by_id) != len(leads) or any(
        not isinstance(lead.lead_id, str)
        or not lead.lead_id.strip()
        or lead.kind not in (None, "branch", "venue", "stall")
        or not isinstance(lead.lead, (MealPlaceLead, WebMealLead))
        or (isinstance(lead.lead, MealPlaceLead) and not lead.lead.place_id.strip())
        for lead in leads
    ):
        raise IdentityInputError()
    for assertion in assertions:
        support = assertion.support
        if (
            assertion.left_lead_id not in by_id
            or assertion.right_lead_id not in by_id
            or assertion.left_lead_id == assertion.right_lead_id
            or assertion.relation not in ("same_place", "distinct", "contained_by")
            or not assertion.explanation.strip()
            or not support.excerpt.strip()
            or not support.citations
            or support.end_index - support.start_index != len(support.excerpt)
            or any(
                not support.start_index
                <= citation.start_index
                < citation.end_index
                <= support.end_index
                for citation in support.citations
            )
        ):
            raise IdentityInputError()

    edges = [
        (item.left_lead_id, item.right_lead_id)
        for item in assertions
        if item.relation == "same_place"
    ]
    provider_ids: dict[str, str] = {}
    for lead in leads:
        if isinstance(lead.lead, MealPlaceLead):
            previous = provider_ids.setdefault(lead.lead.place_id, lead.lead_id)
            if previous != lead.lead_id:
                edges.append((previous, lead.lead_id))

    blocked: set[str] = set()
    conflicts: list[IdentityConflict] = []
    while True:
        groups = _components(tuple(by_id), edges, blocked)
        membership = {item: group[0] for group in groups for item in group}
        newly_blocked: set[str] = set()
        for group in groups:
            kinds = {by_id[item].kind for item in group} - {None}
            parents = {
                membership[item.right_lead_id]
                for item in assertions
                if item.relation == "contained_by" and item.left_lead_id in group
            }
            contradiction = len(kinds) > 1 or len(parents) > 1
            contradiction |= any(
                item.relation in ("distinct", "contained_by")
                and item.left_lead_id in group
                and item.right_lead_id in group
                for item in assertions
            )
            if contradiction and len(group) > 1:
                newly_blocked.update(group)
                conflicts.append(IdentityConflict(group, "conflicting_identity"))
        if not newly_blocked:
            break
        blocked.update(newly_blocked)

    kinds_by_group = {
        group[0]: next(
            (by_id[item].kind for item in group if by_id[item].kind is not None),
            None,
        )
        for group in groups
    }
    parent_claims: dict[str, set[str]] = {}
    for assertion in assertions:
        if assertion.relation == "contained_by":
            child = membership[assertion.left_lead_id]
            parent = membership[assertion.right_lead_id]
            parent_claims.setdefault(child, set()).add(parent)
    parents_by_group: dict[str, str] = {}
    for child, parents in parent_claims.items():
        if len(parents) != 1 or child in parents:
            conflicts.append(IdentityConflict((child,), "conflicting_identity"))
            continue
        parent = next(iter(parents))
        if kinds_by_group[child] not in ("branch", "stall") or (
            kinds_by_group[parent] != "venue"
        ):
            conflicts.append(
                IdentityConflict((child, parent), "unknown_containment_kind")
            )
            continue
        parents_by_group[child] = parent

    accepted: list[IdentityAssertion] = []
    unresolved: list[IdentityAssertion] = []
    for assertion in assertions:
        left = membership[assertion.left_lead_id]
        right = membership[assertion.right_lead_id]
        valid = (
            (assertion.relation == "same_place" and left == right)
            or (assertion.relation == "distinct" and left != right)
            or (
                assertion.relation == "contained_by"
                and parents_by_group.get(left) == right
            )
        )
        (accepted if valid else unresolved).append(assertion)
    identity_ids = {
        group[0]: f"identity_{index}" for index, group in enumerate(groups, 1)
    }
    return IdentityReconciliation(
        tuple(
            ReconciledOutlet(
                identity_ids[group[0]],
                tuple(by_id[item] for item in group),
                kinds_by_group[group[0]],
                identity_ids[parents_by_group[group[0]]]
                if group[0] in parents_by_group
                else None,
            )
            for group in groups
        ),
        tuple(accepted),
        tuple(unresolved),
        tuple(conflicts),
    )

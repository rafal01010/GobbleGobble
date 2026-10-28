from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

type Source = Literal[
    "geoapify_geography",
    "public_google_reviews",
    "minimized_google_reviews",
    "ordinary_google",
    "grounding_lite",
    "unknown_publisher",
    "permitted_independent",
]
type Action = Literal["model", "store", "trace", "display"]
type Material = Literal["raw", "derived"]


@dataclass(frozen=True)
class SourcePolicy:
    model: bool
    store_raw: bool
    store_derived: bool
    trace_raw: bool
    trace_derived: bool
    display_raw: bool
    display_derived: bool
    attribution_required: bool


POLICIES = MappingProxyType(
    {
        "geoapify_geography": SourcePolicy(
            True, True, True, False, False, True, True, True
        ),
        "public_google_reviews": SourcePolicy(
            True, False, False, False, False, True, True, True
        ),
        "minimized_google_reviews": SourcePolicy(
            True, False, False, False, False, True, True, True
        ),
        "ordinary_google": SourcePolicy(
            False, False, False, False, False, False, False, True
        ),
        "grounding_lite": SourcePolicy(
            False, False, False, False, False, False, False, True
        ),
        "unknown_publisher": SourcePolicy(
            False, False, False, False, False, False, False, True
        ),
        "permitted_independent": SourcePolicy(
            False, True, True, False, False, True, True, True
        ),
    }
)


class SourceUseDenied(ValueError):
    pass


def require_source_use(
    sources: tuple[Source, ...],
    action: Action,
    *,
    material: Material = "raw",
    attributed: bool = False,
) -> None:
    for source in sources:
        policy = POLICIES[source]
        allowed = (
            policy.model
            if action == "model"
            else getattr(policy, f"{action}_{material}")
        )
        if not allowed or (
            action == "display" and policy.attribution_required and not attributed
        ):
            raise SourceUseDenied(f"{source} does not permit {action} for {material}")

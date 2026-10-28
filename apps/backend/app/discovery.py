from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal

from app.core.calls import Receipt, Stopped
from app.providers.geoapify import (
    PROVIDER_CREDITS,
    GeoapifyClient,
    GeoapifyInputError,
    LocationLead,
    MealPlaceLead,
    Point,
)


@dataclass(frozen=True)
class MealDiscoveryQuery:
    center: Point | LocationLead = field(repr=False)
    radius_m: int
    page_size: int = 20
    max_pages: int = 3
    start_offset: int = 0

    def __post_init__(self) -> None:
        for value, minimum, maximum in (
            (self.radius_m, 1, 5000),
            (self.page_size, 1, 20),
            (self.max_pages, 1, 5),
            (self.start_offset, 0, 10000),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise GeoapifyInputError()


@dataclass(frozen=True)
class DiscoveryPageReceipt:
    offset: int
    returned_count: int | None
    call: Receipt


@dataclass(frozen=True)
class MealDiscoveryBatch:
    credits: ClassVar[tuple[tuple[str, str], ...]] = PROVIDER_CREDITS
    query: MealDiscoveryQuery = field(repr=False)
    candidates: tuple[MealPlaceLead, ...] = field(repr=False)
    pages: tuple[DiscoveryPageReceipt, ...]
    stop_reason: Literal["short_page", "page_limit", "offset_limit", "provider_stopped"]
    next_offset: int | None


class MealDiscovery:
    def __init__(self, provider: GeoapifyClient) -> None:
        self._provider = provider

    async def discover(self, query: MealDiscoveryQuery) -> MealDiscoveryBatch:
        candidates: dict[str, MealPlaceLead] = {}
        pages: list[DiscoveryPageReceipt] = []
        offset = query.start_offset
        for _ in range(query.max_pages):
            result = await self._provider.nearby_places(
                query.center,
                radius_m=query.radius_m,
                limit=query.page_size,
                offset=offset,
            )
            if isinstance(result, Stopped):
                pages.append(DiscoveryPageReceipt(offset, None, result.receipt))
                return MealDiscoveryBatch(
                    query,
                    tuple(candidates.values()),
                    tuple(pages),
                    "provider_stopped",
                    offset,
                )
            pages.append(
                DiscoveryPageReceipt(offset, len(result.value), result.receipt)
            )
            for lead in result.value:
                candidates.setdefault(lead.place_id, lead)
            offset += len(result.value)
            if len(result.value) < query.page_size:
                return MealDiscoveryBatch(
                    query,
                    tuple(candidates.values()),
                    tuple(pages),
                    "short_page",
                    None,
                )
            if offset > 10000:
                return MealDiscoveryBatch(
                    query,
                    tuple(candidates.values()),
                    tuple(pages),
                    "offset_limit",
                    None,
                )
        return MealDiscoveryBatch(
            query, tuple(candidates.values()), tuple(pages), "page_limit", offset
        )

# Location, request and place contract

GG-004.01 implements vendor-independent Pydantic models in [search.py](../apps/backend/app/schemas/search.py). It is implemented, awaiting section verification at GG-007. These models define data shapes. They do not resolve locations, fetch routes, assess opening hours or expose an API.

## Requests and resolved locations

`SearchRequest` contains an origin, a travel constraint and an aware `eat_now_at` instant. The origin's `kind` preserves `current` or `chosen` after resolution. Its location carries finite latitude and longitude, a valid IANA timezone, optional display information and provider references. Coordinates have inclusive bounds of 90 degrees latitude and 180 degrees longitude in either direction.

The location timezone supplies the context for eating now. The timestamp identifies an instant and need not have the same UTC offset. Server local time is not a substitute for the destination timezone. The later API and location services own resolving inputs and capturing the current instant. Schema validation does not prove that a caller supplied today's time or the correct timezone for those coordinates.

Omitting `travel` selects walking with a 15-minute limit. Walking accepts any positive integer number of minutes. Driving uses exactly 30 integer minutes. Boolean, string and floating point limits are invalid. There is no country whitelist or invented upper walking limit.

Provider references contain an open provider name and that provider's identifier. Provider selection enums and wire types remain in their existing modules. Multiple references can identify the same location without making a vendor identifier the application's internal place ID.

## Places and practical uncertainty

`Place` has an internal `place_id` for a specific branch, venue or stall within a search. It carries a resolved location, optional name and branch label, and an optional `parent_place_id`. A stall can identify its containing venue while retaining its own ID. Missing names and addresses remain valid. A chain name alone does not identify a physical branch.

ID assignment, branch matching, deduplication and parent-reference integrity remain later normalization and relationship work. This child validates one place's structure. It does not verify provider identifiers or relationships among several places. Discovery leads remain separate from this normalized place contract.

An unknown journey contains no duration. An estimated journey contains a travel mode and finite nonnegative duration in seconds, with optional distance in metres. A duration estimate does not itself establish eligibility within a request's limit. Later code compares the estimate with the selected request and qualifies missing routes.

An unknown opening fact implies neither open nor closed. An assessed opening fact records an aware assessment instant and separates opening status from meal service. An open venue can have unknown or unavailable meal service. A resolved place's timezone provides the local context. These shapes do not establish factual support or freshness. Evidence relationships and source policies belong to GG-004.02 and later research tasks.

## Validation and examples

Models reject extra fields and freeze validated instances. Numeric fields reject invalid types and non-finite values. String validation errors hide input values in their formatted message. The models remain location-bearing data, not a safe diagnostics projection. Serialization and structured validation errors must stay within the later ownership and retention controls.

Literal illustrative payloads live under [docs/examples/search-contract/](examples/search-contract/). They are contract examples, not live provider observations. The GG-004.01 evidence record in [the plan](../IMPLEMENTATION_PLAN.md#gg-00401-define-location-request-and-place-types) identifies the actual checks and exported schemas.

The implementation uses [Pydantic discriminated unions](https://pydantic.dev/docs/validation/latest/concepts/unions/#discriminated-unions) for travel and practical states, and [field constraints](https://pydantic.dev/docs/validation/latest/concepts/fields/#field-constraints) for bounded values. Contradictory unknown and known journey fields fail parsing rather than becoming a consumer convention.

The section gate at GG-007 owns the broad valid/invalid contract exercise and full backend checks. GG-004.05 owns authoritative API contract exposure. Provider mapping remains GG-008. No provider or model call is required to import or validate these schemas.

## GG-004.01 payload inventory

| Literal payload | Expected result |
|---|---|
| [Current location](examples/search-contract/request-current-default-walking.json) | Accept. Preserve `current` and default to 15 minutes walking. |
| [Chosen location with walking](examples/search-contract/request-chosen-adjustable-walking.json) | Accept. Preserve `chosen`, provider ID and the 22-minute limit. |
| [Chosen location with driving](examples/search-contract/request-chosen-fixed-driving.json) | Accept. Preserve the fixed 30-minute limit and an aware UTC instant. |
| [Branch with unknown facts](examples/search-contract/place-branch-unknown.json) | Accept. Preserve branch label and both provider references. |
| [Stall with parent venue](examples/search-contract/place-stall-unknown.json) | Accept. Preserve its own ID and its containing venue's ID. |
| [Estimated journey and assessed opening](examples/search-contract/place-estimated-assessed.json) | Accept. Keep meal service unknown despite an open venue. |
| [Invalid coordinates](examples/search-contract/invalid-request-coordinates.json) | Reject latitude 91. |
| [Invalid timezone](examples/search-contract/invalid-request-timezone.json) | Reject an unresolvable timezone. |
| [Invalid driving limit](examples/search-contract/invalid-request-driving.json) | Reject floating point `30.0`. |
| [Contradictory unknown journey](examples/search-contract/invalid-place-unknown-journey-duration.json) | Reject a duration on an unknown journey. |

The local task check accepted and round-tripped the six valid payloads, rejected the four invalid payloads and exported both schemas. This is a cheap contract sanity check, not GG-007 section verification. The schemas and rerunnable check are retained under ignored `data/verification/GG-004.01/attempt-01/`.

From the repository root, with the backend environment already installed, rerun:

```sh
apps/backend/.venv/bin/python data/verification/GG-004.01/attempt-01/check_examples.py
```

The check reads only the illustrative payloads and schema module. It writes `SearchRequest.schema.json`, `Place.schema.json` and source/payload SHA-256 identities into that attempt directory. It exits with code 0 when its assertions pass and exits nonzero on a parsing, assertion or export error. It needs no `.env`, provider credentials or network access. `checks.txt` records the original successful output; `work.md` records design and verification decisions.

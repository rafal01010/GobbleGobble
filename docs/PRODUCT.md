# GobbleGobble product and delivery contract

This document records the agreed first version for GG-001.01. It describes required behavior, not capabilities already available. GG-001.01 is implemented, awaiting section verification at GG-003. Application delivery requires the real-browser acceptance walkthrough at GG-025.

## Purpose

GobbleGobble helps someone decide where to eat now, why a place is worth visiting, and what food to get there. It discovers and compares meal-serving places near the person's current location or another chosen location. Scope is worldwide, without a country whitelist. Uneven source coverage must remain visible rather than become a claim of worldwide completeness.

## Location entry

The journey starts with location entry. A person can use their current location or enter another location manually. Manual entry remains available when browser location permission is declined or unavailable. A chosen Japanese location must be usable without typing Japanese characters. Research can use local-language sources and preserve local place names. English output is a starting assumption.

## Travel limits and eating now

Walking defaults to 15 minutes and allows adjustment. Driving uses a 30-minute limit. Recommendations use journey estimates for the selected mode, rather than treating distance as proof of travel time. Route-unconfirmed alternatives remain distinguishable from places confirmed within the limit. Opening and service evidence must address eating now in the selected location's local time, with uncertainty visible.

## Discovery

Eligible places include restaurants, cafes, stalls, food courts, and takeaway outlets. Research must distinguish specific branches, venues, and individual stalls so the same option is not recommended twice. Missing menus or reviews alone do not disqualify a suitable place. New and unreviewed places remain eligible. Limited source coverage and unconfirmed availability must be distinguishable from a researched finding of no suitable matches.

## Recommendation results

Results contain up to three main recommendations and two to four worthwhile runners-up. A smaller set is acceptable when the actual research explains the shortfall. Each choice explains why to go and what food to get. Inline details expose relevant food information, available prices, complaints, journey estimates, opening qualifications, and source links. Place and Maps links help the person act on a choice. The interface must distinguish completed, limited, no-match, and failed outcomes.

## Food and price claims

Menus and prices are optional. An exact dish, menu item, or price must have supporting evidence. Suggested food inferred from cuisine or other place evidence must be labeled as inference. The result must not present an inferred suggestion as a confirmed menu item. Missing optional details remain explicit rather than become invented facts.

## Reviews and complaints

Mixed reviews remain eligible, with relevant complaints visible to the person choosing. Uniformly bad reviews can justify exclusion only when the evidence supports that conclusion. A small, selected, or biased negative sample alone does not establish that a place's reviews are uniformly bad. Results must state relevant sample limitations and distinguish observed complaints from a claim about all reviewers. The operational exclusion criterion remains work for GG-009 and GG-021.

## Food refinement

Food preferences appear after the initial results as a finite set of choices drawn from researched food options. Selecting a preference keeps the location and travel constraints. Refinement considers the broader researched pool, reuses usable evidence, and adds targeted research only where needed. The person can return to all options. Prior results stay understandable while refinement runs, and stale responses must not replace the active result version.

## Progress and recovery

Research runs durably with visible progress. Refreshing or reconnecting must recover the same run and permitted saved results without duplicate research solely because the browser reconnected. Retry and cancel controls must reflect actual job state. Worker restart, interrupted execution, and result expiry must have understandable recovery behavior. Cancellation must stop further work and prevent later publication for that cancelled run. Saved results remain subject to source retention rules.

## Evidence and privacy

Recommendations must provide attributable sources and truthful uncertainty about food, reviews, opening, and routes. Provider access does not itself establish permission to process content with a model, retain it, derive outputs, or trace it. Factual freshness and permitted retention are separate constraints. Permission to keep an old fact does not make it current, and a fresh fact may still have restricted retention. Source restrictions apply to raw content, derived data, events, and diagnostics. Searches use anonymous local ownership, and location and research data remain scoped to that ownership. Credentials must not appear in browser output, logs, or retained evidence.

## Interface

The interface follows the light Cursor-inspired [visual reference](../design_temp/DESIGN.md). It uses the agreed parchment canvas, bone cards, ink text, restrained accents, and 4px geometry where reference examples conflict. Results, waiting states, and recovery messages must remain readable at desktop and phone widths. Location entry, controls, inline details, and source links must support keyboard use and accessible focus behavior.

## Quality and timing

The delivery target is recommendations at least as useful as normal ChatGPT with web search under matched inputs. Comparison must use a genuine captured ChatGPT answer, with useful food detail, sources, practical accuracy, complaints, and unjustified omissions assessed. An API answer or a fabricated baseline cannot stand in for that comparison. Research has a two-to-three-minute expectation. Actual elapsed time, usage, and cost must be measured. An empty timeout response does not satisfy the target. The comparison protocol belongs to GG-001.03, and paired quality verification belongs to GG-023.

## Local delivery

The deliverable is a locally runnable application using real location and research providers and actual OpenAI calls. Its agreed stack is Python, SvelteKit, and SQLite, with an adaptive recommendation owner and a bounded research agent invoked as a tool through the actual OpenAI Agents SDK. Backend execution owns durable work. Single-host operation is a starting assumption. Test doubles and controlled jobs support development but cannot establish real-data delivery. Provider choice, permitted use, credentials, account access, and billing setup remain unresolved until their scheduled integration and feasibility tasks.

## Acceptance and handover

Delivery requires the GG-025 browser walkthrough against real services, after the preceding section gates pass. The person must be able to start the app from its runbook, search from current and chosen locations, adjust walking, and search within 30 minutes by driving. They must be able to inspect recommendations and sources, refine by a researched food choice, refresh, and stop and restart the local processes. Handover includes the checked local URL, exact setup and lifecycle commands, required service configuration, measured timing and cost evidence, and known limitations. The GG-023 ChatGPT comparison must remain passing on the delivered configuration.

## Exclusions and reference-project boundary

This is a separate project from CartCart. Discovery, comparison, evidence, and later refinement are relevant concepts from its [README](../../CartCart/README.md). Shopping flows, products, sellers, budgets, and CartCart's fixture-default delivery are outside this contract. The first version excludes accounts, open-ended chat, future-date meal planning, and deployment. Other undecided extras remain outside the first version until explicitly agreed.

## Requirement-to-paragraph mapping

Each target below identifies the single contract paragraph under that heading. The requirement names match [implementation plan Appendix E](../IMPLEMENTATION_PLAN.md#appendix-e-audit-agreed-requirement-coverage). This is a scope mapping, not application verification.

| Agreed requirement | Contract paragraph |
|---|---|
| End-to-end local app with real data, APIs, and OpenAI calls | [Local delivery](#local-delivery), [Acceptance and handover](#acceptance-and-handover) |
| Current location and another chosen location | [Location entry](#location-entry) |
| Worldwide scope without a geographic whitelist | [Purpose](#purpose) |
| Eat now using appropriate local time and practical evidence | [Travel limits and eating now](#travel-limits-and-eating-now) |
| Walking defaults to 15 minutes and is adjustable | [Travel limits and eating now](#travel-limits-and-eating-now) |
| Driving within 30 minutes | [Travel limits and eating now](#travel-limits-and-eating-now) |
| Restaurants, cafes, stalls, food courts, takeaway | [Discovery](#discovery) |
| Up to three main recommendations and two to four runners, with justified smaller sets | [Recommendation results](#recommendation-results) |
| Why to go and what food to get | [Recommendation results](#recommendation-results) |
| Menu/price optional and food inference clearly distinguished | [Food and price claims](#food-and-price-claims) |
| New/unreviewed places remain eligible | [Discovery](#discovery) |
| Mixed reviews remain eligible and complaints are shown | [Reviews and complaints](#reviews-and-complaints) |
| Uniformly bad review exclusion respects actual sample limits | [Reviews and complaints](#reviews-and-complaints) |
| Finite food preferences appear after results and derive from research | [Food refinement](#food-refinement) |
| Refinement uses the broader pool and reuses research with targeted extra work | [Food refinement](#food-refinement) |
| Quality at least as useful as normal ChatGPT with web search | [Quality and timing](#quality-and-timing) |
| Two-to-three-minute research expectation | [Quality and timing](#quality-and-timing) |
| Python, SvelteKit, OpenAI, and actual Agents SDK agents as tools | [Local delivery](#local-delivery) |
| Light Cursor-inspired colors and readable desktop/mobile screens | [Interface](#interface) |
| Durable runs, live progress, restart/retry/cancel, and consistent versions | [Progress and recovery](#progress-and-recovery), [Food refinement](#food-refinement) |
| Source attribution, truthful uncertainty, retention, and no secret leakage | [Evidence and privacy](#evidence-and-privacy) |
| Separate project and no silent CartCart feature inheritance | [Exclusions and reference-project boundary](#exclusions-and-reference-project-boundary) |
| Future-date planning and other undecided extras stay outside version one | [Exclusions and reference-project boundary](#exclusions-and-reference-project-boundary) |

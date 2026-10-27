# Product requirements

GobbleGobble is being built to help people choose where to eat now. This document defines the intended first version. The current repository contains a frontend shell, durable-run infrastructure and bounded provider probes. The complete dining journey and real recommendation publication are not yet available. See the [README](../README.md) for the current entry points.

## Purpose

The app helps someone choose a place, understand why it is worth visiting and decide what food to get. It compares meal-serving places near the person's current location or another chosen location. It has no country whitelist. Uneven source coverage must remain visible rather than become a claim of worldwide completeness.

## Location entry

The journey starts with location. A person can use their current location or enter another location manually. Manual entry remains available when browser location permission is declined or unavailable. A chosen Japanese location must be usable without typing Japanese characters. Research can use local-language sources and preserve local place names. Initial output is English.

There is no general question or food-preference textbox in the normal entry flow. Location and travel controls supply the initial constraints. Researched food choices appear only after results.

## Travel limits and eating now

Walking defaults to 15 minutes and allows adjustment. Driving uses a 30-minute limit. Recommendations use estimates for the selected mode rather than treating distance as proof of travel time. Route-unconfirmed alternatives remain distinct from places confirmed within the limit. Opening and meal-service evidence must address eating now in the selected location's local time, with uncertainty visible.

## Discovery

Eligible places include restaurants, cafes, stalls, food courts and takeaway outlets. Research must distinguish branches, venues and individual stalls so the same option is not recommended twice. Missing menus or reviews alone do not disqualify a place. New and unreviewed places remain eligible. Limited coverage and unconfirmed availability must remain distinct from a researched finding of no suitable matches.

## Recommendation results

Results contain up to three main recommendations and two to four worthwhile runners-up. A smaller set is acceptable when research explains the shortfall. Each choice explains why to go and what food to get. Inline details expose relevant food information, available prices, complaints, journey estimates, opening qualifications and source links. Place and Maps links help the person act. Completed, limited, no-match and failed outcomes must be distinguishable.

## Food and price claims

Menus and prices are optional. An exact dish, menu item or price needs supporting evidence. Food suggested from cuisine or other place evidence must be labeled as inference. An inferred suggestion must not appear as a confirmed menu item. Missing optional details remain explicit rather than become invented facts.

## Reviews and complaints

Google reviews are a primary source for relevant matched places. The intended path is the selected anonymous Google review adapter, independently of the geography provider. Places with zero Google reviews remain eligible. Empty or omitted samples, uncertain branch matches, restricted use and access failures need distinct outcomes and labeled permitted fallback. Fallback keeps its own source identity and does not silently replace working Google access.

Google review display, model use, attribution and retention follow the [Google-review contract](INTEGRATIONS.md#public-review-model-input). Bounded access and review-to-model experiments do not establish a complete production integration. Both primary retrieval and fallback need end-to-end evidence before delivery.

Mixed reviews remain eligible, with relevant complaints visible. Uniformly bad reviews can justify exclusion only when the evidence supports that conclusion. A small, selected or biased negative sample does not establish uniformly bad reception. Results must disclose relevant sample limits. The operational exclusion criterion and its production validation remain incomplete.

## Food refinement

After initial results, the app offers a finite set of food choices drawn from the research. A choice preserves location and travel constraints. Refinement considers the broader researched pool, reuses usable evidence and adds targeted research where needed. The person can return to all options. Prior results stay understandable during refinement, and stale responses must not replace the active version.

## Progress and recovery

Research runs durably with visible progress. Refreshing or reconnecting must recover the same run and permitted saved results without duplicate research caused by reconnection. Retry and cancel controls must reflect actual state. Worker restart, interruption and result expiry need understandable recovery. Cancellation must prevent later publication for that run. Source retention rules still apply to saved results.

## Evidence and privacy

Recommendations need attributable sources and truthful uncertainty about food, reviews, opening and routes. Access does not by itself establish permission to process content with a model, retain it, derive outputs or trace it. Freshness and retention are separate. Permission to keep an old fact does not make it current, and a fresh fact may still have restricted retention.

Source restrictions apply to raw content, derived data, events and diagnostics. Searches use anonymous local ownership. Location and research data stay scoped to that owner. Credentials must not appear in browser output, logs or retained evidence.

## Interface

The interface uses a light parchment canvas, bone surfaces, ink text, restrained accents and 4px corners. It remains light under system dark mode. Results, waiting states and recovery messages must read well on desktop and phone widths. Controls, details and source links must support keyboard use and visible focus.

Normal diners should see language about their meal and choices. Do not expose internal agent or tool names, prompts, loops, model temperature, context budgets, run identifiers, schemas, stack traces or raw exceptions. Progress should explain useful work, such as checking nearby places or comparing food options. Errors should state the effect and the next useful action. Technical diagnostics belong in separate developer tools and logs.

The language requirement applies to diner-facing screens. Preserve source meaning and attribution. Technical contributor documentation can use precise engineering terms. The normal experience should keep one main task prominent and reveal details when useful, with minimal input and no scattered implementation notes.

## Quality and timing

The target is recommendations at least as useful as normal ChatGPT with web search under matched inputs. Comparison needs a genuine captured ChatGPT answer. An API answer or fabricated baseline cannot replace it. Human assessment covers food detail, sources, practical accuracy, complaints and unjustified omissions. The [comparison protocol](EVALUATION.md) defines matching and acceptance. No passing paired quality result has been established.

Research has a two-to-three-minute expectation. Measure elapsed time, usage and cost. An empty timeout response does not satisfy the target.

## Local delivery

The intended deliverable runs locally with real location and research providers and actual OpenAI calls. The stack is Python, SvelteKit and SQLite. One recommendation owner can use a bounded research agent as a tool through the OpenAI Agents SDK. Backend execution owns durable work. Single-host operation is the initial target. Controlled jobs support development but do not establish real-data delivery.

## Acceptance and handover

Delivery requires a real-browser walkthrough against real services. A person must be able to follow the runbook, search from current and chosen locations, adjust walking and search within 30 minutes by driving. They must be able to inspect recommendations and sources, refine by a researched food choice, refresh, and stop and restart local processes. Handover needs exact setup and lifecycle commands, service configuration, measured timing and cost, known limits and a passing quality comparison on the delivered configuration.

## Exclusions and reference-project boundary

GobbleGobble is a separate project. Discovery, comparison, evidence and refinement are relevant concepts from its reference application, CartCart. Shopping products, sellers, budgets and shopping-intake forms are outside this product. The first version also excludes accounts, open-ended chat, future-date meal planning and deployment.

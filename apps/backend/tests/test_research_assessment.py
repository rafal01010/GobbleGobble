import json
from dataclasses import replace

import httpx2
import pytest
from openai import AsyncOpenAI
from test_research_agent import model_response, setup_values

from app.agents.research import ProposedFinding, ResearchContribution, ReviewProposal
from app.agents.research_assessment import assess_review_contribution
from app.core.calls import AttemptCalls, Bounds


def contribution(source):
    sample = source.sample
    review = sample.reviews[0]
    finding = ProposedFinding(
        review_label="r0",
        topic="service",
        polarity="negative",
        wording="paraphrase",
        statement="The reviewer found the service slow.",
        basis=None,
    )
    return ResearchContribution(
        source.place_id,
        sample.branch.source_url,
        (ReviewProposal(finding, review, False),),
        (),
        0,
        None,
        sample.sort,
        len(sample.reviews),
        sample.branch.reported_total,
        (),
    )


@pytest.mark.asyncio
async def test_model_assesses_review_in_context_without_storing_it():
    settings, _, source = setup_values()
    request_data = []

    def respond(request):
        request_data.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json=model_response(
                {
                    "decisions": [
                        {
                            "proposal_id": "p0",
                            "review_label": "r0",
                            "verdict": "supported",
                            "rationale": "The reviewer says service was slow.",
                        }
                    ]
                }
            ),
        )

    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=2, total_seconds=5.0))
        result = await assess_review_contribution(
            calls, client, source, contribution(source)
        )
    assert result.status == "assessed"
    assert result.selected == 1 and result.deferred == 0
    assert result.decisions[0].verdict == "supported"
    request = request_data[0]
    assert request["store"] is False
    assert "The chicken was excellent, but the service was slow" in json.dumps(
        request, ensure_ascii=False
    )
    assert calls.receipts[0].input_sources == ("public_google_reviews",)
    assert calls.research_budget.records[0].actual_input == 10
    assert "reviewer says" not in repr(result)
    assert "chicken" not in repr(calls.receipts[0].safe_record())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decisions",
    [
        [],
        [
            {
                "proposal_id": "p0",
                "review_label": "r1",
                "verdict": "supported",
                "rationale": "yes",
            }
        ],
        [
            {
                "proposal_id": "p1",
                "review_label": "r0",
                "verdict": "supported",
                "rationale": "yes",
            }
        ],
        [
            {
                "proposal_id": "p0",
                "review_label": "r0",
                "verdict": "supported",
                "rationale": "",
            }
        ],
    ],
)
async def test_invalid_assessment_is_not_a_supported_finding(decisions):
    settings, _, source = setup_values()
    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    200, json=model_response({"decisions": decisions})
                )
            )
        ),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=2, total_seconds=5.0))
        result = await assess_review_contribution(
            calls, client, source, contribution(source)
        )
    assert result.status == "incomplete"
    assert result.reason == "invalid_assessment"
    assert result.decisions == ()


@pytest.mark.asyncio
async def test_wrong_source_review_cannot_be_sent_for_assessment():
    settings, _, source = setup_values()
    proposal_set = contribution(source)
    swapped = replace(
        proposal_set.proposals[0],
        review=replace(proposal_set.proposals[0].review, text="different branch"),
    )
    proposal_set = replace(proposal_set, proposals=(swapped,))
    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: pytest.fail("wrong source must not be sent")
            )
        ),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=2, total_seconds=5.0))
        result = await assess_review_contribution(calls, client, source, proposal_set)
    assert result.status == "unavailable"
    assert result.reason == "source_mismatch"
    assert calls.receipts == ()


@pytest.mark.asyncio
async def test_contrast_personal_quote_and_japanese_reach_assessment_together():
    settings, request, source = setup_values()
    text = "娘は「おいしい」と言いました。 The noodles were not cold, but service was slow."
    review = replace(source.sample.reviews[0], text=text)
    source = replace(source, sample=replace(source.sample, reviews=(review,)))
    findings = [
        {
            "review_label": "r0",
            "topic": "food",
            "polarity": "positive",
            "wording": "quotation",
            "statement": "おいしい",
            "basis": None,
        },
        {
            "review_label": "r0",
            "topic": "service",
            "polarity": "negative",
            "wording": "quotation",
            "statement": "service was slow",
            "basis": None,
        },
        {
            "review_label": "r0",
            "topic": "food",
            "polarity": "negative",
            "wording": "paraphrase",
            "statement": "The noodles were cold.",
            "basis": None,
        },
    ]
    decisions = [
        {
            "proposal_id": "p0",
            "review_label": "r0",
            "verdict": "supported",
            "rationale": "The daughter says the food is delicious.",
        },
        {
            "proposal_id": "p1",
            "review_label": "r0",
            "verdict": "supported",
            "rationale": "The service complaint appears in the review.",
        },
        {
            "proposal_id": "p2",
            "review_label": "r0",
            "verdict": "unsupported",
            "rationale": "The review says the noodles were not cold.",
        },
    ]
    requests = []
    responses = iter([{"findings": findings, "gaps": []}, {"decisions": decisions}])

    def respond(req):
        requests.append(json.loads(req.content))
        return httpx2.Response(200, json=model_response(next(responses)))

    from app.agents.research import invoke_review_tool, prepare_review_tool

    async with AsyncOpenAI(
        api_key="synthetic-openai-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        max_retries=0,
    ) as client:
        calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=5.0))
        result = await invoke_review_tool(
            prepare_review_tool(calls, client, request, source), "Assess the review"
        )
        assessed = await assess_review_contribution(
            calls, client, source, result.contributions[0]
        )
    assert result.status == "available"
    assert [item.verdict for item in assessed.decisions] == [
        "supported",
        "supported",
        "unsupported",
    ]
    assert text in json.dumps(requests[0], ensure_ascii=False)
    assert text in json.dumps(requests[1], ensure_ascii=False)
    assert all(
        item.finding.review_label == "r0" for item in result.contributions[0].proposals
    )

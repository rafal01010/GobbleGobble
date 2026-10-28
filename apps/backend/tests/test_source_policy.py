import pytest

from app.core.calls import AttemptCalls, Bounds, CallSpec, Completed, Parsed, Stopped
from app.core.settings import Settings
from app.core.source_policy import SourceUseDenied, require_source_use


@pytest.fixture
def settings():
    return Settings.model_construct(
        mode="test",
        location_provider="geoapify",
        discovery_provider="geoapify",
        route_provider="geoapify",
        web_provider="openai",
        owner_model="gpt-4o",
        research_model="gpt-4o-mini",
        geographic_timeout_seconds=1.0,
        model_timeout_seconds=1.0,
    )


@pytest.mark.asyncio
async def test_public_reviews_reach_model_but_other_google_content_does_not(settings):
    calls = AttemptCalls(settings, Bounds(max_requests=2, total_seconds=2.0))

    async def invoke(seconds):
        return Parsed("I did not like the ramen. 太高い")

    def spec(source):
        return CallSpec(
            capability="model",
            use="model_inference",
            model_role="research",
            input_sources=(source,),
        )

    allowed = await calls.run(spec("public_google_reviews"), invoke)
    denied = await calls.run(spec("ordinary_google"), invoke)
    assert isinstance(allowed, Completed)
    assert allowed.value == "I did not like the ramen. 太高い"
    assert isinstance(denied, Stopped)
    assert denied.receipt.outcome == "source_blocked"


def test_review_storage_tracing_and_attribution_rules():
    for material in ("raw", "derived"):
        for action in ("store", "trace"):
            with pytest.raises(SourceUseDenied):
                require_source_use(
                    ("public_google_reviews",), action, material=material
                )
        with pytest.raises(SourceUseDenied):
            require_source_use(("public_google_reviews",), "display", material=material)
        require_source_use(
            ("public_google_reviews",), "display", material=material, attributed=True
        )
    with pytest.raises(SourceUseDenied):
        require_source_use(("geoapify_geography", "unknown_publisher"), "model")

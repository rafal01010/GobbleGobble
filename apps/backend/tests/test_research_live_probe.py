import runpy
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/local/verify-research-live.py"
select_target_location = runpy.run_path(str(SCRIPT))["select_target_location"]


def lead(name, address):
    return SimpleNamespace(name=name, formatted_address=address)


def test_selects_only_one_matching_location():
    intended = lead("Toyo Eatery", "2316 Chino Roces, Makati")
    wrong = lead("Another restaurant", "2316 Chino Roces, Makati")
    assert select_target_location([wrong, intended], "Toyo Eatery", None) is intended
    assert select_target_location([wrong], "Toyo Eatery", None) is None
    assert select_target_location([intended, intended], "Toyo Eatery", None) is None
    assert (
        select_target_location([intended], "Toyo Eatery", "Different address") is None
    )
    assert (
        select_target_location([intended], "Toyo Eatery", intended.formatted_address)
        is intended
    )


def test_known_branch_address_rejects_another_same_name():
    known = "Toyo Eatery, 2316 Chino Roces Avenue Extension, Makati, 1231 Metro Manila, Philippines"
    assert (
        select_target_location(
            [lead("Toyo Eatery", "99 Other Street, Makati")], "Toyo Eatery", known
        )
        is None
    )
    intended = lead("Toyo Eatery", known)
    assert select_target_location([intended], "Toyo Eatery", known) is intended


def test_discovery_handoff_requires_unique_coincident_lead():
    discover_target = runpy.run_path(str(SCRIPT))["discover_target"]

    def location(lat, lon):
        return SimpleNamespace(
            name="Toyo Eatery", point=SimpleNamespace(latitude=lat, longitude=lon)
        )

    origin = location(14.5312541, 121.0226562)
    intended = location(14.5312541, 121.0226562)
    distant = location(14.5312541, 121.0227562)
    assert discover_target([intended], origin) is intended
    assert discover_target([distant], origin) is None
    assert discover_target([intended, intended], origin) is None


def test_source_website_requires_openstreetmap_provenance():
    get_website = runpy.run_path(str(SCRIPT))["source_website"]
    lead = SimpleNamespace(
        provenance=SimpleNamespace(
            datasource={
                "sourcename": "other",
                "raw": {"website": "https://example.com"},
            }
        )
    )
    assert get_website(lead) is None
    lead.provenance.datasource["sourcename"] = "openstreetmap"
    assert get_website(lead) == "https://example.com"

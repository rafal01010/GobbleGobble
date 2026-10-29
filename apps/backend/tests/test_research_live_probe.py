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

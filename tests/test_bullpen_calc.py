from app.services.bullpen_calc import _normalize_pitching_note


def test_normalize_pitching_note_allows_mlb_combined_notes():
    note = _normalize_pitching_note("(L, 3-4)(BS, 5)")

    assert note == "(L, 3-4)(BS, 5)"


def test_normalize_pitching_note_bounds_unexpected_long_values():
    raw_note = "x" * 80

    note = _normalize_pitching_note(raw_note)

    assert note == "x" * 64


def test_normalize_pitching_note_returns_none_for_blank_values():
    assert _normalize_pitching_note("   ") is None

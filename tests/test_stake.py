import pytest
from optimizer.stake import kelly_fraction, quarter_kelly_stake, apply_exposure_cap


def test_kelly_zero_when_fair_priced():
    # p == 1/d  ->  p*d == 1  ->  no edge  ->  f* == 0
    assert kelly_fraction(0.5, 2.0) == pytest.approx(0.0)


def test_kelly_positive_with_shopped_uplift():
    # p=0.50, d=2.04  ->  edge = 0.02  ->  f* = 0.02/1.04
    assert kelly_fraction(0.50, 2.04) == pytest.approx(0.02 / 1.04, rel=1e-9)


def test_kelly_clamped_to_zero_when_negative():
    # p=0.68, d=1.40  ->  p*d = 0.952 < 1  ->  clamp to 0
    assert kelly_fraction(0.68, 1.40) == 0.0


def test_kelly_zero_when_decimal_odds_not_above_one():
    assert kelly_fraction(0.9, 1.0) == 0.0
    assert kelly_fraction(0.9, 0.5) == 0.0


def test_quarter_kelly_unit_scaling():
    # 4% edge on a 2.0x parlay -> f*=0.04 -> 0.25*0.04*100 = 1.0u
    assert quarter_kelly_stake(0.52, 2.0) == pytest.approx(0.25 * 0.04 * 100, rel=1e-9)
    # 2% edge -> ~0.5u
    assert quarter_kelly_stake(0.51, 2.0) == pytest.approx(0.5, rel=1e-9)


def test_quarter_kelly_zero_when_no_edge():
    assert quarter_kelly_stake(0.5, 2.0) == 0.0


def test_exposure_cap_noop_under_cap():
    assert apply_exposure_cap([0.3, 0.4, 0.5], 5.0) == [0.3, 0.4, 0.5]


def test_exposure_cap_scales_proportionally_when_over():
    out = apply_exposure_cap([4.0, 4.0], 5.0)
    assert sum(out) == pytest.approx(5.0)
    assert out[0] == out[1] == pytest.approx(2.5)


def test_exposure_cap_all_zero_unchanged():
    assert apply_exposure_cap([0.0, 0.0], 5.0) == [0.0, 0.0]


from optimizer.stake import size_slate


def test_size_slate_global_cap_groups_all_sports_together():
    # two big raw stakes across different sports, global cap 5u -> summed & scaled
    rows = [(1, 0.52, 2.0, "mlb"), (2, 0.52, 2.0, "nfl")]  # each raw ~1.0u
    out = size_slate(rows, exposure_cap=1.0, cap_scope="global")
    assert sum(out.values()) == pytest.approx(1.0)
    assert out[1] == out[2] == pytest.approx(0.5)


def test_size_slate_per_sport_cap_is_independent():
    rows = [(1, 0.52, 2.0, "mlb"), (2, 0.52, 2.0, "nfl")]  # each raw ~1.0u
    out = size_slate(rows, exposure_cap=1.0, cap_scope="per-sport")
    # each sport has its own 1u budget -> neither is scaled
    assert out[1] == pytest.approx(1.0)
    assert out[2] == pytest.approx(1.0)


def test_size_slate_zero_edge_cards_get_zero():
    rows = [(1, 0.5, 2.0, "mlb"), (2, 0.52, 2.0, "mlb")]  # card 1 no edge, card 2 ~1u
    out = size_slate(rows, exposure_cap=5.0)
    assert out[1] == 0.0
    assert out[2] == pytest.approx(1.0)

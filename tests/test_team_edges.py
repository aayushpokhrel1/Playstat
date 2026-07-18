import pytest
from modeling.team_edges import best_side


def test_best_side_picks_larger_edge():
    # model 0.75 under vs devig implied 0.60 under -> under edge 0.15 wins
    side, mp, ip, edge = best_side(0.25, 0.75, 0.40, 0.60)
    assert side == "under"
    assert edge == pytest.approx(0.15)
    assert mp == pytest.approx(0.75)


def test_best_side_over_when_over_edge_larger():
    side, mp, ip, edge = best_side(0.70, 0.30, 0.50, 0.50)
    assert side == "over"
    assert edge == pytest.approx(0.20)


def test_best_side_ties_go_to_over():
    side, *_ = best_side(0.5, 0.5, 0.4, 0.4)
    assert side == "over"

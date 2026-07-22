import api.main as main


def test_parlay_builder_returns_object_with_truncation_fields(monkeypatch):
    legs = [
        {"game_id": 1, "kind": "player", "label": "A over 0.5", "side": "over",
         "decimal_odds": 1.3, "american_odds": -333, "market_prob": 0.77,
         "model_prob": None, "line_value": 0.5, "player_id": 10, "stat_type": "hits", "market": None},
        {"game_id": 2, "kind": "player", "label": "B under 0.5", "side": "under",
         "decimal_odds": 1.25, "american_odds": -400, "market_prob": 0.80,
         "model_prob": 0.79, "line_value": 0.5, "player_id": 11, "stat_type": "runs", "market": None},
    ]
    monkeypatch.setattr(main.builder, "load_legs", lambda engine, floor: legs)

    out = main.parlay_builder(min_prob=0.5)

    assert out.constructions and out.constructions[0].n_legs == 2
    assert out.truncated is False
    assert out.exhaustive is True
    assert isinstance(out.nodes_searched, int) and out.nodes_searched > 0
    # No EV/edge field leaked into the payload.
    assert not hasattr(out.constructions[0], "ev")

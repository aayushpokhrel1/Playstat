import pytest
from optimizer.stake import size_and_persist


class _Result:
    def __init__(self, rows): self._rows = rows
    def fetchall(self): return self._rows


class _Conn:
    def __init__(self, select_rows, sink):
        self._select_rows = select_rows
        self._sink = sink
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, stmt, params=None):
        sql = str(stmt).lower()
        if sql.strip().startswith("select"):
            return _Result(self._select_rows)
        # UPDATE ... record (parlay_id -> stake)
        self._sink.append(params)
        return _Result([])


class _Engine:
    def __init__(self, select_rows):
        self.select_rows = select_rows
        self.updates = []
    def begin(self): return _Conn(self.select_rows, self.updates)


def test_pass_writes_quarter_kelly_stakes_and_caps():
    # card 1: ~1u edge, card 2: no edge -> 0. Row shape: (parlay_id, p, d, sport)
    rows = [(1, 0.52, 2.0, "mlb"), (2, 0.5, 2.0, "mlb")]
    eng = _Engine(rows)
    n = size_and_persist(eng, exposure_cap=5.0)
    by_pid = {u["pid"]: float(u["stake"]) for u in eng.updates}
    assert n == 2
    assert by_pid[1] == pytest.approx(1.0)
    assert by_pid[2] == 0.0


def test_pass_is_idempotent_recompute_from_scratch():
    rows = [(1, 0.52, 2.0, "mlb")]
    eng = _Engine(rows)
    size_and_persist(eng)
    first = {u["pid"]: float(u["stake"]) for u in eng.updates}
    eng.updates.clear()
    size_and_persist(eng)
    second = {u["pid"]: float(u["stake"]) for u in eng.updates}
    assert first == second

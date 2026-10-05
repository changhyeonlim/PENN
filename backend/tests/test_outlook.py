from penn.tools.outlook import Outlook


def make(th, tp, bh, bp, w):
    return Outlook(th, tp, bh, bp, w, [], {})


def test_small_sample_gives_counts_only():
    o = make(3, 10, 50, 1000, 10)
    assert not o.enough_history and o.ratio is None
    assert "too few" in o.note()


def test_same_as_base_rate():
    o = make(9, 100, 90, 1000, 150)
    assert abs(o.ratio - 1.0) < 1e-9
    assert "same" in o.note()


def test_higher_than_base():
    o = make(30, 150, 90, 1000, 150)
    assert o.ratio > 2 and "Higher" in o.note()

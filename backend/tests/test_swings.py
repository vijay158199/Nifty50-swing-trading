from tests.conftest import make_candles


def test_finds_simple_high_and_low(session_start):
    from app.strategy.swings import find_swings

    # window=1: need 1 bar either side. Bar index 2 is a clear swing high,
    # bar index 5 is a clear swing low.
    rows = [
        (100, 101, 99, 100),
        (100, 102, 100, 101),
        (101, 105, 101, 104),  # swing high @105
        (104, 104, 102, 103),
        (103, 103, 100, 101),
        (101, 101, 95, 96),  # swing low @95
        (96, 98, 96, 97),
        (97, 98, 96, 97),
    ]
    candles = make_candles(rows, session_start, 1)
    swings = find_swings(candles, window=1)

    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    assert any(s.price == 105 for s in highs)
    assert any(s.price == 95 for s in lows)


def test_no_swings_on_monotonic_series(session_start):
    from app.strategy.swings import find_swings

    rows = [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)]
    candles = make_candles(rows, session_start, 1)
    swings = find_swings(candles, window=2)
    assert swings == []


def test_confirmed_swings_as_of_ignores_future_bars(session_start):
    from app.strategy.swings import confirmed_swings_as_of, find_swings

    rows = [
        (100, 101, 99, 100),
        (100, 102, 100, 101),
        (101, 105, 101, 104),  # potential swing high @105 (needs 1 bar after to confirm)
        (104, 104, 102, 103),
        (103, 103, 100, 101),
    ]
    candles = make_candles(rows, session_start, 1)

    # As of index 2 (the bar itself), we can't yet know it's a swing (no bar after it).
    assert confirmed_swings_as_of(candles, window=1, as_of_index=2) == []
    # As of index 3, we have one bar after -> confirmed.
    swings = confirmed_swings_as_of(candles, window=1, as_of_index=3)
    assert any(s.price == 105 for s in swings)
    # Sanity: full-series detection agrees.
    assert find_swings(candles, window=1) == swings

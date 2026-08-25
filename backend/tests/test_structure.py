from tests.conftest import make_candles


def test_bos_high_when_break_continues_bullish(session_start):
    """After a HIGH liquidity interaction, the first break is a swing HIGH
    (before any swing LOW breaks) -> BOS High, bullish continuation."""
    from app.strategy.structure import detect_bos_choch
    from app.strategy.types import Direction, LiquiditySide, StructureType

    rows = [
        (100, 101, 99, 100),
        (100, 105, 100, 102),  # swing high candidate H=105
        (102, 102, 101, 101),  # confirms bar1
        (101, 110, 101, 108),  # closes above 105 -> BOS High
    ]
    candles = make_candles(rows, session_start, 1)

    event = detect_bos_choch(candles, LiquiditySide.HIGH, window=1, search_until=candles.index[-1])

    assert event is not None
    assert event.structure_type is StructureType.BOS
    assert event.direction is Direction.BUY
    assert event.liquidity_side is LiquiditySide.HIGH
    assert event.ts == candles.index[3]
    assert event.signal_label == "BOS High"


def test_choch_high_requires_a_confirming_second_break(session_start):
    """After a HIGH liquidity interaction, price instead breaks a swing LOW
    first - a reversal CANDIDATE, not yet a signal - and is only emitted
    once a SECOND, newer swing low is also broken (the confirming Lower
    Low)."""
    from app.strategy.structure import detect_bos_choch
    from app.strategy.types import Direction, LiquiditySide, StructureType

    rows = [
        (100, 101, 99, 100),
        (100, 101, 95, 97),   # swing low candidate L=95
        (97, 98, 96, 97),     # confirms bar1 low
        (97, 97, 90, 92),     # closes below 95 -> candidate reversal, NOT yet emitted
        (92, 93, 91, 92),     # confirms bar3 as a newer swing low (L=90)
        (92, 92, 85, 87),     # closes below 90 -> confirms the reversal -> CHOCH High
    ]
    candles = make_candles(rows, session_start, 1)

    event = detect_bos_choch(candles, LiquiditySide.HIGH, window=1, search_until=candles.index[-1])

    assert event is not None
    assert event.structure_type is StructureType.CHOCH
    assert event.direction is Direction.SELL
    assert event.liquidity_side is LiquiditySide.HIGH
    assert event.ts == candles.index[5]
    assert event.signal_label == "CHOCH High"


def test_choch_high_returns_none_if_never_confirmed(session_start):
    """The initial break against the liquidity side opens a candidate, but
    if nothing ever breaks a newer swing low to confirm it, no signal is
    produced - a lone break isn't enough."""
    from app.strategy.structure import detect_bos_choch
    from app.strategy.types import LiquiditySide

    rows = [
        (100, 101, 99, 100),
        (100, 101, 95, 97),
        (97, 98, 96, 97),
        (97, 97, 90, 92),   # candidate reversal opens here
        (92, 95, 91, 94),   # chops around, never breaks a new low
    ]
    candles = make_candles(rows, session_start, 1)

    assert detect_bos_choch(candles, LiquiditySide.HIGH, window=1, search_until=candles.index[-1]) is None


def test_bos_low_when_break_continues_bearish(session_start):
    """Mirror of BOS High: after a LOW liquidity interaction, the first
    break is a swing LOW -> BOS Low, bearish continuation."""
    from app.strategy.structure import detect_bos_choch
    from app.strategy.types import Direction, LiquiditySide, StructureType

    rows = [
        (100, 101, 99, 100),
        (100, 100, 95, 98),   # swing low candidate L=95
        (98, 99, 96, 97),     # confirms bar1
        (97, 97, 90, 92),     # closes below 95 -> BOS Low
    ]
    candles = make_candles(rows, session_start, 1)

    event = detect_bos_choch(candles, LiquiditySide.LOW, window=1, search_until=candles.index[-1])

    assert event is not None
    assert event.structure_type.value == "BOS"
    assert event.direction.value == "SELL"
    assert event.liquidity_side is LiquiditySide.LOW
    assert event.signal_label == "BOS Low"


def test_choch_low_requires_a_confirming_second_break(session_start):
    """Mirror of CHOCH High: after a LOW liquidity interaction, price
    instead breaks a swing HIGH first, confirmed by a newer, higher swing
    high (the confirming Higher High) -> CHOCH Low, bullish reversal."""
    from app.strategy.structure import detect_bos_choch
    from app.strategy.types import Direction, LiquiditySide, StructureType

    rows = [
        (100, 101, 99, 100),
        (100, 105, 100, 103),  # swing high candidate H=105
        (103, 104, 102, 103),  # confirms bar1 high
        (103, 110, 103, 108),  # closes above 105 -> candidate reversal
        (108, 109, 107, 108),  # confirms bar3 as a newer swing high (H=110)
        (108, 115, 108, 113),  # closes above 110 -> confirms the reversal -> CHOCH Low
    ]
    candles = make_candles(rows, session_start, 1)

    event = detect_bos_choch(candles, LiquiditySide.LOW, window=1, search_until=candles.index[-1])

    assert event is not None
    assert event.structure_type is StructureType.CHOCH
    assert event.direction is Direction.BUY
    assert event.liquidity_side is LiquiditySide.LOW
    assert event.signal_label == "CHOCH Low"


def test_bos_rejected_when_confirmation_candle_has_a_weak_body(session_start, monkeypatch):
    """settings.require_displacement_candle (default True) rejects a close
    beyond the swing point when the breaking candle's own body is weak
    relative to recent bars - it isn't real displacement, just a close that
    happens to be beyond the level. Same swing setup as
    test_bos_high_when_break_continues_bullish, but bar3's body (0.7) is
    well under 1.5x the ~1.0 average body of bars 0-2."""
    from app.config import settings
    from app.strategy.structure import detect_bos_choch
    from app.strategy.types import LiquiditySide

    rows = [
        (100, 101, 99, 100),          # body 0
        (100, 105, 100, 102),         # swing high candidate H=105, body 2
        (102, 102, 101, 101),         # confirms bar1, body 1
        (104.8, 106, 104.5, 105.5),   # closes above 105 (105.5) but body only 0.7
    ]
    candles = make_candles(rows, session_start, 1)

    monkeypatch.setattr(settings, "require_displacement_candle", True)
    monkeypatch.setattr(settings, "displacement_lookback_bars", 20)
    monkeypatch.setattr(settings, "displacement_body_multiplier", 1.5)
    assert detect_bos_choch(candles, LiquiditySide.HIGH, window=1, search_until=candles.index[-1]) is None

    monkeypatch.setattr(settings, "require_displacement_candle", False)
    event = detect_bos_choch(candles, LiquiditySide.HIGH, window=1, search_until=candles.index[-1])
    assert event is not None
    assert event.structure_type.value == "BOS"


def test_returns_none_when_nothing_breaks(session_start):
    from app.strategy.structure import detect_bos_choch
    from app.strategy.types import LiquiditySide

    rows = [(100 + (i % 3), 101 + (i % 3), 99 + (i % 3), 100 + (i % 3)) for i in range(10)]
    candles = make_candles(rows, session_start, 1)

    assert detect_bos_choch(candles, LiquiditySide.HIGH, window=1, search_until=candles.index[-1]) is None

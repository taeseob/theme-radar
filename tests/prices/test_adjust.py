import pytest

from theme_radar.prices import adjust
from theme_radar.prices.sources.naver import SiseBar
from theme_radar.prices.sources.yf import Bar


@pytest.mark.parametrize("price, valid", [
    (1_999, True), (2_005, True), (2_003, False), (19_990, True), (19_995, False),
    (54_400, True), (54_450, False), (248_500, True), (248_000, True), (89_750, False), (1_812_000, True), (0, False),
])
def test_krx_tick_size(price, valid):
    assert adjust.is_valid_krx_price(price) is valid


def bar(d, close, no_trade=False, volume=100):
    return SiseBar(d, 0 if no_trade else close, 0 if no_trade else close, 0 if no_trade else close, close, 0 if no_trade else volume)


def test_kr_rows_prefers_stored_raw_and_rejects_off_tick_raw():
    bars = [bar("2026-01-02", 1000), bar("2026-01-05", 2010), bar("2026-01-06", 2020)]
    rows, rejected = adjust.kr_rows(bars, {"2026-01-05": 2013, "2026-01-06": 2020}, {"2026-01-02": 10_000})
    assert rejected == ["2026-01-05"]
    assert [(r.trade_date, r.close_raw, r.adj_factor) for r in rows] == [("2026-01-02", 10_000, 0.1), ("2026-01-06", 2020, 1.0)]


def test_kr_rows_carries_raw_close_over_no_trade_day():
    bars = [bar("2026-07-15", 87_300), bar("2026-07-16", 87_300, no_trade=True)]
    rows, _ = adjust.kr_rows(bars, {"2026-07-15": 87_300}, {})
    assert rows[1].close_raw == 87_300 and rows[1].trade_status == "NO_TRADE"


def test_kr_delisted_raw_stops_at_first_off_tick_price_going_back():
    bars = [bar("2025-01-02", 12_345), bar("2025-01-03", 10_000), bar("2025-01-06", 10_010)]
    assert adjust.kr_delisted_raw(bars) == {"2025-01-02": None, "2025-01-03": 10_000, "2025-01-06": 10_010}


def test_us_rows_restore_raw_close_with_later_splits():
    bars = [Bar("2025-11-13", 115.0, 1, 0.0), Bar("2025-11-14", 111.217, 1, 0.0), Bar("2025-11-17", 110.29, 1, 10.0)]
    rows = adjust.us_rows(bars, {})
    assert [(r.close_raw, round(r.adj_factor, 6)) for r in rows] == [(1150.0, 0.1), (1112.17, 0.1), (110.29, 1.0)]


def test_us_rows_keep_stored_raw_and_recompute_factor():
    rows = adjust.us_rows([Bar("2025-11-14", 111.217, 1, 0.0)], {"2025-11-14": 1112.17})
    assert rows[0].close_raw == 1112.17 and rows[0].adj_factor == pytest.approx(0.1, rel=1e-5)


@pytest.mark.parametrize("ratio, expected", [
    (10, ("SPLIT", 10.0)), (2, ("SPLIT", 2.0)), (0.3333333333333333, ("REVERSE_SPLIT", 1 / 3)), (0.2, ("REVERSE_SPLIT", 0.2)),
    (2.39, ("UNKNOWN", None)), (0.9535, ("UNKNOWN", None)), (1.01, ("UNKNOWN", None)), (1.0, ("UNKNOWN", None)),
])
def test_classify_share_ratio(ratio, expected):
    action_type, share_ratio = adjust.classify_share_ratio(ratio)
    assert action_type == expected[0]
    assert share_ratio == (None if expected[1] is None else pytest.approx(expected[1]))


def test_kr_actions_ignores_rounding_jitter():
    rows = [adjust.PriceRow("2025-01-02", 354, 10.0, 3540, None, "NORMAL"),
            adjust.PriceRow("2025-01-03", 356, 9.9888, 3556, None, "NORMAL")]   # 0.1% 흔들림
    assert adjust.kr_actions(rows) == []


def test_factor_changed_tolerance():
    assert not adjust.factor_changed(0.1000001, 0.1, 5440, "KR")
    assert adjust.factor_changed(0.2, 0.1, 5440, "KR")
    assert adjust.factor_changed(None, 0.1, 5440, "KR")
    assert not adjust.factor_changed(None, None, 5440, "KR")


def test_fill_shares_as_of_split_and_approximation_before_first_observation():
    rows = [("2025-09-01", 0.1), ("2025-10-01", 0.1), ("2025-11-17", 1.0), ("2026-01-05", 1.0)]
    obs = [adjust.Observation("2025-09-30", 423_732_334), adjust.Observation("2025-12-31", 4_222_162_150)]
    shares = adjust.fill_shares(rows, obs, [("2025-11-17", 10.0)])
    assert shares == [
        423_732_334,          # 첫 관측 전: 첫 관측치 × 계수 비(0.1 / 0.1)
        423_732_334,
        4_237_323_340,        # 분할 후, 다음 공시 전: × 10
        4_222_162_150,
    ]


def test_fill_shares_applies_split_between_pre_start_filing_and_start():
    """TSCO: 2024-09-28 공시(분할 전) 뒤 2024-12-20 5:1 분할. 수집 시작일 이후 행에도 분할을 곱해야 한다."""
    rows = [("2025-01-02", 1.0), ("2025-01-27", 1.0)]
    obs = [adjust.Observation("2024-09-28", 106_800_000), adjust.Observation("2025-01-27", 535_000_000)]
    assert adjust.fill_shares(rows, obs, [("2024-12-20", 5.0)]) == [534_000_000, 535_000_000]


def test_fill_shares_without_observation_is_none():
    assert adjust.fill_shares([("2025-01-02", 1.0)], [], []) == [None]


def test_drop_implausible_share_observations_by_market_cap_range():
    prices = [("2025-01-02", 10.58), ("2025-11-05", 15.09)]
    obs = [adjust.Observation("2024-06-30", 650_000_000),            # 가격 이력 전 관측치는 가장 이른 가격으로 본다
           adjust.Observation("2025-05-13", 1_000),                  # PSKY 합병 전 지주회사: 시총 1만 달러
           adjust.Observation("2025-11-05", 1_071_666_977),
           adjust.Observation("2026-05-05", 481_790_955_000_000)]    # AEP 자리수 오류: 시총 수천조 달러
    keep, dropped = adjust.drop_implausible(obs, prices, adjust.PLAUSIBLE_MARKET_CAP["US"])
    assert [o.as_of_date for o in dropped] == ["2025-05-13", "2026-05-05"]
    assert [o.as_of_date for o in keep] == ["2024-06-30", "2025-11-05"]
    assert adjust.drop_implausible(obs, [], (1e8, 2e13)) == (obs, [])


def test_fill_shares_uses_previous_row_when_only_observation_is_after_last_trading_day():
    """HD현대미포: 폐지일 전날(2025-12-14) 관측치 하나뿐이고 마지막 거래일은 2025-12-12다."""
    rows = [("2025-06-02", 0.5), ("2025-12-12", 1.0)]
    assert adjust.fill_shares(rows, [adjust.Observation("2025-12-14", 39_942_149)], []) == [19_971_074, 39_942_149]

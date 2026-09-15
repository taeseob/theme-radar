"""docs/07 §9.5 회귀 테스트. 2026-09-15~16 실측 응답에서 기업행위 전후 구간만 잘라 둔 픽스처로 돈다.

라이브러리 버전을 올릴 때도 이 테스트를 통과해야 한다.
"""
import json
from pathlib import Path

import pytest

from theme_radar.prices import adjust
from theme_radar.prices.sources import naver, sec, yf

FIXTURES = Path(__file__).parent.parent / "fixtures" / "prices"
KR = json.loads((FIXTURES / "kr_corporate_actions.json").read_text(encoding="utf-8"))
US = json.loads((FIXTURES / "us_bars.json").read_text(encoding="utf-8"))


def kr_series(code):
    bars = [naver.SiseBar(*b) for b in KR[code]["bars"]]
    rows, rejected = adjust.kr_rows(bars, KR[code]["raw"], {})
    assert rejected == []
    return {r.trade_date: r for r in rows}, adjust.kr_actions(list(rows_of(rows)))


def rows_of(rows):
    return rows.values() if isinstance(rows, dict) else rows


def us_series(ticker):
    bars = [yf.Bar(*b) for b in US[ticker]]
    return {r.trade_date: r for r in adjust.us_rows(bars, {})}, adjust.us_actions(bars)


# ------------------------------------------------------------ KR

def test_kr_001080_split_10_to_1():
    rows, actions = kr_series("001080")
    assert rows["2026-03-06"].close_raw == 54_400
    assert rows["2026-03-06"].adj_factor == pytest.approx(0.1)
    assert [(a.ex_date, a.action_type, a.share_ratio) for a in actions] == [("2026-03-09", "SPLIT", 10.0)]


def test_kr_011300_reverse_split_1_to_10():
    rows, actions = kr_series("011300")
    assert rows["2026-04-27"].close_raw == 354
    assert rows["2026-04-27"].adj_factor == pytest.approx(10)
    assert [(a.ex_date, a.action_type) for a in actions] == [("2026-04-30", "REVERSE_SPLIT")]
    assert actions[0].share_ratio == pytest.approx(0.1)


def test_kr_000670_split_then_bonus_issue():
    rows, actions = kr_series("000670")
    assert rows["2025-04-24"].adj_factor == pytest.approx(0.097284, abs=1e-6)
    assert [(a.ex_date, a.action_type) for a in actions] == [("2025-04-25", "SPLIT"), ("2025-12-29", "UNKNOWN")]


def test_kr_068270_stock_dividend():
    rows, actions = kr_series("068270")
    assert rows["2026-06-02"].adj_factor == pytest.approx(0.953167, abs=1e-6)
    assert [(a.ex_date, a.action_type, a.share_ratio) for a in actions] == [("2026-06-04", "UNKNOWN", None)]


def test_kr_207940_spin_off_boundary_is_trading_resumption():
    rows, actions = kr_series("207940")
    assert rows["2025-10-27"].adj_factor == pytest.approx(1.471744, abs=1e-6)
    assert [(a.ex_date, a.action_type, a.share_ratio) for a in actions] == [("2025-11-24", "UNKNOWN", None)]


def test_kr_332290_reverse_split_1_to_5_keeps_market_cap():
    rows, actions = kr_series("332290")
    assert [(a.ex_date, a.action_type) for a in actions] == [("2026-09-14", "REVERSE_SPLIT")]
    shares = adjust.fill_shares(
        [(d, rows[d].adj_factor) for d in ("2026-09-11", "2026-09-14")],
        [adjust.Observation("2026-09-11", 39_696_247), adjust.Observation("2026-09-14", 7_939_249)],
        [("2026-09-14", actions[0].share_ratio)])
    cap_before = rows["2026-09-11"].close_raw * shares[0]
    cap_after = rows["2026-09-14"].close_raw * shares[1]
    assert shares == [39_696_247, 7_939_249]
    assert cap_after / cap_before == pytest.approx(rows["2026-09-14"].close_adj / rows["2026-09-11"].close_adj, rel=1e-3)


# ------------------------------------------------------------ US

@pytest.mark.parametrize("ticker, day_before, factor, ratio", [
    ("NFLX", "2025-11-14", 0.1, 10.0),
    ("ORLY", "2025-06-09", 1 / 15, 15.0),
    ("NOW", "2025-12-17", 0.2, 5.0),
])
def test_us_integer_splits(ticker, day_before, factor, ratio):
    rows, actions = us_series(ticker)
    assert rows[day_before].adj_factor == pytest.approx(factor, rel=1e-4)
    assert [(a.action_type, a.share_ratio) for a in actions] == [("SPLIT", ratio)]


def test_us_nflx_raw_close_restored():
    rows, _ = us_series("NFLX")
    assert rows["2025-11-14"].close_raw == 1112.17


def test_us_dd_spin_off_is_not_applied_to_shares_but_reverse_split_is():
    _, actions = us_series("DD")
    assert [(a.ex_date, a.action_type) for a in actions] == [("2025-11-03", "UNKNOWN"), ("2026-06-24", "REVERSE_SPLIT")]
    assert actions[0].share_ratio is None
    assert actions[1].share_ratio == pytest.approx(1 / 3)


def test_us_hon_spin_off_and_share_count_from_sec():
    rows, actions = us_series("HON")
    assert [(a.ex_date, a.action_type) for a in actions] == [("2025-10-30", "UNKNOWN"), ("2026-06-29", "UNKNOWN")]
    facts, multi_class = sec.parse_shares((FIXTURES / "sec_hon_shares.json").read_bytes())
    assert not multi_class
    dates = sorted(rows)
    shares = adjust.fill_shares([(d, rows[d].adj_factor) for d in dates],
                                [adjust.Observation(f.end, f.shares) for f in facts], [])
    by_date = dict(zip(dates, shares))
    assert by_date["2026-06-29"] == 633_653_119
    assert by_date["2026-06-30"] == 316_940_010


def test_us_ko_close_is_not_dividend_adjusted():
    rows, _ = us_series("KO")
    assert rows["2025-01-02"].close_raw == 61.84

"""status가 관측값과 할 일을 제대로 뽑는지 확인한다 (docs/09 §4.6)."""
from theme_radar import status
from theme_radar.calc import aggregate
from theme_radar.db import transaction
from theme_radar.jobs import run_job

# 합성 시장의 가격은 2025-01까지다. 실행 시점과 한참 떨어져 있어 "뒤처졌다" 경로가 자연히 걸린다
LAST_TRADE_DATE = "2025-01-17"


def test_reports_markets_and_tells_what_to_run(con, market, db_path):
    report = status.collect(con, db_path)
    kr = next(m for m in report.markets if m.market == "KR")
    assert kr.securities == 4 and kr.last_price_date == LAST_TRADE_DATE   # 폐지된 C는 편입이 닫혀 빠진다
    assert kr.price_lag_days == 0                 # 수집한 달력의 마지막 날과 가격이 같다
    assert report.schema == report.schema_latest
    assert any("prices backfill --market US" in item for item in report.todo)   # US는 가격이 하나도 없다

    text = status.render(report)
    assert LAST_TRADE_DATE in text and "할 일" in text


def test_pending_recalc_and_aggregation_show_up(con, market, db_path):
    with run_job(con, "aggregate", "KR") as run:
        aggregate.run(con, run, "KR", "2025-01-16", full=True, log=lambda message: None)
    with transaction(con):
        con.execute("INSERT INTO recalc_request (market_code, from_date, reason, requested_at) "
                    "VALUES ('KR', '2025-01-02', 'MAPPING', '2025-01-20T00:00:00Z')")

    report = status.collect(con, db_path)
    kr = next(m for m in report.markets if m.market == "KR")
    assert kr.pending_recalc == 1
    # 2025-01의 기준일은 2024-12의 마지막 거래일인데 합성 달력에 없다. 월 기간은 아직 확정되지 않는다
    assert {p.period_type for p in kr.periods} == {"W"}
    assert all(p.calc_version == report.calc_version for p in kr.periods)
    assert any("재계산 요청 1건" in item for item in report.todo)
    assert "aggregate KR" in status.render(report)


def test_pad_counts_display_width_not_characters():
    """한글은 터미널에서 두 칸이다. 글자 수로 채우면 표가 어긋난다."""
    assert status._pad("시장", 6) == "시장" + " " * 2
    assert status._pad("KR", 6) == "KR" + " " * 4

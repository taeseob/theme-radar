from theme_radar.calc.periods import build_periods


def test_week_periods_follow_iso_week_year_and_chain_base_dates():
    days = ["2026-12-24", "2026-12-28", "2026-12-30", "2027-01-04", "2027-01-05"]
    weeks = [p for p in build_periods("KR", days, today="2027-01-06") if p.period_type == "W"]
    assert [(p.period_id, p.period_seq, p.base_date, p.end_date, p.trading_days, p.is_closed) for p in weeks] == [
        ("2026-W52", 1, None, "2026-12-24", 1, True),
        ("2026-W53", 2, "2026-12-24", "2026-12-30", 2, True),      # 2026년은 ISO 기준 53주까지 있다
        ("2027-W01", 3, "2026-12-30", "2027-01-05", 2, False),     # 캘린더 종료일이 아직 지나지 않았다
    ]


def test_month_periods_and_empty_periods_are_skipped():
    days = ["2025-01-02", "2025-01-31", "2025-03-04"]              # 2월은 거래일이 없다
    months = [p for p in build_periods("KR", days, today="2025-03-05") if p.period_type == "M"]
    assert [(p.period_id, p.base_date, p.end_date, p.cal_start, p.cal_end) for p in months] == [
        ("2025-01", None, "2025-01-31", "2025-01-01", "2025-01-31"),
        ("2025-03", "2025-01-31", "2025-03-04", "2025-03-01", "2025-03-31"),
    ]

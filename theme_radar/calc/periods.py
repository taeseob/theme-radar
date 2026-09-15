"""기간 캘린더(period_calendar) 생성 (docs/03 §1).

거래일 캘린더에서 ISO 주와 역월 기간을 만든다. 기간의 기준일은 캘린더 경계가 아니라 거래일이다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from theme_radar.db import transaction


@dataclass(frozen=True)
class Period:
    market_code: str
    period_type: str
    period_id: str
    period_seq: int
    cal_start: str
    cal_end: str
    base_date: str | None
    end_date: str
    trading_days: int
    is_closed: bool


def _week_bounds(d: date) -> tuple[str, str, str]:
    year, week, weekday = d.isocalendar()
    monday = d - timedelta(days=weekday - 1)
    return f"{year}-W{week:02d}", monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def _month_bounds(d: date) -> tuple[str, str, str]:
    first = d.replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return first.strftime("%Y-%m"), first.isoformat(), (next_month - timedelta(days=1)).isoformat()


def build_periods(market: str, trading_days: list[str], today: str) -> list[Period]:
    """거래일에서 주·월 기간을 만든다. 거래일이 없는 기간은 만들지 않는다.

    period_seq는 존재하는 기간마다 1씩 증가하고, base_date는 직전 기간의 마지막 거래일이다.
    캘린더 종료일이 아직 지나지 않은 기간은 잠정(is_closed = False)이다.
    """
    periods: list[Period] = []
    for period_type, bounds in (("W", _week_bounds), ("M", _month_bounds)):
        grouped: dict[str, tuple[str, str, list[str]]] = {}
        for day in sorted(trading_days):
            period_id, start, end = bounds(date.fromisoformat(day))
            grouped.setdefault(period_id, (start, end, []))[2].append(day)
        previous_end = None
        for seq, (period_id, (cal_start, cal_end, days)) in enumerate(sorted(grouped.items(), key=lambda kv: kv[1][2][-1]), 1):
            periods.append(Period(market, period_type, period_id, seq, cal_start, cal_end, previous_end,
                                  days[-1], len(days), cal_end < today))
            previous_end = days[-1]
    return periods


def rebuild(con: sqlite3.Connection, market: str, today: str) -> list[Period]:
    days = [r[0] for r in con.execute("SELECT trade_date FROM trading_calendar WHERE market_code = ? ORDER BY trade_date", (market,))]
    periods = build_periods(market, days, today)
    with transaction(con):
        con.execute("DELETE FROM period_calendar WHERE market_code = ?", (market,))
        con.executemany(
            "INSERT INTO period_calendar (market_code, period_type, period_id, period_seq, cal_start, cal_end, base_date, "
            "end_date, trading_days, is_closed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(p.market_code, p.period_type, p.period_id, p.period_seq, p.cal_start, p.cal_end, p.base_date, p.end_date,
              p.trading_days, int(p.is_closed)) for p in periods])
    return periods


def load(con: sqlite3.Connection, market: str, period_type: str) -> list[Period]:
    return [Period(*r[:8], r[8], bool(r[9])) for r in con.execute(
        "SELECT market_code, period_type, period_id, period_seq, cal_start, cal_end, base_date, end_date, trading_days, "
        "is_closed FROM period_calendar WHERE market_code = ? AND period_type = ? ORDER BY period_seq", (market, period_type))]

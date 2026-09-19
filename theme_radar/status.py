"""`status` 명령 (docs/09 §4.6).

수동으로 쓰는 도구라 화면을 열기 전에 "지금 보이는 값이 최신인가, 아니면 무엇을 먼저 돌려야 하는가"를
한 화면으로 답하는 것이 목적이다. 판단은 하지 않고 관측값과 그로부터 나오는 할 일만 보여 준다.
"""
from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from theme_radar.calc import CALC_VERSION
from theme_radar.prices.context import MARKET_TZ, CLOSE_TIME
from theme_radar.db.migrate import list_migrations, schema_version

STALE_DAYS = 1          # 최근 거래일보다 이만큼 더 뒤처지면 수집이 밀린 것으로 본다
RUNS_SHOWN = 6


def _today(market: str) -> date:
    from datetime import datetime

    return datetime.now(MARKET_TZ[market]).date()


def _market_closed(market: str) -> bool:
    from datetime import datetime

    now = datetime.now(MARKET_TZ[market])
    return (now.hour, now.minute) >= CLOSE_TIME


@dataclass
class PeriodState:
    period_type: str
    period_id: str | None
    is_provisional: bool
    calculated_at: str | None
    calc_version: str | None


@dataclass
class MarketState:
    market: str
    securities: int
    last_price_date: str | None
    last_calendar_date: str | None
    periods: list[PeriodState] = field(default_factory=list)
    pending_recalc: int = 0
    todo: list[str] = field(default_factory=list)

    @property
    def price_lag_days(self) -> int | None:
        """최근 거래일 대비 가격이 며칠 뒤처졌는가. 거래일 기준이 아니라 달력일 기준의 근사다."""
        if not (self.last_price_date and self.last_calendar_date):
            return None
        return (date.fromisoformat(self.last_calendar_date) - date.fromisoformat(self.last_price_date)).days


@dataclass
class Status:
    db_path: Path
    db_bytes: int
    schema: int
    schema_latest: int
    calc_version: str
    markets: list[MarketState] = field(default_factory=list)
    runs: list[sqlite3.Row] = field(default_factory=list)
    warnings: list[sqlite3.Row] = field(default_factory=list)

    @property
    def todo(self) -> list[str]:
        return [item for market in self.markets for item in market.todo]


def _periods(con: sqlite3.Connection, market: str) -> list[PeriodState]:
    rows = con.execute(
        "SELECT u.period_type, u.period_id, u.is_provisional, u.calculated_at, u.calc_version "
        "FROM universe_period_stat u JOIN universe n USING (universe_code) "
        "WHERE n.market_code = ? AND u.period_seq = ("
        "  SELECT MAX(period_seq) FROM universe_period_stat x "
        "  WHERE x.universe_code = u.universe_code AND x.period_type = u.period_type) "
        "ORDER BY u.period_type DESC", (market,)).fetchall()
    return [PeriodState(r["period_type"], r["period_id"], bool(r["is_provisional"]),
                        r["calculated_at"], r["calc_version"]) for r in rows]


def _market(con: sqlite3.Connection, market: str) -> MarketState:
    securities = con.execute(
        "SELECT COUNT(DISTINCT m.security_id) FROM universe_membership m JOIN universe u USING (universe_code) "
        "WHERE u.market_code = ? AND m.valid_to = '9999-12-31'", (market,)).fetchone()[0]
    last_price = con.execute("SELECT MAX(p.trade_date) FROM price_daily p JOIN security s USING (security_id) "
                             "WHERE s.market_code = ?", (market,)).fetchone()[0]
    # 수집한 거래일 달력의 마지막 날. 오늘 장이 아직 안 끝났으면 그 전 거래일까지가 기대치다
    limit = _today(market).isoformat() if _market_closed(market) else (_today(market) - timedelta(days=1)).isoformat()
    last_calendar = con.execute("SELECT MAX(trade_date) FROM trading_calendar WHERE market_code = ? AND trade_date <= ?",
                                (market, limit)).fetchone()[0]
    state = MarketState(market, securities, last_price, last_calendar, _periods(con, market))
    state.pending_recalc = con.execute(
        "SELECT COUNT(*) FROM recalc_request WHERE market_code = ? AND processed_at IS NULL", (market,)).fetchone()[0]

    command = f".venv\\Scripts\\python -m theme_radar daily --market {market}"
    if not last_price:
        state.todo.append(f"{market}: 가격이 없다 → .venv\\Scripts\\python -m theme_radar prices backfill --market {market}")
    elif (state.price_lag_days or 0) > STALE_DAYS:
        state.todo.append(f"{market}: 가격이 {last_price}까지다. 거래일 {last_calendar}보다 뒤처졌다 → {command}")
    if state.pending_recalc:
        state.todo.append(f"{market}: 재계산 요청 {state.pending_recalc}건이 대기 중이다 → {command}")
    stale_calc = [p for p in state.periods if p.calc_version and p.calc_version != CALC_VERSION]
    if stale_calc:
        state.todo.append(f"{market}: 집계가 옛 calc_version({stale_calc[0].calc_version})으로 남아 있다 "
                          f"→ .venv\\Scripts\\python -m theme_radar aggregate --market {market} --full")
    return state


def collect(con: sqlite3.Connection, db_file: Path) -> Status:
    con.row_factory = sqlite3.Row
    status = Status(db_path=db_file, db_bytes=db_file.stat().st_size if db_file.exists() else 0,
                    schema=schema_version(con), schema_latest=len(list_migrations()), calc_version=CALC_VERSION)
    status.markets = [_market(con, row[0]) for row in
                      con.execute("SELECT market_code FROM market ORDER BY market_code")]
    status.runs = con.execute(
        "SELECT run_id, job_name, market_code, status, started_at, finished_at, row_count, message "
        "FROM batch_run ORDER BY run_id DESC LIMIT ?", (RUNS_SHOWN,)).fetchall()
    # 가장 최근 실행들이 남긴 경고만 센다. 지난 실행에서 이미 해결된 경고까지 세면 목록이 늘기만 한다
    status.warnings = con.execute(
        "SELECT rule_code, COUNT(*) AS n FROM validation_result "
        "WHERE passed = 0 AND run_id IN (SELECT MAX(run_id) FROM batch_run WHERE status <> 'RUNNING' "
        "                                GROUP BY job_name, market_code) "
        "GROUP BY rule_code ORDER BY rule_code").fetchall()
    return status


def _size(n: int) -> str:
    return f"{n / 1024 ** 3:.1f}GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f}MB"


def _duration(started: str | None, finished: str | None) -> str:
    if not (started and finished):
        return "—"
    from datetime import datetime

    seconds = (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
    return f"{seconds / 60:.0f}분" if seconds >= 90 else f"{seconds:.0f}초"


def _when(stamp: str | None) -> str:
    """UTC 타임스탬프를 로컬 시각의 `09-15 23:35`로 줄인다."""
    if not stamp:
        return "—"
    from datetime import datetime

    return datetime.fromisoformat(stamp).astimezone().strftime("%m-%d %H:%M")


def _pad(text: str, width: int) -> str:
    """한글은 터미널에서 두 칸을 차지한다. 글자 수가 아니라 표시 너비로 채운다."""
    shown = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(1, width - shown)


def render(status: Status) -> str:
    schema = f"{status.schema}/{status.schema_latest}"
    lines = [f"DB      {status.db_path}  {_size(status.db_bytes)}",
             f"스키마  {schema}" + ("" if status.schema == status.schema_latest else "  ← init-db 필요")
             + f"   계산 {status.calc_version}", ""]

    header = [("시장", 6), ("종목", 7), ("가격", 13), ("거래일", 13), ("주", 17), ("월", 15), ("재계산", 0)]
    lines.append("".join(_pad(name, width) for name, width in header).rstrip())
    for market in status.markets:
        cells = {p.period_type: f"{p.period_id}{' 잠정' if p.is_provisional else ''}" for p in market.periods}
        lag = market.price_lag_days
        behind = lag is not None and lag > STALE_DAYS
        lines.append(_pad(market.market, 6) + _pad(str(market.securities), 7)
                     + _pad(f"{market.last_price_date or '—'}{' !' if behind else ''}", 13)
                     + _pad(market.last_calendar_date or "—", 13)
                     + _pad(cells.get("W", "—"), 17) + _pad(cells.get("M", "—"), 15)
                     + (f"{market.pending_recalc}건 대기" if market.pending_recalc else "없음"))

    if status.runs:
        lines += ["", "최근 실행"]
        for run in status.runs:
            rows = f"{run['row_count']:,}행" if run["row_count"] else ""
            note = f"  {run['message']}" if run["status"] != "SUCCEEDED" and run["message"] else ""
            lines.append("  " + _pad(_when(run["started_at"]), 13)
                         + _pad(f"{run['job_name']} {run['market_code'] or ''}".strip(), 16)
                         + _pad(run["status"], 11)
                         + _pad(_duration(run["started_at"], run["finished_at"]), 7)
                         + rows + note)

    if status.warnings:
        lines += ["", "최근 실행이 남긴 경고 (차단 아님. 규칙은 docs/07 §13, docs/03 §8)",
                  "  " + ", ".join(f"{w['rule_code']} {w['n']}건" for w in status.warnings)]

    lines += ["", "할 일"]
    lines += [f"  {item}" for item in status.todo] or ["  없음. .venv\\Scripts\\python -m theme_radar serve 로 본다"]
    return "\n".join(lines)

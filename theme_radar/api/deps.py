"""요청 공통: 읽기 전용 연결, 파라미터 검증, 응답 봉투와 캐시 헤더 (docs/05 §1, §7)."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from fastapi import Request, Response

from theme_radar.db import connect

MAX_PERIODS = {"W": 260, "M": 120}      # docs/05 §1.1 조회 구간 상한
DEFAULT_PERIODS = 52
PROVISIONAL_MAX_AGE = 300
CLOSED_MAX_AGE = 86_400


class ApiError(Exception):
    """docs/05 §1.3 오류 응답."""

    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.field = status, code, message, field


def get_config(request: Request) -> dict[str, Any]:
    return request.app.state.config


def get_con(request: Request) -> Iterator[sqlite3.Connection]:
    con = connect(request.app.state.db_path, readonly=True)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


@dataclass(frozen=True)
class Scope:
    universe: str
    market: str
    currency: str
    period_type: str
    scheme: str | None = None
    exclusive: bool = True


def scope(con: sqlite3.Connection, universe: str, period: str, scheme: str | None = None) -> Scope:
    """유니버스·스킴·기간 단위를 확인한다. 시장이 다른 조합은 400이다."""
    row = con.execute("SELECT u.universe_code, u.market_code, m.currency FROM universe u JOIN market m USING (market_code) "
                      "WHERE u.universe_code = ?", (universe,)).fetchone()
    if row is None:
        raise ApiError(400, "INVALID_PARAMETER", f"모르는 유니버스다: {universe}", "universe")
    if period not in MAX_PERIODS:
        raise ApiError(400, "INVALID_PARAMETER", f"기간 단위는 W 또는 M이다: {period}", "period")
    if scheme is None:
        return Scope(row[0], row[1], row[2], period)
    scheme_row = con.execute("SELECT market_code, is_exclusive FROM classification_scheme WHERE scheme_code = ?", (scheme,)).fetchone()
    if scheme_row is None:
        raise ApiError(400, "INVALID_PARAMETER", f"모르는 분류 스킴이다: {scheme}", "scheme")
    if scheme_row[0] != row[1]:
        raise ApiError(400, "SCHEME_MARKET_MISMATCH", f"{universe}({row[1]})와 {scheme}({scheme_row[0]})의 시장이 다르다", "scheme")
    return Scope(row[0], row[1], row[2], period, scheme, bool(scheme_row[1]))


@dataclass(frozen=True)
class PeriodRange:
    from_id: str
    to_id: str
    from_seq: int
    to_seq: int
    rows: list[sqlite3.Row]          # period_calendar 행 (period_seq 오름차순)

    @property
    def end_date(self) -> str:
        return self.rows[-1]["end_date"]


def period_range(con: sqlite3.Connection, scope: Scope, from_id: str | None, to_id: str | None,
                 minimum: int = DEFAULT_PERIODS) -> PeriodRange:
    """구간은 양끝 포함이다. 생략하면 최신 기간에서 `minimum`기간 전까지다 (docs/05 §1.1)."""
    periods = con.execute(
        "SELECT period_id, period_seq, cal_start, cal_end, base_date, end_date, trading_days, is_closed "
        "FROM period_calendar WHERE market_code = ? AND period_type = ? AND base_date IS NOT NULL ORDER BY period_seq",
        (scope.market, scope.period_type)).fetchall()
    if not periods:
        raise ApiError(409, "NOT_AVAILABLE", f"{scope.market} {scope.period_type} 기간이 아직 없다")
    by_id = {p["period_id"]: p for p in periods}

    def seq_of(period_id: str, field: str) -> int:
        if period_id not in by_id:
            raise ApiError(400, "INVALID_PERIOD_ID", f"없는 기간 식별자다: {period_id}", field)
        return by_id[period_id]["period_seq"]

    to_seq = seq_of(to_id, "to") if to_id else periods[-1]["period_seq"]
    from_seq = seq_of(from_id, "from") if from_id else max(periods[0]["period_seq"], to_seq - minimum + 1)
    if from_seq > to_seq:
        raise ApiError(400, "INVALID_PARAMETER", "from이 to보다 뒤다", "from")
    if to_seq - from_seq + 1 > MAX_PERIODS[scope.period_type]:
        raise ApiError(400, "RANGE_TOO_LARGE",
                       f"{scope.period_type} 단위 조회 구간 상한은 {MAX_PERIODS[scope.period_type]}기간이다", "from")
    rows = [p for p in periods if from_seq <= p["period_seq"] <= to_seq]
    return PeriodRange(rows[0]["period_id"], rows[-1]["period_id"], from_seq, to_seq, rows)


def is_stale(con: sqlite3.Connection, market: str, until: str) -> bool:
    """재계산 요청이 남아 있는 구간이면 값이 바뀔 수 있다 (docs/04 §4)."""
    return con.execute("SELECT 1 FROM recalc_request WHERE market_code = ? AND processed_at IS NULL AND from_date <= ? LIMIT 1",
                       (market, until)).fetchone() is not None


@dataclass
class CalcInfo:
    calc_version: str | None
    calculated_at: str | None
    provisional: bool


def calc_info(con: sqlite3.Connection, scope: Scope, from_seq: int, to_seq: int) -> CalcInfo:
    row = con.execute(
        "SELECT MAX(calc_version), MAX(calculated_at), MAX(is_provisional) FROM universe_period_stat "
        "WHERE universe_code = ? AND period_type = ? AND period_seq BETWEEN ? AND ?",
        (scope.universe, scope.period_type, from_seq, to_seq)).fetchone()
    return CalcInfo(row[0], row[1], bool(row[2]))


def apply_cache(response: Response, info: CalcInfo, stale: bool) -> None:
    """확정 기간만 있으면 하루, 잠정 기간이 있으면 5분, 재계산 중이면 캐시하지 않는다 (docs/05 §7)."""
    if stale or info.calculated_at is None:
        response.headers["Cache-Control"] = "no-store"
        return
    response.headers["Cache-Control"] = f"max-age={PROVISIONAL_MAX_AGE if info.provisional else CLOSED_MAX_AGE}"
    response.headers["ETag"] = f'"{info.calc_version}:{info.calculated_at}"'


def envelope(scope: Scope, info: CalcInfo, stale: bool, **extra: Any) -> dict[str, Any]:
    meta = {"universe": scope.universe, "period": scope.period_type, "currency": scope.currency,
            "calc_version": info.calc_version, "calculated_at": info.calculated_at, "stale": stale}
    if scope.scheme:
        meta["scheme"] = scope.scheme
    meta.update({k: v for k, v in extra.items() if v is not None})
    return meta


def group_names(con: sqlite3.Connection, scheme: str) -> dict[str, sqlite3.Row]:
    return {r["group_code"]: r for r in con.execute(
        "SELECT group_code, group_name, group_name_en, color_hex, sort_order FROM classification_group WHERE scheme_code = ?",
        (scheme,))}


def badges(config: dict[str, Any], row: sqlite3.Row) -> list[str]:
    """집중도 배지 (docs/03 §9.5). 임계값은 config.toml의 [badges]다."""
    limits = config["badges"]
    out = []
    top1, top3 = row["top1_contrib_share"], row["top3_contrib_share"]
    up_ratio = row["up_cnt"] / row["member_cnt"] if row["member_cnt"] else 0
    if top1 is not None and top1 >= limits["top1_leader"]:
        out.append("1종목 주도")
    if top3 is not None and top3 >= limits["top3_concentrated"] and row["member_cnt"] >= limits["concentrated_min_members"]:
        out.append("소수 종목 집중")
    if up_ratio >= limits["broad_up_ratio"] and (top3 is None or top3 < limits["broad_top3_max"]):
        out.append("전반적 강세")
    if row["cap_weight_spread"] is not None and row["cap_weight_spread"] > 0 and up_ratio < 0.5:
        out.append("대형주 주도")
    return out

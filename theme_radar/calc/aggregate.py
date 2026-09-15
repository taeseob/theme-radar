"""파생 테이블 산출 (docs/04 §3).

기간 하나를 한 트랜잭션으로 쓰고, 차단 검증(V-1 ~ V-7)을 통과한 기간만 커밋한다.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from theme_radar.calc import CALC_VERSION, engine, periods
from theme_radar.calc import validate as calc_validate
from theme_radar.calc.periods import Period
from theme_radar.db import transaction
from theme_radar.jobs import Run, utc_now


@dataclass(frozen=True)
class MemberBase:
    security_id: int
    base_close_adj: float | None
    end_close_adj: float | None
    base_market_cap: float | None
    incl_status: str


@dataclass(frozen=True)
class Scheme:
    scheme_code: str
    exclusive: bool


def universes(con: sqlite3.Connection, market: str) -> list[str]:
    return [r[0] for r in con.execute("SELECT universe_code FROM universe WHERE market_code = ? ORDER BY universe_code", (market,))]


def schemes(con: sqlite3.Connection, market: str) -> list[Scheme]:
    """매핑이 적재된 스킴만 계산한다."""
    return [Scheme(code, bool(exclusive)) for code, exclusive in con.execute(
        "SELECT s.scheme_code, s.is_exclusive FROM classification_scheme s WHERE s.market_code = ? "
        "AND EXISTS (SELECT 1 FROM security_group_map m WHERE m.scheme_code = s.scheme_code) ORDER BY s.scheme_code", (market,))]


def _last_row(con: sqlite3.Connection, security_id: int, on_or_before: str):
    return con.execute(
        "SELECT trade_date, close_adj, market_cap, trade_status FROM price_daily "
        "WHERE security_id = ? AND trade_date <= ? ORDER BY trade_date DESC LIMIT 1", (security_id, on_or_before)).fetchone()


def load_members(con: sqlite3.Connection, universe: str, period: Period) -> list[MemberBase]:
    """기준일 시점 유니버스 구성원의 기준일·종료일 가격과 포함 판정 (docs/03 §10).

    기준일·종료일에 행이 없으면 직전 거래일 종가를 캐리포워드하고, 그 사유에 따라 상태를 나눈다.
    """
    members = con.execute(
        "SELECT m.security_id, s.delisting_date, m.valid_to FROM universe_membership m JOIN security s USING (security_id) "
        "WHERE m.universe_code = ? AND m.valid_from <= ? AND ? <= m.valid_to", (universe, period.base_date, period.base_date)).fetchall()
    base_rows = {sid: row for sid, *row in con.execute(
        "SELECT security_id, close_adj, market_cap, trade_status FROM price_daily WHERE trade_date = ?", (period.base_date,))}
    end_rows = {sid: row for sid, *row in con.execute(
        "SELECT security_id, close_adj, market_cap, trade_status FROM price_daily WHERE trade_date = ?", (period.end_date,))}

    out = []
    for security_id, delisting_date, valid_to in members:
        base = base_rows.get(security_id)
        base_carried = False
        if base is None:
            row = _last_row(con, security_id, period.base_date)
            if row is None:
                out.append(MemberBase(security_id, None, None, None, "NEW_LISTING"))   # 기준일에 아직 상장 전
                continue
            base, base_carried = row[1:], True
        end = end_rows.get(security_id)
        end_carried = False
        if end is None:
            row = _last_row(con, security_id, period.end_date)
            end, end_carried = row[1:], True

        base_adj, base_cap, base_status = base
        end_adj = end[0]
        if not base_cap:
            status = "NO_MCAP"                       # 기준일 시가총액 없음 → 가중치 산정 불가
        elif not base_adj:
            status = "NO_BASE_PRICE"
        elif end_carried and ((delisting_date and delisting_date <= period.end_date) or valid_to < period.end_date):
            status = "DELISTED"                      # 기간 중 폐지·편출: 마지막 거래일 종가로 반영
        elif base_carried or end_carried or base_status != "NORMAL" or end[2] != "NORMAL":
            status = "SUSPENDED"                     # 거래정지·무거래: 직전 종가 캐리포워드 후 포함
        else:
            status = "INCLUDED"
        out.append(MemberBase(security_id, base_adj, end_adj, base_cap, status))
    return out


def _groups_as_of(con: sqlite3.Connection, scheme: str, as_of: str) -> dict[int, str]:
    return {sid: code for sid, code in con.execute(
        "SELECT security_id, group_code FROM security_group_map WHERE scheme_code = ? AND valid_from <= ? AND ? <= valid_to",
        (scheme, as_of, as_of))}


def _previous_ranks(con: sqlite3.Connection, universe: str, scheme: str, period: Period) -> dict[str, int]:
    return {code: rank for code, rank in con.execute(
        "SELECT group_code, rank_ret FROM group_period_stat WHERE universe_code = ? AND scheme_code = ? AND period_type = ? "
        "AND period_seq = ? AND rank_ret IS NOT NULL", (universe, scheme, period.period_type, period.period_seq - 1))}


def compute_period(con: sqlite3.Connection, universe: str, scheme: Scheme, period: Period,
                   members: list[MemberBase]) -> engine.PeriodResult:
    mapping = _groups_as_of(con, scheme.scheme_code, period.base_date)
    return engine.compute([engine.Member(
        security_id=m.security_id, group_code=mapping.get(m.security_id, engine.UNMAPPED),
        base_close_adj=m.base_close_adj, end_close_adj=m.end_close_adj, base_market_cap=m.base_market_cap,
        incl_status=m.incl_status) for m in members])


def write_period(con: sqlite3.Connection, universe: str, scheme: Scheme, period: Period, result: engine.PeriodResult,
                 write_security_rows: bool, now: str) -> int:
    """파생 행을 다시 쓴다. 호출하는 쪽이 트랜잭션을 연다."""
    provisional = int(not period.is_closed)
    key = (universe, period.period_type, period.period_id)
    rows = 0
    if write_security_rows:
        con.execute("DELETE FROM security_period_return WHERE universe_code = ? AND period_type = ? AND period_id = ?", key)
        con.executemany(
            "INSERT INTO security_period_return (universe_code, period_type, period_id, security_id, base_close_adj, "
            "end_close_adj, ret, base_market_cap, weight_universe, incl_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(*key, m.security_id, m.base_close_adj, m.end_close_adj, m.ret, m.base_market_cap, m.weight_universe, m.incl_status)
             for m in result.members])
        rows += len(result.members)
        u = result.universe
        con.execute("DELETE FROM universe_period_stat WHERE universe_code = ? AND period_type = ? AND period_id = ?", key)
        con.execute(
            "INSERT INTO universe_period_stat (universe_code, period_type, period_id, period_seq, ret, ret_equal, ret_median, "
            "base_market_cap, member_cnt, up_cnt, unmapped_cap_ratio, is_provisional, calc_version, calculated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*key, period.period_seq, u.ret, u.ret_equal, u.ret_median, u.base_market_cap, u.member_cnt, u.up_cnt,
             u.unmapped_cap_ratio, provisional, CALC_VERSION, now))
        rows += 1

    group_key = (universe, scheme.scheme_code, period.period_type, period.period_id)
    previous = _previous_ranks(con, universe, scheme.scheme_code, period)
    con.execute("DELETE FROM group_period_stat WHERE universe_code = ? AND scheme_code = ? AND period_type = ? AND period_id = ?",
                group_key)
    con.execute("DELETE FROM group_member_contribution WHERE universe_code = ? AND scheme_code = ? AND period_type = ? AND period_id = ?",
                group_key)
    group_rows, member_rows = [], []
    for g in result.groups:
        rank_prev = previous.get(g.group_code)
        group_rows.append((
            *group_key, g.group_code, period.period_seq, g.ret, g.ret_equal, g.ret_median,
            g.base_weight if scheme.exclusive else None,
            g.contribution if scheme.exclusive else None,
            g.contrib_share if scheme.exclusive else None,
            g.rank_ret, rank_prev, (rank_prev - g.rank_ret) if rank_prev is not None and g.rank_ret is not None else None,
            g.rank_contrib if scheme.exclusive else None,
            g.member_cnt, g.up_cnt, g.hhi, g.effective_n,
            g.top_shares[1], g.top_shares[3], g.top_shares[5],
            g.top_neg_shares[1], g.top_neg_shares[3], g.top_neg_shares[5],
            g.cap_weight_spread, provisional, CALC_VERSION, now))
        member_rows.extend((*group_key, g.group_code, m.security_id, m.weight_in_group, m.ret, m.contribution, rank)
                           for rank, m in enumerate(g.members, 1))
    con.executemany(
        "INSERT INTO group_period_stat (universe_code, scheme_code, period_type, period_id, group_code, period_seq, ret, "
        "ret_equal, ret_median, base_weight, contribution, contrib_share, rank_ret, rank_ret_prev, rank_delta, rank_contrib, "
        "member_cnt, up_cnt, hhi, effective_n, top1_contrib_share, top3_contrib_share, top5_contrib_share, "
        "top1_neg_contrib_share, top3_neg_contrib_share, top5_neg_contrib_share, cap_weight_spread, is_provisional, "
        "calc_version, calculated_at) VALUES (" + ", ".join("?" * 30) + ")", group_rows)
    con.executemany(
        "INSERT INTO group_member_contribution (universe_code, scheme_code, period_type, period_id, group_code, security_id, "
        "weight_in_group, ret, contribution, contrib_rank) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", member_rows)
    return rows + len(group_rows) + len(member_rows)


def target_periods(con: sqlite3.Connection, market: str, universe: str, period_type: str, full: bool,
                   recalc_from: str | None) -> list[Period]:
    """다시 계산할 기간: 전 구간 모드면 전부, 아니면 아직 계산하지 않았거나 잠정치이거나 재계산 요청 구간."""
    all_periods = [p for p in periods.load(con, market, period_type) if p.base_date]
    if full:
        return all_periods
    state = {pid: provisional for pid, provisional in con.execute(
        "SELECT period_id, is_provisional FROM universe_period_stat WHERE universe_code = ? AND period_type = ?",
        (universe, period_type))}
    return [p for p in all_periods
            if p.period_id not in state or state[p.period_id] or (recalc_from and p.end_date >= recalc_from)]


def pending_recalc(con: sqlite3.Connection, market: str) -> tuple[str | None, list[int]]:
    rows = con.execute("SELECT request_id, from_date FROM recalc_request WHERE market_code = ? AND processed_at IS NULL",
                       (market,)).fetchall()
    return (min(r[1] for r in rows) if rows else None), [r[0] for r in rows]


def run(con: sqlite3.Connection, run_state: Run, market: str, today: str, full: bool, log) -> None:
    """시장의 파생 테이블을 산출한다. 기간 오름차순으로 처리해 직전 기간 순위를 잇는다."""
    started = time.monotonic()
    periods.rebuild(con, market, today)
    recalc_from, request_ids = pending_recalc(con, market)
    if recalc_from:
        log(f"재계산 요청 {len(request_ids)}건, {recalc_from} 이후 기간을 다시 계산한다")
    scheme_list = schemes(con, market)
    if not scheme_list:
        raise SystemExit(f"{market} 분류 매핑이 없다. 먼저 load-mapping을 실행한다")

    blocked_periods = []
    for universe in universes(con, market):
        for period_type in ("W", "M"):
            todo = target_periods(con, market, universe, period_type, full, recalc_from)
            for period in todo:
                members = load_members(con, universe, period)
                violations: list[calc_validate.Violation] = []
                try:
                    with transaction(con):
                        for i, scheme in enumerate(scheme_list):
                            result = compute_period(con, universe, scheme, period, members)
                            violations.extend(v for v in calc_validate.check(result, period_type, scheme.exclusive))
                            if any(v.severity == "BLOCK" for v in violations):
                                raise _Blocked()
                            run_state.row_count += write_period(con, universe, scheme, period, result, i == 0, utc_now())
                except _Blocked:
                    blocked_periods.append(f"{universe}/{period_type}/{period.period_id}")
                for v in violations:
                    run_state.check(v.rule_code, f"{universe}/{period_type}/{period.period_id}", v.severity,
                                    passed=False, observed=v.observed, tolerance=v.tolerance, detail=v.detail)
            log(f"{universe}/{period_type}: {len(todo)}개 기간 계산 ({time.monotonic() - started:.1f}s)")

    if request_ids and not blocked_periods:
        with transaction(con):
            con.executemany("UPDATE recalc_request SET run_id = ?, processed_at = ? WHERE request_id = ?",
                            [(run_state.run_id, utc_now(), rid) for rid in request_ids])
    if blocked_periods:
        log(f"검증 실패로 저장하지 않은 기간 {len(blocked_periods)}개: {blocked_periods[:10]}")


class _Blocked(Exception):
    """차단 검증 실패. 해당 기간의 트랜잭션을 되돌린다."""

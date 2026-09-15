"""섹터 API: 범프 차트, 기간 스냅샷, 드릴다운, 섹터 시계열, CSV 내보내기 (docs/05 §3, §5, §6.2)."""
from __future__ import annotations

import csv
import io
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse

from theme_radar.api import deps
from theme_radar.api.models import (BreakdownData, BreakdownMember, BreakdownOthers, BreakdownResponse, BreakdownSummary,
                                    HistoryPoint, HistoryResponse, Meta, RankPoint, RanksData, RanksPeriod, RanksResponse,
                                    RankSeries, ReturnsResponse, SectorRow, r8, r10)

router = APIRouter(tags=["sectors"])
UNMAPPED = "UNMAPPED"
GROUP_COLUMNS = ("group_code, period_id, period_seq, ret, ret_equal, ret_median, base_weight, contribution, contrib_share, "
                 "rank_ret, rank_ret_prev, rank_delta, rank_contrib, member_cnt, up_cnt, hhi, effective_n, "
                 "top1_contrib_share, top3_contrib_share, top5_contrib_share, cap_weight_spread, is_provisional")


def _prepare(con, universe: str, scheme: str, period: str, from_: str | None, to: str | None):
    scope = deps.scope(con, universe, period, scheme)
    rng = deps.period_range(con, scope, from_, to)
    info = deps.calc_info(con, scope, rng.from_seq, rng.to_seq)
    if info.calculated_at is None:
        raise deps.ApiError(409, "NOT_AVAILABLE", f"{rng.from_id} ~ {rng.to_id} 구간의 집계가 아직 없다")
    return scope, rng, info, deps.is_stale(con, scope.market, rng.end_date)


def group_label(names: dict[str, sqlite3.Row], code: str, lang: str) -> str:
    row = names.get(code)
    if row is None:
        return "미매핑" if code == UNMAPPED and lang == "ko" else code
    return (row["group_name_en"] or row["group_name"]) if lang == "en" else row["group_name"]


def latest_period(con, scope: deps.Scope) -> str:
    row = con.execute("SELECT period_id FROM universe_period_stat WHERE universe_code = ? AND period_type = ? "
                      "ORDER BY period_seq DESC LIMIT 1", (scope.universe, scope.period_type)).fetchone()
    if row is None:
        raise deps.ApiError(409, "NOT_AVAILABLE", f"{scope.universe} {scope.period_type} 집계가 아직 없다")
    return row[0]


def _group_rows(con, scope: deps.Scope, period_id: str) -> list[sqlite3.Row]:
    return con.execute(f"SELECT {GROUP_COLUMNS} FROM group_period_stat WHERE universe_code = ? AND scheme_code = ? "
                       "AND period_type = ? AND period_id = ?",
                       (scope.universe, scope.scheme, scope.period_type, period_id)).fetchall()


@router.get("/sectors/ranks", response_model=RanksResponse)
def ranks(response: Response, universe: str, scheme: str, period: str = "W",
          from_: str | None = Query(None, alias="from"), to: str | None = None, lang: str = "ko",
          rank_by: str = Query("return", pattern="^(return|contribution)$"), top_n: int = Query(0, ge=0),
          con: sqlite3.Connection = Depends(deps.get_con)) -> RanksResponse:
    """범프 차트용 순위 시계열. 미매핑 의사 그룹은 순위가 없어 계열에 넣지 않는다."""
    scope, rng, info, stale = _prepare(con, universe, scheme, period, from_, to)
    deps.apply_cache(response, info, stale)
    names = deps.group_names(con, scope.scheme)

    periods = [RanksPeriod(period_id=r["period_id"], end_date=r["end_date"], is_provisional=bool(r["is_provisional"]),
                           universe_return=r8(r["ret"]), group_count=r["group_count"])
               for r in con.execute(
                   "SELECT u.period_id, u.ret, u.is_provisional, p.end_date, "
                   "  (SELECT COUNT(*) FROM group_period_stat g WHERE g.universe_code = u.universe_code "
                   "   AND g.scheme_code = ? AND g.period_type = u.period_type AND g.period_id = u.period_id "
                   "   AND g.group_code <> 'UNMAPPED') AS group_count "
                   "FROM universe_period_stat u JOIN period_calendar p ON p.market_code = ? AND p.period_type = u.period_type "
                   "  AND p.period_id = u.period_id "
                   "WHERE u.universe_code = ? AND u.period_type = ? AND u.period_seq BETWEEN ? AND ? ORDER BY u.period_seq",
                   (scope.scheme, scope.market, scope.universe, scope.period_type, rng.from_seq, rng.to_seq))]

    rows = con.execute(
        f"SELECT {GROUP_COLUMNS} FROM group_period_stat WHERE universe_code = ? AND scheme_code = ? AND period_type = ? "
        "AND period_seq BETWEEN ? AND ? AND group_code <> 'UNMAPPED' ORDER BY group_code, period_seq",
        (scope.universe, scope.scheme, scope.period_type, rng.from_seq, rng.to_seq)).fetchall()

    by_group: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_group.setdefault(row["group_code"], []).append(row)
    best_rank = {code: min((r["rank_contrib" if rank_by == "contribution" else "rank_ret"] or 10**6) for r in group_rows)
                 for code, group_rows in by_group.items()}
    kept = [code for code in by_group if not top_n or best_rank[code] <= top_n]

    series = []
    for code in sorted(kept, key=lambda c: (names[c]["sort_order"] if c in names else 0, c)):
        points = []
        for row in by_group[code]:
            rank = row["rank_contrib"] if rank_by == "contribution" else row["rank_ret"]
            if rank is None:
                continue
            points.append(RankPoint(period_id=row["period_id"], rank=rank,
                                    rank_delta=row["rank_delta"] if rank_by == "return" else None,
                                    ret=r8(row["ret"]), base_weight=r10(row["base_weight"]),
                                    contribution=r8(row["contribution"]), top1_contrib_share=r8(row["top1_contrib_share"]),
                                    member_cnt=row["member_cnt"]))
        series.append(RankSeries(group_code=code, name=group_label(names, code, lang),
                                 color=names[code]["color_hex"] if code in names else None, points=points))

    meta = Meta(**deps.envelope(scope, info, stale, **{"from": rng.from_id, "to": rng.to_id, "rank_by": rank_by,
                                                       "group_count": len(by_group), "lang": lang}))
    return RanksResponse(meta=meta, data=RanksData(periods=periods, series=series, others_count=len(by_group) - len(kept)))


@router.get("/sectors/returns", response_model=ReturnsResponse)
def returns(response: Response, universe: str, scheme: str, period: str = "W", period_id: str | None = None,
            sort: str = Query("return", pattern="^(return|contribution|weight)$"), lang: str = "ko",
            con: sqlite3.Connection = Depends(deps.get_con), config: dict = Depends(deps.get_config)) -> ReturnsResponse:
    """단일 기간 섹터 스냅샷. 미매핑 행은 목록 마지막에 둔다."""
    scope = deps.scope(con, universe, period, scheme)
    period_id = period_id or latest_period(con, scope)
    rng = deps.period_range(con, scope, period_id, period_id)
    info = deps.calc_info(con, scope, rng.from_seq, rng.to_seq)
    stale = deps.is_stale(con, scope.market, rng.end_date)
    rows = _group_rows(con, scope, period_id)
    if not rows:
        raise deps.ApiError(409, "NOT_AVAILABLE", f"{period_id} 집계가 아직 없다")
    deps.apply_cache(response, info, stale)
    names = deps.group_names(con, scope.scheme)
    universe_row = con.execute("SELECT ret, is_provisional FROM universe_period_stat WHERE universe_code = ? "
                               "AND period_type = ? AND period_id = ?", (scope.universe, scope.period_type, period_id)).fetchone()

    def key(row: sqlite3.Row):
        if sort == "contribution":
            return -(row["contribution"] or 0)
        if sort == "weight":
            return -(row["base_weight"] or 0)
        return -row["ret"]

    ordered = sorted((r for r in rows if r["group_code"] != UNMAPPED), key=key)
    ordered += [r for r in rows if r["group_code"] == UNMAPPED]
    data = [SectorRow(
        group_code=row["group_code"], name=group_label(names, row["group_code"], lang),
        color=names[row["group_code"]]["color_hex"] if row["group_code"] in names else None,
        ret=r8(row["ret"]), return_equal=r8(row["ret_equal"]), return_median=r8(row["ret_median"]),
        base_weight=r10(row["base_weight"]), contribution=r8(row["contribution"]), contribution_share=r8(row["contrib_share"]),
        rank=row["rank_ret"], rank_prev=row["rank_ret_prev"], rank_delta=row["rank_delta"],
        member_cnt=row["member_cnt"], up_cnt=row["up_cnt"],
        up_ratio=r8(row["up_cnt"] / row["member_cnt"]) if row["member_cnt"] else None,
        hhi=r10(row["hhi"]), effective_n=round(row["effective_n"], 4) if row["effective_n"] is not None else None,
        top1_contrib_share=r8(row["top1_contrib_share"]), top3_contrib_share=r8(row["top3_contrib_share"]),
        top5_contrib_share=r8(row["top5_contrib_share"]), cap_weight_spread=r8(row["cap_weight_spread"]),
        badges=deps.badges(config, row)) for row in ordered]
    meta = Meta(**deps.envelope(scope, info, stale, period_id=period_id, sort=sort,
                                is_provisional=bool(universe_row["is_provisional"]),
                                universe_return=r8(universe_row["ret"])))
    return ReturnsResponse(meta=meta, data=data)


@router.get("/sectors/{group_code}/breakdown", response_model=BreakdownResponse)
def breakdown(group_code: str, response: Response, universe: str, scheme: str, period: str = "W",
              period_id: str | None = None, limit: int = Query(20, ge=1, le=100),
              side: str = Query("both", pattern="^(top|bottom|both)$"), lang: str = "ko",
              con: sqlite3.Connection = Depends(deps.get_con), config: dict = Depends(deps.get_config)) -> BreakdownResponse:
    """섹터 내 종목 기여도. 반환하지 않은 종목은 others로 합산한다."""
    scope = deps.scope(con, universe, period, scheme)
    period_id = period_id or latest_period(con, scope)
    rng = deps.period_range(con, scope, period_id, period_id)
    info = deps.calc_info(con, scope, rng.from_seq, rng.to_seq)
    stale = deps.is_stale(con, scope.market, rng.end_date)
    group = next((r for r in _group_rows(con, scope, period_id) if r["group_code"] == group_code), None)
    if group is None:
        raise deps.ApiError(404, "NOT_FOUND", f"{period_id}에 {group_code} 그룹이 없다", "group_code")
    deps.apply_cache(response, info, stale)

    rows = con.execute(
        "SELECT m.security_id, s.ticker, s.name_local, s.name_en, m.weight_in_group, m.ret, m.contribution, m.contrib_rank, "
        "       r.weight_universe "
        "FROM group_member_contribution m JOIN security s USING (security_id) "
        "LEFT JOIN security_period_return r ON r.universe_code = m.universe_code AND r.period_type = m.period_type "
        "  AND r.period_id = m.period_id AND r.security_id = m.security_id "
        "WHERE m.universe_code = ? AND m.scheme_code = ? AND m.period_type = ? AND m.period_id = ? AND m.group_code = ? "
        "ORDER BY m.contribution DESC", (scope.universe, scope.scheme, scope.period_type, period_id, group_code)).fetchall()

    picked_index = set(range(min(limit, len(rows)))) if side in ("top", "both") else set()
    if side in ("bottom", "both"):
        picked_index |= set(range(max(0, len(rows) - limit), len(rows)))
    members = [BreakdownMember(
        security_id=row["security_id"], ticker=row["ticker"],
        name=(row["name_en"] or row["name_local"]) if lang == "en" else row["name_local"],
        weight_in_group=r10(row["weight_in_group"]), weight_in_universe=r10(row["weight_universe"]),
        ret=r8(row["ret"]), contribution_in_group=r8(row["contribution"]),
        contribution_in_universe=r8((group["base_weight"] or 0) * row["contribution"]),
        contrib_rank=row["contrib_rank"]) for i, row in enumerate(rows) if i in picked_index]
    rest = [row for i, row in enumerate(rows) if i not in picked_index]
    others = BreakdownOthers(member_cnt=len(rest), weight_in_group=r10(sum(r["weight_in_group"] for r in rest)),
                             contribution_in_group=r8(sum(r["contribution"] for r in rest)))
    summary = BreakdownSummary(
        ret=r8(group["ret"]), return_equal=r8(group["ret_equal"]), cap_weight_spread=r8(group["cap_weight_spread"]),
        base_weight=r10(group["base_weight"]), contribution=r8(group["contribution"]),
        member_cnt=group["member_cnt"], up_cnt=group["up_cnt"],
        up_ratio=r8(group["up_cnt"] / group["member_cnt"]) if group["member_cnt"] else None,
        hhi=r10(group["hhi"]), effective_n=round(group["effective_n"], 4) if group["effective_n"] is not None else None,
        top1_contrib_share=r8(group["top1_contrib_share"]), top3_contrib_share=r8(group["top3_contrib_share"]),
        top5_contrib_share=r8(group["top5_contrib_share"]), badges=deps.badges(config, group))
    names = deps.group_names(con, scope.scheme)
    meta = Meta(**deps.envelope(scope, info, stale, period_id=period_id, group_code=group_code,
                                name=group_label(names, group_code, lang), side=side, limit=limit))
    return BreakdownResponse(meta=meta, data=BreakdownData(summary=summary, members=members, others=others))


@router.get("/sectors/{group_code}/history", response_model=HistoryResponse)
def history(group_code: str, response: Response, universe: str, scheme: str, period: str = "W",
            from_: str | None = Query(None, alias="from"), to: str | None = None, lang: str = "ko",
            con: sqlite3.Connection = Depends(deps.get_con)) -> HistoryResponse:
    scope, rng, info, stale = _prepare(con, universe, scheme, period, from_, to)
    deps.apply_cache(response, info, stale)
    rows = con.execute(
        f"SELECT {GROUP_COLUMNS} FROM group_period_stat WHERE universe_code = ? AND scheme_code = ? AND period_type = ? "
        "AND group_code = ? AND period_seq BETWEEN ? AND ? ORDER BY period_seq",
        (scope.universe, scope.scheme, scope.period_type, group_code, rng.from_seq, rng.to_seq)).fetchall()
    if not rows:
        raise deps.ApiError(404, "NOT_FOUND", f"{group_code} 그룹의 기간 데이터가 없다", "group_code")
    data = [HistoryPoint(period_id=row["period_id"], ret=r8(row["ret"]), rank=row["rank_ret"],
                         contribution=r8(row["contribution"]), base_weight=r10(row["base_weight"]),
                         top1_contrib_share=r8(row["top1_contrib_share"]), hhi=r10(row["hhi"]),
                         up_ratio=r8(row["up_cnt"] / row["member_cnt"]) if row["member_cnt"] else None,
                         cap_weight_spread=r8(row["cap_weight_spread"]), is_provisional=bool(row["is_provisional"]))
            for row in rows]
    names = deps.group_names(con, scope.scheme)
    meta = Meta(**deps.envelope(scope, info, stale, **{"from": rng.from_id, "to": rng.to_id, "group_code": group_code,
                                                       "name": group_label(names, group_code, lang)}))
    return HistoryResponse(meta=meta, data=data)


@router.get("/export/sectors.csv", response_class=PlainTextResponse)
def export_csv(universe: str, scheme: str, period: str = "W", from_: str | None = Query(None, alias="from"),
               to: str | None = None, lang: str = "ko", con: sqlite3.Connection = Depends(deps.get_con)) -> Response:
    """docs/05 §6.2. 범프 차트와 같은 파라미터를 받아 CSV로 준다."""
    scope, rng, info, stale = _prepare(con, universe, scheme, period, from_, to)
    names = deps.group_names(con, scope.scheme)
    rows = con.execute(
        "SELECT g.period_id, p.end_date, g.group_code, g.ret, g.rank_ret, g.base_weight, g.contribution, g.member_cnt "
        "FROM group_period_stat g JOIN period_calendar p ON p.market_code = ? AND p.period_type = g.period_type "
        "  AND p.period_id = g.period_id "
        "WHERE g.universe_code = ? AND g.scheme_code = ? AND g.period_type = ? AND g.period_seq BETWEEN ? AND ? "
        "ORDER BY g.period_seq, g.group_code",
        (scope.market, scope.universe, scope.scheme, scope.period_type, rng.from_seq, rng.to_seq)).fetchall()
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["period_id", "end_date", "group_code", "group_name", "return", "rank", "base_weight",
                     "contribution", "member_cnt"])
    for row in rows:
        writer.writerow([row["period_id"], row["end_date"], row["group_code"], group_label(names, row["group_code"], lang),
                         r8(row["ret"]), row["rank_ret"], r10(row["base_weight"]), r8(row["contribution"]), row["member_cnt"]])
    filename = f"sectors_{scope.universe}_{scope.scheme}_{rng.from_id}_{rng.to_id}.csv"
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})

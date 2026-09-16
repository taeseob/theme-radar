"""prices 하위 명령: python -m theme_radar prices <명령> --market KR|US (docs/07 §5.2)."""
from __future__ import annotations

import argparse
from typing import Any

from theme_radar.jobs import run_job
from theme_radar.prices import collect_kr, collect_us, events, validate
from theme_radar.prices.context import build_context
from theme_radar.prices.sources import yf

MARKET_MODULES = {"KR": collect_kr, "US": collect_us}


def add_commands(commands: argparse._SubParsersAction, open_db) -> None:
    prices = commands.add_parser("prices", help="주가·상장주식수 수집 (docs/07)")
    sub = prices.add_subparsers(dest="prices_command", required=True, metavar="<명령>")

    def market_parser(name: str, help_text: str, func, **kwargs):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--market", required=True, choices=["KR", "US"])
        p.add_argument("--db", help="DB 파일 경로 (기본값: config.toml의 db.path)")
        p.add_argument("--no-raw", action="store_true", help="수집 원문을 저장하지 않는다")
        p.set_defaults(func=lambda args, config: func(args, config, open_db), **kwargs)
        return p

    market_parser("universe", "종목 마스터와 유니버스 편입 이력만 갱신한다", _universe)
    backfill = market_parser("backfill", "수집 시작일부터 전 구간을 받는다 (처음 적재)", _collect, mode="backfill")
    backfill.add_argument("--only", nargs="+", metavar="TICKER", help="이 종목만 받는다 (확인용)")
    market_parser("daily", "마지막 저장일 이후를 받는다 (증분)", _collect, mode="daily", only=None)
    market_parser("reconcile", "전 종목 전 구간 수정 종가를 다시 받아 대사한다 (주간)", _collect, mode="reconcile", only=None)
    restate = market_parser("restate", "지정한 종목의 전 구간 수정 종가를 다시 받는다", _collect, mode="restate")
    restate.add_argument("--only", nargs="+", required=True, metavar="TICKER")
    market_parser("shares", "가격은 받지 않고 상장주식수 관측치 수집·재계산·검증·특이사항만 전 구간으로 다시 한다", _shares)
    market_parser("events", "아무것도 받지 않고 이미 쌓인 수집 데이터에서 특이사항만 다시 뽑는다", _events)


def _universe(args: argparse.Namespace, config: dict[str, Any], open_db) -> int:
    con = open_db(args, config)
    with run_job(con, "prices-universe", args.market) as run:
        ctx = build_context(con, run, config, args.market, full=True, save_raw=not args.no_raw)
        _prepare(ctx)
        MARKET_MODULES[args.market].update_universe(ctx)
    return _exit_code(run)


def _collect(args: argparse.Namespace, config: dict[str, Any], open_db) -> int:
    return collect_market(open_db(args, config), config, args.market, args.mode, args.only, not args.no_raw)


def collect_market(con, config: dict[str, Any], market: str, mode: str, only: list[str] | None = None,
                   save_raw: bool = True) -> int:
    """수집 흐름 한 번. mode: backfill | daily | reconcile | restate (daily만 증분이고 나머지는 전 구간)"""
    args = argparse.Namespace(market=market, no_raw=not save_raw)
    full = mode != "daily"
    with run_job(con, f"prices-{mode}", market, target_scope=",".join(only) if only else None) as run:
        ctx = build_context(con, run, config, market, full=full, save_raw=save_raw)
        _prepare(ctx)
        module = MARKET_MODULES[market]
        scheme = "WI26" if market == "KR" else "GICS"
        if not con.execute("SELECT 1 FROM security_group_map WHERE scheme_code = ? LIMIT 1", (scheme,)).fetchone():
            ctx.log(f"주의: {scheme} 매핑이 비어 있다. 특이사항의 섹터 값이 비므로 먼저 load-mapping --scheme {scheme}을 실행한다")
        delistings = module.update_universe(ctx) if not only else None
        module.update_calendar(ctx)
        module.collect_prices(ctx, only)
        if market == "KR":
            if delistings is None:
                from theme_radar.prices.sources import fdr_krx
                delistings = fdr_krx.fetch_delistings(ctx.raw_dir, ctx.start)
            module.collect_shares(ctx, delistings, only)
        else:
            module.collect_shares(ctx, only)
        validate.run_checks(ctx)
        if not only:
            events.build_events(ctx)
        _report(ctx, run)
    return _exit_code(run)


def _shares(args: argparse.Namespace, config: dict[str, Any], open_db) -> int:
    con = open_db(args, config)
    with run_job(con, "prices-shares", args.market) as run:
        ctx = build_context(con, run, config, args.market, full=True, save_raw=not args.no_raw)
        _prepare(ctx)
        if args.market == "KR":
            from theme_radar.prices.sources import fdr_krx
            collect_kr.collect_shares(ctx, fdr_krx.fetch_delistings(ctx.raw_dir, ctx.start))
        else:
            collect_us.collect_shares(ctx)
        validate.run_checks(ctx)
        events.build_events(ctx)
        _report(ctx, run)
    return _exit_code(run)


def _events(args: argparse.Namespace, config: dict[str, Any], open_db) -> int:
    """특이사항만 다시 뽑는다. 출처처럼 사건 필드가 늘었을 때 수집 없이 채우는 용도다 (docs/07 §11.2)."""
    con = open_db(args, config)
    with run_job(con, "prices-events", args.market) as run:
        ctx = build_context(con, run, config, args.market, full=True, save_raw=not args.no_raw)
        events.build_events(ctx)
    return _exit_code(run)


def _report(ctx, run) -> None:
    failed = [f"{c.rule_code} {c.scope}: {c.detail}" for c in run.checks if not c.passed]
    ctx.log(f"완료. 새 행 {run.row_count}, 검증 실패·경고 {len(failed)}건")
    for line in failed:
        ctx.log(f"  {line[:300]}")


def _prepare(ctx) -> None:
    if ctx.market == "US":
        yf.setup_cache(ctx.cache_dir / "yfinance")


def _exit_code(run) -> int:
    return 2 if run.blocked else 0

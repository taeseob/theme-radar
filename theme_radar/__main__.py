"""CLI 진입점: python -m theme_radar <명령> (docs/09 §4.6)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from theme_radar.config import load_config, resolve_path
from theme_radar.db import connect, migrate, schema_version


def db_path(args: argparse.Namespace, config: dict[str, Any]) -> Path:
    return Path(args.db) if getattr(args, "db", None) else resolve_path(config["db"]["path"])


def open_db(args: argparse.Namespace, config: dict[str, Any]):
    """스키마가 최신인 DB 연결을 연다. 적용하지 않은 마이그레이션이 있으면 init-db를 먼저 실행하라고 알린다."""
    from theme_radar.db.migrate import list_migrations
    con = connect(db_path(args, config), busy_timeout_ms=config["db"]["busy_timeout_ms"])
    if schema_version(con) != len(list_migrations()):
        raise SystemExit("DB 스키마가 최신이 아니다. 먼저 python -m theme_radar init-db를 실행한다")
    return con


def cmd_init_db(args: argparse.Namespace, config: dict[str, Any]) -> int:
    path = db_path(args, config)
    con = connect(path, busy_timeout_ms=config["db"]["busy_timeout_ms"])
    try:
        applied = migrate(con)
        version = schema_version(con)
    finally:
        con.close()
    print(f"DB: {path}")
    print(f"스키마 버전: {version} (이번에 적용: {', '.join(m.path.name for m in applied) or '없음'})")
    return 0


def cmd_load_mapping(args: argparse.Namespace, config: dict[str, Any]) -> int:
    from theme_radar.master.groups import load_scheme
    con = open_db(args, config)
    result = load_scheme(con, args.scheme, config["collect"]["start_date"])
    if result.missing_tickers:
        print(f"적재하지 않았다. 종목 마스터에 없는 티커 {len(result.missing_tickers)}개: {result.missing_tickers[:30]}")
        print(f"먼저 python -m theme_radar prices universe --market {'KR' if args.scheme == 'WI26' else 'US'}을 실행한다")
        return 1
    print(f"{args.scheme}: 그룹 {result.groups}개, 매핑 {result.mappings}종목 ({result.source_batch})")
    return 0


def _market_today(market: str) -> str:
    from datetime import datetime

    from theme_radar.prices.context import MARKET_TZ
    return datetime.now(MARKET_TZ[market]).date().isoformat()


def _logger(market: str):
    import time
    started = time.monotonic()

    def log(message: str) -> None:
        print(f"[{market} {time.monotonic() - started:7.1f}s] {message}", flush=True)
    return log


def run_aggregate(con, args: argparse.Namespace, config: dict[str, Any]) -> int:
    from theme_radar.calc import aggregate
    from theme_radar.jobs import run_job

    log = _logger(args.market)
    with run_job(con, "aggregate", args.market, target_scope="full" if args.full else None) as run:
        aggregate.run(con, run, args.market, _market_today(args.market), args.full, log)
        failed = [f"{c.rule_code} {c.scope}: {c.detail}" for c in run.checks if not c.passed]
        log(f"완료. 파생 행 {run.row_count}, 검증 위반 {len(failed)}건")
        for line in failed[:20]:
            log(f"  {line[:300]}")
        if len(failed) > 20:
            log(f"  ... 그 밖 {len(failed) - 20}건은 validation_result에 있다")
    return 2 if run.blocked else 0


def cmd_aggregate(args: argparse.Namespace, config: dict[str, Any]) -> int:
    return run_aggregate(open_db(args, config), args, config)


def cmd_daily(args: argparse.Namespace, config: dict[str, Any]) -> int:
    """수집 → 집계까지 이어서 실행한다 (docs/09 §4.6). 작업 스케줄러가 부르는 명령이다."""
    from theme_radar.prices.cli import collect_market

    con = open_db(args, config)
    code = collect_market(con, config, args.market, "daily", save_raw=not args.no_raw)
    if code:
        return code
    args.full = False
    return run_aggregate(con, args, config)


def cmd_serve(args: argparse.Namespace, config: dict[str, Any]) -> int:
    """API와 화면을 띄운다 (docs/09 §4.3). 127.0.0.1에만 바인딩한다."""
    from theme_radar.api.app import serve

    if args.port:
        config = {**config, "api": {**config["api"], "port": args.port}}
    print(f"http://{config['api']['host']}:{config['api']['port']} (문서: /docs)")
    serve(config)
    return 0


def build_parser() -> argparse.ArgumentParser:
    from theme_radar.prices.cli import add_commands as add_prices_commands

    parser = argparse.ArgumentParser(prog="python -m theme_radar")
    commands = parser.add_subparsers(dest="command", required=True, metavar="<명령>")

    init_db = commands.add_parser("init-db", help="DB 파일을 만들고 스키마 마이그레이션을 적용한다")
    init_db.add_argument("--db", help="DB 파일 경로 (기본값: config.toml의 db.path)")
    init_db.set_defaults(func=cmd_init_db)

    load_mapping = commands.add_parser("load-mapping", help="섹터 그룹과 현재 분류 매핑을 적재한다 (docs/02 §3.5)")
    load_mapping.add_argument("--scheme", required=True, choices=["WI26", "GICS"])
    load_mapping.add_argument("--db", help="DB 파일 경로 (기본값: config.toml의 db.path)")
    load_mapping.set_defaults(func=cmd_load_mapping)

    add_prices_commands(commands, open_db)

    aggregate = commands.add_parser("aggregate", help="기간 확정과 파생 테이블 산출 (docs/04 §3)")
    aggregate.add_argument("--market", required=True, choices=["KR", "US"])
    aggregate.add_argument("--full", action="store_true", help="전 기간을 다시 계산한다")
    aggregate.add_argument("--db", help="DB 파일 경로 (기본값: config.toml의 db.path)")
    aggregate.set_defaults(func=cmd_aggregate)

    daily = commands.add_parser("daily", help="수집과 집계를 이어서 실행한다 (작업 스케줄러용)")
    daily.add_argument("--market", required=True, choices=["KR", "US"])
    daily.add_argument("--db", help="DB 파일 경로 (기본값: config.toml의 db.path)")
    daily.add_argument("--no-raw", action="store_true", help="수집 원문을 저장하지 않는다")
    daily.set_defaults(func=cmd_daily)

    serve = commands.add_parser("serve", help="API와 화면을 띄운다 (docs/05, 06)")
    serve.add_argument("--port", type=int, help="기본값: config.toml의 api.port")
    serve.add_argument("--db", help="DB 파일 경로 (기본값: config.toml의 db.path)")
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args, load_config())


if __name__ == "__main__":
    sys.exit(main())

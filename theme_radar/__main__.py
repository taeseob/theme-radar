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
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args, load_config())


if __name__ == "__main__":
    sys.exit(main())

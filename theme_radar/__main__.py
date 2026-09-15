"""CLI 진입점: python -m theme_radar <명령> (docs/09 §4.6)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from theme_radar.config import load_config, resolve_path
from theme_radar.db import connect, migrate, schema_version


def cmd_init_db(args: argparse.Namespace, config: dict[str, Any]) -> int:
    path = Path(args.db) if args.db else resolve_path(config["db"]["path"])
    con = connect(path, busy_timeout_ms=config["db"]["busy_timeout_ms"])
    try:
        applied = migrate(con)
        version = schema_version(con)
    finally:
        con.close()
    print(f"DB: {path}")
    print(f"스키마 버전: {version} (이번에 적용: {', '.join(m.path.name for m in applied) or '없음'})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m theme_radar")
    commands = parser.add_subparsers(dest="command", required=True, metavar="<명령>")

    init_db = commands.add_parser("init-db", help="DB 파일을 만들고 스키마 마이그레이션을 적용한다")
    init_db.add_argument("--db", help="DB 파일 경로 (기본값: config.toml의 db.path)")
    init_db.set_defaults(func=cmd_init_db)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args, load_config())


if __name__ == "__main__":
    sys.exit(main())

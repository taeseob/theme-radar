"""번호순 SQL 마이그레이션을 적용한다 (docs/09 §4.1).

파일 이름은 NNNN_설명.sql이고 번호는 1부터 빠짐없이 이어진다.
적용한 마지막 번호는 PRAGMA user_version에 기록한다.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_FILE_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    path: Path


def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations = []
    for path in directory.glob("*.sql"):
        match = _FILE_NAME.match(path.name)
        if not match:
            raise ValueError(f"마이그레이션 파일 이름이 NNNN_설명.sql 형식이 아니다: {path.name}")
        migrations.append(Migration(int(match.group(1)), path))
    migrations.sort(key=lambda m: m.version)
    if [m.version for m in migrations] != list(range(1, len(migrations) + 1)):
        raise ValueError(f"마이그레이션 번호가 1부터 연속이 아니다: {[m.path.name for m in migrations]}")
    return migrations


def schema_version(con: sqlite3.Connection) -> int:
    return con.execute("PRAGMA user_version").fetchone()[0]


def migrate(con: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """아직 적용하지 않은 마이그레이션을 파일마다 한 트랜잭션으로 적용하고, 적용한 목록을 돌려준다.

    con은 connect()로 연 자동 커밋 모드 연결이어야 한다.
    """
    current = schema_version(con)
    migrations = list_migrations(directory)
    if current > len(migrations):
        raise RuntimeError(f"DB 스키마 버전({current})이 코드의 최신 마이그레이션({len(migrations)})보다 높다")
    applied = []
    for migration in migrations[current:]:
        sql = migration.path.read_text(encoding="utf-8")
        try:
            con.executescript(f"BEGIN IMMEDIATE;\n{sql}\n;PRAGMA user_version = {migration.version};\nCOMMIT;")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        applied.append(migration)
    return applied

"""SQLite 연결과 쓰기 트랜잭션 (docs/09 §4.1)."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_BUSY_TIMEOUT_MS = 30_000


def connect(path: Path, *, readonly: bool = False, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """DB 연결을 연다.

    자동 커밋 모드로 열므로 여러 문장을 묶어 쓸 때는 transaction()을 쓴다.
    읽기 전용 연결(API용)은 파일이 없으면 실패하고, 쓰기 문장을 거부한다.
    """
    if readonly:
        con = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, autocommit=True)
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, autocommit=True)
        con.execute("PRAGMA journal_mode = WAL")  # 배치가 쓰는 중에도 API가 마지막 커밋을 읽는다
    con.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """쓰기 트랜잭션. 블록이 예외로 끝나면 롤백한다.

    BEGIN IMMEDIATE로 쓰기 잠금을 먼저 잡는다. 읽기로 시작한 트랜잭션이 도중에 쓰기로 바뀌면
    busy_timeout을 기다리지 않고 SQLITE_BUSY로 실패할 수 있기 때문이다.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise

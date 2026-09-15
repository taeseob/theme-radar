"""작업 실행 기록(batch_run)과 검증 결과(validation_result) (docs/04 §7)."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from theme_radar.db import transaction


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Check:
    rule_code: str
    scope: str
    severity: str          # BLOCK | WARN
    passed: bool
    observed: float | None = None
    tolerance: float | None = None
    detail: str | None = None


@dataclass
class Run:
    con: sqlite3.Connection
    run_id: int
    checks: list[Check] = field(default_factory=list)
    row_count: int = 0

    def check(self, rule_code: str, scope: str, severity: str, passed: bool, **kwargs) -> None:
        self.checks.append(Check(rule_code, scope, severity, passed, **kwargs))

    @property
    def blocked(self) -> bool:
        return any(c.severity == "BLOCK" and not c.passed for c in self.checks)


@contextmanager
def run_job(con: sqlite3.Connection, job_name: str, market_code: str | None = None,
            target_scope: str | None = None) -> Iterator[Run]:
    """batch_run 행을 RUNNING으로 만들고, 끝나면 상태와 검증 결과를 기록한다.

    예외가 나면 FAILED, 차단 검증이 하나라도 실패하면 BLOCKED, 그 밖에는 SUCCEEDED다.
    """
    with transaction(con):
        run_id = con.execute(
            "INSERT INTO batch_run (job_name, market_code, target_scope, status, started_at) VALUES (?, ?, ?, 'RUNNING', ?)",
            (job_name, market_code, target_scope, utc_now()),
        ).lastrowid
    run = Run(con, run_id)
    status, message = "SUCCEEDED", None
    try:
        yield run
        if run.blocked:
            status = "BLOCKED"
    except BaseException as err:
        status, message = "FAILED", f"{type(err).__name__}: {err}"
        raise
    finally:
        if con.in_transaction:
            con.execute("ROLLBACK")
        with transaction(con):
            con.executemany(
                "INSERT OR REPLACE INTO validation_result (run_id, rule_code, scope, severity, passed, observed, tolerance, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_id, c.rule_code, c.scope, c.severity, int(c.passed), c.observed, c.tolerance, c.detail) for c in run.checks],
            )
            con.execute("UPDATE batch_run SET status = ?, finished_at = ?, row_count = ?, message = ? WHERE run_id = ?",
                        (status, utc_now(), run.row_count, message, run_id))

import os
import sqlite3
import subprocess
import sys

import pytest

from theme_radar.__main__ import main
from theme_radar.config import ROOT
from theme_radar.db import connect, migrate, schema_version, transaction

EXPECTED_TABLES = {
    # 마스터
    "market", "universe", "security", "universe_membership",
    "classification_scheme", "classification_group", "security_group_map",
    # 원천
    "trading_calendar", "price_daily", "shares_observation", "corporate_action", "special_event",
    # 파생
    "period_calendar", "security_period_return", "universe_period_stat",
    "group_period_stat", "group_member_contribution",
    # 운영
    "batch_run", "validation_result", "restatement_log", "recalc_request",
}


def table_names(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}


def test_init_creates_all_tables_as_strict(con):
    assert table_names(con) == EXPECTED_TABLES
    strict = dict(con.execute("SELECT name, strict FROM pragma_table_list WHERE schema = 'main' AND name NOT LIKE 'sqlite_%'"))
    assert {name for name, is_strict in strict.items() if not is_strict} == set()
    assert schema_version(con) == 1


def test_seed_codes(con):
    assert con.execute("SELECT market_code, currency FROM market ORDER BY 1").fetchall() == [("KR", "KRW"), ("US", "USD")]
    assert con.execute("SELECT universe_code, market_code FROM universe ORDER BY 1").fetchall() == [
        ("KR_COMMON", "KR"), ("US_SP500", "US")]
    assert con.execute("SELECT scheme_code, market_code, is_exclusive FROM classification_scheme ORDER BY 1").fetchall() == [
        ("GICS", "US", 1), ("WI26", "KR", 1)]


def test_migrate_twice_applies_nothing(db_path):
    con = connect(db_path)
    assert [m.version for m in migrate(con)] == [1]
    assert migrate(con) == []
    assert schema_version(con) == 1
    con.close()


def test_connection_settings(con, db_path):
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 30000

    ro = connect(db_path, readonly=True)
    assert ro.execute("SELECT COUNT(*) FROM market").fetchone()[0] == 2
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        ro.execute("INSERT INTO market VALUES ('JP', '일본', 'JPY', 'Asia/Tokyo')")
    ro.close()


def test_readonly_connection_requires_existing_file(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        connect(tmp_path / "missing.sqlite3", readonly=True)


def test_transaction_rolls_back_on_error(con):
    with pytest.raises(RuntimeError):
        with transaction(con):
            con.execute("INSERT INTO market VALUES ('JP', '일본', 'JPY', 'Asia/Tokyo')")
            raise RuntimeError("중단")
    assert con.execute("SELECT COUNT(*) FROM market WHERE market_code = 'JP'").fetchone()[0] == 0
    assert not con.in_transaction


def test_failed_migration_leaves_previous_version(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_first.sql").write_text("CREATE TABLE a (x INTEGER) STRICT;", encoding="utf-8")
    (migrations / "0002_broken.sql").write_text("CREATE TABLE b (x INTEGER) STRICT;\nINSERT INTO missing VALUES (1);", encoding="utf-8")
    con = connect(tmp_path / "m.sqlite3")

    with pytest.raises(sqlite3.OperationalError):
        migrate(con, migrations)

    assert schema_version(con) == 1
    assert table_names(con) == {"a"}
    assert not con.in_transaction
    con.close()


def test_migration_numbers_must_be_contiguous(tmp_path):
    (tmp_path / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0003_third.sql").write_text("SELECT 1;", encoding="utf-8")
    con = connect(tmp_path / "m.sqlite3")
    with pytest.raises(ValueError, match="연속"):
        migrate(con, tmp_path)
    con.close()


def test_db_newer_than_code_is_rejected(db_path):
    con = connect(db_path)
    con.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="99"):
        migrate(con)
    con.close()


def test_cli_init_db(tmp_path, capsys):
    path = tmp_path / "cli" / "theme_radar.sqlite3"
    assert main(["init-db", "--db", str(path)]) == 0
    assert "0001_init.sql" in capsys.readouterr().out
    assert main(["init-db", "--db", str(path)]) == 0
    assert "없음" in capsys.readouterr().out


def test_cli_runs_as_module(tmp_path):
    path = tmp_path / "module.sqlite3"
    result = subprocess.run(
        [sys.executable, "-m", "theme_radar", "init-db", "--db", str(path)],
        cwd=ROOT, capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    con = connect(path)
    assert schema_version(con) == 1
    con.close()

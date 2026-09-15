"""섹터 그룹(classification_group)과 종목 매핑(security_group_map) 적재.

현재 분류 스냅샷 하나를 수집 시작일부터 전 기간에 적용하고, 다시 적재하면 스킴의 매핑을 통째로 바꾼다 (docs/02 §9 S-14).
"""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from theme_radar.config import ROOT
from theme_radar.db import transaction
from theme_radar.jobs import utc_now

DATA = ROOT / "data"
SCHEMES = {
    # 스킴: (시장, 분류표 파일, 코드 컬럼, 이름 컬럼, 구성종목 파일)
    "WI26": ("KR", "wi26_classification.csv", "sector_code", "sector_name", "wi26_constituents.csv"),
    "GICS": ("US", "gics_classification.csv", "sector_code", "sector_name", "gics_sp500_constituents.csv"),
}


@dataclass(frozen=True)
class LoadResult:
    groups: int
    mappings: int
    missing_tickers: list[str]
    source_batch: str


def _read_csv(path: Path) -> list[dict[str, str]]:
    """GICS 분류표는 필드 앞에 정렬용 공백이 있어 skipinitialspace가 필요하다."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f, skipinitialspace=True)]


def load_scheme(con: sqlite3.Connection, scheme: str, start_date: str) -> LoadResult:
    """그룹을 등록(갱신)하고 매핑을 교체한다. 구성종목 티커가 종목 마스터에 없으면 적재하지 않고 실패한다."""
    market, class_file, code_col, name_col, members_file = SCHEMES[scheme]
    colors = {r["group_code"]: r["color_hex"] for r in _read_csv(DATA / "group_colors.csv") if r["scheme_code"] == scheme}
    groups: dict[str, str] = {}
    for row in _read_csv(DATA / class_file):
        groups.setdefault(row[code_col], row[name_col])

    members = _read_csv(DATA / members_file)
    snapshot = members[0].get("base_date") if scheme == "WI26" else None
    source_batch = f"{members_file}" + (f"@{snapshot}" if snapshot else "")
    tickers = {r[0]: r[1] for r in con.execute(
        "SELECT ticker, security_id FROM security WHERE market_code = ? ORDER BY delisting_date IS NULL", (market,))}
    missing = sorted({r["ticker"] for r in members if r["ticker"] not in tickers})
    unknown_groups = sorted({r["sector_code"] for r in members if r["sector_code"] not in groups})
    if unknown_groups:
        raise ValueError(f"{members_file}에 분류표에 없는 섹터 코드가 있다: {unknown_groups}")
    if missing:
        return LoadResult(len(groups), 0, missing, source_batch)

    with transaction(con):
        for order, (code, name) in enumerate(groups.items(), 1):
            con.execute(
                "INSERT INTO classification_group (scheme_code, group_code, group_name, group_name_en, sort_order, color_hex, valid_from) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (scheme_code, group_code) DO UPDATE SET "
                "group_name = excluded.group_name, group_name_en = excluded.group_name_en, sort_order = excluded.sort_order, "
                "color_hex = excluded.color_hex",
                (scheme, code, name, name if scheme == "GICS" else None, order, colors.get(code), start_date))
        had_mapping = con.execute("SELECT COUNT(*) FROM security_group_map WHERE scheme_code = ?", (scheme,)).fetchone()[0]
        con.execute("DELETE FROM security_group_map WHERE scheme_code = ?", (scheme,))
        con.executemany(
            "INSERT INTO security_group_map (scheme_code, security_id, group_code, valid_from, source_batch) VALUES (?, ?, ?, ?, ?)",
            [(scheme, tickers[r["ticker"]], r["sector_code"], start_date, source_batch) for r in members])
        if had_mapping:
            con.execute("INSERT INTO recalc_request (market_code, scheme_code, from_date, reason, detail, requested_at) "
                        "VALUES (?, ?, ?, 'MAPPING', ?, ?)", (market, scheme, start_date, source_batch, utc_now()))
    return LoadResult(len(groups), len(members), [], source_batch)

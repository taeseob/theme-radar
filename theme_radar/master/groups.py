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


@dataclass(frozen=True)
class Spec:
    """스킴 하나가 어느 파일의 어느 컬럼을 읽는지. 같은 파일에서 계층만 달리 읽는 스킴이 있다."""
    market: str
    class_file: str        # 분류표
    code_col: str          # 분류표의 그룹 코드 컬럼
    name_col: str          # 분류표의 그룹 이름 컬럼
    members_file: str      # 구성종목
    member_code_col: str   # 구성종목의 그룹 코드 컬럼
    name_is_en: bool       # 그룹 이름이 영문이면 group_name_en 에도 같은 값을 넣는다


SCHEMES = {
    "WI26": Spec("KR", "wi26_classification.csv", "sector_code", "sector_name",
                 "wi26_constituents.csv", "sector_code", name_is_en=False),
    "WI26_SUB": Spec("KR", "wi26_classification.csv", "sub_sector_code", "sub_sector_name",
                     "wi26_sub_constituents.csv", "sub_sector_code", name_is_en=False),
    "GICS": Spec("US", "gics_classification.csv", "sector_code", "sector_name",
                 "gics_sp500_constituents.csv", "sector_code", name_is_en=True),
    "GICS_IND": Spec("US", "gics_classification.csv", "industry_code", "industry_name",
                     "gics_sp500_constituents.csv", "industry_code", name_is_en=True),
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
    spec = SCHEMES[scheme]
    colors = {r["group_code"]: r["color_hex"] for r in _read_csv(DATA / "group_colors.csv") if r["scheme_code"] == scheme}
    groups: dict[str, str] = {}
    for row in _read_csv(DATA / spec.class_file):
        groups.setdefault(row[spec.code_col], row[spec.name_col])

    members = _read_csv(DATA / spec.members_file)
    snapshot = members[0].get("base_date")
    source_batch = f"{spec.members_file}" + (f"@{snapshot}" if snapshot else "")
    tickers = {r[0]: r[1] for r in con.execute(
        "SELECT ticker, security_id FROM security WHERE market_code = ? ORDER BY delisting_date IS NULL", (spec.market,))}
    missing = sorted({r["ticker"] for r in members if r["ticker"] not in tickers})
    unknown = sorted({r[spec.member_code_col] for r in members if r[spec.member_code_col] not in groups})
    if unknown:
        raise ValueError(f"{spec.members_file}에 분류표에 없는 {spec.member_code_col} 값이 있다: {unknown}")
    if missing:
        return LoadResult(len(groups), 0, missing, source_batch)

    with transaction(con):
        for order, (code, name) in enumerate(groups.items(), 1):
            con.execute(
                "INSERT INTO classification_group (scheme_code, group_code, group_name, group_name_en, sort_order, color_hex, valid_from) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (scheme_code, group_code) DO UPDATE SET "
                "group_name = excluded.group_name, group_name_en = excluded.group_name_en, sort_order = excluded.sort_order, "
                "color_hex = excluded.color_hex",
                (scheme, code, name, name if spec.name_is_en else None, order, colors.get(code), start_date))
        had_mapping = con.execute("SELECT COUNT(*) FROM security_group_map WHERE scheme_code = ?", (scheme,)).fetchone()[0]
        con.execute("DELETE FROM security_group_map WHERE scheme_code = ?", (scheme,))
        con.executemany(
            "INSERT INTO security_group_map (scheme_code, security_id, group_code, valid_from, source_batch) VALUES (?, ?, ?, ?, ?)",
            [(scheme, tickers[r["ticker"]], r[spec.member_code_col], start_date, source_batch) for r in members])
        if had_mapping:
            con.execute("INSERT INTO recalc_request (market_code, scheme_code, from_date, reason, detail, requested_at) "
                        "VALUES (?, ?, ?, 'MAPPING', ?, ?)", (spec.market, scheme, start_date, source_batch, utc_now()))
    return LoadResult(len(groups), len(members), [], source_batch)

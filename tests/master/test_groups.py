"""분류 스킴 적재 (theme_radar/master/groups.py).

스킴이 어느 파일의 어느 컬럼을 읽는지가 `SCHEMES` 한 곳에 적혀 있다. 계층만 다른 스킴이 같은 파일을
공유하므로, 선언한 컬럼이 실제로 있는지와 코드 집합이 서로 맞는지를 데이터 파일에 대고 확인한다.
"""
from __future__ import annotations

import pytest

from theme_radar.db import transaction
from theme_radar.master.groups import DATA, SCHEMES, _read_csv, load_scheme

START = "2025-01-01"


def codes(spec) -> tuple[set[str], set[str]]:
    """(분류표의 그룹 코드, 구성종목이 쓰는 그룹 코드)"""
    return ({r[spec.code_col] for r in _read_csv(DATA / spec.class_file)},
            {r[spec.member_code_col] for r in _read_csv(DATA / spec.members_file)})


@pytest.mark.parametrize("scheme", list(SCHEMES))
def test_declared_columns_exist(scheme):
    spec = SCHEMES[scheme]
    for path, columns in ((DATA / spec.class_file, (spec.code_col, spec.name_col)),
                          (DATA / spec.members_file, (spec.member_code_col, "ticker"))):
        assert path.exists(), path
        header = _read_csv(path)[0]
        assert set(columns) <= set(header), f"{path.name}에 {set(columns) - set(header)} 컬럼이 없다"


@pytest.mark.parametrize("scheme", list(SCHEMES))
def test_member_codes_are_all_in_classification(scheme):
    known, used = codes(SCHEMES[scheme])
    assert used <= known, f"{scheme}: 분류표에 없는 코드 {sorted(used - known)}"


@pytest.mark.parametrize("scheme", list(SCHEMES))
def test_every_group_has_a_color(scheme):
    known, _ = codes(SCHEMES[scheme])
    colored = {r["group_code"] for r in _read_csv(DATA / "group_colors.csv") if r["scheme_code"] == scheme}
    assert known <= colored, f"{scheme}: 색이 없는 그룹 {sorted(known - colored)}"


def add_us_securities(con, spec) -> int:
    rows = _read_csv(DATA / spec.members_file)
    with transaction(con):
        con.executemany(
            "INSERT INTO security (market_code, board, ticker, name_local, security_type, currency) "
            "VALUES ('US', 'NYSE', ?, ?, 'COMMON', 'USD')", [(r["ticker"], r["company_name"]) for r in rows])
    return len(rows)


def test_gics_levels_load_side_by_side(con):
    """섹터와 산업은 같은 파일의 다른 컬럼이다. 배타성은 스킴 안에서만 따지므로 둘이 공존한다."""
    members = add_us_securities(con, SCHEMES["GICS_IND"])

    sector = load_scheme(con, "GICS", START)
    industry = load_scheme(con, "GICS_IND", START)

    assert (sector.groups, sector.mappings) == (11, members)
    assert (industry.groups, industry.mappings) == (74, members)
    assert industry.missing_tickers == []

    placed = dict(con.execute(
        "SELECT m.scheme_code, m.group_code FROM security_group_map m JOIN security s USING (security_id) "
        "WHERE s.ticker = 'NVDA'"))
    assert placed == {"GICS": "45", "GICS_IND": "453010"}

    named = con.execute("SELECT group_name, group_name_en, color_hex FROM classification_group "
                        "WHERE scheme_code = 'GICS_IND' AND group_code = '453010'").fetchone()
    assert named == ("Semiconductors & Semiconductor Equipment",
                     "Semiconductors & Semiconductor Equipment", "#2a78d6")   # 상위 섹터 45(IT)의 색


def test_reload_requests_recalculation(con):
    """이미 매핑이 있는 스킴을 다시 적재하면 재계산 요청이 남는다."""
    add_us_securities(con, SCHEMES["GICS_IND"])
    load_scheme(con, "GICS_IND", START)
    assert con.execute("SELECT COUNT(*) FROM recalc_request").fetchone()[0] == 0

    load_scheme(con, "GICS_IND", START)
    assert con.execute("SELECT market_code, scheme_code, reason FROM recalc_request").fetchall() == [
        ("US", "GICS_IND", "MAPPING")]

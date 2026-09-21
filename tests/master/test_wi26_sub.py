"""WI26 소분류 매핑 파일의 정합성 (data/wi26_sub_constituents.csv).

소분류는 WiseIndex에서 직접 받을 수 없어 WICS 소분류를 경유해 파생한 값이다(data/README §7).
수집 스크립트가 통과시킨 검증을 파일에 대고 다시 확인한다. 네트워크는 쓰지 않는다.
"""
from __future__ import annotations

import collections

import pytest

from theme_radar.master.groups import DATA, _read_csv


@pytest.fixture(scope="module")
def files():
    return {name: _read_csv(DATA / f"{name}.csv") for name in
            ("wi26_classification", "wi26_constituents", "wi26_sub_constituents", "wi26_wics_map")}


def test_same_tickers_as_the_sector_level(files):
    """WI26과 WI26_SUB의 티커 집합이 같아야 두 스킴의 유니버스 커버리지가 같다."""
    assert ({r["ticker"] for r in files["wi26_sub_constituents"]}
            == {r["ticker"] for r in files["wi26_constituents"]})
    assert len({r["ticker"] for r in files["wi26_sub_constituents"]}) == len(files["wi26_sub_constituents"])


def test_derived_sub_sector_belongs_to_the_original_sector(files):
    """파생한 소분류의 상위 대분류가 WiseIndex에서 받은 대분류와 같은지 — 이 파일의 근거다."""
    parent = {r["sub_sector_code"]: r["sector_code"] for r in files["wi26_classification"]}
    wrong = [(r["ticker"], r["company_name"], parent[r["sub_sector_code"]], r["sector_code"])
             for r in files["wi26_sub_constituents"] if parent[r["sub_sector_code"]] != r["sector_code"]]
    assert wrong == []


def test_every_sub_sector_has_members(files):
    counts = collections.Counter(r["sub_sector_code"] for r in files["wi26_sub_constituents"])
    assert set(counts) == {r["sub_sector_code"] for r in files["wi26_classification"]}
    assert min(counts.values()) >= 1


def test_wics_route_matches_the_map(files):
    """WICS 소분류를 경유한 종목은 대응표가 말하는 소분류에 있어야 한다."""
    route = {r["wics_sub_code"]: r["sub_sector_code"] for r in files["wi26_wics_map"]}
    off_map = [(r["ticker"], r["wics_sub_code"]) for r in files["wi26_sub_constituents"]
               if r["wics_sub_code"] and route.get(r["wics_sub_code"]) != r["sub_sector_code"]]
    assert off_map == []


def test_only_known_sources(files):
    """예외는 source에 드러나 있어야 한다. 값의 뜻은 data/README §7.3."""
    sources = collections.Counter(r["source"].split(":")[0] for r in files["wi26_sub_constituents"])
    assert set(sources) <= {"PDF", "BRIDGE", "SECTOR_ONLY"}

    alone = collections.Counter(r["sector_code"] for r in files["wi26_classification"])
    for row in files["wi26_sub_constituents"]:
        if row["source"] == "SECTOR_ONLY":
            # 업종을 못 찾은 종목이다. 대분류에 소분류가 하나뿐일 때만 소분류가 정해진다
            assert alone[row["sector_code"]] == 1, row["ticker"]
            assert row["wics_sub_code"] == ""

import pytest

from theme_radar.prices.sources.fdr_krx import Delisting
from theme_radar.prices.sources.kind import Listing
from theme_radar.prices.sources.wiki import Change, Constituent
from theme_radar.prices.universe import kr_records, kr_security_type, us_intervals

FUNDS = {"088980"}


@pytest.mark.parametrize("code, name, industry, dept, expected", [
    ("005930", "삼성전자", "통신 및 방송 장비 제조업", "", "COMMON"),
    ("138040", "메리츠금융지주", "기타 금융업", "", "COMMON"),
    ("369370", "블리츠웨이엔터테인먼트", "창작 및 예술관련 서비스업", "", "COMMON"),
    ("395400", "SK리츠", "부동산 임대 및 공급업", "", "REIT"),
    ("0165X0", "메리츠제2호스팩", "금융 지원 서비스업", "SPAC(소속부없음)", "SPAC"),
    ("448760", "IBKS제22호스팩", "", "", "SPAC"),
    ("950130", "엑세스바이오", "", "", "DR"),
    ("900070", "글로벌에스엠", "", "외국기업(소속부없음)", "OTHER"),
    ("088980", "맥쿼리인프라", "신탁업 및 집합투자업", "", "OTHER"),
])
def test_kr_security_type(code, name, industry, dept, expected):
    assert kr_security_type(code, name, industry, dept, FUNDS) == expected


def test_kr_records_skip_konex_transfers_and_old_delistings():
    listings = [Listing("005930", "삼성전자", "KOSPI", "제조", "1975-06-11"), Listing("123456", "코넥스사", "KONEX", "제조", None)]
    delistings = [
        Delisting("057050", "현대홈쇼핑", "KOSPI", "2010-09-13", "2026-07-20", 12_000_000, "완전자회사화"),
        Delisting("005930", "삼성전자", "KOSPI", None, "2025-06-01", None, "이전상장"),      # 현재 상장 중 → 폐지 아님
        Delisting("222222", "옛회사", "KOSDAQ", None, "2024-12-30", None, "감사의견"),       # 수집 시작일 이전
        Delisting("333333", "코넥스폐지", "KONEX", None, "2025-03-01", None, ""),
    ]
    records = kr_records(listings, {}, delistings, "2025-01-01", FUNDS)
    assert [(r.ticker, r.delisting_date) for r in records] == [("005930", None), ("057050", "2026-07-20")]


def c(ticker, added=None, cik="0000000001"):
    return Constituent(ticker, ticker, cik, added)


def test_us_intervals_walk_changes_backward():
    current = [c("AAA"), c("NEW", "2025-06-02"), c("ECHO", "2026-03-23")]
    changes = [
        Change("2026-03-23", "SATS", "EchoStar", "OLD2", "Old Two", ""),      # 편입 뒤 SATS → ECHO 티커 변경
        Change("2025-12-22", None, None, "SOLS", "Solstice", ""),
        Change("2025-10-30", "SOLS", "Solstice", None, None, ""),            # 편입 후 편출
        Change("2025-06-02", "NEW", "New Co", "OLD1", "Old One", ""),
        Change("2020-01-01", "AAA", "Ancient", None, None, ""),              # 수집 시작일 이전은 무시
    ]
    intervals, renames, warnings = us_intervals(current, changes, "2025-01-01")
    assert renames == {"SATS": "ECHO"}
    assert warnings == []
    assert [(i.ticker, i.valid_from, i.valid_to) for i in intervals] == [
        ("AAA", "2025-01-01", "9999-12-31"),
        ("ECHO", "2026-03-23", "9999-12-31"),
        ("NEW", "2025-06-02", "9999-12-31"),
        ("OLD1", "2025-01-01", "2025-06-01"),
        ("OLD2", "2025-01-01", "2026-03-22"),
        ("SOLS", "2025-10-30", "2025-12-21"),
    ]


def test_us_intervals_warn_when_rename_is_ambiguous():
    current = [c("ECHO", "2026-03-23"), c("OTHER", "2026-03-23")]
    changes = [Change("2026-03-23", "SATS", "EchoStar", None, None, "")]
    _, renames, warnings = us_intervals(current, changes, "2025-01-01")
    assert renames == {} and len(warnings) == 2

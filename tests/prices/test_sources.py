"""출처 응답 파서. 실측 응답 형식을 줄여 옮긴 예시로 확인한다."""
import json

import pandas as pd
import pytest

from theme_radar.prices.net import HttpError
from theme_radar.prices.sources import daum, fdr_krx, kind, naver, sec, wiki, yf


def test_parse_sise_is_not_json():
    body = ("\n [['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],\n\n\t\t\n"
            '["20260715", 87300, 87300, 87300, 87300, 1200, 15.4],\n\t\t\n'
            '["20260716", 0, 0, 0, 87300, 0, 15.4]\n\n]').encode("utf-8")
    bars = naver.parse_sise(body)
    assert [(b.trade_date, b.close, b.no_trade) for b in bars] == [("2026-07-15", 87300, False), ("2026-07-16", 87300, True)]


def test_parse_mobile_page_strips_commas():
    body = json.dumps([{"localTradedAt": "2026-09-15", "closePrice": "250,500"}]).encode("utf-8")
    assert naver.parse_mobile_page(body) == [naver.RawClose("2026-09-15", 250500.0)]


def test_parse_daum_quote_handles_delisted():
    assert daum.parse_quote(b'{"tradeDate": "20260915", "listedShareCount": 5846278608}') == daum.Quote("2026-09-15", 5846278608, False)
    assert daum.parse_quote(b'{"tradeDate": null, "listedShareCount": null, "isDelisted": true}') == daum.Quote(None, None, True)


def test_parse_kind_listing_dedupes_codes():
    html = ("<table><tr><th>회사명</th><th>시장구분</th><th>종목코드</th><th>업종</th><th>주요제품</th><th>상장일</th></tr>"
            "<tr><td>삼성전자</td><td>\n 유가 \n</td><td>005930</td><td>제조</td><td>반도체</td><td>1975-06-11</td></tr>"
            "<tr><td>삼성전자</td><td>유가</td><td>005930</td><td>제조</td><td>반도체</td><td>1975-06-11</td></tr>"
            "<tr><td>엔에이치스팩34호</td><td>코스닥</td><td>0197V0</td><td>금융 지원 서비스업</td><td>합병</td><td>2026-09-10</td></tr></table>")
    listings = kind.parse_listing(html.encode("euc-kr"))
    assert [(x.code, x.board, x.listing_date) for x in listings] == [("005930", "KOSPI", "1975-06-11"), ("0197V0", "KOSDAQ", "2026-09-10")]


def test_parse_wiki_changes():
    html = ('<table id="changes"><tr><th>Effective Date</th><th>Added</th><th>Removed</th><th>Reason</th><th>Refs</th></tr>'
            "<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>"
            "<tr><td>August 18, 2026</td><td>RDDT</td><td>Reddit</td><td>AVB</td><td>AvalonBay</td><td>acquired</td><td>[2]</td></tr>"
            "<tr><td>June 29, 2026</td><td>HONA</td><td>Honeywell Aerospace</td><td></td><td></td><td>spin-off</td><td></td></tr></table>")
    changes = wiki.parse_changes(html)
    assert [(x.effective_date, x.added, x.removed) for x in changes] == [("2026-08-18", "RDDT", "AVB"), ("2026-06-29", "HONA", None)]


def fact(end, val, accn, filed):
    return {"end": end, "val": val, "accn": accn, "filed": filed}


def test_parse_sec_shares_dedupes_amendments_and_detects_classes():
    body = json.dumps({"units": {"shares": [
        fact("2025-09-30", 100, "a1", "2025-10-20"), fact("2025-09-30", 101, "a2", "2025-11-01"),   # 정정 공시
        fact("2025-12-31", 0, "a3", "2026-01-20"),                                                  # 오류 값
    ]}}).encode()
    facts, multi = sec.parse_shares(body)
    assert [(f.end, f.shares) for f in facts] == [("2025-09-30", 101)] and not multi

    body = json.dumps({"units": {"shares": [fact("2025-09-30", 100, "a1", "2025-10-20"), fact("2025-09-30", 50, "a1", "2025-10-20")]}}).encode()
    assert sec.parse_shares(body)[1] is True
    assert sec.parse_shares(b'{"units": {"shares": {}}}') == ([], False)


def test_frame_to_index_bars_skips_empty_days():
    """지수 일봉은 거래일 캘린더의 원천이기도 하다. 값이 빈 날은 캔들을 만들 수 없어 건너뛴다 (docs/07 §12)."""
    index = pd.to_datetime(["2026-09-14", "2026-09-15"])
    df = pd.DataFrame({("Open", "^GSPC"): [6500.0, float("nan")], ("High", "^GSPC"): [6560.0, 6600.0],
                       ("Low", "^GSPC"): [6480.0, 6500.0], ("Close", "^GSPC"): [6540.0, 6580.0]}, index=index)
    bars = yf.frame_to_index_bars(df, "^GSPC")
    assert [(b.trade_date, b.open, b.high, b.low, b.close) for b in bars] == [("2026-09-14", 6500.0, 6560.0, 6480.0, 6540.0)]
    assert yf.frame_to_index_bars(pd.DataFrame(), "^GSPC") == []

DELISTING_CSV = "\n".join([
    ",Symbol,Name,Market,SecuGroup,Kind,ListingDate,DelistingDate,Reason,ArrantEnforceDate,ArrantEndDate,"
    "Industry,ParValue,ListingShares,ToSymbol,ToName",
    '0,057050,현대홈쇼핑,KOSPI,주권,보통주,2010-09-13,2026-07-20,완전자회사화,,,,5000,"12,000,000",,',
    "1,028740,경성전기,KOSPI,주권,,1956-03-03,1961-06-30,상장폐지유예기간종료,,,,,,,",
    "2,111111,어떤우선주,KOSDAQ,주권,우선주,2020-01-01,2026-08-01,감사의견,,,,500,1000,,",
    "3,222222,옛회사,KOSDAQ,주권,보통주,2010-01-01,2024-12-30,감사의견,,,,500,2000,,",
]).encode("utf-8")


class FakeFetcher:
    """날짜별 캐시 파일 중 있는 것만 돌려준다. 없는 날은 실제 원격과 같이 404다."""

    def __init__(self, available: dict[str, bytes]):
        self.available = available
        self.tried: list[str] = []

    def get(self, url: str, *, headers=None, raw=None) -> bytes:
        day = url.rsplit("/", 1)[-1].removesuffix(".csv")
        self.tried.append(day)
        if day not in self.available:
            raise HttpError(url, 404, "Not Found")
        return self.available[day]


def test_parse_delistings_keeps_common_stock_in_window():
    """캐시 파일은 1956년부터 누적이라 수집 구간 밖과 보통주 아닌 행을 걸러야 한다."""
    rows = fdr_krx.parse_delistings(DELISTING_CSV, "2025-01-01", "2026-09-28")
    assert [(r.code, r.board, r.delisting_date, r.listing_shares) for r in rows] == [
        ("057050", "KOSPI", "2026-07-20", 12_000_000)]


def test_fetch_delistings_walks_back_to_newest_available_file():
    """캐시 생성이 끊겨도(2026-09-18~28 실측) 마지막 파일로 이어 간다. 내용이 누적이라 그래도 된다."""
    fetcher = FakeFetcher({"2026-09-17": DELISTING_CSV})
    snapshot = fdr_krx.fetch_delistings(fetcher, "2025-01-01", "2026-09-28")
    assert snapshot.base_date == "2026-09-17"
    assert [r.code for r in snapshot.rows] == ["057050"]
    assert fetcher.tried[0] == "2026-09-28" and fetcher.tried[-1] == "2026-09-17"


def test_fetch_delistings_fails_loudly_when_cache_is_gone():
    """FDR은 빈 DataFrame을 돌려줘 엉뚱한 KeyError가 났다. 없으면 없다고 끝낸다."""
    fetcher = FakeFetcher({})
    with pytest.raises(RuntimeError, match="상장폐지 목록 캐시"):
        fdr_krx.fetch_delistings(fetcher, "2025-01-01", "2026-09-28", lookback_days=3)
    assert fetcher.tried == ["2026-09-28", "2026-09-27", "2026-09-26"]


def test_fetch_delistings_does_not_swallow_other_errors():
    class Broken(FakeFetcher):
        def get(self, url, *, headers=None, raw=None):
            raise HttpError(url, 503, "Service Unavailable")

    with pytest.raises(HttpError):
        fdr_krx.fetch_delistings(Broken({}), "2025-01-01", "2026-09-28")

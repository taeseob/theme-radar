"""출처 응답 파서. 실측 응답 형식을 줄여 옮긴 예시로 확인한다."""
import json

from theme_radar.prices.sources import daum, kind, naver, sec, wiki


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

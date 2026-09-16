"""docs/05 명세대로 응답하는지 확인한다. 데이터는 tests/factories.py의 작은 시장이다."""
import threading

import pytest
from fastapi.testclient import TestClient

from theme_radar.api.app import create_app
from theme_radar.calc import aggregate
from theme_radar.config import load_config
from theme_radar.db import connect, transaction
from theme_radar.jobs import run_job

CLOSED, PROVISIONAL = "2025-W02", "2025-W03"


@pytest.fixture
def client(con, market, db_path):
    with run_job(con, "aggregate", "KR") as run:
        aggregate.run(con, run, "KR", "2025-01-16", full=True, log=lambda message: None)   # 2025-W03은 잠정
    return TestClient(create_app(load_config(), db_path))


def get(client, path, **params):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response


def test_meta_endpoints(client):
    assert {u["universe"] for u in get(client, "/api/v1/meta/universes").json()["data"]} == {"KR_COMMON", "US_SP500"}
    schemes = get(client, "/api/v1/meta/schemes", universe="KR_COMMON").json()["data"]
    assert schemes == [{"scheme": "WI26", "name": "WI26 산업분류", "type": "SECTOR", "exclusive": True, "group_count": 2}]
    groups = get(client, "/api/v1/meta/groups", universe="KR_COMMON", scheme="WI26").json()["data"]
    assert [(g["group_code"], g["name"], g["color"]) for g in groups] == [
        ("WI620", "반도체", "#2a78d6"), ("WI500", "은행", "#008300")]
    assert get(client, "/api/v1/meta/groups", universe="KR_COMMON", scheme="WI26", lang="en").json()["data"][0]["name"] == "Semiconductors"
    periods = get(client, "/api/v1/meta/periods", universe="KR_COMMON", period="W").json()["data"]
    assert [p["period_id"] for p in periods] == [CLOSED, PROVISIONAL]
    assert periods[0]["base_date"] == "2025-01-03" and periods[0]["end_date"] == "2025-01-10"


def test_ranks_series_and_top_n(client):
    body = get(client, "/api/v1/sectors/ranks", universe="KR_COMMON", scheme="WI26").json()
    assert body["meta"]["from"] == CLOSED and body["meta"]["to"] == PROVISIONAL and body["meta"]["stale"] is False
    assert [p["period_id"] for p in body["data"]["periods"]] == [CLOSED, PROVISIONAL]
    assert body["data"]["periods"][1]["is_provisional"] is True
    series = {s["group_code"]: s for s in body["data"]["series"]}
    assert set(series) == {"WI620", "WI500"} and body["data"]["others_count"] == 0
    point = series["WI620"]["points"][-1]
    assert point["rank"] == 1 and point["return"] is not None and point["member_cnt"] == 1

    top1 = get(client, "/api/v1/sectors/ranks", universe="KR_COMMON", scheme="WI26", top_n=1).json()["data"]
    assert [s["group_code"] for s in top1["series"]] == ["WI620"] and top1["others_count"] == 1


def test_returns_snapshot_is_sorted_and_carries_badges(client):
    body = get(client, "/api/v1/sectors/returns", universe="KR_COMMON", scheme="WI26", period_id=CLOSED).json()
    assert body["meta"]["period_id"] == CLOSED and body["meta"]["universe_return"] is not None
    codes = [row["group_code"] for row in body["data"]]
    assert codes == ["WI620", "WI500"]                      # 수익률 내림차순
    # 2025-W02의 반도체는 A(+4.95%)와 폐지 종목 C(0%) 둘이다
    assert body["data"][0]["rank"] == 1 and body["data"][0]["member_cnt"] == 2 and body["data"][0]["up_ratio"] == 0.5
    assert "1종목 주도" in body["data"][0]["badges"]          # 양의 기여가 A 하나뿐이다
    weighted = get(client, "/api/v1/sectors/returns", universe="KR_COMMON", scheme="WI26", period_id=CLOSED, sort="weight").json()
    assert [row["group_code"] for row in weighted["data"]][0] == "WI620"


def test_market_summary(client):
    body = get(client, "/api/v1/market/summary", universe="KR_COMMON", period_id=CLOSED).json()["data"]
    assert body["member_cnt"] == 3 and body["up_ratio"] is not None
    assert body["top_contributors"][0]["group_code"] == "WI620"
    assert "공식 지수와 다릅니다" in body["disclaimer"]
    assert body["unmapped_cap_ratio"] == 0


def test_breakdown_members_and_others_add_up(client):
    body = get(client, "/api/v1/sectors/WI620/breakdown", universe="KR_COMMON", scheme="WI26", period_id=CLOSED).json()
    data = body["data"]
    assert body["meta"]["group_code"] == "WI620" and body["meta"]["name"] == "반도체"
    assert [m["ticker"] for m in data["members"]] == ["000001", "000003"]
    assert data["others"]["member_cnt"] == 0
    total = sum(m["contribution_in_group"] for m in data["members"]) + (data["others"]["contribution_in_group"] or 0)
    assert total == pytest.approx(data["summary"]["return"], abs=1e-9)
    assert data["members"][0]["contribution_in_universe"] == pytest.approx(
        data["summary"]["base_weight"] * data["members"][0]["contribution_in_group"], rel=1e-6)


def test_breakdown_limit_splits_members_and_others(client):
    body = get(client, "/api/v1/sectors/WI500/breakdown", universe="KR_COMMON", scheme="WI26",
               period_id=PROVISIONAL, limit=1, side="top").json()["data"]
    assert len(body["members"]) == 1 and body["others"]["member_cnt"] == 1
    total = body["members"][0]["contribution_in_group"] + body["others"]["contribution_in_group"]
    assert total == pytest.approx(body["summary"]["return"], abs=1e-9)


def test_history_and_csv_export(client):
    history = get(client, "/api/v1/sectors/WI620/history", universe="KR_COMMON", scheme="WI26").json()["data"]
    assert [p["period_id"] for p in history] == [CLOSED, PROVISIONAL] and history[-1]["is_provisional"] is True
    assert get(client, "/api/v1/meta/periods", universe="KR_COMMON", last=1).json()["data"] == [
        p for p in get(client, "/api/v1/meta/periods", universe="KR_COMMON").json()["data"] if p["period_id"] == PROVISIONAL]

    csv_response = get(client, "/api/v1/export/sectors.csv", universe="KR_COMMON", scheme="WI26")
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_response.headers["content-disposition"]
    lines = csv_response.text.strip().splitlines()
    assert lines[0] == "period_id,end_date,group_code,group_name,return,rank,base_weight,contribution,member_cnt"
    assert len(lines) == 5                                   # 2기간 × 2그룹


def test_market_cap_candles_from_daily_caps(client):
    body = get(client, "/api/v1/sectors/WI620/market-cap", universe="KR_COMMON", scheme="WI26").json()
    assert body["meta"]["group_code"] == "WI620" and body["meta"]["name"] == "반도체" and body["meta"]["currency"] == "KRW"
    closed, provisional = body["data"]
    # 2025-W02: 시가는 기준일 2025-01-03 값(A 101 × 1천만 + C 200 × 1백만). C가 01-07까지 거래하고 폐지돼 저가가 기간 중에 나온다
    assert closed == {"period_id": CLOSED, "base_date": "2025-01-03", "end_date": "2025-01-10", "is_provisional": False,
                      "open": 1.21e9, "high": 1.23e9, "low": 1.04e9, "close": 1.06e9, "member_cnt": 1}
    # 2025-W03: 줄곧 오른 주라 시가가 저가, 종가가 고가다
    assert (provisional["open"], provisional["high"], provisional["low"], provisional["close"]) == (1.06e9, 1.11e9, 1.06e9, 1.11e9)
    assert provisional["is_provisional"] is True

    banks = get(client, "/api/v1/sectors/WI500/market-cap", universe="KR_COMMON", scheme="WI26", period="W",
                **{"from": PROVISIONAL}).json()["data"]
    # 기준일 B 5천만 + E 7천만, 01-14부터 E 거래정지로 행이 없어 B만 남는다
    assert [(p["open"], p["high"], p["low"], p["close"], p["member_cnt"]) for p in banks] == [(1.2e8, 1.2e8, 5e7, 5e7, 1)]


def test_market_cap_before_daily_caps_are_written(client, con):
    with transaction(con):
        con.execute("DELETE FROM group_daily_cap")
    response = client.get("/api/v1/sectors/WI620/market-cap", params={"universe": "KR_COMMON", "scheme": "WI26"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "NOT_AVAILABLE"


def test_securities_search(client):
    hits = get(client, "/api/v1/securities/search", universe="KR_COMMON", q="에이").json()["data"]
    assert [h["ticker"] for h in hits] == ["000001"] and hits[0]["group_code"] == "WI620"
    assert get(client, "/api/v1/securities/search", universe="KR_COMMON", q="000002").json()["data"][0]["name"] == "비사"


def test_events(client, con, market):
    with transaction(con):
        con.execute("INSERT INTO special_event (market_code, event_date, event_type, security_id, group_code, market_cap, "
                    "sector_share, detail, source, created_at) VALUES ('KR', '2025-01-08', 'LISTING', ?, 'WI500', 5e8, 0.12, "
                    "'비사 편입', 'KIND_LISTING', '2025-01-20T00:00:00Z')", (market["B"],))
        con.execute("INSERT INTO special_event (market_code, event_date, event_type, detail, created_at) "
                    "VALUES ('KR', '2025-01-02', 'DATA_GAP', 'G-1 근사 구간', '2025-01-20T00:00:00Z')")
    body = get(client, "/api/v1/events", universe="KR_COMMON").json()
    assert body["meta"]["by_type"] == {"LISTING": 1, "DATA_GAP": 1}
    assert [e["event_type"] for e in body["data"]] == ["LISTING", "DATA_GAP"]     # 섹터 비중이 큰 순서
    assert body["data"][0]["ticker"] == "000002" and body["data"][0]["group_name"] == "은행"
    assert body["data"][0]["source"] == "KIND_LISTING" and body["data"][1]["source"] is None
    only = get(client, "/api/v1/events", universe="KR_COMMON", type="DATA_GAP").json()["data"]
    assert len(only) == 1 and only[0]["ticker"] is None


def test_cache_headers(client, con):
    closed = client.get("/api/v1/sectors/returns", params={"universe": "KR_COMMON", "scheme": "WI26", "period_id": CLOSED})
    assert closed.headers["cache-control"] == "max-age=86400" and closed.headers["etag"].startswith('"1.0.0:')
    provisional = client.get("/api/v1/sectors/returns", params={"universe": "KR_COMMON", "scheme": "WI26", "period_id": PROVISIONAL})
    assert provisional.headers["cache-control"] == "max-age=300"

    with transaction(con):
        con.execute("INSERT INTO recalc_request (market_code, from_date, reason, requested_at) "
                    "VALUES ('KR', '2025-01-02', 'MAPPING', '2025-01-20T00:00:00Z')")
    stale = client.get("/api/v1/sectors/ranks", params={"universe": "KR_COMMON", "scheme": "WI26"})
    assert stale.json()["meta"]["stale"] is True and stale.headers["cache-control"] == "no-store"


def test_serve_uses_the_db_flag(monkeypatch, con, db_path):
    """--db는 선언만 하고 쓰지 않으면 조용히 운영 DB를 연다."""
    import argparse

    from theme_radar import __main__

    seen = {}
    monkeypatch.setattr("theme_radar.api.app.serve", lambda config: seen.update(config["db"]))
    args = argparse.Namespace(port=None, db=str(db_path))
    __main__.cmd_serve(args, load_config())
    assert seen["path"] == str(db_path)


def test_serve_stops_on_an_outdated_schema(monkeypatch, tmp_path):
    """API는 최신 스키마를 전제한다. 열 하나가 없어 요청마다 500이 나는 것보다 띄울 때 멈추는 편이 낫다."""
    import argparse

    from theme_radar import __main__

    monkeypatch.setattr("theme_radar.api.app.serve", lambda config: pytest.fail("스키마가 낡았는데 API를 띄웠다"))
    args = argparse.Namespace(port=None, db=str(tmp_path / "empty.sqlite3"))
    with pytest.raises(SystemExit, match="init-db"):
        __main__.cmd_serve(args, load_config())


def test_read_connection_survives_a_thread_hop(con, db_path):
    """FastAPI는 의존성과 엔드포인트를 스레드풀의 다른 스레드에서 돌릴 수 있다 (화면이 두 요청을 동시에 보낼 때)."""
    reader = connect(db_path, readonly=True)
    try:
        result = []
        thread = threading.Thread(target=lambda: result.append(reader.execute("SELECT COUNT(*) FROM market").fetchone()[0]))
        thread.start()
        thread.join()
        assert result == [2]
    finally:
        reader.close()


@pytest.mark.parametrize("path, params, status, code", [
    ("/api/v1/sectors/ranks", {"universe": "NOPE", "scheme": "WI26"}, 400, "INVALID_PARAMETER"),
    ("/api/v1/sectors/ranks", {"universe": "US_SP500", "scheme": "WI26"}, 400, "SCHEME_MARKET_MISMATCH"),
    ("/api/v1/sectors/ranks", {"universe": "KR_COMMON", "scheme": "WI26", "from": "2026-W60"}, 400, "INVALID_PERIOD_ID"),
    ("/api/v1/sectors/ranks", {"universe": "KR_COMMON", "scheme": "WI26", "rank_by": "nope"}, 400, "INVALID_PARAMETER"),
    ("/api/v1/sectors/NOPE/breakdown", {"universe": "KR_COMMON", "scheme": "WI26"}, 404, "NOT_FOUND"),
    ("/api/v1/sectors/NOPE/market-cap", {"universe": "KR_COMMON", "scheme": "WI26"}, 404, "NOT_FOUND"),
    ("/api/v1/sectors/returns", {"universe": "US_SP500", "scheme": "GICS"}, 409, "NOT_AVAILABLE"),
])
def test_error_responses(client, path, params, status, code):
    response = client.get(path, params=params)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code

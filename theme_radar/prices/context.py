"""수집 실행에 필요한 설정·연결·요청기를 한곳에 모은다."""
from __future__ import annotations

import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from theme_radar.config import resolve_path
from theme_radar.jobs import Run, utc_now
from theme_radar.prices.net import Fetcher, RateLimiter

MARKET_TZ = {"KR": ZoneInfo("Asia/Seoul"), "US": ZoneInfo("America/New_York")}
CLOSE_TIME = (16, 30)   # 이 시각(현지) 전에 받은 당일 행은 저장하지 않는다 (C-10)


@dataclass
class Context:
    con: sqlite3.Connection
    run: Run
    config: dict[str, Any]
    market: str
    full: bool                 # True: 수집 시작일부터 전 구간, False: 증분
    fetcher: Fetcher
    sec_fetcher: Fetcher | None
    raw_dir: Path | None
    cache_dir: Path
    started: float

    @property
    def start(self) -> str:
        return self.config["collect"]["start_date"]

    @property
    def local_now(self) -> datetime:
        return datetime.now(MARKET_TZ[self.market])

    @property
    def today(self) -> str:
        return self.local_now.date().isoformat()

    @property
    def market_closed(self) -> bool:
        now = self.local_now
        return (now.hour, now.minute) >= CLOSE_TIME

    @property
    def now(self) -> str:
        return utc_now()

    def log(self, message: str) -> None:
        print(f"[{self.market} {time.monotonic() - self.started:7.1f}s] {message}", flush=True)


def build_context(con: sqlite3.Connection, run: Run, config: dict[str, Any], market: str, full: bool,
                  save_raw: bool = True) -> Context:
    http = config["http"]
    raw_dir = resolve_path(config["collect"]["raw_dir"]) if save_raw else None
    fetcher = Fetcher(http["browser_user_agent"], RateLimiter(http["rate_per_s"]), raw_dir, http["timeout_s"], http["retries"])
    sec_fetcher = None
    if market == "US":
        agent = config["sec"]["user_agent"]
        if not agent:
            raise SystemExit("config.local.toml에 [sec] user_agent(연락처 포함)를 적어야 한다 (docs/07 §8.3)")
        sec_fetcher = Fetcher(agent, RateLimiter(config["sec"]["rate_per_s"]), raw_dir, http["timeout_s"], http["retries"])
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return Context(con, run, config, market, full, fetcher, sec_fetcher, raw_dir,
                   resolve_path(config["collect"]["cache_dir"]), time.monotonic())

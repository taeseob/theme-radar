"""직접 호출용 HTTP 요청: 재시도, 호스트별 속도 제한, 원문 저장 (docs/07 §10.3)."""
from __future__ import annotations

import gzip
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"HTTP 요청 실패 ({status}): {url} {message}")
        self.url = url
        self.status = status


class RateLimiter:
    """호스트마다 요청 사이 최소 간격을 지킨다. 여러 스레드가 함께 쓴다."""

    def __init__(self, rate_per_s: float):
        self.interval = 1.0 / rate_per_s
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next.get(host, now))
            self._next[host] = start + self.interval
        if start > now:
            time.sleep(start - now)


@dataclass
class Fetcher:
    user_agent: str
    limiter: RateLimiter
    raw_dir: Path | None = None
    timeout_s: float = 30
    retries: int = 4

    def get(self, url: str, *, headers: dict[str, str] | None = None, raw: str | None = None) -> bytes:
        """본문을 돌려준다. raw('출처/파일명')를 주면 원문을 raw_dir/출처/YYYYMMDD/파일명.gz로 저장한다.

        404는 재시도하지 않고 HttpError(status=404)로 알린다.
        """
        host = urllib.parse.urlsplit(url).netloc
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, **(headers or {})})
        for attempt in range(1, self.retries + 1):
            self.limiter.wait(host)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    body = response.read()
                break
            except urllib.error.HTTPError as err:
                if err.code not in RETRY_STATUS or attempt == self.retries:
                    raise HttpError(url, err.code, err.reason) from err
            except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
                if attempt == self.retries:
                    raise HttpError(url, None, str(err)) from err
            time.sleep(2 ** attempt)
        if raw and self.raw_dir is not None:
            save_raw(self.raw_dir, raw, body)
        return body


def save_raw(raw_dir: Path, name: str, body: bytes) -> None:
    source, _, file_name = name.partition("/")
    path = raw_dir / source / time.strftime("%Y%m%d") / f"{file_name}.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body))

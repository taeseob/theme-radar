"""FastAPI 앱 (docs/05, docs/09 §4.3). 로컬 전용이라 인증을 두지 않는다."""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from theme_radar.api.deps import ApiError
from theme_radar.api.routes import events, market, meta, sectors, securities
from theme_radar.calc import CALC_VERSION
from theme_radar.config import ROOT, load_config, resolve_path

WEB_DIR = ROOT / "web"


def create_app(config: dict[str, Any] | None = None, db_path: Path | str | None = None) -> FastAPI:
    config = config or load_config()
    app = FastAPI(title="theme-radar API", version=CALC_VERSION,
                  description="한국·미국 섹터/테마 수익률과 순위 조회 (docs/05)")
    app.state.config = config
    app.state.db_path = Path(db_path) if db_path else resolve_path(config["db"]["path"])

    for router in (meta.router, sectors.router, market.router, securities.router, events.router):
        app.include_router(router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    def api_error(request: Request, error: ApiError) -> JSONResponse:
        body: dict[str, Any] = {"code": error.code, "message": error.message}
        if error.field:
            body["field"] = error.field
        return JSONResponse({"error": body}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    def invalid_parameter(request: Request, error: RequestValidationError) -> JSONResponse:
        # FastAPI 기본값은 422다. docs/05 §1.3에 맞춰 400 INVALID_PARAMETER로 바꾼다
        first = error.errors()[0]
        field = str(first["loc"][-1]) if first.get("loc") else None
        return JSONResponse({"error": {"code": "INVALID_PARAMETER", "message": first.get("msg", "잘못된 파라미터"),
                                       "field": field}}, status_code=400)

    # 윈도는 레지스트리에 따라 .js를 text/plain으로 주기도 한다. 그러면 브라우저가 ES 모듈을 거부한다
    mimetypes.add_type("text/javascript", ".js")
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def serve(config: dict[str, Any] | None = None) -> None:
    import uvicorn

    config = config or load_config()
    uvicorn.run(create_app(config), host=config["api"]["host"], port=config["api"]["port"], log_level="info")

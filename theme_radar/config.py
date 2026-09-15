"""설정 파일을 읽는다 (docs/09 §4.7).

config.toml(git 포함)을 읽고, config.local.toml(git 제외)이 있으면 같은 키를 덮어쓴다.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.toml"
LOCAL_CONFIG_FILE = ROOT / "config.local.toml"


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path = CONFIG_FILE, local_path: Path = LOCAL_CONFIG_FILE) -> dict[str, Any]:
    with path.open("rb") as f:
        config = tomllib.load(f)
    if local_path.exists():
        with local_path.open("rb") as f:
            config = _merge(config, tomllib.load(f))
    return config


def resolve_path(value: str | Path) -> Path:
    """설정의 상대 경로는 프로젝트 루트 기준으로 해석한다."""
    path = Path(value)
    return path if path.is_absolute() else ROOT / path

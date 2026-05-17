"""Persistent config for AI check (popup classifier API settings)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AiCheckConfig:
    model: str = ""
    base_url: str = ""
    api_format: str = "openai"  # "anthropic" or "openai"
    verify_ssl: bool = True
    api_key: str = ""


def _config_path() -> Path:
    custom = os.getenv("AI_CHECK_CONFIG_FILE", "")
    if custom:
        return Path(custom)
    return Path("configs/ai_check.json")


def load_config() -> AiCheckConfig:
    path = _config_path()
    if not path.is_file():
        return AiCheckConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AiCheckConfig(
            model=data.get("model", ""),
            base_url=data.get("base_url", ""),
            api_format=data.get("api_format", "openai"),
            verify_ssl=data.get("verify_ssl", True),
            api_key=data.get("api_key", ""),
        )
    except (json.JSONDecodeError, OSError):
        return AiCheckConfig()


def save_config(config: AiCheckConfig) -> Path:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "model": config.model,
                "base_url": config.base_url,
                "api_format": config.api_format,
                "verify_ssl": config.verify_ssl,
                "api_key": config.api_key,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path

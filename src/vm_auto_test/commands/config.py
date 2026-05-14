from __future__ import annotations

from pathlib import Path

from vm_auto_test.config import load_config


def validate_config(config_path: Path) -> int:
    load_config(config_path)
    print(f"Config is valid: {config_path}")
    return 0

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path, *, override: bool = False) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {path}")
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            raise ValueError(f"Invalid env line {line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid env line {line_number}: key is empty")
        if not override and key in os.environ:
            continue
        os.environ[key] = _strip_quotes(value.strip())


def load_optional_env_file(path: Path | None) -> None:
    if path is not None:
        load_env_file(path)
        return
    default_path = Path(".env")
    if default_path.exists():
        load_env_file(default_path)


def is_env_configured() -> bool:
    vmrun = os.getenv("VMRUN_PATH", "")
    if not vmrun:
        return False
    return Path(vmrun).is_file() and bool(os.getenv("VMWARE_GUEST_USER", ""))


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from vm_auto_test.config import load_config


@dataclass(frozen=True)
class DoctorCheck:
    label: str
    status: str
    detail: str


_DOCTOR_FAIL = "FAIL"
_DOCTOR_OK = "OK"
_DOCTOR_WARN = "WARN"


def run_doctor(config_path: Path | None, reports_dir: Path) -> int:
    checks = [
        _check_python_version(),
        _check_package_version(),
        _check_vmrun_path(),
    ]
    if config_path is not None:
        checks.append(_check_config_file(config_path))
    checks.append(_check_reports_dir(reports_dir))

    print("VM Auto Test Doctor")
    print()
    for check in checks:
        print(f"[{check.status}] {check.label}: {check.detail}")
    return 3 if any(check.status == _DOCTOR_FAIL for check in checks) else 0


def _check_python_version() -> DoctorCheck:
    current = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    status = _DOCTOR_OK if sys.version_info >= (3, 10) else _DOCTOR_FAIL
    return DoctorCheck("Python", status, current)


def _check_package_version() -> DoctorCheck:
    try:
        package_version = version("vm-auto-test")
    except PackageNotFoundError:
        return DoctorCheck("Package", _DOCTOR_WARN, "vm-auto-test is importable but not installed as package")
    return DoctorCheck("Package", _DOCTOR_OK, package_version)


def _check_vmrun_path() -> DoctorCheck:
    value = os.getenv("VMRUN_PATH")
    if not value:
        return DoctorCheck("VMRUN_PATH", _DOCTOR_FAIL, "not configured")
    path = Path(_clean_path_value(value))
    if not path.is_file():
        return DoctorCheck("VMRUN_PATH", _DOCTOR_FAIL, f"not found: {path}")
    return DoctorCheck("VMRUN_PATH", _DOCTOR_OK, str(path))


def _check_config_file(config_path: Path) -> DoctorCheck:
    try:
        load_config(config_path)
    except Exception as exc:
        return DoctorCheck("Config", _DOCTOR_FAIL, f"invalid: {type(exc).__name__}")
    return DoctorCheck("Config", _DOCTOR_OK, str(config_path))


def _check_reports_dir(reports_dir: Path) -> DoctorCheck:
    try:
        reports_dir.mkdir(parents=True, exist_ok=True)
        probe_path = reports_dir / ".vm-auto-test-write-check"
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink()
    except OSError as exc:
        return DoctorCheck("Reports directory", _DOCTOR_FAIL, f"not writable: {type(exc).__name__}")
    return DoctorCheck("Reports directory", _DOCTOR_OK, str(reports_dir))


def _clean_path_value(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        return cleaned[1:-1]
    return cleaned

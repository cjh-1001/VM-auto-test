from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AvSignature:
    id: str
    name: str
    processes: tuple[str, ...]
    required: tuple[str, ...]


AV_SIGNATURES: tuple[AvSignature, ...] = (
    AvSignature(
        id="qqpc",
        name="腾讯电脑管家",
        processes=("QQPCTray.exe",),
        required=("QQPCTray.exe",),
    ),
    AvSignature(
        id="360",
        name="360安全卫士",
        processes=("360Tray.exe", "ZhuDongFangYu.exe"),
        required=("360Tray.exe",),
    ),
    AvSignature(
        id="huorong",
        name="火绒安全软件",
        processes=("HipsDaemon.exe", "HipsMain.exe", "HipsTrat.exe"),
        required=("HipsDaemon.exe",),
    ),
)


def build_detection_command() -> str:
    """Build a PowerShell one-liner to detect known AV by process name via tasklist."""
    checks = []
    for sig in AV_SIGNATURES:
        escaped = sig.required[0].replace(".", "\\.")
        checks.append(f"if ($p -match '{escaped}') {{ $r += '{sig.name}' }}")

    body = "; ".join(checks)
    return (
        f"$p = tasklist; $r = @(); {body}; "
        "if ($r.Count -gt 0) { Write-Output ($r -join ',') } else { Write-Output 'NONE' }"
    )


def parse_detection_result(stdout: str) -> str | None:
    """Parse detection output. Returns AV name string or None."""
    result = stdout.strip()
    if not result or result == "NONE":
        return None
    return result

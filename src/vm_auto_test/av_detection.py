from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AvLogProfile:
    av_name: str
    log_sources: tuple[tuple[str, str], ...]  # ((guest_path, description), ...)
    export_preset: str = ""  # tencent / huorong / 360


@dataclass(frozen=True)
class AvSignature:
    id: str
    name: str
    processes: tuple[str, ...]
    required: tuple[str, ...]
    log_profile: AvLogProfile | None = None


_360_LOG_PROFILE = AvLogProfile(
    av_name="360安全卫士",
    log_sources=(
        (r"C:\Users\{username}\AppData\Roaming\360Quarant\360safe.Summary.dat", "360 主数据库"),
        (r"C:\Users\{username}\AppData\Roaming\360Quarant\360safe.Summary.union1", "360 Union 文件"),
    ),
    export_preset="360",
)

_TENCENT_LOG_PROFILE = AvLogProfile(
    av_name="腾讯电脑管家",
    log_sources=(
        (r"C:\ProgramData\Tencent\QQPCMgr\TAVWfsDB\defenselog.db", "腾讯管家防御日志"),
    ),
    export_preset="tencent",
)

_HUORONG_LOG_PROFILE = AvLogProfile(
    av_name="火绒安全软件",
    log_sources=(
        (r"C:\ProgramData\Huorong\Sysdiag\log.db", "火绒日志数据库"),
        (r"C:\ProgramData\Huorong\Sysdiag\log.db-wal", "火绒 WAL 日志"),
        (r"C:\ProgramData\Huorong\Sysdiag\log.db-shm", "火绒 SHM 日志"),
    ),
    export_preset="huorong",
)

AV_SIGNATURES: tuple[AvSignature, ...] = (
    AvSignature(
        id="qqpc",
        name="腾讯电脑管家",
        processes=("QQPCTray.exe",),
        required=("QQPCTray.exe",),
        log_profile=_TENCENT_LOG_PROFILE,
    ),
    AvSignature(
        id="360",
        name="360安全卫士",
        processes=("360Tray.exe", "ZhuDongFangYu.exe"),
        required=("360Tray.exe",),
        log_profile=_360_LOG_PROFILE,
    ),
    AvSignature(
        id="huorong",
        name="火绒安全软件",
        processes=("HipsDaemon.exe", "HipsMain.exe", "HipsTrat.exe"),
        required=("HipsDaemon.exe",),
        log_profile=_HUORONG_LOG_PROFILE,
    ),
)


def get_log_profile(av_name: str) -> AvLogProfile | None:
    for sig in AV_SIGNATURES:
        if sig.name == av_name and sig.log_profile is not None:
            return sig.log_profile
    return None


def build_detection_command() -> str:
    """Build a PowerShell one-liner to detect known AV by process name via Get-Process."""
    checks = []
    for sig in AV_SIGNATURES:
        proc_name = sig.required[0].replace(".exe", "")
        checks.append(f"if ($p -match '{proc_name}') {{ $r += '{sig.name}' }}")

    body = "; ".join(checks)
    return (
        "$p = (Get-Process | Select-Object -ExpandProperty ProcessName) -join ' '; "
        "$r = @(); "
        f"{body}; "
        "if ($r.Count -gt 0) { Write-Output ($r -join ',') } else { Write-Output 'NONE' }"
    )


def parse_detection_result(stdout: str) -> str | None:
    """Parse detection output. Returns AV name string or None."""
    result = stdout.strip()
    if not result or result == "NONE":
        return None
    return result

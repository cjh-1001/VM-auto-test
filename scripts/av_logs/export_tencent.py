from __future__ import annotations

from pathlib import Path

from vm_auto_test.av_exporters.common import export_sqlite_tables, write_export

TABLE_LABELS = {
    "AppRunInfoList_60": "应用运行记录",
    "AppNetInfoList_days_60": "应用每日网络流量",
    "AppNetInfoList_all_60": "应用总网络流量",
    "FileInfo_60": "文件信息",
    "sqlite_sequence": "SQLite 自增序列表",
}

FIELD_LABELS = {
    "id": "记录ID",
    "fn": "文件路径",
    "ts": "运行时间",
    "ts_day": "统计日期",
    "tsfirst": "首次时间",
    "lastts": "最后时间",
    "tx": "发送字节数",
    "rx": "接收字节数",
    "pathname": "文件路径",
    "chgtm": "变更时间",
    "sha1": "SHA1",
    "hashsig": "签名哈希",
}


def export_logs(raw_files: tuple[Path, ...], output_dir: Path) -> str:
    database_path = next((raw_file for raw_file in raw_files if raw_file.exists()), None)
    if database_path is None:
        text = "腾讯电脑管家 defenselog.db 不存在"
        return write_export(output_dir, "exported.txt", (text,))
    lines = ["腾讯电脑管家日志导出", ""]
    lines.extend(export_sqlite_tables(database_path, TABLE_LABELS, FIELD_LABELS))
    return write_export(output_dir, "exported.txt", lines)

from __future__ import annotations

from pathlib import Path
from typing import Any

from vm_auto_test.av_exporters.common import (
    extract_ascii_strings,
    extract_utf16le_strings,
    decode_text,
    export_sqlite_tables,
    write_export,
)

TABLE_LABELS = {
    "CF": "配置/版本信息",
    "FI": "隔离文件索引",
    "FQ": "隔离文件详情",
    "sqlite_sequence": "SQLite 自增序列表",
}

FIELD_LABELS = {
    "K": "键",
    "V": "值",
    "VR": "版本/记录值",
    "ID": "记录ID",
    "FO": "原始文件路径",
    "FQ": "隔离区文件路径",
    "M5": "MD5",
    "S1": "SHA1",
    "S6": "SHA256",
    "SZ": "文件大小",
    "FC": "详情二进制",
    "VE": "引擎/版本标记",
    "CS": "状态标记",
}


def _describe_field(_table: str, column_name: str, value: Any) -> str:
    if column_name == "FC":
        return _format_blob(value)
    if column_name == "SZ":
        return _format_size(value)
    return decode_text(value)


def _format_blob(value: Any) -> str:
    if not isinstance(value, bytes):
        return decode_text(value)
    lines = [f"二进制数据，{len(value)} 字节"]
    utf16_strings = extract_utf16le_strings(value)
    ascii_strings = extract_ascii_strings(value)
    if ascii_strings:
        lines.append("  可识别 ASCII 字符串：")
        lines.extend(f"    - {s}" for s in ascii_strings)
    if utf16_strings:
        lines.append("  可识别 UTF-16LE 字符串：")
        lines.extend(f"    - {s}" for s in utf16_strings)
    hex_preview = value[:512].hex(" ")
    suffix = " ..." if len(value) > 512 else ""
    lines.append(f"  前 512 字节 HEX：{hex_preview}{suffix}")
    return "\n".join(lines)


def _format_size(value: Any) -> str:
    try:
        size = int(value)
        if size >= 1024 * 1024:
            return f"{size} B（{size / 1024 / 1024:.2f} MB）"
        if size >= 1024:
            return f"{size} B（{size / 1024:.2f} KB）"
        return f"{size} B"
    except (TypeError, ValueError):
        return decode_text(value)


def _export_union_file(union_path: Path, lines: list[str]) -> None:
    data = union_path.read_bytes()
    lines.append(f"Union 文件：{union_path.name}，{len(data)} 字节")
    utf16_strings = extract_utf16le_strings(data, min_chars=2)
    ascii_strings = extract_ascii_strings(data, min_chars=2)
    if ascii_strings:
        lines.append("  可识别 ASCII 字符串：")
        lines.extend(f"    - {s}" for s in ascii_strings)
    if utf16_strings:
        lines.append("  可识别 UTF-16LE 字符串：")
        lines.extend(f"    - {s}" for s in utf16_strings)
    lines.append("  HEX：" + data.hex(" "))


def export_logs(raw_files: tuple[Path, ...], output_dir: Path) -> str:
    dat_path = _first_existing(raw_files, ".dat")
    if dat_path is None:
        text = "360 Summary 数据库不存在"
        return write_export(output_dir, "exported.txt", (text,))
    lines = ["360safe Summary 日志导出", ""]
    lines.extend(export_sqlite_tables(dat_path, TABLE_LABELS, FIELD_LABELS, value_formatter=_describe_field))
    for raw_file in raw_files:
        if raw_file.suffix.lower().startswith(".union") and raw_file.exists():
            lines.append("")
            _export_union_file(raw_file, lines)
    return write_export(output_dir, "exported.txt", lines)


def _first_existing(raw_files: tuple[Path, ...], suffix: str) -> Path | None:
    for raw_file in raw_files:
        if raw_file.suffix.lower() == suffix and raw_file.exists():
            return raw_file
    return None

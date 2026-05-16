from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterable


def write_export(output_dir: Path, filename: str, lines: Iterable[str]) -> str:
    output_path = output_dir / filename
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return str(output_path)


def decode_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, bytes):
        for encoding in ("utf-8", "gb18030", "gbk", "big5", "utf-16le"):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")
    return str(value)


def extract_ascii_strings(data: bytes, min_chars: int = 4) -> list[str]:
    pattern = rb"[\x20-\x7e]{" + str(min_chars).encode() + rb",}"
    matches = re.findall(pattern, data)
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        s = m.decode("ascii")
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


def extract_utf16le_strings(data: bytes, min_chars: int = 4) -> list[str]:
    results: list[str] = []
    i = 0
    while i < len(data) - 1:
        chars: list[str] = []
        while i < len(data) - 1:
            lo = data[i]
            hi = data[i + 1]
            cp = lo | (hi << 8)
            if cp == 0:
                break
            if 0x20 <= cp <= 0x7E or 0x4E00 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF:
                try:
                    chars.append(chr(cp))
                except ValueError:
                    break
            else:
                break
            i += 2
        if len(chars) >= min_chars:
            results.append("".join(chars))
        while i < len(data) - 1 and (data[i] != 0 or data[i + 1] != 0):
            i += 1
        i += 2
    return results


def export_sqlite_tables(
    database_path: Path,
    table_labels: dict[str, str] | None = None,
    field_labels: dict[str, str] | None = None,
    value_formatter: Callable[[str, str, Any], str] | None = None,
) -> list[str]:
    if table_labels is None:
        table_labels = {}
    if field_labels is None:
        field_labels = {}

    conn = sqlite3.connect(str(database_path))
    conn.text_factory = bytes
    conn.row_factory = sqlite3.Row

    try:
        lines: list[str] = []
        tables = _load_tables(conn)

        for table_name in tables:
            quoted = '"' + table_name.replace('"', '""') + '"'
            count = conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            columns = [
                decode_text(row[1])
                for row in conn.execute(f"PRAGMA table_info({quoted})")
            ]
            label = table_labels.get(table_name, table_name)

            lines.append(f"## {label}（{table_name}）")
            lines.append(f"记录数：{count}")
            lines.append("字段：" + "、".join(columns))
            lines.append("")

            rows = conn.execute(f"SELECT * FROM {quoted}").fetchall()
            for row_index, row in enumerate(rows, start=1):
                lines.append(f"### 第 {row_index} 条记录")
                for col in row.keys():
                    col_name = decode_text(col)
                    col_label = field_labels.get(col_name, col_name)
                    raw_value = row[col]
                    if value_formatter is not None:
                        formatted = value_formatter(table_name, col_name, raw_value)
                    else:
                        formatted = decode_text(raw_value)
                    lines.append(f"- {col_label}（{col_name}）：{formatted}")
                lines.append("")

        return lines
    finally:
        conn.close()


def _load_tables(conn: sqlite3.Connection) -> list[str]:
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    return [decode_text(row[0]) for row in cursor.fetchall()]

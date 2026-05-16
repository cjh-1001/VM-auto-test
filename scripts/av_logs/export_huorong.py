from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import tempfile
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from vm_auto_test.av_exporters.common import write_export

FIELD_LABELS = {
    "id": "记录ID",
    "fid": "功能ID",
    "fname": "功能模块",
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
    "name": "名称",
    "seq": "序号",
    "guid": "事件GUID",
}

# Threat detail field labels (nested JSON inside the `detail` column)
THREAT_DETAIL_LABELS: dict[str, str] = {
    "procname": "进程路径",
    "p_procname": "父进程",
    "cmdline": "命令行",
    "p_cmdline": "父进程命令行",
    "res_cmd": "执行命令",
    "res_path": "目标文件",
    "proc_sha1": "进程SHA1",
    "description": "事件描述",
    "recname": "规则名称",
    "clsname": "规则分类",
    "xpid": "进程PID",
    "p_xpid": "父进程PID",
    "proc_atrstr": "进程属性",
    "montype": "监控类型",
}

_TREATMENT: dict[int, str] = {
    0: "放过",
    1: "询问我",
    2: "阻止",
    3: "结束进程",
}

_RISK_LEVEL: dict[int, str] = {
    0: "安全",
    1: "低风险",
    2: "中风险",
    3: "高风险",
}

_ACTION: dict[int, str] = {
    0: "允许",
    1: "阻止",
}

TABLE_LABELS = {
    "HrLogV3_60": "火绒安全日志",
    "HrTrayMsg_60": "火绒托盘消息",
    "AppRunInfoList_60": "应用运行记录",
    "AppNetInfoList_days_60": "应用每日网络流量",
    "AppNetInfoList_all_60": "应用总网络流量",
    "FileInfo_60": "文件信息",
    "sqlite_sequence": "SQLite 自增序列表",
}

_FNAME_LABELS: dict[str, str] = {
    "sysprot": "系统防护",
    "hips": "HIPS",
    "netprot": "网络防护",
    "virusprot": "病毒防护",
    "fileprot": "文件防护",
}

_CLSNAME_LABELS: dict[str, str] = {
    "risk_prot_v6": "风险防护",
}

TIME_FIELDS = {"ts", "tsfirst", "lastts", "ts_day", "chgtm"}


def decode_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        for encoding in ("utf-8", "gb18030", "gbk", "big5"):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")
    return str(value)


def format_timestamp(value: Any) -> str:
    try:
        numeric_value = int(value)
        if numeric_value > 10_000_000_000_000_000:
            utc_time = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(
                microseconds=numeric_value / 10
            )
        else:
            utc_time = datetime.fromtimestamp(numeric_value, tz=timezone.utc)
        local_time = utc_time.astimezone()
    except (TypeError, ValueError, OverflowError, OSError):
        return decode_text(value)

    return f"{local_time:%Y-%m-%d %H:%M:%S %Z} / UTC {utc_time:%Y-%m-%d %H:%M:%S}"


def format_bytes(value: Any) -> str:
    try:
        byte_count = int(value)
    except (TypeError, ValueError):
        return decode_text(value)

    if byte_count < 1024:
        return f"{byte_count} B"
    if byte_count < 1024 * 1024:
        return f"{byte_count} B（{byte_count / 1024:.2f} KB）"
    return f"{byte_count} B（{byte_count / 1024 / 1024:.2f} MB）"


def describe_field(name: str, value: Any) -> str:
    if name in TIME_FIELDS:
        return format_timestamp(value)
    if name in {"tx", "rx"}:
        return format_bytes(value)
    if name == "detail":
        return _format_threat_detail(value)
    return decode_text(value)


def _format_threat_detail(value: Any) -> str:
    """Parse the detail column JSON and render threat info with Chinese labels."""
    text = decode_text(value)
    if not text:
        return ""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text

    nested = obj.get("detail") if isinstance(obj, dict) else None
    if not isinstance(nested, dict):
        return text

    lines: list[str] = []
    # Category header
    clsname = nested.get("clsname", "")
    clsname_label = _CLSNAME_LABELS.get(str(clsname), str(clsname))
    treatment_val = nested.get("treatment", 0)
    treatment_label = _TREATMENT.get(treatment_val, "未知")
    risk_val = nested.get("risk", 0)
    risk_label = _RISK_LEVEL.get(risk_val, "未知")
    lines.append(f"    事件类型：{clsname_label}")
    lines.append(f"    处理结果：{treatment_label}  |  风险等级：{risk_label}")

    # Key fields first
    for key in (
        "procname", "cmdline", "res_cmd", "res_path",
        "p_procname", "p_cmdline", "xpid", "p_xpid",
        "proc_sha1", "recname", "action", "action_type",
        "proc_atrstr", "description", "montype",
    ):
        raw = nested.get(key)
        if raw is None or raw == "":
            continue
        label = THREAT_DETAIL_LABELS.get(key, key)
        formatted = _format_threat_field(key, raw)
        lines.append(f"    - {label}：{formatted}")

    version = obj.get("version") if isinstance(obj, dict) else None
    if isinstance(version, dict):
        product = version.get("product", "")
        if product:
            lines.append(f"    - 产品版本：{product}")

    return "\n".join(lines) if lines else text


def _format_threat_field(key: str, value: Any) -> str:
    if key == "action" and isinstance(value, int):
        return f"{_ACTION.get(value, '未知')}（{value}）"
    if key == "treatment" and isinstance(value, int):
        return f"{_TREATMENT.get(value, '未知')}（{value}）"
    if key == "risk" and isinstance(value, int):
        return f"{_RISK_LEVEL.get(value, '未知')}（{value}）"
    return decode_text(value)


def recover_database_from_wal(
    wal_path: Path, output_database_path: Path, base_db_path: Path | None = None
) -> None:
    data = wal_path.read_bytes()
    if len(data) < 32:
        raise ValueError("WAL 文件太小，无法解析。")

    magic, version, page_size = struct.unpack(">III", data[:12])
    if magic not in {0x377F0682, 0x377F0683}:
        raise ValueError(f"不是标准 SQLite WAL 文件，magic={magic:#x}")
    if page_size == 1:
        page_size = 65536

    frame_size = 24 + page_size
    frames: dict[int, bytes] = {}
    commits: list[int] = []

    for offset in range(32, len(data) - 24 + 1, frame_size):
        if offset + frame_size > len(data):
            break
        page_no, commit_size = struct.unpack(">II", data[offset : offset + 8])
        if page_no <= 0:
            continue
        frames[page_no] = data[offset + 24 : offset + 24 + page_size]
        if commit_size:
            commits.append(commit_size)

    if not frames:
        raise ValueError("WAL 中没有可恢复的数据页。")

    base_pages: dict[int, bytes] = {}
    if base_db_path is not None and base_db_path.exists():
        base_data = base_db_path.read_bytes()
        for i in range(0, len(base_data), page_size):
            base_pages[i // page_size + 1] = base_data[i : i + page_size]

    merged = {**base_pages, **frames}
    max_page = max(max(merged), max(commits) if commits else 0)
    with output_database_path.open("wb") as output_file:
        for page_no in range(1, max_page + 1):
            output_file.write(merged.get(page_no, b"\x00" * page_size))

    sqlite3.connect(output_database_path).execute("PRAGMA schema_version").connection.close()


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def load_tables(connection: sqlite3.Connection) -> list[str]:
    cursor = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    )
    return [decode_text(row[0]) for row in cursor.fetchall()]


def _is_interception_row(row: sqlite3.Row) -> bool:
    """Return True if this row represents a trojan/malware interception event."""
    detail_raw = row["detail"] if "detail" in row.keys() else None
    text = decode_text(detail_raw) if detail_raw else ""
    if not text:
        return True  # no detail → show it anyway
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return True
    nested = obj.get("detail") if isinstance(obj, dict) else None
    if not isinstance(nested, dict):
        return True
    treatment = nested.get("treatment", 0)
    risk = nested.get("risk", 0)
    if isinstance(treatment, (int, float)) and int(treatment) > 0:
        return True
    if isinstance(risk, (int, float)) and int(risk) > 0:
        return True
    return False


def _export_database_to_lines(database_path: Path) -> list[str]:
    connection = sqlite3.connect(database_path)
    connection.text_factory = bytes
    connection.row_factory = sqlite3.Row

    try:
        lines = [
            f"数据库：{database_path}",
            f"生成时间：{datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z}",
            "",
        ]

        tables = load_tables(connection)
        lines.append(f"表数量：{len(tables)}")
        lines.append("")

        for table_name in tables:
            quoted_table = quote_identifier(table_name)
            count = connection.execute(
                f"SELECT COUNT(*) FROM {quoted_table}"
            ).fetchone()[0]
            columns = [
                decode_text(row[1])
                for row in connection.execute(f"PRAGMA table_info({quoted_table})")
            ]
            table_label = TABLE_LABELS.get(table_name, table_name)

            lines.append(f"## {table_label}（{table_name}）")
            lines.append(f"记录数：{count}")
            lines.append("字段：" + "、".join(columns))
            lines.append("")

            rows = connection.execute(f"SELECT * FROM {quoted_table}").fetchall()
            visible_index = 0
            total_rows = len(rows)
            for row in rows:
                if table_name == "HrLogV3_60" and not _is_interception_row(row):
                    continue
                visible_index += 1
                lines.append(f"### 第 {visible_index} 条记录")
                for column in row.keys():
                    column_name = decode_text(column)
                    label = FIELD_LABELS.get(column_name, column_name)
                    lines.append(
                        f"- {label}（{column_name}）：{describe_field(column_name, row[column])}"
                    )
                lines.append("")

            if table_name == "HrLogV3_60" and visible_index < total_rows:
                lines.append(f"（已过滤 {total_rows - visible_index} 条非拦截记录）")
                lines.append("")

        return lines
    finally:
        connection.close()


def export_database(database_path: Path, report_path: Path) -> None:
    lines = _export_database_to_lines(database_path)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def export_logs(raw_files: tuple[Path, ...], output_dir: Path) -> str:
    header = "火绒安全日志导出"
    wal_path = next((f for f in raw_files if f.suffix == ".db-wal" and f.exists()), None)
    db_path = next((f for f in raw_files if f.suffix == ".db" and f.exists()), None)

    if db_path is not None:
        lines = [header, ""]
        try:
            lines.extend(_export_database_to_lines(db_path))
        except Exception:
            if wal_path is not None:
                wal_data = wal_path.read_bytes()
                if len(wal_data) >= 32:
                    magic = struct.unpack(">I", wal_data[:4])[0]
                    if magic in {0x377F0682, 0x377F0683}:
                        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                            recovered_path = Path(tmp.name)
                        try:
                            recover_database_from_wal(wal_path, recovered_path, db_path)
                            lines = [header, f"从 WAL 恢复：{wal_path.name}", ""]
                            lines.extend(_export_database_to_lines(recovered_path))
                        finally:
                            recovered_path.unlink(missing_ok=True)
                        return write_export(output_dir, "exported.txt", lines)
            raise
        return write_export(output_dir, "exported.txt", lines)

    if wal_path is not None:
        wal_data = wal_path.read_bytes()
        if len(wal_data) >= 32:
            magic = struct.unpack(">I", wal_data[:4])[0]
            if magic in {0x377F0682, 0x377F0683}:
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                    recovered_path = Path(tmp.name)
                try:
                    recover_database_from_wal(wal_path, recovered_path)
                    lines = [header, f"从 WAL 恢复：{wal_path.name}", ""]
                    lines.extend(_export_database_to_lines(recovered_path))
                    return write_export(output_dir, "exported.txt", lines)
                finally:
                    recovered_path.unlink(missing_ok=True)

    return write_export(output_dir, "exported.txt", (header, "", "火绒 log 数据库不存在"))


def parse_args() -> Namespace:
    parser = argparse.ArgumentParser(
        description="从火绒 log.db（或 .db-wal）导出可读文本报告。"
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=r"C:\Users\11913\Desktop\新建文件夹\log.db",
        help="数据库文件（.db）或 WAL 文件（.db-wal）路径",
    )
    parser.add_argument(
        "--database-output",
        default=None,
        help="从 WAL 恢复出的 SQLite 数据库路径（仅 WAL 模式需要）",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="输出文本报告路径（默认与输入同目录同名 .txt）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)

    if not input_path.exists():
        raise FileNotFoundError(f"找不到文件：{input_path}")

    if args.output:
        report_path = Path(args.output)
    else:
        report_path = input_path.with_suffix(".txt")

    if input_path.suffix == ".db-wal":
        wal_path = input_path
        db_path = input_path.with_name(input_path.name.replace(".db-wal", ".db"))
        if args.database_output:
            database_output_path = Path(args.database_output)
        else:
            database_output_path = input_path.with_name(
                input_path.name.replace(".db-wal", "") + "_recovered.db"
            )
        recover_database_from_wal(
            wal_path, database_output_path,
            db_path if db_path.exists() else None,
        )
        export_database(database_output_path, report_path)
        print(f"已恢复数据库：{database_output_path}")
        print(f"已导出报告：{report_path}")
    else:
        db_path = input_path
        report_lines = [f"数据库：{db_path}"]
        try:
            export_database(db_path, report_path)
        except Exception:
            wal_path = input_path.with_suffix(input_path.suffix + "-wal")
            if wal_path.exists():
                print(f"直接读取失败，尝试从 WAL 恢复：{wal_path}")
                if args.database_output:
                    database_output_path = Path(args.database_output)
                else:
                    database_output_path = input_path.with_name(
                        input_path.stem + "_recovered.db"
                    )
                recover_database_from_wal(wal_path, database_output_path, db_path)
                export_database(database_output_path, report_path)
                print(f"已恢复数据库：{database_output_path}")
                print(f"已导出报告：{report_path}")
                return
            raise
        print(f"已导出报告：{report_path}")


if __name__ == "__main__":
    main()

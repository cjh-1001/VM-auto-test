from __future__ import annotations

import html
import json
from pathlib import Path


def generate_report_from_json(input_path: Path, output_path: Path, output_format: str) -> int:
    data = json.loads(input_path.read_text(encoding="utf-8-sig"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        output_path.write_text(_standalone_html_report(data), encoding="utf-8")
    print(f"Report written to: {output_path}")
    return 0


def _standalone_html_report(data: object) -> str:
    title = "VM Auto Test Report"
    body = html.escape(json.dumps(data, ensure_ascii=False, indent=2))
    return (
        "<!doctype html>\n"
        "<html lang=\"zh-CN\">\n"
        "<head><meta charset=\"utf-8\"><title>VM Auto Test Report</title></head>\n"
        "<body>\n"
        f"<h1>{title}</h1>\n"
        f"<pre>{body}</pre>\n"
        "</body>\n"
        "</html>\n"
    )

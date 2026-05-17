from __future__ import annotations

import csv
import html
import json
import unicodedata
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from vm_auto_test.evaluator import output_hash
from vm_auto_test.models import (
    AvAnalyzeResult,
    BatchTestResult,
    Classification,
    CommandResult,
    DeferredImageResult,
    EvaluationResult,
    GuestCredentials,
    SampleSpec,
    SampleTestResult,
    Shell,
    TestCase,
    TestMode,
    TestResult,
    VerificationSpec,
)
from vm_auto_test.config import _sanitize_id


def create_report_dir(base_dir: Path, test_id: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_test_id = _sanitize_id(test_id)
    for suffix in range(100):
        name = f"{timestamp}-{safe_test_id}" if suffix == 0 else f"{timestamp}-{safe_test_id}-{suffix}"
        report_dir = base_dir / name
        try:
            report_dir.mkdir(parents=True, exist_ok=False)
            return report_dir
        except FileExistsError:
            continue
    raise FileExistsError(f"Unable to create unique report directory for {safe_test_id}")


def write_report(result: TestResult) -> None:
    report_dir = Path(result.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_sample_artifacts(
        report_dir,
        before=result.before.combined_output,
        after=result.after.combined_output,
        sample_stdout=result.sample.stdout,
        sample_stderr=result.sample.stderr,
        logs=result.logs,
    )
    (report_dir / "result.json").write_text(
        json.dumps(to_report_dict(result), ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )


def write_sample_report(result: SampleTestResult) -> None:
    report_dir = Path(result.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_sample_artifacts(
        report_dir,
        before=result.before.combined_output,
        after=result.after.combined_output,
        sample_stdout=result.sample.stdout,
        sample_stderr=result.sample.stderr,
        logs=result.logs,
    )
    (report_dir / "result.json").write_text(
        json.dumps(to_sample_report_dict(result), ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )


def write_batch_report(result: BatchTestResult) -> None:
    report_dir = Path(result.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    for sample_result in result.samples:
        _relative_sample_report_dir(result, sample_result)
    for sample_result in result.samples:
        write_sample_report(sample_result)
    (report_dir / "result.json").write_text(
        json.dumps(to_batch_report_dict(result), ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )
    _write_batch_csv(result, report_dir)
    _write_batch_html(result, report_dir)


def to_report_dict(result: TestResult) -> dict[str, Any]:
    test_case = result.test_case
    evaluation = result.evaluation
    return {
        "schema_version": 1,
        "mode": test_case.mode.value,
        "classification": result.classification.value,
        "changed": result.changed,
        "effect_observed": evaluation.effect_observed if evaluation else result.changed,
        "vm_id": test_case.vm_id,
        "snapshot": test_case.snapshot,
        "sample_command": test_case.sample_command,
        "verify_command": test_case.effective_verification().command,
        "verify_shell": test_case.effective_verification().shell.value,
        "sample_shell": test_case.sample_shell.value,
        "baseline_result": test_case.baseline_result,
        "before_hash": output_hash(result.before.combined_output),
        "after_hash": output_hash(result.after.combined_output),
        "sample_capture_method": result.sample.capture_method,
        "before_capture_method": result.before.capture_method,
        "after_capture_method": result.after.capture_method,
        "comparisons": _comparison_dicts(evaluation),
        "av_logs": _log_dicts(result.logs),
        "steps": [asdict(step) for step in result.steps],
        "av_analyze_result": _av_analyze_dict(result.av_analyze_result),
        "image_compare_result": _image_compare_dict(result.image_compare_result),
    }


def to_sample_report_dict(result: SampleTestResult) -> dict[str, Any]:
    test_case = result.test_case
    return {
        "schema_version": 2,
        "mode": test_case.mode.value,
        "sample_id": result.sample_spec.id,
        "classification": result.classification.value,
        "changed": result.changed,
        "effect_observed": result.evaluation.effect_observed,
        "vm_id": test_case.vm_id,
        "snapshot": test_case.snapshot,
        "sample_command": result.sample_spec.command,
        "verify_command": result.before.command,
        "baseline_result": test_case.baseline_result,
        "before_hash": output_hash(result.before.combined_output),
        "after_hash": output_hash(result.after.combined_output),
        "sample_capture_method": result.sample.capture_method,
        "before_capture_method": result.before.capture_method,
        "after_capture_method": result.after.capture_method,
        "comparisons": _comparison_dicts(result.evaluation),
        "av_logs": _log_dicts(result.logs),
        "steps": [asdict(step) for step in result.steps],
        "duration_seconds": round(result.duration_seconds, 2),
        "av_analyze_result": _av_analyze_dict(result.av_analyze_result),
        "image_compare_result": _image_compare_dict(result.image_compare_result),
    }


def to_batch_report_dict(result: BatchTestResult) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for sample in result.samples:
        counts[sample.classification.value] = counts.get(sample.classification.value, 0) + 1
    return {
        "schema_version": 2,
        "mode": result.test_case.mode.value,
        "vm_id": result.test_case.vm_id,
        "snapshot": result.test_case.snapshot,
        "baseline_result": result.test_case.baseline_result,
        "summary": {
            "total": len(result.samples),
            "classification_counts": counts,
            "overall_classification": result.classification.value,
            "duration_seconds": round(result.duration_seconds, 2),
        },
        "samples": [
            {
                "id": sample.sample_spec.id,
                "classification": sample.classification.value,
                "changed": sample.changed,
                "effect_observed": sample.evaluation.effect_observed,
                "sample_command": sample.sample_spec.command,
                "report_dir": _relative_sample_report_dir(result, sample),
                "steps": [asdict(step) for step in sample.steps],
                "duration_seconds": round(sample.duration_seconds, 2),
                "av_analyze_result": _av_analyze_dict(sample.av_analyze_result),
                "image_compare_result": _image_compare_dict(sample.image_compare_result),
            }
            for sample in result.samples
        ],
        "steps": [asdict(step) for step in result.steps],
    }


_BATCH_CSV_FIELDS = (
    "schema_version",
    "mode",
    "vm_id",
    "snapshot",
    "baseline_result",
    "overall_classification",
    "sample_id",
    "sample_command",
    "verify_command",
    "verify_shell",
    "classification",
    "changed",
    "effect_observed",
    "before_hash",
    "after_hash",
    "sample_capture_method",
    "before_capture_method",
    "after_capture_method",
    "av_log_count",
    "av_analyze_log_found",
    "av_analyze_image_result",
    "report_dir",
    "duration_seconds",
)


_EXCEL_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _write_batch_csv(result: BatchTestResult, report_dir: Path) -> None:
    with (report_dir / "result.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=_BATCH_CSV_FIELDS)
        writer.writeheader()
        for row in _batch_csv_rows(result):
            writer.writerow({key: _safe_csv_cell(value) for key, value in row.items()})


def _batch_csv_rows(result: BatchTestResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sample in result.samples:
        verification = _sample_verification(result, sample)
        rows.append(
            {
                "schema_version": 2,
                "mode": result.test_case.mode.value,
                "vm_id": result.test_case.vm_id,
                "snapshot": result.test_case.snapshot or "",
                "baseline_result": result.test_case.baseline_result or "",
                "overall_classification": result.classification.value,
                "sample_id": sample.sample_spec.id,
                "sample_command": sample.sample_spec.command,
                "verify_command": verification.command,
                "verify_shell": verification.shell.value,
                "classification": sample.classification.value,
                "changed": _bool_text(sample.changed),
                "effect_observed": _bool_text(sample.evaluation.effect_observed),
                "before_hash": output_hash(sample.before.combined_output),
                "after_hash": output_hash(sample.after.combined_output),
                "sample_capture_method": sample.sample.capture_method,
                "before_capture_method": sample.before.capture_method,
                "after_capture_method": sample.after.capture_method,
                "av_log_count": len(sample.logs),
                "av_analyze_log_found": _bool_text(sample.av_analyze_result.log_found) if sample.av_analyze_result else "",
                "av_analyze_image_result": _image_compare_csv_text(sample.image_compare_result),
                "report_dir": _relative_sample_report_dir(result, sample),
                "duration_seconds": round(sample.duration_seconds, 2),
            }
        )
    return rows


def _safe_csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    stripped = _strip_csv_formula_padding(text)
    if stripped.startswith(_EXCEL_FORMULA_PREFIXES):
        return f"'{text}"
    return text


def _strip_csv_formula_padding(text: str) -> str:
    index = 0
    for character in text:
        if not character.isspace() and unicodedata.category(character) not in {"Cf", "Cc"}:
            break
        index += 1
    return text[index:]


_HTML_CLASS_LABELS: dict[str, str] = {
    "BASELINE_VALID": "✓ SUCCESS — 有效",
    "BASELINE_INVALID": "✗ FAILED — 无效",
    "AV_NOT_BLOCKED": "✗ FAILED — 未拦截",
    "AV_BLOCKED_OR_NO_CHANGE": "✓ SUCCESS — 已拦截",
    "AV_ANALYZE_BLOCKED": "✓ 已拦截",
    "AV_ANALYZE_NOT_BLOCKED": "✗ 未拦截",
}

_HTML_ROW_CLASS: dict[str, str] = {
    "BASELINE_VALID": "row-pass",
    "BASELINE_INVALID": "row-fail",
    "AV_NOT_BLOCKED": "row-fail",
    "AV_BLOCKED_OR_NO_CHANGE": "row-pass",
    "AV_ANALYZE_BLOCKED": "row-pass",
    "AV_ANALYZE_NOT_BLOCKED": "row-fail",
}


def _write_batch_html(result: BatchTestResult, report_dir: Path, output_path: Path | None = None) -> None:
    total = len(result.samples)
    counts = _classification_counts(result)
    pass_count, fail_count = _classify_pass_fail(counts)
    pass_pct = round(pass_count / total * 100) if total else 0
    fail_pct = 100 - pass_pct
    overall_label = _html_escape(_html_label(result.classification.value))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_duration = _format_duration(result.duration_seconds)
    mode_labels: dict[str, str] = {
        "baseline": "BASELINE — 验证样本有效性",
        "av": "AV — 验证杀软拦截",
        "av_analyze": "AV_ANALYZE — AI分析杀软拦截",
    }
    mode_cn = mode_labels.get(result.test_case.mode.value, result.test_case.mode.value.upper())
    baseline_str = _html_escape(result.test_case.baseline_result or "")

    ring_svg = _build_ring_svg(pass_pct)

    stat_cards = ""
    for key, value in counts.items():
        label = _html_escape(_html_label(key))
        row_class = "stat-pass" if key in ("BASELINE_VALID", "AV_BLOCKED_OR_NO_CHANGE", "AV_ANALYZE_BLOCKED") else "stat-fail"
        stat_cards += f"""            <div class="stat-item {row_class}">
              <span class="stat-dot"></span>
              <span class="stat-label">{label}</span>
              <span class="stat-num">{value}</span>
            </div>
"""

    is_av_analyze = result.test_case.mode.value == "av_analyze"
    sample_rows = "\n".join(_sample_html_row(result, sample, report_dir) for sample in result.samples)

    _sort_map = '"id:0,class:1,fx:2,sc:3,log:4,img:5,dur:6"' if is_av_analyze else '"id:0,class:1,fx:2,sc:3,vc:4,dur:5"'

    if is_av_analyze:
        table_headers = """            <th data-sort="id">样本 ID <span class="sort-arrow">⇅</span></th>
            <th data-sort="log">日志分析 <span class="sort-arrow">⇅</span></th>
            <th data-sort="img">图片对比 <span class="sort-arrow">⇅</span></th>
            <th data-sort="verdict">综合判定 <span class="sort-arrow">⇅</span></th>
            <th data-sort="sc">样本命令 <span class="sort-arrow">⇅</span></th>
            <th data-sort="dur">用时 <span class="sort-arrow">⇅</span></th>
            <th>产出文件</th>"""
    else:
        table_headers = """            <th data-sort="id">样本 ID <span class="sort-arrow">⇅</span></th>
            <th data-sort="class">判定结果 <span class="sort-arrow">⇅</span></th>
            <th data-sort="fx">效果发生 <span class="sort-arrow">⇅</span></th>
            <th data-sort="sc">样本命令 <span class="sort-arrow">⇅</span></th>
            <th data-sort="vc">验证命令 <span class="sort-arrow">⇅</span></th>
            <th data-sort="dur">用时 <span class="sort-arrow">⇅</span></th>
            <th>产出文件</th>"""

    embedded_files_script = _build_embedded_files_script(result, report_dir)

    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VM Auto Test — 批量测试报告</title>
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    body{{
      font-family:"Inter","Segoe UI",system-ui,-apple-system,sans-serif;
      background:#f0f4f8;color:#1e293b;line-height:1.6;min-height:100vh
    }}
    .container{{max-width:1280px;margin:0 auto;padding:2rem 1.5rem}}

    /* ── Top bar ── */
    .topbar{{
      background:linear-gradient(135deg,#0f2b4a 0%,#1a3f6e 100%);
      border-radius:12px;padding:1.5rem 1.75rem;margin-bottom:1.5rem;
      display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:1rem;
      color:#fff
    }}
    .topbar-left h1{{font-size:1.25rem;font-weight:700;letter-spacing:-0.02em}}
    .topbar-left .subtitle{{font-size:0.75rem;color:#94b8d8;margin-top:0.15rem}}
    .topbar-right{{display:flex;gap:0.5rem;flex-wrap:wrap}}
    .topbar-pill{{
      background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.15);
      border-radius:6px;padding:0.35rem 0.75rem;font-size:0.72rem;color:#cbddee
    }}
    .topbar-pill strong{{color:#fff;font-weight:600;margin-right:0.25rem}}

    /* ── Quick stats row ── */
    .quick-row{{
      display:grid;grid-template-columns:repeat(4,1fr);gap:0.75rem;margin-bottom:1.5rem
    }}
    .quick-card{{
      background:#fff;border-radius:10px;padding:1.15rem 1.35rem;
      box-shadow:0 1px 3px rgba(0,0,0,0.05);display:flex;flex-direction:column
    }}
    .quick-card .ql{{font-size:0.68rem;color:#64748b;text-transform:uppercase;letter-spacing:0.07em;margin-bottom:0.25rem}}
    .quick-card .qv{{font-size:2.2rem;font-weight:800;line-height:1.1}}
    .qv-pass{{color:#0d9488}}
    .qv-fail{{color:#e74c3c}}
    .qv-total{{color:#0f172a}}
    .qv-rate{{color:#1e40af}}
    .quick-card .qs{{font-size:0.75rem;color:#94a3b8;margin-top:0.15rem}}

    /* ── Main grid: ring + info + stats ── */
    .main-grid{{
      display:grid;grid-template-columns:200px 1fr;gap:1rem;margin-bottom:1.5rem
    }}
    .ring-panel{{
      background:#fff;border-radius:10px;padding:1.25rem;
      box-shadow:0 1px 3px rgba(0,0,0,0.05);display:flex;
      flex-direction:column;align-items:center;justify-content:center
    }}
    .ring-wrap{{position:relative;width:130px;height:130px}}
    .ring-wrap svg{{transform:rotate(-90deg)}}
    .ring-center{{
      position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center
    }}
    .ring-center .pct{{font-size:1.65rem;font-weight:800;color:#0f172a;line-height:1}}
    .ring-center .pct-label{{font-size:0.62rem;color:#64748b;text-transform:uppercase;letter-spacing:0.06em;margin-top:0.1rem}}
    .ring-legend{{display:flex;gap:1rem;margin-top:0.75rem;font-size:0.7rem;font-weight:600}}
    .ring-legend .lg-pass{{color:#0d9488}}
    .ring-legend .lg-fail{{color:#94a3b8}}

    .info-panel{{
      display:flex;flex-direction:column;gap:0.75rem
    }}
    .info-row{{
      display:grid;grid-template-columns:repeat(3,1fr);gap:0.75rem
    }}
    .info-cell{{
      background:#fff;border-radius:8px;padding:0.85rem 1rem;
      box-shadow:0 1px 2px rgba(0,0,0,0.04)
    }}
    .info-cell dt{{font-size:0.65rem;color:#94a3b8;text-transform:uppercase;letter-spacing:0.07em;margin-bottom:0.2rem}}
    .info-cell dd{{font-size:0.82rem;font-weight:600;color:#1e293b;word-break:break-all}}
    .info-cell dd.mono{{font-family:"Cascadia Code","Fira Code",ui-monospace,monospace;font-size:0.76rem;font-weight:400}}

    .stat-grid{{
      background:#fff;border-radius:10px;padding:1.1rem 1.25rem;
      box-shadow:0 1px 3px rgba(0,0,0,0.05)
    }}
    .stat-grid h3{{font-size:0.78rem;font-weight:700;color:#334155;margin-bottom:0.7rem;text-transform:uppercase;letter-spacing:0.06em}}
    .stat-list{{display:flex;flex-direction:column;gap:0.45rem}}
    .stat-item{{
      display:flex;align-items:center;gap:0.5rem;font-size:0.8rem;padding:0.35rem 0.5rem;border-radius:5px
    }}
    .stat-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
    .stat-pass .stat-dot{{background:#0d9488}}
    .stat-fail .stat-dot{{background:#e74c3c}}
    .stat-label{{flex:1;color:#475569}}
    .stat-num{{font-weight:700;color:#0f172a}}
    .stat-pass{{background:#ecfdf5}}
    .stat-fail{{background:#fef2f2}}

    /* ── Table ── */
    .section-bar{{
      display:flex;align-items:center;gap:0.5rem;margin-bottom:0.75rem
    }}
    .section-bar span{{font-size:0.9rem;font-weight:700;color:#1e293b}}
    .section-bar .count-chip{{
      font-size:0.68rem;background:#e2e8f0;color:#475569;padding:0.15rem 0.55rem;border-radius:99px;font-weight:600
    }}
    .table-wrap{{
      background:#fff;border-radius:10px;overflow:hidden;
      box-shadow:0 1px 3px rgba(0,0,0,0.05);margin-bottom:1.5rem;overflow-x:auto
    }}
    table{{width:100%;border-collapse:collapse;min-width:800px}}
    thead{{border-bottom:2px solid #e2e8f0}}
    th{{
      padding:0.65rem 0.75rem;text-align:left;font-size:0.66rem;
      text-transform:uppercase;letter-spacing:0.06em;color:#64748b;
      background:#f8fafc;white-space:nowrap;font-weight:600;cursor:pointer;
      user-select:none;position:relative;transition:color .15s
    }}
    th:hover{{color:#1e293b}}
    th .sort-arrow{{margin-left:3px;font-size:0.6rem;opacity:.4}}
    th.sort-asc .sort-arrow,.th.sort-desc .sort-arrow{{opacity:1;color:#1e40af}}
    td{{padding:0.6rem 0.75rem;font-size:0.82rem;border-bottom:1px solid #f1f5f9;vertical-align:middle}}
    tbody tr{{transition:background .12s}}
    tbody tr:hover td{{background:#eef4ff !important}}
    tr:last-child td{{border-bottom:none}}
    tr.row-pass{{border-left:3px solid #0d9488}}
    tr.row-fail{{border-left:3px solid #e74c3c}}

    /* ── Badges ── */
    .badge{{
      display:inline-flex;align-items:center;gap:0.3rem;
      padding:0.22rem 0.6rem;border-radius:5px;font-size:0.76rem;font-weight:600;white-space:nowrap
    }}
    .badge-pass{{background:#ccfbf1;color:#0f766e}}
    .badge-fail{{background:#fee2e2;color:#b91c1c}}
    .badge-dot{{width:6px;height:6px;border-radius:50%}}
    .badge-pass .badge-dot{{background:#0d9488}}
    .badge-fail .badge-dot{{background:#e74c3c}}

    /* ── Analysis tags ── */
    .tag{{
      display:inline-block;font-size:0.74rem;font-weight:600;
      padding:0.18rem 0.6rem;border-radius:4px;white-space:nowrap
    }}
    .tag-changed{{background:#fef2f2;color:#b91c1c}}
    .tag-unchanged{{background:#f1f5f9;color:#64748b}}
    .tag-blocked{{background:#ecfdf5;color:#0f766e}}
    .tag-clean{{background:#f1f5f9;color:#94a3b8}}
    .tag-neutral-fail{{background:#fef2f2;color:#b91c1c}}
    /* ── Effect icon ── */
    .effect-cell{{text-align:center}}
    .effect-yes{{color:#0d9488;font-weight:700;font-size:1rem}}
    .effect-no{{color:#cbd5e1;font-weight:700;font-size:1rem}}

    /* ── Command cell ── */
    td.cmd{{max-width:240px}}
    .cmd-wrapper{{
      display:flex;align-items:center;gap:0.35rem
    }}
    .cmd-wrapper code{{
      font-size:0.78rem;background:#f1f5f9;padding:0.15rem 0.4rem;
      border-radius:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0
    }}
    .btn-copy{{
      flex-shrink:0;width:22px;height:22px;border:none;border-radius:4px;
      background:transparent;color:#94a3b8;cursor:pointer;font-size:0.7rem;
      display:flex;align-items:center;justify-content:center;transition:all .15s
    }}
    .btn-copy:hover{{background:#e2e8f0;color:#475569}}
    .btn-copy.copied{{background:#dcfce7;color:#16a34a}}

    /* ── Artifact links ── */
    .artifact-links{{display:flex;flex-wrap:wrap;gap:0.2rem}}
    .artifact-links a{{
      font-size:0.68rem;color:#3b82f6;text-decoration:none;
      padding:0.12rem 0.45rem;border-radius:4px;background:#eff6ff;
      white-space:nowrap;transition:all .12s
    }}
    .artifact-links a:hover{{background:#dbeafe;color:#1d4ed8}}

    /* ── Footer ── */
    .footer{{
      display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:0.75rem;
      padding:1.25rem 0;border-top:1px solid #e2e8f0;
    }}
    .footer .brand{{font-size:0.78rem;color:#94a3b8;font-weight:600;margin-right:0.5rem}}
    .btn-dl{{
      display:inline-flex;align-items:center;gap:0.35rem;
      padding:0.45rem 0.9rem;border-radius:6px;font-size:0.78rem;font-weight:600;
      text-decoration:none;transition:all .15s;border:1px solid #e2e8f0
    }}
    .btn-dl-json{{background:#fff;color:#475569}}
    .btn-dl-json:hover{{background:#f8fafc;border-color:#94a3b8}}
    .btn-dl-csv{{background:#f0f9ff;color:#0369a1;border-color:#bae6fd}}
    .btn-dl-csv:hover{{background:#e0f2fe}}
    .btn-dl-html{{background:#f5f3ff;color:#6d28d9;border-color:#ddd6fe}}
    .btn-dl-html:hover{{background:#ede9fe}}
    .dl-icon{{font-size:0.85rem}}

    /* ── Responsive ── */
    @media(max-width:900px){{
      .quick-row{{grid-template-columns:repeat(2,1fr)}}
      .main-grid{{grid-template-columns:1fr}}
      .ring-panel{{flex-direction:row;gap:1.5rem}}
      .info-row{{grid-template-columns:1fr}}
    }}
    @media(max-width:640px){{
      .container{{padding:1rem}}
      .quick-row{{grid-template-columns:1fr 1fr}}
      .quick-card .qv{{font-size:1.6rem}}
      .topbar{{padding:1rem 1.25rem}}
      .topbar-left h1{{font-size:1.05rem}}
      .footer{{flex-direction:column;align-items:flex-start}}
    }}

    /* ── Drawer ── */
    .drawer-overlay{{
      position:fixed;inset:0;background:rgba(15,23,42,0.45);
      z-index:1000;opacity:0;visibility:hidden;transition:opacity .25s ease,visibility .25s ease
    }}
    .drawer-overlay.open{{opacity:1;visibility:visible}}
    .drawer{{
      position:fixed;top:0;right:0;width:42vw;min-width:420px;max-width:100vw;height:100vh;
      background:#fff;z-index:1001;box-shadow:-4px 0 24px rgba(0,0,0,0.12);
      display:flex;flex-direction:column;
      transform:translateX(100%);transition:transform .28s cubic-bezier(0.16,1,0.3,1)
    }}
    .drawer.open{{transform:translateX(0)}}
    .drawer-header{{
      display:flex;align-items:center;justify-content:space-between;
      padding:1rem 1.25rem;border-bottom:1px solid #e2e8f0;
      background:#f8fafc;flex-shrink:0
    }}
    .drawer-header .drawer-title{{
      font-size:0.82rem;font-weight:600;color:#1e293b;
      font-family:"Cascadia Code","Fira Code",ui-monospace,monospace;
      overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;margin-right:0.75rem
    }}
    .drawer-close{{
      width:28px;height:28px;border:none;border-radius:6px;
      background:transparent;color:#64748b;cursor:pointer;font-size:1.1rem;
      display:flex;align-items:center;justify-content:center;flex-shrink:0;
      transition:all .12s;line-height:1
    }}
    .drawer-close:hover{{background:#fee2e2;color:#b91c1c}}
    .drawer-body{{
      flex:1;overflow-y:auto;padding:1rem 1.25rem
    }}
    .drawer-body pre{{
      font-family:"Cascadia Code","Fira Code",ui-monospace,monospace;
      font-size:0.76rem;line-height:1.55;white-space:pre-wrap;word-break:break-all;
      color:#334155;margin:0
    }}
    .drawer-body img{{
      max-width:100%;height:auto;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,0.08)
    }}
    .drawer-loading{{
      display:flex;align-items:center;justify-content:center;height:100%;color:#94a3b8
    }}
    .drawer-loading .spinner{{
      width:28px;height:28px;border:2.5px solid #e2e8f0;border-top-color:#3b82f6;
      border-radius:50%;animation:spin .7s linear infinite;margin-right:0.6rem
    }}
    @keyframes spin{{to{{transform:rotate(360deg)}}}}
    .drawer-error{{color:#b91c1c;text-align:center;padding:2rem 1rem;font-size:0.82rem}}
    .drawer-body .img-container{{
      display:flex;align-items:center;justify-content:center;min-height:160px
    }}
    .drawer-body .img-container img{{
      max-width:100%;height:auto;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,0.08);cursor:zoom-in;transition:transform .2s
    }}
    .drawer-body .img-container img:hover{{transform:scale(1.02)}}
    .img-zoom-overlay{{
      position:fixed;inset:0;background:rgba(0,0,0,0.82);z-index:2000;
      display:flex;align-items:center;justify-content:center;cursor:zoom-out;
      opacity:0;visibility:hidden;transition:opacity .22s ease,visibility .22s ease
    }}
    .img-zoom-overlay.open{{opacity:1;visibility:visible}}
    .img-zoom-overlay img{{
      max-width:93vw;max-height:93vh;object-fit:contain;border-radius:4px;box-shadow:0 8px 40px rgba(0,0,0,0.5)
    }}
    .json-key{{color:#0369a1}}
    .json-string{{color:#059669}}
    .json-bool{{color:#7c3aed}}
    .json-null{{color:#94a3b8}}
    .json-num{{color:#d97706}}

    @media(max-width:640px){{
      .drawer{{width:100vw}}
    }}
  </style>
</head>
<body>
  <div class="container">

    <!-- ═══ Top bar ═══ -->
    <div class="topbar">
      <div class="topbar-left">
        <h1>VM Auto Test — 批量测试报告</h1>
        <div class="subtitle">{mode_cn}</div>
      </div>
      <div class="topbar-right">
        <span class="topbar-pill"><strong>模式</strong> {_html_escape(result.test_case.mode.value.upper())}</span>
        <span class="topbar-pill"><strong>快照</strong> {_html_escape(result.test_case.snapshot or "—")}</span>
        <span class="topbar-pill"><strong>总用时</strong> {total_duration}</span>
        <span class="topbar-pill"><strong>生成时间</strong> {now_str}</span>
      </div>
    </div>

    <!-- ═══ Quick stats ═══ -->
    <div class="quick-row">
      <div class="quick-card">
        <span class="ql">样本总数</span>
        <span class="qv qv-total">{total}</span>
      </div>
      <div class="quick-card">
        <span class="ql">通过 / 拦截</span>
        <span class="qv qv-pass">{pass_count}</span>
        <span class="qs">占比 {pass_pct}%</span>
      </div>
      <div class="quick-card">
        <span class="ql">未通过 / 未拦截</span>
        <span class="qv qv-fail">{fail_count}</span>
        <span class="qs">占比 {fail_pct}%</span>
      </div>
      <div class="quick-card">
        <span class="ql">通过率</span>
        <span class="qv qv-rate">{pass_pct}%</span>
      </div>
    </div>

    <!-- ═══ Ring + Info + Stats ═══ -->
    <div class="main-grid">
      <div class="ring-panel">
        <div class="ring-wrap">
          {ring_svg}
          <div class="ring-center">
            <div class="pct">{pass_pct}%</div>
            <div class="pct-label">通过率</div>
          </div>
        </div>
        <div class="ring-legend">
          <span class="lg-pass">● 通过</span>
          <span class="lg-fail">● 未通过</span>
        </div>
      </div>
      <div class="info-panel">
        <div class="info-row">
          <div class="info-cell">
            <dt>虚拟机</dt>
            <dd class="mono">{_html_escape(result.test_case.vm_id)}</dd>
          </div>
          <div class="info-cell">
            <dt>快照</dt>
            <dd>{_html_escape(result.test_case.snapshot or "—")}</dd>
          </div>
          <div class="info-cell">
            <dt>综合判定</dt>
            <dd>{overall_label}</dd>
          </div>
        </div>
        <div class="stat-grid">
          <h3>判定分布</h3>
          <div class="stat-list">
{stat_cards}          </div>
        </div>
      </div>
    </div>

    <!-- ═══ Table ═══ -->
    <div class="section-bar">
      <span>样本详情</span>
      <span class="count-chip">{total} samples</span>
    </div>
    <div class="table-wrap">
      <table id="sampleTable">
        <thead>
          <tr>
{table_headers}
          </tr>
        </thead>
        <tbody>
{sample_rows}
        </tbody>
      </table>
    </div>

    <!-- ═══ Footer ═══ -->
    <div class="footer">
      <span class="brand">VM Auto Test</span>
      <a href="result.json" class="btn-dl btn-dl-json"><span class="dl-icon">{_html_escape("{}")}</span> result.json</a>
      <a href="result.csv" class="btn-dl btn-dl-csv"><span class="dl-icon">&#9633;</span> result.csv</a>
      <a href="result.html" class="btn-dl btn-dl-html"><span class="dl-icon">&#60;&#47;&#62;</span> result.html</a>
    </div>
  </div>

  <!-- ═══ Drawer ═══ -->
  <div class="drawer-overlay" id="drawerOverlay"></div>
  <div class="drawer" id="drawer">
    <div class="drawer-header">
      <span class="drawer-title" id="drawerTitle">—</span>
      <button class="drawer-close" id="drawerClose" title="关闭">&times;</button>
    </div>
    <div class="drawer-body" id="drawerBody"></div>
  </div>
  <div class="img-zoom-overlay" id="imgZoomOverlay"><img id="imgZoomImg" src="" alt="" /></div>

  {embedded_files_script}
  <script>
    // ── Table sorting ──
    (function(){{
      var table=document.getElementById('sampleTable');
      if(!table)return;
      var thead=table.querySelector('thead');
      var tbody=table.querySelector('tbody');
      thead.addEventListener('click',function(e){{
        var th=e.target.closest('th');
        if(!th||!th.dataset.sort)return;
        var col=th.dataset.sort;
        var isAsc=th.classList.contains('sort-asc');
        thead.querySelectorAll('th').forEach(function(h){{h.classList.remove('sort-asc','sort-desc')}});
        th.classList.add(isAsc?'sort-desc':'sort-asc');
        var dir=isAsc?-1:1;
        var rows=Array.from(tbody.querySelectorAll('tr'));
        rows.sort(function(a,b){{
          var ca=getCell(a,col);var cb=getCell(b,col);
          var na=parseFloat(ca),nb=parseFloat(cb);
          if(!isNaN(na)&&!isNaN(nb))return(na-nb)*dir;
          return ca.localeCompare(cb,'zh-Hans-CN',{{numeric:true}})*dir;
        }});
        rows.forEach(function(r){{tbody.appendChild(r)}});
      }});
      function getCell(row,col){{
        var map={_sort_map};
        var i=map[col]!=null?map[col]:0;
        var td=row.children[i];
        return(td?td.textContent.trim():'');
      }}
    }})();

    // ── Copy buttons ──
    document.querySelectorAll('.btn-copy').forEach(function(btn){{
      btn.addEventListener('click',function(e){{
        e.preventDefault();
        var code=this.parentElement.querySelector('code');
        if(!code)return;
        navigator.clipboard.writeText(code.textContent||code.innerText).then(function(){{
          btn.classList.add('copied');
          btn.textContent='✓';
          setTimeout(function(){{btn.classList.remove('copied');btn.textContent='⎘';}},1500);
        }}).catch(function(){{}});
      }});
    }});

    // ── Drawer ──
    (function(){{
      var overlay=document.getElementById('drawerOverlay');
      var drawer=document.getElementById('drawer');
      var titleEl=document.getElementById('drawerTitle');
      var bodyEl=document.getElementById('drawerBody');
      var closeBtn=document.getElementById('drawerClose');
      var zoomOverlay=document.getElementById('imgZoomOverlay');
      var zoomImg=document.getElementById('imgZoomImg');
      var openHref=null;
      var activeXhr=null;

      function open(href,label){{
        if(openHref===href)return;
        if(activeXhr)activeXhr.abort();
        openHref=href;
        titleEl.textContent=label;
        bodyEl.innerHTML='<div class="drawer-loading"><span class="spinner"></span>加载中…</div>';
        overlay.classList.add('open');
        drawer.classList.add('open');
        document.body.style.overflow='hidden';

        if(/\\.(png|jpg|jpeg|gif|webp|svg|bmp|ico)$/i.test(href)){{
          var h=_esc(href);
          bodyEl.innerHTML='<div class="img-container"><img src="'+h+'" alt="'+_esc(label)+'" title="点击放大" /></div>';
          var imgEl=bodyEl.querySelector('img');
          if(imgEl){{
            imgEl.addEventListener('click',function(){{
              zoomImg.src=this.src;
              zoomImg.alt=this.alt;
              zoomOverlay.classList.add('open');
              document.body.style.overflow='hidden';
            }});
          }}
          return;
        }}

        if(window.__EMBEDDED__&&window.__EMBEDDED__.hasOwnProperty(href)){{
          var content=window.__EMBEDDED__[href];
          if(/\\.json$/i.test(href)){{
            bodyEl.innerHTML='<pre>'+_jsonHighlight(_escCode(content))+'</pre>';
          }}else{{
            bodyEl.innerHTML='<pre>'+_escCode(content)+'</pre>';
          }}
          return;
        }}

        var isJson=/\\.json$/i.test(href);
        if(!isJson){{
          bodyEl.innerHTML='<iframe src="'+_esc(href)+'" style="width:100%;height:100%;border:none;background:#fff"></iframe>';
          return;
        }}

        activeXhr=new XMLHttpRequest();
        activeXhr.onload=function(){{
          if(activeXhr.status===200||(activeXhr.status===0&&activeXhr.responseText)){{
            bodyEl.innerHTML='<pre>'+_jsonHighlight(_escCode(activeXhr.responseText))+'</pre>';
          }}else{{
            bodyEl.innerHTML='<iframe src="'+_esc(href)+'" style="width:100%;height:100%;border:none;background:#fff"></iframe>';
          }}
        }};
        activeXhr.onerror=function(){{
          bodyEl.innerHTML='<iframe src="'+_esc(href)+'" style="width:100%;height:100%;border:none;background:#fff"></iframe>';
        }};
        activeXhr.open('GET',href,true);
        activeXhr.send();
      }}

      function close(){{
        if(activeXhr)activeXhr.abort();
        overlay.classList.remove('open');
        drawer.classList.remove('open');
        zoomOverlay.classList.remove('open');
        openHref=null;
        activeXhr=null;
        document.body.style.overflow='';
      }}

      function _esc(s){{return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}}
      function _escCode(s){{return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}

      function _jsonHighlight(text){{
        var span=function(cls,m){{return '<span class="'+cls+'">'+m+'</span>';}};
        return text
          .replace(/("(?:[^"\\\\]|\\\\.)*")\\s*:/g,function(m){{return span('json-key',m.slice(0,m.length-1))+':'}})
          .replace(/:\\s*("(?:[^"\\\\]|\\\\.)*")/g,': '+span('json-string','$1'))
          .replace(/:\\s*(true|false)/g,': '+span('json-bool','$1'))
          .replace(/:\\s*(null)/g,': '+span('json-null','$1'))
          .replace(/:\\s*(-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)/g,': '+span('json-num','$1'));
      }}

      zoomOverlay.addEventListener('click',function(){{
        zoomOverlay.classList.remove('open');
      }});

      document.querySelectorAll('.drawer-link').forEach(function(a){{
        a.addEventListener('click',function(e){{
          e.preventDefault();
          e.stopPropagation();
          var href = a.getAttribute('data-href');
          if (href) {{
            open(href, a.textContent.trim());
          }}
        }});
      }});

      overlay.addEventListener('click',close);
      closeBtn.addEventListener('click',close);
      document.addEventListener('keydown',function(e){{if(e.key==='Escape')close();}});
    }})();
  </script>
</body>
</html>
"""
    html_path = output_path or (report_dir / "result.html")
    html_path.write_text(html_text, encoding="utf-8")


def _build_embedded_files_script(result: BatchTestResult, report_dir: Path) -> str:
    """Embed per-sample text file contents as a JS object so drawer works offline."""
    embedded: dict[str, str] = {}
    for sample in result.samples:
        sample_dir = Path(sample.report_dir)
        relative = _relative_sample_report_dir(result, sample)
        for fname in ("result.json", "before.txt", "after.txt"):
            fpath = sample_dir / fname
            if fpath.exists():
                try:
                    content = fpath.read_text(encoding="utf-8-sig")
                except (UnicodeDecodeError, OSError):
                    continue
                embedded[f"{relative}/{fname}"] = content
    if not embedded:
        return ""
    raw = json.dumps(embedded, ensure_ascii=False)
    raw = raw.replace("</", "<\\/")
    return f"<script>window.__EMBEDDED__ = {raw};</script>"


def _build_ring_svg(pct: int) -> str:
    r = 42
    circumference = round(2 * 3.14159265 * r, 2)
    pass_dash = round(circumference * pct / 100, 2)
    return (
        f'<svg width="130" height="130" viewBox="0 0 100 100">'
        f'<circle cx="50" cy="50" r="{r}" fill="none" stroke="#e2e8f0" stroke-width="7"/>'
        f'<circle cx="50" cy="50" r="{r}" fill="none" stroke="#0d9488" stroke-width="7"'
        f' stroke-dasharray="{pass_dash} {circumference}" stroke-dashoffset="0" stroke-linecap="round"/>'
        f'</svg>'
    )


def _classify_pass_fail(counts: dict[str, int]) -> tuple[int, int]:
    pass_count = sum(
        v for k, v in counts.items() if k in ("BASELINE_VALID", "AV_BLOCKED_OR_NO_CHANGE", "AV_ANALYZE_BLOCKED")
    )
    fail_count = sum(
        v for k, v in counts.items() if k in ("BASELINE_INVALID", "AV_NOT_BLOCKED", "AV_ANALYZE_NOT_BLOCKED")
    )
    return pass_count, fail_count


def _sample_html_row(result: BatchTestResult, sample: SampleTestResult, report_dir: Path) -> str:
    verification = _sample_verification(result, sample)
    relative_dir = _relative_sample_report_dir(result, sample)
    classification_value = sample.classification.value
    label = _html_escape(_html_label(classification_value))
    row_class = _HTML_ROW_CLASS.get(classification_value, "")
    badge_class = "badge-pass" if row_class == "row-pass" else "badge-fail"
    is_av_analyze = result.test_case.mode.value == "av_analyze"

    effect = sample.evaluation.effect_observed
    effect_html = '<span class="effect-yes">✓</span>' if effect else '<span class="effect-no">—</span>'

    artifact_links = []
    if is_av_analyze:
        artifact_links = [
            _html_link(f"{relative_dir}/result.json", "result.json"),
            _html_link(f"{relative_dir}/before.txt", "before.txt"),
            _html_link(f"{relative_dir}/after.txt", "after.txt"),
        ]
        before_ss = Path(sample.report_dir) / "screenshot_before.png"
        after_ss = Path(sample.report_dir) / "screenshot_after.png"
        if before_ss.exists():
            artifact_links.append(_html_link(f"{relative_dir}/screenshot_before.png", "screenshot_before"))
        if after_ss.exists():
            artifact_links.append(_html_link(f"{relative_dir}/screenshot_after.png", "screenshot_after"))
    else:
        artifact_links = [
            _html_link(f"{relative_dir}/result.json", "result.json"),
            _html_link(f"{relative_dir}/before.txt", "before.txt"),
            _html_link(f"{relative_dir}/after.txt", "after.txt"),
            _html_link(f"{relative_dir}/sample_stdout.txt", "stdout"),
            _html_link(f"{relative_dir}/sample_stderr.txt", "stderr"),
        ]
        screenshot_path = Path(sample.report_dir) / "screenshot.png"
        if screenshot_path.exists():
            artifact_links.append(_html_link(f"{relative_dir}/screenshot.png", "screenshot"))

    sample_cmd = _html_escape(sample.sample_spec.command)
    verify_cmd = _html_escape(verification.command) if verification.command else "—"

    duration_str = _format_duration(sample.duration_seconds)

    if is_av_analyze:
        return _av_analyze_html_row(
            sample, row_class, relative_dir, sample_cmd, duration_str,
            "".join(artifact_links),
        )

    verify_cell = f'<td class="cmd"><div class="cmd-wrapper"><code title="{verify_cmd}">{verify_cmd}</code><button class="btn-copy" title="复制">⎘</button></div></td>'

    return f"""        <tr class="{row_class}">
          <td><strong>{_html_escape(sample.sample_spec.id)}</strong></td>
          <td><span class="badge {badge_class}"><span class="badge-dot"></span>{label}</span></td>
          <td class="effect-cell">{effect_html}</td>
          <td class="cmd"><div class="cmd-wrapper"><code title="{sample_cmd}">{sample_cmd}</code><button class="btn-copy" title="复制">⎘</button></div></td>
          {verify_cell}
          <td>{duration_str}</td>
          <td><div class="artifact-links">{"".join(artifact_links)}</div></td>
        </tr>"""


def _classification_counts(result: BatchTestResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sample in result.samples:
        counts[sample.classification.value] = counts.get(sample.classification.value, 0) + 1
    return counts


def _sample_verification(result: BatchTestResult, sample: SampleTestResult) -> VerificationSpec:
    return sample.sample_spec.verification or result.test_case.effective_verification()


def _relative_sample_report_dir(result: BatchTestResult, sample: SampleTestResult) -> str:
    batch_dir = Path(result.report_dir).resolve()
    sample_dir = Path(sample.report_dir).resolve()
    try:
        return sample_dir.relative_to(batch_dir).as_posix()
    except ValueError as error:
        raise ValueError("Sample report directory must be inside batch report directory") from error


def _html_escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _html_label(classification_value: str) -> str:
    return _HTML_CLASS_LABELS.get(classification_value, classification_value)


def _html_link(href: str, label: str) -> str:
    return f'<a href="#" data-href="{_html_escape(href)}" class="drawer-link">{_html_escape(label)}</a>'


def _html_image_compare_cell(image_compare_result: Any) -> str:
    if image_compare_result is None:
        return "<td>—</td>"
    value = image_compare_result.value
    if value is None:
        return "<td>—</td>"
    if value.classification.value == "AV_ANALYZE_BLOCKED":
        return '<td><span class="tag tag-blocked">存在差异</span></td>'
    return '<td><span class="tag tag-neutral-fail">基本相同</span></td>'


def _av_analyze_html_row(
    sample: SampleTestResult,
    row_class: str,
    relative_dir: str,
    sample_cmd: str,
    duration_str: str,
    artifact_html: str,
) -> str:
    # Log column
    log_cell = "<td>—</td>"
    log_blocked = False
    if sample.av_analyze_result:
        ar = sample.av_analyze_result
        log_blocked = ar.log_found
        if log_blocked:
            log_cell = '<td><span class="tag tag-blocked">存在记录</span></td>'
        else:
            log_cell = '<td><span class="tag tag-clean">不存在记录</span></td>'

    # Image column
    img_cell = _html_image_compare_cell(sample.image_compare_result)
    img_blocked = False
    if sample.image_compare_result and sample.image_compare_result.value:
        img_blocked = sample.image_compare_result.value.classification.value == "AV_ANALYZE_BLOCKED"

    # Combined verdict (OR logic)
    combined_blocked = log_blocked or img_blocked
    verdict_label = "✓ 已拦截" if combined_blocked else "✗ 未拦截"
    verdict_class = "badge-pass" if combined_blocked else "badge-fail"

    # If they disagree, annotate the verdict with a tooltip
    verdict_title = ""
    has_img = sample.image_compare_result is not None and sample.image_compare_result.value is not None
    img_label = "存在差异" if img_blocked else ("基本相同" if has_img else "无数据")
    log_label = "存在记录" if log_blocked else "不存在记录"
    if combined_blocked and log_blocked != img_blocked:
        verdict_title = f' title="日志{log_label} | 图片{img_label} → 综合判定: 已拦截 (OR)"'

    verdict_cell = f'<td><span class="badge {verdict_class}"{verdict_title}><span class="badge-dot"></span>{verdict_label}</span></td>'

    row_style = ' class="row-pass"' if combined_blocked else ' class="row-fail"'

    return f"""        <tr{row_style}>
          <td><strong>{_html_escape(sample.sample_spec.id)}</strong></td>
          {log_cell}
          {img_cell}
          {verdict_cell}
          <td class="cmd"><div class="cmd-wrapper"><code title="{sample_cmd}">{sample_cmd}</code><button class="btn-copy" title="复制">⎘</button></div></td>
          <td>{duration_str}</td>
          <td><div class="artifact-links">{artifact_html}</div></td>
        </tr>"""


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _image_compare_csv_text(result: Any) -> str:
    if result is None:
        return ""
    value = result.value
    if value is None:
        return "pending"
    return value.classification.value


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.0f}s"


def load_baseline_is_valid(path: str) -> bool:
    result_path = Path(path)
    data = json.loads(result_path.read_text(encoding="utf-8-sig"))
    if data.get("schema_version") == 2 and "summary" in data:
        samples = data.get("samples") or []
        return bool(samples) and all(sample.get("classification") == "BASELINE_VALID" for sample in samples)
    return data.get("classification") == "BASELINE_VALID"


def batch_classification(sample_classifications: tuple[Classification, ...]) -> Classification:
    if not sample_classifications:
        raise ValueError("Batch result requires at least one sample")
    first = sample_classifications[0]
    if first in {Classification.BASELINE_VALID, Classification.BASELINE_INVALID}:
        return Classification.BASELINE_VALID if all(item == Classification.BASELINE_VALID for item in sample_classifications) else Classification.BASELINE_INVALID
    if first in {Classification.AV_ANALYZE_BLOCKED, Classification.AV_ANALYZE_NOT_BLOCKED}:
        return Classification.AV_ANALYZE_NOT_BLOCKED if any(item == Classification.AV_ANALYZE_NOT_BLOCKED for item in sample_classifications) else Classification.AV_ANALYZE_BLOCKED
    return Classification.AV_NOT_BLOCKED if any(item == Classification.AV_NOT_BLOCKED for item in sample_classifications) else Classification.AV_BLOCKED_OR_NO_CHANGE


def _write_sample_artifacts(report_dir: Path, before: str, after: str, sample_stdout: str, sample_stderr: str, logs: tuple[Any, ...]) -> None:
    (report_dir / "before.txt").write_text(before, encoding="utf-8-sig")
    (report_dir / "after.txt").write_text(after, encoding="utf-8-sig")
    (report_dir / "sample_stdout.txt").write_text(sample_stdout, encoding="utf-8-sig")
    (report_dir / "sample_stderr.txt").write_text(sample_stderr, encoding="utf-8-sig")
    if logs:
        logs_dir = report_dir / "av_logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        for log in logs:
            safe_id = _sanitize_id(log.collector_id)
            (logs_dir / f"{safe_id}_stdout.txt").write_text(log.stdout, encoding="utf-8-sig")
            (logs_dir / f"{safe_id}_stderr.txt").write_text(log.stderr, encoding="utf-8-sig")


def _comparison_dicts(evaluation: Any) -> list[dict[str, Any]]:
    if evaluation is None:
        return []
    return [
        {
            "type": comparison.kind.value,
            "passed": comparison.passed,
            "detail": comparison.detail,
            "before_value": comparison.before_value,
            "after_value": comparison.after_value,
        }
        for comparison in evaluation.comparisons
    ]


def _log_dicts(logs: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [
        {
            "collector_id": log.collector_id,
            "command": log.command,
            "exit_code": log.exit_code,
            "capture_method": log.capture_method,
        }
        for log in logs
    ]


def _av_analyze_dict(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "log_found": result.log_found,
        "log_detail": result.log_detail,
        "screenshot_analysis": result.screenshot_analysis,
        "classification": result.classification.value,
    }


def _image_compare_dict(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    value = result.value
    if value is None:
        return {"pending": True}
    return {
        "log_found": value.log_found,
        "log_detail": value.log_detail,
        "screenshot_analysis": value.screenshot_analysis,
        "classification": value.classification.value,
    }


def write_batch_html_from_json(batch_json_path: Path, output_html_path: Path | None = None) -> None:
    """Reconstruct BatchTestResult from JSON and generate rich HTML."""
    import logging

    _LOGGER = logging.getLogger(__name__)

    data = json.loads(batch_json_path.read_text(encoding="utf-8-sig"))
    report_dir = batch_json_path.parent

    if data.get("schema_version") != 2 or "samples" not in data:
        raise ValueError("Not a batch result JSON (schema_version 2 with samples)")

    _dummy_creds = GuestCredentials("", "")
    mode = TestMode(data["mode"])

    samples: list[SampleTestResult] = []
    for sd in data["samples"]:
        relative = sd.get("report_dir", "")
        sample_dir = report_dir / relative
        sample_json_path = sample_dir / "result.json"
        if not sample_json_path.exists():
            _LOGGER.warning("Sample result.json not found: %s", sample_json_path)
            continue

        sample_json = json.loads(sample_json_path.read_text(encoding="utf-8-sig"))

        before_txt = (
            (sample_dir / "before.txt").read_text(encoding="utf-8-sig")
            if (sample_dir / "before.txt").exists()
            else ""
        )
        after_txt = (
            (sample_dir / "after.txt").read_text(encoding="utf-8-sig")
            if (sample_dir / "after.txt").exists()
            else ""
        )
        stdout_txt = (
            (sample_dir / "sample_stdout.txt").read_text(encoding="utf-8-sig")
            if (sample_dir / "sample_stdout.txt").exists()
            else ""
        )
        stderr_txt = (
            (sample_dir / "sample_stderr.txt").read_text(encoding="utf-8-sig")
            if (sample_dir / "sample_stderr.txt").exists()
            else ""
        )

        before_cmd = sample_json.get("before", {}).get("command", "log_collect")
        after_cmd = sample_json.get("after", {}).get("command", "log_collect")

        ar_data = sample_json.get("av_analyze_result")
        av_ar = None
        if ar_data:
            av_ar = AvAnalyzeResult(
                log_found=ar_data.get("log_found", False),
                log_detail=ar_data.get("log_detail", ""),
                screenshot_analysis=ar_data.get("screenshot_analysis"),
                classification=Classification(ar_data["classification"]),
            )

        ic_data = sample_json.get("image_compare_result")
        ic_deferred = None
        if ic_data and not ic_data.get("pending"):
            ic_value = AvAnalyzeResult(
                log_found=ic_data.get("log_found", False),
                log_detail=ic_data.get("log_detail", ""),
                screenshot_analysis=ic_data.get("screenshot_analysis"),
                classification=Classification(ic_data["classification"]),
            )
            ic_deferred = DeferredImageResult(value=ic_value)
        elif not ic_data:
            # Backward compat: extract from image_compare step in old reports
            for step in sample_json.get("steps", []):
                if step.get("name") == "image_compare" and step.get("detail"):
                    try:
                        ic_cls = Classification(step["detail"])
                        ic_deferred = DeferredImageResult(value=AvAnalyzeResult(
                            log_found=False,
                            screenshot_analysis=step["detail"],
                            classification=ic_cls,
                        ))
                    except ValueError:
                        pass
                    break

        sp = SampleTestResult(
            test_case=TestCase(
                vm_id=data["vm_id"],
                snapshot=data.get("snapshot"),
                mode=mode,
                sample_command=sd.get("sample_command", ""),
                verify_command="",
                credentials=_dummy_creds,
            ),
            sample_spec=SampleSpec(
                id=sd["id"],
                command=sd.get("sample_command", ""),
            ),
            report_dir=str(sample_dir),
            before=CommandResult(command=before_cmd, stdout=before_txt),
            sample=CommandResult(command="", stdout=stdout_txt, stderr=stderr_txt),
            after=CommandResult(command=after_cmd, stdout=after_txt),
            evaluation=EvaluationResult(
                changed=sd.get("changed", False),
                effect_observed=sd.get("effect_observed", False),
            ),
            classification=Classification(sd["classification"]),
            duration_seconds=sd.get("duration_seconds", 0),
            av_analyze_result=av_ar,
            image_compare_result=ic_deferred,
        )
        samples.append(sp)

    batch = BatchTestResult(
        test_case=TestCase(
            vm_id=data["vm_id"],
            snapshot=data.get("snapshot"),
            mode=mode,
            sample_command="",
            verify_command="",
            credentials=_dummy_creds,
        ),
        report_dir=str(report_dir),
        samples=tuple(samples),
        classification=Classification(data["summary"]["overall_classification"]),
        duration_seconds=data["summary"].get("duration_seconds", 0),
    )

    _write_batch_html(batch, report_dir, output_html_path)

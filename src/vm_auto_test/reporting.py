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
from vm_auto_test.models import BatchTestResult, Classification, SampleTestResult, TestResult, VerificationSpec
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
        },
        "samples": [
            {
                "id": sample.sample_spec.id,
                "classification": sample.classification.value,
                "changed": sample.changed,
                "effect_observed": sample.evaluation.effect_observed,
                "report_dir": _relative_sample_report_dir(result, sample),
                "steps": [asdict(step) for step in sample.steps],
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
    "report_dir",
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
                "report_dir": _relative_sample_report_dir(result, sample),
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
}

_HTML_ROW_CLASS: dict[str, str] = {
    "BASELINE_VALID": "row-pass",
    "BASELINE_INVALID": "row-fail",
    "AV_NOT_BLOCKED": "row-fail",
    "AV_BLOCKED_OR_NO_CHANGE": "row-pass",
}


def _write_batch_html(result: BatchTestResult, report_dir: Path) -> None:
    total = len(result.samples)
    counts = _classification_counts(result)
    pass_count, fail_count = _classify_pass_fail(counts)
    pass_pct = round(pass_count / total * 100) if total else 0
    fail_pct = 100 - pass_pct
    overall_label = _html_escape(_html_label(result.classification.value))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_cn = "BASELINE — 验证样本有效性" if result.test_case.mode.value == "baseline" else "AV — 验证杀软拦截"
    baseline_str = _html_escape(result.test_case.baseline_result or "")

    ring_svg = _build_ring_svg(pass_pct)

    stat_cards = ""
    for key, value in counts.items():
        label = _html_escape(_html_label(key))
        row_class = "stat-pass" if key in ("BASELINE_VALID", "AV_BLOCKED_OR_NO_CHANGE") else "stat-fail"
        stat_cards += f"""            <div class="stat-item {row_class}">
              <span class="stat-dot"></span>
              <span class="stat-label">{label}</span>
              <span class="stat-num">{value}</span>
            </div>
"""

    sample_rows = "\n".join(_sample_html_row(result, sample, report_dir) for sample in result.samples)

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
    .ring-legend .lg-fail{{color:#e74c3c}}

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
            <th data-sort="id">样本 ID <span class="sort-arrow">⇅</span></th>
            <th data-sort="class">判定结果 <span class="sort-arrow">⇅</span></th>
            <th data-sort="fx">效果发生 <span class="sort-arrow">⇅</span></th>
            <th data-sort="sc">样本命令 <span class="sort-arrow">⇅</span></th>
            <th data-sort="vc">验证命令 <span class="sort-arrow">⇅</span></th>
            <th>产出文件</th>
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
        var map={{id:0,class:1,fx:2,sc:3,vc:4}};
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
  </script>
</body>
</html>
"""
    (report_dir / "result.html").write_text(html_text, encoding="utf-8")


def _build_ring_svg(pct: int) -> str:
    r = 42
    circumference = round(2 * 3.14159265 * r, 2)
    pass_offset = 0
    fail_offset = round(circumference * (100 - pct) / 100, 2)
    return (
        f'<svg width="130" height="130" viewBox="0 0 100 100">'
        f'<circle cx="50" cy="50" r="{r}" fill="none" stroke="#e2e8f0" stroke-width="7"/>'
        f'<circle cx="50" cy="50" r="{r}" fill="none" stroke="#e74c3c" stroke-width="7"'
        f' stroke-dasharray="{circumference}" stroke-dashoffset="{fail_offset}" stroke-linecap="round"/>'
        f'<circle cx="50" cy="50" r="{r}" fill="none" stroke="#0d9488" stroke-width="7"'
        f' stroke-dasharray="{circumference}" stroke-dashoffset="{circumference}" stroke-linecap="round"'
        f' transform="rotate({360 * (100 - pct) / 100} 50 50)"/>'
        f'</svg>'
    )


def _classify_pass_fail(counts: dict[str, int]) -> tuple[int, int]:
    pass_count = sum(
        v for k, v in counts.items() if k in ("BASELINE_VALID", "AV_BLOCKED_OR_NO_CHANGE")
    )
    fail_count = sum(
        v for k, v in counts.items() if k in ("BASELINE_INVALID", "AV_NOT_BLOCKED")
    )
    return pass_count, fail_count


def _sample_html_row(result: BatchTestResult, sample: SampleTestResult, report_dir: Path) -> str:
    verification = _sample_verification(result, sample)
    relative_dir = _relative_sample_report_dir(result, sample)
    classification_value = sample.classification.value
    label = _html_escape(_html_label(classification_value))
    row_class = _HTML_ROW_CLASS.get(classification_value, "")
    badge_class = "badge-pass" if row_class == "row-pass" else "badge-fail"

    effect = sample.evaluation.effect_observed
    effect_html = '<span class="effect-yes">✓</span>' if effect else '<span class="effect-no">—</span>'

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
    verify_cmd = _html_escape(verification.command)

    return f"""        <tr class="{row_class}">
          <td><strong>{_html_escape(sample.sample_spec.id)}</strong></td>
          <td><span class="badge {badge_class}"><span class="badge-dot"></span>{label}</span></td>
          <td class="effect-cell">{effect_html}</td>
          <td class="cmd"><div class="cmd-wrapper"><code title="{sample_cmd}">{sample_cmd}</code><button class="btn-copy" title="复制">⎘</button></div></td>
          <td class="cmd"><div class="cmd-wrapper"><code title="{verify_cmd}">{verify_cmd}</code><button class="btn-copy" title="复制">⎘</button></div></td>
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
    return f'<a href="{_html_escape(href)}">{_html_escape(label)}</a>'


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


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

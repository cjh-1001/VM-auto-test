from __future__ import annotations

import csv
import html
import json
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
                "report_dir": str(Path(sample.report_dir).relative_to(result.report_dir)),
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


_EXCEL_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


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
    if text.startswith(_EXCEL_FORMULA_PREFIXES):
        return f"'{text}"
    return text


def _write_batch_html(result: BatchTestResult, report_dir: Path) -> None:
    counts = _classification_counts(result)
    count_items = "".join(
        f"<li><strong>{_html_escape(key)}</strong>: {_html_escape(value)}</li>"
        for key, value in counts.items()
    )
    sample_rows = "\n".join(_sample_html_row(result, sample, report_dir) for sample in result.samples)
    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>VM Auto Test Batch Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 2rem; color: #1f2937; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d1d5db; padding: 0.5rem; vertical-align: top; }}
    th {{ background: #f3f4f6; text-align: left; }}
    code {{ white-space: pre-wrap; word-break: break-word; }}
  </style>
</head>
<body>
  <h1>VM Auto Test Batch Report</h1>
  <section>
    <h2>Summary</h2>
    <dl>
      <dt>Mode</dt><dd>{_html_escape(result.test_case.mode.value)}</dd>
      <dt>VM</dt><dd><code>{_html_escape(result.test_case.vm_id)}</code></dd>
      <dt>Snapshot</dt><dd>{_html_escape(result.test_case.snapshot or "")}</dd>
      <dt>Baseline result</dt><dd><code>{_html_escape(result.test_case.baseline_result or "")}</code></dd>
      <dt>Total samples</dt><dd>{len(result.samples)}</dd>
      <dt>Overall classification</dt><dd>{_html_escape(result.classification.value)}</dd>
    </dl>
    <h3>Classification counts</h3>
    <ul>{count_items}</ul>
    <p><a href="result.json">result.json</a> · <a href="result.csv">result.csv</a></p>
  </section>
  <section>
    <h2>Samples</h2>
    <table>
      <thead>
        <tr>
          <th>Sample ID</th>
          <th>Classification</th>
          <th>Changed</th>
          <th>Effect observed</th>
          <th>Sample command</th>
          <th>Verification command</th>
          <th>Artifacts</th>
        </tr>
      </thead>
      <tbody>
{sample_rows}
      </tbody>
    </table>
  </section>
</body>
</html>
"""
    (report_dir / "result.html").write_text(html_text, encoding="utf-8")


def _sample_html_row(result: BatchTestResult, sample: SampleTestResult, report_dir: Path) -> str:
    verification = _sample_verification(result, sample)
    relative_dir = _relative_sample_report_dir(result, sample)
    artifact_links = [
        _html_link(f"{relative_dir}/result.json", "result.json"),
        _html_link(f"{relative_dir}/before.txt", "before.txt"),
        _html_link(f"{relative_dir}/after.txt", "after.txt"),
        _html_link(f"{relative_dir}/sample_stdout.txt", "sample_stdout.txt"),
        _html_link(f"{relative_dir}/sample_stderr.txt", "sample_stderr.txt"),
    ]
    screenshot_path = Path(sample.report_dir) / "screenshot.png"
    if screenshot_path.exists():
        artifact_links.append(_html_link(f"{relative_dir}/screenshot.png", "screenshot.png"))
    return f"""        <tr>
          <td>{_html_escape(sample.sample_spec.id)}</td>
          <td>{_html_escape(sample.classification.value)}</td>
          <td>{_html_escape(_bool_text(sample.changed))}</td>
          <td>{_html_escape(_bool_text(sample.evaluation.effect_observed))}</td>
          <td><code>{_html_escape(sample.sample_spec.command)}</code></td>
          <td><code>{_html_escape(verification.command)}</code></td>
          <td>{"<br>".join(artifact_links)}</td>
        </tr>"""


def _classification_counts(result: BatchTestResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sample in result.samples:
        counts[sample.classification.value] = counts.get(sample.classification.value, 0) + 1
    return counts


def _sample_verification(result: BatchTestResult, sample: SampleTestResult) -> VerificationSpec:
    return sample.sample_spec.verification or result.test_case.effective_verification()


def _relative_sample_report_dir(result: BatchTestResult, sample: SampleTestResult) -> str:
    return Path(sample.report_dir).relative_to(result.report_dir).as_posix()


def _html_escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


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

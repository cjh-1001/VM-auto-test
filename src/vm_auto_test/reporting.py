from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from vm_auto_test.evaluator import output_hash
from vm_auto_test.models import BatchTestResult, Classification, SampleTestResult, TestResult
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

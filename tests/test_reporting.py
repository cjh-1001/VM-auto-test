from __future__ import annotations

import json

from vm_auto_test.models import (
    BatchTestResult,
    Classification,
    CommandResult,
    EvaluationResult,
    GuestCredentials,
    SampleSpec,
    SampleTestResult,
    StepResult,
    TestCase,
    TestMode,
)
from vm_auto_test.reporting import load_baseline_is_valid, write_batch_report


def make_sample_result(tmp_path, sample_id: str, classification: Classification) -> SampleTestResult:
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="sample.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
    )
    return SampleTestResult(
        test_case=test_case,
        sample_spec=SampleSpec(id=sample_id, command=f"{sample_id}.exe"),
        report_dir=str(tmp_path / "samples" / sample_id),
        before=CommandResult(command="verify", stdout="before"),
        sample=CommandResult(command=f"{sample_id}.exe", stdout="sample"),
        after=CommandResult(command="verify", stdout="after"),
        evaluation=EvaluationResult(changed=True, effect_observed=True),
        classification=classification,
        steps=(StepResult("evaluate", "passed"),),
    )


def test_write_batch_report_creates_summary_and_sample_artifacts(tmp_path):
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="sample.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
    )
    report = BatchTestResult(
        test_case=test_case,
        report_dir=str(tmp_path),
        samples=(make_sample_result(tmp_path, "one", Classification.BASELINE_VALID),),
        classification=Classification.BASELINE_VALID,
    )

    write_batch_report(report)

    data = json.loads((tmp_path / "result.json").read_text(encoding="utf-8-sig"))
    assert data["schema_version"] == 2
    assert data["summary"]["total"] == 1
    assert data["summary"]["overall_classification"] == "BASELINE_VALID"
    assert (tmp_path / "samples" / "one" / "result.json").exists()
    assert (tmp_path / "samples" / "one" / "before.txt").read_text(encoding="utf-8-sig") == "before"


def test_load_baseline_accepts_batch_report_only_when_all_samples_valid(tmp_path):
    valid_path = tmp_path / "valid.json"
    valid_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "mode": "baseline",
                "summary": {"overall_classification": "BASELINE_VALID"},
                "samples": [{"classification": "BASELINE_VALID"}],
            }
        ),
        encoding="utf-8-sig",
    )
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "mode": "baseline",
                "summary": {"overall_classification": "BASELINE_INVALID"},
                "samples": [{"classification": "BASELINE_INVALID"}],
            }
        ),
        encoding="utf-8-sig",
    )

    assert load_baseline_is_valid(str(valid_path)) is True
    assert load_baseline_is_valid(str(invalid_path)) is False

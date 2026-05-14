from __future__ import annotations

import csv
import io
import json

from vm_auto_test.models import (
    BatchTestResult,
    Classification,
    CommandResult,
    EvaluationResult,
    GuestCredentials,
    SampleSpec,
    SampleTestResult,
    Shell,
    StepResult,
    TestCase,
    TestMode,
    VerificationSpec,
)
from vm_auto_test.reporting import load_baseline_is_valid, write_batch_report


def make_sample_result(
    tmp_path,
    sample_id: str,
    classification: Classification,
    *,
    test_case: TestCase | None = None,
    sample_spec: SampleSpec | None = None,
    before: str = "before",
    after: str = "after",
) -> SampleTestResult:
    test_case = test_case or TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="sample.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
    )
    sample_spec = sample_spec or SampleSpec(id=sample_id, command=f"{sample_id}.exe")
    return SampleTestResult(
        test_case=test_case,
        sample_spec=sample_spec,
        report_dir=str(tmp_path / "samples" / sample_id),
        before=CommandResult(command="verify", stdout=before),
        sample=CommandResult(command=sample_spec.command, stdout="sample"),
        after=CommandResult(command="verify", stdout=after),
        evaluation=EvaluationResult(changed=before != after, effect_observed=before != after),
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
    assert "duration_seconds" in data["summary"]
    assert (tmp_path / "samples" / "one" / "result.json").exists()
    assert (tmp_path / "samples" / "one" / "before.txt").read_text(encoding="utf-8-sig") == "before"


def test_write_batch_report_creates_csv_and_html(tmp_path):
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
        samples=(
            make_sample_result(tmp_path, "one", Classification.BASELINE_VALID, test_case=test_case),
            make_sample_result(tmp_path, "two", Classification.BASELINE_INVALID, test_case=test_case, before="same", after="same"),
        ),
        classification=Classification.BASELINE_INVALID,
    )

    write_batch_report(report)

    csv_path = tmp_path / "result.csv"
    html_path = tmp_path / "result.html"
    assert csv_path.exists()
    assert html_path.exists()
    rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
    assert [row["sample_id"] for row in rows] == ["one", "two"]
    assert rows[0]["classification"] == "BASELINE_VALID"
    assert rows[0]["report_dir"] == "samples/one"
    assert "duration_seconds" in rows[0]
    html = html_path.read_text(encoding="utf-8")
    assert "VM Auto Test — 批量测试报告" in html
    assert "result.csv" in html
    assert "samples/one/result.json" in html


def test_batch_csv_neutralizes_excel_formula_cells(tmp_path):
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
        samples=(
            make_sample_result(
                tmp_path,
                "formula",
                Classification.BASELINE_VALID,
                test_case=test_case,
                sample_spec=SampleSpec(id="formula", command="=calc.exe"),
            ),
        ),
        classification=Classification.BASELINE_VALID,
    )

    write_batch_report(report)

    rows = list(csv.DictReader((tmp_path / "result.csv").read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0]["sample_command"] == "'=calc.exe"


def test_batch_csv_neutralizes_excel_formula_cells_after_whitespace(tmp_path):
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
        samples=(
            make_sample_result(
                tmp_path,
                "formula",
                Classification.BASELINE_VALID,
                test_case=test_case,
                sample_spec=SampleSpec(id="formula", command=" \n=calc.exe"),
            ),
        ),
        classification=Classification.BASELINE_VALID,
    )

    write_batch_report(report)

    rows = list(csv.DictReader(io.StringIO((tmp_path / "result.csv").read_text(encoding="utf-8-sig"))))
    assert rows[0]["sample_command"] == "' \n=calc.exe"


def test_batch_csv_neutralizes_excel_formula_cells_after_zero_width_prefix(tmp_path):
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
        samples=(
            make_sample_result(
                tmp_path,
                "formula",
                Classification.BASELINE_VALID,
                test_case=test_case,
                sample_spec=SampleSpec(id="formula", command="​=calc.exe"),
            ),
        ),
        classification=Classification.BASELINE_VALID,
    )

    write_batch_report(report)

    rows = list(csv.DictReader((tmp_path / "result.csv").read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0]["sample_command"] == "'​=calc.exe"


def test_batch_report_rejects_sample_report_dir_outside_batch_root(tmp_path):
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="sample.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
    )
    sample = make_sample_result(tmp_path, "outside", Classification.BASELINE_VALID, test_case=test_case)
    sample = SampleTestResult(
        test_case=sample.test_case,
        sample_spec=sample.sample_spec,
        report_dir=str(tmp_path.parent / "outside"),
        before=sample.before,
        sample=sample.sample,
        after=sample.after,
        evaluation=sample.evaluation,
        classification=sample.classification,
        steps=sample.steps,
        logs=sample.logs,
    )
    report = BatchTestResult(
        test_case=test_case,
        report_dir=str(tmp_path),
        samples=(sample,),
        classification=Classification.BASELINE_VALID,
    )

    try:
        write_batch_report(report)
    except ValueError as error:
        assert "inside batch report directory" in str(error)
    else:
        raise AssertionError("Expected ValueError for sample report dir outside batch root")


def test_batch_html_escapes_dynamic_values(tmp_path):
    test_case = TestCase(
        vm_id="vm<script>",
        snapshot="clean&safe",
        mode=TestMode.BASELINE,
        sample_command="sample.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
    )
    report = BatchTestResult(
        test_case=test_case,
        report_dir=str(tmp_path),
        samples=(
            make_sample_result(
                tmp_path,
                "html-sample",
                Classification.BASELINE_VALID,
                test_case=test_case,
                sample_spec=SampleSpec(id="html-sample", command="<script>alert(1)</script>"),
            ),
        ),
        classification=Classification.BASELINE_VALID,
    )

    write_batch_report(report)

    html = (tmp_path / "result.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "vm&lt;script&gt;" in html
    assert "clean&amp;safe" in html


def test_batch_reports_use_sample_specific_verification(tmp_path):
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="sample.exe",
        verify_command="default verify",
        credentials=GuestCredentials("user", "pass"),
    )
    sample_spec = SampleSpec(
        id="one",
        command="one.exe",
        verification=VerificationSpec(command="sample verify", shell=Shell.CMD),
    )
    report = BatchTestResult(
        test_case=test_case,
        report_dir=str(tmp_path),
        samples=(make_sample_result(tmp_path, "one", Classification.BASELINE_VALID, test_case=test_case, sample_spec=sample_spec),),
        classification=Classification.BASELINE_VALID,
    )

    write_batch_report(report)

    rows = list(csv.DictReader((tmp_path / "result.csv").read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0]["verify_command"] == "sample verify"
    assert rows[0]["verify_shell"] == "cmd"
    html = (tmp_path / "result.html").read_text(encoding="utf-8")
    assert "sample verify" in html


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

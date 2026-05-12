from __future__ import annotations

import json
from typing import Any

import pytest

from vm_auto_test import cli


VALID_CONFIG = """
vm_id: vm1
snapshot: clean
mode: baseline
guest:
  user: Administrator
  password: secret
sample:
  command: sample.exe
  shell: cmd
verification:
  command: verify
  shell: powershell
"""


def test_config_validate_accepts_valid_config(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG, encoding="utf-8")

    exit_code = cli.main(["config", "validate", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Config is valid" in captured.out
    assert str(config_path) in captured.out


def test_config_validate_rejects_invalid_config(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("vm_id: vm1\n", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        cli.main(["config", "validate", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert "Config error" in captured.err
    assert "guest" in captured.err


def test_report_writes_html_from_json_input(tmp_path, capsys):
    input_path = tmp_path / "result.json"
    output_path = tmp_path / "report.html"
    input_path.write_text(
        json.dumps({"schema_version": 2, "summary": {"total": 1}, "samples": []}),
        encoding="utf-8",
    )

    exit_code = cli.main(["report", "--input", str(input_path), "--output", str(output_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert output_path.exists()
    assert "VM Auto Test" in output_path.read_text(encoding="utf-8")
    assert "Report written to" in captured.out


def test_report_writes_json_from_json_input(tmp_path):
    input_path = tmp_path / "result.json"
    output_path = tmp_path / "copy.json"
    payload = {"schema_version": 2, "summary": {"total": 1}, "samples": [{"id": "one"}]}
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = cli.main([
        "report",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--format",
        "json",
    ])

    assert exit_code == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == payload


def test_report_rejects_missing_input_file(tmp_path, capsys):
    missing_path = tmp_path / "missing.json"
    output_path = tmp_path / "report.html"

    with pytest.raises(SystemExit) as error:
        cli.main(["report", "--input", str(missing_path), "--output", str(output_path)])

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert "File not found" in captured.err


def test_report_rejects_invalid_json(tmp_path, capsys):
    input_path = tmp_path / "result.json"
    output_path = tmp_path / "report.html"
    input_path.write_text("not-json", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        cli.main(["report", "--input", str(input_path), "--output", str(output_path)])

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert "Report error: invalid JSON input" in captured.err


def test_run_rejects_missing_direct_args(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["run"])

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert "run requires --config" in captured.err


def test_run_config_alias_rejects_direct_arg_mixing(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG, encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        cli.main(["run", "--config", str(config_path), "--vm", "other-vm"])

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert "cannot combine --config" in captured.err


def test_run_config_alias_uses_configured_provider(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG, encoding="utf-8")
    seen: dict[str, Any] = {}

    class FakeOrchestrator:
        def __init__(self, provider, reports_dir, progress=None):
            seen["provider"] = provider
            seen["reports_dir"] = reports_dir

        async def run(self, test_case):
            seen["test_case"] = test_case
            return object()

    monkeypatch.setattr(cli, "create_provider", lambda provider_type: f"provider:{provider_type}")
    monkeypatch.setattr(cli, "TestOrchestrator", FakeOrchestrator)

    exit_code = cli.main(["run", "--config", str(config_path)])

    assert exit_code == 0
    assert seen["provider"] == "provider:vmrun"
    assert seen["test_case"].vm_id == "vm1"


def test_doctor_reports_ok_when_environment_is_ready(monkeypatch, tmp_path, capsys):
    vmrun_path = tmp_path / "vmrun.exe"
    vmrun_path.write_text("", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG, encoding="utf-8")
    reports_dir = tmp_path / "reports"
    monkeypatch.setenv("VMRUN_PATH", str(vmrun_path))

    exit_code = cli.main([
        "doctor",
        "--config",
        str(config_path),
        "--reports-dir",
        str(reports_dir),
    ])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "VM Auto Test Doctor" in captured.out
    assert "[OK] Python" in captured.out
    assert "[OK] VMRUN_PATH" in captured.out
    assert "[OK] Config" in captured.out
    assert "[OK] Reports directory" in captured.out
    assert "secret" not in captured.out


def test_doctor_returns_dependency_error_when_vmrun_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("VMRUN_PATH", raising=False)
    env_path = tmp_path / "empty.env"
    env_path.write_text("", encoding="utf-8")

    exit_code = cli.main(["--env-file", str(env_path), "doctor", "--reports-dir", str(tmp_path / "reports")])

    captured = capsys.readouterr()
    assert exit_code == 3
    assert "[FAIL] VMRUN_PATH" in captured.out


def test_doctor_reports_invalid_config_without_printing_password(monkeypatch, tmp_path, capsys):
    vmrun_path = tmp_path / "vmrun.exe"
    vmrun_path.write_text("", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("guest:\n  password: super-secret\n", encoding="utf-8")
    monkeypatch.setenv("VMRUN_PATH", str(vmrun_path))

    exit_code = cli.main(["doctor", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 3
    assert "[FAIL] Config" in captured.out
    assert "super-secret" not in captured.out

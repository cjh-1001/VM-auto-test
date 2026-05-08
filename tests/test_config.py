from __future__ import annotations

from pathlib import Path

import pytest

from vm_auto_test.config import (
    CommandConfig,
    GuestConfig,
    NormalizeConfig,
    TestConfig,
    TimeoutConfig,
    VerificationConfig,
    parse_config,
    parse_csv_samples,
    resolve_guest_password,
    scan_samples_from_directory,
    to_test_case,
    to_yaml_dict,
)
from vm_auto_test.models import AvLogCollectorSpec, ComparisonKind, Shell, TestMode


def test_parse_baseline_config_converts_to_test_case(monkeypatch):
    monkeypatch.setenv("VMWARE_GUEST_PASSWORD", "secret")
    config = parse_config(
        {
            "vm_id": "F:\\VMs\\Win10\\Win10.vmx",
            "snapshot": "clean-base",
            "mode": "baseline",
            "guest": {"user": "Administrator", "password_env": "VMWARE_GUEST_PASSWORD"},
            "sample": {"command": "C:\\Samples\\sample.exe", "shell": "cmd"},
            "verification": {"command": "Get-Content C:\\marker.txt", "shell": "powershell"},
            "reports_dir": "reports",
        }
    )

    test_case = to_test_case(config)

    assert test_case.vm_id == "F:\\VMs\\Win10\\Win10.vmx"
    assert test_case.snapshot == "clean-base"
    assert test_case.mode == TestMode.BASELINE
    assert test_case.credentials.password == "secret"
    assert test_case.sample_shell == Shell.CMD
    assert test_case.verify_shell == Shell.POWERSHELL


def test_parse_av_config_requires_baseline_result():
    with pytest.raises(ValueError, match="baseline_result"):
        parse_config(
            {
                "vm_id": "vm1",
                "snapshot": "av",
                "mode": "av",
                "guest": {"user": "Administrator"},
                "sample": {"command": "sample.exe", "shell": "cmd"},
                "verification": {"command": "verify", "shell": "powershell"},
            }
        )


def test_resolve_guest_password_prefers_explicit_password(monkeypatch):
    monkeypatch.setenv("VMWARE_GUEST_PASSWORD", "env-secret")

    assert resolve_guest_password(GuestConfig(user="u"), password="explicit") == "explicit"


def test_resolve_guest_password_uses_configured_env(monkeypatch):
    monkeypatch.setenv("CUSTOM_GUEST_PASSWORD", "env-secret")

    assert resolve_guest_password(GuestConfig(user="u", password_env="CUSTOM_GUEST_PASSWORD")) == "env-secret"


def test_to_yaml_dict_does_not_include_password_when_only_env_is_set():
    config = TestConfig(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        guest=GuestConfig(user="Administrator", password_env="VMWARE_GUEST_PASSWORD"),
        sample=CommandConfig(command="sample.exe", shell=Shell.CMD),
        verification=VerificationConfig(command="verify", shell=Shell.POWERSHELL),
        timeouts=TimeoutConfig(),
        normalize=NormalizeConfig(),
    )

    data = to_yaml_dict(config)

    assert data["guest"] == {
        "user": "Administrator",
        "password_env": "VMWARE_GUEST_PASSWORD",
    }
    assert "password" not in data["guest"]


def test_parse_config_rejects_invalid_boolean_string():
    with pytest.raises(ValueError, match="must be a boolean"):
        parse_config(
            {
                "vm_id": "vm1",
                "snapshot": "clean",
                "mode": "baseline",
                "guest": {"user": "Administrator"},
                "sample": {"command": "sample.exe", "shell": "cmd"},
                "verification": {"command": "verify", "shell": "powershell"},
                "normalize": {"trim": "sometimes"},
            }
        )


def test_parse_config_supports_multiple_samples_and_comparisons(monkeypatch):
    monkeypatch.setenv("VMWARE_GUEST_PASSWORD", "secret")
    config = parse_config(
        {
            "vm_id": "vm1",
            "snapshot": "clean",
            "mode": "baseline",
            "guest": {"user": "Administrator"},
            "samples": [
                {"id": "one", "command": "one.exe", "shell": "cmd"},
                {
                    "id": "two",
                    "command": "two.ps1",
                    "shell": "powershell",
                    "verification": {
                        "command": "Get-Content C:\\two.json",
                        "shell": "powershell",
                        "comparisons": [
                            {"type": "json_field", "path": "result.status", "expected": "created"}
                        ],
                    },
                },
            ],
            "verification": {
                "command": "verify",
                "shell": "powershell",
                "comparisons": [{"type": "contains", "value": "created"}],
            },
            "av_logs": {
                "collectors": [
                    {
                        "id": "events",
                        "type": "guest_command",
                        "command": "Get-WinEvent -LogName Application -MaxEvents 1",
                        "shell": "powershell",
                    }
                ]
            },
        }
    )

    test_case = to_test_case(config)

    assert [sample.id for sample in test_case.effective_samples()] == ["one", "two"]
    assert test_case.effective_verification().comparisons[0].kind == ComparisonKind.CONTAINS
    assert test_case.effective_samples()[1].verification.comparisons[0].kind == ComparisonKind.JSON_FIELD
    assert test_case.av_log_collectors == (
        AvLogCollectorSpec(
            id="events",
            type="guest_command",
            command="Get-WinEvent -LogName Application -MaxEvents 1",
            shell=Shell.POWERSHELL,
        ),
    )


def test_parse_config_rejects_sample_and_samples_together():
    with pytest.raises(ValueError, match="sample.*samples"):
        parse_config(
            {
                "vm_id": "vm1",
                "snapshot": "clean",
                "mode": "baseline",
                "guest": {"user": "Administrator"},
                "sample": {"command": "sample.exe", "shell": "cmd"},
                "samples": [{"id": "one", "command": "one.exe", "shell": "cmd"}],
                "verification": {"command": "verify", "shell": "powershell"},
            }
        )


def test_parse_config_rejects_unsafe_sample_id():
    with pytest.raises(ValueError, match="Sample id"):
        parse_config(
            {
                "vm_id": "vm1",
                "snapshot": "clean",
                "mode": "baseline",
                "guest": {"user": "Administrator"},
                "samples": [{"id": "../escape", "command": "one.exe", "shell": "cmd"}],
                "verification": {"command": "verify", "shell": "powershell"},
            }
        )


def test_scan_samples_from_directory_detects_exe_and_bat_files(tmp_path):
    (tmp_path / "one.exe").write_text("")
    (tmp_path / "two.exe").write_text("")
    (tmp_path / "run.bat").write_text("")
    (tmp_path / "readme.txt").write_text("")

    samples = scan_samples_from_directory(tmp_path)

    assert len(samples) == 3
    ids = [sample.id for sample in samples]
    assert "one" in ids
    assert "two" in ids
    assert "run" in ids
    assert all(sample.shell == Shell.CMD for sample in samples)


def test_scan_samples_from_directory_infers_powershell_for_ps1(tmp_path):
    (tmp_path / "script.ps1").write_text("")

    samples = scan_samples_from_directory(tmp_path)

    assert len(samples) == 1
    assert samples[0].shell == Shell.POWERSHELL
    assert samples[0].id == "script"


def test_scan_samples_from_directory_uses_custom_globs(tmp_path):
    (tmp_path / "one.exe").write_text("")
    (tmp_path / "two.bat").write_text("")

    samples = scan_samples_from_directory(tmp_path, globs=("*.exe",))

    assert len(samples) == 1
    assert samples[0].id == "one"


def test_scan_samples_from_directory_returns_sorted(tmp_path):
    (tmp_path / "c.exe").write_text("")
    (tmp_path / "a.exe").write_text("")
    (tmp_path / "b.exe").write_text("")

    samples = scan_samples_from_directory(tmp_path)

    assert [sample.id for sample in samples] == ["a", "b", "c"]


def test_scan_samples_from_directory_raises_on_missing_directory():
    with pytest.raises(ValueError, match="Not a directory"):
        scan_samples_from_directory(Path("nonexistent-dir"))


def test_scan_samples_from_directory_raises_when_no_samples_found(tmp_path):
    (tmp_path / "readme.txt").write_text("")

    with pytest.raises(ValueError, match="No sample files found"):
        scan_samples_from_directory(tmp_path)


def test_scan_samples_from_directory_sanitizes_sample_ids(tmp_path):
    (tmp_path / "sample (copy).exe").write_text("")

    samples = scan_samples_from_directory(tmp_path)

    assert len(samples) == 1
    assert samples[0].id == "sample (copy)"


_CSV_HEADER = "sample_file,shell,verify_command,verify_shell"


def _write_csv(path: Path, lines: list[str]) -> Path:
    content = "\n".join([_CSV_HEADER] + lines) + "\n"
    path.write_text(content, encoding="utf-8-sig")
    return path


def test_parse_csv_samples_basic(tmp_path):
    csv_path = _write_csv(
        tmp_path / "samples.csv",
        [
            "one.exe,cmd,hostname,cmd",
            "two.ps1,powershell,Get-Content C:\\marker.txt,powershell",
        ],
    )

    samples = parse_csv_samples(csv_path, samples_base_dir="C:\\Samples")

    assert len(samples) == 2
    assert samples[0].id == "one"
    assert samples[0].command == "C:\\Samples\\one.exe"
    assert samples[0].shell == Shell.CMD
    assert samples[0].verification.command == "hostname"
    assert samples[0].verification.shell == Shell.CMD

    assert samples[1].id == "two"
    assert samples[1].command == "C:\\Samples\\two.ps1"
    assert samples[1].shell == Shell.POWERSHELL
    assert samples[1].verification.command == "Get-Content C:\\marker.txt"
    assert samples[1].verification.shell == Shell.POWERSHELL


def test_parse_csv_samples_absolute_path_unchanged(tmp_path):
    csv_path = _write_csv(
        tmp_path / "samples.csv",
        ["C:\\abs\\payload.exe,cmd,verify,cmd"],
    )

    samples = parse_csv_samples(csv_path)

    assert samples[0].command == "C:\\abs\\payload.exe"


def test_parse_csv_samples_quoted_field_with_comma(tmp_path):
    csv_path = _write_csv(
        tmp_path / "samples.csv",
        ['sample.exe,cmd,"Compare-Object (Get-ItemProperty HKLM:\\.. -Name F).F, something",powershell'],
    )

    samples = parse_csv_samples(csv_path, samples_base_dir="C:\\Samples")

    assert "Compare-Object" in samples[0].verification.command
    assert "," in samples[0].verification.command


def test_parse_csv_samples_raises_missing_columns(tmp_path):
    csv_path = _write_csv(tmp_path / "samples.csv", ["only.exe,cmd,missing_fourth"])

    with pytest.raises(ValueError, match="expected 4 columns"):
        parse_csv_samples(csv_path, samples_base_dir="C:\\Samples")


def test_parse_csv_samples_raises_invalid_shell(tmp_path):
    csv_path = _write_csv(tmp_path / "samples.csv", ["bad.exe,bash,verify,cmd"])

    with pytest.raises(ValueError, match="shell must be"):
        parse_csv_samples(csv_path, samples_base_dir="C:\\Samples")


def test_parse_csv_samples_raises_relative_path_without_base_dir(tmp_path):
    csv_path = _write_csv(tmp_path / "samples.csv", ["sample.exe,cmd,verify,cmd"])

    with pytest.raises(ValueError, match="base-dir"):
        parse_csv_samples(csv_path)


def test_parse_csv_samples_raises_empty_csv(tmp_path):
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("", encoding="utf-8-sig")

    with pytest.raises(ValueError, match="empty"):
        parse_csv_samples(csv_path)


def test_parse_csv_samples_raises_only_header(tmp_path):
    csv_path = tmp_path / "header_only.csv"
    csv_path.write_text(_CSV_HEADER + "\n", encoding="utf-8-sig")

    with pytest.raises(ValueError, match="No data rows"):
        parse_csv_samples(csv_path)

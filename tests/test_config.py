from __future__ import annotations

import pytest

from vm_auto_test.config import (
    CommandConfig,
    GuestConfig,
    NormalizeConfig,
    TestConfig,
    TimeoutConfig,
    VerificationConfig,
    parse_config,
    resolve_guest_password,
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

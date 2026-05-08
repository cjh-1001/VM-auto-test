from __future__ import annotations

import pytest

from vm_auto_test.env import is_env_configured, load_env_file


def test_load_env_file_sets_values(tmp_path, monkeypatch):
    env_file = tmp_path / "lab.env"
    env_file.write_text(
        "# comment\nVMWARE_GUEST_USER=Administrator\nVMWARE_GUEST_PASSWORD='secret'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("VMWARE_GUEST_USER", raising=False)
    monkeypatch.delenv("VMWARE_GUEST_PASSWORD", raising=False)

    load_env_file(env_file)

    assert env_file.exists()
    assert __import__("os").environ["VMWARE_GUEST_USER"] == "Administrator"
    assert __import__("os").environ["VMWARE_GUEST_PASSWORD"] == "secret"


def test_load_env_file_does_not_override_existing_values(tmp_path, monkeypatch):
    env_file = tmp_path / "lab.env"
    env_file.write_text("VMWARE_GUEST_PASSWORD=file-secret\n", encoding="utf-8")
    monkeypatch.setenv("VMWARE_GUEST_PASSWORD", "existing-secret")

    load_env_file(env_file)

    assert __import__("os").environ["VMWARE_GUEST_PASSWORD"] == "existing-secret"


def test_load_env_file_supports_quoted_windows_paths(tmp_path, monkeypatch):
    env_file = tmp_path / "lab.env"
    env_file.write_text(
        'VMRUN_PATH="C:\\Program Files (x86)\\VMware\\VMware Workstation\\vmrun.exe"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("VMRUN_PATH", raising=False)

    load_env_file(env_file)

    assert __import__("os").environ["VMRUN_PATH"] == "C:\\Program Files (x86)\\VMware\\VMware Workstation\\vmrun.exe"


def test_load_env_file_rejects_invalid_lines(tmp_path):
    env_file = tmp_path / "lab.env"
    env_file.write_text("VMWARE_GUEST_PASSWORD\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected KEY=VALUE"):
        load_env_file(env_file)


def test_is_env_configured_returns_true_when_vmrun_exists_and_user_set(tmp_path, monkeypatch):
    vmrun_exe = tmp_path / "vmrun.exe"
    vmrun_exe.write_text("")
    monkeypatch.setenv("VMRUN_PATH", str(vmrun_exe))
    monkeypatch.setenv("VMWARE_GUEST_USER", "Administrator")

    assert is_env_configured()


def test_is_env_configured_returns_false_when_vmrun_missing(monkeypatch):
    monkeypatch.delenv("VMRUN_PATH", raising=False)
    monkeypatch.setenv("VMWARE_GUEST_USER", "Administrator")

    assert not is_env_configured()


def test_is_env_configured_returns_false_when_vmrun_not_a_file(tmp_path, monkeypatch):
    nonexistent = tmp_path / "nonexistent" / "vmrun.exe"
    monkeypatch.setenv("VMRUN_PATH", str(nonexistent))
    monkeypatch.setenv("VMWARE_GUEST_USER", "Administrator")

    assert not is_env_configured()


def test_is_env_configured_returns_false_when_user_empty(tmp_path, monkeypatch):
    vmrun_exe = tmp_path / "vmrun.exe"
    vmrun_exe.write_text("")
    monkeypatch.setenv("VMRUN_PATH", str(vmrun_exe))
    monkeypatch.delenv("VMWARE_GUEST_USER", raising=False)

    assert not is_env_configured()

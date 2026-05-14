from __future__ import annotations

import asyncio
import json

import pytest
from pathlib import Path

from vm_auto_test.av_detection import build_detection_command, parse_detection_result
from vm_auto_test.cli import _BACK, choose_from_list, choose_value, clean_cli_value, format_cli_error, print_progress
from vm_auto_test.evaluator import normalize_output
from vm_auto_test.models import Classification, CommandResult, GuestCredentials, Shell, StepResult, TestCase, TestMode
from vm_auto_test.orchestrator import TestOrchestrator
from vm_auto_test.providers.vmrun_provider import (
    VmrunProvider,
    _make_cmd_wrapper,
    _make_powershell_wrapper,
)

from conftest import FakeProvider, run_case


@pytest.mark.asyncio
async def test_baseline_is_valid_when_verification_output_changes(tmp_path):
    result, provider = await run_case(tmp_path, TestMode.BASELINE, "missing", "present")

    assert result.changed is True
    assert result.classification == Classification.BASELINE_VALID
    assert provider.commands == [
        "revert:clean",
        "start",
        "wait",
        "Get-Item C:\\marker.txt",
        "C:\\Samples\\sample.exe",
        "Get-Item C:\\marker.txt",
    ]
    report_dir = Path(result.report_dir)
    assert report_dir.parent == tmp_path
    assert (report_dir / "result.json").exists()
    assert (report_dir / "before.txt").read_text(encoding="utf-8-sig") == "missing"
    assert (report_dir / "after.txt").read_text(encoding="utf-8-sig") == "present"


@pytest.mark.asyncio
async def test_baseline_is_invalid_when_verification_output_does_not_change(tmp_path):
    result, _ = await run_case(tmp_path, TestMode.BASELINE, "same", "same")

    assert result.changed is False
    assert result.classification == Classification.BASELINE_INVALID


@pytest.mark.asyncio
async def test_av_runs_without_baseline_result(tmp_path):
    provider = FakeProvider(outputs=["NONE", "same", "sample output", "same"])
    test_case = TestCase(
        vm_id="vm1",
        snapshot="av",
        mode=TestMode.AV,
        sample_command="C:\\Samples\\sample.exe",
        verify_command="Get-Item C:\\marker.txt",
        credentials=GuestCredentials("user", "pass"),
    )

    result = await TestOrchestrator(provider, tmp_path).run(test_case)

    assert result.classification == Classification.AV_BLOCKED_OR_NO_CHANGE


@pytest.mark.asyncio
async def test_av_not_blocked_when_verification_output_changes(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({"classification": "BASELINE_VALID"}),
        encoding="utf-8",
    )
    result, _ = await run_case(
        tmp_path,
        TestMode.AV,
        "missing",
        "present",
        baseline_result=str(baseline_path),
    )

    assert result.classification == Classification.AV_NOT_BLOCKED


@pytest.mark.asyncio
async def test_av_blocked_or_no_change_when_verification_output_is_same(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({"classification": "BASELINE_VALID"}),
        encoding="utf-8",
    )
    result, _ = await run_case(
        tmp_path,
        TestMode.AV,
        "same",
        "same",
        baseline_result=str(baseline_path),
    )

    assert result.classification == Classification.AV_BLOCKED_OR_NO_CHANGE


def test_normalize_output_trims_empty_lines_and_line_endings():
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="sample.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
    )

    assert normalize_output("  a\r\n\r\n b  ", test_case) == "a\nb"


@pytest.mark.asyncio
async def test_list_snapshots_filters_total_line():
    class FakeVMRun:
        async def list_snapshots(self, vm_id):
            return "Total snapshots: 2\nclean\nav"

    snapshots = await VmrunProvider(vmrun=FakeVMRun()).list_snapshots("vm1")

    assert snapshots == ["clean", "av"]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self._sleep = asyncio.sleep

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await self._sleep(0)


@pytest.mark.asyncio
async def test_wait_guest_ready_reports_tools_not_running(monkeypatch):
    class FakeVMRun:
        async def check_tools_state(self, vm_id):
            return "stopped"

    events = []
    provider = VmrunProvider(vmrun=FakeVMRun())
    clock = FakeClock()
    monkeypatch.setattr("vm_auto_test.providers.vmrun_provider.asyncio.sleep", clock.sleep)
    monkeypatch.setattr("vm_auto_test.providers.vmrun_provider.time.monotonic", clock.monotonic)

    with pytest.raises(TimeoutError, match="tools_not_running"):
        await provider.wait_guest_ready("vm1", GuestCredentials("user", "pass"), 1, progress=events.append)

    assert StepResult("check_vmware_tools", "failed", "tools_not_running") in events


@pytest.mark.asyncio
async def test_wait_guest_ready_reports_guest_process_check_failure(monkeypatch):
    class FakeVMRun:
        async def check_tools_state(self, vm_id):
            return "running"

        async def list_processes(self, vm_id, user, password):
            raise RuntimeError("password=secret raw vmrun stderr")

    events = []
    provider = VmrunProvider(vmrun=FakeVMRun())
    clock = FakeClock()
    monkeypatch.setattr("vm_auto_test.providers.vmrun_provider.asyncio.sleep", clock.sleep)
    monkeypatch.setattr("vm_auto_test.providers.vmrun_provider.time.monotonic", clock.monotonic)

    with pytest.raises(TimeoutError, match="guest_auth_or_process_check_failed"):
        await provider.wait_guest_ready("vm1", GuestCredentials("user", "pass"), 1, progress=events.append)

    # detail now includes the actual vmrun error for debuggability
    assert any(
        "guest_auth_or_process_check_failed" in event.detail
        for event in events
        if event.name == "guest_process_check" and event.status == "failed"
    )


@pytest.mark.asyncio
async def test_wait_guest_ready_logs_provider_progress_callback_failure(monkeypatch, caplog):
    class FakeVMRun:
        async def check_tools_state(self, vm_id):
            return "stopped"

    provider = VmrunProvider(vmrun=FakeVMRun())
    clock = FakeClock()
    monkeypatch.setattr("vm_auto_test.providers.vmrun_provider.asyncio.sleep", clock.sleep)
    monkeypatch.setattr("vm_auto_test.providers.vmrun_provider.time.monotonic", clock.monotonic)

    def broken_progress(step):
        raise RuntimeError("progress failed")

    with pytest.raises(TimeoutError, match="tools_not_running"):
        await provider.wait_guest_ready("vm1", GuestCredentials("user", "pass"), 1, progress=broken_progress)

    assert "Provider progress callback failed: RuntimeError" in caplog.text


def test_clean_cli_value_strips_surrounding_quotes():
    assert clean_cli_value('"E:\\VM-MCP\\windows11\\Windows 11 x64.vmx"') == "E:\\VM-MCP\\windows11\\Windows 11 x64.vmx"


def test_choose_from_list_returns_selected_item(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "2")

    assert choose_from_list(["clean", "av"]) == "av"


def test_choose_from_list_returns_none_for_cancel(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "0")

    assert choose_from_list(["clean", "av"]) is None


def test_choose_from_list_returns_back_for_b(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "b")

    assert choose_from_list(["clean", "av"]) is _BACK


def test_choose_value_returns_back_for_b(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "b")

    assert choose_value("模式", ["baseline", "av"]) is _BACK


def test_choose_from_list_rejects_out_of_range_selection(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "3")

    with pytest.raises(ValueError, match="在 1 到 2"):
        choose_from_list(["clean", "av"])


def test_choose_from_list_rejects_non_numeric_selection(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "clean")

    with pytest.raises(ValueError, match="必须是数字"):
        choose_from_list(["clean", "av"])


def test_choose_value_returns_default_for_empty_selection(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")

    assert choose_value("Shell", ["cmd", "powershell"], default="cmd") == "cmd"


def test_print_progress_outputs_status_line(capsys):
    print_progress(StepResult("wait_guest_ready", "passed", "guest tools"))

    output = capsys.readouterr().out
    assert "✓" in output
    assert "等待系统就绪" in output
    assert "guest tools" in output


def test_print_progress_skips_started_events(capsys):
    print_progress(StepResult("start_vm", "started", "vm"))
    assert capsys.readouterr().out == ""


def test_print_progress_skips_sub_steps(capsys):
    print_progress(StepResult("guest_script", "passed", "completed"))
    assert capsys.readouterr().out == ""


def test_format_cli_error_shows_runtime_error_message():
    assert format_cli_error(RuntimeError("vmrun failed: command failed")) == "vmrun failed: command failed"


def test_format_cli_error_keeps_missing_vmrun_path_guidance():
    message = "vmrun.exe not found: D:\\VM2\\vmrun.exe. Set VMRUN_PATH to your VMware Workstation vmrun.exe path."

    assert format_cli_error(RuntimeError(message)) == message


def test_format_cli_error_redacts_timeout_error_details():
    error = TimeoutError("Guest tools failed with token=secret")

    assert format_cli_error(error) == "TimeoutError: operation failed"


@pytest.mark.asyncio
async def test_progress_callback_failure_is_logged(tmp_path, caplog):
    provider = FakeProvider(before="missing", after="present")
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="C:\\Samples\\sample.exe",
        verify_command="Get-Item C:\\marker.txt",
        credentials=GuestCredentials("user", "pass"),
    )

    def broken_progress(step):
        raise RuntimeError("progress failed")

    await TestOrchestrator(provider, tmp_path, progress=broken_progress).run(test_case)

    assert "Progress callback failed: RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_run_emits_progress_events(tmp_path):
    provider = FakeProvider(before="missing", after="present")
    events = []
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="C:\\Samples\\sample.exe",
        verify_command="Get-Item C:\\marker.txt",
        credentials=GuestCredentials("user", "pass"),
    )

    await TestOrchestrator(provider, tmp_path, progress=events.append).run(test_case)

    assert [(event.name, event.status) for event in events] == [
        ("create_report_dir", "started"),
        ("create_report_dir", "passed"),
        ("revert_snapshot", "started"),
        ("revert_snapshot", "passed"),
        ("start_vm", "started"),
        ("start_vm", "passed"),
        ("wait_guest_ready", "started"),
        ("wait_guest_ready", "passed"),
        ("before_verification", "started"),
        ("before_verification", "passed"),
        ("before_verification_output", "info"),
        ("run_sample", "started"),
        ("run_sample", "passed"),
        ("after_verification", "started"),
        ("after_verification", "passed"),
        ("after_verification_output", "info"),
        ("evaluate", "started"),
        ("evaluate", "passed"),
        ("write_report", "started"),
        ("write_report", "passed"),
    ]
    assert events[0].detail == "sample"
    assert events[8].detail == "verification"
    assert events[11].detail == "sample"
    assert "C:\\Samples\\sample.exe" not in [event.detail for event in events]


@pytest.mark.asyncio
async def test_run_skips_sample_when_file_not_on_guest(tmp_path):
    provider = FakeProvider(before="initial", after="should_not_be_used", file_exists=False)
    events = []
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="C:\\Samples\\sample.exe",
        verify_command="Get-Item C:\\marker.txt",
        credentials=GuestCredentials("user", "pass"),
    )

    result = await TestOrchestrator(provider, tmp_path, progress=events.append).run(test_case)

    assert result.classification == Classification.BASELINE_INVALID
    assert result.changed is False
    assert result.sample.capture_method == "skipped_file_not_found"
    # after_verification must NOT be in provider.commands (only before)
    assert provider.commands.count("Get-Item C:\\marker.txt") == 1
    # run_sample step should be skipped
    run_steps = [e for e in events if e.name == "run_sample"]
    assert any(e.status == "skipped" for e in run_steps)
    assert any("样本文件不存在" in e.detail for e in run_steps)


@pytest.mark.asyncio
async def test_run_skips_sample_when_file_not_on_guest_with_screenshot(tmp_path):
    provider = FakeProvider(before="initial", after="should_not_be_used", file_exists=False)
    events = []
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="C:\\Samples\\sample.exe",
        verify_command="Get-Item C:\\marker.txt",
        credentials=GuestCredentials("user", "pass"),
        capture_screenshot=True,
    )

    result = await TestOrchestrator(provider, tmp_path, progress=events.append).run(test_case)

    assert result.classification == Classification.BASELINE_INVALID
    # Screenshot should still be captured
    assert "capture_screen:" in str(provider.commands)


# -- script generation tests -------------------------------------------------


def test_make_powershell_wrapper_includes_output_and_exitcode_paths():
    script = _make_powershell_wrapper(
        "C:\\Temp\\user.ps1",
        "C:\\Temp\\out.txt",
        "C:\\Temp\\ec.txt",
    )
    assert "C:\\Temp\\user.ps1" in script
    assert "C:\\Temp\\out.txt" in script
    assert "C:\\Temp\\ec.txt" in script
    assert "$LASTEXITCODE" in script
    assert "Out-File" in script


def test_make_powershell_wrapper_escapes_single_quotes_in_paths():
    script = _make_powershell_wrapper(
        "C:\\Temp\\u'ser.ps1",
        "C:\\Temp\\o'ut.txt",
        "C:\\Temp\\e'c.txt",
    )
    assert "u''ser.ps1" in script
    assert "o''ut.txt" in script
    assert "e''c.txt" in script


def test_make_cmd_wrapper_includes_output_and_exitcode_paths():
    script = _make_cmd_wrapper(
        "C:\\Temp\\user.bat",
        "C:\\Temp\\out.txt",
        "C:\\Temp\\ec.txt",
    )
    assert "chcp 65001" in script
    assert 'call "C:\\Temp\\user.bat"' in script
    assert '> "C:\\Temp\\out.txt" 2>&1' in script
    assert 'echo %ERRORLEVEL% > "C:\\Temp\\ec.txt"' in script
    assert "exit /b 0" in script


def test_make_cmd_wrapper_starts_with_echo_off():
    script = _make_cmd_wrapper("user.bat", "out.txt", "ec.txt")
    assert script.lstrip().startswith("@echo off")


def test_make_cmd_wrapper_preserves_non_ascii_script_path():
    script = _make_cmd_wrapper(
        "C:\\临时目录\\脚本.bat",
        "C:\\临时目录\\输出.txt",
        "C:\\临时目录\\退出码.txt",
    )
    assert 'call "C:\\临时目录\\脚本.bat"' in script
    assert '> "C:\\临时目录\\输出.txt" 2>&1' in script
    assert 'echo %ERRORLEVEL% > "C:\\临时目录\\退出码.txt"' in script


# -- run_guest_command integration tests ------------------------------------


@pytest.mark.asyncio
async def test_run_cmd_passes_progress_and_returns_command_result():
    """Regression test: _run_cmd no longer crashes with NameError on progress."""
    class FakeVMRun:
        async def create_temp_file(self, vm_id, user, password):
            return "C:\\Temp\\vmware-temp-12345.txt"

        async def copy_to_guest(self, vm_id, host_path, guest_path, user, password):
            return "ok"

        async def run_program_in_guest(self, vm_id, program, program_args, user, password):
            return "ok"

        async def copy_from_guest(self, vm_id, guest_path, host_path, user, password):
            from pathlib import Path
            Path(host_path).write_text("hello world", encoding="utf-8")
            return "ok"

        async def delete_file(self, vm_id, guest_path, user, password):
            return "ok"

    provider = VmrunProvider(vmrun=FakeVMRun())
    events = []

    result = await provider.run_guest_command(
        "vm1",
        "echo hello",
        Shell.CMD,
        GuestCredentials("user", "pass"),
        30,
        progress=events.append,
    )

    assert isinstance(result, CommandResult)
    assert result.capture_method == "redirected_file"
    assert any(e.name == "guest_script" for e in events)
    assert any(e.status == "passed" for e in events)


@pytest.mark.asyncio
async def test_run_powershell_passes_progress():
    class FakeVMRun:
        def __init__(self):
            self.program = ""
            self.program_args = []

        async def create_temp_file(self, vm_id, user, password):
            return "C:\\Temp\\vmware-temp-12345.txt"

        async def copy_to_guest(self, vm_id, host_path, guest_path, user, password):
            return "ok"

        async def run_program_in_guest(self, vm_id, program, program_args, user, password):
            self.program = program
            self.program_args = program_args
            return "ok"

        async def copy_from_guest(self, vm_id, guest_path, host_path, user, password):
            from pathlib import Path
            if "exitcode" in guest_path:
                Path(host_path).write_text("0", encoding="utf-8")
            else:
                Path(host_path).write_text("hello world", encoding="utf-8")
            return "ok"

        async def delete_file(self, vm_id, guest_path, user, password):
            return "ok"

    fake_vmrun = FakeVMRun()
    provider = VmrunProvider(vmrun=fake_vmrun)
    events = []

    result = await provider.run_guest_command(
        "vm1",
        "Get-Item C:\\marker.txt",
        Shell.POWERSHELL,
        GuestCredentials("user", "pass"),
        30,
        progress=events.append,
    )

    assert isinstance(result, CommandResult)
    assert result.exit_code == 0
    assert result.capture_method == "redirected_file"
    assert fake_vmrun.program == r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    assert fake_vmrun.program_args[:4] == ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    assert fake_vmrun.program_args[-1].endswith(".wrapper.ps1")


@pytest.mark.asyncio
async def test_run_guest_command_captures_exit_code():
    class FakeVMRun:
        async def create_temp_file(self, vm_id, user, password):
            return "C:\\Temp\\vmware-out.txt"

        async def copy_to_guest(self, vm_id, host_path, guest_path, user, password):
            return "ok"

        async def run_program_in_guest(self, vm_id, program, program_args, user, password):
            return "ok"

        async def copy_from_guest(self, vm_id, guest_path, host_path, user, password):
            from pathlib import Path
            p = Path(host_path)
            if "exitcode" in guest_path:
                p.write_text("42", encoding="utf-8")
            else:
                p.write_text("output with non-zero exit", encoding="utf-8")
            return "ok"

        async def delete_file(self, vm_id, guest_path, user, password):
            return "ok"

    provider = VmrunProvider(vmrun=FakeVMRun())

    result = await provider.run_guest_command(
        "vm1",
        "C:\\Samples\\failing.exe",
        Shell.CMD,
        GuestCredentials("user", "pass"),
        30,
    )

    assert result.exit_code == 42
    assert result.stdout == "output with non-zero exit"


@pytest.mark.asyncio
async def test_run_guest_command_reports_missing_exit_code():
    class FakeVMRun:
        async def create_temp_file(self, vm_id, user, password):
            return "C:\\Temp\\vmware-out.txt"

        async def copy_to_guest(self, vm_id, host_path, guest_path, user, password):
            return "ok"

        async def run_program_in_guest(self, vm_id, program, program_args, user, password):
            return "ok"

        async def copy_from_guest(self, vm_id, guest_path, host_path, user, password):
            from pathlib import Path
            if "exitcode" in guest_path:
                raise RuntimeError("missing exitcode")
            Path(host_path).write_text("output", encoding="utf-8")
            return "ok"

        async def delete_file(self, vm_id, guest_path, user, password):
            return "ok"

    provider = VmrunProvider(vmrun=FakeVMRun())

    result = await provider.run_guest_command(
        "vm1",
        "echo hello",
        Shell.CMD,
        GuestCredentials("user", "pass"),
        30,
    )

    assert result.exit_code == 1
    assert "guest exit code unavailable" in result.stderr


@pytest.mark.asyncio
async def test_run_guest_command_cleans_all_guest_files_when_output_copy_fails():
    class FakeVMRun:
        def __init__(self):
            self.deleted = []

        async def create_temp_file(self, vm_id, user, password):
            return "C:\\Temp\\vmware-out.txt"

        async def copy_to_guest(self, vm_id, host_path, guest_path, user, password):
            return "ok"

        async def run_program_in_guest(self, vm_id, program, program_args, user, password):
            return "ok"

        async def copy_from_guest(self, vm_id, guest_path, host_path, user, password):
            raise RuntimeError("copy failed")

        async def delete_file(self, vm_id, guest_path, user, password):
            self.deleted.append(guest_path)
            return "ok"

    vmrun = FakeVMRun()
    provider = VmrunProvider(vmrun=vmrun)

    with pytest.raises(RuntimeError, match="copy failed"):
        await provider.run_guest_command(
            "vm1",
            "echo hello",
            Shell.CMD,
            GuestCredentials("user", "pass"),
            30,
        )

    assert vmrun.deleted == [
        "C:\\Temp\\vmware-out.txt",
        "C:\\Temp\\vmware-out.txt.exitcode",
        "C:\\Temp\\vmware-out.txt.user.bat",
        "C:\\Temp\\vmware-out.txt.wrapper.bat",
    ]


# -- av_detection tests ------------------------------------------------------


def test_build_detection_command_checks_all_signatures():
    cmd = build_detection_command()

    assert "QQPCTray\\.exe" in cmd
    assert "360Tray\\.exe" in cmd
    assert "HipsDaemon\\.exe" in cmd
    assert "腾讯电脑管家" in cmd
    assert "360安全卫士" in cmd
    assert "火绒安全软件" in cmd
    assert "NONE" in cmd


def test_parse_detection_result_returns_none_for_empty():
    assert parse_detection_result("") is None
    assert parse_detection_result("   ") is None
    assert parse_detection_result("NONE") is None


def test_parse_detection_result_returns_name():
    assert parse_detection_result("腾讯电脑管家") == "腾讯电脑管家"
    assert parse_detection_result("360安全卫士,火绒安全软件") == "360安全卫士,火绒安全软件"


@pytest.mark.asyncio
async def test_detect_av_runs_in_av_mode(tmp_path):
    provider = FakeProvider(outputs=["360安全卫士", "missing", "sample output", "present"])
    events = []
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({"classification": "BASELINE_VALID"}), encoding="utf-8",
    )
    test_case = TestCase(
        vm_id="vm1",
        snapshot="av",
        mode=TestMode.AV,
        sample_command="C:\\Samples\\sample.exe",
        verify_command="Get-Item C:\\marker.txt",
        credentials=GuestCredentials("user", "pass"),
        baseline_result=str(baseline_path),
    )

    await TestOrchestrator(provider, tmp_path, progress=events.append).run(test_case)

    names = [e.name for e in events]
    assert "detect_av" in names
    detect_events = [e for e in events if e.name == "detect_av"]
    assert any(e.status == "passed" for e in detect_events)
    assert any("360安全卫士" in e.detail for e in detect_events)


@pytest.mark.asyncio
async def test_detect_av_not_run_in_baseline_mode(tmp_path):
    provider = FakeProvider(before="missing", after="present")
    events = []

    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="C:\\Samples\\sample.exe",
        verify_command="Get-Item C:\\marker.txt",
        credentials=GuestCredentials("user", "pass"),
    )

    await TestOrchestrator(provider, tmp_path, progress=events.append).run(test_case)

    assert not any(e.name == "detect_av" for e in events)


@pytest.mark.asyncio
async def test_detect_av_failure_is_non_fatal(tmp_path):
    class FailingAvProvider(FakeProvider):
        async def run_guest_command(self, vm_id, command, shell, credentials, timeout_seconds, progress=None):
            self.commands.append(command)
            if "tasklist" in command:
                raise RuntimeError("detection failed")
            return CommandResult(command=command, stdout=self._outputs.pop(0))

    provider = FailingAvProvider(outputs=["NONE", "missing", "sample output", "present"])
    events = []
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({"classification": "BASELINE_VALID"}), encoding="utf-8",
    )
    test_case = TestCase(
        vm_id="vm1",
        snapshot="av",
        mode=TestMode.AV,
        sample_command="C:\\Samples\\sample.exe",
        verify_command="Get-Item C:\\marker.txt",
        credentials=GuestCredentials("user", "pass"),
        baseline_result=str(baseline_path),
    )

    result = await TestOrchestrator(provider, tmp_path, progress=events.append).run(test_case)

    detect_events = [e for e in events if e.name == "detect_av"]
    assert any(e.status == "failed" for e in detect_events)
    # Test still completed
    assert result.classification == Classification.AV_NOT_BLOCKED

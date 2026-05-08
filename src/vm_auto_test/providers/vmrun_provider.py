from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from vm_auto_test.models import CommandResult, GuestCredentials, Shell, StepResult
from vm_auto_test.providers.base import VmwareProvider
from vmware_mcp.vmrun import VMRun

_LOGGER = logging.getLogger(__name__)


def _powershell_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _make_powershell_wrapper(user_script_path: str, output_path: str, exitcode_path: str) -> str:
    return "\n".join(
        [
            "$ErrorActionPreference = 'Continue'",
            "$userScript = " + _powershell_single_quoted(user_script_path),
            "$outputFile = " + _powershell_single_quoted(output_path),
            "$exitCodeFile = " + _powershell_single_quoted(exitcode_path),
            "& $userScript *>&1 | Out-File -FilePath $outputFile -Encoding UTF8",
            "if ($null -ne $LASTEXITCODE) { $exitCode = $LASTEXITCODE } elseif ($?) { $exitCode = 0 } else { $exitCode = 1 }",
            "[System.IO.File]::WriteAllText($exitCodeFile, [string]$exitCode, [System.Text.Encoding]::ASCII)",
            "exit 0",
            "",
        ]
    )


def _make_cmd_wrapper(user_script_path: str, output_path: str, exitcode_path: str) -> str:
    return "".join(
        [
            "@echo off\r\n",
            "chcp 65001 > nul\r\n",
            f'call "{user_script_path}" > "{output_path}" 2>&1\r\n',
            f'echo %ERRORLEVEL% > "{exitcode_path}"\r\n',
            "exit /b 0\r\n",
        ]
    )


def _read_guest_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk", "shift_jis"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_exit_code(path: Path) -> int:
    raw = path.read_bytes()
    value = raw.decode("ascii", errors="replace").strip()
    if not value:
        raise ValueError("empty exit code")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"invalid exit code: {value[:80]!r}") from exc


class VmrunProvider(VmwareProvider):
    def __init__(self, vmrun: VMRun | None = None) -> None:
        self._vmrun = vmrun or VMRun()

    async def list_running_vms(self) -> list[str]:
        output = await self._vmrun.list_running()
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if lines and lines[0].lower().startswith("total running vms"):
            return lines[1:]
        return lines

    _SNAPSHOT_TIMEOUT = 5  # seconds — vmrun hangs on encrypted VMs

    async def list_snapshots(self, vm_id: str) -> list[str]:
        try:
            output = await asyncio.wait_for(self._vmrun.list_snapshots(vm_id), timeout=self._SNAPSHOT_TIMEOUT)
        except asyncio.TimeoutError:
            raise RuntimeError(
                "无法列出快照：查询超时（5s）。\n"
                "如果 VM 开启了访问控制加密，请在 VMware Workstation 中关闭该 VM → "
                "虚拟机设置 → 选项 → 访问控制 → 移除加密。"
            )
        except RuntimeError as exc:
            message = str(exc)
            if any(kw in message.lower() for kw in ("encrypt", "access control")):
                raise RuntimeError(
                    "无法列出快照：VM 开启了访问控制加密。\n"
                    "请在 VMware Workstation 中关闭该 VM → 虚拟机设置 → 选项 → 访问控制 → 移除加密。"
                ) from exc
            raise
        return [
            line.strip()
            for line in output.splitlines()
            if line.strip() and not line.strip().lower().startswith("total snapshots")
        ]

    async def revert_snapshot(self, vm_id: str, snapshot: str) -> None:
        await self._vmrun.revert_to_snapshot(vm_id, snapshot)

    async def start_vm(self, vm_id: str) -> None:
        running_vms = await self.list_running_vms()
        if vm_id in running_vms:
            return
        await self._vmrun.start(vm_id, gui=True)

    async def reset_vm(self, vm_id: str) -> None:
        await self._vmrun.reset(vm_id, hard=False)

    async def wait_guest_ready(
        self,
        vm_id: str,
        credentials: GuestCredentials,
        timeout_seconds: int,
        progress: Callable[[StepResult], None] | None = None,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_reason = "guest_ready_unknown_timeout"
        attempt = 1
        while time.monotonic() < deadline:
            try:
                self._emit_progress(progress, "check_vmware_tools", "started", f"attempt {attempt}")
                state = await self._vmrun.check_tools_state(vm_id)
                if "running" not in state.lower():
                    last_reason = "tools_not_running"
                    self._emit_progress(progress, "check_vmware_tools", "failed", last_reason)
                else:
                    self._emit_progress(progress, "check_vmware_tools", "passed", "running")
                    self._emit_progress(progress, "guest_process_check", "started", f"attempt {attempt}")
                    try:
                        await self._vmrun.list_processes(
                            vm_id,
                            user=credentials.user,
                            password=credentials.password,
                        )
                    except RuntimeError:
                        last_reason = "guest_auth_or_process_check_failed"
                        self._emit_progress(progress, "guest_process_check", "failed", last_reason)
                    else:
                        self._emit_progress(progress, "guest_process_check", "passed", "authenticated")
                        return
            except RuntimeError:
                last_reason = "tools_state_check_failed"
                self._emit_progress(progress, "check_vmware_tools", "failed", last_reason)
            attempt += 1
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                break
            await asyncio.sleep(min(5, remaining_seconds))
        raise TimeoutError(f"Guest readiness timed out: {last_reason}")

    def _emit_progress(
        self,
        progress: Callable[[StepResult], None] | None,
        name: str,
        status: str,
        detail: str,
    ) -> None:
        if progress:
            try:
                progress(StepResult(name, status, detail))
            except Exception as exc:
                _LOGGER.warning("Provider progress callback failed: %s", type(exc).__name__)

    async def run_guest_command(
        self,
        vm_id: str,
        command: str,
        shell: Shell,
        credentials: GuestCredentials,
        timeout_seconds: int,
        progress: Callable[[StepResult], None] | None = None,
    ) -> CommandResult:
        if shell == Shell.POWERSHELL:
            return await self._run_powershell(vm_id, command, credentials, timeout_seconds, progress)
        return await self._run_cmd(vm_id, command, credentials, timeout_seconds, progress)

    async def _run_powershell(
        self,
        vm_id: str,
        command: str,
        credentials: GuestCredentials,
        timeout_seconds: int,
        progress: Callable[[StepResult], None] | None,
    ) -> CommandResult:
        return await self._run_with_file_capture(
            vm_id=vm_id,
            command=command,
            script_ext=".ps1",
            wrapper_maker=_make_powershell_wrapper,
            interpreter=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            interpreter_args=["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"],
            credentials=credentials,
            timeout_seconds=timeout_seconds,
            progress=progress,
        )

    async def _run_cmd(
        self,
        vm_id: str,
        command: str,
        credentials: GuestCredentials,
        timeout_seconds: int,
        progress: Callable[[StepResult], None] | None,
    ) -> CommandResult:
        return await self._run_with_file_capture(
            vm_id=vm_id,
            command=command,
            script_ext=".bat",
            wrapper_maker=_make_cmd_wrapper,
            interpreter="cmd.exe",
            interpreter_args=["/c"],
            credentials=credentials,
            timeout_seconds=timeout_seconds,
            progress=progress,
        )

    async def _run_with_file_capture(
        self,
        vm_id: str,
        command: str,
        script_ext: str,
        wrapper_maker: Callable[[str, str, str], str],
        interpreter: str,
        interpreter_args: list[str],
        credentials: GuestCredentials,
        timeout_seconds: int,
        progress: Callable[[StepResult], None] | None,
    ) -> CommandResult:
        with tempfile.TemporaryDirectory(prefix="vm-auto-test-") as temp_dir:
            host_dir = Path(temp_dir)
            guest_output_path = await self._vmrun.create_temp_file(
                vm_id,
                user=credentials.user,
                password=credentials.password,
            )
            guest_exitcode_path = guest_output_path + ".exitcode"
            guest_user_script_path = guest_output_path + ".user" + script_ext
            guest_wrapper_path = guest_output_path + ".wrapper" + script_ext

            host_user_script = host_dir / ("user" + script_ext)
            host_wrapper = host_dir / ("wrapper" + script_ext)
            script_encoding = "utf-8-sig" if script_ext == ".ps1" else "utf-8"
            wrapper_encoding = "utf-8-sig" if script_ext == ".ps1" else "utf-8"
            if script_ext == ".bat":
                host_user_script.write_text("@echo off\r\n" + command + "\r\n", encoding=script_encoding)
            else:
                host_user_script.write_text(command + "\n", encoding=script_encoding)
            host_wrapper.write_text(
                wrapper_maker(guest_user_script_path, guest_output_path, guest_exitcode_path),
                encoding=wrapper_encoding,
            )

            self._emit_progress(progress, "guest_script", "started", "copying to guest")
            await self._copy_to_guest(vm_id, str(host_user_script), guest_user_script_path, credentials)
            await self._copy_to_guest(vm_id, str(host_wrapper), guest_wrapper_path, credentials)

            host_output = host_dir / "guest-output.txt"
            host_exitcode = host_dir / "guest-exitcode.txt"

            try:
                self._emit_progress(progress, "guest_script", "started", "executing")
                await asyncio.wait_for(
                    self._vmrun.run_program_in_guest(
                        vm_id,
                        interpreter,
                        program_args=[*interpreter_args, guest_wrapper_path],
                        user=credentials.user,
                        password=credentials.password,
                    ),
                    timeout=timeout_seconds,
                )

                self._emit_progress(progress, "guest_script", "started", "retrieving output")
                await self._copy_from_guest(vm_id, guest_output_path, host_output, credentials)
                exit_code, stderr = await self._copy_exit_code(
                    vm_id,
                    guest_exitcode_path,
                    host_exitcode,
                    credentials,
                )

                stdout = _read_guest_text(host_output)
                self._emit_progress(progress, "guest_script", "passed", "completed")
                return CommandResult(
                    command=command,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    capture_method="redirected_file",
                )
            finally:
                await self._cleanup_guest_files(
                    vm_id,
                    [
                        guest_output_path,
                        guest_exitcode_path,
                        guest_user_script_path,
                        guest_wrapper_path,
                    ],
                    credentials,
                )

    async def _copy_to_guest(
        self,
        vm_id: str,
        host_path: str,
        guest_path: str,
        credentials: GuestCredentials,
    ) -> None:
        await self._vmrun.copy_to_guest(
            vm_id,
            host_path,
            guest_path,
            user=credentials.user,
            password=credentials.password,
        )

    async def _copy_from_guest(
        self,
        vm_id: str,
        guest_path: str,
        host_path: Path,
        credentials: GuestCredentials,
    ) -> None:
        await self._vmrun.copy_from_guest(
            vm_id,
            guest_path,
            str(host_path),
            user=credentials.user,
            password=credentials.password,
        )

    async def _copy_exit_code(
        self,
        vm_id: str,
        guest_path: str,
        host_path: Path,
        credentials: GuestCredentials,
    ) -> tuple[int, str]:
        try:
            await self._copy_from_guest(vm_id, guest_path, host_path, credentials)
            return _read_exit_code(host_path), ""
        except (RuntimeError, ValueError) as exc:
            detail = str(exc)
            reason = type(exc).__name__ if not detail else f"{type(exc).__name__}: {detail}"
            return 1, f"guest exit code unavailable: {reason}"

    async def _cleanup_guest_files(
        self,
        vm_id: str,
        guest_paths: list[str],
        credentials: GuestCredentials,
    ) -> None:
        for guest_path in guest_paths:
            try:
                await self._vmrun.delete_file(
                    vm_id,
                    guest_path,
                    user=credentials.user,
                    password=credentials.password,
                )
            except RuntimeError as exc:
                _LOGGER.warning(
                    "Guest temp cleanup failed for %s: %s",
                    Path(guest_path).suffix,
                    type(exc).__name__,
                )

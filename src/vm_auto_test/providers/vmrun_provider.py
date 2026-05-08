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


class VmrunProvider(VmwareProvider):
    def __init__(self, vmrun: VMRun | None = None) -> None:
        self._vmrun = vmrun or VMRun()

    async def list_running_vms(self) -> list[str]:
        output = await self._vmrun.list_running()
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if lines and lines[0].lower().startswith("total running vms"):
            return lines[1:]
        return lines

    async def list_snapshots(self, vm_id: str) -> list[str]:
        output = await self._vmrun.list_snapshots(vm_id)
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
        quoted_command = command.replace("'", "''")
        script = f"$ErrorActionPreference = 'Continue'; Invoke-Expression '{quoted_command}'"
        return await self._run_script_with_file_capture(
            vm_id=vm_id,
            command=command,
            interpreter="powershell.exe",
            script=script,
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
    ) -> CommandResult:
        return await self._run_script_with_file_capture(
            vm_id=vm_id,
            command=command,
            interpreter="cmd.exe",
            script=f"/c {command}",
            credentials=credentials,
            timeout_seconds=timeout_seconds,
            progress=progress,
        )

    async def _run_script_with_file_capture(
        self,
        vm_id: str,
        command: str,
        interpreter: str,
        script: str,
        credentials: GuestCredentials,
        timeout_seconds: int,
    ) -> CommandResult:
        with tempfile.TemporaryDirectory(prefix="vm-auto-test-") as temp_dir:
            host_output = Path(temp_dir) / "guest-output.txt"
            guest_output = await self._vmrun.create_temp_file(
                vm_id,
                user=credentials.user,
                password=credentials.password,
            )
            wrapped_script = self._redirect_script(interpreter, script, guest_output)
            try:
                await asyncio.wait_for(
                    self._vmrun.run_script(
                        vm_id,
                        interpreter,
                        wrapped_script,
                        user=credentials.user,
                        password=credentials.password,
                    ),
                    timeout=timeout_seconds,
                )
                await self._vmrun.copy_from_guest(
                    vm_id,
                    guest_output,
                    str(host_output),
                    user=credentials.user,
                    password=credentials.password,
                )
                stdout = host_output.read_text(encoding="utf-8", errors="replace")
                return CommandResult(
                    command=command,
                    stdout=stdout,
                    capture_method="redirected_file",
                )
            finally:
                try:
                    await self._vmrun.delete_file(
                        vm_id,
                        guest_output,
                        user=credentials.user,
                        password=credentials.password,
                    )
                except RuntimeError as exc:
                    _LOGGER.warning("Guest temp output cleanup failed: %s", type(exc).__name__)

    def _redirect_script(self, interpreter: str, script: str, guest_output: str) -> str:
        if Path(interpreter).name.lower() == "powershell.exe":
            escaped_output = guest_output.replace("'", "''")
            escaped_script = script.replace("'", "''")
            return (
                f"& {{ {escaped_script} }} *>&1 | "
                f"Out-File -FilePath '{escaped_output}' -Encoding UTF8"
            )
        return f'/c {script.removeprefix("/c ")} > "{guest_output}" 2>&1'

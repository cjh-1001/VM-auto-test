from __future__ import annotations

import pytest

from vmware_mcp.vmrun import VMRun, _strip_surrounding_quotes


def test_strip_surrounding_quotes_from_vmrun_path():
    assert _strip_surrounding_quotes('"D:\\VM2\\vmrun.exe"') == "D:\\VM2\\vmrun.exe"


@pytest.mark.asyncio
async def test_vmrun_reports_missing_executable_path():
    vmrun = VMRun(vmrun_path="Z:\\missing\\vmrun.exe")

    with pytest.raises(RuntimeError, match="vmrun.exe not found"):
        await vmrun.list_running()


@pytest.mark.asyncio
async def test_run_program_in_guest_passes_list_args_without_splitting():
    class FakeVMRun(VMRun):
        def __init__(self):
            super().__init__(vmrun_path="vmrun")
            self.calls = []

        async def _run(self, command, *args, guest_user="", guest_pass=""):
            self.calls.append((command, args, guest_user, guest_pass))
            return "ok"

    vmrun = FakeVMRun()

    await vmrun.run_program_in_guest(
        "C:\\VMs\\Windows 11.vmx",
        "powershell.exe",
        program_args=["-File", "C:\\Temp\\script with spaces.ps1"],
        user="Administrator",
        password="secret",
    )

    assert vmrun.calls == [
        (
            "runProgramInGuest",
            (
                "C:\\VMs\\Windows 11.vmx",
                "powershell.exe",
                "-File",
                "C:\\Temp\\script with spaces.ps1",
            ),
            "Administrator",
            "secret",
        )
    ]

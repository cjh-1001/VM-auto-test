from __future__ import annotations

from collections.abc import Callable

from vm_auto_test.models import CommandResult, GuestCredentials, StepResult, TestCase, TestMode
from vm_auto_test.orchestrator import TestOrchestrator
from vm_auto_test.providers.base import VmwareProvider
from vm_auto_test.models import Shell


class FakeProvider(VmwareProvider):
    def __init__(self, before: str = "", after: str = "", outputs: list[str] | None = None) -> None:
        self.commands: list[str] = []
        self._outputs = list(outputs) if outputs is not None else [before, "sample output", after]

    async def list_running_vms(self) -> list[str]:
        return ["vm1"]

    async def list_snapshots(self, vm_id: str) -> list[str]:
        return ["clean", "av"]

    async def revert_snapshot(self, vm_id: str, snapshot: str) -> None:
        self.commands.append(f"revert:{snapshot}")

    async def start_vm(self, vm_id: str) -> None:
        self.commands.append("start")

    async def reset_vm(self, vm_id: str) -> None:
        self.commands.append("reset")

    async def verify_guest_credentials(self, vm_id: str, credentials: GuestCredentials) -> str:
        return "ok"

    async def wait_guest_ready(
        self,
        vm_id: str,
        credentials: GuestCredentials,
        timeout_seconds: int,
        progress: Callable[[StepResult], None] | None = None,
    ) -> None:
        self.commands.append("wait")

    async def run_guest_command(
        self,
        vm_id: str,
        command: str,
        shell: Shell,
        credentials: GuestCredentials,
        timeout_seconds: int,
        progress: Callable[[StepResult], None] | None = None,
    ) -> CommandResult:
        self.commands.append(command)
        return CommandResult(command=command, stdout=self._outputs.pop(0))


async def run_case(tmp_path, mode: TestMode, before: str, after: str, baseline_result: str | None = None):
    provider = FakeProvider(before=before, after=after)
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=mode,
        sample_command="C:\\Samples\\sample.exe",
        verify_command="Get-Item C:\\marker.txt",
        credentials=GuestCredentials("user", "pass"),
        baseline_result=baseline_result,
    )
    result = await TestOrchestrator(provider, tmp_path).run(test_case)
    return result, provider

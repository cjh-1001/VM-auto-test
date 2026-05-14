from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from vm_auto_test.models import CommandResult, GuestCredentials, Shell, StepResult


class VmToolsNotReadyError(Exception):
    """VMware Tools 未就绪，请检查 VM 中是否已安装 VMware Tools。"""


class VmwareProvider(ABC):
    @abstractmethod
    async def list_running_vms(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    async def list_snapshots(self, vm_id: str) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    async def revert_snapshot(self, vm_id: str, snapshot: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def start_vm(self, vm_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def reset_vm(self, vm_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def verify_guest_credentials(self, vm_id: str, credentials: GuestCredentials) -> str:
        """Test guest credentials. Returns "ok" on success, raises on failure."""
        raise NotImplementedError

    @abstractmethod
    async def wait_guest_ready(
        self,
        vm_id: str,
        credentials: GuestCredentials,
        timeout_seconds: int,
        progress: Callable[[StepResult], None] | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def run_guest_command(
        self,
        vm_id: str,
        command: str,
        shell: Shell,
        credentials: GuestCredentials,
        timeout_seconds: int,
        progress: Callable[[StepResult], None] | None = None,
    ) -> CommandResult:
        raise NotImplementedError

    @abstractmethod
    async def file_exists_on_guest(self, vm_id: str, guest_path: str, credentials: GuestCredentials) -> bool:
        """Return True if the file exists on the guest VM."""
        raise NotImplementedError

    @abstractmethod
    async def capture_screen(self, vm_id: str, output_path: str, credentials: GuestCredentials) -> str:
        raise NotImplementedError

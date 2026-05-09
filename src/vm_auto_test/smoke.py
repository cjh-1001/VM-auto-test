from __future__ import annotations

import asyncio
import os
import sys

from vm_auto_test.env import resolve_guest_credentials
from vm_auto_test.models import Shell
from vm_auto_test.providers.vmrun_provider import VmrunProvider

_REQUIRED_ENV = ("VM_AUTO_TEST_SMOKE_VM_ID",)


def missing_smoke_env() -> list[str]:
    return [key for key in _REQUIRED_ENV if not os.getenv(key)]


async def main_async() -> int:
    missing = missing_smoke_env()
    if missing:
        print("Missing smoke test environment: " + ", ".join(missing), file=sys.stderr)
        return 2

    vm_id = os.environ["VM_AUTO_TEST_SMOKE_VM_ID"]
    credentials = resolve_guest_credentials(vm_id)
    if credentials is None:
        print(f"No credentials found for {vm_id}. Run 'vm-auto-test' → [3] 列出 VM → 选择 VM → 配置凭证.", file=sys.stderr)
        return 2

    provider = VmrunProvider()

    snapshots = await provider.list_snapshots(vm_id)
    print(f"snapshots={len(snapshots)}")

    snapshot = os.getenv("VM_AUTO_TEST_SMOKE_SNAPSHOT")
    if snapshot:
        await provider.revert_snapshot(vm_id, snapshot)
        print(f"reverted={snapshot}")

    await provider.start_vm(vm_id)
    await provider.wait_guest_ready(vm_id, credentials, 180)
    result = await provider.run_guest_command(
        vm_id,
        "hostname",
        Shell.CMD,
        credentials,
        60,
    )
    print(f"hostname={result.combined_output.strip()}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()

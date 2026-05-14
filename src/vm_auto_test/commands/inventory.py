from __future__ import annotations

from pathlib import Path

from vm_auto_test.orchestrator import TestOrchestrator
from vm_auto_test.providers.base import VmwareProvider


async def list_running_vms(provider: VmwareProvider) -> int:
    print("  正在查询运行中的 VM ...", flush=True)
    running_vms = await provider.list_running_vms()
    if not running_vms:
        print("  没有运行中的 VM")
        return 0
    for index, vm_id in enumerate(running_vms, start=1):
        print(f"[{index}] {vm_id}")
    return 0


async def list_snapshots(provider: VmwareProvider, vm_path: str) -> int:
    print(f"  正在查询快照: {vm_path}", flush=True)
    orchestrator = TestOrchestrator(provider, Path("reports"))
    try:
        snapshots = await orchestrator.list_snapshots(vm_path)
    except RuntimeError as exc:
        print(f"  {exc}")
        return 0
    if not snapshots:
        print("  没有找到快照，请先在 VMware Workstation 中为该 VM 创建快照")
        return 0
    for index, snapshot in enumerate(snapshots, start=1):
        print(f"  [{index}] {snapshot}")
    return 0


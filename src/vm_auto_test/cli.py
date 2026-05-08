from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

from vm_auto_test.config import (
    DEFAULT_PASSWORD_ENV,
    CommandConfig,
    GuestConfig,
    NormalizeConfig,
    TestConfig,
    TimeoutConfig,
    VerificationConfig,
    load_config,
    to_test_case,
    write_config,
)
from vm_auto_test.env import load_optional_env_file
from vm_auto_test.models import GuestCredentials, Shell, StepResult, TestCase, TestMode
from vm_auto_test.orchestrator import TestOrchestrator
from vm_auto_test.providers.base import VmwareProvider
from vm_auto_test.providers.factory import create_provider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run VMware guest sample validation tests.")
    parser.add_argument("--env-file", help="Load environment variables from a .env file before running")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("vms", help="List running VMs")

    snapshot_parser = subparsers.add_parser("snapshots", help="List snapshots for a VM")
    snapshot_parser.add_argument("--vm", required=True, help="VM ID or .vmx path")

    run_parser = subparsers.add_parser("run", help="Run baseline or AV validation")
    run_parser.add_argument("--vm", required=True, help="VM ID or .vmx path")
    run_parser.add_argument("--mode", choices=[mode.value for mode in TestMode], required=True)
    run_parser.add_argument("--snapshot", help="Snapshot name. If omitted, choose interactively.")
    run_parser.add_argument("--sample-command", required=True, help="Guest command that runs the sample")
    run_parser.add_argument("--sample-shell", choices=[shell.value for shell in Shell], default=Shell.CMD.value)
    run_parser.add_argument("--verify-command", required=True, help="Guest command that verifies effect")
    run_parser.add_argument("--verify-shell", choices=[shell.value for shell in Shell], default=Shell.POWERSHELL.value)
    run_parser.add_argument("--guest-user")
    run_parser.add_argument(
        "--guest-password",
        help="Prefer VMWARE_GUEST_PASSWORD or prompt input",
    )
    run_parser.add_argument("--baseline-result", help="Required for AV mode")
    run_parser.add_argument("--reports-dir", default="reports")

    init_parser = subparsers.add_parser("init-config", help="Create a test config interactively")
    init_parser.add_argument("--output", default="configs/sample.yaml", help="Config file to write")
    init_parser.add_argument("--vm", help="VM ID or .vmx path")
    init_parser.add_argument("--mode", choices=[mode.value for mode in TestMode])

    run_config_parser = subparsers.add_parser("run-config", help="Run validation from a YAML config")
    run_config_parser.add_argument("config", help="YAML config file")
    run_config_parser.add_argument("--guest-password")
    return parser


async def main_async() -> None:
    parser = build_parser()
    args = parser.parse_args()
    load_optional_env_file(Path(args.env_file) if args.env_file else None)
    provider = create_provider("vmrun")

    if args.command == "vms":
        running_vms = await provider.list_running_vms()
        if not running_vms:
            print("No running VMs found.")
            return
        for index, vm_id in enumerate(running_vms, start=1):
            print(f"[{index}] {vm_id}")
        return

    if args.command == "snapshots":
        orchestrator = TestOrchestrator(provider, Path("reports"))
        snapshots = await orchestrator.list_snapshots(clean_cli_value(args.vm))
        if not snapshots:
            print("No snapshots found.")
            return
        for index, snapshot in enumerate(snapshots, start=1):
            print(f"[{index}] {snapshot}")
        return

    if args.command == "init-config":
        config = await build_config_interactively(provider, args.vm, args.mode)
        output = Path(args.output)
        write_config(output, config)
        print(f"config={output}")
        return

    if args.command == "run-config":
        config = load_config(Path(args.config))
        config_provider = create_provider(config.provider.type)
        test_case = to_test_case(config, password=args.guest_password or os.getenv("VMWARE_GUEST_PASSWORD"))
        config_orchestrator = TestOrchestrator(config_provider, Path(config.reports_dir), progress=print_progress)
        if config.samples:
            batch_result = await config_orchestrator.run_batch(test_case)
            print(f"classification={batch_result.classification.value}")
            print(f"total={len(batch_result.samples)}")
            for sample in batch_result.samples:
                print(f"sample={sample.sample_spec.id} classification={sample.classification.value} changed={sample.changed}")
            print(f"report_dir={batch_result.report_dir}")
            return
        result = await config_orchestrator.run(test_case)
        print(f"classification={result.classification.value}")
        print(f"changed={result.changed}")
        print(f"report_dir={result.report_dir}")
        return

    orchestrator = TestOrchestrator(provider, Path(args.reports_dir), progress=print_progress)
    vm_id = clean_cli_value(args.vm)
    snapshot = clean_cli_value(args.snapshot) if args.snapshot else None
    if not snapshot:
        snapshots = await orchestrator.list_snapshots(vm_id)
        if not snapshots:
            raise RuntimeError("No snapshots found for VM")
        snapshot = choose_snapshot(snapshots)

    guest_user = args.guest_user or os.getenv("VMWARE_GUEST_USER", "") or input("Guest user: ")
    guest_password = args.guest_password or os.getenv("VMWARE_GUEST_PASSWORD")
    if guest_password is None:
        guest_password = getpass.getpass("Guest password: ")

    test_case = TestCase(
        vm_id=vm_id,
        snapshot=snapshot,
        mode=TestMode(args.mode),
        sample_command=args.sample_command,
        sample_shell=Shell(args.sample_shell),
        verify_command=args.verify_command,
        verify_shell=Shell(args.verify_shell),
        credentials=GuestCredentials(guest_user, guest_password),
        baseline_result=args.baseline_result,
    )
    result = await orchestrator.run(test_case)
    print(f"classification={result.classification.value}")
    print(f"changed={result.changed}")
    print(f"report_dir={result.report_dir}")


def print_progress(step: StepResult) -> None:
    label = step.name.replace("_", " ")
    detail = f" - {step.detail}" if step.detail else ""
    print(f"[{step.status}] {label}{detail}", flush=True)


async def build_config_interactively(
    provider: VmwareProvider,
    vm_id: str | None,
    mode_value: str | None,
) -> TestConfig:
    selected_vm_id = clean_cli_value(
        vm_id
        or input("VMX path (example: E:\\VM-MCP\\windows11\\Windows 11 x64.vmx): ")
    )
    if not selected_vm_id:
        raise ValueError("VM ID is required")

    snapshots = await provider.list_snapshots(selected_vm_id)
    if not snapshots:
        raise RuntimeError("No snapshots found for VM")
    snapshot = choose_snapshot(snapshots)

    mode = TestMode(mode_value or choose_value("Mode", [mode.value for mode in TestMode]))
    baseline_result = None
    if mode == TestMode.AV:
        print("Baseline result path 填已经通过的 baseline result.json 路径。")
        print("例如: reports\\20260507-120000-000000-sample\\result.json")
        baseline_result = clean_cli_value(input("Baseline result path: "))
        if not baseline_result:
            raise ValueError("AV mode requires baseline result path")

    print("Sample command 是要在 guest 里执行的样本命令。")
    print("例如: C:\\Samples\\sample.exe 或 C:\\Samples\\run.bat")
    sample_command = input("Sample command: ").strip()
    if not sample_command:
        raise ValueError("Sample command is required")
    sample_shell = Shell(choose_value("Sample shell", [shell.value for shell in Shell], default=Shell.CMD.value))

    print("Verification command 是样本运行前后都要执行的验证命令，用来观察是否发生变化。")
    print("例如: type C:\\marker.txt、dir C:\\Users、net user")
    verify_command = input("Verification command: ").strip()
    if not verify_command:
        raise ValueError("Verification command is required")
    verify_shell = Shell(choose_value("Verification shell", [shell.value for shell in Shell], default=Shell.POWERSHELL.value))

    guest_user = input("Guest user inside VM (example: Administrator): ").strip()
    if not guest_user:
        raise ValueError("Guest user is required")
    print("Guest password env 只填环境变量名，不要填真实密码。")
    print(f"如果 .env 里是 VMWARE_GUEST_PASSWORD=<your-password>，这里直接回车或填 {DEFAULT_PASSWORD_ENV}。")
    password_env = input(f"Guest password env name [{DEFAULT_PASSWORD_ENV}]: ").strip() or DEFAULT_PASSWORD_ENV

    return TestConfig(
        vm_id=selected_vm_id,
        snapshot=snapshot,
        mode=mode,
        baseline_result=baseline_result,
        guest=GuestConfig(user=guest_user, password_env=password_env),
        sample=CommandConfig(command=sample_command, shell=sample_shell),
        verification=VerificationConfig(command=verify_command, shell=verify_shell),
        reports_dir=input("Reports dir [reports]: ").strip() or "reports",
        timeouts=TimeoutConfig(),
        normalize=NormalizeConfig(),
    )


def clean_cli_value(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        return cleaned[1:-1]
    return cleaned


def choose_snapshot(snapshots: list[str]) -> str:
    for index, item in enumerate(snapshots, start=1):
        print(f"[{index}] {item}")
    raw_selection = input("Select snapshot: ").strip()
    try:
        selected_index = int(raw_selection)
    except ValueError as exc:
        raise ValueError("Snapshot selection must be a number") from exc
    if selected_index < 1 or selected_index > len(snapshots):
        raise ValueError(f"Snapshot selection must be between 1 and {len(snapshots)}")
    return snapshots[selected_index - 1]


def choose_value(label: str, values: list[str], default: str | None = None) -> str:
    for index, value in enumerate(values, start=1):
        default_marker = " default" if value == default else ""
        print(f"[{index}] {value}{default_marker}")
    raw_selection = input(f"{label}: ").strip()
    if not raw_selection and default:
        return default
    try:
        selected_index = int(raw_selection)
    except ValueError as exc:
        raise ValueError(f"{label} selection must be a number") from exc
    if selected_index < 1 or selected_index > len(values):
        raise ValueError(f"{label} selection must be between 1 and {len(values)}")
    return values[selected_index - 1]


def format_cli_error(exc: Exception) -> str:
    if isinstance(exc, (ValueError, IndexError, NotImplementedError)):
        return str(exc)
    if isinstance(exc, RuntimeError):
        message = str(exc)
        if message.startswith("vmrun.exe not found:"):
            return message
    return f"{type(exc).__name__}: operation failed"


def main() -> None:
    try:
        asyncio.run(main_async())
    except Exception as exc:
        print(format_cli_error(exc), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()

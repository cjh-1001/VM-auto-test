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
    SampleConfig,
    TestConfig,
    TimeoutConfig,
    VerificationConfig,
    load_config,
    parse_csv_samples,
    scan_samples_from_directory,
    to_test_case,
    write_config,
)
from vm_auto_test.env import is_env_configured, load_env_file, load_optional_env_file
from vm_auto_test.models import (
    ComparisonSpec,
    GuestCredentials,
    SampleSpec,
    Shell,
    StepResult,
    TestCase,
    TestMode,
    VerificationSpec,
)
from vm_auto_test.orchestrator import TestOrchestrator
from vm_auto_test.providers.base import VmwareProvider
from vm_auto_test.providers.factory import create_provider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run VMware guest sample validation tests.")
    parser.add_argument("--env-file", help="Load environment variables from a .env file before running")
    subparsers = parser.add_subparsers(dest="command")

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
    init_parser.add_argument("--samples-dir", help="Directory of sample files to auto-generate samples list")

    run_dir_parser = subparsers.add_parser("run-dir", help="Run all samples from a directory")
    run_dir_parser.add_argument("--vm", required=True, help="VM ID or .vmx path")
    run_dir_parser.add_argument("--mode", choices=[mode.value for mode in TestMode], required=True)
    run_dir_parser.add_argument("--snapshot", help="Snapshot name. If omitted, choose interactively.")
    run_dir_parser.add_argument("--dir", required=True, help="Directory containing sample files")
    run_dir_parser.add_argument("--pattern", help="File glob pattern (e.g. *.exe)")
    run_dir_parser.add_argument("--verify-command", required=True, help="Guest command that verifies effect")
    run_dir_parser.add_argument("--verify-shell", choices=[shell.value for shell in Shell], default=Shell.POWERSHELL.value)
    run_dir_parser.add_argument("--guest-user")
    run_dir_parser.add_argument("--guest-password")
    run_dir_parser.add_argument("--baseline-result", help="Required for AV mode")
    run_dir_parser.add_argument("--reports-dir", default="reports")

    run_csv_parser = subparsers.add_parser("run-csv", help="Run all samples from a CSV table")
    run_csv_parser.add_argument("--vm", required=True, help="VM ID or .vmx path")
    run_csv_parser.add_argument("--mode", choices=[mode.value for mode in TestMode], required=True)
    run_csv_parser.add_argument("--snapshot", help="Snapshot name. If omitted, choose interactively.")
    run_csv_parser.add_argument("--csv", required=True, help="Path to CSV file (UTF-8 BOM, columns: sample_file,shell,verify_command,verify_shell)")
    run_csv_parser.add_argument("--samples-base-dir", help="Base directory on VM for relative sample paths")
    run_csv_parser.add_argument("--guest-user")
    run_csv_parser.add_argument("--guest-password")
    run_csv_parser.add_argument("--baseline-result", help="Required for AV mode")
    run_csv_parser.add_argument("--reports-dir", default="reports")

    run_config_parser = subparsers.add_parser("run-config", help="Run validation from a YAML config")
    run_config_parser.add_argument("config", help="YAML config file")
    run_config_parser.add_argument("--guest-password")
    return parser


async def main_async() -> None:
    parser = build_parser()
    args = parser.parse_args()
    load_optional_env_file(Path(args.env_file) if args.env_file else None)
    provider = create_provider("vmrun")

    if args.command is None:
        env_path = Path(args.env_file) if args.env_file else Path(".env")
        if not is_env_configured():
            await _interactive_setup(env_path)
        await _interactive_menu(provider, env_path)
        return

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

    if args.command == "run-dir":
        sample_dir = Path(args.dir)
        globs = (args.pattern,) if args.pattern else None
        sample_configs = scan_samples_from_directory(sample_dir) if globs is None else scan_samples_from_directory(sample_dir, globs=globs)

        run_dir_orchestrator = TestOrchestrator(provider, Path(args.reports_dir), progress=print_progress)
        vm_id = clean_cli_value(args.vm)
        snapshot = clean_cli_value(args.snapshot) if args.snapshot else None
        if not snapshot:
            snapshots_list = await run_dir_orchestrator.list_snapshots(vm_id)
            if not snapshots_list:
                raise RuntimeError("没有找到快照，请确认 VM 已开机且装有 VMware Tools")
            snapshot = choose_from_list(snapshots_list, "选择快照")
            if snapshot is None:
                print("已取消")
                return

        guest_user = args.guest_user or os.getenv("VMWARE_GUEST_USER", "") or input("Guest user: ")
        guest_password = args.guest_password or os.getenv("VMWARE_GUEST_PASSWORD")
        if guest_password is None:
            guest_password = getpass.getpass("Guest password: ")

        sample_specs = tuple(
            SampleSpec(id=cfg.id, command=cfg.command, shell=cfg.shell)
            for cfg in sample_configs
        )
        verify_shell = Shell(args.verify_shell)
        verify_command = clean_cli_value(args.verify_command)
        test_case = TestCase(
            vm_id=vm_id,
            snapshot=snapshot,
            mode=TestMode(args.mode),
            sample_command=sample_configs[0].command,
            sample_shell=sample_configs[0].shell,
            verify_command=verify_command,
            verify_shell=verify_shell,
            credentials=GuestCredentials(guest_user, guest_password),
            baseline_result=args.baseline_result,
            samples=sample_specs,
            verification=VerificationSpec(command=verify_command, shell=verify_shell),
        )
        batch_result = await run_dir_orchestrator.run_batch(test_case)
        print(f"结果: {_classify_cn(batch_result.classification)}  ({batch_result.classification.value})  共 {len(batch_result.samples)} 个样本")
        for sample_item in batch_result.samples:
            print(f"sample={sample_item.sample_spec.id} {_classify_cn(sample_item.classification, short=True)} changed={sample_item.changed}")
        print(f"report_dir={batch_result.report_dir}")
        return

    if args.command == "run-csv":
        csv_path = Path(args.csv)
        sample_configs = parse_csv_samples(csv_path, samples_base_dir=args.samples_base_dir)
        print(f"Loaded {len(sample_configs)} samples from {csv_path}")

        csv_orchestrator = TestOrchestrator(provider, Path(args.reports_dir), progress=print_progress)
        vm_id = clean_cli_value(args.vm)
        snapshot = clean_cli_value(args.snapshot) if args.snapshot else None
        if not snapshot:
            snapshots_list = await csv_orchestrator.list_snapshots(vm_id)
            if not snapshots_list:
                raise RuntimeError("没有找到快照，请确认 VM 已开机且装有 VMware Tools")
            snapshot = choose_from_list(snapshots_list, "选择快照")
            if snapshot is None:
                print("已取消")
                return

        guest_user = args.guest_user or os.getenv("VMWARE_GUEST_USER", "") or input("Guest user: ")
        guest_password = args.guest_password or os.getenv("VMWARE_GUEST_PASSWORD")
        if guest_password is None:
            guest_password = getpass.getpass("Guest password: ")

        sample_specs: list[SampleSpec] = []
        for cfg in sample_configs:
            verification = VerificationSpec(
                command=cfg.verification.command,
                shell=cfg.verification.shell,
            ) if cfg.verification else VerificationSpec(command="", shell=Shell.POWERSHELL)
            sample_specs.append(
                SampleSpec(id=cfg.id, command=cfg.command, shell=cfg.shell, verification=verification)
            )

        first_verification = sample_specs[0].verification if sample_specs[0].verification else VerificationSpec(command="", shell=Shell.POWERSHELL)
        test_case = TestCase(
            vm_id=vm_id,
            snapshot=snapshot,
            mode=TestMode(args.mode),
            sample_command=sample_configs[0].command,
            sample_shell=sample_configs[0].shell,
            verify_command=first_verification.command,
            verify_shell=first_verification.shell,
            credentials=GuestCredentials(guest_user, guest_password),
            baseline_result=args.baseline_result,
            samples=tuple(sample_specs),
            verification=first_verification,
        )
        batch_result = await csv_orchestrator.run_batch(test_case)
        print(f"结果: {_classify_cn(batch_result.classification)}  ({batch_result.classification.value})  共 {len(batch_result.samples)} 个样本")
        for sample_item in batch_result.samples:
            print(f"sample={sample_item.sample_spec.id} {_classify_cn(sample_item.classification, short=True)} changed={sample_item.changed}")
        print(f"report_dir={batch_result.report_dir}")
        return

    if args.command == "init-config":
        config = await build_config_interactively(provider, args.vm, args.mode, samples_dir=args.samples_dir)
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
            print(f"结果: {_classify_cn(batch_result.classification)}  ({batch_result.classification.value})  共 {len(batch_result.samples)} 个样本")
            for sample in batch_result.samples:
                print(f"  {sample.sample_spec.id}  {_classify_cn(sample.classification, short=True)}")
            print(f"report_dir={batch_result.report_dir}")
            return
        result = await config_orchestrator.run(test_case)
        print(f"结果: {_classify_cn(result.classification)}")
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
            raise RuntimeError("没有找到快照，请确认 VM 已开机且装有 VMware Tools")
        snapshot = choose_from_list(snapshots, "选择快照")
        if snapshot is None:
            print("已取消")
            return

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
    print(f"结果: {_classify_cn(result.classification)}")
    print(f"classification={result.classification.value}")
    print(f"changed={result.changed}")
    print(f"report_dir={result.report_dir}")


async def _interactive_menu(provider: VmwareProvider, env_file: Path) -> None:
    while True:
        print("\n  —— VM Auto Test ——")
        print("  [0] 退出")
        print("  [1] 测试单样本")
        print("  [2] 测试多样本 (CSV)")
        print("  [3] 列出 VM")
        print("  [4] 列出快照")
        print("  [5] 重新配置环境")
        choice = input("\n  > ").strip()

        if choice == "0":
            print("  已退出")
            return

        if choice == "5":
            await _interactive_setup(env_file)
            continue

        if choice == "3":
            vms = await provider.list_running_vms()
            if not vms:
                print("  没有运行中的 VM")
            else:
                for i, vm in enumerate(vms, 1):
                    print(f"  [{i}] {vm}")
            continue

        if choice == "4":
            vm_id = clean_cli_value(input("  VM 路径: "))
            orchestrator = TestOrchestrator(provider, Path("reports"))
            snapshots = await orchestrator.list_snapshots(vm_id)
            if not snapshots:
                print("  没有找到快照")
            else:
                for i, s in enumerate(snapshots, 1):
                    print(f"  [{i}] {s}")
            continue

        if choice == "1":
            await _interactive_single(provider)
        elif choice == "2":
            await _interactive_csv(provider)
        else:
            print("  无效选项")


async def _interactive_single(provider: VmwareProvider) -> None:
    # 1. VM
    running = await provider.list_running_vms()
    if running:
        print("\n  —— 选择 VM ——")
        vm_id = choose_from_list(running, "选择 VM")
        if vm_id is None:
            return
    else:
        vm_id = clean_cli_value(input("\n  VM 路径: "))

    # 2. Snapshot
    orchestrator = TestOrchestrator(provider, Path("reports"))
    snapshots = await orchestrator.list_snapshots(vm_id)
    if not snapshots:
        print("  没有找到快照，请确认 VM 已开机且装有 VMware Tools")
        return
    print("\n  —— 选择快照 ——")
    snapshot = choose_from_list(snapshots, "选择快照")
    if snapshot is None:
        return

    # 3. Mode
    print("\n  —— 选择模式 ——")
    print("  baseline = 干净快照，验证样本是否有效（前后输出不同 → 有效）")
    print("  av       = 带杀软快照，验证杀软能否拦截（需先通过 baseline）")
    mode = TestMode(choose_value("模式", ["baseline", "av"], default="baseline"))
    baseline_result = None
    if mode == TestMode.AV:
        print("  AV 模式需要一份已通过的 baseline 报告（result.json）来确认样本本身有效。")
        baseline_result = clean_cli_value(input("  Baseline result.json 路径: "))

    # 4. Sample
    print("\n  —— 样本命令 ——")
    sample_command = input("  样本命令 (例如 C:\\Samples\\sample.exe): ").strip()
    sample_shell = Shell(choose_value("  用哪个 shell 执行", ["cmd", "powershell"], default="cmd"))

    # 5. Verify
    print("\n  —— 验证命令（样本跑前/跑后各执行一次）——")
    verify_command = input("  验证命令: ").strip()
    verify_shell = Shell(choose_value("  用哪个 shell 执行", ["cmd", "powershell"], default="powershell"))

    # 6. Guest
    guest_user = os.getenv("VMWARE_GUEST_USER") or input("  Guest 用户名: ").strip()
    guest_password = os.getenv("VMWARE_GUEST_PASSWORD") or getpass.getpass("  Guest 密码: ")

    # 7. Confirm & run
    print(f"\n  VM:       {vm_id}")
    print(f"  快照:     {snapshot}")
    print(f"  模式:     {mode.value}")
    print(f"  样本:     [{sample_shell.value}] {sample_command}")
    print(f"  验证:     [{verify_shell.value}] {verify_command}")
    if baseline_result:
        print(f"  baseline: {baseline_result}")
    if input("\n  确认执行? [y/N] ").strip().lower() != "y":
        print("  已取消")
        return

    test_case = TestCase(
        vm_id=vm_id, snapshot=snapshot, mode=mode,
        sample_command=sample_command, sample_shell=sample_shell,
        verify_command=verify_command, verify_shell=verify_shell,
        credentials=GuestCredentials(guest_user, guest_password),
        baseline_result=baseline_result,
    )
    orch = TestOrchestrator(provider, Path("reports"), progress=print_progress)
    result = await orch.run(test_case)
    print(f"\n  {_classify_cn(result.classification)}")
    print(f"  报告: {result.report_dir}")


async def _interactive_csv(provider: VmwareProvider) -> None:
    # 1. VM
    running = await provider.list_running_vms()
    if running:
        print("\n  —— 选择 VM ——")
        vm_id = choose_from_list(running, "选择 VM")
        if vm_id is None:
            return
    else:
        vm_id = clean_cli_value(input("\n  VM 路径: "))

    # 2. Snapshot
    orchestrator = TestOrchestrator(provider, Path("reports"))
    snapshots = await orchestrator.list_snapshots(vm_id)
    if not snapshots:
        print("  没有找到快照，请确认 VM 已开机且装有 VMware Tools")
        return
    print("\n  —— 选择快照 ——")
    snapshot = choose_from_list(snapshots, "选择快照")
    if snapshot is None:
        return

    # 3. Mode
    print("\n  —— 选择模式 ——")
    print("  baseline = 干净快照，验证样本是否有效（前后输出不同 → 有效）")
    print("  av       = 带杀软快照，验证杀软能否拦截（需先通过 baseline）")
    mode = TestMode(choose_value("模式", ["baseline", "av"], default="baseline"))
    baseline_result = None
    if mode == TestMode.AV:
        print("  AV 模式需要一份已通过的 baseline 报告（result.json）来确认样本本身有效。")
        baseline_result = clean_cli_value(input("  Baseline result.json 路径: "))

    # 4. CSV
    csv_path = Path(clean_cli_value(input("\n  CSV 文件路径: ").strip()))
    samples_base_dir = input("  VM 上样本目录 (回车跳过): ").strip() or None

    # 5. Guest
    guest_user = os.getenv("VMWARE_GUEST_USER") or input("  Guest 用户名: ").strip()
    guest_password = os.getenv("VMWARE_GUEST_PASSWORD") or getpass.getpass("  Guest 密码: ")

    # 6. Parse & confirm
    sample_configs = parse_csv_samples(csv_path, samples_base_dir=samples_base_dir)
    print(f"\n  从 CSV 读取 {len(sample_configs)} 个样本:")
    for cfg in sample_configs:
        print(f"    [{cfg.shell.value}] {cfg.command}")
        print(f"      verify: [{cfg.verification.shell.value}] {cfg.verification.command}")
    print(f"  VM:       {vm_id}")
    print(f"  快照:     {snapshot}")
    print(f"  模式:     {mode.value}")
    if baseline_result:
        print(f"  baseline: {baseline_result}")
    if input("\n  确认执行? [y/N] ").strip().lower() != "y":
        print("  已取消")
        return

    # 7. Run
    sample_specs: list[SampleSpec] = []
    for cfg in sample_configs:
        v = cfg.verification
        sample_specs.append(SampleSpec(
            id=cfg.id, command=cfg.command, shell=cfg.shell,
            verification=VerificationSpec(command=v.command, shell=v.shell),
        ))
    first_v = sample_specs[0].verification
    test_case = TestCase(
        vm_id=vm_id, snapshot=snapshot, mode=mode,
        sample_command=sample_configs[0].command,
        sample_shell=sample_configs[0].shell,
        verify_command=first_v.command if first_v else "",
        verify_shell=first_v.shell if first_v else Shell.POWERSHELL,
        credentials=GuestCredentials(guest_user, guest_password),
        baseline_result=baseline_result,
        samples=tuple(sample_specs),
        verification=first_v or VerificationSpec(command="", shell=Shell.POWERSHELL),
    )
    orch = TestOrchestrator(provider, Path("reports"), progress=print_progress)
    batch_result = await orch.run_batch(test_case)
    print(f"\n  {_classify_cn(batch_result.classification)}  ({batch_result.classification.value})  共 {len(batch_result.samples)} 个样本")
    for s in batch_result.samples:
        print(f"    {s.sample_spec.id}  {_classify_cn(s.classification, short=True)}")
    print(f"  报告: {batch_result.report_dir}")


async def _interactive_setup(env_file: Path) -> None:
    print("\n  ⚙ 环境配置")
    print("  回车保留当前值，输入新值覆盖\n")

    vmrun_path = _prompt_env("vmrun.exe 路径", os.getenv("VMRUN_PATH"))

    guest_user = _prompt_env("Guest 用户名", os.getenv("VMWARE_GUEST_USER"))

    current_pw = os.getenv("VMWARE_GUEST_PASSWORD", "")
    pw_display = "***" if current_pw else "(未设置)"
    print(f"  Guest 密码  当前: {pw_display}")
    guest_password = getpass.getpass("  新值: ").strip()
    if not guest_password:
        guest_password = current_pw

    print("\n  --- vmrest/MCP 配置 (可选) ---")
    vmware_host = _prompt_env("VMWARE_HOST", os.getenv("VMWARE_HOST"))
    vmware_port = _prompt_env("VMWARE_PORT", os.getenv("VMWARE_PORT"))

    lines = [
        f"VMRUN_PATH={_quote_if_needed(vmrun_path)}",
        "",
        f"VMWARE_GUEST_USER={guest_user}",
        f"VMWARE_GUEST_PASSWORD={guest_password}",
        "",
        f"VMWARE_HOST={vmware_host}",
        f"VMWARE_PORT={vmware_port}",
    ]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    load_env_file(env_file, override=True)
    print("\n  ✓ 配置已保存\n")


def _prompt_env(label: str, current: str | None) -> str:
    display = current if current else "(未设置)"
    new_value = input(f"  {label}  当前: {display}\n  新值: ").strip()
    return new_value if new_value else (current or "")


def _classify_cn(classification: Any, short: bool = False) -> str:
    mapping = {
        "BASELINE_VALID": "有效" if short else "样本有效（前后输出有变化）",
        "BASELINE_INVALID": "无效" if short else "样本无效（前后输出无变化）",
        "AV_NOT_BLOCKED": "未拦截" if short else "杀软未拦截（攻击效果发生）",
        "AV_BLOCKED_OR_NO_CHANGE": "已拦截" if short else "杀软已拦截或未生效",
    }
    value = classification.value if hasattr(classification, "value") else str(classification)
    return mapping.get(value, value)


def _quote_if_needed(value: str) -> str:
    if value and (" " in value or "\\" in value):
        if '"' not in value:
            return f'"{value}"'
    return value


def print_progress(step: StepResult) -> None:
    label = step.name.replace("_", " ")
    detail = f" - {step.detail}" if step.detail else ""
    print(f"[{step.status}] {label}{detail}", flush=True)


async def build_config_interactively(
    provider: VmwareProvider,
    vm_id: str | None,
    mode_value: str | None,
    samples_dir: str | None = None,
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
    snapshot = choose_from_list(snapshots, "选择快照")

    mode = TestMode(mode_value or choose_value("Mode", [mode.value for mode in TestMode]))
    baseline_result = None
    if mode == TestMode.AV:
        print("Baseline result path 填已经通过的 baseline result.json 路径。")
        print("例如: reports\\20260507-120000-000000-sample\\result.json")
        baseline_result = clean_cli_value(input("Baseline result path: "))
        if not baseline_result:
            raise ValueError("AV mode requires baseline result path")

    if samples_dir:
        sample_configs = scan_samples_from_directory(Path(samples_dir))
        print(f"Auto-detected {len(sample_configs)} samples from {samples_dir}:")
        for sample_cfg_item in sample_configs:
            print(f"  - {sample_cfg_item.id}: {sample_cfg_item.command}")
        sample = None
        samples = sample_configs
    else:
        print("Sample command 是要在 guest 里执行的样本命令。")
        print("例如: C:\\Samples\\sample.exe 或 C:\\Samples\\run.bat")
        sample_command = input("Sample command: ").strip()
        if not sample_command:
            raise ValueError("Sample command is required")
        sample_shell = Shell(choose_value("Sample shell", [shell.value for shell in Shell], default=Shell.CMD.value))
        sample = CommandConfig(command=sample_command, shell=sample_shell)
        samples = ()

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
        sample=sample,
        samples=samples,
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


def choose_from_list(items: list[str], label: str = "选择") -> str | None:
    print(f"  [0] 返回")
    for index, item in enumerate(items, start=1):
        print(f"  [{index}] {item}")
    raw_selection = input(f"  {label}: ").strip()
    if raw_selection == "0":
        return None
    try:
        selected_index = int(raw_selection)
    except ValueError as exc:
        raise ValueError("选项必须是数字") from exc
    if selected_index < 1 or selected_index > len(items):
        raise ValueError(f"选项必须在 1 到 {len(items)} 之间")
    return items[selected_index - 1]


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
        raise ValueError("选项必须是数字") from exc
    if selected_index < 1 or selected_index > len(values):
        raise ValueError(f"选项必须在 1 到 {len(values)} 之间")
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

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

from vm_auto_test.config import (
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
from vm_auto_test.env import (
    is_env_configured,
    load_credentials_store,
    load_env_file,
    load_optional_env_file,
    resolve_guest_credentials,
    upsert_vm_credentials,
)
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
from vm_auto_test.providers.base import VmToolsNotReadyError, VmwareProvider
from vm_auto_test.providers.factory import create_provider

_BACK = object()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run VMware guest sample validation tests.")
    parser.add_argument("--env-file", help="Load environment variables from a .env file before running")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("vms", help="List running VMs")

    snapshot_parser = subparsers.add_parser("snapshots", help="List snapshots for a VM")
    snapshot_parser.add_argument("--vm", required=True, nargs="+", help="VM ID or .vmx path (spaces ok)")

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
        help="Guest password",
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
    run_csv_parser.add_argument("--csv", required=True, help="Path to CSV file (UTF-8 BOM, columns: sample_file,verify_command,verify_shell)")
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
        print("  正在查询运行中的 VM ...", flush=True)
        running_vms = await provider.list_running_vms()
        if not running_vms:
            print("  没有运行中的 VM")
            return
        for index, vm_id in enumerate(running_vms, start=1):
            print(f"[{index}] {vm_id}")
        return

    if args.command == "snapshots":
        vm_path = " ".join(args.vm) if isinstance(args.vm, list) else args.vm
        vm_path = clean_cli_value(vm_path)
        print(f"  正在查询快照: {vm_path}", flush=True)
        orchestrator = TestOrchestrator(provider, Path("reports"))
        try:
            snapshots = await orchestrator.list_snapshots(vm_path)
        except RuntimeError as exc:
            print(f"  {exc}")
            return
        if not snapshots:
            print("  没有找到快照，请先在 VMware Workstation 中为该 VM 创建快照")
            return
        for index, snapshot in enumerate(snapshots, start=1):
            print(f"  [{index}] {snapshot}")
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
                raise RuntimeError("没有找到快照，请先在 VMware Workstation 中为该 VM 创建快照")
            snapshot = choose_from_list(snapshots_list, "选择快照")
            if snapshot is None:
                print("已取消")
                return

        creds = resolve_guest_credentials(vm_id)
        if creds:
            guest_user = creds.user
            guest_password = creds.password
        else:
            guest_user = args.guest_user or input("Guest user: ")
            guest_password = args.guest_password
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
        max_id_width = max((_display_width(s.sample_spec.id) for s in batch_result.samples), default=0)
        for sample_item in batch_result.samples:
            label = _classify_cn(sample_item.classification, short=True)
            print(f"  {_display_ljust(sample_item.sample_spec.id, max_id_width + 2)}  {label}")
        print(f"  报告: {batch_result.report_dir}")
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
                raise RuntimeError("没有找到快照，请先在 VMware Workstation 中为该 VM 创建快照")
            snapshot = choose_from_list(snapshots_list, "选择快照")
            if snapshot is None:
                print("已取消")
                return

        creds = resolve_guest_credentials(vm_id)
        if creds:
            guest_user = creds.user
            guest_password = creds.password
        else:
            guest_user = args.guest_user or input("Guest user: ")
            guest_password = args.guest_password
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
        max_id_width = max((_display_width(s.sample_spec.id) for s in batch_result.samples), default=0)
        for sample_item in batch_result.samples:
            label = _classify_cn(sample_item.classification, short=True)
            print(f"  {_display_ljust(sample_item.sample_spec.id, max_id_width + 2)}  {label}")
        print(f"  报告: {batch_result.report_dir}")
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
        test_case = to_test_case(config, password=args.guest_password)
        config_orchestrator = TestOrchestrator(config_provider, Path(config.reports_dir), progress=print_progress)
        if config.samples:
            batch_result = await config_orchestrator.run_batch(test_case)
            print(f"结果: {_classify_cn(batch_result.classification)}  ({batch_result.classification.value})  共 {len(batch_result.samples)} 个样本")
            max_id_width = max((_display_width(s.sample_spec.id) for s in batch_result.samples), default=0)
            for sample in batch_result.samples:
                label = _classify_cn(sample.classification, short=True)
                print(f"  {_display_ljust(sample.sample_spec.id, max_id_width + 2)}  {label}")
            print(f"  报告: {batch_result.report_dir}")
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
            raise RuntimeError("没有找到快照，请先在 VMware Workstation 中为该 VM 创建快照")
        snapshot = choose_from_list(snapshots, "选择快照")
        if snapshot is None:
            print("已取消")
            return

    creds = resolve_guest_credentials(vm_id)
    if creds:
        guest_user = creds.user
        guest_password = creds.password
    else:
        guest_user = args.guest_user or input("Guest user: ")
        guest_password = args.guest_password
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
            await _interactive_list_vms(provider)
            continue

        if choice == "4":
            vm_id = clean_cli_value(input("  VM 路径: "))
            print(f"  正在查询快照 ...", flush=True)
            orchestrator = TestOrchestrator(provider, Path("reports"))
            try:
                snapshots = await orchestrator.list_snapshots(vm_id)
            except RuntimeError as exc:
                print(f"  {exc}")
                continue
            if not snapshots:
                print("  没有找到快照，请先在 VMware Workstation 中为该 VM 创建快照")
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


def _prompt_back(prompt: str) -> str | None:
    """input() wrapper: returns None on 'b' (go back), strips whitespace."""
    print("  输入 b 返回上一步")
    value = input(f"  {prompt}: ").strip()
    if value.lower() == "b":
        return None
    return value


async def _resolve_and_verify_credentials(
    provider: VmwareProvider, vm_id: str
) -> GuestCredentials | None:
    """Resolve saved credentials, verify them, prompt to reconfigure on failure.

    Returns credentials if ready, None if user backs out.
    """
    saved = resolve_guest_credentials(vm_id)
    if saved:
        print(f"  Guest 用户名: {saved.user}  (来自 credentials.json)")
        print("  正在验证凭证 ...", flush=True)
        try:
            await provider.verify_guest_credentials(vm_id, saved)
            print("  凭证验证成功 ✓")
            return saved
        except Exception as exc:
            print(f"  凭证验证失败: {exc}")
            print("  请重新配置凭证。")
    else:
        print("  该虚拟机暂无凭证，请先配置。")

    print()
    guest_user = input("  Guest 用户名: ").strip()
    if not guest_user:
        print("  已取消")
        return None
    guest_password = getpass.getpass("  Guest 密码: ")
    if not guest_password:
        print("  已取消")
        return None
    credentials = GuestCredentials(guest_user, guest_password)

    print("  正在验证凭证 ...", flush=True)
    try:
        await provider.verify_guest_credentials(vm_id, credentials)
        print("  凭证验证成功 ✓")
        upsert_vm_credentials(vm_id, guest_user, guest_password)
        print(f"  已保存到 {os.getenv('VMWARE_CREDENTIALS_FILE', 'credentials.json')}")
        return credentials
    except Exception as exc:
        print(f"  凭证验证失败: {exc}")
        return None


async def _interactive_list_vms(provider: VmwareProvider) -> None:
    vms = await provider.list_running_vms()
    if not vms:
        print("  没有运行中的 VM")
        return
    for i, vm in enumerate(vms, 1):
        store = load_credentials_store()
        tag = "  [已配置]" if vm in store else ""
        print(f"  [{i}] {vm}{tag}")

    print("\n  —— 选择 VM ——")
    result = choose_from_list(vms, "选择 VM")
    if result is None or result is _BACK:
        return
    vm_id = result

    while True:
        store = load_credentials_store()
        if vm_id in store:
            entry = store[vm_id]
            credentials = GuestCredentials(entry["user"], entry["password"])
            print(f"\n  VM: {vm_id}")
            print(f"  已配置凭证: {credentials.user}")
            print("  [0] 返回")
            print("  [1] 验证凭证")
            print("  [2] 重新配置")
            choice = input("\n  > ").strip()
            if choice == "0":
                return
            if choice == "1":
                pass  # verify below
            elif choice == "2":
                credentials = _prompt_vm_credentials(vm_id)
                if credentials is None:
                    continue
            else:
                continue
        else:
            print(f"\n  VM: {vm_id}")
            print("  该 VM 未配置凭证")
            print("  [0] 返回")
            print("  [1] 配置凭证")
            choice = input("\n  > ").strip()
            if choice == "0":
                return
            if choice == "1":
                credentials = _prompt_vm_credentials(vm_id)
                if credentials is None:
                    continue
            else:
                continue

        print(f"\n  正在验证 {credentials.user}@{vm_id} ...")
        try:
            await provider.verify_guest_credentials(vm_id, credentials)
            print("  凭证验证成功 ✓")
            if vm_id not in load_credentials_store():
                upsert_vm_credentials(vm_id, credentials.user, credentials.password)
                print(f"  已保存到 {os.getenv('VMWARE_CREDENTIALS_FILE', 'credentials.json')}")
        except Exception as exc:
            print(f"  凭证验证失败: {exc}")
            print("  请重新配置凭证。")


def _prompt_vm_credentials(vm_id: str) -> GuestCredentials | None:
    """Prompt for VM credentials and save to store."""
    print()
    guest_user = input("  Guest 用户名: ").strip()
    if not guest_user:
        print("  已取消")
        return None
    guest_password = getpass.getpass("  Guest 密码: ")
    if not guest_password:
        print("  已取消")
        return None
    credentials = GuestCredentials(guest_user, guest_password)
    save = input("  保存到配置文件? [Y/n]: ").strip().lower()
    if save != "n":
        upsert_vm_credentials(vm_id, guest_user, guest_password)
        print(f"  已保存到 {os.getenv('VMWARE_CREDENTIALS_FILE', 'credentials.json')}")
    return credentials


async def _interactive_single(provider: VmwareProvider) -> None:
    vm_id: str | None = None
    snapshot: str | None = None
    mode: TestMode | None = None
    baseline_result: str | None = None
    sample_command: str | None = None
    sample_shell: Shell | None = None
    verify_command: str | None = None
    verify_shell: Shell | None = None
    guest_user: str | None = None
    guest_password: str | None = None

    step = 0
    while True:
        # — step 0: VM —
        if step == 0:
            running = await provider.list_running_vms()
            if running:
                print("\n  —— 选择 VM ——")
                result = choose_from_list(running, "选择 VM")
                if result is None:
                    return
                if result is _BACK:
                    return
                vm_id = result
            else:
                print("\n  —— 输入 VM 路径 ——")
                result = _prompt_back("VM 路径")
                if result is None:
                    continue
                vm_id = clean_cli_value(result)
            step = 1
            continue

        # — step 1: Snapshot —
        if step == 1:
            print("  正在查询快照 ...", flush=True)
            orchestrator = TestOrchestrator(provider, Path("reports"))
            try:
                snapshots = await orchestrator.list_snapshots(vm_id)
            except RuntimeError as exc:
                print(f"  {exc}")
                step = 0
                continue
            if not snapshots:
                print("  没有找到快照，请先在 VMware Workstation 中为该 VM 创建快照")
                step = 0
                continue
            print("\n  —— 选择快照 ——")
            result = choose_from_list(snapshots, "选择快照")
            if result is None:
                return
            if result is _BACK:
                step = 0
                continue
            snapshot = result
            step = 2
            continue

        # — step 2: Mode —
        if step == 2:
            print("\n  —— 选择模式 ——")
            print("  baseline = 干净快照，验证样本是否有效（前后输出不同 → 有效）")
            print("  av       = 带杀软快照，验证杀软能否拦截（需先通过 baseline）")
            result = choose_value("模式", ["baseline", "av"], default="baseline")
            if result is _BACK:
                step = 1
                continue
            mode = TestMode(result)
            baseline_result = None
            if mode == TestMode.AV:
                print("  AV 模式需要一份已通过的 baseline 报告（result.json）来确认样本本身有效。")
                result = _prompt_back("Baseline result.json 路径")
                if result is None:
                    step = 1
                    continue
                baseline_result = clean_cli_value(result)
            step = 3
            continue

        # — step 3: Sample —
        if step == 3:
            print("\n  —— 样本路径 ——")
            result = _prompt_back("样本路径 (例如 C:\\Samples\\sample.exe)")
            if result is None:
                step = 2
                continue
            sample_command = result
            sample_shell = Shell("cmd")
            step = 4
            continue

        # — step 4: Verify —
        if step == 4:
            print("\n  —— 验证命令（样本跑前/跑后各执行一次）——")
            result = _prompt_back("验证命令")
            if result is None:
                step = 3
                continue
            verify_command = result
            result = choose_value("用哪个 shell 执行验证命令", ["cmd", "powershell"], default="cmd")
            if result is _BACK:
                step = 3
                continue
            verify_shell = Shell(result)
            step = 5
            continue

        # — step 5: Guest —
        if step == 5:
            print("\n  —— Guest 凭据 ——")
            creds = await _resolve_and_verify_credentials(provider, vm_id)
            if creds is None:
                step = 4
                continue
            guest_user = creds.user
            guest_password = creds.password
            step = 6
            continue

        # — step 6: Confirm —
        if step == 6:
            print(f"\n  VM:       {vm_id}")
            print(f"  快照:     {snapshot}")
            print(f"  模式:     {mode.value}")
            print(f"  样本:     [{sample_shell.value}] {sample_command}")
            print(f"  验证:     [{verify_shell.value}] {verify_command}")
            if baseline_result:
                print(f"  baseline: {baseline_result}")
            confirm = input("  确认执行? [y/N]: ").strip().lower()
            if confirm == "b":
                step = 5
                continue
            if confirm != "y":
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
            try:
                result = await orch.run(test_case)
            except VmToolsNotReadyError:
                return
            print(f"\n  {_classify_cn(result.classification)}")
            print(f"  报告: {result.report_dir}")
            return


async def _interactive_csv(provider: VmwareProvider) -> None:
    vm_id: str | None = None
    snapshot: str | None = None
    mode: TestMode | None = None
    baseline_result: str | None = None
    csv_path: Path | None = None
    samples_base_dir: str | None = None
    guest_user: str | None = None
    guest_password: str | None = None

    step = 0
    while True:
        # — step 0: VM —
        if step == 0:
            running = await provider.list_running_vms()
            if running:
                print("\n  —— 选择 VM ——")
                result = choose_from_list(running, "选择 VM")
                if result is None:
                    return
                if result is _BACK:
                    return
                vm_id = result
            else:
                print("\n  —— 输入 VM 路径 ——")
                result = _prompt_back("VM 路径")
                if result is None:
                    continue
                vm_id = clean_cli_value(result)
            step = 1
            continue

        # — step 1: Snapshot —
        if step == 1:
            print("  正在查询快照 ...", flush=True)
            orchestrator = TestOrchestrator(provider, Path("reports"))
            try:
                snapshots = await orchestrator.list_snapshots(vm_id)
            except RuntimeError as exc:
                print(f"  {exc}")
                step = 0
                continue
            if not snapshots:
                print("  没有找到快照，请先在 VMware Workstation 中为该 VM 创建快照")
                step = 0
                continue
            print("\n  —— 选择快照 ——")
            result = choose_from_list(snapshots, "选择快照")
            if result is None:
                return
            if result is _BACK:
                step = 0
                continue
            snapshot = result
            step = 2
            continue

        # — step 2: Mode —
        if step == 2:
            print("\n  —— 选择模式 ——")
            print("  baseline = 干净快照，验证样本是否有效（前后输出不同 → 有效）")
            print("  av       = 带杀软快照，验证杀软能否拦截（需先通过 baseline）")
            result = choose_value("模式", ["baseline", "av"], default="baseline")
            if result is _BACK:
                step = 1
                continue
            mode = TestMode(result)
            baseline_result = None
            if mode == TestMode.AV:
                print("  AV 模式需要一份已通过的 baseline 报告（result.json）来确认样本本身有效。")
                result = _prompt_back("Baseline result.json 路径")
                if result is None:
                    step = 1
                    continue
                baseline_result = clean_cli_value(result)
            step = 3
            continue

        # — step 3: CSV —
        if step == 3:
            print("\n  —— CSV 配置 ——")
            result = _prompt_back("CSV 文件路径")
            if result is None:
                step = 2
                continue
            csv_path = Path(clean_cli_value(result))
            result = _prompt_back("VM 上样本目录 (绝对路径则留空)")
            if result is None:
                step = 2
                continue
            samples_base_dir = result or None
            step = 4
            continue

        # — step 4: Guest —
        if step == 4:
            print("\n  —— Guest 凭据 ——")
            creds = await _resolve_and_verify_credentials(provider, vm_id)
            if creds is None:
                step = 3
                continue
            guest_user = creds.user
            guest_password = creds.password
            step = 5
            continue

        # — step 5: Parse & Confirm —
        if step == 5:
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
            confirm = input("  确认执行? [y/N]: ").strip().lower()
            if confirm == "b":
                step = 4
                continue
            if confirm != "y":
                print("  已取消")
                return

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
            try:
                batch_result = await orch.run_batch(test_case)
            except VmToolsNotReadyError:
                return
            print(f"\n  {_classify_cn(batch_result.classification)}  ({batch_result.classification.value})  共 {len(batch_result.samples)} 个样本")
            max_id_width = max((_display_width(s.sample_spec.id) for s in batch_result.samples), default=0)
            for s in batch_result.samples:
                label = _classify_cn(s.classification, short=True)
                print(f"    {_display_ljust(s.sample_spec.id, max_id_width + 2)}  {label}")
            print(f"  报告: {batch_result.report_dir}")
            return


async def _interactive_setup(env_file: Path) -> None:
    print("\n  ⚙ 环境配置")
    print("  回车保留当前值，输入新值覆盖\n")

    vmrun_path = _prompt_env("vmrun.exe 路径", os.getenv("VMRUN_PATH"))

    print("\n  --- vmrest/MCP 配置 (可选) ---")
    vmware_host = _prompt_env("VMWARE_HOST", os.getenv("VMWARE_HOST") or "localhost")
    vmware_port = _prompt_env("VMWARE_PORT", os.getenv("VMWARE_PORT") or "8697")

    existing = load_env_file_text(env_file)
    creds_file = _keep_existing("VMWARE_CREDENTIALS_FILE", existing) or "credentials.json"

    lines = [
        f"VMRUN_PATH={_quote_if_needed(vmrun_path)}",
        "",
        f"VMWARE_CREDENTIALS_FILE={_quote_if_needed(creds_file)}",
        "",
        f"VMWARE_HOST={vmware_host}",
        f"VMWARE_PORT={vmware_port}",
    ]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    load_env_file(env_file, override=True)
    print("\n  ✓ 配置已保存\n")
    print("  VM 独立凭证请在主菜单 [3] 列出 VM 中配置。\n")


def _prompt_env(label: str, current: str | None) -> str:
    display = current if current else "(未设置)"
    new_value = input(f"  {label}  当前: {display}\n  新值: ").strip()
    return new_value if new_value else (current or "")


def load_env_file_text(path: Path) -> dict[str, str]:
    """Parse .env file into a dict, preserving existing keys."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = _strip_quotes_env(value.strip())
    return result


def _keep_existing(key: str, existing: dict[str, str]) -> str:
    return existing.get(key, "")


def _strip_quotes_env(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


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


def _display_width(text: str) -> int:
    """Count display width: CJK chars = 2, ASCII = 1."""
    return sum(2 if ord(c) > 127 else 1 for c in text)


def _display_ljust(text: str, width: int) -> str:
    """ljust that accounts for CJK display width."""
    return text + " " * max(0, width - _display_width(text))


def print_progress(step: StepResult) -> None:
    status_col = _display_ljust(f"[{step.status}]", 10)
    stage_col = _display_ljust(step.stage, 14) if step.stage else " " * 14
    label = step.name.replace("_", " ")
    name_col = _display_ljust(label, 24)
    detail = step.detail if step.detail else ""
    print(f"{status_col}{stage_col}{name_col}{detail}", flush=True)


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
        print("Sample path 是要在 guest 里执行的样本路径。")
        print("例如: C:\\Samples\\sample.exe 或 C:\\Samples\\run.bat")
        sample_command = input("Sample command: ").strip()
        if not sample_command:
            raise ValueError("Sample command is required")
        sample = CommandConfig(command=sample_command, shell=Shell.CMD)
        samples = ()

    print("Verification command 是样本运行前后都要执行的验证命令，用来观察是否发生变化。")
    print("例如: type C:\\marker.txt、dir C:\\Users、net user")
    verify_command = input("Verification command: ").strip()
    if not verify_command:
        raise ValueError("Verification command is required")
    verify_shell = Shell(choose_value("Verification shell", [shell.value for shell in Shell], default=Shell.CMD.value))

    guest_user = input("Guest user inside VM (example: Administrator): ").strip()
    if not guest_user:
        raise ValueError("Guest user is required")
    guest_password = input("Guest password: ").strip()
    if not guest_password:
        raise ValueError("Guest password is required")

    return TestConfig(
        vm_id=selected_vm_id,
        snapshot=snapshot,
        mode=mode,
        baseline_result=baseline_result,
        guest=GuestConfig(user=guest_user, password=guest_password),
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
    print(f"  [0] 返回主菜单")
    for index, item in enumerate(items, start=1):
        print(f"  [{index}] {item}")
    print("  输入数字选择，b 回上一步")
    raw_selection = input(f"  {label}: ").strip().lower()
    if raw_selection == "0":
        return None
    if raw_selection == "b":
        return _BACK
    try:
        selected_index = int(raw_selection)
    except ValueError as exc:
        raise ValueError("选项必须是数字") from exc
    if selected_index < 1 or selected_index > len(items):
        raise ValueError(f"选项必须在 1 到 {len(items)} 之间")
    return items[selected_index - 1]


def choose_value(label: str, values: list[str], default: str | None = None) -> str:
    for index, value in enumerate(values, start=1):
        default_marker = " (默认)" if value == default else ""
        print(f"  [{index}] {value}{default_marker}")
    print("  输入数字选择，b 回上一步")
    raw_selection = input(f"  {label}: ").strip().lower()
    if raw_selection == "b":
        return _BACK
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
    if isinstance(exc, (VmToolsNotReadyError, RuntimeError)):
        return str(exc)
    return f"{type(exc).__name__}: operation failed"


def main() -> None:
    try:
        asyncio.run(main_async())
    except Exception as exc:
        print(format_cli_error(exc), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()

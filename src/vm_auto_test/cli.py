from __future__ import annotations

import argparse
import asyncio
import getpass
import html
import json
import os
import re
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Sequence

from vm_auto_test.config import (
    CommandConfig,
    GuestConfig,
    NormalizeConfig,
    SampleConfig,
    TestConfig,
    TimeoutConfig,
    VerificationConfig,
    load_config,
    load_default_ignore_patterns,
    parse_csv_samples,
    scan_samples_from_directory,
    to_test_case,
    write_config,
)
from vm_auto_test.commands.output import classify_cn, display_ljust, display_width, print_batch_report_paths, print_batch_summary, print_progress, reset_progress
from vm_auto_test.env import (
    is_env_configured,
    load_credentials_store,
    load_env_file,
    load_optional_env_file,
    resolve_guest_credentials,
    upsert_vm_credentials,
)
from vm_auto_test.models import (
    AvAnalyzeSpec,
    BatchTestResult,
    ComparisonSpec,
    GuestCredentials,
    PLAN_REPEAT_COUNT_MAX,
    PlanRunResult,
    PlanTask,
    PlanTaskKind,
    SampleSpec,
    Shell,
    StepResult,
    TestCase,
    TestMode,
    TestResult,
    VerificationSpec,
)
from vm_auto_test.orchestrator import TestOrchestrator
from vm_auto_test.reporting import write_batch_html_from_json
from vm_auto_test.providers.base import VmToolsNotReadyError, VmwareProvider
from vm_auto_test.providers.factory import create_provider

_BACK = object()
_ENV_VAR_RE = re.compile(r"%([^%]+)%")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run VMware guest sample validation tests.")
    parser.add_argument("--env-file", help="Load environment variables from a .env file before running")
    subparsers = parser.add_subparsers(dest="command")

    config_parser = subparsers.add_parser("config", help="Manage and validate config files")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    validate_parser = config_subparsers.add_parser("validate", help="Validate a YAML config file")
    validate_parser.add_argument("--config", required=True, help="YAML config file to validate")

    report_parser = subparsers.add_parser("report", help="Generate a standalone report from JSON results")
    report_parser.add_argument("--input", required=True, help="Input result JSON file")
    report_parser.add_argument("--output", required=True, help="Output report file")
    report_parser.add_argument("--format", choices=("html", "json"), default="html")

    doctor_parser = subparsers.add_parser("doctor", help="Check local CLI environment")
    doctor_parser.add_argument("--config", help="Optional YAML config file to validate")
    doctor_parser.add_argument("--reports-dir", default="reports", help="Directory to check for report write access")

    subparsers.add_parser("vms", help="List running VMs")

    snapshot_parser = subparsers.add_parser("snapshots", help="List snapshots for a VM")
    snapshot_parser.add_argument("--vm", required=True, nargs="+", help="VM ID or .vmx path (spaces ok)")

    run_parser = subparsers.add_parser("run", help="Run baseline or AV validation")
    run_parser.add_argument("--vm", help="VM ID or .vmx path")
    run_parser.add_argument("--mode", choices=[m.value for m in TestMode])
    run_parser.add_argument("--snapshot", help="Snapshot name. If omitted, choose interactively.")
    run_parser.add_argument("--sample-command", help="Guest command that runs the sample")
    run_parser.add_argument("--sample-shell", choices=[shell.value for shell in Shell], default=Shell.CMD.value)
    run_parser.add_argument("--verify-command", help="Guest command that verifies effect")
    run_parser.add_argument("--verify-shell", choices=[shell.value for shell in Shell], default=Shell.POWERSHELL.value)
    run_parser.add_argument("--guest-user")
    run_parser.add_argument(
        "--guest-password",
        help="Guest password",
    )
    run_parser.add_argument("--baseline-result", help="Optional baseline report path for reference")
    run_parser.add_argument("--capture-screenshot", action="store_true", default=False, help="Capture VM screenshot after verification")
    run_parser.add_argument("--reports-dir", default="reports")
    run_parser.add_argument("--config", help="YAML config file. When set, run uses config-driven execution.")

    init_parser = subparsers.add_parser("init-config", help="Create a test config interactively")
    init_parser.add_argument("--output", default="configs/sample.yaml", help="Config file to write")
    init_parser.add_argument("--vm", help="VM ID or .vmx path")
    init_parser.add_argument("--mode", choices=[m.value for m in TestMode])
    init_parser.add_argument("--samples-dir", help="Directory of sample files to auto-generate samples list")

    run_dir_parser = subparsers.add_parser("run-dir", help="Run all samples from a directory")
    run_dir_parser.add_argument("--vm", required=True, help="VM ID or .vmx path")
    run_dir_parser.add_argument("--mode", choices=[m.value for m in TestMode], required=True)
    run_dir_parser.add_argument("--snapshot", help="Snapshot name. If omitted, choose interactively.")
    run_dir_parser.add_argument("--dir", required=True, help="Directory containing sample files")
    run_dir_parser.add_argument("--pattern", help="File glob pattern (e.g. *.exe)")
    run_dir_parser.add_argument("--verify-command", help="Guest command that verifies effect")
    run_dir_parser.add_argument("--verify-shell", choices=[shell.value for shell in Shell], default=Shell.POWERSHELL.value)
    run_dir_parser.add_argument("--guest-user")
    run_dir_parser.add_argument("--guest-password")
    run_dir_parser.add_argument("--baseline-result", help="Optional baseline report path for reference")
    run_dir_parser.add_argument("--capture-screenshot", action="store_true", default=False, help="Capture VM screenshot after verification")
    run_dir_parser.add_argument("--reports-dir", default="reports")

    run_csv_parser = subparsers.add_parser("run-csv", help="Run all samples from a CSV table")
    run_csv_parser.add_argument("--vm", required=True, help="VM ID or .vmx path")
    run_csv_parser.add_argument("--mode", choices=[m.value for m in TestMode], required=True)
    run_csv_parser.add_argument("--snapshot", help="Snapshot name. If omitted, choose interactively.")
    run_csv_parser.add_argument("--csv", required=True, help="Path to CSV file (UTF-8 BOM, columns: sample_file,verify_command,verify_shell)")
    run_csv_parser.add_argument("--samples-base-dir", help="Base directory on VM for relative sample paths")
    run_csv_parser.add_argument("--guest-user")
    run_csv_parser.add_argument("--guest-password")
    run_csv_parser.add_argument("--baseline-result", help="Optional baseline report path for reference")
    run_csv_parser.add_argument("--capture-screenshot", action="store_true", default=False, help="Capture VM screenshot after verification")
    run_csv_parser.add_argument("--reports-dir", default="reports")

    run_config_parser = subparsers.add_parser("run-config", help="Run validation from a YAML config")
    run_config_parser.add_argument("config", help="YAML config file")
    run_config_parser.add_argument("--guest-password")
    return parser


async def main_async(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_optional_env_file(Path(clean_cli_value(args.env_file)) if args.env_file else None)

    if args.command == "config":
        if args.config_command == "validate":
            config_path = Path(clean_cli_value(args.config))
            load_config(config_path)
            print(f"Config is valid: {config_path}")
            return 0
        parser.error("config requires a subcommand")

    if args.command == "report":
        _generate_report_from_json(
            Path(clean_cli_value(args.input)),
            Path(clean_cli_value(args.output)),
            args.format,
        )
        print(f"Report written to: {clean_cli_value(args.output)}")
        return 0

    if args.command == "doctor":
        return _run_doctor(
            Path(clean_cli_value(args.config)) if args.config else None,
            Path(clean_cli_value(args.reports_dir)),
        )

    provider = create_provider("vmrun")

    if args.command is None:
        env_path = Path(clean_cli_value(args.env_file)) if args.env_file else Path(".env")
        if not is_env_configured():
            await _interactive_setup(env_path)
        await _interactive_menu(provider, env_path)
        return 0

    if args.command == "vms":
        print("  正在查询运行中的 VM ...", flush=True)
        running_vms = await provider.list_running_vms()
        if not running_vms:
            print("  没有运行中的 VM")
            return 0
        for index, vm_id in enumerate(running_vms, start=1):
            print(f"[{index}] {vm_id}")
        return 0

    if args.command == "snapshots":
        vm_path = " ".join(args.vm) if isinstance(args.vm, list) else args.vm
        vm_path = clean_cli_value(vm_path)
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

    if args.command == "run-dir":
        sample_dir = Path(clean_cli_value(args.dir))
        globs = (args.pattern,) if args.pattern else None
        sample_configs = scan_samples_from_directory(sample_dir) if globs is None else scan_samples_from_directory(sample_dir, globs=globs)

        run_dir_orchestrator = TestOrchestrator(provider, Path(clean_cli_value(args.reports_dir)), progress=print_progress)
        vm_id = clean_cli_value(args.vm)
        snapshot = clean_cli_value(args.snapshot) if args.snapshot else None
        if not snapshot:
            snapshots_list = await run_dir_orchestrator.list_snapshots(vm_id)
            if not snapshots_list:
                raise RuntimeError("没有找到快照，请先在 VMware Workstation 中为该 VM 创建快照")
            snapshot = choose_from_list(snapshots_list, "选择快照")
            if snapshot is None:
                print("已取消")
                return 0

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
        verify_shell = Shell(args.verify_shell) if args.verify_shell else Shell.POWERSHELL
        verify_command = clean_cli_value(args.verify_command) if args.verify_command else ""
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
            capture_screenshot=args.capture_screenshot,
            normalize_ignore_patterns=load_default_ignore_patterns(),
        )
        reset_progress()
        batch_result = await run_dir_orchestrator.run_batch(test_case)
        print_batch_summary(batch_result)
        print_batch_report_paths(batch_result.report_dir)
        return 0

    if args.command == "run-csv":
        csv_path = Path(clean_cli_value(args.csv))
        sample_configs = parse_csv_samples(csv_path, samples_base_dir=clean_cli_value(args.samples_base_dir) if args.samples_base_dir else None)
        print(f"Loaded {len(sample_configs)} samples from {csv_path}")

        csv_orchestrator = TestOrchestrator(provider, Path(clean_cli_value(args.reports_dir)), progress=print_progress)
        vm_id = clean_cli_value(args.vm)
        snapshot = clean_cli_value(args.snapshot) if args.snapshot else None
        if not snapshot:
            snapshots_list = await csv_orchestrator.list_snapshots(vm_id)
            if not snapshots_list:
                raise RuntimeError("没有找到快照，请先在 VMware Workstation 中为该 VM 创建快照")
            snapshot = choose_from_list(snapshots_list, "选择快照")
            if snapshot is None:
                print("已取消")
                return 0

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
            capture_screenshot=args.capture_screenshot,
            normalize_ignore_patterns=load_default_ignore_patterns(),
        )
        reset_progress()
        batch_result = await csv_orchestrator.run_batch(test_case)
        print_batch_summary(batch_result)
        print_batch_report_paths(batch_result.report_dir)
        return 0

    if args.command == "init-config":
        samples_dir_value = clean_cli_value(args.samples_dir) if args.samples_dir else None
        config = await build_config_interactively(provider, args.vm, args.mode, samples_dir=samples_dir_value)
        output = Path(clean_cli_value(args.output))
        write_config(output, config)
        print(f"config={output}")
        return 0

    if args.command == "run-config" or (args.command == "run" and args.config):
        if args.command == "run":
            _validate_run_config_args(parser, args)
        config_path = Path(clean_cli_value(args.config))
        config = load_config(config_path)
        config_provider = create_provider(config.provider.type)
        test_case = to_test_case(config, password=getattr(args, "guest_password", None))
        config_orchestrator = TestOrchestrator(config_provider, Path(config.reports_dir), progress=print_progress)
        if config.samples:
            reset_progress()
            batch_result = await config_orchestrator.run_batch(test_case)
            print_batch_summary(batch_result)
            print_batch_report_paths(batch_result.report_dir)
            return 0
        reset_progress()
        await config_orchestrator.run(test_case)
        return 0

    if args.command == "run":
        _validate_run_args(parser, args)

    orchestrator = TestOrchestrator(provider, Path(clean_cli_value(args.reports_dir)), progress=print_progress)
    vm_id = clean_cli_value(args.vm)
    snapshot = clean_cli_value(args.snapshot) if args.snapshot else None
    if not snapshot:
        snapshots = await orchestrator.list_snapshots(vm_id)
        if not snapshots:
            raise RuntimeError("没有找到快照，请先在 VMware Workstation 中为该 VM 创建快照")
        snapshot = choose_from_list(snapshots, "选择快照")
        if snapshot is None:
            print("已取消")
            return 0

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
        verify_command=args.verify_command or "",
        verify_shell=Shell(args.verify_shell),
        credentials=GuestCredentials(guest_user, guest_password),
        baseline_result=args.baseline_result,
        capture_screenshot=args.capture_screenshot,
        normalize_ignore_patterns=load_default_ignore_patterns(),
    )
    reset_progress()
    await orchestrator.run(test_case)
    return 0


def _generate_report_from_json(input_path: Path, output_path: Path, output_format: str) -> None:
    data = json.loads(input_path.read_text(encoding="utf-8-sig"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    if data.get("schema_version") == 2 and "samples" in data and "mode" in data:
        write_batch_html_from_json(input_path, output_path)
        return
    output_path.write_text(_standalone_html_report(data), encoding="utf-8")


def _standalone_html_report(data: object) -> str:
    title = "VM Auto Test Report"
    body = html.escape(json.dumps(data, ensure_ascii=False, indent=2))
    return (
        "<!doctype html>\n"
        "<html lang=\"zh-CN\">\n"
        "<head><meta charset=\"utf-8\"><title>VM Auto Test Report</title></head>\n"
        "<body>\n"
        f"<h1>{title}</h1>\n"
        f"<pre>{body}</pre>\n"
        "</body>\n"
        "</html>\n"
    )


@dataclass(frozen=True)
class DoctorCheck:
    label: str
    status: str
    detail: str


_DOCTOR_FAIL = "FAIL"
_DOCTOR_OK = "OK"
_DOCTOR_WARN = "WARN"


def _run_doctor(config_path: Path | None, reports_dir: Path) -> int:
    checks = [
        _check_python_version(),
        _check_package_version(),
        _check_vmrun_path(),
    ]
    if config_path is not None:
        checks.append(_check_config_file(config_path))
    checks.append(_check_reports_dir(reports_dir))

    print("VM Auto Test Doctor")
    print()
    for check in checks:
        print(f"[{check.status}] {check.label}: {check.detail}")
    return 3 if any(check.status == _DOCTOR_FAIL for check in checks) else 0


def _check_python_version() -> DoctorCheck:
    current = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    required = sys.version_info >= (3, 10)
    status = _DOCTOR_OK if required else _DOCTOR_FAIL
    return DoctorCheck("Python", status, current)


def _check_package_version() -> DoctorCheck:
    try:
        package_version = version("vm-auto-test")
    except PackageNotFoundError:
        return DoctorCheck("Package", _DOCTOR_WARN, "vm-auto-test is importable but not installed as package")
    return DoctorCheck("Package", _DOCTOR_OK, package_version)


def _check_vmrun_path() -> DoctorCheck:
    value = os.getenv("VMRUN_PATH")
    if not value:
        return DoctorCheck("VMRUN_PATH", _DOCTOR_FAIL, "not configured")
    path = Path(clean_cli_value(value))
    if not path.is_file():
        return DoctorCheck("VMRUN_PATH", _DOCTOR_FAIL, f"not found: {path}")
    return DoctorCheck("VMRUN_PATH", _DOCTOR_OK, str(path))


def _check_config_file(config_path: Path) -> DoctorCheck:
    try:
        load_config(config_path)
    except Exception as exc:
        return DoctorCheck("Config", _DOCTOR_FAIL, f"invalid: {type(exc).__name__}")
    return DoctorCheck("Config", _DOCTOR_OK, str(config_path))


def _check_reports_dir(reports_dir: Path) -> DoctorCheck:
    try:
        reports_dir.mkdir(parents=True, exist_ok=True)
        probe_path = reports_dir / ".vm-auto-test-write-check"
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink()
    except OSError as exc:
        return DoctorCheck("Reports directory", _DOCTOR_FAIL, f"not writable: {type(exc).__name__}")
    return DoctorCheck("Reports directory", _DOCTOR_OK, str(reports_dir))


def _validate_run_config_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    mixed_options = [
        option
        for option, value, default in (
            ("--vm", args.vm, None),
            ("--mode", args.mode, None),
            ("--snapshot", args.snapshot, None),
            ("--sample-command", args.sample_command, None),
            ("--sample-shell", args.sample_shell, Shell.CMD.value),
            ("--verify-command", args.verify_command, None),
            ("--verify-shell", args.verify_shell, Shell.POWERSHELL.value),
            ("--guest-user", args.guest_user, None),
            ("--guest-password", args.guest_password, None),
            ("--baseline-result", args.baseline_result, None),
            ("--capture-screenshot", args.capture_screenshot, False),
            ("--reports-dir", args.reports_dir, "reports"),
        )
        if value != default
    ]
    if mixed_options:
        parser.error("run cannot combine --config with " + ", ".join(mixed_options))


def _validate_run_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    required = [
        ("--vm", args.vm),
        ("--mode", args.mode),
        ("--sample-command", args.sample_command),
    ]
    if args.mode != "av_analyze":
        required.append(("--verify-command", args.verify_command))
    missing = [option for option, value in required if value is None]
    if missing:
        parser.error("run requires --config or " + ", ".join(missing))


async def _interactive_menu(provider: VmwareProvider, env_file: Path) -> None:
    while True:
        print("\n  —— VM Auto Test ——")
        print("  [0] 退出")
        print("  [1] 测试单样本")
        print("  [2] 测试多样本 (CSV)")
        print("  [3] 列出 VM")
        print("  [4] 列出快照")
        print("  [5] 计划任务")
        print("  [6] 重新配置环境")
        choice = input("\n  > ").strip()

        if choice == "0":
            print("  已退出")
            return

        if choice == "6":
            await _interactive_setup(env_file)
            continue

        if choice == "5":
            await _interactive_plan_menu(provider)
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
    """input() wrapper: returns None on 'b' (go back), strips whitespace and quotes."""
    print("  输入 b 返回上一步")
    value = input(f"  {prompt}: ").strip()
    if value.lower() == "b":
        return None
    return clean_cli_value(value)


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
    test_case = await _build_interactive_single_test_case(provider, confirm_action="执行")
    if test_case is None:
        return
    orch = TestOrchestrator(provider, Path("reports"), progress=print_progress)
    try:
        reset_progress()
        await orch.run(test_case)
    except VmToolsNotReadyError:
        return


async def _interactive_av_analyze(provider: VmwareProvider) -> None:
    test_case = await _build_interactive_av_analyze_test_case(provider, confirm_action="执行")
    if test_case is None:
        return
    orch = TestOrchestrator(provider, Path("reports"), progress=print_progress)
    try:
        reset_progress()
        await orch.run(test_case)
    except VmToolsNotReadyError:
        return


async def _build_interactive_av_analyze_test_case(
    provider: VmwareProvider,
    *,
    confirm_action: str,
) -> TestCase | None:
    vm_id: str | None = None
    snapshot: str | None = None
    sample_command: str | None = None
    guest_user: str | None = None
    guest_password: str | None = None

    step = 0
    while True:
        if step == 0:
            running = await provider.list_running_vms()
            if running:
                print("\n  —— 选择 VM ——")
                result = choose_from_list(running, "选择 VM")
                if result is None or result is _BACK:
                    return None
                vm_id = result
            else:
                print("\n  —— 输入 VM 路径 ——")
                result = _prompt_back("VM 路径")
                if result is None:
                    return None
                vm_id = clean_cli_value(result)
            step = 1
            continue

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
            print("\n  —— 选择快照（应已安装杀软）——")
            result = choose_from_list(snapshots, "选择快照")
            if result is None:
                return None
            if result is _BACK:
                step = 0
                continue
            snapshot = result
            step = 2
            continue

        if step == 2:
            print("\n  —— 样本路径 ——")
            result = _prompt_back("样本路径 (例如 C:\\Samples\\malware.exe)")
            if result is None:
                step = 1
                continue
            sample_command = result
            step = 3
            continue

        if step == 3:
            print("\n  —— Guest 凭据 ——")
            creds = await _resolve_and_verify_credentials(provider, vm_id)
            if creds is None:
                step = 2
                continue
            guest_user = creds.user
            guest_password = creds.password
            step = 4
            continue

        if step == 4:
            print(f"\n  VM:         {vm_id}")
            print(f"  快照:       {snapshot}")
            print(f"  模式:       av_analyze (日志分析杀软拦截)")
            print(f"  样本:       {sample_command}")
            print("  分析方式:   自动识别杀软 + 日志关键字匹配")
            print("  截图对比:   已启用（默认）")
            enable_img = True
            confirm = input(f"  确认{confirm_action}? [y/N]: ").strip().lower()
            if confirm == "b":
                step = 3
                continue
            if confirm != "y":
                print("  已取消")
                return None

            from vm_auto_test.models import AvAnalyzeSpec

            av_spec = AvAnalyzeSpec(
                log_collect_shell=Shell.POWERSHELL,
                enable_image_compare=enable_img,
            )
            return TestCase(
                vm_id=vm_id,
                snapshot=snapshot,
                mode=TestMode.AV_ANALYZE,
                sample_command=sample_command,
                sample_shell=Shell.CMD,
                verify_command="",
                verify_shell=Shell.POWERSHELL,
                credentials=GuestCredentials(guest_user, guest_password),
                capture_screenshot=True,
                normalize_ignore_patterns=load_default_ignore_patterns(),
                av_analyze=av_spec,
            )


async def _build_interactive_single_test_case(
    provider: VmwareProvider,
    *,
    confirm_action: str,
) -> TestCase | None:
    vm_id: str | None = None
    snapshot: str | None = None
    mode: TestMode | None = None
    sample_command: str | None = None
    sample_shell: Shell | None = None
    verify_command: str | None = None
    verify_shell: Shell | None = None
    guest_user: str | None = None
    guest_password: str | None = None

    step = 0
    while True:
        if step == 0:
            running = await provider.list_running_vms()
            if running:
                print("\n  —— 选择 VM ——")
                result = choose_from_list(running, "选择 VM")
                if result is None or result is _BACK:
                    return None
                vm_id = result
            else:
                print("\n  —— 输入 VM 路径 ——")
                result = _prompt_back("VM 路径")
                if result is None:
                    return None
                vm_id = clean_cli_value(result)
            step = 1
            continue

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
                return None
            if result is _BACK:
                step = 0
                continue
            snapshot = result
            step = 2
            continue

        if step == 2:
            print("\n  —— 选择模式 ——")
            print("  baseline   = 干净快照，验证样本是否有效（前后输出不同 → 有效）")
            print("  av         = 带杀软快照，验证杀软能否拦截（启动后自动识别杀软环境）")
            print("  av_analyze = AI分析模式：截图+日志+AI判定杀软是否拦截")
            result = choose_value("模式", ["baseline", "av", "av_analyze"], default="baseline")
            if result is _BACK:
                step = 1
                continue
            mode = TestMode(result)
            step = 3
            continue

        if step == 3:
            print("\n  —— 样本路径 ——")
            result = _prompt_back("样本路径 (例如 C:\\Samples\\sample.exe)")
            if result is None:
                step = 2
                continue
            sample_command = result
            sample_shell = Shell.CMD
            if mode == TestMode.AV_ANALYZE:
                step = 5
                verify_command = ""
                verify_shell = Shell.POWERSHELL
            else:
                step = 4
            continue

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

        if step == 5:
            print("\n  —— Guest 凭据 ——")
            creds = await _resolve_and_verify_credentials(provider, vm_id)
            if creds is None:
                step = 3 if mode == TestMode.AV_ANALYZE else 4
                continue
            guest_user = creds.user
            guest_password = creds.password
            if mode != TestMode.AV_ANALYZE:
                resolved = await _resolve_env_vars_in_command(provider, vm_id, verify_command, creds)
                if resolved != verify_command:
                    verify_command = resolved
            step = 6
            continue

        if step == 6:
            if mode == TestMode.AV_ANALYZE:
                capture_screenshot = True
                enable_img = True
            else:
                capture_screenshot = input("  截取 VM 截图? [y/N]: ").strip().lower() == "y"
                enable_img = False
            print(f"\n  VM:       {vm_id}")
            print(f"  快照:     {snapshot}")
            print(f"  模式:     {mode.value}")
            print(f"  样本:     [{sample_shell.value}] {sample_command}")
            if mode != TestMode.AV_ANALYZE:
                print(f"  验证:     [{verify_shell.value}] {verify_command}")
            if capture_screenshot:
                print("  截图:     是")
            if enable_img:
                print("  截图对比:   已启用（默认）")
            confirm = input(f"  确认{confirm_action}? [y/N]: ").strip().lower()
            if confirm == "b":
                step = 3 if mode == TestMode.AV_ANALYZE else 5
                continue
            if confirm != "y":
                print("  已取消")
                return None

            if mode == TestMode.AV_ANALYZE:
                av_spec = AvAnalyzeSpec(
                    log_collect_shell=Shell.POWERSHELL,
                    enable_image_compare=enable_img,
                )
                return TestCase(
                    vm_id=vm_id,
                    snapshot=snapshot,
                    mode=mode,
                    sample_command=sample_command,
                    sample_shell=sample_shell,
                    verify_command="",
                    verify_shell=Shell.POWERSHELL,
                    credentials=GuestCredentials(guest_user, guest_password),
                    capture_screenshot=True,
                    normalize_ignore_patterns=load_default_ignore_patterns(),
                    av_analyze=av_spec,
                )

            return TestCase(
                vm_id=vm_id,
                snapshot=snapshot,
                mode=mode,
                sample_command=sample_command,
                sample_shell=sample_shell,
                verify_command=verify_command,
                verify_shell=verify_shell,
                credentials=GuestCredentials(guest_user, guest_password),
                capture_screenshot=capture_screenshot,
                normalize_ignore_patterns=load_default_ignore_patterns(),
            )


async def _interactive_csv(provider: VmwareProvider) -> None:
    test_case = await _build_interactive_csv_test_case(provider, confirm_action="执行")
    if test_case is None:
        return
    orch = TestOrchestrator(provider, Path("reports"), progress=print_progress)
    try:
        reset_progress()
        batch_result = await orch.run_batch(test_case)
    except VmToolsNotReadyError:
        return
    print_batch_summary(batch_result, indent="    ")
    print_batch_report_paths(batch_result.report_dir, indent="    ")


async def _build_interactive_csv_test_case(
    provider: VmwareProvider,
    *,
    confirm_action: str,
) -> TestCase | None:
    vm_id: str | None = None
    snapshot: str | None = None
    mode: TestMode | None = None
    csv_path: Path | None = None
    samples_base_dir: str | None = None
    guest_user: str | None = None
    guest_password: str | None = None

    step = 0
    while True:
        if step == 0:
            running = await provider.list_running_vms()
            if running:
                print("\n  —— 选择 VM ——")
                result = choose_from_list(running, "选择 VM")
                if result is None or result is _BACK:
                    return None
                vm_id = result
            else:
                print("\n  —— 输入 VM 路径 ——")
                result = _prompt_back("VM 路径")
                if result is None:
                    return None
                vm_id = clean_cli_value(result)
            step = 1
            continue

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
                return None
            if result is _BACK:
                step = 0
                continue
            snapshot = result
            step = 2
            continue

        if step == 2:
            print("\n  —— 选择模式 ——")
            print("  baseline   = 干净快照，验证样本是否有效（前后输出不同 → 有效）")
            print("  av         = 带杀软快照，验证杀软能否拦截")
            print("  av_analyze = AI分析模式：截图+日志+AI判定杀软是否拦截")
            result = choose_value("模式", ["baseline", "av", "av_analyze"], default="baseline")
            if result is _BACK:
                step = 1
                continue
            mode = TestMode(result)
            step = 3
            continue

        if step == 3:
            print("\n  —— CSV 配置 ——")
            if mode == TestMode.AV_ANALYZE:
                print("  av_analyze 模式：CSV 只需要一列 sample_file，无需验证命令")
            result = _prompt_back("CSV 文件路径")
            if result is None:
                step = 2
                continue
            csv_path = Path(clean_cli_value(result))
            if not result or not csv_path.name:
                print("  CSV 文件路径不能为空")
                continue
            if not csv_path.is_file():
                print(f"  文件不存在: {csv_path}")
                continue
            result = _prompt_back("VM 上样本目录 (绝对路径则留空)")
            if result is None:
                step = 2
                continue
            samples_base_dir = clean_cli_value(result) if result else None
            step = 4
            continue

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

        if step == 5:
            sample_configs_raw = parse_csv_samples(csv_path, samples_base_dir=samples_base_dir, mode=mode.value)
            if mode != TestMode.AV_ANALYZE:
                creds_obj = GuestCredentials(guest_user, guest_password)
                sample_configs: list[SampleConfig] = []
                for cfg in sample_configs_raw:
                    verification = cfg.verification
                    if cfg.verification and cfg.verification.command:
                        resolved_cmd = await _resolve_env_vars_in_command(provider, vm_id, cfg.verification.command, creds_obj)
                        if resolved_cmd != cfg.verification.command:
                            verification = VerificationConfig(command=resolved_cmd, shell=cfg.verification.shell)
                    sample_configs.append(SampleConfig(id=cfg.id, command=cfg.command, shell=cfg.shell, verification=verification))
            else:
                sample_configs = list(sample_configs_raw)

            print(f"\n  从 CSV 读取 {len(sample_configs)} 个样本:")
            for cfg in sample_configs:
                print(f"    [{cfg.shell.value}] {cfg.command}")
                if cfg.verification:
                    print(f"      verify: [{cfg.verification.shell.value}] {cfg.verification.command}")
            print(f"  VM:       {vm_id}")
            print(f"  快照:     {snapshot}")
            print(f"  模式:     {mode.value}")
            if mode == TestMode.AV_ANALYZE:
                capture_screenshot = True
                enable_img = True
            else:
                capture_screenshot = input("  截取 VM 截图? [y/N]: ").strip().lower() == "y"
                enable_img = False
            if capture_screenshot:
                print("  截图:     是")
            if enable_img:
                print("  截图对比:   已启用（默认）")
            confirm = input(f"  确认{confirm_action}? [y/N]: ").strip().lower()
            if confirm == "b":
                step = 4
                continue
            if confirm != "y":
                print("  已取消")
                return None

            sample_specs = tuple(
                SampleSpec(
                    id=cfg.id,
                    command=cfg.command,
                    shell=cfg.shell,
                    verification=VerificationSpec(command=cfg.verification.command, shell=cfg.verification.shell) if cfg.verification else None,
                )
                for cfg in sample_configs
            )

            if mode == TestMode.AV_ANALYZE:
                av_spec = AvAnalyzeSpec(
                    log_collect_shell=Shell.POWERSHELL,
                    enable_image_compare=enable_img,
                )
                return TestCase(
                    vm_id=vm_id,
                    snapshot=snapshot,
                    mode=mode,
                    sample_command=sample_configs[0].command,
                    sample_shell=sample_configs[0].shell,
                    verify_command="",
                    verify_shell=Shell.POWERSHELL,
                    credentials=GuestCredentials(guest_user, guest_password),
                    samples=sample_specs,
                    capture_screenshot=True,
                    normalize_ignore_patterns=load_default_ignore_patterns(),
                    av_analyze=av_spec,
                )

            first_v = sample_specs[0].verification
            return TestCase(
                vm_id=vm_id,
                snapshot=snapshot,
                mode=mode,
                sample_command=sample_configs[0].command,
                sample_shell=sample_configs[0].shell,
                verify_command=first_v.command if first_v else "",
                verify_shell=first_v.shell if first_v else Shell.POWERSHELL,
                credentials=GuestCredentials(guest_user, guest_password),
                samples=sample_specs,
                verification=first_v or VerificationSpec(command="", shell=Shell.POWERSHELL),
                capture_screenshot=capture_screenshot,
                normalize_ignore_patterns=load_default_ignore_patterns(),
            )


async def _interactive_plan_menu(provider: VmwareProvider) -> None:
    tasks: list[PlanTask] = []
    next_task_number = 1
    while True:
        print("\n  —— 计划任务 ——")
        print("  [0] 返回主菜单")
        print("  [1] 添加单样本测试")
        print("  [2] 添加多样本测试 (CSV)")
        print("  [3] 查看任务列表")
        print("  [4] 删除任务")
        print("  [5] 清空任务")
        print("  [6] 一键按顺序执行")
        choice = input("\n  > ").strip()

        if choice == "0":
            return
        if choice == "1":
            test_case = await _build_interactive_single_test_case(provider, confirm_action="添加到计划任务")
            if test_case is None:
                continue
            repeat_count = _prompt_repeat_count()
            tasks.append(PlanTask(f"task-{next_task_number}", PlanTaskKind.SINGLE, test_case, repeat_count))
            next_task_number += 1
            print("  已添加计划任务")
            continue
        if choice == "2":
            test_case = await _build_interactive_csv_test_case(provider, confirm_action="添加到计划任务")
            if test_case is None:
                continue
            repeat_count = _prompt_repeat_count()
            tasks.append(PlanTask(f"task-{next_task_number}", PlanTaskKind.BATCH, test_case, repeat_count))
            next_task_number += 1
            print("  已添加计划任务")
            continue
        if choice == "3":
            _print_plan_tasks(tasks)
            continue
        if choice == "4":
            _delete_plan_task(tasks)
            continue
        if choice == "5":
            tasks.clear()
            print("  已清空计划任务")
            continue
        if choice == "6":
            await _run_interactive_plan(provider, tasks)
            continue
        print("  无效选项")


def _prompt_repeat_count() -> int:
    while True:
        value = input("  重复执行次数 [1]: ").strip()
        if not value:
            return 1
        try:
            repeat_count = int(value)
        except ValueError:
            print("  请输入正整数")
            continue
        if repeat_count < 1:
            print("  请输入正整数")
            continue
        if repeat_count > PLAN_REPEAT_COUNT_MAX:
            print(f"  重复次数不能超过 {PLAN_REPEAT_COUNT_MAX}")
            continue
        return repeat_count


def _print_plan_tasks(tasks: Sequence[PlanTask]) -> None:
    if not tasks:
        print("  暂无计划任务")
        return
    print("\n  当前计划任务:")
    for index, task in enumerate(tasks, start=1):
        print(f"  [{index}] {_format_plan_task(task)}")


def _format_plan_task(task: PlanTask) -> str:
    test_case = task.test_case
    kind = "单样本" if task.kind == PlanTaskKind.SINGLE else "多样本"
    sample_count = len(test_case.effective_samples())
    sample_text = test_case.sample_command if task.kind == PlanTaskKind.SINGLE else f"{sample_count} 个样本"
    repeat_text = f" x{task.repeat_count}" if task.repeat_count > 1 else ""
    return (
        f"{task.id} {kind}{repeat_text} | "
        f"VM={test_case.vm_id} | 快照={test_case.snapshot or '(无)'} | "
        f"模式={test_case.mode.value} | {sample_text}"
    )


def _delete_plan_task(tasks: list[PlanTask]) -> None:
    if not tasks:
        print("  暂无计划任务")
        return
    _print_plan_tasks(tasks)
    value = input("  删除序号: ").strip()
    try:
        index = int(value)
    except ValueError:
        print("  请输入有效序号")
        return
    if index < 1 or index > len(tasks):
        print("  序号超出范围")
        return
    removed = tasks.pop(index - 1)
    print(f"  已删除 {removed.id}")


async def _run_interactive_plan(provider: VmwareProvider, tasks: Sequence[PlanTask]) -> None:
    if not tasks:
        print("  暂无计划任务")
        return
    _print_plan_tasks(tasks)
    confirm = input("  确认按顺序执行? [y/N]: ").strip().lower()
    if confirm != "y":
        print("  已取消")
        return
    orch = TestOrchestrator(provider, Path("reports"), progress=print_progress)
    try:
        reset_progress()
        results = await orch.run_plan(tasks)
    except VmToolsNotReadyError:
        return
    _print_plan_results(results)


def _print_plan_results(results: Sequence[PlanRunResult]) -> None:
    print("\n  计划任务执行完成:")
    for result in results:
        report_dir = result.result.report_dir
        print(f"  - {result.task.id} #{result.iteration}: {report_dir}")
        if isinstance(result.result, BatchTestResult):
            print_batch_summary(result.result, indent="    ")
            print_batch_report_paths(report_dir, indent="    ")
        elif isinstance(result.result, TestResult):
            print(f"    报告: {report_dir}")


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


def _quote_if_needed(value: str) -> str:
    if value and (" " in value or "\\" in value):
        if '"' not in value:
            return f'"{value}"'
    return value


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
    if isinstance(exc, argparse.ArgumentError):
        return str(exc)
    if isinstance(exc, json.JSONDecodeError):
        return f"Report error: invalid JSON input: {exc}"
    if isinstance(exc, ValueError):
        return f"Config error: {exc}"
    if isinstance(exc, FileNotFoundError):
        return f"File not found: {exc.filename}"
    if isinstance(exc, (IndexError, NotImplementedError)):
        return str(exc)
    if isinstance(exc, (VmToolsNotReadyError, RuntimeError)):
        return str(exc)
    return f"{type(exc).__name__}: operation failed"


async def _resolve_env_vars_in_command(
    provider: "VmwareProvider",
    vm_id: str,
    command: str,
    credentials: "GuestCredentials",
) -> str:
    """Detect %VAR% in command, expand, then swap any non-credential username to the credential user."""
    var_names = _ENV_VAR_RE.findall(command)
    if not var_names:
        return command

    # Expand each env var via echo (runs as credential user in guest)
    expanded = command
    for var_name in var_names:
        try:
            echo_result = await provider.run_guest_command(
                vm_id,
                f"echo %{var_name}%",
                Shell.CMD,
                credentials,
                timeout_seconds=10,
            )
            value = echo_result.stdout.strip()
            expanded = expanded.replace(f"%{var_name}%", value)
        except Exception:
            continue

    if expanded == command:
        return command

    # Swap any C:\Users\<name>\ that is not the credential user
    auth_user = credentials.user
    _SYSTEM_USERS = {"public", "default", "all users"}
    for m in re.finditer(r"C:\\Users\\([^\\]+)\\?", expanded):
        found_user = m.group(1)
        if found_user.lower() == auth_user.lower():
            break
        if found_user.lower() in _SYSTEM_USERS:
            break
        # Verify it's a real user profile directory before swapping
        try:
            check = await provider.run_guest_command(
                vm_id,
                f'if exist "C:\\Users\\{found_user}\\" (echo Y) else (echo N)',
                Shell.CMD,
                credentials,
                timeout_seconds=10,
            )
            if "Y" not in check.stdout:
                break
        except Exception:
            break
        expanded = expanded.replace(
            f"C:\\Users\\{found_user}\\",
            f"C:\\Users\\{auth_user}\\",
        )
        break

    return expanded


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(main_async(argv))
    except SystemExit:
        raise
    except Exception as exc:
        print(format_cli_error(exc), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    raise SystemExit(main())

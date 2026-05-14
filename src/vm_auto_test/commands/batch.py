from __future__ import annotations

import argparse
import getpass
from pathlib import Path
from typing import Callable

from vm_auto_test.commands.output import print_batch_report_paths, print_batch_summary, print_progress, reset_progress
from vm_auto_test.config import SampleConfig, load_default_ignore_patterns, parse_csv_samples, scan_samples_from_directory
from vm_auto_test.env import resolve_guest_credentials
from vm_auto_test.models import GuestCredentials, SampleSpec, Shell, TestCase, TestMode, VerificationSpec
from vm_auto_test.orchestrator import TestOrchestrator
from vm_auto_test.providers.base import VmwareProvider


CleanValue = Callable[[str], str]
ChooseFromList = Callable[[list[str], str], str | None]


async def run_directory_samples(
    args: argparse.Namespace,
    provider: VmwareProvider,
    clean_value: CleanValue,
    choose_from_list: ChooseFromList,
) -> int:
    sample_dir = Path(clean_value(args.dir))
    globs = (args.pattern,) if args.pattern else None
    sample_configs = scan_samples_from_directory(sample_dir) if globs is None else scan_samples_from_directory(sample_dir, globs=globs)
    _require_samples(sample_configs)

    orchestrator = TestOrchestrator(provider, Path(clean_value(args.reports_dir)), progress=print_progress)
    vm_id = clean_value(args.vm)
    snapshot = await _resolve_snapshot(orchestrator, vm_id, args.snapshot, clean_value, choose_from_list)
    if snapshot is None:
        return 0

    guest_user, guest_password = _resolve_credentials(vm_id, args.guest_user, args.guest_password)
    sample_specs = tuple(SampleSpec(id=cfg.id, command=cfg.command, shell=cfg.shell) for cfg in sample_configs)
    verify_shell = Shell(args.verify_shell)
    verify_command = clean_value(args.verify_command)
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
        normalize_ignore_patterns=tuple(args.ignore_patterns) if args.ignore_patterns else load_default_ignore_patterns(),
    )
    reset_progress()
    batch_result = await orchestrator.run_batch(test_case)
    print_batch_summary(batch_result)
    print_batch_report_paths(batch_result.report_dir)
    return 0


async def run_csv_samples(
    args: argparse.Namespace,
    provider: VmwareProvider,
    clean_value: CleanValue,
    choose_from_list: ChooseFromList,
) -> int:
    csv_path = Path(clean_value(args.csv))
    sample_configs = parse_csv_samples(csv_path, samples_base_dir=clean_value(args.samples_base_dir) if args.samples_base_dir else None)
    _require_samples(sample_configs)
    print(f"Loaded {len(sample_configs)} samples from {csv_path}")

    orchestrator = TestOrchestrator(provider, Path(clean_value(args.reports_dir)), progress=print_progress)
    vm_id = clean_value(args.vm)
    snapshot = await _resolve_snapshot(orchestrator, vm_id, args.snapshot, clean_value, choose_from_list)
    if snapshot is None:
        return 0

    guest_user, guest_password = _resolve_credentials(vm_id, args.guest_user, args.guest_password)
    sample_specs: list[SampleSpec] = []
    for cfg in sample_configs:
        verification = VerificationSpec(
            command=cfg.verification.command,
            shell=cfg.verification.shell,
        ) if cfg.verification else VerificationSpec(command="", shell=Shell.POWERSHELL)
        sample_specs.append(SampleSpec(id=cfg.id, command=cfg.command, shell=cfg.shell, verification=verification))

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
        normalize_ignore_patterns=tuple(args.ignore_patterns) if args.ignore_patterns else load_default_ignore_patterns(),
    )
    reset_progress()
    batch_result = await orchestrator.run_batch(test_case)
    print_batch_summary(batch_result)
    print_batch_report_paths(batch_result.report_dir)
    return 0


async def _resolve_snapshot(
    orchestrator: TestOrchestrator,
    vm_id: str,
    snapshot_value: str | None,
    clean_value: CleanValue,
    choose_from_list: ChooseFromList,
) -> str | None:
    snapshot = clean_value(snapshot_value) if snapshot_value else None
    if snapshot:
        return snapshot

    snapshots_list = await orchestrator.list_snapshots(vm_id)
    if not snapshots_list:
        raise RuntimeError("没有找到快照，请先在 VMware Workstation 中为该 VM 创建快照")
    selected = choose_from_list(snapshots_list, "选择快照")
    if selected is None:
        print("已取消")
        return None
    return selected


def _require_samples(sample_configs: tuple[SampleConfig, ...]) -> None:
    if not sample_configs:
        raise ValueError("No samples found")


def _resolve_credentials(vm_id: str, guest_user_arg: str | None, guest_password_arg: str | None) -> tuple[str, str]:
    creds = resolve_guest_credentials(vm_id)
    if creds:
        return creds.user, creds.password

    guest_user = guest_user_arg or input("Guest user: ")
    guest_password = guest_password_arg
    if guest_password is None:
        guest_password = getpass.getpass("Guest password: ")
    return guest_user, guest_password

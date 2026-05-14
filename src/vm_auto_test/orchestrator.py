from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from vm_auto_test.av_detection import build_detection_command, parse_detection_result
from vm_auto_test.av_logs import collect_av_logs
from vm_auto_test.evaluator import classify_result, evaluate_output
from vm_auto_test.models import (
    BatchTestResult,
    Classification,
    CommandResult,
    EvaluationResult,
    SampleSpec,
    SampleTestResult,
    Shell,
    StepResult,
    TestCase,
    TestMode,
    TestResult,
    VerificationSpec,
)
from vm_auto_test.providers.base import VmwareProvider
from vm_auto_test.reporting import (
    batch_classification,
    create_report_dir,
    write_batch_report,
    write_report,
)

_LOGGER = logging.getLogger(__name__)
_SAMPLE_ID_PATTERN = re.compile(r"^[^\x00-\x1f/\\]{1,64}$")


def _extract_sample_path(command: str) -> str | None:
    """Extract the first path-like token from a sample command string.

    Handles plain paths, quoted paths, and paths with arguments.
    Returns None if no recognizable path is found.
    """
    command = command.strip()
    if not command:
        return None
    if command[0] == '"':
        end = command.find('"', 1)
        if end > 0:
            token = command[1:end]
            return token if token else None
        return None
    space = command.find(" ")
    token = command[:space] if space > 0 else command
    # Strip unmatched trailing quote (e.g. user typed path with closing " only)
    token = token.rstrip('"')
    if not token:
        return None
    if len(token) >= 3 and token[1] == ":" and token[2] == "\\":
        return token
    if token.startswith("\\\\"):
        return token
    return None


def _verdict_text(classification: Classification, mode: TestMode, changed: bool) -> str:
    if mode == TestMode.BASELINE:
        return "✓ SUCCESS — 攻击生效，样本有效" if changed else "✗ FAILED — 攻击未生效，样本无效"
    return "✗ FAILED — 杀软未拦截" if changed else "✓ SUCCESS — 杀软已拦截"
ProgressCallback = Callable[[StepResult], None]
T = TypeVar("T")


class TestOrchestrator:
    def __init__(
        self,
        provider: VmwareProvider,
        report_base_dir: Path,
        progress: ProgressCallback | None = None,
    ) -> None:
        self._provider = provider
        self._report_base_dir = report_base_dir
        self._progress = progress
        self._stage = ""
        self._log_path: Path | None = None

    async def list_snapshots(self, vm_id: str) -> list[str]:
        return await self._provider.list_snapshots(vm_id)

    async def _run_progress_step(
        self,
        name: str,
        detail: str,
        operation: Callable[[], Awaitable[T]],
        passed_detail: str | Callable[[T], str] | None = None,
    ) -> T:
        self._emit(name, "started", detail)
        try:
            result = await operation()
        except Exception as exc:
            self._emit(name, "failed", type(exc).__name__)
            raise
        completed_detail = self._progress_detail(detail, passed_detail, result)
        self._emit(name, "passed", completed_detail)
        return result

    def _run_sync_progress_step(
        self,
        name: str,
        detail: str,
        operation: Callable[[], T],
        passed_detail: str | Callable[[T], str] | None = None,
    ) -> T:
        self._emit(name, "started", detail)
        try:
            result = operation()
        except Exception as exc:
            self._emit(name, "failed", type(exc).__name__)
            raise
        completed_detail = self._progress_detail(detail, passed_detail, result)
        self._emit(name, "passed", completed_detail)
        return result

    def _progress_detail(
        self,
        detail: str,
        passed_detail: str | Callable[[T], str] | None,
        result: T,
    ) -> str:
        if passed_detail is None:
            return detail
        if isinstance(passed_detail, str):
            return passed_detail
        return passed_detail(result)

    def _emit_step(self, step: StepResult) -> None:
        self._emit(step.name, step.status, step.detail)

    def _emit(self, name: str, status: str, detail: str = "") -> None:
        if self._progress:
            try:
                self._progress(StepResult(name, status, detail, self._stage))
            except Exception as exc:
                _LOGGER.warning("Progress callback failed: %s", type(exc).__name__)
        self._write_log(name, status, detail)

    def _write_log(self, name: str, status: str, detail: str) -> None:
        if self._log_path is None:
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stage_part = f"[{self._stage}] " if self._stage else ""
        if name.endswith("_output"):
            for line in detail.splitlines():
                self._log_path.open("a", encoding="utf-8").write(
                    f"{timestamp}  {stage_part}  │ {line}\n"
                )
        elif status == "passed":
            self._log_path.open("a", encoding="utf-8").write(
                f"{timestamp}  {stage_part}✓ {name}  {detail}\n"
            )
        elif status == "failed":
            self._log_path.open("a", encoding="utf-8").write(
                f"{timestamp}  {stage_part}✗ {name}  {detail}\n"
            )
        elif status == "skipped":
            self._log_path.open("a", encoding="utf-8").write(
                f"{timestamp}  {stage_part}⊘ {name}  {detail}\n"
            )

    async def run(self, test_case: TestCase) -> TestResult:
        self._validate_test_case(test_case)
        steps: list[StepResult] = []

        self._stage = "结果"
        report_dir = self._run_sync_progress_step(
            "create_report_dir",
            "sample",
            lambda: create_report_dir(self._report_base_dir, Path(test_case.sample_command).stem or "sample"),
            lambda result: str(result),
        )
        self._log_path = report_dir / "test.log"

        await self._prepare_vm(test_case, steps)
        await self._detect_av_step(test_case, steps)

        verification = test_case.effective_verification()

        self._stage = "验证攻击效果"
        before = await self._run_progress_step(
            "before_verification",
            "verification",
            lambda: self._provider.run_guest_command(
                test_case.vm_id,
                verification.command,
                verification.shell,
                test_case.credentials,
                test_case.command_timeout_seconds,
                progress=self._emit_step,
            ),
            lambda result: result.capture_method,
        )
        steps.append(StepResult("before_verification", "passed", before.capture_method, self._stage))
        self._emit_command_output("before_verification", before)

        sample_path = _extract_sample_path(test_case.sample_command)
        if sample_path is not None and not await self._verify_sample_on_guest(test_case, sample_path):
            self._stage = "运行恶意脚本"
            skip_detail = f"跳过: 样本文件不存在 ({sample_path})"
            self._emit("run_sample", "skipped", skip_detail)
            steps.append(StepResult("run_sample", "skipped", skip_detail, self._stage))
            sample = CommandResult(
                command=test_case.sample_command,
                capture_method="skipped_file_not_found",
            )
            after = before
        else:
            self._stage = "运行恶意脚本"
            sample = await self._run_sample_safe(
                test_case,
                test_case.sample_command,
                test_case.sample_shell,
            )
            steps.append(StepResult("run_sample", sample.exit_code == 0 and "passed" or "failed", sample.capture_method, self._stage))

            self._stage = "验证攻击效果"
            after = await self._run_progress_step(
                "after_verification",
                "verification",
                lambda: self._provider.run_guest_command(
                    test_case.vm_id,
                    verification.command,
                    verification.shell,
                    test_case.credentials,
                    test_case.command_timeout_seconds,
                    progress=self._emit_step,
                ),
                lambda result: result.capture_method,
            )
            steps.append(StepResult("after_verification", "passed", after.capture_method, self._stage))
            self._emit_command_output("after_verification", after)

        if test_case.capture_screenshot:
            self._stage = "验证攻击效果"
            report_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = str(report_dir / "screenshot.png")
            try:
                await self._run_progress_step(
                    "capture_screenshot",
                    "screenshot",
                    lambda: self._provider.capture_screen(
                        test_case.vm_id,
                        screenshot_path,
                        test_case.credentials,
                    ),
                    screenshot_path,
                )
                steps.append(StepResult("capture_screenshot", "passed", screenshot_path, self._stage))
            except Exception as exc:
                self._emit("capture_screenshot", "failed", type(exc).__name__)
                steps.append(StepResult("capture_screenshot", "failed", str(exc), self._stage))

        self._stage = "验证攻击效果"
        if test_case.av_log_collectors:
            logs = await self._run_progress_step(
                "collect_av_logs",
                str(len(test_case.av_log_collectors)),
                lambda: collect_av_logs(self._provider, test_case),
                lambda result: str(len(result)),
            )
            if logs:
                steps.append(StepResult("collect_av_logs", "passed", str(len(logs)), self._stage))
        else:
            logs = ()

        self._stage = "验证攻击效果"
        evaluation, classification = self._run_sync_progress_step(
            "evaluate",
            "comparisons",
            lambda: self._evaluate(before, after, verification, test_case),
            lambda result: _verdict_text(result[1], test_case.mode, result[0].changed),
        )
        steps.append(StepResult("evaluate", "passed", classification.value, self._stage))

        result = TestResult(
            test_case=test_case,
            report_dir=str(report_dir),
            before=before,
            sample=sample,
            after=after,
            changed=evaluation.changed,
            classification=classification,
            steps=tuple(steps),
            evaluation=evaluation,
            logs=logs,
        )

        self._stage = "结果"
        self._run_sync_progress_step(
            "write_report",
            "result.json",
            lambda: write_report(result),
            str(report_dir),
        )
        return result

    async def run_batch(self, test_case: TestCase) -> BatchTestResult:
        self._validate_test_case(test_case)
        t0 = time.monotonic()

        self._stage = "结果"
        report_dir = self._run_sync_progress_step(
            "create_report_dir",
            "batch",
            lambda: create_report_dir(self._report_base_dir, "batch"),
            lambda result: str(result),
        )
        self._log_path = report_dir / "test.log"
        sample_results: list[SampleTestResult] = []
        steps: list[StepResult] = []

        samples = test_case.effective_samples()
        for idx, sample in enumerate(samples):
            self._validate_sample_id(sample.id)
            sample_dir = report_dir / "samples" / sample.id
            self._emit("batch_sample", "started", sample.id)
            run_av_detect = idx == 0
            result = await self._run_single_sample(test_case, sample, sample_dir, run_av_detect=run_av_detect)
            self._emit("batch_sample", "passed", sample.id)
            sample_results.append(result)
            steps.append(StepResult("batch_sample", "passed", sample.id, ""))
            steps.extend(result.steps)

        self._stage = "结果"
        classification = self._run_sync_progress_step(
            "batch_evaluate",
            "summary",
            lambda: batch_classification(tuple(result.classification for result in sample_results)),
            lambda result: result.value,
        )
        steps.append(StepResult("batch_evaluate", "passed", classification.value, self._stage))
        result = BatchTestResult(
            test_case=test_case,
            report_dir=str(report_dir),
            samples=tuple(sample_results),
            classification=classification,
            steps=tuple(steps),
            duration_seconds=time.monotonic() - t0,
        )

        self._stage = "结果"
        self._run_sync_progress_step(
            "write_batch_report",
            "result.json",
            lambda: write_batch_report(result),
            str(report_dir),
        )
        return result

    async def _run_single_sample(
        self,
        test_case: TestCase,
        sample: SampleSpec,
        report_dir: Path,
        *,
        run_av_detect: bool = True,
    ) -> SampleTestResult:
        t0 = time.monotonic()
        steps: list[StepResult] = []

        await self._prepare_vm(test_case, steps)
        if run_av_detect:
            await self._detect_av_step(test_case, steps)

        verification = sample.verification or test_case.effective_verification()

        self._stage = "验证攻击效果"
        before = await self._run_progress_step(
            "before_verification",
            "verification",
            lambda: self._run_verification(test_case, verification),
            lambda result: result.capture_method,
        )
        steps.append(StepResult("before_verification", "passed", before.capture_method, self._stage))
        self._emit_command_output("before_verification", before)

        sample_path = _extract_sample_path(sample.command)
        if sample_path is not None and not await self._verify_sample_on_guest(test_case, sample_path):
            self._stage = "运行恶意脚本"
            skip_detail = f"跳过: 样本文件不存在 ({sample_path})"
            self._emit("run_sample", "skipped", skip_detail)
            steps.append(StepResult("run_sample", "skipped", skip_detail, self._stage))
            sample_result = CommandResult(
                command=sample.command,
                capture_method="skipped_file_not_found",
            )
            after = before
        else:
            self._stage = "运行恶意脚本"
            sample_result = await self._run_sample_safe(test_case, sample.command, sample.shell)
            steps.append(StepResult("run_sample", sample_result.exit_code == 0 and "passed" or "failed", sample_result.capture_method, self._stage))

            self._stage = "验证攻击效果"
            after = await self._run_progress_step(
                "after_verification",
                "verification",
                lambda: self._run_verification(test_case, verification),
                lambda result: result.capture_method,
            )
            steps.append(StepResult("after_verification", "passed", after.capture_method, self._stage))
            self._emit_command_output("after_verification", after)

        if test_case.capture_screenshot:
            self._stage = "验证攻击效果"
            report_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = str(report_dir / "screenshot.png")
            try:
                await self._run_progress_step(
                    "capture_screenshot",
                    "screenshot",
                    lambda: self._provider.capture_screen(
                        test_case.vm_id,
                        screenshot_path,
                        test_case.credentials,
                    ),
                    screenshot_path,
                )
                steps.append(StepResult("capture_screenshot", "passed", screenshot_path, self._stage))
            except Exception as exc:
                self._emit("capture_screenshot", "failed", type(exc).__name__)
                steps.append(StepResult("capture_screenshot", "failed", str(exc), self._stage))

        self._stage = "验证攻击效果"
        if test_case.av_log_collectors:
            logs = await self._run_progress_step(
                "collect_av_logs",
                str(len(test_case.av_log_collectors)),
                lambda: collect_av_logs(self._provider, test_case),
                lambda result: str(len(result)),
            )
            if logs:
                steps.append(StepResult("collect_av_logs", "passed", str(len(logs)), self._stage))
        else:
            logs = ()

        self._stage = "验证攻击效果"
        evaluation, classification = self._run_sync_progress_step(
            "evaluate",
            "comparisons",
            lambda: self._evaluate(before, after, verification, test_case),
            lambda result: _verdict_text(result[1], test_case.mode, result[0].changed),
        )
        steps.append(StepResult("evaluate", "passed", classification.value, self._stage))
        return SampleTestResult(
            test_case=test_case,
            sample_spec=sample,
            report_dir=str(report_dir),
            before=before,
            sample=sample_result,
            after=after,
            evaluation=evaluation,
            classification=classification,
            steps=tuple(steps),
            logs=logs,
            duration_seconds=time.monotonic() - t0,
        )

    async def _prepare_vm(self, test_case: TestCase, steps: list[StepResult]) -> None:
        if test_case.snapshot:
            self._stage = "回滚快照"
            await self._run_progress_step(
                "revert_snapshot",
                "snapshot",
                lambda: self._provider.revert_snapshot(test_case.vm_id, test_case.snapshot or ""),
            )
            steps.append(StepResult("revert_snapshot", "passed", "snapshot", self._stage))

        self._stage = "验证环境"
        await self._run_progress_step(
            "start_vm",
            "vm",
            lambda: self._provider.start_vm(test_case.vm_id),
        )
        steps.append(StepResult("start_vm", "passed", "vm", self._stage))

        await self._run_progress_step(
            "wait_guest_ready",
            "guest tools",
            lambda: self._provider.wait_guest_ready(
                test_case.vm_id,
                test_case.credentials,
                test_case.wait_timeout_seconds,
                progress=self._emit_step,
            ),
        )
        steps.append(StepResult("wait_guest_ready", "passed", "guest tools", self._stage))

    async def _detect_av_step(self, test_case: TestCase, steps: list[StepResult]) -> None:
        if test_case.mode != TestMode.AV:
            return
        self._stage = "验证环境"
        try:
            detected = await self._run_progress_step(
                "detect_av",
                "杀软识别",
                lambda: self._detect_av(test_case),
                lambda result: f"检测到: {result}" if result else "未检测到已知杀软",
            )
            steps.append(StepResult("detect_av", "passed", detected or "未识别", self._stage))
        except Exception:
            pass

    def _evaluate(
        self,
        before: CommandResult,
        after: CommandResult,
        verification: VerificationSpec,
        test_case: TestCase,
    ) -> tuple[EvaluationResult, Classification]:
        evaluation = evaluate_output(before, after, verification, test_case)
        return evaluation, classify_result(evaluation.effect_observed, test_case.mode)

    async def _run_verification(self, test_case: TestCase, verification: VerificationSpec) -> CommandResult:
        return await self._provider.run_guest_command(
            test_case.vm_id,
            verification.command,
            verification.shell,
            test_case.credentials,
            test_case.command_timeout_seconds,
            progress=self._emit_step,
        )

    async def _detect_av(self, test_case: TestCase) -> str | None:
        command = build_detection_command()
        result = await self._provider.run_guest_command(
            test_case.vm_id,
            command,
            Shell.POWERSHELL,
            test_case.credentials,
            test_case.command_timeout_seconds,
            progress=self._emit_step,
        )
        return parse_detection_result(result.stdout)

    def _emit_command_output(self, step_name: str, result: CommandResult) -> None:
        output = result.combined_output.strip()
        if output:
            self._emit(f"{step_name}_output", "info", output)

    async def _run_sample_safe(
        self, test_case: TestCase, command: str, shell: Shell,
    ) -> CommandResult:
        try:
            return await self._run_progress_step(
                "run_sample",
                "sample",
                lambda: self._provider.run_guest_command(
                    test_case.vm_id, command, shell,
                    test_case.credentials, test_case.command_timeout_seconds,
                    progress=self._emit_step,
                ),
                lambda result: result.capture_method,
            )
        except Exception:
            return CommandResult(
                command=command, stdout="", stderr="", exit_code=-1,
                capture_method="blocked_or_timeout",
            )

    async def _verify_sample_on_guest(self, test_case: TestCase, sample_path: str) -> bool:
        try:
            return await self._provider.file_exists_on_guest(
                test_case.vm_id, sample_path, test_case.credentials,
            )
        except Exception as exc:
            _LOGGER.warning(
                "Sample existence check failed for %s, assuming present: %s",
                sample_path, type(exc).__name__,
            )
            return True

    def _validate_test_case(self, test_case: TestCase) -> None:
        pass

    def _validate_sample_id(self, sample_id: str) -> None:
        if not _SAMPLE_ID_PATTERN.fullmatch(sample_id):
            raise ValueError("Sample id must be 1-64 characters and not contain / or \\")

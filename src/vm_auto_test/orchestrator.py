from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from collections.abc import Sequence
from typing import TypeVar

from vm_auto_test.analysis import compare_screenshots, run_analysis
from vm_auto_test.av_detection import build_detection_command, parse_detection_result
from vm_auto_test.av_logs import collect_av_logs
from vm_auto_test.evaluator import classify_result, evaluate_output
from vm_auto_test.models import (
    AvAnalyzeResult,
    AvAnalyzeSpec,
    BatchTestResult,
    Classification,
    CommandResult,
    EvaluationResult,
    PLAN_REPEAT_COUNT_MAX,
    PlanRunResult,
    PlanTask,
    PlanTaskKind,
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
_SAMPLE_SCREENSHOT_DELAY_SECONDS = 10.0


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


def _normalize_log_for_comparison(content: str) -> str:
    """Strip volatile metadata lines so before/after comparison is meaningful."""
    import re as _re

    return _re.sub(
        r"^(数据库：.*|生成时间：.*|从 WAL 恢复：.*)\n",
        "",
        content,
        flags=_re.MULTILINE,
    )


def _verdict_text(classification: Classification, mode: TestMode, changed: bool) -> str:
    if mode == TestMode.BASELINE:
        return "✓ SUCCESS — 攻击生效，样本有效" if changed else "✗ FAILED — 攻击未生效，样本无效"
    if mode == TestMode.AV_ANALYZE:
        return "✗ FAILED — AI分析: 杀软未拦截" if changed else "✓ SUCCESS — AI分析: 杀软已拦截"
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
        self._detected_av_name: str | None = None
        self._guest_username: str | None = None

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
        elif status == "info":
            self._log_path.open("a", encoding="utf-8").write(
                f"{timestamp}  {stage_part}ℹ {name}  {detail}\n"
            )

    async def run(self, test_case: TestCase) -> TestResult:
        if test_case.mode == TestMode.AV_ANALYZE:
            return await self.run_av_analyze(test_case)

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
            sample_started = asyncio.Event()
            screenshot_task = self._create_sample_screenshot_task(test_case, report_dir, sample_started)
            sample = await self._run_sample_safe(
                test_case,
                test_case.sample_command,
                test_case.sample_shell,
                progress=self._sample_launch_progress(sample_started),
            )
            if not sample_started.is_set():
                sample_started.set()
            screenshot_step = await screenshot_task if screenshot_task else None
            steps.append(StepResult("run_sample", sample.exit_code == 0 and "passed" or "failed", sample.capture_method, self._stage))
            if screenshot_step:
                steps.append(screenshot_step)

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

        if test_case.capture_screenshot and not any(step.name == "capture_screenshot" for step in steps):
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

    async def run_plan(self, tasks: Sequence[PlanTask]) -> tuple[PlanRunResult, ...]:
        results: list[PlanRunResult] = []
        for task in tasks:
            if task.repeat_count < 1 or task.repeat_count > PLAN_REPEAT_COUNT_MAX:
                raise ValueError(f"repeat_count must be between 1 and {PLAN_REPEAT_COUNT_MAX}")
            for iteration in range(1, task.repeat_count + 1):
                detail = f"{task.id} #{iteration}"
                self._log_path = None
                self._emit("plan_task", "started", detail)
                try:
                    if task.kind == PlanTaskKind.SINGLE:
                        result = await self.run(task.test_case)
                    elif task.kind == PlanTaskKind.BATCH:
                        result = await self.run_batch(task.test_case)
                    else:
                        raise ValueError(f"Unsupported plan task kind: {task.kind}")
                except Exception as exc:
                    self._log_path = None
                    self._emit("plan_task", "failed", f"{detail}: {type(exc).__name__}")
                    raise
                self._log_path = None
                results.append(PlanRunResult(task=task, iteration=iteration, result=result))
                self._emit("plan_task", "passed", detail)
        return tuple(results)

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
        if test_case.mode == TestMode.AV_ANALYZE:
            return await self._run_single_sample_av_analyze(
                test_case, sample, report_dir, run_av_detect=run_av_detect,
            )

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
            sample_started = asyncio.Event()
            screenshot_task = self._create_sample_screenshot_task(test_case, report_dir, sample_started)
            sample_result = await self._run_sample_safe(
                test_case,
                sample.command,
                sample.shell,
                progress=self._sample_launch_progress(sample_started),
            )
            if not sample_started.is_set():
                sample_started.set()
            screenshot_step = await screenshot_task if screenshot_task else None
            steps.append(StepResult("run_sample", sample_result.exit_code == 0 and "passed" or "failed", sample_result.capture_method, self._stage))
            if screenshot_step:
                steps.append(screenshot_step)

            self._stage = "验证攻击效果"
            after = await self._run_progress_step(
                "after_verification",
                "verification",
                lambda: self._run_verification(test_case, verification),
                lambda result: result.capture_method,
            )
            steps.append(StepResult("after_verification", "passed", after.capture_method, self._stage))
            self._emit_command_output("after_verification", after)

        if test_case.capture_screenshot and not any(step.name == "capture_screenshot" for step in steps):
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
        if test_case.mode not in (TestMode.AV, TestMode.AV_ANALYZE):
            return
        self._stage = "验证环境"
        try:
            detected = await self._run_progress_step(
                "detect_av",
                "杀软识别",
                lambda: self._detect_av(test_case),
                lambda result: f"检测到: {result}" if result else "未检测到已知杀软",
            )
            self._detected_av_name = detected
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

    async def _get_guest_username(self, test_case: TestCase) -> str:
        """Query the guest for the real %%USERNAME%% via cmd (most reliable over vmrun)."""
        if self._guest_username is not None:
            return self._guest_username
        try:
            result = await self._provider.run_guest_command(
                test_case.vm_id,
                "cmd /c echo %USERNAME%",
                Shell.CMD,
                test_case.credentials,
                test_case.command_timeout_seconds,
            )
            value = result.stdout.strip().splitlines()[-1].strip()
            if value:
                self._guest_username = value
                return value
        except Exception:
            pass
        self._guest_username = test_case.credentials.user
        return self._guest_username

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
        self,
        test_case: TestCase,
        command: str,
        shell: Shell,
        progress: ProgressCallback | None = None,
    ) -> CommandResult:
        try:
            return await self._run_progress_step(
                "run_sample",
                "sample",
                lambda: self._provider.run_guest_command(
                    test_case.vm_id, command, shell,
                    test_case.credentials, test_case.command_timeout_seconds,
                    progress=progress or self._emit_step,
                ),
                lambda result: result.capture_method,
            )
        except Exception:
            return CommandResult(
                command=command, stdout="", stderr="", exit_code=-1,
                capture_method="blocked_or_timeout",
            )

    def _sample_launch_progress(self, sample_started: asyncio.Event) -> ProgressCallback:
        def progress(step: StepResult) -> None:
            if self._is_sample_execution_step(step):
                sample_started.set()
            self._emit_step(step)

        return progress

    def _is_sample_execution_step(self, step: StepResult) -> bool:
        return step.name == "guest_script" and step.status == "started" and step.detail.casefold() in {
            "executing",
            "running",
        }

    def _create_sample_screenshot_task(
        self,
        test_case: TestCase,
        report_dir: Path,
        sample_started: asyncio.Event,
        filename: str = "screenshot.png",
    ) -> asyncio.Task[StepResult] | None:
        if not test_case.capture_screenshot:
            return None
        return asyncio.create_task(self._capture_sample_screenshot(
            test_case, report_dir, sample_started, filename,
        ))

    async def _capture_sample_screenshot(
        self,
        test_case: TestCase,
        report_dir: Path,
        sample_started: asyncio.Event,
        filename: str = "screenshot.png",
    ) -> StepResult:
        await sample_started.wait()
        await asyncio.sleep(_SAMPLE_SCREENSHOT_DELAY_SECONDS)
        report_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(report_dir / filename)
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
            return StepResult("capture_screenshot", "passed", screenshot_path, self._stage)
        except Exception as exc:
            return StepResult("capture_screenshot", "failed", str(exc), self._stage)

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

    async def run_av_analyze(self, test_case: TestCase) -> TestResult:
        if test_case.av_analyze is None:
            raise ValueError("AV_ANALYZE mode requires av_analyze config")
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

        # Before: screenshot + collect logs
        self._stage = "截图(before)"
        report_dir.mkdir(parents=True, exist_ok=True)
        before_screenshot_path = str(report_dir / "screenshot_before.png")
        if test_case.capture_screenshot:
            await self._run_progress_step(
                "capture_screenshot_before",
                "screenshot before sample",
                lambda: self._provider.capture_screen(
                    test_case.vm_id,
                    before_screenshot_path,
                    test_case.credentials,
                ),
                before_screenshot_path,
            )
            steps.append(StepResult("capture_screenshot_before", "passed", before_screenshot_path, self._stage))

        self._stage = "收集日志(before)"
        before_log_content = await self._collect_analyze_logs(test_case, report_dir, steps, suffix="before")

        # Run sample
        self._stage = "运行恶意脚本"
        sample_path = _extract_sample_path(test_case.sample_command)
        if sample_path is not None and not await self._verify_sample_on_guest(test_case, sample_path):
            skip_detail = f"跳过: 样本文件不存在 ({sample_path})"
            self._emit("run_sample", "skipped", skip_detail)
            steps.append(StepResult("run_sample", "skipped", skip_detail, self._stage))
            sample = CommandResult(
                command=test_case.sample_command,
                capture_method="skipped_file_not_found",
            )
        else:
            sample_started = asyncio.Event()
            screenshot_task = self._create_sample_screenshot_task(
                test_case, report_dir, sample_started, filename="screenshot_after.png",
            )
            sample = await self._run_sample_safe(
                test_case,
                test_case.sample_command,
                test_case.sample_shell,
                progress=self._sample_launch_progress(sample_started),
            )
            if not sample_started.is_set():
                sample_started.set()
            screenshot_step = await screenshot_task if screenshot_task else None
            steps.append(StepResult("run_sample", sample.exit_code == 0 and "passed" or "failed", sample.capture_method, self._stage))
            if screenshot_step:
                steps.append(screenshot_step)

        after_screenshot_path = str(report_dir / "screenshot_after.png")

        self._stage = "收集日志(after)"
        after_log_content = await self._collect_analyze_logs(test_case, report_dir, steps, suffix="after")

        # Compare before vs after logs (follow AV mode pattern)
        self._stage = "日志分析"
        logs_changed = _normalize_log_for_comparison(after_log_content) != _normalize_log_for_comparison(before_log_content)
        if test_case.av_analyze.api_key_env or test_case.av_analyze.analyzer_command:
            av_analyze_result = await self._run_ai_analysis(
                test_case.av_analyze, after_log_content,
                Path(before_screenshot_path), Path(after_screenshot_path),
            )
        elif logs_changed:
            av_analyze_result = AvAnalyzeResult(
                log_found=True,
                log_detail="日志有变化（与样本执行前对比），杀软记录了新活动",
                classification=Classification.AV_ANALYZE_BLOCKED,
            )
        elif test_case.av_analyze.enable_image_compare:
            self._stage = "截图对比"
            changed, diff_pct, detail = compare_screenshots(
                Path(before_screenshot_path), Path(after_screenshot_path),
                test_case.av_analyze.image_compare_threshold,
            )
            if changed:
                av_analyze_result = AvAnalyzeResult(
                    log_found=False,
                    log_detail=detail,
                    screenshot_analysis=detail,
                    classification=Classification.AV_ANALYZE_BLOCKED,
                )
            else:
                av_analyze_result = AvAnalyzeResult(
                    log_found=False,
                    log_detail=detail,
                    screenshot_analysis=detail,
                    classification=Classification.AV_ANALYZE_NOT_BLOCKED,
                )
        else:
            av_analyze_result = AvAnalyzeResult(
                log_found=False,
                log_detail="日志无变化，杀软未检测到威胁",
                classification=Classification.AV_ANALYZE_NOT_BLOCKED,
            )
        steps.append(StepResult("log_analysis", "passed", av_analyze_result.classification.value, self._stage))

        self._stage = "验证攻击效果"
        if av_analyze_result.classification == Classification.AV_ANALYZE_BLOCKED:
            self._emit("evaluate", "passed", "✓ SUCCESS — 杀软已拦截")
        else:
            self._emit("evaluate", "passed", "✗ FAILED — 杀软未拦截")

        result = TestResult(
            test_case=test_case,
            report_dir=str(report_dir),
            before=CommandResult(command="log_collect", stdout=before_log_content),
            sample=sample,
            after=CommandResult(command="log_collect", stdout=after_log_content),
            changed=av_analyze_result.classification == Classification.AV_ANALYZE_NOT_BLOCKED,
            classification=av_analyze_result.classification,
            steps=tuple(steps),
            evaluation=None,
            logs=(),
            av_analyze_result=av_analyze_result,
        )

        self._stage = "结果"
        self._run_sync_progress_step(
            "write_report",
            "result.json",
            lambda: write_report(result),
            str(report_dir),
        )
        return result

    async def _run_single_sample_av_analyze(
        self,
        test_case: TestCase,
        sample: SampleSpec,
        report_dir: Path,
        *,
        run_av_detect: bool = True,
    ) -> SampleTestResult:
        if test_case.av_analyze is None:
            raise ValueError("AV_ANALYZE mode requires av_analyze config")
        t0 = time.monotonic()
        steps: list[StepResult] = []

        await self._prepare_vm(test_case, steps)
        if run_av_detect:
            await self._detect_av_step(test_case, steps)

        # Before: screenshot + collect logs
        self._stage = "截图(before)"
        report_dir.mkdir(parents=True, exist_ok=True)
        before_screenshot_path = str(report_dir / "screenshot_before.png")
        if test_case.capture_screenshot:
            await self._run_progress_step(
                "capture_screenshot_before",
                "screenshot before sample",
                lambda: self._provider.capture_screen(
                    test_case.vm_id,
                    before_screenshot_path,
                    test_case.credentials,
                ),
                before_screenshot_path,
            )
            steps.append(StepResult("capture_screenshot_before", "passed", before_screenshot_path, self._stage))

        self._stage = "收集日志(before)"
        before_log_content = await self._collect_analyze_logs(test_case, report_dir, steps, suffix="before")

        # Run sample
        self._stage = "运行恶意脚本"
        sample_path = _extract_sample_path(sample.command)
        if sample_path is not None and not await self._verify_sample_on_guest(test_case, sample_path):
            skip_detail = f"跳过: 样本文件不存在 ({sample_path})"
            self._emit("run_sample", "skipped", skip_detail)
            steps.append(StepResult("run_sample", "skipped", skip_detail, self._stage))
            sample_result = CommandResult(
                command=sample.command,
                capture_method="skipped_file_not_found",
            )
        else:
            sample_started = asyncio.Event()
            screenshot_task = self._create_sample_screenshot_task(
                test_case, report_dir, sample_started, filename="screenshot_after.png",
            )
            sample_result = await self._run_sample_safe(
                test_case,
                sample.command,
                sample.shell,
                progress=self._sample_launch_progress(sample_started),
            )
            if not sample_started.is_set():
                sample_started.set()
            screenshot_step = await screenshot_task if screenshot_task else None
            steps.append(StepResult("run_sample", sample_result.exit_code == 0 and "passed" or "failed", sample_result.capture_method, self._stage))
            if screenshot_step:
                steps.append(screenshot_step)

        after_screenshot_path = str(report_dir / "screenshot_after.png")

        self._stage = "收集日志(after)"
        after_log_content = await self._collect_analyze_logs(test_case, report_dir, steps, suffix="after")

        # Compare before vs after logs (follow AV mode pattern)
        self._stage = "日志分析"
        logs_changed = _normalize_log_for_comparison(after_log_content) != _normalize_log_for_comparison(before_log_content)
        if test_case.av_analyze.api_key_env or test_case.av_analyze.analyzer_command:
            av_analyze_result = await self._run_ai_analysis(
                test_case.av_analyze, after_log_content,
                Path(before_screenshot_path), Path(after_screenshot_path),
            )
        elif logs_changed:
            av_analyze_result = AvAnalyzeResult(
                log_found=True,
                log_detail="日志有变化（与样本执行前对比），杀软记录了新活动",
                classification=Classification.AV_ANALYZE_BLOCKED,
            )
        elif test_case.av_analyze.enable_image_compare:
            self._stage = "截图对比"
            changed, diff_pct, detail = compare_screenshots(
                Path(before_screenshot_path), Path(after_screenshot_path),
                test_case.av_analyze.image_compare_threshold,
            )
            if changed:
                av_analyze_result = AvAnalyzeResult(
                    log_found=False,
                    log_detail=detail,
                    screenshot_analysis=detail,
                    classification=Classification.AV_ANALYZE_BLOCKED,
                )
            else:
                av_analyze_result = AvAnalyzeResult(
                    log_found=False,
                    log_detail=detail,
                    screenshot_analysis=detail,
                    classification=Classification.AV_ANALYZE_NOT_BLOCKED,
                )
        else:
            av_analyze_result = AvAnalyzeResult(
                log_found=False,
                log_detail="日志无变化，杀软未检测到威胁",
                classification=Classification.AV_ANALYZE_NOT_BLOCKED,
            )
        steps.append(StepResult("log_analysis", "passed", av_analyze_result.classification.value, self._stage))

        self._stage = "验证攻击效果"
        if av_analyze_result.classification == Classification.AV_ANALYZE_BLOCKED:
            self._emit("evaluate", "passed", "✓ SUCCESS — 杀软已拦截")
        else:
            self._emit("evaluate", "passed", "✗ FAILED — 杀软未拦截")

        logs_changed = av_analyze_result.classification == Classification.AV_ANALYZE_BLOCKED
        return SampleTestResult(
            test_case=test_case,
            sample_spec=sample,
            report_dir=str(report_dir),
            before=CommandResult(command="log_collect", stdout=before_log_content),
            sample=sample_result,
            after=CommandResult(command="log_collect", stdout=after_log_content),
            evaluation=EvaluationResult(
                changed=logs_changed,
                effect_observed=logs_changed,
            ),
            classification=av_analyze_result.classification,
            steps=tuple(steps),
            logs=(),
            duration_seconds=time.monotonic() - t0,
            av_analyze_result=av_analyze_result,
        )

    async def _collect_analyze_logs(
        self,
        test_case: TestCase,
        report_dir: Path,
        steps: list[StepResult],
        suffix: str = "",
    ) -> str:
        config = test_case.av_analyze
        if config is None:
            return ""

        log_dir = report_dir / "av_logs"
        if suffix:
            log_dir = log_dir / suffix
        log_dir.mkdir(parents=True, exist_ok=True)
        log_parts: list[str] = []

        if config.log_collect_command:
            try:
                log_result = await self._provider.run_guest_command(
                    test_case.vm_id,
                    config.log_collect_command,
                    config.log_collect_shell,
                    test_case.credentials,
                    test_case.command_timeout_seconds,
                    progress=self._emit_step,
                )
                if log_result.stdout:
                    log_parts.append(log_result.stdout)
                if log_result.stderr:
                    log_parts.append(log_result.stderr)
                (log_dir / "collect_stdout.txt").write_text(
                    log_result.stdout, encoding="utf-8",
                )
                steps.append(StepResult("collect_logs", "passed", "log collection script", self._stage))
            except Exception as exc:
                self._emit("collect_logs", "failed", str(exc))
                steps.append(StepResult("collect_logs", "failed", str(exc), self._stage))

        # Resolve effective log_sources and export_preset (auto-detect if not configured)
        effective_sources = list(config.log_sources)
        effective_preset = config.log_export_preset

        if not effective_sources and not effective_preset and self._detected_av_name:
            from vm_auto_test.av_detection import get_log_profile
            from vm_auto_test.models import AvLogSource as Als

            profile = get_log_profile(self._detected_av_name)
            if profile:
                effective_sources = [
                    Als(guest_path=path, description=desc)
                    for path, desc in profile.log_sources
                ]
                effective_preset = profile.export_preset
                self._emit("collect_logs", "info", f"自动配置 {len(effective_sources)} 个日志源, 导出预设: {effective_preset}")
                steps.append(StepResult("collect_logs", "passed", f"auto-detected {self._detected_av_name}", self._stage))

        raw_files: list[Path] = []
        for log_source in effective_sources:
            try:
                resolved_guest_path = log_source.guest_path.replace(
                    "{username}", await self._get_guest_username(test_case)
                )
                local_filename = Path(resolved_guest_path).name or "log.txt"
                local_path = log_dir / local_filename
                await self._provider.copy_file_from_guest(
                    test_case.vm_id,
                    resolved_guest_path,
                    str(local_path),
                    test_case.credentials,
                )
                raw_files.append(local_path)
                self._emit("collect_logs", "info", f"copied: {resolved_guest_path}")
                steps.append(StepResult("collect_logs", "passed", resolved_guest_path, self._stage))
            except Exception as exc:
                self._emit("collect_logs", "failed", f"{resolved_guest_path}: {exc}")
                steps.append(StepResult("collect_logs", "failed", str(exc), self._stage))

        if effective_preset and raw_files:
            try:
                from vm_auto_test.av_exporters.presets import run_log_export

                project_root = Path(__file__).resolve().parent.parent.parent
                export_output = run_log_export(
                    effective_preset,
                    tuple(raw_files),
                    log_dir,
                    project_root,
                )
                exported_text = Path(export_output).read_text(encoding="utf-8", errors="replace")
                log_parts.append(exported_text)
                self._emit("export_logs", "passed", f"preset={effective_preset}, {len(exported_text)} chars")
                steps.append(StepResult("export_logs", "passed", effective_preset, self._stage))
            except Exception as exc:
                self._emit("export_logs", "failed", str(exc))
                steps.append(StepResult("export_logs", "failed", str(exc), self._stage))
        else:
            for local_path in raw_files:
                try:
                    content = local_path.read_text(encoding="utf-8", errors="replace")
                    log_parts.append(f"=== {local_path.name} ===\n{content}")
                except Exception:
                    pass

        combined = "\n\n".join(log_parts)
        (log_dir / "collected_logs.txt").write_text(combined, encoding="utf-8")
        return combined

    async def _run_ai_analysis(
        self,
        config: AvAnalyzeSpec,
        log_content: str,
        before_screenshot: Path,
        after_screenshot: Path,
    ) -> AvAnalyzeResult:
        # The caller ensures report_dir parent exists; derive paths
        report_dir = before_screenshot.parent
        log_file = report_dir / "av_logs" / "after" / "collected_logs.txt"

        return await run_analysis(
            config,
            log_content,
            log_file,
            before_screenshot,
            after_screenshot,
            report_dir,
        )

    def _validate_test_case(self, test_case: TestCase) -> None:
        pass

    def _validate_sample_id(self, sample_id: str) -> None:
        if not _SAMPLE_ID_PATTERN.fullmatch(sample_id):
            raise ValueError("Sample id must be 1-64 characters and not contain / or \\")

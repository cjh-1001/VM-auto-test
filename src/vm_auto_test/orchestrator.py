from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from vm_auto_test.av_logs import collect_av_logs
from vm_auto_test.evaluator import classify_result, evaluate_output
from vm_auto_test.models import (
    BatchTestResult,
    Classification,
    CommandResult,
    EvaluationResult,
    SampleSpec,
    SampleTestResult,
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
    load_baseline_is_valid,
    write_batch_report,
    write_report,
)

_LOGGER = logging.getLogger(__name__)
_SAMPLE_ID_PATTERN = re.compile(r"^[^\x00-\x1f/\\]{1,64}$")
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

        await self._prepare_vm(test_case, steps)

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

        self._stage = "运行恶意脚本"
        sample = await self._run_progress_step(
            "run_sample",
            "sample",
            lambda: self._provider.run_guest_command(
                test_case.vm_id,
                test_case.sample_command,
                test_case.sample_shell,
                test_case.credentials,
                test_case.command_timeout_seconds,
                progress=self._emit_step,
            ),
            lambda result: result.capture_method,
        )
        steps.append(StepResult("run_sample", "passed", sample.capture_method, self._stage))

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

        self._stage = "验证攻击效果"
        logs = await self._run_progress_step(
            "collect_av_logs",
            str(len(test_case.av_log_collectors)),
            lambda: collect_av_logs(self._provider, test_case),
            lambda result: str(len(result)),
        )
        if logs:
            steps.append(StepResult("collect_av_logs", "passed", str(len(logs)), self._stage))

        self._stage = "验证攻击效果"
        evaluation, classification = self._run_sync_progress_step(
            "evaluate",
            "comparisons",
            lambda: self._evaluate(before, after, verification, test_case),
            lambda result: result[1].value,
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

        self._stage = "结果"
        report_dir = self._run_sync_progress_step(
            "create_report_dir",
            "batch",
            lambda: create_report_dir(self._report_base_dir, "batch"),
            lambda result: str(result),
        )
        sample_results: list[SampleTestResult] = []
        steps: list[StepResult] = []

        for sample in test_case.effective_samples():
            self._validate_sample_id(sample.id)
            sample_dir = report_dir / "samples" / sample.id
            self._emit("batch_sample", "started", sample.id)
            result = await self._run_single_sample(test_case, sample, sample_dir)
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
    ) -> SampleTestResult:
        steps: list[StepResult] = []

        await self._prepare_vm(test_case, steps)

        verification = sample.verification or test_case.effective_verification()

        self._stage = "验证攻击效果"
        before = await self._run_progress_step(
            "before_verification",
            "verification",
            lambda: self._run_verification(test_case, verification),
            lambda result: result.capture_method,
        )
        steps.append(StepResult("before_verification", "passed", before.capture_method, self._stage))

        self._stage = "运行恶意脚本"
        sample_result = await self._run_progress_step(
            "run_sample",
            "sample",
            lambda: self._provider.run_guest_command(
                test_case.vm_id,
                sample.command,
                sample.shell,
                test_case.credentials,
                test_case.command_timeout_seconds,
                progress=self._emit_step,
            ),
            lambda result: result.capture_method,
        )
        steps.append(StepResult("run_sample", "passed", sample_result.capture_method, self._stage))

        self._stage = "验证攻击效果"
        after = await self._run_progress_step(
            "after_verification",
            "verification",
            lambda: self._run_verification(test_case, verification),
            lambda result: result.capture_method,
        )
        steps.append(StepResult("after_verification", "passed", after.capture_method, self._stage))

        self._stage = "验证攻击效果"
        logs = await self._run_progress_step(
            "collect_av_logs",
            str(len(test_case.av_log_collectors)),
            lambda: collect_av_logs(self._provider, test_case),
            lambda result: str(len(result)),
        )
        if logs:
            steps.append(StepResult("collect_av_logs", "passed", str(len(logs)), self._stage))

        self._stage = "验证攻击效果"
        evaluation, classification = self._run_sync_progress_step(
            "evaluate",
            "comparisons",
            lambda: self._evaluate(before, after, verification, test_case),
            lambda result: result[1].value,
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

    def _validate_test_case(self, test_case: TestCase) -> None:
        if test_case.mode == TestMode.AV:
            if not test_case.baseline_result:
                raise ValueError("AV mode requires a baseline result path")
            if not load_baseline_is_valid(test_case.baseline_result):
                raise ValueError("AV mode requires a BASELINE_VALID result")

    def _validate_sample_id(self, sample_id: str) -> None:
        if not _SAMPLE_ID_PATTERN.fullmatch(sample_id):
            raise ValueError("Sample id must be 1-64 characters and not contain / or \\")

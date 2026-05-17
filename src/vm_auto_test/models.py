from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class TestMode(str, Enum):
    BASELINE = "baseline"
    AV = "av"
    AV_ANALYZE = "av_analyze"


class Classification(str, Enum):
    BASELINE_VALID = "BASELINE_VALID"
    BASELINE_INVALID = "BASELINE_INVALID"
    AV_NOT_BLOCKED = "AV_NOT_BLOCKED"
    AV_BLOCKED_OR_NO_CHANGE = "AV_BLOCKED_OR_NO_CHANGE"
    AV_ANALYZE_BLOCKED = "AV_ANALYZE_BLOCKED"
    AV_ANALYZE_NOT_BLOCKED = "AV_ANALYZE_NOT_BLOCKED"


class Shell(str, Enum):
    CMD = "cmd"
    POWERSHELL = "powershell"


class PlanTaskKind(str, Enum):
    SINGLE = "single"
    BATCH = "batch"


class ComparisonKind(str, Enum):
    CHANGED = "changed"
    CONTAINS = "contains"
    REGEX = "regex"
    JSON_FIELD = "json_field"
    FILE_HASH = "file_hash"


ComparisonTarget = Literal["before", "after"]
PLAN_REPEAT_COUNT_MAX = 100


@dataclass(frozen=True)
class GuestCredentials:
    user: str
    password: str


@dataclass(frozen=True)
class CommandResult:
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    capture_method: str = "direct"

    @property
    def combined_output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


@dataclass(frozen=True)
class ComparisonSpec:
    kind: ComparisonKind
    target: ComparisonTarget = "after"
    value: str | None = None
    pattern: str | None = None
    path: str | None = None
    expected: Any | None = None


@dataclass(frozen=True)
class ComparisonResult:
    kind: ComparisonKind
    passed: bool
    detail: str = ""
    before_value: str | None = None
    after_value: str | None = None


@dataclass(frozen=True)
class EvaluationResult:
    changed: bool
    effect_observed: bool
    comparisons: tuple[ComparisonResult, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class VerificationSpec:
    command: str
    shell: Shell = Shell.POWERSHELL
    comparisons: tuple[ComparisonSpec, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SampleSpec:
    id: str
    command: str
    shell: Shell = Shell.CMD
    verification: VerificationSpec | None = None


@dataclass(frozen=True)
class AvLogCollectorSpec:
    id: str
    type: str
    command: str
    shell: Shell = Shell.POWERSHELL


@dataclass(frozen=True)
class CollectedLog:
    collector_id: str
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    capture_method: str = "direct"


@dataclass(frozen=True)
class AvLogSource:
    guest_path: str
    description: str = ""


@dataclass(frozen=True)
class AvAnalyzeSpec:
    log_sources: tuple[AvLogSource, ...] = field(default_factory=tuple)
    log_collect_command: str | None = None
    log_collect_shell: Shell = Shell.POWERSHELL
    log_export_preset: str = ""
    log_analysis_prompt: str = ""
    screenshot_analysis_prompt: str = ""
    api_key_env: str = ""
    analyzer_command: str = ""
    enable_image_compare: bool = False
    image_compare_threshold: float = 5.0
    popup_classifier_enabled: bool = False
    popup_classifier_model: str = ""
    popup_classifier_base_url: str = ""
    popup_classifier_api_format: str = "openai"
    popup_classifier_verify_ssl: bool = True


@dataclass(frozen=True)
class AvAnalyzeResult:
    log_found: bool
    log_detail: str = ""
    screenshot_analysis: str | None = None
    classification: Classification = Classification.AV_ANALYZE_NOT_BLOCKED


@dataclass
class DeferredImageResult:
    """Mutable holder for a background image comparison result."""
    value: AvAnalyzeResult | None = None


@dataclass(frozen=True)
class TestCase:
    vm_id: str
    snapshot: str | None
    mode: TestMode
    sample_command: str
    verify_command: str
    credentials: GuestCredentials
    verify_shell: Shell = Shell.POWERSHELL
    sample_shell: Shell = Shell.CMD
    baseline_result: str | None = None
    wait_timeout_seconds: int = 180
    command_timeout_seconds: int = 120
    normalize_trim: bool = True
    normalize_ignore_empty_lines: bool = True
    normalize_ignore_patterns: tuple[str, ...] = field(default_factory=tuple)
    samples: tuple[SampleSpec, ...] = field(default_factory=tuple)
    verification: VerificationSpec | None = None
    av_log_collectors: tuple[AvLogCollectorSpec, ...] = field(default_factory=tuple)
    capture_screenshot: bool = False
    av_analyze: AvAnalyzeSpec | None = None

    def effective_samples(self) -> tuple[SampleSpec, ...]:
        if self.samples:
            return self.samples
        return (
            SampleSpec(
                id="sample",
                command=self.sample_command,
                shell=self.sample_shell,
            ),
        )

    def effective_verification(self) -> VerificationSpec:
        if self.verification is not None:
            return self.verification
        return VerificationSpec(command=self.verify_command, shell=self.verify_shell)


@dataclass(frozen=True)
class StepResult:
    name: str
    status: str
    detail: str = ""
    stage: str = ""


@dataclass(frozen=True)
class TestResult:
    test_case: TestCase
    report_dir: str
    before: CommandResult
    sample: CommandResult
    after: CommandResult
    changed: bool
    classification: Classification
    steps: tuple[StepResult, ...] = field(default_factory=tuple)
    evaluation: EvaluationResult | None = None
    logs: tuple[CollectedLog, ...] = field(default_factory=tuple)
    av_analyze_result: AvAnalyzeResult | None = None
    image_compare_result: DeferredImageResult | None = None


@dataclass(frozen=True)
class SampleTestResult:
    test_case: TestCase
    sample_spec: SampleSpec
    report_dir: str
    before: CommandResult
    sample: CommandResult
    after: CommandResult
    evaluation: EvaluationResult
    classification: Classification
    steps: tuple[StepResult, ...] = field(default_factory=tuple)
    logs: tuple[CollectedLog, ...] = field(default_factory=tuple)
    duration_seconds: float = 0.0
    av_analyze_result: AvAnalyzeResult | None = None
    image_compare_result: DeferredImageResult | None = None

    @property
    def changed(self) -> bool:
        return self.evaluation.changed


@dataclass(frozen=True)
class BatchTestResult:
    test_case: TestCase
    report_dir: str
    samples: tuple[SampleTestResult, ...]
    classification: Classification
    steps: tuple[StepResult, ...] = field(default_factory=tuple)
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class PlanTask:
    id: str
    kind: PlanTaskKind
    test_case: TestCase
    repeat_count: int = 1


@dataclass(frozen=True)
class PlanRunResult:
    task: PlanTask
    iteration: int
    result: TestResult | BatchTestResult

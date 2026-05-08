from __future__ import annotations

import getpass
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from vm_auto_test.models import (
    AvLogCollectorSpec,
    ComparisonKind,
    ComparisonSpec,
    GuestCredentials,
    SampleSpec,
    Shell,
    TestCase,
    TestMode,
    VerificationSpec,
)

DEFAULT_PASSWORD_ENV = "VMWARE_GUEST_PASSWORD"
_SAMPLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class GuestConfig:
    user: str
    password_env: str | None = DEFAULT_PASSWORD_ENV
    password: str | None = None


@dataclass(frozen=True)
class CommandConfig:
    command: str
    shell: Shell
    comparisons: tuple[ComparisonConfig, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ComparisonConfig:
    kind: ComparisonKind
    target: Literal["before", "after"] = "after"
    value: str | None = None
    pattern: str | None = None
    path: str | None = None
    expected: Any | None = None


@dataclass(frozen=True)
class VerificationConfig:
    command: str
    shell: Shell
    comparisons: tuple[ComparisonConfig, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SampleConfig:
    id: str
    command: str
    shell: Shell
    verification: VerificationConfig | None = None


@dataclass(frozen=True)
class AvLogCollectorConfig:
    id: str
    type: str
    command: str
    shell: Shell


@dataclass(frozen=True)
class ProviderConfig:
    type: str = "vmrun"


@dataclass(frozen=True)
class TimeoutConfig:
    wait_guest_seconds: int = 180
    command_seconds: int = 120


@dataclass(frozen=True)
class NormalizeConfig:
    trim: bool = True
    ignore_empty_lines: bool = True


@dataclass(frozen=True)
class TestConfig:
    vm_id: str
    snapshot: str | None
    mode: TestMode
    guest: GuestConfig
    sample: CommandConfig | None = None
    verification: VerificationConfig = field(default_factory=lambda: VerificationConfig("", Shell.POWERSHELL))
    reports_dir: str = "reports"
    baseline_result: str | None = None
    timeouts: TimeoutConfig = field(default_factory=TimeoutConfig)
    normalize: NormalizeConfig = field(default_factory=NormalizeConfig)
    samples: tuple[SampleConfig, ...] = field(default_factory=tuple)
    av_log_collectors: tuple[AvLogCollectorConfig, ...] = field(default_factory=tuple)
    provider: ProviderConfig = field(default_factory=ProviderConfig)


def load_config(path: Path) -> TestConfig:
    data = _load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError("Config file must contain a YAML mapping")
    return parse_config(data)


def write_config(path: Path, config: TestConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _dump_yaml(path, to_yaml_dict(config))


def parse_config(data: dict[str, Any]) -> TestConfig:
    if "sample" in data and "samples" in data:
        raise ValueError("Config cannot contain both sample and samples")

    guest_data = _required_mapping(data, "guest")
    verification_data = _required_mapping(data, "verification")
    timeouts_data = data.get("timeouts") or {}
    normalize_data = data.get("normalize") or {}
    provider_data = data.get("provider") or {}

    mode = TestMode(_required_string(data, "mode"))
    baseline_result = data.get("baseline_result")
    if mode == TestMode.AV and not baseline_result:
        raise ValueError("AV config requires baseline_result")

    sample = _parse_legacy_sample(data.get("sample"))
    samples = _parse_samples(data.get("samples"))
    if sample is None and not samples:
        raise ValueError("Config requires sample or samples")

    return TestConfig(
        vm_id=_required_string(data, "vm_id"),
        snapshot=_optional_string(data, "snapshot"),
        mode=mode,
        guest=GuestConfig(
            user=_required_string(guest_data, "user"),
            password_env=_optional_string(guest_data, "password_env") if "password_env" in guest_data else DEFAULT_PASSWORD_ENV,
            password=_optional_string(guest_data, "password"),
        ),
        sample=sample,
        samples=samples,
        verification=_parse_verification(verification_data),
        reports_dir=str(data.get("reports_dir") or "reports"),
        baseline_result=str(baseline_result) if baseline_result else None,
        timeouts=TimeoutConfig(
            wait_guest_seconds=int(timeouts_data.get("wait_guest_seconds", 180)),
            command_seconds=int(timeouts_data.get("command_seconds", 120)),
        ),
        normalize=NormalizeConfig(
            trim=_optional_bool(normalize_data, "trim", True),
            ignore_empty_lines=_optional_bool(normalize_data, "ignore_empty_lines", True),
        ),
        av_log_collectors=_parse_av_log_collectors(data.get("av_logs") or {}),
        provider=ProviderConfig(type=str(provider_data.get("type") or "vmrun")),
    )


def to_test_case(config: TestConfig, password: str | None = None) -> TestCase:
    resolved_password = resolve_guest_password(config.guest, password=password)
    sample = config.sample or CommandConfig(
        command=config.samples[0].command,
        shell=config.samples[0].shell,
    )
    return TestCase(
        vm_id=config.vm_id,
        snapshot=config.snapshot,
        mode=config.mode,
        sample_command=sample.command,
        sample_shell=sample.shell,
        verify_command=config.verification.command,
        verify_shell=config.verification.shell,
        credentials=GuestCredentials(config.guest.user, resolved_password),
        baseline_result=config.baseline_result,
        wait_timeout_seconds=config.timeouts.wait_guest_seconds,
        command_timeout_seconds=config.timeouts.command_seconds,
        normalize_trim=config.normalize.trim,
        normalize_ignore_empty_lines=config.normalize.ignore_empty_lines,
        samples=tuple(_to_sample_spec(sample_config) for sample_config in config.samples),
        verification=_to_verification_spec(config.verification),
        av_log_collectors=tuple(_to_av_log_spec(collector) for collector in config.av_log_collectors),
    )


def resolve_guest_password(guest: GuestConfig, password: str | None = None) -> str:
    if password is not None:
        return password
    if guest.password is not None:
        return guest.password
    if guest.password_env:
        env_password = os.getenv(guest.password_env)
        if env_password is not None:
            return env_password
    return getpass.getpass("Guest password: ")


def to_yaml_dict(config: TestConfig) -> dict[str, Any]:
    data: dict[str, Any] = {
        "vm_id": config.vm_id,
        "snapshot": config.snapshot,
        "mode": config.mode.value,
        "guest": {
            "user": config.guest.user,
        },
        "verification": _verification_to_yaml(config.verification),
        "reports_dir": config.reports_dir,
        "timeouts": {
            "wait_guest_seconds": config.timeouts.wait_guest_seconds,
            "command_seconds": config.timeouts.command_seconds,
        },
        "normalize": {
            "trim": config.normalize.trim,
            "ignore_empty_lines": config.normalize.ignore_empty_lines,
        },
        "provider": {"type": config.provider.type},
    }
    if config.sample is not None:
        data["sample"] = {
            "command": config.sample.command,
            "shell": config.sample.shell.value,
        }
    if config.samples:
        data["samples"] = [_sample_to_yaml(sample) for sample in config.samples]
    if config.av_log_collectors:
        data["av_logs"] = {
            "collectors": [_av_log_to_yaml(collector) for collector in config.av_log_collectors]
        }
    if config.guest.password_env:
        data["guest"]["password_env"] = config.guest.password_env
    if config.guest.password:
        data["guest"]["password"] = config.guest.password
    if config.baseline_result:
        data["baseline_result"] = config.baseline_result
    return data


def _parse_legacy_sample(value: Any) -> CommandConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Config field 'sample' must be a mapping")
    return CommandConfig(
        command=_required_string(value, "command"),
        shell=Shell(_required_string(value, "shell")),
    )


def _parse_samples(value: Any) -> tuple[SampleConfig, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise ValueError("Config field 'samples' must be a non-empty list")
    return tuple(_parse_sample(item) for item in value)


def _parse_sample(value: Any) -> SampleConfig:
    if not isinstance(value, dict):
        raise ValueError("Each sample must be a mapping")
    verification_data = value.get("verification")
    command = _required_string(value, "command")
    return SampleConfig(
        id=_validate_sample_id(_optional_string(value, "id") or _safe_sample_id(command)),
        command=command,
        shell=Shell(_required_string(value, "shell")),
        verification=_parse_verification(verification_data) if isinstance(verification_data, dict) else None,
    )


def _parse_verification(value: dict[str, Any]) -> VerificationConfig:
    comparisons_data = value.get("comparisons") or []
    if not isinstance(comparisons_data, list):
        raise ValueError("Config field 'comparisons' must be a list")
    return VerificationConfig(
        command=_required_string(value, "command"),
        shell=Shell(_required_string(value, "shell")),
        comparisons=tuple(_parse_comparison(item) for item in comparisons_data),
    )


def _parse_comparison(value: Any) -> ComparisonConfig:
    if not isinstance(value, dict):
        raise ValueError("Each comparison must be a mapping")
    target = str(value.get("target") or "after")
    if target not in {"before", "after"}:
        raise ValueError("Comparison target must be 'before' or 'after'")
    return ComparisonConfig(
        kind=ComparisonKind(_required_string(value, "type")),
        target=cast(Literal["before", "after"], target),
        value=_optional_string(value, "value"),
        pattern=_optional_string(value, "pattern"),
        path=_optional_string(value, "path"),
        expected=value.get("expected"),
    )


def _parse_av_log_collectors(value: dict[str, Any]) -> tuple[AvLogCollectorConfig, ...]:
    collectors = value.get("collectors") or []
    if not collectors:
        return ()
    if not isinstance(collectors, list):
        raise ValueError("Config field 'av_logs.collectors' must be a list")
    parsed: list[AvLogCollectorConfig] = []
    for collector in collectors:
        if not isinstance(collector, dict):
            raise ValueError("Each AV log collector must be a mapping")
        parsed.append(
            AvLogCollectorConfig(
                id=_required_string(collector, "id"),
                type=_required_string(collector, "type"),
                command=_required_string(collector, "command"),
                shell=Shell(_required_string(collector, "shell")),
            )
        )
    return tuple(parsed)


def _to_sample_spec(sample: SampleConfig) -> SampleSpec:
    return SampleSpec(
        id=sample.id,
        command=sample.command,
        shell=sample.shell,
        verification=_to_verification_spec(sample.verification) if sample.verification else None,
    )


def _to_verification_spec(verification: VerificationConfig) -> VerificationSpec:
    return VerificationSpec(
        command=verification.command,
        shell=verification.shell,
        comparisons=tuple(
            ComparisonSpec(
                kind=comparison.kind,
                target=comparison.target,
                value=comparison.value,
                pattern=comparison.pattern,
                path=comparison.path,
                expected=comparison.expected,
            )
            for comparison in verification.comparisons
        ),
    )


def _to_av_log_spec(collector: AvLogCollectorConfig) -> AvLogCollectorSpec:
    return AvLogCollectorSpec(
        id=collector.id,
        type=collector.type,
        command=collector.command,
        shell=collector.shell,
    )


def _verification_to_yaml(verification: VerificationConfig) -> dict[str, Any]:
    data: dict[str, Any] = {
        "command": verification.command,
        "shell": verification.shell.value,
    }
    if verification.comparisons:
        data["comparisons"] = [
            {
                key: value
                for key, value in {
                    "type": comparison.kind.value,
                    "target": comparison.target,
                    "value": comparison.value,
                    "pattern": comparison.pattern,
                    "path": comparison.path,
                    "expected": comparison.expected,
                }.items()
                if value is not None
            }
            for comparison in verification.comparisons
        ]
    return data


def _sample_to_yaml(sample: SampleConfig) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": sample.id,
        "command": sample.command,
        "shell": sample.shell.value,
    }
    if sample.verification:
        data["verification"] = _verification_to_yaml(sample.verification)
    return data


def _av_log_to_yaml(collector: AvLogCollectorConfig) -> dict[str, Any]:
    return {
        "id": collector.id,
        "type": collector.type,
        "command": collector.command,
        "shell": collector.shell.value,
    }


def _safe_sample_id(command: str) -> str:
    stem = Path(command).stem or "sample"
    sample_id = "".join(char if char.isalnum() or char in "-_" else "-" for char in stem)
    return _validate_sample_id(sample_id[:64] or "sample")


def _validate_sample_id(sample_id: str) -> str:
    if not _SAMPLE_ID_PATTERN.fullmatch(sample_id):
        raise ValueError("Sample id must match [A-Za-z0-9_-] and be 1-64 characters")
    return sample_id


def _required_mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Config field '{key}' must be a mapping")
    return value


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"Config field '{key}' is required")
    return str(value)


def _optional_string(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None or str(value).strip() == "":
        return None
    return str(value)


def _optional_bool(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized_value = value.strip().lower()
        if normalized_value in {"true", "yes", "1"}:
            return True
        if normalized_value in {"false", "no", "0"}:
            return False
    raise ValueError(f"Config field '{key}' must be a boolean")


def _load_yaml(path: Path) -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required. Install with: pip install -e .") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _dump_yaml(path: Path, data: dict[str, Any]) -> None:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required. Install with: pip install -e .") from exc
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

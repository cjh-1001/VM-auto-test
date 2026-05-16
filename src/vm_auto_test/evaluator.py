from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from vm_auto_test.models import (
    Classification,
    CommandResult,
    ComparisonKind,
    ComparisonResult,
    ComparisonSpec,
    EvaluationResult,
    TestCase,
    TestMode,
    VerificationSpec,
)

_LOGGER = logging.getLogger(__name__)


def normalize_output(value: str, test_case: TestCase) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if test_case.normalize_trim:
        lines = [line.strip() for line in lines]
    if test_case.normalize_ignore_empty_lines:
        lines = [line for line in lines if line]
    for raw in test_case.normalize_ignore_patterns:
        pattern = re.compile(raw)
        matched_lines = [line for line in lines if pattern.search(line)]
        if matched_lines:
            _LOGGER.debug("Ignore pattern %r removed %d line(s): %r", raw, len(matched_lines), matched_lines)
        lines = [line for line in lines if not pattern.search(line)]
    return "\n".join(lines)


def output_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def evaluate_output(
    before: CommandResult,
    after: CommandResult,
    verification: VerificationSpec,
    test_case: TestCase,
) -> EvaluationResult:
    normalized_before = normalize_output(before.combined_output, test_case)
    normalized_after = normalize_output(after.combined_output, test_case)
    changed = normalized_before != normalized_after
    comparisons = verification.comparisons or (ComparisonSpec(kind=ComparisonKind.CHANGED),)
    results = tuple(
        _evaluate_comparison(comparison, normalized_before, normalized_after)
        for comparison in comparisons
    )
    return EvaluationResult(
        changed=changed,
        effect_observed=all(result.passed for result in results),
        comparisons=results,
    )


def classify_result(effect_observed: bool, mode: TestMode) -> Classification:
    if mode == TestMode.BASELINE:
        return Classification.BASELINE_VALID if effect_observed else Classification.BASELINE_INVALID
    if mode == TestMode.AV_ANALYZE:
        return Classification.AV_ANALYZE_NOT_BLOCKED if effect_observed else Classification.AV_ANALYZE_BLOCKED
    return Classification.AV_NOT_BLOCKED if effect_observed else Classification.AV_BLOCKED_OR_NO_CHANGE


def _evaluate_comparison(
    comparison: ComparisonSpec,
    before: str,
    after: str,
) -> ComparisonResult:
    if comparison.kind == ComparisonKind.CHANGED:
        passed = before != after
        return ComparisonResult(
            kind=comparison.kind,
            passed=passed,
            detail="before and after differ" if passed else "before and after are equal",
            before_value=before,
            after_value=after,
        )
    if comparison.kind == ComparisonKind.CONTAINS:
        target_value = _target_value(comparison, before, after)
        expected_value = comparison.value or ""
        passed = expected_value in target_value
        return ComparisonResult(
            kind=comparison.kind,
            passed=passed,
            detail=f"target contains {expected_value!r}" if passed else f"target does not contain {expected_value!r}",
            after_value=target_value,
        )
    if comparison.kind == ComparisonKind.REGEX:
        target_value = _target_value(comparison, before, after)
        pattern = comparison.pattern or ""
        try:
            matched = re.search(pattern, target_value) is not None
        except re.error as exc:
            raise ValueError(f"Invalid regex: {pattern}") from exc
        return ComparisonResult(
            kind=comparison.kind,
            passed=matched,
            detail=f"target matches {pattern!r}" if matched else f"target does not match {pattern!r}",
            after_value=target_value,
        )
    if comparison.kind == ComparisonKind.JSON_FIELD:
        target_value = _target_value(comparison, before, after)
        actual = _json_path_value(target_value, comparison.path or "")
        passed = actual == comparison.expected
        return ComparisonResult(
            kind=comparison.kind,
            passed=passed,
            detail=f"{comparison.path} == {comparison.expected!r}" if passed else f"{comparison.path} == {actual!r}",
            after_value=str(actual),
        )
    if comparison.kind == ComparisonKind.FILE_HASH:
        target_value = _target_value(comparison, before, after)
        actual_hash = output_hash(target_value)
        expected_hash = str(comparison.expected) if comparison.expected is not None else output_hash(before)
        passed = actual_hash == expected_hash
        return ComparisonResult(
            kind=comparison.kind,
            passed=passed,
            detail=f"sha256={actual_hash}",
            before_value=output_hash(before),
            after_value=actual_hash,
        )
    raise ValueError(f"Unsupported comparison kind: {comparison.kind}")


def _target_value(comparison: ComparisonSpec, before: str, after: str) -> str:
    if comparison.target == "before":
        return before
    return after


def _json_path_value(value: str, path: str) -> Any:
    try:
        current: Any = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Comparison target is not valid JSON") from exc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"JSON field not found: {path}")
        current = current[part]
    return current

from __future__ import annotations

import pytest

from vm_auto_test.evaluator import evaluate_output, output_hash
from vm_auto_test.models import (
    CommandResult,
    ComparisonKind,
    ComparisonSpec,
    GuestCredentials,
    TestCase,
    TestMode,
    VerificationSpec,
)


def make_case() -> TestCase:
    return TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="sample.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
    )


def test_evaluate_output_defaults_to_changed_strategy():
    result = evaluate_output(
        CommandResult(command="verify", stdout="missing"),
        CommandResult(command="verify", stdout="present"),
        VerificationSpec(command="verify"),
        make_case(),
    )

    assert result.changed is True
    assert result.effect_observed is True
    assert result.comparisons[0].kind == ComparisonKind.CHANGED


def test_contains_strategy_checks_selected_output():
    result = evaluate_output(
        CommandResult(command="verify", stdout="before"),
        CommandResult(command="verify", stdout="marker created"),
        VerificationSpec(
            command="verify",
            comparisons=(ComparisonSpec(kind=ComparisonKind.CONTAINS, value="created"),),
        ),
        make_case(),
    )

    assert result.effect_observed is True


def test_regex_strategy_rejects_invalid_pattern():
    with pytest.raises(ValueError, match="Invalid regex"):
        evaluate_output(
            CommandResult(command="verify", stdout="before"),
            CommandResult(command="verify", stdout="after"),
            VerificationSpec(
                command="verify",
                comparisons=(ComparisonSpec(kind=ComparisonKind.REGEX, pattern="["),),
            ),
            make_case(),
        )


def test_json_field_strategy_supports_dotted_paths():
    result = evaluate_output(
        CommandResult(command="verify", stdout="{}"),
        CommandResult(command="verify", stdout='{"result": {"status": "created"}}'),
        VerificationSpec(
            command="verify",
            comparisons=(
                ComparisonSpec(
                    kind=ComparisonKind.JSON_FIELD,
                    path="result.status",
                    expected="created",
                ),
            ),
        ),
        make_case(),
    )

    assert result.effect_observed is True


def test_file_hash_strategy_can_compare_expected_hash():
    expected = output_hash("expected content")

    result = evaluate_output(
        CommandResult(command="verify", stdout="old content"),
        CommandResult(command="verify", stdout="expected content"),
        VerificationSpec(
            command="verify",
            comparisons=(ComparisonSpec(kind=ComparisonKind.FILE_HASH, expected=expected),),
        ),
        make_case(),
    )

    assert result.effect_observed is True

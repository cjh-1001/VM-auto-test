from __future__ import annotations

import json

import pytest

from conftest import FakeProvider
from vm_auto_test.models import Classification, GuestCredentials, SampleSpec, TestCase, TestMode
from vm_auto_test.orchestrator import TestOrchestrator


@pytest.mark.asyncio
async def test_run_batch_executes_each_sample_with_isolated_snapshot(tmp_path):
    provider = FakeProvider(outputs=["before-1", "sample-1", "after-1", "before-2", "sample-2", "after-2"])
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="legacy.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
        samples=(
            SampleSpec(id="one", command="one.exe"),
            SampleSpec(id="two", command="two.exe"),
        ),
    )

    result = await TestOrchestrator(provider, tmp_path).run_batch(test_case)

    assert result.classification == Classification.BASELINE_VALID
    assert [sample.sample_spec.id for sample in result.samples] == ["one", "two"]
    assert provider.commands == [
        "revert:clean",
        "start",
        "wait",
        "verify",
        "one.exe",
        "verify",
        "revert:clean",
        "start",
        "wait",
        "verify",
        "two.exe",
        "verify",
    ]


@pytest.mark.asyncio
async def test_run_batch_rejects_programmatic_unsafe_sample_id(tmp_path):
    provider = FakeProvider(outputs=[])
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="legacy.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
        samples=(SampleSpec(id="../escape", command="one.exe"),),
    )

    with pytest.raises(ValueError, match="Sample id"):
        await TestOrchestrator(provider, tmp_path).run_batch(test_case)


@pytest.mark.asyncio
async def test_run_batch_av_requires_valid_batch_baseline(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "mode": "baseline",
                "summary": {"overall_classification": "BASELINE_VALID"},
                "samples": [{"classification": "BASELINE_VALID"}],
            }
        ),
        encoding="utf-8",
    )
    provider = FakeProvider(outputs=["same", "sample", "same"])
    test_case = TestCase(
        vm_id="vm1",
        snapshot="av",
        mode=TestMode.AV,
        sample_command="legacy.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
        baseline_result=str(baseline_path),
        samples=(SampleSpec(id="one", command="one.exe"),),
    )

    result = await TestOrchestrator(provider, tmp_path).run_batch(test_case)

    assert result.classification == Classification.AV_BLOCKED_OR_NO_CHANGE
    assert result.samples[0].classification == Classification.AV_BLOCKED_OR_NO_CHANGE


@pytest.mark.asyncio
async def test_run_batch_emits_sample_progress_events(tmp_path):
    provider = FakeProvider(outputs=["before-1", "sample-1", "after-1", "before-2", "sample-2", "after-2"])
    events = []
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="legacy.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
        samples=(
            SampleSpec(id="one", command="one.exe"),
            SampleSpec(id="two", command="two.exe"),
        ),
    )

    await TestOrchestrator(provider, tmp_path, progress=events.append).run_batch(test_case)

    sample_events = [event for event in events if event.name == "run_batch_sample"]
    assert [(event.status, event.detail) for event in sample_events] == [
        ("started", "one"),
        ("passed", "one"),
        ("started", "two"),
        ("passed", "two"),
    ]
    assert (events[-2].name, events[-2].status) == ("write_batch_report", "started")
    assert (events[-1].name, events[-1].status) == ("write_batch_report", "passed")

from __future__ import annotations

import json

import pytest

from conftest import FakeProvider
from vm_auto_test.models import Classification, GuestCredentials, PlanTask, PlanTaskKind, SampleSpec, TestCase, TestMode
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
    assert result.duration_seconds >= 0
    assert [sample.sample_spec.id for sample in result.samples] == ["one", "two"]
    assert all(sample.duration_seconds >= 0 for sample in result.samples)
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
async def test_run_plan_executes_tasks_in_order_and_honors_repeat_count(tmp_path):
    provider = FakeProvider(outputs=[
        "before-single-1", "sample-single-1", "after-single-1",
        "before-single-2", "sample-single-2", "after-single-2",
        "before-batch", "sample-batch", "after-batch",
    ])
    single_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="single.exe",
        verify_command="verify-single",
        credentials=GuestCredentials("user", "pass"),
    )
    batch_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="legacy.exe",
        verify_command="verify-batch",
        credentials=GuestCredentials("user", "pass"),
        samples=(SampleSpec(id="one", command="batch-one.exe"),),
    )

    results = await TestOrchestrator(provider, tmp_path).run_plan((
        PlanTask(id="task-1", kind=PlanTaskKind.SINGLE, test_case=single_case, repeat_count=2),
        PlanTask(id="task-2", kind=PlanTaskKind.BATCH, test_case=batch_case),
    ))

    assert [(result.task.id, result.iteration) for result in results] == [
        ("task-1", 1),
        ("task-1", 2),
        ("task-2", 1),
    ]
    assert provider.commands == [
        "revert:clean", "start", "wait", "verify-single", "single.exe", "verify-single",
        "revert:clean", "start", "wait", "verify-single", "single.exe", "verify-single",
        "revert:clean", "start", "wait", "verify-batch", "batch-one.exe", "verify-batch",
    ]


@pytest.mark.asyncio
async def test_run_plan_rejects_repeat_count_outside_allowed_range(tmp_path):
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="single.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
    )

    for repeat_count in (0, 101):
        with pytest.raises(ValueError, match="repeat_count"):
            await TestOrchestrator(FakeProvider(), tmp_path).run_plan((
                PlanTask(id="task-1", kind=PlanTaskKind.SINGLE, test_case=test_case, repeat_count=repeat_count),
            ))


@pytest.mark.asyncio
async def test_run_plan_emits_failed_event_when_task_fails(tmp_path):
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="single.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
    )
    events = []

    with pytest.raises(ValueError):
        await TestOrchestrator(FakeProvider(), tmp_path, progress=events.append).run_plan((
            PlanTask(id="task-1", kind="unknown", test_case=test_case),
        ))

    assert [(event.status, event.detail) for event in events if event.name == "plan_task"] == [
        ("started", "task-1 #1"),
        ("failed", "task-1 #1: ValueError"),
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
async def test_run_batch_av_accepts_optional_batch_baseline(tmp_path):
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
    provider = FakeProvider(outputs=["NONE", "same", "sample", "same"])
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
async def test_run_batch_av_without_baseline_result(tmp_path):
    provider = FakeProvider(outputs=["NONE", "same", "sample", "same"])
    test_case = TestCase(
        vm_id="vm1",
        snapshot="av",
        mode=TestMode.AV,
        sample_command="legacy.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
        samples=(SampleSpec(id="one", command="one.exe"),),
    )

    result = await TestOrchestrator(provider, tmp_path).run_batch(test_case)

    assert result.classification == Classification.AV_BLOCKED_OR_NO_CHANGE
    assert result.samples[0].classification == Classification.AV_BLOCKED_OR_NO_CHANGE
    assert result.test_case.baseline_result is None
    report_dir = tmp_path / result.report_dir
    assert (report_dir / "result.csv").exists()
    assert (report_dir / "result.html").exists()


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

    sample_events = [event for event in events if event.name == "batch_sample"]
    assert [(event.status, event.detail) for event in sample_events] == [
        ("started", "one"),
        ("passed", "one"),
        ("started", "two"),
        ("passed", "two"),
    ]
    assert (events[-2].name, events[-2].status) == ("write_batch_report", "started")
    assert (events[-1].name, events[-1].status) == ("write_batch_report", "passed")


@pytest.mark.asyncio
async def test_run_batch_skips_sample_when_file_not_on_guest(tmp_path):
    class SelectiveFileProvider(FakeProvider):
        def __init__(self):
            super().__init__(outputs=["before-1", "before-2", "sample-2", "after-2"])
            self._missing_paths = {"C:\\Samples\\missing.exe"}

        async def file_exists_on_guest(self, vm_id, guest_path, credentials):
            return guest_path not in self._missing_paths

    provider = SelectiveFileProvider()
    events = []
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.BASELINE,
        sample_command="legacy.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
        samples=(
            SampleSpec(id="one", command="C:\\Samples\\missing.exe"),
            SampleSpec(id="two", command="C:\\Samples\\present.exe"),
        ),
    )

    result = await TestOrchestrator(provider, tmp_path, progress=events.append).run_batch(test_case)

    sample_events = [e for e in events if e.name == "run_sample" and e.status != "started"]
    statuses = [(e.status,) for e in sample_events]
    assert statuses == [("skipped",), ("passed",)]
    assert result.samples[0].classification == Classification.BASELINE_INVALID
    assert result.samples[1].classification == Classification.BASELINE_VALID
    assert result.samples[0].sample.capture_method == "skipped_file_not_found"
    assert result.samples[1].sample.capture_method == "direct"

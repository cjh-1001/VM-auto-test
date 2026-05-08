from __future__ import annotations

import pytest

from conftest import FakeProvider
from vm_auto_test.av_logs import collect_av_logs
from vm_auto_test.models import AvLogCollectorSpec, GuestCredentials, Shell, TestCase, TestMode


@pytest.mark.asyncio
async def test_collect_av_logs_runs_user_configured_guest_command():
    provider = FakeProvider(outputs=["log output"])
    test_case = TestCase(
        vm_id="vm1",
        snapshot="clean",
        mode=TestMode.AV,
        sample_command="sample.exe",
        verify_command="verify",
        credentials=GuestCredentials("user", "pass"),
        av_log_collectors=(
            AvLogCollectorSpec(
                id="events",
                type="guest_command",
                command="Get-WinEvent -LogName Application -MaxEvents 1",
                shell=Shell.POWERSHELL,
            ),
        ),
    )

    logs = await collect_av_logs(provider, test_case)

    assert provider.commands == ["Get-WinEvent -LogName Application -MaxEvents 1"]
    assert logs[0].collector_id == "events"
    assert logs[0].stdout == "log output"


@pytest.mark.asyncio
async def test_collect_av_logs_defaults_to_noop():
    logs = await collect_av_logs(
        FakeProvider(outputs=[]),
        TestCase(
            vm_id="vm1",
            snapshot="clean",
            mode=TestMode.BASELINE,
            sample_command="sample.exe",
            verify_command="verify",
            credentials=GuestCredentials("user", "pass"),
        ),
    )

    assert logs == ()

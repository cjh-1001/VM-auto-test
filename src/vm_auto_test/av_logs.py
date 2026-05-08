from __future__ import annotations

from vm_auto_test.models import CollectedLog, TestCase
from vm_auto_test.providers.base import VmwareProvider


async def collect_av_logs(
    provider: VmwareProvider,
    test_case: TestCase,
) -> tuple[CollectedLog, ...]:
    logs: list[CollectedLog] = []
    for collector in test_case.av_log_collectors:
        if collector.type != "guest_command":
            raise ValueError(f"Unsupported AV log collector type: {collector.type}")
        result = await provider.run_guest_command(
            test_case.vm_id,
            collector.command,
            collector.shell,
            test_case.credentials,
            test_case.command_timeout_seconds,
        )
        logs.append(
            CollectedLog(
                collector_id=collector.id,
                command=collector.command,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
                capture_method=result.capture_method,
            )
        )
    return tuple(logs)

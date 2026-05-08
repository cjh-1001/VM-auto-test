from __future__ import annotations

import pytest

from vmware_mcp.vmrun import VMRun, _strip_surrounding_quotes


def test_strip_surrounding_quotes_from_vmrun_path():
    assert _strip_surrounding_quotes('"D:\\VM2\\vmrun.exe"') == "D:\\VM2\\vmrun.exe"


@pytest.mark.asyncio
async def test_vmrun_reports_missing_executable_path():
    vmrun = VMRun(vmrun_path="Z:\\missing\\vmrun.exe")

    with pytest.raises(RuntimeError, match="vmrun.exe not found"):
        await vmrun.list_running()
